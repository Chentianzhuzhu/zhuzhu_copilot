"""真实系统代理链路测试：接管 Windows 系统代理 → 浏览器同款请求经本机转发器 → 还原。

运行期间会短暂修改 HKCU 系统代理，finally 保证还原。
"""
import sys
import types
import urllib.request

SRC = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\src"
TMP = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\.workbuddy-ai\tmp_redis"
sys.path.insert(0, SRC)
sys.path.insert(0, TMP)

from sshrun import HOST, USER, PWD  # noqa: E402

CFG = {
    "proxy_enabled": "1", "proxy_mode": "global", "proxy_domains": "github.com",
    "proxy_host": HOST, "proxy_port": "22", "proxy_user": USER,
    "proxy_pass": PWD, "proxy_local_port": "0",
}
fake = types.ModuleType("zhuzhu_Copilot.core.agent_skills")
fake.settings_view = lambda: CFG
fake.cap_enabled = lambda k: True
import zhuzhu_Copilot  # noqa: E402
import zhuzhu_Copilot.core as _core  # noqa: E402
sys.modules["zhuzhu_Copilot.core.agent_skills"] = fake
_core.agent_skills = fake

from zhuzhu_Copilot.core import agent_sysproxy  # noqa: E402
from zhuzhu_Copilot.core.agent_proxy_local import LocalProxyServer  # noqa: E402

before = agent_sysproxy._read_values()
srv = LocalProxyServer(0)
srv.start()
try:
    assert agent_sysproxy.enable(srv.port), "enable system proxy"
    assert agent_sysproxy.is_pointing_to(srv.port), agent_sysproxy.current_server()
    req = urllib.request.Request(
        "https://api.github.com", headers={"User-Agent": "sysproxy-e2e"})
    with urllib.request.urlopen(req, timeout=25) as resp:   # 走系统代理
        body = resp.read()
        assert resp.status == 200 and b"current_user_url" in body
    print(f"SYSTEM PROXY E2E OK: 127.0.0.1:{srv.port} total={srv.total} ssh={srv.via_ssh}")
finally:
    srv.stop()
    agent_sysproxy.restore()
    after = agent_sysproxy._read_values()
    assert after == before, "system proxy not restored exactly"
    print("SYSTEM PROXY RESTORED: OK")
