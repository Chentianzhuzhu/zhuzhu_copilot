"""Agent 通信层：Agent 间消息总线 + 上下文账本（ContextLedger）。

工作团模式下 Agent 之间有两类非文件沟通：
- 消息总线（mailbox）：chat_with 投递的消息。目标空闲时下一轮拉取；目标繁忙时
  入队暂存，任务开始（on_task_start）时拉取注入上下文。未读消息随会话落盘。
- 上下文账本（ContextLedger）：每个运行实例的上下文轨迹（消息/命令/文件/工具/
  skill/mcp/plugin），供 look_context 监督查看。是否可见由目标的 share_context
  权限决定（look_context 工具侧校验）。

两类数据都是进程内 + 会话落盘（不跨会话长期保存），与共享上下文空间互补：
空间是「成员共同写结论」，总线是「一对一聊天」，账本是「监督审计」。
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque


class BusMessage:
    """一条 Agent 间消息。kind: chat（讨论）/ supervise（监督提醒）/ note（便签）。"""

    __slots__ = ("from_id", "kind", "mid", "text", "to_id", "ts")

    def __init__(self, from_id: str, to_id: str, text: str,
                 kind: str = "chat", mid: str = "", ts: float = 0.0):
        self.mid = mid or uuid.uuid4().hex[:12]
        self.from_id = str(from_id or "unknown")
        self.to_id = str(to_id or "unknown")
        self.kind = str(kind or "chat")
        self.text = str(text or "")
        self.ts = ts or time.time()

    def to_dict(self) -> dict:
        return {"mid": self.mid, "from": self.from_id, "to": self.to_id,
                "kind": self.kind, "text": self.text, "ts": self.ts}

    @classmethod
    def from_dict(cls, d: dict) -> BusMessage:
        d = d or {}
        return cls(str(d.get("from") or ""), str(d.get("to") or ""),
                   str(d.get("text") or ""), str(d.get("kind") or "chat"),
                   str(d.get("mid") or ""), float(d.get("ts") or 0))


# ------------------------------------------------------------
# 消息总线：{to_id: deque[BusMessage]}，进程内 + 可落盘快照
# ------------------------------------------------------------
_bus: dict = {}
_bus_lock = threading.RLock()


def send_message(from_id: str, to_id: str, text: str, kind: str = "chat") -> bool:
    """向目标 Agent 投递一条消息（目标不在线也入队，空闲拉取）。"""
    t = str(text or "").strip()
    to = str(to_id or "").strip()
    if not t or not to:
        return False
    msg = BusMessage(from_id, to, t, kind)
    with _bus_lock:
        _bus.setdefault(to, deque()).append(msg)
    return True


def inbox(agent_id: str, limit: int = 50) -> list:
    """目标拉取并清空自己的未读消息（按时间顺序，最多 limit 条）。"""
    agent_id = str(agent_id or "")
    with _bus_lock:
        q = _bus.pop(agent_id, None)
        if not q:
            return []
        out = []
        for _ in range(max(0, int(limit))):
            if not q:
                break
            out.append(q.popleft())
        if q:
            _bus[agent_id] = q
        return out


def peek_inbox(agent_id: str, limit: int = 10) -> list:
    """只读查看未读消息（不消费）。"""
    agent_id = str(agent_id or "")
    with _bus_lock:
        q = _bus.get(agent_id)
        if not q:
            return []
        return list(q)[:max(0, int(limit))]


def message_count(agent_id: str) -> int:
    """未读消息条数（UI 角标用）。"""
    with _bus_lock:
        return len(_bus.get(str(agent_id or "")) or ())


def bus_snapshot() -> dict:
    """未读消息快照 {to_id: [msg dict]}，供会话落盘。"""
    with _bus_lock:
        return {k: [m.to_dict() for m in v] for k, v in _bus.items()}


def restore_bus(snapshot: dict) -> None:
    """恢复未读消息（会话切换/恢复时）。"""
    if not isinstance(snapshot, dict):
        return
    with _bus_lock:
        for to_id, msgs in snapshot.items():
            if not isinstance(msgs, list):
                continue
            q = _bus.setdefault(str(to_id or ""), deque())
            for m in msgs:
                if isinstance(m, dict):
                    q.append(BusMessage.from_dict(m))


def reset_bus() -> None:
    """清空全部消息（测试/会话清理）。"""
    with _bus_lock:
        _bus.clear()


# ------------------------------------------------------------
# 上下文账本（ContextLedger）：运行实例的上下文轨迹，look_context 读取
# ------------------------------------------------------------
_LEDGER_KINDS = ("message", "command", "file", "tool", "skill", "mcp", "plugin")


class ContextLedger:
    """记录一个运行实例的上下文轨迹（按类型追加，环形上限防膨胀）。"""

    MAX_ITEMS = 400

    def __init__(self, agent_id: str):
        self.agent_id = str(agent_id or "unknown")
        self._items: list = []
        self._lock = threading.RLock()

    def add(self, kind: str, summary: str, detail: str = "") -> None:
        """追加一条轨迹。kind ∈ message/command/file/tool/skill/mcp/plugin。
        summary 为短摘要（UI/监督用），detail 为可选详情（截断防膨胀）。"""
        k = str(kind or "tool").strip().lower()
        if k not in _LEDGER_KINDS:
            k = "tool"
        s = str(summary or "").strip()
        if not s:
            return
        d = str(detail or "").strip()
        if len(d) > 600:
            d = d[:600] + "…"
        with self._lock:
            self._items.append({"kind": k, "summary": s[:200], "detail": d,
                                "ts": time.time()})
            if len(self._items) > self.MAX_ITEMS:
                del self._items[:len(self._items) - self.MAX_ITEMS]

    def items(self, kinds=None, limit: int = 200) -> list:
        """按类型过滤 + 截断（最近 N 条，时间正序）。"""
        with self._lock:
            it = list(self._items)
        if kinds:
            ks = {str(x).lower() for x in kinds}
            it = [x for x in it if x["kind"] in ks]
        if limit and len(it) > limit:
            it = it[-int(limit):]
        return list(it)

    def render(self, kinds=None, limit: int = 80) -> str:
        """渲染为可注入文本（look_context 读取）。"""
        it = self.items(kinds, limit)
        if not it:
            return ""
        lines = []
        for x in it:
            t = time.strftime("%H:%M:%S", time.localtime(x["ts"]))
            lines.append(f"- [{t}] {x['kind']}: {x['summary']}"
                         + (f"\n  {x['detail']}" if x["detail"] else ""))
        return "\n".join(lines)

    def to_dict(self) -> dict:
        with self._lock:
            return {"agent_id": self.agent_id, "items": list(self._items)}

    @classmethod
    def from_dict(cls, d: dict) -> ContextLedger:
        led = cls(str((d or {}).get("agent_id") or "unknown"))
        items = (d or {}).get("items") or []
        if isinstance(items, list):
            with led._lock:
                for it in items:
                    if isinstance(it, dict) and str(it.get("summary") or "").strip():
                        led._items.append({
                            "kind": str(it.get("kind") or "tool").lower()[:20],
                            "summary": str(it.get("summary") or "")[:200],
                            "detail": str(it.get("detail") or "")[:600],
                            "ts": float(it.get("ts") or 0),
                        })
        return led


_ledgers: dict = {}
_ledgers_lock = threading.RLock()


def ledger(agent_id: str) -> ContextLedger:
    """取/建指定实例的上下文账本。"""
    agent_id = str(agent_id or "unknown")
    with _ledgers_lock:
        led = _ledgers.get(agent_id)
        if led is None:
            led = ContextLedger(agent_id)
            _ledgers[agent_id] = led
        return led


def drop_ledger(agent_id: str) -> None:
    """删除指定实例的账本（实例结束/注销时清理）。"""
    with _ledgers_lock:
        _ledgers.pop(str(agent_id or ""), None)


def render_ledger(agent_id: str, kinds=None, limit: int = 80) -> str:
    """look_context 渲染指定实例的上下文轨迹；空则返回空串。"""
    with _ledgers_lock:
        led = _ledgers.get(str(agent_id or ""))
    if led is None:
        return ""
    return led.render(kinds, limit)


def ledgers_snapshot() -> dict:
    """全部账本快照，供会话落盘。"""
    with _ledgers_lock:
        return {k: v.to_dict() for k, v in _ledgers.items()}


def restore_ledgers(snapshot: dict) -> None:
    """恢复账本（会话切换/恢复时）。"""
    if not isinstance(snapshot, dict):
        return
    with _ledgers_lock:
        for aid, d in snapshot.items():
            if isinstance(d, dict):
                _ledgers[str(aid or "")] = ContextLedger.from_dict(d)


def reset_ledgers() -> None:
    """清空全部账本（测试/会话清理）。"""
    with _ledgers_lock:
        _ledgers.clear()
