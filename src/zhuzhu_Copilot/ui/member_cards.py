"""会员 / 积分商品卡片：黑白简约 + 边缘高光 + 推荐档泛蓝光。

视觉规范（整套只用黑 / 灰 / 白，唯一彩色是推荐档的蓝光）：
- **矩形圆角边框**：1px 描边，描边本身做「上亮下暗」的竖向渐变 —— 顶边微亮、底边压深，
  这是卡片有厚度感的关键；圆角统一 12px。
- **边缘高光**：卡片内部顶部有一条横向渐变的白色高光（中间亮、两端淡出），
  配合顶部略亮的底色渐变，形成玻璃边缘的反射感；深色主题更明显，浅色主题作为
  「卡片面比顶边更亮」的细腻过渡。
- **推荐档（Pro）**：描边换成深蓝、外圈叠加多层递减透明度的蓝色光晕，
  顶部叠一枚「推荐」胶囊。一屏里**有且只有**它带彩色与实心按钮。
- **主按钮**：推荐档用反色实心（浅色主题黑底白字 / 深色主题白底黑字），
  其余档位用描边幽灵按钮 —— 视线自然落到推荐项，操作引导不靠文字喊。

卡片自带 14px 外边距（``_EDGE``）：光晕画在这圈里，避免被父布局裁掉；所有卡片
统一这圈留白，内容基线才能对齐。
"""

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout,
)

from zhuzhu_Copilot.core.i18n import ui as _ui
from zhuzhu_Copilot.ui.styles import PALETTE

#: 卡片四周预留：光晕画在这圈内，同时保证多张卡片之间有呼吸空间
_EDGE = 14
#: 圆角
_RADIUS = 12
#: 会员卡与积分卡尺寸（含 _EDGE 外边距）
CARD_W = 244
CARD_H = 252
PACK_H = 212


def _rgb(hex_color: str) -> QColor:
    return QColor(hex_color)


def _mix(a: str, b: str, t: float) -> str:
    """按比例混合两色（t=0 取 a，t=1 取 b）"""
    ca, cb = QColor(a), QColor(b)
    t = max(0.0, min(1.0, float(t)))
    return QColor(
        round(ca.red() + (cb.red() - ca.red()) * t),
        round(ca.green() + (cb.green() - ca.green()) * t),
        round(ca.blue() + (cb.blue() - ca.blue()) * t),
    ).name()


def _is_dark() -> bool:
    return QColor(PALETTE["bg_top"]).lightness() < 128


def _alpha(hex_color: str, a: int) -> QColor:
    c = QColor(hex_color)
    c.setAlpha(max(0, min(255, int(a))))
    return c


class EdgeCard(QFrame):
    """矩形圆角卡片：卡面渐变 + 上亮下暗描边 + 顶部高光（可选外发光）。"""

    def __init__(self, parent=None, *, glow: bool = False, radius: int = _RADIUS,
                 width: int = 0, height: int = 0, padding=(18, 16, 18, 16)):
        super().__init__(parent)
        self._glow = bool(glow)
        self._radius = int(radius)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        if width:
            self.setFixedWidth(int(width))
        if height:
            self.setFixedHeight(int(height))
        self._body = QVBoxLayout(self)
        self._body.setContentsMargins(_EDGE + padding[0], _EDGE + padding[1],
                                      _EDGE + padding[2], _EDGE + padding[3])
        self._body.setSpacing(6)

    # ---------- 配色 ----------
    def _colors(self) -> dict:
        dark = _is_dark()
        card = PALETTE["card"]
        border = PALETTE["border"]
        accent = PALETTE["primary"]
        if dark:
            # 卡面统一暗色平面：不再做"顶亮底暗"的竖向渐变（那是给卡面加的内部
            # 泛光，用户明确不要），一行底色让整页元素色调一致
            bg_top = card
            edge_top = _mix(border, "#FFFFFF", 0.42)
            edge_bottom = border
            glint = 74
        else:
            # 浅色：顶部压一档冷灰，白色高光才有对比可依（纯白卡面上白线是看不见的）
            bg_top = _mix(card, PALETTE["bg_top"], 0.85)
            edge_top = _mix(border, "#FFFFFF", 0.70)
            edge_bottom = border
            glint = 255
        if self._glow:
            edge_top = _mix(accent, "#FFFFFF", 0.25)
            edge_bottom = accent
        return dict(bg_top=bg_top, bg_bottom=card, edge_top=edge_top,
                    edge_bottom=edge_bottom, glint=glint, accent=accent)

    # ---------- 绘制 ----------
    def paintEvent(self, _ev):
        c = self._colors()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(_EDGE, _EDGE, -_EDGE, -_EDGE)
        if r.width() <= 2 or r.height() <= 2:
            return

        # 1) 推荐档外发光：多层递减透明度的圆角描边，由外向内收紧。
        #    先画光晕再画卡面：贴住卡面的那一半会被卡面盖住，只留下自然的外扩。
        if self._glow:
            for i in range(1, 9):
                k = i / 9.0
                pen = QPen(_alpha(c["accent"], int(88 * (1 - k) ** 2)))
                pen.setWidthF(i * 2.2)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(r.adjusted(-i, -i, i, i),
                                  self._radius + i, self._radius + i)

        # 2) 卡面：竖向微渐变（顶亮底暗），比纯色更有「面」的质感
        grad = QLinearGradient(0, r.top(), 0, r.bottom())
        grad.setColorAt(0.0, _rgb(c["bg_top"]))
        grad.setColorAt(1.0, _rgb(c["bg_bottom"]))
        path = QPainterPath()
        path.addRoundedRect(r, self._radius, self._radius)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawPath(path)

        # 3) 描边：上亮下暗；1px 落在半像素上才不发虚
        edge = QLinearGradient(0, r.top(), 0, r.bottom())
        edge.setColorAt(0.0, _rgb(c["edge_top"]))
        edge.setColorAt(1.0, _rgb(c["edge_bottom"]))
        p.setPen(QPen(edge, 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5),
                          self._radius - 0.5, self._radius - 0.5)

        # 4) 顶部边缘高光：中间亮、两端淡出的白色细线（玻璃边缘反射）
        glint = QLinearGradient(r.left() + self._radius, 0,
                                r.right() - self._radius, 0)
        glint.setColorAt(0.0, _alpha("#FFFFFF", 0))
        glint.setColorAt(0.5, _alpha("#FFFFFF", c["glint"]))
        glint.setColorAt(1.0, _alpha("#FFFFFF", 0))
        p.setPen(QPen(glint, 1.6))
        y = r.top() + 1.4
        p.drawLine(QPointF(r.left() + self._radius, y),
                   QPointF(r.right() - self._radius, y))
        p.end()


