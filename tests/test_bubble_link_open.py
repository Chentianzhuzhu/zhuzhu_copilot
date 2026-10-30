# -*- coding: utf-8 -*-
"""回合内链接点击回归：URL / 文件路径必须真的被打开。

背景（用户反馈）：点 Agent 生成的 URL / 文件地址直接报错
`AttributeError: 'function' object has no attribute 'unescape'`。
根因：`agent_panel.py` 里的 `_html` 是 i18n 文案函数（`from core.i18n import html`），
标准库 html 在该文件的别名是 `_htmlmod`；`_on_bubble_link` 误用了 `_html.unescape`。

契约：
  1. `&amp;` 等 HTML 实体必须被解码后打开（http/https 走浏览器）；
  2. `file:///C:/...` 必须还原成 Windows 路径并用系统默认程序打开；
  3. 整个过程不得抛异常（异常只会被兜底吞掉，用户看到的是"点了没反应"）。
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtWidgets import QApplication                          # noqa: E402

_app = QApplication.instance() or QApplication([])

from zhuzhu_Copilot.ui import agent_panel as ap                    # noqa: E402


def _panel():
    """只带链接点击所需状态的最小面板（不建窗口）。"""
    p = ap.AgentPanel.__new__(ap.AgentPanel)
    p._bubble_segs = {}
    return p


def _click(p, url):
    # frame 显式传入，避免依赖 QObject.sender()（非槽函数直接调用时为 None）
    p._on_bubble_link(url, frame=object())


def test_http_link_unescaped_and_opened(monkeypatch):
    """HTML 实体先解码再交给浏览器；不再因 `_html` 是函数而抛 AttributeError。"""
    opened = []
    monkeypatch.setattr(ap.webbrowser, "open", lambda u: opened.append(u))
    _click(_panel(), "https://example.com/a?x=1&amp;y=2")
    assert opened == ["https://example.com/a?x=1&y=2"]


def test_file_link_opens_windows_path(monkeypatch):
    """file:///C:/... → C:\\...，用系统默认程序打开（Agent 产出的文件地址）。"""
    opened = []
    monkeypatch.setattr(ap.os, "startfile", lambda p: opened.append(p))
    _click(_panel(), "file:///C:/Temp/report.docx")
    assert opened == [os.path.normpath("C:/Temp/report.docx")]


def test_bare_absolute_path_opens(monkeypatch):
    """裸绝对路径（未经 file:/// 前缀）同样按系统默认程序打开。"""
    opened = []
    monkeypatch.setattr(ap.os, "startfile", lambda p: opened.append(p))
    _click(_panel(), r"C:\Temp\demo.txt")
    assert opened == [r"C:\Temp\demo.txt"]
