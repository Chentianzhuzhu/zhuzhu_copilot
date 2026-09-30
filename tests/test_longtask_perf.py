"""长任务性能优化回归：引擎每轮热点的缓存必须「快而不失真」。

背景（真实探针 scripts/_probe_longtask_perf.py，120 轮循环开销）：优化前 409 ms/轮
（120 轮 49.1s）→ 优化后 14.5 ms/轮（1.74s）。本文件不测耗时（避免抖动误报），
只守护各热点的**正确性契约**：缓存命中/失效、语义等价、调用方修改不污染缓存 ——
防止为提速引入陈旧数据或工具/schema 泄漏。

热点清单（探针实测）：
  _all_tools 243ms/轮  ← skills_covering_tools(246ms) + tool_schemas(深拷贝) + 子 Agent 注册表读盘
  _sync_skill_msg 57ms/轮 ← load_skills 每次重算目录指纹（_md_key 48ms）
  _system_prompt 8.5ms/轮 ← agent_hooks 每轮重新 exec 工作流 agent.py
  _sync_todo_msg 1.6ms/轮 ← 每轮读 todos.json
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from zhuzhu_Copilot.core import agent_llm
from zhuzhu_Copilot.core import agent_skills as sk
from zhuzhu_Copilot.core import agent_tools as at
from zhuzhu_Copilot.core import agent_workflow as aw

# ---------- token 估算：C 层正则计数必须与逐字符语义完全一致 ----------

def _reference_estimate(text: str) -> int:
    """优化前的实现（逐字符判断 CJK），作为等价性基准。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return int(cjk + (len(text) - cjk) / 4) + 4


def test_estimate_tokens_equivalent_to_charwise_reference():
    cases = ["", "abc", "中文内容", "混合 mixed 中英 123", "　全角空格　",
             "emoji 🙂 与中文", "\u4e00" * 100 + "x" * 100, "line1\nline2\tend"]
    for t in cases:
        assert agent_llm.estimate_tokens(t) == _reference_estimate(t), repr(t[:20])


# ---------- 技能覆盖查询：词边界语义等价 + 缓存失效 ----------

def _stub_skills(monkeypatch, skills):
    monkeypatch.setattr(sk, "load_skills", lambda *a, **k: skills)
    sk._invalidate_skills_cache()


def test_skills_covering_tools_keeps_word_boundary_semantics(monkeypatch, tmp_path):
    _stub_skills(monkeypatch, [
        {"name": "s1", "instruction": "先用 read_file 读取，再调用 run_command 执行。"},
        {"name": "s2", "instruction": "读取文件用 read_file；中文相邻read_file也行。"},
        {"name": "s3", "instruction": "这里的 xread_file 与 read_fileX 都只是说明文字。"},
        {"name": "mcp", "instruction": "调用 my-server_read_file 处理。"},
    ])
    got = sk.skills_covering_tools(["read_file", "run_command", "my-server_read_file"])
    assert got["read_file"] == ["s1", "s2"], got          # 词边界命中，两个技能都记
    assert got["run_command"] == ["s1"]
    assert "s3" not in got.get("read_file", []), "子串（xread_file/read_fileX）不得误匹配"
    # 含非词字符的 MCP 工具名走原正则兜底，仍能命中
    assert got["my-server_read_file"] == ["mcp"], got
    sk._invalidate_skills_cache()


def test_skills_covering_tools_cache_hit_and_invalidation(monkeypatch):
    _stub_skills(monkeypatch, [{"name": "a", "instruction": "调用 read_file"}])
    first = sk.skills_covering_tools(["read_file"])
    assert first == {"read_file": ["a"]}

    # 缓存命中：底层技能内容变了但未失效 → 结果不变（同一任务内工具集稳定）
    monkeypatch.setattr(sk, "load_skills", lambda *a, **k: [])
    assert sk.skills_covering_tools(["read_file"]) == first

    # 显式失效（技能增删/启停）→ 立即重算，不返回陈旧结果
    sk._invalidate_skills_cache()
    assert sk.skills_covering_tools(["read_file"]) == {}
    sk._invalidate_skills_cache()


def test_skills_covering_tools_empty_names():
    assert sk.skills_covering_tools([]) == {}
    assert sk.skills_covering_tools(None) == {}


# ---------- skills 目录指纹：缓存 TTL + 显式失效 ----------

