"""设置页布局探针（加载真实中文字体，避免 offscreen 无字体导致的度量失真）。

目的：定位「设置页右侧文字被挤压 / 被裁切」到底出现在哪一行、由什么撑宽。
offscreen 平台默认没有任何字体（QFontDatabase.families() 为空），文字度量会偏小，
所以必须显式加载系统字体后再量。

用法：python scripts/_probe_settings_layout.py
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtCore import Qt                                                # noqa: E402
from PyQt6.QtGui import QFont, QFontDatabase                               # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton              # noqa: E402
from PyQt6.QtWidgets import QComboBox, QCheckBox, QPlainTextEdit           # noqa: E402
from PyQt6.QtWidgets import QListWidget, QSlider, QLineEdit, QWidget      # noqa: E402

FONTS = ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msyhbd.ttc",
         "C:/Windows/Fonts/simsun.ttc")
LEAF = (QLabel, QPushButton, QLineEdit, QComboBox, QCheckBox, QPlainTextEdit,
        QListWidget, QSlider)


def _pump(app, seconds):
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.001)


def main():
    app = QApplication.instance() or QApplication([])
    loaded = 0
    for f in FONTS:
        if os.path.isfile(f) and QFontDatabase.addApplicationFont(f) >= 0:
            loaded += 1
    fams = QFontDatabase.families()
    print(f"已加载字体文件 {loaded} 个；可用字体族 {len(fams)}；示例={list(fams)[:6]}")
    if "Microsoft YaHei" in fams:
        app.setFont(QFont("Microsoft YaHei", 9))

    from zhuzhu_Copilot.ui import agent_panel as ap
    from zhuzhu_Copilot.ui.agent_panel import _AgentSettingsDialog

    dlg = _AgentSettingsDialog(None)
    dlg.resize(991, 687)
    dlg.show()
    _pump(app, 0.4)
    dlg._ensure_all_pages()
    _pump(app, 0.4)

    sc = dlg._page_scroll
    print(f"\n对话框 {dlg.width()}x{dlg.height()}｜导航宽={dlg.nav.width()}｜"
          f"滚动区={sc.width()}x{sc.height()}｜视口={sc.viewport().width()}x"
          f"{sc.viewport().height()}｜水平滚动条可见={sc.horizontalScrollBar().isVisible()}")

    for i, (name, _k) in enumerate(ap._NAV_ITEMS):
        dlg.nav.setCurrentRow(i)
        _pump(app, 0.2)
        page = dlg.stack.currentWidget()
        if page is None:
            continue
        vp = sc.viewport().width()
        need = page.minimumSizeHint().width()
        flag = "  ★超宽" if need > vp else ""
        print(f"\n[页{i:>2}] {name:<10} 页面宽={page.width():<4} "
              f"最小需求={need:<4} 视口={vp}{flag}")
        # 列出该页「最小宽度需求」最大的三个控件：溢出时就是它们把页面撑宽的
        rows = []
        for c in page.findChildren(QWidget):
            if not isinstance(c, LEAF):
                continue
            txt = ""
            try:
                txt = (c.text() if hasattr(c, "text") else "")[:24]
            except Exception:
                pass
            rows.append((c.minimumSizeHint().width(), type(c).__name__,
                         f"w={c.minimumWidth()}", txt))
        rows.sort(reverse=True)
        for cneed, cname, cmin, txt in rows[:3]:
            print(f"      {cname:<13} minHint={cneed:<4} {cmin:<8} txt={txt!r}")

    # 外观页逐行（滑杆行最容易把右侧数值挤没）
    dlg.nav.setCurrentRow(len(ap._NAV_ITEMS) - 1)
    _pump(app, 0.3)
    page = dlg.stack.currentWidget()
    print(f"\n=== 外观页逐控件（页面宽={page.width()}）===")
    for c in page.findChildren(QLabel) + page.findChildren(QSlider):
        g = c.geometry()
        if g.y() > 320 or g.x() < 600:
            continue
        txt = c.text()[:14] if hasattr(c, "text") else "<slider>"
        print(f"  {type(c).__name__:<8} x={g.x():<4} y={g.y():<4} w={g.width():<4} "
              f"minHint={c.minimumSizeHint().width():<4} {txt!r}")
    dlg.done(0)


if __name__ == "__main__":
    main()
