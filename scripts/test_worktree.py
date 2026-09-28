"""worktree 文件树逻辑测试（离屏：绑定 MainWindow._wt_* 方法到 shim 对象）"""
import os, sys, tempfile, types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PyQt6.QtWidgets import QApplication, QTreeWidget
from PyQt6.QtCore import Qt

app = QApplication([])

from zhuzhu_Copilot.ui.main_window import MainWindow
from zhuzhu_Copilot.core import agent_tools


def _collect(item):
    out = {item.data(0, Qt.ItemDataRole.UserRole): item.text(0)}
    for i in range(item.childCount()):
        out.update(_collect(item.child(i)))
    return out


def _fake():
    fake = types.SimpleNamespace()
    fake.wt_tree = QTreeWidget()
    fake.wt_path = types.SimpleNamespace()
    fake.wt_path.setText = lambda t: setattr(fake, "_path", t)
    fake._wt_root = types.MethodType(MainWindow._wt_root, fake)
    fake._wt_fill = types.MethodType(MainWindow._wt_fill, fake)
    fake._wt_refresh = types.MethodType(MainWindow._wt_refresh, fake)
    return fake


def test_fill():
    d = Path(tempfile.mkdtemp())
    (d / "a.txt").write_text("a")
    (d / "sub").mkdir()
    (d / "sub" / "b.py").write_text("b")
    (d / "sub" / "nested").mkdir()
    (d / "sub" / "nested" / "c.json").write_text("{}")
    agent_tools.set_workdir(str(d))

    fake = _fake()
    fake._wt_refresh()

    root = fake.wt_tree.topLevelItem(0)
    assert root is not None
    paths = _collect(root)
    assert str(d / "a.txt") in paths
    assert str(d / "sub" / "b.py") in paths
    assert str(d / "sub" / "nested" / "c.json") in paths
    assert getattr(fake, "_path").replace("\\", "/") == str(d).replace("\\", "/")
    print(f"[OK] test_fill: {len(paths)} 节点")


def test_empty():
    agent_tools.set_workdir("")
    fake = _fake()
    fake._wt_refresh()
    assert fake.wt_tree.topLevelItem(0) is None
    print(f"[OK] test_empty: {getattr(fake, '_path')}")


if __name__ == "__main__":
    test_fill()
    test_empty()
    print("\n全部通过")