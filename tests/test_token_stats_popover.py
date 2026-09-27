"""token / 上下文统计浮层回归测试。

覆盖三层：
1. 展示层纯函数（千分位/百分比格式化）——面板文案口径；
2. 浮层控件：由引擎快照渲染（分段着色、阈值刻线、缓存命中率条、空态、幂等、主题跟随），
   并真实 show + processEvents 走一遍自绘 paintEvent（自绘代码最易只在运行时炸）；
3. 面板接入：引擎解析口径（_token_stats）、浮层定位（按钮下方/空间不足上翻/边距钳制）、
   失焦守卫（点击浮层外收起、点浮层内不收起、Esc 仅在活动窗口时消费）。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication, QComboBox, QDialog, QPushButton, QWidget

_app = QApplication.instance() or QApplication([])

from winapp_migrator.core import agent_engine
from winapp_migrator.ui import agent_panel as ap


class _FakeLLM:
    model = "popover-test-model"


def _snapshot(used=4000, hit=3000, miss=1000, completion=500):
    """用真实引擎产出快照：既测浮层渲染，也锁住引擎与 UI 之间的字段契约"""
    eng = agent_engine.AgentEngine(_FakeLLM(), text_only=True)
    eng._accum_usage({"prompt_tokens": used, "completion_tokens": completion},
                     {"hit": hit, "miss": miss})
    return eng.context_stats()


def _wait(ms: int):
    """推进事件循环 ms 毫秒：弹出/收起动画由定时器驱动，断言需等其结束"""
    from PyQt6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(int(ms), loop.quit)
    loop.exec()


def _wait_until(pred, timeout_ms: int = 2000, step_ms: int = 20) -> bool:
    """轮询等待条件成立（动画进度取决于真实时钟与平台，固定 sleep 会变成竞态）"""
    from PyQt6.QtCore import QEventLoop, QTimer
    waited = 0
    while waited < timeout_ms:
        if pred():
            return True
        loop = QEventLoop()
        QTimer.singleShot(step_ms, loop.quit)
        loop.exec()
        waited += step_ms
    return bool(pred())


class _PanelStub(ap.AgentPanel):
    """只搭 QDialog 壳的轻代理：复用真实几何/事件逻辑，不构建整个面板。

    真实面板的 show/resize/close 钩子会同步布局、子窗口与引擎（依赖完整控件树），
    在本用例中与定位逻辑无关，故置为无操作，避免测试去构造整棵 UI。"""

    def __init__(self, w=1074, h=692):
        QDialog.__init__(self)
        self._sess = {}
        self._session_id = "s1"
        self._engine = None
        self.resize(w, h)

    def showEvent(self, e):
        pass

    def resizeEvent(self, e):
        pass

    def closeEvent(self, e):
        e.accept()


# ---------- 1. 展示层纯函数 ----------

def test_format_helpers():
    assert ap._fmt_tokens(1234567) == "1,234,567"
    assert ap._fmt_tokens(None) == "0"
    assert ap._fmt_pct(0.0773) == "7.7%"
    assert ap._fmt_pct(0.0005) == "<0.1%"      # 非零极小值不得显示成 0%
    assert ap._fmt_pct(0) == "0.0%"
    assert ap._fmt_pct(None) == "0%"
    assert ap._clamp01(3) == 1.0 and ap._clamp01(-1) == 0.0 and ap._clamp01("x") == 0.0


# ---------- 2. 浮层控件 ----------

def test_popover_renders_upstream_snapshot():
    """上游数据：占用条按 缓存命中/未命中/输出 三段着色（统计口径=输入+输出），阈值画两条刻线，命中率条按比例填充

    占比基数 = 可用输入预算（与三档阈值同口径），不是裸窗口——用裸窗口会把压缩刻线
    画在 0.75×预算/窗口（1M 下 79.4%）这类非整数位置，用户看到的百分比与"到 80% 才
    压缩"的设定对不上，误判为提前压缩。"""
    s = _snapshot(used=4000, hit=3000, miss=1000)
    pop = ap._TokenStatsPopover()
    pop.set_stats(s)
    base = s["budget"]
    assert pop._src_chip.text() == "上游真实用量"
    assert pop._usage.text() == f"{ap._fmt_tokens(4500)} / {ap._fmt_tokens(base)}"
    assert pop._ratio.text() == ap._fmt_pct(4500 / base)
    segs = pop._bar._segments
    assert len(segs) == 3, "输入+输出应拆成三段不同颜色的色段（命中/未命中/输出）"
    assert abs(segs[0][0] - 3000 / base) < 1e-9
    assert abs(segs[1][0] - 1000 / base) < 1e-9
    assert abs(segs[2][0] - 500 / base) < 1e-9
    assert segs[0][1] != segs[1][1] and segs[2][1] != segs[0][1] \
        and segs[2][1] != segs[1][1], "三段颜色必须互不相同（彩色标记）"
    ticks = pop._bar._ticks
    assert [t[2] for t in ticks] == [False, True], "虚线=压缩阈值、实线=硬上限"
    assert abs(ticks[0][0] - s["thresholds"]["compress"] / base) < 1e-9
    # 压缩刻线正好落在设定比例上（默认 75% / 1M 模式 80%），与阈值口径完全一致
    assert abs(ticks[0][0] - s["compress_ratio"]) < 1e-9
    assert pop._cache_bar._segments[0][0] == 0.75
    assert pop._vals["last_in"].text() == "4,000"
    assert pop._vals["last_out"].text() == "500"
    # 累计消耗行用紧凑单位保证一行放得下，精确值在 tooltip（悬浮可读全文）
    assert pop._vals["cum"].text().startswith("4.5k")
    assert "4,500" in pop._vals["cum"].toolTip()
    pop.close()


