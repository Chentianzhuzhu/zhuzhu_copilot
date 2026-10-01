"""长任务 UI 流畅度回归：渲染节流 / 段渲染缓存 / 滚动合并 三项核心契约。

背景：Agent 执行长任务时，流式正文与工具结果会以极高频率（每 token / 每条状态）
触发 AI 气泡重渲染。若每次事件都全量重建富文本并重排滚动区，主线程会被拖垮
（界面卡顿、输入无响应、滚动发涩）——这是「长任务越跑越卡」的根因。

本文件用「轻代理面板」（不建真实控件，渲染方法换计数桩）守护三条节流契约：
  1. _refresh_ai_html 高频调用合并为一次定时渲染（dirty 防抖）
  2. _seg_blocks 逐段结果按内容签名缓存，内容未变的段不重复渲染（消除 O(n²)）
  3. _scroll_bottom 高频调用合并（滚动条只在定时器触发时移动）

注意：断言的是「调用次数」契约而非绝对耗时 —— 耗时随机器波动，次数是确定性的。
（事件流改造后整泡 HTML 拼接被「段 → 区块」映射取代，缓存契约不变，断言面随之
从 _build_ai_html 移到 _seg_blocks。）
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from zhuzhu_Copilot.ui import agent_panel as ap


def _panel():
    """轻代理面板：只带渲染节流用到的状态，渲染方法以桩替代（不建真实控件）。"""
    p = ap.AgentPanel.__new__(ap.AgentPanel)
    p._segments = []
    p._seg_cache = {}
    p._ai_bubble = object()      # 非 None 哨兵：_refresh_ai_html 的前置条件
    p._html_dirty = False
    p._scroll_pending = False
    p._font_scale = lambda: 1.0
    p._bubble_max_width = lambda: 600
    p._ai_turn_max_width = lambda: 600   # 事件流回合铺满内容宽度（截图宽度基准）
    return p


def _wait_until(pred, timeout_ms: int = 3000, step: int = 50) -> bool:
    """等待条件成立（轮询事件循环）。

    全量测试时其它用例可能在事件循环中留下待处理工作，定时器触发会晚于
    400ms；此处按条件轮询（不放大契约、只放宽时序），避免时序敏感误报。
    """
    waited = 0
    while waited < timeout_ms and not pred():
        QTest.qWait(step)
        waited += step
    return pred()


# ---------- 1. 流式刷新合并（dirty 防抖） ----------

def test_refresh_ai_html_coalesces_high_frequency_calls():
    """50 次高频刷新必须合并为 1 次渲染：否则每个 token 全量重建富文本会卡死主线程。"""
    p = _panel()
    p._segments = [{"type": "text", "raw": "x" * 100, "streaming": True}]
    rendered = []
    p._apply_refresh_ai_html = lambda: rendered.append(1)

    for _ in range(50):
        p._refresh_ai_html()
    assert rendered == [], "刷新必须经定时器延迟执行，不得在事件处理中同步重建"
    assert _wait_until(lambda: len(rendered) >= 1), "定时器未在超时内触发渲染"
    QTest.qWait(200)                  # 再等一会，确认没有多余渲染
    assert len(rendered) == 1, f"50 次刷新应合并为 1 次渲染，实际 {len(rendered)}"


def test_refresh_ai_html_dirty_flag_lifecycle():
    """dirty 生命周期：首次调用置位并调度，渲染后复位，之后可再次调度。"""
    p = _panel()
    p._segments = [{"type": "text", "raw": "y" * 10, "streaming": True}]
    p._apply_refresh_ai_html = lambda: setattr(p, "_html_dirty", False)

    p._refresh_ai_html()
    assert p._html_dirty is True
    QTest.qWait(400)
    assert p._html_dirty is False, "渲染完成后必须复位 dirty，否则后续增量永不刷新"


# ---------- 2. 逐段渲染缓存（消除长回复的 O(n²) 重渲染） ----------

def test_seg_blocks_caches_unchanged_segments_by_content():
    """内容未变的段必须命中缓存：包含「对象不同但内容相同」的段（按内容签名而非 id）。"""
    p = _panel()
    renders = []

    def fake_render(seg, i, t, f_main, f_sm, f_op, img_w):
        renders.append((i, t))
        return f"<p>{i}</p>"

    p._render_seg_html = fake_render
    segs = [{"type": "text", "raw": f"line {i}", "streaming": False}
            for i in range(30)]
    first = p._seg_blocks(segs)
    assert len(renders) == 30, "首次构建应逐段渲染"

    renders.clear()
    # 用内容完全相同的新对象重建（模拟历史重载/会话切换）
    rebuilt = [{"type": "text", "raw": f"line {i}", "streaming": False}
               for i in range(30)]
    second = p._seg_blocks(rebuilt)
    assert renders == [], f"内容未变的段不得重渲染，实际重渲染 {len(renders)} 段"
    assert second == first


def test_seg_blocks_rerenders_only_changed_segment():
    """流式追加只改末段 → 只有末段 miss 重渲染，其余段全命中缓存。"""
    p = _panel()
    renders = []
    p._render_seg_html = lambda seg, i, t, a, b, c, d: (renders.append(i), f"<p>{i}</p>")[1]

    segs = [{"type": "text", "raw": "head", "streaming": False},
            {"type": "text", "raw": "tail", "streaming": True}]
    p._seg_blocks(segs)
    assert renders == [0, 1]

    renders.clear()
    segs[1]["raw"] = "tail + more"       # 仅末段内容变化
    p._seg_blocks(segs)
    assert renders == [1], f"只应重渲染变化的段，实际 {renders}"


# ---------- 3. 滚动合并（高频滚动只移动一次滚动条） ----------

def test_scroll_bottom_coalesces_calls():
    """高频滚动请求必须合并：首次仅排「100ms 立即 + 400ms 兜底」两个定时器。"""
    p = _panel()
    scrolled = []
    p._do_scroll_bottom = lambda: scrolled.append(1)

    for _ in range(20):
        p._scroll_bottom()
    assert p._scroll_pending is True, "滚动请求应处于合并等待状态"
    QTest.qWait(700)
    assert len(scrolled) == 2, \
        f"20 次滚动请求应只触发 2 次实际滚动，实际 {len(scrolled)}"


# ---------- 4. 打字指示器文案映射（当前动作 → 状态） ----------

class _FakeLabel:
    """最小 QLabel 替身：只记录文本（转圈行文案断言用，不建真实控件）。"""

    def __init__(self):
        self._t = ""

    def text(self):
        return self._t

    def setText(self, v):
        self._t = v


def test_op_status_text_covers_file_and_command_ops():
    assert ap._op_status_text("write_file") == "正在写入文件"
    assert ap._op_status_text("edit_file") == "正在编辑文件"
    assert ap._op_status_text("search_replace") == "正在编辑文件"
    assert ap._op_status_text("insert_lines") == "正在向文件插入内容"
    assert ap._op_status_text("delete_file") == "正在删除文件"
    assert ap._op_status_text("web_search") == "正在联网搜索"
    assert ap._op_status_text("web_fetch") == "正在抓取网页"
    assert ap._op_status_text("search_code") == "正在检索代码"


def test_op_status_text_covers_requested_actions():
    """用户点名要求同步状态的动作必须各有对应文案。

    搜索文件 / 操作浏览器 / 获取时间 / 轮询命令 / 等待命令 / 回复普通正文。
    """
    # 搜索文件
    assert ap._op_status_text("search_files") == "正在搜索文件"
    assert ap._op_status_text("search_large") == "正在搜索大文件"
    assert ap._op_status_text("explore_project") == "正在扫描项目"
    # 操作浏览器
    assert ap._op_status_text("browser_navigate") == "正在打开网页"
    assert ap._op_status_text("browser_click") == "正在点击页面"
    assert ap._op_status_text("browser_tabs") == "正在读取标签页"
    assert ap._op_status_text("browser_switch_tab") == "正在切换标签页"
    # 获取时间
    assert ap._op_status_text("get_time") == "正在读取时间"
    # 轮询命令 / 等待命令返回
    assert ap._op_status_text("check_command") == "正在轮询命令进度"
    assert ap._op_status_text("run_command") == "正在执行命令"
    # 回复普通正文（非工具动作，独立常量）
    assert ap._OP_STATUS_REPLY == "正在回复正文"


def test_op_status_text_family_fallback():
    """族兜底：未登记精确文案的工具（含未来新增）也得到贴合描述，不生硬回退。"""
    assert ap._op_status_text("browser_hover") == "正在操作浏览器"
    assert ap._op_status_text("list_mcp") == "正在读取清单"
    assert ap._op_status_text("set_mcp") == "正在更新配置"
    assert ap._op_status_text("pause_agent") == "正在处理 Agent 协作"
    assert ap._op_status_text("switch_workflow") == "正在处理工作流"
    assert ap._op_status_text("tts_speak") == "正在处理语音"


def test_op_status_text_fallback_for_unknown_tool():
    assert ap._op_status_text("some_custom_tool") == "正在调用工具 some_custom_tool"


def test_op_status_text_covers_every_registered_tool():
    """扩展性守卫：全部已注册工具都必须有具体文案（精确或族兜底）。

    新增工具若忘记登记、且族兜底也没覆盖，本用例会失败并点名该工具，
    保证文案表跟得上工具集（避免界面上出现生硬的「正在调用工具 xxx」）。
    """
    from zhuzhu_Copilot.core import agent_tools
    fallback = [t["function"]["name"] for t in agent_tools.TOOLS
                if ap._op_status_text(t["function"]["name"]).startswith("正在调用工具")]
    assert fallback == [], f"以下工具未登记状态文案（族兜底也未覆盖）：{fallback}"


# ---------- 5. 转圈行文案同步点（状态必须随动作实时切换） ----------

def _panel_with_spinner():
    """轻代理面板 + 转圈行替身：用于断言 _on_status/_on_delta 是否同步文案。"""
    p = _panel()
    p._spinner_lbl = _FakeLabel()
    p._segments = []
    p._last_activity = 0.0
    p._stop_send_spin = lambda: None
    p._ensure_ai_bubble = lambda: None
    p._refresh_ai_html = lambda: None
    p._scroll_bottom = lambda: None
    p._finish_thinking = lambda: None
    return p


def test_pending_tool_status_syncs_spinner_text():
    """「待执行工具:」阶段即同步文案：预处理（技能拦截/参数校验/确认弹窗）可能耗时，
    若不在此刻更新，转圈行会停在「已思考 N 秒」造成状态不同步。"""
    p = _panel_with_spinner()
    p._on_status("待执行工具: search_files")
    assert p._spinner_lbl.text() == "正在搜索文件"


def test_executing_status_syncs_spinner_text():
    p = _panel_with_spinner()
    p._on_status("正在执行: check_command")
    assert p._spinner_lbl.text() == "正在轮询命令进度"
    p._on_status("正在执行: browser_click")
    assert p._spinner_lbl.text() == "正在点击页面"


def test_reply_body_syncs_spinner_text():
    """回复普通正文期间转圈行显示「正在回复正文」，而非停在「已思考 N 秒」。"""
    p = _panel_with_spinner()
    p._ensure_text_segment = lambda: None
    p._segments = [{"type": "text", "raw": "", "streaming": True}]
    p._on_delta("你好")
    assert p._spinner_lbl.text() == "正在回复正文"


# ---------- 4. 长回合增量重排 / 内容节拍自适应 ----------
# 背景：「区块一多就特别卡」的本质是「每 tick 的 O(区块数) 工作 × 极高频节拍」。
# 下面几条契约分别锁住：每 tick 只重测变化的那一块、什么都没变时不作废高度缓存、
# 长回合放宽内容节拍（短回合不受影响）、非流式区块的浮现层零测量。

from PyQt6.QtGui import QColor, QIcon, QPixmap                       # noqa: E402
from PyQt6.QtWidgets import QVBoxLayout, QWidget                     # noqa: E402

from zhuzhu_Copilot.ui import agent_chat_bubbles as cb               # noqa: E402

_STYLE = cb.ChatStyle(
    card="#1F232C", border="#272C36", border_soft="#333A46", dash="#333A46",
    text="#F3F5F9", text_dim="#9BA3B0", accent="#2F52D8", muted="#9BA3B0",
    icon_shell="#272C36", icon_color="#2F52D8", tag_bg="#2A3040", tag_fg="#9BA3B0",
    user_bg="#2F52D8", user_fg="#FFFFFF", cmd_fg="#F3F5F9", ok_fg="#5B82F6",
    hover="#272C36", panel="#181B21", bg="#101216",
)


def _icon(_kind, size, color):
    pm = QPixmap(size, size)
    pm.fill(QColor(color))
    return QIcon(pm)


def _long_turn(n: int = 30):
    """一条含 n 个区块的真实回合（离屏宿主 → 真实宽度与真实布局）"""
    host = QWidget()
    lay = QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    turn = cb.ChatTurn(_STYLE, _icon)
    lay.addWidget(turn)
    lay.addStretch(1)
    host.resize(760, 600)
    host.show()
    specs = []
    for i in range(n):
        tail = (i == n - 1)
        if i % 4 == 0:
            specs.append((cb.KIND_THINK, {"tag": "EXEC", "body": f"<div>思考 {i}</div>"},
                          ("think", i)))
        elif i % 4 == 1:
            specs.append((cb.KIND_TOOL, {"name": "read_file", "meta": "", "params": {}},
                          ("tool", i)))
        elif i % 4 == 2:
            specs.append((cb.KIND_CMD, {"label": "命令", "cmd": "ls", "out": "a"},
                          ("cmd", i)))
        else:
            specs.append((cb.KIND_STREAM, {"html": f"<div>正文 {i}</div>", "proc": not tail},
                          ("stream", i)))
    turn.render(specs, live=True)
    for _ in range(3):
        _app.processEvents()
    return host, turn, specs


def test_long_turn_streaming_tick_measures_only_changed_block(monkeypatch):
    """长回合流式刷新只重测内容变化的那一块：否则每 tick 都是 O(区块数) 全量测量。"""
    host, turn, specs = _long_turn(32)      # 末块为正文块（便于模拟流式增长）
    try:
        calls = []
        orig = cb.ChatTurn._measure_block

        def spy(self, ref, inner, monotonic):
            calls.append(ref)
            return orig(self, ref, inner, monotonic)

        monkeypatch.setattr(cb.ChatTurn, "_measure_block", spy)
        kind, payload, _sig = specs[-1]
        grown = list(specs)
        grown[-1] = (kind, dict(payload, html=payload["html"] + "<div>新增一行</div>"),
                     ("stream", "grown"))
        turn.render(grown, live=True)
        assert len(calls) <= 1, f"流式刷新只应重测变化的那一块，实际重测 {len(calls)} 块"
    finally:
        host.hide()
        host.deleteLater()
        _app.processEvents()


def test_unchanged_tick_keeps_turn_height_cache():
    """什么都没变的 tick 不得作废回合级高度缓存（否则外层每次询问都要全量重测）。"""
    host, turn, specs = _long_turn(24)
    try:
        turn.render(specs, live=True)          # 同样内容再渲染一次
        assert turn._hfw_cache is not None, "回合高度缓存被无谓作废"
        assert turn._hfw_cache[0] == turn.width()
    finally:
        host.hide()
        host.deleteLater()
        _app.processEvents()


def test_long_turn_relaxes_stream_tick(monkeypatch):
    """长回合（区块多）必须放宽内容节拍：4ms × 大 N 等于把主线程排满。"""
    delays = []
    monkeypatch.setattr(ap.QTimer, "singleShot",
                        staticmethod(lambda ms, fn: delays.append(ms)))
    p = _panel()
    p._ai_bubble = type("T", (), {"_items": [object()] * (ap._STREAM_TICK_BLOCKS + 1)})()
    p._segments = [{"type": "text", "raw": "x" * 10, "streaming": True}]
    p._refresh_ai_html()
    assert delays and delays[-1] == ap._STREAM_TICK_LONG_MS


def test_short_turn_keeps_fast_stream_tick(monkeypatch):
    """短回合保持快节拍：落字响应不受长任务优化影响。"""
    delays = []
    monkeypatch.setattr(ap.QTimer, "singleShot",
                        staticmethod(lambda ms, fn: delays.append(ms)))
    p = _panel()
    p._ai_bubble = type("T", (), {"_items": [object()]})()
    p._segments = [{"type": "text", "raw": "x" * 10, "streaming": True}]
    p._refresh_ai_html()
    assert delays and delays[-1] == ap._STREAM_TICK_MS


def test_stream_tick_tracks_measured_frame_cost(monkeypatch):
    """单帧成本自适应：长正文的单帧成本随长度增长（探针实测 20k 字 ~38ms 均值/67ms 峰值）。

    固定 4ms 节拍在长文下等于把主线程排满（占用率无上界）—— 契约是「节拍 ≥ 实测成本 /
    目标占空比」，即刷新占用率有上界；成本极高时封顶保底更新频率。
    """
    delays = []
    monkeypatch.setattr(ap.QTimer, "singleShot",
                        staticmethod(lambda ms, fn: delays.append(ms)))
    p = _panel()
    p._ai_bubble = type("T", (), {"_items": [object()]})()
    p._segments = [{"type": "text", "raw": "x" * 10, "streaming": True}]

    cost = 40.0
    p._refresh_cost_ema = cost
    p._refresh_ai_html()
    assert delays[-1] == int(cost / ap._STREAM_DUTY_TARGET), \
        f"节拍未随单帧成本放宽：{delays[-1]}ms（单帧 {cost}ms）"
    assert delays[-1] * ap._STREAM_DUTY_TARGET >= cost, "占用率超过目标上限"

    delays.clear()
    p._html_dirty = False
    p._refresh_cost_ema = 10_000.0            # 极端长文：单帧成本远超上限
    p._refresh_ai_html()
    assert delays[-1] == ap._STREAM_TICK_MAX_MS, "节拍必须有上限（保底更新频率）"


def test_frame_cost_ema_is_recorded_and_decays():
    """成本估计：实测记录（节拍唯一依据，不做长度公式猜测）+ 渐进回落（不长期偏大）。"""
    p = _panel()
    p._render_ai_frame = lambda *a, **kw: None
    p._sync_bubble_heights = lambda *a, **kw: None
    p._refresh_cost_ema = 0.0
    p._apply_refresh_ai_html()
    assert p._refresh_cost_ema > 0.0, "单帧成本必须被实测记录"
    assert p._refresh_cost_ema < 5.0, "桩渲染的耗时不应被夸大"

    p._refresh_cost_ema = 100.0
    p._apply_refresh_ai_html()
    assert abs(p._refresh_cost_ema - 100.0 * ap._STREAM_COST_DECAY) < 1e-3, \
        "成本估计必须渐进回落（否则短回合也长期按长文节拍刷新）"


def test_inactive_emerge_band_does_no_measurement():
    """非流式区块的浮现层在几何变化时不得做文本测量：长会话上百个块各测一次即卡顿。"""
    block = cb.StreamBlock(_STYLE)
    block.set_html("<div>x</div>")
    band = block._emerge
    calls = []
    band._area = lambda: (calls.append(1), block._emerge_area())[1]
    block._emerge_touch(by_geometry=True)          # 模拟布局/缩放重排
    assert calls == [], "非流式块不得因几何变化做正文测量"
    block.set_live(True)
    block._emerge_touch(by_geometry=True)
    assert calls, "流式期间几何变化仍需同步浮现层"


# ---------- 5. 长思考：折叠态每 tick 成本恒定（气泡抖动 / 指示器下移的根因） ----------

def _think_host():
    """离屏宿主 + 单个思考气泡（真实宽度与几何）"""
    host = QWidget()
    lay = QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    tb = cb.ThinkBubble(_STYLE, _icon)
    lay.addWidget(tb)
    host.resize(760, 800)
    host.show()
    for _ in range(3):
        _app.processEvents()
    return host, tb


def test_long_thinking_stops_repaving_once_preview_is_saturated():
    """折叠态前缀已饱和后，继续流式追加**不得**再重排富文本。

    历史缺陷：思考正文全文每次都铺进 QLabel，长推理下每 tick 都要重排整篇文档
    （实测单 tick 由 8ms 涨到 60ms+），主线程被排满 → 气泡与打字指示器剧烈抖动、
    消息区上下反复出现大片空白。判据用「内容版本 + 标签文字 + 块高」三项都不变。
    """
    host, tb = _think_host()
    try:
        tb.set_content("PLANNING", "推" * (cb.THINK_PREVIEW_CHARS + 200))
        _app.processEvents()
        ver, text, h = tb._content_ver, tb._body.text(), tb.height()
        assert ver > 0 and text

        for extra in (400, 900, 2000, 5000):     # 继续流式追加
            tb.set_content("PLANNING", "推" * (cb.THINK_PREVIEW_CHARS + extra))
            _app.processEvents()
            assert tb._content_ver == ver, \
                "折叠态可见正文没变却仍走了重排（每 tick 重排整篇文档 → 卡顿抖动）"
            assert tb._body.text() == text
        assert tb.height() == h
    finally:
        host.hide()
        host.deleteLater()


def test_folded_long_thinking_paves_preview_then_full_on_expand():
    """折叠态只铺前缀（成本有界），展开必须铺**全文**（不得仍是残文）。"""
    host, tb = _think_host()
    try:
        full = "推" * (cb.THINK_PREVIEW_CHARS * 3)
        tb.set_content("PLANNING", full)
        _app.processEvents()

        assert tb._fold_full == full, "全文必须留在块内，供展开时使用"
        assert len(tb._body.text()) <= cb.THINK_PREVIEW_CHARS + 1, "折叠态铺了全文（每 tick 重排）"
        assert not tb._fold_btn.isHidden(), "长思考必须给出展开入口"

        tb._fold_btn.click()
        _app.processEvents()
        assert tb._folded is False
        assert tb._body.text() == full, "展开后仍是前缀 = 用户报的「展开被截断」"
        assert tb._body.minimumHeight() > tb._fold_limit_h(), "展开态必须钉到全文高度"

        tb._fold_btn.click()
        _app.processEvents()
        assert tb._folded is True
        assert len(tb._body.text()) <= cb.THINK_PREVIEW_CHARS + 1, "收起后应回到有界前缀"
    finally:
        host.hide()
        host.deleteLater()


def test_think_preview_does_not_shrink_the_fold_decision():
    """前缀本身仍必须被判定为可折叠：否则展开入口会消失、长文被压缩展示。"""
    host, tb = _think_host()
    try:
        tb.set_content("PLANNING", "推" * (cb.THINK_PREVIEW_CHARS + 3000))
        _app.processEvents()
        assert tb._fold_foldable(), "前缀必须仍超过折叠行数上限"
        assert not tb._fold_btn.isHidden()
    finally:
        host.hide()
        host.deleteLater()


def test_turn_height_does_not_stick_after_width_change():
    """宽度变化后不得沿用「只增不减」的旧高度（正文下方会留大片空白）。

    长任务里滚动条会随内容增长而出现：回合变窄 → 正文换行更多 → 高度变大；
    滚动条消失/面板变宽后，若仍把窄宽度时的偏高值当上界，正文上下就会持续留白。
    """
    host = QWidget()
    lay = QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    turn = cb.ChatTurn(_STYLE, _icon)
    lay.addWidget(turn)
    host.resize(760, 400)
    host.show()
    try:
        text = "这是一段会随宽度换行的正文。" * 8
        turn.render([(cb.KIND_STREAM, {"html": f"<div>{text}</div>", "proc": False},
                      ("s", len(text)))], live=True)
        _app.processEvents()
        wide = turn.relayout_heights(700, monotonic=True)
        narrow = turn.relayout_heights(430, monotonic=True)
        assert narrow > wide, "前提校验：变窄后换行更多，高度应变大"
        back = turn.relayout_heights(700, monotonic=True)
        assert back == wide, \
            f"宽度回到 700 后仍停在窄宽度时的偏高值（{back} vs {wide}）→ 正文下方留白"
    finally:
        host.hide()
        host.deleteLater()


