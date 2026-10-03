"""插件生成链路回归：三件套落盘 + 冒烟自检 + AI 自修 + 超时/抓取策略。

真实执行的部分（不是纸面断言）：
  · 自检全程跑**真子进程**：py_compile → `server.py --selftest`（起本地 HTTP 拉页面并逐个调
    /api 端点）→ stdio MCP initialize/tools/list 握手；
  · 本地页面抓取走**真实 http.server**（验证「支持抓取本地回环页面」这条需求）。
仅「AI 设计 JSON 从哪来」这一步用替身：测试环境没有 API key，无法真实调 LLM；
它之后的落盘 → 自检 → 自修 → 登记判定全部按真实代码路径执行。
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import pytest

from zhuzhu_Copilot.core import (agent_engine, agent_plugins, agent_runtime,
                                 agent_skills, agent_tools)

# ── 测试用插件设计（web 型：页面 + 端点，均真实可跑） ──────────────────────────

PAGE_OK = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>demo</title><style>body{background:#101216;color:#e8e8e8}</style></head><body>
<h1>计数器</h1><div id="s">加载中</div>
<button onclick="act('inc')">+1</button><button onclick="act('reset')">重置</button>
<div id="msg"></div><script>
async function refresh(){const r=await fetch('/api/state');const j=await r.json();
  document.getElementById('s').textContent=j.result;}
async function act(n){const r=await fetch('/api/'+n,{method:'POST',
  headers:{'Content-Type':'application/json'},body:'{}'});const j=await r.json();
  document.getElementById('msg').textContent=j.ok?('已执行 '+n):('失败: '+j.error);refresh();}
refresh();</script></body></html>
"""

API_OK = [
    {"name": "state", "description": "读取当前计数", "method": "GET", "args": [],
     "implementation": "return f\"count={_STATE.get('count', 0)}\""},
    {"name": "inc", "description": "计数加一", "method": "POST", "args": [],
     "implementation": ("try:\n    _STATE['count'] = int(_STATE.get('count', 0)) + 1\n"
                        "    return f\"count={_STATE['count']}\"\n"
                        "except Exception as e:\n    return f\"错误: {e}\"")},
    {"name": "reset", "description": "清零", "method": "POST", "args": [],
     "implementation": "_STATE['count'] = 0\nreturn \"已清零\""},
]

SKILL_MD_OK = ("---\nname: demo_counter\ndescription: 演示计数器\n---\n\n# demo\n"
               "先 web_url 再 browser_open 打开给用户。\n")


def _web_spec(**over):
    spec = {"name": "demo_counter", "summary": "演示计数器",
            "skill_md": SKILL_MD_OK, "web_page": PAGE_OK, "api": API_OK}
    spec.update(over)
    return spec


def _mcp_spec(name="demo_mcp"):
    return {"name": name, "summary": "演示 MCP",
            "tools": [{"name": "echo", "description": "回显",
                       "input_schema": {"type": "object",
                                        "properties": {"t": {"type": "string"}},
                                        "required": ["t"]},
                       "implementation": "return str(args.get('t', ''))"}]}


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    """隔离插件目录/技能目录/MCP 配置；自检用真实解释器（必须能真跑子进程）"""
    monkeypatch.setattr(agent_plugins, "PLUGINS_DIR", tmp_path / "plugins")
    monkeypatch.setattr(agent_skills, "CONFIG_DIR", tmp_path / "agent")
    monkeypatch.setattr(agent_plugins, "_plugin_interpreter", lambda: sys.executable)
    monkeypatch.setattr(agent_runtime, "python_interpreter", lambda: sys.executable)
    agent_skills._MCP_CACHE["data"] = None
    agent_skills.invalidate_skills_cache()
    return tmp_path


def _mcp_names() -> list:
    return [s.get("name") for s in agent_skills.load_mcp_servers()]


# ══════════════ 1. 超时：生成类不设「放弃等待」上限 ══════════════

def test_generation_tools_have_no_abandon_timeout():
    """create_plugin 内部是完整 LLM 往返（设计+自检+自修），40s 放弃会让结果与上下文错位。"""
    assert agent_engine._tool_wait_timeout("create_plugin") is None
    assert agent_engine._tool_wait_timeout("run_command") is None
    assert agent_engine._tool_wait_timeout("fast_download") == \
        agent_engine._DOWNLOAD_WAIT_TIMEOUT_S
    # 其余工具仍是 40s 上限：不能因为放开生成类而让所有工具都能无限阻塞
    for name in ("web_fetch", "read_file", "search_files", "create_skill"):
        assert agent_engine._tool_wait_timeout(name) == \
            agent_engine._DEFAULT_WAIT_TIMEOUT_S, name


