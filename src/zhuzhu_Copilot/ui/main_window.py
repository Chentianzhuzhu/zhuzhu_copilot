import os
import sys
import time
import ctypes
import threading
import webbrowser
from pathlib import Path
from typing import List

from zhuzhu_Copilot.core.i18n import ui as _ui, uim as _uim, uif as _uif

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
from zhuzhu_Copilot.ui.tokens import FONT_WEIGHT
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
_ICON_FEEDBACK = ('<svg width="16" height="16" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
                  '<path d="M21 12a8 8 0 0 1-8 8H4l2-3.2A8 8 0 1 1 21 12z" stroke="{C}" stroke-width="2" fill="none" stroke-linejoin="round"/>'
                  '<path d="M8.5 11h7M8.5 14h4" stroke="{C}" stroke-width="2" stroke-linecap="round"/></svg>')

# ---------- 统一控件样式（颜色取自 PALETTE，无硬编码；随主题就地重建） ----------
def _qss_weights() -> dict:
    """招 2：字重档位随主题降档（暗色底粗体发糊，标题/按钮统一减细一档）。"""
    from PyQt6.QtGui import QColor
    return FONT_WEIGHT["dark" if QColor(PALETTE["bg_top"]).lightness() < 128 else "light"]


def _soft_fill(ratio: float = 0.14) -> str:
    """招 1：把描边色以低比例压在卡片底上，得到「无边框的柔和填充」
    （ghost 按钮 / 工具按钮的轮廓来源，替代原来的 1px 描边）。"""
    from PyQt6.QtGui import QColor
    b, c = QColor(PALETTE["border"]), QColor(PALETTE["card"])
    return QColor(int(b.red() * ratio + c.red() * (1 - ratio)),
                  int(b.green() * ratio + c.green() * (1 - ratio)),
                  int(b.blue() * ratio + c.blue() * (1 - ratio))).name()


