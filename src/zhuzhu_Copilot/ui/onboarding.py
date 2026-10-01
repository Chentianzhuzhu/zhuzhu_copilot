# -*- coding: utf-8 -*-
"""首次安装新手指南（精简三步向导）。

在应用首次启动时展示一条引导，追求「简约高效」——3 步内完成：
  1. 欢迎（一句话理解应用 + 核心能力速览）
  2. 高效上手（4 条最常用技巧，每条一行）
  3. 初始偏好（主题 / 执行模式 / 长期记忆，点「完成」即写入并生效）

首次运行结束后写入标记，之后可在「设置」对话框点击「重新查看新手指南」再次打开。

标记存储（修复「首次安装打开程序新手指南无法弹出」）：
- 主标记：用户数据目录内的标记文件 ~/.zhuzhu_Copilot/agent/onboarding_done。
  卸载器会整体删除 ~/.zhuzhu_Copilot，因此「卸载 → 重新安装」后标记自然消失，
  新安装的首次启动必定弹出一次指南；
- 兼容标记：注册表 app_identity.qsettings()/agent_first_run_done
  （旧版本只写这里，而卸载器并不清理注册表 → 卸载重装后被误判为「已看过」，指南永不弹出，
  故判定不再以注册表为准；仅用于识别「老用户升级」，避免打扰）。
"""
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QPoint, QPropertyAnimation, QEasingCurve, \
    QParallelAnimationGroup, QSequentialAnimationGroup
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QStackedWidget,
    QWidget, QComboBox, QCheckBox, QFrame,
    QGraphicsOpacityEffect, QGraphicsBlurEffect,
)
from PyQt6.QtGui import QFont, QIcon

from zhuzhu_Copilot import app_identity

_FIRST_RUN_KEY = "agent_first_run_done"     # 旧版标记（注册表，仅作兼容识别，已随迁移带入新键）
_DATA_ROOT = app_identity.data_root()       # 用户数据目录（卸载时被整体删除）
_MARKER_NAME = "onboarding_done"            # 安装生命周期内的「已看过指南」标记

# 进程启动时用户数据目录是否已存在（由 capture_startup_state() 在最早期抓取）
# 注意：程序启动过程本身会「首次运行自动生成」目录与示例文件，必须在此之前取样，
# 否则无法区分「全新安装」与「老用户升级」。
_startup_state = {"data_dir_existed": None}


def _marker_path() -> Path:
    """标记文件路径：放在用户数据目录内（随卸载删除，不随注册表残留）。"""
    return _DATA_ROOT / "agent" / _MARKER_NAME


def _legacy_registry_done() -> bool:
    """旧版写下的注册表标记是否为「已看过」（读不到时按未看过处理）。

    改名后该标记由 app_identity 在启动迁移时复制到新键，此处读新键即可。
    """
    try:
        q = app_identity.qsettings()
        if not q.contains(_FIRST_RUN_KEY):
            return False
        return str(q.value(_FIRST_RUN_KEY, "")).strip().lower() not in (
            "", "0", "no", "false", "off")
    except Exception:
        return False


def capture_startup_state() -> None:
    """在启动最早期（任何「首次运行自动生成文件」之前）记录用户数据目录是否已存在。

    由 main.py 在创建 QApplication 之前调用；未调用时判定退回旧的注册表语义。
    """
    try:
        _startup_state["data_dir_existed"] = bool(_DATA_ROOT.exists())
    except Exception:
        _startup_state["data_dir_existed"] = None


def is_first_run() -> bool:
    """首次运行判定：首次安装 / 卸载重装 / 数据目录被清空 → True。

    判定顺序：
      1) 数据目录内已有标记文件 → 非首次（本安装已看过指南）；
      2) 进程启动时数据目录不存在 → 首次（全新安装、卸载后重装、数据被清空）；
      3) 启动时数据目录已存在 + 旧版注册表标记为「已看过」→ 老用户升级，不打扰
         （顺便补写标记文件，后续判定只看文件）；
      4) 其余情况 → 首次。
    """
    try:
        if _marker_path().exists():
            return False
    except Exception:
        pass
    try:
        existed = _startup_state.get("data_dir_existed")
    except Exception:
        existed = None
    if existed is False:
        return True                    # 数据目录启动时不存在 → 全新安装，必须弹
    if _legacy_registry_done():
        if existed is True:
            mark_first_run_done()      # 老用户升级：补齐标记文件，避免以后重复判定
        return False
    return True


