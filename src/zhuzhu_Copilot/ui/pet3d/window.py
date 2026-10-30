"""3D 桌宠窗口：QOpenGLWidget 直渲 + 固定在右下角（可选跟随）+ 看光标 + 交互动画状态机。

行为
----
- **默认固定在右下角**（与 GIF 桌宠同一落点，留白 16px）：启动即在那里，**不吸附、
  不追踪鼠标位置**；
- **靠近才看你**：光标离小猫近（窗口边缘 70px 内）时，头部 + 躯干按「猫→光标」
  的方向平滑转向（叠加骨骼旋转，不改烘焙动画）：在头顶就抬头、在左上/右上就抬头朝
  对应方向看、在下方则低头；光标离开到 260px 以外平滑收回正视前方（远处不理会）；
- **眨眼**：每 3 秒眨一次（真实时间驱动，与动画播放速度无关；眼睛压成一条缝、高光消失）；
- **互动**：单击 → 跳一下（挤压 + 起跳 + 尾巴大幅摆）；双击 → 打开 AI 主面板；
  右键 → 菜单（回到右下角 / 跟随鼠标 / 退出）；拖动 → 抓着走，四肢垂下；
- **可选跟随（默认关闭）**：右键菜单「跟随鼠标」开启后窗口才缓动追着光标走，
  移动越快播放越快、腿摆越大、身体前倾越多；光标静止 6 秒后自己回右下角。

所有 3D 交互都是**程序化**的——烘焙进来的内置骨骼动画提供基础节奏（116 帧摇摆），
扭头/甩尾/摆腿/跳跃是每帧叠加的附加旋转，因此不需要额外美术资源。

透明：新渲染层（view.Pet3DView）用 QOpenGLWidget 直渲，Windows DWM 对
QOpenGLWidget 的 alpha 合成是可靠路径（真机验证过），因此**默认就是真透明**，
不再需要旧的「圆角卡片遮罩」或「离屏回读」。环境变量 ZHIZHU_PET3D_TRANSPARENT=0
可强制回退到圆角卡片观感（排障/特殊需求时用）。
"""
from __future__ import annotations

import math
import os
import time
from pathlib import Path

from PyQt6.QtCore import QPoint, QSize, Qt, QTimer
from PyQt6.QtGui import QAction, QCursor, QGuiApplication
from PyQt6.QtWidgets import QApplication, QMenu, QWidget

from zhuzhu_Copilot.core.i18n import ui as _ui

from .rig import PetRig
from .view import DEFAULT_WIDTH, Pet3DView

#: 右下角留白（像素）：与 GIF 桌宠的位置一致（见 desktop_pet._place_bottom_right）
PET_CORNER_MARGIN = 16
#: 默认**不吸附鼠标**：启动即固定在右下角；右键菜单可手动开启「跟随鼠标」
FOLLOW_BY_DEFAULT = False

#: 追随的判定阈值（像素/秒）：低于 IDLE 认为光标没动；高于 RUN 视为奔跑
SPEED_IDLE = 24.0
SPEED_RUN = 900.0
#: 光标静止多久后桌宠自己回角落（秒）
IDLE_RETURN_S = 6.0
#: 一帧最长步长（秒）：切后台/卡顿后不要把动画一次性推进太多
MAX_DT = 0.1


def corner_pos(size: QSize, avail) -> QPoint:
    """右下角锚点（纯函数）：桌宠左上角坐标，与 GIF 桌宠的落点一致。"""
    return QPoint(avail.right() - size.width() - PET_CORNER_MARGIN,
                  avail.bottom() - size.height() - PET_CORNER_MARGIN)


#: 右侧/上方方向上的视线角度上限（度）：再远的光标按上限饱和
LOOK_YAW_MAX_DEG = 38.0
LOOK_PITCH_MAX_DEG = 26.0
#: 光标偏移 → 角度的参考距离（像素）：越小越灵敏。
#: 只有光标靠近桌宠时才跟随，所以这里按「近身范围」标定（150/130），
#: 光标在 50px 外就能看到明显的转头，而不是要横跨半个屏幕才有效果。
LOOK_YAW_REF_PX = 150.0
LOOK_PITCH_REF_PX = 130.0
#: 视线跟随的触发距离（像素，相对桌宠窗口边缘）：
#: 窗口内/NEAR 以内 → 完全跟随；超过 FAR → 平滑回到正视前方（鼠标离得远就不理）
LOOK_NEAR_PX = 70.0
LOOK_FAR_PX = 260.0


