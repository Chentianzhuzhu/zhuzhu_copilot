"""dock（融入主面板）左栏高度分配回归：任务清单卡片底部不得留空白，余量归 Git 面板。

修复前：任务清单在 dock 下限高 5 行，但宿主布局继续按 5:3:2 拉伸它 → 卡片底部出现
41~51px 空白条（用户实测 273x44，位于 Git 面板正下方）。
修复后：① TodosWindow 在 dock 下把窗口高度钉在内容底边（_fit_dock_height）；
        ② 左栏高度封顶（_apply_dock_height_caps）把工作树定在其名义份额、Git 不封顶，
           释放的余量全部由 Git 面板吸收。
"""
from zhuzhu_Copilot import app_identity
import os
import sys
from itertools import pairwise

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication, QDialog, QVBoxLayout, QWidget

_app = QApplication.instance() or QApplication([])

from zhuzhu_Copilot.ui import agent_panel as ap

TODOS = [{"title": f"任务 {i}", "status": "pending"} for i in range(1, 7)]


def _fresh(cls):
    """构造面板并清除持久化/手动尺寸对测试的干扰（panel_size 存在 QSettings，跨运行残留）"""
    w = cls()
    w._manual_h = 0
    w.setMinimumHeight(0)
    w.setMaximumHeight(16777215)
    w.updateGeometry()
    return w


def _handle_h(win) -> int:
    """贴附模式下可见的顶部拖拽把手高度（融入时隐藏、不占位）"""
    h = getattr(win, "_drag_handle", None)
    return int(h.height()) if (h is not None and h.isVisible()) else 0


def _assert_no_blank(win):
    """窗口高度必须恰好 = 面板高度 + 可见把手高度：多出来的部分就是用户看到的空白条带"""
    assert win.height() == win.panel.height() + _handle_h(win), \
        f"窗口 {win.height()} != 面板 {win.panel.height()} + 把手 {_handle_h(win)}（留了空白条带）"


def test_dock_todos_pinned_to_content_capped_by_rows():
    """归纳：dock 下任务清单窗口钉死为 min(内容所需, 融入行数上限)，且面板填满窗口。

    回归 1：原先窗口高度取「面板几何底边」这类瞬时值，窗口可能比面板高 →
    面板上方/下方摊出空白条带（用户看到的 304x34 空白）。
    回归 2：若融入时按「默认尺寸 320」封顶，清单很长时会把同栏 Git 面板挤小
    （用户反馈「git 面板变小了」）→ 融入按 DOCK_MAX_ROWS 行封顶，超出滚动。"""
    win = _fresh(ap.TodosWindow)       # _manual_h=0 → 贴附固定默认尺寸
    win.update_todos(TODOS)            # 6 条 → 内容所需 < 融入行数上限
    win.show()
    _pump(6)
    win.dock_state = "left"            # 模拟已融入（_dock_is_immersed 为真）
    win._set_dock_ui(True)
    _pump(6)
    win._fit_dock_height()
    _pump(4)
    lo, hi = win.minimumHeight(), win.maximumHeight()
    assert lo == hi > 0, f"dock 高度应钉死，实际 min={lo} max={hi}"
    assert hi == min(win.panel.required_height(), win.panel.dock_max_height()), \
        (hi, win.panel.required_height(), win.panel.dock_max_height())
    _assert_no_blank(win)

    # 放进会拉伸的布局里也不应再长高（min=max 钉死 → 空白条消失的关键）
    host = QWidget()
    lay = QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(win)
    lay.addStretch(1)
    host.resize(280, 800)
    host.show()
    _pump(6)
    assert win.height() == hi, f"被拉伸到 {win.height()}（应保持钉死高度 {hi}）"

    win._set_dock_ui(False)
    host.close()
    win.close()


