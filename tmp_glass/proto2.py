import sys
from pathlib import Path
from PyQt6.QtCore import QUrl, QTimer
from PyQt6.QtWidgets import QApplication
from PyQt6.QtQuickWidgets import QQuickWidget

BASE = Path("tmp_glass").resolve()
app = QApplication(sys.argv)
(BASE / "p2.qml").write_text("""
import QtQuick
Rectangle {
    width: 300; height: 200
    color: "#0EA5E9"
    Rectangle { x: 40; y: 40; width: 100; height: 60; color: "#F59E0B" }
}
""", encoding="utf-8")
w = QQuickWidget()
w.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
w.setSource(QUrl.fromLocalFile(str(BASE / "p2.qml")))
print("status:", w.status(), [e.toString() for e in w.errors()])
w.resize(300, 200); w.show()
QTimer.singleShot(400, app.quit)
app.exec()
for name, fn in (("grab", w.grab), ("framebuffer", w.grabFramebuffer)):
    im = fn()
    print(name, im.size(), im.isNull(), "pixel(150,100)=", im.pixelColor(150, 100).name() if not im.isNull() else "-")
    im.save(str(BASE / ("p2_%s.png" % name)))
