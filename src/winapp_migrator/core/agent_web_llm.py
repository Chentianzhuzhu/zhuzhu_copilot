"""DeepSeek 网页版接入：CDP 浏览器桥 + 本地 OpenAI 兼容代理。

PoC 实证（2026-08-30）：网页版接口需要页面 JS 动态生成的 PoW 解与 x-hif-* 签名，
外部直连恒被拒；唯一可行路径是驱动页面自身发送流程（真实键入+回车），从 DOM
读回流式渲染的回复文本。本模块把该流程封装为 OpenAI 兼容的本地代理，引擎零改动。

实现要点：
- 浏览器即凭证：登录态持久化在 ~/.winapp_migrator/browser_profile，无额外凭证文件
- 每次对话前 reload 首页新建会话，保证上下文干净（历史由上层拼接进 prompt）
- 全标准库实现，兼容 Cython 打包
"""
import json
import random
import re
import threading
import time
import uuid
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from winapp_migrator.core import agent_browser

WEB_KIND = "deepseek_web"
# 对外暴露的三档模型：快速 / 专家 / 视图（映射到网页版两开关）
WEB_MODES = {
    "deepseek-chat":         {"thinking": False, "search": False, "title": "快速模式"},
    "deepseek-chat-quick":   {"thinking": False, "search": False, "title": "快速模式"},
    "deepseek-chat-expert":  {"thinking": True,  "search": False, "title": "专家模式"},
    "deepseek-chat-view":    {"thinking": False, "search": True,  "title": "视图模式"},
}
MODELS = ["deepseek-chat-quick", "deepseek-chat-expert", "deepseek-chat-view"]
MODEL_FREE_DESC = "DeepSeek 网页版（免费）"

# 网页版行为可配置项（页面结构/输入框限制变化时只改此处，不碰逻辑）
_CFG = {
    # 完成判定兜底：文本静止多少轮（每轮 0.5s）后判定完成（无 DONE 强信号时）。
    # 长回复 markdown 按块渲染，块间停顿可达数秒——阈值过短会提前收尾截断
    # 回复（含末尾的工具调用 JSON），导致大型任务流中断。
    "idle_done_ticks": 30,          # 15 秒
    # 网页版输入框长度上限防护：prompt 超过该字符数时保留头部（系统指令+工具
    # 清单）+ 尾部最近消息，中段截断。大型任务流多轮后历史会增长到数万字符。
    "max_prompt_chars": 24000,
    "max_prompt_head_chars": 4000,
}

# 思考期文本标记（专家模式：页面显示「深度思考」卡片，思考内容滚动渲染，
# 但正式回复尚未产出）≠ 模型回复。ask() 中在页面未 DONE（操作栏未出现）时
# 将含这些词的提取文本置空，避免思考内容被当作回复输出/流式显示；
# 页面 DONE 后视为正式回复（正文中提及这些词属正常回答）。
_THINKING_RE = re.compile(r"深度思考|正在思考|思考中")

# 回复尾部 UI 杂音标记（页面固定在回复区的标签/免责声明），清洗时按行剔除
UI_NOISE_MARKS = [
    "内容由 AI 生成，请仔细甄别",
    "内容由AI生成，请仔细甄别",
    "深度思考",
    "智能搜索",
]

# 空会话欢迎语（DeepSeek 新版随机变体）：提取到这些文本 ≠ 模型回复，
# 说明消息未发送或回复未渲染，轮询时应视为「尚未回复」继续等待
WELCOME_MARKS = (
    "想从哪里开始", "今天想聊些什么", "随时开始吧", "开始聊天吧",
    "有什么我能帮你的吗", "想聊点什么", "想聊什么", "我能帮你做什么",
    "我能帮什么忙吗", "我能帮上什么忙", "我能为您做什么",
    "使用快速模式开始对话", "使用专家模式开始对话",
    "使用识图模式开始对话", "使用试图模式开始对话", "使用视图模式开始对话",
)

_BRIDGE = None
_LOCK = threading.Lock()
_PROXY = None               # 本地中转代理服务器实例（start_proxy/stop_proxy 管理）

# ---------- 调试日志（诊断工具链路：请求入参 / 模型原始回复尾段）----------
# 固定写到用户目录，文本小、频率低；任何异常均静默，绝不影响主流程。
_DEBUG_LOG = Path.home() / ".winapp_migrator" / "web_llm_debug.log"
_DEBUG_MAX = 5 * 1024 * 1024


