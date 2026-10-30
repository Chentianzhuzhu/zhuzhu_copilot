"""桌宠渲染层：QOpenGLWidget 直渲 + 真透明背景（不依赖 Qt 3D，避开离屏回读）。

## 为什么换实现
旧方案（Qt3DWindow 离屏 + QRenderCapture 回读）在用户的 GT 730 + PyQt6 6.11 上
**整链路必崩**（0xC0000005 / 0xC0000409，崩溃点飘忽），排查后确认是这台机器上
「连续多 GL 上下文 + 回读 + 合成」的复合不稳定。本实现改用 QOpenGLWidget 直渲：

  - 窗口本身走 Qt 的普通 widget 合成，Windows DWM 对 QOpenGLWidget 的 alpha
    合成是可靠路径（真机截图验证：桌面内容能透过背景显示）；
  - 渲染完全自己控制：不用 Qt3D 场景图，不用回读，没有第二个 GL 上下文。

## 本机（GT 730 + PyQt6 6.11）GL 避坑清单（全部经真机验证）
  1. `addShaderFromSourceCode(str)` 必崩 → 用 `QOpenGLShader.compileSourceCode(QByteArray)` + `addShader`；
  2. `glDrawElements` + EBO 必崩 → 索引展开为独立顶点，`glDrawArrays`；
  3. `np.diag(...) @ ...` 在 paintGL 内必崩 → 用 numpy 列缩放（`r[:, i] *= k`）；
  4. `glEnableVertexAttribArray(-1)` 触发驱动故障 → 先判 location >= 0；
  5. QQuaternion 乘法在 GL 上下文内必崩 → 根旋转用纯 numpy 构造；
  6. numpy 矩阵行主序 → `glUniformMatrix*` 传 `transpose=True`；
  7. 像素回读（grab/grabWindow）必崩 → 验证透明用 PIL ImageGrab（GDI 路径）。

## 蒙皮
资产 21 个部件全是刚性块（只受一根骨头影响），顶点上传一次即可，每帧只换矩阵。
骨骼姿态由 numpy 求解（rig.PetRig），交互动画（扭头/甩尾/摆腿/跳跃）逐帧叠加。
"""
from __future__ import annotations

import math
import os

import numpy as np
from PyQt6.QtCore import QByteArray, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QSurfaceFormat
from PyQt6.QtWidgets import QWidget
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtOpenGL import (QOpenGLBuffer, QOpenGLShader, QOpenGLShaderProgram,
                            QOpenGLVertexArrayObject, QOpenGLFunctions_2_0)

from .rig import PetRig, _axis_angle

def _default_width() -> int:
    """桌宠显示宽度（像素）：ZHIZHU_PET3D_WIDTH 可覆盖，默认 112（真透明贴图，尺寸即猫的大小）。"""
    try:
        v = int(os.environ.get("ZHIZHU_PET3D_WIDTH") or 0)
    except ValueError:
        v = 0
    return max(72, min(320, v or 112))


#: 默认桌宠显示宽度（像素）；高度按 300:340 等比
DEFAULT_WIDTH = _default_width()
#: 渲染宽高比（与相机取景一致）
_ASPECT_W, _ASPECT_H = 300.0, 340.0
#: 相机视场角（度，与原版一致）
_FOV_DEG = 34.0
#: 头部/躯干跟随鼠标的平滑时间常数（秒）：一阶低通，光标跳变时仍连续转动
_LOOK_TAU = 0.12
#: 躯干跟随比例：躯干只转视线角度的一小部分（头再补足剩余），避免整只猫拧成麻花
_TORSO_YAW_RATIO = 0.32
_TORSO_PITCH_RATIO = 0.24

