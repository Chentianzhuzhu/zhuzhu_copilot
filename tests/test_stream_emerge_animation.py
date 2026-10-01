# -*- coding: utf-8 -*-
"""流式落字「渐变模糊浮现」动效回归（`agent_chat_bubbles._EmergeBand`）。

被守护的契约（都是肉眼/手感问题，退化了不会报错，只会「又变得一跳一跳」）：
 1. **每种输出都浮现**：思考过程 / 工具调用 / 执行命令 / 回复正文（含通用富文本段）
    都挂了浮现层，且各有正确的「正文区域 + 面底色」（卡片底、面板底、命令块底）；
 2. **只在流式期间播放**：历史会话重排/切回、主题切换重建一律不补动画；
 3. **几何贴住正文底部**：波段覆盖「浮现窗口内落下的行」并按正文区域落位；
 4. **清晰度是行龄的连续函数**：越靠下越模糊、越靠上越清晰，末行必须最模糊
    （用行下沿采样会把末行判成“还没落下”，反而最先清晰——历史缺陷，锁死）；
 5. **上缘无缝、收尾无痕**：波段叠加到清晰正文上后，已落定区域逐像素一致
    （否则会看到一块矩形色斑），且整段在 EMERGE_MS 内收敛回清晰（不会永久发虚）；
 6. **内容/几何两类更新分开**：布局把块拉高不得被当成新落下的字（否则每长一行都起一波）；
 7. **单帧成本有界**：高帧节拍不得拖慢主线程（底图/模糊层缓存与限频必须生效）。

时序敏感项用 QTest.qWait 轮询；数值项用真实像素统计（离屏环境无字形也能判定）。
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest                                                        # noqa: E402
from PyQt6.QtCore import QPoint, QRect, Qt                           # noqa: E402
from PyQt6.QtGui import (                                            # noqa: E402
    QColor,
    QIcon,
    QImage,
    QPainter,
    QPixmap,
    QRegion,
)
from PyQt6.QtTest import QTest                                       # noqa: E402
from PyQt6.QtWidgets import (                                        # noqa: E402
    QApplication,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from zhuzhu_Copilot.ui import agent_chat_bubbles as cb               # noqa: E402
from zhuzhu_Copilot.ui import agent_panel as ap                      # noqa: E402

app = QApplication.instance() or QApplication([])

STYLE = cb.ChatStyle(
    card="#1F232C", border="#272C36", border_soft="#333A46", dash="#333A46",
    text="#F3F5F9", text_dim="#9BA3B0", accent="#2F52D8", muted="#9BA3B0",
    icon_shell="#272C36", icon_color="#2F52D8", tag_bg="#2A3040", tag_fg="#9BA3B0",
    user_bg="#2F52D8", user_fg="#FFFFFF", cmd_fg="#F3F5F9", ok_fg="#5B82F6",
    hover="#272C36", panel="#181B21", bg="#101216",
)

BASE = ("The quick brown fox jumps over the lazy dog and keeps running through the "
        "quiet valley at dawn while the river glints below. ")
# 追加文本要足够长：新落下的区域必须有字，否则「有没有被压暗」无从判定
GROW = ("A brand new line lands here and slowly emerges from the blur, then another line "
        "follows right behind it so the fresh region is dense enough. ")
# 思考正文必须控制在 5 行折叠上限内（折叠后最新思考不在可视区，按设计不播动效）
THINK_BASE = "Checking the workspace layout before touching any file. "
THINK_GROW = "The plan is clear now, so the next step continues right away. "


def _html(text: str) -> str:
    return f'<div style="color:#F3F5F9;font-size:14px;">{text}</div>'


def _icon(kind: str, size: int, color: str) -> QIcon:
    """图标提供者桩：只要返回非空图标即可（本用例不校验图标内容）。"""
    pm = QPixmap(size, size)
    pm.fill(QColor(color))
    return QIcon(pm)


class Host:
    """离屏宿主：给出真实宽度与真实布局（波段几何依赖区块实际几何）。"""

    def __init__(self, width: int = 760):
        self.w = QWidget()
        self.lay = QVBoxLayout(self.w)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.block = cb.StreamBlock(STYLE)
        self.lay.addWidget(self.block)
        self.lay.addStretch(1)
        self.w.resize(width, 400)
        self.w.show()
        for _ in range(4):
            app.processEvents()

    @property
    def band(self) -> cb._EmergeBand:
        return self.block._emerge

    def close(self):
        self.w.hide()
        self.w.deleteLater()
        app.processEvents()


@pytest.fixture
def host():
    h = Host()
    yield h
    h.close()


def _show(widget: QWidget, width: int = 760, height: int = 400) -> QWidget:
    """把任意区块放进真实布局并显示（拿到真实宽度 → 触发首次布局重排）"""
    win = QWidget()
    lay = QVBoxLayout(win)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(widget)
    lay.addStretch(1)
    win.resize(width, height)
    win.show()
    for _ in range(4):
        app.processEvents()
    return win


def _wait(pred, timeout_ms: int = 1500, step: int = 20) -> bool:
    waited = 0
    while waited < timeout_ms and not pred():
        QTest.qWait(step)
        waited += step
    return pred()


def _bare_band() -> cb._EmergeBand:
    """只构一个「纯函数用」的浮现层：行高取自区块字体，不建完整控件树。"""
    band = cb._EmergeBand.__new__(cb._EmergeBand)
    band._owner = QWidget()
    return band


def _crisp(band: cb._EmergeBand) -> QImage:
    """同区域的「清晰正文 + 区块底色」参照图（与浮现层内部取图同一口径）。"""
    w, h = band.width(), band.height()
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(band._fill)
    band._paused = True                 # 参照图不含浮现层自身
    try:
        band._owner.render(img, QPoint(0, 0), QRegion(QRect(band.x(), band.y(), w, h)),
                           QWidget.RenderFlag.DrawWindowBackground
                           | QWidget.RenderFlag.DrawChildren)
    finally:
        band._paused = False
    return img


def _diff(a: QImage, b: QImage, y0: int, y1: int):
    """区域平均亮度差 / 最大亮度差（0-255）"""
    tot, mx, n = 0, 0, 0
    for y in range(max(0, y0), min(a.height(), y1)):
        for x in range(0, a.width(), 3):
            ca, cbb = a.pixelColor(x, y), b.pixelColor(x, y)
            d = max(abs(ca.red() - cbb.red()), abs(ca.green() - cbb.green()),
                    abs(ca.blue() - cbb.blue()))
            tot += d
            mx = max(mx, d)
            n += 1
    return tot / max(1, n), mx


# ---------- 1. 每种输出都挂了浮现层 ----------
def test_every_output_block_has_emerge_band():
    """思考过程 / 工具调用 / 执行命令 / 回复正文 / 通用富文本段都要有浮现层。"""
    think = cb.ThinkBubble(STYLE, _icon)
    row = cb.ToolCallRow(STYLE, _icon)
    cmd = cb.CmdBlock(STYLE)
    stream = cb.StreamBlock(STYLE)
    rich = cb.RichBlock(STYLE)
    try:
        for block in (think, row, cmd, stream, rich):
            assert isinstance(block._emerge, cb._EmergeBand), type(block).__name__
        # 面底色必须与正文所在的那层一致，否则浮现层遮底会露出色斑
        assert think._emerge._fill.name() == STYLE.card.lower()
        assert cmd._emerge._fill.name() == STYLE.panel.lower()
        assert stream._emerge._fill.name() == STYLE.bg.lower()
        assert row._emerge._fill.name() == STYLE.bg.lower()
    finally:
        for block in (think, row, cmd, stream, rich):
            block.deleteLater()
        app.processEvents()


def test_emerge_fill_prefers_band_over_dark_theme_bg():
    """「落字浮现」的遮底色必须优先取 ChatStyle.band —— 否则正文会闪黑。

    回归（用户反馈「agent 输出正文时有黑色元素瞬间出现」）：正文 / 思考 / 工具行 /
    命令块原先都直接拿 `style.bg`（纯深色主题底）当遮色。磨砂玻璃下正文是坐在壁纸上的，
    每落一行字就闪一块纯黑。面板侧因此给出 `band`（壁纸平均色压在面板色上的**不透明**
    合成色 —— 必须不透明才盖得住旧字）。此用例锁死「给了 band 就必须用 band」的链路：
    漏掉任何一个区块，黑块就会从那个区块冒出来。
    """
    import dataclasses

    band = "#2A3140"
    styled = dataclasses.replace(STYLE, band=band)
    blocks = [cb.ThinkBubble(styled, _icon), cb.ToolCallRow(styled, _icon),
              cb.CmdBlock(styled), cb.StreamBlock(styled), cb.RichBlock(styled)]
    try:
        for block in blocks:
            assert block._emerge._fill.name() == band.lower(), type(block).__name__
    finally:
        for block in blocks:
            block.deleteLater()
        app.processEvents()

    # 没给 band（历史主题包 / 老构造点）时仍回退到各自的面底色，不出现空色
    for block in (cb.StreamBlock(STYLE), cb.RichBlock(STYLE)):
        try:
            assert block._emerge._fill.name() == STYLE.bg.lower(), type(block).__name__
        finally:
            block.deleteLater()
    app.processEvents()


def test_tool_row_emerges_when_it_lands_on_screen():
    """工具行/命令块这类「一次成型」的块：内容在布局生效前就写好了，
    必须靠「首次布局重排 = 块刚上屏」把它整块记为刚落下，否则它会直接跳出来。"""
    row = cb.ToolCallRow(STYLE, _icon)
    row.set_live(True)                      # 真实顺序：先建块并写入内容，再交给布局
    row.set_content("read_file", "正在读取", {"path": "a.py"})
    win = _show(row)
    try:
        assert _wait(lambda: row._emerge.isVisible()), "上屏后应起一波整块浮现"
        assert row._emerge._hist, "上屏必须记下浮现基线"
        assert row._emerge._h > 0
        # 上屏瞬间整块必须处在浮现中（而不是直接清晰落地）
        band = row._emerge
        prof = band._profile(max(0, band._h - band.height()), band.height(),
                             time.perf_counter())
        assert prof[-1][1] < 0.6, "上屏瞬间应处于浮现中"
        assert band.width() == band._area().width(), "浮现层必须跟随真实几何"
    finally:
        win.hide()
        win.deleteLater()
        app.processEvents()


def test_bordered_blocks_keep_border_out_of_band():
    """卡片（思考气泡）/ 命令块的浮现层必须让开 1px 描边与圆角：
    否则浮现期间实色遮底会把边框啃掉一段，露出版块缺口。"""
    think = cb.ThinkBubble(STYLE, _icon)
    cmd = cb.CmdBlock(STYLE)
    win1, win2 = _show(think), _show(cmd)
    try:
        for blk in (think, cmd):
            blk.set_live(True)
        think.set_content("PLANNING", _html(THINK_BASE))
        cmd.set_content("run_command", "echo hello", _html(BASE))
        for blk in (think, cmd):
            blk._emerge.touch()
            blk._emerge._sync()
            area = blk._emerge._area()
            assert area.x() >= 2, f"{type(blk).__name__} 浮现区域越过了左边框"
            assert area.x() + area.width() <= blk.width() - 2, \
                f"{type(blk).__name__} 浮现区域越过了右边框"
            if blk._emerge.isVisible():
                assert blk._emerge.x() >= 2
                assert blk._emerge.x() + blk._emerge.width() <= blk.width() - 2
    finally:
        for win in (win1, win2):
            win.hide()
            win.deleteLater()
        app.processEvents()


def test_think_body_growth_animates_like_reply():
    """思考正文增长同样要有浮现（此前只有回复正文有动效）。

    注意用「不触发折叠」的思考文本：折叠态下正文被裁到 5 行、最新思考不在可视区，
    此时不播动效是设计约定（见 ThinkBubble._emerge_area）。
    """
    think = cb.ThinkBubble(STYLE, _icon)
    win = _show(think)
    try:
        think.set_live(True)
        think.set_content("PLANNING", _html(THINK_BASE))
        QTest.qWait(cb.EMERGE_MS + 150)
        h0 = think._emerge._h
        assert h0 > 0, "未折叠的思考正文必须有可浮现区域"
        think.set_content("PLANNING", _html(THINK_BASE + THINK_GROW))
        assert think._emerge._h > h0
        assert _wait(lambda: think._emerge.isVisible()), "思考正文增长应起波"
        prof = think._emerge._profile(max(0, think._emerge._h - think._emerge.height()),
                                      think._emerge.height(), time.perf_counter())
        assert prof[-1][1] < prof[0][1], "新增的思考正文应比旧内容模糊"
    finally:
        win.hide()
        win.deleteLater()
        app.processEvents()


# ---------- 2. 只在流式期间播放 ----------
def test_history_replay_never_animates(host):
    """历史会话重排/切回：不记录落字、不显示浮现层（否则每次切会话都整段重播动画）。"""
    band = host.band
    assert not band._live
    host.block.set_html(_html(BASE))
    host.block.set_html(_html(BASE + GROW))
    QTest.qWait(60)
    assert band._hist == [], "非流式期间不得记录落字"
    assert band.isHidden(), "非流式期间浮现层必须保持隐藏"
    assert not band._timer.isActive(), "非流式期间不得空转定时器"


def test_live_then_end_settles_without_snap(host):
    """流式收尾：set_live(False) 后当前一波自然收敛（不留永久模糊），随后隐藏。"""
    band = host.band
    host.block.set_live(True)
    host.block.set_html(_html(BASE))
    host.block.set_html(_html(BASE + GROW))
    assert band.isVisible(), "落字后浮现层应立刻贴上"
    host.block.set_live(False)
    assert _wait(lambda: not band._timer.isActive()), "收尾后定时器必须停表"
    assert band.isHidden(), "收敛完成后浮现层应隐藏"


def test_hidden_turn_leaves_no_stale_band(host):
    """回合被隐藏（过程区收起/最小化）时必须收起浮现层：否则重新显示会残留旧帧的模糊画面。"""
    band = host.band
    host.block.set_live(True)
    host.block.set_html(_html(BASE))
    host.block.set_html(_html(BASE + GROW))
    assert band.isVisible()
    host.w.hide()
    assert _wait(lambda: band.isHidden()), "隐藏后必须收起浮现层"
    host.w.show()
    QTest.qWait(60)
    assert band.isHidden(), "重现时不得自动显示上一帧"
    host.block.set_html(_html(BASE + GROW + "and a bit more text follows"))
    assert _wait(lambda: band.isVisible()), "再次落字应恢复浮现"


# ---------- 3. 几何 ----------
def test_band_sits_on_content_bottom(host):
    """波段必须贴住正文底边、与正文区域同宽（否则新字会先在清晰态闪一下，
    或波段盖到卡片边框/圆角上）。"""
    band = host.band
    host.block.set_live(True)
    host.block.set_html(_html(BASE))
    QTest.qWait(cb.EMERGE_MS + 150)
    host.block.set_html(_html(BASE + GROW))
    band._sync()
    area = band._area()
    assert band.isVisible()
    assert band.y() + band.height() == area.y() + area.height(), "波段下沿必须等于正文底边"
    assert (band.x(), band.width()) == (area.x(), area.width()), "波段必须与正文区域同列"


def test_geometry_change_is_not_new_text(host):
    """布局把块拉高（resizeEvent）不得被当成新落下的字：否则每长一行都会重新起一波。"""
    band = host.band
    host.block.set_live(True)
    host.block.set_html(_html(BASE))
    QTest.qWait(cb.EMERGE_MS + 150)
    hist = list(band._hist)
    host.block._emerge_touch(by_geometry=True)
    assert band._hist == hist, "几何变化不得改写行龄历史"


def test_width_change_drops_history(host):
    """宽度变化会重排换行：行与内容的对应关系已失效，必须作废旧的行龄历史。"""
    band = host.band
    host.block.set_live(True)
    host.block.set_html(_html(BASE))
    host.block.set_html(_html(BASE + GROW))
    assert band._hist
    real = band._area
    band._area = lambda: QRect(real().x(), real().y(), real().width() + 40, real().height())
    band.touch()
    assert band._hist == [], "宽度变化后必须丢弃行龄历史（不补动画）"
    band._area = real


# ---------- 4. 清晰度曲线 ----------
def test_profile_is_age_gradient_with_youngest_at_bottom():
    """末行（最新落下）必须最模糊 —— 用行下沿采样会把它判成“还没落下”而最先清晰。"""
    band = _bare_band()
    now = 1000.0
    band._hist = [(now - 5.0, 32), (now - 0.20, 48)]   # 老内容 32px + 新落 16px
    band._h = 48
    prof = band._profile(16, 32, now)
    xs = [i for i, _p in prof]
    ps = [p for _i, p in prof]
    assert xs == sorted(xs) and xs[0] == 0 and xs[-1] == 31, "采样点须覆盖首末行"
    assert ps[-1] < ps[0] - 0.2, f"末行必须明显比首行模糊（{ps[-1]:.2f} vs {ps[0]:.2f}）"
    assert ps[0] > 0.99, "上缘为已落定区域，必须收敛到全清晰"
    # 自波段顶向下单调不增（清晰波前只有一个方向：越往下越新越糊）
    for up, down in zip(ps, ps[1:]):
        assert up >= down - 1e-6, "清晰度必须自下而上单调递增"


def test_profile_top_ramp_keeps_band_top_seamless():
    """波段上缘必须收敛到全清晰（上缘外侧是清晰正文，否则叠加后会看到一条色带）。"""
    band = _bare_band()
    now = 900.0
    band._hist = [(now - 0.05, 48)]                    # 整段都是刚落的
    band._h = 48
    prof = band._profile(30, 18, now)
    assert prof[0][1] > 0.99, "带内上缘必须被拉回全清晰"
    assert prof[-1][1] < 0.6, "远离上缘处仍应保持浮现中"


def test_profile_skips_top_ramp_when_band_covers_whole_content():
    """波段即整段正文时上方没有可比对的清晰正文，不得再用上缘过渡把首行强行拉清晰。"""
    band = _bare_band()
    now = 500.0
    band._hist = [(now - 0.05, 16)]
    band._h = 16
    prof = band._profile(0, 16, now)
    assert max(p for _i, p in prof) < 0.6, "首段正文应整体处在浮现中"


def test_writing_line_softens_same_line_growth(host):
    """行内继续落字（内容高度不变）同样要有浮现感，且停顿后必须回到全清晰。"""
    band = host.band
    host.block.set_live(True)
    host.block.set_html(_html(BASE))
    QTest.qWait(cb.EMERGE_MS + 150)          # 先让基线落定
    h0 = band._h
    host.block.set_html(_html(BASE + "same line keeps growing"))   # 只加宽、不加行
    if band._h != h0:                        # 该宽度下恰好换行了：本例只针对行内增长
        pytest.skip("测试文本在当前宽度下发生了换行")
    now = time.perf_counter()
    prof = band._profile(max(0, band._h - band.height()), band.height(), now)
    assert prof[-1][1] < 1.0 - cb.EMERGE_WRITING_DROP * 0.5, "笔尖所在行必须被压软"
    assert prof[0][1] > 0.99, "已落定区域不受影响"
    QTest.qWait(cb.EMERGE_MS + 200)
    prof2 = band._profile(max(0, band._h - band.height()), band.height(), time.perf_counter())
    assert min(p for _i, p in prof2) > 0.99, "停顿后必须整段回到全清晰（不得永久发虚）"


# ---------- 5. 叠加到正文上无缝、且收敛 ----------
def test_overlay_is_seamless_and_converges(host):
    """浮现层叠在清晰正文上：已落定区逐像素一致（无矩形色斑），整段在 EMERGE_MS 内收敛。"""
    band = host.band
    host.block.set_live(True)
    host.block.set_html(_html(BASE))
    QTest.qWait(cb.EMERGE_MS + 150)      # 基线先落定（否则“已落定区”其实也才刚落下）
    host.block.set_html(_html(BASE + GROW))
    t0 = time.perf_counter()
    band._sync()
    app.processEvents()

    ref = _crisp(band)
    area = band._area()
    base_h = int(band._hist[0][1])                 # 本次新增内容的上界（已落定/新增分界）
    # 分界行 = 波段图内的行号：波段顶在内容坐标里的位置 = 内容高 - 波段高
    split = max(3, base_h - (band.y() - area.y()))

    fresh = band._compose(t0)
    assert fresh is not None
    over = ref.copy()
    p = QPainter(over)
    p.drawImage(0, 0, fresh)
    p.end()
    # 上缘羽化带是最容易露出「色带边界」的位置：必须没有可见台阶
    top_avg, top_max = _diff(over, ref, 0, max(3, min(8, split)))
    old_avg, _old_max = _diff(over, ref, 0, max(3, split - 3))
    _new_avg, new_max = _diff(over, ref, split + 1, band.height() - 2)
    assert top_avg < 2.0 and top_max < 20, f"上缘可见接缝（{top_avg:.2f}/{top_max}）"
    assert old_avg < 2.5, f"已落定区整体出现了可见差异（{old_avg:.2f}）"
    assert new_max > 40, f"新落下的行没有被压暗/糊化（峰值差 {new_max}）"

    QTest.qWait(cb.EMERGE_MS + 200)
    settled = band._compose(time.perf_counter())
    over2 = ref.copy()
    p = QPainter(over2)
    p.drawImage(0, 0, settled)
    p.end()
    avg, mx = _diff(over2, ref, 2, band.height() - 2)
    assert avg < 1.0 and mx < 16, f"浮现未收敛回清晰（{avg:.2f}/{mx}）"


# ---------- 6. 单帧成本 ----------
def test_frame_cost_is_bounded(host):
    """高帧节拍：缓存命中帧必须远低于帧预算，内容变化帧也要留出余量。"""
    band = host.band
    host.block.set_live(True)
    host.block.set_html(_html(BASE * 4))
    host.block.set_html(_html(BASE * 4 + GROW))
    band._sync()
    now = time.perf_counter()
    band._compose(now)                       # 预热：建底图 + 模糊层

    n = 30
    t0 = time.perf_counter()
    for _ in range(n):
        band._compose(now)
    cached_ms = (time.perf_counter() - t0) / n * 1000

    t0 = time.perf_counter()
    for _ in range(10):
        band._cache = None                   # 模拟内容变化：重渲底图 + 重算模糊层
        band._compose(now)
    fresh_ms = (time.perf_counter() - t0) / 10 * 1000

    assert cached_ms < 4.0, f"缓存帧过慢：{cached_ms:.2f}ms（EMERGE_TICK_MS={cb.EMERGE_TICK_MS}）"
    assert fresh_ms < 12.0, f"内容变化帧过慢：{fresh_ms:.2f}ms"
    assert cb.EMERGE_TICK_MS <= 8, "动效节拍不得慢于 125fps（高刷屏要能占比持续丝滑）"

    # 帧内连续落字（尺寸不变）必须复用底图：落字刷新可达 240Hz，逐帧重渲会拖垮主线程
    band._rev += 1
    a, _ = band._layers(band.x(), band.y(), band.width(), band.height(), now)
    band._rev += 1
    b, _ = band._layers(band.x(), band.y(), band.width(), band.height(), now + 0.005)
    assert a is b, "同一节拍内的连续落字必须复用底图（不得逐帧重渲）"


# ---------- 7. 源图新鲜度（丝滑感的硬前提） ----------
def test_blur_source_is_fresh_on_every_screen_frame():
    """底图与行龄采样的新鲜度必须不低于屏幕刷新率。

    QWidget 每屏帧最多重绘一次，若底图重建间隔比刷新周期还长，模糊层就会连续几屏帧
    复用旧图 —— 新字在模糊层里「一格一格」跳进去，肉眼就是卡顿（用户反馈的「不够
    丝滑」）。所以两道间隔都必须压在 60Hz 的一帧（≈16.7ms）之内。
    """
    frame_60hz_ms = 1000.0 / 60.0
    assert cb.EMERGE_SRC_MS <= frame_60hz_ms, \
        f"底图重建间隔 {cb.EMERGE_SRC_MS}ms 长于 60Hz 一帧，模糊层会吃旧图"
    assert cb.EMERGE_SAMPLE_MS <= frame_60hz_ms, \
        f"行龄采样间隔 {cb.EMERGE_SAMPLE_MS}ms 长于 60Hz 一帧，波前推进不连续"
    assert cb.EMERGE_MS >= 600, \
        f"浮现窗口 {cb.EMERGE_MS}ms 过短，新字几乎「一下就清楚」，不叫浮现"


# ---------- 8. 离屏渲染预热（开场那半秒不能压在流式第一帧上） ----------
def test_offscreen_warmup_waits_for_a_shown_host_and_really_warms():
    """预热必须作用在**已 show** 的控件上，且成功前不得置位。

    实测（四组对照，同一进程逐个量）：4 参
    `render(图像, 偏移, QRegion, RenderFlags)` 作用在**未 show** 的控件上只要 2ms
    —— 完全吃不到那笔一次性懒初始化；作用在**已 show** 的控件上则要 550~1500ms，
    之后每帧 1ms。这正是必须赶在流式开始前消化掉的那笔开销（否则它压在**第一段流式
    文字的第一帧**上，观感就是开场卡半秒）。所以：
      · 宿主还没上屏时不得置位 `_offscreen_warmed`（提前置位 = 永久放弃预热）；
      · 宿主上屏后必须能真正完成预热。
    """
    host = QWidget()
    area = QScrollArea()
    area.setWidget(host)
    area.resize(400, 300)
    p = ap.AgentPanel.__new__(ap.AgentPanel)
    p.msg_area = area

    p._warm_offscreen_render()          # 宿主尚未上屏
    assert not p.__dict__.get("_offscreen_warmed"), \
        "宿主未上屏就置位 → 这一次预热机会被浪费，开场的半秒仍会压在流式第一帧上"

    area.show()
    QTest.qWait(30)
    p._warm_offscreen_render()
    assert p.__dict__.get("_offscreen_warmed") is True, \
        "宿主已上屏却没有真正完成预热"

    area.hide()
    area.deleteLater()
