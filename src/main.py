import sys
import os
import ctypes
import faulthandler
import tempfile as _tempfile
import multiprocessing
import threading
multiprocessing.freeze_support()

# 确保从 src/ 目录运行时能找到同级的 winapp_migrator 包
_src_dir = os.path.dirname(os.path.abspath(__file__))
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

# 确保打包后能找到模块（onedir 与 onefile 兼容）
if getattr(sys, "frozen", False):
    base_dir = os.path.dirname(sys.executable)
    sys.path.insert(0, base_dir)
    internal_dir = os.path.join(base_dir, "_internal")
    if os.path.isdir(internal_dir):
        sys.path.insert(0, internal_dir)
    # PyInstaller windowed 模式 stdout/stderr 为无效句柄，print/flush 抛 OSError。
    # 重定向到 devnull 防止任何遗留 print 调用导致崩溃弹窗。
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

# QtWebEngine 渲染修复：
# 在虚拟机 / 远程桌面 / 无 GPU 环境下，Chromium GPU 进程无法创建 GLES3 上下文
# （gpu_channel_manager.cc: Failed to create shared context for virtualization），
# 导致 QWebEngineView 白屏、loadFinished=False、JS 不执行；加载真实 URL 时
# Chromium sandbox 还会导致 QtWebEngineProcess 崩溃。
# 必须在 QtWebEngine 内核初始化（首次创建 QWebEngineView）之前设置以下环境变量：
#   - QTWEBENGINE_CHROMIUM_FLAGS   禁用 GPU 硬件加速，改用软件渲染
#   - QTWEBENGINE_DISABLE_SANDBOX  禁用 Chromium 沙箱（避免 QtWebEngineProcess 崩溃）
# 用 setdefault 保留用户自定义覆盖。
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--disable-gpu --no-sandbox --disable-gpu-compositing --disable-software-rasterizer --single-process",
)
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

# 预览面板媒体播放：固定使用 Qt ffmpeg 后端（QT_MEDIA_BACKEND=ffmpeg）。
# 避免 windowsmediaplugin(WM）与 ffmpeg 后端并存时出现的
# "QObject::disconnect: wildcard call ... QFFmpeg::*" 析构时序告警，
# 并让 mp4/mkv/avi/mov/webm 等广泛格式统一走 ffmpeg 解复用/解码。
# setdefault 保留用户显式覆盖。
os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")

# 【关键】QtWebEngine 必须在 QApplication 创建【之前】导入（Qt 硬性要求），
# 否则运行中才 import 会抛 ImportError 导致 WebView 降级/打开外部浏览器。
# 此处（QApplication 创建前）提前加载，后续 new_web_view() 直接命中 sys.modules。
try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
except Exception:
    pass

# 崩溃诊断：进程级崩溃（C 层 segfault/abort）时把全部线程 Python 堆栈写入日志，
# 便于抓取 Windows 报错弹窗背后的真实崩溃点（普通 excepthook 抓不到 C 层崩溃）。
try:
    _fh_path = os.path.join(_tempfile.gettempdir(), "WinAppMigrator",
                            f"faulthandler_{os.getpid()}.log")
    os.makedirs(os.path.dirname(_fh_path), exist_ok=True)
    faulthandler.enable(open(_fh_path, "w", encoding="utf-8"))
except Exception:
    pass

from PyQt6.QtWidgets import QApplication, QWidget, QLabel, QVBoxLayout
from PyQt6.QtCore import Qt, QSettings, QTimer, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QFont, QFontDatabase, QIcon, QColor, QPainter
from PyQt6.QtWidgets import QGraphicsOpacityEffect

from winapp_migrator.ui.main_window import _app_icon_path
from winapp_migrator.ui.styles import apply_palette
from winapp_migrator.utils.helpers import (
    install_excepthook, install_thread_excepthook, install_native_crash_hook)


# 启动动画窗口形状：矩形 + 圆角（单一数据源，改这里即整体生效）。
# 尺寸为既有启动窗大小（宽 x 高），只把四角做成圆角 —— 不改变窗口大小。
# 圆角半径与 UI/UX 主题面板圆角一致（agent_ui_ux.apply_rounded_window 默认 18）。
SPLASH_W = 359
SPLASH_H = 200
SPLASH_RADIUS = 18


def _resolve_theme_mode() -> str:
    """启动动画深浅色自适应：与 agent_panel 的主题解析保持一致（dark/light/auto）。"""
    try:
        v = str(QSettings("WinAppMigrator", "WinAppMigrator").value("agent_theme", "light"))
    except Exception:
        v = "light"
    if v == "auto":
        import datetime
        return "light" if 8 <= datetime.datetime.now().hour < 20 else "dark"
    return v if v in ("dark", "light") else "light"


