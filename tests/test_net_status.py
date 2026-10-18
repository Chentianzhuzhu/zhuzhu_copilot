# -*- coding: utf-8 -*-
"""网络状态判定（core/net_status.py）回归测试。

覆盖本次修复的两个核心点：
1. 判定不再依赖公共 DNS（Cloudflare / Google DNS 被网关拦截也不能算离线）；
2. 探测必须「短、有限、可预算」——UI 线程永不等网络。
"""
from __future__ import annotations

import socket
import time

import pytest

from zhuzhu_Copilot.core import net_status


@pytest.fixture(autouse=True)
def _clean_state():
    net_status.reset()
    yield
    net_status.reset()


# ---------------------------------------------------------------- 状态合成
def test_traffic_success_wins_over_blocked_probe():
    """模型 API 刚成功 → 即使端点探测全失败，也必须显示在线。"""
    net_status.note_success()
    assert net_status.decide(False, "online") == net_status.STATE_ONLINE


def test_probe_success_is_online():
    assert net_status.decide(True, None) == net_status.STATE_ONLINE


def test_blocked_probe_with_link_is_limited_not_offline():
    """企业网关拦截探测目标：系统有链路 → 「受限」，绝不能谎报「离线」。"""
    assert net_status.decide(False, "online") == net_status.STATE_LIMITED
    assert net_status.decide(False, None) == net_status.STATE_LIMITED


def test_no_link_is_offline():
    assert net_status.decide(False, "offline") == net_status.STATE_OFFLINE


def test_unknown_probe_with_link_is_online():
    """没有可探测目标（例如只配了本机代理）时，以系统可达性为准。"""
    assert net_status.decide(None, "online") == net_status.STATE_ONLINE
    assert net_status.decide(None, None) == net_status.STATE_LIMITED


def test_traffic_trust_window_expires(monkeypatch):
    net_status.note_success()
    assert net_status.traffic_ok(90) is True
    # 把「上次成功」推到很久以前：不应再被当作在线证据
    monkeypatch.setattr(net_status, "_last_ok", time.monotonic() - 1000)
    assert net_status.traffic_ok(90) is False


# ---------------------------------------------------------------- 退避
def test_interval_backs_off_and_caps():
    assert net_status.interval(0) == 5.0
    assert net_status.interval(1) == 10.0
    assert net_status.interval(2) == 20.0
    assert net_status.interval(3) == 30.0
    assert net_status.interval(99) == 30.0


def test_note_probe_resets_streak_on_success():
    for _ in range(3):
        net_status.note_probe(False)
    assert net_status.fail_streak() == 3
    net_status.note_probe(True)
    assert net_status.fail_streak() == 0


# ---------------------------------------------------------------- 探测
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_probe_hits_open_port():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    try:
        host, port = srv.getsockname()
        assert net_status.probe([(host, port)], timeout=0.5) is True
    finally:
        srv.close()


def test_probe_closed_port_is_false():
    assert net_status.probe([("127.0.0.1", _free_port())], timeout=0.3) is False


def test_probe_without_targets_is_unknown():
    assert net_status.probe([], timeout=0.1) is None


def test_probe_respects_total_budget():
    """黑洞地址（不回 RST、只丢包）：总耗时必须被预算截断，不能无限等。

    旧实现就是在这里卡死 UI 的——两个目标各 2s 超时，一次检测占满 4s。
    """
    budget = 0.6
    start = time.monotonic()
    result = net_status.probe([("10.255.255.1", 53), ("10.255.255.1", 53)],
                              timeout=5.0, budget=budget)
    elapsed = time.monotonic() - start
    assert result is False
    assert elapsed < budget + 0.6, f"探测超出预算：{elapsed:.2f}s"


# ---------------------------------------------------------------- 端点解析
def test_host_port_parsing():
    assert net_status._host_port("https://api.example.com/v1") == ("api.example.com", 443)
    assert net_status._host_port("http://api.example.com/v1") == ("api.example.com", 80)
    assert net_status._host_port("https://api.example.com:8443/v1") == ("api.example.com", 8443)
    assert net_status._host_port("api.example.com") == ("api.example.com", 443)


def test_host_port_skips_local():
    """本机地址说明不了外网状态，不纳入探测目标。"""
    assert net_status._host_port("http://127.0.0.1:8080/v1") is None
    assert net_status._host_port("http://localhost:8080/v1") is None
    assert net_status._host_port("") is None


def test_default_targets_never_include_public_dns(monkeypatch):
    """回归：公共 DNS（含 Cloudflare 1.1.1.1）不得再作为连通性判据。"""
    monkeypatch.setattr(net_status, "_proxy_endpoint", lambda: None)
    monkeypatch.setattr(net_status, "_update_server_endpoint", lambda: ("update.example.com", 443))
    monkeypatch.setattr(net_status, "_model_endpoints", lambda: [("api.example.com", 443)])
    targets = net_status.default_targets(force_refresh=True)
    # 模型 API 优先：更新服务器常挂 Cloudflare，被网关封掉不代表模型不可用
    assert targets == [("api.example.com", 443), ("update.example.com", 443)]
    banned = {"1.1.1.1", "8.8.8.8", "223.5.5.5", "9.9.9.9"}
    assert not banned & {h for h, _ in targets}


def test_probe_budget_holds_even_when_dns_hangs(monkeypatch):
    """DNS 解析不受 socket 超时约束：预算到点必须硬返回，不能跟着卡住。"""
    real = socket.getaddrinfo

    def _hang(*a, **kw):
        time.sleep(5)
        return real(*a, **kw)

    monkeypatch.setattr(socket, "getaddrinfo", _hang)
    start = time.monotonic()
    result = net_status.probe([("dns-will-hang.invalid", 443)], timeout=5, budget=0.5)
    elapsed = time.monotonic() - start
    assert result is False
    assert elapsed < 1.2, f"预算失效，耗时 {elapsed:.2f}s"


# ---------------------------------------------------------------- UI 线程零 I/O
def test_panel_check_network_does_no_inline_io():
    """回归：顶栏网络检测入口里不允许再出现任何同步网络调用。

    旧实现把 socket.create_connection 直接写在 QTimer 回调里，网关丢包时
    每 5s 卡 4s（拖窗口都掉帧）。这条断言把「UI 线程零 I/O」钉死在源码层面。
    """
    import inspect

    from zhuzhu_Copilot.ui import agent_panel

    src = inspect.getsource(agent_panel.AgentPanel._check_network)
    for banned in ("socket.", "create_connection", "urlopen", "getaddrinfo"):
        assert banned not in src, f"_check_network 里出现了同步网络调用：{banned}"
    assert "threading.Thread" in src   # 探测必须交给后台线程
