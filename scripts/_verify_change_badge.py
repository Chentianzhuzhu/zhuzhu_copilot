"""气泡「-N +M」文件变更徽章 UI 验证（Qt offscreen，轻量桩对象驱动 _add_badge/_add_change_badge）。

验证点：
1. 有变更时生成徽章文本：「-10」红色、「+2」绿色（span 颜色值取自 ERR/OK 调色板）
2. 附着顺序：状态徽章在行首（index 0），变更徽章紧随其后（index 1），重试按钮最右
3. 无变更（added/removed=0）时不生成徽章
"""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PyQt6.QtCore import Qt as _Qt
from PyQt6.QtWidgets import QApplication, QLabel, QHBoxLayout, QSizePolicy, QPushButton
app = QApplication([])

import winapp_migrator.ui.agent_panel as ap


def make_fake():
    """构造仅含 _attach_capsule/_add_badge/_add_change_badge 所需属性的桩对象"""
    bubble = QLabel("AI 内容")
    wrap_lay = __import__("PyQt6.QtWidgets", fromlist=["QVBoxLayout"]).QVBoxLayout()
    retry_row = QHBoxLayout()
    btn = QPushButton("retry")
    retry_row.addStretch(1)
    retry_row.addWidget(btn)
    bubble._wrap_lay = wrap_lay
    bubble._retry_row = retry_row
    fake = SimpleNamespace(
        _ai_bubble=bubble,
        msg_lay=__import__("PyQt6.QtWidgets", fromlist=["QVBoxLayout"]).QVBoxLayout(),
        _end_badge_shown=True,
    )
    fake._bubble_alive = lambda b: True
    fake._place_spinner_bottom = lambda: None
    fake._scroll_bottom = lambda: None
    # 把用到的实例方法绑定到桩对象，使 _add_badge/_add_change_badge 可被无绑调用
    from types import MethodType
    fake._attach_capsule = MethodType(ap.AgentPanel._attach_capsule, fake)
    return fake


def _capsule_text(lbl):
    return lbl.text()


fake = make_fake()
retry_row = fake._ai_bubble._retry_row
# 1) 无变更 → 不生成徽章
t0 = retry_row.count()
ap.AgentPanel._add_change_badge(fake, {"added": 0, "removed": 0, "files": {}})
assert retry_row.count() == t0, "无变更不应生成徽章"
# 2) 状态徽章 + 变更徽章顺序
ap.AgentPanel._add_badge(fake, "Successfully", ap.OK)
ap.AgentPanel._add_change_badge(fake, {"added": 2, "removed": 10,
                                       "files": {"C:/a.py": [2, 4], "C:/b.py": [0, 6]}})
state = retry_row.itemAt(0).widget()
chg = retry_row.itemAt(1).widget()
assert "Successfully" in state.text()
assert isinstance(chg, QLabel)
# 3) 变更徽章内容：-10 红 / +2 绿
txt = chg.text()
assert f'-<span style="color:{ap.ERR};font-weight:700;">10</span>' in txt or \
       f'<span style="color:{ap.ERR};font-weight:700;">-10</span>' in txt, txt
assert f'<span style="color:{ap.OK};font-weight:700;">+2</span>' in txt, txt
# 4) tooltip 提供逐文件明细
tip = chg.toolTip()
assert "C:/a.py" in tip and "C:/b.py" in tip
# 5) 重试按钮仍在行尾（徽章行内的最右侧控件）
last = retry_row.itemAt(retry_row.count() - 1).widget()
assert isinstance(last, QPushButton)
print("[OK] 徽章顺序正确:", [retry_row.itemAt(i).widget().text() if retry_row.itemAt(i).widget() else None
                              for i in range(retry_row.count())])
print("[OK] 变更徽章:", txt.replace("</span>", "|").replace("<span", "["))
print("UI 验证通过")