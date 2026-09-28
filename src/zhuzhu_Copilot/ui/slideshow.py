# -*- coding: utf-8 -*-
"""全屏放映/查看窗口（Office 预览的放大呈现）。

职责：把 office.preview 产出的自包含 HTML 用一个铺满屏幕（或指定屏幕）的
QWebEngineView 呈现，并提供演示级控制：
- 键盘：→ / ↓ / Space / PageDown / Enter = 下一步；← / ↑ / PageUp / Backspace = 上一步；
        Home/End = 首页/末页；Esc = 退出；F11 = 切换全屏
- 鼠标：左键点击 = 下一步（进入放映态后自动推进动画）；右键 = 退出
- 滚轮：下滚 = 下一步，上滚 = 上一步

两种模式：
- ``play``（PPT）：进入 screen 放映态，页面深色底、按视口 contain 适配、逐条播放动画
- ``view``（Word/Excel/PDF）：仅放大呈现并适宽，保留滚动，不做动画放映

设计：不依赖 AgentPanel 内部状态，只接收 HTML 文本与标题；控制全部经
runJavaScript 调用 preview 注入的 __deck* / __fit* 接口，保持单一实现来源。
"""

import os

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QColor, QKeySequence, QPainter
from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from zhuzhu_Copilot.ui.styles import PALETTE


class SlideShowWindow(QWidget):
    """全屏放映窗口：无边框铺满屏幕，Esc/右键退出。"""

    def __init__(self, parent=None, mode: str = "play", title: str = ""):
        super().__init__(parent)
        self._mode = mode if mode in ("play", "view") else "play"
        self._closed = False
        self.setWindowTitle(title or "放映")
        # 独立顶层窗口：无边框 + 置顶，铺满所在屏幕
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)   # 接收键盘
        self.setStyleSheet("background:#000;")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.view = None
        self.hint = QLabel("")
        lay.addWidget(self.hint, 1)
        self._build_view(lay)
        self._hint_timer = QTimer(self)
        self._hint_timer.setSingleShot(True)
        self._hint_timer.timeout.connect(lambda: self.hint.setVisible(False))

    # ---------------- 构建 ----------------
    def _build_view(self, lay):
        try:
            from PyQt6.QtWebEngineWidgets import QWebEngineView
        except Exception as e:
            self._show_hint("Web 引擎不可用，无法全屏呈现：%s" % e, 0)
            return
        try:
            view = QWebEngineView(self)
            view.setStyleSheet("QWebEngineView { background:#000; border:none; }")
            lay.insertWidget(0, view, 1)
            view.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            self.view = view
        except Exception as e:
            self._show_hint("创建渲染视图失败：%s" % e, 0)

    def _show_hint(self, text: str, ms: int = 2600):
        self.hint.setText(text)
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint.setStyleSheet(
            f"color:{PALETTE.get('text', '#F5F5F5')};font-size:14px;"
            f"font-family:'Microsoft YaHei';")
        self.hint.setVisible(True)
        if ms:
            self._hint_timer.start(ms)

    # ---------------- 对外 API ----------------
    def show_html(self, html: str, screen: bool = True):
        """载入 HTML 并（可选）进入放映态；随后铺满屏幕。"""
        if self.view is None:
            return
        # 与 Office 预览同一机制：写临时文件 + setUrl，避免 setHtml 的内容上限
        url = self._write_temp(html)
        if url:
            self.view.setUrl(QUrl.fromLocalFile(url))
        else:
            self.view.setHtml(html)
        self.show()
        self._enter_screen(screen)

    def _enter_screen(self, screen: bool):
        """页面载入后进入呈现模式（含重试：WebEngine 载入是异步的）"""
        if self.view is None:
            return
        if self._mode == "play":
            js = "__fitMode('screen'); __deckPlay(1); __fitSlots();"
        else:
            js = "__fitMode('fullview'); __fitSlots();"
        for delay in (260, 700, 1400):
            QTimer.singleShot(delay, lambda code=js: self._run(code))
        self._show_hint("← / → 翻页　空格 下一步　Esc 退出　F11 全屏", 3200)

    def _write_temp(self, html: str):
        try:
            import tempfile
            d = os.path.join(tempfile.gettempdir(), "zhuzhu_copilot_preview")
            os.makedirs(d, exist_ok=True)
            p = os.path.join(d, "slideshow.html")
            with open(p, "w", encoding="utf-8") as f:
                f.write(html)
            return p
        except Exception:
            return None

    def _run(self, js: str):
        try:
            if self.view is not None:
                self.view.page().runJavaScript(js)
        except Exception:
            pass

    def next_step(self):
        self._run("__deckAdvance();")

    def prev_step(self):
        self._run("__deckPrev();")

    def first_page(self):
        self._run("__deckShowPage(1);")

    def last_page(self):
        self._run("__deckShowPage(__deckCount());")

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    # ---------------- 屏幕铺满 ----------------
    def showEvent(self, ev):
        super().showEvent(ev)
        self._fit_to_screen()
        try:
            self.activateWindow()
            self.raise_()
            if self.view is not None:
                self.view.setFocus()
            self.setFocus()
        except Exception:
            pass

    def _fit_to_screen(self):
        try:
            scr = self.screen() or QApplication.primaryScreen()
            if scr is not None:
                self.setGeometry(scr.geometry())
        except Exception:
            pass
        self.showFullScreen()

    # ---------------- 交互 ----------------
    def keyPressEvent(self, ev):
        k = ev.key()
        if k == Qt.Key.Key_Escape:
            self.close()
            return
        if k == Qt.Key.Key_F11:
            self.toggle_fullscreen()
            return
        if k in (Qt.Key.Key_Right, Qt.Key.Key_Down, Qt.Key.Key_Space,
                 Qt.Key.Key_PageDown, Qt.Key.Key_Return, Qt.Key.Key_Enter,
                 Qt.Key.Key_N):
            self.next_step()
            return
        if k in (Qt.Key.Key_Left, Qt.Key.Key_Up, Qt.Key.Key_PageUp,
                 Qt.Key.Key_Backspace, Qt.Key.Key_P):
            self.prev_step()
            return
        if k == Qt.Key.Key_Home:
            self.first_page()
            return
        if k == Qt.Key.Key_End:
            self.last_page()
            return
        super().keyPressEvent(ev)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.RightButton:
            self.close()
        elif ev.button() == Qt.MouseButton.LeftButton:
            self.next_step()
        else:
            super().mousePressEvent(ev)

    def wheelEvent(self, ev):
        if ev.angleDelta().y() < 0:
            self.next_step()
        else:
            self.prev_step()

    def closeEvent(self, ev):
        """退出时通知页面清掉 screen 态（避免残留状态影响下次预览）"""
        self._closed = True
        self._run("__deckScreen(false);")
        super().closeEvent(ev)


def make_slideshow(parent, html: str, ext: str, title: str = ""):
    """便捷入口：按扩展名选择放映/查看模式并打开全屏窗口。

    PPT → play（放映态，逐条动画）；Word/Excel/PDF → view（放大适宽查看）。
    """
    mode = "play" if ext in ("pptx", "pptm") else "view"
    win = SlideShowWindow(parent, mode=mode, title=title)
    win.show_html(html, screen=(mode == "play"))
    return win
