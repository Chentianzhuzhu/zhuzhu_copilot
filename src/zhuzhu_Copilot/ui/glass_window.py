"""液体玻璃窗口（GPU 实时计算的折射 / 反射 / 边缘高光）。

给 Copilot 插件用的**无框自制窗口**：应用启动即可常驻浮现，背景是真实桌面内容，
玻璃本体在 GPU 上逐像素算出来——不是贴图、不是预渲染滤镜：

  · **折射**：以窗口形状的 SDF（超椭圆 squircle）求法线，按「边缘厚、中心薄」的透镜
    模型偏移采样背景贴图，边缘处桌面被真实弯折（pad 补边保证边缘采样到窗外内容）；
  · **色散**：R/G/B 用不同折射率（不同偏移量 + 不同模糊半径）分别采样，形成彩色边缘；
  · **反射 / 镜面高光**：把法线抬成 3D 法线做 Blinn-Phong，高光条随光角与液态扰动移动；
  · **边缘高光**：菲涅尔（离边缘越近越亮）× N·L 的物理亮边 + 内侧一圈极薄暗线做厚度感；
  · **液态感的角度**：法线叠加随时间缓慢呼吸的正弦扰动，高光与折射随之游走。

实现要点（都是踩过的坑）：
  · PyQt6 没有 QOpenGLFunctions，着色器改用 ctypes 直连 opengl32 / wglGetProcAddress
    （GLSL 1.20 + 固定管线提交四边形，兼容性最好，exe 里同样可用）；
  · 背景贴图必须比窗口**大一圈**（pad），否则边缘折射采样到的是窗口内部内容 → 边缘发脏；
  · 单四边形绘制时关闭混合：开了 SRC_ALPHA 混合会让透明 FBO 变成预乘结果，读回时二次
    乘算 → 偏色；输出前必须 `clamp(col)`，否则预乘溢出会让亮部变黄绿；
  · 抓屏前用 SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE) 把本窗口排除在截屏之外，
    否则背景贴图会拍到自己（反馈糊成一团）；
  · 磨砂在 CPU 端做（缩小再放大），着色器只做小半径精修——比在着色器里堆几十个 taps 便宜。

无 GL 环境（远程桌面 / 虚拟机 / 离屏测试）自动降级为 QPainter 绘制的半透明圆角面板，
功能不受影响，只是没有 GPU 光学效果。
"""

import ctypes
import math
import os
import sys
from ctypes import POINTER, byref, c_char_p, c_float, c_int, c_uint, c_void_p
from pathlib import Path

from PyQt6.QtCore import QPoint, QRect, QSize, Qt, QTimer
from PyQt6.QtGui import (QColor, QCursor, QGuiApplication, QImage, QPainter,
                         QPainterPath, QPen, QSurfaceFormat)
from PyQt6.QtWidgets import QWidget

# ---------------------------------------------------------------------------
# 可调参数（都是"观感旋钮"，外部可通过 set_glass_params 覆盖）
# ---------------------------------------------------------------------------
DEFAULTS = {
    "radius": 30.0,        # 圆角半径（px，逻辑像素）
    "pad": 22.0,           # 补边：抓屏时向四周多抓多少（也是阴影留白）
    "thickness": 40.0,     # 折射强度（px）：越大边缘弯折越明显
    "alpha": 0.50,         # 玻璃底色不透明度（0~1）
    "light": -58.0,        # 光源角度（度，图像坐标系，-58 ≈ 右上方来光）
    "wobble": 0.085,       # 液态扰动幅度（法线扰动）
    "wobble_speed": 1.0,   # 液态呼吸速度
    "frost": 12,           # 磨砂强度（1=不磨砂，越大越糊）
    "fps": 60,             # 目标帧率
    "capture_ms": 900,     # 背景重抓间隔（毫秒）
}

#: 抓屏排除（Windows 10 2004+）：本窗口不进入任何截屏，背景贴图才不会拍到自己
_WDA_EXCLUDEFROMCAPTURE = 0x00000011

# ---------------------------------------------------------------------------
# OpenGL 绑定（GL 1.1 走 opengl32.dll，GL 2.0 走 wglGetProcAddress）
# ---------------------------------------------------------------------------
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
GL_PROJECTION = 0x1701
GL_MODELVIEW = 0x1700
GL_TEXTURE0 = 0x84C0
GL_UNPACK_ALIGNMENT = 0x0CF5
GL_COLOR_BUFFER_BIT = 0x00004000

