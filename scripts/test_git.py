"""agent_git 只读分支查询 + worktree 树逻辑测试（临时 git 仓库）"""
import os, subprocess, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from winapp_migrator.core import agent_git, agent_tools


def _git(dir, *args):
    return subprocess.run(["git", "-C", str(dir), *args],
                          capture_output=True, text=True, check=True)


def test_branches():
    d = Path(tempfile.mkdtemp())
    _git(d, "init", "-b", "main")
    _git(d, "config", "user.email", "t@t.com")
    _git(d, "config", "user.name", "Test Author")
    Path(d, "a.txt").write_text("hello")
    _git(d, "add", ".")
    _git(d, "commit", "-m", "first commit")
    _git(d, "branch", "feature/x")
    _git(d, "checkout", "feature/x")
    Path(d, "b.txt").write_text("world")
    _git(d, "add", ".")
    _git(d, "commit", "-m", "second commit")

    agent_tools.set_workdir(str(d))
    assert agent_git.is_git_repo()
    branches = agent_git.list_branches()
    names = [b["branch"] for b in branches]
    assert "feature/x" in names and "main" in names, names
    # 每个分支都有作者与日期
    for b in branches:
        assert b["author"] == "Test Author", b
        assert b["date"], b
        assert b["hash"], b
    # 最新提交的应是 feature/x（最近提交）
    assert branches[0]["branch"] == "feature/x", branches[0]
    print(f"[OK] test_branches: {[ (b['branch'], b['date'], b['subject']) for b in branches ]}")


def test_not_repo():
    d = Path(tempfile.mkdtemp())
    agent_tools.set_workdir(str(d))
    assert not agent_git.is_git_repo()
    assert agent_git.list_branches() == []
    print("[OK] test_not_repo")


if __name__ == "__main__":
    test_branches()
    test_not_repo()
    print("\n全部通过")