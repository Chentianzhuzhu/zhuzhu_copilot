"""子 Agent 并发派发 + 气泡渲染回归。

覆盖本次修复：
1. dispatch_sub_agents 多任务真正并发（线程池并行，同一批可含重复同名子 Agent）
2. 引擎 _dispatch_tasks 把普通子任务 + 自定义子 Agent 任务合并为一批并发派发
   （此前逐任务串行执行，多子 Agent 根本不同时跑）
3. 子 Agent 气泡渲染：
   - steps（工具/输出步骤）纳入段签名 → 追加步骤后缓存失效、步骤真正显示
   - 流式 raw / 命令输出做空行折叠 → 尾部多余换行不再渲染成片 <br/>（底部空白/段间距大）
"""
import os
import sys
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from zhuzhu_Copilot.core import agent_engine, agent_subagent
from zhuzhu_Copilot.ui import agent_panel


def _make_engine(monkeypatch):
    class _FakeLLM:
        model = "test-model"
        fell_back = False
        silent_fallback = False

        def __init__(self):
            self.tokens = {"prompt": 0, "completion": 0}

        def chat_stream(self, messages, **kw):
            return {"text": "ok", "tool_calls": [], "usage": None,
                    "cache": {"hit": 0, "miss": 0}}

    monkeypatch.setattr(agent_engine.agent_tts, "load_config", lambda: {"auto_read": False})
    return agent_engine.AgentEngine(_FakeLLM(), text_only=True)


def test_dispatch_sub_agents_runs_batch_concurrently(monkeypatch):
    """同一批多个子任务（含重复同名子 Agent）必须真正并发执行。"""
    entered = []
    all_entered = threading.Event()
    release = threading.Event()

    def fake_run_sub_agent(llm, goal, **kw):
        entered.append(kw.get("custom"))
        if len(entered) >= 3:
            all_entered.set()
        release.wait(15)          # 卡住所有并发任务，等待主控放行
        return f"done:{goal[:10]}"

    monkeypatch.setattr(agent_subagent, "run_sub_agent", fake_run_sub_agent)
    tasks = [{"title": "A", "goal": "创建 a.py"}, {"title": "A2", "goal": "创建 b.py"},
             {"title": "A3", "goal": "编辑 c.py"}]
    out_holder = {}

    def _run():
        out_holder["res"] = agent_subagent.dispatch_sub_agents(
            None, tasks, max_workers=3)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    try:
        # 全部 3 个任务同时进入（串行时只会进 1 个并卡住 → 超时判失败）
        assert all_entered.wait(5), "子任务未并发进入（可能被串行执行）"
        assert len(entered) == 3, f"应 3 个任务并发进入，实际 {len(entered)}"
    finally:
        release.set()
    t.join(15)
    res = out_holder.get("res", "")
    assert "【子任务 1】A" in res and "【子任务 3】A3" in res, res


def test_dispatch_sub_agents_unlimited_concurrency(monkeypatch):
    """并发度默认不限（SUB_AGENT_MAX_WORKERS=0）：派发 12 个子任务全部同时进入，
    不再受此前 max_workers=4 的并发上限约束。"""
    n = 12
    entered = []
    all_in = threading.Event()
    gate = threading.Event()

    def fake_run_sub_agent(llm, goal, **kw):
        entered.append(goal)
        if len(entered) >= n:
            all_in.set()
        gate.wait(20)                 # 卡住全部并发任务，等待主控放行
        return f"done:{goal}"

    monkeypatch.setattr(agent_subagent, "run_sub_agent", fake_run_sub_agent)
    tasks = [{"title": f"T{i}", "goal": f"目标{i}"} for i in range(n)]
    holder = {}

    def _run():
        holder["res"] = agent_subagent.dispatch_sub_agents(None, tasks)

    assert agent_subagent.SUB_AGENT_MAX_WORKERS == 0, "默认并发度应为 0（不限制）"
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    try:
        assert all_in.wait(10), f"应 {n} 个子任务并发进入，实际 {len(entered)}"
        assert len(entered) == n
    finally:
        gate.set()
    t.join(20)
    assert f"【子任务 {n}】T{n - 1}" in holder.get("res", "")


