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

from winapp_migrator.ui import agent_panel as ap


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
    from winapp_migrator.core import agent_tools
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