class SplashWindow(QWidget):
    """启动动画窗口：359x200 圆角矩形、居中、无边框，显示 zhuzhu copilot，
    右下角 powered by xiaozhu 灰色小字；深浅色模式自适应。

    形状：尺寸沿用 SPLASH_W x SPLASH_H（不改变窗口大小），四角经
    apply_rounded_window 套圆角，与应用内其它无边框面板走同一套圆角机制
    （Win11 DWM 官方圆角，Win10 降级 SetWindowRgn），观感统一且不引入额外绘制分支。

    动画：整体淡入（极简、无线条图标、无 emoji）。
    主面板就绪后调用 finish_and_close() 淡出并关闭。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        mode = _resolve_theme_mode()
        if mode == "dark":
            self._bg, self._title_c, self._sub_c = \
                "#000000", "#F5F5F5", "#8A8A8A"
        else:
            self._bg, self._title_c, self._sub_c = \
                "#F4F6FA", "#1E293B", "#64748B"
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        # 尺寸沿用既有启动窗大小（不改变窗口大小），仅四角做圆角
        self.setFixedSize(SPLASH_W, SPLASH_H)
        self._center_on_screen()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # 标题：zhuzhu copilot（垂直居中）
        lay.addStretch(1)
        title = QLabel("zhuzhu copilot")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(
            f"color: {self._title_c}; font-size: 26px; font-weight: 700;"
            "font-family: 'Microsoft YaHei UI','Segoe UI'; letter-spacing: 1px;"
            "background: transparent;")
        lay.addWidget(title)

        lay.addStretch(1)

        # 右下角灰色小字
        sub = QLabel("powered by xiaozhu")
        sub.setAlignment(Qt.AlignmentFlag.AlignRight
                         | Qt.AlignmentFlag.AlignBottom)
        sub.setStyleSheet(
            f"color: {self._sub_c}; font-size: 11px; padding: 0 14px 10px 0;"
            "background: transparent;")
        lay.addWidget(sub)

        # 淡入动画
        self._eff = QGraphicsOpacityEffect(self)
        self._eff.setOpacity(0.0)
        self.setGraphicsEffect(self._eff)
        self._fade = QPropertyAnimation(self._eff, b"opacity", self)
        self._fade.setDuration(380)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)

        # 淡出动画（主面板就绪后触发，结束后关闭）
        self._out = QPropertyAnimation(self._eff, b"opacity", self)
        self._out.setDuration(420)
        self._out.setStartValue(1.0)
        self._out.setEndValue(0.0)
        self._out.setEasingCurve(QEasingCurve.Type.InCubic)
        self._out.finished.connect(self.close)

    def _center_on_screen(self):
        try:
            scr = (self.screen() or QApplication.primaryScreen())
            g = scr.availableGeometry()
            self.move(g.left() + (g.width() - self.width()) // 2,
                      g.top() + (g.height() - self.height()) // 2)
        except Exception:
            pass

    def _apply_round(self):
        """给等边窗口套圆角（复用应用统一圆角机制，失败静默不影响启动）。

        窗口未显示时 rect 可能为空、HWND 也可能未创建，setMask/DWM 调用无效，
        故在 showEvent + 延时两档中重复套用（与 _RoundedFloatWindow 的既有做法一致）。
        """
        try:
            from winapp_migrator.core.agent_ui_ux import apply_rounded_window
            apply_rounded_window(self, SPLASH_RADIUS)
        except Exception:
            pass

    def showEvent(self, ev):
        super().showEvent(ev)
        self._apply_round()
        # 首帧/尺寸就绪后各补一次，避免初始方角残留（低帧率与驱动差异兜底）
        QTimer.singleShot(30, self._apply_round)
        QTimer.singleShot(180, self._apply_round)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(self._bg))
        p.end()

    def start(self):
        """显示并播放淡入动画"""
        self.show()
        self._fade.start()

    def finish_and_close(self):
        """主界面就绪：淡出并关闭（重复调用安全）。
        兜底：动画时间轴异常（低帧率/驱动问题）时也会在 700ms 后强制关闭，
        保证启动动画绝不残留挡住主面板。"""
        if self._out.state() == QPropertyAnimation.State.Running:
            return
        try:
            self._fade.stop()
            self._eff.setOpacity(1.0)
        except Exception:
            pass
        self._out.start()
        QTimer.singleShot(700, self._ensure_closed)

    def _ensure_closed(self):
        try:
            if self._out.state() == QPropertyAnimation.State.Running:
                self._out.stop()
            if self.isVisible():
                self.close()
        except Exception:
            pass


def _media_selftest(app, path: str):
    """媒体解码自检：设置环境变量 MEDIA_SELFTEST=<媒体文件> 或命令行参数
    --media-selftest <文件> 启动时解码并播放 3 秒，把 Qt 媒体后端/播放状态
    （mediaStatus/playbackState/错误/播放位置）写入 %USERPROFILE%\\media_selftest.log
    后退出。仅诊断用（安装版解码失败/播放中断时在目标机快速复现），
    正常启动不受影响。"""
    from PyQt6.QtCore import QUrl, QTimer, qInstallMessageHandler, QtMsgType
    from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
    log = os.path.join(os.path.expanduser("~"), "media_selftest.log")
    events = []
    events.append(f"QT_MEDIA_BACKEND={os.environ.get('QT_MEDIA_BACKEND', '')}")
    # 开启 ffmpeg 后端详细日志，便于诊断解码失败的具体原因
    _rules = os.environ.get("QT_LOGGING_RULES", "")
    os.environ["QT_LOGGING_RULES"] = (_rules + ";qt.multimedia.ffmpeg*=true").strip(";")

    def _qt_msg(mode, ctx, msg):
        events.append(f"qt[{mode}]: {msg}")

    qInstallMessageHandler(_qt_msg)
    p = QMediaPlayer()
    ao = QAudioOutput()
    p.setAudioOutput(ao)
    # 视频文件：挂 QVideoWidget 真实渲染（音频文件挂了也不影响，仅不显示）
    try:
        from PyQt6.QtMultimediaWidgets import QVideoWidget
        _vv = QVideoWidget()
        _vv.resize(320, 240)
        _vv.show()
        p.setVideoOutput(_vv)
    except Exception:
        pass
    p.errorOccurred.connect(lambda e, es: events.append(f"error={e}|{es}"))
    p.mediaStatusChanged.connect(lambda s: events.append(f"status={s}"))
    p.playbackStateChanged.connect(lambda s: events.append(f"state={s}"))
    p.positionChanged.connect(lambda pos: events.append(f"pos={pos}"))

    def _finish():
        try:
            with open(log, "w", encoding="utf-8") as f:
                f.write("\n".join(events) + "\n")
        except Exception:
            pass
        # 自检模式：跳过 Qt（含 WebEngine）清理直接退出，保证诊断快速结束
        os._exit(0)

    p.setSource(QUrl.fromLocalFile(path))
    p.play()
    QTimer.singleShot(3000, _finish)
    app.exec()


def _preload_media_dlls():
    """预加载 QtMultimedia ffmpeg 后端依赖的解码库（仅打包版需要）：
    PyInstaller 打包后 Qt 的 QPluginLoader 加载 ffmpegmediaplugin 时，其依赖的
    avcodec/avformat 等 DLL 不在插件的 DLL 搜索路径中 → 插件加载失败 → Qt 回退
    windows(Media Foundation) 后端 → 所有媒体"无法解码"。启动时提前 LoadLibrary
    这些库，插件依赖解析命中已加载模块，ffmpeg 后端即可正常工作。
    源码运行时这些库在 site-packages 下由 find_qt 正确加载，本函数自动空转。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        _base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
        _paths = [os.path.join(_base, "PyQt6", "Qt6", "bin", f)
                  for f in ("avcodec-61.dll", "avformat-61.dll", "avutil-59.dll",
                            "swresample-5.dll", "swscale-8.dll", "opengl32sw.dll")]
        _paths.append(os.path.join(_base, "PyQt6", "Qt6", "plugins", "multimedia",
                                   "ffmpegmediaplugin.dll"))
        for _p in _paths:
            if os.path.isfile(_p):
                ctypes.WinDLL(_p)
    except Exception:
        pass


