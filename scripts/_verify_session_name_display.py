"""回归验证：会话名在下拉收起态不省略号截断（AI 起名 ≤12 字 / 本地兜底 ≤20 字）。

offscreen 平台真实面板 + 隔离会话目录：换成长会话名后检查 combo 宽度自适应，
文本区可用宽度 ≥ 全名渲染所需宽度（Qt 收起态超宽才会省略号）。
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PyQt6.QtWidgets import QApplication

_app = QApplication([])

from zhuzhu_Copilot.ui import agent_panel as ap

TMP = Path(tempfile.mkdtemp(prefix="sessname_disp_"))
p = ap.AgentPanel()
p._sessions_dir = lambda: TMP
for _ in range(8):
    _app.processEvents()

sid = p._create_session()["id"]
p._session_id, p._session_name = sid, "新对话"
p._bind_sess(sid)
p._refresh_session_combo()
p.resize(1280, 800)
for _ in range(4):
    _app.processEvents()

fail = 0


def whole_text_fits(label: str):
    """收敛态下：文本区宽度(去掉下拉箭头/内边距估算) ≥ 全名渲染宽度 → 不会省略号"""
    global fail
    name = p.session_combo.currentText()
    need = p.session_combo.fontMetrics().horizontalAdvance(name) + 14   # 左右内边距余量
    avail = p.session_combo.width()
    print(f"{label}: name={name!r} need={need} combo_w={avail} "
          f"min={p.session_combo.minimumWidth()} max={p.session_combo.maximumWidth()}")
    if name and need > avail:
        fail += 1
        print(f"ERR 该名字在收起态会被省略号截断\n")


# AI 起名结果（12 字上限）
p._apply_session_name(sid, "帮我查一下今天北京的天气和路况如何", source="ai")
whole_text_fits("AI起名12字")

# 宽/窄窗口两档档位都检查
p.resize(700, 800)
for _ in range(4):
    _app.processEvents()
whole_text_fits("AI起名12字-narrow")

# 本地兜底 20 字
p._apply_session_name(sid, "帮我写一个快速排序的Python实现并且附上测试用例", source="local")
whole_text_fits("本地命名20字-narrow")
p.resize(1280, 800)
for _ in range(4):
    _app.processEvents()
whole_text_fits("本地命名20字-wide")

print("失败项:", fail)
os._exit(1 if fail else 0)   # 跳过 Qt offscreen 退出析构，避免误报崩溃退出码