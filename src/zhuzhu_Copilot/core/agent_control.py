"""Agent 控制层：运行中 Agent 实例的统一控制句柄（停止/暂停/警告）。

工作团模式下，每个运行中的 Agent（主 Agent、跨工作流派发的主 Agent、子 Agent）
持有一个 AgentControl，由运行实例在启动时 register、结束时 unregister：
- stop：沿用现有 threading.Event 语义，替换/兼容各循环里的 _stop。
- pause/resume：领导 Agent（如产品经理）或 UI 可暂停单个 Agent；被暂停的循环在
  每轮检查点阻塞等待，恢复后继续；stop 优先，暂停中收到 stop 立即退出。
- warn：领导 Agent 可向目标 Agent 投递警告提醒，目标在下一轮检查点取走并注入上下文。
- agent_id 命名约定：main（会话主 Agent）、wf:<工作流名>、sub:<子 Agent 名>、@:<子 Agent 名>。

同一进程内全局注册表；跨会话/跨工作流共用，由 agent_id 区分。
"""

from __future__ import annotations

import threading
import time


class AgentControl:
    """单个运行中 Agent 的控制句柄。"""

    _PAUSE_POLL = 0.2

    def __init__(self, agent_id: str):
        self.agent_id = str(agent_id or "unknown")
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._warns: list = []
        self._lock = threading.RLock()
        self._created = time.time()

    # ---- stop ----
    @property
    def stop_event(self) -> threading.Event:
        return self._stop

    def stop(self) -> None:
        self._stop.set()

    def is_stopped(self) -> bool:
        return self._stop.is_set()

    # ---- pause / resume ----
    def pause(self) -> None:
        self._pause.set()

    def resume(self) -> None:
        self._pause.clear()

    def is_paused(self) -> bool:
        return self._pause.is_set()

    def wait_if_paused(self) -> bool:
        """暂停中阻塞等待恢复；stop 置位则立即返回 False（调用方应退出循环）。

        返回 True 表示可继续执行本轮。循环每轮开头调用一次即可。
        """
        while self._pause.is_set() and not self._stop.is_set():
            self._pause.wait(self._PAUSE_POLL)
        return not self._stop.is_set()

    # ---- warn ----
    def warn(self, text: str) -> None:
        t = str(text or "").strip()
        if not t:
            return
        with self._lock:
            self._warns.append(t)

    def drain_warns(self) -> list:
        """取走并清空未读警告（调用方注入本轮上下文）。"""
        with self._lock:
            w = self._warns
            self._warns = []
        return w

    def has_warns(self) -> bool:
        with self._lock:
            return bool(self._warns)

    # ---- snapshot（UI 展示用） ----
    def status(self) -> dict:
        return {"agent_id": self.agent_id,
                "stopped": self._stop.is_set(),
                "paused": self._pause.is_set(),
                "warns": len(self._warns),
                "created": self._created}


# ------------------------------------------------------------
# 注册表：{agent_id: AgentControl}
# ------------------------------------------------------------
_registry: dict = {}
_registry_lock = threading.RLock()


def register_control(control: AgentControl) -> None:
    """注册运行实例的控制句柄（同名覆盖旧实例）。"""
    with _registry_lock:
        _registry[control.agent_id] = control


def unregister_control(agent_id: str) -> None:
    """注销运行实例的控制句柄（结束或停止时）。"""
    with _registry_lock:
        _registry.pop(str(agent_id or ""), None)


def agent_control(agent_id: str) -> AgentControl | None:
    """取指定 agent 的控制句柄（未运行返回 None）。"""
    with _registry_lock:
        return _registry.get(str(agent_id or ""))


def agent_controls() -> dict:
    """全部运行实例控制句柄快照（{agent_id: AgentControl}）。"""
    with _registry_lock:
        return dict(_registry)


def active_agent_ids() -> list:
    """运行中 agent id 列表（主 Agent 优先）。"""
    with _registry_lock:
        return sorted(_registry.keys())


def pause_agent(agent_id: str) -> tuple:
    """暂停目标 Agent。返回 (ok, msg)。"""
    c = agent_control(agent_id)
    if c is None:
        return False, f"[pause_agent] Agent「{agent_id}」当前未在运行，无法暂停"
    c.pause()
    return True, f"已暂停 Agent「{agent_id}」，恢复前其任务循环将在检查点挂起"


def resume_agent(agent_id: str) -> tuple:
    """恢复目标 Agent。返回 (ok, msg)。"""
    c = agent_control(agent_id)
    if c is None:
        return False, f"[resume_agent] Agent「{agent_id}」当前未在运行"
    c.resume()
    return True, f"已恢复 Agent「{agent_id}」，任务继续执行"


def warn_agent(agent_id: str, text: str) -> tuple:
    """向目标 Agent 投递警告提醒（下一轮检查点注入其上下文）。返回 (ok, msg)。"""
    c = agent_control(agent_id)
    if c is None:
        return False, (f"[warn_agent] Agent「{agent_id}」当前未在运行，无法投递警告；"
                       "若其稍后启动可在其任务开始前投递")
    c.warn(text)
    return True, f"已向 Agent「{agent_id}」发送警告提醒，其将在下一个任务检查点收到"


def stop_agent(agent_id: str) -> tuple:
    """强制停止目标 Agent（等价于该实例的停止按钮）。返回 (ok, msg)。"""
    c = agent_control(agent_id)
    if c is None:
        return False, f"[stop_agent] Agent「{agent_id}」当前未在运行"
    c.stop()
    return True, f"已发送停止指令给 Agent「{agent_id}」"


def control_from_stop(agent_id: str, stop_event) -> AgentControl:
    """从既有 stop Event 构造并注册控制句柄（引擎已有 _stop 时用，保持兼容）。"""
    c = AgentControl(agent_id)
    if stop_event is not None:
        # 复用既有事件：任何一方 set 都视为停止
        c._stop = stop_event
    register_control(c)
    return c
