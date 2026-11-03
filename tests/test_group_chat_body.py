"""群聊正文的「长文不撑窗口」机制：预览折叠 + 行距自适应。

群聊里一条长消息若整段渲染，会把对话区撑得极高，用户得手动拉大窗口才看得全。
这里锁定三条契约：
1. 超过预览字数 → 只渲染前 N 字并给出「展开全文」；
2. 行距随正文长度自适应（长文收紧、短文宽松）；
3. 展开后渲染完整内容，不丢字。
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest                                            # noqa: E402
from PyQt6.QtWidgets import QApplication                 # noqa: E402

_app = QApplication.instance() or QApplication([])

from zhuzhu_Copilot.ui import agent_chat_bubbles as cb  # noqa: E402
from zhuzhu_Copilot.ui import group_chat_dialog as gcd  # noqa: E402
from zhuzhu_Copilot.ui import styles                     # noqa: E402


@pytest.fixture(autouse=True)
def _palette():
    styles.set_palette("light")
    yield


def _style():
    c = styles.PALETTE
    return cb.ChatStyle(
        card=c["card"], border=c["border"], border_soft=c["border"], dash=c["border"],
        text=c["text"], text_dim=c["text_secondary"], accent=c["primary"],
        muted=c["text_secondary"], icon_shell=c["field"], icon_color=c["text_secondary"],
        tag_bg=c["field"], tag_fg=c["text_secondary"], user_bg=c["primary"],
        user_fg="#FFFFFF", cmd_fg=c["text"], ok_fg=c["primary"], hover=c["hover"],
        panel=c["card"], bg=c["bg_top"])


def test_preview_truncates_long_text_only():
    long_text = "长" * (gcd.BODY_PREVIEW_CHARS + 500)
    shown, truncated = gcd._body_preview(long_text)
    assert truncated is True
    assert len(shown) <= gcd.BODY_PREVIEW_CHARS + 2      # 预览 + 省略号
    short = "短消息"
    assert gcd._body_preview(short) == (short, False)


def test_line_height_adapts_to_length():
    assert gcd._body_line_height("短") == gcd.LINE_HEIGHT_NORMAL
    long_text = "长" * (gcd.BODY_COMPACT_CHARS + 10)
    assert gcd._body_line_height(long_text) == gcd.LINE_HEIGHT_COMPACT
    assert gcd.LINE_HEIGHT_COMPACT < gcd.LINE_HEIGHT_NORMAL


def test_row_collapsed_then_expanded_renders_everything():
    row = gcd._AgentRow(_style(), "架构师", "")
    long_text = "正文内容。" * 400
    row.set_markdown(long_text)
    collapsed = row.body._body.text().count("正文内容")
    assert collapsed < 400, "折叠态只渲染预览"
    assert "展开全文" in row.more_lbl.text()
    row._toggle_more()
    assert row.body._body.text().count("正文内容") == 400, "展开后应渲染全文"
    assert row.more_lbl.text() == "收起"


def test_short_row_has_no_toggle():
    row = gcd._AgentRow(_style(), "乙", "")
    row.set_markdown("就一句话")
    assert not row.more_lbl.isVisible()
    assert row.more_lbl.text() == ""
