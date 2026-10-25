import sys
from pathlib import Path
from PyQt6.QtCore import QUrl, QTimer
from PyQt6.QtGui import QImage, QColor, QPainter
from PyQt6.QtWidgets import QApplication
from PyQt6.QtQuickWidgets import QQuickWidget

BASE = Path("tmp_glass").resolve()
app = QApplication(sys.argv)

img = QImage(300, 200, QImage.Format.Format_ARGB32)
img.fill(QColor("#0EA5E9")); img.save(str(BASE/"bg2.png"))

OLD = """
uniform sampler2D src;
uniform float qt_Opacity;
varying vec2 qt_TexCoord0;
void main() { vec4 c = texture2D(src, qt_TexCoord0); gl_FragColor = vec4(1.0-c.r, c.g, 0.0, 0.9) * qt_Opacity; }
"""
NEW = """
#version 440
layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    float time;
} ubuf;
layout(binding = 1) uniform sampler2D src;
void main() { vec4 c = texture2D(src, qt_TexCoord0); fragColor = vec4(1.0-c.r, c.g, 0.0, 0.9) * ubuf.qt_Opacity; }
"""

for tag, frag in (("old", OLD), ("new", NEW)):
    (BASE / ("p3_%s.frag" % tag)).write_text(frag, encoding="utf-8")
    (BASE / ("p3_%s.qml" % tag)).write_text("""
import QtQuick
Item {
    id: root
    Image { id: bg; anchors.fill: parent; source: "bg2.png"; visible: false }
    ShaderEffect {
        anchors.fill: parent
        property real time: 0.0
        property variant src: bg
        fragmentShader: "p3_%s.frag"
    }
}
""" % tag, encoding="utf-8")
    w = QQuickWidget(); w.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
    w.setSource(QUrl.fromLocalFile(str(BASE / ("p3_%s.qml" % tag))))
    w.resize(300, 200); w.show()
    QTimer.singleShot(400, app.quit); app.exec()
    im = w.grab().toImage()
    print("[%s] status=%s 中心色=%s" % (tag, w.status().name, im.pixelColor(150, 100).name()))
    im.save(str(BASE / ("p3_%s.png" % tag)))
    w.close()
