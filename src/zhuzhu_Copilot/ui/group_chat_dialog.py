"""AI 群聊窗口（独立窗口，与主面板的会话状态机完全隔离）。

布局：
- 左栏：成员（头像 / 名称 / 性格 / 系统级提示词 / 模型）+ 轮数上限 + 工具确认 +
  开始 / 暂停 / 结束 / 存为 Agent；
- 右栏：群聊记录（复用主面板的聊天气泡：Agent 用 Markdown 富文本、用户用深蓝气泡）+
  用户插话输入。

线程模型：调度在 ``agent_group_chat.GroupChatRunner`` 的工作线程，所有回调经
``evt`` 信号切回主线程渲染 —— 与主面板同一套做法，避免跨线程碰 Qt 控件。
流式渲染按 60ms 节流：Markdown 重解析比纯文本插入贵，逐 token 重排会卡住窗口。
"""

import json
import re
import threading
import time

from PyQt6.QtCore import QEvent, QSize, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox,
    QVBoxLayout, QInputDialog, QWidget,
)

from zhuzhu_Copilot.core import (
    agent_agents, agent_avatar, agent_group_chat, agent_llm,
)
from zhuzhu_Copilot.core.i18n import ui as _ui, uif as _uif
from zhuzhu_Copilot.ui import agent_chat_bubbles as chat_bubbles
from zhuzhu_Copilot.ui import agent_edit_dialogs
from zhuzhu_Copilot.ui import avatars as _avatars
from zhuzhu_Copilot.ui.styles import PALETTE
from zhuzhu_Copilot.ui.tokens import (
    FONT_BODY, FONT_SMALL, FONT_TITLE, RADIUS_MD, RADIUS_SM,
    SPACING_MD, SPACING_SM,
)

#: 群聊里 Agent 反问用户时的统一回复：不让任何成员把线程卡在等用户输入上
ASK_USER_REPLY = ("（这是群聊，用户只在旁观。不要向用户提问：请直接陈述你的判断，"
                  "或把问题抛给其他成员继续讨论。）")
#: 流式渲染节流（ms）：Markdown 全量重解析的代价决定不能逐 token 重排
_STREAM_MS = 60
#: 头像尺寸（px）：消息头 / 成员列表
_AVATAR_MSG = 26
_AVATAR_ITEM = 22

_MD_RENDER = None


def _render_md(raw: str) -> str:
    """复用主面板的 Markdown 渲染（同一套解析与主题配色）。

    懒加载：agent_panel 是 1.7MB 的大模块，群聊只有真正渲染时才需要它；
    生产路径下面板早已加载，这里命中缓存零成本。
    """
    global _MD_RENDER
    if _MD_RENDER is None:
        from zhuzhu_Copilot.ui.agent_panel import render_markdown
        _MD_RENDER = render_markdown
    return _MD_RENDER(raw or "")


class _AgentRow(QWidget):
    """一条 Agent 发言：圆形头像 + 名字/时间 + Markdown 正文（复用 RichBlock）。"""

    def __init__(self, style, name: str, avatar_ref: str, ts: float = 0,
                 error: bool = False, parent=None):
        super().__init__(parent)
        self._style = style
        self._name = name
        self._ref = avatar_ref
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 10, 0, 0)
        v.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)
        self.avatar = QLabel()
        self.avatar.setFixedSize(_AVATAR_MSG, _AVATAR_MSG)
        head.addWidget(self.avatar, 0, Qt.AlignmentFlag.AlignTop)
        self.name_lbl = QLabel(name)
        self.time_lbl = QLabel(time.strftime("%H:%M", time.localtime(ts or time.time())))
        head.addWidget(self.name_lbl)
        head.addWidget(self.time_lbl)
        head.addStretch(1)
        v.addLayout(head)
        color = PALETTE["danger"] if error else None
        self.body = chat_bubbles.RichBlock(style, font_size=FONT_BODY, color=color)
        # 正文缩进到头像右侧，与名字左对齐；RichBlock 的高度计算已把该边距计入
        self.body.layout().setContentsMargins(_AVATAR_MSG + 6, 2, 0, 0)
        v.addWidget(self.body)
        self._restyle_head()

    def _restyle_head(self):
        c = PALETTE
        self.avatar.setPixmap(_avatars.avatar_pixmap(
            self._ref, self._name, _AVATAR_MSG, kind="group", bg=c["field"]))
        self.name_lbl.setStyleSheet(
            f"color: {c['text']}; font-size: {FONT_SMALL}px; font-weight: 600;"
            " background: transparent;")
        self.time_lbl.setStyleSheet(
            f"color: {c['text_secondary']}; font-size: {FONT_SMALL - 1}px;"
            " background: transparent;")

    def set_markdown(self, raw: str):
        self.body.set_html(_render_md(raw))

    def restyle(self, style):
        self._style = style
        self.body.restyle(style)
        self._restyle_head()


