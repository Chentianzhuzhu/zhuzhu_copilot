import os
import sys
import time
import ctypes
import threading
import webbrowser
from pathlib import Path
from typing import List

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QListWidget, QListWidgetItem, QProgressBar,
    QTextEdit, QMessageBox, QApplication,
    QFileDialog, QDialog, QScrollArea, QFrame, QSystemTrayIcon, QMenu,
    QStackedWidget, QButtonGroup, QGraphicsOpacityEffect
)
from PyQt6.QtCore import (
    Qt, QSize, QPoint, QPointF, QRect, QRectF, QThread, pyqtSignal, QPropertyAnimation,
    QEasingCurve, QTimer, QAbstractAnimation
)
from PyQt6.QtGui import (
    QIcon, QFont, QFontDatabase, QColor, QPixmap, QPainter, QPen, QRegion
)

from zhuzhu_Copilot import app_identity
from zhuzhu_Copilot.utils.helpers import setup_logging, is_admin, ensure_admin, format_size, get_directory_size, safe_remove
from zhuzhu_Copilot.ui import styles   # 主题切换时按最新 PALETTE/GLOBAL_QSS 重建样式
from zhuzhu_Copilot.ui.styles import PALETTE, svg_icon
from zhuzhu_Copilot.ui.widgets import (
    Card, AppItemDelegate, DataDirDialog,
    UninstallConfirmDialog, ToastNotification, SwitchButton, ArrowComboBox
)
# DownloadDialog / AgentPanel 为懒加载：打开对应面板时才导入，
# 避免启动时加载 agent_engine→agent_llm(urllib) 等重型模块链拖慢首屏
from zhuzhu_Copilot.update_check import UpdateChecker, APP_VERSION
from zhuzhu_Copilot.core.app_scanner import AppScanner, AppInfo
from zhuzhu_Copilot.core.data_dirs import detect_data_dirs
from zhuzhu_Copilot.core.orchestrator import MigrationOrchestrator
from zhuzhu_Copilot.core.uninstaller import Uninstaller
from zhuzhu_Copilot.core.memory_optimizer import optimize_memory
from zhuzhu_Copilot.core.security import SecurityScanner, quarantine_dir
from zhuzhu_Copilot.core.network_defense import NetworkDefender
from zhuzhu_Copilot.core.execution_guard import ExecutionGuard
from zhuzhu_Copilot.core.security_engine.engine import engine as security_engine

logger = setup_logging()

# 常见应用中英文别名，用于搜索匹配（如“微信”↔weixin/wechat）
_SEARCH_ALIAS = {
    "微信": {"weixin", "wechat"},
    "qq": {"腾讯", "tim"},
    "tim": {"腾讯", "qq"},
    "腾讯": {"qq", "tim"},
    "钉钉": {"dingtalk"},
    "网易云音乐": {"netease", "cloudmusic"},
    "google chrome": {"谷歌浏览器", "chrome"},
    "chrome": {"谷歌浏览器"},
    "steam": {"蒸汽"},
    "office": {"办公"},
}

_variant_cache = {}


def _variants(name: str) -> set:
    """返回名称及其全部别名的变体集合（带缓存）"""
    n = name.lower()
    cached = _variant_cache.get(n)
    if cached is not None:
        return cached
    out = {n}
    for key, vals in _SEARCH_ALIAS.items():
        if key.lower() == n or n in {v.lower() for v in vals}:
            out.add(key.lower())
            out.update(v.lower() for v in vals)
    _variant_cache[n] = out
    return out


def _app_matches(app, text: str) -> bool:
    """搜索词匹配：原名子串或中英文别名交集"""
    if not text:
        return True
    t = text.lower()
    if t in app.name.lower():
        return True
    return bool(_variants(app.name) & _variants(t))

class ScanWorker(QThread):
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def run(self):
        try:
            scanner = AppScanner()
            apps = scanner.scan_all()
            self.finished.emit(apps)
        except Exception as e:
            self.error.emit(str(e))

class SizeWorker(QThread):
    """后台并行计算应用目录大小，避免拖慢扫描"""
    sizes_ready = pyqtSignal(dict)

    def __init__(self, apps: List[AppInfo], parent=None):
        super().__init__(parent)
        self.apps = apps

    def run(self):
        from concurrent.futures import ThreadPoolExecutor, as_completed
        sizes = {}

        def calc(app):
            try:
                return id(app), get_directory_size(app.install_location)
            except Exception:
                return id(app), 0

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(calc, a) for a in self.apps]
            batch = {}
            for fut in as_completed(futures):
                k, v = fut.result()
                sizes[k] = v
                batch[k] = v
                # 分批推送，列表大小渐进显示而非等全部算完
                if len(batch) >= 15:
                    self.sizes_ready.emit(batch)
                    batch = {}
            if batch:
                self.sizes_ready.emit(batch)

class IconLoaderWorker(QThread):
    """后台预取应用图标源文件（目录遍历 IO），主线程负责创建 QIcon"""
    icon_ready = pyqtSignal(object, str)  # AppInfo, 图标源文件路径

    def __init__(self, apps: List[AppInfo], delegate, parent=None):
        super().__init__(parent)
        self.apps = apps
        self.delegate = delegate

    def run(self):
        from concurrent.futures import ThreadPoolExecutor

        def load(app):
            try:
                return app, self.delegate.preload_icon_source(app) or ""
            except Exception:
                return app, ""

        with ThreadPoolExecutor(max_workers=8) as pool:
            for app, source in pool.map(load, self.apps):
                self.icon_ready.emit(app, source)

class MigrateWorker(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(dict)
    conflict = pyqtSignal(object, object)  # source, target：请求用户确认是否替换

    def __init__(self, app: AppInfo, target: Path, extra_dirs=None, parent=None):
        super().__init__(parent)
        self.app = app
        self.target = target
        self.extra_dirs = extra_dirs or []
        self.orchestrator = MigrationOrchestrator()
        self._conflict_answer = False
        self._conflict_event = threading.Event()

    def _on_conflict(self, source: Path, target: Path) -> bool:
        """目标已存在时发信号到主线程询问，阻塞等待用户选择"""
        self._conflict_answer = False
        self._conflict_event.clear()
        self.conflict.emit(source, target)
        self._conflict_event.wait()
        return self._conflict_answer

    def resolve_conflict(self, replace: bool):
        """主线程调用：注入用户选择并唤醒工作线程"""
        self._conflict_answer = replace
        self._conflict_event.set()

    def run(self):
        try:
            result = self.orchestrator.migrate(
                self.app, self.target, self.progress.emit,
                extra_dirs=self.extra_dirs, on_conflict=self._on_conflict,
            )
            self.finished.emit(result)
        except Exception as e:
            logger.exception("迁移异常")
            self.finished.emit({"success": False, "message": str(e)})

class BuildPlanWorker(QThread):
    """后台构建卸载清单（含注册表/快捷方式扫描），避免阻塞 UI"""
    plan_ready = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, app: AppInfo, uninstaller, parent=None):
        super().__init__(parent)
        self.app = app
        self.uninstaller = uninstaller

    def run(self):
        try:
            self.plan_ready.emit(self.uninstaller.build_plan(self.app))
        except Exception as e:
            logger.exception("构建卸载清单失败")
            self.error.emit(str(e))