def test_dock_todos_capped_by_rows_and_scrolls():
    """清单条数再多也不挤占同栏 Git 面板：融入高度封顶在 DOCK_MAX_ROWS 行，超出滚动。"""
    cap_rows = ap.TodosPanel.DOCK_MAX_ROWS
    win = _fresh(ap.TodosWindow)
    win.dock_state = "left"
    win.show()
    # 逐条增加：从没超出到远超上限，窗口高度都不得超过行数上限
    for n in (cap_rows + 1, cap_rows + 9, cap_rows + 30):
        win.update_todos([{"title": f"任务 {i}", "status": "pending"}
                          for i in range(1, n + 1)])
        win._set_dock_ui(True)
        _pump(8)
        dock_max = win.panel.dock_max_height()
        assert win.panel.required_height() > dock_max, "用例前提：内容高于行数上限"
        assert win.height() == win.minimumHeight() == win.maximumHeight() == dock_max
        assert win.height() < win.DEFAULT_SIZE[1], "融入高度应低于默认尺寸（把余量留给 Git）"
        _assert_no_blank(win)
        # 列表内容高于视口 → 可滚动（内容高度撑开滚动区内部控件）
        assert win.panel._list.height() > win.panel._scroll.viewport().height()
    # 栏内空间不足时按 set_dock_room 收缩（不与同栏面板相互钳制）
    win.set_dock_room(140)
    _pump(4)
    assert win.height() == 140, win.height()
    _assert_no_blank(win)
    win.close()


def test_dock_todos_does_not_squeeze_git_panel():
    """回归（用户反馈「git 面板变小了」）：清单再长，任务清单高度也不得超过行数上限。

    实测上限 = DOCK_MAX_ROWS 行（等于改造前「限 5 行」的行为），故 Git 面板在
    清单很长时拿到的高度与改造前一致；清单短于上限时才按其内容收缩（Git 只会更大）。"""
    rows_cap = ap.TodosPanel.DOCK_MAX_ROWS
    win = _fresh(ap.TodosWindow)
    win.dock_state = "left"
    win.show()

    def _dock_h(n: int) -> int:
        win.update_todos([{"title": f"任务 {i}", "status": "pending"}
                          for i in range(1, n + 1)])
        win._set_dock_ui(True)
        _pump(8)
        return win.height()

    h5 = _dock_h(rows_cap)          # 恰好等于上限行数 → 上限高度
    hard_cap = h5
    for n in (rows_cap + 1, rows_cap + 20, 80):
        assert _dock_h(n) == hard_cap, f"{n} 条时高度超过 {rows_cap} 行上限（挤压 Git）"
    for n in (1, 2, 3):
        assert _dock_h(n) <= hard_cap, f"{n} 条时高度超过 {rows_cap} 行上限"
    win.close()


def test_todos_height_fits_content_capped_by_default_after_undock():
    """dock → 贴附：任务清单高度随内容自适应，且不超过默认尺寸（超出在面板内滚动）。

    回归（用户反馈「切换为贴附模式时 todos 面板太大」）：原先贴附一律钉成默认 320，
    清单只有一两条时也撑着大片空白。现在短清单贴内容，长清单停在默认高度滚动。"""
    win = _fresh(ap.TodosWindow)
    win.show()
    win.update_todos(TODOS)                 # 6 条 → 内容所需 < 默认尺寸
    win.dock_state = "left"
    win._set_dock_ui(True)
    _pump(6)
    dock_h = win.height()
    assert dock_h == win.minimumHeight() == win.maximumHeight()
    assert dock_h <= win.DEFAULT_SIZE[1], "dock 高度不得超过固定默认尺寸"
    _assert_no_blank(win)

    win.dock_state = "float"                # 模拟 _undock_panel 的顺序
    win._set_dock_ui(False)
    _pump(6)
    cap = win.DEFAULT_SIZE[1]
    floor = win.TODOS_MIN_H                     # 面板可用下限（标题行 + 一行任务）
    assert win.minimumHeight() == win.maximumHeight() == win.height()
    assert win.height() == max(floor, min(win.panel.required_height(), cap)), \
        (win.height(), win.panel.required_height(), cap)
    assert win.height() <= cap
    _assert_no_blank(win)

    # 清单变长 → 停在默认上限并转为面板内滚动
    win.update_todos([{"title": f"任务 {i}", "status": "pending"} for i in range(1, 21)])
    _pump(8)
    assert win.panel.required_height() > cap
    assert win.height() == cap, win.height()
    assert win.panel._list.height() > win.panel._scroll.viewport().height()
    _assert_no_blank(win)

    # 清单变短 → 收回贴内容的高度（不留大片空白）
    win.update_todos(TODOS[:1])
    _pump(8)
    assert win.height() == max(floor, win.panel.required_height()) < cap, win.height()
    win.close()