# ══════════════ 2. 抓取：本地回环 / 内网放行 ══════════════

def test_local_and_intranet_targets_allowed():
    """用户要求支持抓取本地回环与内网页面（含自建插件 UI、dev server、内网系统）。"""
    from urllib.parse import urlparse
    assert agent_tools._NET_TARGET_POLICY == "open", "默认档位应为完全放开"
    for url in ("http://127.0.0.1:8000/api/state", "http://localhost:3000/",
                "http://192.168.1.20/index.html", "http://10.0.8.7/",
                "http://172.16.3.9:81/x", "http://intranet.internal/portal"):
        assert agent_tools._ssrf_blocked(urlparse(url)) is False, url


def test_metadata_blocked_when_policy_tightened(monkeypatch):
    """收紧档位（private/strict）必须重新拦住元数据端点与回环——策略开关真的生效。"""
    from urllib.parse import urlparse
    monkeypatch.setattr(agent_tools, "_NET_TARGET_POLICY", "strict")
    assert agent_tools._ssrf_blocked(urlparse("http://169.254.169.254/latest/meta-data/"))
    assert agent_tools._ssrf_blocked(urlparse("http://metadata.google.internal/"))
    assert agent_tools._ssrf_blocked(urlparse("http://127.0.0.1:8888/"))
    monkeypatch.setattr(agent_tools, "_NET_TARGET_POLICY", "private")
    assert agent_tools._ssrf_blocked(urlparse("http://169.254.169.254/")), "元数据仍须拦"
    assert agent_tools._ssrf_blocked(urlparse("http://127.0.0.1:9000/")) is False, \
        "private 档应放行回环"
    assert agent_tools._ssrf_blocked(urlparse("http://10.1.2.3/")) is False, "private 档放行内网"


def test_web_fetch_reads_local_loopback_page():
    """端到端：真实起一个本地 HTTP 服务，web_fetch 必须能抓到内容（不是纸面放行）。"""
    body = "<html><body><h1>本地服务自检</h1><p>loopback-ok-7391</p></body></html>"

    class _H(BaseHTTPRequestHandler):
        def do_GET(self):
            raw = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        port = srv.server_address[1]
        res = agent_tools._web_fetch(f"http://127.0.0.1:{port}/")
        text = res.get("text") or ""
        assert "loopback-ok-7391" in text, f"未抓到本地页面内容：{text[:200]}"
        # 内网风格地址也要能通过目标校验（真实连通性由上面的回环用例覆盖）
        assert agent_tools._ssrf_blocked(urlparse(f"http://192.168.1.{port}/")) is False
    finally:
        srv.shutdown()
        srv.server_close()


# ══════════════ 3. 三件套落盘 ══════════════

def test_web_plugin_writes_trio_files(iso):
    """web 型必须一次落盘 SKILL.md（技能）+ server.py（MCP + 本地 HTTP UI）。"""
    ok, err = agent_plugins._write_plugin_files("demo_counter", "web", _web_spec())
    assert ok, err
    d = agent_plugins.plugin_dir("demo_counter")
    assert (d / "SKILL.md").is_file(), "web 型也必须生成 SKILL.md（三件套之一）"
    server = (d / "server.py").read_text(encoding="utf-8")
    assert "ThreadingHTTPServer" in server and "def _selftest" in server
    assert "web_url" in server, "MCP 工具里应含自动注入的 web_url"


def test_web_plugin_missing_parts_returns_reason(iso):
    """缺 web_page / api 时不落盘，且给出可读原因（该原因会回灌给模型自修）。"""
    ok, err = agent_plugins._write_plugin_files("bad_web", "web", _web_spec(web_page=""))
    assert ok is False and "web_page" in err
    ok2, err2 = agent_plugins._write_plugin_files("bad_web2", "web", _web_spec(api=[]))
    assert ok2 is False and "api" in err2
    ok3, err3 = agent_plugins._write_plugin_files("bad_mcp", "mcp", {"name": "bad_mcp"})
    assert ok3 is False and "tools" in err3


# ══════════════ 4. 冒烟自检（真跑） ══════════════

def test_generated_web_plugin_passes_selftest(iso):
    assert agent_plugins._write_plugin_files("demo_counter", "web", _web_spec())[0]
    ok, detail = agent_plugins._verify_plugin("demo_counter", "web")
    assert ok, detail
    assert "端点全部调通" in detail and "MCP 工具" in detail


def test_selftest_rejects_short_page(iso):
    """页面过短（空壳）必须被拦下，否则用户点开浏览器是一片空白。"""
    spec = _web_spec(web_page="<html></html>")
    agent_plugins._write_plugin_files("short_page", "web", spec)
    ok, detail = agent_plugins._verify_plugin("short_page", "web")
    assert ok is False and "页面" in detail


