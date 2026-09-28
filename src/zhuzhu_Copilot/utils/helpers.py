import os
import sys
import logging
from pathlib import Path
from datetime import datetime

from zhuzhu_Copilot import app_identity

def setup_logging() -> logging.Logger:
    log_dir = app_identity.temp_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"migrator_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    logger = logging.getLogger(app_identity.APP_SLUG)
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
        # windowed 模式下 stdout 可能为 None，仅作兜底，不阻塞
        if sys.stdout is not None:
            sh = logging.StreamHandler(sys.stdout)
            sh.setFormatter(fmt)
            logger.addHandler(sh)
    return logger

_CRASH_DIR = app_identity.temp_dir() / "crashes"


def set_native_window_icon(widget, ico_path: str) -> None:
    """Windows 下强制设置原生窗口大/小图标（WM_SETICON）。

    Qt 的 setWindowIcon 在部分 Win10/11 环境下仅设置 16x16 小图标，任务栏
    （读取 32x32 大图标）会回退到进程默认图标，导致安装后任务栏显示默认图标
    而非专属图标。此处直接 LoadImage 从 .ico 读取并 SendMessage 设置大/小图标，
    与 Qt 的图标设置互补，确保任务栏/Alt-Tab/标题栏均显示专属图标。非 Windows
    或图标文件缺失时静默跳过，不影响其它平台与源码运行。

    GDI 句柄生命周期：每次调用都通过 LoadImageW 新建 HICON，必须在下一轮替换
    时显式 DestroyIcon 旧句柄，否则主窗口反复 hide/show（托盘还原、隐藏主窗口
    模式等）会持续泄漏 GDI 句柄，进程长期运行后图标渲染资源耗尽。句柄跟踪挂
    在 widget 实例上，单例 MainWindow 场景下窗口关闭后由 OS 回收，无副作用。
    """
    if sys.platform != "win32" or not ico_path or not os.path.isfile(ico_path):
        return
    try:
        import ctypes
        hwnd = int(widget.winId())
        user32 = ctypes.windll.user32
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x10
        WM_SETICON = 0x80
        ICON_SMALL = 0
        ICON_BIG = 1
        # 大图标（任务栏 32x32） / 小图标（标题栏 / Alt-Tab 16x16）
        # ico 已含全尺寸帧（16/24/32/48/64/128/256），LoadImageW 命中精确位图
        hbig = user32.LoadImageW(None, ico_path, IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
        hsmall = user32.LoadImageW(None, ico_path, IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
        if hbig:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hbig)
        if hsmall:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hsmall)
        # 销毁上一轮已交给窗口的图标句柄（避免 GDI 泄漏）
        prev_big = getattr(widget, "_native_icon_big", None)
        prev_small = getattr(widget, "_native_icon_small", None)
        for prev in (prev_big, prev_small):
            if not prev:
                continue
            if prev == hbig or prev == hsmall:
                continue  # 已被本轮复用,不销毁
            try:
                user32.DestroyIcon(prev)
            except Exception:
                pass
        # 记录本轮新句柄供下次调用回收
        if hbig:
            widget._native_icon_big = hbig
        if hsmall:
            widget._native_icon_small = hsmall
    except Exception:
        pass

# 良性（可恢复）异常特征：Qt 事件回调内部的异常会被 PyQt 捕获并继续运行，
# 程序并未真正崩溃，不应弹出崩溃弹窗。命中即仅记日志、跳过弹窗与崩溃文件。
# 需要时可在此扩展新增的良性场景（识别的错误信息片段）。
_BENIGN_ERROR_HINTS = frozenset([
    "object has no attribute '_last_scale'",
    "object has no attribute '_btn_icon_sz'",
])


def _is_benign_error(msg: str) -> bool:
    """判断某未捕获异常是否为可恢复的良性异常（不弹崩溃弹窗）。"""
    return any(h in msg for h in _BENIGN_ERROR_HINTS)


def _write_crash_log(msg: str) -> Path | None:
    """把崩溃详情写入本地文件，返回文件路径（失败返回 None）"""
    try:
        _CRASH_DIR.mkdir(parents=True, exist_ok=True)
        path = _CRASH_DIR / f"crash_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"崩溃时间: {stamp}\n{'-' * 40}\n{msg}")
        return path
    except Exception:
        return None


