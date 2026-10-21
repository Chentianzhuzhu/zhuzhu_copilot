"""沙盒 worktree 的 Git 查询与操作：供 AI 面板 Git 图形面板使用。

只读查询（分支/提交/远程跟踪分支/行数统计）与用户显式触发的写操作
（checkout / add / commit / push）都集中在此，UI 只负责展示与确认。
所有命令均为参数列表调用，不经 shell，提交说明不会被解释。"""

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


def _git(args: list, allow_partial: bool = False, timeout: int = 20) -> list:
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
                           errors="replace", timeout=timeout,
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


def list_commits(limit: int = None, ref: str = "") -> list:
    """返回提交历史（按时间倒序）：[{hash, author, date, subject}]。
    ref 为空时取当前 HEAD；传入分支名可查看该分支历史（不切换工作区）。
    limit=None 返回全部提交（Git 面板要求显示完整历史）；
    传入正整数时钳制到 1~100000 条（供需要限量的调用方使用，防止超大仓库一次性拉取）。"""
    if not is_git_repo():
        return []
    args = ["log"]
    if limit is not None:
        args.append("--max-count=%d" % max(1, min(int(limit), 100000)))
    args += ["--pretty=format:%h%x09%an%x09%ad%x09%s", "--date=short"]
    if ref:
        args.append(ref)
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


# ---------------- 写操作与远程分支 ----------------

def _run(args: list, timeout: int = 60) -> tuple:
    """执行任意 git 命令（含写操作），返回 (returncode, stdout, stderr)。

    与 _git 的区别：不吞掉错误信息 —— checkout/commit/push 失败时要把
    git 的原始 stderr 交给界面展示（例如「本地改动会被覆盖」、认证失败原因）。
    """
    d = _git_root()
    if not d:
        return 1, "", "not a git repository"
    try:
        p = subprocess.run([_git_exe(), "-C", d] + args,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout,
                           creationflags=subprocess.CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        return 1, "", "timeout"
    except Exception as e:
        return 1, "", str(e)
    return p.returncode, p.stdout or "", p.stderr or ""


def list_commits_with_stats(ref: str = "", limit: int = None) -> list:
    """提交历史 + 每条提交的变更行数：add/del。

    一次 git log --numstat 拿全量与统计，避免为每条提交单独起进程。
    大仓库首次解析可能较慢（后台线程执行，界面不阻塞）；超时则回退为
    不带统计的 list_commits，保证列表仍可用。
    """
    if not is_git_repo():
        return []
    args = ["log", "--numstat", "--no-renames",
            "--pretty=format:\x01%h%x09%an%x09%ad%x09%s", "--date=short"]
    if limit is not None:
        args.append("--max-count=%d" % max(1, min(int(limit), 100000)))
    if ref:
        args.append(ref)
    lines = _git(args, allow_partial=True, timeout=60)
    if not lines:
        return list_commits(limit=limit, ref=ref)
    out, cur = [], None
    for ln in lines:
        if ln.startswith("\x01"):
            if cur is not None:
                out.append(cur)
            parts = ln[1:].split("\t")
            if len(parts) < 3:
                cur = None
                continue
            cur = {
                "hash": parts[0],
                "author": parts[1],
                "date": parts[2],
                "subject": parts[3] if len(parts) > 3 else "",
                "add": 0,
                "del": 0,
            }
        elif cur is not None and ln.strip():
            cols = ln.split("\t")
            if len(cols) >= 3:
                # 二进制文件的行数是 "-"，int() 失败即忽略
                try:
                    cur["add"] += int(cols[0])
                except ValueError:
                    pass
                try:
                    cur["del"] += int(cols[1])
                except ValueError:
                    pass
    if cur is not None:
        out.append(cur)
    return out


def branch_push_state() -> dict:
    """本地分支的推送状态：
    {branch: {"upstream": "origin/main", "head": 短哈希,
              "upstream_hash": 短哈希, "pushed": bool}}。

    pushed=True 仅当分支头与上游头完全一致（没有待推送提交）——
    界面据此决定是否标注 origin/xxx：分支领先远程时不标注。
    """
    lines = _git(["for-each-ref", "refs/heads",
                  "--format=%(refname:short)%09%(objectname:short)%09"
                  "%(upstream:short)"])
    out = {}
    for ln in lines:
        parts = ln.split("\t")
        if len(parts) < 2:
            continue
        branch, head = parts[0], parts[1]
        upstream = parts[2].strip() if len(parts) > 2 else ""
        st = {"upstream": upstream, "head": head,
              "upstream_hash": "", "pushed": False}
        if upstream:
            uh = _git(["rev-parse", "--short", upstream])
            st["upstream_hash"] = uh[0].strip() if uh else ""
            st["pushed"] = bool(st["upstream_hash"]) and st["upstream_hash"] == head
        out[branch] = st
    return out


def commit_stats(ref: str = "", limit: int = 100) -> dict:
    """最近 limit 条提交的变更行数：{hash: (add, del)}。

    每条提交都要计算 diff，全量历史会耗时数秒到数十秒（实测 164 条约 8s）。
    界面先用 list_commits 秒出列表，再在后台补这批统计，避免首屏等待。
    """
    return {r["hash"]: (r.get("add") or 0, r.get("del") or 0)
            for r in list_commits_with_stats(ref=ref, limit=limit)}


def status_entries() -> list:
    """工作区改动清单：[{code, path}]。

    code 为 git porcelain 的两字符状态（如 " M"、"??"、"A "）。
    路径取最后一个字段，兼容重命名 "old -> new" 形式。
    """
    lines = _git(["status", "--porcelain"])
    out = []
    for ln in lines:
        if len(ln) < 4:
            continue
        code, path = ln[:2], ln[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        out.append({"code": code, "path": path})
    return out


def checkout(branch: str) -> tuple:
    """切换分支，返回 (ok, 说明)。失败时说明为 git 原始错误（如本地改动会被覆盖）。"""
    branch = (branch or "").strip()
    if not branch:
        return False, "分支名为空"
    rc, out, err = _run(["checkout", branch], timeout=120)
    if rc == 0:
        return True, (out or err).strip()
    return False, (err or out).strip() or "切换失败"


def stage(paths: list) -> tuple:
    """暂存指定文件，返回 (ok, 说明)。"""
    paths = [p for p in (paths or []) if p]
    if not paths:
        return False, "未选择文件"
    rc, out, err = _run(["add", "--"] + paths, timeout=120)
    return rc == 0, (err or out).strip()


def commit(message: str) -> tuple:
    """用给定说明提交已暂存内容，返回 (ok, 说明)。"""
    msg = (message or "").strip()
    if not msg:
        return False, "提交说明不能为空"
    rc, out, err = _run(["commit", "-m", msg], timeout=180)
    return rc == 0, (err or out).strip()


def push() -> tuple:
    """推送当前分支；无 upstream 时自动 push -u origin HEAD。"""
    rc, _out, _err = _run(["rev-parse", "--abbrev-ref", "@{upstream}"], timeout=20)
    args = ["push"] if rc == 0 else ["push", "-u", "origin", "HEAD"]
    rc, out, err = _run(args, timeout=300)
    return rc == 0, (err or out).strip()