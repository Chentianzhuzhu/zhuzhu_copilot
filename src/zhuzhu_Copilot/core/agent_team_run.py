"""成员后台运行器：让跨工作流主 Agent 在后台独立执行，领导者可监督/催促/管控。

与同步派发（dispatch_sub_agents 内联执行）互补，实现「人类工作团队」体验：
- team_start：后台线程运行 run_agent_llm（成员工作流完整能力：人格/工具/技能/LLM），
  运行中已注册 AgentControl → pause/resume/warn/stop 全程可管控。
- 每个成员独立线程：同时运行的跨工作流主 Agent 数量不做上限（派发多少跑多少），
  由主 Agent / 用户自行控制规模。
- 消息驱动续跑：一轮任务结束、收件箱仍有余量时自动以「处理成员消息」再起一轮，
  直到收件箱清空 —— chat_with 催促立即生效，成员「收到即继续工作」。
- 结果回写共同上下文空间（run_agent_llm 已按 shared/space 回写 kind=result），
  领导者 look_context / 共享空间即可收齐结论。
"""
import threading
import time

_workers: dict = {}
_lock = threading.RLock()

# 运行状态常量（look_context 展示用）
_STATUS_RUNNING = "运行中"
_STATUS_DONE = "已完成"
_STATUS_IDLE = "空闲"
_STATUS_ERROR = "异常"


def _load_client(wf):
    """成员工作流专属 LLM 客户端；失败回退全局默认（run_agent_llm 内还会再尝试）。"""
    try:
        from zhuzhu_Copilot.core import agent_llm, agent_workflow
        cfg = agent_llm.load_model_config()
        client, _src = agent_workflow.load_llm_client(cfg, workflow=wf)
        if client is not None:
            return client
    except Exception:
        pass
    from zhuzhu_Copilot.core import agent_llm
    return agent_llm.LLMClient()


def client_from(client=None):
    """由主 Agent 客户端派生同配置的独立客户端（供成员后台线程使用，避免并发共用）。

    与主 Agent 会话同一套 base_url/api_key/model/protocol——保证成员开工配置与主 Agent
    一致（手动选的服务商/模型也能继承）；构造失败回退 settings 默认。
    """
    from zhuzhu_Copilot.core import agent_llm
    try:
        base = getattr(client, "base_url", None) or None
        key = getattr(client, "api_key", None) or None
        model = getattr(client, "model", None) or None
        protocol = getattr(client, "protocol", None) or None
        timeout = getattr(client, "timeout", None) or None
        idle_timeout = getattr(client, "idle_timeout", None) or None
        if base or key or model:
            return agent_llm.LLMClient(
                base_url=base, api_key=key, model=model,
                protocol=protocol or "chat",
                timeout=timeout, idle_timeout=idle_timeout)
    except Exception:
        pass
    return _load_client("")


class _Member:
    __slots__ = ("agent_id", "client", "context", "done", "error", "finished",
                 "goal", "result", "running", "shared", "source", "space",
                 "started", "wf", "conv")

    def __init__(self, agent_id, wf, goal, context, space, shared, source, client=None,
                 conversation=""):
        self.agent_id = agent_id
        self.wf = wf
        self.client = client if client is not None else _load_client(wf)
        self.goal = goal
        self.context = context
        self.space = space
        self.shared = shared
        self.source = source
        self.conv = str(conversation or "")
        self.result = ""
        self.error = ""
        self.started = time.time()
        self.finished = 0.0
        self.running = False
        self.done = threading.Event()


def team_start(agent_id, wf, goal, context="", space="", shared=False, source="",
               client=None, conversation="") -> tuple:
    """启动一个成员后台执行。同一 agent_id 已在运行时不再重复启动。返回 (ok, message)。

    client：主 Agent/调用方提供的客户端（或其配置同款）。缺省按 settings 默认配置
    构造——若主 Agent 会话手动选了服务商/模型，成员用默认配置会与主 Agent 错位，
    导致开工失败。因此引擎派发侧总是把主会话同配置 client 传入，保证成员与主 Agent
    使用同一套连接参数。
    conversation：对话作用域（会话 id）。成员后台线程无继承的线程局部，必须由派发方
    显式传入，保证成员读写的是**该对话**的共同上下文空间（跨对话隔离）。
    """
    from zhuzhu_Copilot.core import agent_workflow
    goal = (goal or "").strip()
    if not goal:
        return False, "空任务，未派发"
    if not agent_workflow.is_workflow(wf):
        return False, f"工作流「{wf}」不存在或已禁用"
    with _lock:
        old = _workers.get(agent_id)
        if old is not None and old.running:
            return False, f"成员「{wf}」已在后台运行中（可 look_context 监督、chat_with 催促）"
        m = _Member(agent_id, wf, goal, context, space, shared, source, client=client,
                    conversation=conversation)
        m.running = True     # 占位：重复派发检测须在线程真正开跑前生效（避免同成员被并发启动两次）
        _workers[agent_id] = m
    # 每个成员一条独立线程（无池上限）：派发数量不受并发上限约束
    try:
        threading.Thread(target=_run_loop, args=(m,), name=f"team-{agent_id}",
                         daemon=True).start()
    except Exception as e:
        with _lock:
            m.running = False
            if _workers.get(agent_id) is m:
                _workers.pop(agent_id, None)
        return False, f"成员「{wf}」后台线程启动失败：{e}"
    return True, f"已派发：成员「{wf}」开始后台执行（可 look_context 监督、chat_with 催促）"