def look_weight(pet_rect, cursor: QPoint) -> float:
    """光标离桌宠的远近 → 跟随权重（窗口内=1，远处=0，中间 smoothstep 过渡）。

    用户要求「鼠标靠近小猫时，头部和躯干才跟随」：远处光标不应该让猫一直歪着头
    盯屏幕另一侧；用窗口边缘距离（而不是中心点）判断，贴着猫身边挪动也算「靠近」。
    """
    dx = max(pet_rect.left() - cursor.x(), 0, cursor.x() - pet_rect.right())
    dy = max(pet_rect.top() - cursor.y(), 0, cursor.y() - pet_rect.bottom())
    dist = math.hypot(dx, dy)
    if dist <= LOOK_NEAR_PX:
        return 1.0
    if dist >= LOOK_FAR_PX:
        return 0.0
    t = (dist - LOOK_NEAR_PX) / (LOOK_FAR_PX - LOOK_NEAR_PX)
    t = t * t * (3.0 - 2.0 * t)          # smoothstep：进出都不会突跳
    return 1.0 - t


def look_angles(center: QPoint, cursor: QPoint) -> tuple:
    """光标相对桌宠中心的偏移 → (yaw, pitch) 角度（度，纯函数便于测试）。

    方向约定（已用骨骼实测校准）：
      - **+yaw = 向屏幕右侧看**（Head 绕 y 轴 +：头转向屏幕右）；
      - **-pitch = 抬头**：Head 绕 x 轴 +x 会让鼻子往下掉（低头），所以光标在头顶
        上方（dy < 0）必须输出**负** pitch。这是「鼠标在头顶就抬头」的关键符号。
    """
    dx = cursor.x() - center.x()
    dy = cursor.y() - center.y()
    yaw = max(-LOOK_YAW_MAX_DEG,
              min(LOOK_YAW_MAX_DEG, dx / LOOK_YAW_REF_PX * LOOK_YAW_MAX_DEG))
    pitch = max(-LOOK_PITCH_MAX_DEG,
                min(LOOK_PITCH_MAX_DEG, dy / LOOK_PITCH_REF_PX * LOOK_PITCH_MAX_DEG))
    return yaw, pitch


def follow_target(cursor: QPoint, size: QSize, avail, idle: bool) -> QPoint:
    """把「光标位置」翻译成桌宠窗口左上角该在的位置（纯函数，便于测试）。

    - 跟随态：站在光标右下侧（不盖住光标本身），并夹在可用区域内；
    - 待机态（光标长时间不动）：回到屏幕右下角。
    """
    if idle:
        return corner_pos(size, avail)
    target = QPoint(cursor.x() - int(size.width() * 0.30),
                    cursor.y() - int(size.height() * 0.55))
    x = max(avail.left() + 4, min(target.x(), avail.right() - size.width() - 4))
    y = max(avail.top() + 4, min(target.y(), avail.bottom() - size.height() - 4))
    return QPoint(x, y)


def motion_for(velocity: float, dragging: bool = False,
               happy: bool = False) -> dict:
    """速度/交互状态 → 动画参数（播放倍速、尾巴幅度、腿部摆幅、前倾、耳朵抖）。

    idle：几乎不动，只留尾巴慢摆 + 呼吸；walk：腿摆起来、身体微前倾；
    run：更快更大；被打断/被点：跳 + 尾巴狂甩。
    """
    if dragging:
        return dict(speed=0.5, tail=4.0, leg=0.0, lean=-12.0, ear=0.0)
    if happy:
        return dict(speed=1.7, tail=26.0, leg=10.0, lean=0.0, ear=9.0)
    if velocity > SPEED_RUN:
        return dict(speed=2.0, tail=20.0, leg=15.0, lean=8.0, ear=6.0)
    if velocity > SPEED_IDLE:
        return dict(speed=1.1, tail=13.0, leg=9.0, lean=4.0, ear=2.0)
    return dict(speed=0.55, tail=7.0, leg=0.0, lean=0.0, ear=0.0)


def asset_prefix() -> Path | None:
    """烘焙资产位置：frozen 取 _MEIPASS/assets/pet3d/kitty，源码取项目 assets 下。"""
    import sys
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", ".")) / "assets" / "pet3d"
    else:
        base = Path(__file__).resolve().parents[4] / "assets" / "pet3d"   # 包在 ui/pet3d 下，多一层
    prefix = base / "kitty"
    for ext in (".npz", ".json"):
        if not prefix.with_suffix(ext).is_file():
            return None
    return prefix


