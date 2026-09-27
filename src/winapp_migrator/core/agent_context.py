"""共同上下文空间（Shared Context Space）——主 Agent / 注册式子 Agent / 临时子 Agent 共用的统一上下文总线。

设计要点：
- **对话隔离**：空间按「对话（会话）」+ space id 双重隔离——物理键为
  `<conversation>::<space id>`。同一对话内的主 Agent 与各子 Agent（含跨工作流主 Agent）
  共用同一份上下文；不同对话即使使用同名空间（含默认空间）也互不可见、互不覆盖。
- **空间隔离**：同一对话内以 space id 隔离互不相关的编队任务；主 Agent 开启空间时可选注入议题种子。
- **双向读写**：任一开启共享的成员（主 Agent / 注册式子 Agent / 临时子 Agent）都可
  `render`（读到主 Agent 议题、其他成员产出与追加条目）与 `append`（写回自己的结论/产物），
  后续成员与主 Agent 立即可见，形成协同记忆。
- **开关由主 Agent 决策**：空间是否开启、哪些子 Agent 参与共享，由主 Agent 通过工具参数
  （`shared_context`）或注册式子 Agent 的默认配置决定；未开启共享时子 Agent 维持独立上下文。
- **容量控制**：默认不限容量（条目数 / 单条字符数 / 渲染预算三项常量均为 0=不限），
  空间可无限写入并全量读写；需要收紧时把对应常量改为正整数，容量逻辑即刻生效。
- **线程安全**：并发子 Agent 同时写入时加锁；进程内生命周期（不落盘），空间关闭即回收。

作用域解析优先级：
- 对话：线程局部（工作线程内显式设置）> 全局当前对话（UI 主线程随会话切换设置）> 空
  （空 = 不隔离，退化为进程级单空间，保持旧行为与无 UI 场景/测试可用）。
- 空间：线程局部（子 Agent 循环内显式设置）> 该对话的活跃空间（主 Agent 开启时设）> 空。

调用方约定：跨线程派发前先在**父线程**取 `current_conversation()`，由子线程入口用
`set_conversation()` 设置，保证子 Agent 解析到同一对话的空间。
"""
import threading
import time
import uuid

# 共同上下文空间容量上限（0 = 不限制，便于按需配置扩展）。
# 默认全部不限制：空间可无限写入条目、单条不截断、渲染不预算截断，
# 由主 Agent/调用方按需控制注入规模。
MAX_ENTRIES = 0        # 条目数上限（0=不限，超出不再裁最旧）
MAX_ENTRY_CHARS = 0    # 单条文本上限（0=不限，不再截断）
MAX_RENDER_CHARS = 0   # render 默认渲染预算（0=不限，全量渲染）

# 默认空间 id（主 Agent 未显式命名时使用）；对话隔离下每个对话各有一份同名空间
DEFAULT_SPACE = "default"
# 对话维度分隔符：物理空间键 = "<conversation>::<逻辑空间 id>"
CONV_SEP = "::"

_lock = threading.RLock()
# 物理键 -> {"created", "owner", "seed", "conv", "sid", "entries", "readers"}
# （readers 空 = 任何人可读，向后兼容）
_spaces: dict = {}
# 对话 -> 该对话的活跃空间（逻辑 id）；主 Agent op=open（activate=True）时设置，
# 供同对话内子 Agent 缺省解析。按对话分键，避免跨对话串联。
_active: dict = {}
# 全局当前对话：UI 主线程随会话切换设置；工作线程用线程局部覆盖
_conv = {"id": ""}
# 线程局部：空间 / 来源标签 / 对话，保证其内嵌套派发、工具调用解析到同一作用域
_tls = threading.local()


# ---------------- 对话作用域 ----------------
def _clean_conv(conv_id) -> str:
    return str(conv_id or "").strip()


