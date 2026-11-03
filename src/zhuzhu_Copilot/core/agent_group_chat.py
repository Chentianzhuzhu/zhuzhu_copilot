"""AI 群聊：多个 Agent 在同一段对话里轮流发言。

设计要点（与单 Agent 会话完全隔离，不改动主面板的会话状态机）：

- **成员独立**：每个成员各自持有 名称 / 性格 / 系统级提示词 / 模型（服务商 + 模型名）。
  成员配置是本模块自己的数据（``group_chats/<id>.json``），与设置页的主 Agent 配置
  互不影响；设置页的「全局规则」仍由引擎统一追加，对每个成员都生效。
- **发言权**：首轮随机挑一位（这就是「系统随机发一条可以开始聊天的命令」）；之后
  发言人可在回复首行写 ``@成员名`` 指定下一位，未指定则随机（排除自己，避免连说）。
- **工具能力**：每个成员用 ``AgentEngine`` 跑一轮（高效模式工具集：文件 / 命令 /
  检索 / 联网），因此群聊不仅能聊，也能真的读文件、查资料、协作干活。
- **上下文**：每轮把「最近群聊记录」作为本轮 user 消息注入，引擎自身的 ``_messages``
  每轮重置 —— 共享的是群聊记录，而不是各自的思维链，token 随轮数线性增长而非平方。
- **终止**：用户随时可停（``stop()`` 置停止事件，当前轮结束后线程退出）；另有轮数
  上限兜底，避免多个 Agent 互相客套把额度烧光。

存储：``~/.zhuzhu_Copilot/group_chats/<id>.json``（配置 + 聊天记录一并落盘）。
"""

import json
import random
import re
import threading
import time

from zhuzhu_Copilot import app_identity
from zhuzhu_Copilot.core import agent_engine, agent_llm, agent_prompt

#: 群聊记录注入本轮上下文的预算：超出后只保留最近的若干条（按字符截断）
TRANSCRIPT_BUDGET = 7000
_PER_MSG_LIMIT = 900
#: 连续失败上限：达到后停止群聊，避免同一错误（余额/鉴权）被反复重试
MAX_CONSEC_FAIL = 3
#: 默认轮数上限（0 = 不限）；可在窗口里调整
DEFAULT_MAX_ROUNDS = 30

_ID_RE = re.compile(r"^g_[0-9a-zA-Z_-]{6,40}$")
_HANDOFF_RE = re.compile(r"^\s*@([^\s@，,：:]{1,32})")


def chats_root():
    root = app_identity.data_root() / "group_chats"
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return root


def _path(cid: str):
    return chats_root() / f"{cid}.json"


def new_chat(participants: list = None, title: str = "", max_rounds: int = None) -> dict:
    """新建一份群聊配置（未落盘）。"""
    now = time.time()
    return {
        "id": "g_%d_%d" % (int(now), random.randint(100, 999)),
        "title": (title or "AI 群聊").strip(),
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
    }


def normalize_chat(d: dict) -> dict:
    d = d or {}
    out = new_chat(max_rounds=d.get("max_rounds", DEFAULT_MAX_ROUNDS))
    out.update({k: d[k] for k in ("id", "title", "created", "updated") if d.get(k)})
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
        _path(cid).unlink(missing_ok=True)
        return True, "已删除"
    except OSError as e:
        return False, f"删除失败: {e}"


def resolve_endpoint(cfg: dict, provider: str = "", model: str = "") -> dict:
    """成员模型 → 实际连接参数（base_url / api_key / protocol / model）。

    未指定模型时回落到用户当前的自动选择；服务商优先按名称精确匹配，
    失配时按模型名回退（与主面板路由同一套逻辑，避免同名模型串号）。
    """
    providers = (cfg or {}).get("providers") or []
    m = (model or "").strip()
    if not m:
        try:
            m = agent_llm.resolve_model(cfg, "medium")
        except Exception:
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
    }


