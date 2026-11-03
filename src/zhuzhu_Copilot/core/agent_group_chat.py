"""AI 群聊：多个 Agent 在同一段对话里轮流发言。

设计要点（与单 Agent 会话完全隔离，不改动主面板的会话状态机）：

- **成员独立**：每个成员各自持有 名称 / 性格 / 系统级提示词 / 模型 / 头像。
  成员配置是本模块自己的数据（``group_chats/<id>.json``）；设置页的「全局规则」
  仍由引擎统一追加，对每个成员都生效。
- **发言权**：首轮随机挑一位（即「系统随机发一条可以开始聊天的命令」）；之后
  发言人可在回复首行写 ``@成员名`` 指定下一位，未指定则随机（排除自己）。
- **共同上下文**：复用 ``agent_context`` 的共享空间（不设条目数 / 长度 / 渲染上限）。
  每个成员的发言写入空间；每位成员只读取「自己上次发言之后新增」的条目 ——
  增量注入让 token 随轮数线性增长，而不是把整段历史反复重发。为此成员引擎关闭了
  「整份空间快照注入」（``shared_snapshot=False``），否则每轮会再拼一次全量快照
  并留在历史里，token 随轮数平方增长。
- **各自的上下文窗口**：每位成员持有**独立且持久**的 AgentEngine（含自己的发言、
  工具结果与压缩状态），窗口按其模型单独解析（``agent_llm.resolve_context``）。
  成员之间互不干扰：A 的上下文被压缩不会影响 B。
- **工具能力**：每个成员用高效模式工具集（文件 / 命令 / 检索 / 联网）跑一轮，
  群聊因此不仅能聊，也能真的读文件、查资料、协作干活。
- **暂停 / 结束**：暂停在当前一轮说完后生效（不截断半句）；结束立即中断。
- **终止**：轮数上限兜底，避免多个 Agent 互相客套把额度烧光。

存储：``~/.zhuzhu_Copilot/group_chats/<id>.json``（配置 + 聊天记录一并落盘）。
"""

import json
import random
import re
import threading
import time

from zhuzhu_Copilot import app_identity
from zhuzhu_Copilot.core import (
    agent_agents, agent_avatar, agent_context, agent_engine, agent_llm, agent_prompt,
)
from zhuzhu_Copilot.core.i18n import ui as _ui

#: 连续失败上限：达到后停止群聊，避免同一错误（余额/鉴权）被反复重试
MAX_CONSEC_FAIL = 3
#: 默认轮数上限（0 = 不限）；可在窗口里调整
DEFAULT_MAX_ROUNDS = 30
#: 暂停轮询间隔（秒）：暂停只是「不再开新一轮」，无需高精度
_PAUSE_POLL = 0.1

_ID_RE = re.compile(r"^g_[0-9a-zA-Z_-]{6,40}$")
_HANDOFF_RE = re.compile(r"^\s*@([^\s@，,：:]{1,32})")

#: 群聊专属工具：成员可主动退出发言（无补充时把发言权交出去，避免为凑字数而重复）。
#: 只在讨论轮次经 ``AgentEngine.extra_tools`` 注入，不进 agent_tools 全局表。
EXIT_SPEECH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "exit_speech",
        "description": ("退出本轮发言：当没有新观点、不想重复别人、或认为无需补充时调用，"
                        "立即结束本轮并把发言权交给下一位成员。"
                        "不要用它回避本应由你回答的问题。"),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {"type": "string",
                           "description": "一句话说明为何退出（可留空）"},
            },
            "required": [],
        },
    },
}


def chats_root():
    root = app_identity.data_root() / "group_chats"
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return root


def _path(cid: str):
    return chats_root() / f"{cid}.json"


def new_chat(participants: list = None, title: str = "", max_rounds: int = None,
             topic: str = "") -> dict:
    """新建一份群聊配置（未落盘）。

    topic：用户自定义的讨论主题。留空 = 自由开聊（成员自行找话题）。
    """
    now = time.time()
    return {
        "id": "g_%d_%d" % (int(now), random.randint(100, 999)),
        "title": (title or "AI 群聊").strip(),
        "topic": str(topic or "").strip(),
        "created": now,
        "updated": now,
        "max_rounds": DEFAULT_MAX_ROUNDS if max_rounds is None else int(max_rounds),
        "participants": [normalize_participant(p) for p in (participants or [])],
        "messages": [],
    }


