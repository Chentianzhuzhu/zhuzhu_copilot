# -*- coding: utf-8 -*-
"""AI 回合包裹层（`_TurnWrap`）必须紧贴回合，不得随正文增长虚高。

用户反馈：「AI 思考大段文字时气泡剧烈抖动，打字指示器疯狂向下移动并剧烈抖动，
AI 输出的区域整体上下方不断有大片空白」。

根因（实测）：回合本体外面还套着一层包裹层（[回合][重试行]）。该层体内含 word-wrap
富文本标签，整条链都带 heightForWidth，Qt 的 `QBoxLayout::heightForWidth` 给这层算出的
高度会**随正文增长而虚高**（253px → 828px，而回合本体始终 238px），外层消息流把它当
实际高度用 → 回合上下（以及指示器上方）就出现越来越大的一片空白，并随每个 tick 增长。

断言口径（都是「包裹层相对回合本体的多出量」这类与机器无关的确定性量）：
  1. 多出量恒等于「间距 + 重试行高度」，不随正文增长；
  2. 回合在包裹层内顶端对齐（不再被垂直居中 → 不再上下对称留白）；
  3. 流式推进时包裹层/消息流容器不持续膨胀；
  4. 包裹层高度只由**回合内容高**决定，与它当前分配出去的高度无关（压扁不可自愈的根因）；
  5. 回合内层装得下全部可见内容（否则底部正文与系统时间行被裁）；
  6. 收展回调不清零回合最小高度；耗时可从**任务起点**起钟（含子 Agent 静默期）。
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest                                        # noqa: E402
from PyQt6.QtWidgets import QApplication             # noqa: E402

_app = QApplication.instance() or QApplication([])

from zhuzhu_Copilot.ui import agent_panel as ap       # noqa: E402
from zhuzhu_Copilot.ui import main_window as mw       # noqa: E402
from zhuzhu_Copilot.ui.agent_panel import AgentPanel  # noqa: E402

CHUNK = "用户要求扫描全盘部署包并生成迁移清单，先枚举安装产物再按指纹过滤；"


def _pump(ms: int = 6):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        _app.processEvents()
        time.sleep(0.002)


def _chrome(wrap) -> int:
    """包裹层钉高时额外加的「间距 + 重试行高」，**判据必须与 `_TurnWrap._real_h` 完全一致**：
    第二项为空（重试行隐藏 / 空布局项）时就不加。

    这里踩过一次坑：测试原先自己按 `v.spacing() + v.itemAt(1).sizeHint().height()` 硬算，
    重试行隐藏时也算进去，于是比生产实现多算几像素 —— 而「隐藏控件还有多大 sizeHint」随字体
    而异（本机 2px、CI 3px），断言就成了「本机刚好落在 ±2 容差内、CI 越界报红」。
    """
    try:
        v = wrap.layout()
        if v is not None and v.count() > 1:
            btm = v.itemAt(1)
            if btm is not None and not btm.isEmpty():
                return int(v.spacing()) + int(btm.sizeHint().height())
    except Exception:
        pass
    return 0


def _snapshot(p):
    """当前几何指纹（用于判断布局是否已收敛）"""
    turn = getattr(p, "_ai_bubble", None)
    if turn is None:
        return None
    wrap = turn.parentWidget()
    return (turn.height(), wrap.height() if wrap is not None else 0,
            turn._box.height(), turn.minimumHeight(), turn.width(),
            _chrome(wrap) if wrap is not None else 0)


def _settle(p, max_ms: int = 300):
    """把布局链推到收敛为止 —— 不依赖跑得快。

    背景（CI 实测）：这些断言比的是**已分配几何**（wrap/turn/_box 的高度）与**内容所需**
    （minimumHeight / 各块最小高之和）。前者由布局系统在事件循环里分配，CI runner 比本机慢，
    只跑固定几毫秒的 processEvents 时，布局往往只走完一半（LayoutRequest → _TurnWrap.sync_height
    → 回合重排 → 内层 _box 重新分配），断言就读到上一轮的旧几何：本机全绿、换台机器整片误报
    （实测 130px vs 483px）。这里显式调用生产路径的入口（sync_height + 各层 layout.activate），
    再跑到几何连续两轮不变为止；仍然收敛不了的话，断言照旧失败。
    """
    end = time.time() + max_ms / 1000.0
    last, stable = object(), 0
    while time.time() < end:
        _app.processEvents()
        turn = getattr(p, "_ai_bubble", None)
        wrap = turn.parentWidget() if turn is not None else None
        if wrap is not None:
            try:
                wrap.sync_height()                  # 生产路径入口：按内容钉包裹层高度
                if wrap.layout() is not None:
                    wrap.layout().activate()        # 立即分配「回合 + 重试行」几何
                if turn.layout() is not None:
                    turn.layout().activate()        # 立即分配回合内层几何
                turn._box_lay.activate()
            except Exception:
                pass
        cur = _snapshot(p)
        if cur == last:
            stable += 1
            if stable >= 2:
                return
        else:
            stable, last = 0, cur
        time.sleep(0.004)


@pytest.fixture(scope="module")
def panel():
    mp = pytest.MonkeyPatch()
    mp.setattr(mw.CopilotPanel, "_start_scan", lambda self, *a: None)
    mp.setattr(mw.CopilotPanel, "_init_update_check", lambda self: None)
    mp.setattr(mw.CopilotPanel, "_check_admin", lambda self: None)
    mp.setattr(mw.CopilotPanel, "_setup_tray", lambda self: None)
    try:
        mp.setattr("zhuzhu_Copilot.ui.desktop_pet.ensure_pet", lambda w: None)
    except Exception:
        pass
    p = AgentPanel(None)
    p.resize(1000, 900)
    p.show()
    _pump(400)
    yield p
    # 必须把顶层窗口收起来：留一个可见的大窗口会让后续用例（CopilotPanel 的开合动画
    # 用例）在事件循环里被拖住 —— 跨文件随机顺序下表现为「某次收起失败」。
    # 只 hide 不 destroy：面板后台线程（MCP 等）还在跑，销毁会让它们向已删对象发信号。
    try:
        p.hide()
        _pump(30)
    except Exception:
        pass
    mp.undo()


_DETAILS: list = []          # 每个 tick 的现场（失败时打进断言信息，CI 只靠日志看得到）


def _hug(p):
    """推进一个 tick 的布局到**收敛**，回报该 tick 的几何
    (回合高, 包裹层高, 期望包裹层高, 回合 y, 重试行高)。

    为什么不「pump 一下就断言」：这里比的是**已分配几何**与**内容所需**，两者都由布局系统
    在事件循环里分配/回写。CI runner 上固定几毫秒的 pump 常常只走完一半布局链
    （LayoutRequest → _TurnWrap.sync_height → 回合重排 → 内层 _box 重新分配），就会拿上一轮
    的旧几何去断言（实测本机全绿、runner 上 130px vs 483px 整片误报）。

    这里只是**把布局推到位**（显式走一次生产入口 sync_height，再跑到几何连续两轮不变），
    不是「等到断言成立」：收敛之后断言照旧生效 —— 包裹层真贴不上回合时，收敛后依然不等，
    用例依然失败。
    """
    turn = p._ai_bubble
    wrap = turn.parentWidget()
    wrap.sync_height()          # 生产路径入口：按回合内容高钉包裹层
    _settle(p)                  # 跑到几何（含 chrome）不动（_settle 每轮也会再 sync 一次）
    want = (max(int(turn.minimumHeight()), int(turn.height())) + _chrome(wrap))
    _DETAILS.append(_geom_detail(p))
    return (turn.height(), wrap.height(), want, turn.geometry().y(), _chrome(wrap))


def _stream_thinking(p, ticks: int):
    """模拟一次长思考的流式推送，返回每个 tick 的
    (回合高, 包裹层高, 期望包裹层高, 回合 y, 重试行高)"""
    p._task_active = True
    p._ensure_ai_bubble()
    p._ensure_spinner()
    p._set_spinner_text("AI 思考中…")
    _DETAILS.clear()
    trace = []
    for _ in range(ticks):
        p._on_reasoning(CHUNK)
        p._apply_refresh_ai_html()
        trace.append(_hug(p))
    return trace


def _geom_detail(p) -> str:
    """失败时把参与判断的几何全打出来（CI 上只有日志能看到现场）"""
    turn = getattr(p, "_ai_bubble", None)
    if turn is None:
        return "（无回合）"
    wrap = turn.parentWidget()
    try:
        hfw = int(turn.heightForWidth(max(1, turn.width())))
    except Exception:
        hfw = -1
    return (f"[宽 {turn.width()} 高 {turn.height()} 最小高 {turn.minimumHeight()} "
            f"HFW {hfw} 内层 {turn._box.height()} 包裹层 {wrap.height() if wrap else -1} "
            f"包裹层固定高 {wrap.minimumHeight() if wrap else -1} "
            f"chrome {_chrome(wrap) if wrap else 0}]")


def test_turn_wrapper_hugs_turn_and_never_inflates(panel):
    """包裹层高度必须恒等于「回合 + 间距 + 重试行」，不得随正文增长虚高。

    历史缺陷：包裹层被 Qt 的 HFW 估算成 253→828px（回合本体始终 238px），外层消息流照它
    布局 → 回合上下留出越来越大的一片空白。
    """
    trace = _stream_thinking(panel, 24)
    assert trace, "未产生流式轨迹"

    for i, (h_turn, h_wrap, want, y, retry_h) in enumerate(trace):
        detail = _DETAILS[i] if i < len(_DETAILS) else ""
        assert abs(h_wrap - want) <= 2, (
            f"第 {i} tick 包裹层 {h_wrap}px ≠ 回合+重试行 {want}px → 回合上下会留大片空白 "
            f"{detail}")
        assert y <= 2, f"第 {i} tick 回合未顶端对齐（y={y}）→ 上下对称留白 {detail}"

    # 包裹层相对回合的「多出量」只应等于间距 + 重试行，全程不得漂移
    extras = {h_wrap - h_turn for h_turn, h_wrap, _w, _y, _r in trace}
    assert max(extras) - min(extras) <= 4, f"多出量在流式期间漂移：{sorted(extras)}"


def test_message_flow_does_not_inflate_during_long_thinking(panel):
    """流式推进时消息流容器不得被虚高高度顶爆（否则滚动区出现大片空白）。"""
    p = panel
    area = p.msg_area
    before = area.widget().height()
    _stream_thinking(p, 30)
    after = area.widget().height()
    assert after - before <= area.viewport().height(), \
        f"消息流容器被撑到 {after}px（此前 {before}px，视口 {area.viewport().height()}px）"


def test_wrapper_recovers_after_being_squeezed_too_small(panel, monkeypatch):
    """包裹层被钉成过小高度后必须自愈 —— 否则回合被永久压扁、块之间互相盖住。

    历史缺陷：`_real_h()` 拿 `turn.height()` 当基准，而回合的实际高度正是包裹层
    （setFixedHeight）分配出来的 —— 任何一次「瞬时偏小」都会变成**不动点**：偏小 →
    回合被压扁 → 块与块互相盖住（用户反馈的「展开执行过程后文字严重折叠遮挡」），
    而且自己再也回不来。基准必须是回合自己的内容高度（heightForWidth）。
    """
    p = panel
    _stream_thinking(p, 10)
    turn = p._ai_bubble
    wrap = turn.parentWidget()

    # 把「回合内容高度」固定成一个探针值：包裹层的高度必须由它决定，
    # **与包裹层当前给回合分配的高度无关**（旧实现拿 turn.height() 当基准，正是后者）。
    marker = 1234
    monkeypatch.setattr(turn, "heightForWidth", lambda _w, _v=marker: _v)
    v = wrap.layout()
    want = marker + v.spacing() + int(v.itemAt(1).sizeHint().height())

    got = []
    for h in (60, 4000, 90):             # 三种截然不同的「瞬时被挤压程度」
        wrap.setFixedHeight(h)
        _pump(20)
        wrap.sync_height()
        got.append(wrap.height())
    assert len(set(got)) == 1, f"包裹层高度跟着被分配的高度走（不是内容高度）：{got}"
    assert got[0] == want, f"包裹层高度 {got[0]}px ≠ 内容所需 {want}px"


def test_toggle_sync_keeps_turn_min_height(panel):
    """过程区收展回调不得清零事件流回合的最小高度。

    历史缺陷：`_sync_after_toggle` 一律 `setMinimumHeight(0)`，而面板对事件流回合是
    **跳过**不钉的（高度自管）—— 清零后没有任何人再钉回来，回合失去基准，随后被包裹层
    按偏小值钉住 → 展开过程区后正文互相盖住。
    """
    p = panel
    _stream_thinking(p, 6)
    turn = p._ai_bubble
    assert turn.minimumHeight() > 0
    p._sync_after_toggle(turn)
    _pump(20)
    # 允许按当前内容重算（内容/宽度变了，值可以变）；不允许的是「失去基准」——
    # 清零后就没人再钉回来，回合随即被包裹层按偏小值压扁。
    need = turn.heightForWidth(turn.width())
    assert turn.minimumHeight() >= need - 2, \
        f"回合最小高度失效（{turn.minimumHeight()} < 内容所需 {need}）→ 会被压扁"


def test_turn_box_is_tall_enough_for_all_visible_blocks(panel):
    """回合内层 `_box` 必须装得下全部可见内容 —— 外层 `_lay` 的默认边距也要计入高度。

    历史缺陷：高度计算只算 `_box_lay` 的边距，漏掉外层 `_lay` 的 9+9（Qt 默认样式边距）
    → 回合真高比预算少 18px，底部内容被压掉：最后一块正文被切、**系统时间行整行不可见**
    （用户反馈的「时间记录的文字被遮挡」）。
    """
    p = panel
    _stream_thinking(p, 10)
    turn = p._ai_bubble
    m = turn._box_lay.contentsMargins()
    need = m.top() + m.bottom()
    for ref in turn._items:
        if ref.widget.isHidden():
            continue
        need += ref.widget.minimumHeight() + ref.spacer.sizeHint().height()
    if turn._settled:
        need += turn._toggle.sizeHint().height()
    if not turn._sys.isHidden():
        need += turn._sys.heightForWidth(max(1, turn.width() - m.left() - m.right()))

    assert turn._box.height() >= need - 2, (
        f"回合内层只有 {turn._box.height()}px，内容需要 {need}px → "
        f"底部 {need - turn._box.height()}px（含系统时间行）会被裁掉 {_geom_detail(p)}")


def test_turn_timing_starts_at_task_launch_not_first_token(panel):
    """耗时计时必须从**任务起点**起钟，不能等第一个内容事件。

    历史缺陷：回合起点在「第一个事件到达」时才取当前时间 —— 慢首字、以及「派发子 Agent
    后长时间没有任何输出」的静默期整段被漏掉（用户反馈的「子 Agent 调用时不计入时长」）。
    起点应取用户发送 / @子Agent 直呼那一刻（`_turn_started_at`）。
    """
    p = panel
    p._task_active = True
    p._turn_started_at = time.time() - 12.0     # 12 秒静默期（子 Agent 在跑）
    p._ai_bubble = None
    p._segments.clear()

    p._ensure_ai_bubble()
    turn = p._ai_bubble
    assert turn is not None
    assert turn.t_start <= time.time() - 11.0, \
        "回合计时起点不是任务起点（前面那段静默期被漏掉）"

    p._render_ai_frame(turn, p._segments)
    assert turn.timing_started, "耗时徽章未起钟"
    assert time.time() - turn.started_at >= 11.0, \
        f"耗时计时起点没落在任务起点（已过 {time.time() - turn.started_at:.1f}s）"


def test_retry_row_appearing_keeps_wrapper_consistent(panel):
    """重试行显隐（悬停重试按钮）后包裹层仍与回合一致，不残留旧高度。"""
    p = panel
    turn = p._ai_bubble
    retry = turn._retry_btn
    retry.setVisible(True)          # 模拟悬停显示
    _h_turn, h_wrap, want, _y, _r = _hug(p)
    assert abs(h_wrap - want) <= 2, \
        f"重试行显示后包裹层 {h_wrap} ≠ 期望 {want} {_geom_detail(p)}"
