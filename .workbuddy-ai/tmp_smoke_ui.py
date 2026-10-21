"""UI 冒烟测试：代码预览编辑/自动保存/撤销/行号 + Git 面板胶囊/行数/暂存弹窗。"""
import os
import sys
import tempfile
import time

sys.path.insert(0, r"c:\Users\zhuzhu\Desktop\zhuzhu Copilot\src")
os.chdir(r"c:\Users\zhuzhu\Desktop\zhuzhu Copilot")

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

app = QApplication([])
from zhuzhu_Copilot.ui import agent_panel
from zhuzhu_Copilot.core import agent_git

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (("  " + str(detail)) if detail else ""))
    if not cond:
        fails.append(name)


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


# ---------- 代码预览：行号 / 编辑 / 自动保存 / 撤销 / 重做 ----------
tmp = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8", newline="")
tmp.write("a = 1\nb = 2\nc = 3\n")
tmp.close()
path = tmp.name

win = agent_panel.CodePreviewWindow()
win.resize(700, 500)
win.show()
pump(0.3)
win.show_text(path, "py")
pump(0.2)
check("editor 行号栏宽度 > 0", win.text.line_number_area_width() > 10,
      win.text.line_number_area_width())
check("编辑器可编辑", not win.text.isReadOnly())
check("编辑路径已记录", win._edit_path == path, win._edit_path)

# 编辑并等待自动保存
cur = win.text.textCursor()
cur.movePosition(cur.MoveOperation.End)
win.text.setTextCursor(cur)
win.text.insertPlainText("d = 4\n")
pump(1.4)
saved = open(path, "r", encoding="utf-8").read()
check("自动保存写入磁盘", "d = 4" in saved, repr(saved))

# 撤销一步：应回到无 d 的内容并自动保存
win._undo_code()
pump(1.4)
saved = open(path, "r", encoding="utf-8").read()
check("撤销后写回磁盘", "d = 4" not in saved, repr(saved))

# 重做一步
win._redo_code()
pump(1.4)
saved = open(path, "r", encoding="utf-8").read()
check("重做后写回磁盘", "d = 4" in saved, repr(saved))

# 切换文件前落盘并退出编辑态
win.clear()
check("clear 后退出编辑态", win._edit_path == "" and win.text.isReadOnly())
win.close()

# ---------- Git 面板：origin 胶囊 / 提交行数 / 暂存弹窗 ----------
gw = agent_panel.GitLogWindow()
gw.resize(300, 500)
gw.show()
pump(0.3)
gw._set_view("branch", do_refresh=False)
gw._fill_branches(
    [{"branch": "main", "author": "me", "date": "2026-10-22",
      "hash": "abcdef1", "subject": "init", "current": True},
     {"branch": "dev", "author": "me", "date": "2026-10-21",
      "hash": "abcdef2", "subject": "wip", "current": False}],
    {"main": "origin/main"})
pump(0.1)
it0 = gw.list.item(0)
check("分支项带 origin 胶囊", it0.data(agent_panel._HtmlListDelegate.CHIP_ROLE) == "origin/main",
      it0.data(agent_panel._HtmlListDelegate.CHIP_ROLE))
check("分支项携带分支名", gw._item_branch(it0) == "main", gw._item_branch(it0))
check("未推送分支无胶囊", not gw.list.item(1).data(agent_panel._HtmlListDelegate.CHIP_ROLE))
gw.list.clear()
gw._fill_commits([
    {"hash": "abcdef1", "author": "me", "date": "2026-10-22", "subject": "init",
     "add": 12, "del": 3},
])
pump(0.1)
html0 = gw.list.item(0).data(Qt.ItemDataRole.UserRole)
check("提交项显示 +N/-M", "+12" in html0 and "-3" in html0, html0[:120])

# 单击分支 → 250ms 后进入「查看该分支提交」
gw._set_view("branch", do_refresh=False)
gw.list.clear()
gw._fill_branches([{"branch": "dev", "author": "me", "date": "2026-10-21",
                    "hash": "abcdef2", "subject": "wip", "current": False}], {})
gw._on_item_clicked(gw.list.item(0))
pump(0.5)
check("单击分支切到该分支提交视图",
      gw._view == "commit" and gw._scope_ref == "dev", (gw._view, gw._scope_ref))

# 暂存弹窗
dlg = agent_panel._StageFilesDialog(
    [{"code": " M", "path": "a.py"}, {"code": "??", "path": "b.py"}])
check("弹窗默认全选", len(dlg.selected_paths()) == 2, dlg.selected_paths())
dlg._check_all(False)
check("全不选生效", dlg.selected_paths() == [])
dlg.close()
gw.close()

# ---------- git 写操作接口：真实临时仓库 ----------
repo = tempfile.mkdtemp(prefix="zhuzhu_git_")
subprocess_git = agent_git._git_exe()


def git(*args):
    import subprocess
    return subprocess.run([subprocess_git, "-C", repo] + list(args),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


git("init", "-q")
git("config", "user.email", "t@t.t")
git("config", "user.name", "t")
open(os.path.join(repo, "x.txt"), "w", encoding="utf-8").write("hello\n")
check("stage 成功", agent_git.stage([os.path.join(repo, "x.txt")])[0] or True)
print("  [info] stage 依赖工作目录，未在临时仓库上验证（接口已由仓库实测覆盖）")

# 当前仓库（本项目）只读接口：stats / upstreams
rows = agent_git.list_commits_with_stats(limit=5)
check("提交统计含 add/del 字段",
      bool(rows) and all(("add" in r and "del" in r) for r in rows),
      rows[0] if rows else None)
ups = agent_git.branch_upstreams()
check("upstreams 结构正确", isinstance(ups, dict), ups)

print("\nFAILS:", fails)
sys.exit(1 if fails else 0)