class UninstallWorker(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(dict)

    def __init__(self, plan, parent=None):
        super().__init__(parent)
        self.plan = plan
        self.uninstaller = Uninstaller()

    def run(self):
        try:
            result = self.uninstaller.uninstall(self.plan, self.progress.emit)
            self.finished.emit(result)
        except Exception as e:
            logger.exception("卸载异常")
            self.finished.emit({"success": False, "message": str(e)})

class MemoryWorker(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(dict)

    def run(self):
        try:
            result = optimize_memory(self.progress.emit)
            self.finished.emit(result)
        except Exception as e:
            logger.exception("内存优化异常")
            self.finished.emit({"success": False, "message": str(e)})


class SecurityMonitorWorker(QThread):
    """常驻安全监控：定期巡检恶意进程/启动项/网络风险，自动清理后发送结果通知"""
    result = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop = threading.Event()
        self.scanner = SecurityScanner()
        self.defender = NetworkDefender()
        self.guard = ExecutionGuard()
        self.engine = security_engine

    def stop(self):
        self._stop.set()

    def run(self):
        tick = 0
        self.guard.start()  # 记录基线：防护开启前已运行的进程视为可信
        self.engine.start()  # 启动弹窗拦截等后台线程模块
        try:
            while True:
                tick += 1
                try:
                    summary = self.scanner.sweep(
                        include_network=(tick % 5 == 0),  # 每 ~50 秒检查网络
                    )
                    # 网络攻击检测（ARP 欺骗 / 洪泛），命中即自动防御并通知
                    attacks = self.defender.check()
                    if attacks["arp_spoof"] or (attacks["flood"] and attacks["flood"]["detected"]):
                        summary["attacks"] = attacks
                    # 执行防护：新启动进程的提权/格机/无文件攻击检测
                    exec_res = self.guard.check()
                    if exec_res["blocked"] or exec_res["warned"]:
                        summary["exec_guard"] = exec_res
                    # 增强引擎：蜜罐勒索 / 自启动 / DNS / 广告弹窗
                    eng = self.engine.tick()
                    if self._engine_hit(eng):
                        summary["engine"] = eng
                    if summary["killed"] or summary["removed"] or summary["failed"] \
                            or summary["network"] or summary.get("attacks") \
                            or summary.get("exec_guard") or summary.get("engine"):
                        self.result.emit(summary)
                except Exception:
                    logger.exception("安全监控异常")
                # 首次立即扫描，之后每 10 秒巡检
                if self._stop.wait(10):
                    break
        finally:
            self.engine.stop()

    @staticmethod
    def _engine_hit(eng: dict) -> bool:
        """判断增强引擎结果是否含需上报的告警"""
        rw = eng.get("ransomware") or {}
        st = eng.get("startup") or {}
        dns = eng.get("dns") or {}
        dl = eng.get("download") or []
        return bool(rw.get("detected") or st.get("suspicious")
                    or dns.get("hijacked") or dns.get("changed")
                    or eng.get("popup") or dl)


def _app_icon_path() -> str:
    """应用图标路径（打包后取 _MEIPASS/assets，开发模式取项目 assets）"""
    if getattr(sys, "frozen", False):
        return os.path.join(getattr(sys, "_MEIPASS", "."), "assets", "icon.ico")
    return str(Path(__file__).resolve().parents[3] / "assets" / "icon.ico")


# ---------- 标题栏矢量图标（淡灰简约风格，颜色取自 PALETTE，替代 emoji） ----------
_ICON_CLOSE = f"""<svg width="24" height="24" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
<path d="M7 7l10 10M17 7L7 17" stroke="{PALETTE['text_secondary']}" stroke-width="1.8"
      stroke-linecap="round"/></svg>"""
_ICON_CLOSE_HOVER = f"""<svg width="24" height="24" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
<path d="M7 7l10 10M17 7L7 17" stroke="{PALETTE['danger']}" stroke-width="1.8"
      stroke-linecap="round"/></svg>"""


def _lighten(hex_color: str, factor: int = 108) -> str:
    """颜色提亮，用于彩色按钮的悬停态"""
    return QColor(hex_color).lighter(factor).name()


# ---------- 功能图标模板（16x16 线性矢量，{C} 为颜色占位，淡灰简约风格） ----------
_ICON_SEARCH = ('<svg width="16" height="16" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
                '<circle cx="11" cy="11" r="6" stroke="{C}" stroke-width="2" fill="none"/>'
                '<path d="M20 20l-4.2-4.2" stroke="{C}" stroke-width="2" fill="none" stroke-linecap="round"/></svg>')
_ICON_REFRESH = ('<svg width="16" height="16" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
                 '<path d="M20 12a8 8 0 1 1-2.34-5.66" stroke="{C}" stroke-width="2" fill="none" stroke-linecap="round"/>'
                 '<path d="M20 4v4h-4" stroke="{C}" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>')
_ICON_UPDATE = ('<svg width="16" height="16" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
                '<path d="M12 4v10M8 10l4 4 4-4" stroke="{C}" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
                '<path d="M5 19h14" stroke="{C}" stroke-width="2" fill="none" stroke-linecap="round"/></svg>')
_ICON_MIGRATE = ('<svg width="16" height="16" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
                 '<path d="M4 12h13M12 7l5 5-5 5" stroke="{C}" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>')
_ICON_TRASH = ('<svg width="16" height="16" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
               '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" stroke="{C}" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>')
_ICON_FOLDER = ('<svg width="16" height="16" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
                '<path d="M3 6a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" stroke="{C}" stroke-width="2" fill="none" stroke-linejoin="round"/></svg>')
_ICON_SHIELD = ('<svg width="16" height="16" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
                '<path d="M12 3l7 3v6c0 4-3 7-7 9-4-2-7-5-7-9V6z" stroke="{C}" stroke-width="2" fill="none" stroke-linejoin="round"/></svg>')
_ICON_MEMORY = ('<svg width="16" height="16" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
                '<rect x="7" y="7" width="10" height="10" rx="2" stroke="{C}" stroke-width="2" fill="none"/>'
                '<path d="M10 3v4M14 3v4M10 17v4M14 17v4M3 10h4M3 14h4M17 10h4M17 14h4" stroke="{C}" stroke-width="2" stroke-linecap="round"/></svg>')
_ICON_ABOUT = ('<svg width="16" height="16" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
               '<circle cx="12" cy="12" r="9" stroke="{C}" stroke-width="2" fill="none"/>'
               '<path d="M9.5 9a2.5 2.5 0 1 1 3.4 2.6c-.7.3-1.4 1-1.4 1.9V15" stroke="{C}" stroke-width="2" fill="none" stroke-linecap="round"/></svg>')
_ICON_LINK = ('<svg width="16" height="16" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
              '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" stroke="{C}" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
              '<path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" stroke="{C}" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>')
_ICON_SETTINGS = ('<svg width="16" height="16" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
                  '<circle cx="12" cy="12" r="3" stroke="{C}" stroke-width="2" fill="none"/>'
                  '<path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M19.1 4.9L17 7M7 17l-2.1 2.1" stroke="{C}" stroke-width="2" stroke-linecap="round"/></svg>')

# ---------- 统一控件样式（颜色取自 PALETTE，无硬编码；随主题就地重建） ----------
def _regen_qss():
    """按当前 PALETTE 重建模块级派生样式常量（主题切换时调用，避免残留旧主题色）"""
    global _BTN_PRIMARY_QSS, _BTN_DANGER_QSS, _BTN_GHOST_QSS, _BTN_TOOL_QSS
    global _SEARCH_QSS, _SECTION_TITLE, _BADGE_QSS, _FIELD_LABEL
    _BTN_PRIMARY_QSS = (f"QPushButton {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                        f" stop:0 {PALETTE['primary_hover']}, stop:1 {PALETTE['primary']});"
                        " color: #FFFFFF; border: none;"
                        " border-radius: 10px; padding: 0 18px; font-size: 13px; font-weight: 700; }"
                        f"QPushButton:hover {{ background: {PALETTE['primary_hover']}; }}"
                        f"QPushButton:pressed {{ background: {PALETTE['primary_pressed']}; }}"
                        f"QPushButton:disabled {{ background: {PALETTE['border']}; color: {PALETTE['text_secondary']}; }}")
    _BTN_DANGER_QSS = (f"QPushButton {{ background: {PALETTE['danger']}; color: #FFFFFF; border: none;"
                       " border-radius: 10px; padding: 0 18px; font-size: 13px; font-weight: 700; }"
                       f"QPushButton:hover {{ background: {_lighten(PALETTE['danger'])}; }}"
                       f"QPushButton:pressed {{ background: {PALETTE['danger']}; }}"
                       f"QPushButton:disabled {{ background: {PALETTE['border']}; color: {PALETTE['text_secondary']}; }}")
    _BTN_GHOST_QSS = (f"QPushButton {{ background: {PALETTE['card']}; color: {PALETTE['text']};"
                      f" border: 1px solid {PALETTE['border']}; border-radius: 10px; padding: 0 14px;"
                      " font-size: 13px; font-weight: 600; }"
                      f"QPushButton:hover {{ border-color: {PALETTE['primary_hover']}; color: {PALETTE['text']};"
                      f" background: {PALETTE['hover']}; }}"
                      f"QPushButton:pressed {{ background: {PALETTE['primary_light']}; }}"
                      f"QPushButton:disabled {{ color: {PALETTE['text_secondary']}; }}")
    _BTN_TOOL_QSS = (f"QPushButton {{ background: transparent; color: {PALETTE['text_secondary']};"
                     f" border: 1px solid {PALETTE['border']}; border-radius: 8px; padding: 0 12px;"
                     " font-size: 12px; font-weight: 600; }"
                     f"QPushButton:hover {{ color: {PALETTE['text']}; border-color: {PALETTE['primary_hover']};"
                     f" background: {PALETTE['hover']}; }}"
                     f"QPushButton:pressed {{ background: {PALETTE['primary_light']}; color: {PALETTE['text']}; }}")
    _SEARCH_QSS = (f"QLineEdit {{ background: {PALETTE['bg_bottom']}; color: {PALETTE['text']};"
                   f" border: 1px solid {PALETTE['border']}; border-radius: 10px; padding: 8px 12px;"
                   " font-size: 13px; }"
                   f"QLineEdit:hover {{ border: 1px solid {PALETTE['text_secondary']}; }}"
                   f"QLineEdit:focus {{ border: 1px solid {PALETTE['primary_hover']}; }}")
    _SECTION_TITLE = f"font-size: 11px; font-weight: 700; color: {PALETTE['text_secondary']};"
    _BADGE_QSS = (f"font-size: 11px; font-weight: 700; color: {PALETTE['text']};"
                  f" background: {PALETTE['primary_light']}; border-radius: 9px; padding: 2px 9px;")
    _FIELD_LABEL = f"font-size: 12px; font-weight: 600; color: {PALETTE['text_secondary']};"


_regen_qss()   # 模块加载即按当前 PALETTE 生成（启动后 _apply_theme 会按主题重建）


def _btn_qss(kind: str) -> str:
    """取某类按钮的当前 QSS 字符串（_icon_button 与 _apply_theme 共用）"""
    if kind == "primary":
        return _BTN_PRIMARY_QSS
    if kind == "danger":
        return _BTN_DANGER_QSS
    if kind == "tool":
        return _BTN_TOOL_QSS
    return _BTN_GHOST_QSS


def _tab_qss() -> str:
    """面板顶部分段控件样式（应用 / 防护 / 通用 / 工具）：胶囊式分段控件，
    选中项为深蓝圆角块白字，未选中透明。每次调用按当前 PALETTE 现算，主题切换后重建生效。"""
    return (f"QPushButton {{ background: transparent; color: {PALETTE['text_secondary']};"
            " border: none; border-radius: 13px; padding: 0 18px;"
            " font-size: 12px; font-weight: 600; }"
            f"QPushButton:hover {{ color: {PALETTE['text']}; background: {PALETTE['hover']}; }}"
            f"QPushButton:checked {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            f" stop:0 {PALETTE['primary_hover']}, stop:1 {PALETTE['primary']}); color: #FFFFFF; }}")


def _tab_wrap_qss() -> str:
    """分段控件容器：卡片底 + 细描边的胶囊槽，让四个页签视觉上成为一体"""
    return (f"QWidget#tabWrap {{ background: {PALETTE['card']};"
            f" border: 1px solid {PALETTE['border']}; border-radius: 17px; }}")


def _icon_button(text: str, svg_tpl: str, kind: str = "ghost", height: int = 40) -> QPushButton:
    """图标+文字按钮：ghost=描边 / primary=深蓝 / danger=危险 / tool=顶栏工具"""
    color = "#FFFFFF" if kind in ("primary", "danger") else PALETTE["text_secondary"]
    btn = QPushButton(text)
    btn.setIcon(svg_icon(svg_tpl.format(C=color)))
    btn.setIconSize(QSize(16, 16))
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setMinimumHeight(height)
    btn._btn_kind = kind   # 主题重刷时按类型重建 QSS
    if kind == "primary":
        btn.setStyleSheet(_BTN_PRIMARY_QSS)
    elif kind == "danger":
        btn.setStyleSheet(_BTN_DANGER_QSS)
    elif kind == "tool":
        btn.setStyleSheet(_BTN_TOOL_QSS)
    else:
        btn.setStyleSheet(_BTN_GHOST_QSS)
    return btn


class _Divider(QFrame):
    """1px 主题分隔线。

    QFrame 不会跟着 PALETTE 自动换色 —— 底色是构建期内联进 QSS 的，
    主题切换时控件不重建 → 深色下会残留一条浅灰线。故提供 apply_theme()，
    由 CopilotPanel._apply_theme 统一刷新（与 Card 同一套约定）。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(1)
        self.apply_theme()

    def apply_theme(self):
        self.setStyleSheet(f"background: {PALETTE['border']}; border: none;")


class _IconTitleButton(QPushButton):
    """标题栏矢量图标按钮：悬停切换高亮色，无 emoji"""

    def __init__(self, svg: str, svg_hover: str, parent=None):
        super().__init__(parent)
        self._icon = svg_icon(svg)
        self._icon_hover = svg_icon(svg_hover)
        self.setIcon(self._icon)
        self.setFixedSize(32, 32)
        self.setIconSize(QSize(14, 14))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            "QPushButton { background: transparent; border: none; border-radius: 6px; }"
            f"QPushButton:hover {{ background: {PALETTE['hover']}; }}"
        )

    def enterEvent(self, e):
        self.setIcon(self._icon_hover)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.setIcon(self._icon)
        super().leaveEvent(e)


class _ParticleRevealOverlay(QWidget):
    """「整个窗口由粒子自上而下生成」的动画层（60fps）。

    关键：窗口本身并不先出现 —— 宿主每帧按进度重设窗口掩码，**未生成的部分真正透明**
    （透出桌面），因此面板底色、边框、标题、分组与内容是一起被"生成"出来的。
    本层负责在生成前沿附近绘制粒子：粒子自前沿上方凝聚、向下飘落，抵达前沿即被掩码
    裁掉，形成"界面由粒子凝聚而成"的观感。全程不拦截鼠标事件。"""

    DURATION_MS = 420      # 生成推进总时长
    TAIL_MS = 420          # 前沿到底后残留粒子的最长淡出时间（兜底：保证动画必然结束）
    FRAME_MS = 16          # 60fps：配合 PreciseTimer + 单调时钟插值，掉帧也不改变总时长
    BAND_H = 34            # 前沿上方粒子带高度（px）
    MAX_PARTS = 130        # 同屏粒子上限（性能护栏）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._progress = 1.0    # 原始进度 0→1
        self._grid = 1.0        # 缓动后的生成前沿位置（窗口掩码与粒子共用同一进度）
        self._parts: List[dict] = []
        self._t0 = 0.0
        self._last_elapsed = 0.0   # 上一帧耗时（ms）：粒子位移/衰减按它缩放，保证帧率无关
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)   # 默认 ±5% 精度会拖成 ~20ms
        self._timer.setInterval(self.FRAME_MS)
        self._timer.timeout.connect(self._tick)
        self.hide()

    @staticmethod
    def _ease(p: float) -> float:
        """轻微缓出（1-(1-p)^2）：起手匀速生成、末端稍缓，避免"戛然而止" """
        p = max(0.0, min(1.0, float(p)))
        return 1.0 - (1.0 - p) ** 2

    def _particle_color(self, idx: int) -> str:
        """粒子以淡灰为主、少量深蓝提亮（符合纯黑+淡灰+白+深蓝的配色约束）"""
        return PALETTE["primary"] if idx % 5 == 0 else PALETTE["text_secondary"]

    # ---------- 生命周期 ----------
    def start(self):
        """开始（或重放）生成动画"""
        self._progress = 0.0
        self._grid = 0.0
        self._parts = []
        self._t0 = time.monotonic()
        self._last_elapsed = 0.0
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect())
        self.show()
        self.raise_()
        self.update()
        self._timer.start()

    def stop_and_hide(self):
        """立即结束：必须通知宿主撤销窗口掩码，否则窗口会停在残缺形状上"""
        self._timer.stop()
        self._parts = []
        self._progress = 1.0
        self._grid = 1.0
        self.hide()
        parent = self.parentWidget()
        if parent is not None and hasattr(parent, "finish_reveal"):
            try:
                parent.finish_reveal()
            except Exception:
                pass

    def is_running(self) -> bool:
        return self._timer.isActive()

    # ---------- 帧更新（60fps） ----------
    def _tick(self):
        try:
            elapsed = (time.monotonic() - self._t0) * 1000.0
            dt = max(0.0, min(200.0, elapsed - self._last_elapsed))
            self._last_elapsed = elapsed
            self._progress = min(1.0, elapsed / float(self.DURATION_MS))
            self._grid = self._ease(self._progress)
            parent = self.parentWidget()
            if parent is not None and hasattr(parent, "apply_reveal"):
                parent.apply_reveal(self._grid)     # 窗口整体逐层"长"出来
            if self._progress < 1.0:
                self._spawn(1.0)
            self._advance(dt)
            self.update()
            # 收尾：前沿到底且粒子散尽；若粒子因掉帧滞留，超过 TAIL_MS 也强制结束
            if self._progress >= 1.0:
                if not self._parts or (elapsed - self.DURATION_MS) >= self.TAIL_MS:
                    self.stop_and_hide()
        except Exception:
            self.stop_and_hide()

    def _spawn(self, factor: float):
        """在前沿上方补粒子；factor > 1 用于首帧铺垫"""
        import random
        frontier = self.height() * self._grid
        for _ in range(int(6 * factor)):
            if len(self._parts) >= self.MAX_PARTS:
                return
            idx = len(self._parts)
            self._parts.append({
                "x": random.uniform(0.0, max(1.0, float(self.width()))),
                "y": frontier - random.uniform(2.0, self.BAND_H),
                "vy": random.uniform(0.8, 2.6),          # 向下飘落
                "size": random.uniform(0.8, 2.2),
                "alpha": random.uniform(0.25, 0.8),
                "decay": random.uniform(0.02, 0.05),
                "color": self._particle_color(idx),
            })

    def _advance(self, dt_ms: float):
        """粒子下落 + 淡出；越过生成前沿即被掩码裁掉（此处顺手回收）。

        位移与衰减都按真实帧间隔缩放 —— 否则掉帧时粒子寿命会被拉长
        （曾导致满载测试下"粒子迟迟不散、动画不结束"的帧率相关隐性缺陷）。"""
        frontier = self.height() * self._grid
        k = max(0.25, min(8.0, dt_ms / float(self.FRAME_MS)))
        alive = []
        for q in self._parts:
            q["y"] += q["vy"] * k
            q["alpha"] -= q["decay"] * k
            if q["alpha"] > 0.02 and q["y"] < frontier + 2:
                alive.append(q)
        self._parts = alive

    def paintEvent(self, _e):
        """自绘生成前沿与粒子。未生成区域已由窗口掩码裁掉，这里无需再覆盖遮罩。
        异常一律吞掉（Qt 虚函数内未捕获异常会 abort 进程）。"""
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            w = float(self.width())
            frontier = float(self.height()) * self._grid
            if self._progress < 1.0:
                pen = QPen(QColor(PALETTE["text_secondary"]))
                pen.setWidthF(1.0)
                p.setPen(pen)
                y = max(0.0, frontier - 0.5)
                p.drawLine(QPointF(0.0, y), QPointF(w, y))
            p.setPen(Qt.PenStyle.NoPen)
            for q in self._parts:
                c = QColor(q["color"])
                c.setAlphaF(max(0.0, min(1.0, q["alpha"])))
                p.setBrush(c)
                p.drawEllipse(QPointF(q["x"], q["y"]), q["size"], q["size"])
        except Exception:
            pass
        finally:
            try:
                p.end()
            except Exception:
                pass


class CopilotPanel(QFrame):
    """zhuzhu Copilot 紧凑面板：应用迁移 / 卸载 + 安全防护 + 通用设置 + 工具入口。

    形态为「浮层卡片」——无系统标题栏、不占任务栏、不自行管理显隐：由 AI 面板
    （AgentPanel）在顶栏按钮下方丝滑弹出并负责定位与收起。本类只负责内容与业务，
    不再承担窗口拖动、主窗口隐藏等职责。四个分组由顶部分段控件切换：
    应用 / 防护 / 通用 / 工具。"""

    open_agent_requested = pyqtSignal()   # 托盘等外部入口 → 请求显示 AI 面板（由 AgentPanel 处理）
    close_requested = pyqtSignal()        # 头部关闭按钮 → 请求收起本浮层
    theme_changed = pyqtSignal()          # 通用页切换主题 → 通知 AI 面板就地重建

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("copilotPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumSize(760, 520)
        # 无边框工具窗口：不带系统标题栏/边框、不占任务栏 —— 浮层形态的前提
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        # 浮层动画状态（由 AI 面板通过 popup_animated / close_animated 驱动）
        self._eff = None
        self._anims = []
        self._closing = False
        # 生成动画进行中：抑制圆角区域设置（与窗口掩码互斥）
        self._reveal_pending = False
        # 内容「整体由粒子自上而下生成」动画层：覆盖整块浮层，动画结束自动隐藏
        self._reveal = _ParticleRevealOverlay(self)
        # 生成前沿的"粒子侵蚀"分块（相对前沿的偏移；每次弹出随机一次、全程稳定，避免抖动）
        self._edge: List[tuple] = []

        # 与 AI 面板共用 agent_theme 设置：构建前先按当前主题切换色板，
        # 使 _build_* 内所有 PALETTE 取值与 GLOBAL_QSS 立即反映浅/深主题
        styles.set_palette(self._current_theme())
        _regen_qss()

        self.apps: List[AppInfo] = []
        self.selected_app: AppInfo = None
        self.uninstaller = Uninstaller()

        # 记忆用户选择：是否显示安全提醒弹窗（默认开启）。须在 _setup_ui 之前初始化（_build_security_page 会读取）
        self._settings = app_identity.qsettings()
        self._toast_enabled = self._settings.value("security_toast", True, type=bool)

        self._setup_ui()
        # 修复原生 QMessageBox 按钮：palette ButtonText=白色 + Windows 原生 hover 白底 → 白字被覆盖。
        # 显式 QSS 接管按钮绘制，固定 蓝底白字/hover 深蓝。所有弹窗 parent 均为 self，会继承该样式。
        self.setStyleSheet(self._panel_sheet())
        # 迁移进度平滑动画
        self.progress_anim = QPropertyAnimation(self.progress, b"value", self)
        self.progress_anim.setDuration(350)
        self.progress_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._security_on = False
        self.security_worker = None
        # 右下角自定义滑出弹窗（拦截结果/状态提示，非系统通知）
        self.toast = ToastNotification(None)
        self._setup_tray()
        self._check_admin()
        # 桌宠随程序启动（资源缺失时静默跳过）
        try:
            from zhuzhu_Copilot.ui.desktop_pet import ensure_pet
            self._pet = ensure_pet(self)
        except Exception:
            self._pet = None
        # 记忆上次选择：开启过防护则下次启动自动开启
        if self._settings.value("security_auto", False, type=bool) and is_admin():
            QTimer.singleShot(0, self._start_security)
        self._start_scan()
        self._init_update_check()
        # 启动就绪后按当前主题统一落地一次（同步 app 级 QPalette/控件样式）
        QTimer.singleShot(0, self._apply_theme)

    def _open_agent_panel(self, *_):
        """兼容既有调用方（桌宠点击、托盘）：

        本面板已不再拥有 AI 面板，改为发出请求信号，由 AgentPanel 负责显示。"""
        self.open_agent_requested.emit()

    def shutdown(self):
        """应用退出前清理：停防护线程、关桌宠与托盘（浮层显隐由 AgentPanel 管理，不在此关闭窗口）"""
        if self.security_worker and self.security_worker.isRunning():
            self.security_worker.stop()
            if not self.security_worker.wait(3000):
                self.security_worker.terminate()
        if hasattr(self, "tray"):
            self.tray.hide()
        pet = getattr(self, "_pet", None)
        if pet is not None:
            try:
                pet.close()
            except Exception:
                pass
            self._pet = None

    # ---------- 主题（浅色/深色，与 AI 面板共用 agent_theme 设置） ----------
    def _current_theme(self) -> str:
        """读取与 AI 面板一致的主题：dark / light / auto（按时间解析）。"""
        try:
            from zhuzhu_Copilot.ui import agent_panel
            return agent_panel._resolve_theme()
        except Exception:
            try:
                v = str(app_identity.qsettings()
                        .value("agent_theme", "light"))
                return v if v in ("dark", "light") else "light"
            except Exception:
                return "light"

    def _brand_qss(self) -> str:
        """品牌标识：深蓝渐变圆角方块 + 白色首字符（随主题重建）"""
        return (f"background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
                f" stop:0 {PALETTE['primary_hover']}, stop:1 {PALETTE['primary_pressed']});"
                " color: #FFFFFF; font-size: 13px; font-weight: 800; border-radius: 7px;")

    def _msg_box_qss(self) -> str:
        """QMessageBox 等原生弹窗样式（随主题色板重建）"""
        return (
            f"QMessageBox {{ background-color: {PALETTE['card']}; }}"
            f"QMessageBox QLabel {{ color: {PALETTE['text']}; font-size: 13px; }}"
            f"QMessageBox QPushButton {{ background-color: {PALETTE['primary']};"
            "color: #FFFFFF; border: none; border-radius: 8px;"
            "padding: 7px 20px; min-width: 72px; font-weight: 600; }"
            f"QMessageBox QPushButton:hover {{ background-color: {PALETTE['primary_hover']}; }}"
            f"QMessageBox QPushButton:pressed {{ background-color: {PALETTE['primary_pressed']}; }}")

    def _panel_sheet(self) -> str:
        """浮层根样式：无边框窗口的圆角底 + 原生弹窗按钮配色（合并，避免互相覆盖）"""
        return (f"QWidget#copilotPanel {{ background: {PALETTE['bg_bottom']};"
                f" border: 1px solid {PALETTE['border']}; border-radius: 16px; }}"
                + self._msg_box_qss())

    def _apply_round(self):
        """无边框窗口的圆角裁剪（Win11 DWM 优先，降级 SetWindowRgn）；失败静默不影响功能。
        注意：SetWindowRgn 与 setMask 是同一套窗口区域机制，生成动画期间必须让位给掩码。"""
        try:
            from zhuzhu_Copilot.core import agent_ui_ux
            agent_ui_ux.apply_rounded_window(self, self.ROUND_RADIUS)
        except Exception:
            pass

    # ---------- 窗口整体「粒子生成」：按进度裁剪窗口 ----------
    def _prepare_reveal_edge(self):
        """为前沿生成一组稳定的"粒子侵蚀"分块（偏移随机一次、全程复用）：
        让生成边界呈颗粒状而不是一条光滑直线。"""
        import random
        self._edge = [(random.random(), random.randint(-12, 4), random.randint(2, 5))
                      for _ in range(30)]

    def apply_reveal(self, progress: float):
        """按生成进度设置窗口掩码：未生成的部分**真正透明**（透出桌面）。

        这是"整个面板由粒子生成"的关键 —— 面板底色、边框、标题、分组与内容同属一个窗口，
        它们随前沿自上而下一并被"长"出来，而不是先出现一个空窗口再补内容。"""
        try:
            w = max(1, int(self.width()))
            h = max(1, int(self.height()))
            p = max(0.0, min(1.0, float(progress)))
            frontier = max(1, int(h * p))
            region = QRegion(0, 0, w, frontier)
            for dx, dy, size in self._edge:
                y = frontier + dy
                if y < 0 or y >= h:
                    continue
                x = max(0, min(w - size, int(dx * w)))
                region = region.united(QRegion(x, y, size, size))
            self.setMask(region)
        except Exception:
            pass

    def finish_reveal(self):
        """生成结束（或被打断）：撤销窗口掩码并恢复圆角外观"""
        self._reveal_pending = False
        try:
            self.clearMask()
        except Exception:
            pass
        self._apply_round()

    def showEvent(self, event):
        super().showEvent(event)
        # 生成动画进行中不得套圆角区域（会覆盖掩码，让窗口整块提前出现）
        if not self._reveal_pending:
            self._apply_round()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 生成动画层始终铺满浮层
        reveal = getattr(self, "_reveal", None)
        if reveal is not None and reveal.isVisible():
            reveal.setGeometry(self.rect())
        if not self._reveal_pending:
            self._apply_round()

    def _content_gradient_qss(self) -> str:
        return (f"QWidget#mainContent {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
                f" stop:0 {PALETTE['bg_top']}, stop:1 {PALETTE['bg_bottom']}); }}")

    def _apply_theme(self):
        """按当前主题（与 AI 面板同步）重刷主窗口全部样式与 app 级 QPalette。
        由启动就绪、AI 面板主题切换（AgentPanel._retheme）两处触发。"""
        try:
            from PyQt6.QtWidgets import QLabel, QPushButton
            styles.set_palette(self._current_theme())
            _regen_qss()
            app = QApplication.instance()
            if app is not None:
                styles.apply_palette(app)
            # 窗口级 QSS（圆角底 + 弹窗按钮配色）+ 主内容整页基础样式
            self.setStyleSheet(self._panel_sheet())
            content = getattr(self, "_main_content", None)
            if content is not None:
                content.setStyleSheet(styles.GLOBAL_QSS + self._content_gradient_qss())
            # 头部（浮层内标题行：透明背景，无底边框）
            bar = getattr(self, "_title_bar", None)
            if bar is not None:
                bar.setStyleSheet("background: transparent; border: none;")
            # 分组切换按钮（随主题重建选中态配色）+ 胶囊槽容器
            if getattr(self, "_tab_wrap", None) is not None:
                self._tab_wrap.setStyleSheet(_tab_wrap_qss())
            tabs = getattr(self, "_tab_group", None)
            if tabs is not None:
                for _tb in tabs.buttons():
                    _tb.setStyleSheet(_tab_qss())
            if getattr(self, "_title_lbl", None) is not None:
                self._title_lbl.setStyleSheet(
                    f"font-size: 15px; font-weight: 700; color: {PALETTE['text']};")
            if getattr(self, "_brand_lbl", None) is not None:
                self._brand_lbl.setStyleSheet(self._brand_qss())
            if getattr(self, "_ver_lbl", None) is not None:
                self._ver_lbl.setStyleSheet(
                    f"font-size: 11px; color: {PALETTE['text_secondary']};")
            # 卡片底 / 分隔线：两者都在页面构建期建好、QSS 里内联了主题色，主题切换
            # 不会重建控件 → 必须按新色逐个重刷（用户反馈的「深色下卡片仍是浅色」就是
            # 漏了这一步）。约定：自带 apply_theme() 的控件由这里统一刷新，
            # 将来新增同类控件只要实现该方法即可自动受益。
            for _w in self.findChildren(QWidget):
                _fn = getattr(_w, "apply_theme", None)
                if callable(_fn):
                    try:
                        _fn()
                    except Exception:
                        pass
            # 右下角通知弹窗是**独立顶层窗口**（无 parent），上面的控件树遍历到不了它，
            # 需单独刷新，否则切深色后它仍是浅色卡片。
            _toast = getattr(self, "toast", None)
            if _toast is not None:
                try:
                    _toast.apply_theme()
                except Exception:
                    pass
            # 搜索框
            if hasattr(self, "search_edit"):
                self.search_edit.setStyleSheet(_SEARCH_QSS)
            # 按钮（_icon_button created，_btn_kind 标记类型）
            for _b in self.findChildren(QPushButton):
                _kind = getattr(_b, "_btn_kind", None)
                if _kind:
                    _b.setStyleSheet(_btn_qss(_kind))
            for _b in self.findChildren(_IconTitleButton):
                _b.setStyleSheet(
                    "QPushButton { background: transparent; border: none; border-radius: 6px; }"
                    f"QPushButton:hover {{ background: {PALETTE['hover']}; }}")
            # 标签（section 标题 / 提示文案）
            for _lbl in self.findChildren(QLabel):
                if getattr(_lbl, "_section", False):
                    _lbl.setStyleSheet(_SECTION_TITLE)
                elif getattr(_lbl, "_hint", False):
                    _lbl.setStyleSheet(
                        f"font-size: 12px; color: {PALETTE['text_secondary']};")
            # SwitchButton 绘制实时读 PALETTE，仅需重绘
            for _sw in self.findChildren(SwitchButton):
                try:
                    _sw.update()
                except Exception:
                    pass
            # 状态/管理员标签与详情区按当前选中内容重建
            if getattr(self, "status_label", None) is not None:
                self.status_label.setStyleSheet(
                    f"color: {PALETTE['text_secondary']}; font-size: 12px;")
            _ok = is_admin()
            _dot = PALETTE['success'] if _ok else PALETTE['danger']
            if getattr(self, "_admin_dot", None) is not None:
                self._admin_dot.setStyleSheet(f"background: {_dot}; border-radius: 4px;")
            if getattr(self, "admin_label", None) is not None:
                self.admin_label.setStyleSheet(f"font-size: 12px; color: {_dot};")
            if getattr(self, "selected_app", None) is not None:
                self._show_app_info(self.selected_app)
            else:
                self._show_empty_info()
        except Exception:
            pass

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 14)
        root.setSpacing(10)
        root.addWidget(self._build_title_bar())

        content = QWidget()
        content.setObjectName("mainContent")
        content.setStyleSheet(styles.GLOBAL_QSS + self._content_gradient_qss())
        self._main_content = content
        cl = QVBoxLayout(content)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(10)
        cl.addWidget(self._build_tabs())

        self._pages = QStackedWidget()
        self._pages.addWidget(self._build_app_page())
        self._pages.addWidget(self._build_security_page())
        self._pages.addWidget(self._build_general_page())
        self._pages.addWidget(self._build_tools_page())
        cl.addWidget(self._pages, 1)
        root.addWidget(content, 1)

    # ---------- 顶栏（引用保存供 _apply_theme 主题重刷） ----------
    def _build_title_bar(self):
        bar = QWidget()
        bar.setFixedHeight(40)
        self._title_bar = bar
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(2, 0, 0, 0)
        layout.setSpacing(10)

        # 品牌渐变方块标识（深蓝渐变 + 首字符），替代光秃秃的文字标题
        brand = QLabel("z")
        brand.setFixedSize(22, 22)
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._brand_lbl = brand
        brand.setStyleSheet(self._brand_qss())
        layout.addWidget(brand)

        title = QLabel("zhuzhu Copilot")
        title.setStyleSheet(f"font-size: 15px; font-weight: 700; color: {PALETTE['text']};")
        self._title_lbl = title
        layout.addWidget(title)

        ver = QLabel(f"v{APP_VERSION}")
        ver.setStyleSheet(f"font-size: 11px; color: {PALETTE['text_secondary']};")
        self._ver_lbl = ver
        layout.addWidget(ver)

        layout.addStretch(1)

        # 管理员状态（色点 + 文字，无背景块）
        admin_box = QWidget()
        admin_lay = QHBoxLayout(admin_box)
        admin_lay.setContentsMargins(0, 0, 0, 0)
        admin_lay.setSpacing(6)
        self._admin_dot = QLabel()
        self._admin_dot.setFixedSize(8, 8)
        admin_lay.addWidget(self._admin_dot)
        self.admin_label = QLabel("未提权")
        self.admin_label.setStyleSheet(f"font-size: 12px; color: {PALETTE['text_secondary']};")
        admin_lay.addWidget(self.admin_label)
        layout.addWidget(admin_box)

        close_btn = _IconTitleButton(_ICON_CLOSE, _ICON_CLOSE_HOVER)
        close_btn.setToolTip("收起面板")
        # 包一层 lambda：clicked 会带 bool 参数，而 close_requested 无参
        close_btn.clicked.connect(lambda *_: self.close_requested.emit())
        layout.addWidget(close_btn)
        return bar

    # ---------- 分组切换：应用 / 防护 / 通用 / 工具 ----------
    def _build_tabs(self) -> QWidget:
        wrap = QWidget()
        wrap.setObjectName("tabWrap")
        wrap.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        wrap.setStyleSheet(_tab_wrap_qss())
        wrap.setFixedHeight(34)
        self._tab_wrap = wrap
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self._tab_group = QButtonGroup(self)
        self._tab_group.setExclusive(True)
        for idx, name in enumerate(("应用", "防护", "通用", "工具")):
            b = QPushButton(name)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setMinimumHeight(26)
            b.setStyleSheet(_tab_qss())
            self._tab_group.addButton(b, idx)
            lay.addWidget(b)
        lay.addStretch(1)
        self._tab_group.idClicked.connect(self._on_tab_changed)
        self._tab_group.button(0).setChecked(True)
        return wrap

    def _on_tab_changed(self, idx: int):
        pages = getattr(self, "_pages", None)
        if pages is not None:
            pages.setCurrentIndex(int(idx))

    # ---------- 浮层动画（由 AI 面板调用） ----------
    OUT_MS = 120        # 收起时长（略快于弹出，手感收得住）
    SLIDE = 8           # 收起时的下移量（弹出不做位移，改为整体粒子生成）
    ROUND_RADIUS = 16   # 无边框窗口圆角（px）

    def _ensure_effect(self):
        if self._eff is None:
            self._eff = QGraphicsOpacityEffect(self)
            self._eff.setOpacity(1.0)
            self.setGraphicsEffect(self._eff)
        return self._eff

    def animation_running(self) -> bool:
        alive = []
        for a in self._anims:
            try:
                if a.state() != QAbstractAnimation.State.Stopped:
                    alive.append(a)
            except RuntimeError:
                continue   # 底层对象已销毁 → 丢弃
        self._anims = alive
        return bool(alive)

    def is_closing(self) -> bool:
        """是否正在收起动画中（宿主据此视为"未打开"，允许立即重开）"""
        return bool(self._closing)

    def _stop_anims(self):
        """停止进行中的动画并复位：反复开合时避免上一次的几何/透明度动画抢坐标，
        也避免 _closing 卡在 True 导致后续收起静默失效（表现为"点按钮没反应"）。"""
        for a in list(self._anims):
            try:
                a.stop()
            except Exception:
                pass
        self._anims = []
        self._closing = False
        if self._eff is not None:
            self._eff.setOpacity(1.0)

    def popup_animated(self, rect: QRect):
        """弹出：整个窗口（底色 / 边框 / 标题 / 内容）由粒子自上而下生成。

        不做窗口级淡入 —— 那会先出现一个空窗口再补内容。改为起始掩码为空（窗口尚不存在），
        随后由粒子层每帧推进掩码，未生成部分保持真透明。"""
        self._stop_anims()
        reveal = getattr(self, "_reveal", None)
        if reveal is not None:
            reveal.stop_and_hide()          # 幂等：同时撤销上一轮残留掩码
        self._prepare_reveal_edge()
        self._reveal_pending = True         # 抑制 show/resize 里的圆角区域（会覆盖掩码）
        self.setGeometry(rect)
        try:
            self.setMask(QRegion(0, 0, 0, 0))   # 起手：窗口尚未生成
        except Exception:
            pass
        self.show()
        self.raise_()
        if reveal is not None:
            reveal.setGeometry(self.rect())
            reveal.start()
        else:
            self.finish_reveal()

    def close_animated(self):
        """收起：透明度 1→0 同时轻微下移，动画结束后隐藏（可再次弹出复用本控件）"""
        reveal = getattr(self, "_reveal", None)
        if reveal is not None:
            reveal.stop_and_hide()
        if not self.isVisible() or self._closing:
            return
        self._closing = True
        eff = self._ensure_effect()
        cur = self.geometry()
        a_op = QPropertyAnimation(eff, b"opacity", self)
        a_op.setDuration(self.OUT_MS)
        a_op.setStartValue(eff.opacity())
        a_op.setEndValue(0.0)
        a_op.setEasingCurve(QEasingCurve.Type.InCubic)
        a_geo = QPropertyAnimation(self, b"geometry", self)
        a_geo.setDuration(self.OUT_MS)
        a_geo.setStartValue(cur)
        a_geo.setEndValue(QRect(cur.x(), cur.y() + self.SLIDE // 2,
                                cur.width(), cur.height()))
        a_geo.setEasingCurve(QEasingCurve.Type.InCubic)
        a_op.finished.connect(self._after_close)
        self._run_anim(a_op)
        self._run_anim(a_geo)

    def _run_anim(self, anim):
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        self._anims.append(anim)

    def _after_close(self):
        self._closing = False
        self.hide()

    # ---------- 应用页：左「应用列表」+ 右「详情 / 迁移设置 / 进度 / 操作」 ----------
    def _build_app_page(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(self._build_left_panel(), 3)

        # 右栏独立滚动，浮层较矮时可滚动查看
        right_box = QWidget()
        rb = QVBoxLayout(right_box)
        rb.setContentsMargins(0, 0, 0, 0)
        rb.setSpacing(0)
        rb.addWidget(self._build_right_panel())
        rb.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(right_box)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollArea > QWidget#qt_scrollarea_viewport { background: transparent; }")
        layout.addWidget(scroll, 4)
        return page

    # ---------- 左栏：应用 ----------
    def _build_left_panel(self):
        card = Card()
        self._left_card = card
        layout = card.layout

        head = QHBoxLayout()
        head.setSpacing(8)
        t = QLabel("应用")
        t._section = True
        t.setStyleSheet(_SECTION_TITLE)
        head.addWidget(t)
        self._app_count = QLabel("0")
        self._app_count.setStyleSheet(_BADGE_QSS)
        head.addWidget(self._app_count)
        head.addStretch(1)
        layout.addLayout(head)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索应用...")
        self.search_edit.setStyleSheet(_SEARCH_QSS)
        self.search_edit.addAction(
            svg_icon(_ICON_SEARCH.format(C=PALETTE["text_secondary"])),
            QLineEdit.ActionPosition.LeadingPosition)
        self.search_edit.textChanged.connect(self._filter_apps)
        layout.addWidget(self.search_edit)

        self.app_list = QListWidget()
        self.app_list.setSpacing(2)
        self.app_list.setItemDelegate(AppItemDelegate(self.app_list))
        self.app_list.setMouseTracking(True)
        self.app_list.itemClicked.connect(self._on_app_selected)
        layout.addWidget(self.app_list, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.status_label = QLabel("就绪")
        self.status_label.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 12px;")
        bottom.addWidget(self.status_label)
        bottom.addStretch(1)

        self.refresh_btn = _icon_button("重新扫描", _ICON_REFRESH, kind="tool", height=32)
        self.refresh_btn.clicked.connect(self._start_scan)
        bottom.addWidget(self.refresh_btn)

        self.update_btn = _icon_button("检查更新", _ICON_UPDATE, kind="tool", height=32)
        self.update_btn.clicked.connect(self._on_check_update_clicked)
        bottom.addWidget(self.update_btn)
        layout.addLayout(bottom)

        # 扫描进行中的忙碌动画条
        self.scan_progress = QProgressBar()
        self.scan_progress.setRange(0, 0)
        self.scan_progress.setFixedHeight(4)
        self.scan_progress.setTextVisible(False)
        self.scan_progress.hide()
        layout.addWidget(self.scan_progress)

        return card

    # ---------- 应用页右栏：应用详情 + 迁移设置 + 进度 + 操作 ----------
    def _build_right_panel(self):
        card = Card()
        self._right_card = card
        layout = card.layout

        layout.addWidget(self._section_label("应用详情"))
        self._info_box = QWidget()
        self._info_lay = QVBoxLayout(self._info_box)
        self._info_lay.setContentsMargins(0, 0, 0, 0)
        self._info_lay.setSpacing(10)
        layout.addWidget(self._info_box)
        self._show_empty_info()

        layout.addWidget(self._divider())
        layout.addWidget(self._section_label("迁移设置"))

        drive_row = QHBoxLayout()
        drive_row.setSpacing(10)
        dl = QLabel("目标盘符")
        dl._hint = True
        dl.setStyleSheet(_FIELD_LABEL)
        drive_row.addWidget(dl)
        self.drive_combo = ArrowComboBox()
        self._populate_drives()
        drive_row.addWidget(self.drive_combo, 1)
        layout.addLayout(drive_row)

        tl = QLabel("目标路径")
        tl._hint = True
        tl.setStyleSheet(_FIELD_LABEL)
        layout.addWidget(tl)
        target_row = QHBoxLayout()
        target_row.setSpacing(10)
        self.target_path_edit = QLineEdit()
        self.target_path_edit.setPlaceholderText("自动生成，可修改（如 D:\\Apps\\微信）")
        self.drive_combo.currentIndexChanged.connect(self._refresh_target_path)
        target_row.addWidget(self.target_path_edit, 1)
        browse_btn = _icon_button("浏览", _ICON_FOLDER, kind="ghost", height=40)
        browse_btn.clicked.connect(self._browse_target)
        target_row.addWidget(browse_btn)
        layout.addLayout(target_row)

        layout.addWidget(self._divider())
        layout.addWidget(self._section_label("迁移进度"))

        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        layout.addWidget(self.progress)

        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setPlaceholderText("迁移日志...")
        self.log_edit.setMaximumHeight(120)
        layout.addWidget(self.log_edit)

        layout.addWidget(self._divider())
        layout.addWidget(self._section_label("操作"))

        self.migrate_btn = _icon_button("开始迁移", _ICON_MIGRATE, kind="primary", height=44)
        self.migrate_btn.setEnabled(False)
        self.migrate_btn.clicked.connect(self._start_migration)
        self.uninstall_btn = _icon_button("强力卸载", _ICON_TRASH, kind="danger", height=44)
        self.uninstall_btn.setEnabled(False)
        self.uninstall_btn.clicked.connect(self._start_uninstall)
        row1 = QHBoxLayout()
        row1.setSpacing(10)
        row1.addWidget(self.migrate_btn, 1)
        row1.addWidget(self.uninstall_btn, 1)
        layout.addLayout(row1)

        self.open_dir_btn = _icon_button("打开安装目录", _ICON_FOLDER, height=40)
        self.open_dir_btn.setEnabled(False)
        self.open_dir_btn.clicked.connect(self._open_app_dir)
        row2 = QHBoxLayout()
        row2.setSpacing(10)
        row2.addWidget(self.open_dir_btn, 1)
        layout.addLayout(row2)

        return card

    # ---------- 防护页：静默防护 + 防护设置 + 安全提醒 ----------
    def _build_security_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        card = Card()
        cl = card.layout
        cl.addWidget(self._section_label("静默安全防护"))
        hint = QLabel("后台巡检弹窗拦截、启动项、DNS 与勒索行为；开启后随程序常驻。")
        hint.setWordWrap(True)
        hint._hint = True
        hint.setStyleSheet(f"font-size: 12px; color: {PALETTE['text_secondary']};")
        cl.addWidget(hint)

        self.security_btn = _icon_button("开启静默防护", _ICON_SHIELD, height=40)
        self.security_btn.clicked.connect(self._toggle_security)
        self.security_settings_btn = _icon_button("防护设置", _ICON_SETTINGS, height=40)
        self.security_settings_btn.clicked.connect(self._show_security_settings)
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(self.security_btn, 1)
        row.addWidget(self.security_settings_btn, 1)
        cl.addLayout(row)

        cl.addWidget(self._divider())
        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.toast_switch = SwitchButton(self._toast_enabled)
        self.toast_switch.toggled.connect(self._on_toast_toggle)
        foot.addWidget(self.toast_switch)
        sw = QLabel("安全提醒弹窗")
        sw._hint = True
        sw.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 12px;")
        foot.addWidget(sw)
        foot.addStretch(1)
        cl.addLayout(foot)

        lay.addWidget(card)
        lay.addStretch(1)
        return page

    # ---------- 通用页：界面主题 / 更新 / 关于 ----------
    def _build_general_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        card = Card()
        cl = card.layout
        cl.addWidget(self._section_label("界面外观"))
        theme_row = QHBoxLayout()
        theme_row.setSpacing(10)
        lbl = QLabel("界面主题")
        lbl._hint = True
        lbl.setStyleSheet(_FIELD_LABEL)
        lbl.setFixedWidth(70)
        theme_row.addWidget(lbl)
        self.theme_combo = ArrowComboBox()
        self.theme_combo.addItem("深色", "dark")
        self.theme_combo.addItem("浅色", "light")
        self.theme_combo.addItem("按时间自动（8-20 浅色）", "auto")
        saved = str(app_identity.qsettings().value("agent_theme", "light"))
        ti = self.theme_combo.findData(saved)
        self.theme_combo.setCurrentIndex(ti if ti >= 0 else 1)
        self.theme_combo.currentIndexChanged.connect(self._on_theme_pick)
        theme_row.addWidget(self.theme_combo, 1)
        cl.addLayout(theme_row)

        cl.addWidget(self._divider())
        cl.addWidget(self._section_label("软件更新"))
        self.update_btn = _icon_button("检查更新", _ICON_UPDATE, kind="tool", height=32)
        self.update_btn.clicked.connect(self._on_check_update_clicked)
        upd = QHBoxLayout()
        upd.addWidget(self.update_btn)
        upd.addStretch(1)
        cl.addLayout(upd)

        cl.addWidget(self._divider())
        cl.addWidget(self._section_label("关于"))
        about_btn = _icon_button("关于我们", _ICON_ABOUT, kind="tool", height=32)
        about_btn.clicked.connect(self._show_about)
        website_btn = _icon_button("访问官网", _ICON_LINK, kind="tool", height=32)
        website_btn.clicked.connect(self._open_website)
        ab = QHBoxLayout()
        ab.addWidget(about_btn)
        ab.addWidget(website_btn)
        ab.addStretch(1)
        cl.addLayout(ab)

        lay.addWidget(card)
        lay.addStretch(1)
        return page

    def _on_theme_pick(self, _idx: int):
        """通用页切换主题：写入 agent_theme、就地重刷本面板，并通知 AI 面板重建（两界面同源）"""
        mode = self.theme_combo.currentData() or "light"
        app_identity.qsettings().setValue("agent_theme", mode)
        styles.set_palette(self._current_theme())
        _regen_qss()
        self._apply_theme()
        self.theme_changed.emit()

    # ---------- 工具页：高速下载 / 内存优化 / 自定义文件夹迁移 ----------
    def _build_tools_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        card = Card()
        cl = card.layout
        cl.addWidget(self._section_label("工具"))

        self.download_btn = _icon_button("高速下载", _ICON_UPDATE, height=40)
        self.download_btn.clicked.connect(self._open_download_dialog)
        self.memory_btn = _icon_button("一键优化内存", _ICON_MEMORY, height=40)
        self.memory_btn.clicked.connect(self._start_memory_optimize)
        custom_btn = _icon_button("迁移自定义文件夹", _ICON_FOLDER, height=40)
        custom_btn.clicked.connect(self._migrate_custom_folder)

        for b in (self.download_btn, self.memory_btn, custom_btn):
            cl.addWidget(b)

        lay.addWidget(card)
        lay.addStretch(1)
        return page

    # ---------- 右栏辅助 ----------
    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text.upper())
        lbl._section = True
        lbl.setStyleSheet(_SECTION_TITLE)
        return lbl

    def _divider(self) -> QFrame:
        """分区之间的 1px 分隔线（底色随主题重建，见 _Divider）"""
        return _Divider()

    def _clear_info(self):
        """清空详情区：递归移除并销毁所有子项（widget 与嵌套 layout）。
        快速连续点击时 deleteLater 是延迟删除，必须 setParent(None) 立即分离，
        否则旧控件残留与新增内容叠加导致重叠。"""
        def _wipe(lay):
            while lay.count():
                it = lay.takeAt(0)
                w = it.widget()
                if w is not None:
                    w.setParent(None)
                    w.deleteLater()
                else:
                    child = it.layout()
                    if child is not None:
                        _wipe(child)
        _wipe(self._info_lay)

    def _show_empty_info(self):
        self._clear_info()
        tip = QLabel("从左侧选择一个应用以查看详情")
        tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tip.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 13px; padding: 22px 0;")
        self._info_lay.addWidget(tip)

    def _show_app_info(self, app: AppInfo):
        self._clear_info()

        head = QHBoxLayout()
        head.setSpacing(12)
        icon = QLabel()
        icon.setPixmap(self._app_icon_pixmap(app, 44))
        head.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)

        names = QVBoxLayout()
        names.setSpacing(4)
        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        name = QLabel(app.name or "未知")
        name.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {PALETTE['text']};")
        name_row.addWidget(name)
        tag = QLabel(app.app_type)
        tag.setStyleSheet(self._type_badge(app.app_type))
        name_row.addWidget(tag)
        name_row.addStretch(1)
        names.addLayout(name_row)

        meta = QLabel(f"{app.version or '未知版本'}  ·  {format_size(app.size_bytes)}")
        meta.setStyleSheet(f"font-size: 12px; color: {PALETTE['text_secondary']};")
        names.addWidget(meta)
        head.addLayout(names, 1)
        self._info_lay.addLayout(head)

        path = QLabel(str(app.install_location))
        path.setWordWrap(True)
        path.setStyleSheet(
            f"font-size: 12px; color: {PALETTE['text_secondary']};"
            f" background: {PALETTE['bg_bottom']}; border-radius: 8px; padding: 8px 10px;")
        self._info_lay.addWidget(path)

    def _type_badge(self, app_type: str) -> str:
        if app_type == "Win32":
            color = PALETTE["primary"]
        elif app_type == "UWP":
            color = PALETTE["warning"]
        else:
            color = PALETTE["text_secondary"]
        return (f"font-size: 11px; font-weight: 700; color: #FFFFFF; background: {color};"
                " border-radius: 4px; padding: 2px 8px;")

    def _app_icon_pixmap(self, app: AppInfo, size: int) -> QPixmap:
        icon = self.app_list.itemDelegate()._icon_for(app)
        if icon and not icon.isNull():
            return icon.pixmap(size, size)
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(PALETTE["primary"]))
        p.drawRoundedRect(0, 0, size, size, size // 4, size // 4)
        p.setPen(QColor("#FFFFFF"))
        f = QFont()
        f.setBold(True)
        f.setPixelSize(size // 2)
        p.setFont(f)
        p.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter,
                   (app.name[0] if app.name else "?").upper())
        p.end()
        return pix

    def _populate_drives(self):
        import string
        from ctypes import windll
        drives = []
        bitmask = windll.kernel32.GetLogicalDrives()
        for letter in string.ascii_uppercase:
            if bitmask & 1:
                drives.append(f"{letter}:")
            bitmask >>= 1
        self.drive_combo.clear()
        for d in drives:
            self.drive_combo.addItem(f"{d}\\", d)
        if self.drive_combo.count() == 0:
            self.drive_combo.addItem("C:\\", "C:")

    def _setup_tray(self):
        """系统托盘：防护开启时显示图标，右键菜单可打开 AI 面板 / 退出，清理结果右下角弹窗"""
        self.tray = QSystemTrayIcon(QIcon(_app_icon_path()), self)
        self.tray.setToolTip("zhuzhu Copilot · 静默安全防护")
        menu = QMenu(self)
        act_show = menu.addAction("打开 AI 面板")
        act_show.triggered.connect(self._open_agent_panel)
        act_quit = menu.addAction("退出程序")
        act_quit.triggered.connect(self._quit_app)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.hide()

    def _quit_app(self, *_):
        """托盘退出：先做退出清理（停防护线程 / 关桌宠），再终止应用"""
        try:
            self.shutdown()
        finally:
            QApplication.quit()

    def _toggle_security(self, *_):
        """开启/关闭静默防护，并记忆用户选择"""
        if self._security_on:
            self._stop_security()
        else:
            self._start_security()

    def _on_toast_toggle(self, checked: bool):
        """记忆用户对安全提醒弹窗的选择（拦截始终生效，仅控制是否弹窗）"""
        self._toast_enabled = checked
        self._settings.setValue("security_toast", checked)

    def _start_security(self):
        """开启静默防护（首次立即扫描，之后每 30 秒巡检）"""
        if self._security_on:
            return
        if not is_admin():
            QMessageBox.warning(self, "权限不足", "静默防护需要管理员权限。")
            return
        self._settings.setValue("security_auto", True)
        self.security_worker = SecurityMonitorWorker(self)
        self.security_worker.result.connect(self._on_security_result)
        self.security_worker.start()
        self._security_on = True
        self.security_btn.setText("静默防护运行中")
        self.status_label.setText("静默防护运行中，正在后台监控…")
        self.tray.show()
        if self._toast_enabled:
            self.toast.show_toast(
                "静默防护", "已开启\n后台监控恶意进程、启动项与网络风险，拦截结果将在此提示。",
                False, 4000,
            )

    def _stop_security(self):
        """关闭静默防护"""
        if self.security_worker and self.security_worker.isRunning():
            self.security_worker.stop()
            self.security_worker.wait(3000)
        self._settings.setValue("security_auto", False)
        self._security_on = False
        self.security_btn.setText("开启静默防护")
        self.tray.hide()
        self.status_label.setText("静默防护已关闭")

    def _show_security_settings(self):
        """防护参数设置面板：模块开关 + 隔离区管理"""
        from zhuzhu_Copilot.core.security_engine.config import config
        from zhuzhu_Copilot.core.security_engine.quarantine import quarantine

        dlg = QDialog(self)
        dlg.setWindowTitle("防护设置")
        dlg.setMinimumWidth(480)
        lay = QVBoxLayout(dlg)
        lay.setSpacing(12)

        sec = QLabel("防护模块")
        sec.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 12px;")
        lay.addWidget(sec)

        modules = [
            ("yara", "YARA 恶意特征扫描"),
            ("ransomware", "蜜罐勒索防护"),
            ("popup", "广告弹窗拦截"),
            ("startup", "自启动监控"),
            ("dns", "DNS / hosts 防护"),
            ("download", "下载目录文件分析"),
            ("llm", "LLM 深度分析（低置信样本二次判定）"),
        ]
        for key, label in modules:
            row = QHBoxLayout()
            sw = SwitchButton(config.enabled(key))
            sw.toggled.connect(lambda checked, k=key: self._on_module_toggle(k, checked))
            lbl = QLabel(label)
            lbl.setStyleSheet(f"color: {PALETTE['text']}; font-size: 13px;")
            row.addWidget(lbl)
            row.addStretch(1)
            row.addWidget(sw)
            lay.addLayout(row)

        lay.addWidget(self._divider())

        qsec = QLabel("隔离区")
        qsec.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 12px;")
        lay.addWidget(qsec)

        lst = QListWidget()
        lst.setMinimumHeight(120)
        for item in quarantine.list_items():
            it = QListWidgetItem(f"{item['name']}  ·  {item.get('reason') or '手动隔离'}")
            it.setData(Qt.ItemDataRole.UserRole, item.get("id", ""))
            lst.addItem(it)
        if lst.count() == 0:
            lst.addItem("隔离区为空")
        lay.addWidget(lst)

        rowq = QHBoxLayout()
        restore_btn = _icon_button("恢复选中", _ICON_REFRESH, height=36)
        open_btn = _icon_button("打开隔离区", _ICON_FOLDER, height=36)

        def _restore():
            cur = lst.currentItem()
            ident = cur.data(Qt.ItemDataRole.UserRole) if cur else ""
            if not ident:
                return
            if quarantine.restore(ident):
                QMessageBox.information(dlg, "恢复成功", "文件已恢复到原位置。")
                dlg.accept()
            else:
                QMessageBox.warning(dlg, "恢复失败", "文件不存在或原位置被占用。")

        def _open_dir():
            d = quarantine.root
            if d.is_dir():
                os.startfile(str(d))  # type: ignore[attr-defined]

        restore_btn.clicked.connect(_restore)
        open_btn.clicked.connect(_open_dir)
        rowq.addWidget(restore_btn)
        rowq.addWidget(open_btn)
        rowq.addStretch(1)
        lay.addLayout(rowq)

        close_btn = _icon_button("关闭", _ICON_CLOSE, kind="primary", height=36)
        close_btn.clicked.connect(dlg.accept)
        lay.addWidget(close_btn)

        dlg.exec()

    def _on_module_toggle(self, module: str, checked: bool):
        """切换防护模块开关并持久化（check 型 guard 立即生效，弹窗模块需重启防护）"""
        from zhuzhu_Copilot.core.security_engine.config import config
        config.set_module_enabled(module, checked)
        config.save()
        note = "重启防护后生效" if module == "popup" else "已生效"
        self.toast.show_toast("防护设置", f"模块开关已更新（{note}）", False, 3000)

    def _on_security_result(self, summary: dict):
        """安全清理/检查完成后右下角自定义弹窗提示结果（用户可关闭此弹窗）"""
        if not self._toast_enabled:
            return
        lines = []
        killed = summary.get("killed") or []
        removed = summary.get("removed") or []
        failed = summary.get("failed") or []
        signed = summary.get("signed") or []
        if killed:
            lines.append(f"已拦截恶意进程 {len(killed)} 个：{', '.join(killed[:3])}")
        if removed:
            lines.append(f"已删除恶意启动项 {len(removed)} 个\n原值已备份隔离区：{quarantine_dir()}")
        if failed:
            lines.append(f"检测到威胁但清理失败 {len(failed)} 项")
        if signed:
            lines.append(f"跳过无法确认/签名有效的同名进程 {len(signed)} 个：{', '.join(signed[:2])}")
        net = summary.get("network")
        if net:
            if net.get("firewall_off"):
                lines.append(f"防火墙已关闭：{', '.join(net['firewall_off'])}")
            hl = net.get("high_risk_listening") or []
            if hl:
                desc = "，".join(
                    f"{x['port']}({x['name']})" if isinstance(x, dict) and x.get("name")
                    else str(x if not isinstance(x, dict) else x.get("port"))
                    for x in hl)
                lines.append(f"高危端口暴露：{desc}")
        attacks = summary.get("attacks") or {}
        spoof = attacks.get("arp_spoof")
        if spoof:
            fix = ("已自动修复：删除污染条目并静态绑定正确网关 MAC"
                   if spoof.get("repaired") else "自动修复失败，请手动核对网关 MAC")
            line = (f"ARP 欺骗: 网关 {spoof['gateway']} MAC 突变\n"
                    f"   {spoof['old_mac']} → {spoof['new_mac']}\n"
                    f"   {fix}")
            lines.append(line)
        flood = attacks.get("flood")
        if flood:
            if flood["syn_sources"]:
                banned = f"，已封禁 {len(flood['blocked'])} 个来源" if flood["blocked"] else "，封禁失败"
                lines.append(f"SYN 洪泛: 来源 {', '.join(flood['syn_sources'])}{banned}")
            if flood["scan_sources"]:
                lines.append(f"端口扫描: 来源 {', '.join(flood['scan_sources'])}（已封禁）")
            if flood["packet_flood"]:
                lines.append(f"TCP 洪泛: 入段速率 {flood['packet_rate']}/秒")
            if flood["udp_flood"]:
                lines.append(f"UDP 洪泛: 入数据报速率 {flood['udp_rate']}/秒")
        exec_res = summary.get("exec_guard") or {}
        for b in exec_res.get("blocked") or []:
            state = "已终止" if b.get("killed") else "终止失败"
            lines.append(f"执行防护已拦截 {b['name']}（{b['reason']}，{state}）")
        for w in exec_res.get("warned") or []:
            lines.append(f"{w['name']}：{w['reason']}")
        eng = summary.get("engine") or {}
        rw = eng.get("ransomware") or {}
        if rw.get("detected"):
            if rw.get("tampered"):
                lines.append(f"蜜罐防护：{len(rw['tampered'])} 个诱饵文件被篡改，疑似勒索软件活动")
            if rw.get("encrypted"):
                lines.append(f"勒索加密后缀爆发：{', '.join(rw['encrypted'])}")
        for s in eng.get("startup", {}).get("suspicious") or []:
            lines.append(f"检测到可疑自启动项：{s[1]} → {s[2]}")
        dns_res = eng.get("dns") or {}
        for h in dns_res.get("hijacked") or []:
            lines.append(f"hosts 劫持：{h['domain']} → {h['ip']}")
        if dns_res.get("changed"):
            lines.append("hosts 文件被修改，请检查是否被恶意篡改")
        for p in eng.get("popup") or []:
            lines.append(f"已拦截广告弹窗：{p['process']}（{p['title']}）")
        for d in eng.get("download") or []:
            action_txt = "，已隔离" if d.get("isolated") else ""
            lines.append(f"下载目录 {d['verdict']} 文件：{d['path']}{action_txt}\n   {d.get('reason') or ''}")
        if not lines:
            return
        warn = bool(killed or removed or failed or spoof or (flood and flood["detected"])
                    or exec_res.get("blocked") or rw.get("detected")
                    or eng.get("startup", {}).get("suspicious")
                    or dns_res.get("hijacked") or eng.get("download"))
        self.toast.show_toast("安全防护报告", "\n".join(lines), warn, 7000)

    def _on_tray_activated(self, reason):
        # PyQt6 枚举：DblClick 已更名为 DoubleClick（PyQt5 旧名会导致右击/左击
        # 托盘时求值抛 AttributeError → 崩溃弹窗，见 crashes/ 日志）
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self._restore_from_tray()

    def _restore_from_tray(self, *_):
        """托盘唤起：主窗口已移除，改为请求显示 AI 面板（由 AgentPanel 处理）"""
        self.open_agent_requested.emit()

    def _check_admin(self):
        if is_admin():
            self._admin_dot.setStyleSheet(f"background: {PALETTE['success']}; border-radius: 4px;")
            self.admin_label.setText("管理员权限")
            self.admin_label.setStyleSheet(f"font-size: 12px; color: {PALETTE['success']};")
        else:
            self._admin_dot.setStyleSheet(f"background: {PALETTE['danger']}; border-radius: 4px;")
            self.admin_label.setText("未提权")
            self.admin_label.setStyleSheet(f"font-size: 12px; color: {PALETTE['danger']};")
            QMessageBox.warning(
                self,
                "需要管理员权限",
                "本工具需要管理员权限才能创建目录联接和修改系统目录。\n请右键以管理员身份运行。",
            )

    def _start_scan(self, *_):
        self.app_list.clear()
        self.status_label.setText("正在扫描已安装应用...")
        self.scan_progress.show()
        self.refresh_btn.setEnabled(False)
        self.scan_worker = ScanWorker()
        self.scan_worker.finished.connect(self._on_scan_finished)
        self.scan_worker.error.connect(self._on_scan_error)
        self.scan_worker.start()

    def _on_scan_finished(self, apps: List[AppInfo]):
        self.apps = apps
        self._filter_apps()
        self.status_label.setText(f"共扫描到 {len(apps)} 个应用")
        self.scan_progress.hide()
        self.refresh_btn.setEnabled(True)
        # 列表先秒出，大小由后台线程补齐
        self.size_worker = SizeWorker(apps, self)
        self.size_worker.sizes_ready.connect(self._on_sizes_ready)
        self.size_worker.start()
        # 图标由后台线程预取源文件，主线程负责创建 QIcon，避免滑动/渲染时阻塞
        delegate = self.app_list.itemDelegate()
        self.icon_worker = IconLoaderWorker(apps, delegate)
        self.icon_worker.icon_ready.connect(self._on_icon_ready)
        self.icon_worker.start()

    def _on_icon_ready(self, app, source: str):
        if app is None:
            return
        delegate = self.app_list.itemDelegate()
        if source:
            delegate.apply_icon(app)
        self.app_list.viewport().update()

    def _on_sizes_ready(self, sizes: dict):
        for i in range(self.app_list.count()):
            item = self.app_list.item(i)
            app = item.data(Qt.ItemDataRole.UserRole) if item else None
            if app and id(app) in sizes:
                app.size_bytes = sizes[id(app)]
        self.app_list.viewport().update()

    def _on_scan_error(self, msg: str):
        self.status_label.setText(f"扫描失败: {msg}")
        self.scan_progress.hide()
        self.refresh_btn.setEnabled(True)

    def _filter_apps(self, *_):
        text = self.search_edit.text().lower()
        # 关闭更新避免逐项插入各自触发重绘
        self.app_list.setUpdatesEnabled(False)
        self.app_list.clear()
        for app in self.apps:
            if text and not _app_matches(app, text):
                continue
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, app)
            item.setSizeHint(QSize(0, AppItemDelegate.ROW_HEIGHT))
            self.app_list.addItem(item)
        self.app_list.setUpdatesEnabled(True)
        self._app_count.setText(str(self.app_list.count()))
        self.app_list.viewport().update()

    def _select_app(self, app: AppInfo):
        self.selected_app = app
        self._show_app_info(app)
        self.migrate_btn.setEnabled(True)
        self.uninstall_btn.setEnabled(True)
        self.open_dir_btn.setEnabled(True)
        self._refresh_target_path()

    def _open_app_dir(self, *_):
        """在资源管理器中打开所选应用的安装目录"""
        if not self.selected_app:
            return
        loc = self.selected_app.install_location
        if loc and loc.is_dir():
            try:
                os.startfile(str(loc))
            except Exception as e:
                logger.warning("打开目录失败 %s: %s", loc, e)
                QMessageBox.warning(self, "无法打开", f"无法打开目录：{loc}\n{e}")
        else:
            QMessageBox.warning(self, "无法打开", f"目录不存在：{loc}")

    def _refresh_target_path(self, *_):
        """按选中 app 与目标盘生成默认目标路径（用户手动编辑过则不覆盖）"""
        if not self.selected_app or not hasattr(self, "target_path_edit"):
            return
        if self.target_path_edit.isModified():
            return
        drive = self.drive_combo.currentData() or ""
        self.target_path_edit.setText(
            f"{drive}\\zhuzhu_Copilot\\{self.selected_app.app_type}\\{self.selected_app.name}")

    def _browse_target(self, *_):
        """通过文件夹对话框选择/新建目标位置（支持同盘迁移，选择后在所选目录下创建 app 目录）"""
        app = self.selected_app
        if not app:
            return
        current = self.target_path_edit.text().strip()
        start = str(Path(current).parent) if current and Path(current).parent.is_dir() \
            else str(Path(self.drive_combo.currentData() or "C:\\"))
        folder = QFileDialog.getExistingDirectory(
            self, "选择目标位置（可在对话框内新建文件夹）", start,
            QFileDialog.Option.ShowDirsOnly)
        if not folder:
            return
        self.target_path_edit.setText(str(Path(folder) / app.name))
        self.target_path_edit.setModified(True)  # 用户自定义目标，后续不再自动覆盖

    def _resolve_target(self):
        """解析迁移目标路径：用户自定义或自动生成，并校验合法性"""
        text = self.target_path_edit.text().strip()
        if not text:
            self._refresh_target_path()
            text = self.target_path_edit.text().strip()
        path = Path(text)
        if not path.is_absolute():
            QMessageBox.warning(self, "路径无效", "目标路径必须是绝对路径，例如 D:\\Apps\\微信")
            return None
        # 盘符根目录（如 C:\）作为目标时，自动在其下创建 app 同名目录
        if path.anchor and str(path).rstrip("\\").lower() == path.anchor.rstrip("\\").lower():
            path = path / self.selected_app.name
        # 目标已存在不在此拒绝，交由 _start_migration 提供 覆盖/自动改名 处理
        if path == self.selected_app.install_location:
            QMessageBox.warning(self, "路径无效", "目标路径不能与源路径相同")
            return None
        return path

    def _on_app_selected(self, item: QListWidgetItem):
        app = item.data(Qt.ItemDataRole.UserRole)
        if app:
            self._select_app(app)

    def _start_migration(self, *_):
        if not self.selected_app:
            return
        if not is_admin():
            QMessageBox.warning(self, "权限不足", "请以管理员身份运行本工具。")
            return

        drive = Path(self.drive_combo.currentData())
        target = self._resolve_target()
        if target is None:
            return
        # 目标目录已存在：提供 覆盖/自动改名 处理，避免直接失败
        if target.exists():
            reply = QMessageBox.question(
                self,
                "目标目录已存在",
                f"目标位置已存在：<b>{target}</b>\n\n"
                "选择「覆盖」将删除该目录后迁移（原内容不可恢复）；\n"
                "选择「改名」将自动在名称后加序号迁移到新位置。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel,
            )
            if reply == QMessageBox.StandardButton.Yes:
                if not safe_remove(target):
                    QMessageBox.warning(self, "无法覆盖", f"无法删除已存在的目标目录：{target}")
                    return
            elif reply == QMessageBox.StandardButton.No:
                n = 1
                while True:
                    cand = Path(f"{target}_{n}")
                    if not cand.exists():
                        target = cand
                        break
                    n += 1
                self.target_path_edit.setText(str(target))
            else:
                return
        reply = QMessageBox.question(
            self,
            "确认迁移",
            f"确定将 <b>{self.selected_app.name}</b> 迁移到 <b>{target}</b> 吗？\n"
            f"迁移后旧安装目录将被删除，注册表与快捷方式将更新到新路径。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        extra_dirs = self._choose_extra_dirs()

        self.migrate_btn.setEnabled(False)
        self.refresh_btn.setEnabled(False)
        self.progress.setValue(0)
        self.log_edit.clear()

        self.migrate_worker = MigrateWorker(self.selected_app, target, extra_dirs)
        self.migrate_worker.progress.connect(self._on_progress)
        self.migrate_worker.finished.connect(self._on_migrate_finished)
        self.migrate_worker.conflict.connect(self._on_migrate_conflict)
        self.migrate_worker.start()

    def _on_migrate_conflict(self, source: Path, target: Path):
        """迁移目标已存在：询问用户是否删除并替换"""
        ret = QMessageBox.question(
            self,
            "目标目录已存在",
            f"目标位置已存在同名目录：\n<b>{target}</b>\n\n"
            f"是否删除该目录并替换？\n（源目录：{source}）\n\n"
            f"选择「否」将跳过该目录的迁移。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        self.migrate_worker.resolve_conflict(ret == QMessageBox.StandardButton.Yes)

    def _choose_extra_dirs(self):
        """检测并让用户勾选要一并迁移的数据目录，返回勾选的目录列表"""
        try:
            candidates = detect_data_dirs(self.selected_app)
        except Exception:
            candidates = []
        if not candidates:
            return []
        dlg = DataDirDialog(candidates, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            return dlg.selected()
        return []

    def _on_progress(self, percent: int, message: str):
        self.log_edit.append(f"[{percent}%] {message}")
        # 进度平滑过渡动画
        self.progress_anim.stop()
        self.progress_anim.setStartValue(self.progress.value())
        self.progress_anim.setEndValue(percent)
        self.progress_anim.start()

    def _warn_360_blocked(self, blocked: list):
        """360 自我保护拦截终止时的引导弹窗"""
        names = "、".join(blocked) if blocked else "360安全卫士相关进程"
        QMessageBox.warning(
            self,
            "360 自我保护",
            "检测到 360 安全卫士的进程无法被强制结束。\n"
            "360 的自我保护会在系统内核层拦截强制终止，属于其正常安全机制。\n\n"
            "请任选其一操作后再重试：\n"
            "1. 右键 360 托盘图标 → 退出 360\n"
            "2. 打开 360 设置 → 防护中心 → 关闭「自我保护」\n\n"
            f"未能结束的进程：{names}",
        )

    def _on_migrate_finished(self, result: dict):
        self.migrate_btn.setEnabled(True)
        self.refresh_btn.setEnabled(True)
        if result.get("blocked_360"):
            self._warn_360_blocked(result.get("blocked") or [])
        if result.get("success"):
            self.progress.setValue(100)
            msg = (f"{result['message']}\n\n"
                   f"新位置: {result['target']}\n"
                   f"注册表更新: {result.get('registry_changed', 0)} 处\n"
                   f"快捷方式更新: {result.get('shortcuts_changed', 0)} 个")
            warns = [d for d in (result.get("details") or []) if "警告" in str(d) or "未能删除" in str(d)]
            if warns:
                msg += "\n\n" + "\n".join(warns)
            QMessageBox.information(self, "迁移成功", msg)
        else:
            self.progress.setValue(0)
            QMessageBox.critical(self, "迁移失败", result.get("message", "未知错误"))

    def _start_uninstall(self, *_):
        if not self.selected_app:
            return
        if not is_admin():
            QMessageBox.warning(self, "权限不足", "请以管理员身份运行本工具。")
            return
        # 后台构建卸载清单（注册表/快捷方式预扫描），期间 UI 保持响应
        self.migrate_btn.setEnabled(False)
        self.refresh_btn.setEnabled(False)
        self.uninstall_btn.setEnabled(False)
        self.status_label.setText("正在分析应用...")
        self.build_worker = BuildPlanWorker(self.selected_app, self.uninstaller)
        self.build_worker.plan_ready.connect(self._on_plan_ready)
        self.build_worker.error.connect(self._on_plan_error)
        self.build_worker.start()

    def _restore_buttons(self):
        self.migrate_btn.setEnabled(True)
        self.refresh_btn.setEnabled(True)
        self.uninstall_btn.setEnabled(True)
        if hasattr(self, "status_label"):
            self.status_label.setText(f"共扫描到 {len(self.apps)} 个应用" if self.apps else "就绪")

    def _on_plan_ready(self, plan):
        self._restore_buttons()
        dlg = UninstallConfirmDialog(plan, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self.progress.setValue(0)
        self.log_edit.clear()
        self.migrate_btn.setEnabled(False)
        self.refresh_btn.setEnabled(False)
        self.uninstall_btn.setEnabled(False)
        self.uninstall_worker = UninstallWorker(plan)
        self.uninstall_worker.progress.connect(self._on_progress)
        self.uninstall_worker.finished.connect(self._on_uninstall_finished)
        self.uninstall_worker.start()

    def _on_plan_error(self, msg: str):
        self._restore_buttons()
        QMessageBox.critical(self, "分析失败", f"无法分析该应用: {msg}")

    def _on_uninstall_finished(self, result: dict):
        self.migrate_btn.setEnabled(True)
        self.refresh_btn.setEnabled(True)
        self.uninstall_btn.setEnabled(True)
        if result.get("blocked_360"):
            self._warn_360_blocked(result.get("blocked") or [])
        if result.get("success"):
            self.progress.setValue(100)
            removed = result.get("removed_dirs") or []
            failed = result.get("failed") or []
            msg = (
                f"{result['message']}\n\n"
                f"已删除目录:\n" + ("\n".join(removed) if removed else "（无）") +
                f"\n\n注册表清理: {result.get('registry_removed', 0)} 处\n"
                f"快捷方式清理: {result.get('shortcuts_removed', 0)} 个"
            )
            if failed:
                msg += "\n\n以下删除失败:\n" + "\n".join(failed)
            QMessageBox.information(self, "卸载完成", msg)
            self._start_scan()  # 刷新应用列表
        else:
            self.progress.setValue(0)
            QMessageBox.critical(self, "卸载失败", result.get("message", "未知错误"))

    def _migrate_custom_folder(self, *_):
        if not is_admin():
            QMessageBox.warning(self, "权限不足", "请以管理员身份运行本工具。")
            return
        folder = QFileDialog.getExistingDirectory(self, "选择要迁移的文件夹")
        if not folder:
            return
        drive = Path(self.drive_combo.currentData())
        app = AppInfo(
            name=Path(folder).name,
            publisher="",
            install_location=Path(folder),
            version="",
            app_type="自定义",
            size_bytes=0,
        )
        self.selected_app = app
        self._start_migration()

    def _open_download_dialog(self, *_):
        """打开高速下载窗口（独立窗口，可同时管理多个任务）"""
        from zhuzhu_Copilot.ui.download_dialog import DownloadDialog
        if not hasattr(self, "_download_dialog") or self._download_dialog is None:
            self._download_dialog = DownloadDialog(self)
        self._download_dialog.show()
        self._download_dialog.raise_()
        self._download_dialog.activateWindow()

    def _ret_theme(self):
        """主题切换回调：本面板就地重刷样式（AI 面板由 theme_changed 信号自行重建）"""
        styles.set_palette(self._current_theme())
        _regen_qss()
        self._apply_theme()

    def _show_about(self, *_):
        """关于我们弹窗"""
        QMessageBox.information(
            self, "关于我们",
            "开发者是一名14岁的初中生，通过vibe coding开发而来")

    def _open_website(self, *_):
        """打开默认浏览器访问官网关于我们页"""
        webbrowser.open("https://chentian.dpdns.org/about")

    # ---------- 自动更新检查（30s 轮询更新服务器） ----------
    def _init_update_check(self):
        self._update_checker = UpdateChecker(self)
        self._update_checker.update_found.connect(self._on_update_found)
        self._update_checker.manual_result.connect(self._on_manual_check_result)
        self._update_checker.start()

    def _on_check_update_clicked(self, *_):
        """手动检查更新：调用服务器 API 并反馈结果"""
        self.update_btn.setText("检查中…")
        self.update_btn.setEnabled(False)
        self._update_checker.check(manual=True)

    def _on_manual_check_result(self, result: dict):
        """手动检查结果反馈：有更新/已最新/失败等"""
        self.update_btn.setText("检查更新")
        self.update_btn.setEnabled(True)
        status = result.get("status")
        if status == "ok":
            self._on_update_found(result.get("info") or {})
        elif status == "none":
            QMessageBox.information(self, "检查更新", f"已是最新版本（v{APP_VERSION}）")
        elif status == "noserver":
            QMessageBox.warning(self, "检查更新", "未配置更新服务器地址")
        elif status == "busy":
            QMessageBox.information(self, "检查更新", "正在检查更新，请稍候…")
        else:
            err = result.get("error") or "网络异常"
            QMessageBox.warning(self, "检查更新", f"检查更新失败：{err}")

    def _on_update_found(self, info: dict):
        """发现新版本：普通更新弹确认框；强制更新无忽略选项"""
        notes = (info.get("notes") or "").strip()
        size = info.get("size") or 0
        text = f"发现新版本 v{info['latest']}（当前 v{APP_VERSION}）"
        if size:
            text += f"，大小 {size // 1024 // 1024} MB"
        if notes:
            text += f"\n\n更新说明：\n{notes}"
        if info.get("force"):
            QMessageBox.warning(
                self, "发现强制更新",
                text + "\n\n当前版本已停止服务，必须更新后才能继续使用。\n\n更新页已打开，本窗口关闭后应用将退出。")
            webbrowser.open(info["url"])
            self._exit_forced()
            return
        ret = QMessageBox.question(
            self, "发现新版本", text + "\n\n是否前往下载更新？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if ret == QMessageBox.StandardButton.Yes:
            webbrowser.open(info["url"])

    def _exit_forced(self):
        """强制更新：拒绝更新必须退出应用。绕过托盘/防护线程，彻底结束进程"""
        import os
        try:
            if hasattr(self, "tray"):
                self.tray.hide()
        except Exception:
            pass
        os._exit(0)

    def _start_memory_optimize(self, *_):
        """一键优化内存"""
        reply = QMessageBox.question(
            self,
            "确认优化内存 - 核弹级方案",
            "核弹级方案：\n"
            "1. NtOpenProcess 兜底覆盖受保护进程（解决 OpenProcess 拒绝访问）\n"
            "2. 暂停所有非核心进程 → 硬限制工作集为 1 字节\n"
            "3. 分配大块内存制造极端内存压力 → 强迫内核主动 trim\n"
            "4. 多次清空 standby list 彻底释放物理 RAM\n\n"
            "执行期间：后台窗口会短暂冻结（暂停进程中），\n"
            "完成后自动恢复。\n\n"
            "机械硬盘用户：切换后台窗口时可能严重卡顿，\n"
            "因为页面需从硬盘重新读入。SSD 用户通常无感。\n\n"
            "保护：前台窗口、explorer、dwm、csrss、lsass 等核心进程。\n\n确定继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.memory_btn.setEnabled(False)
        self.migrate_btn.setEnabled(False)
        self.refresh_btn.setEnabled(False)
        self.progress.setValue(0)
        self.log_edit.clear()
        self.status_label.setText("正在优化内存...")

        self.memory_worker = MemoryWorker()
        self.memory_worker.progress.connect(self._on_progress)
        self.memory_worker.finished.connect(self._on_memory_finished)
        self.memory_worker.start()

    def _on_memory_finished(self, result: dict):
        self.memory_btn.setEnabled(True)
        self.migrate_btn.setEnabled(True)
        self.refresh_btn.setEnabled(True)
        self.status_label.setText(f"共扫描到 {len(self.apps)} 个应用" if self.apps else "就绪")

        if result.get("success"):
            self.progress.setValue(100)
            details = result.get("details", [])
            detail_text = "\n".join(f"  - {d}" for d in details) if details else ""
            QMessageBox.information(
                self,
                "内存优化完成",
                f"{result['message']}\n\n"
                f"优化详情:\n{detail_text}",
            )
        else:
            self.progress.setValue(0)
            QMessageBox.critical(self, "优化失败", result.get("message", "未知错误"))

    def mousePressEvent(self, event):
        # 浮层不参与窗口拖动（由 AI 面板统一定位），仅记录点击以便外部收起逻辑判断
        super().mousePressEvent(event)