class GroupChatRunner(threading.Thread):
    """群聊调度线程：一轮一位发言人，直到用户停止 / 达到轮数上限 / 连续失败。

    回调都在本线程触发，UI 侧必须自行 marshal 到主线程（信号或 QMetaObject）。
    - on_status(text)            状态行（"正在准备…"、"小明 正在发言"）
    - on_delta(name, text)       流式增量
    - on_message(dict)           一条完整消息 {"speaker","text","ts"}
    - on_end(reason)             结束（reason 为可直接展示的中文原因）
    - confirm(name, args) -> bool 工具执行前确认（默认放行）
    - ask_user(args) -> str      Agent 反问用户时的回复（群聊里不应阻塞用户）
    """

    def __init__(self, chat: dict, llm_cfg: dict,
                 on_status=None, on_delta=None, on_message=None, on_end=None,
                 confirm=None, ask_user=None):
        super().__init__(daemon=True)
        self.chat = chat
        self.cfg = llm_cfg or {}
        self.on_status = on_status or (lambda _t: None)
        self.on_delta = on_delta or (lambda _n, _t: None)
        self.on_message = on_message or (lambda _m: None)
        self.on_end = on_end or (lambda _r: None)
        self.confirm = confirm or (lambda _n, _a: True)
        self.ask_user = ask_user or (lambda _a: "")
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._messages = list(chat.get("messages") or [])
        self._engines = {}          # 成员名 → (引擎, 端点签名)：模型变更才重建
        self._last_reply = ""
        self.end_reason = ""

    # ---------- 外部控制 ----------
    def stop(self):
        self._stop.set()

    def say(self, text: str) -> bool:
        """用户插话：写入群聊记录，下一轮发言即可看到。"""
        text = (text or "").strip()
        if not text:
            return False
        msg = {"speaker": "用户", "text": text, "ts": time.time(), "user": True}
        with self._lock:
            self._messages.append(msg)
        self.on_message(msg)
        return True

    # ---------- 主循环 ----------
    def run(self):
        try:
            parts = self.chat.get("participants") or []
            if len(parts) < 2:
                self._finish("至少需要两个成员才能开始群聊")
                return
            speaker = random.choice(parts)
            rounds = 0
            max_rounds = int(self.chat.get("max_rounds") or 0)
            fails = 0
            while not self._stop.is_set():
                if max_rounds > 0 and rounds >= max_rounds:
                    self._finish("已达轮数上限（%d 轮）" % max_rounds)
                    return
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

    def _finish(self, reason: str):
        self.end_reason = reason
        self._stop.set()
        try:
            self.on_end(reason)
        except Exception:
            pass

    # ---------- 单轮 ----------
    def _turn(self, p: dict, first: bool) -> bool:
        name = p.get("name") or "成员"
        if self._stop.is_set():
            return True
        self.on_status("正在准备发言：%s" % name)
        eng = self._engine_for(p)
        eng._messages = []          # 每轮独立：共享的是群聊记录，不是各自的对话史
        # 停止事件共用：用户点「结束」时，正在执行的这一轮也能立刻中断，
        # 而不是等它把工具跑完（引擎内部全程轮询 _stop）。
        eng._stop = self._stop
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

        eng.on_delta = _delta
        eng.on_status = _status
        # 工具执行前确认：群聊由用户事先授权的策略决定（见 UI 的「工具执行前确认」）
        eng.confirm = self.confirm
        eng.ask_user = self.ask_user
        prompt = self._turn_prompt(p, first=first)
        try:
            eng.run(prompt)
        except Exception as e:
            self._last_reply = ""
            text = "（本轮执行失败：%s）" % e
            self._emit(name, text)
            return False
        text = "".join(buf).strip()
        if self._stop.is_set() and not text:
            return True
        if not text:
            text = "（没有输出）"
        self._emit(name, text)
        self._last_reply = text
        return True

    def _emit(self, name: str, text: str):
        msg = {"speaker": name, "text": text, "ts": time.time()}
        with self._lock:
            self._messages.append(msg)
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
            conversation=str(self.chat.get("id") or ""))
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
            "5. 这是群聊而非任务执行：默认不调用工具，只有确实需要核实事实/读文件时才调用，"
            "且不要向用户请示。\n"
            % (name, "、".join(others) or "（无）"))
        return base + rules

    def _transcript(self) -> str:
        with self._lock:
            msgs = list(self._messages)
        if not msgs:
            return "（暂无发言）"
        lines, total = [], 0
        for m in reversed(msgs):
            text = str(m.get("text") or "").strip()
            if len(text) > _PER_MSG_LIMIT:
                text = text[:_PER_MSG_LIMIT] + "…"
            line = "[%s] %s" % (m.get("speaker") or "?", text)
            if total + len(line) > TRANSCRIPT_BUDGET:
                break
            lines.append(line)
            total += len(line)
        lines.reverse()
        return "\n".join(lines)

    def _turn_prompt(self, p: dict, first: bool) -> str:
        name = p.get("name") or "成员"
        head = ("讨论开始，请你先说第一句。" if first
                else "现在轮到你（%s）发言。" % name)
        return (
            "【群聊记录】\n%s\n\n"
            "【本轮指令】%s\n要求：直接给出你的发言正文（可以先用一行 @成员名 指定下一位），"
            "不要输出「好的」「收到」这类应答语，不要重复群聊记录。"
            % (self._transcript(), head))