def _show_crash_dialog(msg: str, path: Path | None):
    """弹出崩溃日志弹窗（简约深色风）：展示崩溃时间与详情，支持一键复制。
    崩溃修复后不可再自身崩溃：内部全部兜底 try。"""
    try:
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import (
            QApplication, QDialog, QVBoxLayout, QHBoxLayout,
            QLabel, QPlainTextEdit, QPushButton)
        app = QApplication.instance()
        if app is None:
            return
        dlg = QDialog()
        dlg.setWindowTitle("程序崩溃")
        dlg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        dlg.setStyleSheet(
            "QDialog { background-color: #16181d; }"
            "QLabel { color: #e6e6e6; font-size: 13px; }"
            "QLabel#crash_time { color: #9aa0aa; font-size: 12px; }"
            "QLabel#crash_path { color: #5b7fe0; font-size: 11px; }"
            "QPushButton { background-color: #1f2430; color: #ffffff; border: 1px solid #333a48;"
            "              border-radius: 8px; padding: 8px 18px; font-size: 13px; font-weight: 600; }"
            "QPushButton:hover { background-color: #2a3140; border-color: #3f6fdc; }"
            "QPushButton#primary { background-color: #1f4fbf; border: none; }"
            "QPushButton#primary:hover { background-color: #2b63e0; }"
            "QPlainTextEdit { background-color: #0f1115; color: #d8dce4; border: 1px solid #333a48;"
            "                 border-radius: 8px; font-family: Consolas, 'Microsoft YaHei UI';"
            "                 font-size: 12px; }")
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(12)
        title = QLabel("程序发生异常已崩溃")
        title.setStyleSheet("font-size: 16px; font-weight: 700; color: #3f6fdc;")
        lay.addWidget(title)
        time_lbl = QLabel(f"崩溃时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        time_lbl.setObjectName("crash_time")
        lay.addWidget(time_lbl)
        # 复用全局对象名 crash_path，展示本地保存路径（无则提示）
        page_lbl = QLabel("崩溃日志详情（下方为完整堆栈）：")
        page_lbl.setStyleSheet("color: #9aa0aa; font-size: 12px; font-weight: 600;")
        lay.addWidget(page_lbl)
        detail = QPlainTextEdit()
        detail.setReadOnly(True)
        detail.setPlainText(msg)
        detail.setMinimumSize(560, 300)
        lay.addWidget(detail)
        if path is not None:
            path_lbl = QLabel(f"已保存至本地: {path}")
            path_lbl.setObjectName("crash_path")
            path_lbl.setWordWrap(True)
            lay.addWidget(path_lbl)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        copy_btn = QPushButton("一键复制崩溃日志")
        copy_btn.setObjectName("primary")
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn.clicked.connect(lambda: _copy_and_feedback(copy_btn, detail.toPlainText()))
        btn_row.addWidget(copy_btn)
        close_btn = QPushButton("关闭")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)
        dlg.resize(640, 460)
        dlg.exec()
    except Exception:
        try:
            import traceback
            traceback.print_exc()
        except Exception:
            pass


def _copy_and_feedback(btn, text: str):
    try:
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(text)
        btn.setText("已复制 ✓")
    except Exception:
        btn.setText("复制失败")


def install_excepthook():
    """将未捕获异常写入日志 + 写入本地崩溃文件 + 弹出崩溃详情弹窗（含一键复制）。"""
    import traceback
    logger = setup_logging()
    _in_hook = [False]   # 防递归：弹窗自身崩溃时不再触发本 hook

    def hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        if _in_hook[0]:
            return
        _in_hook[0] = True
        try:
            msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
            # 良性可恢复异常（Qt 回调内经 PyQt 捕获、程序仍运行）：仅记日志，不弹崩溃弹窗
            if _is_benign_error(msg):
                logger.warning("已恢复的良性异常（不视为崩溃）:\n%s", msg)
                return
            logger.critical("未捕获异常:\n%s", msg)
            path = _write_crash_log(msg)
            _show_crash_dialog(msg, path)
        finally:
            _in_hook[0] = False
        try:
            sys.__excepthook__(exc_type, exc_value, exc_tb)
        except Exception:
            pass

    sys.excepthook = hook


def install_thread_excepthook():
    """捕获后台线程（引擎/子 Agent/轮询等）的未捕获 Python 异常并写日志。
    后台线程异常默认只打印到 stderr（窗口化/源码运行常无输出），且可能破坏
    共享状态；统一记入崩溃文件便于排查。"""
    import threading
    import traceback as _tb

    def hook(args):
        try:
            msg = "".join(_tb.format_exception(args.exc_type, args.exc_value, args.exc_tb))
            logger = setup_logging()
            logger.error("后台线程未捕获异常:\n%s", msg)
            _write_crash_log(msg)
        except Exception:
            pass

    try:
        threading.excepthook = hook
    except Exception:
        pass


