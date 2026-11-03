"""头像渲染：把头像引用画成圆形 QPixmap / QIcon。

- 有头像：按「填满圆形」等比缩放并居中裁切，四角透明；
- 无头像：圆形底色 + 名字首字占位（颜色取自当前主题色板，不引入新配色）。

纯渲染工具，不持有状态；调用方每次重绘重新取图即可随主题/头像变化。
"""

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap

from zhuzhu_Copilot.core import agent_avatar
from zhuzhu_Copilot.ui.styles import PALETTE


def _placeholder(name: str) -> str:
    """占位字：名字首个非空白字符（中文取单字，英文取大写首字母）。"""
    for ch in (name or "").strip():
        if not ch.isspace():
            return ch.upper() if ch.isascii() else ch
    return "?"


def avatar_pixmap(ref: str, name: str, size: int, kind: str = "agent",
                  bg: str = "", fg: str = "", ring: str = "") -> QPixmap:
    """渲染圆形头像（size×size，含透明边）。

    bg / fg / ring 留空时取当前色板：底色=凹陷字段色、字色=次级文字色、
    描边=卡片描边色（一圈极细的描边让浅色底上的头像边界可辨）。
    """
    size = max(8, int(size))
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    circle = QPainterPath()
    # 内缩 0.5px：描边不会被裁掉一半（否则圆边看起来发虚）
    circle.addEllipse(QRectF(0.5, 0.5, size - 1.0, size - 1.0))
    p.setClipPath(circle)

    path = agent_avatar.resolve(ref, kind) if ref else ""
    if path:
        src = QPixmap(path)
        if not src.isNull():
            scaled = src.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap(int((size - scaled.width()) / 2),
                         int((size - scaled.height()) / 2), scaled)
        else:
            path = ""
    if not path:
        p.fillPath(circle, QColor(bg or PALETTE["field"]))
        p.setPen(QPen(QColor(fg or PALETTE["text_secondary"])))
        f = QFont("Microsoft YaHei UI")
        f.setPixelSize(max(9, int(size * 0.42)))
        f.setBold(False)
        p.setFont(f)
        p.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter,
                   _placeholder(name))

    # 细描边：浅色主题下白色头像贴白底时靠它区分边界
    p.setClipping(False)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor(ring or PALETTE["border"]), 1.0))
    p.drawEllipse(QRectF(0.5, 0.5, size - 1.0, size - 1.0))
    p.end()
    return pm


def avatar_icon(ref: str, name: str, size: int, kind: str = "agent",
                bg: str = "", fg: str = "") -> QIcon:
    """头像 QIcon（卡片列表等按 QIcon 取图的控件用）。"""
    return QIcon(avatar_pixmap(ref, name, size, kind=kind, bg=bg, fg=fg))
