"""高速下载窗口：多任务管理（独立对话框）

- 输入 URL 添加任务，支持同时多个任务并行
- 每个任务显示文件名 / 进度 / 实时速度 / 状态，可单独取消
- 保存目录弹窗选择并记忆上次目录
"""

import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt, QSize, QTimer, QSettings
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QProgressBar, QListWidget, QListWidgetItem, QMessageBox,
    QFileDialog, QComboBox,
)

from winapp_migrator.core.fast_download import DownloadTask
from winapp_migrator.ui.styles import PALETTE
from winapp_migrator.ui.widgets import add_brand_footer

_DONE_STATES = ("done", "error", "canceled")


def _app_icon_path() -> str:
    """应用图标路径（打包后取 _MEIPASS/assets，开发模式取项目 assets）"""
    if getattr(sys, "frozen", False):
        return os.path.join(getattr(sys, "_MEIPASS", "."), "assets", "icon.ico")
    return str(Path(__file__).resolve().parents[3] / "assets" / "icon.ico")


class _TaskRow(QWidget):
    """单个下载任务行：文件名 + 进度条 + 速度/状态 + 取消"""

    def __init__(self, task: DownloadTask, on_pause, on_cancel, parent=None):
        super().__init__(parent)
        self.task = task
        self._on_pause = on_pause
        self._on_cancel = on_cancel

        self.name_label = QLabel("准备中…")
        self.name_label.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {PALETTE['text']};")
        self.name_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.pause_btn = QPushButton("暂停")
        self.pause_btn.setFixedSize(56, 26)
        self.pause_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pause_btn.setStyleSheet(
            f"background: transparent; color: {PALETTE['primary']}; font-size: 12px; "
            "border: 1px solid #93C5FD; border-radius: 6px;"
        )
        self.pause_btn.clicked.connect(self._click_pause)

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setFixedSize(56, 26)
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.setStyleSheet(
            f"background: transparent; color: {PALETTE['danger']}; font-size: 12px; "
            "border: 1px solid #FCA5A5; border-radius: 6px;"
        )
        self.cancel_btn.clicked.connect(self._click_cancel)

        self.progress = QProgressBar()
        self.progress.setFixedHeight(10)
        self.progress.setTextVisible(False)

        self.info_label = QLabel("")
        self.info_label.setStyleSheet(f"font-size: 12px; color: {PALETTE['text_secondary']};")

        top = QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(self.name_label, 1)
        top.addWidget(self.pause_btn)
        top.addWidget(self.cancel_btn)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(4)
        lay.addLayout(top)
        lay.addWidget(self.progress)
        lay.addWidget(self.info_label)

    def _click_pause(self, *_):
        """点击暂停/继续：立即给出反馈，等待线程状态同步"""
        if self.task.snapshot()["status"] == "paused":
            self._on_pause(self.task)  # 实际是 resume
            self.pause_btn.setEnabled(False)
            self.pause_btn.setText("继续中")
            return
        self._on_pause(self.task)
        self.pause_btn.setEnabled(False)
        self.pause_btn.setText("暂停中")

    def _click_cancel(self, *_):
        """点击取消：立即反馈，等待线程退出"""
        self._on_cancel(self.task)
        self.pause_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self.info_label.setText("⏹ 正在取消…")

    def update_view(self, snap: dict, speed: float):
        """按任务快照刷新显示"""
        name = snap.get("display_name") or snap["filename"] \
            or os.path.basename(snap["path"]) \
            or getattr(self.task, "url", "").split("/")[-1]
        self.name_label.setText(name)
        status = snap["status"]
        total, done = snap["total"], snap["done"]
        pct = int(done * 100 / total) if total > 0 else 0
        self.progress.setRange(0, 100)
        self.progress.setValue(pct)

        if status == "downloading":
            self.pause_btn.setVisible(True)
            self.pause_btn.setEnabled(True)
            self.pause_btn.setText("暂停")
            segs = int(snap.get("segments") or 16)
            mode_txt = f"{segs} 线程并发" if snap.get("mode") == "multi" else "单线程"
            info = f"⏳ 下载中 · {_fmt_size(done)} / {_fmt_size(total)} · {mode_txt}"
            if speed > 0:
                info += f" · {_fmt_size(int(speed))}/s"
            self.cancel_btn.setVisible(True)
            self.cancel_btn.setEnabled(True)
            self.cancel_btn.setText("取消")
        elif status == "paused":
            self.pause_btn.setVisible(True)
            self.pause_btn.setEnabled(True)
            self.pause_btn.setText("继续")
            self.cancel_btn.setVisible(True)
            self.cancel_btn.setEnabled(True)
            self.cancel_btn.setText("取消")
            info = f"⏸ 已暂停 · {_fmt_size(done)} / {_fmt_size(total)}（分片已保留，可随时继续）"
        elif status == "done":
            self.pause_btn.setVisible(False)
            self.cancel_btn.setVisible(False)
            info = f"✅ 已完成 · {_fmt_size(total)} → {snap['path']}"
        elif status == "error":
            self.pause_btn.setVisible(False)
            self.cancel_btn.setVisible(False)
            info = f"❌ 失败：{snap['error']}"
        elif status == "canceled":
            self.pause_btn.setVisible(False)
            self.cancel_btn.setVisible(False)
            info = "⏹ 已取消"
        else:  # pending
            self.pause_btn.setVisible(True)
            self.pause_btn.setEnabled(True)
            self.pause_btn.setText("暂停")
            self.cancel_btn.setVisible(True)
            self.cancel_btn.setEnabled(True)
            self.cancel_btn.setText("取消")
            info = "⏳ 正在连接服务器…"
        self.info_label.setText(info)
        self.info_label.setToolTip(info)


