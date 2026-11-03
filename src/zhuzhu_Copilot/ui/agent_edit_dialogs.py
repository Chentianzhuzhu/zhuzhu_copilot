"""自定义 Agent / 子 Agent / 群聊成员的编辑表单（管理页与群聊窗口共用）。

三个入口：
- :func:`edit_custom_agent` —— 名称 / 头像 / 性格 / 系统级提示词 / 绑定对话流；
- :func:`edit_sub_agent`    —— 名称 / 描述 / 任务指令 / 允许工具 / 性格 / 系统级提示词；
- :func:`edit_group_participant` —— 群聊成员：名称 / 头像 / 性格 / 提示词 / 模型。

表单只有四种控件（单行 / 多行 / 下拉 / 头像），统一由 ``_FormDialog`` 渲染：新增
字段只加一条 ``Field`` 声明，不需要再写对话框类。校验只做「必填」与「名称可用」
这类表单级判断，真正的合法性（保留名、工作流是否存在）仍由 core 层裁决 —— 单一
事实来源，避免 UI 与存储层的规则漂移。
"""

import re

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QMessageBox, QFileDialog, QWidget,
)

from zhuzhu_Copilot.core import agent_avatar
from zhuzhu_Copilot.core.i18n import ui as _ui
from zhuzhu_Copilot.ui import avatars as _avatars
from zhuzhu_Copilot.ui.styles import PALETTE
from zhuzhu_Copilot.ui.tokens import (
    FONT_BODY, FONT_SMALL, RADIUS_SM, SPACING_MD, SPACING_SM,
)
from zhuzhu_Copilot.ui.widgets import ArrowComboBox, add_brand_footer

#: 自定义 Agent 名：与 core/agent_agents._NAME_RE 同规则（core 层仍会再校验）
_CUSTOM_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
#: 子 Agent 名：与 core/agent_subagent._SUB_NAME_RE 同规则
_SUB_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,50}$")


class Field:
    """一个表单字段的声明（kind: text / multiline / combo / avatar）。"""

    def __init__(self, key: str, label: str, value: str = "", kind: str = "text",
                 placeholder: str = "", height: int = 96, options: list = None,
                 required: bool = False, editable: bool = True,
                 store_kind: str = "agent"):
        self.key = key
        self.label = label
        self.value = value or ""
        self.kind = kind
        self.placeholder = placeholder
        self.height = height
        self.options = options or []      # combo: [(展示文本, 值)]
        self.required = required
        self.editable = editable
        self.store_kind = store_kind      # avatar: agent / group


class _AvatarPicker(QWidget):
    """头像选择器：圆形预览 + 选择/移除。

    选图即刻落盘（uuid 命名，与名称无关），返回引用；旧图不在这里删 —— 编辑中途
    取消时若删了旧图，已保存的 Agent 会变成「头像丢失」。孤儿文件统一由
    ``agent_avatar.gc()`` 在保存成功后按全量引用对账回收。
    """

    SIZE = 44

    def __init__(self, name_hint: str, ref: str, store_kind: str = "agent",
                 parent=None):
        super().__init__(parent)
        self._name = name_hint or ""
        self._ref = str(ref or "")
        self._kind = store_kind
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACING_SM)
        self.preview = QLabel()
        self.preview.setFixedSize(self.SIZE, self.SIZE)
        row.addWidget(self.preview)
        self.pick_btn = QPushButton(_ui("选择图片"))
        self.pick_btn.setAutoDefault(False)
        self.pick_btn.setObjectName("formGhostBtn")
        self.pick_btn.clicked.connect(self._pick)
        self.clear_btn = QPushButton(_ui("移除"))
        self.clear_btn.setAutoDefault(False)
        self.clear_btn.setObjectName("formGhostBtn")
        self.clear_btn.clicked.connect(self._clear)
        row.addWidget(self.pick_btn)
        row.addWidget(self.clear_btn)
        self.hint = QLabel("")
        self.hint.setObjectName("formHint")
        row.addWidget(self.hint, 1)
        self._refresh()

    def ref(self) -> str:
        return self._ref

    def _refresh(self):
        self.preview.setPixmap(_avatars.avatar_pixmap(
            self._ref, self._name, self.SIZE, kind=self._kind))
        self.hint.setText(_ui("已设置头像") if self._ref else _ui("未设置（显示名字首字）"))

    def _pick(self):
        path, _f = QFileDialog.getOpenFileName(
            self, _ui("选择头像"), "",
            _ui("图片 (*.png *.jpg *.jpeg *.webp *.bmp)"))
        if not path:
            return
        ok, ref, msg = agent_avatar.store(path, self._kind)
        if not ok:
            QMessageBox.warning(self, _ui("无法使用该图片"), msg)
            return
        self._ref = ref
        self._refresh()

    def _clear(self):
        self._ref = ""
        self._refresh()


