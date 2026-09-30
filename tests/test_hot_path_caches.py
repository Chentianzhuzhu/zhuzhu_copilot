# -*- coding: utf-8 -*-
"""热路径缓存的契约测试（长任务每轮热点：工作流解析 / 元数据 / 子 Agent 注册表 / 数据目录）。

背景（实测，scripts/_probe_longtask_perf.py）：引擎每轮会多次解析「激活工作流 /
是否存在 / 元数据 / 核心模块 / 子 Agent 注册表」，每次都 stat 一遍（Windows 杀软下
单次 ≈0.5ms）+ 深拷贝设置，实测吃掉长任务每轮预算的一大块；优化为「短 TTL 的指纹/
解析缓存 + 写入侧显式失效」。

断言口径（与机器速度无关的确定性契约）：
  1. 应用内改动（switch / 启用停用 / 创建删除 / 写元数据 / 注册注销）**立即生效**，
     不依赖 TTL 过期；
  2. 数据目录缓存按家目录隔离（测试切换家目录不串味），显式失效后立刻重算；
  3. 免拷贝的 settings 视图仍读到最新设置，且 load_settings 返回值改动不污染缓存。
"""
import pytest

from zhuzhu_Copilot import app_identity                       # noqa: E402
from zhuzhu_Copilot.core import agent_skills                  # noqa: E402
from zhuzhu_Copilot.core import agent_subagent                # noqa: E402
from zhuzhu_Copilot.core import agent_workflow as wf          # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_caches():
    """每个用例前后清空工作流解析/指纹缓存与数据目录缓存，避免用例互相串味。

    家目录统一由 conftest 的会话级隔离提供，本夹具**不改家目录**：workflows_root 的
    「进程内只初始化一次」标记（_ROOT_READY）与家目录绑定，逐用例换家目录会让默认
    工作流目录不存在于新家目录下（误报「工作流不存在: _default」）。"""
    app_identity.invalidate_data_root_cache()
    wf.invalidate_workflow_caches()
    wf.set_current_workflow("")
    yield
    wf.set_current_workflow("")
    wf.invalidate_workflow_caches()
    app_identity.invalidate_data_root_cache()


def _make(name: str, desc: str = "") -> None:
    ok, msg = wf.create_workflow(name, desc)
    assert ok, msg


def test_set_active_visible_immediately():
    """set_active 后 active_workflow() 立即返回新值（不依赖 TTL 过期）"""
    _make("t_alpha")
    assert wf.active_workflow() == wf.DEFAULT_WORKFLOW
    ok, msg = wf.set_active("t_alpha")
    assert ok, msg
    assert wf.active_workflow() == "t_alpha", "切换激活工作流必须立即生效"
    ok, msg = wf.set_active(wf.DEFAULT_WORKFLOW)
    assert ok, msg
    assert wf.active_workflow() == wf.DEFAULT_WORKFLOW


def test_create_delete_visible_immediately():
    """新建/删除工作流后 is_workflow() 立即反映（存在性缓存随之刷新）"""
    assert not wf.is_workflow("t_beta")
    _make("t_beta")
    assert wf.is_workflow("t_beta"), "新建后必须立即可见"
    ok, msg = wf.delete_workflow("t_beta")
    assert ok, msg
    assert not wf.is_workflow("t_beta"), "删除后必须立即不可见（回退默认）"


def test_enable_toggle_visible_immediately():
    """启停工作流后 is_workflow() 立即反映"""
    _make("t_gamma")
    assert wf.is_workflow("t_gamma")
    ok, msg = wf.set_enabled("t_gamma", False)
    assert ok, msg
    assert not wf.is_workflow("t_gamma"), "禁用后必须立即可见（视为不存在）"
    ok, msg = wf.set_enabled("t_gamma", True)
    assert ok, msg
    assert wf.is_workflow("t_gamma")


def test_write_meta_visible_immediately():
    """_write_meta 后同一秒内读到的就是新内容（写入侧显式刷新指纹/解析缓存）"""
    _make("t_delta")
    wf._write_meta("t_delta", {"name": "t_delta", "description": "第一版"})
    assert wf._read_meta("t_delta").get("description") == "第一版"
    wf._write_meta("t_delta", {"name": "t_delta", "description": "第二版"})
    assert wf._read_meta("t_delta").get("description") == "第二版", "改写后必须立即读到新内容"


def test_subagent_registry_visible_immediately():
    """注册/注销子 Agent 后 registered_subagents() 立即反映（注册表指纹同刷）"""
    _make("t_sub")
    ok, msg = agent_subagent.register_subagent("t_helper", "", "做点小事",
                                               workflow="t_sub")
    assert ok, msg
    names = [x["name"] for x in agent_subagent.registered_subagents("t_sub")]
    assert "t_helper" in names, "注册后必须立即可见（工具列表按它组装）"
    ok, msg = agent_subagent.unregister_subagent("t_helper", workflow="t_sub")
    assert ok, msg
    names = [x["name"] for x in agent_subagent.registered_subagents("t_sub")]
    assert "t_helper" not in names, "注销后必须立即消失"


def test_data_root_cache_is_per_home(tmp_path, monkeypatch):
    """数据目录缓存按家目录隔离：切换家目录必须重算（测试/多用户场景不串味）"""
    h1, h2 = tmp_path / "h1", tmp_path / "h2"
    (h1 / app_identity.DATA_DIR_NAME).mkdir(parents=True)
    (h2 / app_identity.DATA_DIR_NAME).mkdir(parents=True)
    monkeypatch.setattr(app_identity, "_home", lambda: h1)
    assert app_identity.data_root() == h1 / app_identity.DATA_DIR_NAME
    monkeypatch.setattr(app_identity, "_home", lambda: h2)
    assert app_identity.data_root() == h2 / app_identity.DATA_DIR_NAME, \
        "切换家目录后不得返回上一个家目录的缓存"


def test_data_root_invalidate_recomputes(tmp_path, monkeypatch):
    """invalidate_data_root_cache 后立刻重算（迁移/改名后调用，避免沿用旧解析）"""
    legacy = tmp_path / app_identity.LEGACY_DATA_DIR_NAME
    legacy.mkdir(parents=True)
    monkeypatch.setattr(app_identity, "_home", lambda: tmp_path)
    assert app_identity.data_root() == legacy, "新目录不存在时应回退旧目录"
    (tmp_path / app_identity.DATA_DIR_NAME).mkdir(parents=True)
    app_identity.invalidate_data_root_cache()
    assert app_identity.data_root() == tmp_path / app_identity.DATA_DIR_NAME, \
        "失效后必须按当前磁盘状态重算（新目录优先）"


def test_cap_enabled_reads_latest_without_polluting_cache():
    """能力开关走免拷贝只读视图：保存后立即生效，且 load_settings 返回值改动不污染缓存"""
    original = agent_skills.load_settings()
    try:
        agent_skills.save_settings({"cap_mcp": "0"})
        assert agent_skills.cap_enabled("mcp") is False, "保存后必须立即生效"
        s = agent_skills.load_settings()
        s["cap_mcp"] = "1"                       # 改深拷贝：不得影响缓存
        assert agent_skills.cap_enabled("mcp") is False, \
            "load_settings 返回的是深拷贝：改动它不得污染设置缓存"
        agent_skills.save_settings({"cap_mcp": "1"})
        assert agent_skills.cap_enabled("mcp") is True
    finally:
        agent_skills.save_settings(original)