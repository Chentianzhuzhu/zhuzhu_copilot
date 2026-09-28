"""桌宠：透明 GIF 常驻桌面右下角（任务栏上方），随主程序启动。
鼠标悬停期间 → 持续循环播放；离开 → 暂停回到首帧。
另外每 60 秒会自动播一次完整循环（唤醒宠物）。

用 PIL 预加载帧，避免 QMovie 加载大 GIF 崩溃。
"""
import os
import sys
from pathlib import Path
from typing import List, Tuple

from PyQt6.QtCore import Qt, QPoint, QSize, QTimer
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
        """用 PIL 加载 GIF 所有帧为 QPixmap 列表"""
        try:
            from PIL import Image, ImageSequence
            import io
            img = Image.open(path)
            # 注意：不能先对整张 GIF 做 convert("RGBA")，否则会丢失多帧迭代能力；
            # 需在原始图像上逐帧迭代，再对每一帧单独转 RGBA。
            for frame in ImageSequence.Iterator(img):
                frame_copy = frame.convert("RGBA")
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
        act_stay = QAction("回到右下角", self)
        act_stay.triggered.connect(self._place_bottom_right)
        act_quit = QAction("退出", self)
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


def ensure_pet(main_window) -> "DesktopPet | None":
    """随主程序启动桌宠（单例，附着在 main_window 生命周期上）。
    资源缺失时静默返回 None，避免拖累主程序。"""
    if not hasattr(main_window, "_pet") or main_window._pet is None:
        gif = _pet_gif_path()
        if not gif:
            return None
        main_window._pet = DesktopPet(gif, open_panel_cb=main_window._open_agent_panel)
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
