# -*- coding: utf-8 -*-
"""运行环境策略（用户要求）：

- 应用内置 Python / Node 运行时 = **内置 MCP / 工具 / 插件 server 的依赖运行环境**
  （调用内置能力不依赖用户系统环境），同时也是 **python / node 脚本的默认执行环境**：
  简单脚本、以及依赖已满足的脚本（如 blender/bpy、librosa）都直接在它里面跑；
- 显式路径（项目自带 venv、系统工具链）与无关的系统命令走用户本机环境。

守护四件事：
  A. 系统提示词对所有 agent（含自定义人格这类「整体替换」分支）都带运行环境约定；
  B. run_command 按命令选择内置运行时并注入子进程 env（只改子进程 PATH，不改系统 PATH）；
  C. 内置运行时不可用时回退本机环境，并在结果里如实说明；
  D. 打包脚本会把内置 MCP/插件的 requirements.txt 装进随包运行时。
"""
import importlib.util
import inspect
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zhuzhu_Copilot.core import (agent_engine, agent_runtime, agent_skills,   # noqa: E402
                                 agent_tools)


class _StubLLM:
    model = "stub"
    fell_back = False
    silent_fallback = False


def test_runtime_notice_states_bundled_runtime_rule():
    """约定文本必须点名：内置运行时、脚本环境、python/node/pip、以及本机环境的边界。"""
    text = agent_skills.runtime_env_notice()
    assert text.strip(), "运行环境约定不能为空"
    for kw in ("内置运行时", "本机", "python", "node", "pip", "MCP"):
        assert kw in text, f"约定缺少「{kw}」：{text}"


def test_system_prompt_carries_runtime_notice_for_all_agents():
    """自定义人格（整体替换系统提示）与默认人设都必须带上该约定。"""
    eng = agent_engine.AgentEngine(_StubLLM(), text_only=True)
    notice = agent_skills.runtime_env_notice().strip()
    assert notice

    eng.persona = "你是测试人格，只负责验证提示词拼接。"
    assert notice in eng._system_prompt(""), "自定义人格分支漏了运行环境约定"

    eng.persona = None
    assert notice in eng._system_prompt(""), "默认分支漏了运行环境约定"


def test_command_env_picks_bundled_runtime_only_for_script_commands():
    """python/node 命令 → 内置运行时前置；系统命令 / 显式路径 → 不接管（本机环境）。"""
    env, note = agent_runtime.command_env("python script.py")
    if agent_runtime.is_installed("python"):
        assert env is not None and note == ""
        assert env["PATH"].split(os.pathsep)[0] == str(agent_runtime.python_bin())
    else:
        assert env is None and "不可用" in note

    # 与 python/node 无关的系统命令：不注入
    assert agent_runtime.command_env("dir C:\\") == (None, "")
    # 显式解释器路径（项目自带 venv / 系统工具链）：不命中内置运行时
    assert agent_runtime.command_env(r".venv\Scripts\python.exe app.py") == (None, "")
    assert agent_runtime.command_env(r"C:\Tools\node.exe index.js") == (None, "")


def test_command_env_falls_back_when_runtime_unavailable(monkeypatch):
    """运行时确实不可用：不阻断命令，回退本机环境并给出明确提示。"""
    monkeypatch.setattr(agent_runtime, "is_installed", lambda name: False)
    monkeypatch.setattr(agent_runtime, "ensure_runtime", lambda name, cb=None: (False, "缺失"))
    env, note = agent_runtime.command_env("python x.py")
    assert env is None and "回退本机环境" in note


def test_run_command_injects_runtime_without_touching_os_environ():
    """行为侧：`python -c` 命中内置运行时；进程环境（PATH 等）不被修改。"""
    path_before = os.environ.get("PATH")
    npm_before = os.environ.get("npm_config_cache")
    res = agent_tools._run_command(
        'python -c "import sys; print(sys.executable)"', wait=30)
    assert res.get("text"), res
    assert os.environ.get("PATH") == path_before, "不得修改进程 PATH（只应改子进程 env）"
    assert os.environ.get("npm_config_cache") == npm_before, "不得把沙盒变量写进进程环境"
    if agent_runtime.is_installed("python"):
        assert "runtime" in (res.get("text") or "").replace("/", "\\").lower(), res


def test_run_command_surfaces_runtime_fallback_note(monkeypatch):
    """运行时不可用时，命令照常执行，但结果里必须注明已回退本机环境。"""
    monkeypatch.setattr(agent_runtime, "is_installed", lambda name: False)
    monkeypatch.setattr(agent_runtime, "ensure_runtime", lambda name, cb=None: (False, "缺失"))
    res = agent_tools._run_command(
        'python -c "print(12345)"', wait=30)
    text = res.get("text") or ""
    assert "12345" in text, res
    assert "回退本机环境" in text, text


def test_run_command_uses_runtime_env_helpers():
    """实现侧：run_command 必须走 command_env（按命令选运行时），而不是无条件本机环境。"""
    src = inspect.getsource(agent_tools._run_command)
    assert "command_env" in src, "run_command 未按命令选择内置运行时"


def test_prepare_bundle_installs_plugin_requirements():
    """打包脚本必须收集插件依赖清单并装进随包运行时（否则装机后插件缺依赖）。"""
    repo = Path(__file__).resolve().parents[1]
    path = repo / "scripts" / "prepare_runtime_bundle.py"
    spec = importlib.util.spec_from_file_location("prepare_runtime_bundle", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    reqs = [str(p).replace("\\", "/") for p in mod._plugin_requirements()]
    assert any(p.endswith("plugins/audio-analyzer/requirements.txt") for p in reqs), reqs
    assert callable(getattr(mod, "_install_plugin_deps", None))
