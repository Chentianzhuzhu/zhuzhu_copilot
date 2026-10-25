"""液体玻璃着色器原型：ctypes 直连 OpenGL 2.1，离屏 FBO 渲染成 PNG 供肉眼验收。"""

import ctypes
import math
import sys
from ctypes import POINTER, byref, c_char_p, c_float, c_int, c_uint, c_void_p
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage, QLinearGradient, QOffscreenSurface, QOpenGLContext, QPainter, QSurfaceFormat
from PyQt6.QtOpenGL import QOpenGLFramebufferObject
from PyQt6.QtWidgets import QApplication

OUT = Path(__file__).resolve().parent

# ---------------------------------------------------------------- GL FFI
GL_TEXTURE_2D = 0x0DE1
GL_RGBA = 0x1908
GL_UNSIGNED_BYTE = 0x1401
GL_TEXTURE_MIN_FILTER = 0x2801
GL_TEXTURE_MAG_FILTER = 0x2800
GL_LINEAR = 0x2601
GL_TEXTURE_WRAP_S = 0x2802
GL_TEXTURE_WRAP_T = 0x2803
GL_CLAMP_TO_EDGE = 0x812F
GL_VERTEX_SHADER = 0x8B31
GL_FRAGMENT_SHADER = 0x8B30
GL_COMPILE_STATUS = 0x8B81
GL_LINK_STATUS = 0x8B82
GL_QUADS = 0x0007
GL_BLEND = 0x0BE2
GL_SRC_ALPHA = 0x0302
GL_ONE_MINUS_SRC_ALPHA = 0x0303
GL_PROJECTION = 0x1701
GL_MODELVIEW = 0x1700
GL_TEXTURE0 = 0x84C0
GL_UNPACK_ALIGNMENT = 0x0CF5


class GL:
    """最小 OpenGL 绑定（GL1.1 走 opengl32.dll，GL2.0 走 wglGetProcAddress）。"""

    def __init__(self):
        self.dll = ctypes.WinDLL("opengl32")
        self._wgl = self.dll.wglGetProcAddress
        self._wgl.restype = c_void_p
        self._wgl.argtypes = [c_char_p]

    def _proc(self, name):
        addr = self._wgl(name.encode())
        if not addr:
            addr = ctypes.cast(getattr(self.dll, name), c_void_p).value
        return addr

    def fn(self, name, restype, *argtypes):
        addr = self._proc(name)
        if not addr:
            raise AttributeError("OpenGL 函数不可用: " + name)
        return ctypes.CFUNCTYPE(restype, *argtypes)(addr)