def install_native_crash_hook() -> bool:
    """注册 Windows 原生异常过滤器：捕获 C 层崩溃（如 access violation 0xC0000005）。

    faulthandler/excepthook 只对 Python 层有效；原生层段错误（崩溃线程无
    Python 帧）时进程直接消失、无任何输出。此过滤器在崩溃瞬间记录异常码 +
    全部 Python 线程栈（可看出主线程/引擎线程正在执行哪个功能），写入
    crashes/native_*.log，供下次定位具体崩溃模块。注册成功返回 True。"""
    try:
        import ctypes
        import sys as _sys
        import traceback as _tb
        import threading as _th
        from datetime import datetime as _dt

        _LPEXCEPTION_POINTERS = ctypes.POINTER(ctypes.c_void_p)
        _LPTOP = ctypes.WINFUNCTYPE(ctypes.c_long, _LPEXCEPTION_POINTERS)
        _lock = _th.Lock()

        def _dump(msg: str):
            with _lock:
                try:
                    _CRASH_DIR.mkdir(parents=True, exist_ok=True)
                    path = _CRASH_DIR / f"native_{_dt.now().strftime('%Y%m%d_%H%M%S')}.log"
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(f"原生崩溃时间: {_dt.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                        f.write(f"{'-' * 40}\n{msg}\n")
                        f.write(f"{'-' * 40}\n全部线程 Python 栈:\n")
                        for tid, frame in _sys._current_frames().items():
                            f.write(f"\n--- thread {tid} ---\n")
                            f.write("".join(_tb.format_stack(frame)))
                except Exception:
                    pass

        @_LPTOP
        def _handler(exc_info):
            code = 0
            try:
                # exc_info[0] = PEXCEPTION_RECORD，首字段 ExceptionCode（ULONG）
                rec = ctypes.cast(exc_info, ctypes.POINTER(ctypes.c_ulong))
                code = int(rec[0]) if rec else 0
            except Exception:
                pass
            _dump(f"Windows fatal exception: 0x{code & 0xFFFFFFFF:08X}"
                  + (" (access violation)" if (code & 0xFFFFFFFF) == 0xC0000005 else ""))
            # EXCEPTION_EXECUTE_HANDLER：交由系统终止进程（不尝试恢复崩溃现场）
            return 1

        _keep = _handler   # 保持引用防 GC（ctypes 回调被回收会导致崩溃钩子失效）
        ctypes.windll.kernel32.SetUnhandledExceptionFilter(_handler)
        return True
    except Exception:
        return False


def is_admin() -> bool:
    try:
        return os.getuid() == 0
    except AttributeError:
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin() != 0

def ensure_admin():
    if not is_admin():
        raise PermissionError("本工具需要管理员权限才能迁移应用。请右键以管理员身份运行。")

def format_size(size_bytes: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"

def get_directory_size(path: Path, max_depth: int = 3) -> int:
    """统计目录大小，限制递归深度以加快扫描速度；跳过目录联接防循环"""
    total = 0

    def walk(p: Path, depth: int):
        nonlocal total
        if depth > max_depth:
            return
        try:
            for entry in os.scandir(p):
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if _is_junction(Path(entry.path)):
                            continue
                        walk(Path(entry.path), depth + 1)
                    else:
                        total += entry.stat(follow_symlinks=False).st_size
                except (OSError, PermissionError):
                    continue
        except (OSError, PermissionError):
            pass

    walk(path, 0)
    return total

def safe_remove(path: Path) -> bool:
    """安全删除文件/目录：处理只读属性与瞬时占用，失败自动重试"""
    import shutil
    import stat as stat_mod
    import time

    def _force(func, p, exc_info):
        # 删除失败（常见：文件/目录只读）时清除只读属性后重试一次
        try:
            os.chmod(p, stat_mod.S_IWRITE | stat_mod.S_IREAD)
            func(p)
        except Exception:
            pass

    for _ in range(4):
        try:
            if path.is_symlink() or _is_junction(path):
                path.unlink()
                return True
            if path.is_dir():
                shutil.rmtree(path, onerror=_force)
                if not path.exists():
                    return True
            elif path.exists():
                path.unlink()
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False

def _is_junction(path: Path) -> bool:
    if not path.exists():
        return False
    import ctypes
    FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
    attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
    return attrs != -1 and (attrs & FILE_ATTRIBUTE_REPARSE_POINT) != 0