def test_md_key_cache_respects_invalidation(tmp_path):
    d = tmp_path / "skills"
    (d / "alpha").mkdir(parents=True)
    (d / "alpha" / "SKILL.md").write_text("A", encoding="utf-8")
    k1 = sk._md_key(d)

    (d / "beta").mkdir()
    (d / "beta" / "SKILL.md").write_text("B", encoding="utf-8")
    assert sk._md_key(d) == k1, "TTL 内应命中缓存（避免每轮扫盘）"

    sk._invalidate_skills_cache()          # 导入/删除/启停技能后必须立即重扫
    k2 = sk._md_key(d)
    assert k2 != k1 and "beta" in k2, (k1, k2)


# ---------- 工作流核心模块：按 mtime 缓存但热更新仍即时生效 ----------

def test_load_module_caches_but_hot_reloads(monkeypatch, tmp_path):
    monkeypatch.setattr(aw, "workflow_dir", lambda name: tmp_path / name)
    monkeypatch.setattr(aw, "_ROOT_READY", True)
    d = tmp_path / "w1"
    d.mkdir()
    f = d / "agent.py"
    f.write_text("VALUE = 1\n", encoding="utf-8")

    m1 = aw._load_module("w1", "agent.py")
    assert m1 is not None and m1.VALUE == 1
    assert aw._load_module("w1", "agent.py") is m1, "内容未变时应复用同一模块对象（不重复 exec）"

    f.write_text("VALUE = 2\n", encoding="utf-8")
    os.utime(f, (time.time() + 5, time.time() + 5))    # 保证 mtime 变化（避开同 tick 写入）
    m2 = aw._load_module("w1", "agent.py")
    assert m2 is not None and m2.VALUE == 2, "改写 agent.py 后必须重新加载（热更新语义）"

    f.unlink()
    assert aw._load_module("w1", "agent.py") is None, "文件删除后应回退内置实现"


# ---------- workflow.json 元数据：缓存 + 写入即时可见 + 返回拷贝 ----------

def test_read_meta_cache_write_refresh_and_copy(monkeypatch, tmp_path):
    monkeypatch.setattr(aw, "workflow_dir", lambda name: tmp_path / name)
    aw._write_meta("wmeta", {"enabled": True, "skill_states": {"a": True}})
    got = aw._read_meta("wmeta")
    assert got["enabled"] is True

    got["enabled"] = False                 # 调用方就地修改不得污染缓存（skill_states 同理会改）
    assert aw._read_meta("wmeta")["enabled"] is True

    aw._write_meta("wmeta", {"enabled": False})
    assert aw._read_meta("wmeta")["enabled"] is False, "写入后必须立即读到新值（用户改动生效）"


# ---------- 工具 schema：缓存深拷贝不得被调用方破坏 ----------

def test_tool_schemas_returns_independent_list():
    from zhuzhu_Copilot.core.agent_workflow import DEFAULT_WORKFLOW
    base = at.tool_schemas(DEFAULT_WORKFLOW)
    names = {t["function"]["name"] for t in base}
    assert "read_file" in names and "run_command" in names

    base.clear()                           # 调用方清空返回列表
    base.append({"function": {"name": "fake"}})
    again = at.tool_schemas(DEFAULT_WORKFLOW)
    names2 = {t["function"]["name"] for t in again}
    assert "read_file" in names2 and "run_command" in names2, "缓存被调用方修改污染"
    assert "fake" not in names2


# ---------- 任务清单：每轮读取走缓存但写盘后即时可见 ----------

def test_load_todos_reflects_writes(monkeypatch, tmp_path):
    f = tmp_path / "todos.json"
    monkeypatch.setattr(at, "TODO_FILE", f)
    monkeypatch.setattr(at, "_TODO_CACHE", {})
    # 显式传 "" = 无会话作用域（兼容文件）：清单按会话隔离后，缺省调用会解析到
    # 当前会话（UI 当前对话），此处直接验证「文件缓存 + 写盘即时可见」的存储语义。
    f.write_text(json.dumps([{"title": "t1", "status": "pending"}]), encoding="utf-8")
    assert [t["title"] for t in at.load_todos("")] == ["t1"]
    assert [t["title"] for t in at.load_todos("")] == ["t1"]   # 缓存命中路径

    f.write_text(json.dumps([{"title": "t2", "status": "done"},
                             {"bad": 1}]), encoding="utf-8")
    os.utime(f, (time.time() + 5, time.time() + 5))
    assert [t["title"] for t in at.load_todos("")] == ["t2"], "写盘后必须读到最新清单"

    f.unlink()
    assert at.load_todos("") == []