def test_popover_paint_smoke():
    """真实 show + 事件循环：分段条/刻线/圆角裁剪的绘制路径必须无异常"""
    pop = ap._TokenStatsPopover()
    pop.set_stats(_snapshot())
    pop.resize(pop.PREFERRED_WIDTH, pop.sizeHint().height())
    pop.show()
    for _ in range(4):
        _app.processEvents()
    assert pop.isVisible() and pop.sizeHint().height() > 0
    pop.hide()


def test_popover_estimate_source_uses_single_faded_segment():
    """本地估算无缓存拆分信息：单段且颜色与上游命中段不同（上游/估算一眼可辨）"""
    eng = agent_engine.AgentEngine(_FakeLLM(), text_only=True)
    eng._messages = [{"role": "user", "content": "估算用上下文" * 50}]
    s = eng.context_stats()
    assert s["source"] == "estimate"
    pop = ap._TokenStatsPopover()
    pop.set_stats(s)
    assert pop._src_chip.text() == "本地估算"
    assert len(pop._bar._segments) == 1
    assert pop._cache_bar._segments == []          # 无上游数据 → 命中率条不填充
    assert "本地估算" in pop._hint.text()
    pop.close()


def test_popover_empty_state_and_idempotency():
    """空态可渲染；同一份数据重复注入不重排（400ms 轮询下的空转防护）"""
    pop = ap._TokenStatsPopover()
    pop.set_stats(None)
    assert pop._usage.text() == "—" and pop._src_chip.text() == "无数据"
    assert pop._bar._segments == [] and pop._bar._ticks == []
    s = _snapshot()
    pop.set_stats(s)
    sig = pop._sig
    pop.set_stats(dict(s))
    assert pop._sig == sig, "数据未变时不应重新渲染"
    pop.close()


