# -*- coding: utf-8 -*-
"""全局磨砂玻璃外观内核：参数化 + 面板级缓存。

职责边界：本模块只回答「一块玻璃该长什么样」，不认识界面上有哪些控件。
因此主面板、对话框、弹层、聊天气泡都能复用同一份实现，观感天然一致。

三层结构
  1. 参数层  ``GlassParams`` / ``PARAM_SPECS``
     外观参数的唯一事实来源：设置页滑杆、校验夹紧、持久化、agent 工具共用同一份规格。
  2. 资源层  ``GlassBackground``
     背景图 → 按参数签名缓存的模糊位图。模糊是最贵的一步，**只在参数或尺寸变化时重算一次**，
     之后所有玻璃层共享同一份位图（连续无缝），逐控件零额外开销。
  3. 绘制层  ``paint_glass_surface`` / ``GlassSurface`` / ``GlassSkin``

分层约束：core 层不得反向依赖 ui 层（全仓现状如此），故本模块不 import 任何 ui.*。
主题相关的两件事通过注入解决：
  - ``set_theme_probe(fn)``：由 ui 层注入「当前是否浅色主题」的判定；
  - 磨砂底色 ``_FROST_LIGHT`` / ``_FROST_DARK`` 是本模块的具名常量（与 ui 层色板同值）。

四色规范：高光与描边只用纯白，磨砂底色由主题决定（浅色为白、深色为石墨黑），
本模块不出现任何彩色字面量。
"""

from __future__ import annotations

import json
import math
import shutil
import threading
import weakref
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt, QTimer
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QGuiApplication,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PyQt6.QtWidgets import QWidget

from zhuzhu_Copilot import app_identity

# ── 几何规格（玻璃专属档位；控件圆角仍走 ui.tokens，调用方可显式传入 radius） ──
RADIUS_GLASS = 12        # 玻璃面板默认圆角
RADIUS_WINDOW = 18       # 无边框窗口圆角（与既有窗口蒙版一致）
RADIUS_NONE = 0          # 铺满窗口的根玻璃层不切圆角

# ── 渲染常量（全部具名，禁止在绘制逻辑里散落魔法数值） ──
_FROST_ALPHA_MAX = 168       # frost=1 时磨砂底的 alpha（越大越"毛"、越不透明）
_TOPPGLOSS_ALPHA_MAX = 92    # 顶部受光高光的标称 alpha
_SHEEN_ALPHA_MAX = 62        # 对角液光的标称 alpha
_GLOW_ALPHA_MAX = 96         # 底部微光的标称 alpha
_STROKE_ALPHA_MAX = 186      # 边缘反光描边的标称 alpha
_STROKE_WIDTH = 1
_LIQUID_DRIFT = 0.45         # 液光横向摆幅（相对宽度）
_LIQUID_RISE = 0.50          # 顶部高光纵向流动幅度（相对高度）
_LIQUID_SHEEN_FLOOR = 0.40   # 液态感对液光强度的静态占比下限

# 模糊降采样：模糊本身抹掉高频，先缩小再模糊再放大会得到视觉等价结果，
# 而耗时降一个量级（滑杆拖动时才不至于卡住主线程）。上限即模糊前的最大边长。
_BLUR_MAX_EDGE = 768
_BLUR_MIN_EDGE = 1

# 液态相位推进：_PHASE_INTERVAL_MS 一跳，_PHASE_CYCLE_S 秒走完一个循环
# 根表面（主面板/子窗口/设置页）的磨砂纱强度：这些是**不透明窗口**，壁纸
# 画上去之后必须再压一层纱，否则正文直接叠在照片上不可读。
_ROOT_VEIL_FLOOR = 0.42   # 最低强度（保证文字可读的地板）
_ROOT_VEIL_SPAN = 0.34    # frost 再往上加的部分（frost=1 → 0.76）

# 面板类大表面（设置页 / 侧栏面板 / 预览面板）的更透档：
# 同样由「磨砂程度」驱动，但地板更低 → 壁纸透出更多、不再是一块块深色底板。
# 与聊天气泡分开：气泡的正文密度高，仍用上面的默认地板。
PANEL_FILL_FLOOR = 0.30
PANEL_FILL_SPAN = 0.26

# 「禁止深色背景附着」：玻璃开启时，容器类表面一律不再铺自己的底色
# （直接透出窗口根部的磨砂玻璃）；只有确实需要边界的交互态才用一层极淡的
# **白色**洗色 —— 既看得出控件范围与悬停反馈，又不是一块深色底。
SHEER_WASH = 0.10           # 小控件（按钮 / 输入框补底）的浅色洗色
SHEER_WASH_STRONG = 0.22    # 选中 / 当前项
HOVER_WASH_ALPHA = 0.5      # 附着（悬停 / 按下）态：白色 50% 透明（用户指定）

_PHASE_INTERVAL_MS = 33
_PHASE_CYCLE_S = 4.0
_PHASE_STEP = (_PHASE_INTERVAL_MS / 1000.0) / _PHASE_CYCLE_S

# 磨砂底色（主题无关的兜底值；浅色=白、深色=石墨黑，与 ui 层色板 BG 同值）
_FROST_LIGHT = "#FFFFFF"
_FROST_DARK = "#101216"

# 背景图存放目录名与允许的扩展名（白名单，避免把任意文件当图片读）
_BACKGROUND_DIR_NAME = "backgrounds"
_BACKGROUND_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".webp")
_CONFIG_NAME = "glass.json"


# ══════════════════════════ 1) 参数层 ══════════════════════════

@dataclass(frozen=True)
class ParamSpec:
    """一个可调参数的完整规格：设置页滑杆、取值夹紧、显示格式都由它派生。

    新增一个可调维度只需往 ``PARAM_SPECS`` 里加一条——UI、持久化、agent 工具
    会自动认得它，不需要改任何一处渲染或设置代码。
    """

    key: str
    label: str
    low: float
    high: float
    default: float
    step: float
    hint: str
    unit: str = ""
    percent: bool = False

    def clamp(self, value) -> float:
        """把任意输入夹到合法区间并吸附到步长；非法输入回落默认值。"""
        try:
            v = float(value)
        except (TypeError, ValueError):
            return float(self.default)
        if math.isnan(v) or math.isinf(v):
            return float(self.default)
        v = min(float(self.high), max(float(self.low), v))
        if self.step > 0:
            steps = round((v - self.low) / self.step)
            v = self.low + steps * self.step
        return round(v, 4)


