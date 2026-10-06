"""用户反馈对话框：收集问题/建议并提交到服务器（模态，后台线程提交不阻塞 UI）。

- 反馈内容必填（5-2000 字），实时字数计数（接近上限变橙、超限变红并禁止提交）
- 联系方式选填；提交中按钮 loading，按服务器结果分别提示成功 / 限流 / 参数错误 / 网络错误
- 客户端本地每日提交计数（QSettings）：达到 3 次/天即禁用提交按钮，服务器 429 时同步锁定
"""
import sys
from datetime import date
from pathlib import Path

from zhuzhu_Copilot.core.i18n import ui as _ui, uif as _uif
from zhuzhu_Copilot.core.feedback_client import FeedbackClient, MIN_LEN, MAX_LEN
from zhuzhu_Copilot import app_identity

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QMessageBox,
)
from PyQt6.QtWidgets import QApplication

from zhuzhu_Copilot.ui.styles import PALETTE
from zhuzhu_Copilot.ui.widgets import add_brand_footer

# 接近上限的阈值（字数进入此区间时计数变橙，提醒用户即将达上限）
_WARN_NEAR_LIMIT = 1900
# 每日反馈提交上限（与服务器 DAILY_LIMIT 保持一致）
DAILY_LIMIT = 3


def _app_icon_path() -> str:
    """应用图标路径（打包后取 _MEIPASS/assets，开发模式取项目 assets）——与 download_dialog 同一逻辑"""
    if getattr(sys, "frozen", False):
        import os
        return os.path.join(getattr(sys, "_MEIPASS", "."), "assets", "icon.ico")
    return str(Path(__file__).resolve().parents[3] / "assets" / "icon.ico")