def test_popover_marks_near_threshold_states_with_restrained_accent():
    """越过压缩阈值/硬上限时只用极小面积语义色提示（百分比文字 + 刻线），
    进度条主体仍是深蓝/灰（不破坏黑白灰+深蓝的基调）"""
    eng = agent_engine.AgentEngine(_FakeLLM(), text_only=True)
    win = eng._ctx_window
    pop = ap._TokenStatsPopover()

    pop.set_stats(_snapshot(used=int(win * 0.5), hit=int(win * 0.5), miss=0))
    assert ap.WARN.lower() not in pop._ratio.styleSheet().lower()
    assert ap.ERR.lower() not in pop._ratio.styleSheet().lower()

    pop.set_stats(_snapshot(used=int(win * 0.80), hit=int(win * 0.80), miss=0))
    assert ap.WARN.lower() in pop._ratio.styleSheet().lower()
    assert pop._bar._segments[0][1].name().lower() == ap.ACCENT.lower()

    pop.set_stats(_snapshot(used=int(win * 0.96), hit=int(win * 0.96), miss=0))
    assert ap.ERR.lower() in pop._ratio.styleSheet().lower()
    assert pop._bar._segments[0][1].name().lower() == ap.ACCENT.lower()
    pop.close()


def test_popover_shows_window_source_budget_and_compaction():
    """上限来源（含可用输入预算）与压缩事件必须可见——这是"上限从哪来/压过几次"的唯一出口"""
    s = _snapshot(used=4000, hit=3000, miss=1000)
    s["window_source"] = "upstream"
    s["budget"] = s["window"] - s["max_output"]
    s["compaction"] = {"count": 2, "last_at": 1.0, "last_merged": 5, "last_saved": 12345}
    pop = ap._TokenStatsPopover()
    pop.set_stats(s)
    src_row = pop._vals["window_src"].text()
    assert "上游声明" in src_row
    assert ap._fmt_tokens_compact(s["budget"]) in src_row
    assert "…" not in src_row, "预算数字不应被省略号截断"
    assert "上游服务商" in pop._vals["window_src"].toolTip(), "来源全称应可在悬浮 tooltip 中看到"
    assert pop._vals["compaction"].text().startswith("2 次")
    assert ap._fmt_tokens(12345) in pop._vals["compaction"].text()

    # 1M 开关来源与"尚未压缩"空态
    s["window_source"] = "1m"
    s["compaction"] = {"count": 0, "last_at": 0.0, "last_merged": 0, "last_saved": 0}
    pop.set_stats(s)
    assert "1M 开关" in pop._vals["window_src"].text()
    assert pop._vals["compaction"].text() == "尚未压缩"
    pop.close()


def test_popover_ratio_color_has_three_tiers():
    """分层阈值配色：接近（强调色）→ 越过压缩阈值（预警色）→ 越过硬上限（危险色）

    阈值与占用同基数（可用输入预算），故人工改阈值时也必须按 budget 构造，
    否则口径不一致会得到无意义的判定。"""
    eng = agent_engine.AgentEngine(_FakeLLM(), text_only=True)
    base = eng._input_budget()
    pop = ap._TokenStatsPopover()

    s = _snapshot(used=int(base * 0.5), hit=int(base * 0.5), miss=0)
    s["warn"] = False
    pop.set_stats(s)
    assert pop._ratio.styleSheet().lower().find(ap.TEXT_DIM.lower()) >= 0

    s["warn"] = True                                    # 越过预警线但未到压缩线
    s["thresholds"]["compress"] = int(base * 0.9)
    s["thresholds"]["ceiling"] = int(base * 0.99)
    pop.set_stats(s)
    assert ap.ACCENT_HOVER.lower() in pop._ratio.styleSheet().lower()

    s["used"] = int(base * 0.95)                         # 越过压缩阈值
    s["warn"] = True
    pop.set_stats(s)
    assert ap.WARN.lower() in pop._ratio.styleSheet().lower()

    s["used"] = int(base * 0.995)                        # 越过硬上限
    pop.set_stats(s)
    assert ap.ERR.lower() in pop._ratio.styleSheet().lower()
    pop.close()