def set_conversation(conv_id: str) -> str:
    """设置**当前线程**的对话作用域，返回设置前的值（供调用方 finally 复位）。"""
    prev = thread_local_conversation()
    _tls.conv = _clean_conv(conv_id)
    return prev


def set_conversation_global(conv_id: str) -> str:
    """设置进程级当前对话（UI 主线程在会话切换/新建时调用），返回旧值。"""
    with _lock:
        prev = _conv.get("id", "")
        _conv["id"] = _clean_conv(conv_id)
        return prev


def thread_local_conversation() -> str:
    """仅取线程局部对话（不含全局回退），供调用方保存/复位用。"""
    return getattr(_tls, "conv", "") or ""


def current_conversation() -> str:
    """解析当前对话：线程局部 > 全局当前对话 > 空（不隔离）。"""
    return thread_local_conversation() or _conv.get("id", "")


def conversation_of(space_key: str) -> str:
    """取物理空间键所属对话（非物理键或空对话返回空串）。"""
    s = str(space_key or "").strip()
    head, sep, tail = s.partition(CONV_SEP)
    return head if (sep and head and tail) else ""


def local_space(space_key: str) -> str:
    """取物理空间键的逻辑空间 id（对话无关，UI 展示/会话落盘用）。"""
    s = str(space_key or "").strip()
    head, sep, tail = s.partition(CONV_SEP)
    return tail if (sep and head and tail) else s


def scoped(space_id: str) -> str:
    """把逻辑空间 id 归一化为物理键：`<当前对话>::<id>`；已是物理键则原样返回。
    无当前对话时保持原样（向后兼容：测试/无 UI 场景退化为进程级单空间）。"""
    s = str(space_id or "").strip()
    if not s:
        return ""
    if conversation_of(s):
        return s                      # 已是物理键（跨线程传递 / 落盘恢复）
    cid = current_conversation()
    return f"{cid}{CONV_SEP}{s}" if cid else s


# ---------------- 空间作用域 ----------------
def set_current(space_id: str) -> None:
    """设置当前线程的共享空间（子 Agent 循环内调用）；空串表示退出线程级共享。"""
    _tls.space = str(space_id or "").strip()


def set_source(source: str) -> None:
    """设置当前线程的写入来源标签（子 Agent 循环内调用，供 shared_context 工具默认归属）。"""
    _tls.source = source or ""


def current_source() -> str:
    """当前线程的写入来源标签（无则空串）。"""
    return getattr(_tls, "source", "") or ""


def thread_local_space() -> str:
    """仅取线程局部空间（不含全局活跃回退），供调用方保存/复位用。"""
    return getattr(_tls, "space", "")


def thread_local_source() -> str:
    """仅取线程局部来源标签，供调用方保存/复位用。"""
    return getattr(_tls, "source", "")


def current() -> str:
    """解析当前共享空间（逻辑 id）：线程局部 > 本对话活跃空间 > 空（未开启共享）。"""
    return getattr(_tls, "space", "") or active_space()


def active_space() -> str:
    """当前对话的活跃空间（逻辑 id，无则空串）。"""
    with _lock:
        return _active.get(current_conversation(), "")


def set_active(space_id: str) -> None:
    """设置当前对话的活跃空间（空串清除）；仅当空间存在时生效。"""
    cid = current_conversation()
    key = scoped(space_id)
    with _lock:
        _active[cid] = local_space(key) if key in _spaces else ""


def has_space(space_id: str) -> bool:
    """空间是否存在于**当前对话**（跨对话同名空间互不可见即返回 False）。"""
    with _lock:
        return scoped(space_id) in _spaces


