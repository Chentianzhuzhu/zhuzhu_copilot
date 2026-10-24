"""Agent 行为评分服务（Copilot 插件 agent-scorecard）。

**纯运行时插件**：不修改任何应用源码文件，启用即生效，exe 打包版同样可用。

  · 采集：包装 `agent_tools.execute_tool` 统计工具调用，包装 `AgentEngine.run`
          记录任务起止与 token 增量（含子 Agent 并发执行，线程安全）；
  · 展示：任务结束回到 GUI 线程弹出评分窗（耗时 / 工具·MCP·技能·Agent 调用 / token）；
  · 回传：评分 + 评语 + 指标发回当前会话，提示 Agent 自我反省；
  · 查询：运行时向 LLM 注入 `score_last_task` 工具（schema + 执行分支）。

由插件包 `copilot.json` 的 `services` 声明，主窗口在 GUI 线程调用 `start()`。
"""

import threading
import time

_TOOL_NAME = "score_last_task"
_SCHEMA = {
    "type": "function",
    "function": {
        "name": _TOOL_NAME,
        "description": "查看上一次任务的执行指标：耗时、工具/MCP/技能/子 Agent 各自的"
                       "调用次数、token 消耗。用户问「刚才用了多久/花了多少 token」"
                       "或你想自评效率时调用。",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

_LOCK = threading.Lock()
#: 当前任务采集状态（同一时刻通常只有一个前台任务在跑；depth 用于嵌套子 Agent）
_SESSION = {"t0": 0.0, "tools": {}, "tokens0": {}, "depth": 0}
#: 最近一次任务快照（score_last_task 读取）
_LAST = {}

#: 被替换函数的原值（stop 时还原）
_ORIG = {}
_bridge = None
_state = {"patched": False, "tool_added": False}


# ------------------------------------------------------------
# 采集
# ------------------------------------------------------------
def begin(engine=None) -> None:
    """任务开始：最外层任务才重置计时/计数（子 Agent 嵌套不重开一轮）。"""
    with _LOCK:
        if _SESSION["depth"] == 0:
            _SESSION["t0"] = time.time()
            _SESSION["tools"] = {}
            try:
                _SESSION["tokens0"] = dict(getattr(engine, "tokens", None) or {})
            except Exception:
                _SESSION["tokens0"] = {}
        _SESSION["depth"] += 1


def end(engine=None):
    """任务结束：回到最外层时生成快照，嵌套层返回 None（只弹一次评分窗）。"""
    with _LOCK:
        _SESSION["depth"] = max(0, _SESSION["depth"] - 1)
        root = _SESSION["depth"] == 0
    return snapshot(engine) if root else None


def note_tool(name: str) -> None:
    """记录一次工具调用（可能来自并发工具线程，故走全局锁）。"""
    key = str(name or "").strip()
    if not key:
        return
    with _LOCK:
        if not _SESSION["t0"]:
            _SESSION["t0"] = time.time()
        tools = _SESSION["tools"]
        tools[key] = tools.get(key, 0) + 1


def classify(name: str) -> str:
    """把工具名归类：mcp / skill / agent / tool（评分窗按类分组展示）。"""
    n = str(name or "")
    try:
        from zhuzhu_Copilot.core import agent_plugins
        if n.endswith("-mcp") or n in agent_plugins.plugin_mcp_names():
            return "mcp"
        if n in agent_plugins.plugin_skill_names():
            return "skill"
    except Exception:
        pass
    if n.startswith("sub_") or n in ("dispatch_sub_agents", "explore_project",
                                     "search_large"):
        return "agent"
    return "tool"


def _token_delta(engine) -> dict:
    """本次任务的 token 增量（引擎 tokens 为跨请求累计，需减去任务起点基线）。"""
    cur = getattr(engine, "tokens", None) or {}
    base = _SESSION.get("tokens0") or {}
    try:
        prompt = max(0, int(cur.get("prompt") or 0) - int(base.get("prompt") or 0))
        completion = max(0, int(cur.get("completion") or 0)
                         - int(base.get("completion") or 0))
    except (TypeError, ValueError):
        prompt = completion = 0
    return {"prompt": prompt, "completion": completion,
            "total": prompt + completion}


def snapshot(engine=None) -> dict:
    """生成本次任务的指标快照（耗时 / token / 分类工具清单）。"""
    with _LOCK:
        t0 = float(_SESSION["t0"] or 0.0)
        tools = dict(_SESSION["tools"])
    tokens = _token_delta(engine)
    groups = {"tool": [], "mcp": [], "skill": [], "agent": []}
    for name, cnt in sorted(tools.items(), key=lambda kv: (-kv[1], kv[0])):
        groups.setdefault(classify(name), []).append((name, int(cnt)))
    snap = {
        "seconds": round(max(0.0, time.time() - t0), 1) if t0 else 0.0,
        "tokens": tokens,
        "groups": groups,
        "calls": sum(int(c) for c in tools.values()),
        "started_at": t0,
    }
    global _LAST
    _LAST = snap
    return snap


def last_snapshot() -> dict:
    """最近一次任务快照（score_last_task 工具用）。"""
    return dict(_LAST or {})


def _worth_scoring(snap: dict) -> bool:
    """这轮是否值得弹窗：有工具调用 / 有 token 消耗 / 耗时 ≥1s 之一即可。

    旧实现要求「必须有工具调用」，纯问答任务（如"你好"）跑完毫无反馈，
    用户会误判成插件没生效。
    """
    snap = snap or {}
    return (int(snap.get("calls") or 0) > 0
            or float(snap.get("seconds") or 0) >= 1.0
            or int((snap.get("tokens") or {}).get("total") or 0) > 0)


def format_metrics(snap: dict) -> str:
    """把快照整理成可读文本（对话框 / 工具返回 / 回传消息共用）。"""
    snap = snap or {}
    tokens = snap.get("tokens") or {}
    lines = [
        "执行时间：%.1f 秒" % float(snap.get("seconds") or 0),
        "Token：输入 %d / 输出 %d（合计 %d）" % (
            int(tokens.get("prompt") or 0), int(tokens.get("completion") or 0),
            int(tokens.get("total") or 0)),
        "工具调用：%d 次" % int(snap.get("calls") or 0),
    ]
    labels = (("tool", "工具"), ("mcp", "MCP"), ("skill", "技能"),
              ("agent", "子 Agent"))
    for key, label in labels:
        items = (snap.get("groups") or {}).get(key) or []
        if items:
            lines.append("%s：%s" % (label, "、".join("%s×%d" % (n, c)
                                                    for n, c in items)))
    return "\n".join(lines)


# ------------------------------------------------------------
# 回传
# ------------------------------------------------------------
def feedback_text(snap: dict, rating: int, comment: str = "") -> str:
    """生成发回对话的评分反馈（提示 Agent 自我反省并改进）。"""
    rating = max(1, min(5, int(rating or 0)))
    stars = "★" * rating + "☆" * (5 - rating)
    body = ["【任务评分】用户对本次任务打分：%s（%d/5）" % (stars, rating)]
    cm = str(comment or "").strip()
    if cm:
        body.append("用户评语：%s" % cm)
    body.append("任务指标：\n" + format_metrics(snap))
    body.append(
        "请据此**自我反省**：1) 哪一步做得好、为什么；2) 哪些环节拖慢了时间或浪费了 "
        "token / 工具调用；3) 给出下次同类任务可执行的改进清单（不超过 5 条）。"
        "直接输出反省与改进计划，不要重新执行任务。")
    return "\n\n".join(body)


# ------------------------------------------------------------
# 评分窗（GUI 线程调用）
# ------------------------------------------------------------
def build_dialog(snap: dict, parent=None):
    """构造评分对话框（延迟导入 PyQt，避免非 GUI 环境 import 失败）。"""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPushButton,
                                 QTextEdit, QVBoxLayout)

    class ScoreDialog(QDialog):
        def __init__(self, data: dict, parent=None):
            super().__init__(parent)
            self.rating = 0
            self.comment = ""
            self.setWindowTitle("任务评分 · 帮助 Agent 改进")
            self.setMinimumWidth(460)
            self.setModal(True)
            lay = QVBoxLayout(self)
            lay.setContentsMargins(18, 16, 18, 16)
            lay.setSpacing(10)
            title = QLabel("本次任务表现如何？")
            title.setStyleSheet("font-size: 15px; font-weight: 700;")
            lay.addWidget(title)
            metrics = QLabel(format_metrics(data))
            metrics.setWordWrap(True)
            metrics.setStyleSheet("font-size: 12px;")
            metrics.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            lay.addWidget(metrics)
            row = QHBoxLayout()
            row.addWidget(QLabel("评分："))
            self._btns = []
            for i in range(1, 6):
                b = QPushButton("★" * i)
                b.setCheckable(True)
                b.setAutoDefault(False)
                b.clicked.connect(lambda _c=False, _i=i: self._pick(_i))
                row.addWidget(b)
                self._btns.append(b)
            row.addStretch(1)
            lay.addLayout(row)
            self.edit = QTextEdit()
            self.edit.setPlaceholderText("评语（可选）：哪里好、哪里可以更好…")
            self.edit.setFixedHeight(88)
            lay.addWidget(self.edit)
            btns = QHBoxLayout()
            skip = QPushButton("跳过")
            skip.setAutoDefault(False)
            skip.clicked.connect(self.reject)
            submit = QPushButton("提交评分")
            submit.setDefault(True)
            submit.clicked.connect(self._submit)
            btns.addStretch(1)
            btns.addWidget(skip)
            btns.addWidget(submit)
            lay.addLayout(btns)

        def _pick(self, n: int):
            self.rating = int(n)
            for i, b in enumerate(self._btns, start=1):
                b.setChecked(i <= n)

        def _submit(self):
            if self.rating <= 0:
                self.rating = 4        # 未点星默认 4 分，避免误触无法提交
            self.comment = self.edit.toPlainText().strip()
            self.accept()

    return ScoreDialog(snap, parent)


def _panel():
    """当前 Agent 面板（main.py 在创建后挂到 QApplication 实例上）。"""
    try:
        from PyQt6.QtWidgets import QApplication
        return getattr(QApplication.instance(), "_agent_panel", None)
    except Exception:
        return None


def _send_to_agent(panel, text: str) -> None:
    """把评分反馈作为用户消息发回当前会话（走面板既有发送链路）。

    任务刚结束时 `_task_active` 可能还没被轮询复位，此时 `_send` 只会把消息排进
    队列，而"完成"状态已经处理过、不会再触发自动发送 → 反馈永远发不出去。
    故先等任务标志回落（最多约 6 秒），再调用发送。
    """
    from PyQt6.QtCore import QTimer

    def _deliver(tries: int = 0) -> None:
        if panel is None:
            return
        if getattr(panel, "_task_active", False) and tries < 20:
            QTimer.singleShot(300, lambda: _deliver(tries + 1))
            return
        try:
            panel.input.setPlainText(text)
            panel._send()
        except Exception:
            pass

    _deliver()


def _show(snap: dict) -> None:
    """GUI 线程：弹评分窗，用户提交后回传 Agent。"""
    try:
        panel = _panel()
        dlg = build_dialog(snap, parent=panel)
        if dlg.exec() != dlg.DialogCode.Accepted:
            return
        _send_to_agent(panel, feedback_text(snap, dlg.rating, dlg.comment))
    except Exception:
        pass


def _make_bridge():
    """GUI 线程创建的信号桥：worker 线程 emit → GUI 线程弹窗。"""
    from PyQt6.QtCore import QObject, pyqtSignal

    class _Bridge(QObject):
        scored = pyqtSignal(object)     # dict 快照

        def __init__(self):
            super().__init__()
            self.scored.connect(_show)

    return _Bridge()


# ------------------------------------------------------------
# 安装 / 卸载（运行时 patch，不写源码）
# ------------------------------------------------------------
def _install_tool_schema(agent_tools) -> None:
    """把 score_last_task 注入 LLM 可见工具表（运行时追加，stop 时移除）。"""
    names = {t.get("function", {}).get("name") for t in agent_tools.TOOLS}
    if _TOOL_NAME not in names:
        agent_tools.TOOLS.append(_SCHEMA)
        _state["tool_added"] = True
    _clear_schema_cache(agent_tools)


def _clear_schema_cache(agent_tools) -> None:
    """清掉内置 schema 的语言分桶缓存，使新工具对下一轮请求生效。"""
    try:
        agent_tools._BUILTIN_SCHEMAS.clear()
    except Exception:
        pass


def start():
    """安装运行时钩子（幂等）。返回句柄供 stop() 使用。"""
    global _bridge
    if _state["patched"]:
        return {"stop": stop}
    try:
        from zhuzhu_Copilot.core import agent_engine, agent_tools
    except Exception:
        return {"stop": stop}
    _bridge = _make_bridge()

    # 1) 工具执行：统计调用 + 接管 score_last_task
    _ORIG["execute_tool"] = agent_tools.execute_tool

    def _execute_tool(name, *args, **kwargs):
        if str(name) == _TOOL_NAME:
            return {"text": format_metrics(last_snapshot()), "images": []}
        note_tool(name)
        return _ORIG["execute_tool"](name, *args, **kwargs)

    agent_tools.execute_tool = _execute_tool
    _install_tool_schema(agent_tools)

    # 2) 任务生命周期：记录起止/token 基线，结束时排队弹评分窗
    _ORIG["run"] = agent_engine.AgentEngine.run

    def _run(self, *args, **kwargs):
        begin(self)
        try:
            return _ORIG["run"](self, *args, **kwargs)
        finally:
            snap = end(self)
            # 只要这轮真的跑了（有耗时 / 有 token / 有工具调用）就弹窗；
            # 只要求"有工具调用"会让纯问答任务静默无反馈，用户以为插件没生效。
            if snap and _bridge is not None and _worth_scoring(snap):
                _bridge.scored.emit(snap)     # 排队到 GUI 线程弹窗

    agent_engine.AgentEngine.run = _run
    _state["patched"] = True
    return {"stop": stop}


def stop():
    """卸载运行时钩子（停用插件/退出时调用）。"""
    global _bridge
    if not _state["patched"]:
        return
    try:
        from zhuzhu_Copilot.core import agent_engine, agent_tools
        if _ORIG.get("execute_tool") is not None:
            agent_tools.execute_tool = _ORIG["execute_tool"]
        if _ORIG.get("run") is not None:
            agent_engine.AgentEngine.run = _ORIG["run"]
        if _state["tool_added"]:
            agent_tools.TOOLS[:] = [
                t for t in agent_tools.TOOLS
                if t.get("function", {}).get("name") != _TOOL_NAME]
        _clear_schema_cache(agent_tools)
    except Exception:
        pass
    _ORIG.clear()
    _state["patched"] = False
    _state["tool_added"] = False
    _bridge = None
    with _LOCK:
        _SESSION["depth"] = 0
