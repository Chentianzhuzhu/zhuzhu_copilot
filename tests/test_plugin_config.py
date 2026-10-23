"""插件配置与管理回归：config.json 读写、env 注入、MCP 联动、导出插件包。

真实执行的部分（不是纸面断言）：
  · 配置真写盘真读回；非法配置真被拒（键/值/保留环境变量名）；
  · set_plugin_config 真联动更新 mcp_servers.json 里已登记服务器的 env；
  · export_plugin_zip 真打包（含排除项），导出 → 删除 → 再导入真往返；
  · MCP stdio 传输真把 env 合并进子进程环境（Popen 替身捕获实参）。

LLM 评审用注入替身（测试环境不发起真实网络请求）。
"""

import json
import zipfile

import pytest

from zhuzhu_Copilot.core import (agent_mcp, agent_plugins, agent_skills,
                                 plugin_guard)

SAFE_SERVER = ('"""MCP server 桩"""\n'
               'def tool_echo(args):\n    return str(args.get("t", ""))\n')


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    """隔离插件目录与技能配置；LLM 评审替身默认安全"""
    monkeypatch.setattr(agent_plugins, "PLUGINS_DIR", tmp_path / "plugins")
    monkeypatch.setattr(agent_skills, "CONFIG_DIR", tmp_path / "agent")
    monkeypatch.setattr(plugin_guard, "_llm_review",
                        lambda blobs: {"verdict": "safe", "reason": "测试替身：安全"})
    agent_skills._MCP_CACHE["data"] = None
    agent_skills.invalidate_skills_cache()
    return tmp_path


def _mk_mcp_plugin(name, with_config=None):
    """落盘一个带 server.py 的 mcp 型插件并登记 MCP"""
    d = agent_plugins.plugin_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    (d / "server.py").write_text(SAFE_SERVER, encoding="utf-8")
    meta = {"name": name, "kind": "mcp", "enabled": True,
            "description": "t", "author": "测试", "icon": ""}
    agent_plugins._write_meta(name, meta)
    ok, msg = agent_plugins._register_imported_plugin(name)
    assert ok, msg
    if with_config:
        ok, msg = agent_plugins.set_plugin_config(name, with_config)
        assert ok, msg
    return d


# ══════════ 1. config.json 读写与校验 ══════════

def test_config_roundtrip(iso):
    """配置写盘后原样读回（文本/数字/布尔三类标量）"""
    _mk_mcp_plugin("cfg1")
    cfg = {"api_key": "sk-abc", "count": 3, "debug": True}
    ok, msg = agent_plugins.set_plugin_config("cfg1", cfg)
    assert ok, msg
    assert agent_plugins.get_plugin_config("cfg1") == cfg
    assert (agent_plugins.plugin_dir("cfg1") / "config.json").is_file()


def test_get_config_missing_or_corrupt_returns_empty(iso):
    """缺配置/配置损坏都返回 {}，不拖累插件其余功能"""
    _mk_mcp_plugin("cfg2")
    assert agent_plugins.get_plugin_config("cfg2") == {}
    (agent_plugins.plugin_dir("cfg2") / "config.json").write_text(
        "{ 不是 json", encoding="utf-8")
    assert agent_plugins.get_plugin_config("cfg2") == {}
    (agent_plugins.plugin_dir("cfg2") / "config.json").write_text(
        '["列表不是对象"]', encoding="utf-8")
    assert agent_plugins.get_plugin_config("cfg2") == {}


@pytest.mark.parametrize("bad,why", [
    (["不是", "对象"], "JSON 对象"),
    ({"坏 key": "v"}, "不合法"),
    ({"": "v"}, "不合法"),
    ({"path": "/tmp"}, "关键环境变量"),
    ({"HOME": "x"}, "关键环境变量"),
    ({"k": ["列表"]}, "标量"),
    ({"k": {"嵌套": 1}}, "标量"),
    ({"k": None}, "标量"),
    ({"k": "x" * 5000}, "过长"),
])
def test_set_config_rejects_invalid(iso, bad, why):
    _mk_mcp_plugin("cfg3")
    ok, msg = agent_plugins.set_plugin_config("cfg3", bad)
    assert ok is False and why in msg, msg
    assert agent_plugins.get_plugin_config("cfg3") == {}, "非法配置不得落盘"