def test_dispatch_sub_agents_explicit_cap_still_effective(monkeypatch):
    """显式传入 max_workers 时并发上限仍生效（限制能力保留、可配置，非删除）。"""
    lock = threading.Lock()
    state = {"active": 0, "peak": 0}

    def fake_run_sub_agent(llm, goal, **kw):
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        time.sleep(0.05)
        with lock:
            state["active"] -= 1
        return goal

    monkeypatch.setattr(agent_subagent, "run_sub_agent", fake_run_sub_agent)
    tasks = [{"title": f"T{i}", "goal": f"目标{i}"} for i in range(8)]
    agent_subagent.dispatch_sub_agents(None, tasks, max_workers=2)
    assert state["peak"] <= 2, f"显式上限 2 未生效，峰值并发 {state['peak']}"


def test_dispatch_sub_agents_collect_order(monkeypatch):
    """collect 收集器按任务原始顺序返回（供引擎按主顺序组装汇总）。"""
    def fake_run_sub_agent(llm, goal, **kw):
        return f"r:{goal[:8]}"

    monkeypatch.setattr(agent_subagent, "run_sub_agent", fake_run_sub_agent)
    tasks = [{"title": "T1", "goal": "目标一"}, {"title": "T2", "goal": "目标二"}]
    coll = []
    agent_subagent.dispatch_sub_agents(None, tasks, collect=coll)
    assert [t for _i, t, _x in coll] == ["T1", "T2"] and len(coll) == 2


def test_engine_dispatch_tasks_batches_once(monkeypatch):
    """引擎 _dispatch_tasks：普通子任务合并为一次并发批派发（不再逐任务串行）。"""
    eng = _make_engine(monkeypatch)
    calls = []

    def fake_dispatch(llm, tasks, **kw):
        calls.append(list(tasks))
        coll = kw.get("collect")
        if coll is not None:
            for i, t in enumerate(tasks):
                coll.append((i, t["title"], f"out:{t['title']}"))
        return ";".join(t["title"] for t in tasks)

    monkeypatch.setattr(agent_subagent, "dispatch_sub_agents", fake_dispatch)
    tasks = [{"title": "任务1", "goal": "创建 f1.py", "context": "c"},
             {"title": "任务2", "goal": "写入 f2.py", "context": "c"},
             {"title": "任务3", "goal": "搜索 xxx", "context": "c"}]
    out = eng._dispatch_tasks(tasks)
    assert len(calls) == 1 and len(calls[0]) == 3, f"应一次批派 3 个任务，实际: {len(calls)} 次"
    assert "【子任务 1】任务1" in out and "【子任务 3】任务3" in out
    assert "out:任务2" in out


def test_engine_dispatch_tasks_repeat_same_custom_sub(monkeypatch):
    """同名自定义子 Agent 可在一个批次中重复派发（两次均并发运行）。"""
    eng = _make_engine(monkeypatch)
    conf = {"name": "coder", "goal": "你是一位编码助手。", "allowed": ["write_file", "edit_file"]}
    monkeypatch.setattr(agent_subagent, "subagent_tool", lambda name, wf=None: dict(conf))
    calls = []

    def fake_dispatch(llm, tasks, **kw):
        calls.append(list(tasks))
        coll = kw.get("collect")
        if coll is not None:
            for i, t in enumerate(tasks):
                coll.append((i, t["title"], "done"))
        return ""

    monkeypatch.setattr(agent_subagent, "dispatch_sub_agents", fake_dispatch)
    tasks = [{"title": "S1", "goal": "改 a.py", "sub": "coder", "context": "c"},
             {"title": "S2", "goal": "改 b.py", "sub": "coder", "context": "c"}]
    eng._dispatch_tasks(tasks)
    assert len(calls) == 1 and len(calls[0]) == 2, "同名子 Agent 应合并为一批并发派发"
    assert all(t.get("custom") and t.get("persona") for t in calls[0])
    # 未注册的子 Agent → 派发失败提示且不进批
    monkeypatch.setattr(agent_subagent, "subagent_tool", lambda name, wf=None: None)
    monkeypatch.setattr(agent_subagent, "dispatch_sub_agents", fake_dispatch)
    out = eng._dispatch_tasks([{"title": "X", "goal": "g", "sub": "nope", "context": "c"}])
    assert "未在当前工作流注册" in out


