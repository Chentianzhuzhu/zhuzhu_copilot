import os
import re
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QDialog, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QCheckBox, QSizePolicy, QGraphicsDropShadowEffect, QStyledItemDelegate, QStyle,
    QFileIconProvider, QApplication, QComboBox
)
from PyQt6.QtCore import (
    Qt, QSize, QRect, QFileInfo, QPoint, QTimer, QObject, QPropertyAnimation,
    QParallelAnimationGroup, QEasingCurve, pyqtSignal, pyqtProperty
)
from PyQt6.QtGui import (QColor, QPainter, QIcon, QFont, QFontMetrics, QPixmap,
                         QPainterPath, QPen)

from zhuzhu_Copilot.ui.styles import PALETTE, svg_icon
from zhuzhu_Copilot.ui.tokens import COMBO_ARROW_W
from zhuzhu_Copilot.core.i18n import ui as _ui, uim as _uim, uif as _uif

# 卸载/安装类程序名，图标无意义，查找主程序时排除
_BANNED_EXE = {
    "unins", "unins000", "unins001", "uninst", "uninstall", "uninstaller",
    "setup", "install", "installer", "update", "updater", "redist",
}
# UWP 包内常见 logo 命名，优先选用
_UWP_LOGO_HINTS = ("storelogo", "square150x150logo", "square44x44logo", "applogo")

# Toast 关闭按钮矢量图标（淡灰简约，替代文本符号）
_TOAST_CLOSE_SVG = f"""<svg width="16" height="16" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
<path d="M7 7l10 10M17 7L7 17" stroke="{PALETTE['text_secondary']}" stroke-width="2"
      stroke-linecap="round"/></svg>"""


def _danger_hover() -> str:
    """危险色按钮的悬停提亮色"""
    return QColor(PALETTE["danger"]).lighter(112).name()


