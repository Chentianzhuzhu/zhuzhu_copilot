"""验证：输入行圆形按钮（上传/优化/发送）在窗口缩放/最大化时禁止放大、保持圆形。"""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath("src"))
from PyQt6.QtWidgets import QApplication, QPushButton, QPlainTextEdit
app = QApplication([])
import winapp_migrator.ui.agent_panel as ap

# 1. 样式常量：发送按钮 radius=17（34/2 正圆）、DIM 同
assert "border-radius: 17px" in ap._BTN_PRIMARY, "发送按钮应为正圆"
assert "border-radius: 17px" in ap._BTN_DIM, "DIM 按钮应为正圆"
print("1. 发送按钮样式 radius 17px（正圆）: OK")

# 2. 最小实例验证 _apply_scale 不再放大按钮
class _Stub:
    pass
inst = _Stub()
inst._last_scale = 1.0
inst.input = QPlainTextEdit()
inst.attach_btn = QPushButton()
inst.attach_btn.setFixedSize(42, 42)
inst.attach_btn.setIconSize(__import__("PyQt6.QtCore", fromlist=["QSize"]).QSize(20, 20))
inst.action_btn = QPushButton()
inst.action_btn.setFixedSize(34, 34)
inst.action_btn.setIconSize(__import__("PyQt6.QtCore", fromlist=["QSize"]).QSize(16, 16))
inst.optimize_btn = QPushButton()
inst.optimize_btn.setFixedSize(42, 42)
inst._apply_scale = ap.AgentPanel._apply_bottom_scale.__get__(inst, ap.AgentPanel)
inst.height = lambda: int(660 * 1.8)   # 模拟最大化高度

# 最大化（窗口高度约为 1.8x 基准 → k 明显大于 1）
inst._apply_scale()
assert inst.attach_btn.width() == 42 and inst.attach_btn.height() == 42, \
    f"上传按钮被放大: {inst.attach_btn.width()}x{inst.attach_btn.height()}"
assert inst.action_btn.width() == 34 and inst.action_btn.height() == 34, \
    f"发送按钮被放大: {inst.action_btn.width()}x{inst.action_btn.height()}"
assert inst.action_btn.iconSize().width() == 16, "发送按钮图标被放大"
print("2. 窗口缩放后按钮保持初始尺寸（42/42, 34/34）: OK")

# 3. 缩放系数继续增大也不放大
inst.height = lambda: int(660 * 2.5)
inst._apply_scale()
assert inst.attach_btn.width() == 42 and inst.action_btn.width() == 34
print("3. 更大缩放（2.5）仍不放大: OK")

# 4. 输入框高度随缩放（原有行为保留）
inst.height = lambda: int(660 * 1.8)
inst._apply_scale()
assert inst.input.minimumHeight() == int(32 * 1.8), "输入框缩放行为被破坏"
print("4. 输入框高度随窗口缩放保留: OK")

print("SMOKE OK")