def _new_render_obj():
    """构造轻量渲染代理（复用真实 _seg_blocks/_render_seg_html，不构造整个面板）。"""
    obj = agent_panel.AgentPanel.__new__(agent_panel.AgentPanel)
    obj._seg_cache = {}
    obj._font_scale = lambda: 1.0
    obj._bubble_max_width = lambda: 520
    obj._ai_turn_max_width = lambda: 520   # 事件流回合铺满内容宽度（截图宽度基准）
    return obj


def _render_sub(obj, segs):
    """用轻代理对象（不构造整个面板）渲染组段：拼接各段区块的内层 HTML，
    与事件流回合实际消费的内容一致。同一 obj 保留 _seg_cache，可验证缓存命中/失效。"""
    return "".join(str(payload.get("html") or "")
                   for _kind, payload, _sig in obj._seg_blocks(segs))


def test_sub_steps_invalidate_render_cache():
    """steps 追加后重渲染必须包含步骤（此前签名漏掉 steps → 缓存命中旧 HTML）。
    用任务进行中的展开态（collapsed=False）验证步骤渲染。"""
    obj = _new_render_obj()
    seg = {"type": "sub", "title": "编码", "raw": "正在处理…", "steps": [],
           "collapsed": False}
    html1 = _render_sub(obj, [seg])
    assert "file.py" not in html1
    seg["steps"].append({"kind": "tool", "text": "write_file file.py"})
    html2 = _render_sub(obj, [seg])
    assert "write_file file.py" in html2, "追加步骤后渲染未刷新（缓存旧 HTML）"
    seg["steps"].append({"kind": "output", "text": "已写入"})
    html3 = _render_sub(obj, [seg])
    assert "已写入" in html3


def test_sub_raw_trailing_newlines_collapsed():
    """raw/输出尾部多余换行不再渲染成片 <br/>（消除气泡底部大块空白）。"""
    obj = _new_render_obj()
    seg = {"type": "sub", "title": "搜索", "raw": "结果1\n结果2\n\n\n",
           "steps": [{"kind": "output", "text": "123\n\n\n"}], "collapsed": False}
    html = _render_sub(obj, [seg])
    # 正文折叠为 结果1<br/>结果2 后直接闭合，无多余尾部 <br/>
    assert "结果1<br/>结果2</div>" in html
    # 输出步骤折叠后为单个 123，无尾部 <br/>
    assert ">123</div>" in html and "123<br/>" not in html


def test_sub_empty_raw_no_blank_box():
    """raw 为空的子块不渲染空边框框体（不再出现大块空白）。"""
    obj = _new_render_obj()
    seg = {"type": "sub", "title": "等待", "raw": "", "steps": []}
    html = _render_sub(obj, [seg])
    assert "子Agent · 等待" in html
    assert html.count("border-left:2px") == 0, "空 raw 不应渲染边框框体"


# ---------- 长上下文体积护栏（长对话「任何操作都卡」的根因修复） ----------

def _big_sub(title="大子块", steps=40, chars=6000):
    """构造一个「大」子 Agent 块：多步 + 超长输出（实测真实会话单块可达 ~90KB 富文本）"""
    return {"type": "sub", "title": title, "raw": "总结 " * 2000,
            "steps": [{"kind": "tool" if i % 2 else "output",
                       "text": f"step{i} " + "x" * chars} for i in range(steps)],
            "collapsed": False}


def test_sub_collapsed_by_default_for_history():
    """历史子块（无 collapsed 字段）默认折叠为一行 —— 老会话才不会被大子块撑爆。"""
    obj = _new_render_obj()
    seg = {"type": "sub", "title": "考古", "raw": "很长" * 5000,
           "steps": [{"kind": "output", "text": "y" * 6000}]}
    html = _render_sub(obj, [seg])
    assert len(html) < 400, f"折叠态应为一行的体量，实际 {len(html)} 字符"
    assert "子Agent · 考古" in html and "已折叠 · 点击展开" in html
    assert "sub:toggle:0" in html, "折叠态必须给出可点击的展开链接"
    assert "yyyy" not in html, "折叠态不得渲染步骤正文"