def test_popover_theme_aware_stylesheet():
    """主题切换后 apply_theme 必须用新色板重生成样式（否则浅色下仍是深色卡片）"""
    pop = ap._TokenStatsPopover()
    old = ap.PANEL
    try:
        ap.PANEL = "#ABCDEF"
        pop.apply_theme()
        assert "#ABCDEF" in pop.styleSheet()
    finally:
        ap.PANEL = old
        pop.apply_theme()
    assert "#ABCDEF" not in pop.styleSheet()
    pop.close()


# ---------- 3. 面板接入 ----------

def test_token_stats_uses_active_session_engine():
    """统计口径与 token_label 一致：优先会话内引擎，回退 self._engine；
    无引擎时仍返回界面侧字段（工作流/模型），仅数值为空态"""
    p = _PanelStub()
    p._sess["s1"] = {"workflow": "dataflow"}
    bare = p._token_stats()
    assert not bare.get("window")            # 无引擎 → 无窗口/无用量
    assert bare["workflow"] == "dataflow"    # 界面侧信息照常携带
    p._engine = object()                     # 不支持统计的引擎 → 同样是空态而非报错
    assert not p._token_stats().get("window")
    eng = agent_engine.AgentEngine(_FakeLLM(), text_only=True)
    eng._accum_usage({"prompt_tokens": 1234, "completion_tokens": 6})
    p._sess["s1"] = {"engine": eng}
    assert p._token_stats()["used"] == 1234 + 6   # 标准口径：输入+输出
    p._session_id = "s2"                                   # 切到无引擎会话 → 数值空态
    assert not p._token_stats().get("window")


def test_popover_carries_workflow_and_model_merged_from_status_button():
    """原顶栏「状态」按钮的信息（工作流 / 模型）已并入浮层：无论有无引擎都可见，
    有引擎时数值区自动补全（这是"删除旁边按钮"后必须保留的信息）"""
    p = _PanelStub()
    p._sess["s1"] = {"workflow": "dataflow"}
    combo = QComboBox(p)
    combo.addItem("deepseek-chat")
    p.model_combo = combo
    stats = p._token_stats()
    assert stats["workflow"] == "dataflow"
    assert stats["model"] == "deepseek-chat"        # 界面当前选择优先于引擎记录

    pop = ap._TokenStatsPopover()
    pop.set_stats(stats)
    assert pop._vals["workflow"].text() == "dataflow"
    assert pop._vals["model"].text() == "deepseek-chat"
    assert pop._usage.text() == "—"                 # 无引擎：数值空态，信息行照常显示

    eng = agent_engine.AgentEngine(_FakeLLM(), text_only=True)
    eng._accum_usage({"prompt_tokens": 800, "completion_tokens": 20})
    p._sess["s1"] = {"engine": eng, "workflow": "dataflow"}
    pop.set_stats(p._token_stats())
    assert pop._usage.text().startswith("820 / ")
    assert pop._vals["workflow"].text() == "dataflow"
    pop.close()


def test_popover_geometry_below_button_then_flips_above():
    """定位规则：默认贴按钮下方右对齐；下方空间不足上翻；横向超出则钳进边距"""
    p = _PanelStub(1074, 692)
    btn = QPushButton(p)
    btn.setGeometry(1000, 10, 34, 34)
    p.token_btn = btn
    pop = ap._TokenStatsPopover(p)
    pop.resize(pop.PREFERRED_WIDTH, pop.sizeHint().height())
    geo = p._token_pop_geometry(pop)
    assert geo.y() == 10 + 34 + ap.SPACING_SM
    assert geo.x() + geo.width() <= p.width() - ap.SPACING_SM   # 右对齐且不出界
    assert geo.x() > 0

    p.resize(400, 120)                                     # 下方放不下 → 上翻并钳制在边距内
    geo2 = p._token_pop_geometry(pop)
    assert geo2.y() == ap.SPACING_SM
    assert geo2.x() == max(ap.SPACING_SM, p.width() - geo2.width() - ap.SPACING_SM)
    assert geo2.right() <= p.width() - ap.SPACING_SM


