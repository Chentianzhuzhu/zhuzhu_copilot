"""工作团（Agent Team）：由多个工作流 + 一个领导者（产品经理）工作流组成的默认专家团队。

核心设计（产品经理激活制）：
- 团队定义：~/.zhuzhu_Copilot/team.json（缺省用内置默认团队，用户可改）。
  结构 {name, leader, members:[工作流名]}。leader 为领导者工作流（产品经理）。
- 激活：用户 @product_manager（切到产品经理工作流）即激活团队模式——开启共同上下文
  空间（seed=本轮任务），产品经理作为领导者可 look_context 监督成员、chat_with 讨论、
  pause/resume/warn 纠偏成员；可 dispatch_sub_agents(agent=成员工作流) 总派发设计任务。
- 未激活时各工作流独立运行（向后兼容），普通对话不受团队逻辑打扰。
- 成员工作流主 Agent 在跨工作流派发（run_agent_llm）时注册 wf:<名> 控制句柄，
  领导者即可对其暂停/警告；开启共享空间后其结论写回空间供团队复用。
"""

from __future__ import annotations
from zhuzhu_Copilot import app_identity

import json
import threading
from pathlib import Path

# 默认团队：领导者 + 成员（可按需在 team.json 覆盖；禁止硬编码于调用点）
DEFAULT_TEAM = {
    "name": "默认开发团队",
    "leader": "product_manager",            # 产品经理（核心领导者）工作流
    "members": ["zhuzhu_copilot",           # 默认工作流（继承原有人格）
                "frontend_design",          # 前端设计
                "product_dev",              # 产品开发
                "backend_dev",              # 后端开发
                "product_debug"],           # 产品调试
}

_team_lock = threading.RLock()
_team_cache = None       # (mtime_ns, size, config)
_TEAM_FILE = None        # 延迟计算（需要 CONFIG_DIR）


def _team_path() -> Path:
    global _TEAM_FILE
    if _TEAM_FILE is None:
        try:
            from zhuzhu_Copilot.core import agent_skills
            _TEAM_FILE = Path(agent_skills.CONFIG_DIR) / "team.json"
        except Exception:
            _TEAM_FILE = app_identity.data_root() / "team.json"
    return _TEAM_FILE


def team_config() -> dict:
    """读取团队配置（team.json；无则用内置默认团队）。"""
    global _team_cache
    f = _team_path()
    try:
        st = f.stat()
        fp = (st.st_mtime_ns, st.st_size)
    except Exception:
        return dict(DEFAULT_TEAM)
    with _team_lock:
        if _team_cache is not None and _team_cache[0] == fp:
            return dict(_team_cache[1])
    try:
        data = json.loads(f.read_text(encoding="utf-8", errors="replace"))
        if not isinstance(data, dict) or not str(data.get("leader") or "").strip():
            return dict(DEFAULT_TEAM)
        members = [str(m).strip() for m in (data.get("members") or []) if str(m).strip()]
        out = {"name": str(data.get("name") or DEFAULT_TEAM["name"]),
               "leader": str(data.get("leader") or "").strip(),
               "members": members}
        with _team_lock:
            _team_cache = (fp, out)
        return dict(out)
    except Exception:
        return dict(DEFAULT_TEAM)


def save_team_config(config: dict) -> tuple:
    """保存团队配置到 team.json。返回 (ok, msg)。"""
    cfg = dict(config or {})
    leader = str(cfg.get("leader") or "").strip()
    members = [str(m).strip() for m in (cfg.get("members") or []) if str(m).strip()]
    if not leader:
        return False, "[team] 缺少 leader（领导者工作流名）"
    if not members:
        return False, "[team] 缺少 members（成员工作流名列表）"
    data = {"name": str(cfg.get("name") or DEFAULT_TEAM["name"]),
            "leader": leader, "members": members}
    try:
        f = _team_path()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        return False, f"[team] 写入失败: {e}"
    global _team_cache
    with _team_lock:
        _team_cache = None
    return True, f"团队配置已保存（领导者={leader}，成员={len(members)} 个工作流）"


def team_leader() -> str:
    """领导者工作流名。"""
    return team_config().get("leader") or ""


def team_members() -> list:
    """团队成员工作流名列表（不含领导者）。"""
    return list(team_config().get("members") or [])


def is_leader_workflow(workflow: str) -> bool:
    """是否为领导者（产品经理）工作流。"""
    return str(workflow or "") == team_leader()


def is_member_workflow(workflow: str) -> bool:
    """是否为团队成员工作流（含领导者）。"""
    wf = str(workflow or "")
    return wf == team_leader() or wf in team_members()


def team_active() -> bool:
    """团队模式是否已激活（存在 leader 开启的共同上下文空间）。"""
    try:
        from zhuzhu_Copilot.core import agent_context
        sid = agent_context.active_space()
        if not sid or not agent_context.has_space(sid):
            return False
        owner = agent_context.stats(sid).get("owner") or ""
        return bool(owner) and owner != "主Agent"
    except Exception:
        return False


def activate_team(seed: str = "", space: str = "") -> str:
    """激活团队模式：开启共同上下文空间（缺省用默认空间 id，可显式指定）。
    返回空间 id。已激活/已存在则沿用（不重置，保留既有协同记忆）。"""
    from zhuzhu_Copilot.core import agent_context
    leader = team_leader()
    sid = str(space or "").strip() or agent_context.DEFAULT_SPACE
    if agent_context.has_space(sid):
        # 沿用既有空间：补设活跃，保证主循环注入快照
        agent_context.set_active(sid)
        return sid
    agent_context.open_space(sid, seed=str(seed or "")[:6000], owner=leader,
                             activate=True)
    return sid


def deactivate_team() -> tuple:
    """解散团队模式：关闭当前团队空间。返回 (ok, msg)。"""
    from zhuzhu_Copilot.core import agent_context
    sid = agent_context.active_space()
    if not sid:
        return False, "团队模式当前未激活（没有开启中的团队空间）"
    ok, msg = agent_context.close_space(sid)
    return ok, msg


def team_status() -> dict:
    """团队状态摘要（UI/工具展示用）。"""
    cfg = team_config()
    try:
        from zhuzhu_Copilot.core import agent_context
        sid = agent_context.active_space()
        stats = agent_context.stats(sid) if sid else None
    except Exception:
        stats = None
    return {"name": cfg.get("name", ""), "leader": cfg.get("leader", ""),
            "members": list(cfg.get("members") or []),
            "active": team_active(),
            "space": sid if (sid and agent_context.has_space(sid)) else "",
            "entries": (stats or {}).get("entries", 0)}
