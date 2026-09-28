"""欢迎页时段欢迎语（按本地小时整点区间返回对应文案，覆盖全天 24 小时）。

时间区间（用户需求，整点闭区间）：
- 0-6 凌晨   7-11 早上   12-14 中午   15-19 下午   20-23 晚上
"""
import datetime
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from zhuzhu_Copilot.ui import agent_panel as ap


def _g(hour: int) -> str:
    return ap._welcome_greeting(datetime.datetime(2026, 9, 19, hour, 0, 0))


def test_midnight_0_to_6():
    for h in (0, 3, 6):
        assert _g(h) == "凌晨了，快睡吧！！！幸苦了一天喵喵喵！！！"


def test_morning_7_to_11():
    for h in (7, 9, 11):
        assert _g(h) == "早上好，新的一天开始了，喵~"


def test_noon_12_to_14():
    for h in (12, 13, 14):
        assert _g(h) == "中午了，有点困了，喵~"


def test_afternoon_15_to_19():
    for h in (15, 17, 19):
        assert _g(h) == "下午好，继续努力！！！"


def test_evening_20_to_23():
    for h in (20, 21, 23):
        assert _g(h) == "瞌睡了，但bug还没修完！！！"


def test_covers_all_24_hours():
    """全天每个整点均有对应文案（无空档），且 5 个时段文案都实际出现"""
    greets = {_g(h) for h in range(24)}
    assert len(greets) == 5


def test_default_now_uses_local_time():
    """不传时间时按本地当前小时返回，且必然命中 5 个文案之一"""
    assert ap._welcome_greeting() in {g for _, _, g in ap._WELCOME_GREETINGS}
