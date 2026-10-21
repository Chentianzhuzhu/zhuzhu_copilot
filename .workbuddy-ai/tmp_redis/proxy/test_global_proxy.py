"""全局代理端到端测试：路由决策 / 本地 SOCKS5+HTTP 转发 / 系统代理快照恢复 / 服务生命周期。

凭据从 .workbuddy-ai/tmp_redis/sshrun.py 读取（仅本机存在，不入库）。
"""
import os
import socket
import ssl
import sys
import tempfile
import types
from pathlib import Path

SRC = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\src"
TMP = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\.workbuddy-ai\tmp_redis"
sys.path.insert(0, SRC)
sys.path.insert(0, TMP)

from sshrun import HOST, USER, PWD  # noqa: E402

CFG = {
    "proxy_enabled": "1",
    "proxy_mode": "global",
    "proxy_domains": "github.com",
    "proxy_host": HOST,
    "proxy_port": "22",
    "proxy_user": USER,
    "proxy_pass": PWD,
    "proxy_local_port": "0",
}
fake = types.ModuleType("zhuzhu_Copilot.core.agent_skills")
fake.settings_view = lambda: CFG
fake.cap_enabled = lambda key: True
pkg = types.ModuleType("zhuzhu_Copilot")
pkg.__path__ = []
core = types.ModuleType("zhuzhu_Copilot.core")
core.__path__ = [SRC + r"\zhuzhu_Copilot\core"]
sys.modules.setdefault("zhuzhu_Copilot", pkg)
sys.modules.setdefault("zhuzhu_Copilot.core", core)
sys.modules["zhuzhu_Copilot.core.agent_skills"] = fake

from zhuzhu_Copilot.core import agent_proxy, agent_proxy_local, agent_sysproxy  # noqa: E402

UA = "zhuzhu-global-proxy-test"


def http_get_via_tls(tls_sock, host, path="/"):
    req = f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUser-Agent: {UA}\r\nConnection: close\r\n\r\n"
    tls_sock.sendall(req.encode())
    data = b""
    while True:
        chunk = tls_sock.recv(65536)
        if not chunk:
            break
        data += chunk
    head = data.split(b"\r\n", 1)[0].decode("iso-8859-1")
    return head, data


def case_routing():
    assert agent_proxy.route_host("github.com") == "ssh", "global -> ssh"
    assert agent_proxy.route_host("127.0.0.1") == "direct", "loopback direct"
    CFG["proxy_mode"] = "fallback"
    assert agent_proxy.route_host("api.github.com") == "ssh", "domain rule -> ssh"
    assert agent_proxy.route_host("www.baidu.com") == "direct", "fallback non-match -> direct"
    CFG["proxy_domains"] = ""
    assert agent_proxy.route_host("github.com") == "direct", "fallback no rule -> direct"
    CFG["proxy_mode"] = "global"
    CFG["proxy_domains"] = "github.com"
    print("A routing: OK")


def case_http_connect(port):
    s = socket.create_connection(("127.0.0.1", port), 10)
    s.sendall(b"CONNECT api.github.com:443 HTTP/1.1\r\nHost: api.github.com:443\r\n\r\n")
    head = b""
    while b"\r\n\r\n" not in head:
        head += s.recv(4096)
    assert b" 200 " in head.split(b"\r\n")[0], head
    ctx = ssl.create_default_context()
    tls = ctx.wrap_socket(s, server_hostname="api.github.com")
    status, body = http_get_via_tls(tls, "api.github.com")
    tls.close()
    assert "200" in status and b"current_user_url" in body, status
    print("B HTTP CONNECT -> SSH -> GitHub API: 200")