def test_selftest_rejects_broken_endpoint(iso):
    """端点实现抛异常必须被拦下并点名该端点（错误会回灌给模型）。"""
    spec = _web_spec(api=[
        {"name": "state", "description": "读", "method": "GET", "args": [],
         "implementation": "raise RuntimeError('boom')"},
        {"name": "inc", "description": "写", "method": "POST", "args": [],
         "implementation": "return 'ok'"},
    ])
    agent_plugins._write_plugin_files("broken_ep", "web", spec)
    ok, detail = agent_plugins._verify_plugin("broken_ep", "web")
    assert ok is False and "state" in detail, detail


def test_selftest_rejects_syntax_error(iso):
    spec = _web_spec(api=[{"name": "state", "description": "读", "method": "GET",
                           "args": [], "implementation": "return (1"}])
    agent_plugins._write_plugin_files("syntax_bad", "web", spec)
    ok, detail = agent_plugins._verify_plugin("syntax_bad", "web")
    assert ok is False and "语法" in detail, detail


def test_mcp_plugin_passes_stdio_handshake(iso):
    assert agent_plugins._write_plugin_files("demo_mcp", "mcp", _mcp_spec())[0]
    ok, detail = agent_plugins._verify_plugin("demo_mcp", "mcp")
    assert ok and "echo" in detail, detail


def test_verify_installs_deps_only_when_declared(iso, monkeypatch):
    """带 requirements.txt 的插件先装依赖再自检，否则第三方依赖的插件会被误判不合格。"""
    from zhuzhu_Copilot.core import agent_deps
    calls = []
    monkeypatch.setattr(agent_deps, "ensure_dir_deps", lambda d: calls.append(d))
    agent_plugins._write_plugin_files("demo_mcp", "mcp", _mcp_spec())
    assert agent_plugins._verify_plugin("demo_mcp", "mcp")[0]
    assert calls == [], "未声明依赖时不应触发安装（避免无谓的 pip 调用）"
    spec = dict(_mcp_spec("demo_mcp2"), dependencies=["some-declared-dep"])
    agent_plugins._write_plugin_files("demo_mcp2", "mcp", spec)
    assert agent_plugins._verify_plugin("demo_mcp2", "mcp")[0]
    assert len(calls) == 1, "声明依赖时必须先装依赖再自检"


def test_skill_plugin_requires_frontmatter(iso):
    ok, err = agent_plugins._write_plugin_files(
        "demo_skill", "skill", {"name": "demo_skill", "summary": "s",
                                "skill_md": "# 没有 frontmatter\n"})
    assert ok, err
    ok2, detail = agent_plugins._verify_plugin("demo_skill", "skill")
    assert ok2 is False and "frontmatter" in detail


# ══════════════ 5. 生成编排：自检 → 自修 → 登记 ══════════════

def test_create_plugin_registers_only_after_verification(iso, monkeypatch):
    calls = []

    def fake_spec(desc, kind):
        calls.append(desc)
        return _web_spec()

    monkeypatch.setattr(agent_plugins, "_ai_generate_spec", fake_spec)
    ok, msg = agent_plugins.create_plugin_from_nl("做一个计数器", kind="web")
    assert ok, msg
    assert "冒烟自检通过" in msg
    assert "demo_counter-mcp" in _mcp_names(), "自检通过后才登记 MCP"
    assert (iso / "agent" / "skills" / "demo_counter" / "SKILL.md").is_file(), \
        "技能必须一并登记"
    assert len(calls) == 1, "一次通过不应触发修正轮"


def test_create_plugin_repairs_with_error_feedback(iso, monkeypatch):
    """首轮页面空壳 → 自检失败 → 把错误回灌给模型 → 第二轮修正后通过。"""
    seen = []

    def fake_spec(desc, kind):
        seen.append(desc)
        if len(seen) == 1:
            return _web_spec(web_page="<html></html>")
        return _web_spec()

    monkeypatch.setattr(agent_plugins, "_ai_generate_spec", fake_spec)
    ok, msg = agent_plugins.create_plugin_from_nl("做一个计数器", kind="web")
    assert ok and "冒烟自检通过" in msg, msg
    assert len(seen) == 2, "应触发一轮自修"
    assert "未通过自检" in seen[1] and "页面" in seen[1], \
        f"修正轮必须带上自检错误：{seen[1][:200]}"
    assert "demo_counter-mcp" in _mcp_names()
    meta = json.loads((agent_plugins.plugin_dir("demo_counter") / "plugin.json")
                      .read_text(encoding="utf-8"))
    assert meta.get("verified") is True, "自检结论应落盘到 plugin.json"


