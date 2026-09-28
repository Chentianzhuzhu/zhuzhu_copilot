"""网络防御测试：IP 工具函数 / 回环判定 / 阈值配置驱动（不触碰真实网卡）"""
import time

from zhuzhu_Copilot.core import network_defense as nd


def test_fmt_ip_roundtrip():
    assert nd._fmt_ip(0) == "0.0.0.0"
    assert nd._fmt_ip(nd._ip_to_dw("192.168.1.10")) == "192.168.1.10"
    assert nd._fmt_ip(nd._ip_to_dw("10.0.0.1")) == "10.0.0.1"


def test_is_loopback():
    for ip in ("127.0.0.1", "127.8.8.8", "::1", "0.0.0.0"):
        assert nd._is_loopback(ip), ip
    for ip in ("192.168.1.10", "10.0.0.5", "172.16.0.1"):
        assert not nd._is_loopback(ip), ip


def test_thresholds_from_config():
    d = nd.NetworkDefender()
    # 默认阈值（security.json network 段）
    assert d.syn_threshold == 100
    assert d.seg_threshold == 5000
    assert d.udp_threshold == 4000
    assert d.scan_ports == 60
    assert d.scan_rounds >= 1 and d.syn_rounds >= 1


def test_check_flood_scan_detection(monkeypatch):
    """端口扫描判定：单源访问 60+ 不同本地端口、连续 2 轮 → 封禁"""
    d = nd.NetworkDefender()
    rows = [{"state": nd.MIB_TCP_STATE_SYN_RCVD, "local_port": i,
             "remote_ip": "203.0.113.7"} for i in range(1, 65)]
    monkeypatch.setattr(nd, "_tcp_rows", lambda: rows)
    monkeypatch.setattr(nd, "_tcp_stats", lambda: (0, 0))
    monkeypatch.setattr(nd, "_udp_datagrams", lambda: 0)
    monkeypatch.setattr(nd, "block_ip", lambda ip: True)
    r1 = d._check_flood()
    assert r1["scan_sources"] == []          # 第一轮：未达确认轮数
    r2 = d._check_flood()
    assert "203.0.113.7" in r2["scan_sources"]
    assert "203.0.113.7" in r2["blocked"]


def test_check_flood_loopback_excluded(monkeypatch):
    """回环来源不参与扫描/洪泛判定（防本地工具误封）"""
    d = nd.NetworkDefender()
    rows = [{"state": nd.MIB_TCP_STATE_SYN_RCVD, "local_port": i,
             "remote_ip": "127.0.0.1"} for i in range(1, 65)]
    monkeypatch.setattr(nd, "_tcp_rows", lambda: rows)
    monkeypatch.setattr(nd, "_tcp_stats", lambda: (0, 0))
    monkeypatch.setattr(nd, "_udp_datagrams", lambda: 0)
    r = d._check_flood()
    assert r["scan_sources"] == [] and r["syn_sources"] == []


def test_check_flood_udp_rate(monkeypatch):
    """UDP 入数据报速率差分 → udp_flood"""
    d = nd.NetworkDefender()
    monkeypatch.setattr(nd, "_tcp_rows", lambda: [])
    monkeypatch.setattr(nd, "_tcp_stats", lambda: (0, 0))
    monkeypatch.setattr(nd, "_udp_datagrams", lambda: 10_000_000)
    d._last_udp = 1_000_000
    d._last_udp_time = time.time() - 9.0     # 9 秒 +9M 数据报 → 100 万/秒
    r = d._check_flood()
    assert r["udp_flood"] is True
    assert r["udp_rate"] > d.udp_threshold