def test_restore_pre_dock_size_keeps_todos_cap():
    """浮出时只沿用融入前的宽度：高度上限（默认 320）不被融入前的贴合高度覆盖，
    否则上限会被压到当时的清单长度、之后清单变长也长不高。"""
    win = _fresh(ap.TodosWindow)
    win.show()
    win.update_todos(TODOS[:1])            # 1 条 → 贴附高度很小（< 320）
    _pump(6)
    small = win.height()
    assert small < win.DEFAULT_SIZE[1]
    win.restore_pre_dock_size(280, small)   # 融入前尺寸就是这个小高度
    _pump(4)
    assert win._fixed_height() == win.DEFAULT_SIZE[1], "上限不得被贴合高度覆盖"
    win.update_todos([{"title": f"任务 {i}", "status": "pending"} for i in range(1, 12)])
    _pump(8)
    assert win.height() == win.DEFAULT_SIZE[1], "清单变长应能长到默认上限"
    win.close()


def test_dock_caps_give_surplus_to_git_panel():
    """左栏封顶：工作树按其名义份额封顶、Git 不封顶（吸收余量）、任务清单自管。"""
    wt, git, todos = _fresh(ap.WorktreeWindow), _fresh(ap.GitLogWindow), _fresh(ap.TodosWindow)
    order = [wt, git, todos]
    host = QWidget()
    lay = QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    for p in order:
        lay.addWidget(p)
    host.resize(280, 900)
    host.show()
    for _ in range(4):
        _app.processEvents()

    fake = ap.AgentPanel.__new__(ap.AgentPanel)   # 轻代理：只调用纯布局逻辑
    fake._apply_dock_height_caps("left", order, lay, host,
                                {"wtWin": 5, "gitLogWin": 3, "todosWin": 2})
    avail = host.height() - lay.spacing() * (len(order) - 1)
    expect_wt = int(avail * 5 / 10)
    assert wt.maximumHeight() == expect_wt, (wt.maximumHeight(), expect_wt)
    assert git.maximumHeight() == 16777215, "Git 面板必须不封顶（负责吸收余量）"
    assert todos.maximumHeight() != expect_wt, "任务清单不应被按份额封顶（由自身内容定高）"

    # 非左侧（右侧）不参与该分配，且右侧面板保持不封顶
    git.setMaximumHeight(123)
    fake._apply_dock_height_caps("right", [git], lay, host, {})
    assert git.maximumHeight() == 123, "右侧栏不应改动面板高度上限"

    host.close()
    for p in order:
        p.close()


def _drag(slot, gdx: float, gdy: float):
    """模拟真实拖拽：press → move（走 mouseMoveEvent 的 float 计算）→ release。"""
    start = QPointF(100.0, 100.0)
    slot.mousePressEvent(QMouseEvent(
        QEvent.Type.MouseButtonPress, QPointF(1.0, 1.0), start,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier))
    slot.mouseMoveEvent(QMouseEvent(
        QEvent.Type.MouseMove, QPointF(1.0, 1.0), QPointF(100.0 + gdx, 100.0 + gdy),
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier))
    slot.mouseReleaseEvent(QMouseEvent(
        QEvent.Type.MouseButtonRelease, QPointF(1.0, 1.0),
        QPointF(100.0 + gdx, 100.0 + gdy),
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier))


