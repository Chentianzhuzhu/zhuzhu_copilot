import sys
from pathlib import Path
from PyQt6.QtCore import QUrl, QTimer
from PyQt6.QtGui import QImage, QColor, QPainter, QLinearGradient
from PyQt6.QtWidgets import QApplication
from PyQt6.QtQuickWidgets import QQuickWidget

BASE = Path("tmp_glass").resolve()
app = QApplication(sys.argv)

img = QImage(600, 400, QImage.Format.Format_ARGB32)
img.fill(QColor("#101828"))
p = QPainter(img)
g = QLinearGradient(0, 0, 600, 400)
g.setColorAt(0, QColor("#1E3A8A")); g.setColorAt(0.5, QColor("#0EA5E9")); g.setColorAt(1, QColor("#F59E0B"))
p.fillRect(img.rect(), g)
p.setPen(QColor("#FFFFFF"))
for i in range(0, 600, 40):
    p.drawLine(i, 0, i, 400); p.drawLine(0, i, 600, i)
p.end()
img.save(str(BASE / "bg.png"))

(BASE / "proto.frag").write_text("""
uniform sampler2D src;
uniform float qt_Opacity;
uniform float time;
varying vec2 qt_TexCoord0;
void main() {
    vec4 c = texture2D(src, qt_TexCoord0);
    gl_FragColor = vec4(c.r, c.g, 1.0 - c.b, 0.85) * qt_Opacity;
}
""", encoding="utf-8")

(BASE / "proto.qml").write_text("""
import QtQuick
Item {
    id: root
    property real uTime: 0.0
    Image { id: bg; anchors.fill: parent; source: "bg.png"; visible: false }
    ShaderEffect {
        anchors.fill: parent
        property real time: root.uTime
        property variant src: bg
        fragmentShader: "proto.frag"
    }
}
""", encoding="utf-8")

w = QQuickWidget()
w.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
w.setSource(QUrl.fromLocalFile(str(BASE / "proto.qml")))
print("status:", w.status())
for e in w.errors():
    print("  QML错误:", e.toString())
w.resize(600, 400)
w.show()
QTimer.singleShot(500, app.quit)
app.exec()
grab = w.grab()
print("grab:", grab.size(), grab.isNull())
grab.save(str(BASE / "proto_out.png"))
