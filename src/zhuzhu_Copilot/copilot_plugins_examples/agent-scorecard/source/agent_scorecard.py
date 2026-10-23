"""Agent 能力评分（Copilot 插件 agent-scorecard）。

采集 + 展示 + 回传，三件事：
  · 采集：`note_tool()` 由插件**替换**的 `agent_engine._tool_wait_timeout` 调用，
    每次工具调用记一次（线程安全）；`begin()/snapshot()` 记录任务起止时间与 token；
  · 展示：`ScoreDialog` 显示执行时间、调用过的工具 / MCP / 技能 / Agent 插件、
    token 消耗，用户打 1~5 分并写评语；
  · 回传：`feedback_text()` 生成「评分 + 评语 + 指标」文本，由插件服务发回对话，
    提示 Agent 自我反省并给出改进计划。

本文件是插件 `source/` 下的可读副本；插件实际执行的是 source_ops.json 里
create_file 的 content（两者内容一致）。
"""

import threading
import time

_LOCK = threading.Lock()
#: id(engine) -> {"t0": float, "tools": {name: count}}
_CURRENT = {}
#: 最近一次任务快照（供 score_last_task 工具/对话框读取）
_LAST = {}


# ------------------------------------------------------------
# 采集
# ------------------------------------------------------------

def begin(engine) -> None:
    """任务开始：记录起点并清空工具计数。"""
    try:
        with _LOCK:
            _CURRENT[id(engine)] = {"t0": time.time(), "tools": {}}
    except Exception:
        pass


def note_tool(name: str) -> None:
    """记录一次工具调用（由替换后的 _tool_wait_timeout 调用，engine 线程）。"""
    key = str(name or "").strip()
    if not key:
        return
    try:
        with _LOCK:
            # 归到当前最近开始的引擎（同一时刻通常只有一个前台任务在跑）
            if not _CURRENT:
                _CURRENT["_default"] = {"t0": time.time(), "tools": {}}
            rec = _CURRENT[max(_CURRENT, key=lambda k: _CURRENT[k]["t0"])]
            rec["tools"][key] = rec["tools"].get(key, 0) + 1
    except Exception:
        pass


def classify(name: str) -> str:
    """把工具名归类：mcp / skill / plugin / tool（用于评分窗分类展示）。"""
    n = str(name or "")
    try:
        from zhuzhu_Copilot.core import agent_plugins
        mcp_names = agent_plugins.plugin_mcp_names()
        skill_names = agent_plugins.plugin_skill_names()
        if n.endswith("-mcp") or n in mcp_names:
            return "mcp"
        if n in skill_names:
            return "plugin"
    except Exception:
        pass
    if n.startswith("sub_") or n in ("dispatch_sub_agents", "explore_project",
                                     "search_large"):
        return "agent"
    return "tool"


def snapshot(engine) -> dict:
    """生成本次任务的指标快照（执行时间 / token / 分类工具清单）。"""
    t0 = 0.0
    tools = {}
    try:
        with _LOCK:
            rec = _CURRENT.get(id(engine)) or _CURRENT.get("_default") or {}
            t0 = float(rec.get("t0") or 0.0)
            tools = dict(rec.get("tools") or {})
    except Exception:
        pass
    tokens = {"prompt": 0, "completion": 0, "total": 0}
    try:
        t = getattr(engine, "tokens", None) or {}
        prompt = int(t.get("prompt") or 0)
        completion = int(t.get("completion") or 0)
        tokens = {"prompt": prompt, "completion": completion,
                  "total": prompt + completion}
    except Exception:
        pass
    groups = {"tool": [], "mcp": [], "skill": [], "plugin": [], "agent": []}
    for name, cnt in sorted(tools.items(), key=lambda kv: (-kv[1], kv[0])):
        groups.setdefault(classify(name), []).append((name, int(cnt)))
    total_calls = sum(int(c) for c in tools.values())
    snap = {
        "seconds": round(max(0.0, time.time() - t0), 1) if t0 else 0.0,
        "tokens": tokens,
        "groups": groups,
        "calls": total_calls,
        "started_at": t0,
    }
    global _LAST
    _LAST = snap
    return snap


def last_snapshot() -> dict:
    """最近一次任务快照（工具 score_last_task 用）。"""
    return dict(_LAST or {})


def format_metrics(snap: dict) -> str:
    """把快照整理成可读文本（对话框 / 工具返回 / 回传消息共用）。"""
    snap = snap or {}
    tokens = snap.get("tokens") or {}
    secs = float(snap.get("seconds") or 0)
    lines = [
        "执行时间：%.1f 秒" % secs,
        "Token：输入 %d / 输出 %d（合计 %d）" % (
            int(tokens.get("prompt") or 0), int(tokens.get("completion") or 0),
            int(tokens.get("total") or 0)),
        "工具调用：%d 次" % int(snap.get("calls") or 0),
    ]
    labels = (("tool", "工具"), ("mcp", "MCP"), ("skill", "技能"),
              ("plugin", "Agent 插件"), ("agent", "子 Agent"))
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