class _FormDialog(QDialog):
    """极简表单对话框：标签在上、控件在下（多行字段在窄窗口里最易读）。"""

    def __init__(self, parent, title: str, fields: list,
                 hint: str = "", width: int = 620, height: int = 620,
                 name_hint: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(width, height)
        self._fields = fields
        self._widgets = {}
        self._name_hint = name_hint

        self.setStyleSheet(
            f"QDialog {{ background: {PALETTE['card']}; }}"
            f"QLabel {{ color: {PALETTE['text']}; font-size: {FONT_BODY}px; }}"
            "QLineEdit, QPlainTextEdit {"
            f" background: {PALETTE['field']}; color: {PALETTE['text']};"
            f" border: none; border-radius: {RADIUS_SM}px; padding: 8px;"
            f" font-size: {FONT_BODY}px; selection-background-color: {PALETTE['primary']}; }}"
            f"QPushButton {{ border-radius: {RADIUS_SM}px; padding: 7px 18px;"
            f" font-size: {FONT_BODY}px; font-weight: 600; }}"
            f"QPushButton#formGhostBtn {{ background: transparent; color: {PALETTE['text']};"
            f" border: 1px solid {PALETTE['border']}; padding: 5px 12px; }}"
            f"QLabel#formHint {{ color: {PALETTE['text_secondary']};"
            f" font-size: {FONT_SMALL}px; }}")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(SPACING_MD + 6, SPACING_MD + 6,
                               SPACING_MD + 6, SPACING_MD)
        lay.setSpacing(SPACING_SM)
        head = QLabel(title)
        head.setStyleSheet(
            f"color: {PALETTE['text']}; font-size: 16px; font-weight: 600;")
        lay.addWidget(head)
        if hint:
            h = QLabel(hint)
            h.setWordWrap(True)
            h.setStyleSheet(
                f"color: {PALETTE['text_secondary']}; font-size: {FONT_SMALL}px;")
            lay.addWidget(h)

        for f in fields:
            lab = QLabel(f.label + (" *" if f.required else ""))
            lab.setStyleSheet(
                f"color: {PALETTE['text_secondary']}; font-size: {FONT_SMALL}px;"
                f" font-weight: 600; margin-top: 4px;")
            lay.addWidget(lab)
            w = self._build_widget(f)
            self._widgets[f.key] = w
            lay.addWidget(w)

        lay.addStretch(1)
        row = QHBoxLayout()
        row.setSpacing(SPACING_SM)
        row.addStretch(1)
        cancel = QPushButton(_ui("取消"))
        cancel.setAutoDefault(False)
        cancel.setStyleSheet(
            f"background: transparent; color: {PALETTE['text_secondary']};"
            f" border: 1px solid {PALETTE['border']};")
        cancel.clicked.connect(self.reject)
        ok = QPushButton(_ui("保存"))
        ok.setDefault(True)
        ok.setStyleSheet(
            f"background: {PALETTE['primary']}; color: #FFFFFF; border: none;")
        ok.clicked.connect(self._on_ok)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)
        add_brand_footer(self)

    def _build_widget(self, f: Field):
        if f.kind == "avatar":
            return _AvatarPicker(self._name_hint, f.value, f.store_kind)
        if f.kind == "multiline":
            w = QPlainTextEdit()
            w.setPlainText(f.value)
            w.setPlaceholderText(f.placeholder)
            w.setFixedHeight(f.height)
            return w
        if f.kind == "combo":
            w = ArrowComboBox()
            w.set_glass_body(False)
            for label, val in f.options:
                w.addItem(label, val)
            idx = w.findData(f.value)
            if idx < 0 and f.value not in (None, ""):
                idx = w.findData(str(f.value))
            w.setCurrentIndex(idx if idx >= 0 else 0)
            w.setFixedHeight(34)
            return w
        w = QLineEdit()
        w.setText(f.value)
        w.setPlaceholderText(f.placeholder)
        w.setReadOnly(not f.editable)
        if not f.editable:
            w.setStyleSheet(
                f"background: transparent; color: {PALETTE['text_secondary']};"
                " border: none; padding: 8px;")
        w.setFixedHeight(34)
        return w

    def values(self) -> dict:
        """读取表单值。下拉返回 currentData 原样（字符串或 (服务商, 模型) 元组）。"""
        out = {}
        for f in self._fields:
            w = self._widgets[f.key]
            if f.kind == "avatar":
                out[f.key] = w.ref()
            elif f.kind == "multiline":
                out[f.key] = w.toPlainText().strip()
            elif f.kind == "combo":
                out[f.key] = w.currentData()
            else:
                out[f.key] = w.text().strip()
        return out

    def _on_ok(self):
        vals = self.values()
        for f in self._fields:
            if f.required and not vals.get(f.key):
                QMessageBox.warning(self, _ui("提示"),
                                    _ui("请填写：") + f.label)
                self._widgets[f.key].setFocus()
                return
        self.accept()


