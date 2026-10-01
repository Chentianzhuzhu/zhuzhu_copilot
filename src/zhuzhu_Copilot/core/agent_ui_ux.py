# -*- coding: utf-8 -*-
"""渲染与窗口基础工具。

历史说明：本模块原名 `agent_ui_ux`，曾承担「UI/UX 包管理 + 液态玻璃引擎」两大职责
（热插拔自定义面板、Acrylic 毛玻璃、波纹、包导入导出等）。那套机制建立在
「让模型生成 build_ui.py 代码并沙箱执行来重构界面」之上，脆弱且与「全局统一玻璃材质」
的设计目标冲突，已于 2026-10-01 整体移除。

保留的只有与「包」无关、被全应用依赖的四项基础设施（模块名沿用旧名以避免
无谓的全仓重命名风险，职责以本文件为准）：
  1. Markdown / HTML / 文本 / Web 预览   —— ``render_markdown_html`` 等
  2. QtWebEngine 可用性探测             —— ``web_engine_available`` / ``new_web_view``
  3. 窗口圆角蒙版                        —— ``apply_rounded_window``
  4. 主题色读取                          —— ``_panel_theme_col``

新的全局磨砂玻璃外观内核在 ``core/app_glass.py``（参数化 + 面板级缓存），
与包机制无关，任何窗口都可以直接装配。
"""

import os
from zhuzhu_Copilot import app_identity  # noqa: F401  （保留导出，供外部既有引用）

# QtWebEngine 渲染兜底：与 src/main.py 相同的 Chromium 配置，
# 防止不经 main.py 的入口（如测试脚本）直接创建 QWebEngineView 时在无 GPU
# 环境下白屏/加载失败或 QtWebEngineProcess 崩溃。
# 必须在 QtWebEngine 内核初始化前设置，setdefault 不覆盖用户配置。
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--disable-gpu --no-sandbox --disable-gpu-compositing --disable-software-rasterizer",
)
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

# 【关键】QtWebEngine 必须在 QCoreApplication 创建【之前】导入，否则运行时才
# try-import 会抛 ImportError（"QtWebEngineWidgets must be imported ... before a
# QCoreApplication instance is created"），导致 web_engine_available() 误判 False、
# new_web_view() 返回 None，Web 预览降级为静态提示/外部浏览器。
# 本模块在 main.py 中于 QApplication 创建前被链式导入（main.py 顶部 import
# MainWindow → agent_panel → agent_ui_ux），此处模块级提前加载即可让后续
# web_engine_available()/new_web_view() 直接命中 sys.modules。
try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
except Exception:
    pass

try:
    from PyQt6.QtWidgets import QWidget as _QWidget
except Exception:
    _QWidget = object   # 无 PyQt 环境（如纯测试）时退化为普通基类


# ══════════════════════════ 预览渲染 ══════════════════════════

def _escape_html(s) -> str:
    """HTML 转义（备用路径，预览渲染失败时保护原始文本）"""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _panel_theme_col(name, fallback):
    """读取当前主题色（agent_panel 模块运行时全局，随主题切换实时变化）"""
    try:
        from zhuzhu_Copilot.ui import agent_panel as _ap
        v = getattr(_ap, name, None)
        return v if v else fallback
    except Exception:
        return fallback


def render_markdown_html(md_text, fragment=False, css_extra=""):
    """把 Markdown 渲染成 HTML：复用聊天渲染器 `_md_to_html`，自动继承当前主题配色。
    - fragment=True：返回纯 body 片段（嵌入他人文档时用）；
    - css_extra：追加自定义 CSS（body/table/代码块等），供用户定制预览外观；
    - 渲染失败回退为 HTML 转义的纯文本，绝不抛错。"""
    frag = ""
    try:
        if md_text:
            from zhuzhu_Copilot.ui import agent_panel as _ap
            frag = _ap._md_to_html(str(md_text))
    except Exception:
        frag = _escape_html(md_text or "")
    if fragment:
        return frag
    text = _panel_theme_col("TEXT", "#e6e8ec")
    dim = _panel_theme_col("TEXT_DIM", "#9aa1aa")
    accent = _panel_theme_col("ACCENT", "#4c9dff")
    card = _panel_theme_col("CARD", "#23282e")
    code_bg = _panel_theme_col("CODE_BG", "#181b20")
    css = (
        "body{margin:12px;font-size:14px;line-height:1.65;"
        f"color:{text};background:{card};}}"
        f"h1,h2,h3,h4,h5,h6{{color:{text};}} p{{margin:5px 0;}}"
        f"pre{{background:{code_bg};border-radius:6px;padding:8px;}}"
        "code{font-family:Consolas,monospace;font-size:12px;}"
        f"a{{color:{accent};}} hr{{border:none;border-top:1px solid {dim};}}"
        f"blockquote{{border-left:3px solid {accent};margin:6px 0;padding:2px 12px;}}"
        "table{border-collapse:collapse;} th,td{border:1px solid "
        f"{dim};padding:4px 8px;}} ul{{padding-left:18px;}}"
        + (css_extra or "")
    )
    return ("<!DOCTYPE html><html><head><meta charset='utf-8'/><style>"
            + css + "</style></head><body>" + frag + "</body></html>")


def web_engine_available() -> bool:
    """QtWebEngine 是否可用（未安装 PyQt6-WebEngine 时返回 False，代码可据此降级）。

    注意：不能使用 importlib.util.find_spec 探测——PyInstaller 打包后（frozen 环境）
    模块在 PYZ 归档内，find_spec 会错误返回 None，导致已打包的 QtWebEngine 被误判为
    "未安装" 而降级。必须用 try-import 才能真正确认可导入。
    """
    try:
        from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
        return True
    except Exception:
        return False