def test_panel_resize_drag_does_not_raise():
    """拖动四边/四角调整大小不得抛异常。

    回归：mouseMoveEvent 用 globalPosition()（QPointF/float）直接喂
    QRect.setLeft/setRight/setTop/setBottom → TypeError（int 参数），
    每次拖动都弹崩溃框（崩溃日志已记录）。
    注：拖拽松手会把尺寸写入 QSettings（真实行为），测试结束必须清掉这些键，
    否则会污染用户实际使用的面板尺寸。"""
    q = app_identity.qsettings()
    for cls in (ap.WorktreeWindow, ap.GitLogWindow, ap.TodosWindow):
        win = _fresh(cls)
        win.resize(300, 320)
        win.show()
        for _ in range(4):
            _app.processEvents()
        slots = getattr(getattr(win, "_grip", None), "_slots", {})
        assert slots, f"{cls.__name__} 未安装 resize 热区"
        for mode in ("R", "BR", "B", "TL", "L", "T"):
            _drag(slots[mode], 24.5, 18.5)      # 小数增量：取整缺失时必崩
        win.close()
        q.remove(f"panel_size/{win.objectName()}")
    q.sync()


def _body_drag(win, gdx: float, gdy: float) -> bool:
    """在面板体上模拟按下→拖动→松开，返回按下后是否进入拖拽态"""
    start = QPointF(100.0, 100.0)
    win.mousePressEvent(QMouseEvent(
        QEvent.Type.MouseButtonPress, QPointF(5.0, 5.0), start,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier))
    dragging = bool(win._dragging)
    win.mouseMoveEvent(QMouseEvent(
        QEvent.Type.MouseMove, QPointF(5.0, 5.0), QPointF(100.0 + gdx, 100.0 + gdy),
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier))
    win.mouseReleaseEvent(QMouseEvent(
        QEvent.Type.MouseButtonRelease, QPointF(5.0, 5.0),
        QPointF(100.0 + gdx, 100.0 + gdy),
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier))
    return dragging


def test_dock_mode_disables_todos_body_drag():
    """融入模式下禁用任务清单面板拖动（位置由栏内布局管理）；贴附模式仍可拖动。"""
    win = _fresh(ap.TodosWindow)
    win.show()
    _pump(6)

    # 贴附：基线——面板体拖动生效
    pos0 = win.pos()
    assert _body_drag(win, 40, 30) is True, "贴附态应可拖动"
    assert win.pos() != pos0, "贴附态拖动应改变窗口位置"

    # 融入：拖动被禁用（既不进入拖拽态，位置也不变）
    win.dock_state = "left"
    win._set_dock_ui(True)
    _pump(6)
    pos1 = win.pos()
    assert _body_drag(win, 60, 50) is False, "融入态不应进入拖拽态"
    assert win.pos() == pos1, "融入态拖动不得改变窗口位置"
    win._set_dock_ui(False)
    win.close()


def _dock_column():
    """构造真实 AgentPanel 并切到融入模式，返回 (panel, 左栏, 面板窗口列表)"""
    p = ap.AgentPanel()
    p.resize(1500, 1000)
    p.show()
    _pump(16)
    p._todos_enabled = lambda: True
    p._git_enabled = lambda: True
    p._apply_panel_mode("dock")
    _pump(20)
    return p, p._dock_left, p._dock_panel_order("left")


def test_dock_panels_touch_each_other():
    """融入模式下相邻面板必须首尾相接，不留底色缝隙（用户反馈「git 面板上方和下方的
    缝隙仍未占满」）。

    缝隙来自两处，都要为 0：① 栏内布局间距（已设 0）；② 各面板自身朝向相邻面板的
    内容边距——面板窗口底色与栏底色相同，10~12px 的边距看起来就是一条缝。"""
    p, col, order = _dock_column()
    try:
        lay = col.property("_dock_lay")
        assert lay is not None and lay.spacing() == 0, "栏内布局间距须为 0"
        assert [w.objectName() for w in order] == ["wtWin", "gitLogWin", "todosWin"]
        for a, b in pairwise(order):                  # 上下相邻窗口不得有空档
            assert a.y() + a.height() == b.y(), (a.objectName(), a.y(), a.height(), b.y())
        assert order[0].layout().contentsMargins().bottom() == 0, \
            "工作树底边须贴边，否则与 Git 之间露出底色缝"
        gm = p.git_win.layout().contentsMargins()
        assert gm.top() == 0 and gm.bottom() == 0, \
            f"Git 面板上下须贴边（当前 top={gm.top()} bottom={gm.bottom()}）"
        assert p.todos_win.panel.layout().contentsMargins().top() == 0, \
            "任务清单顶边须贴边，否则与 Git 之间露出底色缝"
    finally:
        p.close()