def _preload_agent_panel_ready():
    """AI 面板空闲预热（后台线程，QApplication 创建后立即启动）：
    1. import agent_panel（1.6 万行单文件 + 顶层主题初始化约 1s）——把「首次点击
       打开面板」时的同步 import 卡顿移到启动后台完成；用户点开时模块已在
       sys.modules，_open_agent_panel 直接命中无阻塞。
    2. 预加载/解密模型配置（PBKDF2 200k 迭代约 0.65s）——进程内只派生一次，
       面板构造时 load_model_config 直接命中解密缓存，不再阻塞打开。
    说明：模块顶层副作用（apply_theme 读 QSettings、QDialog.showEvent 补丁、
    全局色板赋值）不创建任何控件、不依赖 QApplication 主线程，后台线程执行安全；
    与主线程唯一的交互是 sys.modules 写入（Python import 锁保证原子）。
    """
    try:
        import winapp_migrator.ui.agent_panel  # noqa: F401
    except Exception:
        pass
    try:
        from winapp_migrator.core import agent_llm, agent_skills
        agent_skills.load_settings()          # 填充 settings 解密缓存
        agent_llm.load_model_config()         # 填充模型配置解密缓存
    except Exception:
        pass


def _open_agent_panel(app):
    """创建并显示 AI 面板（应用唯一主界面，内部按需懒创建 zhuzhu Copilot 浮层）。

    引用挂在 QApplication 上：面板 parent=None 且无其他持有者，否则可能被 GC 回收。
    """
    from winapp_migrator.ui.agent_panel import AgentPanel
    panel = AgentPanel(None)
    app._agent_panel = panel
    panel.show()
    panel.raise_()
    panel.activateWindow()
    return panel


