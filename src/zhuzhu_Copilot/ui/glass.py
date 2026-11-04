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
GLASS_DEFAULT = 50          # 磨砂强度默认
# 各模式的默认不透明度：自定义背景薄薄一层白（主要靠模糊出效果）；
# 深/浅色模式下背后是主题渐变，需要更实一点才看得出卡片边界
ALPHA_DEFAULTS = {"wallpaper": 10, "dark": 55, "light": 55}
ALPHA_DEFAULT = ALPHA_DEFAULTS["wallpaper"]

_PREFS_KEY = "ui_glass"          # 磨砂强度（模糊/高光）
# UI 组件不透明度（0=全透明, 100=不透明）：**三套模式各存一份**。
# 自定义背景下这一份就是「白色填充」的不透明度；深/浅色模式各自记自己那份，
# 切换主题时不会把对方的数值带过去（浅色调很透、深色调很实是常见用法）。
_ALPHA_KEY = "ui_glass_alpha"    # 旧版单值：仅用于迁移
_ALPHA_KEYS = {
    "wallpaper": "ui_glass_alpha_wallpaper",
    "dark": "ui_glass_alpha_dark",
    "light": "ui_glass_alpha_light",
}
_DOWNSCALE = 4              # 磨砂底降采样倍数（线性）：像素量降到 1/16
_BLUR_MIN, _BLUR_MAX = 2.0, 24.0

_strength: int | None = None
_alphas: dict = {}          # mode -> 不透明度（懒加载，按需缓存）
_listeners: list = []
_capturing = False          # 抓场景期间禁止重入（隐藏/显示会触发重绘，重绘又来抓 → 递归）
# 每个**窗口**各存一份磨砂层：主面板与 4 个悬浮面板各自有独立窗口，共用一个槽位
# 会互相踩踏 —— 每次换窗口都要重算一遍 720px 的模糊（约 3~8ms），一屏多面板时
# 每帧都在重算。用 WeakKeyDictionary：窗口销毁自动回收，不用手工清理。
_layers: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()   # window → (key, pm, scale)
_LAYER_CACHE_MAX = 6        # 最多缓存几个窗口的磨砂层（主面板 + 4 个浮窗 + 余量）

#: 磨砂层长边上限（px）。磨砂层永远是模糊的，按窗口原尺寸糊一次要 10ms 上下，
#: 窗口拖拽改尺寸时会一顿一顿；缩到 480 再放大，观感一致，成本降到 1ms 级。
_LAYER_MAX_EDGE = 720


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
    reset_layers()                            # 磨砂层随强度重建
    if persist:
        try:
            app_identity.qsettings().setValue(_PREFS_KEY, str(v))
        except Exception:
            pass
    _notify()
    return v


def mode() -> str:
    """当前玻璃所处的底色模式：``wallpaper``（自定义背景）/ ``dark`` / ``light``。"""
    try:
        if app_wallpaper.active():
            return "wallpaper"
    except Exception:
        pass
    return "dark" if QColor(PALETTE["bg_top"]).lightness() < 128 else "light"


def alpha(mode_key: str | None = None) -> int:
    """UI 组件不透明度 0~100（0=全透明，100=不透明）。

    与「磨砂强度」解耦：强度决定**糊多厉害**，这里决定**透多少**。

    按模式各存一份：自定义背景 = 白色填充的不透明度（默认 10%，主要靠模糊出
    效果）；深色 / 浅色模式 = 该模式填充色的不透明度（默认 55%）。这样浅色调很
    透、深色调很实互不影响，也不会切主题后判若两套。
    """
    key = mode_key or mode()
    if key in _alphas:
        return _alphas[key]
    default = ALPHA_DEFAULTS.get(key, 50)
    val = None
    try:
        raw = app_identity.qsettings().value(_ALPHA_KEYS.get(key, _ALPHA_KEY), None)
        if raw is not None:
            val = int(float(raw))
        elif key == "wallpaper":
            # 旧版只有单值，且只在自定义背景场景下被调过 → 迁移成这一份的初始值；
            # 深/浅色模式用各自默认值（沿用旧值可能把深色面板调成全透明）
            legacy = app_identity.qsettings().value(_ALPHA_KEY, None)
            if legacy is not None:
                val = int(float(legacy))
    except Exception:
        val = None
    if val is None:
        val = default
    val = max(GLASS_MIN, min(GLASS_MAX, val))
    _alphas[key] = val
    return val


