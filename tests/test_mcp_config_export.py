"""MCP 配置导出：标准 mcpServers JSON 的结构、停用过滤与密钥脱敏。"""
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from zhuzhu_Copilot.core import agent_skills


def _servers() -> list:
    return [
        {"name": "excel", "type": "stdio", "command": "python", "args": ["-m", "x"],
         "env": {"API_KEY": "sk-real", "PATH_EXTRA": "/opt/bin"}},
        {"name": "remote", "type": "sse", "url": "http://localhost:9000/sse"},
        {"name": "off", "type": "stdio", "command": "python", "enabled": False},
    ]


def test_export_masks_secret_env_by_default():
    data = json.loads(agent_skills.mcp_servers_json(_servers()))
    excel = data["mcpServers"]["excel"]
    assert excel["command"] == "python"
    assert excel["args"] == ["-m", "x"]
    assert excel["env"]["PATH_EXTRA"] == "/opt/bin"
    assert excel["env"]["API_KEY"] == agent_skills._MCP_SECRET_PLACEHOLDER


def test_export_can_include_plain_secrets():
    data = json.loads(agent_skills.mcp_servers_json(_servers(), include_secrets=True))
    assert data["mcpServers"]["excel"]["env"]["API_KEY"] == "sk-real"


def test_export_sse_entry_and_disabled_filtered():
    data = json.loads(agent_skills.mcp_servers_json(_servers()))
    assert data["mcpServers"]["remote"] == {"url": "http://localhost:9000/sse",
                                            "type": "sse"}
    assert "off" not in data["mcpServers"], "停用的服务器不导出"


def test_export_empty_when_no_enabled_server():
    data = json.loads(agent_skills.mcp_servers_json([{"name": "x", "enabled": False}]))
    assert data == {"mcpServers": {}}