PARAM_SPECS: tuple[ParamSpec, ...] = (
    ParamSpec("blur", "背景模糊", 0, 48, 18, 1,
              "背景图透出前的高斯模糊半径：越大越朦胧，细节越不可辨", unit="px"),
    ParamSpec("frost", "磨砂程度", 0.0, 1.0, 0.42, 0.01,
              "磨砂底的不透明程度：越大越“毛”，背景越被遮蔽", percent=True),
    ParamSpec("edge", "边缘高光", 0.0, 1.0, 0.55, 0.01,
              "玻璃受光边缘描边与顶部反光的强度：越大玻璃越有厚度", percent=True),
    ParamSpec("opacity", "透明度", 0.0, 1.0, 0.35, 0.01,
              "整层玻璃的不透明度：0 完全透明只见背景，1 完全不透明", percent=True),
    ParamSpec("liquid", "液态感", 0.0, 1.0, 0.25, 0.01,
              "液光强度与底部呼吸的幅度：越大越像流动的液体", percent=True),
)

_PARAM_BY_KEY = {s.key: s for s in PARAM_SPECS}

# 背景适配方式（与 CSS background-size 同名，便于理解）
BG_FITS = ("cover", "contain", "stretch", "tile")
_BG_FIT_DEFAULT = "cover"


def spec(key: str) -> Optional[ParamSpec]:
    """按 key 取参数规格；未知 key 返回 None（调用方据此忽略扩展字段）。"""
    return _PARAM_BY_KEY.get(key)


def param_keys() -> tuple[str, ...]:
    return tuple(s.key for s in PARAM_SPECS)


def format_value(s: ParamSpec, value) -> str:
    """参数值的显示文本（设置页与工具回执共用，避免两处各写一套格式）。"""
    v = s.clamp(value)
    if s.percent:
        return f"{int(round(v * 100))}%"
    return f"{int(round(v))}{s.unit}"


@dataclass(frozen=True)
class GlassParams:
    """全局玻璃外观参数（不可变）。

    修改一律走 ``with_param`` / ``with_fields`` 生成新实例，再交给 ``set_params`` 广播——
    避免「改了一半被别的代码读到」的中间态。
    """

    enabled: bool = True                 # 玻璃总开关（关闭即回到不透明主题底色）
    bg_image: str = ""                   # 背景图绝对路径（空 = 用主题渐变）
    bg_fit: str = _BG_FIT_DEFAULT        # cover / contain / stretch / tile
    blur: float = 18
    frost: float = 0.42
    edge: float = 0.55
    opacity: float = 0.35
    liquid: float = 0.25
    liquid_anim: bool = False            # 液态流动动效（默认关闭：持续重绘有 CPU 代价）
    frameless: bool = False              # 无边框玻璃窗口外壳（默认关闭：会换掉系统标题栏）

    # ---- 取值 ----

    def value(self, key: str) -> float:
        """取数值型参数。

        非数值（含布尔、字符串等脏数据）**回落该参数的默认值**而不是 0——
        回落 0 会把「配置损坏」悄悄变成「模糊关掉、磨砂全无」这种难以察觉的坏外观。
        """
        v = getattr(self, key, None)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            s = _PARAM_BY_KEY.get(key)
            return float(s.default) if s is not None else 0.0
        return float(v)

    def clamped(self) -> "GlassParams":
        """按 PARAM_SPECS 夹紧全部数值参数，并校正 bg_fit / bg_image。"""
        data = {s.key: s.clamp(self.value(s.key)) for s in PARAM_SPECS}
        fit = self.bg_fit if self.bg_fit in BG_FITS else _BG_FIT_DEFAULT
        return replace(self, bg_fit=fit, bg_image=str(self.bg_image or ""), **data)

    def is_light(self) -> bool:
        """当前主题是否浅色——决定磨砂底色（白 / 石墨黑）。"""
        return theme_is_light()

    def frost_color(self) -> QColor:
        return QColor(_FROST_LIGHT if theme_is_light() else _FROST_DARK)

    # ---- 派生 ----

    def with_param(self, key: str, value) -> "GlassParams":
        """按参数规格改一个数值参数；未知 key 原样返回（前向兼容）。"""
        s = spec(key)
        if s is None:
            return self
        return replace(self.clamped(), **{key: s.clamp(value)})

    def with_fields(self, **kw) -> "GlassParams":
        """改任意字段（enabled / bg_image / bg_fit / liquid_anim 等）并夹紧。"""
        known = {k: v for k, v in kw.items() if hasattr(self, k)}
        if not known:
            return self
        return replace(self, **known).clamped()

    def signature(self) -> tuple:
        """渲染缓存签名：任何影响绘制结果的字段都必须在里面。"""
        return (self.enabled, self.bg_image, self.bg_fit, self.blur, self.frost,
                self.edge, self.opacity, self.liquid, theme_is_light())

    # ---- 序列化 ----

    def to_dict(self) -> dict:
        return {
            "enabled": bool(self.enabled),
            "bg_image": self.bg_image,
            "bg_fit": self.bg_fit,
            **{s.key: self.value(s.key) for s in PARAM_SPECS},
            "liquid_anim": bool(self.liquid_anim),
            "frameless": bool(self.frameless),
        }

    @classmethod
    def from_dict(cls, data) -> "GlassParams":
        """从任意字典构造（未知键忽略、缺失键取默认、非法值夹紧）。"""
        if not isinstance(data, dict):
            return cls()
        base = cls()
        kw = {
            "enabled": bool(data.get("enabled", base.enabled)),
            "bg_image": str(data.get("bg_image", "") or ""),
            "bg_fit": str(data.get("bg_fit", base.bg_fit) or _BG_FIT_DEFAULT),
            "liquid_anim": bool(data.get("liquid_anim", base.liquid_anim)),
            "frameless": bool(data.get("frameless", base.frameless)),
        }
        for s in PARAM_SPECS:
            kw[s.key] = data.get(s.key, s.default)
        return cls(**kw).clamped()


