"""沙盒 worktree 的 Git 只读查询：供 AI 面板 git 图形面板展示分支/时间/作者。
仅做只读查询，不提供任何 push/pull/checkout 等写操作（由 UI 层约束）。"""

import os
import subprocess
import time
from pathlib import Path

from zhuzhu_Copilot.core import agent_tools


def _git_root() -> str:
    """就近向上查找 git 仓库根目录。

    - 工作目录已显式设置：只在工作目录及其父目录链上找 .git。工作目录不是（也不在）
      git 仓库内时返回空，绝不再回退到进程 cwd/模块目录 —— 否则切换工作目录到普通文件夹后，
      git 面板会回退到应用自身源码仓库，出现「仍显示旧工作目录仓库」的错觉。
    - 工作目录未设置（空）：回退到进程 cwd、应用模块目录（启动初期/未选目录时仍能展示分支）。
    """
    base = agent_tools.get_workdir() or ""
    if base:
        candidates = [base]
    else:
        candidates = [str(Path.cwd()), str(Path(__file__).resolve().parent)]
    for start in candidates:
        p = Path(start)
        for q in [p, *p.parents]:
            g = q / ".git"
            if g.exists() or g.is_dir():
                return str(q)
    return ""


def repo_dir() -> str:
    """返回 git 仓库根目录（工作目录就近向上查找）"""
    return _git_root()


def is_git_repo() -> bool:
    return bool(_git_root())


def _git_exe() -> str:
    """定位 git 可执行文件：GUI 启动的进程 PATH 可能不含 git，回退常见安装路径。"""
    for d in (str(Path.home() / "AppData/Local/Programs/Git/cmd"),
              r"C:\Program Files\Git\bin", r"C:\Program Files\Git\cmd",
              r"C:\Program Files (x86)\Git\bin", r"C:\Program Files (x86)\Git\cmd"):
        exe = Path(d) / "git.exe"
        if exe.exists():
            return str(exe)
    return "git"


def _git(args: list, allow_partial: bool = False) -> list:
    """在 git 仓库根目录执行只读 git 查询，失败返回 []

    allow_partial=True 时：命令非零退出但已有输出也照用（git log 遍历历史时若遇到
    缺失/损坏的上游对象会在中途失败，此时 stdout 里已包含能读到的提交）。
    否则会把「本该显示的提交」连同输出一起丢掉，面板只剩「（暂无提交记录）」——
    表现为「Git 面板只能显示分支、显示不了提交」。"""
    d = _git_root()
    if not d:
        return []
    try:
        # git 输出为 UTF-8（含中文提交信息）；中文 Windows 默认 GBK 解码会失败，
        # 显式按 UTF-8 解码并容错，避免 p.stdout 为 None 导致后续崩溃
        p = subprocess.run([_git_exe(), "-C", d] + args,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=20,
                           creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception:
        return []
    if p.returncode != 0 and not allow_partial:
        return []
    if p.stdout is None:   # 防御：解码异常时 stdout 可能为 None
        return []
    return p.stdout.splitlines()


def list_branches() -> list:
    """返回分支列表（按最近提交时间倒序）：[{branch, author, date, subject, hash, current}]。
    仅读取本地分支及其最新提交信息，不做任何写操作。"""
    if not is_git_repo():
        return []
    cur = current_branch()
    lines = _git(["for-each-ref", "--sort=-committerdate", "refs/heads",
                  "--format=%(refname:short)%09%(authorname)%09"
                  "%(committerdate:short)%09%(objectname:short)%09%(subject)"])
    out = []
    for ln in lines:
        parts = ln.split("\t")
        if len(parts) < 4:
            continue
        out.append({
            "branch": parts[0],
            "author": parts[1],
            "date": parts[2],
            "hash": parts[3],
            "subject": parts[4] if len(parts) > 4 else "",
            "current": parts[0] == cur,
        })
    return out


def list_commits(limit: int = None) -> list:
    """返回提交历史（按时间倒序）：[{hash, author, date, subject}]。只读，不跨分支。
    limit=None 返回全部提交（Git 面板要求显示完整历史）；
    传入正整数时钳制到 1~100000 条（供需要限量的调用方使用，防止超大仓库一次性拉取）。"""
    if not is_git_repo():
        return []
    if limit is None:
        args = ["log", "--pretty=format:%h%x09%an%x09%ad%x09%s", "--date=short"]
    else:
        k = max(1, min(int(limit), 100000))
        args = ["log", "--max-count=%d" % k,
                "--pretty=format:%h%x09%an%x09%ad%x09%s", "--date=short"]
    lines = _git(args, allow_partial=True)   # 历史断裂时也显示已读到的提交（见 _git）
    out = []
    for ln in lines:
        parts = ln.split("\t")
        if len(parts) < 3:
            continue
        out.append({
            "hash": parts[0],
            "author": parts[1],
            "date": parts[2],
            "subject": parts[3] if len(parts) > 3 else "",
        })
    return out


def current_branch() -> str:
    """当前所在分支（只读）"""
    lines = _git(["rev-parse", "--abbrev-ref", "HEAD"])
    return lines[0].strip() if lines else ""


def head_hash() -> str:
    """当前 HEAD 完整哈希（用于及时发现新提交，只读）"""
    lines = _git(["rev-parse", "HEAD"])
    return lines[0].strip() if lines else ""


def repo_mtime() -> float:
    """仓库最近变化时间戳（基于 .git/HEAD 与 refs/heads/* 的 mtime，纯文件系统 stat）。
    用于周期刷新时判断是否有新提交/切分支，避免每几秒起一次 git 子进程造成卡顿。"""
    d = _git_root()
    if not d:
        return 0.0
    gd = Path(d) / ".git"
    latest = 0.0
    cands = [gd / "HEAD"]
    try:
        for head in (gd / "refs").glob("heads/*"):
            cands.append(head)
    except OSError:
        pass
    for c in cands:
        try:
            latest = max(latest, c.stat().st_mtime)
        except OSError:
            pass
    return latest