"""玻璃皮肤：真磨砂底 + 半透明填充 + 边缘高光。

强度 0~100 由「设置 → 背景 → 磨砂强度」调节（复用设置页的胶囊滑杆），存 QSettings：

    0    关闭玻璃：表面回归实色面板（与旧观感一致，最省 CPU）
    50   默认：填充 50% 通透 + 中等磨砂（用户指定的基准）
    100  最通透（alpha≈0.12）+ 最强磨砂与边缘高光

为什么「磨砂」要自己再糊一层，而不是让表面透出壁纸就完事：
壁纸的模糊是**整窗统一**的（设置 → 背景 → 模糊程度）。表面若只是半透明，透出来的
模糊度与四周完全一致 —— 看不出「玻璃比背景更糊」的层次，只是一块透明色片。
这里为玻璃表面单独再糊一层，才有真实的厚度感。

性能（本项目的硬约束：性能 > 美观）：
  · 磨砂底按**窗口尺寸**算一次并缓存：壁纸先铺进 1/4 降采样画布再高斯模糊
    （半径同比缩小），像素量降为 1/16，观感一致但成本一个数量级下降；
  · 每个表面只做一次「裁剪 + blit」，裁剪结果也缓存（键含位置/尺寸/主题/强度）；
  · 无壁纸时不糊（身后只是主题渐变，糊了也看不出），只叠半透明填充与高光；
  · 强度 0 时完全短路，不建任何缓存。
"""

import weakref

from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, Qt
from PyQt6.QtGui import (QBrush, QColor, QLinearGradient, QPainter, QPainterPath,
                         QPen, QPixmap, QRadialGradient)

from zhuzhu_Copilot import app_identity
from zhuzhu_Copilot.core import app_wallpaper
from zhuzhu_Copilot.ui.styles import PALETTE
from zhuzhu_Copilot.ui.tokens import RADIUS_MD

GLASS_MIN = 0
GLASS_MAX = 100
GLASS_DEFAULT = 50

_PREFS_KEY = "ui_glass"
_DOWNSCALE = 4              # 磨砂底降采样倍数（线性）：像素量降到 1/16
_BLUR_MIN, _BLUR_MAX = 2.0, 24.0

_strength: int | None = None
_listeners: list = []
_capturing = False          # 抓场景期间禁止重入（隐藏/显示会触发重绘，重绘又来抓 → 递归）
# pm 是「长边不超过 _LAYER_MAX_EDGE」的缩小版磨砂层，scale 为 窗口px / 图层px
_layer: dict = {"key": None, "pm": None, "scale": 1.0}

#: 磨砂层长边上限（px）。磨砂层永远是模糊的，按窗口原尺寸糊一次要 10ms 上下，
#: 窗口拖拽改尺寸时会一顿一顿；缩到 480 再放大，观感一致，成本降到 1ms 级。
_LAYER_MAX_EDGE = 480


# ---------------------------------------------------------------- 强度 / 派生量
def strength() -> int:
    """当前磨砂强度 0~100（首次读 QSettings，之后走内存）。"""
    global _strength
    if _strength is None:
        try:
            raw = app_identity.qsettings().value(_PREFS_KEY, GLASS_DEFAULT)
            _strength = int(float(raw))
        except Exception:
            _strength = GLASS_DEFAULT
        _strength = max(GLASS_MIN, min(GLASS_MAX, _strength))
    return _strength


def set_strength(value: int, persist: bool = True) -> int:
    """写入强度并通知所有玻璃表面重绘；返回落库后的值。"""
    global _strength
    try:
        v = int(round(float(value)))
    except Exception:
        v = GLASS_DEFAULT
    v = max(GLASS_MIN, min(GLASS_MAX, v))
    _strength = v
    _layer["key"] = None                      # 磨砂层随强度重建
    if persist:
        try:
            app_identity.qsettings().setValue(_PREFS_KEY, str(v))
        except Exception:
            pass
    _notify()
    return v


def ratio() -> float:
    """强度归一化到 0~1。"""
    return strength() / float(GLASS_MAX)


def enabled() -> bool:
    return strength() > GLASS_MIN


def opacity() -> float:
    """半透明填充的不透明度：强度 0 → 1.0（实色），50 → 0.5，100 → 0.12。"""
    return max(0.12, 1.0 - ratio())


def blur_radius() -> float:
    """磨砂半径（全尺寸像素）：强度 0 → 不糊，50 → 13px，100 → 24px。"""
    return _BLUR_MIN + (_BLUR_MAX - _BLUR_MIN) * ratio()


def rim_alpha() -> float:
    """边缘高光的峰值透明度：强度越大，玻璃越"凸"。"""
    return 0.06 + 0.30 * ratio()