# ---------- 单条消息 token 估算：按消息身份缓存，结果必须与全量重算逐字节等价 ----------
#
# 背景（探针实测 120 轮循环）：_estimate_tokens 每轮被调用 4 次（发送前预计算 / 占用
# 判定 ×2 / 压缩判定），全量重算占循环开销 50%+（3.344s → 0.107s）。故按消息对象
# 身份缓存单条估算 —— 本组测试守护「缓存不得引入陈旧值」：内容被替换、消息被裁剪、
# 数组内容/工具调用/图片等成分都必须实时反映。

def _perf_engine():
    """轻量引擎实例：只依赖 stub LLM（不读设置、不起线程），专测估算缓存契约。"""
    from zhuzhu_Copilot.core import agent_engine as ae

    class _StubLLM:
        model = "stub"
        fell_back = False
        silent_fallback = False

    return ae.AgentEngine(_StubLLM(), text_only=True)


def _uncached_estimate(eng) -> int:
    return sum(eng._msg_estimate(m) for m in eng._messages)


def test_estimate_tokens_cache_equals_uncached_sum():
    eng = _perf_engine()
    eng._messages = [
        {"role": "system", "content": "很长的系统提示词" * 50},
        {"role": "user", "content": "中文与 english mixed 123"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "数组文本"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}],
         "tool_calls": [{"id": "c1", "function": {"name": "read_file",
                                                  "arguments": '{"path": "a.txt"}'}}]},
        {"role": "tool", "content": "工具输出" * 20},
    ]
    first = eng._estimate_tokens()
    assert first == _uncached_estimate(eng), "首次估算须与全量重算一致"
    assert eng._estimate_tokens() == first, "缓存命中路径结果必须相同"


def test_estimate_tokens_cache_invalidates_on_content_replacement():
    eng = _perf_engine()
    msg = {"role": "system", "content": "短提示"}
    eng._messages = [msg, {"role": "user", "content": "任务"}]
    eng._estimate_tokens()

    msg["content"] = "长提示" * 800           # 每轮重建 system 的就地替换路径
    assert eng._estimate_tokens() == _uncached_estimate(eng), "内容被替换必须重算"

    eng._messages[1]["content"] = [{"type": "text", "text": "换成数组文本"}]
    assert eng._estimate_tokens() == _uncached_estimate(eng), "content 类型变化必须重算"

    # 工具调用参数改写（tool_calls 列表对象被替换）同样要重算
    eng._messages.append({"role": "assistant", "content": "",
                          "tool_calls": [{"id": "c1",
                                          "function": {"name": "x", "arguments": "{}"}}]})
    eng._estimate_tokens()
    eng._messages[-1]["tool_calls"] = [
        {"id": "c1", "function": {"name": "x", "arguments": '{"path": "' + "y" * 300 + '"}'}}]
    assert eng._estimate_tokens() == _uncached_estimate(eng), "tool_calls 改写必须重算"


def test_estimate_tokens_cache_drops_removed_messages():
    eng = _perf_engine()
    eng._messages = [{"role": "system", "content": "sys"}] + \
        [{"role": "user", "content": f"消息{i}" * 10} for i in range(5)]
    eng._estimate_tokens()

    del eng._messages[1:4]                    # 压缩/硬裁：丢弃最旧整段
    assert eng._estimate_tokens() == _uncached_estimate(eng), "已删消息不得滞留缓存"

    eng._messages = [{"role": "system", "content": "sys"}]   # 列表整体重建
    assert eng._estimate_tokens() == _uncached_estimate(eng)


def test_estimate_tokens_cache_is_bounded_after_repeated_trim():
    from zhuzhu_Copilot.core.agent_engine import _EST_CACHE_SLACK
    eng = _perf_engine()
    eng._messages = [{"role": "system", "content": "sys"}]
    for i in range(300):
        eng._messages.append({"role": "user", "content": f"n{i}"})
        eng._estimate_tokens()
        if len(eng._messages) > 5:
            del eng._messages[1:4]            # 反复裁剪：缓存不得无限增长
    assert len(eng._est_cache) <= len(eng._messages) + _EST_CACHE_SLACK
    assert eng._estimate_tokens() == _uncached_estimate(eng)