# ---- 眨眼：每 3 秒一次，真实时间驱动（不受动画播放速度影响） ----
_BLINK_PERIOD_S = 3.0
_BLINK_DOWN_S = 0.07      # 闭眼用时
_BLINK_UP_S = 0.11        # 睁眼用时
_BLINK_EYE_OPEN_SCALE = 0.06   # 闭眼时眼睛沿竖直方向压到 6%（留一条缝，不显得空洞）
_BLINK_GLINT_OPEN_SCALE = 0.02  # 高光几乎压没
_BLINK_EYE_PARTS = ("kitty_eye_l", "kitty_eye_r")
_BLINK_GLINT_PARTS = ("kitty_glint_l", "kitty_glint_r")

_GL_TRIANGLES = 0x0004
_GL_FLOAT = 0x1406
_GL_COLOR_BUFFER_BIT = 0x4000
_GL_DEPTH_BUFFER_BIT = 0x100
_GL_DEPTH_TEST = 0x0B71
_GL_ARRAY_BUFFER = 0x8892

VERT_SRC = """
#version 120
attribute vec3 a_pos;
attribute vec3 a_nrm;
uniform mat4 u_mvp;
uniform mat4 u_mv;
uniform mat3 u_norm;
varying vec3 v_nrm;
varying vec3 v_pos;
void main(void) {
    v_nrm = u_norm * a_nrm;
    vec4 p = u_mv * vec4(a_pos, 1.0);
    v_pos = p.xyz;
    gl_Position = u_mvp * vec4(a_pos, 1.0);
}
"""

FRAG_SRC = """
#version 120
uniform vec4 u_color;
uniform vec3 u_light1;    // view space 主光
uniform vec3 u_light2;    // view space 补光
uniform vec3 u_light3dir; // 轮廓光方向(view space)
varying vec3 v_nrm;
varying vec3 v_pos;
vec3 safe_norm(vec3 v) { return v / max(length(v), 1e-6); }
void main(void) {
    vec3 N = safe_norm(v_nrm);
    vec3 V = safe_norm(-v_pos);
    vec3 L1 = safe_norm(u_light1 - v_pos);
    vec3 L2 = safe_norm(u_light2 - v_pos);
    vec3 L3 = safe_norm(u_light3dir);
    // 半兰伯特（包裹光）：背光面也不落到纯黑，弱化强阴影、整体提亮
    // （用户要求「亮度调高，无需很强的阴影感」）。
    float d1 = dot(N, L1) * 0.5 + 0.5;
    float d2 = dot(N, L2) * 0.5 + 0.5;
    float d3 = max(dot(N, L3), 0.0);
    vec3 albedo = u_color.rgb;
    vec3 c = albedo * (0.58 + 0.42 * d1 + 0.18 * d2 + 0.10 * d3);
    vec3 H = safe_norm(L1 + V);
    float spec = pow(max(dot(N, H), 0.0), 26.0) * 0.12;
    c += vec3(0.35, 0.38, 0.45) * spec;
    gl_FragColor = vec4(c, u_color.a);
}
"""


def _srgb(c: float) -> float:
    """线性色 → sRGB（Blender 的 diffuse_color 是线性的，QColor 按 sRGB 解释）。"""
    c = max(0.0, min(1.0, float(c)))
    return 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055


