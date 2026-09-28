"""CopilotPanel（原 zhuzhu Copilot 主窗口 → AI 面板内的紧凑浮层）测试。

覆盖：四个分组页与关键控件齐备、分组切换、浮层动画接口可用、对外信号齐备。
所有副作用（真实磁盘扫描 / 托盘 / 桌宠 / 管理员告警）在 fixture 中隔离。
"""
from zhuzhu_Copilot import app_identity
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest                                                    # noqa: E402
from PyQt6.QtCore import QRect, Qt                                # noqa: E402
from PyQt6.QtWidgets import QApplication, QPushButton             # noqa: E402

from zhuzhu_Copilot.ui import main_window as mw                  # noqa: E402

app = QApplication.instance() or QApplication([])

# 业务方法需要的控件：缺失会在运行期 AttributeError，故逐一断言
_REQUIRED_ATTRS = (
    "_app_count", "search_edit", "app_list", "status_label", "refresh_btn",
    "scan_progress", "_info_box",
    "drive_combo", "target_path_edit", "progress", "log_edit",
    "migrate_btn", "uninstall_btn", "open_dir_btn",
    "security_btn", "security_settings_btn", "toast_switch",
    "update_btn", "theme_combo",
    "download_btn", "memory_btn",
    "_pages", "_tab_group",
)


@pytest.fixture()
def panel(monkeypatch):
    """隔离构造副作用：不真扫描系统 / 不建托盘与桌宠 / 不弹权限告警"""
    monkeypatch.setattr(mw.CopilotPanel, "_start_scan", lambda self, *a: None)
    monkeypatch.setattr(mw.CopilotPanel, "_init_update_check", lambda self: None)
    monkeypatch.setattr(mw.CopilotPanel, "_check_admin", lambda self: None)
    monkeypatch.setattr(mw.CopilotPanel, "_setup_tray", lambda self: None)
    monkeypatch.setattr("zhuzhu_Copilot.ui.desktop_pet.ensure_pet", lambda w: None)
    p = mw.CopilotPanel(None)
    yield p
    p.deleteLater()


def test_all_business_widgets_exist(panel):
    missing = [a for a in _REQUIRED_ATTRS if getattr(panel, a, None) is None]
    assert not missing, f"缺少业务控件：{missing}"


def test_four_tabs_and_switch(panel):
    """顶部分段控件为 应用 / 防护 / 通用 / 工具，切换即切换页面索引"""
    buttons = panel._tab_group.buttons()
    assert [b.text() for b in buttons] == ["应用", "防护", "通用", "工具"]
    assert panel._pages.count() == 4
    assert panel._pages.currentIndex() == 0
    for idx in (1, 2, 3, 0):
        panel._tab_group.idClicked.emit(idx)
        assert panel._pages.currentIndex() == idx


def test_security_and_tools_pages_hold_expected_buttons(panel):
    """防护页与工具页各自承载对应入口（原右栏按钮已按分组重新归位）"""
    texts = [b.text() for b in panel.findChildren(QPushButton)]
    for want in ("开启静默防护", "防护设置", "检查更新", "关于我们",
                 "高速下载", "一键优化内存", "迁移自定义文件夹",
                 "开始迁移", "强力卸载", "打开安装目录", "重新扫描"):
        assert want in texts, f"缺少按钮：{want}"


def _pump(ms: int):
    """推进事件循环 ms 毫秒（动画依赖真实时间，processEvents 本身不等待）"""
    import time
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def test_popup_starts_with_no_window_area(panel):
    """起手瞬间窗口区域为空：面板不是"先弹出一个空窗口再补内容"。

    用户明确要求整体面板由粒子生成，因此第一帧必须什么窗口都还没有。"""
    panel.popup_animated(QRect(120, 90, 900, 580))
    assert panel.mask().isEmpty(), "动画起手应无窗口区域（否则空窗口会先出现）"


