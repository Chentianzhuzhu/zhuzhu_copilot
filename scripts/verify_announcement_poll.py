# -*- coding: utf-8 -*-
"""验证「系统公告 30s 轮询」：启动即首拉、周期 30s、内容变化才广播、登出停止。

无需真实网络：monkeypatch auth_client._http_get_json 返回可控 payload。
无需真实 QApplication 事件循环：直接驱动 QTimer 的 timeout 信号与内部方法。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from PyQt6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from zhuzhu_Copilot.core import auth_client as ac  # noqa: E402

ok = True


def check(label: str, cond: bool) -> None:
    global ok
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")
    ok = ok and cond


# ---------- 1. 轮询间隔常量 ----------
check("ANNOUNCEMENT_POLL_INTERVAL_S == 30",
      ac.ANNOUNCEMENT_POLL_INTERVAL_S == 30)

# ---------- 2. 假 HTTP：记录调用并返回可变公告 ----------
calls: list[str] = []
state = {"content": ""}


def fake_get(url: str, token: str = "", timeout: int = 15):
    calls.append(url)
    if url.endswith("/api/announcement"):
        return True, {"code": 0, "data": {"content": state["content"],
                                          "enabled": bool(state["content"])}}, 200
    return False, {}, 404


ac._http_get_json = fake_get  # type: ignore[assignment]

client = ac.AuthClient()

# ---------- 3. 启动即首拉 ----------
emitted: list[dict] = []
client.announcement_updated.connect(lambda d: emitted.append(dict(d)))

state["content"] = "首次公告"
client.start_announcement_polling()
import time as _t


def _pump(until, limit=80):
    """等待条件成立；期间 pump 事件循环，让后台线程的跨线程信号得以投递。"""
    for _ in range(limit):
        _app.processEvents()
        if until():
            return True
        _t.sleep(0.05)
    _app.processEvents()
    return until()


_pump(lambda: bool(calls))
check(f"启动即发起首次轮询（calls={len(calls)}）", len(calls) >= 1)
check(f"首拉路径正确 /api/announcement（{calls[0] if calls else ''}）",
      bool(calls) and calls[0].endswith("/api/announcement"))
_pump(lambda: bool(emitted))
check(f"首次公告已广播（{emitted[-1]['content'] if emitted else ''}）",
      bool(emitted) and emitted[-1]["content"] == "首次公告")
check("公告已缓存到 client.announcement()", client.announcement() == "首次公告")

# ---------- 4. 定时器确实以 30s 周期在跑 ----------
t = getattr(client, "_announce_timer", None)
check("公告轮询定时器已创建", t is not None)
check(f"定时器间隔 = {t.interval() if t else -1} ms（应为 30000）",
      bool(t) and t.interval() == 30000)
check("定时器处于激活状态", bool(t) and t.isActive())

# ---------- 5. 内容不变 → 不重复广播 ----------
before = len(emitted)
n_before = len(calls)
client.poll_announcement_async()
_pump(lambda: len(calls) > n_before)
_pump(lambda: False, limit=6)   # 多 pump 几轮，确认没有多余广播
check(f"内容未变时不重复广播（emitted 增量={len(emitted) - before}）",
      len(emitted) == before)

# ---------- 6. 内容变化 → 广播 ----------
state["content"] = "更新后的公告"
client.poll_announcement_async()
_pump(lambda: bool(emitted) and emitted[-1]["content"] == "更新后的公告")
check(f"内容变化后广播新公告（{emitted[-1]['content'] if emitted else ''}）",
      bool(emitted) and emitted[-1]["content"] == "更新后的公告")

# ---------- 7. 幂等：重复 start 不叠加定时器 ----------
same = getattr(client, "_announce_timer", None)
client.start_announcement_polling()
check("重复调用 start 不重建定时器（幂等）",
      getattr(client, "_announce_timer", None) is same)

# ---------- 8. 停止轮询 ----------
client.stop_announcement_polling()
check("stop 后定时器已清理且不再激活",
      getattr(client, "_announce_timer", None) is None
      and (not t or not t.isActive()))

# ---------- 9. 清凭证时自动停止 ----------
client.start_announcement_polling()
client.clear_credentials()
check("清凭证（登出/强退）后轮询自动停止",
      getattr(client, "_announce_timer", None) is None)

print()
print("ANNOUNCEMENT_POLL", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
