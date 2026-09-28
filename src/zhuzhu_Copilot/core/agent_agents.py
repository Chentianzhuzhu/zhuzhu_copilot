"""自定义 Agent 管理（Cordis 独立于工作流的轻量人格）。

每个自定义 agent = 名称 + 系统提示词（人格）+ 可选绑定工作流（提供 tools/skill/llm）。
`@<agent>` 在输入框会话级切换当前会话的人格，与工作流解耦：
  - 人格覆盖：引擎 _system_prompt 优先使用该 agent 的 system_prompt。
  - 跨工作流：若配置 bound_workflow，切换时一并切到该工作流以获得其工具/技能；
    未配置则沿用当前会话工作流的工具/技能。
  - @_default 内置保留名：切回内置默认人格（不吃自定义 agent）。

存储：~/.zhuzhu_Copilot/agents/<name>.json（可 extend 为数据库/远程，不改调用方契约）。
"""

from zhuzhu_Copilot import app_identity
import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

_log = logging.getLogger("zhuzhu_Copilot.agent_agents")

# 内置保留名：@_default 切回默认人格，不允许作为自定义 agent 名
RESERVED = {"_default", "default"}

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def agents_root() -> Path:
    """自定义 agent 根目录（不存在则创建）"""
    root = app_identity.data_root() / "agents"
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return root


def _safe_name(name: str) -> str:
    """规范化 agent 名：仅允许字母数字/下划线/短横线，去首尾空白。非法返回空串。"""
    n = (name or "").strip()
    return n if _NAME_RE.match(n) else ""


def list_agents() -> list:
    """列出全部自定义 agent（按名称排序）。reserved 内置默认不在此列，
    由调用方以 @_default 处理切回。"""
    out = []
    for p in sorted(agents_root().glob("*.json")):
        ag = _load(p)
        if ag:
            out.append(ag)
    return out


def get_agent(name: str) -> Optional[dict]:
    """读取单个自定义 agent 配置；reserved / 不存在返回 None。"""
    name = _safe_name(name)
    if not name or name in RESERVED:
        return None
    return _load(agents_root() / f"{name}.json")


def _load(p: Path) -> Optional[dict]:
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict) or not d.get("name"):
            return None
        d.setdefault("bound_workflow", "")
        return d
    except Exception:
        return None


def save_agent(name: str, system_prompt: str,
               bound_workflow: str = "") -> tuple:
    """新建/更新自定义 agent。成功返回 (True, 提示)，失败返回 (False, 原因)。"""
    name = _safe_name(name)
    if not name:
        return False, "agent 名非法：仅允许字母/数字/下划线/短横线，且长度 1-64"
    if name in RESERVED:
        return False, f"「{name}」为内置保留名，不能用作自定义 agent"
    if not (system_prompt or "").strip():
        return False, "system_prompt（人格）不能为空"
    from zhuzhu_Copilot.core import agent_workflow
    bound = (bound_workflow or "").strip()
    if bound and bound != agent_workflow.DEFAULT_WORKFLOW \
            and not agent_workflow.is_workflow(bound):
        return False, f"绑定工作流不存在或已禁用: {bound}"
    ag = {
        "name": name,
        "system_prompt": system_prompt,
        "bound_workflow": bound,
        "updated": time.time(),
    }
    existing = get_agent(name)
    ag["created"] = (existing or {}).get("created", time.time())
    try:
        agents_root().mkdir(parents=True, exist_ok=True)
        with open(agents_root() / f"{name}.json", "w", encoding="utf-8") as f:
            json.dump(ag, f, ensure_ascii=False)
        return True, f"自定义 Agent「{name}」已保存"
    except OSError as e:
        return False, f"保存失败: {e}"


def delete_agent(name: str) -> tuple:
    """删除自定义 agent。成功返回 (True, 提示)，失败返回 (False, 原因)。"""
    name = _safe_name(name)
    if not name or name in RESERVED:
        return False, f"agent 名非法或为保留名: {name or '空'}"
    try:
        (agents_root() / f"{name}.json").unlink(missing_ok=True)
        return True, f"自定义 Agent「{name}」已删除"
    except OSError as e:
        return False, f"删除失败: {e}"