class FeedbackDialog(QDialog):
    """用户反馈对话框（模态）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_ui("用户反馈"))
        self.setWindowIcon(QIcon(_app_icon_path()))
        self.setMinimumWidth(480)
        self.setModal(True)
        self._client: FeedbackClient | None = None   # 提交中保持引用，避免线程未结束即被回收
        self._submitting = False                     # 请求进行中：拦截重复提交并锁定按钮
        self._local_locked = False                   # 本地/服务器确认今日已达上限：永久禁用提交
        self._build_ui()
        self._apply_local_rate_limit()   # 检查本地每日计数，可能直接禁用按钮
        self._on_content_changed()       # 初始化按钮/计数状态

    # ---------- 本地每日提交计数（QSettings 持久化，防无限提交） ----------
    def _today_key(self) -> str:
        return date.today().isoformat()

    def _local_count(self) -> int:
        q = app_identity.qsettings()
        if q.value("feedback/date", "") != self._today_key():
            return 0
        return int(q.value("feedback/count", 0) or 0)

    def _increment_local_count(self):
        q = app_identity.qsettings()
        today = self._today_key()
        if q.value("feedback/date", "") != today:
            q.setValue("feedback/date", today)
            q.setValue("feedback/count", 1)
        else:
            q.setValue("feedback/count", int(q.value("feedback/count", 0) or 0) + 1)
        q.sync()

    def _lock_local(self):
        """服务器返回 429 或本地计数达上限：标记今日已用尽，永久禁用本次会话的提交按钮"""
        q = app_identity.qsettings()
        q.setValue("feedback/date", self._today_key())
        q.setValue("feedback/count", DAILY_LIMIT)
        q.sync()
        self._local_locked = True

    def _apply_local_rate_limit(self):
        """对话框打开时检查本地计数：达上限则禁用按钮并显示提示"""
        if self._local_count() >= DAILY_LIMIT:
            self._local_locked = True
            self.submit_btn.setText(_ui("今日已达上限"))
            self.submit_btn.setEnabled(False)
            self.submit_btn.setToolTip(_ui("今日反馈次数已达上限（3次/天），明天再来吧～"))

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(10)

        intro = QLabel(_ui("告诉我们你的想法和建议，每一条反馈都会被认真阅读。"))
        intro.setWordWrap(True)
        intro.setStyleSheet(f"font-size: 12px; color: {PALETTE['text_secondary']};")
        lay.addWidget(intro)

        self.content_edit = QPlainTextEdit()
        self.content_edit.setPlaceholderText(_ui("请描述你遇到的问题或建议（5-2000字）..."))
        self.content_edit.setMinimumHeight(120)
        self.content_edit.setStyleSheet(
            f"QPlainTextEdit {{ background-color: {PALETTE['card']}; color: {PALETTE['text']};"
            f" border: 1px solid {PALETTE['border']}; border-radius: 10px; padding: 10px;"
            f" selection-background-color: {PALETTE['primary']}; selection-color: #FFFFFF; }}"
            f"QPlainTextEdit:focus {{ border: 1px solid {PALETTE['primary_hover']}; }}"
        )
        self.content_edit.textChanged.connect(self._on_content_changed)
        lay.addWidget(self.content_edit)

        # 字数计数行：右对齐，实时更新
        count_row = QHBoxLayout()
        count_row.addStretch(1)
        self.count_label = QLabel("0 / 2000")
        self.count_label.setStyleSheet(f"font-size: 12px; color: {PALETTE['text_secondary']};")
        count_row.addWidget(self.count_label)
        lay.addLayout(count_row)

        self.contact_edit = QLineEdit()
        self.contact_edit.setPlaceholderText(_ui("邮箱 / QQ / 微信（选填，方便我们回复你）"))
        self.contact_edit.setMinimumHeight(34)
        self.contact_edit.setStyleSheet(
            f"QLineEdit {{ background-color: {PALETTE['card']}; color: {PALETTE['text']};"
            f" border: 1px solid {PALETTE['border']}; border-radius: 8px; padding: 0 10px;"
            f" selection-background-color: {PALETTE['primary']}; selection-color: #FFFFFF; }}"
            f"QLineEdit:focus {{ border: 1px solid {PALETTE['primary_hover']}; }}"
        )
        lay.addWidget(self.contact_edit)

        # 按钮区：提交（主题色，内容不足 5 字时禁用）+ 取消
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.submit_btn = QPushButton(_ui("提交"))
        self.submit_btn.setMinimumHeight(36)
        self.submit_btn.setMinimumWidth(110)
        self.submit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._apply_submit_qss()
        self.submit_btn.clicked.connect(self._on_submit)
        cancel_btn = QPushButton(_ui("取消"))
        cancel_btn.setMinimumHeight(36)
        cancel_btn.setMinimumWidth(90)
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet(
            f"QPushButton {{ background: {PALETTE['card']}; color: {PALETTE['text']};"
            f" border: 1px solid {PALETTE['border']}; border-radius: 8px; font-size: 13px; }}"
            f"QPushButton:hover {{ border-color: {PALETTE['primary_hover']}; background: {PALETTE['hover']}; }}"
        )
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self.submit_btn)
        btn_row.addWidget(cancel_btn)
        lay.addLayout(btn_row)

        add_brand_footer(self)

    def _apply_submit_qss(self):
        """提交按钮样式：主题色主按钮，含禁用态（内联样式会覆盖全局 QSS，禁用态需自带）"""
        self.submit_btn.setStyleSheet(
            f"QPushButton {{ background-color: {PALETTE['primary']}; color: white;"
            f" font-weight: 700; border: none; border-radius: 8px; font-size: 13px; }}"
            f"QPushButton:hover {{ background-color: {PALETTE['primary_hover']}; }}"
            f"QPushButton:disabled {{ background: {PALETTE['border']}; color: {PALETTE['text_secondary']}; }}"
        )

    # ---------- 内容长度 / 提交按钮状态 ----------
    def _on_content_changed(self):
        """字数变化：更新计数颜色，按 5-2000 字开关提交按钮"""
        n = len(self.content_edit.toPlainText())
        self.count_label.setText(f"{n} / {MAX_LEN}")
        if n > MAX_LEN:
            self.count_label.setStyleSheet(
                f"font-size: 12px; color: {PALETTE['danger']}; font-weight: 700;")
        elif n >= _WARN_NEAR_LIMIT:
            self.count_label.setStyleSheet(f"font-size: 12px; color: {PALETTE['warning']};")
        else:
            self.count_label.setStyleSheet(f"font-size: 12px; color: {PALETTE['text_secondary']};")
        self.submit_btn.setEnabled(MIN_LEN <= n <= MAX_LEN and not self._submitting and not self._local_locked)

    # ---------- 提交 ----------
    def _on_submit(self, *_):
        content = self.content_edit.toPlainText().strip()
        if self._local_locked or self._submitting or not (MIN_LEN <= len(content) <= MAX_LEN):
            return   # 按钮状态已拦截，双保险
        self._submitting = True
        self.submit_btn.setText(_ui("提交中..."))
        self.submit_btn.setEnabled(False)
        self._client = FeedbackClient(self)
        self._client.feedback_result.connect(self._on_result)
        self._client.submit(content, self.contact_edit.text())

    def _on_result(self, result: dict):
        """提交结果回调（信号经 Qt 队列切回 UI 线程）"""
        self._submitting = False
        status = result.get("status")
        if status == "ok":
            self._increment_local_count()   # 本地计数 +1
            info = result.get("info") or {}
            token = info.get("token") or ""
            # 显示查询凭证，支持一键复制（凭证仅显示一次，丢失无法找回）
            box = QMessageBox(self)
            box.setWindowTitle(_ui("用户反馈"))
            box.setIcon(QMessageBox.Icon.Information)
            if token:
                box.setText(_ui("反馈已提交！感谢你的建议。\n\n请复制下方查询凭证，用于在官网查询官方回复：\n") + token)
                copy_btn = box.addButton(_ui("复制凭证"), QMessageBox.ButtonRole.AcceptRole)
                close_btn = box.addButton(_ui("关闭"), QMessageBox.ButtonRole.RejectRole)
                box.exec()
                if box.clickedButton() is copy_btn:
                    QApplication.clipboard().setText(token)
            else:
                box.setText(_ui("反馈已提交！感谢你的建议。"))
                box.addButton(_ui("确定"), QMessageBox.ButtonRole.AcceptRole)
                box.exec()
            self.accept()   # 成功即关闭对话框
            return
        # 失败：按类型处理
        if status == "rate_limited":
            # 服务器确认今日超限：本地锁定，永久禁用本次会话提交按钮
            self._lock_local()
            self.submit_btn.setText(_ui("今日已达上限"))
            self.submit_btn.setEnabled(False)
            self.submit_btn.setToolTip(_ui("今日反馈次数已达上限（3次/天），明天再来吧～"))
            QMessageBox.warning(
                self, _ui("用户反馈"),
                _ui("今日反馈次数已达上限（3次/天）\n明天再来吧～"))
        elif status == "network_error":
            # 网络错误：恢复按钮允许重试
            self.submit_btn.setText(_ui("提交"))
            self.submit_btn.setEnabled(not self._local_locked)
            QMessageBox.warning(
                self, _ui("用户反馈"),
                _ui("网络连接失败，请检查网络后重试"))
        else:
            # 参数错误/服务器错误：恢复按钮允许重试
            self.submit_btn.setText(_ui("提交"))
            self.submit_btn.setEnabled(not self._local_locked)
            QMessageBox.warning(
                self, _ui("用户反馈"),
                _uif("提交失败：{a0}", a0=result.get("error") or _ui("未知错误")))
