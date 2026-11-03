"""AI 群聊窗口（独立窗口，与主面板的会话状态机完全隔离）。

布局：
- 左栏：成员（名称 / 性格 / 系统级提示词 / 模型）+ 轮数上限 + 工具确认 + 开始/结束；
- 右栏：群聊记录（逐条实时流式追加）+ 用户插话输入。

线程模型：调度在 ``agent_group_chat.GroupChatRunner`` 的工作线程，所有回调经
``evt`` 信号切回主线程渲染 —— 与主面板同一套做法，避免跨线程碰 Qt 控件。
"""

import json
import threading
import time

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QTextBlockFormat, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
    QTextBrowser, QVBoxLayout, QInputDialog, QWidget,
)

from zhuzhu_Copilot.core import agent_agents, agent_group_chat, agent_llm
from zhuzhu_Copilot.core.i18n import ui as _ui, uif as _uif
from zhuzhu_Copilot.ui import agent_edit_dialogs
from zhuzhu_Copilot.ui.styles import PALETTE
from zhuzhu_Copilot.ui.tokens import (
    FONT_BODY, FONT_SMALL, FONT_TITLE, RADIUS_MD, RADIUS_SM,
    SPACING_MD, SPACING_SM,
)

#: 群聊里 Agent 反问用户时的统一回复：不让任何成员把线程卡在等用户输入上
ASK_USER_REPLY = ("（这是群聊，用户只在旁观。不要向用户提问：请直接陈述你的判断，"
                  "或把问题抛给其他成员继续讨论。）")


class GroupChatDialog(QDialog):
    """AI 群聊：多成员轮流发言，用户随时可插话或结束。"""

    evt = pyqtSignal(str, object)

    def __init__(self, parent=None, chat_id: str = ""):
        super().__init__(parent)
        self.setWindowTitle(_ui("AI 群聊"))
        self.setModal(False)
        self.resize(1120, 740)
        self.setMinimumSize(880, 560)
        self._chat = agent_group_chat.load_chat(chat_id) if chat_id else \
            agent_group_chat.new_chat([])
        if self._chat is None:
            self._chat = agent_group_chat.new_chat([])
        self._runner = None
        self._live_name = ""
        self.evt.connect(self._on_evt)
        self._build_ui()
        self._apply_style()
        self._refresh_members()
        self._render_all()
        self._sync_run_buttons()

    # ---------- UI ----------
    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(SPACING_MD, SPACING_MD, SPACING_MD, SPACING_MD)
        root.setSpacing(SPACING_MD)

        # 左栏
        left = QWidget()
        left.setFixedWidth(340)
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
        self.members.setMinimumHeight(180)
        self.members.itemDoubleClicked.connect(lambda *_: self._edit_member())
        lv.addWidget(self.members, 1)

        mrow = QHBoxLayout()
        mrow.setSpacing(6)
        # 成员按钮在讨论进行中一律禁用：运行中改成员会让发言人索引与记录错位
        self._member_btns = []
        for text, fn in ((_ui("添加"), self._add_member),
                         (_ui("编辑"), self._edit_member),
                         (_ui("删除"), self._del_member),
                         (_ui("导入 Agent"), self._import_member)):
            b = QPushButton(text)
            b.setObjectName("gcGhostBtn")
            b.setAutoDefault(False)
            b.clicked.connect(fn)
            mrow.addWidget(b)
            self._member_btns.append(b)
        lv.addLayout(mrow)

        lv.addWidget(self._section(_ui("讨论设置")))
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
        self.clear_btn = QPushButton(_ui("清空记录"))
        self.clear_btn.setObjectName("gcGhostBtn")
        self.clear_btn.setAutoDefault(False)
        self.clear_btn.clicked.connect(self._clear_all)
        brow.addWidget(self.run_btn, 1)
        brow.addWidget(self.clear_btn)
        lv.addLayout(brow)
        root.addWidget(left)

        # 右栏
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(SPACING_SM)
        self.transcript = QTextBrowser()
        self.transcript.setObjectName("gcLog")
        self.transcript.setOpenExternalLinks(True)
        rv.addWidget(self.transcript, 1)

        irow = QHBoxLayout()
        irow.setSpacing(SPACING_SM)
        self.input = QPlainTextEdit()
        self.input.setObjectName("gcInput")
        self.input.setPlaceholderText(_ui("以用户身份插话（会进入群聊记录，下一位发言时可见）"))
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
QListWidget#gcMembers::item {{ padding: 7px 8px; border-radius: {RADIUS_SM}px; }}
QListWidget#gcMembers::item:selected {{ background: {c['primary_light']}; color: {c['text']}; }}
QTextBrowser#gcLog {{ background: {c['card']}; border: none;
                      border-radius: {RADIUS_MD}px; padding: 10px; }}
QPlainTextEdit#gcInput, QSpinBox {{ background: {c['field']}; color: {c['text']};
    border: none; border-radius: {RADIUS_SM}px; padding: 6px;
    selection-background-color: {c['primary']}; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0px; border: none; }}
QPushButton#gcGhostBtn {{ background: transparent; color: {c['text']};
    border: 1px solid {c['border']}; border-radius: {RADIUS_SM}px;
    padding: 6px 12px; font-weight: 600; }}
QPushButton#gcPrimaryBtn {{ background: {c['primary']}; color: #FFFFFF; border: none;
    border-radius: {RADIUS_SM}px; padding: 8px 18px; font-weight: 700; }}