def edit_custom_agent(parent, agent: dict = None, workflows: list = None):
    """新建 / 编辑自定义 Agent（@agent）。返回表单值 dict，取消返回 None。

    agent: 已存在的记录（编辑）或 None（新建）。
    workflows: 可绑定的对话流名列表；None 表示不显示绑定项。
    """
    ag = agent or {}
    fields = [
        Field("name", _ui("Agent 名称（@名称 调用，仅字母/数字/下划线/短横线）"),
              ag.get("name", ""), placeholder="reviewer",
              required=True, editable=not agent),
        Field("avatar", _ui("头像"), ag.get("avatar", ""), kind="avatar",
              store_kind="agent"),
        Field("persona", _ui("性格（人设口吻与立场）"),
              ag.get("persona", ""), kind="multiline", height=110,
              placeholder=_ui("如：你是一位谨慎的审稿人，先找问题再给建议，语气克制。")),
        Field("system_prompt", _ui("系统级提示词（职责与规则）"),
              ag.get("system_prompt", ""), kind="multiline", height=150,
              placeholder=_ui("如：只做代码审查，不改文件；每条结论附文件与行号。")),
    ]
    if workflows:
        fields.append(Field("bound_workflow", _ui("绑定对话流（可选，决定工具与技能）"),
                            ag.get("bound_workflow", ""), kind="combo",
                            options=[(_ui("不绑定（沿用当前对话流）"), "")] +
                                    [(w, w) for w in workflows]))
    dlg = _FormDialog(parent, _ui("自定义 Agent"), fields,
                      hint=_ui("性格与系统级提示词至少填一项；两栏会在切换时合成该 Agent "
                               "的完整人格。设置页的「全局规则」始终追加，不受影响。"),
                      height=760 if workflows else 660,
                      name_hint=ag.get("name", ""))
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    vals = dlg.values()
    if not agent and not _CUSTOM_NAME_RE.match(vals.get("name", "")):
        QMessageBox.warning(parent, _ui("名称不可用"),
                            _ui("Agent 名称仅允许字母、数字、下划线与短横线，长度 1-64。"))
        return None
    if not vals.get("persona") and not vals.get("system_prompt"):
        QMessageBox.warning(parent, _ui("提示"),
                            _ui("性格与系统级提示词至少填写一项。"))
        return None
    return vals


