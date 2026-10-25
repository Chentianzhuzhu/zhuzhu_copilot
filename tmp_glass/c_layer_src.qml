
import QtQuick
Rectangle {
    color: "#0EA5E9"
    layer.enabled: true
    layer.effect: ShaderEffect { fragmentShader: "c_layer_src.frag" }
}
