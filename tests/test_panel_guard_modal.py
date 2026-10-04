# -*- coding: utf-8 -*-
"""「点击浏览选择工作目录时界面卡死」—— 面板守卫必须在模态对话框期间停手。

用户反馈：点击「浏览」选择项目（工作）目录时界面直接卡死。

根因（实测定位）：todos / git / 工作树 / 代码预览都是独立 Tool 顶层窗口，
`_guard_timer` 每 800ms 调一次 `_guard_panels()`，只要本应用处于激活状态就对这些
可见面板 `raise_()`。「浏览」用的原生目录对话框是**模态**的 —— 它打开期间本应用
仍然是激活窗口，于是面板被反复抬到对话框之上，抢走 z 序与交互焦点：
用户看到的现象就是「点了浏览之后对话框还在、但点什么都没反应」（卡死）。

修法：`_guard_panels` 检测到 `QApplication.activeModalWidget()` 非空时整轮跳过。

断言口径（与机器无关的确定性量）：
  1. 无模态窗口时守卫确实会抬升面板（基线，保证用例不是空跑）；
  2. 有模态窗口时一次都不抬升（修复点）。
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest                                        # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog     # noqa: E402

_app = QApplication.instance() or QApplication([])

from zhuzhu_Copilot.ui import main_window as mw       # noqa: E402
from zhuzhu_Copilot.ui.agent_panel import AgentPanel  # noqa: E402


def _pump(ms: int = 8):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        _app.processEvents()


class _FakeFloatWin:
    """替身面板：只关心「是否被 raise_()」，避免依赖真实面板是否已创建。"""

    dock_state = "float"
    _ai_managed = False

    def __init__(self):
        self.raised = 0

    def isVisible(self):
        return True

    def raise_(self):
        self.raised += 1


@pytest.fixture()
def panel(monkeypatch):
    """构造真实面板（与其它 UI 用例同款隔离），并屏蔽重初始化逻辑。"""
    mp = pytest.MonkeyPatch()
    mp.setattr(mw.CopilotPanel, "_start_scan", lambda self, *a: None)
    mp.setattr(mw.CopilotPanel, "_init_update_check", lambda self: None)
    mp.setattr(mw.CopilotPanel, "_check_admin", lambda self: None)
    mp.setattr(mw.CopilotPanel, "_setup_tray", lambda self: None)
    try:
        mp.setattr("zhuzhu_Copilot.ui.desktop_pet.ensure_pet", lambda w: None)
    except Exception:
        pass
    p = AgentPanel(None)
    p.resize(1000, 900)
    p.show()
    _pump(120)
    yield p
    try:
        p.hide()
        _pump(40)
    except Exception:
        pass
    mp.undo()


def test_guard_skips_while_modal_dialog_open(panel, monkeypatch):
    """守卫在模态对话框打开期间必须整轮跳过（否则会把面板插到对话框之上）。"""
    fake = _FakeFloatWin()
    # 让守卫认为「有个 float 面板可见、且本应用处于激活状态」
    monkeypatch.setattr(panel, "todos_win", fake, raising=False)
    monkeypatch.setattr(panel, "_todos_enabled", lambda: True)
    monkeypatch.setattr(panel, "isVisible", lambda: True)
    monkeypatch.setattr(panel, "isActiveWindow", lambda: True)
    monkeypatch.setattr(panel, "_panel_minimized", False, raising=False)

    # --- 基线：无模态窗口 → 守卫确实会抬升面板 ---
    assert _app.activeModalWidget() is None, "用例前置条件：不应有残留模态窗口"
    fake.raised = 0
    panel._guard_panels()
    assert fake.raised > 0, "无模态窗口时守卫应正常抬升面板（基线断言）"

    # --- 修复点：存在模态对话框（等价于「浏览」目录框）→ 一次都不抬升 ---
    dlg = QDialog(panel)
    dlg.setModal(True)
    dlg.show()
    _pump(40)
    try:
        assert _app.activeModalWidget() is not None, "模态对话框应被 activeModalWidget 识别"
        fake.raised = 0
        panel._guard_panels()
        assert fake.raised == 0, \
            "模态对话框打开时守卫不得抬升面板（会抢走对话框焦点，表现为界面卡死）"
    finally:
        dlg.close()
        _pump(20)


def test_thumb_worker_never_creates_gui_resources():
    """工作树的缩略图线程只允许产出 QImage（线程安全），不得构造 QPixmap/QIcon/QPainter。

    这是上一个卡死的同级根因：QPixmap / QPainter / QIcon **只能在 GUI 线程使用**。
    早期实现让子线程直接调 _file_thumb（内部构造 QPixmap/QIcon），会与主线程争用
    GDI；主线程那时若停在原生模态对话框里（选工作目录的「浏览」框）就互相等待 → 卡死。
    因此这里用 AST 把该约束固化成断言，防止以后又被改回子线程构造。
    """
    import ast

    src_path = (Path(__file__).resolve().parents[1] / "src" / "zhuzhu_Copilot"
                / "ui" / "agent_panel.py")
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    worker = None
    for cls in tree.body:
        if isinstance(cls, ast.ClassDef) and cls.name == "WorktreeWindow":
            for n in cls.body:
                if isinstance(n, ast.FunctionDef) and n.name == "_load_thumbs":
                    worker = n
    assert worker is not None, "未找到 WorktreeWindow._load_thumbs"

    used = set()
    for node in ast.walk(worker):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            used.add(node.attr)
    banned = sorted(used & {"QPixmap", "QIcon", "QPainter",
                            "_file_thumb", "_line_icon", "_file_icon"})
    assert not banned, f"缩略图线程不得使用 GUI 专属资源: {banned}"
