"""agent_proxy SSH 隧道回退端到端测试。

凭据只从环境变量读取，不在仓库中保存任何服务器地址或密码：
  WB_PROXY_HOST / WB_PROXY_PORT / WB_PROXY_USER / WB_PROXY_PASS
未设置时跳过需要真实服务器的用例（B/E），只跑纯逻辑用例（A/C/D）。

用例：
  A. 直连成功 → 不走 SSH（urllib 原路径）
  B. 直连失败 → 回退 SSH 隧道 → 请求成功
  C. 代理未启用 → 直连失败直接抛错
  D. 回退目标为本机地址 → 不走 SSH
  E. SSH 隧道多域名实测（pypi / cloudflare / python）
"""
import os
import sys
import types

SRC = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\src"
sys.path.insert(0, SRC)

CFG = {
    "proxy_enabled": "1",
    "proxy_mode": "fallback",          # 本文件验证回退语义；全局模式见 test_global_proxy.py
    "proxy_domains": "",
    "proxy_host": os.environ.get("WB_PROXY_HOST", ""),
    "proxy_port": os.environ.get("WB_PROXY_PORT", "22"),
    "proxy_user": os.environ.get("WB_PROXY_USER", ""),
    "proxy_pass": os.environ.get("WB_PROXY_PASS", ""),
}
HAS_SERVER = bool(CFG["proxy_host"] and CFG["proxy_user"])
fake = types.ModuleType("zhuzhu_Copilot.core.agent_skills")
fake.settings_view = lambda: CFG
fake.cap_enabled = lambda key: True
pkg = types.ModuleType("zhuzhu_Copilot")
pkg.__path__ = []
core = types.ModuleType("zhuzhu_Copilot.core")
core.__path__ = [SRC + r"\zhuzhu_Copilot\core"]
sys.modules["zhuzhu_Copilot"] = pkg
sys.modules["zhuzhu_Copilot.core"] = core
sys.modules["zhuzhu_Copilot.core.agent_skills"] = fake

from zhuzhu_Copilot.core import agent_proxy  # noqa: E402
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402

UA = {"User-Agent": "zhuzhu-copilot-proxy-test"}
real_urlopen = urllib.request.urlopen


def case_a():
    # 用国内必通的站点验证直连成功路径不触发 SSH 回退
    req = urllib.request.Request("https://www.baidu.com", headers=UA)
    with agent_proxy.urlopen(req, timeout=20) as resp:
        assert resp.status == 200, resp.status
    assert not agent_proxy.last_fallback(), "direct-ok should not engage fallback"
    print("A direct-ok: 200 (no fallback engaged)")


def case_b():
    def boom(req, timeout=None, **k):
        raise urllib.error.URLError("simulated direct failure")
    urllib.request.urlopen = boom
    try:
        req = urllib.request.Request("https://api.github.com", headers=UA)
        with agent_proxy.urlopen(req, timeout=25) as resp:
            assert resp.status == 200, resp.status
            body = resp.read()
        assert b"current_user_url" in body, "unexpected body"
        print("B direct-fail -> SSH fallback: 200, body ok")
        print("  last_fallback:", agent_proxy.last_fallback())
    finally:
        urllib.request.urlopen = real_urlopen


def case_c():
    def boom(req, timeout=None, **k):
        raise urllib.error.URLError("simulated direct failure")
    urllib.request.urlopen = boom
    try:
        CFG["proxy_enabled"] = "0"
        req = urllib.request.Request("https://api.github.com", headers=UA)
        agent_proxy.urlopen(req, timeout=3)
        print("C disabled: UNEXPECTED success")
    except urllib.error.URLError as e:
        print("C disabled -> direct error raised as-is:", e)
    finally:
        urllib.request.urlopen = real_urlopen
        CFG["proxy_enabled"] = "1"


def case_d():
    def boom(req, timeout=None, **k):
        raise urllib.error.URLError("simulated direct failure")
    urllib.request.urlopen = boom
    try:
        req = urllib.request.Request("http://127.0.0.1:59999/", headers=UA)
        agent_proxy.urlopen(req, timeout=2)
        print("D loopback: UNEXPECTED success")
    except (urllib.error.URLError, OSError) as e:
        print("D loopback -> no proxy attempted:", type(e).__name__)
    finally:
        urllib.request.urlopen = real_urlopen


def case_e():
    targets = [
        ("https://pypi.org/simple/", b"pypi"),
        ("https://www.cloudflare.com/cdn-cgi/trace", b"fl="),
        # python.org 的 CDN 可能强制 gzip，正文标记不可靠，仅校验 200
        ("https://www.python.org/", b""),
    ]
    for url, marker in targets:
        def boom(req, timeout=None, **k):
            raise urllib.error.URLError("simulated direct failure")
        urllib.request.urlopen = boom
        try:
            req = urllib.request.Request(url, headers=UA)
            with agent_proxy.urlopen(req, timeout=25) as resp:
                body = resp.read(16384)
                ok = resp.status == 200 and (not marker or marker in body.lower())
                print(f"E {url}: status={resp.status} marker={'hit' if ok else 'MISS'}")
                assert ok, f"unexpected response for {url}"
        finally:
            urllib.request.urlopen = real_urlopen


if __name__ == "__main__":
    case_a()
    if HAS_SERVER:
        case_b()
    else:
        print("B skipped: set WB_PROXY_HOST/WB_PROXY_USER/WB_PROXY_PASS to run")
    case_c()
    case_d()
    if HAS_SERVER:
        case_e()
    else:
        print("E skipped: no SSH server configured")
    print("ALL CASES DONE")