def test_set_config_rejects_missing_plugin(iso):
    ok, msg = agent_plugins.set_plugin_config("no_such", {"a": 1})
    assert ok is False and "不存在" in msg


# ══════════ 2. env 生成（大写 + 保留键跳过） ══════════

def test_plugin_config_env_uppercases_and_skips_reserved(iso):
    """env 生成：键名大写；手改 config.json 混入保留键时注入阶段仍跳过"""
    _mk_mcp_plugin("cfg4")
    # 直接写盘绕过保存校验（模拟用户手改 config.json）——注入层是第二道防线
    (agent_plugins.plugin_dir("cfg4") / "config.json").write_text(json.dumps(
        {"api_key": "sk-x", "path": "/hack", "flag": False, "n": 7},
        ensure_ascii=False), encoding="utf-8")
    env = agent_plugins.plugin_config_env("cfg4")
    assert env == {"API_KEY": "sk-x", "FLAG": "false", "N": "7"}
    assert "PATH" not in env, "保留环境变量名不得注入"


def test_mcp_config_for_includes_env(iso):
    """登记 MCP 时 stdio 配置带上 env（插件配置进 server 子进程的通道）"""
    _mk_mcp_plugin("cfg5", {"token": "t-1"})
    meta = agent_plugins._read_meta("cfg5")
    cfg = agent_plugins._mcp_config_for(meta)
    assert cfg["type"] == "stdio" and cfg["env"] == {"TOKEN": "t-1"}
    servers = agent_skills.load_mcp_servers()
    entry = [s for s in servers if s.get("name") == cfg["name"]][0]
    assert entry["env"] == {"TOKEN": "t-1"}, "登记表里也要有 env"


# ══════════ 3. 保存配置联动更新已登记 MCP ══════════

def test_set_config_updates_registered_mcp_env(iso):
    """改配置 → mcp_servers.json 对应服务器 env 同步更新（重连后生效）"""
    _mk_mcp_plugin("cfg6")
    servers = agent_skills.load_mcp_servers()
    entry = [s for s in servers if s.get("name") == "cfg6-mcp"][0]
    assert "env" not in entry
    ok, msg = agent_plugins.set_plugin_config("cfg6", {"token": "t-9"})
    assert ok and "重连" in msg
    servers = agent_skills.load_mcp_servers()
    entry = [s for s in servers if s.get("name") == "cfg6-mcp"][0]
    assert entry["env"] == {"TOKEN": "t-9"}
    # 再次改为空 → env 移除
    agent_plugins.set_plugin_config("cfg6", {})
    servers = agent_skills.load_mcp_servers()
    entry = [s for s in servers if s.get("name") == "cfg6-mcp"][0]
    assert "env" not in entry


def test_reenable_plugin_restores_env(iso):
    """停用再启用：MCP 重新登记时 env 不丢"""
    _mk_mcp_plugin("cfg7", {"token": "t-7"})
    assert agent_plugins.set_plugin_enabled("cfg7", False)[0]
    servers = agent_skills.load_mcp_servers()
    assert not any(s.get("name") == "cfg7-mcp" for s in servers)
    assert agent_plugins.set_plugin_enabled("cfg7", True)[0]
    servers = agent_skills.load_mcp_servers()
    entry = [s for s in servers if s.get("name") == "cfg7-mcp"][0]
    assert entry["env"] == {"TOKEN": "t-7"}


# ══════════ 4. 导出插件包 ══════════