class Pet3DView(QOpenGLWidget):
    """透明桌宠视图：直接 GL 渲染，背景透明（DWM 合成）。"""

    pressed = pyqtSignal(object)          # 全局坐标 QPoint
    dragged = pyqtSignal(object)
    released = pyqtSignal(object)
    doubleClicked = pyqtSignal()
    contextRequested = pyqtSignal(object)

    def __init__(self, rig: PetRig, parent: QWidget | None = None,
                 size: QSize | None = None, transparent: bool = True):
        super().__init__(parent)
        self._rig = rig
        self._transparent = bool(transparent)
        self._time = 0.0
        self._speed = 1.0
        self._look_yaw = 0.0
        self._look_pitch = 0.0
        # 平滑后的实际朝向 / 躯干分量（advance 里逐帧低通，绘制只用这三个）
        self._look_yaw_s = 0.0
        self._look_pitch_s = 0.0
        self._torso_yaw_s = 0.0
        self._torso_pitch_s = 0.0
        self._tail_amp = 0.0
        self._tail_phase = 0.0
        self._ear_twitch = 0.0
        self._leg_swing = 0.0
        self._hop = 0.0
        self._lean = 0.0
        self._squash = 1.0
        self._body_yaw = 0.0
        self._breath_phase = 0.0
        # 眨眼状态：clock 用真实 dt 累加（_time 会被速度缩放，不能用来计时）
        self._blink_clock = 0.0
        self._blink = 0.0
        # 眼睛/高光的绑定空间质心：眨眼时绕各自质心沿竖直轴（绑定 Z）压扁
        self._blink_parts: dict = {}
        for _p in rig.parts:
            if _p.name in _BLINK_EYE_PARTS or _p.name in _BLINK_GLINT_PARTS:
                self._blink_parts[_p.name] = np.asarray(
                    _p.pos[:, :3], dtype=np.float64).mean(axis=0)

        if self._transparent:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            # 帧缓冲必须带 alpha 通道：否则真透明窗口会被 DWM 合成为黑底
            # （「背景不透明」的根因）。必须在 show 之前设置。
            fmt = QSurfaceFormat()
            fmt.setAlphaBufferSize(8)
            fmt.setDepthBufferSize(24)
            fmt.setSamples(0)
            self.setFormat(fmt)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setMouseTracking(True)

        self._meshes = []          # [(vao, count, part)]
        self._prog = None
        self._f = None
        self._attribs = (-1, -1)
        self._uniforms = {}
        self._mvp_cache = {}

        if size is not None:
            self.resize(size)
        else:
            self.resize(DEFAULT_WIDTH, int(DEFAULT_WIDTH * _ASPECT_H / _ASPECT_W))

    # ---------- GL ----------
    def initializeGL(self) -> None:
        f = QOpenGLFunctions_2_0()
        f.initializeOpenGLFunctions()
        self._f = f

        prog = QOpenGLShaderProgram(self)
        vs = QOpenGLShader(QOpenGLShader.ShaderTypeBit.Vertex, self)
        fs = QOpenGLShader(QOpenGLShader.ShaderTypeBit.Fragment, self)
        vs.compileSourceCode(QByteArray(VERT_SRC.encode()))
        fs.compileSourceCode(QByteArray(FRAG_SRC.encode()))
        prog.addShader(vs)
        prog.addShader(fs)
        if not prog.link():
            raise RuntimeError("桌宠着色器链接失败: %s" % prog.log())
        self._prog = prog

        self._attribs = (prog.attributeLocation(b"a_pos"),
                         prog.attributeLocation(b"a_nrm"))
        self._uniforms = {
            "mvp": prog.uniformLocation(b"u_mvp"),
            "mv": prog.uniformLocation(b"u_mv"),
            "norm": prog.uniformLocation(b"u_norm"),
            "color": prog.uniformLocation(b"u_color"),
            "light1": prog.uniformLocation(b"u_light1"),
            "light2": prog.uniformLocation(b"u_light2"),
            "light3": prog.uniformLocation(b"u_light3dir"),
        }

        f.glEnable(_GL_DEPTH_TEST)
        # 不启用背面剔除：网格绕向与原版 Qt3D 渲染一致（QPhongMaterial 默认不剔除）

        # 相机（与原版 _frame_camera 同一套取景公式）
        lo, hi = self._rig.bounds_min, self._rig.bounds_max
        height = float(hi[2] - lo[2])
        width = float(max(hi[0] - lo[0], hi[1] - lo[1]))
        dist = (max(height, width) * 0.5) / math.tan(math.radians(_FOV_DEG) / 2) / 0.78
        cy = float((hi[2] + lo[2]) * 0.5)
        self._view = _look_at(np.array([0.0, cy + height * 0.12, dist]),
                              np.array([0.0, cy, 0.0]),
                              np.array([0.0, 1.0, 0.0]))

        # 部件网格：索引展开为非索引顶点（glDrawElements 本机必崩）
        for part in self._rig.parts:
            vao = QOpenGLVertexArrayObject(self)
            vao.create()
            vao.bind()
            vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
            vbo.create()
            vbo.bind()
            base = np.concatenate([part.pos[:, :3], part.nrm[:, :3]],
                                  axis=1).astype(np.float32)
            verts = base[part.indices]
            vbo.allocate(verts.tobytes(), verts.nbytes)
            for a in self._attribs:
                if a >= 0:
                    f.glEnableVertexAttribArray(a)
            f.glVertexAttribPointer(self._attribs[0], 3, _GL_FLOAT, False, 24, 0)
            f.glVertexAttribPointer(self._attribs[1], 3, _GL_FLOAT, False, 24, 12)
            vbo.release()
            vao.release()
            self._meshes.append((vao, len(verts), part))

        self._mats = np.zeros((len(self._meshes), 4, 4), dtype=np.float64)

    def _frame_gl(self, f) -> None:
        """清屏（透明/不透明）→ 计算姿态 → 逐部件上传并绘制。"""
        f.glViewport(0, 0, max(1, self.width()), max(1, self.height()))
        if self._transparent:
            f.glClearColor(0.0, 0.0, 0.0, 0.0)
        else:
            f.glClearColor(0.082, 0.094, 0.118, 1.0)
        f.glClear(_GL_COLOR_BUFFER_BIT | _GL_DEPTH_BUFFER_BIT)

        prog = self._prog
        if prog is None:
            return
        prog.bind()
        u = self._uniforms

        # 灯光（view space，与原版 _build_lights 一致）
        f.glUniform3f(u["light1"], 1.6, 2.4, 2.6)
        f.glUniform3f(u["light2"], -2.2, 1.1, 1.5)
        f.glUniform3f(u["light3"], 0.0, -0.8, -1.2)

        # 根变换：绕 X -90° 立起（Blender Z-up → GL Y-up）+ body_yaw 转朝向 + 挤压
        # （纯 numpy 构造；QQuaternion 乘法在本机 GL 上下文内必崩）
        ry = np.deg2rad(self._body_yaw)
        rx = np.deg2rad(-90.0 + self._lean)
        cy_, sy_ = math.cos(ry), math.sin(ry)
        cx_, sx_ = math.cos(rx), math.sin(rx)
        r = np.eye(4)
        r[:3, :3] = np.array([[cy_, -sy_, 0.0], [sy_, cy_, 0.0],
                              [0.0, 0.0, 1.0]]) @ np.array(
            [[1.0, 0.0, 0.0], [0.0, cx_, -sx_], [0.0, sx_, cx_]])
        sq = max(0.25, self._squash)
        r[:, 0] *= 1.0 / sq
        r[:, 1] *= sq
        calm = 1.0 - 0.6 * min(1.0, self._speed / 2.0)
        breath = math.sin(self._breath_phase) * 0.007 * calm
        r[0, 3] = 0.0
        r[1, 3] = self._hop + breath
        r[2, 3] = 0.0

        mats = self._rig.pose(self._time, self._extra_rot(), self._extra_shift())
        blink = self._blink
        for i, (vao, count, part) in enumerate(self._meshes):
            m = mats[int(part.joints[0, 0])]
            model = r @ m
            # 眨眼：眼睛/高光绕各自质心沿绑定竖直轴压扁（眼睛闭成一条缝，高光几乎消失）。
            # 变换乘在蒙皮矩阵**右侧**（绑定空间）：压扁沿部件自身的竖直方向，之后才随
            # 头部骨骼旋转 —— 抬头/转头时闭眼方向也跟着转，不会歪掉。
            c = self._blink_parts.get(part.name)
            if blink > 1e-3 and c is not None:
                k = (_BLINK_EYE_OPEN_SCALE if part.name in _BLINK_EYE_PARTS
                     else _BLINK_GLINT_OPEN_SCALE)
                s = 1.0 - (1.0 - k) * blink
                xf = np.eye(4)
                xf[2, 2] = s
                xf[2, 3] = float(c[2]) * (1.0 - s)
                model = model @ xf
            mv = self._view @ model
            mvp = self._proj @ mv
            norm = np.linalg.inv(mv[:3, :3])
            f.glUniformMatrix4fv(u["mvp"], 1, True,
                                 np.ascontiguousarray(mvp, dtype=np.float32).tobytes())
            f.glUniformMatrix4fv(u["mv"], 1, True,
                                 np.ascontiguousarray(mv, dtype=np.float32).tobytes())
            f.glUniformMatrix3fv(u["norm"], 1, True,
                                 np.ascontiguousarray(norm, dtype=np.float32).tobytes())
            col = part.color
            f.glUniform4f(u["color"], _srgb(col[0]), _srgb(col[1]), _srgb(col[2]), 1.0)
            vao.bind()
            f.glDrawArrays(_GL_TRIANGLES, 0, count)
            vao.release()
        prog.release()

    def paintGL(self) -> None:
        if self._prog is None:
            return
        self._proj = _perspective(_FOV_DEG, self.width() / max(1, self.height()),
                                  0.05, 100.0)
        self._frame_gl(self._f)

    # ---------- 动画 ----------
    def _extra_rot(self) -> dict:
        extra = {}
        # 头部跟随：总朝向 = 躯干分量 + 头部余量（Spine 是 Head 的父级，两层角度会叠加，
        # 这里把头部角度扣掉躯干那份，保证「看向鼠标」的总角度仍等于 look_yaw/pitch）。
        ty, tp = self._torso_yaw_s, self._torso_pitch_s
        extra["Head"] = (_axis_angle("x", self._look_pitch_s - tp)
                         @ _axis_angle("y", self._look_yaw_s - ty))
        # 躯干跟随：Spine 转一个小角度（身体/肚皮/项圈都绑在它下面，会一起轻轻转向）
        if abs(ty) > 0.05 or abs(tp) > 0.05:
            extra["Spine"] = _axis_angle("x", tp) @ _axis_angle("y", ty)
        wag = self._tail_amp
        for i, name in enumerate(("Tail_1", "Tail_2", "Tail_3")):
            ph = self._tail_phase - i * 0.55
            extra[name] = (_axis_angle("z", math.sin(ph) * wag * (0.5 + 0.25 * i))
                           @ _axis_angle("x", math.cos(ph * 0.5) * wag * 0.22 * (i + 1)))
        tw = math.sin(self._time * 7.5) * self._ear_twitch
        extra["L_Ear"] = _axis_angle("x", tw)
        extra["R_Ear"] = _axis_angle("x", -tw)
        sw = math.sin(self._time * 6.0) * self._leg_swing
        extra["L_Foreleg"] = _axis_angle("x", sw)
        extra["R_Hindleg"] = _axis_angle("x", sw)
        extra["R_Foreleg"] = _axis_angle("x", -sw * 0.6)
        extra["L_Hindleg"] = _axis_angle("x", -sw * 0.6)
        return extra

    def _extra_shift(self) -> dict:
        # 四肢/尾巴用世界空间平移做"看得见的"摆动（旋转是原地打转）
        shift = {}
        if abs(self._tail_amp) > 0.5:
            amp = 0.004 * self._tail_amp
            for i, name in enumerate(("Tail_1", "Tail_2", "Tail_3")):
                ph = self._tail_phase - i * 0.5
                shift[name] = (math.sin(ph) * amp * (0.8 + 0.35 * i), 0.0,
                               math.cos(ph * 1.3) * amp * 0.35)
        if self._leg_swing > 0.5:
            step = math.sin(self._time * 6.0) * 0.004 * self._leg_swing
            shift["L_Foreleg"] = (0.0, 0.0, step)
            shift["R_Hindleg"] = (0.0, 0.0, step)
            shift["R_Foreleg"] = (0.0, 0.0, -step * 0.6)
            shift["L_Hindleg"] = (0.0, 0.0, -step * 0.6)
        return shift

    def set_motion(self, **kwargs) -> None:
        for name in ("speed", "look_yaw", "look_pitch", "tail_amp", "ear_twitch",
                     "leg_swing", "hop", "lean", "squash", "body_yaw"):
            if kwargs.get(name) is not None:
                setattr(self, "_" + name, float(kwargs[name]))

    def advance(self, dt: float) -> None:
        """推进一帧动画：只更新状态 + 请求重绘，矩阵/绘制都在 paintGL 里。"""
        dt = max(0.0, min(0.1, float(dt)))
        self._time += dt * max(0.05, self._speed)
        self._tail_phase += dt * (2.4 + 5.0 * min(1.0, abs(self._tail_amp) / 26.0))
        self._breath_phase += dt * 5.7
        # 头/躯干朝向低通：cursor 每帧给的是「目标角」，这里做指数平滑后再参与蒙皮，
        # 观感是「丝滑转头」而不是逐帧硬切（30fps 下尤其明显）。
        k = 1.0 - math.exp(-dt / _LOOK_TAU)
        self._look_yaw_s += (self._look_yaw - self._look_yaw_s) * k
        self._look_pitch_s += (self._look_pitch - self._look_pitch_s) * k
        self._torso_yaw_s += (self._look_yaw * _TORSO_YAW_RATIO - self._torso_yaw_s) * k
        self._torso_pitch_s += (self._look_pitch * _TORSO_PITCH_RATIO - self._torso_pitch_s) * k
        # 眨眼：周期末尾开始，先闭后睁；启动 3 秒后才第一次眨，不会一出现就闭眼
        self._blink_clock += dt
        _since = self._blink_clock % _BLINK_PERIOD_S
        _start = _BLINK_PERIOD_S - (_BLINK_DOWN_S + _BLINK_UP_S)
        if _since >= _start:
            _ph = _since - _start
            if _ph < _BLINK_DOWN_S:
                p = _ph / _BLINK_DOWN_S
            else:
                p = 1.0 - (_ph - _BLINK_DOWN_S) / _BLINK_UP_S
            self._blink = max(0.0, min(1.0, p)) ** 2 * (3.0 - 2.0 * max(0.0, min(1.0, p)))
        else:
            self._blink = 0.0
        self.update()

    # ---------- 鼠标：QOpenGLWidget 直接收 ----------
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.pressed.emit(e.globalPosition().toPoint())
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            self.dragged.emit(e.globalPosition().toPoint())
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self.released.emit(e.globalPosition().toPoint())
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.doubleClicked.emit()
        super().mouseDoubleClickEvent(e)

    def contextMenuEvent(self, e):
        self.contextRequested.emit(e.globalPos())
        e.accept()


def _look_at(eye, center, up) -> np.ndarray:
    """视图矩阵（行主序 4×4，上传时 transpose）。"""
    f = center - eye
    f = f / np.linalg.norm(f)
    s = np.cross(f, up)
    s = s / np.linalg.norm(s)
    u = np.cross(s, f)
    m = np.eye(4)
    m[0, :3] = s
    m[1, :3] = u
    m[2, :3] = -f
    m[0, 3] = -np.dot(s, eye)
    m[1, 3] = -np.dot(u, eye)
    m[2, 3] = np.dot(f, eye)
    return m


def _perspective(fov_deg: float, aspect: float, near: float, far: float) -> np.ndarray:
    f = 1.0 / math.tan(math.radians(fov_deg) / 2)
    m = np.zeros((4, 4))
    m[0, 0] = f / aspect
    m[1, 1] = f
    m[2, 2] = (far + near) / (near - far)
    m[2, 3] = 2 * far * near / (near - far)
    m[3, 2] = -1.0
    return m
