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

def test_update_shipped_plugin_refreshes_mtime_for_hot_reload(tmp_path):
    """随包插件同步后必须刷新 mtime：MCP 热加载按「脚本指纹(mtime,size)」判断变更。

    copy2 会保留源文件旧 mtime —— 若目标 mtime 仍是旧的，运行中的 server 进程
    永远不被判定为"变了"，用户只能重启应用才能用上新代码（实测就是「改了没生效」）。
    """
    import os
    import time

    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "plugin.json").write_text(
        json.dumps({"name": "demo", "revision": 2}), encoding="utf-8")
    (dst / "plugin.json").write_text(
        json.dumps({"name": "demo", "revision": 1}), encoding="utf-8")
    (src / "server.py").write_text("print('new code')\n", encoding="utf-8")
    (dst / "server.py").write_text("print('old code')\n", encoding="utf-8")
    (dst / "config.json").write_text('{"token": "keep"}', encoding="utf-8")
    old = time.time() - 3600
    os.utime(src / "server.py", (old, old))        # 模拟"源文件是旧 mtime"

    assert agent_plugins._update_shipped_plugin(src, dst) is True
    assert (dst / "server.py").read_text(encoding="utf-8") == "print('new code')\n"
    assert (dst / "server.py").stat().st_mtime > time.time() - 60, "必须刷新为当前时间"
    assert (dst / "config.json").read_text(encoding="utf-8") == '{"token": "keep"}', \
        "config.json 是用户数据，同步时不得覆盖"


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


# ══════════════ 对话内生成：骨架 → 主 Agent 自实现 → 登记热加载 ══════════════
# 用户要求：① 生成插件/技能后无需重启应用；② 生成过程不得「AI 调 AI」。
# 这两条由 create_plugin(骨架) + plugin_register(自检/登记/热加载) 保证。

@pytest.fixture()
def iso_real(tmp_path, monkeypatch):
    """同 iso，但保留真实解释器（自检要真跑 server.py）。"""
    monkeypatch.setattr(agent_plugins, "PLUGINS_DIR", tmp_path / "plugins")
    monkeypatch.setattr(agent_skills, "CONFIG_DIR", tmp_path / "agent")
    agent_skills._MCP_CACHE["data"] = None
    agent_skills.invalidate_skills_cache()
    return tmp_path


def test_scaffold_then_register_without_any_ai(iso_real, monkeypatch):
    """骨架 + 登记全程不得请求大模型（回归：工具内 AI 调 AI 生成插件）。"""
    def _boom(*a, **k):
        raise AssertionError("对话内生成插件不允许在工具内部再调用大模型")
    monkeypatch.setattr(agent_plugins, "_ai_generate_spec", _boom, raising=False)

    ok, msg = agent_plugins.scaffold_plugin("demo_tool", "combined", "演示插件")
    assert ok and "plugin_register" in msg
    d = iso_real / "plugins" / "demo_tool"
    for f in ("plugin.json", "SKILL.md", "server.py", "config.json", "README.md"):
        assert (d / f).is_file(), f"骨架缺少 {f}"

    ok, msg = agent_plugins.register_plugin("demo_tool")
    assert ok, msg
    assert "冒烟自检通过" in msg
    meta = json.loads((d / "plugin.json").read_text(encoding="utf-8"))
    assert meta["verified"] is True
    assert meta["mcp_name"] == "demo_tool-mcp"
    # MCP 已登记、技能已生效（同一进程内立刻可读，不存在「重启才加载」）
    assert any(s.get("name") == "demo_tool-mcp" for s in agent_skills.load_mcp_servers())
    assert "demo_tool" in [s.get("name") for s in agent_skills.load_skills()]


def test_plugin_edit_reconnects_without_restart(iso_real):
    """改写 server.py 后再次热加载必须重连并暴露新工具（而非继续跑旧代码）。"""
    import time

    from zhuzhu_Copilot.core.agent_mcp import McpManager

    assert agent_plugins.scaffold_plugin("hot_demo", "mcp", "热加载演示")[0]
    assert agent_plugins.register_plugin("hot_demo")[0]
    d = iso_real / "plugins" / "hot_demo"
    mgr = McpManager()
    try:
        tools = mgr.reload(agent_skills.load_mcp_servers())
        names = [t["function"]["name"] for t in tools]
        assert "run" in names, names

        # 模拟主 Agent 改代码：换一个工具名
        src = (d / "server.py").read_text(encoding="utf-8")
        (d / "server.py").write_text(src.replace('"name": "run"', '"name": "run_v2"'),
                                     encoding="utf-8")
        time.sleep(0.02)
        tools2 = mgr.reload(agent_skills.load_mcp_servers())
        names2 = [t["function"]["name"] for t in tools2]
        assert "run_v2" in names2, f"脚本改写后未热重连（仍只有 {names2}）"
    finally:
        mgr.close_all()


def test_tool_schema_and_dispatch_use_scaffold(iso):
    """工具表与分发必须走「骨架」语义：create_plugin 需要 name，另提供 plugin_register。"""
    from zhuzhu_Copilot.core import agent_tools

    schemas = {t["function"]["name"]: t for t in agent_tools.tool_schemas()}
    assert "plugin_register" in schemas, "缺少 plugin_register（登记 + 热加载入口）"
    assert "name" in schemas["create_plugin"]["function"]["parameters"]["required"]
    assert "AI 生成" not in schemas["create_plugin"]["function"]["description"]

    res = agent_tools.execute_tool(
        "create_plugin",
        {"name": "dispatch_demo", "kind": "skill", "description": "分发验证"},
        allow_dangerous=True)
    assert "骨架" in res.get("text", ""), res
    assert (iso / "plugins" / "dispatch_demo" / "SKILL.md").is_file()
