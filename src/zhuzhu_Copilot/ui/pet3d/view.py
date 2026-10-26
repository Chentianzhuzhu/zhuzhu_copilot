"""Qt 3D 渲染层：把烘焙好的桌宠按帧蒙皮后画出来。

为什么是「每帧上传顶点」而不是让 Qt 3D 自己蒙皮：Qt 6 的 Qt 3D 没有 FBX/glTF 场景
导入插件，也不支持从 Python 侧组装 GPU 蒙皮管线；而这份资产的每个部件都是**刚性块**
（只受一根骨头影响），CPU 侧用 numpy 做一次 3×3 变换只要零点几毫秒，14k 顶点/30fps
的总上传量约 10MB/s，完全够用。好处是骨骼姿态完全由我们控制，交互动画（扭头、甩尾、
跳跃）可以逐帧叠加在烘焙动画之上。

坐标：资产是 Blender 世界空间（Z 朝上），根实体统一绕 X 轴 -90° 转到 Qt 的 Y 朝上；
因此「面向 +Z」就是猫脸朝向相机。
"""
from __future__ import annotations

import math

import numpy as np
from PyQt6.QtCore import QByteArray, QEvent, QPoint, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QMatrix4x4, QQuaternion, QSurfaceFormat, QVector3D
from PyQt6.QtWidgets import QVBoxLayout, QWidget
from PyQt6.Qt3DCore import QAttribute, QBuffer, QEntity, QGeometry, QTransform
from PyQt6.Qt3DExtras import Qt3DWindow, QPhongMaterial
from PyQt6.Qt3DRender import QDirectionalLight, QGeometryRenderer, QPointLight

from .rig import PetRig, _axis_angle


def _srgb(c: float) -> float:
    """线性色 → sRGB（Blender 的 diffuse_color 是线性的，QColor 按 sRGB 解释）。"""
    c = max(0.0, min(1.0, float(c)))
    return 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055


def _qcolor(rgb, alpha: int = 255) -> QColor:
    r, g, b = (int(round(_srgb(v) * 255)) for v in rgb[:3])
    return QColor(r, g, b, alpha)


