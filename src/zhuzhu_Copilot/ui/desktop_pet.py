"""桌宠：默认 3D（pet3d：QOpenGLWidget 直渲 + 跟随鼠标），不可用时回退 GIF。

3D 桌宠的模块导入与资产解析在后台线程预加载（preload_pet3d），启动动画期间完成，
面板打开后建窗口几乎零延迟。

GIF 回退：透明 GIF 常驻桌面右下角（任务栏上方），悬停循环播放、离开停回首帧，
每 60 秒自动播一次。用 PIL 预加载帧，避免 QMovie 加载大 GIF 崩溃。
"""
import os
import sys
from pathlib import Path
from typing import List, Tuple

from zhuzhu_Copilot.core.i18n import ui as _ui

from PyQt6.QtCore import Qt, QPoint, QSize, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QPixmap, QIcon, QAction
from PyQt6.QtWidgets import QApplication, QWidget, QLabel, QMenu


def _pet_gif_path() -> str:
    """桌宠 GIF：frozen 打包取 _MEIPASS/assets/pet.gif（spec 的 datas 把 assets 整目录
    打进 _MEIPASS/assets），源码直跑取项目 assets/pet.gif，缺失则返回空串"""
    if getattr(sys, "frozen", False):
        # 打包后 assets 目录位于 _MEIPASS/assets 下，pet.gif 在其中
        return os.path.join(getattr(sys, "_MEIPASS", "."), "assets", "pet.gif")
    base = str(Path(__file__).resolve().parents[3] / "assets")
    gif = os.path.join(base, "pet.gif")
    return gif if os.path.exists(gif) else ""


