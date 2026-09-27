"""GitLogWindow 高度回归验证（按真实布局 visualItemRect 断言）。

背景：QListView 的行高实际取自 _HtmlListDelegate.sizeHint（而非 item 显式 sizeHint），
旧实现按错误宽度测量且内边距 +4 不足，导致最后一行「作者·时间」被裁切约 8-10px。
本测试直接断言视图布局后的真实行高，长提交（换行）行高必须 > 短提交且完整容纳三行文本。
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PyQt6.QtWidgets import QApplication
app = QApplication([])

from winapp_migrator.core import agent_git, agent_tools
from winapp_migrator.ui.agent_panel import GitLogWindow


def _git(d, *args):
    return subprocess.run(["git", "-C", str(d), *args],
                          capture_output=True, text=True, check=True)


d = Path(tempfile.mkdtemp(prefix="git_h_"))
_git(d, "init", "-b", "main", "-q")
_git(d, "config", "user.email", "a@b.c")
_git(d, "config", "user.name", "作者姓名非常非常非常非常非常非常非常非常长")
try:
    (d / "f.txt").write_text("x")
    _git(d, "add", ".")
    _git(d, "commit", "-m", "短提交", "-q")
    (d / "f.txt").write_text("y" * 50)
    _git(d, "add", ".")
    _git(d, "commit", "-m",
         "这条提交说明特别长会超过面板宽度导致自动换行从而把作者时间行挤出固定高度范围之外造成挤压遮挡",
         "-q")

    agent_tools.set_workdir(str(d))
    win = GitLogWindow()
    win.resize(280, 400)
    win.show()
    app.processEvents()
    win._fill_commits(agent_git.list_commits())
    win.list.doItemsLayout()
    app.processEvents()

    h0 = win.list.visualItemRect(win.list.item(0)).height()   # 长提交（最新在上）
    h1 = win.list.visualItemRect(win.list.item(1)).height()   # 短提交
    print(f"长提交行高={h0}px 短提交行高={h1}px")
    # 长提交说明折行 → 行高应明显大于短提交，且都高于三个文本行+内边距的下限
    assert h0 > h1, f"长提交行高 {h0} 应大于短提交 {h1}"
    assert h0 >= 80, f"长提交应容纳折行后全部 4 行文本（实际 {h0}）"
    assert h1 >= 64, f"短提交三行文本+上下内边距至少 64px（实际 {h1}）"
    print("[OK] 真实行高自适应，作者/时间行不再被裁切")
finally:
    win.close()
    shutil.rmtree(d, ignore_errors=True)

print("高度回归验证通过")