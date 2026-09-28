"""验证：视频全屏隐藏宿主/子面板（真正全屏），退出全屏恢复。"""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")
sys.path.insert(0, os.path.abspath("src"))
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
app = QApplication([])
from zhuzhu_Copilot.ui import agent_panel as ap

pre = ap.CodePreviewWindow(None)
pre.resize(441, 500)
assert pre._ensure_media_page(), "媒体页构建失败"
pre._media_stage.setCurrentIndex(0)

# fake 宿主（AgentPanel），含三个子面板
class _Win:
    def __init__(self):
        self._hidden = False
    def isHidden(self):
        return self._hidden
    def hide(self):
        self._hidden = True
    def show(self):
        self._hidden = False


class _Host(_Win):
    def __init__(self, code):
        super().__init__()
        self.code_win = code
        self.todos_win = _Win()
        self.git_win = _Win()
        self.wt_win = _Win()


host = _Host(pre)
pre.parentWidget = lambda: host

# 1. _media_host 能定位宿主
assert pre._media_host() is host, "_media_host 未定位宿主"
print("1. _media_host 定位宿主: OK")

# 2. 进入全屏：宿主与子面板隐藏 + 真正全屏状态
pre._enter_media_fullscreen()
assert pre._media_full, "未进入全屏"
assert host._hidden, "宿主未隐藏"
assert host.todos_win._hidden and host.git_win._hidden and host.wt_win._hidden, "子面板未隐藏"
print("2a. 宿主/子面板隐藏: OK, windowState=", pre.windowState(), "visible=", pre.isVisible())
assert pre.windowState() & Qt.WindowState.WindowFullScreen, "未设置真正全屏状态"
print("2. 全屏隐藏宿主/子面板 + 真正全屏状态: OK, isFullScreen=", pre.isFullScreen())

# 3. 退出全屏：恢复宿主与子面板 + 清除全屏状态
pre._media_exit_fullscreen()
assert not pre._media_full, "未退出全屏"
assert not host._hidden, "宿主未恢复"
assert not host.todos_win._hidden and not host.git_win._hidden and not host.wt_win._hidden, "子面板未恢复"
assert not (pre.windowState() & Qt.WindowState.WindowFullScreen), "全屏状态未清除"
print("3. 退出全屏恢复宿主/子面板 + 清全屏状态: OK")

print("SMOKE OK")