def test_popover_geometry_without_button_falls_back_to_top_left():
    """自定义 UI/UX 包未提供入口按钮时，浮层仍要落在面板内（左上角）而非飞出窗口"""
    p = _PanelStub(600, 400)
    pop = ap._TokenStatsPopover(p)
    pop.resize(pop.PREFERRED_WIDTH, pop.sizeHint().height())
    geo = p._token_pop_geometry(pop)
    assert (geo.x(), geo.y()) == (ap.SPACING_SM, ap.SPACING_SM)


def test_dismiss_filter_closes_on_outside_click_only():
    """点浮层外收起；点浮层内或入口按钮不收起（避免"关了又开"与误关）"""
    p = _PanelStub(600, 400)
    btn = QPushButton(p)
    # 用顶层浮层：可见性语义与真实一致（子控件在父窗口未显示时 isVisible 恒为 False）
    pop = ap._TokenStatsPopover()
    pop.show()
    other = QWidget(p)
    f = ap._PopoverDismissFilter(pop, btn, p)
    press = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(1, 1), QPointF(1, 1),
                        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                        Qt.KeyboardModifier.NoModifier)
    assert pop.isVisible()
    assert f.eventFilter(btn, press) is False              # 入口按钮：交给 clicked 切换
    assert pop._closing is False
    assert f.eventFilter(pop, press) is False              # 浮层内部：不收起
    assert pop._closing is False
    assert f.eventFilter(other, press) is False            # 浮层外：收起，但不吞事件
    assert pop._closing is True
    pop.hide()


def test_open_refresh_close_lifecycle_on_panel():
    """面板级完整闭环：点击开→随轮询刷新→跟随窗口尺寸→收起→重建清理。

    这条链路把引擎数据、浮层渲染、定位与全局事件守卫串起来，是"按钮点击后弹出面板"
    的主路径回归。"""
    p = _PanelStub(1074, 692)
    p.show()
    btn = QPushButton(p)
    btn.setGeometry(1000, 10, 34, 34)
    p.token_btn = btn
    eng = agent_engine.AgentEngine(_FakeLLM(), text_only=True)
    eng._accum_usage({"prompt_tokens": 4000, "completion_tokens": 500},
                     {"hit": 3000, "miss": 1000})
    p._sess["s1"] = {"engine": eng}

    p.toggle_token_stats()                                  # 打开
    assert p._token_pop_open is True and p._token_dismiss_on is True
    pop = p._token_pop
    assert pop.isVisible() and pop._src_chip.text() == "上游真实用量"
    # 渐变丝滑弹出的两个要素：透明度效果已挂载 + 自下方 SLIDE 位移到目标位置
    assert pop.graphicsEffect() is not None, "淡入需要 QGraphicsOpacityEffect"
    assert pop.animation_running() is True, "弹出应启动动画（透明度+位移）"
    target_y = p._token_pop_geometry(pop).y()
    assert pop.geometry().y() == target_y + pop.SLIDE, "起始位置应在目标位置下方 SLIDE"
    assert _wait_until(lambda: not pop.animation_running()), "弹出动画应在超时前结束"
    assert pop.geometry().y() == target_y

    eng._accum_usage({"prompt_tokens": 8000, "completion_tokens": 20},
                     {"hit": 3000, "miss": 5000})
    p._refresh_token_pop()                                  # 400ms 轮询路径
    assert pop._vals["last_in"].text() == "8,000"
    base = eng.context_stats()["budget"]                    # 占比基数=可用输入预算
    assert [round(seg[0], 6) for seg in pop._bar._segments] == \
        [round(3000 / base, 6), round(5000 / base, 6), round(20 / base, 6)]

    p.resize(500, 300)                                      # 窗口变化后位置跟随且不出界
    p._refresh_token_pop()
    geo = pop.geometry()
    assert geo.y() == ap.SPACING_SM and geo.right() <= 500 - ap.SPACING_SM

    p.toggle_token_stats()                                  # 再次点击收起
    assert p._token_pop_open is False and p._token_dismiss_on is False
    assert _wait_until(lambda: not pop.isVisible()), "收起动画结束后浮层应隐藏"

    p.open_token_stats()                                    # 可重复打开（控件复用而非重建）
    assert _wait_until(lambda: pop.isVisible() and not pop.animation_running())
    assert p._token_pop is pop
    p._reset_token_pop()                                    # 主题重建：丢弃浮层并解绑守卫
    assert p.__dict__.get("_token_pop") is None
    assert p._token_dismiss_on is False
    p.close()


