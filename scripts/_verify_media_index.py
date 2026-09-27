"""验证：媒体页堆叠索引正确，双击视频路由到媒体页而非空白页。"""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")
sys.path.insert(0, os.path.abspath("src"))
from PyQt6.QtWidgets import QApplication
app = QApplication([])
from winapp_migrator.ui import agent_panel as ap

pre = ap.CodePreviewWindow(None)
pre.resize(441, 500)

# 构建前 stack：web/html/text/img/empty = 5 页
print("initial stack count:", pre.stack.count())
assert pre.stack.count() == 5

# 首次打开媒体 → 惰性构建，索引应为 5（empty 之后）
assert pre._ensure_media_page() is True
print("media stack index:", pre._media_stack_index, "stack count:", pre.stack.count())
assert pre._media_stack_index == 5

# 模拟 show_file 双击视频：应切到媒体页而非空白页
pre._manual_mode = False
pre.show_media("/x/clip.mp4", "mp4")
print("current index:", pre.stack.currentIndex(), "media index:", pre._media_stack_index)
assert pre.stack.currentIndex() == pre._media_stack_index, "必须切到媒体页"
# 视频画面在 stage index 0
assert pre._media_stage.currentIndex() == 0
print("SMOKE OK")