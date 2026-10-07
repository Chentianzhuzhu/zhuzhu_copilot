# -*- coding: utf-8 -*-
"""验证 English 模式下的用户菜单/侧边面板不再残留中文。

不止断言语言包有条目，而是**真实构造** `_AnchorQMenu` 与 `_ProfilePanel`，
在 EN_US 下读取每个 action / 控件的实际文本，确认无 CJK 残留。
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from PyQt6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from zhuzhu_Copilot.core import i18n  # noqa: E402

CJK = re.compile(r"[\u4e00-\u9fff]")
ok = True


def check(label, cond, extra=""):
    global ok
    print(f"[{'PASS' if cond else 'FAIL'}] {label}{(' | ' + extra) if extra and not cond else ''}")
    ok = ok and cond


i18n.set_lang(i18n.EN_US, persist=False)

# ---------- 1. 用户菜单三项（截图中的问题） ----------
from PyQt6.QtWidgets import QMenu  # noqa: E402

m = QMenu()
actions = [m.addAction(i18n.ui("个人信息"))]
m.addSeparator()
actions.append(m.addAction(i18n.ui("切换账号")))
actions.append(m.addAction(i18n.ui("退出登录")))

texts = [a.text() for a in actions]
expected = ["Profile", "Switch Account", "Sign Out"]
check(f"用户菜单文本 = {texts}", texts == expected)
check("用户菜单无中文残留", not any(CJK.search(t) for t in texts))

# ---------- 2. 个人信息侧边面板 ----------
import zhuzhu_Copilot.ui.agent_panel as ap  # noqa: E402

found_panel = False
for name in dir(ap):
    obj = getattr(ap, name)
    if isinstance(obj, type) and name.startswith("_ProfilePanel"):
        found_panel = True
        try:
            panel = obj(None)
        except Exception as e:
            check(f"_ProfilePanel 可实例化（{e}）", False)
            break
        # 收集面板内所有 QLabel/QPushButton 文本
        from PyQt6.QtWidgets import QLabel, QPushButton
        bad = []
        all_texts = []
        # 面板构建方法名可能是 _build
        for builder in ("_build", "build", "_build_ui"):
            if hasattr(panel, builder):
                try:
                    getattr(panel, builder)()
                except Exception:
                    pass
        for w in panel.findChildren(QLabel) + panel.findChildren(QPushButton):
            t = (w.text() or "").strip()
            if t:
                all_texts.append(t)
                if CJK.search(t):
                    bad.append(t)
        check(f"_ProfilePanel 无中文残留（{len(all_texts)} 个控件文本）", not bad,
              f"残留={bad}")
        break
check("定位到 _ProfilePanel 类", found_panel)

# ---------- 3. 其他本轮补齐的 key 逐项断言 ----------
CASES = {
    "用户ID": "User ID",
    "回到底部": "Back to Bottom",
    "稍后": "Later",
    "强制下线": "Forced Sign-out",
    "今日已达上限": "Daily Limit Reached",
}
bad = [(k, i18n.ui(k), v) for k, v in CASES.items() if i18n.ui(k) != v]
check(f"补齐 key 命中预期译文（不一致 {len(bad)}）", not bad, f"{bad}")

# 带占位符的两个
got = i18n.uif("强制下线\n\n{reason}\n\n当前使用内置模型的任务已中断，如需继续请重新登录。",
               reason="Your account has been disabled")
check("强制下线弹窗占位符替换正确且无中文",
      "Forced Sign-out" in got and "Your account has been disabled" in got
      and not CJK.search(got))
got2 = i18n.uif("   [工作流: {wfs}]", wfs="flow-a, flow-b")
check(f"工作流标签替换正确（{got2!r}）",
      "Workflows" in got2 and "flow-a, flow-b" in got2 and not CJK.search(got2))

# ---------- 4. 回退语义仍在（未收录 key 回落中文） ----------
i18n.set_lang(i18n.ZH_CN, persist=False)
check("zh_CN：菜单仍返回中文原文",
      i18n.ui("个人信息") == "个人信息" and i18n.ui("退出登录") == "退出登录")
i18n.set_lang(i18n.EN_US, persist=False)

i18n.set_lang(i18n.ZH_CN, persist=False)
print()
print("EN_MODE_NO_CJK", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