def test_dismiss_filter_escape_only_consumed_when_window_active():
    """Esc：浮层所在窗口为活动窗口时消费（返回 True）；否则放行给模态弹窗"""

    class _Win:
        def __init__(self, active):
            self._a = active

        def isActiveWindow(self):
            return self._a

    class _Pop:
        def __init__(self, active=True, visible=True):
            self.closed = 0
            self._w = _Win(active)
            self._v = visible

        def isVisible(self):
            return self._v

        def window(self):
            return self._w

        def close_animated(self):
            self.closed += 1

    esc = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
                    Qt.KeyboardModifier.NoModifier)
    pop = _Pop(active=True)
    assert ap._PopoverDismissFilter(pop, None).eventFilter(None, esc) is True
    assert pop.closed == 1
    pop2 = _Pop(active=False)
    assert ap._PopoverDismissFilter(pop2, None).eventFilter(None, esc) is False
    assert pop2.closed == 0
    pop3 = _Pop(visible=False)
    assert ap._PopoverDismissFilter(pop3, None).eventFilter(None, esc) is False
    assert pop3.closed == 0


# ---------- 排版：任何字体/宽度下都不得挤压、遮挡或裁切 ----------

def _one_m_stats():
    """1M 上限下最"宽"的数据（9 位上限 + 长模型名 + 大额累计），最容易挤爆卡片"""
    win, reserve = 1048576, 32768
    budget = win - reserve
    prompt, completion = 812646, 12345
    used = prompt + completion      # 标准口径：占用 = 输入 + 输出
    return {
        "used": used, "source": "upstream", "window": win, "budget": budget,
        "ratio": used / win, "warn": True, "window_source": "1m",
        "max_output": reserve,
        "thresholds": {"warn": int(budget * 0.7), "compress": int(budget * 0.8),
                       "ceiling": int(budget * 0.95), "recent": int(budget * 0.5)},
        "last": {"prompt": prompt, "completion": completion,
                 "cache_hit": prompt - 12646, "cache_miss": 12646, "at": 0},
        "cumulative": {"prompt": 1234567, "completion": 234567, "total": 1469134,
                       "cache_hit": 1200000, "cache_miss": 34567},
        "cache_rate": 0.972, "messages": 1287,
        "model": "deepseek-v4.1-flash-0126-preview-long-name",
        "workflow": "sansheng_liubu_workflow",
        "compaction": {"count": 3, "last_at": 1.0, "last_merged": 9,
                       "last_saved": 152340},
    }


def _place(pop, panel, stats):
    """按面板算法落位并激活布局（真实尺寸 → 才谈得上"是否溢出"）"""
    pop.set_stats(stats)
    pop.apply_theme()
    w, h = panel._token_pop_size(pop)
    pop.resize(w, h)
    pop.layout().activate()
    for _ in range(3):
        _app.processEvents()
    return w, h