# ── 参数状态：全局单例 + 弱引用订阅（主面板与各弹窗共享同一份参数） ──

_lock = threading.RLock()
_params: Optional[GlassParams] = None
_listeners: list = []


def _config_path() -> Path:
    """glass.json 路径。惰性求值（data_root 会随迁移/测试切换家目录而变化）。"""
    return app_identity.data_root() / "agent" / _CONFIG_NAME


def load() -> GlassParams:
    """从磁盘读参数（带进程内缓存）。文件缺失/损坏一律回落默认值。"""
    global _params
    with _lock:
        if _params is not None:
            return _params
        data = {}
        try:
            p = _config_path()
            if p.is_file():
                data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        _params = GlassParams.from_dict(data)
        return _params


def save(p: GlassParams) -> bool:
    """写盘。失败静默返回 False（外观设置丢失不应影响功能）。"""
    try:
        path = _config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(p.to_dict(), ensure_ascii=False, indent=2),
                        encoding="utf-8", newline="\n")
        return True
    except Exception:
        return False


def params() -> GlassParams:
    return load()


def set_params(p: GlassParams, persist: bool = True) -> GlassParams:
    """设置参数并广播给所有订阅者，返回**夹紧后**的实际值。"""
    global _params
    fixed = p.clamped() if isinstance(p, GlassParams) else GlassParams.from_dict(p)
    with _lock:
        _params = fixed
    if persist:
        save(fixed)
    _notify()
    return fixed


def set_param(key: str, value, persist: bool = True) -> GlassParams:
    """改单个参数（设置页滑杆 / agent 工具共用入口）。"""
    return set_params(params().with_param(key, value), persist=persist)


def set_fields(persist: bool = True, **kw) -> GlassParams:
    """改非数值字段（enabled / bg_image / bg_fit / liquid_anim）。"""
    return set_params(params().with_fields(**kw), persist=persist)


def reset(persist: bool = True) -> GlassParams:
    """恢复默认外观（不动背景图以外的用户数据）。"""
    d = GlassParams()
    return set_params(replace(d, bg_image=params().bg_image), persist=persist)


def reset_all(persist: bool = True) -> GlassParams:
    """恢复出厂外观（含清除背景图）。"""
    return set_params(GlassParams(), persist=persist)


def _weak_callback(cb: Callable[[], None]):
    """把任意可调用对象包成弱引用，避免订阅者被参数模块长期持有而泄漏。

    绑定方法必须走 WeakMethod（普通 weakref.ref 会随临时对象立刻失效）；
    不可弱引用的可调用对象（如某些内建）退化为强引用——这类极少，且调用方显式注册。
    """
    import inspect
    if inspect.ismethod(cb):
        return weakref.WeakMethod(cb)
    try:
        return weakref.ref(cb)
    except TypeError:
        return lambda: cb


def subscribe(cb: Callable[[], None]) -> None:
    """订阅参数变化（弱引用持有，调用方无需手动反订阅）。"""
    if not callable(cb):
        return
    with _lock:
        _listeners.append(_weak_callback(cb))


def unsubscribe(cb: Callable[[], None]) -> None:
    """取消订阅，并顺手清掉已失效的弱引用。

    **必须实现**：订阅者若先于参数模块销毁（如窗口关闭），仍然会被回调到；
    回调里再去碰已销毁的 Qt 对象（例如 `QTimer(self)`）是进程级崩溃而不是异常。
    """
    if not callable(cb):
        return
    with _lock:
        keep = []
        for ref in _listeners:
            fn = ref()
            if fn is None:
                continue                      # 已失效，顺手回收
            if fn == cb:
                continue                      # 绑定方法按 (实例, 函数) 比较，可正确命中
            keep.append(ref)
        _listeners[:] = keep


def _notify() -> None:
    """在锁外依次回调，避免回调里再次 set_params 造成死锁。"""
    with _lock:
        refs = list(_listeners)
    alive = []
    for ref in refs:
        fn = ref()
        if fn is None:
            continue
        alive.append(ref)
        try:
            fn()
        except Exception:
            pass
    with _lock:
        _listeners[:] = alive


def invalidate() -> None:
    """丢弃参数缓存，下次 ``params()`` 重新读盘（切换数据目录/测试用）。"""
    global _params
    with _lock:
        _params = None


# ══════════════════════════ 2) 背景图资源 ══════════════════════════

def backgrounds_dir() -> Path:
    return app_identity.data_root() / "agent" / _BACKGROUND_DIR_NAME


def import_background(source, apply: bool = True) -> tuple:
    """把一张图片收编进应用数据目录并返回 ``(ok, msg, stored_path)``。

    为什么必须复制而不是直接引用原路径：用户随手选的图可能在临时目录、
    下载目录或被清理的相册里，直接引用会导致「重启后背景丢失」。

    ``apply=True``（默认）同时把它设为当前背景 —— 收图与应用是同一个意图，
    拆成两步只会让调用方漏掉第二步。
    """
    src = Path(str(source or "")).expanduser()
    if not src.is_file():
        return (False, f"背景图不存在：{src}", "")
    ext = src.suffix.lower()
    if ext not in _BACKGROUND_EXTS:
        return (False, f"不支持的图片格式 {ext or '(无扩展名)'}，"
                       f"可用：{'、'.join(_BACKGROUND_EXTS)}", "")
    # 收编前先验证 Qt 真的能解码：解不了的图一旦设为背景，会让窗口根底转
    # 透明却没有壁纸可画（透明黑块）。在这里拒绝并给出明确原因。
    from PyQt6.QtGui import QPixmap
    if QPixmap(str(src)).isNull():
        return (False, f"无法识别的图片：{src.name}（Qt 不支持该格式或文件已损坏）", "")
    try:
        dest_dir = backgrounds_dir()
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"wallpaper{ext}"
        if src.resolve() != dest.resolve():
            shutil.copyfile(src, dest)
        # 目录里只保留当前一张：换图后旧扩展名的壁纸文件会变成死数据
        for old in dest_dir.glob("wallpaper.*"):
            if old.suffix.lower() != ext:
                try:
                    old.unlink()
                except OSError:
                    pass
        _SOURCE_CACHE.clear()
        _TINT_CACHE.clear()
        if apply:
            set_fields(bg_image=str(dest))
        return (True, f"背景图已设置：{dest.name}", str(dest))
    except Exception as exc:
        return (False, f"复制背景图失败：{exc}", "")


