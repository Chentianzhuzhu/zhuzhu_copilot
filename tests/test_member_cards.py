"""会员/积分卡片的视觉契约：尺寸、推荐强调、按钮态与发光绘制。

这些断言保护的是**视觉层级**本身（用户明确要求）：卡面为一色（深色纯黑 / 浅色纯白）、
可交互主按钮一律实心反色、禁用态必须回落成描边幽灵按钮、顶边高光始终可见 ——
改样式时若把层级改坏，这里会直接失败。
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest                                            # noqa: E402
from PyQt6.QtWidgets import QApplication                 # noqa: E402

_app = QApplication.instance() or QApplication([])

from zhuzhu_Copilot.ui import member_cards as mc          # noqa: E402
from zhuzhu_Copilot.ui import styles                      # noqa: E402


@pytest.fixture(autouse=True)
def _light_palette():
    styles.set_palette("light")
    yield


def _card(**kw):
    base = dict(title="Pro 版", price_main="¥7", price_unit="/ 月",
                perks=["每月 1000 积分", "每日签到 +100"], cta="购买")
    base.update(kw)
    return mc.ProductCard(**base)


def test_card_geometry_matches_tokens():
    """宽度固定、高度是**最小值**：权益条目多时卡片按内容长高，文字不被压扁。"""
    card = _card()
    assert card.width() == mc.CARD_W
    assert card.height() >= mc.CARD_H
    pack = mc.build_product_card(title="150 积分", price_main="¥1", perks=["轻量补充"],
                                 cta="购买", compact=True)
    assert pack.width() == mc.CARD_W and pack.height() >= mc.PACK_H


def test_filled_cta_is_inverse_of_card_fill():
    """可交互主按钮一律实心反色：浅色主题黑底白字（与白卡面相反）。"""
    pro = _card(recommended=True, badge="推荐")
    normal = _card()
    for card in (pro, normal):
        qss = card.buy_btn.styleSheet()
        assert "background: #000000" in qss      # 浅色主题：与白卡面相反
        assert "color: #FFFFFF" in qss
        assert "border: none" in qss             # 实心，不留描边
    # 推荐胶囊只在推荐档出现
    assert pro._badge is not None
    assert normal._badge is None


def test_disabled_cta_falls_back_to_outline():
    card = _card(recommended=True, cta="不可购买", cta_enabled=False)
    qss = card.buy_btn.styleSheet()
    assert card.buy_btn.isEnabled() is False
    assert "background: transparent" in qss
    assert f"color: {styles.PALETTE['text_secondary']}" in qss   # 置灰必须看得出来


def test_set_cta_switches_style_between_states():
    card = _card(recommended=True)
    card.set_cta("续费", True, "续费叠加 30 天")
    assert card.buy_btn.text() == "续费" and card.buy_btn.isEnabled()
    assert "background: #000000" in card.buy_btn.styleSheet()   # 实心反色
    card.set_cta("当前版本", False)
    assert "background: transparent" in card.buy_btn.styleSheet()


def test_current_tier_gets_chip():
    card = _card(current=True)
    assert card._chip is not None and card._chip.text() == "当前"
    assert _card()._chip is None


def test_glow_only_on_recommended(monkeypatch):
    """发光画在卡片外的留白里：只有推荐档那圈像素是不透明的。"""
    plain = _card()
    glow = _card(recommended=True, badge="推荐")
    for c in (plain, glow):
        c.resize(c.width(), c.height())
        c.show()
    _app.processEvents()
    p_plain = plain.grab().toImage()
    p_glow = glow.grab().toImage()
    cx = plain.width() // 2
    y = mc._EDGE - 6            # 卡面之外、光晕之内
    assert p_plain.pixelColor(cx, y).alpha() == 0, "普通卡片外侧不应有任何着色"
    assert p_glow.pixelColor(cx, y).alpha() > 0, "推荐卡片应有外发光"
    # 卡面本身两个都要画出来（不透明）
    mid = plain.height() // 2
    assert p_plain.pixelColor(cx, mid).alpha() == 255
    assert p_glow.pixelColor(cx, mid).alpha() == 255
    plain.close()
    glow.close()


def test_edge_highlight_line_present():
    """顶边高光：卡面顶部第一行内部像素比卡片中部更亮（浅色主题下的高光带）。"""
    card = _card()
    card.resize(card.width(), card.height())
    card.show()
    _app.processEvents()
    img = card.grab().toImage()
    cx = card.width() // 2
    top = img.pixelColor(cx, mc._EDGE + 1).name()
    mid = img.pixelColor(cx, card.height() // 2).name()
    # 浅色主题卡面是纯白，高光改用极浅冷灰（白线画在白卡面上等于没画）
    assert top == "#e3e8f0", f"顶边高光应为极浅冷灰，实际 {top}"
    assert top != mid
    card.close()
