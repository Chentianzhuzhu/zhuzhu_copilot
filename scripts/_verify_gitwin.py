"""GitLogWindow 回归测试（Qt offscreen）：切换工作目录后面板必须呈现新仓库内容"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PyQt6.QtWidgets import QApplication
app = QApplication([])

from zhuzhu_Copilot.core import agent_git, agent_tools
from zhuzhu_Copilot.ui.agent_panel import GitLogWindow
from PyQt6.QtCore import Qt as _Qt


def _git(d, *args):
    return subprocess.run(["git", "-C", str(d), *args],
                          capture_output=True, text=True, check=True)


def make_repo(base, name):
    d = base / name
    d.mkdir()
    _git(d, "init", "-b", "main", "-q")
    _git(d, "config", "user.email", "t@t.com")
    _git(d, "config", "user.name", "T")
    (d / "f.txt").write_text(name)
    _git(d, "add", ".")
    _git(d, "commit", "-m", f"commit {name}", "-q")
    return d


def pump(win, timeout=15.0):
    """驱动事件循环直到面板完成加载（busy=False 且列表非占位）"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
        if not win._busy and win.list.count() > 0:
            txt = win.list.item(0).data(_Qt.ItemDataRole.UserRole) or ""
            if "加载中" not in txt:
                return txt
    return ""


base = Path(tempfile.mkdtemp(prefix="gitwin_"))
repo_a = make_repo(base, "repo_a")
repo_b = make_repo(base, "repo_b")

try:
    agent_tools.set_workdir(str(repo_a))
    win = GitLogWindow()
    win.resize(280, 320)
    win.refresh()
    first = pump(win)
    assert "commit repo_a" in first, f"初始应为 repo_a: {first!r}"
    print("[OK] 初始面板显示 repo_a")

    # 切到 repo_b：面板必须刷新到新仓库
    agent_tools.set_workdir(str(repo_b))
    win.refresh()
    txt = pump(win, 20)
    assert "commit repo_b" in txt, f"切换后应为 repo_b: {txt!r}"
    print("[OK] 切换工作目录后面板显示 repo_b")

    # 切到非仓库目录：必须报「不是 Git 仓库」，不得回退到应用自身仓库
    plain = base / "plain"
    plain.mkdir()
    agent_tools.set_workdir(str(plain))
    win.refresh()
    txt = pump(win, 20)
    assert "不是 Git 仓库" in txt, f"非仓库目录应提示而不是显示旧仓库: {txt!r}"
    print("[OK] 非仓库目录正确提示:", txt.strip().split(">")[1].split("<")[0] if txt else "")
finally:
    win.close()
    shutil.rmtree(base, ignore_errors=True)

print("回归测试通过")