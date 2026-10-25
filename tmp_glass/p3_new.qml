
import QtQuick
Item {
    id: root
    Image { id: bg; anchors.fill: parent; source: "bg2.png"; visible: false }
    ShaderEffect {
        anchors.fill: parent
        property real time: 0.0
        property variant src: bg
        fragmentShader: "p3_new.frag"
    }
}
