"""设置页 / 子面板旧 UI 元素残留探针。

用户反馈：设置页左侧导航项、右侧各子项，以及工作树 / Git / 任务清单 / 代码预览面板
里「仍有原有 UI 元素的残留」。本探针构造真实控件并截图，逐控件打印「自带样式表 /
实际底色」，用于分辨哪些控件还停留在旧实色外观（玻璃外壳下就是一块不透明的旧 UI）。

用法：python scripts/_probe_settings_residual.py
"""
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtWidgets import QApplication, QWidget, QLabel, QPushButton          # noqa: E402
from PyQt6.QtWidgets import QLineEdit, QComboBox, QCheckBox, QPlainTextEdit     # noqa: E402
from PyQt6.QtWidgets import QListWidget, QTreeWidget, QScrollArea               # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "_residual_shot"

_LEAF = (QLabel, QPushButton, QLineEdit, QComboBox, QCheckBox, QPlainTextEdit,
         QListWidget, QTreeWidget)


def _pump(app, seconds):
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.001)


def _shot(widget, name):
    try:
        OUT.mkdir(exist_ok=True)
        widget.grab().save(str(OUT / f"{name}.png"))
    except Exception as e:
        print(f"  截图失败 {name}: {e}")


def _own_qss(w: QWidget) -> str:
    """控件自身样式表里出现的 background 声明（旧实色的来源）"""
    try:
        s = w.styleSheet() or ""
    except Exception:
        return ""
    if "background" not in s:
        return ""
    parts = [seg.strip() for seg in s.split(";") if "background" in seg]
    return " | ".join(parts)[:110]


def _dump_page(page: QWidget, tag: str):
    from PyQt6.QtCore import Qt as _Qt
    if page is None:
        print(f"  [{tag}] 页面为 None")
        return
    print(f"  [{tag}] page={page.width()}x{page.height()} "
          f"minHint={page.minimumSizeHint().width()}")
    kids = [c for c in page.findChildren(QWidget) if isinstance(c, _LEAF)]
    print(f"    子控件数={len(kids)}")
    for c in kids:
        txt = ""
        try:
            if isinstance(c, (QLabel, QPushButton, QCheckBox)):
                txt = c.text().replace("\n", " ")[:22]
            elif isinstance(c, QComboBox):
                txt = c.currentText()[:22]
        except Exception:
            pass
        qss = _own_qss(c)
        flag = "★旧实色" if qss else ""
        print(f"      {type(c).__name__:<13} geo={c.geometry().getRect()} "
              f"min={c.minimumSizeHint().width():<4} txt={txt!r:<26} {flag}{qss}")


def main():
    real = app_identity_root()
    home = Path(tempfile.mkdtemp(prefix="res_"))
    if real.exists():
        shutil.copytree(real, home / real.name,
                        ignore=shutil.ignore_patterns("logs", "*.log", "cache"))
    from zhuzhu_Copilot import app_identity
    app_identity._home = lambda: home
    app_identity._migrated = True
    app = QApplication.instance() or QApplication([])

    from zhuzhu_Copilot.core import app_glass
    from zhuzhu_Copilot.ui import agent_panel as ap
    from zhuzhu_Copilot.ui.agent_panel import _AgentSettingsDialog, _NAV_ITEMS

    # 真实数据目录里玻璃可能被关掉（那就只剩主题实色，看不出残留差异）：
    # 这里强制开启材质 + 保留背景图，复核「有壁纸时的玻璃观感」。
    app_glass.set_fields(persist=False, enabled=True)
    p = app_glass.params()
    print(f"玻璃参数：enabled={p.enabled} bg_image={bool(p.bg_image)} "
          f"frost={p.frost} opacity={p.opacity} 壁纸可解码={app_glass.wallpaper_ok()}")
    print(f"壁纸激活={ap._glass_wallpaper_active()}  "
          f"_gsurface(PANEL)={ap._gsurface(ap.PANEL)}  "
          f"_gfill(PANEL)={ap._gfill(ap.PANEL)}")

    dlg = _AgentSettingsDialog(None)
    dlg.resize(991, 687)
    dlg.show()
    _pump(app, 0.5)
    dlg._ensure_all_pages()
    _pump(app, 0.5)

    print(f"=== 设置页：导航项 {dlg.nav.count()} 个 / 页面 {dlg.stack.count()} 个 ===")
    print(f"对话框 {dlg.width()}x{dlg.height()} 导航宽={dlg.nav.width()} "
          f"滚动区宽={dlg._page_scroll.width()} "
          f"滚动区可视宽={dlg._page_scroll.viewport().width()}")
    _shot(dlg, "settings_full")

    for i, (name, _key) in enumerate(_NAV_ITEMS):
        dlg.nav.setCurrentRow(i)
        _pump(app, 0.25)
        page = dlg.stack.currentWidget()
        _shot(page, f"page_{i:02d}")
        if i not in (0, 13):        # 只详查通用页与外观页，其余仅截图
            continue
        print(f"\n--- 页[{i}] {name} 可见宽={dlg._page_scroll.viewport().width()}")
        _dump_page(page, name)

    print("\n=== 子面板 ===")
    for cls_name in ("WorktreeWindow", "GitLogWindow", "TodosWindow",
                     "CodePreviewWindow"):
        cls = getattr(ap, cls_name, None)
        if cls is None:
            print(f"  {cls_name}: 不存在")
            continue
        try:
            w = cls(None)
            w.resize(w.DEFAULT_SIZE[0], w.DEFAULT_SIZE[1]
                     if hasattr(w, "DEFAULT_SIZE") else 320)
            w.show()
            _pump(app, 0.6)
            print(f"\n[{cls_name}] {w.width()}x{w.height()} qss={_own_qss(w) or '（无）'}")
            kids = [c for c in w.findChildren(QWidget) if isinstance(c, _LEAF)]
            for c in kids[:14]:
                qss = _own_qss(c)
                flag = "★旧实色" if qss else ""
                print(f"    {type(c).__name__:<13} geo={c.geometry().getRect()} "
                      f"{flag}{qss}")
            _shot(w, f"win_{cls_name}")
            w.close()
        except Exception as e:
            import traceback
            print(f"  {cls_name} 构造失败: {e}")
            traceback.print_exc()

    dlg.close()


def app_identity_root():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from zhuzhu_Copilot import app_identity
    try:
        return app_identity.data_root()
    except Exception:
        return Path.home() / ".zhuzhu_Copilot"


if __name__ == "__main__":
    main()