# ---------------------------------------------------------------- 变更通知
def subscribe(callback) -> None:
    """登记重绘回调（玻璃表面在 __init__ 里调；控件销毁后自动失效）。"""
    try:
        ref = weakref.WeakMethod(callback) if getattr(callback, "__self__", None) \
            else weakref.ref(callback)
    except TypeError:
        return
    _listeners.append(ref)


def _notify() -> None:
    alive = []
    for ref in _listeners:
        cb = ref()
        if cb is None:
            continue
        alive.append(ref)
        try:
            cb()
        except RuntimeError:
            continue        # 控件已销毁
        except Exception:
            continue
    _listeners[:] = alive


# ---------------------------------------------------------------- 磨砂底
def register_host(window) -> None:
    """标记「背后真的画着壁纸」的顶层窗口（主面板）。

    只有主面板适合做真磨砂：它在自己的 paintEvent 里铺壁纸，子表面裁它的像素才
    对得上。对话框是独立不透明窗口，若也去采样壁纸，会把自己的窗口里画上壁纸，
    与其余区域割裂 —— 所以未登记为宿主时只叠半透明填充 + 高光。
    """
    try:
        window.setProperty("_glass_host", True)
    except RuntimeError:
        pass


def _layer_key(size) -> tuple:
    p = app_wallpaper.params()
    # 模糊档位取整到 4px：拖动强度滑杆时只有跨档才重建磨砂层，填充透明度
    # 是绘制期实时值（不参与缓存），因此拖动过程仍然跟手
    blur_step = int(round(blur_radius() / 4.0))
    return (size.width(), size.height(), PALETTE["bg_top"], blur_step,
            p.bg_image, p.bg_fit, round(float(p.bg_blur), 2),
            round(float(p.bg_dim), 2), app_wallpaper.active())


def _frosted_layer(window) -> QPixmap | None:
    """整窗磨砂底（壁纸 + 压暗纱 + 玻璃额外模糊），长边不超过 ``_LAYER_MAX_EDGE``。

    返回的是缩小版：调用方按 ``_layer["scale"]`` 折算源矩形，绘制时放大回目标尺寸。
    非宿主窗口返回 None（对话框等独立窗口不在壁纸上，只能叠半透明填充）。
    """
    if not enabled() or window is None:
        return None
    try:
        if not window.property("_glass_host"):
            return None
    except RuntimeError:
        return None
    size = window.size()
    key = _layer_key(size)
    if _layer["key"] == key and _layer["pm"] is not None:
        return _layer["pm"]
    pm = None
    scale = 1.0
    if app_wallpaper.active() and size.width() > 1 and size.height() > 1:
        longest = max(size.width(), size.height())
        scale = max(1.0, longest / float(_LAYER_MAX_EDGE))
        sw = max(1, round(size.width() / scale))
        sh = max(1, round(size.height() / scale))
        small = QPixmap(sw, sh)
        small.fill(Qt.GlobalColor.transparent)
        p = QPainter(small)
        p.scale(sw / size.width(), sh / size.height())   # 缩小画布上仍按整窗坐标铺壁纸
        ok = app_wallpaper.paint(p, QRect(0, 0, size.width(), size.height()))
        p.end()
        if ok:
            pm = app_wallpaper.blur_pixmap(small, blur_radius() / scale)
    _layer["key"], _layer["pm"], _layer["scale"] = key, pm, scale
    return pm


def capture_scene(widget, rect: QRect | None = None) -> None:
    """抓「widget 背后**真实渲染**的内容」并磨砂化，存到控件上供后续绘制使用。

    与壁纸层采样的区别：壁纸层只有背景图，看不到压在上面的卡片、气泡与文字；
    这里直接取窗口当前帧（壁纸 + 兄弟控件 + 文本），所以弹层/悬浮按钮能"反"出
    它真正压着的东西。仅在自定义背景模式下有意义（无壁纸时背后就是主题渐变）。

    抓图瞬间**暂时隐藏本控件**：否则会把自己的上一帧拍进去，形成自反射的正反馈
    （画面表现为越描越亮的鬼影）。隐藏/显示在同一个调用栈内完成，不跑事件循环，
    因此不会闪。
    """
    global _capturing
    if _capturing or not enabled() or not app_wallpaper.active():
        return
    rect = QRect(widget.rect()) if rect is None else QRect(rect)
    if rect.isEmpty():
        return
    try:
        win = widget.window()
        if win is None:
            return
        pos = widget.mapTo(win, rect.topLeft())
    except RuntimeError:
        return
    margin = int(round(blur_radius())) + 2          # 留出模糊过渡带，避免边缘发黑
    src = QRect(pos.x() - margin, pos.y() - margin,
                rect.width() + margin * 2, rect.height() + margin * 2)
    src = src.intersected(win.rect())
    if src.isEmpty():
        return
    was = widget.isVisible()
    _capturing = True
    try:
        if was:
            widget.setVisible(False)
        pm = win.grab(src)
    except RuntimeError:
        return
    finally:
        if was:
            try:
                widget.setVisible(True)
            except RuntimeError:
                pass
        _capturing = False
    if pm is None or pm.isNull():
        return
    blurred = app_wallpaper.blur_pixmap(pm, blur_radius())
    off_x, off_y = pos.x() - src.x(), pos.y() - src.y()
    try:
        snap = blurred.copy(off_x, off_y, rect.width(), rect.height())
    except Exception:
        return
    widget.__dict__["_glass_scene"] = snap