def test_sub_toggle_link_changes_render_and_cache():
    """折叠/展开切换必须重新渲染（签名含 collapsed，否则缓存返回旧 HTML）。"""
    obj = _new_render_obj()
    seg = _big_sub(steps=3, chars=50)
    seg["collapsed"] = True
    collapsed_html = _render_sub(obj, [seg])
    assert "已折叠 · 点击展开" in collapsed_html
    seg["collapsed"] = False
    open_html = _render_sub(obj, [seg])
    assert "收起 ▲" in open_html and "step0" in open_html
    assert len(open_html) > len(collapsed_html)


def test_sub_open_view_is_bounded():
    """展开态有体积上限：只渲染最近 N 步、单步/总结各自截断（点击展开不会再次卡死）。"""
    obj = _new_render_obj()
    seg = _big_sub(steps=40, chars=6000)
    html = _render_sub(obj, [seg])
    assert f"仅展示最近 {agent_panel._SUB_OPEN_MAX_STEPS} 步" in html
    assert "step0 " not in html, "最早步骤应被裁掉"
    assert "step39" in html, "最近步骤必须保留"
    assert "x" * (agent_panel._SUB_STEP_TRUNCATE + 50) not in html, "单步输出需截断"


def test_each_process_block_is_size_bounded():
    """单块体积有界：过程段各自成为独立区块控件，每段自带截断上限 → 长对话里不会出现
    「单个控件承载数百 KB 富文本 → 布局卡死」。

    事件流改造后过程区不再由一个 QLabel 容纳，旧的「整泡 HTML 预算」护栏下沉为
    「单段截断」这一不变式（_RESULT_TRUNCATE / _SUB_*）。这里守住该不变式：
    无论多少个大子块，每块尺寸都落在上限内。
    """
    obj = _new_render_obj()
    segs = [_big_sub(f"子{i}") for i in range(6)]
    segs.append({"type": "text", "raw": "最终答复正文" * 20})
    blocks = obj._seg_blocks(segs)
    assert len(blocks) == 7, "每段各成一个区块（正文独立且不被裁掉）"

    # 单块上限 = 步骤数 × 单步截断 × 2（2 倍余量覆盖转义与标记开销）
    bound = agent_panel._SUB_OPEN_MAX_STEPS * agent_panel._SUB_STEP_TRUNCATE * 2
    for idx, (kind, payload, _sig) in enumerate(blocks):
        size = len(str(payload.get("html") or ""))
        assert size <= bound, f"第 {idx} 块（{kind}）{size} 字符超出单块上限 {bound}"
    assert "最终答复正文" in str(blocks[-1][1].get("html")), "正文不得被截断"
    assert all(s["collapsed"] is False for s in segs[:-1]), \
        "数据层不再被护栏回写 collapsed（折叠由回合容器的过程区统一承担）"


def test_at_call_uses_registered_shared_switch(monkeypatch):
    """@调用注册式子 Agent：@调用没有主 Agent 在场逐次分配，按注册的 shared_context
    决定是否加入共同上下文空间，并把子 Agent 名作为写回空间的来源标签。"""
    import types
    p = agent_panel.AgentPanel.__new__(agent_panel.AgentPanel)
    p._sess = {"s1": {"sub_history": {}}}
    p._sub_stop = None
    p.evt_signal = types.SimpleNamespace(emit=lambda *a, **k: None)
    seen = {}

    def fake_run(llm, goal, **kw):
        seen["goal"] = goal
        seen.update(kw)
        return "ok"

    monkeypatch.setattr(agent_panel.agent_subagent, "run_sub_agent", fake_run)
    conf = {"name": "menxia", "goal": "审议", "allowed": [],
            "persona": "", "shared_context": True}
    p._subagent_worker("s1", "menxia", conf, "审第 2 节", None)
    assert seen["goal"] == "审第 2 节" and seen["custom"] is True
    assert seen["shared_context"] is True
    assert seen["source"] == "menxia", "写回空间的来源须为子 Agent 名（成员区分彼此产出）"

    # 注册未预设共享（None）→ @调用按共享全开策略加入共享空间（缺省开启）
    p._subagent_worker("s1", "menxia", {**conf, "shared_context": None}, "再来一次", None)
    assert seen["shared_context"] is True
    # 注册显式关闭（false）→ @调用保持独立上下文
    p._subagent_worker("s1", "menxia", {**conf, "shared_context": False}, "第三次", None)
    assert seen["shared_context"] is False