class DesktopPet(QWidget):
    def __init__(self, gif_path: str, open_panel_cb=None):
        super().__init__()
        self._drag_offset: QPoint | None = None
        self._open_panel_cb = open_panel_cb   # 双击桌宠快速打开 AI 主面板的回调
        self._playing = False    # 动画是否应推进（悬停持续播放 / 定时播一轮）
        self._auto_once = False  # 是否为"每 1 分钟播一轮"模式（播完一轮自动停下）
        self._frames: List[QPixmap] = []
        self._durations: List[int] = []
        self._current_frame = 0

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)

        # 用 PIL 预加载所有帧，避免 QMovie 崩溃
        self._load_frames(gif_path)
        if not self._frames:
            # 兜底：空窗口
            self.setFixedSize(240, 240)
            self._place_bottom_right()
            return

        # 定时器：帧切换
        self._frame_timer = QTimer(self)
        self._frame_timer.timeout.connect(self._advance_frame)
        self._frame_timer.start(self._durations[0] if self._durations else 100)

        # 定时器：每 60 秒自动播一次
        self._auto_timer = QTimer(self)
        self._auto_timer.setInterval(60_000)
        self._auto_timer.timeout.connect(self._auto_play_once)
        self._auto_timer.start()

        self._label = QLabel(self)
        self._label.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._label.setScaledContents(True)
        # 让鼠标事件穿透到父窗口，确保悬停 enter/leave 与拖拽都能正常触发
        self._label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        size = self._frames[0].size() if self._frames else QSize(240, 240)
        self.setFixedSize(size)
        self._label.setFixedSize(size)
        self._place_bottom_right()

        self.setWindowIcon(QIcon(gif_path))
        self._show_frame(0)

    # ---------- 加载 GIF 帧 ----------
    def _load_frames(self, path: str) -> None:
        """用 PIL 加载 GIF 所有帧为 QPixmap 列表

        GIF 透明只有 1-bit（像素要么全透要么不透，无半透明），边缘抗锯齿像素被
        二值化后形成明显的黑色锯齿与孤立杂点。这里对每帧 alpha 做「中值去杂点 +
        高斯羽化」，把硬边修成柔和的抗锯齿边缘。
        """
        try:
            from PIL import Image, ImageSequence, ImageFilter
            import io
            img = Image.open(path)
            # 注意：不能先对整张 GIF 做 convert("RGBA")，否则会丢失多帧迭代能力；
            # 需在原始图像上逐帧迭代，再对每一帧单独转 RGBA。
            for frame in ImageSequence.Iterator(img):
                frame_copy = frame.convert("RGBA")
                try:
                    _r, _g, _b, _a = frame_copy.split()
                    # 中值滤波去掉孤立杂点，再轻微高斯羽化硬边
                    _a = _a.filter(ImageFilter.MedianFilter(3))
                    _a = _a.filter(ImageFilter.GaussianBlur(1.1))
                    frame_copy = Image.merge("RGBA", (_r, _g, _b, _a))
                except Exception:
                    pass
                buf = io.BytesIO()
                frame_copy.save(buf, format="PNG")
                buf.seek(0)
                pixmap = QPixmap()
                pixmap.loadFromData(buf.read(), "PNG")
                self._frames.append(pixmap)
                dur = frame.info.get("duration", 100)
                self._durations.append(dur)
        except Exception as e:
            print(f"[桌宠] 加载 GIF 失败: {e}")

    # ---------- 定位 ----------
    def _place_bottom_right(self):
        screen = QApplication.primaryScreen()
        if screen is None:
            screen = self.screen()
        if screen is None:
            self.move(0, 0)
            return
        avail = screen.availableGeometry()
        margin = 16
        self.move(avail.right() - self.width() - margin,
                  avail.bottom() - self.height() - margin)

    # ---------- 帧控制 ----------
    def _show_frame(self, idx: int) -> None:
        if not self._frames:
            return
        self._current_frame = idx % len(self._frames)
        self._label.setPixmap(self._frames[self._current_frame])

    def _advance_frame(self) -> None:
        if not self._frames:
            return
        if not self._playing:
            self._frame_timer.stop()
            return
        next_idx = (self._current_frame + 1) % len(self._frames)
        self._show_frame(next_idx)
        dur = self._durations[next_idx] if self._durations else 100
        dur = max(dur, 10)
        # "每 1 分钟播一轮"模式：回到首帧即代表一轮播完，停下恢复静止
        if self._auto_once and next_idx == 0:
            self._playing = False
            self._auto_once = False
            self._frame_timer.stop()
            return
        self._frame_timer.start(dur)

    def _auto_play_once(self) -> None:
        """定时器回调：非悬停时每 60 秒播一轮完整动画（播完自动停）"""
        if self._playing:
            return
        self._playing = True
        self._auto_once = True
        self._show_frame(0)
        self._frame_timer.start(self._durations[0] if self._durations else 100)

    # ---------- 事件处理 ----------
    def enterEvent(self, event) -> None:
        # 悬停：持续循环播放，取消"播一轮就停"的自动模式
        self._playing = True
        self._auto_once = False
        self._show_frame(0)
        self._frame_timer.start(self._durations[0] if self._durations else 100)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._playing = False
        self._auto_once = False
        self._frame_timer.stop()
        self._show_frame(0)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        """双击桌宠：快速打开 AI 主面板（回调由 ensure_pet 注入）"""
        if event.button() == Qt.MouseButton.LeftButton and callable(self._open_panel_cb):
            self._open_panel_cb()
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        act_stay = QAction(_ui("回到右下角"), self)
        act_stay.triggered.connect(self._place_bottom_right)
        act_quit = QAction(_ui("退出"), self)
        # 仅隐藏本次会话（停定时器），不退出整个程序；重启程序后桌宠重新显示
        act_quit.triggered.connect(self._hide_pet)
        menu.addAction(act_stay)
        menu.addAction(act_quit)
        menu.exec(event.globalPos())

    def _hide_pet(self) -> None:
        """右键「退出」：停止动画并隐藏，仅本次会话生效，不关闭主程序"""
        self._playing = False
        self._auto_once = False
        self._frame_timer.stop()
        self._auto_timer.stop()
        self.hide()

    def closeEvent(self, event) -> None:
        self._auto_timer.stop()
        self._frame_timer.stop()
        self._playing = False
        super().closeEvent(event)