def drop_scene(widget) -> None:
    """丢弃控件上的场景反射快照（背景变了但控件还没重新抓时调用）。"""
    widget.__dict__.pop("_glass_scene", None)


#: 合成块缓存上限（物理像素）：超过就不缓存，避免大卡片把内存吃光。
#: 1.5M ≈ 6MB/张，覆盖设置页卡片与各类弹层；再大（整页级卡片）改为每帧重建，
#: 这类控件只在改尺寸/换肤时整块重绘，重建成本可接受。
_BLOCK_CACHE_MAX_PX = 1_500_000


def _glass_block(widget, rect: QRect, radius: float, tint: str,
                 min_alpha: float, reflect: bool = False) -> QPixmap:
    """生成（并缓存）整块玻璃贴图：**圆角磨砂底 + 半透明填充**，四角已透明。

    为什么预合成成一张图：磨砂底 blit（~2.2ms/950x600@2x）、半透明填充（~1.2ms）、
    高光描边（~0.8ms）三步都在每次 paintEvent 里做，一次全卡片重绘要 4ms 以上。
    合成后每帧只剩一次整块 blit，重绘成本与「有没有玻璃」基本无关。
    键含层键/位置/尺寸/圆角/底色/不透明度/dpr，任一变化自动重建。
    """
    dpr = max(1.0, float(widget.devicePixelRatioF()))
    # 先取磨砂层（会把 _layer["key"] 算好），再拼缓存键，否则首次调用键里是 None
    layer = _frosted_layer(widget.window()) if enabled() else None
    scene = widget.__dict__.get("_glass_scene") if (reflect and enabled()) else None
    if scene is not None and scene.size() != rect.size():
        scene = None
    # 用 cacheKey 而不是 id()：id 会被回收后复用，可能把旧快照当成新的（画面卡住）
    key = (_layer["key"], scene.cacheKey() if scene is not None else 0,
           rect.x(), rect.y(), rect.width(), rect.height(),
           round(radius, 2), tint, round(min_alpha, 3), round(opacity(), 3),
           round(dpr, 2))
    cache = widget.__dict__.get("_glass_block")
    if cache is not None and cache[0] == key:
        return cache[1]

    pm = QPixmap(max(1, round(rect.width() * dpr)), max(1, round(rect.height() * dpr)))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, rect.width(), rect.height()), radius, radius)

    if scene is not None:
        p.setClipPath(path)                  # 场景帧已裁好尺寸，只需裁圆角
        p.drawPixmap(0, 0, scene)
    elif layer is not None:
        try:
            pos = widget.mapTo(widget.window(), rect.topLeft())
        except RuntimeError:
            pos = None
        k = float(_layer.get("scale") or 1.0)
        if pos is not None and pos.x() >= 0 and pos.y() >= 0 \
                and pos.x() + rect.width() <= layer.width() * k \
                and pos.y() + rect.height() <= layer.height() * k:
            p.setClipPath(path)              # 底图裁进圆角，四角才不会出直角
            p.drawPixmap(QRectF(0, 0, rect.width(), rect.height()), layer,
                         QRectF(pos.x() / k, pos.y() / k,
                                rect.width() / k, rect.height() / k))
    fill = QColor(tint)
    fill.setAlphaF(max(min_alpha, opacity()))
    p.fillPath(path, fill)
    p.end()

    if rect.width() * rect.height() * dpr * dpr <= _BLOCK_CACHE_MAX_PX:
        widget.__dict__["_glass_block"] = (key, pm)
    return pm