def normalize_participant(p: dict) -> dict:
    """统一成员字段（缺省补空串），存储与 UI 共用同一形状。"""
    p = p or {}
    return {
        "name": str(p.get("name") or "").strip(),
        "persona": str(p.get("persona") or "").strip(),
        "system_prompt": str(p.get("system_prompt") or "").strip(),
        "provider": str(p.get("provider") or "").strip(),
        "model": str(p.get("model") or "").strip(),
        # 头像引用（core/agent_avatar 的返回值，kind="group"）；空=用首字占位图
        "avatar": str(p.get("avatar") or "").strip(),
    }


def normalize_chat(d: dict) -> dict:
    d = d or {}
    out = new_chat(max_rounds=d.get("max_rounds", DEFAULT_MAX_ROUNDS))
    out.update({k: d[k] for k in ("id", "title", "topic", "created", "updated")
                if d.get(k)})
    out["participants"] = [normalize_participant(p) for p in (d.get("participants") or [])]
    out["messages"] = [m for m in (d.get("messages") or []) if isinstance(m, dict)]
    return out


def load_chat(cid: str):
    if not _ID_RE.match(cid or ""):
        return None
    try:
        with open(_path(cid), encoding="utf-8") as f:
            return normalize_chat(json.load(f))
    except Exception:
        return None


def list_chats() -> list:
    out = []
    for p in sorted(chats_root().glob("g_*.json")):
        try:
            with open(p, encoding="utf-8") as f:
                d = normalize_chat(json.load(f))
            d["_messages"] = len(d.get("messages") or [])
            out.append(d)
        except Exception:
            continue
    out.sort(key=lambda x: x.get("updated", 0), reverse=True)
    return out


def save_chat(chat: dict) -> tuple:
    cid = str((chat or {}).get("id") or "")
    if not _ID_RE.match(cid):
        return False, "群聊 id 非法"
    chat["updated"] = time.time()
    try:
        chats_root().mkdir(parents=True, exist_ok=True)
        tmp = _path(cid).with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(chat, f, ensure_ascii=False, indent=2)
        tmp.replace(_path(cid))
        return True, "已保存"
    except OSError as e:
        return False, f"保存失败: {e}"


def delete_chat(cid: str) -> tuple:
    if not _ID_RE.match(cid or ""):
        return False, "群聊 id 非法"
    try:
        old = load_chat(cid) or {}
        for p in old.get("participants") or []:
            if p.get("avatar"):
                agent_avatar.remove(p.get("avatar"), "group")
        _path(cid).unlink(missing_ok=True)
        return True, "已删除"
    except OSError as e:
        return False, f"删除失败: {e}"


def resolve_endpoint(cfg: dict, provider: str = "", model: str = "") -> dict:
    """成员模型 → 实际连接参数（base_url / api_key / protocol / model / provider）。

    未指定模型时回落到用户当前的自动选择；服务商优先按名称精确匹配，
    失配时按模型名回退（与主面板路由同一套逻辑，避免同名模型串号）。
    """
    providers = (cfg or {}).get("providers") or []
    m = (model or "").strip()
    if not m:
        try:
            m = agent_llm.resolve_model(cfg, "medium")
        except Exception:
            m = ""
        # resolve_model 期望的是「模型配置」字典；传入整份配置时它可能回传非字符串，
        # 这里统一收敛成模型名，避免把 dict 当模型名塞进 LLMClient。
        if not isinstance(m, str) or not m.strip():
            m = agent_llm.DEFAULT_MODEL
    sel = None
    if provider:
        sel = next((p for p in providers if p.get("name") == provider), None)
    if sel is None:
        try:
            sel = agent_llm.provider_for_model(cfg, m, provider_name=provider)
        except Exception:
            sel = None
    sel = sel or {}
    return {
        "base_url": (sel.get("base_url") or agent_llm.DEFAULT_BASE_URL),
        "api_key": (sel.get("api_key") or agent_llm.DEFAULT_API_KEY),
        "protocol": (sel.get("protocol") or "chat"),
        "model": m,
        # 供 resolve_context 读取上游声明的窗口/输出上限
        "provider": sel,
    }


