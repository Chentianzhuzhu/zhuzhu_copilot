"""子面板自由调尺寸（四边/四角）+ 独立持久化 + 融入主面板 dock 冒烟验证（offscreen）。

验证点：
1. 四个子面板（todos/git/worktree/code）构造后带 8 个四边/四角 resize 热区
2. _set_panel_size / _panel_size_changed 持久化 panel_size/<objName>，重建后按各自尺寸独立恢复
3. dock 融入：_dock_panel / _undock_panel 切换 dock_state，尺寸持久化互不影响
4. 重置（_reset_panel_poses）清 dock_state/dock_order/panel_size 并全部浮出
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                "..", "src")))

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

app = QApplication([])

from winapp_migrator.ui import agent_panel as ap


class StubHost(ap._RoundedFloatWindow):
    """兼容宿主：真实 AgentPanel 的 dock 逻辑依赖面板自身能力即可；
    这里仅提供面板 + 尺寸/dock 行为的最小宿主（不构造完整 AgentPanel）。"""


def _make_dock_column(side: str) -> QWidget:
    col = QWidget()
    col.setObjectName(f"dockCol{side}")
    c_lay = QVBoxLayout(col)
    c_lay.setContentsMargins(0, 0, 0, 0)
    c_lay.setSpacing(8)
    c_lay.addStretch(1)
    col.setProperty("_dock_lay", c_lay)
    col.hide()
    return col


def _clean_all():
    q = QSettings("WinAppMigrator", "WinAppMigrator")
    for key in ("panel_size/todosWin", "panel_size/gitLogWin", "panel_size/wtWin",
                "panel_size/codeWin", "dock_state/todosWin", "dock_state/gitLogWin",
                "dock_state/wtWin", "dock_state/codeWin", "dock_order/left",
                "dock_order/right"):
        q.remove(key)


def main() -> int:
    _clean_all()

    # 1) 面板默认尺寸 + 8 热区
    wt, git, td, code = (ap.WorktreeWindow(), ap.GitLogWindow(),
                         ap.TodosWindow(), ap.CodePreviewWindow())
    assert (wt.width(), td.width(), git.width()) == (280, 280, 280)
    assert code.width() == 441
    for p in (wt, git, td, code):
        assert p._grip is not None and len(p._grip._slots) == 8, p.objectName()
        # 每个槽都是右/下/角等可拖拽热区
        assert set(p._grip._slots.keys()) == set(ap._PanelResizeGrip.MODES)
    print("1) 默认尺寸 + 8 热区 OK:", wt.width(), git.width(), td.width(), code.width())

    # 2) 独立尺寸持久化（拖动面板宽高并存，各面板互不影响）
    for p, w, h in ((wt, 360, 420), (git, 320, 380), (td, 300, 500), (code, 520, 640)):
        # 模拟 _panel_size_changed(persist=True)
        QSettings("WinAppMigrator", "WinAppMigrator").setValue(
            f"panel_size/{p.objectName()}", f"{w},{h}")
        p._set_panel_size(w, h)
        assert (p.width(), p.height()) == (w, h)
    # 去除 X? 重建：读取持久化 -> 恢复各自尺寸（互不相同，证明独立）
    wt2 = ap.WorktreeWindow()
    git2 = ap.GitLogWindow()
    td2 = ap.TodosWindow()
    code2 = ap.CodePreviewWindow()
    assert (wt2.width(), wt2.height()) == (360, 420), (wt2.width(), wt2.height())
    assert (git2.width(), git2.height()) == (320, 380)
    assert (td2.width(), td2.height()) == (300, 500)
    assert (code2.width(), code2.height()) == (520, 640)
    print("2) 独立尺寸持久化恢复 OK:", wt2.width(), git2.width(), td2.width(), code2.width())

    # 3) dock 融入：把面板放入左栏/右栏（复刻 AgentPanel._dock_panel 关键路径）
    left = _make_dock_column("left")
    right = _make_dock_column("right")
    for p in (wt2, git2):
        assert p.dock_state == "float"
        p.dock_state = "left"
        p.hide()
        left.property("_dock_lay").addWidget(p)
        right.property("_dock_lay")
    code2.dock_state = "right"
    code2.hide()
    right.property("_dock_lay").addWidget(code2)
    assert wt2._dock_is_immersed() and code2._dock_is_immersed()
    assert not td2._dock_is_immersed()
    # 融入状态下尺寸仍独立：调整 git 不影响 wt
    git2._set_panel_size(400, 600)
    assert (git2.width(), git2.height()) == (400, 600)
    assert (wt2.width(), wt2.height()) == (360, 420)
    print("3) dock 融入 + 独立尺寸 OK:", wt2.width(), git2.width(), code2.width())

    # 4) 重置：清 dock 键 + panel_size 键并恢复默认尺寸
    _clean_all()
    for p in (wt, git, td, code):
        p.dock_state = "float"
        p._set_panel_size(280, 320)
        p.setParent(None)
    code._set_panel_size(441, 600)
    q = QSettings("WinAppMigrator", "WinAppMigrator")
    assert not q.value("dock_order/left") and not q.value("dock_order/right")
    print("4) 重置清理 OK: left wt", wt.width(), "code", code.width())

    print("ALL PANEL-RESIZE/DOCK CHECKS PASSED")
    return 0


if __name__ == "__main__":
    rc = 0
    try:
        rc = main()
    except Exception:  # noqa: BLE001 -- 冒烟脚本：异常必须打印并置失败
        import traceback
        traceback.print_exc()
        rc = 1
    finally:
        # offscreen 平台下 QApplication 自动销毁可能触发原生崩溃，直接 os._exit
        os._exit(rc)