def test_create_plugin_does_not_register_broken_mcp(iso, monkeypatch):
    """自修后仍不合格：如实报告，且绝不把坏 server 写进 MCP 注册表。"""
    monkeypatch.setattr(agent_plugins, "_ai_generate_spec",
                        lambda desc, kind: _web_spec(web_page="<html></html>"))
    ok, msg = agent_plugins.create_plugin_from_nl("做一个坏的", kind="web")
    assert ok, "插件文件仍应保留（用户可人工查看/修正）"
    assert "未登记 MCP" in msg, msg
    assert _mcp_names() == [], "坏插件不得进 MCP 注册表"
    assert agent_plugins.plugin_dir("demo_counter").is_dir(), "文件仍保留供人工查看"
    meta = json.loads((agent_plugins.plugin_dir("demo_counter") / "plugin.json")
                      .read_text(encoding="utf-8"))
    assert meta.get("verified") is False


def test_create_plugin_missing_web_parts_triggers_repair(iso, monkeypatch):
    """首轮缺 api（AI 常犯）也必须走自修而不是写出空 server.py。"""
    seen = []

    def fake_spec(desc, kind):
        seen.append(desc)
        return _web_spec(api=[]) if len(seen) == 1 else _web_spec()

    monkeypatch.setattr(agent_plugins, "_ai_generate_spec", fake_spec)
    ok, msg = agent_plugins.create_plugin_from_nl("缺端点的插件", kind="web")
    assert ok and "冒烟自检通过" in msg, msg
    assert "api" in seen[1], f"修正轮应被告知缺 api：{seen[1][:200]}"


# ══════════════ 6. 文档/描述守卫（防止约束随版本漂移） ══════════════

def test_tool_and_skill_docs_promise_trio_and_selftest():
    desc = {t["function"]["name"]: t["function"]["description"] for t in agent_tools.TOOLS}
    cp = desc["create_plugin"]
    for kw in ("三件套", "自检", "本地 HTTP", "127.0.0.1"):
        assert kw in cp, f"create_plugin 描述缺少「{kw}」，模型不会按新约束执行"
    assert "127.0.0.1" in desc["web_fetch"], "web_fetch 描述应写明支持本地地址"

    inst = agent_skills._BUILTIN_MD_SKILLS["plugin-create"]["instruction"]
    for kw in ("三件套", "冒烟自检", "state", "web_fetch"):
        assert kw in inst, f"plugin-create 技能缺少「{kw}」"
    import os
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "src", "zhuzhu_Copilot", "skills", "plugin-create", "SKILL.md")
    with open(root, encoding="utf-8") as f:
        md = f.read()
    for kw in ("三件套", "冒烟自检", "state", "web_fetch"):
        assert kw in md, f"随包 plugin-create/SKILL.md 缺少「{kw}」（两份副本必须同步）"


def test_generation_uses_long_llm_timeout(monkeypatch):
    """生成类 LLM 请求必须走长超时档（默认 60s 会把慢 Provider 的正常生成误报为失败）。"""
    from zhuzhu_Copilot.core import agent_llm
    assert agent_llm.GEN_TIMEOUT_S >= 300, "生成类超时档应显著高于默认 60s"
    seen = {}

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        def chat(self, messages, max_tokens=1024, timeout=60.0, stop=None):
            seen["timeout"] = timeout
            seen["max_tokens"] = max_tokens
            return {"text": json.dumps(_web_spec())}

    monkeypatch.setattr(agent_llm, "LLMClient", _FakeClient)
    monkeypatch.setattr(agent_llm, "load_model_config",
                        lambda: {"base_url": "http://x", "api_key": "k", "model": "m"})
    spec = agent_plugins._ai_generate_spec("做一个计数器", "web")
    assert spec.get("api") and spec.get("web_page"), spec
    assert seen["timeout"] == agent_llm.GEN_TIMEOUT_S


def test_web_prompt_requires_skill_md_for_web_kind():
    """web 型生成提示必须索要 skill_md（否则三件套缺 SKILL.md）。"""
    import re
    path = agent_plugins.__file__
    with open(path, encoding="utf-8") as f:
        src = f.read()
    block = src[src.index("if kind == \"web\":"):src.index("else:\n        sys_p = (")]
    assert '"skill_md"' in block, "web 型提示未要求 skill_md"
    assert re.search(r"必须提供 `state`", block), "web 型提示未强制 state 读状态端点"
    assert "全部内联" in block, "web 型提示未约束离线内联（禁 CDN）"
