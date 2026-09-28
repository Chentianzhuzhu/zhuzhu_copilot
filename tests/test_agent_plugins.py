"""agent_plugins 测试：市场插件导入（远程 SSE MCP + 本地技能）、启用/停用/删除联动。

配置目录全部隔离到 tmp_path，不触碰真实用户配置。"""
import json
import zipfile

import pytest

from zhuzhu_Copilot.core import agent_plugins, agent_runtime, agent_skills


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    """隔离插件目录 / 技能目录 / MCP 配置，固定解释器"""
    monkeypatch.setattr(agent_plugins, "PLUGINS_DIR", tmp_path / "plugins")
    monkeypatch.setattr(agent_skills, "CONFIG_DIR", tmp_path / "agent")
    monkeypatch.setattr(agent_runtime, "python_interpreter", lambda: "python-test")
    agent_skills._MCP_CACHE["data"] = None
    agent_skills.invalidate_skills_cache()
    return tmp_path


def _mcp_servers() -> list:
    return agent_skills.load_mcp_servers()


def _skills_dir(tmp_path):
    return tmp_path / "agent" / "skills"


def _make_zip(path, files: dict):
    with zipfile.ZipFile(path, "w") as z:
        for name, content in files.items():
            z.writestr(name, content)


SSE_PLUGIN_JSON = {
    "name": "marketdemo",
    "description": "远程 MCP + 本地技能市场插件",
    "kind": "combined",
    "enabled": True,
    "mcp_type": "sse",
    "mcp_url": "https://mcp.example.com/sse",
}

SKILL_MD = """---
name: marketdemo
description: 市场技能示例
---

# marketdemo

当用户要求"市场示例"时使用本技能。

## 流程
1. 调用插件 MCP 工具完成操作。
2. 向用户汇报结果。

## 约束
禁止编造结果。
"""

STDIO_PLUGIN_JSON = {
    "name": "localplugin",
    "description": "本地 stdio 插件",
    "kind": "combined",
    "enabled": True,
}


# ---------- _mcp_config_for ----------

def test_mcp_config_sse(iso):
    meta = {"name": "p", "mcp_type": "sse", "mcp_url": "https://x.com/sse"}
    cfg = agent_plugins._mcp_config_for(meta)
    assert cfg == {"name": "p-mcp", "type": "sse", "url": "https://x.com/sse"}


def test_mcp_config_stdio_default(iso):
    (iso / "plugins" / "p").mkdir(parents=True)
    (iso / "plugins" / "p" / "server.py").write_text("")
    meta = {"name": "p"}
    cfg = agent_plugins._mcp_config_for(meta)
    assert cfg["type"] == "stdio"
    assert cfg["command"] == "python-test"
    assert cfg["args"] == [str(iso / "plugins" / "p" / "server.py")]


def test_mcp_config_stdio_custom_command(iso):
    (iso / "plugins" / "p").mkdir(parents=True)
    meta = {"name": "p", "mcp_type": "stdio",
            "mcp_command": "node", "mcp_args": ["server.js"]}
    cfg = agent_plugins._mcp_config_for(meta)
    assert cfg == {"name": "p-mcp", "type": "stdio", "command": "node", "args": ["server.js"]}


def test_mcp_config_sse_missing_url(iso):
    # 声明 sse 但缺 url 且无 server.py → 返回 None（不可登记）
    meta = {"name": "p", "mcp_type": "sse"}
    assert agent_plugins._mcp_config_for(meta) is None


# ---------- 市场插件 zip 导入 ----------

def test_import_zip_sse_registers_skill_and_mcp(iso):
    pkg = iso / "market.zip"
    _make_zip(pkg, {
        "plugin.json": json.dumps(SSE_PLUGIN_JSON, ensure_ascii=False),
        "SKILL.md": SKILL_MD,
    })
    ok, msg = agent_plugins.import_plugin_zip(str(pkg))
    assert ok
    assert "技能 /marketdemo 已登记" in msg
    assert "MCP marketdemo-mcp（sse）已登记" in msg
    # 插件目录落盘
    assert (iso / "plugins" / "marketdemo" / "plugin.json").is_file()
    # 技能已登记到技能目录
    assert (_skills_dir(iso) / "marketdemo" / "SKILL.md").is_file()
    # MCP 已写入 mcp_servers.json 且为远程 sse
    servers = _mcp_servers()
    assert any(s.get("name") == "marketdemo-mcp" and s.get("type") == "sse"
               and s.get("url") == "https://mcp.example.com/sse" for s in servers)


