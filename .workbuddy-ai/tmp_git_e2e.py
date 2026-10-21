"""真实 git 仓库端到端：stage / commit / 行数统计 / upstream / push（本地 bare 远程）。"""
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, r"c:\Users\zhuzhu\Desktop\zhuzhu Copilot\src")
from zhuzhu_Copilot.core import agent_git, agent_tools

git = agent_git._git_exe()
base = tempfile.mkdtemp(prefix="zhuzhu_e2e_")
remote = os.path.join(base, "remote.git")
repo = os.path.join(base, "work")

subprocess.run([git, "init", "--bare", "-q", remote], check=True)
subprocess.run([git, "init", "-q", repo], check=True)
for k, v in (("user.email", "t@t.t"), ("user.name", "tester")):
    subprocess.run([git, "-C", repo, "config", k, v], check=True)
subprocess.run([git, "-C", repo, "remote", "add", "origin", remote], check=True)

agent_tools.set_workdir(repo)
print("repo root =", agent_git.repo_dir())

f = os.path.join(repo, "a.txt")
open(f, "w", encoding="utf-8").write("l1\nl2\nl3\n")
print("status =", agent_git.status_entries())

print("stage =", agent_git.stage(["a.txt"]))
print("status(staged) =", agent_git.status_entries())
print("commit =", agent_git.commit("feat: 初始提交"))
print("status(after commit) =", agent_git.status_entries())

rows = agent_git.list_commits_with_stats()
print("stats =", rows)

print("upstreams(no push) =", agent_git.branch_upstreams())
print("push =", agent_git.push())
print("upstreams(after push) =", agent_git.branch_upstreams())

open(f, "a", encoding="utf-8").write("l4\n")
print("stage2 =", agent_git.stage(["a.txt"]), "commit2 =", agent_git.commit("feat: 追加一行"))
print("stats2 =", agent_git.list_commits_with_stats(limit=2))
print("push2 =", agent_git.push())
print("branch list =", agent_git.list_branches())

# 分支：建分支并检出（模拟面板双击）
subprocess.run([git, "-C", repo, "branch", "dev"], check=True)
print("checkout dev =", agent_git.checkout("dev"))
print("current =", agent_git.current_branch())
print("dev stats =", len(agent_git.list_commits_with_stats(ref="dev")))
print("checkout main =", agent_git.checkout("main"))
print("current =", agent_git.current_branch())