def test_export_zip_contents_and_exclusions(iso, tmp_path):
    """导出包含全部插件文件；排除 __pycache__/backups/applied.json"""
    d = _mk_mcp_plugin("exp1", {"token": "t"})
    (d / "icon.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    (d / "__pycache__").mkdir(exist_ok=True)
    (d / "__pycache__" / "server.cpython-313.pyc").write_bytes(b"\x00\x01")
    (d / "backups").mkdir(exist_ok=True)
    (d / "backups" / "server.py.bak").write_text("old", encoding="utf-8")
    (d / "applied.json").write_text('{"files": []}', encoding="utf-8")
    dest = tmp_path / "out" / "exp1.zip"
    ok, msg = agent_plugins.export_plugin_zip("exp1", str(dest))
    assert ok, msg
    with zipfile.ZipFile(dest) as z:
        names = z.namelist()
    assert "exp1/plugin.json" in names
    assert "exp1/server.py" in names
    assert "exp1/config.json" in names, "配置要一起导出（换机即用）"
    assert "exp1/icon.png" in names
    assert not any("__pycache__" in n for n in names)
    assert not any(n.startswith("exp1/backups/") for n in names)
    assert "exp1/applied.json" not in names


def test_export_requires_path_and_plugin(iso, tmp_path):
    _mk_mcp_plugin("exp2")
    ok, _ = agent_plugins.export_plugin_zip("exp2", "")
    assert ok is False
    ok, _ = agent_plugins.export_plugin_zip("no_such", str(tmp_path / "x.zip"))
    assert ok is False


def test_export_then_reimport_roundtrip(iso, tmp_path):
    """导出 → 删除原插件 → 再导入：插件与配置完整回来"""
    _mk_mcp_plugin("exp3", {"token": "t-3"})
    dest = tmp_path / "exp3.zip"
    assert agent_plugins.export_plugin_zip("exp3", str(dest))[0]
    assert agent_plugins.delete_plugin("exp3")[0]
    ok, msg = agent_plugins.import_plugin_zip(str(dest))
    assert ok, msg
    assert agent_plugins.get_plugin_config("exp3") == {"token": "t-3"}
    servers = agent_skills.load_mcp_servers()
    assert any(s.get("name") == "exp3-mcp" and s.get("env") == {"TOKEN": "t-3"}
               for s in servers), "再导入后 env 一并恢复"


# ══════════ 5. MCP stdio env 注入（传输层） ══════════

def test_mcp_client_passes_env_to_stdio_transport(iso, monkeypatch):
    """McpClient 把 config.env 透传给 stdio 传输"""
    seen = {}

    class _FakeTransport:
        def __init__(self, command, args, env=None):
            seen["command"], seen["args"], seen["env"] = command, args, env

        def request(self, method, params):
            return {}

        def close(self):
            pass

    monkeypatch.setattr(agent_mcp, "_StdioTransport", _FakeTransport)
    agent_mcp.McpClient("t", {"type": "stdio", "command": "py",
                              "args": ["s.py"], "env": {"API_KEY": "V"}})
    assert seen["env"] == {"API_KEY": "V"}
    assert seen["command"] == "py" and seen["args"] == ["s.py"]


def test_stdio_transport_merges_env_into_process_env(iso, monkeypatch):
    """_StdioTransport 用「继承当前环境 + 覆盖注入项」起子进程（PATH 不丢）"""
    captured = {}

    class _FakeProc:
        stdout = []

        def __init__(self, *a, **kw):
            captured["env"] = kw.get("env")

    monkeypatch.setattr(agent_mcp.subprocess, "Popen", _FakeProc)
    agent_mcp._StdioTransport("py", ["s.py"], {"API_KEY": "V"})
    env = captured["env"]
    assert env is not None and env.get("API_KEY") == "V"
    assert "PATH" in env, "必须在当前进程环境基础上覆盖，不能丢掉 PATH"


def test_stdio_transport_without_env_keeps_none(iso, monkeypatch):
    """无注入配置时 env=None（Popen 默认继承，行为与旧版一致）"""
    captured = {}

    class _FakeProc:
        stdout = []

        def __init__(self, *a, **kw):
            captured["env"] = kw.get("env")

    monkeypatch.setattr(agent_mcp.subprocess, "Popen", _FakeProc)
    agent_mcp._StdioTransport("py", ["s.py"])
    assert captured["env"] is None