GLASS_VS = """
#version 120
varying vec2 vUV;
void main() {
    vUV = gl_MultiTexCoord0.xy;
    gl_Position = ftransform();
}
"""

GLASS_FS = """
#version 120
uniform sampler2D uBg;
uniform vec2  uSize;      // 窗口尺寸（设备像素）
uniform vec2  uTex;       // 背景贴图尺寸（窗口 + 四周 pad）
uniform float uPad;       // 补边（设备像素）
uniform float uTime;
uniform float uMargin;    // 阴影留白
uniform float uThickness;
uniform float uAlpha;
uniform float uLight;
uniform float uWobble;
varying vec2 vUV;

// 超椭圆（squircle）SDF：0 = 边缘，<0 内部
float sqD(vec2 p, vec2 b, float n) {
    vec2 q = abs(p) / b;
    return pow(pow(q.x, n) + pow(q.y, n), 1.0 / n) - 1.0;
}

// SDF 梯度 → 外法线（有限差分，中心处梯度≈0，由调用方用 lens 衰减）
vec2 sqN(vec2 p, vec2 b, float n) {
    float e = 1.5;
    float d0 = sqD(p, b, n);
    float dx = sqD(p + vec2(e, 0.0), b, n) - d0;
    float dy = sqD(p + vec2(0.0, e), b, n) - d0;
    vec2 g = vec2(dx, dy);
    float len = length(g);
    return len > 1e-6 ? g / len : vec2(0.0, 0.0);
}

// 3x3 精修模糊（大半径磨砂已在 CPU 端做完）
vec4 blurBg(vec2 uv, float r) {
    vec2 px = 1.0 / uTex;
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
    vec2 tuv = (vec2(uPad) + vec2(vUV.x, 1.0 - vUV.y) * uSize) / uTex;

    float n = 4.0;
    float dpx = sqD(p, half_, n) * min(half_.x, half_.y);   // 近似像素距离

    if (dpx > 0.0) {                                        // 形状外：软阴影 + 冷光晕
        float sh = exp(-dpx / (uMargin * 0.34)) * 0.46;
        float halo = exp(-dpx / (uMargin * 1.1)) * 0.07;
        gl_FragColor = vec4(0.01 + halo * 0.4, 0.02 + halo * 0.5, 0.05 + halo * 0.8, sh);
        return;
    }

    float depth = -dpx;
    float lens = smoothstep(0.0, min(half_.x, half_.y) * 0.30, depth);
    vec2 nrm = sqN(p, half_, n);

    // 液态感：法线随时间缓慢呼吸（高光与折射随之游走）
    float wob = sin(p.y * 0.017 + uTime * 1.05) * cos(p.x * 0.013 - uTime * 0.75);
    nrm = normalize(nrm + vec2(wob, -wob) * uWobble);

    // 折射：边缘强、中心弱（真实玻璃透镜）
    vec2 off = nrm * (1.0 - lens) * uThickness / uTex;
    vec2 ruv = clamp(tuv + off, vec2(0.0), vec2(1.0));
    // 色散：R/B 折射率不同
    vec3 refr = vec3(
        blurBg(ruv + off * 0.30, 1.6).r,
        blurBg(ruv, 2.0).g,
        blurBg(ruv - off * 0.30, 2.4).b);

    vec3 tint = vec3(0.90, 0.95, 1.0);
    vec3 col = mix(refr, tint, 0.13) * 1.05;

    vec2 L = vec2(cos(uLight), sin(uLight));
    float rimL = clamp(dot(nrm, L), 0.0, 1.0);

    // 边缘高光：菲涅尔 × N·L
    float edge = smoothstep(-2.2, 0.0, dpx);
    col += vec3(1.0) * edge * (0.06 + 0.80 * pow(rimL, 2.6));
    col += vec3(0.66, 0.86, 1.0) * edge * 0.26 * (1.0 - rimL);
    // 内侧一圈极薄暗线：勾出玻璃厚度
    float innerLine = smoothstep(-5.0, -2.2, dpx) * (1.0 - smoothstep(-2.2, -1.6, dpx));
    col *= 1.0 - 0.18 * innerLine;

    // 镜面高光（3D 法线 + Blinn-Phong，随液态扰动游走）
    vec3 N3 = normalize(vec3(nrm * 0.40, 1.0));
    vec3 L3 = normalize(vec3(L * 0.62, 1.0));
    float spec = pow(max(dot(reflect(-L3, N3), vec3(0.0, 0.0, 1.0)), 0.0), 8.0);
    col += vec3(1.0) * spec * 0.17;

    // 顶部厚度高光带
    float topBand = smoothstep(0.60, 1.0, vUV.y) * (1.0 - lens * 0.55);
    col += vec3(1.0) * topBand * 0.13;

    // 底部内阴影
    float botEdge = smoothstep(-9.0, 0.0, dpx) * (1.0 - smoothstep(0.0, 0.22, vUV.y));
    col *= 1.0 - 0.16 * botEdge;

    col = clamp(col, 0.0, 1.0);
    float a = mix(uAlpha + 0.34, uAlpha + 0.10, lens);
    gl_FragColor = vec4(col * a, a);      // 预乘输出（QOpenGLWidget 合成按预乘解释）
}
"""


