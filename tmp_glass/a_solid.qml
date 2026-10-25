
import QtQuick
Item {
    Rectangle { anchors.fill: parent; color: "#0EA5E9" }
    ShaderEffect { anchors.fill: parent; fragmentShader: "a_solid.frag" }
}
