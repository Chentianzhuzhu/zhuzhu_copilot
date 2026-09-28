# -*- coding: utf-8 -*-
"""验证托盘激活崩溃修复：PyQt6 枚举名 + 四种激活原因不再抛 AttributeError"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
from PyQt6.QtWidgets import QApplication, QSystemTrayIcon

app = QApplication(sys.argv)
import zhuzhu_Copilot.ui.main_window as mw
from zhuzhu_Copilot.ui.main_window import MainWindow

# 1. 枚举名断言
assert hasattr(QSystemTrayIcon.ActivationReason, "DoubleClick"), "PyQt6 缺少 DoubleClick"
assert not hasattr(QSystemTrayIcon.ActivationReason, "DblClick"), "PyQt6 不应再有 DblClick（旧名）"
print("PASS: PyQt6 枚举名为 DoubleClick（旧名 DblClick 已移除）")


class FakeWin:
    """最小窗口替身：齐全槽函数所需接口"""
    def __init__(self):
        self.restored = 0
    def _restore_from_tray(self, *_):
        self.restored += 1
    def raise_(self):
        pass
    def activateWindow(self):
        pass


# 2. 四种激活原因逐一触发（模拟左击/双击/右击/中键）
expect = {
    QSystemTrayIcon.ActivationReason.Trigger: 1,
    QSystemTrayIcon.ActivationReason.DoubleClick: 1,
    QSystemTrayIcon.ActivationReason.Context: 0,
    QSystemTrayIcon.ActivationReason.MiddleClick: 0,
}
for reason, want in expect.items():
    f = FakeWin()
    try:
        MainWindow._on_tray_activated(f, reason)
    except Exception as e:
        print("FAIL: reason=%s 抛异常 %s" % (reason, e))
        os._exit(1)
    assert f.restored == want, "reason=%s 恢复次数=%d 期望=%d" % (reason, f.restored, want)
print("PASS: 左击/双击恢复主窗、右击/中键仅激活窗口，无任何异常")
print("ALL-PASS")
os._exit(0)