def clear_background(purge_files: bool = True) -> tuple:
    """清除背景图设置（默认同时删掉已收编的图片文件）。返回 ``(ok, msg)``。"""
    if purge_files:
        try:
            d = backgrounds_dir()
            if d.is_dir():
                for f in d.iterdir():
                    if f.is_file() and f.suffix.lower() in _BACKGROUND_EXTS:
                        f.unlink()
        except Exception:
            pass
    _SOURCE_CACHE.clear()
    p = set_fields(bg_image="")
    return (True, f"已清除背景图，回到主题渐变（当前透明度 "
                  f"{format_value(spec('opacity'), p.value('opacity'))}）")


# ══════════════════════════ 主题探测（由 ui 层注入） ══════════════════════════

_theme_probe: Optional[Callable[[], bool]] = None


def set_theme_probe(fn: Optional[Callable[[], bool]]) -> None:
    """注入「当前是否浅色主题」的判定函数。

    刻意做成注入而不是直接读 ui 层：core 不反向依赖 ui，且测试可注入确定值。
    """
    global _theme_probe
    _theme_probe = fn


def theme_is_light() -> bool:
    if _theme_probe is not None:
        try:
            return bool(_theme_probe())
        except Exception:
            return False
    return False


# ══════════════════════════ 3) 绘制层 ══════════════════════════