def export_member(chat: dict, member_name: str, agent_name: str) -> tuple:
    """把群聊成员保存为可 @ 调用的自定义 Agent（人格 / 提示词 / 头像一并带走）。

    返回 (ok, 提示)。名称沿用 agent_agents 的 ASCII 规则（@名称 调用），
    中文成员名需在此指定一个合法英文名。
    """
    member = next((p for p in (chat.get("participants") or [])
                   if p.get("name") == member_name), None)
    if not member:
        return False, "成员不存在"
    ref = ""
    if member.get("avatar"):
        ref = agent_avatar.copy_to(member.get("avatar"), "group", "agent")
    return agent_agents.save_agent(
        agent_name, member.get("system_prompt", ""), persona=member.get("persona", ""),
        avatar=ref or "")


class GroupChatRunner(threading.Thread):
    """群聊调度线程：一轮一位发言人，直到用户停止 / 暂停 / 达到轮数上限。

    回调都在本线程触发，UI 侧必须自行 marshal 到主线程（信号或 QMetaObject）。
    - on_status(text)            状态行
    - on_delta(name, text)       流式增量
    - on_message(dict)           一条完整消息 {"speaker","text","ts","error"?}
    - on_end(reason)             结束（reason 为可直接展示的中文原因）
    - confirm(name, args) -> bool 工具执行前确认（默认放行）
    - ask_user(args) -> str      Agent 反问用户时的回复（群聊里不应阻塞用户）
    """

    def __init__(self, chat: dict, llm_cfg: dict,
                 on_status=None, on_delta=None, on_message=None, on_end=None,
                 confirm=None, ask_user=None, on_reasoning=None,
                 on_summary_delta=None, on_summary=None):
        super().__init__(daemon=True)
        self.chat = chat
        self.cfg = llm_cfg or {}
        self.on_status = on_status or (lambda _t: None)
        self.on_delta = on_delta or (lambda _n, _t: None)
        self.on_message = on_message or (lambda _m: None)
        self.on_end = on_end or (lambda _r: None)
        self.confirm = confirm or (lambda _n, _a: True)
        self.ask_user = ask_user or (lambda _a: "")
        self.on_reasoning = on_reasoning or (lambda _n, _t: None)
        self.on_summary_delta = on_summary_delta or (lambda _n, _t: None)
        self.on_summary = on_summary or (lambda _m: None)
        self._stop = threading.Event()
        self._pause = threading.Event()      # set = 暂停中（下一轮不再开始）
        self._turn_stop = threading.Event()  # 本轮专用：退出/结束都从这里中断
        self._exit_requested = False         # 本轮成员是否调用了 exit_speech
        self._lock = threading.Lock()
        self._messages = list(chat.get("messages") or [])
        self._engines = {}          # 成员名 → (引擎, 端点签名)：模型变更才重建
        self._seen = {}             # 成员名 → 已消费的「非自身」共享条目数（增量上下文）
        self._space = ""            # 共同上下文空间 id（每个群聊一份）
        self._last_reply = ""
        self.end_reason = ""

    # ---------- 外部控制 ----------
    def stop(self):
        self._stop.set()
        # 同时中断在途的那一轮，否则要等它把工具/输出跑完
        try:
            self._turn_stop.set()
        except Exception:
            pass

    def pause(self):
        """暂停：当前这一轮会说完，之后不再开新一轮（不截断半句）。"""
        self._pause.set()

    def resume(self):
        self._pause.clear()

    @property
    def paused(self) -> bool:
        return self._pause.is_set()

    def say(self, text: str) -> bool:
        """用户插话：写入群聊记录与共同上下文，下一轮发言即可看到。"""
        text = (text or "").strip()
        if not text:
            return False
        msg = {"speaker": "用户", "text": text, "ts": time.time(), "user": True}
        with self._lock:
            self._messages.append(msg)
        self._append_space("用户", text)
        self.on_message(msg)
        return True

    # ---------- 主循环 ----------
    def run(self):
        try:
            parts = self.chat.get("participants") or []
            if len(parts) < 2:
                self._finish("至少需要两个成员才能开始群聊")
                return
            self._open_space()
            speaker = random.choice(parts)
            rounds = 0
            max_rounds = int(self.chat.get("max_rounds") or 0)
            fails = 0
            while not self._stop.is_set():
                if max_rounds > 0 and rounds >= max_rounds:
                    self._finish("已达轮数上限（%d 轮）" % max_rounds)
                    return
                if not self._wait_if_paused():
                    break
                ok = self._turn(speaker, first=(rounds == 0))
                rounds += 1
                fails = 0 if ok else fails + 1
                if fails >= MAX_CONSEC_FAIL:
                    self._finish("连续 %d 轮失败，已停止" % fails)
                    return
                if self._stop.is_set():
                    break
                nxt = self._next_speaker(speaker, parts)
                if nxt is None:
                    self._finish("没有可用的下一位成员")
                    return
                speaker = nxt
            self._finish("已结束")
        except Exception as e:      # 线程内异常必须转成可读原因，否则窗口无反馈
            self._finish("群聊异常：%s" % e)

    def _wait_if_paused(self) -> bool:
        """暂停期间阻塞；返回 False 表示等待期间被停止。"""
        while self._pause.is_set() and not self._stop.is_set():
            time.sleep(_PAUSE_POLL)
        return not self._stop.is_set()

    def _finish(self, reason: str):
        self.end_reason = reason
        self._stop.set()
        try:
            self.on_end(reason)
        except Exception:
            pass

    # ---------- 共同上下文 ----------
    def _open_space(self):
        """开启本群聊的共同上下文空间，并用已落盘的历史回填。

        回填是「重开旧群聊继续聊」的关键：成员各自的内存上下文是新的，但共同空间
        能从磁盘记录重建出完整议题脉络，不必让用户重新开场。
        空间的议题种子优先取用户填写的讨论主题（没有主题时退回群聊标题）。
        """
        cid = str(self.chat.get("id") or "")
        try:
            agent_context.set_conversation(cid)
            seed = (str(self.chat.get("topic") or "").strip()
                    or str(self.chat.get("title") or ""))
            self._space = agent_context.open_space(
                "gc_" + cid, seed=seed, owner="群聊", activate=True)
            for m in self._messages:
                text = str(m.get("text") or "").strip()
                if text:
                    agent_context.append(
                        self._space, source=str(m.get("speaker") or "成员"),
                        text=text, kind="note", source_type="message")
            self._seen.clear()
        except Exception:
            self._space = ""

    def _append_space(self, source: str, text: str, kind: str = "note"):
        if not self._space:
            return
        self._in_chat_scope(
            lambda: agent_context.append(self._space, source=source, text=text,
                                         kind=kind, source_type="message"))

    def _in_chat_scope(self, fn):
        """在本群聊自己的对话作用域内执行。

        共同上下文按对话隔离：用户插话发生在**主线程**（面板当前会话的作用域），
        若直接用 `agent_context` 会写到别的作用域里、空间找不到。这里临时切到本群聊
        的 id，执行完复原，保证两个线程读写的都是同一份空间。
        """
        cid = str(self.chat.get("id") or "")
        try:
            prev = agent_context.thread_local_conversation()
        except Exception:
            prev = ""
        try:
            agent_context.set_conversation(cid)
            return fn()
        except Exception:
            return None
        finally:
            try:
                agent_context.set_conversation(prev or "")
            except Exception:
                pass

    def _shared_delta(self, name: str) -> str:
        """本成员上次发言之后新增的共享条目（排除自己的产出，避免自我重复）。"""
        if not self._space:
            return ""
        try:
            items = self._in_chat_scope(lambda: [
                e for e in agent_context.entries(self._space)
                if str(e.get("source") or "") != name]) or []
        except Exception:
            return ""
        seen = int(self._seen.get(name, 0))
        delta = items[seen:] if seen < len(items) else []
        self._seen[name] = len(items)
        if not delta:
            return ""
        return agent_context.render_items(delta)

    # ---------- 单轮 ----------
    def _turn(self, p: dict, first: bool) -> bool:
        name = p.get("name") or "成员"
        if self._stop.is_set():
            return True
        self.on_status("正在准备发言：%s" % name)
        eng = self._engine_for(p)
        # 每轮一个独立停止事件：用户「结束」与成员主动「退出发言」都能立刻中断本轮，
        # 但退出只结束这一轮，不能把整场讨论停掉（共享事件会把整场一起停）。
        self._turn_stop = threading.Event()
        eng._stop = self._turn_stop
        self._exit_requested = False
        # 群聊专属工具：退出发言（只在讨论轮次暴露，主对话不受影响）
        eng.extra_tools = [EXIT_SPEECH_SCHEMA]
        eng.extra_tool_handlers = {"exit_speech": self._on_exit_speech}
        buf = []

        def _delta(s):
            if not s:
                return
            buf.append(s)
            try:
                self.on_delta(name, s)
            except Exception:
                pass

        def _status(s):
            try:
                self.on_status("%s：%s" % (name, s))
            except Exception:
                pass

        def _reason(s):
            if not s:
                return
            try:
                self.on_reasoning(name, s)
            except Exception:
                pass

        eng.on_delta = _delta
        eng.on_status = _status
        eng.on_reasoning = _reason
        # 工具执行前确认：群聊由用户事先授权的策略决定（见 UI 的「工具执行前确认」）
        eng.confirm = self.confirm
        eng.ask_user = self.ask_user
        prompt = self._turn_prompt(p, first=first, shared=self._shared_delta(name))
        self._apply_effort(eng, p, prompt)
        try:
            eng.run(prompt)
        except Exception as e:
            self._last_reply = ""
            self._emit(name, "本轮执行失败：%s" % e, error=True)
            return False
        if self._exit_requested:
            # 成员主动退出：不产出正文，只留一行「跳过」记录，发言权交给下一位
            self._exit_requested = False
            self._last_reply = ""
            self._emit(name, _ui("（无补充，跳过本轮）"), skip=True)
            return True
        text = "".join(buf).strip()
        if self._stop.is_set() and not text:
            return True
        if not text:
            text = "（没有输出）"
        self._emit(name, text)
        self._last_reply = text
        return True

    def _on_exit_speech(self, args: dict) -> dict:
        """成员调用「退出发言」：结束本轮并让发言权流转。"""
        self._exit_requested = True
        self._turn_stop.set()
        reason = str((args or {}).get("reason") or "").strip()
        return {"text": ("[已退出发言] " + reason) if reason else "[已退出发言]",
                "images": []}

    def _emit(self, name: str, text: str, error: bool = False, skip: bool = False,
              kind: str = "note"):
        msg = {"speaker": name, "text": text, "ts": time.time()}
        if error:
            msg["error"] = True
        if skip:
            msg["skip"] = True
        if kind != "note":
            msg["kind"] = kind
        with self._lock:
            self._messages.append(msg)
        if not error and not skip:
            self._append_space(name, text, kind=kind)
        try:
            self.on_message(msg)
        except Exception:
            pass

    def _next_speaker(self, cur: dict, parts: list):
        """下一位发言人：优先沿用本轮 @交接，否则随机（排除自己）。"""
        names = {p["name"] for p in parts}
        hand = _HANDOFF_RE.match(self._last_reply or "")
        if hand:
            target = hand.group(1).strip()
            for p in parts:
                if p["name"] == target and target in names and target != cur["name"]:
                    return p
        pool = [p for p in parts if p["name"] != cur["name"]]
        return random.choice(pool) if pool else None

    # ---------- 提示词 ----------
    def _apply_effort(self, eng, p: dict, text: str = ""):
        """按全局「思考强度 / 思考模式」给该成员的本轮请求装配力度参数。

        与主面板同一套映射（agent_llm.build_effort_params）：
        - 力度直接取全局滑块值（``model.effort``）：群聊是讨论而非任务，没有
          「任务难度」可估，跟随滑块最贴合「跟随全局思考强度」的预期；
        - 思考模式（``model.think_mode``）与「每次强制思考」（``model.force_think``）
          同样生效：强制思考时即便力度低也保持思考。
        模型不支持时该函数自身返回不启用的参数，不会发出非法请求。
        """
        ep = resolve_endpoint(self.cfg, p.get("provider"), p.get("model"))
        cfg = self.cfg or {}
        effort = str(cfg.get("effort") or "medium")
        think = "on" if cfg.get("force_think") else str(cfg.get("think_mode") or "auto")
        if think not in ("on", "off"):
            think = "auto"
        try:
            declared = agent_llm.declared_effort_levels(
                (ep.get("provider") or {}).get("model_limits"), ep["model"])
        except Exception:
            declared = []
        llm = getattr(eng, "llm", None)
        if llm is None:
            return
        try:
            llm.effort_params = agent_llm.build_effort_params(
                ep["model"], effort, think, declared_levels=declared)
        except Exception:
            llm.effort_params = {}

    def _engine_for(self, p: dict):
        ep = resolve_endpoint(self.cfg, p.get("provider"), p.get("model"))
        sig = (ep["base_url"], ep["model"], ep["api_key"], ep["protocol"])
        cached = self._engines.get(p["name"])
        if cached and cached[1] == sig:
            return cached[0]
        client = agent_llm.LLMClient(
            base_url=ep["base_url"], api_key=ep["api_key"], model=ep["model"],
            protocol=ep["protocol"])
        client.auto_fallback = True
        client.silent_fallback = True
        # 上下文窗口按**该成员自己的模型**解析：不同成员可以是不同窗口、不同压缩节奏，
        # 互不干扰（引擎按窗口自行压缩，压缩只影响本成员的私有历史）。
        try:
            ctx = agent_llm.resolve_context(ep["base_url"], ep["model"],
                                            provider=ep.get("provider") or {})
        except Exception:
            ctx = {}
        eng = agent_engine.AgentEngine(
            client,
            text_only=True,
            memory_enabled=False,
            # 群聊不是任务流：跳过「开发规则确认/技能路由硬拦截」，让成员直接开口
            direct=True,
            # 高效模式：只发最小工具集与精简提示词 —— 群聊每轮都重发 system，
            # 全套工具手册会让 token 成本翻几倍，而群聊真正需要的只有文件/命令/检索
            efficient_mode=True,
            allow_subagents=False,
            persona=self._system_persona(p),
            conversation=str(self.chat.get("id") or ""),
            context_window=ctx.get("window"),
            max_output_tokens=ctx.get("max_output") or None,
            long_context_1m=bool(ctx.get("long_1m")),
            window_source=str(ctx.get("source") or ""),
            # 共同上下文由本调度层做「成员增量注入」：引擎侧再注一次整份快照会重复，
            # 且快照留在历史里会让 token 随轮数平方增长
            shared_snapshot=False)
        self._engines[p["name"]] = (eng, sig)
        return eng

    def _system_persona(self, p: dict) -> str:
        name = p.get("name") or "成员"
        others = [x["name"] for x in (self.chat.get("participants") or [])
                  if x.get("name") != name]
        base = agent_prompt.compose(p.get("persona") or "", p.get("system_prompt") or "")
        if not base:
            base = ("你是群聊成员「%s」，一位有独立判断力的讨论者：观点明确、"
                    "有依据、不迎合。" % name)
        rules = (
            "\n\n【群聊规则】\n"
            "1. 你是群聊成员「%s」，其他成员：%s。\n"
            "2. 只表达你自己的观点与依据；不要替别人发言，不要写旁白、舞台说明或"
            "「某某说：」这类转述。\n"
            "3. 每次发言 1-3 段、不超过 200 字；不复述别人已说过的内容，直接推进讨论。\n"
            "4. 若想让特定成员接着发言，在回复第一行单独写「@成员名」；不写则由系统随机指定。\n"
            "5. 你们共享同一个共同上下文空间：别人的发言会作为「新增上下文」提供给你，"
            "你的发言也会写入其中，因此不必重复别人的结论。\n"
            "6. 这是群聊而非任务执行：默认不调用工具，只有确实需要核实事实/读文件时才调用，"
            "且不要向用户请示。\n"
            "7. **正文禁止输出执行计划**：不要写步骤清单、「我先…然后…」式的流程说明或"
            "待办列表；需要规划就在思考过程中完成，正文只给结论与论据。\n"
            "8. 本轮确实没有新观点时，调用 exit_speech 工具退出发言，"
            "不要为凑字数重复别人或输出空话。\n"
            % (name, "、".join(others) or "（无）"))
        return base + rules

    def _turn_prompt(self, p: dict, first: bool, shared: str = "") -> str:
        name = p.get("name") or "成员"
        topic = str(self.chat.get("topic") or "").strip()
        if first:
            head = ("讨论开始，请你围绕上述主题先说第一句。" if topic
                    else "讨论开始，请你先说第一句。")
        else:
            head = ("现在轮到你（%s）发言，请紧扣主题。" % name if topic
                    else "现在轮到你（%s）发言。" % name)
        ctx = (shared or "").strip()
        if ctx:
            ctx_block = ("【新增上下文】（其他人自你上次发言以来的内容，按时间顺序）：\n"
                         + ctx + "\n\n")
        elif first:
            ctx_block = ""
        else:
            ctx_block = "【新增上下文】（暂无新内容，你可以主动起一个话题或回应上一位）\n\n"
        # 主题每轮都随消息重发：长对话里模型会逐渐跑题，system 里的约束压不住
        topic_block = ("【讨论主题】%s\n\n" % topic) if topic else ""
        return (
            topic_block + ctx_block
            + "【本轮指令】%s\n要求：直接给出你的发言正文（可以先用一行 @成员名 指定下一位），"
            "不要输出「好的」「收到」这类应答语，不要重复已有发言。"
            % head)

    # ---------- 指定成员总结 ----------
    def summarize(self, member_name: str, on_delta=None) -> dict:
        """让指定成员对整场讨论做总结，返回并广播一条 ``kind="summary"`` 的消息。

        独立于讨论循环调用（UI 在未开始/已结束时触发，此时另起一个 runner 实例）：
        - 总结者用**新引擎**（不带讨论期的私有历史），输入是**完整**共同上下文空间
          （不截断）—— 覆盖所有成员的发言，避免被自己的历史偏见带偏；
        - 窗口与思考强度同样取全局设置（与讨论轮次一致）；
        - 总结不会写回空间：它是结论产物，不是讨论内容，否则会被后续成员当成发言引用。
        """
        p = next((x for x in (self.chat.get("participants") or [])
                  if x.get("name") == member_name), None)
        if p is None:
            return {}
        if not self._space:
            self._open_space()
        name = p.get("name") or "成员"
        self.on_status("正在生成总结：%s" % name)
        eng = self._engine_for(p)
        self._turn_stop = threading.Event()
        eng._stop = self._turn_stop
        eng.extra_tools = []            # 总结不参与发言权流转
        eng.extra_tool_handlers = {}
        buf = []

        def _delta(s):
            if not s:
                return
            buf.append(s)
            try:
                on_delta(name, s)
            except Exception:
                pass

        def _reason(s):
            if s:
                try:
                    self.on_reasoning(name, s)
                except Exception:
                    pass

        def _status(s):
            try:
                self.on_status("%s：%s" % (name, s))
            except Exception:
                pass

        eng.on_delta = _delta
        eng.on_reasoning = _reason
        eng.on_status = _status
        prompt = self._summary_prompt()
        self._apply_effort(eng, p, prompt)
        try:
            eng.run(prompt)
        except Exception as e:
            text = "总结失败：%s" % e
            msg = {"speaker": name, "text": text, "ts": time.time(),
                   "kind": "summary", "error": True}
            with self._lock:
                self._messages.append(msg)
            try:
                self.on_summary(msg)
            except Exception:
                pass
            return msg
        text = "".join(buf).strip() or "（未产出总结）"
        msg = {"speaker": name, "text": text, "ts": time.time(), "kind": "summary"}
        with self._lock:
            self._messages.append(msg)
        try:
            self.on_summary(msg)
        except Exception:
            pass
        return msg

    def _summary_prompt(self) -> str:
        """总结提示词：完整空间 + 固定三段式输出，明确禁止计划式正文与再发起讨论。"""
        space = ""
        if self._space:
            space = self._in_chat_scope(
                lambda: agent_context.render(self._space, max_chars=0)) or ""
        topic = str(self.chat.get("topic") or "").strip()
        head = ("【讨论总结】请把本群%s的讨论整理成可直接阅读的结论。"
                % (("围绕「%s」" % topic) if topic else ""))
        return (
            head + "\n\n"
            + (space.strip() or "（本群暂无发言记录）") + "\n\n"
            "【输出要求】\n"
            "1. 先用一句话给出结论；\n"
            "2. 再列主要分歧与各方立场（按成员名归类，没有分歧就写「无实质分歧」）；\n"
            "3. 最后给 1-3 条可执行的下一步。\n"
            "只输出总结正文：不要再提问、不要 @ 成员、不要复述原文，"
            "也不要输出执行计划或思考步骤。")