def _fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"


class DownloadDialog(QDialog):
    """高速下载窗口"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🚀 高速下载")
        self.setWindowIcon(QIcon(_app_icon_path()))
        self.setMinimumSize(1048, 492)
        self.resize(1048, 492)

        self._settings = QSettings("WinAppMigrator", "WinAppMigrator")
        self._tasks: List[DownloadTask] = []
        self._rows: Dict[int, _TaskRow] = {}
        self._prev_done: Dict[int, float] = {}
        self._smooth_speed: Dict[int, float] = {}
        self._last_poll_at = time.time()

        self._build_ui()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(250)

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)

        title = QLabel("🚀 高速下载（多线程分段加速，比浏览器快 2-3 倍）")
        title.setStyleSheet(f"font-size: 15px; font-weight: 700; color: {PALETTE['text']};")
        lay.addWidget(title)

        input_row = QHBoxLayout()
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("粘贴文件下载地址（http/https），如 https://example.com/file.zip")
        self.url_edit.setMinimumHeight(36)
        self.url_edit.returnPressed.connect(self._add_task)
        input_row.addWidget(self.url_edit, 1)
        self.add_btn = QPushButton("＋ 添加任务")
        self.add_btn.setMinimumHeight(36)
        self.add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_btn.setStyleSheet(
            f"background-color: {PALETTE['primary']}; color: white; font-weight: 700; "
            "border: none; border-radius: 8px; padding: 0 16px;"
        )
        self.add_btn.clicked.connect(self._add_task)
        input_row.addWidget(self.add_btn)
        lay.addLayout(input_row)

        opt_row = QHBoxLayout()
        opt_row.setSpacing(8)
        opt_label = QLabel("速度档位")
        opt_label.setStyleSheet(f"font-size: 13px; color: {PALETTE['text_secondary']};")
        opt_row.addWidget(opt_label)
        self.seg_combo = QComboBox()
        self.seg_combo.addItem("⚡ 极限 · 32 连接（榨干带宽）", 32)
        self.seg_combo.addItem("🚀 高速 · 16 连接", 16)
        self.seg_combo.addItem("标准 · 8 连接", 8)
        saved = self._settings.value("download_segments", 32, type=int)
        idx = self.seg_combo.findData(saved)
        self.seg_combo.setCurrentIndex(idx if idx >= 0 else 0)
        opt_row.addWidget(self.seg_combo)
        opt_row.addStretch(1)
        lay.addLayout(opt_row)

        self.task_list = QListWidget()
        self.task_list.setStyleSheet(
            "QListWidget { background: transparent; border: none; }"
            f"QListWidget::item {{ border-bottom: 1px solid {PALETTE['border']}; padding: 6px 2px; }}"
        )
        lay.addWidget(self.task_list, 1)

        hint = QLabel("提示：支持 http/https 直链；多线程并发下载（动态分段 + 连接池），"
                      "服务器不支持 Range 时自动单线程")
        hint.setStyleSheet(f"font-size: 12px; color: {PALETTE['text_secondary']};")
        lay.addWidget(hint)
        add_brand_footer(self)

    # ---------- 任务管理 ----------
    def _pick_dir(self) -> Optional[str]:
        """弹窗选择保存目录并记忆"""
        last = self._settings.value("download_dir", str(Path.home() / "Downloads"))
        if not os.path.isdir(str(last)):
            last = str(Path.home())
        dest = QFileDialog.getExistingDirectory(self, "选择下载保存目录", str(last))
        if dest:
            self._settings.setValue("download_dir", dest)
        return dest or None

    def _add_task(self, *_):
        url = "".join(self.url_edit.text().split())  # 清理所有空白/换行
        if not url.lower().startswith(("http://", "https://")):
            QMessageBox.warning(self, "提示", "请输入以 http:// 或 https:// 开头的下载地址")
            return
        dest = self._pick_dir()
        if not dest:
            return

        segments = int(self.seg_combo.currentData())
        self._settings.setValue("download_segments", segments)
        task = DownloadTask(url, dest, segments=segments)
        task.start()
        self._tasks.append(task)

        item = QListWidgetItem(self.task_list)
        row = _TaskRow(task, self._pause_task, self._cancel_task)
        item.setSizeHint(QSize(0, 92))  # 固定行高，避免行内控件重叠
        self.task_list.addItem(item)
        self.task_list.setItemWidget(item, row)
        self._rows[id(task)] = row
        self._prev_done[id(task)] = 0.0

        self.url_edit.clear()
        self.url_edit.setFocus()

    def _pause_task(self, task: DownloadTask):
        """暂停/继续：按任务当前状态自动选择"""
        task.pause() if task.snapshot()["status"] == "downloading" else task.resume()

    def _cancel_task(self, task: DownloadTask):
        task.cancel()

    # ---------- 轮询刷新 ----------
    def _poll(self):
        now = time.time()
        dt = max(now - self._last_poll_at, 0.25)
        for task in list(self._tasks):
            snap = task.snapshot()
            row = self._rows.get(id(task))
            if not row:
                continue
            prev = self._prev_done.get(id(task), 0.0)
            inst = (snap["done"] - prev) / dt if snap["status"] == "downloading" else 0.0
            # 指数平滑速度显示，避免读数忽高忽低
            last = self._smooth_speed.get(id(task), inst)
            speed = inst * 0.35 + last * 0.65 if snap["status"] == "downloading" else 0.0
            self._smooth_speed[id(task)] = speed
            self._prev_done[id(task)] = float(snap["done"])
            row.update_view(snap, speed)
        self._last_poll_at = now

    def closeEvent(self, event):
        active = [t for t in self._tasks
                  if t.snapshot()["status"] in ("downloading", "paused")]
        if active:
            ret = QMessageBox.question(
                self, "下载未完成",
                f"还有 {len(active)} 个任务未完成（下载中/已暂停），关闭将取消这些任务并删除分片。确定关闭？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ret != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            for t in active:
                t.cancel()
            for t in active:
                t.join(3)
        event.accept()
