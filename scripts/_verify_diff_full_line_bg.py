"""回归：diff 红绿标识用块级背景铺满整行（而非只覆盖每行代码长度）。
修复前：ExtraSelection + LineUnderCursor 只画选中文本实际宽度（代码长度）。
修复后：QTextBlockFormat 块级背景铺满整行可视宽度；恢复视图时清除。
"""
import os, sys, tempfile, pathlib
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath("src"))
from PyQt6.QtGui import QTextBlockFormat, QColor
from PyQt6.QtWidgets import QApplication
app = QApplication([])
from zhuzhu_Copilot.ui import agent_panel as ap

tmpf = pathlib.Path(tempfile.mkdtemp()) / "demo.py"
new_content = "def add(a, b):\n    return a + b\n"
old_content = "def add(a, b):\n    # 旧实现\n    return a - b\n"
tmpf.write_text(new_content, encoding="utf-8")

win = ap.CodePreviewWindow()
win.setFixedWidth(ap.CodePreviewWindow.WIDTH)
win.show_file(str(tmpf))
win.show_diff(str(tmpf), old_content, new_content)
doc = win.text.document()

def blocks_bg():
    out = {}
    for i in range(doc.blockCount()):
        b = doc.findBlockByNumber(i)
        if not b.isValid():
            continue
        out[i] = b.blockFormat().background()
    return out

bg = blocks_bg()
changed = [i for i, t in enumerate(win.text.toPlainText().splitlines())
           if t.startswith("+ ") or t.startswith("- ")]
print("变更行号:", changed, " 块背景:", {k: (v.color().name() if v.style() else None) for k, v in bg.items()})

ADD_BG, DEL_BG = QColor(34, 120, 70, 110).name(), QColor(170, 45, 45, 120).name()
# 1) 变更行都有红/绿块级背景（铺满整行），+/- 行颜色正确
assert changed, "无变更行"
for i in changed:
    color = bg[i].color().name()
    assert color in (ADD_BG, DEL_BG), f"变更行 {i} 背景色异常: {color}"
print("1) 变更行红/绿块级背景铺满整行 PASS")

# 2) 未变更行不携带红/绿背景
for i in range(doc.blockCount()):
    if i in changed:
        continue
    color = bg[i].color().name() if bg[i].style() else None
    assert color not in (ADD_BG, DEL_BG), f"未变更行 {i} 被误加红/绿背景: {color}"
print("2) 未变更行无红/绿背景 PASS")

# 3) 恢复视图后红/绿背景清除（回到磁盘内容，不残留 diff 高亮）
win._restore_text_view(str(tmpf), win._diff_seq)
bg2 = blocks_bg()
leftover = [i for i, v in bg2.items()
            if v.style() and v.color().name() in (ADD_BG, DEL_BG)]
assert not leftover, f"恢复后残留红/绿背景: {leftover}"
assert "return a + b" in win.text.toPlainText() and "旧实现" not in win.text.toPlainText(), \
    "恢复未回到磁盘内容"
print("3) 恢复后红/绿背景清除、内容恢复 PASS")

# 4) show_diff 不再使用 ExtraSelection 覆盖（源码断言）
src = open(os.path.join(os.path.dirname(ap.__file__), "agent_panel.py"),
           encoding="utf-8").read()
seg = src.split("def show_diff(")[1].split("def _diff_protected")[0]
assert "_QTextEdit.ExtraSelection(" not in seg, "show_diff 仍用 ExtraSelection（只覆盖代码长度）"
assert "QTextBlockFormat" in seg, "show_diff 未使用块级背景"
print("4) 实现改用块级背景 PASS")
print("ALL RESULT: PASS")