def test_import_zip_stdio_registers_mcp(iso):
    local_skill = SKILL_MD.replace("marketdemo", "localplugin").replace("市场示例", "本地示例")
    pkg = iso / "local.zip"
    _make_zip(pkg, {
        "plugin.json": json.dumps(STDIO_PLUGIN_JSON, ensure_ascii=False),
        "SKILL.md": local_skill,
        "server.py": "print('mock server')",
    })
    ok, msg = agent_plugins.import_plugin_zip(str(pkg))
    assert ok
    assert "MCP localplugin-mcp（stdio）已登记" in msg
    servers = _mcp_servers()
    assert any(s.get("name") == "localplugin-mcp" and s.get("type") == "stdio"
               and s.get("command") == "python-test"
               and s.get("args") == [str(iso / "plugins" / "localplugin" / "server.py")]
               for s in servers)
    assert (_skills_dir(iso) / "localplugin" / "SKILL.md").is_file()


def test_import_zip_missing_plugin_json(iso):
    pkg = iso / "bad.zip"
    _make_zip(pkg, {"SKILL.md": SKILL_MD})
    ok, _ = agent_plugins.import_plugin_zip(str(pkg))
    assert not ok


def test_import_zip_duplicate_rejected(iso):
    pkg = iso / "dup.zip"
    _make_zip(pkg, {"plugin.json": json.dumps(SSE_PLUGIN_JSON, ensure_ascii=False),
                    "SKILL.md": SKILL_MD})
    assert agent_plugins.import_plugin_zip(str(pkg))[0]
    ok, msg = agent_plugins.import_plugin_zip(str(pkg))
    assert not ok and "已存在" in msg


def test_import_zip_path_traversal_rejected(iso):
    pkg = iso / "evil.zip"
    _make_zip(pkg, {"../evil.py": "print('pwn')",
                    "plugin.json": json.dumps(STDIO_PLUGIN_JSON, ensure_ascii=False)})
    ok, msg = agent_plugins.import_plugin_zip(str(pkg))
    assert not ok and "非法路径" in msg


# ---------- 启用/停用/删除联动 ----------

def test_toggle_enabled_removes_and_restores_mcp(iso):
    pkg = iso / "t.zip"
    _make_zip(pkg, {"plugin.json": json.dumps(SSE_PLUGIN_JSON, ensure_ascii=False),
                    "SKILL.md": SKILL_MD})
    assert agent_plugins.import_plugin_zip(str(pkg))[0]
    assert any(s.get("name") == "marketdemo-mcp" for s in _mcp_servers())
    # 停用 → 移除 MCP 登记
    ok, _ = agent_plugins.set_plugin_enabled("marketdemo", False)
    assert ok
    assert not any(s.get("name") == "marketdemo-mcp" for s in _mcp_servers())
    # 启用 → 恢复登记
    ok, _ = agent_plugins.set_plugin_enabled("marketdemo", True)
    assert ok
    assert any(s.get("name") == "marketdemo-mcp" for s in _mcp_servers())


def test_delete_plugin_unregisters_mcp_and_skill(iso):
    pkg = iso / "d.zip"
    _make_zip(pkg, {"plugin.json": json.dumps(SSE_PLUGIN_JSON, ensure_ascii=False),
                    "SKILL.md": SKILL_MD})
    assert agent_plugins.import_plugin_zip(str(pkg))[0]
    ok, _ = agent_plugins.delete_plugin("marketdemo")
    assert ok
    assert not (iso / "plugins" / "marketdemo").exists()
    assert not (_skills_dir(iso) / "marketdemo").exists()
    assert not any(s.get("name") == "marketdemo-mcp" for s in _mcp_servers())


def test_import_plugin_skill_wraps_into_plugin(iso, tmp_path):
    """标准技能导入为 skill 型插件：技能先导入技能目录，再包装为插件"""
    md = tmp_path / "weird_skill.md"
    md.write_text(SKILL_MD.replace("marketdemo", "weird_skill"), encoding="utf-8")
    ok, msg = agent_plugins.import_plugin_skill(str(md))
    assert ok
    assert "skill 型" in msg
    assert (iso / "plugins" / "weird_skill" / "plugin.json").is_file()
    assert (iso / "plugins" / "weird_skill" / "SKILL.md").is_file()
