import os, sys, time
os.environ["GLASS_KEEP_CAPTURABLE"] = "1"
from pathlib import Path
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QLinearGradient
from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget
sys.path.insert(0, "src")
from zhuzhu_Copilot.ui.glass_window import GlassWindow

OUT = Path("tmp_glass").resolve()
app = QApplication(sys.argv)

# 铺一层"桌面"：桌面壁纸无法控制，这里用一个大背景窗口模拟
desk = QWidget()
desk.setWindowFlags(__import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.WindowType.FramelessWindowHint)
desk.setGeometry(60, 60, 1100, 720)
desk.setStyleSheet("background: #0B1220;")
class Desk(QWidget):
    def paintEvent(self, e):
        p = QPainter(self)
        g = QLinearGradient(0, 0, self.width(), self.height())
        g.setColorAt(0.0, QColor("#1E3A8A")); g.setColorAt(0.4, QColor("#0EA5E9"))
        g.setColorAt(0.7, QColor("#7C3AED")); g.setColorAt(1.0, QColor("#F59E0B"))
        p.fillRect(self.rect(), g)
        for gy in range(0, self.height() + 60, 60):
            for gx in range(0, self.width() + 60, 60):
                if ((gx // 60) + (gy // 60)) % 2 == 0:
                    p.fillRect(gx, gy, 60, 60, QColor(255, 255, 255, 30))
        f = QFont(); f.setPointSize(40); f.setBold(True)
        p.setFont(f); p.setPen(QColor(255, 255, 255, 220))
        p.drawText(40, 110, "DESKTOP 背景")
        p.drawText(40, 170, "折射 / 反射 / 边缘高光")
desk.__class__ = Desk
desk.show()

content = QWidget()
lay = QVBoxLayout(content)
lbl = QLabel("液体玻璃窗口（GPU 实时计算）")
lbl.setStyleSheet("color: white; font-size: 18px; font-weight: 700;")
lay.addWidget(lbl)
lay.addWidget(QLabel("这是插件页面内容区：玻璃壳在下面，折射的是真实桌面。"))
lay.addStretch(1)

win = GlassWindow(title="性能看板", width=680, height=440, content=content)
win.showCentered()
print("gpu_active:", win.gpu_active(), "| failure:", win.surface().failure())

def shot():
    scr = win.screen()
    g = win.frameGeometry()
    img = scr.grabWindow(0, g.x(), g.y(), g.width(), g.height())
    img.save(str(OUT / "window_shot.png"))
    print("截图:", img.size(), img.isNull())
    app.quit()

QTimer.singleShot(1200, shot)
app.exec()
