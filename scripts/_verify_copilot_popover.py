"""端到端离屏验证：AI 面板内的 zhuzhu Copilot 入口按钮与浮层开合。

构造完整 AgentPanel（真实 __init__）后：
  1. 顶栏存在 copilot 按钮，且位于「上下文统计」按钮左侧；
  2. 点击可丝滑弹出 Copilot 浮层（真实 CopilotPanel），四分组齐备；
  3. 再次点击 / 关闭请求可收起浮层。
副作用隔离：CopilotPanel 的磁盘扫描、托盘、管理员告警、桌宠均被打桩。

用法：python scripts/_verify_copilot_popover.py（退出码 0 = 通过）
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from PyQt6.QtCore import QPoint, Qt                           # noqa: E402
from PyQt6.QtWidgets import QApplication                     # noqa: E402

app = QApplication([])

import winapp_migrator.ui.main_window as mw                  # noqa: E402

# 隔离 CopilotPanel 构造副作用（避免真实扫描 / 弹窗 / 托盘 / 桌宠）
mw.CopilotPanel._start_scan = lambda self, *a: None
mw.CopilotPanel._init_update_check = lambda self: None
mw.CopilotPanel._check_admin = lambda self: None
mw.CopilotPanel._setup_tray = lambda self: None
try:
    import winapp_migrator.ui.desktop_pet as dp
    dp.ensure_pet = lambda w: None
except Exception:
    pass

from winapp_migrator.ui.agent_panel import AgentPanel         # noqa: E402

FAILS = []


def check(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def pump(ms):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


t0 = time.time()
panel = AgentPanel(None)
print(f"AgentPanel 构造耗时 {time.time() - t0:.1f}s")
panel.resize(1400, 900)
panel.show()
pump(400)

print("[1] 顶栏入口按钮")
check(hasattr(panel, "copilot_btn"), "存在 copilot_btn")
check(hasattr(panel, "token_btn"), "存在 token_btn（上下文统计）")
try:
    x_cop = panel.copilot_btn.mapTo(panel, QPoint(0, 0)).x()
    x_tok = panel.token_btn.mapTo(panel, QPoint(0, 0)).x()
    y_cop = panel.copilot_btn.mapTo(panel, QPoint(0, 0)).y()
    y_tok = panel.token_btn.mapTo(panel, QPoint(0, 0)).y()
    check(x_cop < x_tok and y_cop == y_tok,
          f"copilot 按钮位于统计按钮左侧同一行（copilot x={x_cop} < token x={x_tok}，y={y_cop}/{y_tok}）")
except Exception as e:
    check(False, f"顶栏按钮位置检查异常: {e}")

print("[2] 弹出浮层")
panel.toggle_copilot_panel()
pump(500)
cp = panel._copilot_panel
check(cp is not None, "浮层已创建")
check(bool(cp and cp.isVisible()), "浮层可见")
if cp is not None:
    tabs = [b.text() for b in cp._tab_group.buttons()]
    check(tabs == ["应用", "防护", "通用", "工具"], f"四分组齐备: {tabs}")
    geo = cp.geometry()
    btn_rect_top = panel.copilot_btn.mapToGlobal(
        panel.copilot_btn.rect().bottomLeft()).y()
    check(abs(geo.top() - btn_rect_top) < 40,
          f"浮层贴近按钮下方（浮层top={geo.top()} 按钮底={btn_rect_top}）")
    scr = app.primaryScreen().availableGeometry()
    check(scr.contains(geo), f"浮层未越界（{geo.getRect()} in {scr.getRect()}）")

print("[3] 收起浮层")
panel.toggle_copilot_panel()
pump(400)
check(bool(cp and not cp.isVisible()), "再次点击后浮层收起")

print("[4] 头部关闭按钮请求收起")
panel.open_copilot_panel()
pump(400)
if cp is not None:
    cp.close_requested.emit()
    pump(400)
    check(not cp.isVisible(), "close_requested 触发收起")

print("[5] 浮层为无边框窗口")
_flags = cp.windowFlags() if cp is not None else 0
check(bool(_flags & Qt.WindowType.FramelessWindowHint), "带无边框标志（FramelessWindowHint）")
check(bool(_flags & Qt.WindowType.Tool), "带 Tool 标志（不占任务栏）")
check(not bool(_flags & Qt.WindowType.WindowTitleHint), "无系统标题栏")

print("[6] 整个窗口由粒子自上而下生成（60fps）")
check(bool(cp and cp._reveal.FRAME_MS == 16), "帧间隔 16ms（60fps，PreciseTimer）")
panel.open_copilot_panel()
check(bool(cp and cp.mask().isEmpty()), "起手无窗口区域（不是先弹出空窗口）")
pump(1500)
check(bool(cp and not cp._reveal.is_running()), "粒子生成动画已结束")
check(bool(cp and cp.mask().isEmpty()), "结束后窗口掩码已撤销（恢复完整形状）")
check(bool(cp and cp.isVisible()), "生成完成后浮层保持可见")
if cp is not None:
    # 逐进度直接驱动掩码：动画基于单调时钟，靠等待会一步跑完而看不到中间态
    cp._prepare_reveal_edge()
    hs = []
    for pr in (0.0, 0.35, 0.7, 1.0):
        cp.apply_reveal(pr)
        hs.append(cp.mask().boundingRect().height())
    check(hs[0] < hs[1] < hs[2] <= hs[3], f"窗口区域随进度自上而下增长: {hs}")
    cp.finish_reveal()
    check(cp.mask().isEmpty(), "finish_reveal 后掩码已撤销")

print("[7] 外部收起后单次点击即弹出（回归：曾需点两次）")
_f = panel.__dict__.get("_copilot_dismiss")
check(_f is not None, "失焦守卫已挂载")
if _f is not None:
    _f._dismiss()          # 等价于"点击浮层外部 / 按 Esc"
    pump(400)
    check(bool(cp and not cp.isVisible()), "外部收起生效")
    check(panel._copilot_open is False, "宿主状态位已同步")
    panel.toggle_copilot_panel()
    pump(400)
    check(bool(cp and cp.isVisible()), "单次点击即重新弹出")

print("[8] 浮层开关状态与宿主一致")
panel.close_copilot_panel()
pump(400)
check(panel._copilot_open is False, "_copilot_open 已复位")

print()
print("FAIL:" if FAILS else "PASS: Copilot 入口按钮与浮层在真实面板内工作正常。")
for f in FAILS:
    print("  -", f)
sys.exit(1 if FAILS else 0)
