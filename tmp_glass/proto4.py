import sys
from pathlib import Path
from PyQt6.QtCore import QUrl, QTimer
from PyQt6.QtWidgets import QApplication
from PyQt6.QtQuickWidgets import QQuickWidget

BASE = Path("tmp_glass").resolve()
app = QApplication(sys.argv)

CASES = {
 "a_solid": ("""
uniform float qt_Opacity;
varying vec2 qt_TexCoord0;
void main() { gl_FragColor = vec4(1.0, 0.0, 0.0, 1.0) * qt_Opacity; }
""", """
import QtQuick
Item {
    Rectangle { anchors.fill: parent; color: "#0EA5E9" }
    ShaderEffect { anchors.fill: parent; fragmentShader: "a_solid.frag" }
}
"""),
 "b_layer_solid": ("""
uniform float qt_Opacity;
varying vec2 qt_TexCoord0;
void main() { gl_FragColor = vec4(0.0, 1.0, 0.0, 1.0) * qt_Opacity; }
""", """
import QtQuick
Rectangle {
    color: "#0EA5E9"
    layer.enabled: true
    layer.effect: ShaderEffect { fragmentShader: "b_layer_solid.frag" }
}
"""),
 "c_layer_src": ("""
uniform sampler2D source;
uniform float qt_Opacity;
varying vec2 qt_TexCoord0;
void main() { vec4 c = texture2D(source, qt_TexCoord0); gl_FragColor = vec4(1.0-c.r, c.g, 0.0, 1.0) * qt_Opacity; }
""", """
import QtQuick
Rectangle {
    color: "#0EA5E9"
    layer.enabled: true
    layer.effect: ShaderEffect { fragmentShader: "c_layer_src.frag" }
}
"""),
}

for tag, (frag, qml) in CASES.items():
    (BASE / (tag + ".frag")).write_text(frag, encoding="utf-8")
    (BASE / (tag + ".qml")).write_text(qml, encoding="utf-8")
    w = QQuickWidget(); w.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
    w.setSource(QUrl.fromLocalFile(str(BASE / (tag + ".qml"))))
    w.resize(200, 120); w.show()
    QTimer.singleShot(350, app.quit); app.exec()
    im = w.grab().toImage()
    print("[%s] %s 中心=%s" % (tag, w.status().name, im.pixelColor(100, 60).name()))
    im.save(str(BASE / (tag + ".png")))
    w.close()