def _dbg(msg: str):
    try:
        with _LOCK:
            if _DEBUG_LOG.exists() and _DEBUG_LOG.stat().st_size > _DEBUG_MAX:
                _DEBUG_LOG.write_text("", encoding="utf-8")   # 超限轮转清空
            with open(_DEBUG_LOG, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass


# ---------- 纯函数 ----------
# 文本协议工具调用规则说明（注入 prompt 顶部；可配置，需保持 JSON 协议一致）
_TOOL_PROTOCOL_RULE = (
    "\n\n[工具调用规则]\n"
    "你可以调用以下工具完成任务：\n"
    "__TOOLS__\n"
    "如果任务需要调用工具，请仅输出一行 JSON（不要附加任何其它文字、不要用代码块围栏，"
    "不要使用 Action / Action Parameters / Action Input 等旧式写法）：\n"
    '{"tool": "工具名", "arguments": {"参数名": 值}}\n'
    "所有参数必须放在 arguments 字段内（不要平铺在顶层）；收到工具结果后继续作答。\n"
)


def _format_tools(tools: list) -> str:
    """把 OpenAI tools 简化为可读清单，注入 prompt 供模型识别。"""
    rows = []
    for t in tools or []:
        fn = (t.get("function") or {}) if isinstance(t, dict) else {}
        name = str(fn.get("name") or "").strip()
        if not name:
            continue
        desc = str(fn.get("description") or "").replace("\n", " ")[:200]
        params = fn.get("parameters") or {}
        props = params.get("properties") or {}
        req = params.get("required") or []
        args = ", ".join(f"{k}:{v.get('type', '')}" for k, v in (props or {}).items())
        rows.append(f"- {name}: {desc}（参数：{args or '无'}；必填：{', '.join(req) or '无'}）")
    return "\n".join(rows) if rows else "（无可用工具）"


def serialize_prompt(messages: list, tools: list = None) -> str:
    """把 OpenAI messages 序列化为单段 prompt（网页版无角色数组，只接受一个 prompt）。

    tools 非空时在 prompt 顶部注入工具清单与调用协议；工具回执消息（assistant
    tool_calls / tool）转为文本对话段落，多模态 content 数组只取文本段。
    """
    lines = []
    if tools:
        lines.append(_TOOL_PROTOCOL_RULE.replace("__TOOLS__", _format_tools(tools)).strip())
    for m in messages or []:
        role = str(m.get("role") or "").lower()
        content = m.get("content") or ""
        if isinstance(content, list):        # OpenAI 多模态 content 数组
            content = " ".join(
                str(x.get("text") or "") for x in content
                if isinstance(x, dict) and x.get("type") == "text")
        content = str(content).strip()
        if role == "tool":
            lines.append(f"[工具结果]\n{content}")          # 工具执行结果注入
            continue
        if role == "assistant" and m.get("tool_calls"):
            if content:
                lines.append(f"[助手]\n{content}")
            for tc in m["tool_calls"] or []:
                fn = (tc.get("function") or {}) if isinstance(tc, dict) else {}
                lines.append(f"[助手调用工具] {fn.get('name', '')}({fn.get('arguments', '')})")
            continue
        if role == "system":
            lines.append(f"[系统指令]\n{content}")
        elif role == "assistant":
            lines.append(f"[助手]\n{content}")
        else:
            lines.append(f"[用户]\n{content}")
    text = "\n\n".join(lines)
    # 超长保护：网页版输入框有长度上限，大型任务流多轮后全历史重放会超限，
    # 导致键入/发送失败、任务流中断。裁剪策略：保留头部（系统指令+工具清单）
    # 与尾部（最近消息），中段历史截断并标注。阈值可配置（_CFG）。
    max_chars = _CFG["max_prompt_chars"]
    if len(text) > max_chars:
        head_chars = _CFG["max_prompt_head_chars"]
        gap = "\n\n……（历史过长已截断：保留系统指令/工具清单与最近上下文）……\n\n"
        text = text[:head_chars] + gap + text[-(max_chars - head_chars - len(gap)):]
    return text


# JSON 转义白名单：仅保留几乎不会出现在 Windows 路径字母前的转义。
# 故意排除 t/n/r/f/b —— 这些字母常出现在路径里（C:\test、C:\node_modules），
# 模型写路径时本意是字面反斜杠+字母（非法 JSON），若当合法转义（tab/换行）会
# 解析出错误字符、破坏路径。模型若真想表达换行/制表符会用真实字符（合法 JSON
# 首次解析即过，不进入本修复分支）。
_JSON_ESCAPE_OK = set('"\\/u')

# 旧式 ReAct 输出标记（网页版模型训练先验，实测会绕过严格 JSON 协议而输出：
#   Action: update_todo
#   Action Parameters: {"todos": [...]}
# 或 Action Input: {...}；解析端做兼容回退，白名单过滤避免误判。）
_REACT_NAME = re.compile(
    r"(?im)^[ \t]*(?:Action|Tool|工具)[ \t]*[:：][ \t]*([A-Za-z_][A-Za-z0-9_-]*)[ \t]*$")
_REACT_PARAMS = re.compile(
    r"(?im)^[ \t]*(?:Action Parameters|Action Input|Tool Parameters|Tool Input|"
    r"Action 参数|Action 输入|工具参数|工具输入|参数|Parameters|Input|Arguments)"
    r"[ \t]*[:：]")


def _coerce_args(d: dict) -> dict:
    """把模型 JSON 对象的参数规范为 arguments dict。

    兼容嵌套 arguments / 误用 parameters 键 / 顶层平铺（除 tool 外全并入）。"""
    args = d.get("arguments")
    if not isinstance(args, dict):
        params = d.get("parameters")
        if isinstance(params, dict):
            args = params
        else:
            args = {kk: vv for kk, vv in d.items()
                    if kk not in ("tool", "action", "Action")}
    return args if isinstance(args, dict) else {}


def _parse_json_obj(seg: str):
    """在段内定位并解析第一个完整 JSON 对象。成功返回 dict，失败返回 None。"""
    j = seg.find("{")
    while j != -1:
        depth, in_str, esc, k = 0, False, False, j
        while k < len(seg):
            c = seg[k]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            else:
                if c == '"':
                    in_str = True
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        raw = seg[j:k + 1]
                        try:
                            return json.loads(raw)
                        except Exception:
                            try:
                                return json.loads(_fix_json_backslash(raw))
                            except Exception:
                                break
            k += 1
        j = seg.find("{", j + 1)
    return None


def _parse_json_at(text: str, j: int):
    """从位置 j（必须是 '{'）开始配平解析一个 JSON 对象。成功返回 dict，失败 None。"""
    depth, in_str, esc, k = 0, False, False, j
    while k < len(text):
        c = text[k]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    raw = text[j:k + 1]
                    try:
                        return json.loads(raw)
                    except Exception:
                        try:
                            return json.loads(_fix_json_backslash(raw))
                        except Exception:
                            return None
        k += 1
    return None


def _react_call(text: str, allowed):
    """旧式 ReAct 输出（Action: name + Action Parameters/Input: {json}）。

    仅 name 命中白名单才接受；返回 {"start", "name", "arguments"} 或 None。"""
    heads = list(_REACT_NAME.finditer(text))
    am = set(allowed or [])
    for idx, m in enumerate(heads):
        name = m.group(1).strip()
        if name not in am:
            continue
        seg_start = m.end()
        seg_end = heads[idx + 1].start() if idx + 1 < len(heads) else len(text)
        seg = text[seg_start:seg_end]
        pm = _REACT_PARAMS.search(seg)
        if pm:
            seg = seg[pm.end():]
        d = _parse_json_obj(seg)
        if d is None:
            continue
        args = _coerce_args(d)
        jname = str(d.get("tool") or d.get("action") or "").strip()
        if jname and jname in am:
            name = jname
        return {"start": m.start(), "name": name, "arguments": args}
    return None


# 「[调用工具] 工具名{...}」「调用工具：工具名 {...}」等标记（模型实测变体）
_CALL_MARKER = re.compile(
    r"(?:[\[【]\s*)?(?:调用工具|工具调用|调用|工具|Tool|tool)\s*[\]】]?\s*[:：]?\s*$")
# 带标记的工具名（不必在白名单内）：臆造工具名也要回传，让引擎回「未知工具」
# 提示模型自我纠正，而不是静默当作正文导致任务提前结束。
_CALL_MARKER_HEAD = re.compile(
    r"(?im)(?:[\[【]\s*(?:调用工具|工具调用|工具|Tool|tool)\s*[\]】]?\s*[:：]?\s*"
    r"|\[?(?:调用工具|工具调用|调用|工具|Tool|tool)\s*[:：]\s*)"
    r"([A-Za-z_][A-Za-z0-9_-]*)[ \t]*[（(]?[\s]*\{")


def _named_json_call(text: str, allowed):
    """「标记 + 工具名 + JSON 参数」输出（如 [调用工具] explore_project{"directory": "..."}）。

    命中两种来源：① 白名单工具名后紧跟 JSON（模型可能省略标记也直接写）；
    ② 带「调用工具」标记的工具名（即便不在白名单，也回传交给引擎提示纠正）。
    返回 {"start","name","arguments"}（取最早出现者）。"""
    cands = []
    if allowed:
        alt = "|".join(re.escape(n) for n in sorted(set(allowed), key=len, reverse=True))
        if alt:
            for m in re.finditer(
                    r"(?m)(?<![A-Za-z0-9_-])(" + alt + r")[ \t]*[（(]?[\s]*\{", text):
                cands.append(_mk_call(text, m, m.group(1)))
    for m in _CALL_MARKER_HEAD.finditer(text):
        cands.append(_mk_call(text, m, m.group(1)))
    cands = [c for c in cands if c]
    return min(cands, key=lambda x: x["start"]) if cands else None


def _mk_call(text: str, m, name: str):
    """按匹配位置解析紧跟其后的 JSON，组装调用 dict（解析失败返回 None）。"""
    j = text.rfind("{", 0, m.end())
    if j < 0:
        return None
    d = _parse_json_at(text, j)
    if d is None:
        return None
    start = m.start()
    mm = _CALL_MARKER.search(text[:start])
    if mm:                      # 起点前移到「[调用工具]」等标记处（不向用户显示标记）
        start = mm.start()
    return {"start": start, "name": name, "arguments": _coerce_args(d)}


def _strict_json_call(text: str):
    """严格协议 {"tool": ..., "arguments": {...}}：返回 {"start","name","arguments"}。"""
    i = text.find('"tool"')
    while i != -1:
        j = text.rfind("{", 0, i)
        if j != -1:
            d = _parse_json_at(text, j)
            if d is not None:
                name = str(d.get("tool") or "").strip()
                if name:
                    return {"start": j, "name": name, "arguments": _coerce_args(d)}
        i = text.find('"tool"', i + 1)
    return None


def _find_call(text: str, allowed: list = None):
    """统一的工具调用定位：严格 JSON / [调用工具] 标记 / 旧式 ReAct，取最早出现者。"""
    if not text:
        return None
    cands = []
    c = _strict_json_call(text)
    if c:
        cands.append(c)
    c = _named_json_call(text, allowed)      # 白名单名 / 带调用标记（含臆造名）
    if c:
        cands.append(c)
    if allowed:
        c = _react_call(text, allowed)
        if c:
            cands.append(c)
    elif _REACT_NAME.search(text):
        c = _react_call(text, _REACT_NAME.findall(text))
        if c:
            cands.append(c)
    return min(cands, key=lambda x: x["start"]) if cands else None


def _fix_json_backslash(s: str) -> str:
    """修复模型输出的非法 JSON 转义：Windows 路径（如 C:\\Users）里的反斜杠
    未按 JSON 规范写成双反斜杠，json.loads 直接抛错。把反斜杠后跟非合法
    转义字符的组合双写为 \\\\（仅影响非法序列，合法转义原样保留）。"""
    out, i, n = [], 0, len(s)
    while i < n:
        c = s[i]
        if c == "\\" and i + 1 < n and s[i + 1] not in _JSON_ESCAPE_OK:
            out.append("\\\\")
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _artifact_start(text: str, allowed: list = None) -> int:
    """定位回复中「工具调用尾巴」的最早起点（用于流式转发时抑制该段）。

    复用 _find_call 的统一识别（严格 JSON / [调用工具] 标记 / 旧式 ReAct），
    并额外纳入「工具头行」的早期形态（Action: 行、代码块围栏后接 JSON 等），
    使流式阶段就能在 JSON 尚未写全时锁定起点。无发现返回 -1。"""
    if not text:
        return -1
    c = _find_call(text, allowed)
    start = c["start"] if c else -1
    if c and c["start"] > 0:
        # JSON 前若紧跟代码围栏（```json），起点前移到围栏行，避免围栏字符外泄
        k = text.rfind("\n", 0, c["start"])
        line_start = k + 1 if k != -1 else 0
        if text[line_start:c["start"]].strip().startswith("```"):
            start = line_start
    # 尚未成形的 Action 行（名字未写完/参数未到）也先锁存起点
    for m in _REACT_NAME.finditer(text):
        if allowed is None or m.group(1) in set(allowed or []):
            if start == -1 or m.start() < start:
                start = m.start()
            break
    return start


class _BodyStreamer:
    """流式转发器：把网页版逐段渲染的正文实时转发给 SSE 客户端，同时抑制
    「工具调用尾巴」（正文结束后模型输出的 Action:/JSON 调用块）不让用户看到
    原始 JSON，等整条回复收完后由调用方解析为正式 tool_calls。

    实时判定（每次增量）：
    - 在未发出区找「工具头」：已闭合行的完整 Action: name 行 / 含 "tool" 的
      JSON 对象起点 / 当前开口行以 Action: 或 { 开头（部分输入也算）；
    - 找到 → 只发工具头之前的正文，之后全部锁存缓冲（不向 UI 漏出调用 JSON）；
    - 找不到 → 整段正文立即实时发出（正文无需等段落收尾，延迟最小）。
    finish() 收到完整回复后用 _artifact_start 复核并补发残留正文。
    """
    _HEAD_PREFIX = re.compile(r"^\s*(?:Action|Tool|工具)\s*[:：]\s*")
    _JSON_PREFIX = re.compile(r"^\s*\{[^{}]*$")

    def __init__(self, on_emit, allowed: list = None):
        self.on_emit = on_emit
        self.allowed = allowed
        self.cum = ""          # 已接收的全部文本
        self.flushed = 0       # 已实时发出的位置
        self._latched = False  # 命中工具头后锁存
        self.artifact = False  # finish 后是否确认含工具调用尾巴

    def snapshot(self, full: str):
        """完整快照更新（ask 每次文本变化时回调）。"""
        if full is None:
            return
        if self.cum and full.startswith(self.cum):
            self.cum = full                       # 正常增长
        else:
            self.cum = full                       # 页面重渲染/整块替换：重建
            self.flushed = 0
            self._latched = False
        self._forward()

    def _head_at(self, C: str) -> int:
        """在未发出区内找工具头起点（返回索引或 -1）。"""
        if self._latched:
            return self.flushed
        # 1) 已闭合行的完整 Action: name 行
        for m in _REACT_NAME.finditer(C):
            if m.start() >= self.flushed:
                if self.allowed is None or m.group(1) in self.allowed:
                    return m.start()
        # 2) 含 "tool" 键的 JSON 对象起点（严格协议；与解析器一致的起点定位）
        i = C.find('"tool"', self.flushed)
        while i != -1:
            j = C.rfind("{", self.flushed, i)
            if j >= self.flushed:
                return j
            i = C.find('"tool"', i + 1)
        # 3) 当前开口行（最后一个换行之后）以 Action: 或 { 开头 → 可能是工具头
        tail = C[self.flushed:]
        nl = tail.rfind("\n")
        open_line = tail[nl + 1:] if nl != -1 else tail
        if self._HEAD_PREFIX.match(open_line) or self._JSON_PREFIX.match(open_line):
            return self.flushed + (nl + 1 if nl != -1 else 0)
        return -1

    def _forward(self):
        C = self.cum
        s = self._head_at(C)
        if s >= self.flushed:
            if s > self.flushed:
                seg = C[self.flushed:s]
                if seg and self.on_emit:
                    self.on_emit(seg)
            self.flushed = s
            self._latched = True
            return
        # 无工具头：整段实时发出
        if len(C) > self.flushed:
            seg = C[self.flushed:]
            self.flushed = len(C)
            if seg and self.on_emit:
                self.on_emit(seg)

    def finish(self, raw: str = None, found: bool = None) -> bool:
        """整条回复收尾：raw 为解析用完整文本（默认 self.cum）；
        found 为调用方工具解析结果（True=确有工具调用，False=无）。
        返回是否确认存在工具调用尾巴（已抑制、未作为正文发出）。"""
        if raw is not None:
            self.cum = raw
            if self.flushed > len(self.cum):   # 尾部杂音清洗导致文本变短
                self.flushed = len(self.cum)
        C = self.cum or ""
        if found is False:
            # 解析确认无工具调用：此前被锁存的候选一律作为正文收尾（防内容被吞）
            seg = C[self.flushed:]
            self.flushed = len(C)
            if seg and self.on_emit:
                self.on_emit(seg)
            self.artifact = False
            return False
        s = _artifact_start(C, self.allowed)
        if s >= self.flushed:
            seg = C[self.flushed:s]
            self.flushed = s
            if seg and self.on_emit:
                self.on_emit(seg)
            self.artifact = True
        else:
            seg = C[self.flushed:]
            self.flushed = len(C)
            if seg and self.on_emit:
                self.on_emit(seg)
            self.artifact = False
        return self.artifact


def extract_tool_call(text: str, allowed: list = None):
    """从模型回复提取文本协议工具调用。

    返回 (json 前的正文, {"name", "arguments"})；未识别到返回 None。
    主协议为严格 JSON（{"tool": "...", "arguments": {...}}），用平衡括号扫描
    定位完整 JSON 对象（容忍嵌套/换行/围栏）：从每个 "tool" 键向前找最近的
    "{"，再逐字符配平括号直到闭合，解析成功后返回。
    容错：① 模型常输出未转义的 Windows 路径（C:\\Users\\...）导致 json.loads
    失败，自动把非法反斜杠双写后重试；② 模型倾向平铺参数
    （{"tool": "...", "path": "..."} 而非嵌套 arguments），顶层除 tool 外
    的键一律并入 arguments。

    allowed（工具名白名单，代理层传入）：非 None 时先做严格 JSON 解析；未命中
    再兼容解析网页版模型的旧式 ReAct 输出（Action: name + Action Parameters/Input:
    {json}）。给出白名单可避免把正文中闲聊式的 "Action:" 误判为工具调用。
    """
    text = text or ""
    i = text.find('"tool"')
    while i != -1:
        j = text.rfind("{", 0, i)
        if j == -1:
            break
        depth, in_str, esc, k = 0, False, False, j
        while k < len(text):
            c = text[k]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            else:
                if c == '"':
                    in_str = True
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        raw = text[j:k + 1]
                        try:
                            d = json.loads(raw)
                        except Exception:
                            try:
                                d = json.loads(_fix_json_backslash(raw))
                            except Exception:
                                break
                        name = str(d.get("tool") or "").strip()
                        if name:
                            return (text[:j].strip(),
                                    {"name": name,
                                     "arguments": _coerce_args(d)})
                        break
            k += 1
        i = text.find('"tool"', i + 1)
    # 兼容回退：严格 JSON 未命中且给了白名单时，解析网页版模型的其它输出形态
    # （[调用工具] 工具名{...} / 旧式 ReAct Action: name + Action Parameters）
    if allowed is not None:
        c = _find_call(text, allowed)
        if c:
            return (text[:c["start"]].strip(),
                    {"name": c["name"], "arguments": c["arguments"]})
    return None


def parse_tool_call(text: str) -> dict:
    """兼容接口：仅返回 {"name","arguments"} 或 None（需保留正文时用 extract_tool_call）。"""
    r = extract_tool_call(text)
    return r[1] if r else None


# ---------- 富文本：网页渲染 HTML → Markdown ----------
# 网页版回复经 markdown 渲染后，innerText 会丢失标题/加粗/代码块/表格等结构，
# 界面侧是 Markdown→HTML 渲染，故从 DOM 取 innerHTML 反解析为 Markdown，
# 才能在气泡里还原富文本（全标准库实现，兼容 Cython 打包）。
class _HtmlToMarkdown(HTMLParser):
    """精简 HTML→Markdown：标题/段落/列表/代码块/行内强调/链接/表格。"""
    _SKIP = {"button", "script", "style", "svg", "noscript"}
    _HEAD = {"h1": "#", "h2": "##", "h3": "###", "h4": "####",
             "h5": "#####", "h6": "######"}
    _BLOCK = {"p", "div", "section", "li"}   # tr/table/td/th 由表格分支单独处理

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self._skip = 0
        self._pre = False
        self._pre_lang = ""
        self._pre_buf = []
        self._lists = []        # 列表栈：("ul"|"ol", 序号)
        self._row = []
        self._table = []
        self._cell = []
        self._in_cell = False
        self._link = None       # 当前链接 href（等待结束标签补 (url)）

    # ---------- 工具 ----------
    def _nl(self):
        if self.out and not str(self.out[-1]).endswith("\n"):
            self.out.append("\n")

    @staticmethod
    def _lang(attrs: dict) -> str:
        cls = str(attrs.get("class") or "")
        m = re.search(r"(?:language|lang)-([A-Za-z0-9+#._-]+)", cls)
        return m.group(1) if m else ""

    # ---------- 标签 ----------
    def handle_starttag(self, tag, attrs):
        a = {str(k): str(v or "") for k, v in attrs}
        if tag in self._SKIP:
            self._skip += 1
            return
        if self._skip:
            return
        if tag == "pre":
            self._pre, self._pre_buf = True, []
            self._pre_lang = self._lang(a)
            return
        if self._pre:
            # 语言标记可能挂在 <pre> 或内层 <code class="language-xxx">
            if tag == "code" and not self._pre_lang:
                self._pre_lang = self._lang(a)
            return
        if tag in self._HEAD:
            self._nl()
            self.out.append(self._HEAD[tag] + " ")
        elif tag in ("ul", "ol"):
            self._nl()
            self._lists.append((tag, 0))
        elif tag == "li":
            self._nl()
            kind, _ = self._lists[-1] if self._lists else ("ul", 0)
            indent = "  " * max(0, len(self._lists) - 1)
            if kind == "ol":
                self._lists[-1] = ("ol", self._lists[-1][1] + 1)
                self.out.append(f"{indent}{self._lists[-1][1]}. ")
            else:
                self.out.append(f"{indent}- ")
        elif tag in ("td", "th"):
            self._cell = []
            self._in_cell = True
        elif tag == "tr":
            self._row = []
        elif tag == "br":
            self.out.append("\n")
        elif tag == "hr":
            self._nl()
            self.out.append("---\n")
        elif tag in ("strong", "b"):
            self.out.append("**")
        elif tag in ("em", "i"):
            self.out.append("*")
        elif tag == "code":
            self.out.append("`")
        elif tag == "a":
            self._link = a.get("href") or ""
            self.out.append("[")

    def handle_endtag(self, tag):
        if tag in self._SKIP:
            self._skip = max(0, self._skip - 1)
            return
        if tag == "pre" and self._pre:
            code = "".join(self._pre_buf).rstrip("\n")
            self._pre, self._pre_buf = False, []
            self._nl()
            self.out.append(f"```{self._pre_lang}\n{code}\n```\n\n")
            return
        if self._skip or self._pre:
            return
        if tag in self._HEAD:
            self.out.append("\n\n")
        elif tag in self._BLOCK:
            self.out.append("\n\n") if tag in ("p", "div", "section", "pre") else None
        elif tag in ("ul", "ol"):
            if self._lists:
                self._lists.pop()
            self._nl()
        elif tag in ("strong", "b"):
            self.out.append("**")
        elif tag in ("em", "i"):
            self.out.append("*")
        elif tag == "code":
            self.out.append("`")
        elif tag == "a":
            href = self._link or ""
            self._link = None
            self.out.append(f"]({href})" if href else "]")
        elif tag in ("td", "th"):
            if self._row is not None:
                self._row.append("".join(self._cell).strip())
            self._cell = []
            self._in_cell = False
        elif tag == "tr":
            if self._table is not None and self._row:
                self._table.append(self._row)
            self._row = []
        elif tag == "table":
            rows = self._table or []
            self._table = []
            if rows:
                self._nl()
                head = rows[0]
                self.out.append("| " + " | ".join(head) + " |\n")
                self.out.append("| " + " | ".join("---" for _ in head) + " |\n")
                for r in rows[1:]:
                    self.out.append("| " + " | ".join(r) + " |\n")
                self.out.append("\n")

    def handle_data(self, data):
        if self._skip:
            return
        if self._pre:
            self._pre_buf.append(data)
            return
        if self._in_cell:          # 表格单元格：只进单元格缓冲，不直接输出
            self._cell.append(data)
            return
        self.out.append(data)

    def result(self) -> str:
        md = "".join(self.out)
        md = re.sub(r"\n{3,}", "\n\n", md)
        return md.strip()


def html_to_markdown(html: str) -> str:
    """把网页版助手消息的渲染 HTML 转为 Markdown（失败返回空串，由调用方回落）。"""
    if not html:
        return ""
    try:
        p = _HtmlToMarkdown()
        p.feed(html)
        p.close()
        return p.result()
    except Exception:
        return ""


def strip_ui_noise(text: str) -> str:
    """剔除回复尾部页面 UI 杂音（末尾纯噪声行 + 免责声明截断）。

    UI 标签固定在回复区末尾独立行，先按行从尾部剔除；免责声明可能嵌在末行，
    再用强标记截断。避免用「最先出现位置」误剪正文（如用户问什么是深度思考）。"""
    if not text:
        return ""
    noise = set(UI_NOISE_MARKS)
    lines = text.rstrip().splitlines()
    while lines and lines[-1].strip() in noise:
        lines.pop()
    out = "\n".join(lines).strip()
    for mark in ("内容由 AI 生成，请仔细甄别", "内容由AI生成，请仔细甄别"):
        i = out.find(mark)
        if i >= 0:
            out = out[:i].rstrip()
            break
    return out.strip()


# ---------- 浏览器桥 ----------
class WebBridge:
    """封装 CDP 页面驱动作业：ensure_ready / ask / close。"""

    HOME = "https://chat.deepseek.com"
    _LOGIN_PROBE = ("(!!document.querySelector('[class*=textarea], [class*=input-area], "
                    'textarea, [contenteditable="true"]\'))')
    _INPUT_LEN = ("(() => { const b = document.querySelector"
                  "('textarea,[contenteditable=\"true\"]'); if(!b) return -1;"
                  " return (b.value !== undefined ? b.value : b.innerText||'').length; })()")
    _MAIN_TEXT = ("(() => { return document.body.innerText; })()")
    _STOP_EXISTS = ("(!!document.querySelector('button[class*=stop],"
                    "button[aria-label*=\"停止\"],[class*=stop-generate],"
                    "[class*=generate-over]'))")
    # 页面生成状态检测，返回 DONE / GENERATING / NO_MSG / UNKNOWN：
    # - GENERATING：停止元素存在 / 页面处于思考中（正文未渲染，含思考卡片）
    # - NO_MSG：最后一条助手消息未开始渲染
    # - DONE：页面出现消息操作栏（复制/点赞/重新生成）——DeepSeek 仅在整个
    #   回复渲染完成后才挂出操作栏，是「真完成」的唯一强信号。新会话每轮只有
    #   一条助手消息，操作栏必属于它，故全局检测即可（与操作栏 DOM 位置无关）。
    # - UNKNOWN：结构无法识别（页面改版），由调用方走文本静止兜底判定。
    # ★ 检测顺序关键：DONE 判定必须优先于「思考中」判定——思考完成后页面可能
    #   残留「深度思考」字样，若先判思考会永远等不到 DONE；反之完成时操作栏
    #   必然出现，可立即收尾。
    # ★ 不能仅凭「文本静止」判定完成：长回复按块渲染，块间停顿可达数秒，
    #   提前收尾会截断回复（含末尾的工具调用 JSON）。
    _GEN_STATE_JS = r"""
(() => {
  const has = (s) => !!document.querySelector(s);
  const msgs = document.querySelectorAll('[class*="ds-assistant-message"]');
  const body = document.body.innerText || '';
  const thinking = /深度思考中|正在思考|思考中/.test(body);
  if (msgs.length && !(msgs[msgs.length - 1].innerText || '').trim() && thinking) {
    return 'GENERATING';   // 思考中且正文未开始渲染
  }
  if (has('[aria-label*="复制"],[aria-label*="点赞"],[aria-label*="重新生成"],'
          '[class*="copy"],[class*="like"],[class*="regenerate"],'
          '[class*="Copy"],[class*="Like"],[class*="Regenerate"],'
          '[title*="复制"],[title*="点赞"]')) return 'DONE';
  if (has('[class*="stop"],[class*="Stop"],[aria-label*="停止"]')) return 'GENERATING';
  if (thinking) return 'GENERATING';   // 思考期（含思考卡片文本非空的情况）
  if (!msgs.length) return 'NO_MSG';
  if (!(msgs[msgs.length - 1].innerText || '').trim()) return 'NO_MSG';
  return 'UNKNOWN';
})()
"""
    # 兜底停止：agent 判定完成但页面仍在生成（无 DONE 信号的降级路径）时，
    # 主动点击停止按钮终止生成，避免网页版继续输出消耗资源（「疯狂输出」）。
    _CLICK_STOP_JS = r"""
(() => {
  const els = [...document.querySelectorAll('button,[role="button"],[tabindex],div,span')];
  for (const el of els) {
    const cls = (el.className || '');
    const al = (el.getAttribute('aria-label') || '');
    const t = (el.textContent || '').trim();
    if ((/(^|[\s-])stop([\s-]|$)/i.test(cls) || /停止/.test(al)) && t.length <= 4) {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) { try { el.click(); return 'STOPPED'; } catch (e) {} }
    }
  }
  return 'NONE';
})()
"""
    # 页面内「开启新对话」按钮（无导航切换，免除 reload 弹窗闪烁）。
    # 新版页面所有交互元素从 <button> 改为 <div tabindex>/[role=button]，
    # 选择器必须覆盖，否则 NO_BTN 导致每次发到当前会话（上下文污染——
    # 模型会延续上一话题而非执行当前工具任务）。
    # 找不到按钮时返回 'NAV_HOME'，由 ask() 回首页（空态=新会话）。
    _NEW_CHAT_JS = r"""
(() => {
  const els = [...document.querySelectorAll('button, [role="button"], [tabindex], a, span, div')];
  for (const el of els) {
    const al = ((el.getAttribute('aria-label') || '') + ' ' + (el.textContent || ''));
    if (/新对话|开启新对话|new ?chat/i.test(al) && (el.textContent || '').trim().length <= 20) {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) { el.click(); return 'clicked:' + el.tagName; }
    }
  }
  return 'NAV_HOME';
})()
"""
    # 按档位切换模式：优先点击顶部模式 tab（快速/专家/识图），
    # 页面文案多变（快速模式/专家模式/试图模式/识图模式/视图模式），
    # 用别名**精确相等**匹配（tab 元素文本就是「快速模式」等短词；
    # 包含匹配会命中「快速模式快速模式专家模式...」整条 tab 容器 → 误点无效）。
    # 返回 'clicked:xxx' / 'NO_TAB'。
    _SET_MODE_TAB_JS = r"""
(() => {
  const mode = __MODE__;
  const aliases = {
    quick: ['快速模式', '快速'],
    expert: ['专家模式', '专家'],
    view: ['视图模式', '试图模式', '识图模式', '识图', '视图', '试图'],
  };
  const keys = aliases[mode] || aliases.quick;
  const els = [...document.querySelectorAll('button, [role="button"], [tabindex], a, span, div')];
  for (const el of els) {
    const t = ((el.textContent || '').trim() || (el.getAttribute('aria-label') || ''));
    if (!t || t.length > 8) continue;
    if (keys.includes(t)) {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) { el.click(); return 'clicked:' + t; }
    }
  }
  return 'NO_TAB';
})()
"""
    # 开关兜底（旧版界面/无 tab 时）：按档位设置「深度思考/智能搜索」开关。
    # 选择器覆盖 div；文案按包含匹配 + 别名表（__ALIASES__）收敛。
    _SET_SWITCH_JS = r"""
(() => {
  const spec = __SPEC__;
  const aliases = __ALIASES__;
  const els = [...document.querySelectorAll('button, [role="button"], [tabindex], a, span, div')];
  const findEl = (label) => {
    const keys = (aliases[label] || [label]).map(k => String(k).toLowerCase());
    return els.find(x => {
      const t = (((x.textContent || '').trim() || (x.getAttribute('aria-label') || ''))
                 .toLowerCase());
      return keys.some(k => t.includes(k)) && (x.textContent || '').trim().length <= 20;
    });
  };
  for (const s of spec) {
    const el = findEl(s.label);
    if (!el) continue;
    const cls = el.className || '';
    const active = /(is-|--)(active|checked|selected|on|pressed)/.test(cls)
      || el.getAttribute('aria-pressed') === 'true'
      || el.getAttribute('aria-checked') === 'true';
    if (active !== s.on) el.click();
  }
  return 'OK';
})()
"""
    # 模式开关按钮文案别名（页面文案变化时自动适配）
    _MODE_ALIASES = {
        "深度思考": ["深度思考", "deepthink", "深度"],
        "智能搜索": ["智能搜索", "联网搜索", "联网", "搜索"],
    }
    # 模型名 -> 页面模式档位（_SET_MODE_TAB_JS 的 aliases 键）
    _MODE_KEYS = {"deepseek-chat": "quick", "deepseek-chat-quick": "quick",
                  "deepseek-chat-expert": "expert", "deepseek-chat-view": "view"}

    # 键入：用 React 受控组件标准方式设置值——原生 value setter + 冒泡 input 事件，
    # 确保 React state 同步（CDP Input.insertText 改 DOM 但不走 React setter 路径，
    # state 仍为空 → 点发送提交空消息 → 页面无回显）。
    _TYPING_JS = r"""
(() => {
  const b = document.querySelector('textarea,[contenteditable="true"]');
  if (!b) return 'NO_BOX';
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLTextAreaElement.prototype, 'value').set;
  if (setter) { setter.call(b, __PROMPT__); } else { b.value = __PROMPT__; }
  b.dispatchEvent(new Event('input', {bubbles: true}));
  return 'OK';
})()
"""
    # 发送按钮定位：新版按钮是 div。输入区右下有两个 ds-button——
    # 左侧附件（ds-button--iconLabelPrimary）与右侧发送（ds-button--primary），
    # 必须精确匹配 primary 发送按钮，包含匹配会点到附件（弹文件选择器）。
    # 定位成功后优先 el.click()（React onClick 可响应），失败再返回坐标
    # 供 _click_send 做真实鼠标点击。
    _SEND_JS = r"""
(() => {
  const b = document.querySelector('textarea,[contenteditable="true"]');
  if (!b) return 'NO_BOX';
  const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  let target = document.querySelector('[class*="ds-button--primary"]');
  if (!target || !visible(target)) {
    let p = b;
    for (let i = 0; i < 5 && p; i++) p = p.parentElement;
    const scope = p || b.parentElement;
    const cands = [...scope.querySelectorAll('[class*="ds-button--primary"], [class*="ds-button--filled"], button, [role="button"], [tabindex]')]
      .filter(visible);
    if (cands.length) {
      target = cands.slice().sort(
        (a, c) => c.getBoundingClientRect().right - a.getBoundingClientRect().right)[0];
    }
  }
  if (!target) return 'NO_SEND';
  try {
    target.click();
    return 'CLICKED';
  } catch (e) {}
  const r = target.getBoundingClientRect();
  if (r.width <= 0 || r.height <= 0) return 'NO_VISIBLE';
  return JSON.stringify({x: Math.round(r.x + r.width / 2),
                         y: Math.round(r.y + r.height / 2)});
})()
"""
    # 最后一条助手消息提取（多策略，适配新版哈希类名 DOM）：
    # 1) 新版稳定语义类 ds-assistant-message*（DeepSeek 助手消息专用，含完整
    #    markdown 内容；用户消息是 ds-collapsible-text，不会被匹配）。
    #    ★ 不能用裸 [class*=ds-markdown] 取最后一个——流式渲染时 markdown
    #    按块创建（段落/标题/列表分别成元素），取"最后一个"得到的是最新碎片
    #    （实测会截断成 "-\n5"）；ds-assistant-message 容器才是完整整条消息。
    # 2) 老版 .ds-markdown 兜底（取内容最长的，避免碎片）。
    # 3) the-header 消息项兜底：跳过含模式标签的用户项。
    _LAST_ASSISTANT_JS = r"""
(() => {
  const pick = (nodes) => {
    for (let i = nodes.length - 1; i >= 0; i--) {
      const t = (nodes[i].innerText || '').trim();
      if (t) return t;
    }
    return '';
  };
  const m0 = document.querySelectorAll('[class*="ds-assistant-message"]');
  const r0 = pick(m0);
  if (r0) return r0;
  const m1 = document.querySelectorAll('[class*=ds-markdown]');
  let best = '';
  for (const n of m1) {
    const t = (n.innerText || '').trim();
    if (t.length > best.length) best = t;
  }
  if (best) return best;
  const m2 = document.querySelectorAll('[class*=ds-message], [class*=message]');
  const r2 = pick(m2);
  if (r2) return r2;
  // 新版 the-header 消息项：用户消息项内含模式标签（快速模式/专家模式/识图等），
  // 助手消息项不含——倒序取最后一条「非用户」消息项文本
  const items = [...document.querySelectorAll('[class*="the-header"]')];
  for (let i = items.length - 1; i >= 0; i--) {
    const t = (items[i].innerText || '').trim();
    if (!t || t.length < 2) continue;
    const isUser = [...items[i].querySelectorAll('div')].some(d => {
      const s = (d.innerText || '').trim();
      return /^(快速模式|专家模式|识图模式|试图模式|视图模式|深度思考|智能搜索)$/.test(s);
    });
    if (isUser) continue;               // 用户消息项（含模式标签）跳过
    if (/^(内容由 ?AI 生成|深度思考|智能搜索)/.test(t)) continue;
    return t;
  }
    return '';
})()
"""
    # 同 _LAST_ASSISTANT_JS 的定位逻辑，但取 innerHTML（保留渲染结构，
    # 供 HTML→Markdown 还原富文本；定位失败返回空串，调用方回落 innerText）
    _LAST_ASSISTANT_HTML_JS = r"""
(() => {
  const pick = (nodes) => {
    for (let i = nodes.length - 1; i >= 0; i--) {
      const h = (nodes[i].innerHTML || '').trim();
      if (h) return h;
    }
    return '';
  };
  const m0 = document.querySelectorAll('[class*="ds-assistant-message"]');
  const r0 = pick(m0);
  if (r0) return r0;
  const m1 = document.querySelectorAll('[class*=ds-markdown]');
  let best = '';
  for (const n of m1) {
    const h = (n.innerHTML || '').trim();
    if (h.length > best.length) best = h;
  }
  if (best) return best;
  const m2 = document.querySelectorAll('[class*=ds-message], [class*=message]');
  return pick(m2);
})()
"""

    def __init__(self):
        self._ctrl = None
        self._ready_page = False

    def ensure_ready(self, wait_login: float = 180.0, visible: bool = False) -> tuple:
        """确保浏览器桥就绪（浏览器常驻后台，仅首次启动；日志探测登录态）。

        使用独立 BrowserController 实例（不与 AI 电脑操控共享）；visible=True
        （登录引导）时窗口保持在屏幕内，否则移出屏幕后台化，发消息不再弹窗。
        """
        if self._ctrl is not None and self._ready_page:
            if visible:
                self._show_window()
            return True, "浏览器桥已就绪（后台常驻）"
        ctrl = agent_browser.BrowserController()
        # 窗口必须保持可见（visibilityState=visible）才能让页面全速处理 SSE
        # 流式回复——实测屏幕外（-32000）被 Chromium 标记 hidden，回复挂起
        # 45s+ 无内容；visible 时 2s 出回复。启动即放屏幕内（左上角），
        # 随后由 _hide_window 立即移到右下角小窗（最小打扰且保持可见）。
        ok, msg = ctrl.start(window_pos=None if visible else (0, 0))
        if not ok:
            return False, msg
        self._ctrl = ctrl
        if not visible:
            self._hide_window()
        ctrl.navigate(self.HOME)
        deadline = time.time() + wait_login
        while time.time() < deadline:
            st = ctrl.eval(self._LOGIN_PROBE)
            if st and st.get("ok") and st.get("text") == "true":
                break
            time.sleep(2)
        else:
            try:
                ctrl.stop()
            except Exception:
                pass
            self._ctrl = None
            return False, "未检测到登录态（聊天输入框未出现），请在设置页完成一次网页版登录后重试"
        self._ready_page = True
        if visible:
            self._show_window()
        else:
            self._hide_window()
        return True, "浏览器桥已就绪"

    def ask(self, prompt: str, on_delta=None, on_full=None, stop=None, wait=240.0,
            mode: str = "deepseek-chat-quick") -> str:
        """驱动一次对话：新会话 → 按档位设模式 → 键入 → 鼠标发送 → 提取回复。

        mode 取 WEB_MODES 键（快速/专家/视图）。on_delta(str)：流式渲染增量
        逐段回调；on_full(str)：每次文本变化的完整快照回调（页面重渲染/整块
        替换场景更稳，供工具尾巴抑制流式转发用）；stop() 返回 True 时中止等待。"""
        assert self._ctrl is not None, "请先 ensure_ready()"
        ctrl = self._ctrl
        # 1) 新建会话（容错：点击失败回首页重开，绝不发到旧会话污染上下文）
        try:
            nr = ctrl.eval(self._NEW_CHAT_JS)
            if nr and (nr.get("text") or "").startswith("NAV_HOME"):
                # 页面无「新对话」按钮（会话内布局变化）→ 直接回首页
                # （DeepSeek 首页空态即新会话），等待输入框重建
                ctrl.navigate(self.HOME)
                deadline = time.time() + 30
                while time.time() < deadline:
                    st = ctrl.eval(self._LOGIN_PROBE)
                    if st and st.get("ok") and st.get("text") == "true":
                        break
                    time.sleep(0.8)
        except Exception:
            pass
        time.sleep(random.uniform(0.6, 1.2))
        deadline = time.time() + 30
        while time.time() < deadline:
            st = ctrl.eval(self._LOGIN_PROBE)
            if st and st.get("ok") and st.get("text") == "true":
                break
            time.sleep(0.8)
        time.sleep(random.uniform(0.5, 1.0))
        # 2) 按档位设置模式：优先点击顶部模式 tab（新版），
        #    无 tab 时退回「深度思考/智能搜索」开关（旧版/兜底）
        wm = WEB_MODES.get(mode) or WEB_MODES["deepseek-chat-quick"]
        # 模型名（deepseek-chat-expert）映射为档位键（expert），
        # _SET_MODE_TAB_JS 的 aliases 只认 quick/expert/view，直接注入
        # 模型名会让 aliases[mode] 恒 undefined → 永远点「快速模式」
        tab_key = self._MODE_KEYS.get(mode, "quick")
        tab_js = self._SET_MODE_TAB_JS.replace("__MODE__", json.dumps(tab_key))
        tab_r = ctrl.eval(tab_js)
        if tab_r and tab_r.get("ok") and (tab_r.get("text") or "").startswith("clicked"):
            # 切模式 tab 会触发页面异步重建输入区（React 重挂载 textarea），
            # 立即键入/发送会作用到旧元素或空态 → 先等重建完成并重新探测输入框
            time.sleep(random.uniform(1.5, 2.5))
            deadline = time.time() + 20
            while time.time() < deadline:
                st = ctrl.eval(self._LOGIN_PROBE)
                if st and st.get("ok") and st.get("text") == "true":
                    break
                time.sleep(0.8)
        else:
            spec = [{"label": "深度思考", "on": bool(wm["thinking"])},
                    {"label": "智能搜索", "on": bool(wm["search"])}]
            js = self._SET_SWITCH_JS.replace("__SPEC__", json.dumps(spec, ensure_ascii=False))
            js = js.replace("__ALIASES__", json.dumps(self._MODE_ALIASES, ensure_ascii=False))
            ctrl.eval(js)
        # 3) 聚焦 + 键入（React 受控组件原生 setter + input 事件，
        #    确保 state 同步；CDP insertText 不同步 state 会导致发空消息）
        ctrl.eval("(() => {const b=document.querySelector"
                  "('textarea,[contenteditable=\"true\"]'); if(!b)return'NO_BOX';"
                  " b.focus(); b.scrollIntoView({block:'center'}); return 'OK';})()")
        ctrl.eval(self._TYPING_JS.replace("__PROMPT__", json.dumps(prompt, ensure_ascii=False)))
        # 真人化节奏：键入后随机停顿再点发送，避免固定时序被风控识别
        time.sleep(random.uniform(0.4, 0.9))
        # 4) 发送：优先真实鼠标点击发送按钮（新版 div 按钮 + 更接近真人，
        #    规避 el.click()/Enter 在 React 受控输入上不触发提交的问题）；
        #    兜底回车。发送后确认输入框已清空（提交成功的标志），
        #    未清空则重试一次，避免静默失败白等一轮。
        if not self._click_send():
            for t in ("keyDown", "keyUp"):
                ctrl._call("Input.dispatchKeyEvent", {
                    "type": t, "key": "Enter", "code": "Enter",
                    "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13},
                    session=ctrl._active_session)
        time.sleep(random.uniform(0.8, 1.5))
        if self._input_len() > 0:
            if not self._click_send():
                for t in ("keyDown", "keyUp"):
                    ctrl._call("Input.dispatchKeyEvent", {
                        "type": t, "key": "Enter", "code": "Enter",
                        "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13},
                        session=ctrl._active_session)
        # 5) 轮询「最后一条助手消息节点」文本（增量逐段回调模拟流式）。
        # 完成判定优先级：
        #   ① DONE 强信号（页面出现消息操作栏 = 整条回复渲染完成）→ 立即结束；
        #   ② GENERATING / 思考期（文本为空）→ 重置稳定计数继续等；
        #   ③ 页面结构无法识别时按文本静止 _CFG.idle_done_ticks（~15s）兜底；
        #   ④ 总 deadline（wait）兜底。
        # 绝不使用短文本静止阈值：长回复 markdown 按块渲染，块间停顿可达数秒，
        # 过早判定会截断回复（含末尾的工具调用 JSON）→ 大型任务流中断。
        last_text, full_text, idle = "", "", 0
        idle_done = _CFG["idle_done_ticks"]
        deadline = time.time() + wait
        while time.time() < deadline:
            if stop and stop():
                break
            time.sleep(0.5)
            try:
                # 优先富文本（HTML→Markdown），保留标题/代码块/列表/表格结构
                text = self._last_assistant_md()
                state = self._gen_state()
            except Exception:
                continue
            if text and any(m in text for m in WELCOME_MARKS):
                text = ""   # 空会话欢迎语 ≠ 模型回复（消息未发/回复未渲染）
            elif text and _THINKING_RE.search(text) and state != "DONE":
                text = ""   # 思考期内容 ≠ 模型回复；仅 DONE（操作栏出现）后视为正式回复
            if text and text != last_text:          # 有增量 → 逐段回调
                if last_text and text.startswith(last_text):
                    delta = text[len(last_text):]
                else:
                    delta = text
                last_text = text
                full_text = text
                idle = 0
                if on_full and text:
                    on_full(text)
                if on_delta and delta:
                    on_delta(delta)
                continue
            if state == "DONE":                     # 强信号：整条消息渲染完成
                break
            if state == "GENERATING" or not text:   # 仍在生成 / 思考期
                idle = 0
                continue
            if idle >= idle_done:                   # 兜底：结构未知 + 文本静止
                break
            idle += 1
        # 降级收尾：兜底判定完成但页面仍在生成（无 DONE 信号）→ 点停止按钮，
        # 避免网页版继续输出消耗资源
        if self._gen_state() == "GENERATING":
            self._click_stop()
        return strip_ui_noise(full_text or last_text)

    # ---------- 发送 ----------
    def _input_len(self) -> int:
        """当前输入框文本长度（-1=输入框不存在）。"""
        try:
            out = self._ctrl.eval(self._INPUT_LEN)
            if out and out.get("ok"):
                try:
                    return int(out.get("text") or "-1")
                except Exception:
                    return -1
        except Exception:
            pass
        return -1

    def _click_send(self) -> bool:
        """点击发送按钮：JS el.click() 优先，返回坐标时再做真实鼠标点击。

        新版页面发送按钮是 div（.ds-button--primary），输入区右下有两个 ds-button
        （左侧附件/右侧发送），el.click() 与 Enter 在 React 受控 textarea 上有时
        不触发提交；真实鼠标事件更接近真人操作，同时降低自动化风控误判。
        返回是否成功定位并触发。"""
        try:
            out = self._ctrl.eval(self._SEND_JS)
            if not out or not out.get("ok"):
                return False
            txt = (out.get("text") or "").strip()
            if txt in ("NO_BOX", "NO_SEND", "NO_VISIBLE", ""):
                return False
            if txt == "CLICKED":            # el.click() 已触发
                return True
            pos = json.loads(txt)
            # 真人化：点击坐标加 ±3px 随机抖动，避免精确中心点被风控识别
            dx, dy = random.randint(-3, 3), random.randint(-3, 3)
            for t in ("mousePressed", "mouseReleased"):
                self._ctrl._call("Input.dispatchMouseEvent", {
                    "type": t, "x": pos["x"] + dx, "y": pos["y"] + dy,
                    "button": "left", "clickCount": 1},
                    session=self._ctrl._active_session)
            return True
        except Exception:
            return False

    # ---------- 窗口后台/前台 ----------
    # 窗口保持可见（右下角小窗）是硬性要求：Chromium 对窗口移出屏幕/不可见
    # 的页面置 visibilityState=hidden，页面 JS 整体降级，DeepSeek 的 SSE 流式
    # 回复会挂起（实测 hidden 45s 无回复，visible 2s 出回复）。
    # 「最小打扰」方案：窗口贴屏幕右下角，尺寸收小，用户几乎感知不到。
    _CORNER_W, _CORNER_H = 640, 480

    def _hide_window(self):
        """把桥专用浏览器窗口移到屏幕右下角小窗（保持 visibilityState=visible）。"""
        try:
            out = self._ctrl.eval(
                "JSON.stringify({w: screen.availWidth, h: screen.availHeight})")
            if out and out.get("ok"):
                s = json.loads(out.get("text") or "{}")
                w = int(s.get("w") or 1920)
                h = int(s.get("h") or 1080)
                self._set_bounds(w - self._CORNER_W - 8, h - self._CORNER_H - 8,
                                 width=self._CORNER_W, height=self._CORNER_H)
            else:
                self._set_bounds(1200, 560,
                                 width=self._CORNER_W, height=self._CORNER_H)
        except Exception:
            pass

    def _show_window(self):
        try:
            self._set_bounds(120, 120)
        except Exception:
            pass

    def _set_bounds(self, left, top, width=1280, height=800):
        """把桥专用浏览器窗口移到指定位置（右下角小窗 / 登录引导正常位）。

        浏览器级端点调用 Browser.getWindowForTarget 必须显式携带 page targetId，
        否则无关联 target 直接 CDP 报错，_hide_window 静默失败 → 窗口始终在屏幕上，
        表现为「每次发消息浏览器都弹出来」。先 Target.getTargets 取首个 page 目标。"""
        try:
            tid = ""
            targets = self._ctrl._call("Target.getTargets", {})
            for t in (targets.get("targetInfos") or []):
                if t.get("type") == "page":
                    tid = t.get("targetId") or ""
                    break
            r = self._ctrl._call("Browser.getWindowForTarget",
                                 {"targetId": tid} if tid else {})
            wid = (r or {}).get("windowId")
            if wid:
                self._ctrl._call("Browser.setWindowBounds", {
                    "windowId": wid,
                    "bounds": {"left": left, "top": top,
                               "width": width, "height": height,
                               "windowState": "normal"}})
        except Exception:
            pass

    # ---------- 内部 ----------
    def _main_text(self) -> str:
        out = self._ctrl.eval(self._MAIN_TEXT)
        return out.get("text", "") if out.get("ok") else ""

    def _last_assistant_text(self) -> str:
        out = self._ctrl.eval(self._LAST_ASSISTANT_JS)
        return out.get("text", "") if out.get("ok") else ""

    def _last_assistant_html(self) -> str:
        out = self._ctrl.eval(self._LAST_ASSISTANT_HTML_JS)
        return out.get("text", "") if out.get("ok") else ""

    def _last_assistant_md(self) -> str:
        """提取最后一条助手消息的文本，优先富文本（HTML→Markdown）。

        网页版把 markdown 渲染成 DOM，innerText 会丢掉标题/加粗/代码块/表格等
        结构，界面侧又是 Markdown→HTML 渲染，故取 innerHTML 反解析回 Markdown
        才能显示富文本；转换失败或明显丢内容时回落 innerText。"""
        md = html_to_markdown(self._last_assistant_html())
        if not md:
            return self._last_assistant_text()
        plain = self._last_assistant_text()
        if plain and len(md) < len(plain) * 0.6:   # 转换疑似丢内容
            return plain
        return md

    def _stop_exists(self) -> bool:
        out = self._ctrl.eval(self._STOP_EXISTS)
        return out.get("text") == "true"

    def _gen_state(self) -> str:
        """当前页面生成状态：DONE / GENERATING / NO_MSG / UNKNOWN。"""
        try:
            out = self._ctrl.eval(self._GEN_STATE_JS)
            return out.get("text", "") if out.get("ok") else "UNKNOWN"
        except Exception:
            return "UNKNOWN"

    def _click_stop(self) -> bool:
        """兜底点击停止按钮（agent 判定完成但页面仍在生成的降级路径）。"""
        try:
            out = self._ctrl.eval(self._CLICK_STOP_JS)
            return out.get("text") == "STOPPED"
        except Exception:
            return False

    def close(self):
        try:
            if self._ctrl is not None:
                self._ctrl.stop()
        except Exception:
            pass
        self._ctrl = None


def bridge() -> WebBridge:
    global _BRIDGE
    if _BRIDGE is None:
        with _LOCK:
            if _BRIDGE is None:
                _BRIDGE = WebBridge()
    return _BRIDGE


def has_valid_credentials() -> bool:
    """轻量登录态探测：已曾启动过浏览器桥且登录过即视为有效（细粒度由 ensure_ready 判定）。"""
    return (Path.home() / ".winapp_migrator" / "browser_profile").exists()


def login_now() -> tuple:
    """供 UI 调用：后台线程启动浏览器桥并引导登录（窗口置于屏幕内可见）。返回 (ok, msg)。"""
    try:
        return bridge().ensure_ready(wait_login=240.0, visible=True)
    except Exception as e:
        return False, f"引导登录失败: {e}"


# ---------- 本地中转代理 ----------
class _ProxyServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.server_address[1]}/v1"


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def do_GET(self):
        if self.path.rstrip("/") in ("/v1/models", "/models"):
            self._json(200, {"object": "list",
                             "data": [{"id": n, "object": "model"} for n in MODELS]})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path.rstrip("/") != "/v1/chat/completions":
            self._json(404, {"error": "not found"})
            return
        try:
            ln = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(ln).decode("utf-8") or "{}")
        except Exception:
            self._json(400, {"error": "invalid json body"})
            return
        messages = payload.get("messages") or []
        tools = payload.get("tools") or []
        tool_names = [str(((t.get("function") or {}).get("name") or "")).strip()
                      for t in (tools or []) if isinstance(t, dict)]
        tool_names = [n for n in tool_names if n]
        model = payload.get("model")
        try:
            # tools 必须注入 prompt：网页版无角色数组/函数声明，模型靠顶部
            # 注入的工具清单与调用协议才知道有哪些工具、如何输出调用 JSON
            prompt = serialize_prompt(messages, tools)
        except Exception as e:
            self._json(400, {"error": f"prompt 序列化失败: {e}"})
            return
        _dbg(f"REQ model={model} stream={bool(payload.get('stream'))} "
             f"tools={len(tools)} prompt_len={len(prompt)} "
             f"tool_names={','.join((((t.get('function') or {}).get('name') or '') for t in (tools or [])))[:200]} "
             f"prompt_tail={prompt[-160:]!r}")
        # 先确保浏览器桥就绪（登录态校验），失败直接返回错误，不消耗后续流式响应
        try:
            okb, msgb = bridge().ensure_ready(wait_login=90.0)
        except Exception as e:
            okb, msgb = False, f"浏览器桥启动失败: {e}"
        if not okb:
            self._json(401, {"error": msgb, "code": "WEB_NOT_READY"})
            return
        stream = bool(payload.get("stream"))
        _req_id = uuid.uuid4().hex
        if stream:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.flush()
            seq = 0

            def _emit(delta=None, tc=None, finish=None, msg_tc=None):
                """发送一个 SSE chunk。
                delta=正文增量；tc=(id,name,args) 工具调用增量；
                finish=finish_reason（'stop'/'tool_calls'）；msg_tc=完整工具调用回显。"""
                nonlocal seq
                seq += 1
                d = {}
                if delta:
                    d["content"] = delta
                if tc:
                    d["tool_calls"] = [{
                        "index": 0, "id": tc[0], "type": "function",
                        "function": {"name": tc[1], "arguments": tc[2] or ""}}]
                choice = {"index": 0, "delta": d, "finish_reason": finish}
                if msg_tc:
                    choice["message"] = {"role": "assistant", "tool_calls": msg_tc}
                chunk = {"id": _req_id, "object": "chat.completion.chunk",
                         "created": int(time.time()), "model": "deepseek-chat",
                         "choices": [choice]}
                self.wfile.write(
                    f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()

            if tools:
                # 工具请求：正文实时流式转发（_BodyStreamer 抑制结尾的工具调用
                # JSON，避免用户看到原始 Action:/{"tool" 尾巴），完整回复收尾后
                # 再解析文本协议并转为标准 tool_calls 下发。工具调用 JSON 在回复
                # 最末，正文部分可放心边渲染边转发，无需整条缓冲。
                streamer = _BodyStreamer(_emit, tool_names)
                try:
                    raw_reply = bridge().ask(prompt, on_full=streamer.snapshot,
                                             mode=model)
                except Exception as e:
                    raw_reply = ""
                    _emit(delta=f"\n[网页版调用失败: {e}]")
                extracted = extract_tool_call(raw_reply, tool_names)
                has_tail = streamer.finish(raw_reply, found=bool(extracted))
                if not extracted:
                    _dbg(f"STREAM_NO_TOOL_CALL reply_len={len(raw_reply)} "
                         f"has_tail={has_tail} tail={raw_reply[-600:]!r}")
                else:
                    _dbg(f"STREAM_TOOL_CALL name={extracted[1]['name']} "
                         f"args_len={len(json.dumps(extracted[1]['arguments'], ensure_ascii=False))} "
                         f"has_tail={has_tail}")
                if extracted:
                    # 正文已由 streamer 实时发出；此处仅补工具调用增量
                    _, call = extracted
                    cid = "call_" + _req_id[:12]
                    args_json = json.dumps(call["arguments"], ensure_ascii=False)
                    _emit(tc=(cid, call["name"], ""))
                    _emit(tc=(cid, "", args_json))
                    # finish 块同时回显完整 tool_calls（引擎双保险：增量聚合 + 完整覆盖）
                    _emit(finish="tool_calls", msg_tc=[{
                        "index": 0, "id": cid, "type": "function",
                        "function": {"name": call["name"], "arguments": args_json}}])
                else:
                    _emit(finish="stop")
            else:
                # 普通对话：实时流式转发（逐段增量）；mode 决定网页版开关档位
                try:
                    bridge().ask(prompt, on_delta=_emit, mode=model)
                except Exception as e:
                    _emit(delta=f"\n[网页版调用失败: {e}]")
                _emit(finish="stop")
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        else:
            parts = []
            try:
                bridge().ask(prompt, on_delta=parts.append, mode=model)
            except Exception as e:
                parts.append(f"\n[网页版调用失败: {e}]")
            raw_reply = "".join(parts)
            extracted = extract_tool_call(raw_reply, tool_names)
            if not extracted:
                _dbg(f"BATCH_NO_TOOL_CALL reply_len={len(raw_reply)} "
                     f"tail={raw_reply[-600:]!r}")
            else:
                _dbg(f"BATCH_TOOL_CALL name={extracted[1]['name']} "
                     f"args_len={len(json.dumps(extracted[1]['arguments'], ensure_ascii=False))}")
            message = {"role": "assistant"}
            if extracted:
                text, call = extracted
                message["content"] = strip_ui_noise(text) or None
                message["tool_calls"] = [{
                    "id": "call_" + _req_id[:12], "type": "function",
                    "function": {"name": call["name"],
                                 "arguments": json.dumps(call["arguments"],
                                                         ensure_ascii=False)}}]
            else:
                message["content"] = strip_ui_noise("".join(parts))
            self._json(200, {"id": _req_id, "object": "chat.completion",
                             "created": int(time.time()), "model": "deepseek-chat",
                             "choices": [{"index": 0, "message": message,
                                          "finish_reason": "stop"}]})


def start_proxy() -> dict:
    """幂等启动本地代理（仅 WEB_KIND 服务商使用）。返回 {ok, base_url, msg}。"""
    global _PROXY
    if _PROXY is not None:
        return {"ok": True, "base_url": _PROXY.base_url, "msg": "代理运行中"}
    try:
        srv = _ProxyServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        _PROXY = srv
        return {"ok": True, "base_url": srv.base_url, "msg": "代理已启动"}
    except Exception as e:
        return {"ok": False, "base_url": "", "msg": f"代理启动失败: {e}"}


def stop_proxy():
    global _PROXY
    if _PROXY is not None:
        try:
            _PROXY.shutdown()
            _PROXY.server_close()
        except Exception:
            pass
        _PROXY = None


def ensure_web_proxy(kind_check: bool = True) -> dict:
    """load_model_config 注入点调用：幂等启动本地代理（kind=deepseek_web 时走此路径）。
    kind_check=True 时仅会替代已经被点击的网页版服务商 base_url（恒 ok 启动代理）。"""
    r = start_proxy()
    return r