def test_reveal_masks_window_progressively(panel):
    """窗口掩码随进度自上而下增长：未生成部分"不存在"（不是先出现空窗口）。

    直接按进度驱动（而非等真实时间流逝）：动画基于单调时钟，若靠 pump 观察，
    首次 processEvents 的阻塞就会让动画一步跑完，看不到中间态。"""
    panel.resize(900, 580)
    panel._prepare_reveal_edge()
    heights = []
    for p in (0.0, 0.3, 0.6, 0.95):
        panel.apply_reveal(p)
        heights.append(panel.mask().boundingRect().height())
    assert heights[0] <= 12, f"进度 0 时应几乎没有窗口区域，实际 {heights[0]}px"
    for a, b in zip(heights, heights[1:]):
        assert b > a, f"掩码未随进度向下增长：{heights}"
    assert heights[-1] < 580, f"未完成时不应铺满整个窗口（{heights[-1]}px）"
    panel.apply_reveal(1.0)
    assert panel.mask().boundingRect().height() >= 580, "进度 1 时应覆盖整个窗口"
    panel.finish_reveal()
    assert panel.mask().isEmpty(), "生成结束后应撤销窗口掩码"


def _pump_until(pred, timeout_ms: int = 6000, step_ms: int = 20) -> bool:
    """推进事件循环直到条件成立或超时（动画依赖真实时间与 CPU 负载）。

    回归点：原用例固定 _pump(1600) 后直接断言动画已结束。全量运行（并发/负载高）
    时事件循环推进更慢，粒子动画可能仍在跑 → 间歇性失败（CI 抖动）。改为按条件
    轮询（不放宽契约：仍然要求动画最终结束），仅把"多久算够"交给条件判定。
    """
    import time
    end = time.time() + timeout_ms / 1000.0
    while time.time() < end:
        if pred():
            return True
        app.processEvents()
        time.sleep(step_ms / 1000.0)
    return pred()


def test_reveal_playback_completes_and_clears_mask(panel):
    """完整播放一次：粒子层停止、窗口掩码撤销、窗口保持可见"""
    panel.popup_animated(QRect(120, 90, 900, 580))
    assert _pump_until(lambda: not panel._reveal.is_running()), "粒子生成动画未结束"
    app.processEvents()
    assert panel.mask().isEmpty(), "生成结束后应撤销窗口掩码"
    assert panel.isVisible(), "生成完成后浮层应保持可见"
    assert panel.width() == 900 and panel.height() == 580


def test_particle_decay_scales_with_frame_interval(panel):
    """粒子衰减按真实帧间隔缩放（帧率无关）：低帧率下粒子寿命不被拉长。

    回归点：衰减曾按"每帧固定值"递减，掉帧时粒子迟迟不散 → 动画结束被拖延。"""
    panel.resize(800, 560)
    panel._reveal._grid = 1.0

    def one_frame(dt_ms):
        panel._reveal._parts = [{"x": 10.0, "y": 10.0, "vy": 1.0, "size": 1.0,
                                 "alpha": 0.5, "decay": 0.05, "color": "#B4B2A9"}]
        panel._reveal._advance(dt_ms)
        return panel._reveal._parts[0]["alpha"]

    a_16 = one_frame(16.0)   # 一个 60fps 帧
    a_64 = one_frame(64.0)   # 相当于 4 个 60fps 帧的时间
    assert abs((0.5 - a_64) - 4 * (0.5 - a_16)) < 1e-9, \
        f"衰减未按帧间隔缩放：16ms 掉 {0.5 - a_16:.4f}，64ms 掉 {0.5 - a_64:.4f}"


def test_reveal_finishes_even_if_particles_stick(panel, monkeypatch):
    """兜底：即使粒子永不消散（模拟掉帧/异常），动画也必须在 DURATION + TAIL 内收尾"""
    panel.popup_animated(QRect(100, 80, 820, 560))
    monkeypatch.setattr(panel._reveal, "_advance", lambda *a, **k: None)   # 粒子永不清空
    _pump(1600)
    assert not panel._reveal.is_running(), "兜底未生效：动画没有结束"
    assert panel.mask().isEmpty(), "兜底结束后应撤销窗口掩码"


