# -*- coding: utf-8 -*-
"""全局背景图（壁纸）设置：持久化 + 收编 + 变化广播 + 模糊 + 铺满绘制。

职责边界：本模块只回答「用哪张图、怎么铺、糊多少、压多暗」，**不做材质装饰**
（没有磨砂噪声、液态折射、边缘高光）。原 ``core/app_glass`` 的材质能力已移除，
壁纸改为按适配方式铺满 → 可选高斯模糊 → 压一层可调压暗纱保证正文可读。

分层约束：core 层不得反向依赖 ui 层，故本模块不 import 任何 ui.*；
绘制只用 QtGui / QtWidgets 的图元，不认识界面上有哪些控件。
"""

from __future__ import annotations

import json
import shutil
import threading
import weakref
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import QRect, QRectF, QSize, Qt
from PyQt6.QtGui import QBrush, QColor, QGuiApplication, QImage, QPainter, QPixmap

from zhuzhu_Copilot import app_identity

# 背景适配方式（与 CSS background-size 同名，便于理解）
BG_FITS = ("cover", "contain", "stretch", "tile")
_BG_FIT_DEFAULT = "cover"

# 高斯模糊半径（px）：0 = 原图，越大越糊。上限取 40 —— 再大已看不出画面内容，
# 只是白白拖慢绘制（模糊是逐像素的重活）。
BLUR_MIN, BLUR_MAX, BLUR_DEFAULT = 0.0, 40.0, 0.0

# 压暗强度（%）：壁纸之上压主题底色的不透明度，是正文可读性的地板。
# 容器底色在有壁纸时会整体改透明（见 ui/agent_panel 的表面色覆盖），
# 若没有这一层，正文会直接压在照片上而不可读。
DIM_MIN, DIM_MAX, DIM_DEFAULT = 0.0, 100.0, 62.0

# 背景图存放目录名与允许的扩展名（白名单，避免把任意文件当图片读）
_BACKGROUND_DIR_NAME = "backgrounds"
_BACKGROUND_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".webp")
_CONFIG_NAME = "wallpaper.json"

_SCRIM_FALLBACK = "#101216"


# ══════════════════════════ 参数层 ══════════════════════════

@dataclass(frozen=True)
class WallpaperParams:
    """当前壁纸设置（不可变）。

    修改一律走 ``with_fields`` 生成新实例再交给 ``set_params`` 广播，
    避免「改了一半被别的代码读到」的中间态。
    """

    bg_image: str = ""                   # 背景图绝对路径（空 = 用主题渐变）
    bg_fit: str = _BG_FIT_DEFAULT        # cover / contain / stretch / tile
    bg_blur: float = BLUR_DEFAULT        # 高斯模糊半径（px）
    bg_dim: float = DIM_DEFAULT          # 压暗强度（%）

    def clamped(self) -> "WallpaperParams":
        fit = self.bg_fit if self.bg_fit in BG_FITS else _BG_FIT_DEFAULT
        return replace(self,
                       bg_fit=fit,
                       bg_image=str(self.bg_image or ""),
                       bg_blur=_num(self.bg_blur, BLUR_MIN, BLUR_MAX, BLUR_DEFAULT),
                       bg_dim=_num(self.bg_dim, DIM_MIN, DIM_MAX, DIM_DEFAULT))

    def with_fields(self, **kw) -> "WallpaperParams":
        known = {k: v for k, v in kw.items() if hasattr(self, k)}
        if not known:
            return self
        return replace(self, **known).clamped()

    def to_dict(self) -> dict:
        return {"bg_image": self.bg_image, "bg_fit": self.bg_fit,
                "bg_blur": self.bg_blur, "bg_dim": self.bg_dim}

    @classmethod
    def from_dict(cls, data) -> "WallpaperParams":
        if not isinstance(data, dict):
            return cls()
        return cls(bg_image=str(data.get("bg_image", "") or ""),
                   bg_fit=str(data.get("bg_fit", _BG_FIT_DEFAULT) or _BG_FIT_DEFAULT),
                   bg_blur=data.get("bg_blur", BLUR_DEFAULT),
                   bg_dim=data.get("bg_dim", DIM_DEFAULT)).clamped()