class _GLBinder:
    """最小 OpenGL 函数绑定（延迟到首次绘制，避免无 GL 环境导入即炸）。"""

    _instance = None

    def __init__(self):
        self.dll = ctypes.WinDLL("opengl32")
        self._wgl = self.dll.wglGetProcAddress
        self._wgl.restype = c_void_p
        self._wgl.argtypes = [c_char_p]
        self.f = {}

    @classmethod
    def get(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _addr(self, name):
        addr = self._wgl(name.encode())
        if not addr:
            try:
                addr = ctypes.cast(getattr(self.dll, name), c_void_p).value
            except AttributeError:
                return None
        return addr

    def fn(self, name, restype, *argtypes):
        key = name
        if key in self.f:
            return self.f[key]
        addr = self._addr(name)
        if not addr:
            raise AttributeError("OpenGL 函数不可用: " + name)
        f = ctypes.CFUNCTYPE(restype, *argtypes)(addr)
        self.f[key] = f
        return f

    def load(self):
        """装载绘制所需的全部入口（缺一个就抛，由调用方降级）。"""
        d = self.dll
        g = self.f
        g["glViewport"] = self.fn("glViewport", None, c_int, c_int, c_int, c_int)
        g["glClearColor"] = self.fn("glClearColor", None, c_float, c_float, c_float, c_float)
        g["glClear"] = self.fn("glClear", None, c_uint)
        g["glDisable"] = self.fn("glDisable", None, c_uint)
        g["glGenTextures"] = self.fn("glGenTextures", None, c_int, POINTER(c_uint))
        g["glBindTexture"] = self.fn("glBindTexture", None, c_uint, c_uint)
        g["glTexImage2D"] = self.fn("glTexImage2D", None, c_uint, c_int, c_int, c_int,
                                    c_int, c_uint, c_uint, c_void_p)
        g["glTexParameteri"] = self.fn("glTexParameteri", None, c_uint, c_uint, c_int)
        g["glPixelStorei"] = self.fn("glPixelStorei", None, c_uint, c_int)
        g["glActiveTexture"] = self.fn("glActiveTexture", None, c_uint)
        g["glMatrixMode"] = self.fn("glMatrixMode", None, c_uint)
        g["glLoadIdentity"] = self.fn("glLoadIdentity", None)
        g["glOrtho"] = self.fn("glOrtho", None, ctypes.c_double, ctypes.c_double,
                               ctypes.c_double, ctypes.c_double,
                               ctypes.c_double, ctypes.c_double)
        g["glBegin"] = self.fn("glBegin", None, c_uint)
        g["glEnd"] = self.fn("glEnd", None)
        g["glVertex2f"] = self.fn("glVertex2f", None, c_float, c_float)
        g["glTexCoord2f"] = self.fn("glTexCoord2f", None, c_float, c_float)
        g["glCreateShader"] = self.fn("glCreateShader", c_uint, c_uint)
        g["glShaderSource"] = self.fn("glShaderSource", None, c_uint, c_int,
                                      POINTER(c_char_p), POINTER(c_int))
        g["glCompileShader"] = self.fn("glCompileShader", None, c_uint)
        g["glGetShaderiv"] = self.fn("glGetShaderiv", None, c_uint, c_uint, POINTER(c_int))
        g["glGetShaderInfoLog"] = self.fn("glGetShaderInfoLog", None, c_uint, c_int,
                                          POINTER(c_int), c_char_p)
        g["glCreateProgram"] = self.fn("glCreateProgram", c_uint)
        g["glAttachShader"] = self.fn("glAttachShader", None, c_uint, c_uint)
        g["glLinkProgram"] = self.fn("glLinkProgram", None, c_uint)
        g["glGetProgramiv"] = self.fn("glGetProgramiv", None, c_uint, c_uint, POINTER(c_int))
        g["glGetProgramInfoLog"] = self.fn("glGetProgramInfoLog", None, c_uint, c_int,
                                           POINTER(c_int), c_char_p)
        g["glUseProgram"] = self.fn("glUseProgram", None, c_uint)
        g["glGetUniformLocation"] = self.fn("glGetUniformLocation", c_int, c_uint, c_char_p)
        g["glUniform1f"] = self.fn("glUniform1f", None, c_int, c_float)
        g["glUniform2f"] = self.fn("glUniform2f", None, c_int, c_float, c_float)
        g["glUniform1i"] = self.fn("glUniform1i", None, c_int, c_int)
        return g

    def compile(self, vs_src, fs_src):
        g = self.f

        def one(kind, src):
            sh = g["glCreateShader"](kind)
            buf = c_char_p(src.encode("utf-8"))
            g["glShaderSource"](sh, 1, byref(buf), None)
            g["glCompileShader"](sh)
            ok = c_int(0)
            g["glGetShaderiv"](sh, GL_COMPILE_STATUS, byref(ok))
            if not ok.value:
                log = ctypes.create_string_buffer(8192)
                ln = c_int(0)
                g["glGetShaderInfoLog"](sh, 8192, byref(ln), log)
                raise RuntimeError("着色器编译失败：" + log.value.decode("utf-8", "replace"))
            return sh

        vs, fs = one(GL_VERTEX_SHADER, vs_src), one(GL_FRAGMENT_SHADER, fs_src)
        prog = g["glCreateProgram"]()
        g["glAttachShader"](prog, vs)
        g["glAttachShader"](prog, fs)
        g["glLinkProgram"](prog)
        ok = c_int(0)
        g["glGetProgramiv"](prog, GL_LINK_STATUS, byref(ok))
        if not ok.value:
            log = ctypes.create_string_buffer(8192)
            ln = c_int(0)
            g["glGetProgramInfoLog"](prog, 8192, byref(ln), log)
            raise RuntimeError("着色器链接失败：" + log.value.decode("utf-8", "replace"))
        return prog


def gl_available() -> bool:
    """当前环境能否创建 GL 2.1 上下文（远程桌面/纯离屏下可能为 False）。"""
    try:
        from PyQt6.QtGui import QOffscreenSurface, QOpenGLContext
        fmt = QSurfaceFormat()
        fmt.setVersion(2, 1)
        fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.NoProfile)
        surf = QOffscreenSurface()
        surf.setFormat(fmt)
        surf.create()
        if not surf.isValid():
            return False
        ctx = QOpenGLContext()
        ctx.setFormat(fmt)
        if not ctx.create():
            return False
        ok = ctx.makeCurrent(surf)
        ctx.doneCurrent()
        return bool(ok)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# GPU 玻璃面（QOpenGLWidget）