def _regen_qss():
    """按当前 PALETTE 重建模块级派生样式常量（主题切换时调用，避免残留旧主题色）"""
    global _BTN_PRIMARY_QSS, _BTN_DANGER_QSS, _BTN_GHOST_QSS, _BTN_TOOL_QSS
    global _SEARCH_QSS, _SECTION_TITLE, _BADGE_QSS, _FIELD_LABEL
    _w = _qss_weights()
    _ghost_fill = _soft_fill()
    _ghost_fill_hover = _soft_fill(0.24)
    _BTN_PRIMARY_QSS = (f"QPushButton {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                        f" stop:0 {PALETTE['primary_hover']}, stop:1 {PALETTE['primary']});"
                        " color: #FFFFFF; border: none;"
                        f" border-radius: 10px; padding: 0 18px; font-size: 13px; font-weight: {_w['title']}; }}"
                        f"QPushButton:hover {{ background: {PALETTE['primary_hover']}; }}"
                        f"QPushButton:pressed {{ background: {PALETTE['primary_pressed']}; }}"
                        f"QPushButton:disabled {{ background: {PALETTE['border']}; color: {PALETTE['text_secondary']}; }}")
    _BTN_DANGER_QSS = (f"QPushButton {{ background: {PALETTE['danger']}; color: #FFFFFF; border: none;"
                       f" border-radius: 10px; padding: 0 18px; font-size: 13px; font-weight: {_w['title']}; }}"
                       f"QPushButton:hover {{ background: {_lighten(PALETTE['danger'])}; }}"
                       f"QPushButton:pressed {{ background: {PALETTE['danger']}; }}"
                       f"QPushButton:disabled {{ background: {PALETTE['border']}; color: {PALETTE['text_secondary']}; }}")
    # 招 1：ghost / 工具按钮不描边，用低透明填充划轮廓；hover 填充加深、文字提亮
    _BTN_GHOST_QSS = (f"QPushButton {{ background: {_ghost_fill}; color: {PALETTE['text']};"
                      f" border: none; border-radius: 10px; padding: 0 14px;"
                      f" font-size: 13px; font-weight: {_w['button']}; }}"
                      f"QPushButton:hover {{ color: {PALETTE['text']};"
                      f" background: {_ghost_fill_hover}; }}"
                      f"QPushButton:pressed {{ background: {PALETTE['primary_light']}; }}"
                      f"QPushButton:disabled {{ color: {PALETTE['text_secondary']}; }}")
    _BTN_TOOL_QSS = (f"QPushButton {{ background: {_ghost_fill}; color: {PALETTE['text_secondary']};"
                     f" border: none; border-radius: 8px; padding: 0 12px;"
                     f" font-size: 12px; font-weight: {_w['button']}; }}"
                     f"QPushButton:hover {{ color: {PALETTE['text']};"
                     f" background: {_ghost_fill_hover}; }}"
                     f"QPushButton:pressed {{ background: {PALETTE['primary_light']}; color: {PALETTE['text']}; }}")
    # 招 1：搜索框改「凹陷字段」（比窗口底暗一档），聚焦整体加深，不描边
    _SEARCH_QSS = (f"QLineEdit {{ background: {PALETTE['field']}; color: {PALETTE['text']};"
                   f" border: none; border-radius: 10px; padding: 8px 12px;"
                   " font-size: 13px; }"
                   f"QLineEdit:hover {{ background: {PALETTE['hover']}; }}"
                   f"QLineEdit:focus {{ background: {PALETTE['hover']}; }}")
    _SECTION_TITLE = f"font-size: 11px; font-weight: {_w['strong']}; color: {PALETTE['text_secondary']};"
    _BADGE_QSS = (f"font-size: 11px; font-weight: {_w['strong']}; color: {PALETTE['text']};"
                  f" background: {PALETTE['primary_light']}; border-radius: 9px; padding: 2px 9px;")
    _FIELD_LABEL = f"font-size: 12px; font-weight: {_w['button']}; color: {PALETTE['text_secondary']};"


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
            f" font-size: 12px; font-weight: {_qss_weights()['button']}; }}"
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
        # 桌宠随程序启动（异步：主窗口先显示，3D 资产生成放到后台线程，
        # 资源缺失或加载失败时静默回退 GIF，不阻塞启动路径）
        self._pet = None
        try:
            from zhuzhu_Copilot.ui.desktop_pet import start_pet_async
            start_pet_async(self)
        except Exception:
            pass
        # 记忆上次选择：开启过防护则下次启动自动开启
        if self._settings.value("security_auto", False, type=bool) and is_admin():
            QTimer.singleShot(0, self._start_security)
        # Copilot 插件扩展（应用级：页面/服务/托盘/快捷键），在托盘就绪之后加载
        try:
            self._load_copilot_extensions()
        except Exception:
            pass
        self._start_scan()
        self._init_update_check()
        # 启动就绪后按当前主题统一落地一次（同步 app 级 QPalette/控件样式）
        QTimer.singleShot(0, self._apply_theme)

    def _open_agent_panel(self, *_):
        """兼容既有调用方（桌宠点击、托盘）：

        本面板已不再拥有 AI 面板，改为发出请求信号，由 AgentPanel 负责显示。"""
        self.open_agent_requested.emit()

    def shutdown(self):
        """应用退出前清理：停防护线程、停 Copilot 插件服务、关桌宠与托盘（浮层显隐由 AgentPanel 管理，不在此关闭窗口）"""
        if self.security_worker and self.security_worker.isRunning():
            self.security_worker.stop()
            if not self.security_worker.wait(3000):
                self.security_worker.terminate()
        try:
            self._shutdown_copilot_extensions()
        except Exception:
            pass
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
                f" color: #FFFFFF; font-size: 13px; font-weight: {_qss_weights()['title']}; border-radius: 7px;")

    def _msg_box_qss(self) -> str:
        """QMessageBox 等原生弹窗样式（随主题色板重建）"""
        return (
            f"QMessageBox {{ background-color: {PALETTE['card']}; }}"
            f"QMessageBox QLabel {{ color: {PALETTE['text']}; font-size: 13px; }}"
            f"QMessageBox QPushButton {{ background-color: {PALETTE['primary']};"
            "color: #FFFFFF; border: none; border-radius: 8px;"
            f"padding: 7px 20px; min-width: 72px; font-weight: {_qss_weights()['button']}; }}"
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
            from zhuzhu_Copilot.core import ui_infra
            ui_infra.apply_rounded_window(self, self.ROUND_RADIUS)
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
                    f"font-size: 15px; font-weight: {_qss_weights()['title']}; color: {PALETTE['text']};")
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
        title.setStyleSheet(f"font-size: 15px; font-weight: {_qss_weights()['title']}; color: {PALETTE['text']};")
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
        self.admin_label = QLabel(_ui("未提权"))
        self.admin_label.setStyleSheet(f"font-size: 12px; color: {PALETTE['text_secondary']};")
        admin_lay.addWidget(self.admin_label)
        layout.addWidget(admin_box)

        close_btn = _IconTitleButton(_ICON_CLOSE, _ICON_CLOSE_HOVER)
        close_btn.setToolTip(_ui("收起面板"))
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
            # 标签文案在**此处**过 _ui：元组是中文原文（i18n 的 key），
            # 不能在模块导入期求值，否则切换语言后不再刷新。
            b = QPushButton(_ui(name))
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
        t = QLabel(_ui("应用"))
        t._section = True
        t.setStyleSheet(_SECTION_TITLE)
        head.addWidget(t)
        self._app_count = QLabel("0")
        self._app_count.setStyleSheet(_BADGE_QSS)
        head.addWidget(self._app_count)
        head.addStretch(1)
        layout.addLayout(head)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(_ui("搜索应用..."))
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
        self.status_label = QLabel(_ui("就绪"))
        self.status_label.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 12px;")
        bottom.addWidget(self.status_label)
        bottom.addStretch(1)

        self.refresh_btn = _icon_button(_ui("重新扫描"), _ICON_REFRESH, kind="tool", height=32)
        self.refresh_btn.clicked.connect(self._start_scan)
        bottom.addWidget(self.refresh_btn)

        self.update_btn = _icon_button(_ui("检查更新"), _ICON_UPDATE, kind="tool", height=32)
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

        layout.addWidget(self._section_label(_ui("应用详情")))
        self._info_box = QWidget()
        self._info_lay = QVBoxLayout(self._info_box)
        self._info_lay.setContentsMargins(0, 0, 0, 0)
        self._info_lay.setSpacing(10)
        layout.addWidget(self._info_box)
        self._show_empty_info()

        layout.addWidget(self._divider())
        layout.addWidget(self._section_label(_ui("迁移设置")))

        drive_row = QHBoxLayout()
        drive_row.setSpacing(10)
        dl = QLabel(_ui("目标盘符"))
        dl._hint = True
        dl.setStyleSheet(_FIELD_LABEL)
        drive_row.addWidget(dl)
        self.drive_combo = ArrowComboBox()
        self._populate_drives()
        drive_row.addWidget(self.drive_combo, 1)
        layout.addLayout(drive_row)

        tl = QLabel(_ui("目标路径"))
        tl._hint = True
        tl.setStyleSheet(_FIELD_LABEL)
        layout.addWidget(tl)
        target_row = QHBoxLayout()
        target_row.setSpacing(10)
        self.target_path_edit = QLineEdit()
        self.target_path_edit.setPlaceholderText(
            _ui("自动生成，可修改（如 D:\\Apps\\微信）"))
        self.drive_combo.currentIndexChanged.connect(self._refresh_target_path)
        target_row.addWidget(self.target_path_edit, 1)
        browse_btn = _icon_button(_ui("浏览"), _ICON_FOLDER, kind="ghost", height=40)
        browse_btn.clicked.connect(self._browse_target)
        target_row.addWidget(browse_btn)
        layout.addLayout(target_row)

        layout.addWidget(self._divider())
        layout.addWidget(self._section_label(_ui("迁移进度")))

        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        layout.addWidget(self.progress)

        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setPlaceholderText(_ui("迁移日志..."))
        self.log_edit.setMaximumHeight(120)
        layout.addWidget(self.log_edit)

        layout.addWidget(self._divider())
        layout.addWidget(self._section_label(_ui("操作")))

        self.migrate_btn = _icon_button(_ui("开始迁移"), _ICON_MIGRATE, kind="primary", height=44)
        self.migrate_btn.setEnabled(False)
        self.migrate_btn.clicked.connect(self._start_migration)
        self.uninstall_btn = _icon_button(_ui("强力卸载"), _ICON_TRASH, kind="danger", height=44)
        self.uninstall_btn.setEnabled(False)
        self.uninstall_btn.clicked.connect(self._start_uninstall)
        row1 = QHBoxLayout()
        row1.setSpacing(10)
        row1.addWidget(self.migrate_btn, 1)
        row1.addWidget(self.uninstall_btn, 1)
        layout.addLayout(row1)

        self.open_dir_btn = _icon_button(_ui("打开安装目录"), _ICON_FOLDER, height=40)
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
        cl.addWidget(self._section_label(_ui("静默安全防护")))
        hint = QLabel(_ui("后台巡检弹窗拦截、启动项、DNS 与勒索行为；开启后随程序常驻。"))
        hint.setWordWrap(True)
        hint._hint = True
        hint.setStyleSheet(f"font-size: 12px; color: {PALETTE['text_secondary']};")
        cl.addWidget(hint)

        self.security_btn = _icon_button(_ui("开启静默防护"), _ICON_SHIELD, height=40)
        self.security_btn.clicked.connect(self._toggle_security)
        self.security_settings_btn = _icon_button(_ui("防护设置"), _ICON_SETTINGS, height=40)
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
        sw = QLabel(_ui("安全提醒弹窗"))
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
        cl.addWidget(self._section_label(_ui("界面外观")))
        theme_row = QHBoxLayout()
        theme_row.setSpacing(10)
        lbl = QLabel(_ui("界面主题"))
        lbl._hint = True
        lbl.setStyleSheet(_FIELD_LABEL)
        lbl.setFixedWidth(70)
        theme_row.addWidget(lbl)
        self.theme_combo = ArrowComboBox()
        self.theme_combo.addItem(_ui("深色"), "dark")
        self.theme_combo.addItem(_ui("浅色"), "light")
        self.theme_combo.addItem(_ui("按时间自动（8-20 浅色）"), "auto")
        saved = str(app_identity.qsettings().value("agent_theme", "light"))
        ti = self.theme_combo.findData(saved)
        self.theme_combo.setCurrentIndex(ti if ti >= 0 else 1)
        self.theme_combo.currentIndexChanged.connect(self._on_theme_pick)
        theme_row.addWidget(self.theme_combo, 1)
        cl.addLayout(theme_row)

        cl.addWidget(self._divider())
        cl.addWidget(self._section_label(_ui("软件更新")))
        self.update_btn = _icon_button(_ui("检查更新"), _ICON_UPDATE, kind="tool", height=32)
        self.update_btn.clicked.connect(self._on_check_update_clicked)
        upd = QHBoxLayout()
        upd.addWidget(self.update_btn)
        upd.addStretch(1)
        cl.addLayout(upd)

        cl.addWidget(self._divider())
        cl.addWidget(self._section_label(_ui("关于")))
        about_btn = _icon_button(_ui("关于我们"), _ICON_ABOUT, kind="tool", height=32)
        about_btn.clicked.connect(self._show_about)
        website_btn = _icon_button(_ui("访问官网"), _ICON_LINK, kind="tool", height=32)
        website_btn.clicked.connect(self._open_website)
        feedback_btn = _icon_button(_ui("提交反馈"), _ICON_FEEDBACK, kind="tool", height=32)
        feedback_btn.clicked.connect(self._open_feedback_dialog)
        ab = QHBoxLayout()
        ab.addWidget(about_btn)
        ab.addWidget(website_btn)
        ab.addWidget(feedback_btn)
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
        cl.addWidget(self._section_label(_ui("工具")))

        self.download_btn = _icon_button(_ui("高速下载"), _ICON_UPDATE, height=40)
        self.download_btn.clicked.connect(self._open_download_dialog)
        self.memory_btn = _icon_button(_ui("一键优化内存"), _ICON_MEMORY, height=40)
        self.memory_btn.clicked.connect(self._start_memory_optimize)
        custom_btn = _icon_button(_ui("迁移自定义文件夹"), _ICON_FOLDER, height=40)
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
        tip = QLabel(_ui("从左侧选择一个应用以查看详情"))
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
        name = QLabel(app.name or _ui("未知"))
        name.setStyleSheet(f"font-size: 16px; font-weight: {_qss_weights()['title']}; color: {PALETTE['text']};")
        name_row.addWidget(name)
        tag = QLabel(app.app_type)
        tag.setStyleSheet(self._type_badge(app.app_type))
        name_row.addWidget(tag)
        name_row.addStretch(1)
        names.addLayout(name_row)

        meta = QLabel(_uif("{a0}  ·  {a1}",
                       a0=app.version or _ui("未知版本"),
                       a1=format_size(app.size_bytes)))
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
        return (f"font-size: 11px; font-weight: {_qss_weights()['strong']}; color: #FFFFFF; background: {color};"
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
        self.tray.setToolTip(_ui("zhuzhu Copilot · 静默安全防护"))
        menu = QMenu(self)
        act_show = menu.addAction(_ui("打开 AI 面板"))
        act_show.triggered.connect(self._open_agent_panel)
        act_quit = menu.addAction(_ui("退出程序"))
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

    # ---------- Copilot 插件扩展（应用级，独立于 Agent 插件体系） ----------

    def _load_copilot_extensions(self):
        """加载本窗口可见的 Copilot 插件扩展（页面 + 应用级扩展）。"""
        self._copilot_ext = {"pages": [], "services": [], "tray": [],
                             "shortcuts": []}
        load_copilot_extensions(panel=self)

    def _import_copilot_module(self, name: str, d: Path, rel: str):
        """从插件包目录动态导入模块（按文件路径加载；失败抛异常由调用方捕获）。"""
        return import_copilot_module(name, d, rel)

    def _register_copilot_entry(self, name: str, d: Path, section: str,
                                entry: dict):
        """登记单条扩展（pages/services/tray/shortcuts）。"""
        return register_copilot_entry(self, name, d, section, entry)

    def _register_copilot_entry_impl(self, name: str, d: Path, section: str,
                                     entry: dict):
        if section == "pages":
            mod = self._import_copilot_module(name, d, entry.get("module"))
            widget_cls = getattr(mod, str(entry.get("widget") or ""), None)
            if widget_cls is None:
                raise AttributeError(f"页面类不存在: {entry.get('widget')}")
            idx = self._pages.count()
            b = QPushButton(_ui(str(entry.get("title") or name)))
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setMinimumHeight(26)
            b.setStyleSheet(_tab_qss())
            self._tab_group.addButton(b, idx)
            lay = self._tab_wrap.layout()
            lay.insertWidget(max(lay.count() - 1, 0), b)
            page = widget_cls(self._pages)
            self._pages.addWidget(page)
            self._copilot_ext["pages"].append(
                {"plugin": name, "id": entry.get("id"), "index": idx})
        elif section == "services":
            mod = self._import_copilot_module(name, d, entry.get("module"))
            start = getattr(mod, str(entry.get("entry") or ""), None)
            if not callable(start):
                raise AttributeError(f"服务入口函数不存在: {entry.get('entry')}")
            handle = start()
            self._copilot_ext["services"].append(
                {"plugin": name, "id": entry.get("id"), "module": mod,
                 "stop": str(entry.get("stop") or ""), "handle": handle})
        elif section == "tray":
            mod = self._import_copilot_module(name, d, entry.get("module"))
            action = getattr(mod, str(entry.get("action") or ""), None)
            if not callable(action):
                raise AttributeError(f"托盘动作函数不存在: {entry.get('action')}")
            menu = self.tray.contextMenu()
            act = menu.addAction(_ui(str(entry.get("title") or name)))
            act.triggered.connect(lambda *a, _f=action: _f())
            self._copilot_ext["tray"].append(
                {"plugin": name, "id": entry.get("id"), "action": act})
        elif section == "shortcuts":
            from PyQt6.QtGui import QShortcut, QKeySequence
            mod = self._import_copilot_module(name, d, entry.get("module"))
            action = getattr(mod, str(entry.get("action") or ""), None)
            if not callable(action):
                raise AttributeError(f"快捷键动作函数不存在: {entry.get('action')}")
            sc = QShortcut(QKeySequence(str(entry.get("hotkey") or "")), self)
            sc.activated.connect(lambda *a, _f=action: _f())
            self._copilot_ext["shortcuts"].append(
                {"plugin": name, "id": entry.get("id"), "shortcut": sc})

    def _shutdown_copilot_extensions(self):
        """退出清理：停后台服务、移除托盘动作与快捷键（页面随窗口销毁）。"""
        ext = getattr(self, "_copilot_ext", None) or {}
        for svc in reversed(ext.get("services") or []):
            stop = getattr(svc.get("module"), svc.get("stop") or "", None)
            if callable(stop):
                try:
                    stop()
                except Exception:
                    pass
            handle = svc.get("handle")
            closer = getattr(handle, "stop", None) or getattr(handle, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:
                    pass
        for act in ext.get("tray") or []:
            try:
                menu = self.tray.contextMenu()
                if menu is not None:
                    menu.removeAction(act.get("action"))
            except Exception:
                pass
        for sc in ext.get("shortcuts") or []:
            try:
                sc.get("shortcut").setEnabled(False)
                sc.get("shortcut").deleteLater()
            except Exception:
                pass
        self._copilot_ext = {"pages": [], "services": [], "tray": [],
                             "shortcuts": []}

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
            QMessageBox.warning(self, _ui("权限不足"), _ui("静默防护需要管理员权限。"))
            return
        self._settings.setValue("security_auto", True)
        self.security_worker = SecurityMonitorWorker(self)
        self.security_worker.result.connect(self._on_security_result)
        self.security_worker.start()
        self._security_on = True
        self.security_btn.setText(_ui("静默防护运行中"))
        self.status_label.setText(_ui("静默防护运行中，正在后台监控…"))
        self.tray.show()
        if self._toast_enabled:
            self.toast.show_toast(
                "静默防护",
                _uim("已开启\n后台监控恶意进程、启动项与网络风险，拦截结果将在此提示。"),
                False, 4000,
            )

    def _stop_security(self):
        """关闭静默防护"""
        if self.security_worker and self.security_worker.isRunning():
            self.security_worker.stop()
            self.security_worker.wait(3000)
        self._settings.setValue("security_auto", False)
        self._security_on = False
        self.security_btn.setText(_ui("开启静默防护"))
        self.tray.hide()
        self.status_label.setText(_ui("静默防护已关闭"))

    def _show_security_settings(self):
        """防护参数设置面板：模块开关 + 隔离区管理"""
        from zhuzhu_Copilot.core.security_engine.config import config
        from zhuzhu_Copilot.core.security_engine.quarantine import quarantine

        dlg = QDialog(self)
        dlg.setWindowTitle(_ui("防护设置"))
        dlg.setMinimumWidth(480)
        lay = QVBoxLayout(dlg)
        lay.setSpacing(12)

        sec = QLabel(_ui("防护模块"))
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

        qsec = QLabel(_ui("隔离区"))
        qsec.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 12px;")
        lay.addWidget(qsec)

        lst = QListWidget()
        lst.setMinimumHeight(120)
        for item in quarantine.list_items():
            it = QListWidgetItem(_uif("{a0}  ·  {a1}",
                                a0=item["name"],
                                a1=item.get("reason") or _ui("手动隔离")))
            it.setData(Qt.ItemDataRole.UserRole, item.get("id", ""))
            lst.addItem(it)
        if lst.count() == 0:
            lst.addItem(_ui("隔离区为空"))
        lay.addWidget(lst)

        rowq = QHBoxLayout()
        restore_btn = _icon_button(_ui("恢复选中"), _ICON_REFRESH, height=36)
        open_btn = _icon_button(_ui("打开隔离区"), _ICON_FOLDER, height=36)

        def _restore():
            cur = lst.currentItem()
            ident = cur.data(Qt.ItemDataRole.UserRole) if cur else ""
            if not ident:
                return
            if quarantine.restore(ident):
                QMessageBox.information(dlg, _ui("恢复成功"), _ui("文件已恢复到原位置。"))
                dlg.accept()
            else:
                QMessageBox.warning(dlg, _ui("恢复失败"), _ui("文件不存在或原位置被占用。"))

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

        close_btn = _icon_button(_ui("关闭"), _ICON_CLOSE, kind="primary", height=36)
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
            self.admin_label.setText(_ui("管理员权限"))
            self.admin_label.setStyleSheet(f"font-size: 12px; color: {PALETTE['success']};")
        else:
            self._admin_dot.setStyleSheet(f"background: {PALETTE['danger']}; border-radius: 4px;")
            self.admin_label.setText(_ui("未提权"))
            self.admin_label.setStyleSheet(f"font-size: 12px; color: {PALETTE['danger']};")
            # 非模态 + 只提示一次：
            # 1) 用 warning()（模态）会在每次未提权启动时弹一个阻塞窗，把随后所有
            #    点击全部吞掉 —— 用户观感正是「点什么都只没反应、但也不是未响应」，
            #    与最大化/还原连点时的假死投诉完全同源。改 open() 让对话框不抢输入。
            # 2) 已经提示过就不再烦：状态灯常驻「未提权」，需要时用户自己会去右键提权。
            try:
                if self._settings.value("admin_warned", False, type=bool):
                    return
                self._settings.setValue("admin_warned", True)
            except Exception:
                pass
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle(_ui("需要管理员权限"))
            box.setText(_uim("本工具需要管理员权限才能创建目录联接和修改系统目录。\n"
                             "请右键以管理员身份运行。"))
            box.setStandardButtons(QMessageBox.StandardButton.Ok)
            box.open()   # 非模态：不阻塞主事件循环，不吞掉面板上的点击

    def _start_scan(self, *_):
        self.app_list.clear()
        self.status_label.setText(_ui("正在扫描已安装应用..."))
        self.scan_progress.show()
        self.refresh_btn.setEnabled(False)
        self.scan_worker = ScanWorker()
        self.scan_worker.finished.connect(self._on_scan_finished)
        self.scan_worker.error.connect(self._on_scan_error)
        self.scan_worker.start()

    def _on_scan_finished(self, apps: List[AppInfo]):
        self.apps = apps
        self._filter_apps()
        self.status_label.setText(_uif("共扫描到 {n} 个应用", n=len(apps)))
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
        self.status_label.setText(_uif("扫描失败: {a0}", a0=msg))
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
                QMessageBox.warning(self, _ui("无法打开"), _uif('无法打开目录：{a0}\n{a1}', a0=loc, a1=e))
        else:
            QMessageBox.warning(self, _ui("无法打开"), _uif("目录不存在：{a0}", a0=loc))

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
            self, _ui("选择目标位置（可在对话框内新建文件夹）"), start,
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
            QMessageBox.warning(self, _ui("路径无效"), _ui("目标路径必须是绝对路径，例如 D:\\Apps\\微信"))
            return None
        # 盘符根目录（如 C:\）作为目标时，自动在其下创建 app 同名目录
        if path.anchor and str(path).rstrip("\\").lower() == path.anchor.rstrip("\\").lower():
            path = path / self.selected_app.name
        # 目标已存在不在此拒绝，交由 _start_migration 提供 覆盖/自动改名 处理
        if path == self.selected_app.install_location:
            QMessageBox.warning(self, _ui("路径无效"), _ui("目标路径不能与源路径相同"))
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
            QMessageBox.warning(self, _ui("权限不足"), _ui("请以管理员身份运行本工具。"))
            return

        drive = Path(self.drive_combo.currentData())
        target = self._resolve_target()
        if target is None:
            return
        # 目标目录已存在：提供 覆盖/自动改名 处理，避免直接失败
        if target.exists():
            reply = QMessageBox.question(
                self,
                _ui("目标目录已存在"),
                _uim("目标位置已存在：<b>{target}</b>\n\n"
                     "选择「覆盖」将删除该目录后迁移（原内容不可恢复）；\n"
                     "选择「改名」将自动在名称后加序号迁移到新位置。")
                .format(target=target),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel,
            )
            if reply == QMessageBox.StandardButton.Yes:
                if not safe_remove(target):
                    QMessageBox.warning(self, _ui("无法覆盖"), _uif("无法删除已存在的目标目录：{a0}", a0=target))
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
            _ui("确认迁移"),
            _uim("确定将 <b>{app}</b> 迁移到 <b>{target}</b> 吗？\n"
                 "迁移后旧安装目录将被删除，注册表与快捷方式将更新到新路径。")
            .format(app=self.selected_app.name, target=target),
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
            _ui("目标目录已存在"),
            _uim("目标位置已存在同名目录：\n<b>{target}</b>\n\n"
                 "是否删除该目录并替换？\n（源目录：{source}）\n\n"
                 "选择「否」将跳过该目录的迁移。")
            .format(target=target, source=source),
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
            _ui("360 自我保护"),
            _uim("检测到 360 安全卫士的进程无法被强制结束。\n"
                 "360 的自我保护会在系统内核层拦截强制终止，属于其正常安全机制。\n\n"
                 "请任选其一操作后再重试：\n"
                 "1. 右键 360 托盘图标 → 退出 360\n"
                 "2. 打开 360 设置 → 防护中心 → 关闭「自我保护」\n\n"
                 "未能结束的进程：{names}").format(names=names),
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
            QMessageBox.information(self, _ui("迁移成功"), msg)
        else:
            self.progress.setValue(0)
            QMessageBox.critical(self, _ui("迁移失败"), result.get("message", "未知错误"))

    def _start_uninstall(self, *_):
        if not self.selected_app:
            return
        if not is_admin():
            QMessageBox.warning(self, _ui("权限不足"), _ui("请以管理员身份运行本工具。"))
            return
        # 后台构建卸载清单（注册表/快捷方式预扫描），期间 UI 保持响应
        self.migrate_btn.setEnabled(False)
        self.refresh_btn.setEnabled(False)
        self.uninstall_btn.setEnabled(False)
        self.status_label.setText(_ui("正在分析应用..."))
        self.build_worker = BuildPlanWorker(self.selected_app, self.uninstaller)
        self.build_worker.plan_ready.connect(self._on_plan_ready)
        self.build_worker.error.connect(self._on_plan_error)
        self.build_worker.start()

    def _restore_buttons(self):
        self.migrate_btn.setEnabled(True)
        self.refresh_btn.setEnabled(True)
        self.uninstall_btn.setEnabled(True)
        if hasattr(self, "status_label"):
            self.status_label.setText(
                _uif("共扫描到 {n} 个应用", n=len(self.apps)) if self.apps else _ui("就绪"))

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
        QMessageBox.critical(self, _ui("分析失败"), _uif("无法分析该应用: {a0}", a0=msg))

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
            QMessageBox.information(self, _ui("卸载完成"), msg)
            self._start_scan()  # 刷新应用列表
        else:
            self.progress.setValue(0)
            QMessageBox.critical(self, _ui("卸载失败"), result.get("message", "未知错误"))

    def _migrate_custom_folder(self, *_):
        if not is_admin():
            QMessageBox.warning(self, _ui("权限不足"), _ui("请以管理员身份运行本工具。"))
            return
        folder = QFileDialog.getExistingDirectory(self, _ui("选择要迁移的文件夹"))
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
            self, _ui("关于我们"),
            _ui("开发者是一名14岁的初中生，通过vibe coding开发而来"))

    def _open_website(self, *_):
        """打开默认浏览器访问官网关于我们页"""
        webbrowser.open("https://chentian.dpdns.org/about")

    def _open_feedback_dialog(self, *_):
        """打开用户反馈对话框（模态，parent=主窗口以保证居中与模态归属）"""
        from zhuzhu_Copilot.ui.feedback_dialog import FeedbackDialog
        dlg = FeedbackDialog(self)
        dlg.exec()

    # ---------- 自动更新检查（30s 轮询更新服务器） ----------
    def _init_update_check(self):
        self._update_checker = UpdateChecker(self)
        self._update_checker.update_found.connect(self._on_update_found)
        self._update_checker.manual_result.connect(self._on_manual_check_result)
        self._update_checker.start()

    def _on_check_update_clicked(self, *_):
        """手动检查更新：调用服务器 API 并反馈结果"""
        self.update_btn.setText(_ui("检查中…"))
        self.update_btn.setEnabled(False)
        self._update_checker.check(manual=True)

    def _on_manual_check_result(self, result: dict):
        """手动检查结果反馈：有更新/已最新/失败等"""
        self.update_btn.setText(_ui("检查更新"))
        self.update_btn.setEnabled(True)
        status = result.get("status")
        if status == "ok":
            self._on_update_found(result.get("info") or {})
        elif status == "none":
            QMessageBox.information(self, _ui("检查更新"), _uif("已是最新版本（v{a0}）", a0=APP_VERSION))
        elif status == "noserver":
            QMessageBox.warning(self, _ui("检查更新"), _ui("未配置更新服务器地址"))
        elif status == "busy":
            QMessageBox.information(self, _ui("检查更新"), _ui("正在检查更新，请稍候…"))
        else:
            err = result.get("error") or "网络异常"
            hint = ""
            # 更新服务器常挂在 Cloudflare 后面，企业网关可能只封了它：
            # 只要应用自身请求正常，就明确告诉用户「不影响使用」，别再吓人。
            try:
                from zhuzhu_Copilot.core import net_status
                if net_status.traffic_ok():
                    hint = _ui("\n\n（网络连接正常，仅更新服务器不可达；不影响模型对话与日常使用。）")
            except Exception:
                pass
            QMessageBox.warning(self, _ui("检查更新"), _uif("检查更新失败：{a0}", a0=err) + hint)

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
                self, _ui("发现强制更新"),
                text + _uim("\n\n当前版本已停止服务，必须更新后才能继续使用。\n\n"
                           "更新页已打开，本窗口关闭后应用将退出。"))
            webbrowser.open(info["url"])
            self._exit_forced()
            return
        ret = QMessageBox.question(
            self, _ui("发现新版本"), text + "\n\n是否前往下载更新？",
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
            _ui("确认优化内存 - 核弹级方案"),
            _uim("核弹级方案：\n"
                 "1. NtOpenProcess 兜底覆盖受保护进程（解决 OpenProcess 拒绝访问）\n"
                 "2. 暂停所有非核心进程 → 硬限制工作集为 1 字节\n"
                 "3. 分配大块内存制造极端内存压力 → 强迫内核主动 trim\n"
                 "4. 多次清空 standby list 彻底释放物理 RAM\n\n"
                 "执行期间：后台窗口会短暂冻结（暂停进程中），\n"
                 "完成后自动恢复。\n\n"
                 "机械硬盘用户：切换后台窗口时可能严重卡顿，\n"
                 "因为页面需从硬盘重新读入。SSD 用户通常无感。\n\n"
                 "保护：前台窗口、explorer、dwm、csrss、lsass 等核心进程。\n\n确定继续？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.memory_btn.setEnabled(False)
        self.migrate_btn.setEnabled(False)
        self.refresh_btn.setEnabled(False)
        self.progress.setValue(0)
        self.log_edit.clear()
        self.status_label.setText(_ui("正在优化内存..."))

        self.memory_worker = MemoryWorker()
        self.memory_worker.progress.connect(self._on_progress)
        self.memory_worker.finished.connect(self._on_memory_finished)
        self.memory_worker.start()

    def _on_memory_finished(self, result: dict):
        self.memory_btn.setEnabled(True)
        self.migrate_btn.setEnabled(True)
        self.refresh_btn.setEnabled(True)
        self.status_label.setText(
            _uif("共扫描到 {n} 个应用", n=len(self.apps)) if self.apps else _ui("就绪"))

        if result.get("success"):
            self.progress.setValue(100)
            details = result.get("details", [])
            detail_text = "\n".join(f"  - {d}" for d in details) if details else ""
            QMessageBox.information(
                self,
                _ui("内存优化完成"),
                _uif('{a0}\n\n优化详情:\n{a1}', a0=result['message'], a1=detail_text),
            )
        else:
            self.progress.setValue(0)
            QMessageBox.critical(self, _ui("优化失败"), result.get("message", "未知错误"))

    def mousePressEvent(self, event):
        # 浮层不参与窗口拖动（由 AI 面板统一定位），仅记录点击以便外部收起逻辑判断
        super().mousePressEvent(event)


# ════════════════════════════════════════════════════════════════
# Copilot 插件扩展（模块级实现，AgentPanel 与 CopilotPanel 共用）
# ════════════════════════════════════════════════════════════════

#: 进程内已启动的应用级扩展（服务/托盘/快捷键）：保证只启动一次
#: （主界面启动时加载一次；之后创建 Copilot 浮层不会重复 start）。
_COPILOT_EXT_STATE = {"started": set(), "services": [], "tray": [], "shortcuts": [],
                      "host": None}   # host：页面宿主（MainWindow），停用时据此摘页签


def _ext_pkg_name(name: str) -> str:
    """插件在 sys.modules 里的合成包名（每插件一个包，支撑包内相对导入）。"""
    import re as _re
    return "_copilot_ext_" + _re.sub(r"\W", "_", str(name))


def _ensure_ext_package(name: str, d: Path) -> str:
    """为插件目录建合成包（__path__ 指向插件目录），支持插件内多模块/相对导入。

    这是热插拔的基础设施：旧实现把每个文件映射成扁平顶层模块，插件内部
    `from . import xxx` / `from .services.xxx import y` 全部会失败；
    而且重新启用时旧模块对象残留，改了代码也拿到旧版本。
    """
    import sys
    import types
    d = Path(d).resolve()
    pkg_name = _ext_pkg_name(name)
    pkg = sys.modules.get(pkg_name)
    if pkg is None or list(getattr(pkg, "__path__", []) or []) != [str(d)]:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(d)]
        pkg.__package__ = pkg_name
        pkg.__file__ = str(d / "__init__.py")
        sys.modules[pkg_name] = pkg
    return pkg_name


def import_copilot_module(name: str, d: Path, rel: str):
    """从插件包目录按相对路径动态导入模块（失败抛异常由调用方捕获）。

    每次调用都重新执行磁盘最新代码（热插拔：停用再启用立即拿到新实现）。
    模块挂在 `_copilot_ext_<plugin>.<路径>` 合成包下，支持包内相对导入。
    """
    import sys
    import importlib.util
    import types
    d = Path(d).resolve()
    p = (d / str(rel)).resolve()
    if not str(p).startswith(str(d)) or not p.is_file():
        raise FileNotFoundError(f"模块不存在: {rel}")
    pkg_name = _ensure_ext_package(name, d)
    parts = list(Path(str(rel)).with_suffix("").parts)
    # 补齐中间父包（合成 namespace package），保证相对导入链完整
    for i in range(1, len(parts)):
        sub_name = pkg_name + "." + ".".join(parts[:i])
        if sub_name not in sys.modules:
            sub = types.ModuleType(sub_name)
            sub.__path__ = [str(d.joinpath(*parts[:i]))]
            sub.__package__ = sub_name
            sys.modules[sub_name] = sub
    mod_name = pkg_name + "." + ".".join(parts)
    # 旧版本先摘掉（同一次启用中重复调用也以磁盘代码为准）
    sys.modules.pop(mod_name, None)
    spec = importlib.util.spec_from_file_location(mod_name, str(p))
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载模块: {rel}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def unload_extension_modules(name: str) -> None:
    """停用/删除插件时把它的合成包模块全部摘出 sys.modules。

    不摘的话再次启用会命中旧模块对象（"改了没生效"），也避免长期运行的插件
    旧版本在 sys.modules 里无限堆积。
    """
    import sys
    pkg_name = _ext_pkg_name(name)
    prefix = pkg_name + "."
    for m in [m for m in list(sys.modules) if m == pkg_name or m.startswith(prefix)]:
        sys.modules.pop(m, None)


def register_copilot_entry(panel, name: str, d: Path, section: str, entry: dict):
    """登记单条 Copilot 插件扩展。

    · pages      —— 需要面板（panel 非 None 才登记）：默认追加为浮层页签；
                    条目带 `"window": "glass"` 时改为**独立无框液体玻璃窗口**
                    （应用启动即常驻浮现，`auto_show` 控制是否自动显示）
    · services   —— 进程级启动一次（主界面启动时即加载，不依赖浮层）
    · tray       —— 追加到托盘菜单（需要面板有 tray）
    · shortcuts  —— 全局快捷键（挂在 panel 上）

    热插拔：每条登记都在 `_COPILOT_EXT_STATE` 里留下可撤销的句柄，
    由 `unregister_copilot_plugin()` 在停用/删除时逐一移除（页签、窗口、托盘、快捷键）。
    """
    if panel is not None:
        _COPILOT_EXT_STATE["host"] = panel

    if section == "pages":
        if panel is None:
            return None          # 没有面板可挂页面：跳过（面板创建后再登记）
        key = f"{name}:{section}:{entry.get('id')}"
        if key in _COPILOT_EXT_STATE["started"]:
            return None          # 已登记过：不重复加页签
        mod = import_copilot_module(name, d, entry.get("module"))
        widget_cls = getattr(mod, str(entry.get("widget") or ""), None)
        if widget_cls is None:
            raise AttributeError(f"页面类不存在: {entry.get('widget')}")
        title = _ui(str(entry.get("title") or name))
        ext = getattr(panel, "_copilot_ext", None)
        mode = str(entry.get("window") or "").strip().lower()
        if mode == "glass":
            rec = _register_glass_page(panel, name, entry, widget_cls, title)
            if ext is not None and rec is not None:
                ext["pages"].append(rec)
            _COPILOT_EXT_STATE["started"].add(key)
            return None
        idx = panel._pages.count()
        b = QPushButton(title)
        b.setCheckable(True)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setMinimumHeight(26)
        b.setStyleSheet(_tab_qss())
        panel._tab_group.addButton(b, idx)
        lay = panel._tab_wrap.layout()
        lay.insertWidget(max(lay.count() - 1, 0), b)
        page = widget_cls(panel._pages)
        panel._pages.addWidget(page)
        if ext is not None:
            ext["pages"].append({"plugin": name, "id": entry.get("id"),
                                 "index": idx, "button": b, "widget": page})
        _COPILOT_EXT_STATE["started"].add(key)
        return None

    key = f"{name}:{section}:{entry.get('id')}"
    if key in _COPILOT_EXT_STATE["started"]:
        return None              # 已启动过：不重复注册
    if section == "services":
        mod = import_copilot_module(name, d, entry.get("module"))
        start = getattr(mod, str(entry.get("entry") or ""), None)
        if not callable(start):
            raise AttributeError(f"服务入口函数不存在: {entry.get('entry')}")
        handle = start()
        _COPILOT_EXT_STATE["services"].append(
            {"plugin": name, "id": entry.get("id"), "module": mod,
             "stop": str(entry.get("stop") or ""), "handle": handle})
    elif section == "tray":
        mod = import_copilot_module(name, d, entry.get("module"))
        action = getattr(mod, str(entry.get("action") or ""), None)
        if not callable(action):
            raise AttributeError(f"托盘动作函数不存在: {entry.get('action')}")
        act = None
        menu = None
        if panel is not None and getattr(panel, "tray", None) is not None:
            menu = panel.tray.contextMenu()
            act = menu.addAction(_ui(str(entry.get("title") or name)))
            act.triggered.connect(lambda *a, _f=action: _f())
        _COPILOT_EXT_STATE["tray"].append(
            {"plugin": name, "id": entry.get("id"), "action": act,
             "menu": menu, "callable": action})
    elif section == "shortcuts":
        from PyQt6.QtGui import QKeySequence, QShortcut
        mod = import_copilot_module(name, d, entry.get("module"))
        action = getattr(mod, str(entry.get("action") or ""), None)
        if not callable(action):
            raise AttributeError(f"快捷键动作函数不存在: {entry.get('action')}")
        sc = None
        if panel is not None:
            sc = QShortcut(QKeySequence(str(entry.get("hotkey") or "")), panel)
            sc.activated.connect(lambda *a, _f=action: _f())
        _COPILOT_EXT_STATE["shortcuts"].append(
            {"plugin": name, "id": entry.get("id"), "shortcut": sc,
             "callable": action})
    _COPILOT_EXT_STATE["started"].add(key)
    return None


def _register_glass_page(panel, name: str, entry: dict, widget_cls, title: str):
    """把一个插件页面放进独立的液体玻璃窗口（无框、置顶、常驻浮现）。"""
    from zhuzhu_Copilot.ui.glass_window import GlassWindow

    page = widget_cls()                 # 顶层窗口里的页面没有父容器
    win = GlassWindow(title=title,
                      width=int(entry.get("width") or 760),
                      height=int(entry.get("height") or 520),
                      content=page)
    pos = entry.get("position")
    auto = entry.get("auto_show", True)
    if isinstance(pos, (list, tuple)) and len(pos) == 2:
        try:
            win.move(int(pos[0]), int(pos[1]))
            auto and win.show()
        except (TypeError, ValueError):
            win.showCentered()
    elif auto:
        win.showCentered()
        # 已有玻璃窗口时依次错开，避免多个窗口完全重叠
        n = sum(1 for r in (_COPILOT_EXT_STATE.get("_glass") or [])
                if r is not None and r.isVisible())
        if n:
            win.move(win.x() + 34 * n, win.y() + 30 * n)
    lst = _COPILOT_EXT_STATE.setdefault("_glass", [])
    lst.append(win)
    _COPILOT_EXT_STATE["_glass"] = [w for w in lst if w is not None]
    return {"plugin": name, "id": entry.get("id"), "index": -1,
            "button": None, "widget": page, "window": win}


def unregister_copilot_plugin(name: str) -> None:
    """撤销某插件的全部应用级扩展（页签 / 玻璃窗口 / 托盘 / 快捷键）。

    这是**热插拔**的关键：停用或删除插件时立即把它从界面上摘掉，不必重启应用。
    服务由 `stop_copilot_plugin` 负责停（先停服务再摘 UI，避免回调打到已销毁控件）。
    """
    host = _COPILOT_EXT_STATE.get("host")
    ext = getattr(host, "_copilot_ext", None) if host is not None else None
    if ext is not None:
        kept = []
        for rec in ext.get("pages") or []:
            if str(rec.get("plugin") or "") != name:
                kept.append(rec)
                continue
            win = rec.get("window")
            if win is not None:
                try:
                    win.hide()
                    win.deleteLater()
                except Exception:
                    pass
                continue
            b, w = rec.get("button"), rec.get("widget")
            try:
                if host is not None and w is not None:
                    host._pages.removeWidget(w)
                    w.setParent(None)      # 立刻脱离父级（deleteLater 要等事件循环）
                    w.deleteLater()
                if b is not None:
                    host._tab_group.removeButton(b)
                    lay = host._tab_wrap.layout()
                    if lay is not None:
                        lay.removeWidget(b)
                    b.setParent(None)
                    b.deleteLater()
            except Exception:
                pass
        ext["pages"] = kept

    kept_tray = []
    for rec in _COPILOT_EXT_STATE.get("tray") or []:
        if str(rec.get("plugin") or "") == name:
            act, menu = rec.get("action"), rec.get("menu")
            try:
                if act is not None and menu is not None:
                    menu.removeAction(act)
            except Exception:
                pass
            continue
        kept_tray.append(rec)
    _COPILOT_EXT_STATE["tray"] = kept_tray

    kept_sc = []
    for rec in _COPILOT_EXT_STATE.get("shortcuts") or []:
        if str(rec.get("plugin") or "") == name:
            sc = rec.get("shortcut")
            try:
                if sc is not None:
                    sc.setEnabled(False)
                    sc.deleteLater()
            except Exception:
                pass
            continue
        kept_sc.append(rec)
    _COPILOT_EXT_STATE["shortcuts"] = kept_sc

    _COPILOT_EXT_STATE["started"].difference_update(
        {k for k in _COPILOT_EXT_STATE["started"] if k.startswith(f"{name}:")})


def start_copilot_plugin(name: str, panel=None) -> tuple:
    """运行期启用插件：立即加载其扩展，不必重启应用（热插拔）。

    panel 缺省时用启动时登记的宿主（MainWindow），因此从设置页点「启用」也能
    当场挂上页签 / 弹出玻璃窗口。只补未登记过的条目（`started` 去重），
    重复启用/多次调用都安全。返回 (ok, message)。
    """
    if panel is None:
        panel = _COPILOT_EXT_STATE.get("host")
    try:
        from zhuzhu_Copilot.core import copilot_plugins
        d = copilot_plugins.plugin_dir(name)
        man = copilot_plugins.load_copilot_manifest(d)
    except Exception as e:
        return False, f"读取插件「{name}」失败：{e}"
    try:
        for section, entry in copilot_plugins._iter_manifest_entries(man):
            register_copilot_entry(panel, name, d, section, entry)
    except Exception as e:
        return False, f"插件「{name}」扩展加载失败：{e}"
    return True, ""


def stop_copilot_plugin(name: str) -> None:
    """运行期停用插件：**先摘掉页签/玻璃窗口/托盘/快捷键，再停后台服务**。

    热插拔的关键顺序：先摘 UI 再停服务——反过来的话，服务回调可能打到已经
    deleteLater 的控件上（Qt 会抛 RuntimeError 或静默崩溃）。
    """
    try:
        unregister_copilot_plugin(name)
    except Exception:
        pass
    kept = []
    for svc in _COPILOT_EXT_STATE.get("services") or []:
        if str(svc.get("plugin") or "") != name:
            kept.append(svc)
            continue
        stop = getattr(svc.get("module"), svc.get("stop") or "", None)
        if callable(stop):
            try:
                stop()
            except Exception:
                pass
        handle = svc.get("handle")
        closer = getattr(handle, "stop", None) or getattr(handle, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:
                pass
    _COPILOT_EXT_STATE["services"] = kept
    _COPILOT_EXT_STATE["started"].difference_update(
        {k for k in _COPILOT_EXT_STATE["started"]
         if k.startswith(f"{name}:")})
    # 服务已停、UI 已摘：插件合成包模块整体摘出 sys.modules，
    # 再启用时 import_copilot_module 会按磁盘最新代码重新执行。
    try:
        unload_extension_modules(name)
    except Exception:
        pass


def copilot_plugin_running(name: str) -> bool:
    """插件的后台服务是否已在本进程启动（UI 显示运行状态，便于排查「启用却没反应」）。"""
    return any(str(s.get("plugin") or "") == name
               for s in _COPILOT_EXT_STATE.get("services") or [])


def copilot_plugin_error(name: str) -> str:
    """该插件上次加载扩展时的失败原因（无失败返回空串）。"""
    return str((_COPILOT_EXT_STATE.get("errors") or {}).get(name) or "")


def restart_copilot_services(panel=None) -> None:
    """重载转译层后重建后台服务。

    服务句柄绑定的是**旧模块对象**：热加载替换模块后，旧句柄不会自动用上新代码，
    必须先停掉再按启用列表重新 start（幂等，未启用的插件不会启动）。
    """
    for svc in reversed(_COPILOT_EXT_STATE.get("services") or []):
        stop = getattr(svc.get("module"), svc.get("stop") or "", None)
        if callable(stop):
            try:
                stop()
            except Exception:
                pass
        handle = svc.get("handle")
        closer = getattr(handle, "stop", None) or getattr(handle, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:
                pass
    _COPILOT_EXT_STATE["services"] = []
    _COPILOT_EXT_STATE["started"].difference_update(
        {k for k in _COPILOT_EXT_STATE["started"] if ":services:" in k})
    load_copilot_extensions(panel)


def load_copilot_extensions(panel=None) -> dict:
    """加载启用中的 Copilot 插件扩展（页面 / 后台服务 / 托盘动作 / 快捷键）。

    panel 传 CopilotPanel 实例时会登记页面类扩展；传 None（如主界面 AgentPanel
    启动时）只加载应用级扩展（服务/托盘/快捷键）——这就是「启用 Copilot 插件后
    立即生效、不依赖浮层是否打开」的关键。

    单插件失败只跳过不阻塞；服务/托盘/快捷键进程内只启动一次。
    """
    state = _COPILOT_EXT_STATE
    try:
        from zhuzhu_Copilot.core import copilot_plugins
        plugins = [p for p in copilot_plugins.list_plugins()
                   if p.get("enabled", True)]
    except Exception as e:
        state["errors"] = {"": f"读取插件列表失败：{e}"}
        return state
    errors = {}
    # 页面宿主：显式传入优先，否则用启动时登记的 MainWindow——这样
    # restart_copilot_services() 这类"只重启服务"的调用也不会漏掉页面
    host = panel if panel is not None else _COPILOT_EXT_STATE.get("host")
    for p in plugins:
        name = str(p.get("name") or "")
        try:
            d = copilot_plugins.plugin_dir(name)
            man = copilot_plugins.load_copilot_manifest(d)
            for section, entry in copilot_plugins._iter_manifest_entries(man):
                register_copilot_entry(host, name, d, section, entry)
        except Exception as e:
            # 单插件容错：不阻塞其余插件；原因留给 UI 显示（旧实现静默吞掉，
            # 用户只能看到「启用了却没反应」）
            errors[name] = f"{type(e).__name__}: {e}"
            continue
    state["errors"] = errors
    return state


def shutdown_copilot_extensions() -> None:
    """退出清理：停后台服务（托盘/快捷键随进程退出自动失效）。"""
    for svc in reversed(_COPILOT_EXT_STATE.get("services") or []):
        stop = getattr(svc.get("module"), svc.get("stop") or "", None)
        if callable(stop):
            try:
                stop()
            except Exception:
                pass
        handle = svc.get("handle")
        closer = getattr(handle, "stop", None) or getattr(handle, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:
                pass
    _COPILOT_EXT_STATE["started"].clear()
    _COPILOT_EXT_STATE["services"] = []


# ---------------------------------------------------------------------------
# 热插拔 UI 事件桥：核心层（copilot_plugins）在启停/删除/回滚时触发事件，
# 本模块负责把页面/服务/托盘/快捷键即时挂摘。GUI 线程直接执行；AI 工具链路
# （工作线程）由 main.py 注册的 UI 派发器经信号槽 marshal 到 GUI 线程。
# 回调以稳定 key 注册：本模块自身被热重载后函数对象会重建，按 key 替换不重复挂。
# ---------------------------------------------------------------------------

def _hot_hook_before_disable(name: str) -> None:
    try:
        stop_copilot_plugin(name)
    except Exception:
        pass


def _hot_hook_after_enable(name: str) -> None:
    try:
        start_copilot_plugin(name)
    except Exception:
        pass
    # 源码变更可能改写了其他启用插件的服务代码：全量重建一次（幂等）
    restart_copilot_services()


def _hot_hook_after_change(name: str) -> None:
    # 停用/删除/回滚/强制回滚后：按启用列表重建服务与页面（被删插件不会再登记）
    restart_copilot_services()


def install_hotplug_hooks() -> bool:
    """把 UI 挂摘动作注册到热加载引擎（幂等；可在任意线程导入本模块时调用）。

    注意：只注册回调，不创建 QObject；跨线程 marshal 由 main.py 的 UI 桥负责。
    """
    try:
        from zhuzhu_Copilot.core import hot_reload
    except Exception:
        return False
    hot_reload.on_ui_event("before_disable", _hot_hook_before_disable,
                           key="main_window.before_disable")
    hot_reload.on_ui_event("after_enable", _hot_hook_after_enable,
                           key="main_window.after_enable")
    hot_reload.on_ui_event("after_change", _hot_hook_after_change,
                           key="main_window.after_change")
    return True


# 模块导入即注册（预热线程导入也安全：这里不碰任何 QWidget）。
install_hotplug_hooks()