def _overflow_items(pop):
    """返回所有"文字比控件宽"的项（图例逐行、明细逐行），空 = 无挤压"""
    import re
    bad = []

    def chk(name, lbl, text=None):
        txt = lbl.text() if text is None else text
        need = lbl.fontMetrics().horizontalAdvance(txt)
        if need > lbl.width() + 1:
            bad.append((name, need - lbl.width(), txt))

    for name in ("_usage", "_ratio", "_hint"):
        chk(name, getattr(pop, name))
    # 图例是"富文本 + 每条一行"：必须按行校验（整体测量会把多行当一行误判）
    plain = re.sub(r"<[^>]+>", "", pop._ticks_lbl.text())
    for i, line in enumerate(plain.split("●")):
        if line.strip():
            chk(f"ticks行{i}", pop._ticks_lbl, line)
    # 自动换行标签：高度必须容得下换行后的行数（否则底部被裁）
    for name in ("_cache_lbl", "_hint"):
        lbl = getattr(pop, name)
        if lbl.height() + 1 < lbl.heightForWidth(lbl.width()):
            bad.append((name, "换行高度不足", lbl.text()))
    # 明细：键名不得与值重叠；值已按可用宽省略，必须放得下
    for r in range(pop._grid.rowCount()):
        k = pop._grid.itemAtPosition(r, 0).widget()
        v = pop._grid.itemAtPosition(r, 1).widget()
        if k.geometry().right() + 1 > v.geometry().left():
            bad.append((f"row{r}", "键名与值重叠", f"{k.text()}/{v.text()}"))
        chk(f"row{r}值", v)
    return bad


def test_popover_layout_never_overflows_including_1m():
    """1M 下的最宽数据也不得出现文字溢出/重叠（"挤压遮挡"就是这里没守住）"""
    panel = _PanelStub()
    pop = ap._TokenStatsPopover(panel)
    _place(pop, panel, _one_m_stats())
    bad = _overflow_items(pop)
    assert bad == [], f"存在文字溢出/重叠：{bad}"
    bottoms = [c.geometry().bottom() for c in pop.findChildren(ap.QLabel)]
    assert max(bottoms) <= pop.height(), "子控件超出卡片高度（底部文字被裁）"
    pop.close()


def test_popover_shows_full_1m_total():
    """1M 下的上限必须完整显示（曾因与百分比抢宽度被挤成省略号）

    占用条与百分比同基数（可用输入预算 = 窗口 − 预留输出），所以标签显示的是
    824,991 / 1,015,808；1M 窗口本身仍完整可见（来源行标签 + 悬浮 tooltip）。
    """
    panel = _PanelStub()
    pop = ap._TokenStatsPopover(panel)
    _place(pop, panel, _one_m_stats())
    usage = pop._usage.text()
    assert "1,015,808" in usage, f"可用输入预算必须完整可见，实际：{usage!r}"
    assert "…" not in usage
    assert "824,991" in usage
    assert "1,048,576" in pop._usage.toolTip(), "1M 窗口仍须可直接查到（悬浮）"
    assert "1,048,576" in pop._vals["window_src"].toolTip()
    assert pop._ratio.text() == ap._fmt_pct(824991 / 1015808)
    assert "1M 开关" in pop._vals["window_src"].text()
    pop.close()


def test_popover_long_value_elides_with_full_tooltip():
    """放不下的值用中间省略（不硬裁到压住键名），全文进 tooltip"""
    panel = _PanelStub()
    pop = ap._TokenStatsPopover(panel)
    s = _one_m_stats()
    s["model"] = "deepseek-v4.1-flash-0126-preview-with-a-very-long-name"
    _place(pop, panel, s)
    lbl = pop._vals["model"]
    assert lbl.text() == s["model"] or "…" in lbl.text()
    assert lbl.toolTip() == s["model"], "省略后必须能在 tooltip 看到全文"
    assert lbl.fontMetrics().horizontalAdvance(lbl.text()) <= lbl.width() + 1
    pop.close()


def test_popover_resize_relayouts_value_elision():
    """卡片尺寸变化后省略宽度需重算（否则窄卡片下仍会溢出/硬裁）"""
    panel = _PanelStub()
    pop = ap._TokenStatsPopover(panel)
    s = _one_m_stats()
    s["model"] = "deepseek-v4.1-flash-0126-preview-with-a-very-long-name"
    _place(pop, panel, s)
    wide = pop._vals["model"].text()
    pop.resize(250, pop.height())
    pop.layout().activate()
    for _ in range(2):
        _app.processEvents()
    lbl = pop._vals["model"]
    assert lbl.fontMetrics().horizontalAdvance(lbl.text()) <= lbl.width() + 1
    assert len(lbl.text()) <= len(wide)
    pop.close()