def case_socks5(port):
    s = socket.create_connection(("127.0.0.1", port), 15)
    s.sendall(b"\x05\x01\x00")
    assert _recv_n(s, 2) == b"\x05\x00"
    host = "api.github.com"
    s.sendall(b"\x05\x01\x00\x03" + bytes([len(host)]) + host.encode() + (443).to_bytes(2, "big"))
    rep = _recv_n(s, 10)
    assert rep[:2] == b"\x05\x00", rep
    ctx = ssl.create_default_context()
    tls = ctx.wrap_socket(s, server_hostname=host)
    status, body = http_get_via_tls(tls, host)
    tls.close()
    assert "200" in status and b"current_user_url" in body, status
    print("C SOCKS5 -> SSH -> GitHub API: 200")


def _recv_n(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("closed")
        buf += chunk
    return buf


def case_direct_route(port):
    CFG["proxy_mode"] = "fallback"
    try:
        s = socket.create_connection(("127.0.0.1", port), 10)
        s.sendall(b"CONNECT www.baidu.com:443 HTTP/1.1\r\nHost: www.baidu.com:443\r\n\r\n")
        head = b""
        while b"\r\n\r\n" not in head:
            head += s.recv(4096)
        assert b" 200 " in head.split(b"\r\n")[0], head
        ctx = ssl.create_default_context()
        tls = ctx.wrap_socket(s, server_hostname="www.baidu.com")
        status, _ = http_get_via_tls(tls, "www.baidu.com")
        tls.close()
        assert "200" in status, status
        print("D fallback direct route (baidu): 200")
    finally:
        CFG["proxy_mode"] = "global"


def case_sysproxy():
    import winreg
    key = r"Software\zhuzhu_Copilot_Test\Internet Settings"
    winreg.CreateKey(winreg.HKEY_CURRENT_USER, key)
    tmp = Path(tempfile.mkdtemp()) / "snap.json"
    old_key, old_path = agent_sysproxy._KEY, agent_sysproxy._snapshot_path
    agent_sysproxy._KEY = key
    agent_sysproxy._snapshot_path = lambda: tmp
    try:
        assert agent_sysproxy.enable(12345) is True
        assert agent_sysproxy.is_pointing_to(12345)
        assert tmp.is_file(), "snapshot written"
        assert agent_sysproxy.restore() is True
        assert not agent_sysproxy.is_pointing_to(12345)
        assert not tmp.exists(), "snapshot removed"
        print("E sysproxy enable/restore snapshot: OK")
    finally:
        agent_sysproxy._KEY, agent_sysproxy._snapshot_path = old_key, old_path
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\zhuzhu_Copilot_Test")
        except OSError:
            pass


def case_service():
    from zhuzhu_Copilot.core import agent_proxy_service
    calls = {"enable": [], "restore": 0}

    class FakeSys:
        @staticmethod
        def enable(port):
            calls["enable"].append(port)
            return True

        @staticmethod
        def restore():
            calls["restore"] += 1
            return True

        @staticmethod
        def current_server():
            return "127.0.0.1:1"

    real = agent_proxy_service.agent_sysproxy
    agent_proxy_service.agent_sysproxy = FakeSys
    try:
        st = agent_proxy_service.apply()
        assert st["active"] and st["port"] > 0, st
        assert calls["enable"], "system proxy enabled"
        CFG["proxy_mode"] = "fallback"
        st = agent_proxy_service.apply()
        assert not st["active"], st
        assert calls["restore"] >= 1
        print("F service apply/stop lifecycle: OK")
    finally:
        CFG["proxy_mode"] = "global"
        agent_proxy_service.shutdown()
        agent_proxy_service.agent_sysproxy = real


if __name__ == "__main__":
    case_routing()
    srv = agent_proxy_local.LocalProxyServer(0)
    srv.start()
    try:
        case_http_connect(srv.port)
        case_socks5(srv.port)
        case_direct_route(srv.port)
        print(f"   counters: total={srv.total} ssh={srv.via_ssh} direct={srv.direct}")
    finally:
        srv.stop()
    case_sysproxy()
    case_service()
    print("ALL GLOBAL PROXY CASES DONE")
