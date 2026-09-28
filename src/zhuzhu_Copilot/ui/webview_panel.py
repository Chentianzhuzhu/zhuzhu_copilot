# -*- coding: utf-8 -*-
"""WebView 面板：使用 QWebEngineView 渲染网页/本地文件，支持导航、前进后退、刷新等。

功能：
- 地址栏输入 URL 或本地文件路径
- 前进/后退/刷新按钮
- 加载进度显示
- 下载文件前弹窗确认保存位置
- 支持快捷键（Ctrl+L 聚焦地址栏、Ctrl+R 刷新）
"""

import os
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWebEngineCore import QWebEngineDownloadRequest
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QProgressBar, QToolButton, QFrame, QSpacerItem,
    QSizePolicy, QFileDialog
)

from zhuzhu_Copilot.ui.styles import PALETTE, GLOBAL_QSS
from zhuzhu_Copilot.ui.widgets import Card


class WebViewPanel(QWidget):
    """WebView 面板：网页浏览与本地文件预览"""

    url_changed = pyqtSignal(str)  # URL 变更信号
    title_changed = pyqtSignal(str)  # 页面标题变更信号
    load_finished = pyqtSignal(bool)  # 加载完成信号
    load_progress = pyqtSignal(int)  # 加载进度信号

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("webViewPanel")
        self._current_url = ""
        self._build_ui()
        self._connect_signals()
        self._setup_download_prompt()

    def _build_ui(self):
        """构建 WebView 面板 UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 导航工具栏
        nav_bar = self._build_nav_bar()
        layout.addWidget(nav_bar)

        # 地址栏
        addr_bar = self._build_address_bar()
        layout.addWidget(addr_bar)

        # WebView 主体
        self.web_view = QWebEngineView()
        self.web_view.setRenderProcessGoneDetails(False)  # 禁用崩溃检测（提升性能）
        self.web_view.setStyleSheet(f"""
            QWebEngineView {{
                background: {PALETTE['card']};
                border: none;
            }}
        """)
        layout.addWidget(self.web_view, stretch=1)

        # 底部状态栏
        status_bar = self._build_status_bar()
        layout.addWidget(status_bar)

    def _build_nav_bar(self) -> QWidget:
        """构建导航按钮栏（前进/后退/刷新/主页）"""
        bar = QWidget()
        bar.setFixedHeight(36)
        bar.setStyleSheet(f"""
            QWidget {{
                background: {PALETTE['panel']};
                border-bottom: 1px solid {PALETTE['border']};
            }}
        """)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(4)

        # 后退按钮
        self.back_btn = QToolButton()
        self.back_btn.setText("←")
        self.back_btn.setFixedSize(28, 28)
        self.back_btn.setEnabled(False)
        self.back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back_btn.setStyleSheet(self._nav_btn_style())
        layout.addWidget(self.back_btn)

        # 前进按钮
        self.forward_btn = QToolButton()
        self.forward_btn.setText("→")
        self.forward_btn.setFixedSize(28, 28)
        self.forward_btn.setEnabled(False)
        self.forward_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.forward_btn.setStyleSheet(self._nav_btn_style())
        layout.addWidget(self.forward_btn)

        # 刷新按钮
        self.refresh_btn = QToolButton()
        self.refresh_btn.setText("⟳")
        self.refresh_btn.setFixedSize(28, 28)
        self.refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_btn.setStyleSheet(self._nav_btn_style())
        layout.addWidget(self.refresh_btn)

        # 主页按钮
        self.home_btn = QToolButton()
        self.home_btn.setText("⌂")
        self.home_btn.setFixedSize(28, 28)
        self.home_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.home_btn.setStyleSheet(self._nav_btn_style())
        layout.addWidget(self.home_btn)

        layout.addStretch(1)

        # 标签页提示
        self.nav_label = QLabel("WebView")
        self.nav_label.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 11px;")
        layout.addWidget(self.nav_label)

        return bar

    def _build_address_bar(self) -> QWidget:
        """构建地址栏"""
        bar = QWidget()
        bar.setFixedHeight(32)
        bar.setStyleSheet(f"""
            QWidget {{
                background: {PALETTE['bg']};
                border-bottom: 1px solid {PALETTE['border']};
            }}
        """)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(6)

        # URL 输入框
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("输入网址或本地文件路径 (如 file:///C:/path/to/file.html)")
        self.url_edit.returnPressed.connect(self._navigate_to_url)
        self.url_edit.setStyleSheet(f"""
            QLineEdit {{
                background: {PALETTE['panel']};
                color: {PALETTE['text']};
                border: 1px solid {PALETTE['border']};
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 12px;
            }}
            QLineEdit:focus {{
                border: 1px solid {PALETTE['primary']};
            }}
        """)
        layout.addWidget(self.url_edit, stretch=1)

        # 跳转按钮
        go_btn = QPushButton("GO")
        go_btn.setFixedSize(48, 24)
        go_btn.clicked.connect(self._navigate_to_url)
        go_btn.setStyleSheet(f"""
            QPushButton {{
                background: {PALETTE['primary']};
                color: white;
                border: none;
                border-radius: 6px;
                font-size: 11px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: {PALETTE['primary_hover']};
            }}
        """)
        layout.addWidget(go_btn)

        return bar

    def _build_status_bar(self) -> QWidget:
        """构建状态栏（进度条 + 状态文本）"""
        bar = QWidget()
        bar.setFixedHeight(24)
        bar.setStyleSheet(f"""
            QWidget {{
                background: {PALETTE['panel']};
                border-top: 1px solid {PALETTE['border']};
            }}
        """)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(6)

        # 进度条
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # 无限循环表示加载中
        self.progress.setFixedHeight(4)
        self.progress.setTextVisible(False)
        self.progress.setStyleSheet(f"""
            QProgressBar {{
                background: transparent;
                border: none;
            }}
            QProgressBar::chunk {{
                background: {PALETTE['primary']};
                border-radius: 2px;
            }}
        """)
        layout.addWidget(self.progress, stretch=1)

        # 状态文本
        self.status_label = QLabel("就绪")
        self.status_label.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 10px;")
        layout.addWidget(self.status_label)

        return bar

    def _nav_btn_style(self) -> str:
        """导航按钮样式"""
        return f"""
            QToolButton {{
                background: transparent;
                border: none;
                border-radius: 4px;
                color: {PALETTE['text']};
                font-size: 14px;
                font-weight: bold;
            }}
            QToolButton:hover {{
                background: {PALETTE['hover']};
            }}
            QToolButton:disabled {{
                color: {PALETTE['text_disabled']};
            }}
        """

    def _connect_signals(self):
        """连接信号"""
        self.back_btn.clicked.connect(self.web_view.back)
        self.forward_btn.clicked.connect(self.web_view.forward)
        self.refresh_btn.clicked.connect(self._reload)
        self.home_btn.clicked.connect(self._go_home)
        self.url_edit.returnPressed.connect(self._navigate_to_url)
        
        # WebView 信号
        self.web_view.urlChanged.connect(self._on_url_changed)
        self.web_view.titleChanged.connect(self._on_title_changed)
        self.web_view.loadProgress.connect(self._on_load_progress)
        self.web_view.loadFinished.connect(self._on_load_finished)
        self.web_view.loadStarted.connect(self._on_load_started)

        # 快捷键
        self._add_shortcut(QKeySequence("Ctrl+L"), self._focus_url)
        self._add_shortcut(QKeySequence("Ctrl+R"), self._reload)
        self._add_shortcut(QKeySequence("F5"), self._reload)

    def _add_shortcut(self, sequence, callback):
        """添加快捷键"""
        action = QAction(self)
        action.setShortcut(sequence)
        action.triggered.connect(callback)
        self.addAction(action)

    def _navigate_to_url(self):
        """导航到地址栏中的 URL"""
        text = self.url_edit.text().strip()
        if not text:
            return
        
        # 检测是否为本地文件路径
        if os.path.exists(text):
            url = QUrl.fromLocalFile(text)
        elif text.startswith(("http://", "https://", "file://", "about:")):
            url = QUrl(text)
        else:
            # 尝试作为 URL 处理
            if ":" not in text and "/" not in text:
                # 可能是域名，添加 https://
                url = QUrl(f"https://{text}")
            else:
                url = QUrl(text)
        
        self.web_view.setUrl(url)

    def _reload(self):
        """重新加载当前页面"""
        self.web_view.reload()

    def _go_home(self):
        """返回主页（空白页）"""
        self.web_view.setUrl(QUrl("about:blank"))

    def _focus_url(self):
        """聚焦地址栏并选中全部内容"""
        self.url_edit.setFocus()
        self.url_edit.selectAll()

    def _on_url_changed(self, url: QUrl):
        """URL 变更回调"""
        self._current_url = url.toString()
        self.url_edit.setText(self._current_url)
        self.url_changed.emit(self._current_url)
        self.status_label.setText(f"URL: {self._current_url[:60]}{'...' if len(self._current_url) > 60 else ''}")

    def _on_title_changed(self, title: str):
        """标题变更回调"""
        self.title_changed.emit(title)
        if title:
            self.status_label.setText(f"Title: {title[:40]}{'...' if len(title) > 40 else ''}")

    def _on_load_started(self):
        """开始加载"""
        self.progress.show()
        self.status_label.setText("加载中...")

    def _on_load_progress(self, prog: int):
        """加载进度回调"""
        self.load_progress.emit(prog)

    def _on_load_finished(self, ok: bool):
        """加载完成回调"""
        self.progress.hide()
        self.progress.setValue(0)
        self.load_finished.emit(ok)
        if ok:
            self.status_label.setText("加载完成")
        else:
            self.status_label.setText("加载失败")
        
        # 更新导航按钮状态
        self.back_btn.setEnabled(self.web_view.canGoBack())
        self.forward_btn.setEnabled(self.web_view.canGoForward())

    def navigate(self, url: str):
        """导航到指定 URL"""
        if url.startswith(("http://", "https://", "file://", "about:")):
            self.web_view.setUrl(QUrl(url))
        elif os.path.exists(url):
            self.web_view.setUrl(QUrl.fromLocalFile(url))
        else:
            self.web_view.setUrl(QUrl(f"https://{url}"))

    def go_back(self):
        """后退"""
        if self.web_view.canGoBack():
            self.web_view.back()

    def go_forward(self):
        """前进"""
        if self.web_view.canGoForward():
            self.web_view.forward()

    def reload_page(self):
        """刷新页面"""
        self.web_view.reload()

    def stop(self):
        """停止加载"""
        self.web_view.stop()

    def get_current_url(self) -> str:
        """获取当前 URL"""
        return self._current_url

    def get_page_title(self) -> str:
        """获取页面标题"""
        return self.web_view.title()

    def evaluate_js(self, js: str) -> object:
        """执行 JavaScript 并返回结果"""
        result = [None]
        def callback(r):
            import json
            try:
                result[0] = json.loads(r.toString()) if r.toString() else r.toBool() if r.type() == 2 else r.toString()
            except:
                result[0] = r.toString()
        self.web_view.page().runJavaScript(js, callback)
        return result[0]

    def clear(self):
        """清空 WebView 内容"""
        self.web_view.setUrl(QUrl("about:blank"))
        self.url_edit.clear()
        self._current_url = ""

    def _setup_download_prompt(self):
        """拦截 WebView 下载请求：先弹窗确认保存位置，再触发下载。"""
        profile = self.web_view.page().profile()
        profile.downloadRequested.connect(self._on_download_requested)

    def _on_download_requested(self, item: QWebEngineDownloadRequest):
        """下载请求回调：暂停下载 → 弹保存位置对话框 → 确认后恢复/取消。"""
        item.pause()
        suggested = item.downloadFileName() or item.suggestedFileName() or ""
        default_path = str(Path.cwd() / suggested) if suggested else str(Path.home() / "Downloads")
        save_path, _ = QFileDialog.getSaveFileName(
            self, "保存下载文件", default_path)
        if not save_path:
            item.cancel()
            self.status_label.setText("已取消下载")
            return
        target = Path(save_path)
        item.setDownloadDirectory(str(target.parent))
        item.setDownloadFileName(target.name)
        item.resume()
        self.status_label.setText(f"开始下载: {target.name}")