def test_dock_reclaims_margins_into_git_panel():
    """贴边省下的高度确实归 Git 面板：清单窗口矮多少（上下边距合计），Git 就高多少。"""
    p, _col, _order = _dock_column()
    try:
        p.todos_win.update_todos(TODOS)
        _pump(10)
        git_dock, todos_dock = p.git_win.height(), p.todos_win.height()
        m = ap.TodosPanel.MARGINS                  # (上, 右, 下, 左)
        reclaimed = m[1] + m[3]                    # 融入时归零的上下边距合计
        assert reclaimed > 0
        # 恢复常规边距（贴附口径）后：清单变高 reclaimed，Git 相应变矮
        p.todos_win.panel.set_dock_tight(False)
        p.todos_win._fit_dock_height()
        _pump(8)
        assert p.todos_win.height() - todos_dock == reclaimed, \
            (p.todos_win.height(), todos_dock, reclaimed)
        assert git_dock - p.git_win.height() == reclaimed, \
            (git_dock, p.git_win.height(), reclaimed)
    finally:
        p.close()


def test_restore_pre_dock_geometry_shrinks_without_record():
    """dock → 贴附：无可还原的「融入前尺寸」时（例如启动即为融入偏好）必须按贴附最小宽
    收紧主面板（用户反馈「ai 主面板应自动缩小」），而不是停在融入时的偏宽尺寸；
    有记录时照旧还原记录尺寸。"""
    p = ap.AgentPanel()
    try:
        p.resize(1840, 900)
        p.show()
        _pump(16)
        wide = p.width()
        assert wide > p.minimumWidth(), "用例前提：窗口比贴附最小宽更宽"

        p._pre_dock_geo = None             # 无融入前尺寸记录
        p._restore_pre_dock_geometry()
        _pump(6)
        assert p.width() == p.minimumWidth() < wide, \
            f"未自动缩小：w={p.width()} minW={p.minimumWidth()} 原={wide}"

        p._pre_dock_geo = (wide, 900)      # 有记录 → 还原它
        p._restore_pre_dock_geometry()
        _pump(6)
        assert p.width() == wide, f"有记录时应还原 {wide}，实际 {p.width()}"
    finally:
        p.close()


def test_reset_panel_poses_keeps_todos_content_sized():
    """「重置全部面板位置」不得把任务清单固定成 280x320（清单短时会被拉长）。"""
    win = _fresh(ap.TodosWindow)
    win.update_todos(TODOS[:2])          # 清单很短
    win.show()
    for _ in range(4):
        _app.processEvents()

    host = ap.AgentPanel.__new__(ap.AgentPanel)   # 轻代理：_reset_panel_poses 按属性取面板
    host.wt_win = _fresh(ap.WorktreeWindow)
    host.git_win = _fresh(ap.GitLogWindow)
    host.todos_win = win
    host.code_win = None
    host._ext_panels = {}
    host._applied_panel_mode = None
    host._request_side_sync = lambda *a, **k: None
    host._apply_panel_mode = lambda *a, **k: None
    ap._reset_panel_poses(host)
    for _ in range(6):
        _app.processEvents()

    content = win.panel.geometry().bottom() + 1 \
        + win.layout().contentsMargins().bottom()
    assert win.height() <= content + 22, \
        f"重置后任务清单被拉长：h={win.height()} 内容底={content}"
    for p in (host.wt_win, host.git_win):
        p.close()
    win.close()