def set_alpha(value: int, persist: bool = True, mode_key: str | None = None) -> int:
    """写入**指定模式**（默认当前模式）的不透明度并刷新所有玻璃表面。"""
    key = mode_key or mode()
    try:
        v = int(round(float(value)))
    except Exception:
        v = 50
    v = max(GLASS_MIN, min(GLASS_MAX, v))
    _alphas[key] = v
    if persist:
        try:
            app_identity.qsettings().setValue(_ALPHA_KEYS.get(key, _ALPHA_KEY), str(v))
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
    """半透明填充的不透明度：由用户设定的「组件透明度」直接决定。

    0（最透明）→ 0.10（仍留一点底，保证文字可读）；100（不透明）→ 1.0。
    默认 alpha=50 → 0.5，与旧版「强度 50 = 半透明」的观感一致。
    """
    return max(0.10, min(1.0, alpha() / float(GLASS_MAX)))


def blur_radius() -> float:
    """磨砂半径（全尺寸像素）：强度 0 → 不糊，50 → 13px，100 → 24px。"""
    return _BLUR_MIN + (_BLUR_MAX - _BLUR_MIN) * ratio()


def rim_alpha() -> float:
    """边缘高光的峰值透明度：强度越大，玻璃越"凸"。"""
    return 0.06 + 0.30 * ratio()


# ---------------------------------------------------------------- 变更通知
def subscribe(callback) -> None:
    """登记重绘回调（玻璃表面在 __init__ 里调；控件销毁后自动失效）。

    存「弱引用宿主 + 方法名」而不是 WeakMethod：Qt 的内建方法没有 ``__func__``，
    WeakMethod 会抛 AttributeError（不是 TypeError），会把构造期直接打断。
    """
    owner = getattr(callback, "__self__", None)
    name = getattr(callback, "__name__", "")
    try:
        if owner is not None and name:
            _listeners.append((weakref.ref(owner), name))
        else:
            _listeners.append((weakref.ref(callback), ""))
    except TypeError:
        return


def _notify() -> None:
    alive = []
    for ref, name in _listeners:
        obj = ref()
        if obj is None:
            continue                    # 控件已销毁：顺手丢掉这条登记
        alive.append((ref, name))
        try:
            (getattr(obj, name) if name else obj)()
        except Exception:
            continue                    # 单个表面刷新失败不影响其它表面
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


def reset_layers() -> None:
    """作废所有窗口的磨砂层缓存（改强度 / 换壁纸 / 换主题后调用）。"""
    _layers.clear()


def _frosted_layer(window):
    """返回 ``(磨砂层, 缓存键, 缩放比)``；``磨砂层`` 为 None 表示该窗口没有背景可采样。"""
    """整窗磨砂底（壁纸 + 压暗纱 + 玻璃额外模糊），长边不超过 ``_LAYER_MAX_EDGE``。

    返回的是缩小版：调用方按 ``_layer["scale"]`` 折算源矩形，绘制时放大回目标尺寸。
    非宿主窗口返回 None（对话框等独立窗口不在壁纸上，只能叠半透明填充）。
    """
    if not enabled() or window is None:
        return None, None, 1.0
    try:
        if not window.property("_glass_host"):
            return None, None, 1.0
        size = window.size()
    except RuntimeError:
        return None, None, 1.0
    key = _layer_key(size)
    hit = _layers.get(window)
    if hit is not None and hit[1] == key:      # (pm, key, scale)
        return hit
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
    entry = (pm, key, scale)      # 顺序与返回声明一致：(磨砂层, 缓存键, 缩放比)
    if len(_layers) >= _LAYER_CACHE_MAX:
        _layers.clear()                 # 极端情况（大量浮窗）：整批作废，代价一次性
    _layers[window] = entry
    return entry