def open_space(space_id: str = "", seed: str = "", owner: str = "",
               activate: bool = True, readers=None) -> str:
    """在当前对话内开启（已存在则重置）一个共同上下文空间，返回**逻辑** space id。
    seed 为主 Agent 注入的议题/任务背景；activate=True 时同时设为该对话的活跃空间。
    readers：允许读取该空间的 agent id 集合（None/空 = 任何人可读，向后兼容）；
    团队场景可限制只有开启共享的成员可读。"""
    cid = current_conversation()
    logical = str(space_id or "").strip() or f"ctx_{uuid.uuid4().hex[:8]}"
    key = f"{cid}{CONV_SEP}{logical}" if cid else logical
    rs = {str(x).strip() for x in (readers or [])} - {""}
    body = str(seed or "")
    if MAX_ENTRY_CHARS and len(body) > MAX_ENTRY_CHARS:
        body = body[:MAX_ENTRY_CHARS]
    with _lock:
        _spaces[key] = {"created": time.time(), "owner": str(owner or ""),
                        "seed": body, "entries": [],
                        "readers": rs, "conv": cid, "sid": logical}
        if activate:
            _active[cid] = logical
    return logical


def grant_read(space_id: str, agent_id: str) -> bool:
    """授予某 agent 读取空间权限（团队/工作流切换继承时用）。返回是否成功。"""
    with _lock:
        sp = _spaces.get(scoped(space_id))
        if sp is None:
            return False
        aid = str(agent_id or "").strip()
        if aid:
            sp.setdefault("readers", set()).add(aid)
        return True


def revoke_read(space_id: str, agent_id: str) -> bool:
    """撤销某 agent 读取空间权限。"""
    with _lock:
        sp = _spaces.get(scoped(space_id))
        if sp is None:
            return False
        sp.get("readers", set()).discard(str(agent_id or ""))
        return True


def space_readers(space_id: str) -> list:
    """空间当前允许读取者列表（空=任何人可读）。"""
    with _lock:
        sp = _spaces.get(scoped(space_id))
        return sorted(sp.get("readers", ())) if sp else []


def _can_read(sp: dict, viewer: str) -> bool:
    """权限校验：readers 为空 = 公开可读；否则 viewer 需在其中或是空间 owner。"""
    if not str(viewer or "").strip():
        return True
    rs = sp.get("readers") or set()
    if not rs:
        return True
    return str(viewer or "") in rs or str(viewer or "") == str(sp.get("owner") or "")


def close_space(space_id: str = "") -> tuple:
    """关闭当前对话内的空间并回收。缺省关闭该对话的活跃空间。返回 (ok, message)。"""
    cid = current_conversation()
    logical = str(space_id or "").strip() or active_space()
    if not logical:
        return False, "当前没有开启中的共同上下文空间"
    key = f"{cid}{CONV_SEP}{local_space(logical)}" if cid else logical
    with _lock:
        existed = _spaces.pop(key, None) is not None
        if _active.get(cid) == local_space(logical):
            _active[cid] = ""
    return (True, f"已关闭共同上下文空间 {local_space(logical)}") if existed \
        else (False, f"空间不存在: {local_space(logical)}")


def close_conversation(conv_id: str) -> int:
    """回收某对话的全部空间（删除对话/清空对话时调用），返回回收数量。
    不影响其他对话的空间与活跃状态。"""
    cid = _clean_conv(conv_id)
    with _lock:
        keys = [k for k, sp in _spaces.items() if str(sp.get("conv") or "") == cid]
        for k in keys:
            _spaces.pop(k, None)
        _active.pop(cid, None)
    return len(keys)


def seed_text(space_id: str) -> str:
    """空间议题种子（主 Agent 开启时注入的背景）。"""
    with _lock:
        sp = _spaces.get(scoped(space_id))
        return str(sp.get("seed") or "") if sp else ""