def _create_pet(main_window):
    """建桌宠：**默认 3D**（pet3d：QOpenGLWidget 直渲 + 跟随鼠标互动），
    3D 不可用（缺模块 / 资产缺失 / 初始化抛错）时回退到原来的 GIF 桌宠。

    环境变量 ZHIZHU_PET3D=0 可强制回退 GIF（排障/省 GPU 时用）。
    """
    if not _pet3d_disabled():
        try:
            from zhuzhu_Copilot.ui.pet3d import Pet3DWindow, asset_prefix, available
            if available():
                prefix = asset_prefix()
                if prefix is not None:
                    return Pet3DWindow(prefix, open_panel_cb=main_window._open_agent_panel)
        except Exception as e:
            print(f"[桌宠] 3D 桌宠不可用，回退 GIF：{e}")
    gif = _pet_gif_path()
    if not gif:
        return None
    return DesktopPet(gif, open_panel_cb=main_window._open_agent_panel)


#: 3D 桌宠预加载结果（模块级单例）：
#:   thread  QThread 引用（必须常驻，run() 期间被 GC 会让线程崩溃）
#:   prefix  资产前缀 Path；rig 解析好的 PetRig
#:   done/ok 是否已结束、是否成功
_PRELOAD = {"thread": None, "prefix": None, "rig": None, "done": False, "ok": False}

#: 面板尚未显示时，桌宠建窗的重试间隔与最大次数（100ms × 50 = 最多等 5 秒）
_PET_SHOW_RETRY_MS = 100
_PET_SHOW_RETRY_MAX = 50


def _pet3d_disabled() -> bool:
    """ZHIZHU_PET3D=0 强制关掉 3D 桌宠（排障/省 GPU），走 GIF。"""
    return str(os.environ.get("ZHIZHU_PET3D", "1")).strip().lower() \
        in ("0", "false", "off", "no")


class _Pet3DPreloader(QThread):
    """后台预加载 3D 桌宠：导入 GL 渲染模块（实测 ~1.1s）+ 解析烘焙资产（~0.14s）。

    全过程只产生纯 Python / numpy 对象，不碰 QWidget，跨线程安全；主线程拿到
    结果后才建窗口。结果写入 _PRELOAD，ready 仅作「完成」通知。
    """

    ready = pyqtSignal(bool)

    def run(self) -> None:
        ok = False
        try:
            if _pet3d_disabled():
                raise RuntimeError("ZHIZHU_PET3D=0")
            import numpy  # noqa: F401
            from PyQt6 import QtOpenGL, QtOpenGLWidgets  # noqa: F401
            from zhuzhu_Copilot.ui.pet3d import window as _window
            prefix = _window.asset_prefix()
            if prefix is None:
                raise RuntimeError("缺少 pet3d 资产")
            from zhuzhu_Copilot.ui.pet3d.rig import PetRig
            _PRELOAD["rig"] = PetRig(prefix)
            _PRELOAD["prefix"] = prefix
            ok = True
        except Exception as e:
            print(f"[桌宠] 3D 桌宠预加载失败：{e}")
        finally:
            _PRELOAD["ok"] = ok
            _PRELOAD["done"] = True
            self.ready.emit(ok)


def preload_pet3d() -> None:
    """提前在后台线程预加载 3D 桌宠（幂等）。与启动动画并行，桌宠出现几乎零延迟。"""
    if _PRELOAD["thread"] is not None or _PRELOAD["done"]:
        return
    try:
        loader = _Pet3DPreloader()
    except Exception as e:
        print(f"[桌宠] 预加载线程创建失败：{e}")
        _PRELOAD["done"] = True
        return
    _PRELOAD["thread"] = loader    # 常驻引用：QThread 被 GC 会让 run() 崩溃
    loader.start()


