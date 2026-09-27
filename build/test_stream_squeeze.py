# -*- coding: utf-8 -*-
"""复现流式生成的挤压：QGraphicsOpacityEffect 是否导致换行 QLabel 高度与内容不符。
对比：带 effect（动画后残留） vs 不带 effect 的换行标签在相同宽度下的高度。"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import (QApplication, QWidget, QLabel, QVBoxLayout,
                             QHBoxLayout, QScrollArea, QGraphicsOpacityEffect)
from PyQt6.QtCore import QPropertyAnimation, QEasingCurve

app = QApplication(sys.argv)


def make(dst_w: int, text: str):
    sc = QScrollArea()
    sc.setWidgetResizable(True)
    cont = QWidget()
    lay = QVBoxLayout(cont)
    lay.setSpacing(10)
    lay.addStretch(1)
    sc.setWidget(cont)
    sc.resize(dst_w + 40, 500)
    sc.show()
    return sc, cont, lay


TEXT = ("这是一段用于复现流式生成时气泡挤压问题的较长的中文回复文本，" * 12) + \
       ("very-long-unbroken-token-" * 40) + \
       ("流式输出会在同一气泡内反复 setText，检查高度是否按宽度正确换行折算。" * 10)


def run(with_effect: bool, sim_count: int = 6):
    w, cont, lay = make(640, "")
    lbl = QLabel("")
    lbl.setWordWrap(True)
    lbl.setMaximumWidth(560)
    lbl.setStyleSheet("border-radius: 16px; padding: 10px 14px;")
    if with_effect:
        eff = QGraphicsOpacityEffect(lbl)
        lbl.setGraphicsEffect(eff)
        anim = QPropertyAnimation(eff, b"opacity", lbl)
        anim.setDuration(50)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.finished.connect(lambda w=lbl: w.setGraphicsEffect(None))
        anim.start()
    row = QHBoxLayout()
    row.addWidget(lbl)
    row.addStretch(1)
    lay.insertLayout(lay.count() - 1, row)
    # 模拟流式分块 setText
    for i in range(sim_count):
        chunk = TEXT[: (i + 1) * (len(TEXT) // sim_count)]
        lbl.setText(f"<span>{chunk}</span>")
        app.processEvents()
    lbl.setText(f"<span>{TEXT}</span>")
    for _ in range(5):
        app.processEvents()
    hfw = lbl.heightForWidth(lbl.width())
    eq = hfw == -1
    h = lbl.height()
    return eq, hfw if not eq else lbl.sizeHint().height(), h, lbl.width()


for with_eff in (False, True):
    try:
        eq, hfw, h, w_lbl = run(with_eff)
        print("effect=%s label_w=%d hfw=%d actual_h=%d squeezed=%s" % (
            with_eff, w_lbl, hfw, h, h < hfw), flush=True)
    except Exception as e:
        print("effect=%s EXC: %s" % (with_eff, e), flush=True)
os._exit(0)