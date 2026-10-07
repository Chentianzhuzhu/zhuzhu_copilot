# -*- coding: utf-8 -*-
"""验证顶栏「默认公告」改动：
1. 字号增大：样式表 font-size 由 FONT_SMALL(12) 提升到 FONT_TITLE(16)。
2. 不限制显示字数：去掉 maximumWidth(420) 硬上限；_set_announcement 不再按 60 字截断。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from zhuzhu_Copilot.ui import agent_panel as ap  # noqa: E402
from zhuzhu_Copilot.ui.tokens import FONT_SMALL, FONT_TITLE  # noqa: E402

ok = True


def check(label, cond, extra=""):
    global ok
    print(f"[{'PASS' if cond else 'FAIL'}] {label}"
          f"{(' | ' + str(extra)) if extra and not cond else ''}")
    ok = ok and bool(cond)


panel = ap.AgentPanel()
panel.resize(1440, 900)          # 真实窗口宽度：验证公告位能正常展开（不再被 420px 封顶）
panel.show()                     # 子控件 isVisible() 依赖祖先链可见，故先显示面板
app.processEvents()
lbl = panel._announce_label

# 离屏平台默认无 CJK 字体（中文渲染成方框），显式指定系统自带中文字体再截图。
# 找一个真实可用的中文字体：字体缺失会让 QLabel 的 sizeHint 偏小，影响宽度断言。
_cjk_font_ok = False
try:
    from PyQt6.QtGui import QFont, QFontDatabase
    _fams = set(QFontDatabase.families())
    for _f in ("Microsoft YaHei UI", "Microsoft YaHei", "微软雅黑", "SimHei", "SimSun"):
        if _f in _fams:
            app.setFont(QFont(_f, 12))
            _cjk_font_ok = True
            print(f"截图字体: {_f}")
            break
    if not _cjk_font_ok:
        print("未找到中文字体，中文将以方框显示（不影响文本/尺寸断言）")
except Exception as _e:                                   # noqa: BLE001
    print("设置字体失败:", _e)

# ---------- 1. 字号 ----------
ss = lbl.styleSheet()
print(f"样式表: {ss}")
check(f"字号已增大为 FONT_TITLE={FONT_TITLE}px（原 FONT_SMALL={FONT_SMALL}px）",
      f"font-size: {FONT_TITLE}px" in ss and FONT_TITLE > FONT_SMALL, ss)

# ---------- 2. 宽度不再封顶 ----------
check("已移除 maximumWidth(420)（恢复默认上限 16777215）",
      lbl.maximumWidth() == 16777215, lbl.maximumWidth())
check("minimumWidth == 0（空间不足时可收缩，不撑破顶栏）",
      lbl.minimumWidth() == 0, lbl.minimumWidth())

# ---------- 3. 不限制显示字数 ----------
LONG = ("这是一条用于验证的超长系统公告：为了确认顶栏公告位不再按字数截断，"
        "此处写入远超六十个字符的文本。" * 6)
assert len(LONG) > 60, len(LONG)
panel._set_announcement({"content": LONG})
got = lbl.text()
print(f"公告长度: 原文 {len(LONG)} 字 → 显示 {len(got)} 字")
check("★ 超长公告完整显示（不再截断成 60 字 + …）", got == LONG, len(got))
check("★ tooltip 为完整原文", lbl.toolTip() == LONG)
check("公告位可见", lbl.isVisible())

# 边界：恰好 61 字（旧逻辑会截断）
edge = "边" * 61
panel._set_announcement({"content": edge})
check("61 字边界同样不截断", lbl.text() == edge, len(lbl.text()))

app.processEvents()
# 回归：空公告仍隐藏
panel._set_announcement({"content": "   "})
check("空公告 → 整块隐藏（不占位）", lbl.text() == "" and lbl.isHidden())

# ---------- 截图 ----------
panel._set_announcement({"content": LONG})
app.processEvents()
top = lbl.parentWidget()
try:
    top.grab().save(os.path.join(ROOT, "scripts", "_verify_announce_topbar.png"))
except Exception as e:                                    # noqa: BLE001
    print("顶栏截图失败:", e)
lbl.grab().save(os.path.join(ROOT, "scripts", "_verify_announce_label.png"))
app.processEvents()
print(f"标签尺寸: {lbl.width()}x{lbl.height()}")
# 真正语义：文本完整的 sizeHint 宽于旧上限 420px —— 说明宽度不再被硬封顶，
# 实际占宽由顶栏布局与 CPU/状态区共享决定（stretch 会分摊剩余空间）。
_hint = lbl.sizeHint().width()
print(f"完整文本 sizeHint 宽度: {_hint}px（旧上限 420px）")
if _cjk_font_ok:
    check("★ 公告文本 sizeHint 超过旧上限 420px（宽度不再封顶，长公告可完整排布）",
          _hint > 420, _hint)
else:
    # 无中文字体时每个汉字只占一个方框宽度，sizeHint 偏小 —— 该断言在此环境不可测，
    # 明确标记 SKIP 而不是伪装成失败。宽度不再封顶已由 maximumWidth 断言覆盖。
    print("[SKIP] ★ 公告文本 sizeHint 宽度断言：离屏环境无 CJK 字体，像素宽度不可测")

print(f"\n{'ALL PASS' if ok else 'HAS FAILURES'}")
sys.stdout.flush()
# AgentPanel 会拉起 WeChatBridge 轮询等后台线程，正常退出时解释器清理会崩溃
# （与本改动无关的历史现象）；用 os._exit 跳过析构，保证退出码反映断言结果。
os._exit(0 if ok else 1)