class SwitchButton(QWidget):
    """滑块开关：点击切换，带动画。信号 toggled(bool) 在用户点击时发射"""

    toggled = pyqtSignal(bool)

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self._checked = checked
        self._knob = 1.0 if checked else 0.0   # 滑块位置 0~1
        self.setFixedSize(48, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def knob(self) -> float:
        return self._knob

    def set_knob(self, v: float):
        self._knob = v
        self.update()

    knob = pyqtProperty(float, knob, set_knob)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool):
        if checked == self._checked:
            return
        self._checked = checked
        self._anim.stop()
        self._anim.setStartValue(self._knob)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setChecked(not self._checked)
            self.toggled.emit(self._checked)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._checked:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(PALETTE["primary"]))
            p.drawRoundedRect(self.rect(), 13, 13)
        else:
            p.setPen(QPen(QColor(PALETTE["border"]), 1))
            p.setBrush(QColor(PALETTE["bg_bottom"]))
            p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 13, 13)
        pad = 3
        d = self.height() - pad * 2
        x = pad + self._knob * (self.width() - d - pad * 2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(int(round(x)), pad, d, d)


class ArrowComboBox(QComboBox):
    """带旋转动画下拉箭头的 QComboBox：收起时 chevron 朝下，展开后旋转 180° 朝上。

    为什么必须自绘：一旦样式表接管了 `QComboBox::drop-down`，Qt 就**不再绘制原生箭头**；
    而 QSS 又画不出三角 —— 用「透明左右边框 + 实心上边框」拼三角会被 Qt 渲染成一个
    实心小方块（用户反馈「下拉箭头是个方块」）。自绘是同时满足
    「箭头存在 / 形状正确 / 随主题与 hover 换色」的唯一做法。

    箭头预留宽度与 QSS `::drop-down` 取同一 Design Token（COMBO_ARROW_W），两处不会漂移。
    颜色在绘制时实时读 PALETTE，因此主题切换无需重建控件。
    """

    ARROW_W = COMBO_ARROW_W      # 与 QSS ::drop-down 宽度同源
    ARROW_HALF = 4.0             # chevron 半宽（px）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._arrow_angle = 0.0        # 箭头旋转角度：0=收起，180=展开
        self._arrow_open = False
        self._arrow_hover = False
        self._arrow_anim = QPropertyAnimation(self, b"arrowAngle", self)
        self._arrow_anim.setDuration(160)
        self._arrow_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    # ---------- 动画属性 ----------
    def get_arrowAngle(self) -> float:
        return self._arrow_angle

    def set_arrowAngle(self, a: float):
        self._arrow_angle = a
        self.update()

    arrowAngle = pyqtProperty(float, get_arrowAngle, set_arrowAngle)

    # ---------- 展开/收起触发旋转动画 ----------
    def showPopup(self):
        super().showPopup()
        self._run_arrow(True)

    def hidePopup(self):
        super().hidePopup()
        self._run_arrow(False)

    def _run_arrow(self, opening: bool):
        self._arrow_open = opening
        self._arrow_anim.stop()
        self._arrow_anim.setStartValue(self._arrow_angle)
        self._arrow_anim.setEndValue(180.0 if opening else 0.0)
        self._arrow_anim.start()

    def enterEvent(self, e):
        self._arrow_hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._arrow_hover = False
        self.update()
        super().leaveEvent(e)

    def apply_theme(self):
        """主题切换后重绘箭头（颜色在 paintEvent 实时取 PALETTE，只需重绘）。"""
        self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        # 在右侧下拉区自绘旋转箭头（∨ 形折线，展开后旋转 180° 变为 ∧）
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx = self.rect().right() - self.ARROW_W / 2
        cy = self.rect().center().y()
        p.save()
        p.translate(cx, cy)
        p.rotate(self._arrow_angle)
        color = PALETTE["primary"] if (self._arrow_open or self._arrow_hover) \
            else PALETTE["text_secondary"]
        pen = QPen(QColor(color))
        pen.setWidthF(1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        s = self.ARROW_HALF
        path = QPainterPath()
        path.moveTo(-s, -s * 0.5)
        path.lineTo(0.0, s * 0.5)
        path.lineTo(s, -s * 0.5)
        p.drawPath(path)
        p.restore()
        p.end()


class Card(QWidget):
    """浮层内的内容卡片：圆角底 + 细描边（纯黑+淡灰+白+深蓝四色系）。

    QSS 里内联了主题色，**必须**提供 `apply_theme()`：卡片是在页面构建期建好的，
    主题切换时控件不会被重建，只有重新按当前 PALETTE 生成 QSS 才能跟着换色
    （用户反馈的「深色模式下卡片仍是浅色」就是漏了这一步）。
    """

    def __init__(self, title="", parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._title_label = None

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(18, 16, 18, 16)
        self.layout.setSpacing(12)

        if title:
            title_label = QLabel(title)
            title_label.setObjectName("cardTitle")
            self._title_label = title_label
            self.layout.addWidget(title_label)
        self.apply_theme()

    def apply_theme(self):
        """按当前 PALETTE 重建卡片与标题的样式（幂等，主题切换时由外层统一调用）。"""
        self.setStyleSheet(f"""
            QWidget#card {{
                background-color: {PALETTE['card']};
                border-radius: 14px;
                border: 1px solid {PALETTE['border']};
            }}
        """)
        if self._title_label is not None:
            self._title_label.setStyleSheet(
                f"font-size: 16px; font-weight: 700; color: {PALETTE['text']};")

class PrimaryButton(QPushButton):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(40)

class SecondaryButton(QPushButton):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setObjectName("secondary")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(40)


class _BrandFooterPlacer(QObject):
    """左下角品牌字样定位器：跟随窗口 resize 保持在左下角"""

    def __init__(self, foot: QLabel, dialog: QDialog):
        super().__init__(dialog)
        self._foot, self._dialog = foot, dialog
        dialog.installEventFilter(self)
        QTimer.singleShot(0, self._place)

    def _place(self):
        f, d = self._foot, self._dialog
        f.adjustSize()
        f.move(8, d.height() - f.height() - 8)

    def eventFilter(self, obj, ev):
        from PyQt6.QtCore import QEvent
        if obj is self._dialog and ev.type() == QEvent.Type.Resize:
            self._place()
        return False


def add_brand_footer(dialog: QDialog, overlay: bool = False) -> None:
    """在所有对话框底部左下角追加 'Create By Xiaozhu' 品牌字样。

    overlay=False：QVBoxLayout 根布局直接在末尾追加；
    其他布局（QFormLayout）或 overlay=True（主面板等复杂布局）
    用绝对定位叠加在左下角并跟随窗口缩放。
    """
    from PyQt6.QtWidgets import QFormLayout
    lay = dialog.layout()
    if lay is None:
        return
    foot = QLabel("Create By Xiaozhu")
    foot.setObjectName("brandFooter")
    foot.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 10px; background: transparent;")
    foot.setAlignment(Qt.AlignmentFlag.AlignLeft)
    if overlay or not isinstance(lay, QVBoxLayout):
        foot.setParent(dialog)
        _BrandFooterPlacer(foot, dialog)
        foot.show()
        return
    lay.addWidget(foot)


class DataDirDialog(QDialog):
    """迁移前勾选要一并移动的数据目录（AppData/文档等安装目录之外的目录）"""

    def __init__(self, candidates, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_ui("选择要一并迁移的数据目录"))
        self.setMinimumWidth(600)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        tip = QLabel(_ui("检测到以下与安装目录之外的关联数据目录，勾选后将一并移动到目标盘："))
        tip.setWordWrap(True)
        tip.setStyleSheet(f"color: {PALETTE['text']}; font-size: 13px; font-weight: 600;")
        layout.addWidget(tip)

        self._boxes = []
        self._items = []
        for p in candidates:
            cb = QCheckBox(str(p))
            cb.setChecked(True)
            cb.setStyleSheet("font-size: 13px; padding: 4px 0;")
            self._boxes.append(cb)
            self._items.append(p)
            layout.addWidget(cb)

        hint = QLabel(_ui("提示：如微信/QQ 的聊天记录目录。迁移后若应用找不到数据，请在应用设置内重新指定该目录。"))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 11px;")
        layout.addWidget(hint)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        skip_btn = QPushButton(_ui("仅迁移主目录"))
        skip_btn.clicked.connect(lambda: self.done(QDialog.DialogCode.Rejected))
        btn_layout.addWidget(skip_btn)
        ok_btn = PrimaryButton(_ui("迁移勾选目录"))
        ok_btn.clicked.connect(self.accept)
        btn_layout.addWidget(ok_btn)
        layout.addLayout(btn_layout)

    def selected(self):
        return [p for cb, p in zip(self._boxes, self._items) if cb.isChecked()]


class UninstallConfirmDialog(QDialog):
    """强力卸载前展示删除清单并要求确认"""

    def __init__(self, plan, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_ui("确认强力卸载"))
        self.setMinimumWidth(620)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        head = QLabel(_uif("将强力卸载 <b>{a0}</b>（{a1}）", a0=plan.app.name, a1=plan.app.app_type))
        head.setStyleSheet(f"font-size: 15px; color: {PALETTE['text']};")
        layout.addWidget(head)

        if plan.is_uwp:
            info = QLabel(
                _uim("UWP 应用包：<b>{pkg}</b>\n"
                     "将调用 Remove-AppxPackage 卸载该应用及其数据。")
                .format(pkg=plan.package_name)
            )
            info.setWordWrap(True)
            info.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 13px;")
            layout.addWidget(info)
        else:
            self._add_section(layout, _ui("安装根目录（将删除）"), [str(plan.root)])
            native = getattr(plan, "native_uninstaller", None)
            if native:
                native_exe = native[0] if isinstance(native, tuple) else native
                self._add_section(
                    layout, _ui("自带卸载器（将优先调用）"),
                    [native_exe, _ui("运行完成后再执行下方清理，以正确保留卸载钩子。")],
                )
            if plan.data_dirs:
                self._add_section(layout, _ui("数据/存档/聊天记录目录（将删除）"), [str(d) for d in plan.data_dirs])
            else:
                self._add_section(layout, _ui("数据目录"), [_ui("未检测到关联数据目录")])
            self._add_section(layout, _ui("其他清理"), [
                f"快捷方式：{len(plan.shortcuts)} 个",
                f"注册表关联项：{len(plan.registry_entries)} 处",
            ])

        warn = QLabel(_ui("此操作不可恢复！以上内容将被永久删除。"))
        warn.setWordWrap(True)
        warn.setStyleSheet(f"color: {PALETTE['danger']}; font-size: 13px; font-weight: 700;")
        layout.addWidget(warn)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        cancel_btn = QPushButton(_ui("取消"))
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
        ok_btn = QPushButton(_ui("确认卸载"))
        ok_btn.setStyleSheet(
            f"QPushButton {{ background-color: {PALETTE['danger']}; color: white; font-weight: 700; "
            "border: none; border-radius: 8px; padding: 8px 22px; }"
            f"QPushButton:hover {{ background-color: {_danger_hover()}; }}"
        )
        ok_btn.clicked.connect(self.accept)
        btn_layout.addWidget(ok_btn)
        layout.addLayout(btn_layout)
        add_brand_footer(self)

    def _add_section(self, layout, title, lines):
        tl = QLabel(title)
        tl.setStyleSheet(f"color: {PALETTE['text']}; font-size: 12px; font-weight: 700; margin-top: 4px;")
        layout.addWidget(tl)
        for line in lines:
            l = QLabel(line)
            l.setWordWrap(True)
            l.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 12px;")
            layout.addWidget(l)


class AppItemDelegate(QStyledItemDelegate):
    """自绘应用列表项：真实图标/首字符、类型标签、名称、路径、大小，选中态完整渲染"""
    ROW_HEIGHT = 68

    def __init__(self, parent=None):
        super().__init__(parent)
        self._provider = QFileIconProvider()
        self._icon_cache = {}      # id(app) -> QIcon
        self._source_cache = {}    # id(app) -> 图标源文件路径(str | None)

    def sizeHint(self, option, index):
        return QSize(0, self.ROW_HEIGHT)

    def paint(self, painter, option, index):
        app = index.data(Qt.ItemDataRole.UserRole)
        if not app:
            super().paint(painter, option, index)
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect

        # --- 背景（选中/悬停/默认）---
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        bg_rect = QRect(rect.left() + 3, rect.top() + 3, rect.width() - 6, rect.height() - 6)
        if selected:
            painter.setBrush(QColor(PALETTE["primary_light"]))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(bg_rect, 12, 12)
            # 左侧选中指示条
            painter.setBrush(QColor(PALETTE["primary_hover"]))
            painter.drawRoundedRect(QRect(rect.left() + 3, rect.top() + 14, 4, rect.height() - 28), 2, 2)
        elif hovered:
            painter.setBrush(QColor(PALETTE["hover"]))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(bg_rect, 12, 12)

        # --- 图标 ---
        icon_rect = QRect(rect.left() + 16, rect.top() + (rect.height() - 36) // 2, 36, 36)
        icon = self._icon_for(app)
        if icon is not None and not icon.isNull():
            painter.drawPixmap(icon_rect, icon.pixmap(36, 36))
        else:
            # 首字符圆形占位
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(PALETTE["primary"]))
            painter.drawEllipse(icon_rect)
            painter.setPen(QColor("white"))
            char_font = painter.font()
            char_font.setBold(True)
            char_font.setPixelSize(16)
            painter.setFont(char_font)
            first = (app.name[0] if app.name else "?").upper()
            painter.drawText(icon_rect, Qt.AlignmentFlag.AlignCenter, first)

        # --- 文本区 ---
        text_left = rect.left() + 64
        text_right = rect.right() - 14
        text_top = rect.top() + (rect.height() - 40) // 2

        # 第二行：类型标签 + 路径
        type_color = PALETTE["primary"] if app.app_type == "Win32" else PALETTE["warning"]
        if app.app_type == "自定义":
            type_color = PALETTE["text_secondary"]
        tag_width, tag_height = 38, 16
        tag_rect = QRect(text_left, text_top + 26, tag_width, tag_height)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(type_color))
        painter.drawRoundedRect(tag_rect, 3, 3)
        painter.setPen(QColor("white"))
        tag_font = painter.font()
        tag_font.setPixelSize(10)
        tag_font.setBold(True)
        painter.setFont(tag_font)
        painter.drawText(tag_rect, Qt.AlignmentFlag.AlignCenter, app.app_type)

        # 路径（elide）
        path_x = tag_rect.right() + 8
        path_font = QFont(painter.font())
        path_font.setPixelSize(11)
        path_metrics = QFontMetrics(path_font)
        path_text = str(app.install_location)
        path_width = text_right - path_x
        elided_path = path_metrics.elidedText(path_text, Qt.TextElideMode.ElideMiddle, max(0, path_width))
        painter.setPen(QColor(PALETTE["text_secondary"]))
        painter.setFont(path_font)
        painter.drawText(QRect(path_x, text_top + 23, max(0, path_width), 20),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided_path)

        # 第一行：名称（elide）
        size_text = self._format_size(app.size_bytes)
        size_font = QFont(painter.font())
        size_font.setPixelSize(11)
        size_metrics = QFontMetrics(size_font)
        size_width = size_metrics.horizontalAdvance(size_text) + 4
        name_font = QFont(painter.font())
        name_font.setPixelSize(13)
        name_font.setBold(True)
        name_metrics = QFontMetrics(name_font)
        name_max = text_right - text_left - size_width - 8
        elided_name = name_metrics.elidedText(app.name, Qt.TextElideMode.ElideRight, max(0, name_max))
        painter.setPen(QColor(PALETTE["text"]))
        painter.setFont(name_font)
        painter.drawText(QRect(text_left, text_top, max(0, name_max), 22),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided_name)

        # 大小（右对齐）
        painter.setPen(QColor(PALETTE["text_secondary"]))
        painter.setFont(size_font)
        painter.drawText(QRect(text_right - size_width, text_top, size_width, 22),
                         Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, size_text)

        painter.restore()

    def _icon_for(self, app) -> QIcon:
        # 仅读缓存；未命中返回空图标（paint 用首字符占位），图标由后台线程预取后填充
        return self._icon_cache.get(id(app), QIcon())

    def _icon_source(self, app) -> str | None:
        return self._source_cache.get(id(app))

    def preload_icon_source(self, app) -> str | None:
        """后台线程调用：只做目录搜索 IO，不创建 QIcon（Qt 界面对象需在主线程创建）"""
        key = id(app)
        if key not in self._source_cache:
            try:
                self._source_cache[key] = self._find_icon_file(app)
            except Exception:
                self._source_cache[key] = None
        return self._source_cache[key]

    def apply_icon(self, app) -> None:
        """主线程调用：按已缓存的图标源构建 QIcon，写入缓存"""
        key = id(app)
        if key not in self._icon_cache:
            self._icon_cache[key] = self._resolve_icon(app)

    def _resolve_icon(self, app) -> QIcon:
        source = self._icon_source(app)
        if not source:
            return QIcon()
        # 1. 系统文件图标（exe/ico/dll 直接有效）
        try:
            icon = self._provider.icon(QFileInfo(source))
            if not icon.isNull():
                return icon
        except Exception:
            pass
        # 2. ExtractIconEx 从 exe/dll/ico 直接提取图标资源
        pix = self._extract_icon(source)
        if pix is not None:
            return QIcon(pix)
        # 3. UWP logo png 直接加载
        if source.lower().endswith(".png"):
            pix = QPixmap(source)
            if not pix.isNull():
                return QIcon(pix)
        return QIcon()

    def _find_icon_file(self, app) -> str | None:
        """解析图标源文件：DisplayIcon 指定文件 > 安装目录内评分最佳 exe/ico/UWP logo"""
        # 1. 注册表 DisplayIcon 直接指定
        if app.app_type != "UWP" and app.executable:
            f = self._parse_display_icon(app.executable)
            if f:
                return f
        base = app.install_location
        if not base or not base.is_dir():
            return None
        # 2. 目录内搜索（深度<=2），按与 app 名称的匹配度评分
        name = (app.name or "").lower()
        best, best_score = None, -1
        try:
            for dirpath, dirnames, filenames in os.walk(base):
                depth = len(Path(dirpath).relative_to(base).parts)
                if depth > 2:
                    dirnames.clear()
                    continue
                # 跳过目录联接，防止循环跟随（低版本 Python 无 isjunction 时回退）
                for d in list(dirnames):
                    try:
                        if os.path.isjunction(os.path.join(dirpath, d)):
                            dirnames.remove(d)
                    except (AttributeError, OSError):
                        pass
                for fn in filenames:
                    low = fn.lower()
                    stem = fn[:-4]
                    is_png = low.endswith(".png") and app.app_type == "UWP"
                    if not (low.endswith(".exe") or low.endswith(".ico") or is_png):
                        continue
                    if low.endswith(".exe") and stem.lower() in _BANNED_EXE:
                        continue
                    if is_png:
                        # UWP logo 文件名与 app 名无关，按命名 hint 给分；splash 图排除
                        s = stem.lower()
                        if "splash" in s:
                            continue
                        score = 70 - depth * 5 if (s in _UWP_LOGO_HINTS or "logo" in s or "icon" in s) else -1
                    else:
                        score = self._score_name(stem, name) - depth * 5
                    if score > best_score:
                        best_score = score
                        best = str(Path(dirpath) / fn)
        except Exception:
            pass
        return best

    @staticmethod
    def _score_name(stem: str, app_name: str) -> int:
        """文件主名与 app 名称的匹配得分，越高越可能是主程序"""
        stem, name = stem.lower(), app_name.lower()
        if not stem:
            return -1
        if stem == name:
            return 100
        if name.startswith(stem) or stem.startswith(name):
            return 80
        if stem in name or name in stem:
            return 60
        return 10

    @staticmethod
    def _parse_display_icon(raw: str) -> str | None:
        """解析注册表 DisplayIcon：支持 "path",0、path,-101、裸路径，含环境变量"""
        if not raw:
            return None
        raw = raw.strip()
        m = re.search(r'"([^"]+)"', raw)
        if m:
            p = Path(os.path.expandvars(m.group(1).strip()))
            return str(p) if p.is_file() else None
        if "," in raw:
            p = Path(os.path.expandvars(raw.split(",")[0].strip()))
            if p.is_file():
                return str(p)
        p = Path(os.path.expandvars(raw))
        if p.is_file():
            return str(p)
        m = re.search(r"([A-Za-z]:[^,]+?\.(?:exe|ico|dll))", raw, re.IGNORECASE)
        if m:
            p = Path(os.path.expandvars(m.group(1)))
            return str(p) if p.is_file() else None
        return None

    @staticmethod
    def _extract_icon(path: str):
        """ExtractIconEx 提取文件图标资源，返回 QPixmap；失败返回 None"""
        try:
            import win32gui
            large, small = win32gui.ExtractIconEx(path, 0)
            handles = list(large) + list(small)
            if not handles:
                return None
            pix = QPixmap.fromWinHICON(handles[0])
            for h in handles:
                win32gui.DestroyIcon(h)
            return pix if not pix.isNull() else None
        except Exception:
            return None

    @staticmethod
    def _format_size(size):
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} PB"


class ToastNotification(QWidget):
    """屏幕右下角（任务栏上方）自定义滑出通知弹窗（非系统通知/原生对话框）

    - 无边框置顶、不抢焦点，圆角卡片 + 阴影
    - 从屏幕右侧向左滑入，停留后向右滑出并淡出
    - 可点击 ✕ 立即关闭
    """

    _MARGIN = 16
    _WIDTH = 404

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(self._WIDTH)

        # 滑入：从右侧外平移到目标位置
        self._anim_in = QPropertyAnimation(self, b"pos", self)
        self._anim_in.setDuration(320)
        self._anim_in.setEasingCurve(QEasingCurve.Type.OutCubic)
        # 滑出：向右平移 + 淡出
        self._anim_out = QPropertyAnimation(self, b"pos", self)
        self._anim_out.setDuration(300)
        self._anim_out.setEasingCurve(QEasingCurve.Type.InCubic)
        self._fade_out = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade_out.setDuration(280)
        self._fade_out.setStartValue(1.0)
        self._fade_out.setEndValue(0.0)
        self._out_group = QParallelAnimationGroup(self)
        self._out_group.addAnimation(self._anim_out)
        self._out_group.addAnimation(self._fade_out)
        self._out_group.finished.connect(self.hide)

        self._stay = QTimer(self)
        self._stay.setSingleShot(True)
        self._stay.timeout.connect(self._slide_out)
        self._build_ui()

    def _build_ui(self):
        root = QWidget(self)
        root.setObjectName("toast")
        root.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._root = root
        shadow = QGraphicsDropShadowEffect(root)
        shadow.setBlurRadius(40)
        shadow.setColor(QColor(0, 0, 0, 96))
        shadow.setOffset(0, 8)
        root.setGraphicsEffect(shadow)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 12, 18, 26)   # 给投影留出绘制空间（原 2px 会裁掉阴影）
        outer.addWidget(root)

        lay = QHBoxLayout(root)
        lay.setContentsMargins(16, 14, 12, 14)
        lay.setSpacing(12)

        self._accent = QLabel()
        self._accent.setFixedSize(4, 40)
        lay.addWidget(self._accent, 0, Qt.AlignmentFlag.AlignVCenter)

        texts = QVBoxLayout()
        texts.setSpacing(4)
        self._title = QLabel()          # 文案配色统一由 apply_theme() 落地
        texts.addWidget(self._title)
        self._body = QLabel()
        self._body.setWordWrap(True)
        texts.addWidget(self._body)
        lay.addLayout(texts, 1)

        close = QPushButton()
        close.setIcon(svg_icon(_TOAST_CLOSE_SVG))
        close.setIconSize(QSize(14, 14))
        close.setFixedSize(24, 24)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.clicked.connect(self._close_now)
        lay.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
        self.apply_theme()

    def apply_theme(self):
        """按当前 PALETTE 重建弹窗配色（壁纸/主题切换时由外部调用）。

        弹窗是无父窗口的独立顶层窗口，不随主面板控件树遍历到 —— 构造期烘焙的主题色
        若不主动重刷，切到深色后弹窗会仍是浅色卡片（与浮层卡片同一类缺陷）。
        强调条颜色在 show_toast 时按需取色，不在这里固定。
        """
        self.setStyleSheet(f"""
            QLabel {{ background: transparent; }}
            QPushButton {{
                background: transparent; border: none; border-radius: 12px;
                color: {PALETTE['text_secondary']}; font-size: 14px; font-weight: 700;
            }}
            QPushButton:hover {{ background: {PALETTE['hover']}; color: {PALETTE['danger']}; }}
        """)
        root = getattr(self, "_root", None)
        if root is not None:
            root.setStyleSheet(f"""
                QWidget#toast {{
                    background-color: {PALETTE['card']};
                    border: 1px solid {PALETTE['border']};
                    border-radius: 14px;
                }}
            """)
        if getattr(self, "_title", None) is not None:
            self._title.setStyleSheet(
                f"font-size: 13px; font-weight: 700; color: {PALETTE['text']};")
        if getattr(self, "_body", None) is not None:
            self._body.setStyleSheet(
                f"font-size: 12px; color: {PALETTE['text_secondary']};")

    def show_toast(self, title: str, message: str, warn: bool = False, duration: int = 6000):
        """显示/刷新右下角弹窗并重新滑入"""
        self._title.setText(title)
        self._body.setText(message)
        accent = PALETTE["danger"] if warn else PALETTE["primary"]
        self._accent.setStyleSheet(f"background-color: {accent}; border-radius: 2px;")
        self.adjustSize()

        screen = QApplication.primaryScreen().availableGeometry()
        target = QPoint(screen.right() - self.width() - self._MARGIN,
                        screen.bottom() - self.height() - self._MARGIN)
        self.setWindowOpacity(1.0)
        self.move(screen.right() + 8, target.y())  # 从屏幕右侧外起始
        self.show()
        self.raise_()

        self._anim_in.stop()
        self._anim_in.setStartValue(self.pos())
        self._anim_in.setEndValue(target)
        self._anim_in.start()

        self._stay.stop()
        self._stay.start(duration)

    def _slide_out(self):
        screen = QApplication.primaryScreen().availableGeometry()
        self._anim_out.stop()
        self._fade_out.stop()
        self._anim_out.setStartValue(self.pos())
        self._anim_out.setEndValue(QPoint(screen.right() + 8, self.pos().y()))
        self._out_group.start()

    def _close_now(self, *_):
        self._stay.stop()
        self._anim_in.stop()
        self.hide()