def main():
    # 新手指南判定取样：必须在任何「首次运行自动生成文件」动作之前记录用户数据目录是否已存在，
    # 否则程序启动自身创建的 ~/.winapp_migrator 会让「全新安装」被误判为「老用户升级」→ 指南不弹。
    try:
        from winapp_migrator.ui.onboarding import capture_startup_state
        capture_startup_state()
    except Exception:
        pass
    install_excepthook()
    install_thread_excepthook()
    install_native_crash_hook()
    _preload_media_dlls()

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    # Windows 任务栏独立图标：不设 AppUserModelID 时任务栏会把应用并入 python.exe 并显示默认图标。
    # 【关键】必须在 QApplication 创建之前设置——Windows 规定该调用须发生在进程创建
    # 任何窗口之前；QApplication 内部会预创建消息窗口，放在其后会导致任务栏图标
    # "时有时无"（竞态取决于窗口创建与 AUMID 注册的先后）。
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "zhuzhu.Copilot.App")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName("zhuzhu Copilot")
    app.setApplicationDisplayName("zhuzhu Copilot")

    # 确保默认 UI/UX 包与主题持久化（仅首次写入，已存在则不覆盖用户选择）
    _qs = QSettings("WinAppMigrator", "WinAppMigrator")
    if not _qs.contains("agent_uiux"):
        _qs.setValue("agent_uiux", "default")
    if not _qs.contains("agent_theme"):
        _qs.setValue("agent_theme", "light")   # 安装后默认浅色

    # Windows 任务栏独立图标（AppUserModelID 已在 QApplication 之前设置，见上方）
    # 应用级图标：主窗口与所有对话框（QMessageBox 等）左上角图标均继承自此
    app.setWindowIcon(QIcon(_app_icon_path()))

    default_families = ["Microsoft YaHei UI", "Segoe UI", "PingFang SC"]
    available_families = QFontDatabase.families()
    family = next((f for f in default_families if f in available_families), "Arial")
    app.setFont(QFont(family, 10))

    apply_palette(app)

    # 媒体解码自检入口（MEDIA_SELFTEST 环境变量或 --media-selftest <file> 参数触发，
    # 仅诊断用）：UAC 提升会丢失环境变量，故同时支持命令行参数方式
    _selftest = os.environ.get("MEDIA_SELFTEST", "")
    if not _selftest:
        _argv = [a for a in sys.argv]
        if "--media-selftest" in _argv:
            _i = _argv.index("--media-selftest")
            if _i + 1 < len(_argv):
                _selftest = _argv[_i + 1]
    if _selftest:
        _media_selftest(app, _selftest)
        return

    # 主窗口已移除：启动即进入 AI 面板（zhuzhu Copilot 浮层由面板顶栏按钮或托盘唤起）。
    # 启动动画：先显示圆角矩形动画窗口（尺寸不变，仅四角圆角），
    # 面板就绪后淡出（界面不再"空白卡顿"）。
    splash = SplashWindow()
    splash.start()

    def _close_splash_then_open_panel():
        # 先关闭启动动画窗口，再弹出 AI 面板（避免面板先出、启动窗还在的重叠感）
        splash.finish_and_close()
        panel = _open_agent_panel(app)
        # 预创建 Copilot 浮层（不显示）：桌宠 / 托盘 / 首次应用扫描随程序启动，保持原有启动行为
        panel.prewarm_copilot_panel()
        # 启动后台线程预热 AI 面板模块与配置解密缓存（节省的部分成本与面板生命周期并行）。
        # 注意：必须在 QApplication 创建后启动（模块顶层 apply_theme 读取 QSettings、
        # 补丁 QDialog.showEvent 均不依赖主线程）。
        threading.Thread(target=_preload_agent_panel_ready,
                         daemon=True, name="agent_panel_preload").start()

    # 启动动画展示 2 秒后立即关闭启动窗口，再弹出 AI 面板。
    QTimer.singleShot(2000, _close_splash_then_open_panel)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