class Pet3DView(QWidget):
    """桌宠 3D 视图（内嵌 Qt3DWindow）。外部只需喂 dt 与互动参数。"""

    pressed = pyqtSignal(object)          # 全局坐标 QPoint
    dragged = pyqtSignal(object)
    released = pyqtSignal(object)
    doubleClicked = pyqtSignal()
    contextRequested = pyqtSignal(object)

    def __init__(self, rig: PetRig, parent=None, size: QSize | None = None,
                 transparent: bool = True):
        super().__init__(parent)
        self._rig = rig
        self._time = 0.0
        self._speed = 1.0
        self._look_yaw = 0.0
        self._look_pitch = 0.0
        self._tail_amp = 0.0
        self._tail_phase = 0.0
        self._ear_twitch = 0.0
        self._leg_swing = 0.0
        self._hop = 0.0
        self._lean = 0.0
        self._squash = 1.0
        self._body_yaw = 0.0
        self._breath_phase = 0.0

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, transparent)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, transparent)

        fmt = QSurfaceFormat()
        fmt.setAlphaBufferSize(8 if transparent else 0)
        fmt.setSamples(4)
        QSurfaceFormat.setDefaultFormat(fmt)

        self._win = Qt3DWindow()
        # 显式给这个 QWindow 设带 alpha 的 surface format：只在 QApplication 之后
        # setDefaultFormat 是不够的（平台集成已初始化），逐窗口设置才会真正申请 alpha 通道。
        self._win.setFormat(fmt)
        self._win.defaultFrameGraph().setClearColor(
            QColor(0, 0, 0, 0) if transparent else QColor(6, 7, 10, 255))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._container = QWidget.createWindowContainer(self._win, self)
        self._container.setMinimumSize(1, 1)
        self._container.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay.addWidget(self._container)

        self._root = QEntity()
        self._root_tf = QTransform()
        self._root_tf.setRotation(QQuaternion.fromEulerAngles(-90.0, 0.0, 0.0))
        self._root.addComponent(self._root_tf)
        self._build_parts()
        self._build_lights()
        self._win.setRootEntity(self._root)
        self._frame_camera()

        # 鼠标事件：Qt3D 的窗口是原生子窗口，事件只到 QWindow、不到父 widget，
        # 也不能用「透明遮罩 widget 盖上去」——那会把 3D 视口遮住（实测整屏发白）。
        # 正确做法是给 QWindow 装事件过滤器。
        self._win.installEventFilter(self)

        if size is not None:
            self.resize(size)

    def eventFilter(self, obj, ev) -> bool:
        """把 3D 窗口上的鼠标事件转成信号，交给桌宠窗口做拖拽/点击。"""
        if obj is self._win:
            t = ev.type()
            if t == QEvent.Type.MouseButtonPress:
                self.pressed.emit(ev.globalPosition().toPoint())
            elif t == QEvent.Type.MouseMove:
                self.dragged.emit(ev.globalPosition().toPoint())
            elif t == QEvent.Type.MouseButtonRelease:
                self.released.emit(ev.globalPosition().toPoint())
            elif t == QEvent.Type.MouseButtonDblClick:
                self.doubleClicked.emit()
            elif t == QEvent.Type.ContextMenu:
                self.contextRequested.emit(ev.globalPos())
        return super().eventFilter(obj, ev)

    def resizeEvent(self, event) -> None:
        """相机宽高比跟着视口走（否则画面会被拉伸）。"""
        super().resizeEvent(event)
        try:
            h = max(1, self.height())
            w = max(1, self.width())
            cam = self._win.camera()
            cam.lens().setAspectRatio(w / h)
        except Exception:
            pass

    # ---------- 场景搭建 ----------
    def _build_parts(self) -> None:
        self._bufs = []
        self._parts = []
        for part in self._rig.parts:
            geom = QGeometry(self._root)
            pos = np.ascontiguousarray(part.pos[:, :3], dtype=np.float32)
            nrm = np.ascontiguousarray(part.nrm[:, :3], dtype=np.float32)

            pos_buf = QBuffer(geom)
            pos_buf.setData(QByteArray(pos.tobytes()))
            pos_attr = QAttribute(geom)
            pos_attr.setName(QAttribute.defaultPositionAttributeName())
            pos_attr.setVertexBaseType(QAttribute.VertexBaseType.Float)
            pos_attr.setVertexSize(3)
            pos_attr.setAttributeType(QAttribute.AttributeType.VertexAttribute)
            pos_attr.setBuffer(pos_buf)
            pos_attr.setCount(len(pos))
            pos_attr.setByteStride(12)

            nrm_buf = QBuffer(geom)
            nrm_buf.setData(QByteArray(nrm.tobytes()))
            nrm_attr = QAttribute(geom)
            nrm_attr.setName(QAttribute.defaultNormalAttributeName())
            nrm_attr.setVertexBaseType(QAttribute.VertexBaseType.Float)
            nrm_attr.setVertexSize(3)
            nrm_attr.setAttributeType(QAttribute.AttributeType.VertexAttribute)
            nrm_attr.setBuffer(nrm_buf)
            nrm_attr.setCount(len(nrm))
            nrm_attr.setByteStride(12)

            # Qt6 去掉了 QBuffer::BufferType：一个 buffer 是索引还是顶点，由绑定它的
            # QAttribute 类型决定（见下方 IndexAttribute），无需也不能再 setType。
            idx_buf = QBuffer(geom)
            idx = np.ascontiguousarray(part.indices, dtype=np.uint32)
            idx_buf.setData(QByteArray(idx.tobytes()))
            idx_attr = QAttribute(geom)
            idx_attr.setVertexBaseType(QAttribute.VertexBaseType.UnsignedInt)
            idx_attr.setAttributeType(QAttribute.AttributeType.IndexAttribute)
            idx_attr.setBuffer(idx_buf)
            idx_attr.setCount(len(idx))

            geom.addAttribute(pos_attr)
            geom.addAttribute(nrm_attr)
            geom.addAttribute(idx_attr)

            renderer = QGeometryRenderer()
            renderer.setGeometry(geom)
            renderer.setPrimitiveType(QGeometryRenderer.PrimitiveType.Triangles)

            mat = QPhongMaterial(self._root)
            col = _qcolor(part.color)
            mat.setAmbient(QColor(int(col.red() * 0.38), int(col.green() * 0.38),
                                  int(col.blue() * 0.38), 255))
            mat.setDiffuse(col)
            mat.setSpecular(QColor(58, 64, 76, 255))
            mat.setShininess(26.0)

            ent = QEntity(self._root)
            ent.addComponent(renderer)
            ent.addComponent(mat)
            # 刚性部件（只受一根骨头影响）：顶点数据只上传一次，之后每帧只改实体
            # 变换矩阵 —— 让 GPU 去乘，省掉每帧 300KB 级的缓冲区上传。
            # 这份资产 21 个部件全是刚性块，所以常规路径下每帧几乎零上传。
            xf = QTransform()
            ent.addComponent(xf)
            self._parts.append(part)
            self._bufs.append((pos_buf, nrm_buf, xf))

    def _build_lights(self) -> None:
        """主光 + 冷色补光 + 背后轮廓光：低模在纯黑背景上也能看出体积。"""
        key = QEntity(self._root)
        kt = QTransform()
        kt.setTranslation(QVector3D(1.6, 2.4, 2.6))
        key.addComponent(kt)
        kl = QPointLight(key)
        kl.setColor(QColor(255, 246, 232))
        kl.setIntensity(1.2)
        key.addComponent(kl)

        fill = QEntity(self._root)
        ft = QTransform()
        ft.setTranslation(QVector3D(-2.2, 1.1, 1.5))
        fill.addComponent(ft)
        fl = QPointLight(fill)
        fl.setColor(QColor(150, 176, 216))
        fl.setIntensity(0.6)
        fill.addComponent(fl)

        rim = QEntity(self._root)
        rt = QTransform()
        rt.setRotation(QQuaternion.fromEulerAngles(-120.0, 0.0, 0.0))
        rim.addComponent(rt)
        rl = QDirectionalLight(rim)
        rl.setColor(QColor(130, 150, 190))
        rl.setIntensity(0.9)
        rim.addComponent(rl)

    def _frame_camera(self) -> None:
        """按包围盒自动取景：留 12% 边距，视线略高于中心。"""
        lo, hi = self._rig.bounds_min, self._rig.bounds_max
        height = float(hi[2] - lo[2])
        width = float(max(hi[0] - lo[0], hi[1] - lo[1]))
        fov = 34.0
        dist = (max(height, width) * 0.5) / math.tan(math.radians(fov) / 2) / 0.78
        cy = float((hi[2] + lo[2]) * 0.5)
        cam = self._win.camera()
        cam.lens().setPerspectiveProjection(fov, 1.0, 0.05, 100.0)
        cam.setPosition(QVector3D(0.0, cy + height * 0.12, dist))
        cam.setViewCenter(QVector3D(0.0, cy, 0.0))
        cam.setUpVector(QVector3D(0.0, 1.0, 0.0))

    # ---------- 外部驱动 ----------
    def set_motion(self, **kwargs) -> None:
        for name in ("speed", "look_yaw", "look_pitch", "tail_amp", "ear_twitch",
                     "leg_swing", "hop", "lean", "squash", "body_yaw"):
            if kwargs.get(name) is not None:
                setattr(self, "_" + name, float(kwargs[name]))

    def advance(self, dt: float) -> None:
        """推进一帧：算骨骼矩阵 → 蒙皮 → 上传顶点。dt 单位为秒。"""
        dt = max(0.0, min(0.1, float(dt)))
        self._time += dt * max(0.05, self._speed)
        self._tail_phase += dt * (2.4 + 5.0 * min(1.0, abs(self._tail_amp) / 26.0))
        # 呼吸起伏：越静止越明显（0.9Hz，幅度约身高的 0.4%），跑动时收小
        self._breath_phase += dt * 5.7
        calm = 1.0 - 0.6 * min(1.0, self._speed / 2.0)
        breath = math.sin(self._breath_phase) * 0.007 * calm

        extra = {}
        extra["Head"] = _axis_angle("x", self._look_pitch) @ _axis_angle("y", self._look_yaw)
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

        # 四肢/尾巴用世界空间平移做"看得见的"摆动（旋转是原地打转，见 rig.pose 注释）
        shift = {}
        if abs(self._tail_amp) > 0.5:
            amp = 0.004 * self._tail_amp          # 单摆幅度：按尾巴摆幅档位缩放
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

        mats = self._rig.pose(self._time, extra, shift)

        # 先绕 X 把模型立起来（Blender Z-up → Qt Y-up），再绕世界 Y 转身体朝向；
        # 不做负缩放镜像：那会把法线与三角形绕序一起翻掉，光照和背面剔除都会错。
        self._root_tf.setRotation(
            QQuaternion.fromAxisAndAngle(QVector3D(0, 1, 0), self._body_yaw)
            * QQuaternion.fromAxisAndAngle(QVector3D(1, 0, 0), -90.0 + self._lean))
        self._root_tf.setTranslation(QVector3D(0.0, self._hop + breath, 0.0))
        sq = max(0.25, self._squash)
        self._root_tf.setScale3D(QVector3D(1.0 / sq, sq, 1.0 / sq))

        for part, (pos_buf, nrm_buf, xf) in zip(self._parts, self._bufs):
            if part.rigid:
                m = mats[int(part.joints[0, 0])]
                xf.setMatrix(QMatrix4x4(*m.astype(float).ravel().tolist()))
                continue
            pos, nrm = self._rig.skin(part, mats)
            pos_buf.setData(QByteArray(pos.tobytes()))
            nrm_buf.setData(QByteArray(nrm.tobytes()))