def test_popover_legend_and_cache_wrap_instead_of_clipping():
    """阈值图例每条一行、缓存明细自动换行：高度随行数增长，不做横向硬裁"""
    panel = _PanelStub()
    pop = ap._TokenStatsPopover(panel)
    _place(pop, panel, _one_m_stats())
    assert pop._ticks_lbl.wordWrap() and "<br>" in pop._ticks_lbl.text()
    assert pop._cache_lbl.wordWrap()
    assert pop._ticks_lbl.height() > pop._ticks_lbl.fontMetrics().height()
    pop.close()


def test_token_pop_size_fits_panel_and_content():
    """尺寸算法：宽度钳到面板可用宽；高度取布局实际需要（换行标签的 sizeHint 偏小）"""
    wide = _PanelStub()
    pop = ap._TokenStatsPopover(wide)
    w, h = _place(pop, wide, _one_m_stats())
    assert w == pop.PREFERRED_WIDTH
    assert h >= pop.layout().heightForWidth(w), "高度必须容纳换行后的真实内容"
    assert max(c.geometry().bottom() for c in pop.findChildren(ap.QLabel)) <= h
    pop.close()

    narrow = _PanelStub(w=300, h=500)
    pop2 = ap._TokenStatsPopover(narrow)
    w2, _h2 = _place(pop2, narrow, _one_m_stats())
    assert w2 < pop2.PREFERRED_WIDTH, "窄面板下卡片宽度必须收缩，不能越界"
    assert w2 >= 240
    pop2.close()


def test_popover_not_cramped_by_exceptionally_wide_numbers():
    """极端大数值（位数最多）下依然不溢出：值列省略必须兜得住"""
    panel = _PanelStub()
    pop = ap._TokenStatsPopover(panel)
    s = _one_m_stats()
    s["cumulative"] = {"prompt": 98765432, "completion": 12345678,
                       "total": 111111110, "cache_hit": 90000000,
                       "cache_miss": 8765432}
    s["compaction"] = {"count": 123, "last_at": 1.0, "last_merged": 99,
                       "last_saved": 9876543}
    s["messages"] = 99999
    _place(pop, panel, s)
    bad = _overflow_items(pop)
    assert bad == [], f"极端数值下出现溢出：{bad}"
    pop.close()


def test_segments_degenerate_upstream_data_clamped():
    """退化数据：hit>used / 缺 completion / hit=0 纯输出段——三段划分仍钳制自洽、不越界"""
    pal = ap._usage_palette()
    win = 1000
    s1 = {"used": 100, "source": "upstream",
          "last": {"cache_hit": 999, "cache_miss": 0, "completion": 0}}
    segs1 = ap._TokenStatsPopover._segments(s1, pal, win)
    assert [round(r * win) for r, _ in segs1] == [100]          # hit 钳到 used
    s2 = {"used": 500, "source": "upstream",
          "last": {"cache_hit": 200, "cache_miss": 300}}
    segs2 = ap._TokenStatsPopover._segments(s2, pal, win)
    assert [round(r * win) for r, _ in segs2] == [200, 300]     # 缺 completion → 无输出段
    s3 = {"used": 400, "source": "upstream",
          "last": {"cache_hit": 0, "cache_miss": 0, "completion": 400}}
    segs3 = ap._TokenStatsPopover._segments(s3, pal, win)
    assert [round(r * win) for r, _ in segs3] == [400]          # 纯输出段
    assert all(0.0 <= r <= 1.0 for segs in (segs1, segs2, segs3)
               for r, _ in segs), "各段占比必须钳制在 [0,1]"