def available() -> bool:
    """3D 桌宠是否可用：Qt OpenGL widget 模块 + numpy + 烘焙资产三样齐全才用。"""
    try:
        import numpy  # noqa: F401
        from PyQt6 import QtOpenGLWidgets  # noqa: F401
        from PyQt6 import QtOpenGL  # noqa: F401
    except Exception:
        return False
    return asset_prefix() is not None


def _want_transparent() -> bool:
    """是否真透明背景（默认开）。设 ZHIZHU_PET3D_TRANSPARENT=0 回退圆角卡片观感。"""
    v = str(os.environ.get("ZHIZHU_PET3D_TRANSPARENT", "1")).strip().lower()
    return v not in ("0", "false", "off", "no", "")


#: 桌宠窗口尺寸（宽，像素）。真透明贴图，所以尺寸就是猫的大小。
#: 宽高比单一来源在 view（渲染取景 300:340），宽度默认取 view.DEFAULT_WIDTH（112）。
PET_WIDTH = DEFAULT_WIDTH
PET_HEIGHT = int(PET_WIDTH * 340 / 300)


class Pet3DWindow(QWidget):
    """无边框透明置顶窗口，内部是 QOpenGLWidget 直渲视图。"""

    def __init__(self, prefix: Path | None = None, open_panel_cb=None,
                 size: QSize | None = None, rig: PetRig | None = None):
        super().__init__()
        self._open_panel_cb = open_panel_cb
        # rig 可由后台线程预加载后传入（避免启动时同步解析 NPZ 阻塞主线程）
        self._rig = rig if rig is not None else PetRig(prefix)
        self._transparent = _want_transparent()
        self._view = Pet3DView(self._rig, self,
                               size=size or QSize(PET_WIDTH, PET_HEIGHT),
                               transparent=self._transparent)
        self._view.setGeometry(0, 0, self._view.width(), self._view.height())

        # NoDropShadowWindowHint：透明窗口不加系统阴影，否则 DWM 会给整块矩形
        # 画一圈阴影，看起来仍是个不透明的方框。
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool
                            | Qt.WindowType.NoDropShadowWindowHint)
        if self._transparent:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setMouseTracking(True)
        self.resize(self._view.size())

        # ---- 跟随状态 ----
        self._follow = FOLLOW_BY_DEFAULT
        self._dragging = False
        self._drag_offset = QPoint()
        self._last_cursor = None
        self._cursor_still_s = 0.0
        self._last_move_at = time.monotonic()
        self._vel = 0.0            # 光标速度（像素/秒，平滑后）
        self._happy_until = 0.0
        self._body_yaw = 0.0

        self._view.pressed.connect(self._on_press)
        self._view.dragged.connect(self._on_drag)
        self._view.released.connect(self._on_release)
        self._view.doubleClicked.connect(self._on_double_click)
        self._view.contextRequested.connect(self._on_context)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)                     # ~30fps：动效与开销的平衡点
        self._last_tick = time.monotonic()
        self._place_bottom_right()

    # ---------- 定位 ----------
    def _screen_rect(self, at: QPoint | None = None):
        screen = QGuiApplication.screenAt(at) if at is not None else None
        screen = screen or QGuiApplication.primaryScreen()
        return screen.availableGeometry()

    def _place_bottom_right(self) -> None:
        avail = self._screen_rect()
        self.move(corner_pos(self.size(), avail))

    # ---------- 主循环 ----------
    def _tick(self) -> None:
        now = time.monotonic()
        dt = min(MAX_DT, max(0.001, now - self._last_tick))
        self._last_tick = now

        cursor = QCursor.pos()
        if not self._dragging:
            self._update_follow(cursor, dt)
        self._update_pose(cursor, dt, now)
        self._view.advance(dt)

    def _update_follow(self, cursor: QPoint, dt: float) -> None:
        """窗口朝光标缓动：始终落后一小段，跑起来才像在追。"""
        prev = self._last_cursor or cursor
        dist = math.hypot(cursor.x() - prev.x(), cursor.y() - prev.y())
        inst = dist / dt
        self._vel += (inst - self._vel) * min(1.0, dt * 8.0)      # 平滑，防抖
        self._cursor_still_s = 0.0 if dist > 1.5 else self._cursor_still_s + dt
        self._last_cursor = cursor

        if not self._follow:
            return
        idle = self._cursor_still_s > IDLE_RETURN_S
        avail = self._screen_rect(None if idle else cursor)
        target = follow_target(cursor, self.size(), avail, idle)

        cur = self.pos()
        # 距离越远走得越快，但有上限：慢速跟随观感比瞬移好
        max_step = 1100.0 * dt
        dx, dy = target.x() - cur.x(), target.y() - cur.y()
        gap = math.hypot(dx, dy)
        if gap < 1.0:
            return
        step = min(max_step, gap * min(1.0, 6.0 * dt) + 2.0)
        ease = min(1.0, 0.12 + 0.5 * min(1.0, self._vel / SPEED_RUN))
        self.move(int(cur.x() + dx * ease), int(cur.y() + dy * ease))
        # 朝移动方向侧身（不做镜像缩放：那会破坏法线与绕序）
        self._body_yaw = -26.0 if dx < -2 else (26.0 if dx > 2 else 0.0)

    def _update_pose(self, cursor: QPoint, dt: float, now: float) -> None:
        """把「速度 + 光标方位 + 点击状态」翻译成每帧的动画参数。"""
        center = self.geometry().center()
        # 光标在头顶 → 抬头；左上 → 抬头看左上；右上 → 抬头看右上（符号见 look_angles）。
        # 但**只有靠近小猫时**才跟随：远处权重为 0，目标角回 0，低通会平滑转回正视前方。
        yaw, pitch = look_angles(center, cursor)
        w = look_weight(self.geometry(), cursor)
        yaw *= w
        pitch *= w

        happy = now < self._happy_until
        # 未开启跟随时窗口不会移动：不能把「光标在动」当成「猫在跑」，否则会原地奔跑。
        # 拖动时交给 dragging 分支（四肢垂下），与速度无关。
        vel = self._vel if (self._follow or self._dragging) else 0.0
        m = motion_for(vel, dragging=self._dragging, happy=happy)
        speed, tail, leg, lean, ear = (m["speed"], m["tail"], m["leg"],
                                      m["lean"], m["ear"])

        # 跳跃：起跳后按抛物线下落，落地带一次挤压缓冲
        hop, squash = 0.0, 1.0
        if happy:
            k = max(0.0, 1.0 - (self._happy_until - now) / 0.9)
            hop = math.sin(min(1.0, k) * math.pi) * 0.22
            squash = 1.0 + 0.10 * math.cos(min(1.0, k) * math.pi * 2)

        self._view.set_motion(speed=speed, look_yaw=yaw,
                              look_pitch=pitch, tail_amp=tail, ear_twitch=ear,
                              leg_swing=leg, hop=hop, lean=lean,
                              squash=squash, body_yaw=self._body_yaw)

    # ---------- 鼠标交互 ----------
    def _on_press(self, global_pos: QPoint) -> None:
        self._dragging = True
        self._drag_offset = global_pos - self.pos()
        self._happy_until = time.monotonic() + 0.9

    def _on_drag(self, global_pos: QPoint) -> None:
        if self._dragging:
            self.move(global_pos - self._drag_offset)

    def _on_release(self, global_pos: QPoint) -> None:
        self._dragging = False

    def _on_double_click(self) -> None:
        if callable(self._open_panel_cb):
            self._open_panel_cb()

    def _on_context(self, global_pos: QPoint) -> None:
        menu = QMenu(self)
        act_stay = QAction(_ui("回到右下角"), self)
        act_stay.triggered.connect(self._place_bottom_right)
        act_follow = QAction(_ui("暂停跟随鼠标") if self._follow else _ui("恢复跟随鼠标"), self)
        act_follow.triggered.connect(self._toggle_follow)
        act_quit = QAction(_ui("退出"), self)
        act_quit.triggered.connect(self.hide)
        menu.addAction(act_stay)
        menu.addAction(act_follow)
        menu.addSeparator()
        menu.addAction(act_quit)
        menu.exec(global_pos)

    def _toggle_follow(self) -> None:
        self._follow = not self._follow
        if not self._follow:
            self._place_bottom_right()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        view = getattr(self, "_view", None)
        if view is not None:
            view.setGeometry(0, 0, self.width(), self.height())

    # ---------- 生命周期 ----------
    def hideEvent(self, event) -> None:
        """隐藏时停掉 33ms 定时器：桌宠不可见就别烧 CPU/GPU。"""
        self._timer.stop()
        super().hideEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._last_tick = time.monotonic()
        if not self._timer.isActive():
            self._timer.start(33)

    def closeEvent(self, event) -> None:
        self._timer.stop()
        super().closeEvent(event)