class _Pill(QLabel):
    """卡片上的小胶囊基类：绝对定位（不占布局高度）。"""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = QFont("Microsoft YaHei UI")
        f.setPixelSize(10)
        f.setBold(True)
        self.setFont(f)

    def restyle(self):
        self.setStyleSheet(self._qss())
        self.adjustSize()


class _Badge(_Pill):
    """推荐胶囊：叠在卡片顶部描边上，一半在外、一半在内。"""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setObjectName("membBadge")

    def _qss(self) -> str:
        return (f"#membBadge {{ background: {PALETTE['primary']}; color: #FFFFFF;"
                " border: none; border-radius: 9px; padding: 2px 10px;"
                " font-weight: 700; }")


class _Chip(_Pill):
    """「当前」胶囊：描边样式，留在黑白灰体系内。"""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setObjectName("membChip")
        f = self.font()
        f.setBold(False)
        self.setFont(f)

    def _qss(self) -> str:
        return (f"#membChip {{ color: {PALETTE['text_secondary']};"
                f" border: 1px solid {PALETTE['border']}; border-radius: 8px;"
                " padding: 1px 7px; background: transparent; font-weight: 400; }")


class ProductCard(EdgeCard):
    """一张商品卡：标题 / 价格 / 权益条目 / 主按钮。

    ``recommended=True`` 的档位才带蓝光、推荐胶囊与实心按钮；其余档位一律黑白灰。
    """

    def __init__(self, *, title: str, price_main: str, price_unit: str = "",
                 perks=None, cta: str = "", cta_enabled: bool = True,
                 cta_tip: str = "", recommended: bool = False, current: bool = False,
                 badge: str = "", height: int = CARD_H, parent=None):
        super().__init__(parent, glow=recommended, width=CARD_W, height=height)
        self._recommended = bool(recommended)
        self._enabled = bool(cta_enabled)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)
        self._title = QLabel(title)
        head.addWidget(self._title, 0, Qt.AlignmentFlag.AlignVCenter)
        self._chip = _Chip(_ui("当前")) if current else None
        if self._chip is not None:
            head.addWidget(self._chip, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        self._body.addLayout(head)

        price_row = QHBoxLayout()
        price_row.setContentsMargins(0, 2, 0, 2)
        price_row.setSpacing(4)
        self._price = QLabel(price_main)
        self._price.setObjectName("membPrice")
        price_row.addWidget(self._price, 0, Qt.AlignmentFlag.AlignBaseline)
        self._unit = QLabel(price_unit)
        self._unit.setObjectName("membUnit")
        price_row.addWidget(self._unit, 0, Qt.AlignmentFlag.AlignBaseline)
        price_row.addStretch(1)
        self._body.addLayout(price_row)

        self._perks, self._dots = [], []
        for text in (perks or []):
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(7)
            dot = QLabel("·")
            dot.setObjectName("membDot")
            dot.setFixedWidth(6)
            row.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
            lab = QLabel(text)
            lab.setObjectName("membPerk")
            lab.setWordWrap(True)
            row.addWidget(lab, 1)
            self._body.addLayout(row)
            self._perks.append(lab)
            self._dots.append(dot)
        self._body.addStretch(1)

        self.buy_btn = QPushButton(cta)
        self.buy_btn.setAutoDefault(False)
        self.buy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.buy_btn.setFixedHeight(34)
        self.buy_btn.setToolTip(cta_tip)
        self.buy_btn.setEnabled(self._enabled)
        self._body.addWidget(self.buy_btn)

        self._badge = _Badge(badge, self) if badge else None
        self.restyle()

    # ---------- 外部状态 ----------
    def set_cta(self, label: str, enabled: bool, tip: str = ""):
        """刷新主按钮（购买 / 续费 / 升级 / 当前版本 / 不可购买）。"""
        self._enabled = bool(enabled)
        self.buy_btn.setText(label)
        self.buy_btn.setEnabled(self._enabled)
        self.buy_btn.setToolTip(tip)
        self._apply_button_style()

    # ---------- 主题 ----------
    def restyle(self):
        """重设全部字色与按钮样式（首次构建与主题切换共用）。"""
        self._title.setStyleSheet(
            f"color: {PALETTE['text']}; font-size: 15px; font-weight: 600;"
            " background: transparent;")
        self._price.setStyleSheet(
            f"color: {PALETTE['text']}; background: transparent;"
            " font-size: 26px; font-weight: 700;")
        self._unit.setStyleSheet(
            f"color: {PALETTE['text_secondary']}; font-size: 11px;"
            " background: transparent;")
        for lab in self._perks:
            lab.setStyleSheet(
                f"color: {PALETTE['text_secondary']}; font-size: 11.5px;"
                " background: transparent;")
        for dot in self._dots:
            dot.setStyleSheet(
                f"color: {PALETTE['text_secondary']}; font-size: 13px;"
                " background: transparent;")
        if self._chip is not None:
            self._chip.restyle()
        if self._badge is not None:
            self._badge.restyle()
            self._place_badge()
        self._apply_button_style()
        self.update()

    def _apply_button_style(self):
        c = PALETTE
        if not self._enabled:
            # 禁用态：描边 + 次级字色，绝不沿用实心强调（否则看不出不可点）
            qss = (f"QPushButton {{ background: transparent;"
                   f" color: {c['text_secondary']}; border: 1px solid {c['border']};"
                   " border-radius: 8px; font-size: 12.5px; font-weight: 600; }")
        elif self._recommended:
            # 反色实心：浅色主题黑底白字、深色主题白底黑字（黑白体系里的最强引导）
            qss = (f"QPushButton {{ background: {c['text']}; color: {c['bg_top']};"
                   " border: none; border-radius: 8px; font-size: 12.5px;"
                   " font-weight: 700; }")
        else:
            qss = (f"QPushButton {{ background: transparent; color: {c['text']};"
                   f" border: 1px solid {c['border']}; border-radius: 8px;"
                   " font-size: 12.5px; font-weight: 600; }")
        self.buy_btn.setStyleSheet(qss)

    def _place_badge(self):
        if self._badge is None:
            return
        w = self._badge.sizeHint().width()
        self._badge.resize(w, self._badge.sizeHint().height())
        self._badge.move(int((self.width() - w) / 2), _EDGE - 10)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._place_badge()


class InfoBanner(EdgeCard):
    """信息条（每日签到等）：与商品卡同一套描边 + 高光，无发光、无按钮。"""

    def __init__(self, title: str, subtitle: str = "", parent=None):
        super().__init__(parent, glow=False, width=0, padding=(16, 12, 16, 12))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._title = QLabel(title)
        self._body.addWidget(self._title)
        self._sub = QLabel(subtitle)
        self._sub.setWordWrap(True)
        self._body.addWidget(self._sub)
        self.restyle()

    def set_subtitle(self, text: str):
        self._sub.setText(text or "")

    def restyle(self):
        self._title.setStyleSheet(
            f"color: {PALETTE['text']}; font-size: 13px; font-weight: 600;"
            " background: transparent;")
        self._sub.setStyleSheet(
            f"color: {PALETTE['text_secondary']}; font-size: 11.5px;"
            " background: transparent;")
        self.update()


def build_product_card(*, title: str, price_main: str, price_unit: str = "",
                       perks=None, cta: str = "", cta_enabled: bool = True,
                       cta_tip: str = "", recommended: bool = False,
                       current: bool = False, badge: str = "",
                       compact: bool = False, height: int = CARD_H,
                       parent=None) -> ProductCard:
    """工厂函数：会员档位与积分包用同一套卡片，后者更矮、内边距更紧。"""
    card = ProductCard(title=title, price_main=price_main, price_unit=price_unit,
                       perks=perks, cta=cta, cta_enabled=cta_enabled,
                       cta_tip=cta_tip, recommended=recommended,
                       current=current, badge=badge,
                       height=(PACK_H if compact else height), parent=parent)
    if compact:
        card._body.setContentsMargins(_EDGE + 16, _EDGE + 14, _EDGE + 16, _EDGE + 14)
    return card