def new_web_view(parent=None):
    """创建一个 QWebEngineView；未安装 QtWebEngine 时返回 None（不抛错）。"""
    try:
        from PyQt6.QtWebEngineWidgets import QWebEngineView
        return QWebEngineView(parent)
    except Exception:
        return None


class _PreviewPanel(_QWidget):
    """通用预览面板：Markdown / HTML / 文本 / Web 统一预览。

    后端策略：
      - kind='web'     优先 QWebEngineView（真实浏览器，可 set_url 访问网页/跑 JS），
                       未安装 QtWebEngine 时自动回退 QTextBrowser（仅静态内容）。
      - 其他(markdown/html/text)  用 QTextBrowser（零额外依赖、随主题配色）。

    所有方法尽力而为、失败静默，绝不抛错影响宿主 UI。
    """

    def __init__(self, kind="markdown", parent=None):
        super().__init__(parent)
        from PyQt6.QtWidgets import QVBoxLayout
        self._kind = (kind or "markdown").lower()
        self._web = None      # QWebEngineView（web 且已装依赖时）
        self._tb = None       # QTextBrowser（轻量/回退后端）
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._ensure_backend()
        w = self._web if self._web is not None else self._tb
        if w is not None:
            self._lay.addWidget(w)

    def _ensure_backend(self):
        from PyQt6.QtWidgets import QTextBrowser
        if self._tb is not None or self._web is not None:
            return
        if self._kind == "web":
            try:
                from PyQt6.QtWebEngineWidgets import QWebEngineView
                self._web = QWebEngineView(self)
            except Exception:
                self._web = None
        if self._web is None:
            self._tb = QTextBrowser(self)
            self._tb.setOpenExternalLinks(True)

    def render_markdown(self, text):
        """Markdown → 当前主题配色的 HTML 预览（返回是否成功）"""
        try:
            return self.set_html(render_markdown_html(text or ""))
        except Exception:
            return False

    def set_html(self, html):
        """展示原始 HTML（返回是否成功）"""
        try:
            if self._web is not None:
                self._web.setHtml(html or "")
            else:
                self._tb.setHtml(html or "")
            return True
        except Exception:
            return False

    def set_text(self, text):
        """展示纯文本（返回是否成功；web 后端优先用 set_html/set_url）"""
        try:
            if self._tb is not None:
                self._tb.setPlainText(str(text or ""))
                return True
            return self.set_html(_escape_html(text or ""))
        except Exception:
            return False

    def set_url(self, url):
        """跳转真实网页（仅 web 后端 QWebEngineView 支持；未装依赖返回 False）"""
        try:
            if self._web is None or not url:
                return False
            from PyQt6.QtCore import QUrl
            self._web.setUrl(QUrl(str(url)))
            return True
        except Exception:
            return False


def create_preview_widget(kind="markdown", parent=None):
    """一键创建预览控件：`pv = create_preview_widget('markdown'); layout.addWidget(pv)`。
    kind ∈ {'markdown','html','text','web'}。失败返回 None（尽力而为）。"""
    try:
        return _PreviewPanel(kind=kind, parent=parent)
    except Exception:
        return None


# ══════════════════════════ 窗口圆角 ══════════════════════════

def apply_rounded_window(widget, radius=18):
    """给窗口套圆角蒙版：把矩形窗口四角剪成圆角。

    优先使用 Windows 11 DWMWA_WINDOW_CORNER_PREFERENCE（官方圆角 API），
    降级使用 SetWindowRgn + CreateRoundRectRgn。失败静默，绝不影响功能。
    """
    try:
        import ctypes
        maximized = bool(getattr(widget, "_glass_maximized", False)) \
            or widget.isMaximized() or widget.isFullScreen()
        if maximized:
            # 清除窗口区域设置，恢复矩形（最大化/全屏铺满屏幕无需圆角）
            user32 = ctypes.windll.user32
            hwnd = int(widget.winId())
            if hwnd != 0:
                user32.SetWindowRgn(hwnd, 0, True)
                # 同时关闭 DWM 圆角（Win11 DWMWA_WINDOW_CORNER_PREFERENCE）：
                # 仅清 SetWindowRgn 时 DWM 圆角仍会裁剪边缘，导致覆盖不全
                try:
                    dwmapi = ctypes.windll.dwmapi
                    corner = ctypes.c_int(1)   # WCP_DONOTROUND
                    dwmapi.DwmSetWindowAttribute(
                        hwnd, 33, ctypes.byref(corner), ctypes.sizeof(corner))
                except Exception:
                    pass
            return
        r = widget.rect()
        if r.width() <= 0 or r.height() <= 0:
            return
        # 确保窗口 HWND 已创建
        hwnd = int(widget.winId())
        if hwnd == 0:
            return
        # 方法 1：Windows 11 DWMWA_WINDOW_CORNER_PREFERENCE（官方圆角 API）
        dwmapi = ctypes.windll.dwmapi
        try:
            corner_pref = ctypes.c_int(2)  # WCP_ROUND
            result = dwmapi.DwmSetWindowAttribute(
                hwnd, 33, ctypes.byref(corner_pref), ctypes.sizeof(corner_pref))
            if result == 0:  # S_OK
                return
        except Exception:
            pass  # Windows 10 或更低版本不支持此 API
        # 方法 2：CreateRoundRectRgn + SetWindowRgn
        gdi32 = ctypes.windll.gdi32
        user32 = ctypes.windll.user32
        rgn = gdi32.CreateRoundRectRgn(r.left(), r.top(), r.right(), r.bottom(),
                                       radius * 2, radius * 2)
        if rgn:
            user32.SetWindowRgn(hwnd, rgn, True)
    except Exception:
        pass