# ---------------------------------------------------------------------------
def _make_surface_class():
    """延迟导入 QOpenGLWidget（无 GL 环境时 Qt 也能正常导入，这里只是保持惰性）。"""
    from PyQt6.QtOpenGLWidgets import QOpenGLWidget

    class GlassSurface(QOpenGLWidget):
        """把背景贴图按液体玻璃着色器画出来的 GPU 面。"""

        def __init__(self, parent=None):
            super().__init__(parent)
            fmt = QSurfaceFormat()
            fmt.setVersion(2, 1)
            fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.NoProfile)
            fmt.setAlphaBufferSize(8)
            fmt.setSwapInterval(0)
            self.setFormat(fmt)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self._bg = QImage()
            self._tex = c_uint(0)
            self._prog = 0
            self._ready = False
            self._failed = ""
            self._time = 0.0
            self._pad_px = 1.0
            self._win_px = QSize(1, 1)
            self._params = dict(DEFAULTS)

        # ---- 对外 ----
        def set_params(self, **kw):
            for k, v in kw.items():
                if k in self._params:
                    self._params[k] = v
            self.update()

        def params(self):
            return dict(self._params)

        def set_background(self, img: QImage, pad_px: float, win_px: QSize):
            """设置背景贴图（窗口 + 四周补边，设备像素）与几何。"""
            self._bg = img
            self._pad_px = float(pad_px)
            self._win_px = QSize(max(1, win_px.width()), max(1, win_px.height()))
            self._upload()
            self.update()

        def set_time(self, t: float):
            self._time = float(t)
            self.update()

        def gpu_ready(self) -> bool:
            return self._ready

        def failure(self) -> str:
            return self._failed

        # ---- GL ----
        def initializeGL(self):
            try:
                binder = _GLBinder.get()
                binder.load()
                self._prog = binder.compile(GLASS_VS, GLASS_FS)
                binder.f["glPixelStorei"](GL_UNPACK_ALIGNMENT, 1)
                self._ready = True
                self._upload()
            except Exception as e:
                self._ready = False
                self._failed = str(e)[:300]

        def _upload(self):
            if not self._ready or self._bg.isNull():
                return
            gl = _GLBinder.get()
            img = self._bg
            if img.format() != QImage.Format.Format_RGBA8888:
                img = img.convertToFormat(QImage.Format.Format_RGBA8888)
            raw = img.constBits().asstring(img.sizeInBytes())
            keep = ctypes.create_string_buffer(raw, len(raw))   # 调用期间保活
            g = gl.f
            if not self._tex.value:
                tex = c_uint(0)
                g["glGenTextures"](1, byref(tex))
                self._tex = tex
            g["glBindTexture"](GL_TEXTURE_2D, self._tex.value)
            g["glTexParameteri"](GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            g["glTexParameteri"](GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            g["glTexParameteri"](GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            g["glTexParameteri"](GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
            g["glTexImage2D"](GL_TEXTURE_2D, 0, GL_RGBA, img.width(), img.height(), 0,
                              GL_RGBA, GL_UNSIGNED_BYTE, ctypes.cast(keep, c_void_p))

        def paintGL(self):
            if not self._ready:
                return
            gl = _GLBinder.get()
            g = gl.f
            dpr = self.devicePixelRatioF()
            w = max(1, int(round(self.width() * dpr)))
            h = max(1, int(round(self.height() * dpr)))
            g["glViewport"](0, 0, w, h)
            g["glClearColor"](0.0, 0.0, 0.0, 0.0)
            g["glClear"](GL_COLOR_BUFFER_BIT)
            g["glDisable"](GL_BLEND)          # 单四边形：关闭混合，避免透明 FBO 变预乘
            if self._bg.isNull():
                return
            if not self._tex.value:
                self._upload()                 # 先有背景、后有 GL 上下文时补上传
                if not self._tex.value:
                    return
            p = self._params
            g["glUseProgram"](self._prog)
            g["glActiveTexture"](GL_TEXTURE0)
            g["glBindTexture"](GL_TEXTURE_2D, self._tex.value)
            u = lambda name: g["glGetUniformLocation"](self._prog, name.encode())
            g["glUniform1i"](u("uBg"), 0)
            g["glUniform2f"](u("uSize"), float(self._win_px.width()),
                            float(self._win_px.height()))
            g["glUniform2f"](u("uTex"), float(self._bg.width()),
                            float(self._bg.height()))
            g["glUniform1f"](u("uPad"), float(self._pad_px))
            g["glUniform1f"](u("uTime"), float(self._time))
            g["glUniform1f"](u("uMargin"), float(p["pad"] * dpr))
            g["glUniform1f"](u("uThickness"), float(p["thickness"] * dpr))
            g["glUniform1f"](u("uAlpha"), float(p["alpha"]))
            g["glUniform1f"](u("uLight"), math.radians(float(p["light"])))
            g["glUniform1f"](u("uWobble"), float(p["wobble"]))
            g["glMatrixMode"](GL_PROJECTION)
            g["glLoadIdentity"]()
            g["glOrtho"](0.0, 1.0, 0.0, 1.0, -1.0, 1.0)
            g["glMatrixMode"](GL_MODELVIEW)
            g["glLoadIdentity"]()
            g["glBegin"](GL_QUADS)
            for (x, y) in ((0, 0), (1, 0), (1, 1), (0, 1)):
                g["glTexCoord2f"](float(x), float(y))
                g["glVertex2f"](float(x), float(y))
            g["glEnd"]()

    return GlassSurface


# ---------------------------------------------------------------------------
# 无 GL 降级面（QPainter 半透明圆角面板，保证功能可用）
# ---------------------------------------------------------------------------
class _FallbackSurface(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._bg = QImage()
        self._params = dict(DEFAULTS)

    def set_params(self, **kw):
        for k, v in kw.items():
            if k in self._params:
                self._params[k] = v
        self.update()

    def params(self):
        return dict(self._params)

    def set_background(self, img, pad_px=0.0, win_px=None):
        self._bg = img
        self.update()

    def set_time(self, t):
        pass

    def gpu_ready(self):
        return False

    def failure(self):
        return "no-gl"

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        dpr = self.devicePixelRatioF()
        r = float(self._params["radius"])
        rect = self.rect().adjusted(int(4 * dpr), int(4 * dpr),
                                    -int(4 * dpr), -int(4 * dpr))
        path = QPainterPath()
        path.addRoundedRect(rect.toRectF(), r, r)
        p.fillPath(path, QColor(22, 30, 44, 236))
        p.setPen(QPen(QColor(255, 255, 255, 46), 1.4))
        p.drawPath(path)


# ---------------------------------------------------------------------------
# 无框玻璃窗口
# ---------------------------------------------------------------------------
class GlassWindow(QWidget):
    """无框、置顶、可拖拽/缩放的液体玻璃窗口。

    content 传一个 QWidget（例如插件页面），会被放在玻璃壳内部的留白区域；
    不传则只显示玻璃壳（可当纯装饰/提示窗）。
    """

    TITLE_H = 44

    def __init__(self, title: str = "", parent=None, width: int = 780,
                 height: int = 540, content: QWidget = None, **params):
        super().__init__(parent)
        self._params = dict(DEFAULTS)
        self._params.update({k: v for k, v in params.items() if k in DEFAULTS})
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setWindowTitle(title or "Glass")
        self.resize(int(width), int(height))
        self.setMinimumSize(320, 200)

        self._surface = None
        self._content = None
        self._title = title
        self._drag_from = None
        self._resize_edges = None
        self._t0 = 0.0
        self._cap_timer = None
        self._anim = None
        self._excluded = False
        self._dbg = {"grab": "", "err": ""}

        self._build_surface()
        if content is not None:
            self.setContent(content)
        self.setMouseTracking(True)

    # ---- 构建 ----
    def _build_surface(self):
        cls = None
        forced = str(os.environ.get("GLASS_MODE") or "").strip().lower()
        if forced == "painter":
            cls = None                      # 强制降级（远程桌面/驱动异常时排障用）
        elif gl_available():
            try:
                cls = _make_surface_class()
            except Exception:
                cls = None
        if cls is not None:
            try:
                self._surface = cls(self)
            except Exception:
                self._surface = None
        if self._surface is None:
            self._surface = _FallbackSurface(self)
        self._surface.set_params(**self._params)

    def _layout_children(self):
        dpr = self.devicePixelRatioF()
        pad = int(round(float(self._params["pad"]) * dpr))
        if self._surface is not None:
            self._surface.setGeometry(self.rect())
        if self._content is not None:
            inner = self.rect().adjusted(pad + 6, self.TITLE_H, -pad - 6, -pad - 6)
            if inner.width() > 10 and inner.height() > 10:
                self._content.setGeometry(inner)

    def setContent(self, widget: QWidget):
        """把内容控件放进玻璃壳（内容自身背景应尽量透明）。"""
        if widget is None:
            return
        widget.setParent(self)
        widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        try:
            widget.setStyleSheet("background: transparent;")
        except Exception:
            pass
        widget.show()
        self._content = widget
        self._layout_children()

    def setTitle(self, text: str):
        self._title = text or ""
        self.update()

    def surface(self):
        return self._surface

    def gpu_active(self) -> bool:
        return bool(self._surface is not None and self._surface.gpu_ready())

    def diagnostics(self) -> dict:
        """诊断信息（抓屏是否成功 / GL 是否就绪 / 失败原因）。"""
        d = dict(self._dbg)
        d["gpu"] = self.gpu_active()
        d["surface_error"] = self._surface.failure() if self._surface else "no-surface"
        d["excluded_from_capture"] = bool(self._excluded)
        return d

    def set_params(self, **kw):
        self._params.update({k: v for k, v in kw.items() if k in DEFAULTS})
        if self._surface is not None:
            self._surface.set_params(**self._params)
        self._layout_children()
        self._capture()

    # ---- 生命周期 ----
    def showEvent(self, e):
        super().showEvent(e)
        import time as _time
        self._t0 = _time.perf_counter()
        self._exclude_from_capture()
        self._layout_children()
        QTimer.singleShot(30, self._capture)
        if self._anim is None:
            self._anim = QTimer(self)
            fps = max(10, min(120, int(self._params["fps"])))
            self._anim.setInterval(max(6, int(1000 / fps)))
            self._anim.timeout.connect(self._tick)
        self._anim.start()
        if self._cap_timer is None:
            self._cap_timer = QTimer(self)
            self._cap_timer.setTimerType(Qt.TimerType.VeryCoarseTimer)
            self._cap_timer.setInterval(max(200, int(self._params["capture_ms"])))
            self._cap_timer.timeout.connect(self._capture)
        self._cap_timer.start()

    def hideEvent(self, e):
        super().hideEvent(e)
        for t in (self._anim, self._cap_timer):
            if t is not None:
                t.stop()

    def _tick(self):
        import time as _time
        if self._surface is None:
            return
        self._surface.set_time(_time.perf_counter() - self._t0)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._layout_children()
        if self._cap_timer is not None:
            self._cap_timer.stop()
            QTimer.singleShot(120, self._recapture)

    def _recapture(self):
        self._capture()
        if self._cap_timer is not None and self.isVisible():
            self._cap_timer.start()

    def moveEvent(self, e):
        super().moveEvent(e)
        if self._cap_timer is not None and self.isVisible():
            self._cap_timer.stop()
            QTimer.singleShot(160, self._recapture)

    # ---- 背景抓取（真实桌面，GPU 折射的采样源）----
    def _exclude_from_capture(self):
        """把本窗口排除在任何截屏之外：否则背景贴图会拍到玻璃自己（反馈糊）。"""
        if self._excluded or not sys.platform.startswith("win"):
            return
        if os.environ.get("GLASS_KEEP_CAPTURABLE") == "1":
            return          # 自测/演示用：允许被截屏（否则抓不到自己）
        try:
            hwnd = int(self.winId())
            user32 = ctypes.WinDLL("user32")
            self._excluded = bool(
                user32.SetWindowDisplayAffinity(ctypes.c_void_p(hwnd),
                                                ctypes.c_uint(_WDA_EXCLUDEFROMCAPTURE)))
        except Exception:
            self._excluded = False

    def _capture(self):
        if self._surface is None or not self.isVisible():
            return
        try:
            dpr = self.devicePixelRatioF()
            pad_log = float(self._params["pad"])
            pad_px = max(1, int(round(pad_log * dpr)))
            g = self.frameGeometry()
            scr = self.screen() or QGuiApplication.primaryScreen()
            if scr is None:
                return
            sg = scr.geometry()
            x = g.x() - pad_log - sg.x()
            y = g.y() - pad_log - sg.y()
            w = g.width() + pad_log * 2
            h = g.height() + pad_log * 2
            pm = scr.grabWindow(0, int(x), int(y), int(w), int(h))   # 返回 QPixmap
            img = pm.toImage()
            self._dbg["grab"] = "ok" if not img.isNull() else "null"
            if img.isNull():
                img = self._neutral_bg(int(w), int(h))
            tw = max(8, int(round(w * dpr)))
            th = max(8, int(round(h * dpr)))
            img = img.convertToFormat(QImage.Format.Format_RGBA8888)
            if img.width() != tw or img.height() != th:
                img = img.scaled(tw, th, Qt.AspectRatioMode.IgnoreAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
            img = self._frost(img)
            self._surface.set_background(
                img, pad_px, QSize(max(1, tw - pad_px * 2), max(1, th - pad_px * 2)))
            self._dbg["err"] = ""
        except Exception as e:
            self._dbg["err"] = str(e)[:200]

    def _neutral_bg(self, w: int, h: int) -> QImage:
        """抓不到桌面时的中性底（深色渐变）：宁可像磨砂深玻璃，也不要窗口"消失"。"""
        img = QImage(max(8, w), max(8, h), QImage.Format.Format_ARGB32)
        p = QPainter(img)
        from PyQt6.QtGui import QLinearGradient
        gr = QLinearGradient(0, 0, w, h)
        gr.setColorAt(0.0, QColor(24, 34, 52))
        gr.setColorAt(1.0, QColor(12, 18, 30))
        p.fillRect(img.rect(), gr)
        p.end()
        return img

    def _frost(self, img: QImage) -> QImage:
        """磨砂：缩小再放大（等效大半径模糊，CPU 一次搞定）。"""
        f = max(1, int(self._params.get("frost") or 1))
        if f <= 1:
            return img
        small = img.scaled(max(8, img.width() // f), max(8, img.height() // f),
                           Qt.AspectRatioMode.IgnoreAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
        return small.scaled(img.width(), img.height(),
                            Qt.AspectRatioMode.IgnoreAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)

    # ---- 拖拽 / 缩放 ----
    def _edges_at(self, pos: QPoint):
        m = 8
        r = self.rect()
        edges = Qt.Edge(0)
        if pos.x() <= m:
            edges |= Qt.Edge.LeftEdge
        elif pos.x() >= r.width() - m:
            edges |= Qt.Edge.RightEdge
        if pos.y() <= m:
            edges |= Qt.Edge.TopEdge
        elif pos.y() >= r.height() - m:
            edges |= Qt.Edge.BottomEdge
        return edges

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        edges = self._edges_at(e.position().toPoint())
        if int(edges):
            try:
                if self.windowHandle() is not None and \
                        self.windowHandle().startSystemResize(edges):
                    return
            except Exception:
                pass
            self._resize_edges = edges
            self._drag_from = (e.globalPosition().toPoint(), self.geometry())
            return
        if e.position().y() <= self.TITLE_H:
            try:
                if self.windowHandle() is not None and self.windowHandle().startSystemMove():
                    return
            except Exception:
                pass
            self._drag_from = (e.globalPosition().toPoint(), self.geometry())

    def mouseMoveEvent(self, e):
        if self._drag_from is not None:
            start, geo = self._drag_from
            delta = e.globalPosition().toPoint() - start
            if self._resize_edges:
                r = QRect(geo)
                if int(self._resize_edges & Qt.Edge.LeftEdge):
                    r.setLeft(min(r.left() + delta.x(), r.right() - 200))
                if int(self._resize_edges & Qt.Edge.TopEdge):
                    r.setTop(min(r.top() + delta.y(), r.bottom() - 120))
                if int(self._resize_edges & Qt.Edge.RightEdge):
                    r.setRight(max(r.right() + delta.x(), r.left() + 200))
                if int(self._resize_edges & Qt.Edge.BottomEdge):
                    r.setBottom(max(r.bottom() + delta.y(), r.top() + 120))
                self.setGeometry(r)
            else:
                self.move(geo.topLeft() + delta)
            return
        edges = self._edges_at(e.position().toPoint())
        if int(edges) in (int(Qt.Edge.LeftEdge) | int(Qt.Edge.TopEdge),
                          int(Qt.Edge.RightEdge) | int(Qt.Edge.BottomEdge)):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif int(edges) in (int(Qt.Edge.RightEdge) | int(Qt.Edge.TopEdge),
                            int(Qt.Edge.LeftEdge) | int(Qt.Edge.BottomEdge)):
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        elif int(edges) & (int(Qt.Edge.LeftEdge) | int(Qt.Edge.RightEdge)):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif int(edges) & (int(Qt.Edge.TopEdge) | int(Qt.Edge.BottomEdge)):
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        else:
            self.unsetCursor()

    def mouseReleaseEvent(self, e):
        self._drag_from = None
        self._resize_edges = None
        self.unsetCursor()

    def showCentered(self):
        """在鼠标所在屏幕居中显示（常驻浮现的默认位置）。"""
        scr = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if scr is not None:
            a = scr.availableGeometry()
            self.move(a.center() - self.rect().center())
        self.show()
        self.raise_()


def create_glass_window(title: str, content: QWidget = None, **params) -> GlassWindow:
    """便捷构造：按参数创建玻璃窗口（失败也返回降级版本，绝不抛）。"""
    w = int(params.pop("width", 780))
    h = int(params.pop("height", 540))
    return GlassWindow(title=title, width=w, height=h, content=content, **params)