def _run_loop(m) -> None:
    """成员后台主循环：执行目标 → 收件箱有余量则续跑处理消息，直到清空。

    失败后不立即退出：若收件箱仍有催促消息（chat_with），以「处理成员消息」再起
    一轮并把失败原因带进上下文，让领导者能继续沟通/纠偏（最多 3 次失败续跑，
    防止反复失败死循环）。
    """
    from zhuzhu_Copilot.core import agent_bus, agent_context, agent_subagent
    m.running = True
    _err_reruns = 0
    # 对话作用域：成员后台线程落地派发方的会话，保证读写的是本对话的共同上下文空间
    prev_conv = agent_context.thread_local_conversation()
    agent_context.set_conversation(m.conv)
    try:
        while not m.done.is_set():
            goal = m.goal
            m.goal = ""
            if not goal:
                break
            try:
                out = agent_subagent.run_agent_llm(
                    m.client, m.wf, goal, stop=lambda: m.done.is_set(),
                    context=m.context, shared_context=m.shared, space=m.space,
                    source=m.source, agent_id=m.agent_id, conversation=m.conv)
                m.result = out
            except Exception as e:
                m.result = f"[成员异常] {e}"
                m.error = str(e)
                # 失败不退出：有催促消息时续跑一轮处理消息（把失败原因带上）
                _err_reruns += 1
                if _err_reruns > 3:
                    break
                try:
                    _in = agent_bus.inbox(m.agent_id, 5)
                except Exception:
                    _in = []
                if not _in:
                    break
                lines = "\n".join(f"[{x.from_id}] {x.text}" for x in _in)
                m.goal = ("你最近一轮执行失败（原因：%s）。"
                          "请结合当前任务与团队目标，重新安排并继续推进：\n"
                          % str(e)) + lines
                m.context = ""
                continue
            m.context = ""   # 首轮专用上下文用完即清
            try:
                _in = agent_bus.inbox(m.agent_id, 5)
            except Exception:
                _in = []
            if not _in:
                break
            lines = "\n".join(f"[{x.from_id}] {x.text}" for x in _in)
            m.goal = ("收到来自其他 Agent 的消息，请结合当前任务与团队目标处理"
                      "（回应 / 调整 / 汇报）：\n" + lines)
    finally:
        agent_context.set_conversation(prev_conv)
        m.running = False
        m.finished = time.time()
        m.done.set()


def team_status(agent_id: str = "") -> dict:
    """成员运行状态快照。agent_id 空 → 全部；否则单成员（不存在返回空 dict）。"""
    with _lock:
        if agent_id:
            m = _workers.get(str(agent_id or ""))
            items = [(str(agent_id), m)] if m is not None else []
        else:
            items = list(_workers.items())
    out = {}
    for k, v in items:
        out[k] = {"wf": v.wf,
                  "running": v.running,
                  "done": bool(v.result and not v.running and not v.error),
                  "error": bool(v.error),
                  "started": time.strftime("%H:%M:%S", time.localtime(v.started)),
                  "finished": (time.strftime("%H:%M:%S", time.localtime(v.finished))
                               if v.finished else "")}
    return out


def team_status_label(agent_id: str) -> str:
    """单成员状态文本（look_context 附加展示用）。不在册 → 空闲。"""
    with _lock:
        m = _workers.get(str(agent_id or ""))
    if m is None:
        return _STATUS_IDLE
    if m.running:
        return _STATUS_RUNNING
    if m.error:
        return _STATUS_ERROR
    if m.result:
        return _STATUS_DONE
    return _STATUS_IDLE


def team_running(agent_id: str) -> bool:
    with _lock:
        m = _workers.get(str(agent_id or ""))
        return bool(m is not None and m.running)


def team_result(agent_id: str) -> str:
    with _lock:
        m = _workers.get(str(agent_id or ""))
        return (m.result if m is not None else "") or ""


def team_stop(agent_id: str) -> None:
    with _lock:
        m = _workers.get(str(agent_id or ""))
        if m is None:
            return
        m.done.set()


def team_stop_all(conversation: str = None) -> None:
    """停止后台成员。conversation=None 停全部（向后兼容）；给定时只停**该对话**的成员
    ——对话隔离下，某个对话停止任务不应牵连其他对话仍在工作的成员。"""
    with _lock:
        items = [m for m in _workers.values()
                 if conversation is None or m.conv == str(conversation or "")]
    for m in items:
        m.done.set()


def team_reset() -> None:
    """清空全部成员状态（测试用）。"""
    with _lock:
        for m in _workers.values():
            m.done.set()
        _workers.clear()