def edit_sub_agent(parent, sub: dict = None, workflows: list = None):
    """新建 / 编辑子 Agent（subagents.json）。返回表单值 dict，取消返回 None。"""
    it = sub or {}
    fields = [
        Field("name", _ui("子 Agent 名称（如 sub_reviewer）"),
              it.get("name", ""), placeholder="sub_reviewer",
              required=True, editable=not sub),
        Field("description", _ui("简短描述（列表展示用）"),
              it.get("description", ""), placeholder=_ui("代码审查")),
        Field("goal", _ui("任务指令（goal，必填）"),
              it.get("goal", ""), kind="multiline", height=120, required=True,
              placeholder=_ui("如：审查给定改动的正确性、边界情况与安全性，逐条给出结论。")),
        Field("persona", _ui("性格（人设口吻与立场）"),
              it.get("persona", ""), kind="multiline", height=90,
              placeholder=_ui("如：严格、就事论事，先给结论再给依据。")),
        Field("system_prompt", _ui("系统级提示词（职责与规则）"),
              it.get("system_prompt", ""), kind="multiline", height=110,
              placeholder=_ui("如：只读代码不改文件；输出必须附文件路径与行号。")),
        Field("allowed", _ui("允许工具（逗号分隔，留空=全部白名单）"),
              ", ".join(it.get("allowed") or []),
              placeholder="read_file, search_code, grep"),
    ]
    if workflows:
        fields.append(Field("workflow", _ui("所属对话流"),
                            it.get("_workflow", ""), kind="combo",
                            options=[(w, w) for w in workflows]))
    dlg = _FormDialog(parent, _ui("子 Agent"), fields,
                      hint=_ui("子 Agent 由主 Agent 调用或用户以 @名称 直接唤起；"
                               "性格与系统级提示词留空时使用内置中性规则。"),
                      height=760 if workflows else 700)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    vals = dlg.values()
    if not sub and not _SUB_NAME_RE.match(vals.get("name", "")):
        QMessageBox.warning(parent, _ui("名称不可用"),
                            _ui("子 Agent 名称仅允许字母、数字、下划线与短横线，长度 1-50。"))
        return None
    return vals


def edit_group_participant(parent, participant: dict = None, model_options: list = None):
    """新建 / 编辑群聊成员（名称 / 性格 / 系统级提示词 / 模型）。

    model_options: [(展示文本, (provider, model))]，首项由调用方提供「跟随全局」。
    返回 dict（``model`` 为 (provider, model) 元组），取消返回 None。
    """
    p = participant or {}
    fields = [
        Field("name", _ui("成员名称（群聊内称呼，可中文）"),
              p.get("name", ""), placeholder=_ui("如：架构师"),
              required=True, editable=not participant),
        Field("avatar", _ui("头像"), p.get("avatar", ""), kind="avatar",
              store_kind="group"),
        Field("persona", _ui("性格（人设口吻与立场）"),
              p.get("persona", ""), kind="multiline", height=110,
              placeholder=_ui("如：务实、挑剔，优先考虑可维护性与失败场景。")),
        Field("system_prompt", _ui("系统级提示词（职责与规则）"),
              p.get("system_prompt", ""), kind="multiline", height=130,
              placeholder=_ui("如：只讨论方案取舍，每条意见给出代价与替代方案。")),
        Field("model", _ui("模型"),
              (p.get("provider", ""), p.get("model", "")), kind="combo",
              options=model_options or []),
    ]
    dlg = _FormDialog(parent, _ui("群聊成员"), fields,
                      hint=_ui("每个成员各自独立：性格、规则、模型与头像互不影响；"
                               "设置页的「全局规则」对所有成员都生效。"),
                      height=700, name_hint=p.get("name", ""))
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    vals = dlg.values()
    if not participant and not (vals.get("name") or "").strip():
        QMessageBox.warning(parent, _ui("提示"), _ui("请填写成员名称。"))
        return None
    m = vals.get("model")
    if isinstance(m, tuple) and len(m) == 2:
        vals["provider"], vals["model"] = str(m[0] or ""), str(m[1] or "")
    else:
        vals["provider"], vals["model"] = "", ""
    return vals
