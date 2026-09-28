"""验证：媒体播放时停用 AI 文件跳转预览 + 全屏清蒙版/关 DWM 圆角路径存在。"""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")
sys.path.insert(0, os.path.abspath("src"))
from PyQt6.QtWidgets import QApplication
app = QApplication([])
from zhuzhu_Copilot.ui import agent_panel as ap
from PyQt6.QtMultimedia import QMediaPlayer

# 1. is_media_active：未播放时 False，播放/暂停时 True
pre = ap.CodePreviewWindow(None)
pre.resize(441, 500)
assert pre._ensure_media_page(), "媒体页构建失败"
assert pre.is_media_active() is False, "未播放媒体应为 False"
# 模拟播放中（monkeypatch playbackState）
pre.media_player.playbackState = lambda: QMediaPlayer.PlaybackState.PlayingState
assert pre.is_media_active() is True, "播放中应为 True"
pre.media_player.playbackState = lambda: QMediaPlayer.PlaybackState.PausedState
assert pre.is_media_active() is True, "暂停中应为 True（不打断）"
pre.media_player.playbackState = lambda: QMediaPlayer.PlaybackState.StoppedState
assert pre.is_media_active() is False, "停止应为 False"
print("1. is_media_active 状态判定: OK")

# 2. _on_file_diff：媒体播放中跳过差异跳转
calls = []
class FakeCW:
    def show_diff(self, *a):
        calls.append(1)
    def is_media_active(self):
        return True
    def show(self):
        pass
    def raise_(self):
        pass
    def isVisible(self):
        return True
    def isHidden(self):
        return False
p = ap.AgentPanel.__new__(ap.AgentPanel)
p.code_win = FakeCW()
p._on_file_diff("/nonexist/a.py", "", "")
assert not calls, "媒体播放中不应跳转预览"
print("2. 媒体播放中跳过文件跳转预览: OK")

# 3. 非媒体播放时正常跳转
calls2 = []
class FakeCW2:
    def show_diff(self, *a):
        calls2.append(1)
    def is_media_active(self):
        return False
    def show(self):
        pass
    def raise_(self):
        pass
    def isVisible(self):
        return True
p.code_win = FakeCW2()
p._on_file_diff("/nonexist/b.py", "", "")
assert calls2, "非媒体播放应正常跳转"
print("3. 非媒体播放正常跳转: OK")

# 4. 源码检查：全屏清蒙版 + 关闭 DWM 圆角路径存在
src = open(os.path.join(os.path.dirname(ap.__file__), "agent_panel.py"), encoding="utf-8").read()
assert "SetWindowRgn(hwnd, 0, True)" in src, "全屏清蒙版缺失"
assert "WCP_DONOTROUND" in src or "corner = ctypes.c_int(1)" in src, "全屏关 DWM 圆角缺失"
import zhuzhu_Copilot.core.agent_ui_ux as aux
usrc = open(os.path.dirname(aux.__file__) + "/agent_ui_ux.py", encoding="utf-8").read()
assert "WCP_DONOTROUND" in usrc, "apply_rounded_window 全屏关 DWM 圆角缺失"
print("4. 全屏清蒙版 + 关 DWM 圆角路径存在: OK")

print("SMOKE OK")