def start_pet_async(main_window, delay_ms: int = 0):
    """启动桌宠：导入 + 资产解析已在后台线程（见 preload_pet3d），
    这里只在主线程建窗口，不再有启动同步阻塞。

    - 预加载已完成：直接建窗口；
    - 仍在进行：等 ready 信号（结果写在 _PRELOAD，信号只作通知）；
    - 3D 不可用 / 建窗失败：回退 GIF 桌宠。
    """
    if getattr(main_window, "_pet", None) is not None:
        return

    def _spawn():
        preload_pet3d()
        loader = _PRELOAD["thread"]
        fired = False

        def _finish(ok: bool) -> None:
            nonlocal fired
            if fired:
                return
            fired = True
            _spawn_pet3d(main_window, ok)

        if loader is not None:
            loader.ready.connect(_finish)
        # 预加载可能在我们连上信号之前就已结束：done 由后台线程写，
        # 连上之后再查一次，保证不漏（fired 防重复建窗）。
        if _PRELOAD["done"]:
            _finish(bool(_PRELOAD["ok"]))

    if delay_ms > 0:
        QTimer.singleShot(delay_ms, _spawn)
    else:
        _spawn()


def _spawn_pet3d(main_window, ok: bool, attempt: int = 0) -> None:
    """主线程建 3D 桌宠窗口（rig 是纯 numpy 对象，跨线程安全）。

    主窗口可能还没显示（桌宠在面板 __init__ 里启动，面板随后才 show），
    此时按 _PET_SHOW_RETRY_MS 重试，最多等 _PET_SHOW_RETRY_MAX 次。
    """
    try:
        if getattr(main_window, "_pet", None) is not None:
            return
        if not main_window.isVisible():
            if attempt < _PET_SHOW_RETRY_MAX:
                QTimer.singleShot(
                    _PET_SHOW_RETRY_MS,
                    lambda: _spawn_pet3d(main_window, ok, attempt + 1))
            return  # 主窗口已关闭/一直未显示，不弹桌宠
        prefix, rig = _PRELOAD["prefix"], _PRELOAD["rig"]
        if not ok or prefix is None or rig is None:
            # 预加载阶段已打印具体原因（缺模块/缺资产/ZHIZHU_PET3D=0），这里直接回退
            _spawn_gif(main_window)
            return
        from zhuzhu_Copilot.ui.pet3d import Pet3DWindow
        pet = Pet3DWindow(prefix, open_panel_cb=main_window._open_agent_panel,
                          rig=rig)
        main_window._pet = pet
        pet.show()
    except Exception as e:
        print(f"[桌宠] 3D 桌宠初始化失败，回退 GIF：{e}")
        _spawn_gif(main_window)


def _spawn_gif(main_window) -> None:
    try:
        gif = _pet_gif_path()
        if not gif:
            return
        pet = DesktopPet(gif, open_panel_cb=main_window._open_agent_panel)
        main_window._pet = pet
        pet.show()
    except Exception:
        main_window._pet = None


def ensure_pet(main_window) -> "DesktopPet | None":
    """随主程序启动桌宠（单例，附着在 main_window 生命周期上）。
    资源缺失时静默返回 None，避免拖累主程序。"""
    if not hasattr(main_window, "_pet") or main_window._pet is None:
        pet = _create_pet(main_window)
        if pet is None:
            return None
        main_window._pet = pet
    pet = main_window._pet
    if not pet.isVisible():
        pet.show()
    return pet


def destroy_pet(main_window) -> None:
    """主程序关闭时显式销毁桌宠"""
    pet = getattr(main_window, "_pet", None)
    if pet is not None:
        try:
            pet.close()
        except Exception:
            pass
        main_window._pet = None