def mark_first_run_done() -> None:
    """写入「已看过新手指南」标记（数据目录标记文件为主，注册表键兼容保留）。"""
    try:
        p = _marker_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("1", encoding="utf-8")
    except Exception:
        pass
    try:
        app_identity.qsettings().setValue(_FIRST_RUN_KEY, "1")
    except Exception:
        pass


def _ap(*names):
    """延迟导入 agent_panel，按名称返回模块内符号（避免顶层循环导入）。"""
    import zhuzhu_Copilot.ui.agent_panel as _m
    if not names:
        return _m
    if len(names) == 1:
        return getattr(_m, names[0])
    return tuple(getattr(_m, n) for n in names)


# ---------------- 翻页动画舞台 ----------------
class _SlideStage(QWidget):
    """翻页动画的舞台：内层承载内容，位移 + 模糊挂在这里；淡入淡出挂在外层。

    为什么分两层：Qt 里一个控件只能挂一个 QGraphicsEffect，而翻页要同时「渐变（不透明度）」
    与「模糊」，只能各挂一层：

        stage（不透明度） → content（模糊 + 水平位移）

    内层**不参与任何布局**（几何由本类铺满），否则布局重排会覆盖动画写的位移。
    """

    def __init__(self, content: QWidget, parent=None):
        super().__init__(parent)
        self._content = content
        content.setParent(self)
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(1.0)
        self.setGraphicsEffect(self._opacity)
        self._blur = QGraphicsBlurEffect(content)
        self._blur.setBlurRadius(0.0)
        content.setGraphicsEffect(self._blur)
        self._animating = False

    @property
    def content(self) -> QWidget:
        return self._content

    @property
    def opacity_effect(self) -> QGraphicsOpacityEffect:
        return self._opacity

    @property
    def blur_effect(self) -> QGraphicsBlurEffect:
        return self._blur

    def begin_anim(self) -> None:
        """进入动画态：期间 resizeEvent 不再把内容位移归零，避免与动画抢几何。"""
        self._animating = True

    def end_anim(self) -> None:
        self._animating = False
        self._content.move(0, 0)
        self._blur.setBlurRadius(0.0)
        self._opacity.setOpacity(1.0)

    def resizeEvent(self, ev):
        self._content.resize(self.size())
        if not self._animating:
            self._content.move(0, 0)
        super().resizeEvent(ev)


# ---------------- 工具：把一组文本渲染成带标题的卡片 ----------------
def _build_card(parent: QWidget, title: str, lines, *, accent: bool = False) -> QWidget:
    """渲染一张「标题 + 若干行说明」卡片。

    lines: list[str]，每个元素作为一行富文本（支持简单 <b>/<br>）。

    刻意不加描边：一块一行说明、每行都套一层边框会把整页切得零碎，
    只用底色分组即可（简约优先）。
    """
    _m = _ap()
    TEXT = _m.TEXT
    CARD = _m.PANEL if not accent else _m.CARD
    card = QWidget(parent)
    card.setStyleSheet(f"background: {CARD}; border-radius: 12px;")
    lay = QVBoxLayout(card)
    lay.setContentsMargins(20, 16, 20, 16)
    lay.setSpacing(8)
    t = QLabel(title)
    t.setStyleSheet(f"color: {_m.ACCENT_HOVER}; font-size: 15px; font-weight: 800;")
    t.setWordWrap(True)
    lay.addWidget(t)
    for ln in lines:
        b = QLabel(ln)
        b.setStyleSheet(f"color: {TEXT}; font-size: 13px;")
        b.setWordWrap(True)
        b.setTextFormat(Qt.TextFormat.RichText)
        b.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(b)
    return card