class _UserRow(QWidget):
    """一条用户发言：右对齐的深蓝气泡（复用主面板 UserBubble）。"""

    def __init__(self, style, text: str, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 10, 0, 0)
        h.setSpacing(0)
        h.addStretch(1)
        self.bubble = chat_bubbles.UserBubble(style)
        # 用户输入按纯文本渲染：防止文本里的 < 被当成富文本标签吞掉内容
        self.bubble.setTextFormat(Qt.TextFormat.PlainText)
        self.bubble.setText(text or "")
        self.bubble.setMaximumWidth(560)
        h.addWidget(self.bubble, 0, Qt.AlignmentFlag.AlignTop)

    def restyle(self, style):
        self.bubble.restyle(style)


class GroupChatDialog(QDialog):
    """AI 群聊：多成员轮流发言，用户随时可插话、暂停或结束。"""

    evt = pyqtSignal(str, object)

    def __init__(self, parent=None, chat_id: str = ""):
        super().__init__(parent)
        self.setWindowTitle(_ui("AI 群聊"))
        self.setModal(False)
        self.resize(1180, 780)
        self.setMinimumSize(900, 580)
        self._chat = agent_group_chat.load_chat(chat_id) if chat_id else \
            agent_group_chat.new_chat([])
        if self._chat is None:
            self._chat = agent_group_chat.new_chat([])
        self._runner = None
        self._rows = []             # 已渲染的消息控件（换肤时原地 restyle）
        self._live_name = ""
        self._live_buf = ""
        self._live_row = None
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(_STREAM_MS)
        self._render_timer.timeout.connect(self._flush_live)
        self._style = self._chat_style()
        self.evt.connect(self._on_evt)
        self._build_ui()
        self._apply_style()
        self._refresh_members()
        self._render_all()
        self._sync_run_buttons()

    # ---------- 主题 / 气泡样式 ----------
    def _chat_style(self):
        """气泡样式快照：优先用主面板的（同一套映射），无面板时按色板兜底。"""
        p = self.parent()
        fn = getattr(p, "_chat_style", None)
        if callable(fn):
            try:
                return fn()
            except Exception:
                pass
        c = PALETTE
        return chat_bubbles.ChatStyle(
            card=c["card"], border=c["border"], border_soft=c["border"],
            dash=c["border"], text=c["text"], text_dim=c["text_secondary"],
            accent=c["primary"], muted=c["text_secondary"], icon_shell=c["field"],
            icon_color=c["text_secondary"], tag_bg=c["field"],
            tag_fg=c["text_secondary"], user_bg=c["primary"], user_fg="#FFFFFF",
            cmd_fg=c["text"], ok_fg=c["primary"], hover=c["hover"],
            panel=c["card"], bg=c["bg_top"])

    # ---------- UI ----------
    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(SPACING_MD, SPACING_MD, SPACING_MD, SPACING_MD)
        root.setSpacing(SPACING_MD)

        left = QWidget()
        left.setFixedWidth(360)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(SPACING_SM)

        title = QLabel(_ui("AI 群聊"))
        title.setObjectName("gcTitle")
        lv.addWidget(title)
        hint = QLabel(_ui("多个 Agent 在同一段对话里轮流发言：系统随机指定第一位开始，"
                          "之后由发言人 @ 下一位或随机接棒。"))
        hint.setObjectName("gcHint")
        hint.setWordWrap(True)
        lv.addWidget(hint)

        lv.addWidget(self._section(_ui("成员")))
        self.members = QListWidget()
        self.members.setObjectName("gcMembers")
        self.members.setMinimumHeight(140)
        self.members.setIconSize(QSize(_AVATAR_ITEM, _AVATAR_ITEM))
        self.members.itemDoubleClicked.connect(lambda *_: self._edit_member())
        lv.addWidget(self.members, 1)

        mrow = QHBoxLayout()
        mrow.setSpacing(6)
        self._member_btns = []
        for text, fn in ((_ui("添加"), self._add_member),
                         (_ui("编辑"), self._edit_member),
                         (_ui("删除"), self._del_member),
                         (_ui("导入"), self._import_member),
                         (_ui("存为 Agent"), self._save_as_agent)):
            b = QPushButton(text)
            b.setObjectName("gcGhostBtn")
            b.setAutoDefault(False)
            b.clicked.connect(fn)
            mrow.addWidget(b)
            self._member_btns.append(b)
        lv.addLayout(mrow)

        lv.addWidget(self._section(_ui("讨论设置")))
        # 讨论主题：用户可自定义议题。每轮随消息重发（长对话防跑题），
        # 同时作为共同上下文空间的议题种子。
        self.topic_edit = QPlainTextEdit()
        self.topic_edit.setObjectName("gcInput")
        self.topic_edit.setPlaceholderText(_ui(
            "讨论主题（留空则自由开聊）\n如：给桌面应用定下 v2 的功能优先级"))
        self.topic_edit.setFixedHeight(54)
        self.topic_edit.setToolTip(_ui("主题会随每轮发言一起发送，长对话也不跑题；讨论开始后不可修改"))
        self.topic_edit.setPlainText(str(self._chat.get("topic") or ""))
        self.topic_edit.textChanged.connect(self._on_topic_changed)
        lv.addWidget(self.topic_edit)
        # 输入即改内存，落盘去抖：避免每敲一个字都写一次 JSON
        self._topic_save = QTimer(self)
        self._topic_save.setSingleShot(True)
        self._topic_save.setInterval(800)
        self._topic_save.timeout.connect(self._persist)

        srow = QHBoxLayout()
        srow.setSpacing(6)
        lab = QLabel(_ui("最多轮数"))
        lab.setObjectName("gcHint")
        srow.addWidget(lab)
        self.rounds = QSpinBox()
        self.rounds.setRange(0, 999)
        self.rounds.setValue(int(self._chat.get("max_rounds") or 0))
        self.rounds.setSpecialValueText(_ui("不限"))
        self.rounds.setFixedWidth(84)
        srow.addWidget(self.rounds)
        srow.addStretch(1)
        lv.addLayout(srow)

        self.confirm_check = QCheckBox(_ui("工具执行前逐个确认（默认自动放行）"))
        self.confirm_check.setChecked(False)
        lv.addWidget(self.confirm_check)

        self.status = QLabel(_ui("未开始"))
        self.status.setObjectName("gcHint")
        self.status.setWordWrap(True)
        lv.addWidget(self.status)

        brow = QHBoxLayout()
        brow.setSpacing(SPACING_SM)
        self.run_btn = QPushButton(_ui("开始讨论"))
        self.run_btn.setObjectName("gcPrimaryBtn")
        self.run_btn.setAutoDefault(False)
        self.run_btn.clicked.connect(self._toggle_run)
        self.pause_btn = QPushButton(_ui("暂停"))
        self.pause_btn.setObjectName("gcGhostBtn")
        self.pause_btn.setAutoDefault(False)
        self.pause_btn.setToolTip(_ui("暂停：当前这一轮说完后停住，点「继续」从下一位接着聊"))
        self.pause_btn.clicked.connect(self._toggle_pause)
        self.clear_btn = QPushButton(_ui("清空"))
        self.clear_btn.setObjectName("gcGhostBtn")
        self.clear_btn.setAutoDefault(False)
        self.clear_btn.clicked.connect(self._clear_all)
        brow.addWidget(self.run_btn, 1)
        brow.addWidget(self.pause_btn)
        brow.addWidget(self.clear_btn)
        lv.addLayout(brow)
        root.addWidget(left)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(SPACING_SM)
        self.log = QScrollArea()
        self.log.setObjectName("gcLog")
        self.log.setWidgetResizable(True)
        self.log.setFrameShape(QFrame.Shape.NoFrame)
        self.log.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.log_host = QWidget()
        self.log_host.setObjectName("gcLogHost")
        self.log_lay = QVBoxLayout(self.log_host)
        self.log_lay.setContentsMargins(14, 4, 14, 8)
        self.log_lay.setSpacing(0)
        self.log_lay.addStretch(1)
        self.log.setWidget(self.log_host)
        rv.addWidget(self.log, 1)

        irow = QHBoxLayout()
        irow.setSpacing(SPACING_SM)
        self.input = QPlainTextEdit()
        self.input.setObjectName("gcInput")
        self.input.setPlaceholderText(_ui("以用户身份插话（会进入共同上下文，下一位发言时可见）"))
        self.input.setFixedHeight(58)
        self.input.installEventFilter(self)
        send = QPushButton(_ui("插话"))
        send.setObjectName("gcGhostBtn")
        send.setAutoDefault(False)
        send.clicked.connect(self._say)
        irow.addWidget(self.input, 1)
        irow.addWidget(send)
        rv.addLayout(irow)
        root.addWidget(right, 1)

    def _section(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("gcSection")
        return lab

    def _apply_style(self):
        c = PALETTE
        qss = f"""
QDialog {{ background: {c['bg_top']}; }}
QWidget {{ color: {c['text']}; font-size: {FONT_BODY}px; }}
QLabel#gcTitle {{ font-size: {FONT_TITLE}px; font-weight: 600; }}
QLabel#gcHint {{ color: {c['text_secondary']}; font-size: {FONT_SMALL}px; }}
QLabel#gcSection {{ color: {c['text_secondary']}; font-size: {FONT_SMALL}px;
                    font-weight: 600; margin-top: 6px; }}
QListWidget#gcMembers {{ background: {c['card']}; border: none;
                         border-radius: {RADIUS_MD}px; padding: 6px; }}
QListWidget#gcMembers::item {{ padding: 6px 8px; border-radius: {RADIUS_SM}px; }}
QListWidget#gcMembers::item:selected {{ background: {c['primary_light']}; color: {c['text']}; }}
QScrollArea#gcLog {{ background: {c['card']}; border: none;
                     border-radius: {RADIUS_MD}px; }}
QWidget#gcLogHost {{ background: {c['card']}; }}
QPlainTextEdit#gcInput, QSpinBox {{ background: {c['field']}; color: {c['text']};
    border: none; border-radius: {RADIUS_SM}px; padding: 6px;
    selection-background-color: {c['primary']}; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0px; border: none; }}
QPushButton#gcGhostBtn {{ background: transparent; color: {c['text']};
    border: 1px solid {c['border']}; border-radius: {RADIUS_SM}px;
    padding: 6px 12px; font-weight: 600; }}
QPushButton#gcGhostBtn:disabled {{ color: {c['text_secondary']}; }}
QPushButton#gcPrimaryBtn {{ background: {c['primary']}; color: #FFFFFF; border: none;
    border-radius: {RADIUS_SM}px; padding: 8px 18px; font-weight: 700; }}
QPushButton#gcPrimaryBtn:disabled {{ background: {c['border']}; color: {c['text_secondary']}; }}
"""
        self.setStyleSheet(qss)

    def apply_theme(self):
        """主题切换后由主面板调用：换肤并就地重绘已有消息（不重建窗口、不中断讨论）。"""
        self._style = self._chat_style()
        self._apply_style()
        for row in self._rows:
            try:
                row.restyle(self._style)
            except Exception:
                pass
        self.members.setIconSize(QSize(_AVATAR_ITEM, _AVATAR_ITEM))
        self._refresh_member_icons()

    # ---------- 成员 ----------
    def _model_options(self) -> list:
        """[(展示文本, (provider, model))]：首项跟随全局，其余按服务商展开。"""
        opts = [(_ui("跟随全局（自动选择）"), ("", ""))]
        try:
            cfg = agent_llm.load_model_config()
        except Exception:
            cfg = {}
        for p in cfg.get("providers") or []:
            name = str(p.get("name") or "")
            for m in (p.get("models") or []):
                opts.append((f"{name} / {m}" if name else str(m), (name, str(m))))
        return opts

    def _refresh_members(self):
        self.members.clear()
        for p in self._chat.get("participants") or []:
            label = p["name"]
            label += "　·　" + (p["model"] or _ui("跟随全局"))
            it = QListWidgetItem(label)
            it.setData(Qt.ItemDataRole.UserRole, p["name"])
            it.setIcon(QIcon(_avatars.avatar_pixmap(
                p.get("avatar"), p["name"], _AVATAR_ITEM, kind="group")))
            it.setSizeHint(QSize(0, 34))
            tip = p.get("persona") or p.get("system_prompt") or ""
            it.setToolTip(tip[:400])
            self.members.addItem(it)

    def _refresh_member_icons(self):
        """只重刷头像图标（换肤/换头像时不必重建列表项）。"""
        parts = {p["name"]: p for p in self._chat.get("participants") or []}
        for i in range(self.members.count()):
            it = self.members.item(i)
            name = str(it.data(Qt.ItemDataRole.UserRole) or "")
            p = parts.get(name) or {}
            it.setIcon(QIcon(_avatars.avatar_pixmap(
                p.get("avatar"), name, _AVATAR_ITEM, kind="group")))

    def _unique_name(self, base: str) -> str:
        names = {p["name"] for p in self._chat.get("participants") or []}
        if base not in names:
            return base
        i = 2
        while f"{base}{i}" in names:
            i += 1
        return f"{base}{i}"

    def _add_member(self):
        vals = agent_edit_dialogs.edit_group_participant(
            self, None, self._model_options())
        if not vals:
            return
        vals["name"] = self._unique_name(vals["name"])
        self._chat.setdefault("participants", []).append(
            agent_group_chat.normalize_participant(vals))
        self._refresh_members()
        self._persist()
        self._gc_group_avatars()

    def _edit_member(self):
        it = self.members.currentItem()
        if it is None:
            return
        name = str(it.data(Qt.ItemDataRole.UserRole) or "")
        parts = self._chat.get("participants") or []
        idx = next((i for i, p in enumerate(parts) if p["name"] == name), -1)
        if idx < 0:
            return
        vals = agent_edit_dialogs.edit_group_participant(
            self, parts[idx], self._model_options())
        if not vals:
            return
        parts[idx] = agent_group_chat.normalize_participant(vals)
        self._refresh_members()
        self._persist()
        self._gc_group_avatars()
        # 头像/名字可能变了：历史消息按名字取头像，重绘一次保持一致
        self._live_name = ""
        self._render_all()

    def _del_member(self):
        it = self.members.currentItem()
        if it is None:
            return
        name = str(it.data(Qt.ItemDataRole.UserRole) or "")
        self._chat["participants"] = [p for p in (self._chat.get("participants") or [])
                                      if p["name"] != name]
        self._refresh_members()
        self._persist()
        self._gc_group_avatars()

    def _import_member(self):
        """从 Agent 管理页已有的自定义 Agent 导入人格（模型/头像一并带入）。"""
        agents = agent_agents.list_agents()
        if not agents:
            QMessageBox.information(self, _ui("提示"),
                                    _ui("暂无自定义 Agent。请先在设置 - Agent 管理中创建。"))
            return
        names = [a["name"] for a in agents]
        name, ok = QInputDialog.getItem(
            self, _ui("导入 Agent"), _ui("选择要导入的 Agent（人格与提示词一起带入）："),
            names, 0, False)
        if not ok or not name:
            return
        ag = next((a for a in agents if a["name"] == name), None)
        if not ag:
            return
        # 头像跨 kind 复制：删除原 Agent 时不能把群聊里正在用的头像一起删掉
        ref = agent_avatar.copy_to(ag.get("avatar"), "agent", "group") \
            if ag.get("avatar") else ""
        p = agent_group_chat.normalize_participant({
            "name": self._unique_name(ag["name"]),
            "persona": ag.get("persona") or "",
            "system_prompt": ag.get("system_prompt") or "",
            "avatar": ref,
        })
        self._chat.setdefault("participants", []).append(p)
        self._refresh_members()
        self._persist()

    def _save_as_agent(self):
        """把选中的群聊成员保存为可 @ 调用的自定义 Agent（含头像）。"""
        it = self.members.currentItem()
        if it is None:
            QMessageBox.information(self, _ui("提示"), _ui("请先选择要保存的成员"))
            return
        name = str(it.data(Qt.ItemDataRole.UserRole) or "")
        member = next((p for p in (self._chat.get("participants") or [])
                       if p["name"] == name), None)
        if not member:
            return
        # 成员名可中文，而 @ 调用要求 ASCII：给一个合规的默认名，用户可改
        suggested = re.sub(r"[^A-Za-z0-9_-]", "", name) or "agent"
        agent_name, ok = QInputDialog.getText(
            self, _ui("存为 Agent"),
            _ui("保存后可在主对话输入框用 @名称 调用。\nAgent 名称（仅字母/数字/下划线/短横线）："),
            text=suggested)
        if not ok or not agent_name.strip():
            return
        ok2, msg = agent_group_chat.export_member(self._chat, name, agent_name.strip())
        if not ok2:
            QMessageBox.warning(self, _ui("保存失败"), msg)
            return
        p = self.parent()
        if p is not None and hasattr(p, "_invalidate_cmd_cache"):
            p._invalidate_cmd_cache()
        if p is not None and hasattr(p, "_gc_agent_avatars"):
            p._gc_agent_avatars()
        QMessageBox.information(self, _ui("已保存"), _uif(
            "{a0}\n\n在主对话输入 @{a1} 即可切换到该人格。", a0=msg, a1=agent_name.strip()))

    def _gc_group_avatars(self):
        """回收未被任何群聊引用的成员头像（换图/删除成员留下的孤儿）。"""
        try:
            used = [p.get("avatar")
                    for c in agent_group_chat.list_chats()
                    for p in (c.get("participants") or [])]
            agent_avatar.gc("group", used)
        except Exception:
            pass

    # ---------- 运行 ----------
    # ---------- 讨论设置 ----------
    def _on_topic_changed(self):
        """主题输入即同步到内存配置，并去抖落盘。"""
        self._chat["topic"] = self.topic_edit.toPlainText().strip()
        timer = getattr(self, "_topic_save", None)
        if timer is not None:
            timer.start()

    def _sync_run_buttons(self):
        running = self._runner is not None and self._runner.is_alive()
        paused = bool(running and self._runner.paused)
        self.run_btn.setText(_ui("结束讨论") if running else _ui("开始讨论"))
        self.run_btn.setEnabled(True)
        self.pause_btn.setEnabled(running)
        self.pause_btn.setText(_ui("继续") if paused else _ui("暂停"))
        self.clear_btn.setEnabled(not running)
        self.members.setEnabled(not running)
        for b in getattr(self, "_member_btns", []):
            b.setEnabled(not running)
        self.rounds.setEnabled(not running)
        self.topic_edit.setEnabled(not running)

    def _toggle_run(self):
        if self._runner is not None and self._runner.is_alive():
            self._runner.stop()
            self.status.setText(_ui("正在结束…"))
            return
        if len(self._chat.get("participants") or []) < 2:
            QMessageBox.information(self, _ui("提示"), _ui("至少需要两个成员才能开始群聊。"))
            return
        self._chat["max_rounds"] = int(self.rounds.value())
        self._chat["topic"] = self.topic_edit.toPlainText().strip()
        ok, msg = agent_group_chat.save_chat(self._chat)
        if not ok:
            QMessageBox.warning(self, _ui("保存失败"), msg)
        try:
            cfg = agent_llm.load_model_config()
        except Exception:
            cfg = {}
        self._runner = agent_group_chat.GroupChatRunner(
            self._chat, cfg,
            on_status=lambda t: self.evt.emit("status", t),
            on_delta=lambda n, t: self.evt.emit("delta", (n, t)),
            on_message=lambda m: self.evt.emit("message", m),
            on_end=lambda r: self.evt.emit("end", r),
            confirm=self._confirm_from_worker,
            ask_user=lambda _a: ASK_USER_REPLY)
        self._live_name = ""
        self._live_buf = ""
        self._live_row = None
        self.status.setText(_ui("已开始，等待第一位成员发言…"))
        self._runner.start()
        self._sync_run_buttons()

    def _toggle_pause(self):
        r = self._runner
        if r is None or not r.is_alive():
            return
        if r.paused:
            r.resume()
            self.status.setText(_ui("已继续"))
        else:
            r.pause()
            self.status.setText(_ui("当前一轮说完后暂停…"))
        self._sync_run_buttons()

    def _confirm_from_worker(self, name: str, args: dict) -> bool:
        """工作线程调用：需要确认时切到主线程弹窗，并阻塞等待结果（超时=拒绝）。"""
        if not self.confirm_check.isChecked():
            return True
        box, ev = {}, threading.Event()
        self.evt.emit("confirm", (name, args, ev, box))
        if not ev.wait(120):
            return False
        return bool(box.get("ok"))

    # ---------- 事件（主线程） ----------
    def _on_evt(self, kind: str, payload):
        if kind == "status":
            self.status.setText(str(payload))
        elif kind == "delta":
            name, text = payload
            self._append_delta(str(name), str(text))
        elif kind == "message":
            msg = payload
            # 落盘：群聊是独立窗口，崩溃/关窗都不该丢记录（一条几百字节，频率很低）
            self._chat.setdefault("messages", []).append(msg)
            self._persist()
            self._append_message(msg)
        elif kind == "end":
            self.status.setText(_uif("已结束：{a0}", a0=str(payload)))
            self._flush_live(final=True)
            self._runner = None
            self._persist()
            self._sync_run_buttons()
        elif kind == "confirm":
            self._ask_confirm(payload)

    def _ask_confirm(self, payload):
        name, args, ev, box = payload
        try:
            body = json.dumps(args, ensure_ascii=False)[:900]
        except Exception:
            body = str(args)[:900]
        ret = QMessageBox.question(
            self, _ui("工具执行确认"),
            _uif("{a0}\n\n参数：{a1}\n\n允许本次执行？", a0=name, a1=body))
        box["ok"] = ret == QMessageBox.StandardButton.Yes
        ev.set()

    # ---------- 渲染 ----------
    def _at_bottom(self, slack: int = 60) -> bool:
        bar = self.log.verticalScrollBar()
        return bar.value() >= bar.maximum() - slack

    def _scroll_bottom(self):
        bar = self.log.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _insert_row(self, row: QWidget, stick: bool = True):
        self.log_lay.insertWidget(self.log_lay.count() - 1, row)
        self._rows.append(row)
        if stick:
            # 布局尚未完成时 maximum 还是旧值：下一帧再落底
            self._scroll_bottom()
            QTimer.singleShot(0, self._scroll_bottom)

    def _avatar_of(self, name: str) -> str:
        p = next((x for x in (self._chat.get("participants") or [])
                  if x.get("name") == name), None)
        return (p or {}).get("avatar") or ""

    def _append_delta(self, name: str, text: str):
        if name != self._live_name:
            self._flush_live(final=True)
            self._live_name = name
            self._live_buf = ""
            stick = self._at_bottom()
            self._live_row = _AgentRow(self._style, name, self._avatar_of(name))
            self._insert_row(self._live_row, stick)
        self._live_buf += text
        if not self._render_timer.isActive():
            self._render_timer.start()

    def _flush_live(self, final: bool = False):
        if self._live_row is None:
            return
        stick = self._at_bottom()
        self._live_row.set_markdown(self._live_buf)
        if stick:
            QTimer.singleShot(0, self._scroll_bottom)
        if final:
            self._live_row = None
            self._live_buf = ""
            self._live_name = ""

    def _append_message(self, msg: dict):
        speaker = str(msg.get("speaker") or "?")
        text = str(msg.get("text") or "")
        if msg.get("user"):
            self._insert_row(_UserRow(self._style, text))
            return
        if speaker == self._live_name and self._live_buf.strip() and self._live_row is not None:
            # 本轮已流式渲染完毕：把最终文本再对齐一次（避免半解析的 Markdown 残留）
            self._live_row.set_markdown(text)
            self._flush_live(final=True)
            return
        row = _AgentRow(self._style, speaker, self._avatar_of(speaker),
                        ts=float(msg.get("ts") or 0), error=bool(msg.get("error")))
        row.set_markdown(text)
        self._insert_row(row)

    def _clear_rows(self):
        while self.log_lay.count() > 1:
            item = self.log_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._rows = []
        self._live_row = None
        self._live_buf = ""
        self._live_name = ""

    def _render_all(self):
        self._clear_rows()
        msgs = self._chat.get("messages") or []
        if not msgs:
            hint = QLabel(_ui("还没有发言。点击「开始讨论」由系统随机指定第一位成员开始。"))
            hint.setObjectName("gcHint")
            hint.setWordWrap(True)
            self.log_lay.insertWidget(0, hint)
            return
        for m in msgs:
            speaker = str(m.get("speaker") or "?")
            text = str(m.get("text") or "")
            if m.get("user"):
                self._insert_row(_UserRow(self._style, text), stick=False)
                continue
            row = _AgentRow(self._style, speaker, self._avatar_of(speaker),
                            ts=float(m.get("ts") or 0), error=bool(m.get("error")))
            row.set_markdown(text)
            self._insert_row(row, stick=False)
        QTimer.singleShot(0, self._scroll_bottom)

    # ---------- 用户插话 / 清空 / 收尾 ----------
    def _say(self):
        text = self.input.toPlainText().strip()
        if not text:
            return
        self.input.clear()
        if self._runner is not None and self._runner.is_alive():
            self._runner.say(text)      # 运行中：写入共享上下文，下一轮可见
        else:
            msg = {"speaker": _ui("用户"), "text": text, "ts": time.time(), "user": True}
            self._chat.setdefault("messages", []).append(msg)
            self._insert_row(_UserRow(self._style, text))
            self._persist()

    def _clear_all(self):
        if self._runner is not None and self._runner.is_alive():
            return
        ret = QMessageBox.question(self, _ui("确认清空"),
                                   _ui("清空本次群聊的全部记录？成员配置会保留。"))
        if ret != QMessageBox.StandardButton.Yes:
            return
        self._chat["messages"] = []
        self._persist()
        self._render_all()

    def _persist(self):
        """落盘；空群聊（没有成员也没有发言）不写文件，避免打开就关留下垃圾配置。"""
        if not (self._chat.get("participants") or self._chat.get("messages")):
            return False
        return agent_group_chat.save_chat(self._chat)[0]

    def eventFilter(self, obj, ev):
        """输入框 Ctrl+Enter 发送（Enter 换行，避免误发长段插话）。"""
        if obj is self.input and ev.type() == QEvent.Type.KeyPress:
            if (ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                    and ev.modifiers() & Qt.KeyboardModifier.ControlModifier):
                self._say()
                return True
        return super().eventFilter(obj, ev)

    def closeEvent(self, ev):
        try:
            if self._runner is not None and self._runner.is_alive():
                self._runner.stop()
        finally:
            # 断开事件信号：线程收尾时可能还有一条在途消息，窗口已销毁会抛 RuntimeError
            try:
                self.evt.disconnect(self._on_evt)
            except Exception:
                pass
            self._persist()
            super().closeEvent(ev)