def capture_scene(widget, rect: QRect | None = None,
                  blur_scale: float = 1.0) -> None:
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
    # 布局内的控件隐藏会触发父布局重排（兄弟控件跳动）；这类表面本来也不做反射，
    # 直接放弃抓取，回落到壁纸层采样
    try:
        parent = widget.parentWidget()
        if was and parent is not None and parent.layout() is not None:
            return
    except RuntimeError:
        return
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
    # blur_scale > 1：给文字密集的面板更深的磨砂（背景文字更容易被"抹平"）
    blurred = app_wallpaper.blur_pixmap(pm, blur_radius() * max(1.0, float(blur_scale)))
    off_x, off_y = pos.x() - src.x(), pos.y() - src.y()
    try:
        snap = blurred.copy(off_x, off_y, rect.width(), rect.height())
    except Exception:
        return
    widget.__dict__["_glass_scene"] = snap


def invalidate(widget, *, scene: bool = False) -> None:
    """丢掉控件的玻璃贴图缓存并重绘（点一下玻璃就没了时用它强制重建一次）。

    scene=True 时连场景反射快照一起丢弃（下次绘制回落到壁纸层，不会出现空白底）。
    """
    try:
        widget.__dict__.pop("_glass_block", None)
        if scene:
            widget.__dict__.pop("_glass_scene", None)
        widget.update()
    except RuntimeError:
        pass


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
    native = max(1.0, float(widget.devicePixelRatioF()))
    layer, layer_key, layer_scale = (_frosted_layer(widget.window()) if enabled()
                                     else (None, None, 1.0))
    scene = widget.__dict__.get("_glass_scene") if (reflect and enabled()) else None
    if scene is not None and scene.size() != rect.size():
        scene = None
    # 贴图一律按原生 dpr 生成：1:1 blit 是最快的路径。曾试过"大表面用 1x 省显存"，
    # 结果每帧都要把 1x 平滑放大回 2x，实测反而从 6ms 涨到 13ms（得不偿失）
    area_px = rect.width() * rect.height() * native * native
    dpr = native
    # 用 cacheKey 而不是 id()：id 会被回收后复用，可能把旧快照当成新的（画面卡住）
    key = (layer_key, scene.cacheKey() if scene is not None else 0,
           rect.x(), rect.y(), rect.width(), rect.height(),
           round(radius, 2), tint, round(min_alpha, 3),
           round(dpr, 2))
    cache = widget.__dict__.get("_glass_block")
    if cache is not None and cache[0] == key:
        return cache[1]

    pm = QPixmap(max(1, round(rect.width() * dpr)), max(1, round(rect.height() * dpr)))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    # 磨砂层是缩小版，放大回目标尺寸必须走平滑插值：默认最近邻会放大成一块块
    # 方块像素（用户截图里的"高斯模糊像素感太明显"）
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
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
        k = float(layer_scale or 1.0)
        if pos is not None and pos.x() >= 0 and pos.y() >= 0 \
                and pos.x() + rect.width() <= layer.width() * k \
                and pos.y() + rect.height() <= layer.height() * k:
            p.setClipPath(path)              # 底图裁进圆角，四角才不会出直角
            p.drawPixmap(QRectF(0, 0, rect.width(), rect.height()), layer,
                         QRectF(pos.x() / k, pos.y() / k,
                                rect.width() / k, rect.height() / k))
    fill = QColor(tint)
    fill.setAlphaF(max(0.0, min(1.0, min_alpha)))
    p.fillPath(path, fill)
    p.end()

    if area_px <= _BLOCK_CACHE_MAX_PX:
        widget.__dict__["_glass_block"] = (key, pm)
    return pm