class OnboardingWizard(QDialog):
    """精简三步首启新手指南向导。纯 Qt 组件，无 emoji，线性图标风格。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("zhuzhu Copilot · 新手指南")
        self.setWindowIcon(QIcon(_ap("_app_icon_path")()))
        self.resize(620, 480)
        self.setMinimumSize(560, 430)
        self.setModal(True)

        self._theme_changed = False
        self._apply_requested = False   # 是否在偏好页点过「完成」

        _m = _ap()
        self._TEXT = _m.TEXT
        self._DIM = _m.TEXT_DIM
        self._PANEL = _m.PANEL
        self._BORDER = _m.BORDER
        self._ACCENT = _m.ACCENT
        self._ACCENT_HOVER = _m.ACCENT_HOVER

        root = QVBoxLayout(self)
        root.setContentsMargins(26, 22, 26, 18)
        root.setSpacing(12)

        # ---------- 顶部标题 + 步骤圆点指示 ----------
        self._title = QLabel()
        self._title.setStyleSheet(
            f"color: {self._TEXT}; font-size: 21px; font-weight: 800;")
        root.addWidget(self._title)

        self._dots = QLabel()
        self._dots.setStyleSheet(f"color: {self._DIM}; font-size: 12px; letter-spacing: 3px;")
        root.addWidget(self._dots)

        # ---------- 内容区 ----------
        # 栈外面套一层动画舞台：翻页时对「整页内容」做水平位移 + 模糊 + 淡入淡出
        self._stack = QStackedWidget()
        self._stage = _SlideStage(self._stack)
        self._page_anim = None          # 正在跑的翻页动画（保持引用，避免被 GC 回收）
        root.addWidget(self._stage, 1)

        # ---------- 底部导航 ----------
        nav = QHBoxLayout()
        nav.setSpacing(10)
        self._skip_btn = QPushButton("跳过")
        self._skip_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {self._DIM}; border: none;"
            "font-size: 12px; padding: 6px 8px; }"
            f"QPushButton:hover {{ color: {self._TEXT}; }}")
        self._skip_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._skip_btn.clicked.connect(self._on_skip)
        nav.addWidget(self._skip_btn, 1)

        self._back_btn = QPushButton("上一步")
        self._back_btn.setStyleSheet(
            f"QPushButton {{ background: {self._PANEL}; color: {self._TEXT};"
            f"border: 1px solid {self._BORDER}; border-radius: 10px;"
            "padding: 8px 24px; font-size: 13px; font-weight: 600; }"
            f"QPushButton:hover {{ border-color: {self._ACCENT_HOVER}; }}")
        self._back_btn.setAutoDefault(False)
        self._back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._back_btn.clicked.connect(self._on_back)
        nav.addWidget(self._back_btn)

        self._next_btn = QPushButton("下一步")
        self._next_btn.setStyleSheet(
            f"QPushButton {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            f"stop:0 {self._ACCENT_HOVER}, stop:1 {self._ACCENT});"
            " color: #FFFFFF; border: none;"
            "border-radius: 10px; padding: 9px 32px; font-size: 13px; font-weight: 700; }"
            f"QPushButton:hover {{ background: {self._ACCENT_HOVER}; }}"
            f"QPushButton:pressed {{ background: {self._ACCENT}; }}")
        self._next_btn.setAutoDefault(False)
        self._next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._next_btn.clicked.connect(self._on_next)
        nav.addWidget(self._next_btn)
        root.addLayout(nav)

        # ---------- 构建各步骤页（3 步，简约高效） ----------
        self._steps = [
            ("欢迎使用 zhuzhu Copilot", self._build_welcome),
            ("高效上手四则", self._build_tips),
            ("初始偏好", self._build_prefs),
        ]
        for _t, _builder in self._steps:
            self._stack.addWidget(_builder())

        self._idx = 0
        self._refresh()

    # ---------------- 页面构建 ----------------
    def _page_wrap(self) -> QWidget:
        p = QWidget()
        lay = QVBoxLayout(p)
        lay.setContentsMargins(4, 6, 8, 4)
        lay.setSpacing(12)
        lay.addStretch(1)
        return p

    def _build_welcome(self) -> QWidget:
        p = self._page_wrap()
        lay = p.layout()
        lay.insertWidget(0, _build_card(
            p, "一句话理解它",
            ["顶部输入框输入指令或问题，AI 会按你设定的「执行模式」自主阅读文件、"
              "调用工具、修改代码并给出结果；你随时可以打断、继续或改用「每步确认」模式。"],
            accent=True))
        lay.insertWidget(1, _build_card(
            p, "它能做什么",
            ["• <b>对话与任务</b>：多对话并发，每个对话独立工作目录，互不干扰。",
             "• <b>技能与扩展</b>：内置写作/代码/运维等技能，支持 MCP 服务器与插件。",
             "• <b>桌面集成</b>：屏幕截图、文件拖拽、TTS 语音、桌宠与桌面歌词。"]))
        lay.addStretch(1)
        return p

    def _build_tips(self) -> QWidget:
        p = self._page_wrap()
        lay = p.layout()
        tips = [
            ("输入 / 与 @", "在输入框输入 <b>/</b> 打开命令面板，输入 <b>@</b> 选择 Agent。"),
            ("直接拖文件", "把图片 / 文档 / 压缩包拖进对话框，AI 即可读取处理。"),
            ("执行模式", "<b>Ask</b> 每步确认最稳妥；<b>Edit</b> 仅危险命令确认；<b>YOLO</b> 直行。"),
            ("长期记忆", "开启后重要背景写入 memory.md，跨对话自动读取。"),
        ]
        for title, line in tips:
            lay.insertWidget(lay.count() - 1, _build_card(p, title, [line]))
        lay.addStretch(1)
        return p

    def _build_prefs(self) -> QWidget:
        """初始偏好页：核心偏好直接设置，点「完成」写入配置。"""
        p = self._page_wrap()
        lay = p.layout()
        c = QWidget(p)
        c.setStyleSheet(
            f"background: {self._PANEL}; border: 1px solid {self._BORDER};"
            "border-radius: 12px;")
        cl = QVBoxLayout(c)
        cl.setContentsMargins(20, 16, 20, 16)
        cl.setSpacing(14)

        # 主题
        tr = QHBoxLayout()
        tl = QLabel("界面主题")
        tl.setStyleSheet(f"color: {self._TEXT}; font-size: 13px; font-weight: 600;")
        tl.setFixedWidth(96)
        tr.addWidget(tl)
        self._theme_combo = QComboBox()
        self._theme_combo.addItem("深色", "dark")
        self._theme_combo.addItem("浅色", "light")
        self._theme_combo.addItem("按时间自动（8-20 浅色）", "auto")
        from zhuzhu_Copilot.ui.agent_panel import _theme_setting
        try:
            _cur = _theme_setting()
            _i = self._theme_combo.findData(_cur)
            self._theme_combo.setCurrentIndex(_i if _i >= 0 else 2)
        except Exception:
            pass
        tr.addWidget(self._theme_combo, 1)
        cl.addLayout(tr)

        # 执行模式
        mr = QHBoxLayout()
        ml = QLabel("执行模式")
        ml.setStyleSheet(f"color: {self._TEXT}; font-size: 13px; font-weight: 600;")
        ml.setFixedWidth(96)
        mr.addWidget(ml)
        self._mode_combo = QComboBox()
        self._mode_combo.addItem("AskBeforeEdit（每步确认，最稳妥）", "ask")
        self._mode_combo.addItem("Edit（仅非白名单命令确认）", "edit")
        self._mode_combo.addItem("YOLO（无确认直行）", "yolo")
        try:
            _m = app_identity.qsettings().value("agent_mode", "ask")
            _i = self._mode_combo.findData(_m)
            self._mode_combo.setCurrentIndex(_i if _i >= 0 else 0)
        except Exception:
            pass
        mr.addWidget(self._mode_combo, 1)
        cl.addLayout(mr)

        # 长期记忆
        from zhuzhu_Copilot.core import agent_skills
        try:
            _mem = bool(agent_skills.load_settings().get("memory_enabled", True))
        except Exception:
            _mem = True
        self._memory_check = self._mk_check(
            "开启长期记忆（AI 可读写 memory.md，跨对话记住背景）", _mem)
        cl.addWidget(self._memory_check)

        hint = QLabel("点击「完成」写入并即时生效；其余设置可随时在「设置」里调整。")
        hint.setStyleSheet(f"color: {self._DIM}; font-size: 12px;")
        hint.setWordWrap(True)
        cl.addWidget(hint)

        lay.insertWidget(lay.count() - 1, c)
        lay.addStretch(1)
        return p

    # ---------------- 控件辅助 ----------------
    def _mk_check(self, text: str, checked: bool) -> QCheckBox:
        cb = QCheckBox(text)
        cb.setChecked(checked)
        cb.setStyleSheet(f"color: {self._TEXT}; font-size: 13px; spacing: 8px;")
        return cb

    # ---------------- 导航 ----------------
    def _sync_nav(self):
        """标题 / 步骤圆点 / 按钮状态。

        与内容翻页解耦：翻页动画一触发就先切这些，视觉上「导航立刻响应、内容滑过去」。
        """
        title, _ = self._steps[self._idx]
        self._title.setText(title)
        # 圆点步骤指示：当前步高亮为深蓝实心圆，其余为淡灰圆（颜色随主题，不再硬编码）
        n = len(self._steps)
        _dot_on = f'<span style="color:{self._ACCENT_HOVER};">&#9679;</span>'
        _dot_off = f'<span style="color:{self._DIM};">&#9679;</span>'
        self._dots.setText(" ".join(_dot_on if i == self._idx else _dot_off
                                    for i in range(n)) + f"   {self._idx + 1} / {n}")
        self._back_btn.setVisible(self._idx > 0)
        self._next_btn.setText("完成" if self._idx == n - 1 else "下一步")

    def _refresh(self):
        """立即切页（无动画）——仅首次进入时用。"""
        self._sync_nav()
        self._stack.setCurrentIndex(self._idx)

    # ---------------- 翻页动画（上一步 / 下一步） ----------------
    # 时长是唯一的"速率"调节杆：QPropertyAnimation 由 Qt 内部的统一 16ms 定时器驱动（≈60Hz），
    # PyQt6 没有公开的帧率/定时器间隔 API；且本项目此前实测过 8ms 节拍反而更卡
    # （主线程被排满、帧间隔不匀），已改回 16ms——均匀的帧间隔才是真的丝滑。
    _PAGE_ANIM_MS = 260        # 单段时长：滑出、滑入各一段（合计约 0.5s）
    _PAGE_BLUR_MAX = 14.0      # 滑出到最远处时的模糊半径（像素）

    def _animate_page_change(self, new_idx: int, forward: bool) -> None:
        """翻页动画：旧页向左（后退时向右）滑出并逐渐模糊、淡出，随后新页从相反一侧滑入、清晰、淡入。

        用「滑出 → 换页 → 滑入」两段式，而不是两页同屏对推：QStackedWidget 一次只显示一页，
        要对推就得自己接管子页几何与可见性，脆弱且收益不大；而换页点落在旧页已完全不可见时，
        看不出接缝。
        """
        if new_idx == self._idx:
            return
        prev = self._page_anim
        if prev is not None:
            # 连点按钮：先终止上一段，否则两段动画会抢同一个 pos / 模糊半径
            try:
                prev.stop()
            except Exception:
                pass
        self._idx = new_idx
        self._sync_nav()

        stage, content = self._stage, self._stack
        width = max(stage.width(), 1)
        out_dx = -width if forward else width       # 滑出方向
        in_dx = width if forward else -width        # 滑入起点（与滑出方向相反）
        ms, blur_max = self._PAGE_ANIM_MS, self._PAGE_BLUR_MAX
        ease = QEasingCurve.Type.OutCubic if forward else QEasingCurve.Type.InCubic

        stage.begin_anim()

        def _segment(pos_from: int, pos_to: int, blur_from: float, blur_to: float,
                     op_from: float, op_to: float) -> QParallelAnimationGroup:
            """一段动画：位移 + 模糊 + 不透明度同步跑。"""
            grp = QParallelAnimationGroup()
            pos = QPropertyAnimation(content, b"pos", grp)
            pos.setDuration(ms)
            pos.setEasingCurve(ease)
            pos.setStartValue(QPoint(pos_from, 0))
            pos.setEndValue(QPoint(pos_to, 0))
            grp.addAnimation(pos)
            for target, prop, start, end in (
                    (stage.blur_effect, b"blurRadius", blur_from, blur_to),
                    (stage.opacity_effect, b"opacity", op_from, op_to)):
                anim = QPropertyAnimation(target, prop, grp)
                anim.setDuration(ms)
                anim.setStartValue(start)
                anim.setEndValue(end)
                grp.addAnimation(anim)
            return grp

        seq = QSequentialAnimationGroup(self)
        seq.addAnimation(_segment(0, out_dx, 0.0, blur_max, 1.0, 0.0))     # 滑出（变模糊、淡出）
        seq.addAnimation(_segment(in_dx, 0, blur_max, 0.0, 0.0, 1.0))      # 滑入（变清晰、淡入）
        # 换页点：第一段结束（旧页已完全退出）→ 切到新页，第二段再把它送进来
        seq.animationAt(0).finished.connect(
            lambda idx=new_idx: self._stack.setCurrentIndex(idx))
        seq.finished.connect(stage.end_anim)
        self._page_anim = seq
        seq.start()

    def _on_back(self):
        if self._idx > 0:
            self._animate_page_change(self._idx - 1, forward=False)

    def _on_next(self):
        if self._idx == len(self._steps) - 1:
            # 偏好页即最后一步：完成时写入配置
            self._apply_requested = True
            self._apply_preferences()
            mark_first_run_done()
            self.accept()
            return
        self._animate_page_change(self._idx + 1, forward=True)

    def _on_skip(self):
        mark_first_run_done()
        self.reject()

    # ---------------- 偏好写入 ----------------
    def _apply_preferences(self):
        from zhuzhu_Copilot.core import agent_skills
        from zhuzhu_Copilot.ui.agent_panel import _theme_setting
        q = app_identity.qsettings()
        try:
            theme = self._theme_combo.currentData() or "auto"
        except Exception:
            theme = _theme_setting()
        try:
            old_theme = _theme_setting()
        except Exception:
            old_theme = None
        self._theme_changed = (old_theme is not None and old_theme != theme)
        q.setValue("agent_theme", theme)
        try:
            q.setValue("agent_mode", self._mode_combo.currentData() or "ask")
        except Exception:
            pass
        try:
            s = agent_skills.load_settings()
            s = dict(s)
            s["memory_enabled"] = bool(self._memory_check.isChecked())
            agent_skills.save_settings(s)
        except Exception:
            pass

    def preferences_applied(self) -> bool:
        return self._apply_requested


def build_wizard(parent=None) -> OnboardingWizard:
    """构造向导并套上应用统一的弹窗外观（毛玻璃 + 尺寸下限 + 居中）。

    启动流程（首次安装先走指南）与设置里「重新查看」共用同一套外观，
    否则同一个向导会在两处呈现出不同大小。返回值由调用方负责 exec()。
    """
    dlg = OnboardingWizard(parent)
    # 尺寸下限 + 居中的统一处理（弹窗外观现由应用级 QSS 的主题色统一承载）
    def _normalize():
        try:
            dlg.adjustSize()
            if dlg.width() < 700:
                dlg.resize(760, max(dlg.height(), 560))
        except Exception:
            pass
        try:
            from zhuzhu_Copilot.ui.agent_panel import _center_dialog_on_screen
            _center_dialog_on_screen(dlg)
        except Exception:
            pass

    QTimer.singleShot(0, _normalize)
    return dlg