def append(space_id: str, source: str, text: str, kind: str = "note",
           source_type: str = "") -> bool:
    """向空间追加一条条目（成员双向写回）。超出容量自动裁剪最旧条目。
    source_type：产生该条目的上下文来源类型（message/command/file/tool/skill/
    mcp/plugin/result 等，用于监督分类）。返回是否写入成功（空间不存在返回 False）。"""
    body = str(text or "").strip()
    if not body:
        return False
    if MAX_ENTRY_CHARS and len(body) > MAX_ENTRY_CHARS:
        body = body[:MAX_ENTRY_CHARS] + "…"
    key = scoped(space_id)
    with _lock:
        sp = _spaces.get(key)
        if sp is None:
            return False
        entries = sp["entries"]
        entries.append({"source": str(source or "unknown").strip() or "unknown",
                        "kind": str(kind or "note").strip() or "note",
                        "text": body, "ts": time.time(),
                        "source_type": str(source_type or "").strip()[:24]})
        if MAX_ENTRIES and len(entries) > MAX_ENTRIES:
            del entries[:len(entries) - MAX_ENTRIES]
    return True


def entries(space_id: str, limit: int = 0, viewer: str = "") -> list:
    """空间条目快照（不含议题种子）。limit>0 时只取最近 limit 条。
    viewer：读取者 agent id，空间设限时校验其读取权限（无权限返回 []）。"""
    with _lock:
        sp = _spaces.get(scoped(space_id))
        if sp is None:
            return []
        if not _can_read(sp, viewer):
            return []
        items = [dict(e) for e in sp["entries"]]
    return items[-int(limit):] if limit and int(limit) > 0 else items


def _fmt_entry(e: dict) -> str:
    ts = time.strftime("%H:%M:%S", time.localtime(e.get("ts") or time.time()))
    return f"- [{ts}] {e.get('source')}（{e.get('kind')}）：{e.get('text')}"


def render(space_id: str, exclude: str = "", include_seed: bool = True,
           max_chars: int = None, limit: int = 0,
           viewer: str = "") -> str:
    """把空间渲染为可注入子 Agent 上下文的文本。
    exclude：排除某来源（子 Agent 读取时可排除自己此前的产出，避免自我重复）。
    limit：只取最近 limit 条（0=全部）。
    max_chars：None 取 MAX_RENDER_CHARS（默认 0=不限制，全量渲染）；>0 时从最近条目
    向前回填，超出即截断，议题种子始终保留。运行时解析常量，便于配置热调。
    viewer：读取者 agent id，空间设限时校验其读取权限（无权限返回空串）。"""
    logical = local_space(str(space_id or "").strip())
    with _lock:
        sp = _spaces.get(scoped(space_id))
        if sp is None:
            return ""
        if not _can_read(sp, viewer):
            return ""
        seed = str(sp.get("seed") or "") if include_seed else ""
        items = [dict(e) for e in sp["entries"]]
    ex = str(exclude or "").strip()
    if ex:
        items = [e for e in items if e.get("source") != ex]
    if limit and int(limit) > 0:
        items = items[-int(limit):]
    head = [f"【共同上下文空间 {logical}】"]
    if seed:
        head.append(f"- 议题（主 Agent 开启）：{seed}")
    lines = [_fmt_entry(e) for e in items]
    budget = max(int(MAX_RENDER_CHARS if max_chars is None else max_chars), 0)
    if not budget:     # 0=不限制：全量渲染
        return "\n".join(head + lines) if lines else "\n".join(head)
    # 从最近条目向前回填，超出 max_chars 即截断（保证最新协同信息优先可见）；
    # 单条自身超预算时按剩余预算截断，避免一条超长条目撑爆注入上下文。
    kept, omitted = [], 0
    for ln in reversed(lines):
        if budget <= 0:
            omitted += 1
            continue
        if len(ln) + 1 > budget:
            kept.append(ln[:max(budget - 1, 0)] + "…")
            omitted += 1
            budget = 0
            continue
        kept.append(ln)
        budget -= len(ln) + 1
    if omitted:
        kept.append(f"- …（较早的 {omitted} 条未完整显示）")
    head.extend(reversed(kept))
    if not items and not seed:
        head.append("- （空间为空：暂无成员写入）")
    return "\n".join(head)