def build_gl():
    g = GL()
    f = {}
    f["glViewport"] = g.fn("glViewport", None, c_int, c_int, c_int, c_int)
    f["glClearColor"] = g.fn("glClearColor", None, c_float, c_float, c_float, c_float)
    f["glClear"] = g.fn("glClear", None, c_uint)
    f["glEnable"] = g.fn("glEnable", None, c_uint)
    f["glDisable"] = g.fn("glDisable", None, c_uint)
    f["glBlendFunc"] = g.fn("glBlendFunc", None, c_uint, c_uint)
    f["glGenTextures"] = g.fn("glGenTextures", None, c_int, POINTER(c_uint))
    f["glBindTexture"] = g.fn("glBindTexture", None, c_uint, c_uint)
    f["glTexImage2D"] = g.fn("glTexImage2D", None, c_uint, c_int, c_int, c_int,
                             c_int, c_uint, c_uint, c_void_p)
    f["glTexParameteri"] = g.fn("glTexParameteri", None, c_uint, c_uint, c_int)
    f["glPixelStorei"] = g.fn("glPixelStorei", None, c_uint, c_int)
    f["glActiveTexture"] = g.fn("glActiveTexture", None, c_uint)
    f["glMatrixMode"] = g.fn("glMatrixMode", None, c_uint)
    f["glLoadIdentity"] = g.fn("glLoadIdentity", None)
    f["glOrtho"] = g.fn("glOrtho", None, ctypes.c_double, ctypes.c_double,
                        ctypes.c_double, ctypes.c_double, ctypes.c_double, ctypes.c_double)
    f["glBegin"] = g.fn("glBegin", None, c_uint)
    f["glEnd"] = g.fn("glEnd", None)
    f["glVertex2f"] = g.fn("glVertex2f", None, c_float, c_float)
    f["glTexCoord2f"] = g.fn("glTexCoord2f", None, c_float, c_float)
    f["glCreateShader"] = g.fn("glCreateShader", c_uint, c_uint)
    f["glShaderSource"] = g.fn("glShaderSource", None, c_uint, c_int, POINTER(c_char_p),
                               POINTER(c_int))
    f["glCompileShader"] = g.fn("glCompileShader", None, c_uint)
    f["glGetShaderiv"] = g.fn("glGetShaderiv", None, c_uint, c_uint, POINTER(c_int))
    f["glGetShaderInfoLog"] = g.fn("glGetShaderInfoLog", None, c_uint, c_int,
                                   POINTER(c_int), c_char_p)
    f["glCreateProgram"] = g.fn("glCreateProgram", c_uint)
    f["glAttachShader"] = g.fn("glAttachShader", None, c_uint, c_uint)
    f["glLinkProgram"] = g.fn("glLinkProgram", None, c_uint)
    f["glGetProgramiv"] = g.fn("glGetProgramiv", None, c_uint, c_uint, POINTER(c_int))
    f["glGetProgramInfoLog"] = g.fn("glGetProgramInfoLog", None, c_uint, c_int,
                                    POINTER(c_int), c_char_p)
    f["glUseProgram"] = g.fn("glUseProgram", None, c_uint)
    f["glGetUniformLocation"] = g.fn("glGetUniformLocation", c_int, c_uint, c_char_p)
    f["glUniform1f"] = g.fn("glUniform1f", None, c_int, c_float)
    f["glUniform2f"] = g.fn("glUniform2f", None, c_int, c_float, c_float)
    f["glUniform1i"] = g.fn("glUniform1i", None, c_int, c_int)
    return f


def compile_program(gl, vs_src, fs_src):
    def one(kind, src):
        sh = gl["glCreateShader"](kind)
        buf = c_char_p(src.encode("utf-8"))
        gl["glShaderSource"](sh, 1, byref(buf), None)
        gl["glCompileShader"](sh)
        ok = c_int(0)
        gl["glGetShaderiv"](sh, GL_COMPILE_STATUS, byref(ok))
        if not ok.value:
            log = ctypes.create_string_buffer(8192)
            ln = c_int(0)
            gl["glGetShaderInfoLog"](sh, 8192, byref(ln), log)
            raise RuntimeError("shader 编译失败:\n" + log.value.decode("utf-8", "replace"))
        return sh

    vs, fs = one(GL_VERTEX_SHADER, vs_src), one(GL_FRAGMENT_SHADER, fs_src)
    prog = gl["glCreateProgram"]()
    gl["glAttachShader"](prog, vs)
    gl["glAttachShader"](prog, fs)
    gl["glLinkProgram"](prog)
    ok = c_int(0)
    gl["glGetProgramiv"](prog, GL_LINK_STATUS, byref(ok))
    if not ok.value:
        log = ctypes.create_string_buffer(8192)
        ln = c_int(0)
        gl["glGetProgramInfoLog"](prog, 8192, byref(ln), log)
        raise RuntimeError("program 链接失败:\n" + log.value.decode("utf-8", "replace"))
    return prog


VS = """
#version 120
varying vec2 vUV;
void main() {
    vUV = gl_MultiTexCoord0.xy;
    gl_Position = ftransform();
}
"""

