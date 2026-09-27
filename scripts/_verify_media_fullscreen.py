"""验证：视频全屏融入预览面板（非独立窗口）+ 底部控制条鼠标唤出/自动隐藏。"""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")
sys.path.insert(0, os.path.abspath("src"))
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QEvent, QPointF
from PyQt6.QtGui import QMouseEvent
app = QApplication([])
from winapp_migrator.ui import agent_panel as ap

pre = ap.CodePreviewWindow(None)
pre.resize(441, 500)
assert pre._ensure_media_page(), "媒体页构建失败"
bar = pre.media_ctrl_bar
assert bar is not None

# 0. 音频输出修复：显式 QAudioOutput 已创建并持有、音量联动生效
ao = getattr(pre, "media_audio", None)
assert ao is not None, "QAudioOutput 未显式创建（视频可能无声）"
assert abs(ao.volume() - 0.8) < 0.01, f"音量未联动, got {ao.volume()}"
print("0. 显式 QAudioOutput + 音量联动: OK, volume=", ao.volume())

# 1. 进入全屏：预览面板自身全屏（不再 video_view.setFullScreen 独立窗口）
pre._media_stage.setCurrentIndex(0)   # 视频画面
pre._enter_media_fullscreen()
assert pre._media_full is True, "未进入全屏"
assert pre.title.isHidden(), "标题未隐藏"
assert pre.mode_row.isHidden(), "模式行未隐藏"
assert pre.maximumWidth() == 16777215, f"固定宽度未放开, got max={pre.maximumWidth()}"
print("1. 进入全屏（预览面板全屏，装饰隐藏）: OK, isFullScreen=", pre.isFullScreen())

# 2. 控制条显示 + 自动隐藏定时器已启动
assert not bar.isHidden(), "进入全屏应显示控制条"
assert pre._media_ctrl_timer is not None and pre._media_ctrl_timer.isActive(), "隐藏定时器未启动"
print("2. 控制条初始显示 + 隐藏定时器启动: OK")

# 3. 模拟移动鼠标 → 唤出控制条（QApplication 级过滤：任意控件 MouseMove 均可）
bar.hide()   # 先隐藏
ev = QMouseEvent(QEvent.Type.MouseMove, QPointF(100, 100), QPointF(100, 100),
                 QPointF(100, 100), __import__("PyQt6.QtCore").QtCore.Qt.MouseButton.NoButton,
                 __import__("PyQt6.QtCore").QtCore.Qt.MouseButton.NoButton,
                 __import__("PyQt6.QtCore").QtCore.Qt.KeyboardModifier.NoModifier)
# 任意控件（非预览面板）作为 obj：模拟 app 级过滤捕获深层子控件/渲染层事件
pre.eventFilter(app, ev)
assert not bar.isHidden(), "app 级 MouseMove 未唤出控制条"
bar.hide()
pre.eventFilter(pre.video_view, ev)
assert not bar.isHidden(), "video_view MouseMove 未唤出控制条"
assert pre._media_ctrl_timer.isActive(), "移动鼠标未重启隐藏计时"
print("3. 移动鼠标唤出控制条（app 级 + video_view）: OK")

# 4. 退出全屏：装饰/尺寸/控制条恢复
pre._show_media_ctrl(False)   # 模拟 3 秒沉浸隐藏控制条
assert bar.isHidden(), "沉浸应隐藏控制条"
pre._media_exit_fullscreen()
assert pre._media_full is False, "未退出全屏"
assert not bar.isHidden(), "退出全屏后底部控制条未恢复显示"
assert not pre.title.isHidden(), "标题未恢复"
assert not pre.mode_row.isHidden(), "模式行未恢复"
assert pre.minimumWidth() == 441, f"固定宽度未恢复, got {pre.minimumWidth()}"
assert not pre.isFullScreen(), "窗口仍全屏"
print("4. 退出全屏恢复装饰/尺寸/控制条: OK")

# 5. _media_fullscreen 开关切换
pre._media_fullscreen()
assert pre._media_full is True, "全屏按钮未进入全屏"
pre._media_fullscreen()
assert pre._media_full is False, "全屏按钮未退出全屏"
print("5. 全屏按钮开关切换: OK")

# 6. shutdown_media 在全屏中可安全退出
pre._media_fullscreen()
pre.shutdown_media()
assert pre._media_full is False, "shutdown_media 未退出全屏"
print("6. shutdown_media 全屏安全退出: OK")

print("SMOKE OK")
