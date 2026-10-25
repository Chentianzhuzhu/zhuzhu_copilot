
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