FS = """
#version 120
uniform sampler2D uBg;
uniform vec2  uSize;
uniform float uTime;
uniform float uRadius;
uniform float uMargin;
uniform float uThickness;
uniform float uAlpha;
uniform float uLight;
uniform float uPad;
uniform vec2 uTex;
uniform float uDebug;
varying vec2 vUV;

float sqD(vec2 p, vec2 b, float n) {
    vec2 q = abs(p) / b;
    return pow(pow(q.x, n) + pow(q.y, n), 1.0 / n) - 1.0;
}

vec2 sqN(vec2 p, vec2 b, float n) {
    float e = 1.5;
    float d0 = sqD(p, b, n);
    float dx = sqD(p + vec2(e, 0.0), b, n) - d0;
    float dy = sqD(p + vec2(0.0, e), b, n) - d0;
    vec2 g = vec2(dx, dy);
    float len = length(g);
    return len > 1e-6 ? g / len : vec2(0.0, 0.0);
}

vec4 blurBg(vec2 uv, float r) {
    vec2 px = 1.0 / uSize;
    vec4 acc = texture2D(uBg, clamp(uv, 0.0, 1.0)) * 0.30;
    float wsum = 0.30;
    for (int i = -1; i <= 1; i++) {
        for (int j = -1; j <= 1; j++) {
            if (i == 0 && j == 0) continue;
            float w = (i == 0 || j == 0) ? 0.10 : 0.055;
            acc += texture2D(uBg, clamp(uv + vec2(float(i), float(j)) * px * r, 0.0, 1.0)) * w;
            wsum += w;
        }
    }
    return acc / wsum;
}

void main() {
    vec2 px = vUV * uSize;
    vec2 c = uSize * 0.5;
    vec2 p = px - c;
    vec2 half_ = c - uMargin;
    // 背景贴图 = 窗口区域 + 四周 uPad 像素；把窗口像素映射到贴图 uv
    vec2 tuv = (vec2(uPad) + vec2(vUV.x, 1.0 - vUV.y) * uSize) / uTex;

    float n = 4.0;
    float d = sqD(p, half_, n);
    float dpx = d * min(half_.x, half_.y);   // 近似像素距离，0=边缘，<0 内部

    if (dpx > 0.0) {                          // 外部：软阴影 + 外圈冷光
        float sh = exp(-dpx / (uMargin * 0.34)) * 0.46;
        float halo = exp(-dpx / (uMargin * 1.1)) * 0.07;
        gl_FragColor = vec4(0.01 + halo * 0.4, 0.02 + halo * 0.5, 0.05 + halo * 0.8, sh);
        return;
    }

    float depth = -dpx;
    float lens = smoothstep(0.0, min(half_.x, half_.y) * 0.30, depth);   // 0=边缘 1=中心
    vec2 nrm = sqN(p, half_, n);

    float wob = sin(p.y * 0.017 + uTime * 1.05) * cos(p.x * 0.013 - uTime * 0.75);
    nrm = normalize(nrm + vec2(wob, -wob) * 0.085);

    vec2 off = nrm * (1.0 - lens) * uThickness / uTex;
    vec2 ruv = clamp(tuv + off, vec2(0.0), vec2(1.0));
    vec3 refr = vec3(
        blurBg(ruv + off * 0.30, 1.6).r,
        blurBg(ruv, 2.0).g,
        blurBg(ruv - off * 0.30, 2.4).b);

    if (uDebug > 0.5 && uDebug < 1.5) { gl_FragColor = vec4(refr, 1.0); return; }
    vec3 tint = vec3(0.90, 0.95, 1.0);
    vec3 col = mix(refr, tint, 0.13) * 1.05;

    vec2 L = vec2(cos(uLight), sin(uLight));
    float rimL = clamp(dot(nrm, L), 0.0, 1.0);

    // 边缘菲涅尔 + 内描边（iOS 玻璃的亮边）
    float edge = smoothstep(-2.2, 0.0, dpx);
    col += vec3(1.0) * edge * (0.06 + 0.80 * pow(rimL, 2.6));
    col += vec3(0.66, 0.86, 1.0) * edge * 0.26 * (1.0 - rimL);
    // 边缘内侧一圈极薄的暗线，勾出玻璃厚度
    float innerLine = smoothstep(-5.0, -2.2, dpx) * (1.0 - smoothstep(-2.2, -1.6, dpx));
    col *= 1.0 - 0.18 * innerLine;

    // 镜面高光条（随光角移动）
    vec3 N3 = normalize(vec3(nrm * 0.40, 1.0));
    vec3 L3 = normalize(vec3(L * 0.62, 1.0));
    float spec = pow(max(dot(reflect(-L3, N3), vec3(0.0, 0.0, 1.0)), 0.0), 8.0);
    col += vec3(1.0) * spec * 0.17;

    // 顶部厚度高光带
    float topBand = smoothstep(0.60, 1.0, vUV.y) * (1.0 - lens * 0.55);
    col += vec3(1.0) * topBand * 0.13;

    // 底部内阴影（厚度感）
    float botEdge = smoothstep(-9.0, 0.0, dpx) * (1.0 - smoothstep(0.0, 0.22, vUV.y));
    col *= 1.0 - 0.16 * botEdge;

    if (uDebug > 1.5) { gl_FragColor = vec4(col, 1.0); return; }
    col = clamp(col, 0.0, 1.0);
    float a = mix(uAlpha + 0.34, uAlpha + 0.10, lens);
    gl_FragColor = vec4(col * a, a);
}
"""