def test_caps_skipped_when_share_below_panel_minimum():
    """栏高偏小、份额低于面板最小高度时不封顶（避免与 minimumHeight 相互钳制）。"""
    wt, git = _fresh(ap.WorktreeWindow), _fresh(ap.GitLogWindow)
    wt.setMinimumHeight(400)
    order = [wt, git]
    host = QWidget()
    lay = QVBoxLayout(host)
    lay.setSpacing(8)
    for p in order:
        lay.addWidget(p)
    host.resize(280, 300)          # 可用高约 292 → 份额 5/8*292 ≈ 182 < 400
    host.show()
    for _ in range(4):
        _app.processEvents()

    fake = ap.AgentPanel.__new__(ap.AgentPanel)
    fake._apply_dock_height_caps("left", order, lay, host,
                                {"wtWin": 5, "gitLogWin": 3})
    assert wt.maximumHeight() == 16777215, "份额不足最小高度时不应封顶"
    host.close()
    for p in order:
        p.close()


def _pump(n: int = 12):
    for _ in range(n):
        _app.processEvents()


def _fake_panel(dock_side: str):
    """轻代理：只带事件处理用到的属性（不构造完整 AgentPanel）；四面板均已显示。"""
    fake = ap.AgentPanel.__new__(ap.AgentPanel)
    fake._panel_minimized = False
    fake._ext_panels = {}
    for name, cls in (("wt_win", ap.WorktreeWindow), ("git_win", ap.GitLogWindow),
                      ("todos_win", ap.TodosWindow), ("code_win", ap.CodePreviewWindow)):
        w = _fresh(cls)
        w.dock_state = dock_side
        w.show()
        setattr(fake, name, w)
    _pump(6)
    return fake


def test_collapse_skips_docked_panels():
    """最小化/隐藏时只收起「浮出」面板，融入主面板的面板必须原样保留（回归核心）。

    回归根因：原实现对四面板一律 hide()，而 dock 子面板是主面板的普通子控件，
    恢复路径上的 _sync_*_win 对 dock_state != "float" 一律提前返回 → 无人重新 show()，
    表现为最小化再返回后左右两侧子面板整体消失。"""
    docked = _fake_panel("left")
    ap.AgentPanel._collapse_float_windows(docked)
    assert [w.isHidden() for w in (docked.wt_win, docked.git_win,
                                   docked.todos_win, docked.code_win)] == [False] * 4, \
        "dock 面板不得被显式 hide()（否则恢复窗口后无人复原）"
    for name in ("wt_win", "git_win", "todos_win", "code_win"):
        getattr(docked, name).close()

    floated = _fake_panel("float")
    ap.AgentPanel._collapse_float_windows(floated)
    assert [w.isHidden() for w in (floated.wt_win, floated.git_win,
                                   floated.todos_win, floated.code_win)] == [True] * 4, \
        "浮出（贴附）面板仍须在最小化时收起"
    ap.AgentPanel._collapse_float_windows(floated)   # 幂等，不抛异常


def test_dock_panels_survive_minimize_restore():
    """真实窗口最小化 → 恢复：dock 子面板随窗口自动回来（Qt 子控件语义），不得消失。"""
    fake = _fake_panel("left")
    host = QDialog()
    lay = QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    for name in ("wt_win", "git_win", "todos_win", "code_win"):
        lay.addWidget(getattr(fake, name))
    host.resize(900, 700)
    host.show()
    _pump(8)
    assert all(not getattr(fake, n).isHidden() for n in
               ("wt_win", "git_win", "todos_win", "code_win"))

    host.showMinimized()
    _pump(8)
    ap.AgentPanel._collapse_float_windows(fake)      # 最小化分支的真实行为
    host.showNormal()
    _pump(12)

    missing = [n for n in ("wt_win", "git_win", "todos_win", "code_win")
               if not getattr(fake, n).isVisible()]
    assert not missing, f"最小化恢复后 dock 子面板消失: {missing}"
    host.close()


def test_event_handlers_use_collapse_helper():
    """源码守卫：changeEvent/hideEvent 必须走 _collapse_float_windows（勿退回一律 hide）。"""
    import inspect
    src = inspect.getsource(ap.AgentPanel.changeEvent)
    assert "_collapse_float_windows()" in src, "最小化分支未复用收起逻辑"
    assert "_collapse_ext_panels()" in src
    hide_src = inspect.getsource(ap.AgentPanel.hideEvent)
    assert "_collapse_float_windows()" in hide_src, "hideEvent 未复用收起逻辑"
