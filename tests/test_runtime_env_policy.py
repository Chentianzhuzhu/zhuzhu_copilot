# -*- coding: utf-8 -*-
"""运行环境策略（用户要求）：

- 工作任务（装依赖 / 跑脚本 / 构建 / 测试）必须用**本机** Python / Node；
- 应用内置运行时仅用于「以工具身份运行」的进程（MCP server、插件 server.py 等）。

守护三件事：
  A. 系统提示词对所有 agent（含自定义人格/工作流提示词这类「整体替换」分支）都带运行环境约定；
  B. run_command 不再把内置运行时注入子进程环境（否则工作任务会落到应用运行时里）；
  C. 打包脚本会把内置 MCP/插件的 requirements.txt 装进随包运行时。
"""
import importlib.util
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zhuzhu_Copilot.core import agent_engine, agent_skills, agent_tools  # noqa: E402


class _StubLLM:
    model = "stub"
    fell_back = False
    silent_fallback = False


def test_runtime_notice_states_host_env_rule():
    """约定文本必须同时点名「本机环境」与「内置运行时只在工具侧用」。"""
    text = agent_skills.runtime_env_notice()
    assert text.strip(), "运行环境约定不能为空"
    for kw in ("本机", "内置运行时", "python", "node", "pip"):
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


def test_run_command_does_not_inject_bundled_runtime():
    """run_command 的实现里不得再出现运行时注入（sandbox_env / ensure_runtime）。"""
    src = inspect.getsource(agent_tools._run_command)
    assert "sandbox_env" not in src, "run_command 仍在注入内置运行时环境"
    assert "ensure_runtime" not in src, "run_command 仍会按命令部署/使用内置运行时"
    # 行为侧：普通命令能真实跑通（继承本机环境）
    res = agent_tools._run_command("echo runtime-policy-probe", wait=20)
    assert "runtime-policy-probe" in (res.get("text") or ""), res


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