def _stats_key(key: str) -> dict:
    """按物理键取统计快照（内部用：调用方已持锁）。"""
    sp = _spaces.get(key)
    logical = local_space(key)
    cid = str(sp.get("conv") or "") if sp else conversation_of(key)
    if sp is None:
        return {"space": logical, "conv": cid, "exists": False, "entries": 0,
                "chars": 0, "active": False, "created": 0.0, "owner": ""}
    chars = sum(len(e.get("text") or "") for e in sp["entries"])
    return {"space": logical, "conv": cid, "exists": True, "entries": len(sp["entries"]),
            "chars": chars, "active": _active.get(cid) == logical,
            "created": sp.get("created", 0.0), "owner": sp.get("owner", "")}


def stats(space_id: str = "") -> dict:
    """空间统计：条目数/字符数/创建时间/是否为当前对话的活跃空间。
    space_id 为空 → 取当前对话的活跃空间。"""
    with _lock:
        return _stats_key(scoped(space_id) or scoped(active_space()))


def list_spaces(all_conversations: bool = False) -> list:
    """列出空间统计（按创建时间倒序）。默认**只列当前对话**的空间（对话隔离）；
    all_conversations=True 时列出全部对话的空间（诊断用）。"""
    cid = current_conversation()
    with _lock:
        sids = [k for k, sp in _spaces.items()
                if all_conversations or str(sp.get("conv") or "") == cid]
        return sorted((_stats_key(k) for k in sids),
                      key=lambda x: x.get("created", 0.0), reverse=True)


def reset_all() -> None:
    """清空全部空间、活跃状态与对话作用域（测试/会话清理用）。"""
    with _lock:
        _spaces.clear()
        _active.clear()
        _conv["id"] = ""
    _tls.space = ""
    _tls.conv = ""


def spaces_snapshot() -> dict:
    """全部空间 + 各对话活跃空间快照，供会话落盘（readers 转 list 便于 JSON 序列化）。
    空间键为物理键（含对话前缀），恢复时不依赖当前对话作用域。"""
    with _lock:
        return {"active": dict(_active),
                "spaces": {
                    key: {"created": sp.get("created", 0.0),
                          "owner": sp.get("owner", ""),
                          "seed": sp.get("seed", ""),
                          "conv": str(sp.get("conv") or ""),
                          "sid": local_space(key),
                          "readers": sorted(sp.get("readers", ())),
                          "entries": [dict(e) for e in sp.get("entries", [])]}
                    for key, sp in _spaces.items()}}


def restore_spaces(snapshot: dict) -> None:
    """恢复空间与各对话活跃状态（会话切换/恢复时）。"""
    if not isinstance(snapshot, dict):
        return
    with _lock:
        raw = snapshot.get("spaces") if isinstance(snapshot.get("spaces"), dict) else {}
        for key, sp in raw.items():
            if not isinstance(sp, dict):
                continue
            cid = str(sp.get("conv") or conversation_of(key) or "")
            logical = str(sp.get("sid") or local_space(key))
            phys = f"{cid}{CONV_SEP}{logical}" if cid else logical
            _spaces[phys] = {
                "created": float(sp.get("created") or 0),
                "owner": str(sp.get("owner") or ""),
                "seed": str(sp.get("seed") or ""),
                "readers": {str(x) for x in (sp.get("readers") or [])} - {""},
                "entries": [dict(e) for e in (sp.get("entries") or []) if isinstance(e, dict)],
                "conv": cid, "sid": logical,
            }
        act = snapshot.get("active")
        if isinstance(act, dict):
            for cid, logical in act.items():
                ck = _clean_conv(cid)
                ls = local_space(str(logical or ""))
                key = f"{ck}{CONV_SEP}{ls}" if ck else ls
                _active[ck] = ls if (ls and key in _spaces) else ""
        else:   # 兼容旧快照：单值活跃空间（无对话维度）
            ls = str(act or "")
            _active[""] = ls if ls in _spaces else ""
