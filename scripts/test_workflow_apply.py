"""工作流激活生效 + 生成反馈回归验证（临时根目录，不污染真实 ~/.zhuzhu_Copilot）"""
import sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from zhuzhu_Copilot.core import agent_workflow as aw

TMP = Path(tempfile.mkdtemp())
aw.workflows_root = lambda: TMP  # 重定向根目录，避免改动真实环境


def _reset():
    import shutil
    for p in TMP.iterdir():
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
    aw._WF_LOCAL.wf = ""


def _ensure():
    """模拟真实 workflows_root() 的初始化：创建 _default 目录"""
    d = TMP / "_default"
    d.mkdir(parents=True, exist_ok=True)
    (d / "README.md").write_text("default", encoding="utf-8")


def test_activation_default():
    """问题2：激活默认工作流 _default 应成功且 active_workflow() 返回 _default"""
    _reset()
    _ensure()
    ok, msg = aw.set_active("_default")
    assert ok, f"set_active(_default) 失败: {msg}"
    assert aw.active_workflow() == "_default", \
        f"active_workflow()={aw.active_workflow()!r} 期望 _default"
    # 会话线程局部不应影响主线程全局激活
    aw.set_current_workflow("_default")
    assert aw.active_workflow() == "_default"
    aw.set_current_workflow("")
    print(f"[OK] test_activation_default: {msg}")


def test_generation_feedback():
    """问题1：create_workflow_from_nl 应调用 on_status 回调且成功创建目录"""
    _reset()
    _ensure()

    def fake_gen(desc, want):
        return {"name": "mywf", "description": "测试工作流",
                "files": {"tools.py": "TOOLS = []\ndef execute_tool(*a, **k): return {'text': 'ok'}"}}
    aw._ai_generate_workflow = fake_gen
    statuses = []
    ok, msg = aw.create_workflow_from_nl(
        "帮我创建一个测试工作流", on_status=lambda s: statuses.append(s))
    assert ok, f"创建失败: {msg}"
    assert statuses, "on_status 未被调用（工作流生成无及时反馈）"
    assert aw.workflow_dir("mywf").joinpath("tools.py").is_file()
    print(f"[OK] test_generation_feedback: statuses={statuses} | {msg}")


def test_llm_tools_resolve_active():
    """问题2：apply_tools/load_llm_client/agent_hooks 应解析到指定工作流目录"""
    _reset()
    _ensure()
    aw.set_active("_default")
    assert aw.apply_tools() == (0, aw.DEFAULT_WORKFLOW)
    hooks = aw.agent_hooks()
    assert hooks["name"] == "_default"
    print("[OK] test_llm_tools_resolve_active: 工具/钩子回退默认工作流")


if __name__ == "__main__":
    test_activation_default()
    test_generation_feedback()
    test_llm_tools_resolve_active()
    print("\n全部通过")