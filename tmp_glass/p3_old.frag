
uniform sampler2D src;
uniform float qt_Opacity;
varying vec2 qt_TexCoord0;
void main() { vec4 c = texture2D(src, qt_TexCoord0); gl_FragColor = vec4(1.0-c.r, c.g, 0.0, 0.9) * qt_Opacity; }