def _rim(p: QPainter, rect: QRect, radius: float) -> None:
    """边缘高光：只在**左上 / 右下**两个角做高光覆盖（不再整圈描边）。

    做法：同一条圆角路径描两次，各自裁进对角线一侧 ——
    左上半区用「角上亮、向中心衰减」的渐变，右下半区同样处理。
    这样光只落在两个对角，中间四条边没有线，观感是"玻璃被斜上方光源打亮"，
    而不是给每个控件套一圈边框。缝（中段的主题发丝线）另由 ``_hairline`` 补，
    避免无壁纸时半透明卡片与背景糊在一起。
    """
    hi = QColor(255, 255, 255)
    hi.setAlphaF(min(0.85, rim_alpha() + 0.18))
    inner = QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5)
    path = QPainterPath()
    path.addRoundedRect(inner, radius, radius)

    # 左上角高光：裁到左上三角，白 → 透明
    p.save()
    p.setClipRect(QRectF(rect.x(), rect.y(), rect.width(), rect.height() * 0.62))
    grad_tl = QLinearGradient(inner.topLeft(), inner.bottomRight())
    grad_tl.setColorAt(0.0, hi)
    grad_tl.setColorAt(0.5, QColor(255, 255, 255, 0))
    p.setPen(QPen(QBrush(grad_tl), 1.4))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)
    p.restore()

    # 右下角高光：裁到右下三角，白 → 透明（同一条渐变反向取用）
    p.save()
    p.setClipRect(QRectF(rect.x(), rect.y() + rect.height() * 0.38,
                         rect.width(), rect.height() * 0.62))
    grad_br = QLinearGradient(inner.bottomRight(), inner.topLeft())
    grad_br.setColorAt(0.0, hi)
    grad_br.setColorAt(0.5, QColor(255, 255, 255, 0))
    p.setPen(QPen(QBrush(grad_br), 1.4))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)
    p.restore()

    # 角部柔光：两块很淡的径向光斑压在角内侧，"覆盖"感更接近实拍玻璃
    for cx, cy in ((rect.x(), rect.y()),
                   (rect.x() + rect.width(), rect.y() + rect.height())):
        rad = min(rect.width(), rect.height()) * 0.34
        if rad < 6:
            continue
        glow = QRadialGradient(QPointF(cx, cy), rad)
        c = QColor(255, 255, 255)
        c.setAlphaF(min(0.12, rim_alpha() * 0.40))
        glow.setColorAt(0.0, c)
        glow.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.save()
        p.setClipPath(path)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(glow))
        p.drawRect(rect)
        p.restore()


def _hairline(p: QPainter, rect: QRect, radius: float) -> None:
    """中段发丝线：只在自定义背景/玻璃态下补一层极淡的边界，防止半透明卡片糊进背景。

    与角部高光同色系（主题 border），透明度压到很低，不是"第二圈边框"。
    """
    line = QColor(PALETTE["border"])
    line.setAlphaF(0.55)
    p.setPen(QPen(line, 1.0))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)


def paint_surface(widget, p: QPainter, rect: QRect | None = None,
                  radius: float | None = None, tint: str | None = None,
                  rim: bool = True, min_alpha: float = 0.0,
                  reflect: bool = False, hairline: bool = True) -> None:
    """把一个表面画成玻璃：整块贴图（磨砂 + 半透明填充）+ 边缘高光。

    ``tint`` 是该表面的主题底色（卡片 card / 浮层 elevated），强度决定其不透明度；
    强度为 0 时画回实色底 + 发丝描边，与旧观感一致（不走贴图，最省）。
    ``min_alpha`` 给「文字密度高、必须保证可读」的表面（通知）兜底不透明度。
    ``reflect=True`` 优先用 ``capture_scene()`` 抓到的真实场景帧（能反出背景里的
    卡片/文字），没有快照时退回壁纸层采样。
    """
    rect = QRect(widget.rect()) if rect is None else QRect(rect)
    if rect.isEmpty():
        return
    radius = RADIUS_MD if radius is None else float(radius)
    base = tint or PALETTE["card"]

    if not enabled():
        # 关闭玻璃：实色圆角 + 发丝描边（与旧观感一致，且这条路最省）
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), radius, radius)
        p.fillPath(path, QColor(base))
        if rim:
            p.setPen(QPen(QColor(PALETTE["border"]), 1.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        return

    block = _glass_block(widget, rect, radius, base, min_alpha, reflect=reflect)
    p.drawPixmap(rect.topLeft(), block)

    if rim:
        _rim(p, rect, radius)
        if hairline:
            _hairline(p, rect, radius)


def repaint_all() -> None:
    """强度变化后刷新所有玻璃表面（供设置页滑杆使用）。

    只需作废磨砂层 + 通知订阅者：每个表面下一次绘制会用新键重新裁切，
    裁剪缓存因此自动失效（键里含层键）。
    """
    _layer["key"] = None
    _notify()
