"""agent_git 数据层测试：分支/提交查询 + 工作目录切换后指向新仓库（只读，纯 git 子进程）"""
import shutil
import subprocess

import pytest

from zhuzhu_Copilot.core import agent_git, agent_tools


def _has_git() -> bool:
    return shutil.which("git") is not None


def _git(d, *args):
    return subprocess.run(["git", "-C", str(d), *args],
                          capture_output=True, text=True, check=True)


@pytest.fixture()
def fresh_repo(tmp_path):
    d = tmp_path / "main"
    d.mkdir()
    _git(d, "init", "-b", "main", "-q")
    _git(d, "config", "user.email", "t@t.com")
    _git(d, "config", "user.name", "Test Author")
    (d / "a.txt").write_text("content")
    _git(d, "add", ".")
    _git(d, "commit", "-m", f"commit in {d.name}", "-q")
    return d


@pytest.mark.skipif(not _has_git(), reason="git 未安装")
def test_list_commits_and_branches(fresh_repo):
    agent_tools.set_workdir(str(fresh_repo))
    assert agent_git.is_git_repo()
    commits = agent_git.list_commits()
    assert len(commits) >= 1
    assert commits[0]["subject"] == f"commit in {fresh_repo.name}"
    assert commits[0]["author"] and commits[0]["date"]
    branches = agent_git.list_branches()
    assert any(b["branch"] == "main" and b["current"] for b in branches)


@pytest.mark.skipif(not _has_git(), reason="git 未安装")
def test_switch_workdir_changes_repo(tmp_path):
    """切换工作目录后，git 数据必须来自新仓库（旧仓库面板内容失效的根因回归测试）"""
    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    for r, label in ((repo_a, "A"), (repo_b, "B")):
        r.mkdir()
        _git(r, "init", "-b", "main", "-q")
        _git(r, "config", "user.email", "t@t.com")
        _git(r, "config", "user.name", "T")
        (r / "f.txt").write_text(label)
        _git(r, "add", ".")
        _git(r, "commit", "-m", f"first of {label}", "-q")
        _git(r, "tag", f"v{label}")

    agent_tools.set_workdir(str(repo_a))
    assert agent_git.head_hash() == _git(repo_a, "rev-parse", "HEAD").stdout.strip()
    assert agent_git.list_commits()[0]["subject"] == "first of A"

    agent_tools.set_workdir(str(repo_b))
    assert agent_git.head_hash() == _git(repo_b, "rev-parse", "HEAD").stdout.strip()
    assert agent_git.list_commits()[0]["subject"] == "first of B"
    assert agent_git.repo_dir() == str(repo_b)   # 仓库根同步切换，不再停留在旧仓库


@pytest.mark.skipif(not _has_git(), reason="git 未安装")
def test_non_repo_workdir(fresh_repo, tmp_path):
    agent_tools.set_workdir(str(fresh_repo))
    agent_tools.set_workdir(str(tmp_path / "plain"))
    (tmp_path / "plain").mkdir()
    assert not agent_git.is_git_repo()
    assert agent_git.list_commits() == []


def test_git_keeps_partial_output_only_when_allowed(monkeypatch):
    """_git 默认丢弃失败输出；allow_partial=True 时保留已读到的行（list_commits 用）。"""
    class _R:
        returncode = 128
        stdout = "abc123\t张三\t2026-01-01\t标题\n"

    monkeypatch.setattr(agent_git, "_git_root", lambda: "C:/dummy")
    monkeypatch.setattr(agent_git.subprocess, "run", lambda *a, **k: _R())
    assert agent_git._git(["log"]) == []
    assert agent_git._git(["log"], allow_partial=True) == _R.stdout.splitlines()


@pytest.mark.skipif(not _has_git(), reason="git 未安装")
def test_list_commits_survives_broken_history(tmp_path):
    """历史断裂（缺少上游对象）时必须显示能读到的提交，不能整列空掉。

    回归（用户反馈「git 面板无法显示提交、只能显示分支」）：git log 遍历到断裂处会
    非零退出，_git 原先把 stdout 一并丢弃 → 面板永远只剩「（暂无提交记录）」；
    分支走 for-each-ref 不受影响，于是表现为「只有分支、没有提交」。"""
    d = tmp_path / "broken"
    d.mkdir()
    _git(d, "init", "-b", "main", "-q")
    _git(d, "config", "user.email", "t@t.com")
    _git(d, "config", "user.name", "T")
    for i in range(3):
        (d / "f.txt").write_text(str(i))
        _git(d, "add", ".")
        _git(d, "commit", "-m", f"c{i}", "-q")
    # 删掉最早那条提交的对象 → 遍历父链到此处失败（模拟仓库历史损坏）
    root = _git(d, "rev-list", "--max-parents=0", "HEAD").stdout.strip()
    obj = d / ".git" / "objects" / root[:2] / root[2:]
    assert obj.exists(), obj
    obj.chmod(0o666)
    obj.unlink()
    probe = subprocess.run(["git", "-C", str(d), "log"],
                           capture_output=True, text=True, check=False)
    assert probe.returncode != 0, "用例前提：全量 git log 应失败"

    agent_tools.set_workdir(str(d))
    assert agent_git.is_git_repo()
    commits = agent_git.list_commits()
    assert commits, "历史断裂时也须显示能读到的提交（不得为空）"
    assert commits[0]["subject"] == "c2"        # 最新提交仍可见
    assert agent_git.list_branches(), "分支查询不受影响"