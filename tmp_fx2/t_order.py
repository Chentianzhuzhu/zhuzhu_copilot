"""进程内验证：收起/展开顺序、DWM 过渡开关、复用浮层、状态终态。"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.getcwd(), "src"))
from PyQt6.QtCore import QRect, Qt, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog  # noqa: E402

from zhuzhu_Copilot.ui import window_fx  # noqa: E402

app = QApplication(sys.argv)
win = QDialog()
win.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
win.setGeometry(QRect(38, 130, 1825, 810))
win.show()

fx = window_fx.WindowFoldAnimator(win, radius=18)

t0 = time.monotonic()


def ms():
    return (time.monotonic() - t0) * 1000.0


fx.prewarm()
print("prewarm done @%.0fms spare=%s" % (ms(), fx._spare is not None))

# 记录调用顺序
order = []
orig_play = fx._play


def play(pix, start, end, mode, on_done, before=None):
    order.append(("play", mode, ms()))
    return orig_play(pix, start, end, mode, on_done, before)


fx._play = play

orig_set_trans = window_fx.set_transitions


def set_trans(v, d):
    order.append(("trans", bool(d), ms()))
    return orig_set_trans(v, d)


window_fx.set_transitions = set_trans

# 记录 showMinimized 的调用时刻，验证它发生在浮层显示之后
orig_min = win.showMinimized


def show_min():
    order.append(("showMinimized", None, ms()))
    return orig_min()


win.showMinimized = show_min


def step1():
    print("conceal ->", fx.start_conceal(), "busy=", fx.busy(),
          "sprite=", fx._sprite is not None, "min=", win.isMinimized())


def step2():
    print("  after conceal: min=%s busy=%s spare=%s trans_off=%s"
          % (win.isMinimized(), fx.busy(), fx._spare is not None, fx._trans_seq))
    print("reveal ->", fx.start_reveal(), "sprite=", fx._sprite is not None)


def step3():
    print("  after reveal: min=%s busy=%s geo=%s restored_at=%.0f"
          % (win.isMinimized(), fx.busy(), win.geometry(), fx.recently_restored()))
    app.quit()


QTimer.singleShot(300, step1)
QTimer.singleShot(1000, step2)
QTimer.singleShot(1800, step3)
app.exec()

print("\n--- order ---")
for item in order:
    print("  %-14s %-8s %.0fms" % (item[0], item[1], item[2]))
print("final min=%s" % win.isMinimized())