PAD = 44


def make_bg(w, h):
    W2, H2 = w + PAD * 2, h + PAD * 2
    img = QImage(W2, H2, QImage.Format.Format_ARGB32)
    img.fill(QColor("#0B1220"))
    p = QPainter(img)
    p.translate(PAD, PAD)
    g = QLinearGradient(0, 0, w, h)
    g.setColorAt(0.0, QColor("#1E3A8A"))
    g.setColorAt(0.45, QColor("#0EA5E9"))
    g.setColorAt(0.75, QColor("#7C3AED"))
    g.setColorAt(1.0, QColor("#F59E0B"))
    p.fillRect(0, 0, w, h, g)
    p.setPen(Qt.PenStyle.NoPen)
    for gy in range(0, h + 44, 44):
        for gx in range(0, w + 44, 44):
            if ((gx // 44) + (gy // 44)) % 2 == 0:
                p.fillRect(gx, gy, 44, 44, QColor(255, 255, 255, 34))
    p.setPen(QColor(255, 255, 255, 90))
    for i in range(0, w, 34):
        p.drawLine(i, 0, i, h)
    for i in range(0, h, 34):
        p.drawLine(0, i, w, i)
    p.setPen(QColor(255, 255, 255, 235))
    f = p.font()
    f.setPointSize(26)
    f.setBold(True)
    p.setFont(f)
    p.drawText(28, 60, "LIQUID GLASS")
    p.drawText(28, 104, "折射 / 反射 / 边缘高光")
    p.end()
    # 磨砂：先缩小再放大（等效大半径模糊，CPU 一次完成，比在着色器里堆 taps 便宜）
    small = img.scaled(max(8, W2 // 12), max(8, H2 // 12),
                       Qt.AspectRatioMode.IgnoreAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)
    return small.scaled(W2, H2, Qt.AspectRatioMode.IgnoreAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)


def main():
    app = QApplication(sys.argv)
    fmt = QSurfaceFormat()
    fmt.setVersion(2, 1)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.NoProfile)
    fmt.setAlphaBufferSize(8)
    surf = QOffscreenSurface()
    surf.setFormat(fmt)
    surf.create()
    ctx = QOpenGLContext()
    ctx.setFormat(fmt)
    assert ctx.create(), "无法创建 GL 上下文"
    assert ctx.makeCurrent(surf), "makeCurrent 失败"

    gl = build_gl()
    prog = compile_program(gl, VS, FS)
    print("着色器编译/链接：OK")

    W, H = 560, 360
    bg = make_bg(W, H)
    bg = bg.convertToFormat(QImage.Format.Format_RGBA8888)
    tex = c_uint(0)
    gl["glGenTextures"](1, byref(tex))
    gl["glBindTexture"](GL_TEXTURE_2D, tex.value)
    gl["glTexParameteri"](GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
    gl["glTexParameteri"](GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
    gl["glTexParameteri"](GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
    gl["glTexParameteri"](GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
    gl["glPixelStorei"](GL_UNPACK_ALIGNMENT, 1)
    raw = bg.constBits().asstring(bg.sizeInBytes())
    _keep = ctypes.create_string_buffer(raw, len(raw))
    gl["glTexImage2D"](GL_TEXTURE_2D, 0, GL_RGBA, bg.width(), bg.height(), 0,
                       GL_RGBA, GL_UNSIGNED_BYTE, ctypes.cast(_keep, c_void_p))

    fbo = QOpenGLFramebufferObject(W, H)
    fbo.bind()
    gl["glViewport"](0, 0, W, H)
    gl["glClearColor"](0.0, 0.0, 0.0, 0.0)
    gl["glClear"](0x00004000)
    gl["glDisable"](GL_BLEND)
    gl["glUseProgram"](prog)
    gl["glActiveTexture"](GL_TEXTURE0)
    gl["glBindTexture"](GL_TEXTURE_2D, tex.value)
    u = lambda name: gl["glGetUniformLocation"](prog, name.encode())
    gl["glUniform1i"](u("uBg"), 0)
    gl["glUniform2f"](u("uSize"), float(W), float(H))
    gl["glUniform1f"](u("uTime"), 1.2)
    gl["glUniform1f"](u("uRadius"), 46.0)
    gl["glUniform1f"](u("uMargin"), 26.0)
    gl["glUniform1f"](u("uThickness"), 40.0)
    gl["glUniform1f"](u("uAlpha"), 0.50)
    gl["glUniform1f"](u("uLight"), math.radians(-58))
    gl["glUniform1f"](u("uPad"), float(PAD))
    gl["glUniform2f"](u("uTex"), float(bg.width()), float(bg.height()))
    gl["glUniform1f"](u("uDebug"), 0.0)

    gl["glMatrixMode"](GL_PROJECTION)
    gl["glLoadIdentity"]()
    gl["glOrtho"](0.0, 1.0, 0.0, 1.0, -1.0, 1.0)
    gl["glMatrixMode"](GL_MODELVIEW)
    gl["glLoadIdentity"]()
    gl["glBegin"](GL_QUADS)
    for (x, y) in ((0, 0), (1, 0), (1, 1), (0, 1)):
        gl["glTexCoord2f"](float(x), float(y))
        gl["glVertex2f"](float(x), float(y))
    gl["glEnd"]()

    img = fbo.toImage()
    fbo.release()
    img.save(str(OUT / "glass_out.png"))

    # 叠到底图上，便于看折射是否真的扭曲了背景
    comp = QImage(bg.convertToFormat(QImage.Format.Format_ARGB32))
    p = QPainter(comp)
    p.drawImage(PAD, PAD, img)
    p.end()
    comp.save(str(OUT / "glass_composite.png"))

    def px(x, y):
        c = img.pixelColor(x, y)
        return (c.red(), c.green(), c.blue(), c.alpha())

    print("输出：", OUT / "glass_out.png")
    print("中心像素:", px(W // 2, H // 2), "| 左上边缘:", px(40, 60), "| 角落(0,0):", px(2, 2))
    print("不透明像素占比: %.1f%%" % (
        100.0 * sum(1 for y in range(0, H, 4) for x in range(0, W, 4)
                    if img.pixelColor(x, y).alpha() > 8) / (len(range(0, H, 4)) * len(range(0, W, 4)))))


if __name__ == "__main__":
    main()