def gaussian_blur(pixmap: QPixmap, radius: float) -> QPixmap:
    """真正的毛玻璃模糊：Qt 没有 CSS 那样的 backdrop-filter，用 Pillow 做真高斯模糊。

    大半径下先降采样再模糊再放大（视觉等价、耗时降一个量级）。
    输入为 None / 空图 / 半径为 0 时原样返回；任何失败都不抛异常，退回未模糊图。
    """
    if pixmap is None or pixmap.isNull():
        return pixmap
    r = max(0.0, float(radius))
    if r <= 0:
        return pixmap
    src = pixmap
    factor = 1
    edge = max(pixmap.width(), pixmap.height())
    if edge > _BLUR_MAX_EDGE:
        factor = max(1, int(math.ceil(edge / float(_BLUR_MAX_EDGE))))
        src = pixmap.scaled(max(_BLUR_MIN_EDGE, pixmap.width() // factor),
                            max(_BLUR_MIN_EDGE, pixmap.height() // factor),
                            Qt.AspectRatioMode.IgnoreAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)
    try:
        import io as _io
        from PIL import Image, ImageFilter
        from PyQt6.QtCore import QBuffer, QIODevice
        from PyQt6.QtGui import QImage
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        src.save(buf, "PNG")
        png = bytes(buf.data())
        buf.close()
        pil = Image.open(_io.BytesIO(png)).convert("RGBA")
        pil = pil.filter(ImageFilter.GaussianBlur(r / factor))
        raw = pil.tobytes("raw", "RGBA")
        qimg = QImage(raw, pil.width, pil.height, pil.width * 4,
                      QImage.Format.Format_RGBA8888)
        out = QPixmap.fromImage(qimg)
    except Exception:
        return pixmap
    if out.isNull():
        return pixmap
    if factor > 1:
        out = out.scaled(pixmap.size(),
                         Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    return out


_SOURCE_CACHE: dict = {}
_TINT_CACHE: dict = {}      # 壁纸平均色缓存（路径, mtime）→ QColor


def _source_pixmap(path: str, mtime: int) -> Optional[QPixmap]:
    """按 (路径, mtime) 缓存原图，避免每次缩放窗口都重新解码大图。"""
    key = (path, mtime)
    hit = _SOURCE_CACHE.get(key)
    if hit is not None and not hit.isNull():
        return hit
    pm = QPixmap(path)
    if pm.isNull():
        return None
    _SOURCE_CACHE.clear()
    _SOURCE_CACHE[key] = pm
    return pm


def wallpaper_ok() -> bool:
    """当前配置的背景图是否**真的能解码显示**。

    文件存在 ≠ 能显示：Qt 不认的格式/损坏文件若只判存在，会让窗口根底
    转成透明却没有壁纸可画 → 界面变成透明黑块。必须以能加载出位图为准。
    """
    p = load()
    path = p.bg_image or ""
    if not p.enabled or not path:
        return False
    try:
        mtime = Path(path).stat().st_mtime_ns
    except OSError:
        return False
    return _source_pixmap(path, mtime) is not None


def root_veil_color(fallback: str, alpha_scale: float = 1.0) -> QColor:
    """根表面的磨砂纱颜色（壁纸平均色，带 alpha）。

    强度 = 地板 + frost 追加，再乘整层透明度；纱越厚文字越可读，壁纸越含蓄。
    """
    q = load()
    c = QColor(frost_surface_color(fallback))
    # 刻意**不乘** opacity：这一层是可读性地板。若乘上去，用户把透明度调低
    # 正文就会重新压在照片上（用户反馈"设置项被遮挡"的第二个成因）。
    a = (_ROOT_VEIL_FLOOR + _ROOT_VEIL_SPAN * max(0.0, min(1.0, q.frost))) \
        * max(0.0, min(1.0, alpha_scale))
    c.setAlpha(int(round(255 * max(0.0, min(1.0, a)))))
    return c


def wallpaper_tint() -> Optional[QColor]:
    """壁纸平均色（1×1 采样），给不透明控件一个「来自背景」的玻璃底色。

    背景图被模糊后以低透明度透出，但有些表面是**系统级不透明窗口**（如
    QToolTip、原生对话框）——真半透明在它们身上会与窗口默认底色叠成黑色。
    这类表面改用壁纸平均色做底，观感同样是「磨砂玻璃」，且完全不依赖合成。
    无壁纸 / 玻璃关闭 / 读取失败返回 None（调用方回落主题色）。
    """
    p = load()
    path = p.bg_image or ""
    if not p.enabled or not path:
        return None
    try:
        mtime = Path(path).stat().st_mtime_ns
    except OSError:
        return None
    key = (path, mtime)
    hit = _TINT_CACHE.get(key)
    if hit is not None:
        return QColor(hit)
    src = _source_pixmap(path, mtime)
    if src is None:
        return None
    one = src.scaled(1, 1, Qt.AspectRatioMode.IgnoreAspectRatio,
                     Qt.TransformationMode.SmoothTransformation)
    c = one.toImage().pixelColor(0, 0)
    _TINT_CACHE.clear()
    _TINT_CACHE[key] = QColor(c)
    return QColor(c)


def frost_surface_color(fallback: str, alpha: float = 1.0) -> str:
    """不透明表面的磨砂底色：壁纸平均色按磨砂程度压在主题底色上。

    与 ``control_fill`` 的差别：那个返回真半透明 rgba（适合叠在壁纸上的控件），
    这个返回**不透明**实色（适合不能透明的表面）。返回 #RRGGBB 或 #AARRGGBB。
    """
    q = load()
    base = QColor(fallback)
    if not q.enabled:
        c, ratio = base, 0.0
    else:
        c = wallpaper_tint()
        ratio = q.frost if c is not None else 0.0
        if c is None:                      # 无壁纸：用主题磨砂底色兜底
            c = QColor(_FROST_LIGHT if theme_is_light() else _FROST_DARK)
    mixed = QColor(
        int(round(c.red() * ratio + base.red() * (1 - ratio))),
        int(round(c.green() * ratio + base.green() * (1 - ratio))),
        int(round(c.blue() * ratio + base.blue() * (1 - ratio))),
    )
    a = max(0, min(255, int(round(255 * max(0.0, min(1.0, alpha))))))
    return f"#{a:02X}{mixed.red():02X}{mixed.green():02X}{mixed.blue():02X}"


def _quantize(v: int, grid: int = 48) -> int:
    """把屏幕坐标量化到网格：拖动窗口时不至于逐像素重算背景模糊。"""
    return int(v) // grid * grid


def _source_rect_for_window(src_size: QSize, screen: QSize, win: QSize,
                            origin: tuple, fit: str) -> Optional[QRect]:
    """算出「本窗口这一块」在**原图**上对应的矩形（按屏幕坐标映射）。

    背景图若每个窗口各自缩放到自身尺寸，面板之间、面板与桌面之间就对不上，
    视觉上是一块块生硬贴片（用户反馈的「面板对背景的残留元素生硬衔接」）。
    这里统一按屏幕映射：窗口在屏幕上的哪一块，就取背景图的哪一块。
    返回 None 表示沿用「按窗口自身渲染」（平铺 / 窗口落在 contain 留白处）。
    """
    sw, sh = int(src_size.width()), int(src_size.height())
    ww, wh = int(win.width()), int(win.height())
    SW, SH = int(screen.width()), int(screen.height())
    if sw <= 0 or sh <= 0 or ww <= 0 or wh <= 0 or SW <= 0 or SH <= 0:
        return None
    if fit == "tile":
        return None                     # 平铺：按窗口自身重复渲染（少见用法，保持原行为）
    ox, oy = int(origin[0]), int(origin[1])
    if fit == "stretch":
        sx, sy = sw / SW, sh / SH
        return QRect(int(ox * sx), int(oy * sy),
                     max(1, round(ww * sx)), max(1, round(wh * sy)))
    if fit == "contain":
        s = min(sw / SW, sh / SH)       # 图像在屏幕上的显示比例与居中位置
        dw, dh = sw * s, sh * s
        px, py = (SW - dw) / 2, (SH - dh) / 2
        if ox < px or oy < py or ox + ww > px + dw or oy + wh > py + dh:
            return None                 # 窗口压到留白 → 回退窗口自身渲染
        return QRect(int((ox - px) / s), int((oy - py) / s),
                     max(1, round(ww / s)), max(1, round(wh / s)))
    # cover（默认）：等比放大到覆盖整屏后居中裁切
    s = max(sw / SW, sh / SH)
    dw, dh = sw * s, sh * s
    px, py = (SW - dw) / 2, (SH - dh) / 2
    return QRect(int((ox - px) / s), int((oy - py) / s),
                 max(1, round(ww / s)), max(1, round(wh / s)))


def _shape_pixmap(src: QPixmap, size: QSize, fit: str) -> QPixmap:
    """把原图按适配方式铺到目标尺寸（cover/contain/stretch/tile）。"""
    w, h = max(1, size.width()), max(1, size.height())
    out = QPixmap(w, h)
    out.fill(Qt.GlobalColor.transparent)
    if src.isNull() or w <= 0 or h <= 0:
        return out
    if fit == "stretch":
        return src.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
    if fit == "tile":
        painter = QPainter(out)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        step_x = min(w, src.width()) or w
        step_y = min(h, src.height()) or h
        for y in range(0, h, step_y):
            for x in range(0, w, step_x):
                painter.drawPixmap(x, y, src)
        painter.end()
        return out
    if fit == "contain":
        scaled = src.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)
        painter = QPainter(out)
        painter.drawPixmap((w - scaled.width()) // 2,
                           (h - scaled.height()) // 2, scaled)
        painter.end()
        return out
    # cover：等比放大到刚好覆盖，再居中裁切
    scaled = src.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation)
    x = max(0, (scaled.width() - w) // 2)
    y = max(0, (scaled.height() - h) // 2)
    return scaled.copy(QRect(x, y, min(w, scaled.width()), min(h, scaled.height())))


class GlassBackground:
    """一扇窗口的背景资源：背景图（按参数模糊）的有状态缓存。

    生命周期与窗口一致（``GlassSkin`` 持有）。只要参数签名与窗口尺寸不变，
    ``blurred()`` 直接返回同一份 QPixmap —— 这是「面板级缓存」的落点，
    上百个玻璃层共享一次模糊的成果。
    """

    def __init__(self, p: GlassParams, size: QSize,
                 origin: tuple = (0, 0), screen: Optional[QSize] = None):
        self._params = p
        self._size = QSize(max(1, size.width()), max(1, size.height()))
        self._origin = (int(origin[0]), int(origin[1]))   # 窗口左上角在屏幕上的坐标
        self._screen = screen or QSize(1920, 1080)        # 屏幕尺寸（cover 映射基准）
        self._key: Optional[tuple] = None
        self._blurred: Optional[QPixmap] = None

    def set_view(self, origin: tuple, screen: QSize) -> None:
        """更新「窗口在屏幕上的位置 / 屏幕尺寸」；变化则让缓存失效。"""
        origin = (int(origin[0]), int(origin[1]))
        if origin != self._origin or screen != self._screen:
            self._origin = origin
            self._screen = screen
            self._key = None

    def resize(self, size: QSize) -> None:
        w, h = max(1, size.width()), max(1, size.height())
        if (w, h) != (self._size.width(), self._size.height()):
            self._size = QSize(w, h)
            self._key = None

    def set_params(self, p: GlassParams) -> None:
        """换参数：签名变化时下次 ``blurred()`` 自动重算。"""
        if p.signature() != self._params.signature():
            self._key = None
        self._params = p

    def key(self) -> tuple:
        """缓存签名：路径 + mtime 保证换了图/改了图会失效。"""
        path = self._params.bg_image or ""
        mtime = 0
        if path:
            try:
                mtime = Path(path).stat().st_mtime_ns
            except OSError:
                mtime = -1        # 文件消失 → 与"存在"区分开，走无背景分支
        return (path, mtime, self._params.bg_fit,
                int(round(self._params.blur)),
                self._size.width(), self._size.height(),
                self._origin[0], self._origin[1],
                self._screen.width(), self._screen.height())

    def has_wallpaper(self) -> bool:
        path = self._params.bg_image or ""
        return bool(path) and Path(path).is_file()

    def blurred(self) -> Optional[QPixmap]:
        """窗口尺寸的模糊背景图；无背景图或读取失败返回 None。

        内容取自背景图中**本窗口所处的那一块**（按屏幕坐标映射），这样各面板之间、
        面板与桌面之间是连续的同一张壁纸，不会出现一块块贴片式的生硬衔接。
        """
        k = self.key()
        if k == self._key:
            return self._blurred
        if not self.has_wallpaper():
            self._key, self._blurred = k, None
            return None
        src = _source_pixmap(k[0], k[1])
        if src is None:
            self._key, self._blurred = k, None
            return None
        area = _source_rect_for_window(src.size(), self._screen, self._size,
                                       self._origin, self._params.bg_fit)
        if area is not None:
            area = area.intersected(QRect(0, 0, src.width(), src.height()))
        if area is not None and not area.isEmpty():
            piece = src.copy(area).scaled(
                self._size, Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
        else:
            piece = _shape_pixmap(src, self._size, self._params.bg_fit)
        self._blurred = gaussian_blur(piece, self._params.blur)
        self._key = k
        return self._blurred

    def invalidate(self) -> None:
        self._key = None


def paint_glass_surface(painter: QPainter, rect, p: GlassParams,
                        frost_color: Optional[QColor] = None,
                        bg_pixmap: Optional[QPixmap] = None,
                        bg_origin: tuple = (0, 0),
                        radius: int = RADIUS_GLASS,
                        phase: float = 0.0) -> None:
    """在 painter 上绘制一层参数化磨砂玻璃。

    绘制顺序（下 → 上）：透出模糊背景 → 磨砂底 → 顶部受光 → 对角液光 →
    底部微光 → 边缘反光。所有强度均由 ``p`` 驱动，没有任何隐藏的固定系数。

    - ``bg_pixmap`` / ``bg_origin``：窗口尺寸的模糊背景 + 本层左上角在窗口坐标系
      中的位置。相邻玻璃层据此采样到**连续同一张**背景，接缝处不会错位。
    - ``phase``：液态流动相位 0~1，由 ``GlassSkin`` 的定时器推进；0 为静止。
    """
    if painter is None or rect is None:
        return
    if rect.width() <= 0 or rect.height() <= 0 or not p.enabled:
        return

    from PyQt6.QtCore import QPointF, QRectF

    r = QRectF(rect)
    rad = max(0, int(radius))
    ph = max(0.0, min(1.0, float(phase)))
    e = max(0.0, min(1.0, p.edge))
    li = max(0.0, min(1.0, p.liquid))
    op = max(0.0, min(1.0, p.opacity))
    if op <= 0.0:
        return

    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # 圆角裁剪：玻璃厚度/高光都不许溢出圆角
    clip = QPainterPath()
    if rad > 0:
        clip.addRoundedRect(r, rad, rad)
        painter.setClipPath(clip)

    # 1) 透出模糊背景（连续采样：本层在窗口中的位置决定取哪一块）
    if bg_pixmap is not None and not bg_pixmap.isNull():
        ox, oy = int(bg_origin[0]), int(bg_origin[1])
        src = QRect(ox, oy, int(rect.width()), int(rect.height()))
        painter.drawPixmap(QRect(int(rect.left()), int(rect.top()),
                                 int(rect.width()), int(rect.height())),
                           bg_pixmap, src)

    # 2) 磨砂底
    base = QColor(frost_color) if frost_color is not None else QColor(_FROST_LIGHT)
    base.setAlpha(int(round(_FROST_ALPHA_MAX * p.frost * op)))
    if base.alpha() > 0:
        painter.fillRect(rect, base)

    # 3) 顶部受光（强度由 edge 定，纵向位置随 phase 流动）
    top_a = int(round(_TOPPGLOSS_ALPHA_MAX * e * op))
    if top_a > 0:
        shift = r.height() * _LIQUID_RISE * li \
            * (0.5 - 0.5 * math.cos(ph * 2 * math.pi))
        y0 = r.top() + shift
        grad = QLinearGradient(0, y0, 0, y0 + r.height() * 0.62)
        grad.setColorAt(0.0, QColor(255, 255, 255, top_a))
        grad.setColorAt(0.45, QColor(255, 255, 255, int(top_a * 0.2)))
        grad.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.fillRect(rect, QBrush(grad))

    # 4) 对角液光（强度含液态感的静态下限，横向摆动随 phase 流动）
    sheen_a = int(round(_SHEEN_ALPHA_MAX * e * op
                        * (_LIQUID_SHEEN_FLOOR + (1 - _LIQUID_SHEEN_FLOOR) * li)))
    if sheen_a > 0:
        drift = math.sin(ph * 2 * math.pi) * _LIQUID_DRIFT * li
        grad = QLinearGradient(
            QPointF(r.left() + r.width() * drift, r.top()),
            QPointF(r.right() + r.width() * drift, r.bottom()))
        grad.setColorAt(0.0, QColor(255, 255, 255, sheen_a))
        grad.setColorAt(0.34, QColor(255, 255, 255, 0))
        grad.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.fillRect(rect, QBrush(grad))

    # 5) 底部微光（液态感的静态表达：越大越有明显的呼吸光）
    glow_a = int(round(_GLOW_ALPHA_MAX * li * op))
    if glow_a > 0:
        breathe = 0.75 + 0.25 * math.sin(ph * 2 * math.pi)
        a = max(0, min(255, int(glow_a * breathe)))
        grad = QRadialGradient(
            QPointF(r.center().x(), r.bottom() + r.height() * 0.12),
            max(1.0, r.width() * 0.92))
        grad.setColorAt(0.0, QColor(255, 255, 255, a))
        grad.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.fillRect(rect, QBrush(grad))

    painter.restore()

    # 6) 边缘反光（不裁剪，描边压在边界上才完整）
    stroke_a = int(round(_STROKE_ALPHA_MAX * e * op))
    if stroke_a > 0 and rad > 0:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(255, 255, 255, stroke_a))
        pen.setWidth(_STROKE_WIDTH)
        painter.setPen(pen)
        painter.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), rad, rad)
        painter.restore()


class GlassSurface(QWidget):
    """通用磨砂玻璃层：贴在宿主控件上、鼠标穿透、跟随宿主尺寸。

    只服务**承载内容的大面积表面**（顶栏 / 侧栏 / 输入区 / 卡片 / 气泡）。
    按钮、输入框、下拉这类小控件走 QSS 半透明玻璃填充——它们数量多、尺寸小，
    逐控件铺一层 QWidget 得不偿失，观感上也没有区别。
    """

    def __init__(self, host: QWidget, skin: "GlassSkin",
                 radius: int = RADIUS_GLASS):
        super().__init__(host)
        self._skin = skin
        self._radius = max(0, int(radius))
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setStyleSheet("background: transparent;")
        self.setGeometry(host.rect())
        self.lower()
        host.installEventFilter(self)

    def set_radius(self, radius: int) -> None:
        self._radius = max(0, int(radius))
        self.update()

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.Resize:
            try:
                self.setGeometry(obj.rect())
            except Exception:
                pass
        return False

    def paintEvent(self, _ev):
        p = self._skin.params
        if not p.enabled:
            return
        painter = QPainter(self)
        paint_glass_surface(
            painter, self.rect(), p,
            frost_color=p.frost_color(),
            bg_pixmap=self._skin.background_blurred(),
            bg_origin=self._skin.surface_origin(self),
            radius=self._radius,
            phase=self._skin.phase)
        painter.end()


class GlassSkin(QObject):
    """装配到一扇顶层窗口的玻璃皮肤。

    一个窗口一个实例；参数是全局的（``app_glass.params()``），实例只负责
    「按当前参数把自己这扇窗画对」，因此主面板与各弹窗天然一致。

    职责：背景资源缓存、参数变化广播、液态相位定时器、根背景绘制、
    玻璃层登记与几何同步。
    """

    def __init__(self, window: QWidget, radius: int = RADIUS_WINDOW):
        super().__init__(window)
        self._window = window
        self._radius = max(0, int(radius))
        self._bg: Optional[GlassBackground] = None
        self._surfaces: list = []
        self.phase = 0.0
        self._timer: Optional[QTimer] = None
        subscribe(self._on_params_changed)
        window.installEventFilter(self)
        self._sync_timer()

    # ---- 参数与背景 ----

    @property
    def params(self) -> GlassParams:
        return load()

    def _window_view(self) -> tuple:
        """窗口客户区左上角在屏幕上的坐标（量化）与屏幕尺寸。

        量化到 48px 网格：拖动窗口时不必逐像素重算背景模糊（模糊后本就分辨不出）。
        """
        origin = (0, 0)
        try:
            pt = self._window.mapToGlobal(QPoint(0, 0))
            origin = (_quantize(pt.x()), _quantize(pt.y()))
        except Exception:
            pass
        size = QSize(1920, 1080)
        try:
            scr = self._window.screen() or QGuiApplication.primaryScreen()
            if scr is not None:
                size = scr.geometry().size()
        except Exception:
            pass
        return origin, size

    def _background(self) -> GlassBackground:
        if self._bg is None:
            self._bg = GlassBackground(self.params, self._window.size())
        self._bg.set_params(self.params)
        self._bg.resize(self._window.size())
        origin, screen = self._window_view()
        self._bg.set_view(origin, screen)
        return self._bg

    def background_blurred(self) -> Optional[QPixmap]:
        return self._background().blurred()

    def has_wallpaper(self) -> bool:
        return self._background().has_wallpaper()

    def surface_origin(self, widget: QWidget) -> tuple:
        """widget 左上角在**皮肤窗口**坐标系中的位置（供连续采样背景）。"""
        try:
            pt = widget.mapTo(self._window, widget.rect().topLeft())
            return (pt.x(), pt.y())
        except Exception:
            return (0, 0)

    # ---- 玻璃层 ----

    def add_surface(self, host: QWidget, radius: int = RADIUS_GLASS) -> GlassSurface:
        """在 host 上铺一层玻璃。重复调用同一 host 会先移除旧层（幂等）。"""
        for s in list(self._surfaces):
            if s[0] is host:
                self._drop(host)
        self._surfaces.append((host, radius))
        surf = GlassSurface(host, self, radius)
        host._glass_surface = surf        # 防 GC + 便于外部取用
        return surf

    def remove_surface(self, host: QWidget) -> None:
        self._drop(host)

    def _drop(self, host: QWidget) -> None:
        self._surfaces = [s for s in self._surfaces if s[0] is not host]
        old = getattr(host, "_glass_surface", None)
        if old is not None:
            try:
                old.setParent(None)
                old.deleteLater()
            except Exception:
                pass
            try:
                host._glass_surface = None
            except Exception:
                pass

    # ---- 根背景 ----

    def paint_root(self, painter: QPainter, rect) -> None:
        """窗口根背景：模糊背景图（无图则留给 QSS 的主题渐变透出）。"""
        p = self.params
        if not p.enabled:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        bg = self.background_blurred()
        if bg is not None and not bg.isNull():
            painter.drawPixmap(QRect(int(rect.left()), int(rect.top()),
                                     int(rect.width()), int(rect.height())),
                               bg, bg.rect())
        painter.restore()

    # ---- 刷新与相位 ----

    def refresh(self) -> None:
        """参数变化后重算背景、重绘所有玻璃层。"""
        if not self._alive():
            return
        if self._bg is not None:
            self._bg.set_params(self.params)
            self._bg.invalidate()
        for host, _radius in list(self._surfaces):
            surf = getattr(host, "_glass_surface", None)
            if surf is not None:
                try:
                    surf.setGeometry(host.rect())
                    surf.update()
                except Exception:
                    pass
        try:
            self._window.update()
        except Exception:
            pass

    def _alive(self) -> bool:
        """宿主窗口的 C++ 对象是否还在。

        Qt 对象被销毁后，任何属性访问都会抛 RuntimeError；一旦在回调里继续触碰它
        （典型如 `QTimer(self)`），后果是进程级崩溃而非可捕获异常，因此每个入口都要先拦。
        """
        if self._window is None:
            return False
        try:
            self._window.width()
            return True
        except Exception:
            return False

    def _on_params_changed(self) -> None:
        if not self._alive():
            return
        self._sync_timer()
        self.refresh()

    def _sync_timer(self) -> None:
        """液态动效开关变化时启停相位定时器（默认关闭 → 全程零重绘）。"""
        if not self._alive():
            return
        p = self.params
        need = bool(p.enabled and p.liquid_anim)
        if need and self._timer is None:
            self._timer = QTimer(self)
            self._timer.setInterval(_PHASE_INTERVAL_MS)
            self._timer.timeout.connect(self._tick)
            self._timer.start()
        elif not need and self._timer is not None:
            self._timer.stop()
            self._timer.deleteLater()
            self._timer = None
            self.phase = 0.0

    def _tick(self) -> None:
        if not self._alive():
            return
        self.phase = (self.phase + _PHASE_STEP) % 1.0
        for host, _radius in list(self._surfaces):
            surf = getattr(host, "_glass_surface", None)
            if surf is not None:
                try:
                    surf.update()
                except Exception:
                    pass

    def eventFilter(self, obj, ev):
        if obj is self._window and ev.type() == QEvent.Type.Resize:
            if self._bg is not None:
                self._bg.resize(self._window.size())
            self._window.update()
        return False

    def dispose(self) -> None:
        """解除装配（窗口销毁/关闭玻璃时调用）。

        必须同时**取消订阅**：否则窗口没了，参数一变仍会回调到本对象。
        """
        unsubscribe(self._on_params_changed)
        if self._timer is not None:
            try:
                self._timer.stop()
                self._timer.deleteLater()
            except Exception:
                pass
            self._timer = None
        for host, _radius in list(self._surfaces):
            self._drop(host)
        self._surfaces = []
        self._bg = None
        self._window = None


def install(window: QWidget, radius: int = RADIUS_WINDOW) -> GlassSkin:
    """给窗口装配玻璃皮肤（幂等：重复调用返回既有实例）。"""
    skin = getattr(window, "_glass_skin", None)
    if isinstance(skin, GlassSkin):
        return skin
    skin = GlassSkin(window, radius)
    try:
        window._glass_skin = skin      # 防 GC：皮肤必须活到窗口销毁
    except Exception:
        pass
    return skin


def skin_of(window: QWidget) -> Optional[GlassSkin]:
    s = getattr(window, "_glass_skin", None)
    return s if isinstance(s, GlassSkin) else None


# ══════════════════════════ 4) QSS 参数化 ══════════════════════════

def rgba(color, alpha: float) -> str:
    """把颜色按 alpha(0~1) 转成 QSS 可用的 rgba() 字符串。

    玻璃材质的 QSS 落点：控件的半透明底色统一由这里生成，
    保证「透明度 / 磨砂程度」两个滑杆同时作用于控件层与绘制层。
    """
    c = QColor(color)
    a = max(0.0, min(1.0, float(alpha)))
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {a:.3f})"


def legible_fill(color: str, p: Optional[GlassParams] = None,
                 floor: Optional[float] = None,
                 span: Optional[float] = None) -> str:
    """大表面（聊天气泡 / 卡片 / 面板）的半透明玻璃填充：带**可读性地板**。

    与 control_fill 的区别：control_fill 的 alpha = frost×opacity，透明度调低会
    让正文直接压在壁纸上；这一层用 root_veil 同款地板（0.42 + frost×0.34），
    保证气泡在壁纸上依然可读、又能透出壁纸。

    - ``floor`` / ``span``：可读性地板与「随磨砂程度增长」的部分。面板类大表面
      （设置页 / 侧栏面板）用更透的一档 ``PANEL_FILL_FLOOR``，壁纸透出更多；
      聊天气泡沿用默认地板，保证正文可读。
    """
    q = p if p is not None else params()
    if not q.enabled:
        return QColor(color).name()
    fl = _ROOT_VEIL_FLOOR if floor is None else max(0.0, min(1.0, float(floor)))
    sp = _ROOT_VEIL_SPAN if span is None else max(0.0, min(1.0, float(span)))
    a = fl + sp * max(0.0, min(1.0, q.frost))
    return rgba(color, a)


def control_fill(surface_color, p: Optional[GlassParams] = None) -> str:
    """小控件（按钮/输入框/列表项）的玻璃填充色。

    与大面积玻璃层同源：磨砂程度 → alpha、透明度 → 整体缩放。
    玻璃关闭时返回全不透明色，等价于原主题外观。
    """
    q = p if p is not None else params()
    if not q.enabled:
        return QColor(surface_color).name()
    alpha = q.frost * q.opacity
    return rgba(surface_color, alpha)
