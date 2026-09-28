"""本地 SMB 等端口误报修复测试：本地服务端口不判为暴露，真实高危端口仍提示"""
from unittest.mock import patch

from zhuzhu_Copilot.core.security import SecurityScanner


def _check(listening, fw_off=()):
    with patch("zhuzhu_Copilot.core.security._listening_ports", lambda: listening), \
         patch("zhuzhu_Copilot.core.security._firewall_off_profiles", lambda: list(fw_off)):
        return SecurityScanner().check_network()


def test_local_service_ports_not_flagged():
    """SMB(445) / NetBIOS(139·135) / mDNS(5353) / SSDP(1900) 不再误报"""
    res = _check({445, 139, 135, 137, 138, 5353, 1900})
    assert res["high_risk_listening"] == []
    assert res["risk"] is False


def test_real_risk_ports_still_flagged():
    """RDP/FTP/MySQL/Redis 等应用端口仍会提示（不削弱真实风险检测）"""
    res = _check({3389, 21, 3306, 6379, 445})
    ports = {x["port"] for x in res["high_risk_listening"]}
    assert {3389, 21, 3306, 6379} <= ports
    assert 445 not in ports
    # 端口说明（名称）随配置输出
    info = {x["port"]: x["name"] for x in res["high_risk_listening"]}
    assert info[3389] == "RDP"


def test_override_via_config(monkeypatch):
    """配置可覆盖本地服务白名单：把 3389 加入 local_allowed_ports 后不再提示"""
    from zhuzhu_Copilot.core.security_engine.config import config
    original = list(config.list_of("network.local_allowed_ports", []))
    try:
        config.data["network"]["local_allowed_ports"] = original + [3389]
        res = _check({3389})
        assert res["high_risk_listening"] == []
    finally:
        config.data["network"]["local_allowed_ports"] = original