QPushButton#gcPrimaryBtn:disabled {{ background: {c['border']}; color: {c['text_secondary']}; }}
"""
        self.setStyleSheet(qss)

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
            if p.get("model"):
                label += "　·　" + p["model"]
            else:
                label += "　·　" + _ui("跟随全局")
            it = QListWidgetItem(label)
            it.setData(Qt.ItemDataRole.UserRole, p["name"])
            tip = p.get("persona") or p.get("system_prompt") or ""
            it.setToolTip(tip[:400])
            self.members.addItem(it)

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

    def _del_member(self):
        it = self.members.currentItem()
        if it is None:
            return
        name = str(it.data(Qt.ItemDataRole.UserRole) or "")
        self._chat["participants"] = [p for p in (self._chat.get("participants") or [])
                                      if p["name"] != name]
        self._refresh_members()
        self._persist()

    def _import_member(self):
        """从 Agent 管理页已有的自定义 Agent 导入人格（模型仍可单独设置）。"""
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
        p = agent_group_chat.normalize_participant({
            "name": self._unique_name(ag["name"]),
            "persona": ag.get("persona") or "",
            "system_prompt": ag.get("system_prompt") or "",
        })
        self._chat.setdefault("participants", []).append(p)
        self._refresh_members()
        self._persist()

    # ---------- 运行 ----------
    def _sync_run_buttons(self):
        running = self._runner is not None and self._runner.is_alive()
        self.run_btn.setText(_ui("结束讨论") if running else _ui("开始讨论"))
        self.run_btn.setEnabled(True)
        self.clear_btn.setEnabled(not running)
        self.members.setEnabled(not running)
        for b in getattr(self, "_member_btns", []):
            b.setEnabled(not running)
        self.rounds.setEnabled(not running)

    def _toggle_run(self):
        if self._runner is not None and self._runner.is_alive():
            self._runner.stop()
            self.status.setText(_ui("正在结束…"))
            return
        if len(self._chat.get("participants") or []) < 2:
            QMessageBox.information(self, _ui("提示"), _ui("至少需要两个成员才能开始群聊。"))
            return
        self._chat["max_rounds"] = int(self.rounds.value())
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
        self.status.setText(_ui("已开始，等待第一位成员发言…"))
        self._runner.start()
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
            self._runner = None
            self._live_name = ""
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
    def _char_fmt(self, color: str, size: float, bold: bool = False) -> QTextCharFormat:
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        fmt.setFontPointSize(size)
        if bold:
            fmt.setFontWeight(600)
        return fmt

    def _cursor_end(self) -> QTextCursor:
        c = QTextCursor(self.transcript.document())
        c.movePosition(QTextCursor.MoveOperation.End)
        return c

    def _append_header(self, name: str):
        """插入一条消息的发言人标签，并返回已定位到正文起始处的游标。

        消息之间的呼吸靠块上边距（14px）而不是空行：空行会随记录变多把窗口撑成
        大段留白，且复制出来全是空格。
        """
        c = self._cursor_end()
        doc = self.transcript.document()
        head_bf = QTextBlockFormat()
        head_bf.setTopMargin(14)
        if not doc.isEmpty():
            c.insertBlock(head_bf)
        stamp = time.strftime("%H:%M")
        c.insertText(f"{name}  {stamp}",
                     self._char_fmt(PALETTE["text_secondary"], 8.5, True))
        body_bf = QTextBlockFormat()
        body_bf.setTopMargin(2)
        body_bf.setLineHeight(
            128, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        c.insertBlock(body_bf)
        return c

    def _append_delta(self, name: str, text: str):
        if name != self._live_name:
            self._append_header(name)
            self._live_name = name
        c = self._cursor_end()
        c.insertText(text, self._char_fmt(PALETTE["text"], 10))
        self._scroll_bottom()

    def _append_message(self, msg: dict):
        speaker = str(msg.get("speaker") or "?")
        text = str(msg.get("text") or "")
        if speaker == self._live_name and text.strip():
            # 本轮已流式渲染完毕：只落一个新块边界，避免重复显示全文
            self._live_name = ""
            c = self._cursor_end()
            c.insertBlock()
            self._scroll_bottom()
            return
        self._append_static(speaker, text, user=bool(msg.get("user")))

    def _append_static(self, speaker: str, text: str, user: bool = False):
        c = self._append_header((_ui("用户") if user else speaker))
        c.insertText(text, self._char_fmt(
            PALETTE["text"] if not user else PALETTE["primary_hover"], 10))
        self._scroll_bottom()

    def _scroll_bottom(self):
        sb = self.transcript.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _render_all(self):
        self.transcript.clear()
        self._live_name = ""
        msgs = self._chat.get("messages") or []
        if not msgs:
            c = self._cursor_end()
            c.insertText(_ui("还没有发言。点击「开始讨论」由系统随机指定第一位成员开始。"),
                         self._char_fmt(PALETTE["text_secondary"], 10))
            return
        for m in msgs:
            self._append_static(str(m.get("speaker") or "?"),
                                str(m.get("text") or ""),
                                user=bool(m.get("user")))

    # ---------- 用户插话 / 清空 / 收尾 ----------
    def _say(self):
        text = self.input.toPlainText().strip()
        if not text:
            return
        self.input.clear()
        if self._runner is not None and self._runner.is_alive():
            self._runner.say(text)      # 运行中：写入共享记录，下一轮可见
        else:
            msg = {"speaker": _ui("用户"), "text": text, "ts": time.time(), "user": True}
            self._chat.setdefault("messages", []).append(msg)
            self._append_static(_ui("用户"), text, user=True)
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

    def apply_theme(self):
        """主题切换后由主面板调用：重刷样式；空闲时按新色板重绘聊天记录。

        运行中不重绘：重绘会清空文档，正在流式输出的那一条会丢掉已显示的部分。
        """
        self._apply_style()
        running = self._runner is not None and self._runner.is_alive()
        if not running:
            self._render_all()

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