def _num(value, low: float, high: float, fallback: float) -> float:
    """夹紧到 [low, high]；非数字（含配置文件被手改成字符串）一律回落默认值。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return fallback
    if v != v:                     # NaN 会绕过 min/max 比较，必须单独挡掉
        return fallback
    return max(low, min(high, v))


# ── 状态：全局单例 + 弱引用订阅（主面板与设置页共享同一份设置） ──

_lock = threading.RLock()
_params: Optional[WallpaperParams] = None
_listeners: list = []
_source_cache: dict = {}


def _config_path() -> Path:
    """wallpaper.json 路径。惰性求值（data_root 会随迁移/测试切换家目录而变化）。"""
    return app_identity.data_root() / "agent" / _CONFIG_NAME


def load() -> WallpaperParams:
    """从磁盘读设置（带进程内缓存）。文件缺失/损坏一律回落默认值。"""
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
        _params = WallpaperParams.from_dict(data)
        return _params


def save(p: WallpaperParams) -> bool:
    """写盘。失败静默返回 False（外观设置丢失不应影响功能）。"""
    try:
        path = _config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(p.to_dict(), ensure_ascii=False, indent=2),
                        encoding="utf-8", newline="\n")
        return True
    except Exception:
        return False


def params() -> WallpaperParams:
    return load()


def set_params(p: WallpaperParams, persist: bool = True) -> WallpaperParams:
    """设置并广播给所有订阅者，返回夹紧后的实际值。"""
    global _params
    with _lock:
        _params = p.clamped()
    if persist:
        save(_params)
    _notify()
    return _params


def set_fields(persist: bool = True, **kw) -> WallpaperParams:
    return set_params(params().with_fields(**kw), persist=persist)


def reset_all(persist: bool = True) -> WallpaperParams:
    """恢复默认（清除背景图与适配方式）。"""
    return set_params(WallpaperParams(), persist=persist)


def _weak_callback(cb: Callable[[], None]):
    """把任意可调用对象包成弱引用，避免订阅者被本模块长期持有而泄漏。

    绑定方法必须走 WeakMethod（普通 weakref.ref 会随临时对象立刻失效）。
    """
    import inspect
    if inspect.ismethod(cb):
        return weakref.WeakMethod(cb)
    try:
        return weakref.ref(cb)
    except TypeError:
        return lambda: cb


def subscribe(cb: Callable[[], None]) -> None:
    """订阅设置变化（弱引用持有，调用方无需手动反订阅）。"""
    if not callable(cb):
        return
    with _lock:
        _listeners.append(_weak_callback(cb))


def unsubscribe(cb: Callable[[], None]) -> None:
    """取消订阅，并顺手清掉已失效的弱引用。

    订阅者若先于本模块销毁（如窗口关闭）仍会被回调到，回调里再碰已销毁的 Qt
    对象是进程级崩溃而不是异常，因此必须提供反订阅入口。
    """
    if not callable(cb):
        return
    with _lock:
        keep = []
        for ref in _listeners:
            fn = ref()
            if fn is None:
                continue
            if fn == cb:
                continue
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


def _clear_caches() -> None:
    """丢弃解码/模糊缓存：换图、清图、切换数据目录都必须调用，
    否则会拿旧图的模糊结果画在新设置上。"""
    _source_cache.clear()
    for cache in (_fit_cache, _blur_cache):
        cache["key"] = None
        cache["value"] = None
        cache["src"] = None


def invalidate() -> None:
    """丢弃缓存，下次 ``params()`` 重新读盘（切换数据目录/测试用）。"""
    global _params
    with _lock:
        _params = None
        _clear_caches()


# ══════════════════════════ 背景图资源 ══════════════════════════

def backgrounds_dir() -> Path:
    return app_identity.data_root() / "agent" / _BACKGROUND_DIR_NAME


def _source_pixmap(path: str) -> Optional[QPixmap]:
    """按 (路径, mtime) 缓存解码结果，避免每次缩放窗口都重新解码大图。

    QPixmap 只能在存在 QGuiApplication 的前提下创建：模块导入期（agent_panel
    顶层 apply_theme → _apply_surface_mode → active() 探测壁纸可用性）尚无应用
    实例，直接创建会触发 Qt 致命错误（进程级崩溃 0xC0000409；本机实测：
    pytest 收集阶段导入 agent_panel 时整进程猝死）。无实例时返回 None（视为
    「暂不可用」），控件构建时的 refresh_surface_mode 会在有实例后重算。
    """
    if QGuiApplication.instance() is None:
        return None
    try:
        mtime = Path(path).stat().st_mtime_ns
    except OSError:
        return None
    key = (path, mtime)
    hit = _source_cache.get(key)
    if hit is not None and not hit.isNull():
        return hit
    # 先用 QImage 试解码再转 QPixmap：坏图/被截断的图直接交给 QPixmap 会在部分
    # 进程状态下终止进程（libpng 报 bad header 后触发 STATUS_STACK_BUFFER_OVERRUN）。
    img = QImage(path)
    if img.isNull():
        return None
    pm = QPixmap.fromImage(img)
    if pm.isNull():
        return None
    _source_cache.clear()
    _source_cache[key] = pm
    return pm


def active() -> bool:
    """当前配置的背景图是否**真的能解码显示**。

    文件存在 ≠ 能显示：Qt 不认的格式/损坏文件若只判存在，会让窗口根底
    转透明却没有壁纸可画 → 界面变成黑块。必须以能加载出位图为准。

    无 QGuiApplication（模块导入早期 / 纯脚本环境）时恒为 False：此时根本
    无法创建 QPixmap（Qt 致命报错会整进程崩溃），也不可能有窗口可绘制。
    """
    path = load().bg_image or ""
    if not path:
        return False
    return _source_pixmap(path) is not None


def import_background(source, apply: bool = True) -> tuple:
    """把一张图片收编进应用数据目录并返回 ``(ok, msg, stored_path)``。

    为什么必须复制而不是直接引用原路径：用户随手选的图可能在临时目录、
    下载目录或被清理的相册里，直接引用会导致「重启后背景丢失」。

    性能要点：4K 图单次解码实测 ~95ms（`_source_pixmap`），而设置背景这条链路上
    「校验源图 → 收编后 active()/绘制」本来会各解一次（共 2~3 次）。这里复用
    `_source_pixmap` 的解码结果：先解一次做校验，收编后**把这张已解码的位图挂到新
    路径的缓存键上**，后续 `active()` 与首帧绘制直接命中缓存 —— 一次设置只解一次。
    """
    src = Path(str(source or "")).expanduser()
    if not src.is_file():
        return (False, f"背景图不存在：{src}", "")
    ext = src.suffix.lower()
    if ext not in _BACKGROUND_EXTS:
        return (False, f"不支持的图片格式 {ext or '(无扩展名)'}，"
                       f"可用：{'、'.join(_BACKGROUND_EXTS)}", "")
    decoded = _source_pixmap(str(src))
    if decoded is None:
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
        _clear_caches()          # 换图必须丢弃旧图的模糊/适配缓存
        # 预热解码缓存：把上面那次解码结果挂到收编后的路径键上（键格式见 _source_pixmap）
        try:
            _source_cache[(str(dest), Path(dest).stat().st_mtime_ns)] = decoded
        except OSError:
            pass
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
    _clear_caches()
    set_fields(bg_image="")
    return (True, "已清除背景图，回到主题渐变")


# ══════════════════════════ 绘制 ══════════════════════════

def paint(painter: QPainter, rect: QRect, scrim: str = _SCRIM_FALLBACK) -> bool:
    """把当前壁纸按适配方式铺满 ``rect``：先按设置做高斯模糊，再压一层纯色纱。

    返回 False 表示「没有可用壁纸」，调用方应回退到主题渐变/系统绘制。
    """
    p = load()
    pm = _source_pixmap(p.bg_image) if p.bg_image else None
    if pm is None or pm.isNull():
        return False
    fit, blur = p.bg_fit, p.bg_blur
    size = QSize(max(1, rect.width()), max(1, rect.height()))
    try:
        painter.save()
        painter.setClipRect(rect)
        # 先按适配方式摆进「与绘制区同尺寸」的画布再模糊 —— 顺序很关键，见 _fitted
        painter.drawPixmap(rect, _blurred(_fitted(pm, size, fit, scrim), blur))
        painter.fillRect(rect, _scrim_color(scrim, p.bg_dim))
    except Exception:
        return False
    finally:
        try:
            painter.restore()
        except Exception:
            pass
    return True


# ══════════════════════════ 摆版 + 高斯模糊 ══════════════════════════

# 两张缓存都只留最近一份：摆版与模糊都是逐像素重活，窗口每帧重算必然掉帧，
# 而同一时刻只需要「当前这张图 + 当前尺寸 + 当前适配/半径」这一份结果。
# 两份缓存都额外持有源图引用：QPixmap 被销毁后 cacheKey 会被复用，
# 只存 key 不存源，会拿上一张图的缓存画在这一张上。
_fit_cache: dict = {"key": None, "value": None, "src": None}
_blur_cache: dict = {"key": None, "value": None, "src": None}


def _fitted(src: QPixmap, size: QSize, fit: str, backdrop: str) -> QPixmap:
    """按适配方式把图摆进 ``size`` 的画布，返回与绘制区同尺寸的**不透明**位图（含缓存）。

    **先摆版、后模糊**（由 paint 保证）而不是反过来：模糊会让边缘像素与画布外的
    透明相混而发暗，只有让可见区正好等于整张画布、把这段过渡带推到画布之外，
    四边才不会露出一条发暗的边（cover 模式下尤其明显）。

    画布底填 ``backdrop``（主题底色）而不是透明：contain 的留白区若保持透明，
    模糊同样会把透明混进图里，整张壁纸都会发暗并与下层混色。
    """
    key = (src.cacheKey(), size.width(), size.height(), fit, backdrop)
    cached = _fit_cache["value"]
    if _fit_cache["key"] == key and cached is not None and _fit_cache["src"] is src:
        return cached
    base = QPixmap(size)
    if base.isNull():
        return src
    _c = QColor(backdrop)
    base.fill(_c if _c.isValid() else QColor(_SCRIM_FALLBACK))
    painter = QPainter(base)
    try:
        if fit == "tile":
            painter.fillRect(base.rect(), QBrush(src))
        elif fit == "stretch":
            painter.drawPixmap(base.rect(), src)
        else:
            mode = (Qt.AspectRatioMode.KeepAspectRatio if fit == "contain"
                    else Qt.AspectRatioMode.KeepAspectRatioByExpanding)
            scaled = src.scaled(size, mode, Qt.TransformationMode.SmoothTransformation)
            painter.drawPixmap((size.width() - scaled.width()) // 2,
                               (size.height() - scaled.height()) // 2, scaled)
    finally:
        painter.end()
    _fit_cache.update(key=key, value=base, src=src)
    return base


def _blur_margin(radius: float) -> int:
    """模糊过渡带的宽度：垫在可见区四周，裁掉后可见区四边亮度才正常。

    取半径的 2 倍 —— 实测半径 16 时影响范围接近 32px，只留 1 倍会残留一圈暗边。
    """
    return int(round(max(0.0, float(radius)))) * 2 + 2


def _padded(src: QPixmap, margin: int) -> QPixmap:
    """把原图摆进四周各留 ``margin`` 的画布，留白由**最外一圈像素拉伸**填满。"""
    canvas = QPixmap(src.width() + margin * 2, src.height() + margin * 2)
    if canvas.isNull():
        return canvas
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    try:
        painter.drawPixmap(margin, margin, src)
        if margin > 0:
            sw, sh = src.width(), src.height()
            painter.drawPixmap(QRect(0, 0, canvas.width(), margin),
                               src, QRect(0, 0, sw, 1))                       # 上
            painter.drawPixmap(QRect(0, margin + sh, canvas.width(), margin),
                               src, QRect(0, sh - 1, sw, 1))                  # 下
            painter.drawPixmap(QRect(0, 0, margin, canvas.height()),
                               src, QRect(0, 0, 1, sh))                       # 左
            painter.drawPixmap(QRect(margin + sw, 0, margin, canvas.height()),
                               src, QRect(sw - 1, 0, 1, sh))                  # 右
    finally:
        painter.end()
    return canvas


def _blurred(src: QPixmap, radius: float) -> QPixmap:
    """对位图做高斯模糊，返回与 ``src`` 同尺寸的位图（半径 0 直接原样返回）。"""
    if radius <= 0 or src.isNull():
        return src
    # cacheKey 唯一标识底层像素数据：同一张图被重复请求时不必重算
    key = (src.cacheKey(), src.width(), src.height(), round(float(radius), 2))
    if _blur_cache["key"] == key and _blur_cache["value"] is not None \
            and _blur_cache["src"] is src:
        return _blur_cache["value"]
    out = _gaussian_blur(src, radius)
    _blur_cache.update(key=key, value=out, src=src)
    return out


def _gaussian_blur(src: QPixmap, radius: float) -> QPixmap:
    """真实高斯模糊：借 Qt 自带的模糊特效走场景渲染，不手写卷积。

    画布四周先垫一圈「由边缘像素延伸出来」的边（见 ``_padded``），渲染完再把这圈裁掉。
    这圈边不能留透明：模糊核会取到画布外的像素，留透明就会把透明混进可见区 ——
    实测半径 16 时可见区中心 alpha 被压到 243、四角只剩 68，壁纸整体发暗并与下层混色。
    任何异常都返回原图：背景糊不出来只是不好看，绝不能让窗口画不出来。

    **必须在原尺寸上按原半径模糊**：曾在模糊前把画布降采样到短边 512、模糊后再用
    Fast 放大回原尺寸（想省「像素数 × 半径」的成本）。降采样把半径也等比缩小了，
    等效模糊半径被压掉一大截 —— 实测同一半径下画面明显更「糊而脏」（边缘发虚、
    细节被抹成块状），用户反馈质感太差。降采样省下的耗时换来的是可见的画质损失，
    不值得，故回退为原尺寸直糊：半径语义与观感跟滑块刻度严格一致。
    """
    from PyQt6.QtWidgets import QGraphicsBlurEffect, QGraphicsScene
    margin = _blur_margin(radius)
    try:
        canvas = _padded(src, margin)
        if canvas.isNull():
            return src
        scene = QGraphicsScene()
        item = scene.addPixmap(canvas)
        eff = QGraphicsBlurEffect()
        eff.setBlurRadius(float(radius))
        item.setGraphicsEffect(eff)
        out = QPixmap(canvas.size())
        out.fill(Qt.GlobalColor.transparent)
        painter = QPainter(out)
        # 源/目标矩形都取画布坐标系下的同一矩形 → 1:1 渲染，不发生缩放
        scene.render(painter, QRectF(out.rect()), QRectF(canvas.rect()))
        painter.end()
        result = out.copy(margin, margin, src.width(), src.height())
        return result if not result.isNull() else src
    except Exception:
        return src


def _scrim_color(color: str, dim: float = DIM_DEFAULT) -> QColor:
    """压暗纱：主题底色 + 用户设置的强度（百分比 → alpha）。

    解析失败回退深色兜底色（不引入新色相）；dim 走 ``_num`` 夹紧，
    保证 0 时完全透出壁纸、100 时 Wallpaper 不可见只剩主题底。
    """
    c = QColor(color)
    if not c.isValid():
        c = QColor(_SCRIM_FALLBACK)
    c.setAlphaF(_num(dim, DIM_MIN, DIM_MAX, DIM_DEFAULT) / 100.0)
    return c
