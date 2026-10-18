# -*- coding: utf-8 -*-
"""验证「语音输入」接入（科大讯飞语音听写）：

1. 麦克风按钮位于输入框右侧（输入行内、输入框右边缘之右）。
2. 输入框上有蓝色渐变遮罩子控件，不吃鼠标事件、初始隐藏。
3. 遮罩随音量「快起慢落」平滑，几何贴合输入框（录音中拉高也跟随）。
4. 识别文本以「开始录音时的原文」为前缀整段回填输入框。
5. 中止（发送/切会话/隐藏）后迟到的中间结果被丢弃，按钮与遮罩复位。
6. 截图：空闲输入行 + 遮罩低/中/高音量三态。

运行：python scripts/verify_voice_input.py
"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from zhuzhu_Copilot.ui import agent_panel as ap  # noqa: E402

ok = True


def check(label, cond, extra=""):
    global ok
    print(f"[{'PASS' if cond else 'FAIL'}] {label}"
          f"{(' | ' + str(extra)) if extra and not cond else ''}")
    ok = ok and bool(cond)


# 离屏平台默认无 CJK 字体（中文渲染成方框），显式指定系统自带中文字体再截图
try:
    from PyQt6.QtGui import QFont, QFontDatabase
    _fams = set(QFontDatabase.families())
    for _f in ("Microsoft YaHei UI", "Microsoft YaHei", "微软雅黑", "SimHei", "SimSun"):
        if _f in _fams:
            app.setFont(QFont(_f, 12))
            break
except Exception:                                            # noqa: BLE001
    pass

panel = ap.AgentPanel()
# 必须按**面板自身的最小宽**开窗：dock 模式下左右侧栏占位，最小宽由
# _apply_dock_min_size 抬到 1074+侧栏，用更窄的尺寸测会把输入行挤到溢出，
# 那是「窗口小于允许的最小宽」的伪故障，不是布局问题。
panel.resize(max(1900, panel.minimumWidth()), 980)
panel.show()
for _ in range(30):
    app.processEvents()
    time.sleep(0.02)

inp = panel.input
btn = panel.voice_btn
ov = panel._voice_overlay


def _row_layout():
    """输入行 = 同时装着输入框与语音按钮的那个 QHBoxLayout。"""
    found = [None]

    def _walk(lay):
        if lay is None:
            return
        for i in range(lay.count()):
            it = lay.itemAt(i)
            wd = it.widget()
            if wd is inp:
                found[0] = lay
            if wd is not None:
                _walk(wd.layout())
            _walk(it.layout())

    _walk(panel.layout())
    return found[0]


row = _row_layout()
# 布局缓存要显式作废后再量：构造期会留下一个按中间态算出的过期高度
# （实测行高会短暂停在 136），不 invalidate 量到的是假值。
row.invalidate()
app.processEvents()
print(f"面板 {panel.width()}x{panel.height()}  输入行 {row.geometry().width()}px")

# ---------- 1. 按钮位置：输入框右侧 ----------
print("\n== 1. 语音按钮位于输入框右侧 ==")
check("voice_btn 存在且是输入行的兄弟控件",
      btn is not None and btn.parentWidget() is inp.parentWidget())
_in_r = inp.mapTo(panel, QPoint(inp.width(), 0)).x()
_btn_l = btn.mapTo(panel, QPoint(0, 0)).x()
check(f"按钮左边缘({_btn_l}) 在输入框右边缘({_in_r}) 之右", _btn_l >= _in_r, _btn_l)
check("按钮为圆形尺寸（直径 == _ROUND_BTN_D）",
      btn.width() == ap._ROUND_BTN_D and btn.height() == ap._ROUND_BTN_D,
      f"{btn.width()}x{btn.height()}")
check("按钮图标非空", not btn.icon().isNull())
check("按钮提示文案非空", bool(btn.toolTip()))

# 同一行内：垂直中心与输入框对齐
_in_cy = inp.mapTo(panel, QPoint(0, inp.height() // 2)).y()
_btn_cy = btn.mapTo(panel, QPoint(0, btn.height() // 2)).y()
check(f"与输入框垂直居中（差 {abs(_in_cy - _btn_cy)}px）", abs(_in_cy - _btn_cy) <= 6)

# 宽度代价：输入行最小宽只应增加「按钮直径 + 一个间距」，不能更多
check(f"输入行最小宽 {row.minimumSize().width()}px（含按钮新增 48px）",
      row.minimumSize().width() <= 700, row.minimumSize().width())
check("输入框实际宽度充裕（未被按钮挤扁）", inp.width() >= 300, inp.width())

# ---------- 2. 遮罩基础属性 ----------
print("\n== 2. 蓝色渐变遮罩 ==")
check("遮罩是输入框的子控件", ov.parentWidget() is inp)
check("遮罩不吃鼠标事件（WA_TransparentForMouseEvents）",
      ov.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
check("遮罩初始隐藏", not ov.isVisible())
check("遮罩动画定时器初始停止", not ov._timer.isActive())

# ---------- 3. 音量平滑与几何跟随 ----------
print("\n== 3. 遮罩随音量波动 ==")
ov.start()
app.processEvents()
check("start() 后遮罩可见", ov.isVisible())
check("start() 后动画定时器运行", ov._timer.isActive())
check("遮罩几何贴合输入框",
      ov.width() == inp.width() and ov.height() == inp.height(),
      f"overlay={ov.width()}x{ov.height()} input={inp.width()}x{inp.height()}")

ov.set_level(0.9)
for _ in range(6):
    ov._tick()
_after_attack = ov.level
print(f"   音量 0.9 连打 6 帧 → level={_after_attack:.3f}")
check("音量拉高后 level 明显上升（>0.5）", _after_attack > 0.5, _after_attack)

# 快起慢落：同一帧数下回落幅度必须小于上升幅度
ov.set_level(0.0)
for _ in range(6):
    ov._tick()
_after_decay = ov.level
print(f"   音量归零再打 6 帧 → level={_after_decay:.3f}")
check("停顿时缓慢回落（回落比上升慢）",
      _after_decay > 0.05 and (_after_attack - _after_decay) < _after_attack * 0.95,
      _after_decay)

# 不同音量 → 不同视觉强度（截图对比用）
shots = {}
for name, lvl in (("low", 0.15), ("mid", 0.5), ("high", 1.0)):
    ov._target = lvl
    ov._level = lvl
    for _ in range(2):
        ov._tick()
    app.processEvents()
    p = os.path.join(ROOT, "scripts", f"_verify_voice_overlay_{name}.png")
    inp.grab().save(p)
    shots[name] = p
    print(f"   截图 {name}: level={ov.level:.2f} -> {os.path.basename(p)}")

# 几何跟随：输入框因多行内容被拉高后，遮罩必须跟着变高。
# 注意不能用 setFixedHeight 硬拉 —— 输入框的高度由内容驱动（_auto_height 会把它
# 压回内容所需高度），必须真的写入多行文本，走的才是真实路径。
ov._sync_geometry()
_h0 = ov.height()
inp.setPlainText("第一行\n第二行\n第三行")
for _ in range(5):
    app.processEvents()
inp._auto_height()
app.processEvents()
ov._tick()
print(f"   写入 3 行文本 → 输入框高 {_h0} -> {inp.height()}，遮罩高 {ov.height()}")
check("输入框随内容变高", inp.height() > _h0, inp.height())
check("遮罩同步贴合输入框（多行高度）",
      ov.height() == inp.height() and ov.height() > _h0,
      f"overlay={ov.height()} input={inp.height()}")
inp.setPlainText("")
for _ in range(5):
    app.processEvents()
inp._auto_height()
app.processEvents()
ov._sync_geometry()
check("清空后遮罩随之收回单行高",
      ov.height() == inp.height() and ov.height() == _h0,
      f"overlay={ov.height()} input={inp.height()}")

ov.stop()
app.processEvents()
check("stop() 后遮罩隐藏", not ov.isVisible())
check("stop() 后动画定时器停止", not ov._timer.isActive())

# ---------- 4/5. 会话状态机（用桩替换真实识别会话，避免真实联网/占麦） ----------
print("\n== 4/5. 会话状态机与文本回填 ==")


class _StubSession:
    """替身：只记录 start/stop，由测试手动驱动信号，行为与真实会话一致。"""

    last = None

    def __init__(self, on_text=None, on_level=None, on_error=None, on_state=None,
                 **kw):
        self.on_text, self.on_level = on_text, on_level
        self.on_error, self.on_state = on_error, on_state
        self.started = False
        self.stopped = False
        _StubSession.last = self

    @property
    def active(self):
        return self.started and not self.stopped

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


from zhuzhu_Copilot.core import agent_asr  # noqa: E402

_real_session = agent_asr.VoiceInputSession
agent_asr.VoiceInputSession = _StubSession

inp.setPlainText("帮我写个")
panel._voice_start()
app.processEvents()
sess = _StubSession.last
check("开始后进入录音态", panel._voice_active is True)
check("会话已启动", sess is not None and sess.started)
check("遮罩已显示", ov.isVisible())
check("按钮切到录音样式（背景渐变强调色）",
      "qlineargradient" in btn.styleSheet(), btn.styleSheet()[:60])

# 模拟识别线程回调（信号 → 主线程）
panel.voice_text_signal.emit("一个脚本")
app.processEvents()
check("识别文本以原文为前缀回填",
      inp.toPlainText() == "帮我写个一个脚本", repr(inp.toPlainText()))
panel.voice_text_signal.emit("一个脚本，用 Python 写")
app.processEvents()
check("后续全量文本覆盖而非追加",
      inp.toPlainText() == "帮我写个一个脚本，用 Python 写", repr(inp.toPlainText()))
panel.voice_level_signal.emit(0.7)
app.processEvents()
check("音量信号写入遮罩", abs(ov._target - 0.7) < 1e-6, ov._target)

# 结束：会话回 finished → 状态复位
panel._voice_stop()
check("结束中按钮禁用（防重复点击）", not btn.isEnabled())
panel.voice_state_signal.emit("finished")
app.processEvents()
check("finished 后退出录音态", panel._voice_active is False)
check("finished 后遮罩收起", not ov.isVisible())
check("finished 后按钮恢复可用", btn.isEnabled())
check("finished 后按钮回到常态皮肤（无渐变）",
      "qlineargradient" not in btn.styleSheet())
check("结束不清空已识别文本",
      inp.toPlainText() == "帮我写个一个脚本，用 Python 写", repr(inp.toPlainText()))

# 中止语义：迟到的中间结果必须被丢弃
inp.setPlainText("")
panel._voice_start()
app.processEvents()
sess2 = _StubSession.last
panel._voice_abort()
app.processEvents()
check("abort 后退出录音态", panel._voice_active is False)
check("abort 已请求会话停止", sess2.stopped)
panel.voice_text_signal.emit("迟到的结果")
app.processEvents()
check("★ abort 后迟到中间结果被丢弃（输入框保持空）",
      inp.toPlainText() == "", repr(inp.toPlainText()))

# 错误路径
panel._voice_start()
app.processEvents()
panel.voice_error_signal.emit("测试错误")
app.processEvents()
check("错误后自动收尾并复位", panel._voice_active is False and btn.isEnabled())

# 发送即中止（_send 的第一道守卫）
inp.setPlainText("")
panel._voice_start()
app.processEvents()
check("发送前处于录音态", panel._voice_active is True)
panel._voice_abort()          # _send 内部走的就是这条
app.processEvents()
check("发送路径会中止语音输入", panel._voice_active is False)

agent_asr.VoiceInputSession = _real_session

# ---------- 6. 截图 ----------
print("\n== 6. 截图 ==")
inp.setPlainText("")
panel._voice_abort()
app.processEvents()
row = btn.parentWidget()
row_png = os.path.join(ROOT, "scripts", "_verify_voice_row.png")
row.grab().save(row_png)
print(f"   输入行截图 -> {os.path.basename(row_png)}")
print(f"   遮罩截图   -> {[os.path.basename(v) for v in shots.values()]}")

print(f"\n{'ALL PASS' if ok else 'HAS FAILURES'}")
sys.stdout.flush()
# AgentPanel 会拉起 WeChatBridge 轮询等后台线程，正常退出时解释器清理会崩溃
# （与本改动无关的历史现象）；用 os._exit 跳过析构，保证退出码反映断言结果。
os._exit(0 if ok else 1)