def _rim(p: QPainter, rect: QRect, radius: float) -> None:
    """边缘高光：**顶边最亮，沿左右两侧向下渐弱到侧边中部**，下半圈是普通发丝线。

    画法只有一条描边：竖直渐变的笔刷（顶=亮 → 侧边中部=发丝线 → 底=发丝线），
    于是「顶边 → 两侧上半段」形成由高到低的连续过渡，底部保持安静。
    不往内部填任何渐变（用户明确不要内部泛光），线宽固定 1px（宽线在长直边上
    会出现抗锯齿撕裂的亮带）。
    """
    hi = QColor(255, 255, 255)
    hi.setAlphaF(min(0.9, rim_alpha() + 0.22))
    # 壁纸模式下填充是白的，描边也必须同色系，否则白玻璃外面套一圈灰线
    if app_wallpaper.active():
        line = QColor(255, 255, 255)
        line.setAlphaF(0.34)
    else:
        line = QColor(PALETTE["border"])
        line.setAlphaF(0.55)

    inner = QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5)
    path = QPainterPath()
    path.addRoundedRect(inner, radius, radius)

    grad = QLinearGradient(inner.topLeft(), inner.bottomLeft())
    grad.setColorAt(0.0, hi)      # 顶边：最亮
    grad.setColorAt(0.5, line)     # 两侧中部：收到发丝线
    grad.setColorAt(1.0, line)     # 底边：保持发丝线
    pen = QPen(QBrush(grad), 1.0)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)


def paint_surface(widget, p: QPainter, rect: QRect | None = None,
                  radius: float | None = None, tint: str | None = None,
                  rim: bool = True, min_alpha: float = 0.0,
                  reflect: bool = False) -> None:
    """把一个表面画成玻璃：整块贴图（磨砂 + 半透明填充）+ 边缘高光。

    ``tint`` 是该表面的主题底色（卡片 card / 浮层 elevated），强度决定其不透明度；
    强度为 0 时画回实色底 + 发丝描边，与旧观感一致（不走贴图，最省）。
    ``min_alpha`` 给「文字密度高、必须保证可读」的表面（通知）兜底不透明度。
    ``reflect=True`` 优先用 ``capture_scene()`` 抓到的真实场景帧（能反出背景里的
    卡片/文字），没有快照时退回壁纸层采样。

    **自定义背景模式下底统一取白色**（不再是各表面的主题色）：否则深色主题的面板
    偏黑、浅色主题的偏白，同一屏上"黑白交替"（用户截图反馈）。白色填充 + 磨砂
    才能得到一致的玻璃观感。
    """
    rect = QRect(widget.rect()) if rect is None else QRect(rect)
    if rect.isEmpty():
        return
    radius = RADIUS_MD if radius is None else float(radius)
    # 壁纸模式统一白底；非壁纸模式沿用各表面的主题底色
    base = "#FFFFFF" if app_wallpaper.active() else (tint or PALETTE["card"])

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

    # 不透明度完全交给「组件透明度」（分模式各存一份），这里只保留一个下限，
    # 防止调成 0 之后卡片彻底消失、只剩描边
    a = max(min_alpha, opacity())
    block = _glass_block(widget, rect, radius, base, a, reflect=reflect)
    p.drawPixmap(rect.topLeft(), block)

    if rim:
        _rim(p, rect, radius)


def fill_inner(widget, p: QPainter, rect: QRect | None = None,
               radius: float | None = None, color: str | None = None,
               alpha: float = 0.32, inset: float = 1.4) -> None:
    """**只改内部填充**：把颜色填在描边内侧，边缘高光与磨砂底完全不动。

    hover / press 用它而不是换 tint —— 换 tint 会连整块贴图（含描边高光）一起重建，
    悬停时边缘会跟着变色/变亮，观感像"整块被点亮"。这里只在内缩 1.4px 的圆角区域
    里叠一层色，描边保持原样。
    """
    rect = QRect(widget.rect()) if rect is None else QRect(rect)
    if rect.isEmpty():
        return
    radius = RADIUS_MD if radius is None else float(radius)
    inner = QRectF(rect).adjusted(inset, inset, -inset, -inset)
    if inner.width() <= 1 or inner.height() <= 1:
        return
    path = QPainterPath()
    path.addRoundedRect(inner, max(0.0, radius - inset), max(0.0, radius - inset))
    c = QColor(color or PALETTE["primary_light"])
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    p.fillPath(path, c)


def repaint_all() -> None:
    """强度变化后刷新所有玻璃表面（供设置页滑杆使用）。

    只需作废磨砂层 + 通知订阅者：每个表面下一次绘制会用新键重新裁切，
    裁剪缓存因此自动失效（键里含层键）。
    """
    reset_layers()
    _notify()
