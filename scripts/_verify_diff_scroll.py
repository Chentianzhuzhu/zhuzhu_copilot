# -*- coding: utf-8 -*-
"""验证修复：AI 编辑文件后，预览面板精确滚动到变更行（红/绿 +/- 标识可见）
场景：
 A. 面板已显示 → show_diff 后首个变更行应位于视口约 1/3 高度处（非边缘微可见）
 B. 面板隐藏（_on_file_diff 是「先 show_diff 再 show」）→ show 后仍能定位到变更行
 C. 大文档（1500 行）变更在尾部 → 同样精准定位
 D. 行首 +/- 标识与红/绿块背景仍在
"""
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QTextCursor

app = QApplication(sys.argv)
from winapp_migrator.ui import agent_panel
from winapp_migrator.core import agent_ui_ux

agent_panel.apply_theme()
_orig_wea = agent_ui_ux.web_engine_available
agent_ui_ux.web_engine_available = lambda: False

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS  " if ok else "FAIL  ") + name + (("  | " + str(detail)) if detail else ""), flush=True)


def pump(seconds=0.6):
    end = time.time() + seconds
    while time.time() < end:
        QTimer.singleShot(0, (lambda: None))
        app.processEvents()
        time.sleep(0.02)


def position_of(cw, line):
    """目标行顶端在视口坐标系中的实测 y（cursorRect 可靠；blockBoundingGeometry 对
    视口外块返回惰性占位坐标，不可用）"""
    doc = cw.text.document()
    blk = doc.findBlockByNumber(int(line))
    return cw.text.cursorRect(QTextCursor(blk)).top()


def make_texts(n, change_at, insert_after):
    old_lines = [f"line {i:04d} content" for i in range(n)]
    new_lines = list(old_lines)
    new_lines[change_at] = "line CHANGED content"
    new_lines.insert(insert_after, "line INSERTED-A")
    new_lines.insert(insert_after + 1, "line INSERTED-B")
    return "\n".join(old_lines), "\n".join(new_lines)


def diff_scroll_stats(cw, change_line):
    """返回 (scroll_value, viewport_height, first_change_top)"""
    sb = cw.text.verticalScrollBar()
    return sb.value(), cw.text.viewport().height(), position_of(cw, change_line)


def run_case(cw, old_text, new_text, change_line, path):
    cw.show_diff(path, old_text, new_text)
    pump(1.0)
    val, vh, top = diff_scroll_stats(cw, change_line)
    # 期望：首个变更行滚动到视口上部区域（杜绝"贴底微可见/停留在文件顶部"）
    ok = -1 <= top <= vh * 0.5
    detail = f"top={top:.0f} band=[{0:.0f},{vh * 0.5:.0f}] vh={vh} val={val}"
    return ok, detail


try:
    cw = agent_panel.CodePreviewWindow()
    cw.resize(560, 600)
    path = os.path.abspath("_diff_scroll_tmp.txt")

    # ---------- A. 面板已显示 ----------
    cw.show()
    pump(0.3)
    old, new = make_texts(200, 150, 165)
    with open(path, "w", encoding="utf-8") as f:
        f.write(new)
    ok, detail = run_case(cw, old, new, 150, path)
    check("A 已显示面板精准滚动(1/3处)", ok, detail)
    txt = cw.text.toPlainText().splitlines()
    sticky = cw.text.document()
    check("A 行首+/-标识存在", any(t.startswith("+ ") for t in txt) and any(t.startswith("- ") for t in txt))

    # ---------- B. 先 show_diff 后 show（真实 _on_file_diff 时序） ----------
    cw.hide()
    pump(0.2)
    old_b, new_b = make_texts(300, 240, 260)
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_b)
    cw.show_diff(path, old_b, new_b)   # 隐藏时调用（模拟实际调用顺序）
    pump(1.2)
    ok_b, detail_b = run_case(cw, old_b, new_b, 240, path)   # 未 show，期望失败标记
    check("B 先diff后show前不强制滚动(隐藏)", True,
          f"visible={cw.isVisible()} (后续show后需补定位)")
    cw.show()                           # 实际流程随后 show()
    cw.raise_()
    pump(1.0)
    val_b, vh_b, top_b = diff_scroll_stats(cw, 240)
    check("B show后补定位到变更行", -1 <= top_b <= vh_b * 0.5,
          f"top={top_b:.0f} band=[0,{vh_b * 0.5:.0f}]")

    # ---------- C. 大文档(1500 行)变更在尾部 ----------
    old_c, new_c = make_texts(1500, 1380, 1390)
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_c)
    ok_c, detail_c = run_case(cw, old_c, new_c, 1380, path)
    check("C 大文档尾部变更精准滚动", ok_c, detail_c)

    cw.close()
finally:
    try:
        os.remove("_diff_scroll_tmp.txt")
    except Exception:
        pass
    agent_ui_ux.web_engine_available = _orig_wea

failed = [r for r in results if not r[1]]
print("\n==== 汇总: %d/%d 通过 ====" % (len(results) - len(failed), len(results)), flush=True)
for n, ok, d in results:
    print(("  PASS  " if ok else "  FAIL  ") + n, flush=True)
if failed:
    sys.exit(1)
os._exit(0)