def test_reveal_runs_at_60fps_with_precise_timer(panel):
    """动画按 60fps 推进：16ms 间隔 + 精确计时器（默认 ±5% 精度会拖成约 20ms）"""
    assert panel._reveal.FRAME_MS == 16
    panel.popup_animated(QRect(100, 80, 820, 560))
    timer = panel._reveal._timer
    assert timer.isActive()
    assert timer.interval() == 16
    assert timer.timerType() == Qt.TimerType.PreciseTimer


def test_close_hides_panel(panel):
    """收起动画结束后浮层隐藏"""
    panel.popup_animated(QRect(120, 90, 900, 580))
    _pump(200)
    panel.close_animated()
    _pump(400)
    assert not panel.isVisible()


def test_surface_is_frameless_tool_window(panel):
    """浮层必须是无边框窗口：无系统标题栏/边框，且不占任务栏"""
    flags = panel.windowFlags()
    assert bool(flags & Qt.WindowType.FramelessWindowHint), "缺少无边框标志"
    assert bool(flags & Qt.WindowType.Tool), "缺少 Tool 标志（不占任务栏）"
    assert not bool(flags & Qt.WindowType.WindowTitleHint), "不应带系统标题栏"


def test_repeated_toggle_is_stable(panel):
    """连续开合三次都成功：不会出现某次"弹不出来"（状态错位的回归点）"""
    for i in range(3):
        panel.popup_animated(QRect(100, 80, 820, 560))
        _pump(120)
        assert panel.isVisible(), f"第 {i + 1} 次弹出失败"
        assert not panel.is_closing(), f"第 {i + 1} 次弹出后 _closing 未复位"
        panel.close_animated()
        _pump(260)
        assert not panel.isVisible(), f"第 {i + 1} 次收起失败"


def test_popup_resets_stale_closing_state(panel):
    """收起动画未结束时立即重开：必须复位状态，否则之后收起会静默失效"""
    geo = QRect(100, 80, 820, 560)
    panel.popup_animated(geo)
    _pump(150)
    panel.close_animated()
    panel.popup_animated(geo)          # 收起进行中直接重开
    _pump(220)
    assert panel.isVisible()
    assert not panel.is_closing(), "_closing 未复位"
    panel.close_animated()
    _pump(400)
    assert not panel.isVisible(), "复位后收起应当生效"


def test_stop_anims_forces_open_state(panel):
    """_stop_anims 是"清理上一轮动画"的唯一入口：调用后必须处于可打开状态"""
    panel._closing = True
    panel._stop_anims()
    assert panel.is_closing() is False
    assert not panel.animation_running()


def test_signals_available(panel):
    """对外信号：头部关闭 / 托盘唤起 / 主题变更（由 AI 面板连接）"""
    got = {"close": 0, "open": 0, "theme": 0}
    panel.close_requested.connect(lambda: got.__setitem__("close", got["close"] + 1))
    panel.open_agent_requested.connect(lambda: got.__setitem__("open", got["open"] + 1))
    panel.theme_changed.connect(lambda: got.__setitem__("theme", got["theme"] + 1))
    panel.close_requested.emit()
    panel.open_agent_requested.emit()
    panel.theme_changed.emit()
    assert got == {"close": 1, "open": 1, "theme": 1}


def test_theme_pick_persists_setting(panel):
    """通用页切换主题：写入 agent_theme 并发出 theme_changed（两界面同源）。
    测试结束后恢复原值，避免污染用户真实主题设置。"""
    s = app_identity.qsettings()
    old = s.value("agent_theme")
    try:
        fired = []
        panel.theme_changed.connect(lambda: fired.append(True))
        # 选一个与当前不同的项，确保 currentIndexChanged 真的触发
        target = "auto" if panel.theme_combo.currentData() != "auto" else "dark"
        idx = panel.theme_combo.findData(target)
        assert idx >= 0
        panel.theme_combo.setCurrentIndex(idx)
        assert s.value("agent_theme") == target
        assert fired, "切换主题未发出 theme_changed"
    finally:
        s.setValue("agent_theme", old)


def test_shutdown_is_idempotent(panel):
    """退出清理：无防护线程 / 无桌宠时也必须安全返回"""
    panel.shutdown()
    panel.shutdown()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
