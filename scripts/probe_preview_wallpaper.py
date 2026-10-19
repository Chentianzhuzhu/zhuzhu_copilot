# -*- coding: utf-8 -*-
"""探针：自定义背景（壁纸）开启时，预览面板各层实际计算出的边框色/底色。

对 Wallpaper active() 打桩为 True，构建 AgentPanel 后打印：
  - 右 dock 栏容器、codeWin 根、html_view / text / img_scroll / img_label 的 styleSheet
  - 媒体页与 diff 视图若有实体色也一并列出
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from zhuzhu_Copilot.ui import agent_panel as ap  # noqa: E402

# 打桩：壁纸视为可用
ap.app_wallpaper.active = lambda: True

# 触发透明态重算（模拟启动时壁纸已设置）
ap.refresh_surface_mode()
print("SURFACE_TRANSPARENT =", ap._SURFACE_TRANSPARENT)
print("_panel_surface('BG') =", ap._panel_surface("BG"))
print("_panel_stroke('BORDER') =", ap._panel_stroke("BORDER"))

panel = ap.AgentPanel()
cw = getattr(panel, "code_win", None)
print("\ncode_win:", type(cw).__name__ if cw else None)
if cw is not None:
    print("root QSS:", cw.styleSheet())
    for name in ("html_view", "text", "img_scroll", "img_label", "mode", "title"):
        w = getattr(cw, name, None)
        if w is not None:
            print(f"--- {name} ---\n{w.styleSheet()}")
col = panel._dock_column("right")
print("\ndockCol right QSS:", col.styleSheet())
