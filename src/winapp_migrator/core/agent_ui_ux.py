"""UI/UX 包管理：热插拔自定义面板 UI

每个包是一个目录（~/.winapp_migrator/ui_ux/<name>/），包含：
  - ui_ux.json    包元数据（name, description, is_builtin, version）
  - build_ui.py   构建函数 def build_ui(self): 替换 AgentPanel._build_ui()
  - build_welcome.py  可选，def build_welcome(self): 替换 _build_welcome()
  - styles.py     可选，自定义主题色常量（如 BG, TEXT, ACCENT 等）

内置默认包 'default' 不可删除，启动时自动生成。
"""

import ast
import builtins
import copy
import json
import logging
import os
import re
import shutil
import sys
import threading
import types
from pathlib import Path

# QtWebEngine 渲染兜底：与 src/main.py 相同的 Chromium 配置，
# 防止不经 main.py 的入口（如测试脚本）直接创建 QWebEngineView 时在无 GPU
# 环境下白屏/加载失败或 QtWebEngineProcess 崩溃。
# 必须在 QtWebEngine 内核初始化前设置，setdefault 不覆盖用户配置。
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--disable-gpu --no-sandbox --disable-gpu-compositing --disable-software-rasterizer",
)
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

# 【关键】QtWebEngine 必须在 QCoreApplication 创建【之前】导入，否则运行时才
# try-import 会抛 ImportError（"QtWebEngineWidgets must be imported ... before a
# QCoreApplication instance is created"），导致 web_engine_available() 误判 False、
# new_web_view() 返回 None，Web 预览降级为静态提示/外部浏览器。
# 本模块在 main.py 中于 QApplication 创建前被链式导入（main.py 顶部 import
# MainWindow → agent_panel → agent_ui_ux），此处模块级提前加载即可让后续
# web_engine_available()/new_web_view() 直接命中 sys.modules。
try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
except Exception:
    pass

try:
    from PyQt6.QtCore import QObject as _QObject
except Exception:
    _QObject = object   # 无 PyQt 环境（如纯测试）时退化为普通基类

try:
    from PyQt6.QtWidgets import QWidget as _QWidget
except Exception:
    _QWidget = _QObject   # 无 PyQt 环境（如纯测试）时退化为普通基类

_UI_UX_DIR = Path.home() / ".winapp_migrator" / "ui_ux"
_ACTIVE_FILE = _UI_UX_DIR / "active.txt"
_LOCK = threading.Lock()

# 最近一次 build_ui 加载失败的异常信息（供 UI 展示真实原因）
_LAST_UIUX_ERROR = ""
# build_ui 必须赋值的 self 属性（契约要求，缺一即回退默认）
_UIUX_REQUIRED_ATTRS = (
    "_root_lay", "title", "session_combo", "new_btn", "settings_btn",
    "token_label", "wf_label", "msg_area", "msg_lay", "_welcome_page",
    "msg_stack", "cmd_list", "input", "attach_btn", "optimize_btn",
    "model_combo", "action_btn", "todos_win", "git_win", "wt_win", "code_win",
)


def _set_last_error(msg: str) -> None:
    """记录最近一次 build_ui 执行失败原因（线程安全）"""
    global _LAST_UIUX_ERROR
    with _LOCK:
        _LAST_UIUX_ERROR = str(msg or "")


def get_last_build_error() -> str:
    """获取最近一次 build_ui 加载失败的原因（无失败返回空串）"""
    with _LOCK:
        return _LAST_UIUX_ERROR

# 默认包的名称（内置不可删除）
_DEFAULT_PACKAGE = "default"
DEFAULT_PACKAGE = _DEFAULT_PACKAGE   # 公共别名

# ── 内置默认包代码 ──────────────────────────────────────────────
_DEFAULT_BUILD_UI_CODE = """\
def build_ui(self):
    root = QVBoxLayout(self)
    root.setContentsMargins(16, 14, 16, 14)
    root.setSpacing(10)
    self._root_lay = root

    # 顶栏
    top = QHBoxLayout()
    top.setSpacing(8)
    self.title = QLabel("AI AGENT")
    self.title.setStyleSheet(f"color: {ACCENT}; font-size: 16px; font-weight: 800;")
    top.addWidget(self.title)

    self.session_combo = QComboBox()
    self.session_combo.setMinimumWidth(150)
    self.session_combo.setMaximumWidth(200)
    self.session_combo.setItemDelegate(_SessionStatusDelegate(self))
    self.session_combo.currentIndexChanged.connect(self._on_session_selected)
    self.session_combo.view().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    self.session_combo.view().customContextMenuRequested.connect(self._on_session_context_menu)
    top.addWidget(self.session_combo)

    self.new_btn = QPushButton(_line_icon("new"), "")
    self.new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    self.new_btn.setAutoDefault(False)
    self.new_btn.setFixedSize(34, 34)
    self.new_btn.setIconSize(QSize(18, 18))
    self.new_btn.setToolTip("新对话")
    self.new_btn.setStyleSheet(_BTN_ICON)
    self.new_btn.clicked.connect(self._new_session)
    top.addWidget(self.new_btn)

    self.settings_btn = QPushButton(_svg_icon(_GEAR_SVG, 20, TEXT_DIM), "")
    self.settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    self.settings_btn.setAutoDefault(False)
    self.settings_btn.setFixedSize(34, 34)
    self.settings_btn.setIconSize(QSize(18, 18))
    self.settings_btn.setToolTip("AI 设置：执行模式 / 工作目录 / 工作力度 / 规则 / 提示词 / 模型接入")
    self.settings_btn.setStyleSheet(_BTN_ICON)
    self.settings_btn.clicked.connect(self._open_settings)
    top.addWidget(self.settings_btn)

    top.addStretch(1)

    self.token_label = QLabel("0 tk")
    self.token_label.setStyleSheet(f"color: {TEXT_DIM}; font-size: 12px;")
    self.token_label.setToolTip("已用 tokens")
    top.addWidget(self.token_label)

    self.wf_label = QLabel("")
    self.wf_label.setStyleSheet(f"color: {TEXT_DIM}; font-size: 12px;")
    self.wf_label.setToolTip("当前对话使用的工作流")
    top.addWidget(self.wf_label)

    clear_btn = QPushButton(_line_icon("trash"), "")
    clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    clear_btn.setAutoDefault(False)
    clear_btn.setFixedSize(34, 34)
    clear_btn.setIconSize(QSize(18, 18))
    clear_btn.setToolTip("清空上下文并永久删除该对话")
    clear_btn.setAutoDefault(False)
    clear_btn.setStyleSheet(_BTN_GHOST)
    clear_btn.clicked.connect(self._clear_chat)
    top.addWidget(clear_btn)

    root.addLayout(top)

    # 主体
    body = QVBoxLayout()
    body.setSpacing(10)
    self.todos_win = TodosWindow(self)
    self.todos_win.clear_requested.connect(self._on_todos_clear)
    self.git_win = GitLogWindow(self)
    self.wt_win = WorktreeWindow(self)
    self.wt_win.file_open_requested.connect(self._open_code_preview)
    self.code_win = CodePreviewWindow(self)
    right = QVBoxLayout()
    right.setSpacing(10)

    self.msg_area = QScrollArea()
    self.msg_area.setWidgetResizable(True)
    self.msg_area.setStyleSheet(
        "QScrollArea { background: transparent; border: none; }"
        + _scrollbar_css(8, 4, both=True))
    container = QWidget()
    container.setStyleSheet("background: transparent;")
    self.msg_lay = QVBoxLayout(container)
    self.msg_lay.setContentsMargins(6, 6, 6, 6)
    self.msg_lay.setSpacing(10)
    self.msg_lay.addStretch(1)
    self.msg_area.setWidget(container)

    self._welcome_page = self._build_welcome()
    self.msg_stack = QStackedWidget()
    self.msg_stack.addWidget(self.msg_area)
    self.msg_stack.addWidget(self._welcome_page)
    right.addWidget(self.msg_stack, 1)

    self.cmd_list = QListWidget()
    self.cmd_list.setStyleSheet(self._cmd_list_qss())
    self.cmd_list.hide()
    self.cmd_list.itemClicked.connect(self._on_cmd_selected)
    right.addWidget(self.cmd_list)

    self._attach_bar = QWidget()
    self._attach_bar.setStyleSheet("background: transparent;")
    self._attach_lay = FlowLayout(self._attach_bar, margin=0, spacing=8)
    self._attach_bar.setVisible(False)
    right.addWidget(self._attach_bar)

    self.queue_panel = QueuePanel(self)
    self.queue_panel.hide()
    self.queue_panel.edit_clicked.connect(self._on_queue_edit)
    self.queue_panel.delete_clicked.connect(self._on_queue_delete)
    self.queue_panel.clear_clicked.connect(self._on_queue_clear)

    # 输入栏
    bottom = QHBoxLayout()
    bottom.setSpacing(6)
    self.input = _DropTextEdit()
    self.input.setPlaceholderText("描述任务，例如：帮我打开百度搜索天气（输入 / 查看命令）")
    self.input.setMinimumHeight(32)
    self.input.setMaximumHeight(110)
    self.input.setStyleSheet(
        f"QPlainTextEdit {{ background: {PANEL}; color: {TEXT}; border: 1px solid {BORDER};"
        "border-radius: 10px; padding: 5px 10px; font-size: 14px; }}"
        f"QPlainTextEdit:focus {{ border: 1px solid {ACCENT}; }}")
    self.input.submit.connect(self._send)
    self.input.textChanged.connect(self._update_cmd_suggestions)
    self.input.textChanged.connect(self._sync_action_style)
    self.input.textChanged.connect(self._on_input_cancel_edit)
    self.input.installEventFilter(self)
    self.input.fileDropped.connect(self._on_input_files_dropped)
    bottom.addWidget(self.input, 1)

    self.attach_btn = QPushButton(_line_icon("plus", 20, ACCENT), "")
    self.attach_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    self.attach_btn.setAutoDefault(False)
    self.attach_btn.setFixedSize(42, 42)
    self.attach_btn.setIconSize(QSize(20, 20))
    self.attach_btn.setToolTip("上传文件/图片给 AI")
    self.attach_btn.setStyleSheet(
        f"QPushButton {{ background: {PANEL}; border: 1px solid {BORDER};"
        f"border-radius: 21px; }}"
        f"QPushButton:hover {{ border: 1px solid {ACCENT}; }}")
    self.attach_btn.clicked.connect(self._pick_attachments)
    bottom.addWidget(self.attach_btn)

    self.optimize_btn = QPushButton(_line_icon("magic", 20, ACCENT), "")
    self.optimize_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    self.optimize_btn.setAutoDefault(False)
    self.optimize_btn.setFixedSize(42, 42)
    self.optimize_btn.setIconSize(QSize(20, 20))
    self.optimize_btn.setToolTip("优化提示词")
    self.optimize_btn.setStyleSheet(
        f"QPushButton {{ background: {PANEL}; border: 1px solid {BORDER};"
        f"border-radius: 21px; }}"
        f"QPushButton:hover {{ border: 1px solid {ACCENT}; }}"
        f"QPushButton:disabled {{ border: 1px solid {BORDER}; opacity: 0.5; }}")
    self.optimize_btn.clicked.connect(self._on_optimize_clicked)
    bottom.addWidget(self.optimize_btn)

    self.model_combo = _ArrowComboBox()
    self.model_combo.setMinimumWidth(150)
    self.model_combo.setMaximumWidth(230)
    self.model_combo.setStyleSheet(_QCOMBO)
    self.model_combo.setToolTip("手动切换本次使用的模型")
    self.model_combo.currentIndexChanged.connect(self._on_model_combo)
    self.model_combo.setAcceptDrops(False)
    bottom.addWidget(self.model_combo)

    self.action_btn = QPushButton(_line_icon("send", 16, "#FFFFFF"), "")
    self.action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    self.action_btn.setAutoDefault(False)
    self.action_btn.setFixedSize(34, 34)
    self.action_btn.setIconSize(QSize(16, 16))
    self.action_btn.setStyleSheet(_BTN_PRIMARY)
    self.action_btn.setToolTip("发送")
    self.action_btn.clicked.connect(self._on_action_clicked)
    bottom.addWidget(self.action_btn)

    right.addLayout(bottom)
    body.addLayout(right, 1)
    root.addLayout(body, 1)
"""

_DEFAULT_WELCOME_CODE = """\
def build_welcome(self):
    w = QWidget()
    w.setStyleSheet("background: transparent;")
    w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    lay = QVBoxLayout(w)
    lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lay.setSpacing(12)
    icon = QLabel()
    pix = QPixmap(_app_icon_path())
    if not pix.isNull():
        icon.setPixmap(pix.scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation))
    icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lay.addWidget(icon)
    t = QLabel("zhuzhu Copilot")
    t.setStyleSheet(f"color: {ACCENT}; font-size: 22px; font-weight: 800;")
    t.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lay.addWidget(t)
    s = QLabel("Cordis AI Agent · 自定义工作流 · 热插拔 UI/UX")
    s.setStyleSheet(f"color: {TEXT_DIM}; font-size: 13px;")
    s.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lay.addWidget(s)
    tips = QLabel(
        "• 功能切换、执行模式、工作目录、工作力度、模型选择 → 右上角设置<br>"
        "• 直接输入任务描述，按 Enter 发送<br>"
        "• 输入 / 查看可用命令<br>"
        "• 拖拽文件/图片到输入框或 Ctrl+V 粘贴截图<br>"
        "• 支持自定义 UI/UX 包 — 设置 → UI/UX 自定义")
    tips.setStyleSheet(f"color: {TEXT_DIM}; font-size: 12px;")
    tips.setAlignment(Qt.AlignmentFlag.AlignCenter)
    tips.setWordWrap(True)
    tips.setMaximumWidth(420)
    lay.addWidget(tips)
    return w
"""


# ── 公共 API ────────────────────────────────────────────────────


def _ensure_dir():
    """确保 UI/UX 根目录、默认包与随应用分发的内置包存在"""
    _UI_UX_DIR.mkdir(parents=True, exist_ok=True)
    # 先清理已不再随应用分发的内置包残留（并复位被清包的活跃状态），
    # 再播种当前内置包，避免旧内置主题遗留在包列表/活跃态中
    _purge_stale_builtin_packages()
    _ensure_default_package()
    _ensure_builtin_packages()
    # 启动时自动更新内置包：内置版本更新则覆盖用户目录，确保打包后 UI/UX 同步
    try:
        seed_all_builtin_packages()
    except Exception:
        pass


def _ensure_default_package():
    """确保内置默认包存在（不可删除，仅写入一次）"""
    d = _UI_UX_DIR / _DEFAULT_PACKAGE
    if not d.is_dir():
        d.mkdir(parents=True, exist_ok=True)
        meta = {"name": _DEFAULT_PACKAGE, "description": "内置默认 UI/UX（不可删除）",
                "is_builtin": True, "version": 1}
        (d / "ui_ux.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        (d / "build_ui.py").write_text(_DEFAULT_BUILD_UI_CODE, encoding="utf-8")
        (d / "build_welcome.py").write_text(_DEFAULT_WELCOME_CODE, encoding="utf-8")


# ── 随应用分发的内置 UI/UX 包（非默认，打包时自动集成） ──


def _builtin_packages_dir() -> Path:
    """定位随应用分发的内置 UI/UX 包目录（默认包之外的非默认内置包）。
    - 源码/开发：winapp_migrator/ui_ux/（与本模块同包）
    - PyInstaller onefile：解包到 sys._MEIPASS/winapp_migrator/ui_ux
    - PyInstaller onedir：<可执行文件目录>/_internal/winapp_migrator/ui_ux
    目录不存在返回空 Path（非冻结环境无内置包时不报错）。"""
    try:
        pkg = Path(__file__).resolve().parent.parent / "ui_ux"
        if pkg.is_dir():
            return pkg
    except Exception:
        pass
    if getattr(sys, "frozen", False):
        for base in (getattr(sys, "_MEIPASS", None), str(Path(sys.executable).parent)):
            if not base:
                continue
            cand = Path(base) / "winapp_migrator" / "ui_ux"
            if cand.is_dir():
                return cand
    return Path()


def _builtin_package_names() -> list:
    """列出随应用分发、非默认的内置 UI/UX 包名（含 ui_ux.json 的目录）"""
    d = _builtin_packages_dir()
    if not d.is_dir():
        return []
    out = []
    for child in sorted(d.iterdir()):
        if not child.is_dir() or child.name == _DEFAULT_PACKAGE:
            continue
        if (child / "ui_ux.json").is_file():
            out.append(child.name)
    return out


def _purge_stale_builtin_packages():
    """清理已不再随应用分发的内置 UI/UX 包残留（如已下架的旧内置主题）。
    仅删除用户目录中标记 is_builtin 且当前已无内置版本对应的包目录；
    用户自建包（is_builtin 非真）不受影响。若被清理的正是活跃包，则
    自动复位为内置默认包。任何异常静默，绝不阻塞启动。"""
    try:
        shipped = set(_builtin_package_names())
        for d in list(_UI_UX_DIR.iterdir()):
            if not d.is_dir() or d.name == _DEFAULT_PACKAGE:
                continue
            meta = _read_meta(d) or {}
            if not meta.get("is_builtin") or d.name in shipped:
                continue
            shutil.rmtree(d, ignore_errors=True)
            _invalidate_active_cache()
            if get_active_package() == d.name:
                _persist_active(_DEFAULT_PACKAGE)
    except Exception:
        pass


def _ensure_builtin_packages():
    """把随应用分发的内置 UI/UX 包播种到用户配置目录。
    仅当目标目录不存在时整体复制（不覆盖用户已编辑/升级过的包），
    并确保播种副本的元数据带 is_builtin=True（不可删除）。"""
    src_root = _builtin_packages_dir()
    if not src_root.is_dir():
        return
    for name in _builtin_package_names():
        src = src_root / name
        dst = _UI_UX_DIR / name
        if dst.exists():
            continue
        try:
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            meta = _read_meta(dst) or {"name": name}
            meta["name"] = name
            meta["is_builtin"] = True
            _write_meta(dst, meta)
        except Exception:
            pass


def _seed_builtin_package_if_needed(name: str) -> bool:
    """检查并重新播种内置 UI/UX 包。
    若用户目录已有该包，比对内置版本时间戳：内置更新则覆盖；否则跳过。
    返回 True 表示执行了播种（覆盖或新建），False 表示无需操作。"""
    src_root = _builtin_packages_dir()
    if not src_root.is_dir():
        return False
    src = src_root / name
    dst = _UI_UX_DIR / name
    if not src.is_dir():
        return False
    # 新建：直接复制
    if not dst.exists():
        try:
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            meta = _read_meta(dst) or {"name": name}
            meta["name"] = name
            meta["is_builtin"] = True
            _write_meta(dst, meta)
            return True
        except Exception:
            return False
    # 已存在：比对时间戳，内置更新则覆盖
    try:
        src_mtime = max(f.stat().st_mtime for f in src.rglob("*") if f.is_file())
        dst_mtime = max(f.stat().st_mtime for f in dst.rglob("*") if f.is_file())
        if src_mtime <= dst_mtime:
            return False
        # 内置更新，覆盖用户目录的包
        shutil.rmtree(dst)
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        meta = _read_meta(dst) or {"name": name}
        meta["name"] = name
        meta["is_builtin"] = True
        _write_meta(dst, meta)
        return True
    except Exception:
        return False


def seed_all_builtin_packages() -> list:
    """启动时调用：遍历所有内置包，按需播种/更新。
    返回更新过的包名列表。"""
    updated = []
    src_root = _builtin_packages_dir()
    if not src_root.is_dir():
        return updated
    for name in _builtin_package_names():
        if _seed_builtin_package_if_needed(name):
            updated.append(name)
    return updated


def list_packages() -> list:
    """返回所有 UI/UX 包元数据列表，按 name 排序"""
    _ensure_dir()
    out = []
    if not _UI_UX_DIR.is_dir():
        return out
    for d in sorted(_UI_UX_DIR.iterdir()):
        if not d.is_dir():
            continue
        meta = _read_meta(d)
        if meta:
            out.append(meta)
    return out


def get_package(name: str) -> dict:
    """获取单个包元数据，不存在返回空 dict"""
    _ensure_dir()
    d = _UI_UX_DIR / name
    return _read_meta(d) or {}


def _read_meta(d: Path) -> dict:
    """读取 ui_ux.json 元数据"""
    f = d / "ui_ux.json"
    if not f.is_file():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}


def _write_meta(d: Path, meta: dict) -> None:
    """写入 ui_ux.json 元数据"""
    (d / "ui_ux.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def is_builtin(name: str) -> bool:
    """判断是否为内置包（不可删除）"""
    m = get_package(name)
    return bool(m.get("is_builtin"))


def is_default(name: str) -> bool:
    """判断是否为默认包"""
    return name == _DEFAULT_PACKAGE


# 活跃包名读取缓存：get_active_package 被面板 __init__/_build_ui/_retheme 高频调用，
# 每次读 active.txt + is_dir 磁盘 IO；切换 UI/UX 后失效。
_ACTIVE_CACHE: dict = {"name": None}


def _invalidate_active_cache() -> None:
    _ACTIVE_CACHE["name"] = None


def _persist_active(name: str) -> None:
    """持久化活跃 UI/UX 包：active.txt（主）+ QSettings 冗余（防 active.txt 丢失）。
    QSettings 在无 QCoreApplication 环境下调用会告警，失败时静默忽略（active.txt 为主）。"""
    try:
        _ACTIVE_FILE.write_text(name, encoding="utf-8")
    except OSError:
        pass
    try:
        from PyQt6.QtCore import QSettings
        QSettings("WinAppMigrator", "WinAppMigrator").setValue("agent_uiux", name)
    except Exception:
        pass
    _invalidate_active_cache()


def get_active_package() -> str:
    """获取当前活跃 UI/UX 包名，默认返回 'default'。
    主来源 active.txt；缺失或指向不存在的包时回退 QSettings 冗余记录（重启/清理后仍保持）。
    结果缓存：避免 __init__/_build_ui/_retheme 内多次读盘。"""
    cached = _ACTIVE_CACHE.get("name")
    if cached is not None:
        return cached
    name = _DEFAULT_PACKAGE
    _ensure_dir()
    if _ACTIVE_FILE.is_file():
        n = _ACTIVE_FILE.read_text(encoding="utf-8", errors="replace").strip()
        if n and (_UI_UX_DIR / n).is_dir():
            name = n
    if name == _DEFAULT_PACKAGE:
        # 兜底：active.txt 丢失或包被移除 → 读 QSettings 冗余记录并恢复 active.txt
        try:
            from PyQt6.QtCore import QSettings
            qs = QSettings("WinAppMigrator", "WinAppMigrator")
            n = str(qs.value("agent_uiux", "") or "").strip()
            if n and (_UI_UX_DIR / n).is_dir():
                try:
                    _ACTIVE_FILE.write_text(n, encoding="utf-8")
                except Exception:
                    pass
                name = n
        except Exception:
            pass
    _ACTIVE_CACHE["name"] = name
    return name


def is_custom_package_active() -> bool:
    """当前活跃 UI/UX 是否为自定义包（非内置 default）。

    用于让自定义主题（如用户自建包）的专属效果——Acrylic 毛玻璃、无边框标题栏、
    透明背景、鼠标液态波纹等——仅在自定义包激活时生效，绝不污染内置默认主题。
    """
    try:
        return get_active_package() != _DEFAULT_PACKAGE
    except Exception:
        return False


def set_active_package(name: str) -> tuple:
    """设置活跃 UI/UX 包（持久化：active.txt + QSettings，重启/切换主题后保持）。
    返回 (ok, msg)"""
    _ensure_dir()
    if not name or name == _DEFAULT_PACKAGE:
        # 切回默认
        _persist_active(_DEFAULT_PACKAGE)
        return (True, "已切换至默认 UI/UX")
    d = _UI_UX_DIR / name
    if not d.is_dir() or not (d / "build_ui.py").is_file():
        return (False, f"UI/UX 包 '{name}' 不存在或缺少 build_ui.py")
    _persist_active(name)
    return (True, f"已切换至 UI/UX 包 '{name}'")


def get_build_ui_code(name: str) -> str:
    """读取指定包的 build_ui.py 代码"""
    f = _UI_UX_DIR / name / "build_ui.py"
    if not f.is_file():
        return ""
    return f.read_text(encoding="utf-8", errors="replace")


def get_welcome_code(name: str) -> str:
    """读取指定包的 build_welcome.py 代码（可选）"""
    f = _UI_UX_DIR / name / "build_welcome.py"
    if not f.is_file():
        return ""
    return f.read_text(encoding="utf-8", errors="replace")


# 主题色板允许的键（与 agent_panel._THEMES 一致）
THEME_KEYS = ("BG", "BG_BOTTOM", "PANEL", "CARD", "BORDER", "BORDER_SOFT", "TEXT",
              "TEXT_DIM", "ACCENT", "ACCENT_HOVER", "LINK_COLOR", "USER_BG", "AI_BG",
              "OK", "WARN", "ERR", "HOVER", "CODE_ACCENT", "CODE_BG")


def get_package_theme(name: str) -> dict:
    """读取包的自定义主题色板：{"dark": {...}, "light": {...}}，无则返回空 dict"""
    meta = get_package(name)
    theme = meta.get("theme")
    return theme if isinstance(theme, dict) else {}


def update_package_theme(name: str, theme: dict) -> tuple:
    """写入包的自定义主题色板（{dark:{...}, light:{...}}，键限 THEME_KEYS）。
    与已有主题合并：传入的键覆盖，未传入的键保留；theme 传空 {} 删除全部主题。
    返回 (ok, msg)。"""
    d = _UI_UX_DIR / name
    if not d.is_dir():
        return (False, f"包 '{name}' 不存在")
    if theme is None:
        theme = {}
    if not isinstance(theme, dict):
        return (False, "主题色板必须是 JSON 对象 {dark:{...}, light:{...}}")
    meta = _read_meta(d) or {"name": name, "is_builtin": False, "version": 1}
    old = meta.get("theme") if isinstance(meta.get("theme"), dict) else {}
    # 合并：提供的键覆盖，未提供的保留旧值
    clean = {}
    for mode in ("dark", "light"):
        sub = theme.get(mode)
        base = old.get(mode) if isinstance(old.get(mode), dict) else {}
        merged = dict(base)
        if isinstance(sub, dict):
            for k, v in sub.items():
                if k in THEME_KEYS:
                    merged[k] = str(v)
        if merged:
            clean[mode] = merged
    if clean:
        meta["theme"] = clean
    else:
        meta.pop("theme", None)
    _write_meta(d, meta)
    return (True, f"UI/UX 包 '{name}' 主题色板已更新")


def read_package_part(name: str, part: str) -> tuple:
    """读取包内某部分内容：part=build_ui / build_welcome / theme / meta。
    返回 (ok, text)。"""
    d = _UI_UX_DIR / name
    if not d.is_dir():
        return (False, f"包 '{name}' 不存在")
    if part == "build_ui":
        return (True, get_build_ui_code(name))
    if part == "build_welcome":
        return (True, get_welcome_code(name))
    if part == "theme":
        import json as _j
        return (True, _j.dumps(get_package_theme(name), ensure_ascii=False, indent=2))
    if part == "meta":
        import json as _j
        return (True, _j.dumps(get_package(name), ensure_ascii=False, indent=2))
    return (False, f"未知部分 {part}，可选 build_ui/build_welcome/theme/meta")


def duplicate_package(name: str, new_name: str) -> tuple:
    """复制一个 UI/UX 包为新包。返回 (ok, msg)。"""
    _ensure_dir()
    if not new_name or not new_name.strip():
        return (False, "新包名不能为空")
    new_name = new_name.strip()
    if not re.match(r'^[a-zA-Z0-9_-]+$', new_name):
        return (False, "包名只允许字母、数字、下划线和连字符")
    src = _UI_UX_DIR / name
    dst = _UI_UX_DIR / new_name
    if not src.is_dir():
        return (False, f"源包 '{name}' 不存在")
    if dst.is_dir():
        return (False, f"包 '{new_name}' 已存在")
    try:
        shutil.copytree(src, dst)
        meta = _read_meta(dst) or {}
        meta["name"] = new_name
        meta["is_builtin"] = False
        meta.pop("description", None)
        _write_meta(dst, meta)
        return (True, f"已复制 '{name}' → '{new_name}'")
    except OSError as e:
        return (False, f"复制失败: {e}")


def set_build_ui_code(name: str, code: str) -> tuple:
    """写入包的 build_ui.py 代码"""
    if not name:
        return (False, "包名不能为空")
    d = _UI_UX_DIR / name
    if not d.is_dir():
        return (False, f"包 '{name}' 不存在")
    try:
        (d / "build_ui.py").write_text(code, encoding="utf-8")
        return (True, "build_ui.py 已更新")
    except OSError as e:
        return (False, f"写入失败: {e}")


def set_welcome_code(name: str, code: str) -> tuple:
    """写入包的 build_welcome.py 代码"""
    if not name:
        return (False, "包名不能为空")
    d = _UI_UX_DIR / name
    if not d.is_dir():
        return (False, f"包 '{name}' 不存在")
    try:
        (d / "build_welcome.py").write_text(code, encoding="utf-8")
        return (True, "build_welcome.py 已更新")
    except OSError as e:
        return (False, f"写入失败: {e}")


def create_package(name: str, description: str,
                   build_ui_code: str = "",
                   build_welcome_code: str = "",
                   theme: dict = None,
                   plugins: list = None,
                   mcp: list = None) -> tuple:
    """创建新 UI/UX 包。theme 为可选自定义主题色板 {dark:{...}, light:{...}}；
    plugins/mcp 为可选依赖声明（导出 zip 时连同打包）。
    返回 (ok, msg)"""
    _ensure_dir()
    if not name or not name.strip():
        return (False, "包名不能为空")
    name = name.strip()
    # 安全校验：只允许字母数字下划线连字符
    import re
    if not re.match(r'^[a-zA-Z0-9_-]+$', name):
        return (False, "包名只允许字母、数字、下划线和连字符")
    if name == _DEFAULT_PACKAGE:
        return (False, f"'{_DEFAULT_PACKAGE}' 是保留内置包名")
    d = _UI_UX_DIR / name
    if d.is_dir():
        return (False, f"包 '{name}' 已存在")
    try:
        d.mkdir(parents=True, exist_ok=True)
        meta = {"name": name, "description": description or "",
                "is_builtin": False, "version": 1}
        if isinstance(theme, dict) and theme:
            clean = {}
            for mode in ("dark", "light"):
                sub = theme.get(mode)
                if isinstance(sub, dict):
                    clean[mode] = {k: str(v) for k, v in sub.items()
                                   if k in THEME_KEYS}
            if clean:
                meta["theme"] = clean
        deps = _clean_deps(plugins, mcp)
        if deps:
            meta.update(deps)
        _write_meta(d, meta)
        # 写入代码，若为空则使用默认模板
        (d / "build_ui.py").write_text(
            build_ui_code or _DEFAULT_BUILD_UI_CODE, encoding="utf-8")
        if build_welcome_code:
            (d / "build_welcome.py").write_text(build_welcome_code, encoding="utf-8")
        return (True, f"UI/UX 包 '{name}' 创建成功")
    except OSError as e:
        return (False, f"创建失败: {e}")


def _clean_deps(plugins, mcp) -> dict:
    """清洗依赖声明为 ui_ux.json 元数据：{"plugins": [...], "mcp": [...]}"""
    out = {}
    if isinstance(plugins, (list, tuple)):
        pl = [str(x).strip() for x in plugins if str(x).strip()]
        if pl:
            out["plugins"] = pl
    if isinstance(mcp, (list, tuple)):
        mc = [str(x).strip() for x in mcp if str(x).strip()]
        if mc:
            out["mcp"] = mc
    return out


def update_package_deps(name: str, plugins: list = None, mcp: list = None) -> tuple:
    """更新 UI/UX 包的依赖声明（plugins/mcp，导出 zip 时连同打包）。
    plugins/mcp 传 None 表示保留原值；传空列表 [] 表示清空。
    返回 (ok, msg)。"""
    d = _UI_UX_DIR / name
    if not d.is_dir():
        return (False, f"包 '{name}' 不存在")
    meta = _read_meta(d) or {"name": name, "is_builtin": False, "version": 1}
    if plugins is not None:
        pl = [str(x).strip() for x in plugins if str(x).strip()] if isinstance(plugins, (list, tuple)) else []
        if pl:
            meta["plugins"] = pl
        else:
            meta.pop("plugins", None)
    if mcp is not None:
        mc = [str(x).strip() for x in mcp if str(x).strip()] if isinstance(mcp, (list, tuple)) else []
        if mc:
            meta["mcp"] = mc
        else:
            meta.pop("mcp", None)
    _write_meta(d, meta)
    extra = []
    if "plugins" in meta:
        extra.append(f"插件 {len(meta['plugins'])} 个")
    if "mcp" in meta:
        extra.append(f"MCP {len(meta['mcp'])} 个")
    dep_txt = f"（{', '.join(extra)}）" if extra else ""
    return (True, f"UI/UX 包 '{name}' 依赖已更新{dep_txt}，导出时会连同打包")


def delete_package(name: str) -> tuple:
    """删除用户自建 UI/UX 包。内置包不可删除。返回 (ok, msg)"""
    if not name:
        return (False, "包名不能为空")
    if is_builtin(name):
        return (False, f"内置包 '{name}' 不可删除")
    d = _UI_UX_DIR / name
    if not d.is_dir():
        return (False, f"包 '{name}' 不存在")
    try:
        shutil.rmtree(d)
        # 如果删除的是当前活跃包，切回默认
        if get_active_package() == name:
            _persist_active(_DEFAULT_PACKAGE)
        return (True, f"UI/UX 包 '{name}' 已删除")
    except OSError as e:
        return (False, f"删除失败: {e}")


def edit_package_meta(name: str, description: str = None) -> tuple:
    """编辑包元数据（目前仅支持 description）。返回 (ok, msg)"""
    d = _UI_UX_DIR / name
    if not d.is_dir():
        return (False, f"包 '{name}' 不存在")
    meta = _read_meta(d) or {"name": name, "is_builtin": False, "version": 1}
    if description is not None:
        meta["description"] = description
    _write_meta(d, meta)
    return (True, "元数据已更新")


# ── 动态加载 build_ui ───────────────────────────────────────────


def apply_package_theme() -> bool:
    """应用当前活跃 UI/UX 包的自定义主题色板（覆盖 agent_panel 模块级颜色常量）。

    幂等：先重置为全局主题（apply_theme），再按当前 dark/light 用包色板覆盖。
    无包主题时返回 False（模块保持全局主题）。"""
    import sys as _sys
    mod = _sys.modules.get("winapp_migrator.ui.agent_panel")
    if mod is None:
        return False
    try:
        mod.apply_theme()   # 重置为全局主题，保证切换包/切回默认后颜色不残留
    except Exception:
        pass
    name = get_active_package()
    if not name or name == _DEFAULT_PACKAGE:
        return False
    theme = get_package_theme(name)
    if not theme:
        return False
    try:
        cur = mod._resolve_theme()
    except Exception:
        cur = "dark"
    colors = theme.get(cur)
    # 浅色模式下包缺浅色板 → 保持全局浅色（绝不套用深色板，否则浅色界面/聊天气泡代码块
    # 全部变深色，违反"浅色跟随"）；非浅色模式缺板时回退包的深色板（如仅提供 dark 的包）。
    if not isinstance(colors, dict) or not colors:
        if cur == "light":
            return False
        colors = theme.get("dark")
    if not isinstance(colors, dict) or not colors:
        return False
    try:
        mod.apply_theme_custom(colors)
        return True
    except Exception:
        return False


def _box_blur(pixmap, radius: int = 16):
    """真正的毛玻璃模糊：Pillow 高斯模糊（Qt 无 CSS backdrop-filter: blur()）。

    输入 QPixmap，返回模糊后的新 QPixmap（透明区域保持透明）。失败时原样返回，
    绝不抛异常（保证 AI 生成的玻璃绘制不会因此闪退）。"""
    if pixmap is None or pixmap.isNull():
        return pixmap
    radius = max(1, int(radius))
    try:
        from PyQt6.QtCore import QBuffer, QIODevice
        from PyQt6.QtGui import QImage, QPixmap
        import io as _io
        from PIL import Image, ImageFilter
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        pixmap.save(buf, "PNG")
        png = bytes(buf.data())
        buf.close()
        pil = Image.open(_io.BytesIO(png)).convert("RGBA")
        blurred = pil.filter(ImageFilter.GaussianBlur(radius))
        raw = blurred.tobytes("raw", "RGBA")
        qimg = QImage(raw, blurred.width, blurred.height,
                      blurred.width * 4, QImage.Format.Format_RGBA8888)
        out = QPixmap.fromImage(qimg)
        return out if not out.isNull() else pixmap
    except Exception:
        return pixmap


def _glass_fill(painter, rect, radius: int = 18, tint=None, bg_pixmap=None,
                accent=None, phase: float = 0.0):
    """在 painter 上绘制一块真正的「液体磨砂玻璃」（供 AI 生成的 build_ui 使用）。

    参数：
    - rect: 玻璃区域（QRect）
    - radius: 圆角半径（px）
    - tint: 玻璃底色 QColor（**默认 None = 纯透明无色**，只透出模糊背景；要染色才传，且必须低 alpha）
    - bg_pixmap: 待透出的背景快照 QPixmap（可选；非空时先高斯模糊再绘制，实现真毛玻璃"背景模糊透出"）
    - accent: 主题强调色 QColor（**默认 None = 流光/反光均为纯白，保证"无色混入"**）
    - phase: 液态流动相位 0.0~1.0（用 QTimer 循环 0→1 更新，制造液态动感；0 = 静止）

    绘制顺序：模糊背景透出 → 纯透明底色 → 顶部高光（随 phase 流动）→
    对角液光（随 phase 流动）→ 底部微光（随 phase 呼吸）→ 边缘反光描边。"""
    from PyQt6.QtGui import (QColor as _QC, QLinearGradient, QRadialGradient,
                             QBrush, QPen, QPainterPath, QPainter as _QP)
    from PyQt6.QtCore import QPointF, QRectF, Qt as _Qt

    if rect is None or rect.width() <= 0 or rect.height() <= 0:
        return
    r = QRectF(rect)
    rad = max(1, int(radius))
    ph = max(0.0, min(1.0, float(phase)))

    clip = QPainterPath()
    clip.addRoundedRect(r, rad, rad)

    painter.save()
    painter.setRenderHint(_QP.RenderHint.Antialiasing)
    painter.setClipPath(clip)

    # 1) 透出模糊背景（真毛玻璃，纯透明核心）
    if bg_pixmap is not None and not bg_pixmap.isNull():
        blurred = _box_blur(bg_pixmap, 16)
        painter.drawPixmap(
            rect.topLeft(),
            blurred.scaled(rect.size(),
                           _Qt.AspectRatioMode.IgnoreAspectRatio,
                           _Qt.TransformationMode.SmoothTransformation))

    # 2) 纯透明底色（默认纯白极淡，无色混入）
    base = _QC(tint) if tint is not None else _QC(255, 255, 255, 28)
    painter.fillRect(rect, base)

    # 3) 顶部高光（白色，随 phase 上下流动）
    hi_y = rect.top() + rect.height() * (0.5 * ph)
    hi = QLinearGradient(0, hi_y, 0, hi_y + rect.height() * 0.62)
    hi.setColorAt(0, _QC(255, 255, 255, 68))
    hi.setColorAt(0.45, _QC(255, 255, 255, 14))
    hi.setColorAt(1, _QC(255, 255, 255, 0))
    painter.fillRect(rect, QBrush(hi))

    # 4) 对角液光（白色，随 phase 左右摆动 → 液态流动）
    drift = (ph - 0.5) * 0.55
    sh = QLinearGradient(QPointF(rect.left() + rect.width() * drift, rect.top()),
                         QPointF(rect.right() + rect.width() * drift, rect.bottom()))
    sh.setColorAt(0, _QC(255, 255, 255, 46))
    sh.setColorAt(0.34, _QC(255, 255, 255, 0))
    sh.setColorAt(1, _QC(255, 255, 255, 0))
    painter.fillRect(rect, QBrush(sh))

    # 5) 底部微光（纯白或 accent，随 phase 呼吸）
    glow_alpha = 40 + int(40 * ph)
    if accent is not None:
        a = _QC(accent)
        glow_c = _QC(a.red(), a.green(), a.blue(), glow_alpha)
    else:
        glow_c = _QC(255, 255, 255, glow_alpha)
    rg = QRadialGradient(QPointF(rect.center().x(), rect.bottom() + rect.height() * 0.12),
                         rect.width() * 0.92)
    rg.setColorAt(0, glow_c)
    rg.setColorAt(1, _QC(glow_c.red(), glow_c.green(), glow_c.blue(), 0))
    painter.fillRect(rect, QBrush(rg))

    painter.restore()

    # 6) 边缘反光（玻璃厚度 / 受光边缘）—— 不 clip，描边完整；默认纯白，无彩色
    painter.save()
    painter.setRenderHint(_QP.RenderHint.Antialiasing)
    pen_out = QPen(_QC(255, 255, 255, 128))
    pen_out.setWidth(1)
    painter.setPen(pen_out)
    painter.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), rad, rad)
    if accent is not None:
        a = _QC(accent)
        pen_glow = QPen(_QC(a.red(), a.green(), a.blue(), 90))
        pen_glow.setWidth(2)
        painter.setPen(pen_glow)
        painter.drawRoundedRect(r.adjusted(1, 1, -1, -1), rad, rad)
    painter.restore()


def draw_liquid_glass(painter, rect, phase=0.0, radius=0, bg_pixmap=None,
                      bg_top=None, bg_bottom=None):
    """绘制一块「标准液态玻璃表面」背景（纯透明无色 + 液态流动动感）。

    相比 _glass_fill，本函数额外负责：
    - 未提供 bg_pixmap 时，自绘「渐变底 + 环境光斑」作为玻璃透出的背景；
    - 更强的环境光斑（白色，无色），营造"透过玻璃看到背后光"的透明感；
    - 调用 _glass_fill 完成真模糊玻璃层 + 流动高光 + 边缘反光。

    参数：
    - painter: 目标 QPainter
    - rect: 玻璃区域 QRect
    - phase: 液态流动相位 0.0~1.0（QTimer 循环 0→1 更新）
    - radius: 圆角半径（0 = 直角，铺满面板背景时用 0）
    - bg_pixmap: 预生成的背景快照（可选，非空则跳过自绘底）
    - bg_top / bg_bottom: 渐变底色（默认取当前主题 BG / BG_BOTTOM）
    """
    from PyQt6.QtGui import (QColor as _QC, QLinearGradient, QRadialGradient,
                             QBrush, QPixmap, QPainter as _QP)
    from PyQt6.QtCore import QPointF, Qt as _Qt
    if rect is None or rect.width() <= 0 or rect.height() <= 0:
        return
    rw, rh = rect.width(), rect.height()
    ph = max(0.0, min(1.0, float(phase)))

    if bg_top is None or bg_bottom is None:
        import sys as _sys
        _mod = _sys.modules.get("winapp_migrator.ui.agent_panel")
        _bg = getattr(_mod, "BG", "#050608") if _mod else "#050608"
        _bgb = getattr(_mod, "BG_BOTTOM", "#0B0D11") if _mod else "#0B0D11"
        bg_top = bg_top or _bg
        bg_bottom = bg_bottom or _bgb

    # 1) 背景快照：渐变底 + 环境光斑（外部未提供时自绘）
    if bg_pixmap is None or bg_pixmap.isNull():
        bg = QPixmap(rect.size())
        bg.fill(_Qt.GlobalColor.transparent)
        bp = _QP(bg)
        g = QLinearGradient(0, 0, rw, rh)
        g.setColorAt(0, _QC(bg_top))
        g.setColorAt(1, _QC(bg_bottom))
        bp.fillRect(rect, g)
        for fx, fy, fr, fa in ((0.16, 0.10, 0.75, 42),
                               (0.88, 0.88, 0.85, 36),
                               (0.72, 0.12, 0.55, 24),
                               (0.12, 0.92, 0.60, 22)):
            gp = QRadialGradient(QPointF(rw * fx, rh * fy), rw * fr)
            gp.setColorAt(0, _QC(255, 255, 255, fa))
            gp.setColorAt(1, _QC(255, 255, 255, 0))
            bp.fillRect(rect, QBrush(gp))
        bp.end()
        bg_pixmap = bg

    # 2) 玻璃层（真模糊透出 + 纯白高光 + 液态流动 + 边缘反光）
    _glass_fill(painter, rect, radius=radius, tint=None,
                bg_pixmap=bg_pixmap, accent=None, phase=ph)


def grab_desktop_glass(widget, radius=16):
    """抓取 widget 背后屏幕区域（桌面/其他窗口）并高斯模糊，返回 QPixmap。

    实现「透出桌面」的毛玻璃背景：窗口透明时，屏幕该区域即为桌面内容，
    抓取 → 高斯模糊 → 得到透出桌面的磨砂背景。失败返回 None（绝不抛异常）。
    """
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import QPoint
    try:
        app = QApplication.instance()
        if app is None or widget is None:
            return None
        screen = app.primaryScreen()
        if screen is None:
            return None
        w = max(1, widget.width())
        h = max(1, widget.height())
        tl = widget.mapToGlobal(QPoint(0, 0))
        pm = screen.grabWindow(0, tl.x(), tl.y(), w, h)
        if pm is None or pm.isNull():
            return None
        return _box_blur(pm, radius)
    except Exception:
        return None


def draw_desktop_glass(painter, rect, blur_pm=None, phase=0.0, radius=0):
    """绘制「透出桌面」的液态玻璃表面：模糊桌面背景 + 纯白液态高光。

    与 draw_liquid_glass 不同，本函数**不画任何渐变底 / 主题色底**：
    只画模糊桌面（透出）+ 纯白流动高光 + 边缘反光，完全不受深/浅主题影响。
    """
    from PyQt6.QtGui import (QColor as _QC, QLinearGradient, QRadialGradient,
                             QBrush, QPen, QPainter as _QP)
    from PyQt6.QtCore import QPointF, QRectF, Qt as _Qt
    if rect is None or rect.width() <= 0 or rect.height() <= 0:
        return
    r = QRectF(rect)
    rad = max(1, int(radius)) if radius > 0 else 0
    ph = max(0.0, min(1.0, float(phase)))

    painter.save()
    painter.setRenderHint(_QP.RenderHint.Antialiasing)

    # 1) 透出桌面：画模糊背景（无模糊背景时画一层极淡透明，避免纯黑）
    if blur_pm is not None and not blur_pm.isNull():
        painter.drawPixmap(rect.topLeft(), blur_pm.scaled(
            rect.size(), _Qt.AspectRatioMode.IgnoreAspectRatio,
            _Qt.TransformationMode.SmoothTransformation))
    else:
        painter.fillRect(rect, _QC(0, 0, 0, 1))

    # 2) 顶部高光（纯白，随 phase 上下流动）
    hi_y = rect.top() + rect.height() * (0.5 * ph)
    hi = QLinearGradient(0, hi_y, 0, hi_y + rect.height() * 0.62)
    hi.setColorAt(0, _QC(255, 255, 255, 58))
    hi.setColorAt(0.45, _QC(255, 255, 255, 12))
    hi.setColorAt(1, _QC(255, 255, 255, 0))
    painter.fillRect(rect, QBrush(hi))

    # 3) 对角液光（纯白，随 phase 左右摆动 → 液态流动）
    drift = (ph - 0.5) * 0.55
    sh = QLinearGradient(QPointF(rect.left() + rect.width() * drift, rect.top()),
                         QPointF(rect.right() + rect.width() * drift, rect.bottom()))
    sh.setColorAt(0, _QC(255, 255, 255, 38))
    sh.setColorAt(0.34, _QC(255, 255, 255, 0))
    sh.setColorAt(1, _QC(255, 255, 255, 0))
    painter.fillRect(rect, QBrush(sh))

    # 4) 底部微光（纯白，随 phase 呼吸）
    glow_alpha = 30 + int(30 * ph)
    rg = QRadialGradient(QPointF(rect.center().x(), rect.bottom() + rect.height() * 0.12),
                         rect.width() * 0.92)
    rg.setColorAt(0, _QC(255, 255, 255, glow_alpha))
    rg.setColorAt(1, _QC(255, 255, 255, 0))
    painter.fillRect(rect, QBrush(rg))

    painter.restore()

    # 5) 边缘反光（玻璃厚度）
    if rad > 0:
        painter.save()
        painter.setRenderHint(_QP.RenderHint.Antialiasing)
        pen_out = QPen(_QC(255, 255, 255, 110))
        pen_out.setWidth(1)
        painter.setPen(pen_out)
        painter.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), rad, rad)
        painter.restore()


def _set_window_accent(widget, state, tint=0x14808080):
    """设置窗口 Acrylic 状态（内部）：state=4 Acrylic / 3 BlurBehind / 0 关闭。
    关闭时窗口退化为纯透明（无模糊），用于拖动/缩放期间消除 DWM 逐帧重算模糊的卡顿。"""
    import ctypes
    try:
        hwnd = int(widget.winId())
        if hwnd == 0:
            return False

        class _AccentPolicy(ctypes.Structure):
            _fields_ = [("AccentState", ctypes.c_uint),
                        ("AccentFlags", ctypes.c_uint),
                        ("GradientColor", ctypes.c_uint),
                        ("AnimationId", ctypes.c_uint)]

        class _WinCompatAttrData(ctypes.Structure):
            _fields_ = [("Attribute", ctypes.c_int),
                        ("Data", ctypes.c_void_p),
                        ("SizeOfData", ctypes.c_size_t)]

        accent = _AccentPolicy()
        accent.AccentState = int(state)
        accent.AccentFlags = 2       # 绘制整个窗口（含非客户区边框）
        accent.GradientColor = ctypes.c_uint(int(tint) & 0xFFFFFFFF).value
        data = _WinCompatAttrData()
        data.Attribute = 19          # WCA_ACCENT_POLICY
        data.Data = ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p)
        data.SizeOfData = ctypes.sizeof(accent)

        user32 = ctypes.windll.user32
        fn = user32.SetWindowCompositionAttribute
        fn.argtypes = [ctypes.c_void_p, ctypes.POINTER(_WinCompatAttrData)]
        fn.restype = ctypes.c_int
        return bool(fn(ctypes.c_void_p(hwnd), ctypes.byref(data)))
    except Exception:
        return False


def _acrylic_state() -> int:
    """返回当前 Windows 版本适用的 Acrylic 状态：Win10 1809(17763)+ 用 4，否则 3。"""
    import sys as _sys
    try:
        _v = _sys.getwindowsversion()
        if getattr(_v, "build", 0) < 17763:
            return 3   # ACCENT_ENABLE_BLURBEHIND
    except Exception:
        pass
    return 4           # ACCENT_ENABLE_ACRYLICBLURBEHIND


def apply_acrylic(widget, tint=0x14808080):
    """给窗口启用 Windows Acrylic 毛玻璃：系统实时透出桌面（含标题栏边框），
    窗口移动时由系统实时更新，无需抓屏。GradientColor 为 AABBGGRR；默认中性灰磨砂
    （alpha≈8%，无色相），在透出桌面的同时加入轻微磨砂/毛玻璃质感、保持高透明度。
    返回 True 成功，失败返回 False（绝不抛异常）。"""
    return _set_window_accent(widget, _acrylic_state(), tint)


def set_acrylic_enabled(widget, enabled, tint=0x14808080):
    """开启/关闭 Acrylic 毛玻璃。拖动/缩放窗口期间关闭（state=0，纯透明无模糊）
    以消除 DWM 逐帧重算模糊导致的严重卡顿，结束后恢复。返回 True 成功，绝不抛异常。"""
    return _set_window_accent(widget, _acrylic_state() if enabled else 0, tint)


def glassify_dialog(dlg, tint=0x50FFFFFF, frost_alpha=86):
    """给对话框应用液态玻璃：Acrylic 毛玻璃透桌面 + 浅色磨砂底（增强磨砂感而非暗色感，
    黑字可读）+ 透明背景 + 鼠标液态波动。
    frost_alpha 控制磨砂底不透明度（越大磨砂感越强、越不透明；越小越透、液态感越强），
    tint 控制 Acrylic 混合色 alpha（越小越透）。默认偏磨砂，供需要可读性的弹窗；
    AskUser/命令确认弹窗可传更低 frost_alpha 提升液态感。
    仅在自定义 UI/UX 包激活时生效（内置默认主题不启用，避免污染默认外观）。
    可在对话框 __init__ 末尾调用；返回 True 成功，失败返回 False（绝不抛异常）。"""
    if not is_custom_package_active():
        return False
    from PyQt6.QtWidgets import QWidget, QApplication
    from PyQt6.QtGui import QPainter, QColor, QPainterPath
    from PyQt6.QtCore import Qt, QEvent, QTimer, QRectF

    class _RippleOverlay(QWidget):
        def __init__(self, parent, frost):
            super().__init__(parent)
            self._frost = frost
            self._pos = None
            self._pulse = 0.0
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setStyleSheet("background: transparent;")
            self.setGeometry(parent.rect())
            self.lower()
            self._pulse_timer = QTimer(self)
            self._pulse_timer.setInterval(30)
            self._pulse_timer.timeout.connect(self._decay_pulse)

        def _decay_pulse(self):
            self._pulse = max(0.0, self._pulse - 0.12)
            if self._pulse <= 0.0:
                self._pulse_timer.stop()
            self.update()

        def set_ripple(self, pos, wave=False):
            # 节流：位置变化 <4px 不重绘；wave=True（鼠标移动）触发波纹扩散，
            # wave=False（滚动）仅跟随不扩散。
            if self._pos is not None:
                dx = abs(pos.x() - self._pos.x())
                dy = abs(pos.y() - self._pos.y())
                if dx < 4 and dy < 4:
                    return
            self._pos = pos
            if wave:
                self._pulse = 1.0
                self._pulse_timer.start()
            self.update()

        def paintEvent(self, ev):
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            # 半透磨砂底（圆角裁剪）：浅色磨砂（增强"磨砂感"而非"暗色感"），
            # 透明度适中让 Acrylic 毛玻璃透出，液态感更好。
            # 圆角与窗口圆角蒙版一致（18），磨砂盖满蒙版范围，杜绝边缘露出方形 Acrylic 方角。
            clip = QPainterPath()
            clip.addRoundedRect(QRectF(self.rect()), 18, 18)
            p.save()
            p.setClipPath(clip)
            p.fillRect(self.rect(), QColor(248, 250, 253, self._frost))
            p.restore()
            # 与主面板统一：不画顶部整片高光、不画整圈白色描边/受光边线（避免"长方形"分割感），
            # 对话框轮廓由 Acrylic 磨砂底 + 柔和光斑/底部微光呈现
            draw_glass_sheen(p, self.rect(), radius=18, top_gloss=False,
                             edge_glow=False)
            if self._pos is not None:
                draw_liquid_ripple(p, self.rect(), self._pos, pulse=self._pulse)
            p.end()

    try:
        dlg.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        overlay = _RippleOverlay(dlg, frost_alpha)
        dlg._glass_overlay = overlay
        # QObject 事件过滤器（纯函数会被 PyQt 拒绝 → 此前 Acrylic/波纹从未生效）
        _RippleGlassFilter(dlg, overlay)
        try:
            dlg.winId()   # 强制创建 HWND，确保 apply_acrylic 立即生效
            apply_acrylic(dlg, tint)
        except Exception:
            QTimer.singleShot(0, lambda: apply_acrylic(dlg, tint))
        # 圆角蒙版：剪掉方形窗角的 Acrylic 模糊方角，杜绝方块边角（主面板同款）
        try:
            apply_rounded_window(dlg, 18)
        except Exception:
            pass
        # 对话框内所有下拉弹出层同样叠加 Acrylic 液态玻璃（与主面板一致）
        try:
            from PyQt6.QtWidgets import QComboBox as _QCb
            for _cb in dlg.findChildren(_QCb):
                try:
                    glassify_combo_view(_cb)
                except Exception:
                    pass
        except Exception:
            pass
        return True
    except Exception:
        return False


class _PopupGlassFilter(_QObject):
    """下拉/菜单弹出窗口的 Acrylic 液态玻璃事件过滤器。
    PyQt6 的 installEventFilter 必须接收 QObject 实例；此前用纯 Python 函数会被
    PyQt 拒绝（TypeError 被 try/except 吞掉），导致 Acrylic 从未真正叠加——下拉
    菜单因此一直没有液态玻璃效果。本类以 QObject 实现，弹出层显示时真正启用 Acrylic。"""

    def __init__(self, view, tint=0x8E202024):
        super().__init__()
        self._tint = tint
        try:
            view.installEventFilter(self)
        except Exception:
            pass
        try:
            view._glass_popup_filter = self   # 防 GC 回收
        except Exception:
            pass

    def eventFilter(self, obj, ev):
        from PyQt6.QtCore import QEvent, Qt as _Qt
        if ev.type() == QEvent.Type.Show:
            try:
                def _tr(w):
                    try:
                        w.setAttribute(_Qt.WidgetAttribute.WA_TranslucentBackground, True)
                        w.setAutoFillBackground(False)
                    except Exception:
                        pass
                _tr(obj)
                try:
                    _tr(obj.viewport())
                except Exception:
                    pass
                popup = obj.window()
                if popup is not None and popup is not obj:
                    _tr(popup)
                    try:
                        popup.winId()
                        apply_acrylic(popup, self._tint)
                    except Exception:
                        pass
                    # 弹出窗口 Show 时 HWND/透明属性可能尚未稳定，延迟再补一次 Acrylic
                    try:
                        from PyQt6.QtCore import QTimer as _Timer
                        _Timer.singleShot(
                            40,
                            lambda w=popup: (_tr(w), apply_acrylic(w, self._tint)))
                    except Exception:
                        pass
            except Exception:
                pass
        return False


class _RippleGlassFilter(_QObject):
    """对话框鼠标液态波动过滤器（QObject 实现，见 _PopupGlassFilter 说明）。"""

    def __init__(self, dlg, overlay):
        super().__init__()
        self._dlg = dlg
        self._overlay = overlay
        try:
            dlg.installEventFilter(self)
        except Exception:
            pass

    def eventFilter(self, obj, ev):
        from PyQt6.QtCore import QEvent
        try:
            dlg, overlay = self._dlg, self._overlay
            if ev.type() == QEvent.Type.MouseMove:
                try:
                    gp = ev.globalPosition().toPoint()
                    local = dlg.mapFromGlobal(gp)
                    if dlg.rect().contains(local):
                        overlay.set_ripple(local, wave=True)
                    elif overlay._pos is not None:
                        overlay.set_ripple(None)
                except Exception:
                    pass
            elif ev.type() == QEvent.Type.Wheel:
                try:
                    gp = ev.globalPosition().toPoint()
                    local = dlg.mapFromGlobal(gp)
                    if dlg.rect().contains(local):
                        overlay.set_ripple(local)
                except Exception:
                    pass
            elif ev.type() == QEvent.Type.Resize and obj is dlg:
                overlay.setGeometry(dlg.rect())
                overlay.lower()
                # 尺寸变化 → 圆角蒙版随尺寸重算（最大化时自动清除）
                try:
                    apply_rounded_window(dlg, 18)
                except Exception:
                    pass
        except Exception:
            pass
        return False


class _AutoGlassFilter(_QObject):
    """应用级自动液态玻璃化：自定义 UI/UX 包激活时，任何 QDialog（含 QMessageBox/
    QInputDialog/QFileDialog 与自定义弹窗）显示即自动叠加 Acrylic + 液态波动，
    无需逐处手动调用 glassify_dialog——覆盖「很多弹窗仍是深/浅色」的遗漏。
    已玻璃化（_glass_overlay）或主面板（_glass_bg）跳过，避免重复。"""

    def __init__(self):
        super().__init__()
        self._busy = False

    def eventFilter(self, obj, ev):
        from PyQt6.QtCore import QEvent, QTimer
        try:
            if ev.type() == QEvent.Type.ToolTip:
                # 工具提示：QSS 的 QToolTip 选择器不匹配实际 qtooltip_label 控件，
                # 需在显示后强制给该控件自身样式（浅色液态玻璃底 + 黑字）。
                if is_custom_package_active():
                    QTimer.singleShot(0, self._fix_tooltip)
                    QTimer.singleShot(80, self._fix_tooltip)
                return False
            if ev.type() == QEvent.Type.Show and not self._busy:
                if not is_custom_package_active():
                    return False
                if getattr(obj, "_glass_overlay", None) is not None:
                    return False
                if getattr(obj, "_glass_bg", None) is not None:
                    return False
                from PyQt6.QtWidgets import QDialog, QMenu, QMessageBox
                # 原生系统对话框（QMessageBox/QInputDialog/QFileDialog 等）不做液态玻璃化：
                # 它们会被设成透明窗口叠加 Acrylic，透明区域在 Acrylic 未生效/抓图未合成时
                # 显示为"纯黑"，且窗口自身 QSS 浅色背景在透明窗口上不绘制。
                # 保持它们不透明，由应用级 QSS 渲染不透明浅色液态背景（黑字可读）。
                if isinstance(obj, QMessageBox):
                    return False
                # QDialog：整体液态玻璃（Acrylic + 液态波动）——仅自定义对话框
                if isinstance(obj, QDialog):
                    self._busy = True
                    try:
                        glassify_dialog(obj)
                    except Exception:
                        pass
                    finally:
                        self._busy = False
                # QMenu：实时毛玻璃 + 圆角液态（与主面板同风格，去不透明白底）
                elif isinstance(obj, QMenu):
                    self._busy = True
                    try:
                        # 菜单尺寸/HWND 弹出后才稳定 → 立即 + 延迟各修一次
                        glassify_menu(obj)
                        QTimer.singleShot(30, lambda m=obj: glassify_menu(m))
                    except Exception:
                        pass
                    finally:
                        self._busy = False
        except Exception:
            pass
        return False

    def _fix_tooltip(self):
        try:
            from PyQt6.QtWidgets import QApplication, QLabel
            app = QApplication.instance()
            if app is None:
                return
            for w in app.topLevelWidgets():
                if w.isVisible() and isinstance(w, QLabel) and w.objectName() == "qtooltip_label":
                    w.setStyleSheet(
                        "background:#F7F9FC; color:#0B0F14;"
                        "border:1px solid rgba(255,255,255,220);"
                        "border-radius:6px; padding:6px 10px; font-size:12px;")
                    w.update()
        except Exception:
            pass


_AUTO_GLASS_FILTER = None   # 全局单例，防重复安装


def ensure_auto_glass():
    """安装应用级自动液态玻璃过滤器（幂等）。仅在自定义包激活且 QApplication
    存在时生效；失败静默。"""
    global _AUTO_GLASS_FILTER
    try:
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            return
        if _AUTO_GLASS_FILTER is None:
            _AUTO_GLASS_FILTER = _AutoGlassFilter()
            app.installEventFilter(_AUTO_GLASS_FILTER)
    except Exception:
        pass


class _PopupGlassFixer(_QObject):
    """下拉弹出容器修正：QSS 会令弹出视图在每次打开时重建（combo.view() 是旧对象），
    因此把过滤器装在 QComboBox 上，在鼠标/键盘触发弹出后定位「当前」容器：
    - 去原生边框 + 加圆角蒙版（消除系统方框边框与方块边角）
    - 容器透明 + 叠加 Acrylic 真液态玻璃（实时透出/模糊桌面，观感更透、去实心感）
    弹出的矩形窗口本就透明（Acrylic），仅由圆角蒙版剪掉四角；列表文字可读性由
    QSS 的半透明渐变兜底（Acrylic 失败也会保持可读的不透明白底）。绝不强制覆盖
    为不透明白块或清空 QSS。"""
    # 液态玻璃合成色（AABBGGRR）：低 alpha 亮白——真毛玻璃透出更多桌面（透明/液态强），
    # 亮白调不至于把深色桌面渲染成一片黑
    TINT = 0x2AFFFFFF

    def __init__(self, combo, radius=12, tint=None):
        super().__init__()
        self._radius = max(0, int(radius))
        self._tint = tint if tint is not None else self.TINT
        try:
            combo.installEventFilter(self)
            combo._glass_popup_fixer = self   # 防 GC
        except Exception:
            pass

    def eventFilter(self, obj, ev):
        from PyQt6.QtCore import QEvent
        try:
            if ev.type() in (QEvent.Type.MouseButtonPress,
                             QEvent.Type.KeyPress,
                             QEvent.Type.FocusIn,
                             QEvent.Type.MouseButtonDblClick):
                # 弹出由鼠标/键盘触发，弹出发生在该事件之后 → 下一轮修正当前容器
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(0, lambda c=obj: self._fix_popup(c))
        except Exception:
            pass
        return False

    def _fix_popup(self, combo):
        try:
            view = combo.view()
            if view is None:
                return
            popup = view.window()
            if popup is None or popup is view:
                return
            self._fix(popup)
            # 弹出首帧 HWND/透明属性可能尚未稳定 → 延迟再各补一次，确保 Acrylic 与圆角落地
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(30, lambda p=popup: self._fix(p))
            QTimer.singleShot(120, lambda p=popup: self._fix(p))
        except Exception:
            pass

    def _fix(self, popup):
        """== 下拉弹出容器：稳定液态玻璃外观 ==
        不透明、不抓屏、不透明窗口（该系统上抓屏/Acrylic 会踩 变深/偏白/漂移 之一）：
        用不透明液态玻璃渐变（亮顶高光→明亮中段→底部微光，与主面板下拉同观感）呈现，
        稳定不深、鼠标扫过不叠加。去原生边框 + 圆角蒙版消除方块边角。"""
        try:
            from PyQt6.QtWidgets import QFrame
            popup.setFrameShape(QFrame.Shape.NoFrame)
        except Exception:
            pass
        try:
            from PyQt6.QtWidgets import QAbstractItemView
            for _v in popup.findChildren(QAbstractItemView):
                _v.setStyleSheet(
                    "QAbstractItemView { border: 1px solid rgba(255,255,255,220);"
                    " border-radius: 12px;"
                    " background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                    " stop:0 #FFFFFF, stop:0.15 #FAFBFE, stop:0.5 #EFF3FA,"
                    " stop:1 #F6F8FC); }"
                    "QAbstractItemView::item { padding: 7px 14px; border-radius: 7px; }"
                    "QAbstractItemView::item:hover { background: rgba(255,255,255,235); }"
                    "QAbstractItemView::item:selected { background: #FFFFFF; }")
                break
        except Exception:
            pass
        try:
            apply_rounded_window(popup, self._radius)
        except Exception:
            pass
        try:
            popup.update()
        except Exception:
            pass


def glassify_combo_view(combo, tint=0x2AFFFFFF, radius=12):
    """下拉弹出容器统一处理：本函数在 QComboBox 上装 _PopupGlassFixer，弹出时
    去原生边框 + 窗口透明 + 软件实时毛玻璃层（确定性可见，不依赖 Acrylic）+
    圆角蒙版。item 高亮不透明防叠加。失败静默（绝不影响功能）。"""
    if not is_custom_package_active():
        return False
    if combo is None:
        return False
    try:
        if getattr(combo, "_glass_popup_done", False):
            return True
        combo._glass_popup_done = True
        _PopupGlassFixer(combo, radius=radius, tint=tint)
        return True
    except Exception:
        return False


def apply_rounded_window(widget, radius=18):
    """给无边框玻璃窗口套圆角蒙版：把矩形窗口四角剪成圆角。
    优先使用 Windows 11 DWMWA_WINDOW_CORNER_PREFERENCE（官方圆角 API），
    降级使用 SetWindowRgn + CreateRoundRectRgn。失败静默，绝不影响功能。"""
    try:
        import ctypes
        from PyQt6.QtCore import QRectF
        maximized = bool(getattr(widget, "_glass_maximized", False)) \
            or widget.isMaximized() or widget.isFullScreen()
        if maximized:
            # 清除窗口区域设置，恢复矩形（最大化/全屏铺满屏幕无需圆角）
            user32 = ctypes.windll.user32
            hwnd = int(widget.winId())
            if hwnd != 0:
                user32.SetWindowRgn(hwnd, 0, True)
                # 同时关闭 DWM 圆角（Win11 DWMWA_WINDOW_CORNER_PREFERENCE）：
                # 仅清 SetWindowRgn 时 DWM 圆角仍会裁剪边缘，导致覆盖不全
                try:
                    dwmapi = ctypes.windll.dwmapi
                    corner = ctypes.c_int(1)   # WCP_DONOTROUND
                    dwmapi.DwmSetWindowAttribute(
                        hwnd, 33, ctypes.byref(corner), ctypes.sizeof(corner))
                except Exception:
                    pass
            return
        r = widget.rect()
        if r.width() <= 0 or r.height() <= 0:
            return
        # 确保窗口 HWND 已创建
        hwnd = int(widget.winId())
        if hwnd == 0:
            return
        # 方法 1：尝试使用 Windows 11 DWMWA_WINDOW_CORNER_PREFERENCE（官方圆角 API）
        dwmapi = ctypes.windll.dwmapi
        try:
            # DWMWA_WINDOW_CORNER_PREFERENCE = 33
            # WCP_DEFAULT = 0, WCP_DONOTROUND = 1, WCP_ROUND = 2, WCP_ROUNDSMALL = 3
            corner_pref = ctypes.c_int(2)  # WCP_ROUND
            result = dwmapi.DwmSetWindowAttribute(
                hwnd,
                33,  # DWMWA_WINDOW_CORNER_PREFERENCE
                ctypes.byref(corner_pref),
                ctypes.sizeof(corner_pref)
            )
            if result == 0:  # S_OK
                return
        except Exception:
            pass  # Windows 10 或更低版本不支持此 API
        # 方法 2：使用 CreateRoundRectRgn + SetWindowRgn
        gdi32 = ctypes.windll.gdi32
        user32 = ctypes.windll.user32
        # CreateRoundRectRgn(x1, y1, x2, y2, x3, y4)
        # x1,y1=左上角，x2,y2=右下角，x3,y4=椭圆宽度和高度
        rgn = gdi32.CreateRoundRectRgn(r.left(), r.top(), r.right(), r.bottom(), radius * 2, radius * 2)
        if rgn:
            user32.SetWindowRgn(hwnd, rgn, True)
    except Exception:
        pass


def glassify_menu(menu, radius=12, tint=0x34FFFFFF):
    """给 QMenu（右键/下拉菜单）加真实时毛玻璃 + 液态玻璃（同主面板风格）：
    - 菜单窗口透明 + Acrylic：窗口移动时背后内容实时映射，真毛玻璃
    - 去原生边框 + 圆角蒙版（消除方块边角）
    亮白 tint 让磨砂偏白透（深色桌面不会显黑）；item 高亮走不透明色避免透明窗口
    叠加泛白。菜单条目由自身 paintEvent 绘制，故不走子控件毛玻璃层。失败静默。"""
    if not is_custom_package_active():
        return False
    if menu is None:
        return False
    try:
        menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        menu.setAutoFillBackground(False)
    except Exception:
        pass
    try:
        from PyQt6.QtWidgets import QFrame
        menu.setFrameShape(QFrame.Shape.NoFrame)
    except Exception:
        pass
    try:
        menu.winId()
        apply_acrylic(menu, tint)
    except Exception:
        pass
    try:
        apply_rounded_window(menu, radius)
    except Exception:
        pass
    try:
        menu.update()
    except Exception:
        pass
    return True


def cleanup_custom_ui(panel, restore_window_flags=True):
    """清除自定义 UI/UX 包在面板上留下的运行时痕迹（切回默认/其他主题前调用），
    确保不残留液态玻璃等自定义外观：
    - 删除玻璃覆盖层（_glass_bg/_RippleOverlay 等直接子控件，不在布局内，retheme 清不到）
      并移除其挂到 QApplication 的事件过滤器；
    - 关闭透明背景（WA_TranslucentBackground）、清除圆角蒙版、关闭 Acrylic 毛玻璃；
    - restore_window_flags=True 时还原系统标题栏窗口标志（默认主题用系统标题栏）；
    - 还原被覆盖的 _top_bar_offset 等方法、复位玻璃最大化/拖动状态。
    失败静默，绝不影响功能。"""
    if panel is None:
        return
    from PyQt6.QtCore import Qt as _Qt
    from PyQt6.QtWidgets import QApplication as _App
    try:
        # 1) 删除玻璃覆盖层（_glass_bg/_glass_overlay 及 _GlassSurface/_RippleOverlay 等
        # 直接子控件，不在布局内，retheme 清不到）+ 移除其应用级事件过滤器。
        # 注意只清覆盖层，不碰 todos_win/git_win 等子面板（_retheme 单独处理）。
        _to_del = set()
        for _k in ("_glass_bg", "_glass_overlay"):
            _w = getattr(panel, _k, None)
            if _w is not None:
                _to_del.add(_w)
        for _ch in list(panel.children()):
            _n = type(_ch).__name__
            if "GlassSurface" in _n or "RippleOverlay" in _n:
                _to_del.add(_ch)
        for _w in _to_del:
            try:
                _app = _App.instance()
                if _app is not None:
                    _app.removeEventFilter(_w)
            except Exception:
                pass
            try:
                _w.hide()
                _w.deleteLater()
            except Exception:
                pass
        for _k in ("_glass_bg", "_glass_overlay"):
            try:
                setattr(panel, _k, None)
            except Exception:
                pass
        # 2) 关闭透明背景 + 恢复系统背景绘制
        #    WA_NoSystemBackground 也是液态玻璃设置的，漏关会导致切默认主题后面板
        #    背景（尤其布局 margins 区域）不绘制 → 四周透明，像残留液态玻璃元素。
        try:
            panel.setAttribute(_Qt.WidgetAttribute.WA_TranslucentBackground, False)
        except Exception:
            pass
        try:
            panel.setAttribute(_Qt.WidgetAttribute.WA_NoSystemBackground, False)
        except Exception:
            pass
        # 3) 清除圆角蒙版
        try:
            panel.clearMask()
        except Exception:
            pass
        # 4) 关闭 Acrylic 毛玻璃
        try:
            set_acrylic_enabled(panel, False)
        except Exception:
            pass
        # 5) 还原系统标题栏窗口标志（仅切到非自定义主题时）
        if restore_window_flags:
            try:
                _f = panel.windowFlags()
                if _f & _Qt.WindowType.FramelessWindowHint:
                    _f &= ~_Qt.WindowType.FramelessWindowHint
                    panel.setWindowFlags(
                        _f | _Qt.WindowType.Window
                        | _Qt.WindowType.WindowMinMaxButtonsHint
                        | _Qt.WindowType.WindowMaximizeButtonHint
                        | _Qt.WindowType.WindowMinimizeButtonHint)
            except Exception:
                pass
            # 默认主题：强制自动填充背景，让 QDialog 渐变铺满整个窗口（含布局
            # margins 区域），消除切默认后四周透明（液态玻璃残留感）
            try:
                panel.setAutoFillBackground(True)
            except Exception:
                pass
        # 6) 还原被覆盖的实例方法/属性
        try:
            if "_top_bar_offset" in vars(panel):
                delattr(panel, "_top_bar_offset")
        except Exception:
            pass
        # 7) 复位玻璃最大化/拖动状态（离开自定义主题时连最大化标志一起复位，
        #    自定义→自定义热更新时仅复位拖动标志，保留最大化几何）
        for _k in ("_glass_dragging", "_glass_drag_on"):
            try:
                if hasattr(panel, _k):
                    setattr(panel, _k, False)
            except Exception:
                pass
        if restore_window_flags:
            try:
                if hasattr(panel, "_glass_maximized"):
                    setattr(panel, "_glass_maximized", False)
            except Exception:
                pass
    except Exception:
        pass


def draw_liquid_ripple(painter, rect, pos, pulse=0.0):
    """在鼠标位置绘制液态波动（纯白、无色）：如液滴般中心亮、柔和向外衰减，并随
    鼠标移动轻微「呼吸」与形变（液态流动），而非离散的波纹环。
    pos 为鼠标在 rect 内的坐标（QPoint/QPointF，可超出 rect，函数会裁剪）。
    pulse 为液态相位（0.0~1.0，1→0 衰减）：控制光斑呼吸与折射高光漂移。"""
    from PyQt6.QtGui import QColor as _QC, QRadialGradient, QBrush, QPainter as _QP
    from PyQt6.QtCore import QPointF
    if rect is None or pos is None or rect.width() <= 0 or rect.height() <= 0:
        return
    cx = max(rect.left(), min(rect.right(), float(pos.x())))
    cy = max(rect.top(), min(rect.bottom(), float(pos.y())))
    r = max(rect.width(), rect.height()) * 0.8
    ph = max(0.0, min(1.0, float(pulse)))
    breath = 1.0 + 0.18 * ph                    # 呼吸：光斑随相位轻微胀大
    glow = 120 + int(44 * ph)                   # 呼吸：中心亮度随相位轻微提升
    painter.save()
    painter.setRenderHint(_QP.RenderHint.Antialiasing)
    # 主液滴：平滑径向渐变（无离散环），中心亮、向外柔和衰减，覆盖更大面积
    g = QRadialGradient(QPointF(cx, cy), r * breath)
    g.setColorAt(0.0, _QC(255, 255, 255, glow))
    g.setColorAt(0.08, _QC(255, 255, 255, int(glow * 0.6)))
    g.setColorAt(0.25, _QC(255, 255, 255, int(glow * 0.3)))
    g.setColorAt(0.5, _QC(255, 255, 255, int(glow * 0.12)))
    g.setColorAt(0.75, _QC(255, 255, 255, int(glow * 0.04)))
    g.setColorAt(1.0, _QC(255, 255, 255, 0))
    painter.fillRect(rect, QBrush(g))
    # 折射高光：略偏左上，随相位轻微漂移 = 液态流动
    drift = 0.14 + 0.07 * ph
    g2 = QRadialGradient(QPointF(cx - r * drift, cy - r * drift), r * 0.5)
    g2.setColorAt(0.0, _QC(255, 255, 255, 46))
    g2.setColorAt(0.5, _QC(255, 255, 255, 15))
    g2.setColorAt(1.0, _QC(255, 255, 255, 0))
    painter.fillRect(rect, QBrush(g2))
    painter.restore()


def draw_glass_sheen(painter, rect, radius=16, top_gloss=True, edge_glow=True):
    """绘制液态玻璃光泽层（iOS 26 风格）：顶部高光 + 底部微光 + 边缘反光描边 + 内缘高光 + 顶部受光边线，
    纯白无色，叠加在 Acrylic 毛玻璃之上，增强玻璃的反光质感、边缘光泽与静态流体质感（无对角反光）。
    top_gloss=False 时跳过顶部高光填充（对话框等小面板上顶部整片渐变像半透明长方形，可去掉）。
    edge_glow=False 时跳过整圈白色描边/内缘高光/顶部受光边线（主面板等大面积玻璃面上，
    白色圆角矩形描边会把整个面板框成一个显眼的"长方形"，造成顶部/聊天/底部区域的分割感）。"""
    from PyQt6.QtGui import (QColor as _QC, QLinearGradient, QRadialGradient,
                             QBrush, QPen, QPainter as _QP)
    from PyQt6.QtCore import QPointF, QRectF
    if rect is None or rect.width() <= 0 or rect.height() <= 0:
        return
    r = QRectF(rect)
    rad = max(1, int(radius))
    painter.save()
    painter.setRenderHint(_QP.RenderHint.Antialiasing)
    if top_gloss:
        # 1) 顶部高光（玻璃受光面）
        hi = QLinearGradient(0, rect.top(), 0, rect.top() + rect.height() * 0.45)
        hi.setColorAt(0.0, _QC(255, 255, 255, 84))
        hi.setColorAt(0.55, _QC(255, 255, 255, 20))
        hi.setColorAt(1.0, _QC(255, 255, 255, 0))
        painter.fillRect(rect, QBrush(hi))
    # 2) 角落光斑（左上/右下径向微光，模拟液体折光的汇聚点）
    for fx, fy, fr, fa in ((0.0, 0.0, 0.5, 30), (1.0, 1.0, 0.5, 24)):
        cs = QRadialGradient(QPointF(rect.left() + rect.width() * fx,
                                     rect.top() + rect.height() * fy),
                             rect.width() * fr)
        cs.setColorAt(0.0, _QC(255, 255, 255, fa))
        cs.setColorAt(1.0, _QC(255, 255, 255, 0))
        painter.fillRect(rect, QBrush(cs))
    # 3) 底部微光（玻璃底部折射）
    rg = QRadialGradient(QPointF(rect.center().x(), rect.bottom() + rect.height() * 0.1),
                         rect.width() * 0.8)
    rg.setColorAt(0.0, _QC(255, 255, 255, 40))
    rg.setColorAt(1.0, _QC(255, 255, 255, 0))
    painter.fillRect(rect, QBrush(rg))
    # 4) 边缘反光描边（玻璃边缘亮线，iOS 26 风格更锐利）
    if edge_glow:
        pen = QPen(_QC(255, 255, 255, 195))
        pen.setWidthF(1.2)
        painter.setPen(pen)
        painter.drawRoundedRect(r.adjusted(0.6, 0.6, -0.6, -0.6), rad, rad)
        # 5) 内缘高光（更细亮线，增强玻璃厚度感）
        pen2 = QPen(_QC(255, 255, 255, 90))
        pen2.setWidth(1)
        painter.setPen(pen2)
        painter.drawRoundedRect(r.adjusted(1.8, 1.8, -1.8, -1.8), rad, rad)
        # 6) 顶部受光边线（极细亮线，增强玻璃上缘受光光泽）
        pen3 = QPen(_QC(255, 255, 255, 135))
        pen3.setWidthF(1.0)
        painter.setPen(pen3)
        painter.drawLine(QPointF(rect.left() + rad * 0.5, rect.top() + 0.8),
                         QPointF(rect.right() - rad * 0.5, rect.top() + 0.8))
    painter.restore()


# ── 预览能力：Markdown / HTML / 文本 / Web 渲染预览面板 ─────────────────────
# 供 UI/UX 自定义包(build_ui)及自定义工具复用：用户可在此基础上任意深度自定义
# 预览面板外观/行为，也可自行构建新的预览窗。所有函数尽力而为、失败静默回退，
# 绝不因能力缺失（如未装 QtWebEngine）而影响宿主 UI。

def _escape_html(s) -> str:
    """HTML 转义（备用路径，预览渲染失败时保护原始文本）"""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _panel_theme_col(name, fallback):
    """读取当前主题色（agent_panel 模块运行时全局，随主题切换实时变化）"""
    try:
        from winapp_migrator.ui import agent_panel as _ap
        v = getattr(_ap, name, None)
        return v if v else fallback
    except Exception:
        return fallback


def render_markdown_html(md_text, fragment=False, css_extra=""):
    """把 Markdown 渲染成 HTML：复用聊天渲染器 `_md_to_html`，自动继承当前主题配色。
    - fragment=True：返回纯 body 片段（嵌入他人文档时用）；
    - css_extra：追加自定义 CSS（body/table/代码块等），供用户定制预览外观；
    - 渲染失败回退为 HTML 转义的纯文本，绝不抛错。"""
    frag = ""
    try:
        if md_text:
            from winapp_migrator.ui import agent_panel as _ap
            frag = _ap._md_to_html(str(md_text))
    except Exception:
        frag = _escape_html(md_text or "")
    if fragment:
        return frag
    text = _panel_theme_col("TEXT", "#e6e8ec")
    dim = _panel_theme_col("TEXT_DIM", "#9aa1aa")
    accent = _panel_theme_col("ACCENT", "#4c9dff")
    card = _panel_theme_col("CARD", "#23282e")
    code_bg = _panel_theme_col("CODE_BG", "#181b20")
    css = (
        "body{margin:12px;font-size:14px;line-height:1.65;"
        f"color:{text};background:{card};}}"
        f"h1,h2,h3,h4,h5,h6{{color:{text};}} p{{margin:5px 0;}}"
        f"pre{{background:{code_bg};border-radius:6px;padding:8px;}}"
        "code{font-family:Consolas,monospace;font-size:12px;}"
        f"a{{color:{accent};}} hr{{border:none;border-top:1px solid {dim};}}"
        f"blockquote{{border-left:3px solid {accent};margin:6px 0;padding:2px 12px;}}"
        "table{border-collapse:collapse;} th,td{border:1px solid "
        f"{dim};padding:4px 8px;}} ul{{padding-left:18px;}}"
        + (css_extra or "")
    )
    return ("<!DOCTYPE html><html><head><meta charset='utf-8'/><style>"
            + css + "</style></head><body>" + frag + "</body></html>")


def web_engine_available() -> bool:
    """QtWebEngine 是否可用（未安装 PyQt6-WebEngine 时返回 False，代码可据此降级）。

    注意：不能使用 importlib.util.find_spec 探测——PyInstaller 打包后（frozen 环境）
    模块在 PYZ 归档内，find_spec 会错误返回 None，导致已打包的 QtWebEngine 被误判为
    "未安装" 而降级。必须用 try-import 才能真正确认可导入。
    """
    try:
        from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
        return True
    except Exception:
        return False


def new_web_view(parent=None):
    """创建一个 QWebEngineView；未安装 QtWebEngine 时返回 None（不抛错）。
    适合需要真实浏览器内核（跑 JS/访问网页）的自定义场景。"""
    try:
        from PyQt6.QtWebEngineWidgets import QWebEngineView
        return QWebEngineView(parent)
    except Exception:
        return None


class _PreviewPanel(_QWidget):
    """通用预览面板：Markdown / HTML / 文本 / Web 统一预览。

    后端策略：
      - kind='web'      优先 QWebEngineView（真实浏览器，可 set_url 访问网页/跑 JS），
                        未安装 QtWebEngine 时自动回退 QTextBrowser（仅静态内容）。
      - 其他(markdown/html/text)  用 QTextBrowser（零额外依赖、随主题配色）。

    用法（build_ui / 自定义包内）：
        pv = PreviewPanel(kind='markdown', parent=self)
        pv.render_markdown('# Hello')        # Markdown → 本主题配色预览
        pv.set_html('<b>html</b>')           # 原始 HTML
        pv.set_text('plain')                 # 纯文本
        ok = pv.set_url('https://...')       # 仅 web 后端支持；返回是否成功
        layout.addWidget(pv)

    所有方法尽力而为、失败静默，绝不抛错影响宿主 UI。
    """

    def __init__(self, kind="markdown", parent=None):
        super().__init__(parent)
        from PyQt6.QtWidgets import QVBoxLayout
        self._kind = (kind or "markdown").lower()
        self._web = None      # QWebEngineView（web 且已装依赖时）
        self._tb = None       # QTextBrowser（轻量/回退后端）
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._ensure_backend()
        w = self._web if self._web is not None else self._tb
        if w is not None:
            self._lay.addWidget(w)

    def _ensure_backend(self):
        from PyQt6.QtWidgets import QTextBrowser
        if self._tb is not None or self._web is not None:
            return
        if self._kind == "web":
            try:
                from PyQt6.QtWebEngineWidgets import QWebEngineView
                self._web = QWebEngineView(self)
            except Exception:
                self._web = None
        if self._web is None:
            self._tb = QTextBrowser(self)
            self._tb.setOpenExternalLinks(True)

    def render_markdown(self, text):
        """Markdown → 当前主题配色的 HTML 预览（返回是否成功）"""
        try:
            return self.set_html(render_markdown_html(text or ""))
        except Exception:
            return False

    def set_html(self, html):
        """展示原始 HTML（返回是否成功）"""
        try:
            if self._web is not None:
                self._web.setHtml(html or "")
            else:
                self._tb.setHtml(html or "")
            return True
        except Exception:
            return False

    def set_text(self, text):
        """展示纯文本（返回是否成功；web 后端优先用 set_html/set_url）"""
        try:
            if self._tb is not None:
                self._tb.setPlainText(str(text or ""))
                return True
            return self.set_html(_escape_html(text or ""))
        except Exception:
            return False

    def set_url(self, url):
        """跳转真实网页（仅 web 后端 QWebEngineView 支持；未装依赖返回 False）"""
        try:
            if self._web is None or not url:
                return False
            from PyQt6.QtCore import QUrl
            self._web.setUrl(QUrl(str(url)))
            return True
        except Exception:
            return False


# 便于用户引用的公开别名
def create_preview_widget(kind="markdown", parent=None):
    """一键创建预览控件：`pv = create_preview_widget('markdown'); layout.addWidget(pv)`。
    kind ∈ {'markdown','html','text','web'}。失败返回 None（尽力而为）。"""
    try:
        return _PreviewPanel(kind=kind, parent=parent)
    except Exception:
        return None


def _build_namespace(panel) -> dict:
    """构建 build_ui/build_welcome 的执行命名空间：
    Qt 全组件（按钮/滑块/输入框/对话框/菜单/动画等）+ 主题/样式常量 + 工具函数，
    实现 AI 对任意 UI/UX 组件的深度自定义。"""
    from PyQt6 import QtWidgets, QtGui, QtCore
    ns = {"self": panel}
    # QtWidgets 常用组件（深度自定义：按钮形状/滑块/输入框/对话框/菜单/进度条等全部可用）
    for _n in ("QVBoxLayout", "QHBoxLayout", "QGridLayout", "QFormLayout",
               "QLabel", "QPushButton", "QToolButton", "QComboBox", "QScrollArea",
               "QStackedWidget", "QListWidget", "QWidget", "QSlider", "QCheckBox",
               "QRadioButton", "QLineEdit", "QFrame", "QMenu", "QTabWidget",
               "QSplitter", "QProgressBar", "QDialog", "QSizePolicy",
               "QGraphicsDropShadowEffect", "QGraphicsBlurEffect", "QGraphicsScene",
               "QGraphicsPixmapItem", "QButtonGroup", "QDialogButtonBox",
               "QStackedLayout", "QScrollBar", "QMessageBox", "QApplication"):
        ns[_n] = getattr(QtWidgets, _n, None)
    # QtGui 组件（字体/图标/颜色/渐变/画笔等）
    for _n in ("QPixmap", "QImage", "QFont", "QIcon", "QColor", "QPalette", "QPainter",
               "QPen", "QBrush", "QLinearGradient", "QRadialGradient", "QPainterPath",
               "QRegion", "QTextOption", "QTextCursor", "QTextDocument"):
        ns[_n] = getattr(QtGui, _n, None)
    # QtCore 组件（尺寸/坐标/定时器/动画/线程等）
    for _n in ("Qt", "QSize", "QPoint", "QPointF", "QRect", "QRectF", "QTimer", "QUrl",
               "QEvent", "QPropertyAnimation", "QVariantAnimation", "QEasingCurve",
               "QThread", "QAbstractAnimation"):
        ns[_n] = getattr(QtCore, _n, None)
    # 主题常量（覆盖为空值，稍后从 agent_panel 模块填充实际值）
    for _k in THEME_KEYS:
        ns[_k] = None
    # 样式常量
    for _k in ("_BTN_GHOST", "_BTN_COMPACT", "_BTN_GHOST_ACCENT", "_BTN_PRIMARY",
               "_BTN_DIM", "_QCOMBO", "_BTN_ICON", "_BTN_DANGER"):
        ns[_k] = None
    # 工具函数
    for _k in ("_line_icon", "_svg_icon", "_scrollbar_css", "_app_icon_path",
               "_std_icon", "_file_icon", "_file_thumb", "_redraw_titlebar",
               "_esc", "_warn_box", "attach_combo_checkmark",
               "render_markdown_html", "web_engine_available", "new_web_view",
               "create_preview_widget"):
        ns[_k] = None
    # 类引用
    for _k in ("_SessionStatusDelegate", "TodosWindow", "GitLogWindow",
               "WorktreeWindow", "CodePreviewWindow", "QueuePanel",
               "_DropTextEdit", "_ArrowComboBox", "FlowLayout", "_CodeHighlighter",
               "_GEAR_SVG", "_ComboCheckDelegate", "_PreviewPanel"):
        ns[_k] = None
    # 毛玻璃辅助（本模块提供，直接注入，供 AI 生成液体玻璃用）
    ns["_box_blur"] = _box_blur
    ns["_glass_fill"] = _glass_fill
    ns["draw_liquid_glass"] = draw_liquid_glass
    ns["grab_desktop_glass"] = grab_desktop_glass
    ns["draw_desktop_glass"] = draw_desktop_glass
    ns["apply_acrylic"] = apply_acrylic
    ns["apply_rounded_window"] = apply_rounded_window
    ns["cleanup_custom_ui"] = cleanup_custom_ui
    ns["draw_liquid_ripple"] = draw_liquid_ripple
    ns["draw_glass_sheen"] = draw_glass_sheen
    ns["glassify_dialog"] = glassify_dialog
    ns["glassify_combo_view"] = glassify_combo_view
    ns["glassify_menu"] = glassify_menu
    # 预览能力（本模块提供，直接注入）：Markdown/HTML/文本/Web 渲染预览面板
    ns["render_markdown_html"] = render_markdown_html
    ns["web_engine_available"] = web_engine_available
    ns["new_web_view"] = new_web_view
    ns["create_preview_widget"] = create_preview_widget
    ns["_PreviewPanel"] = _PreviewPanel
    ns["PreviewPanel"] = _PreviewPanel   # 公开别名（build_ui 里更顺手的写法）
    # 从 agent_panel 模块填充实际引用（缺失项保持 None，运行时报错会触发回退默认）
    import sys as _sys
    mod = _sys.modules.get("winapp_migrator.ui.agent_panel")
    if mod is not None:
        for _k in list(ns.keys()):
            if _k == "self":
                continue
            _val = getattr(mod, _k, None)
            if _val is not None:
                ns[_k] = _val
    return ns


def _validate_build_ui_code(code: str) -> tuple:
    """静态校验 AI 生成的 build_ui 代码：
    1) 语法检查；2) 未定义标识符检查（对照 build 命名空间 + builtins + 代码内绑定，
       提前发现拼写错误/使用了未注入的名称）；3) 必填 self 属性检查（调用
       build_default_ui 时视为默认微调，跳过属性检查）。
    返回 (ok, 错误信息或 '')。"""
    if not code or not code.strip():
        return (False, "build_ui 代码为空")
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return (False, f"语法错误: {e}")
    # 禁止 import 与危险能力（契约硬性要求；namespace 已注入全部所需名称）
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            return (False, "代码包含 import 语句（禁止）。应直接使用 build 命名空间注入的组件/常量/函数")
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id in ("exec", "eval", "open", "__import__", "compile",
                           "os", "subprocess", "sys"):
                return (False, f"使用了被禁止的能力: {node.id}")
    # 收集代码内绑定的名字（模块级函数/类/赋值/参数/导入/异常名/推导式变量）
    bound = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            bound.add(node.name)
            for a in list(node.args.args) + list(node.args.kwonlyargs):
                bound.add(a.arg)
            if node.args.vararg:
                bound.add(node.args.vararg.arg)
            if node.args.kwarg:
                bound.add(node.args.kwarg.arg)
        elif isinstance(node, ast.ClassDef):
            bound.add(node.name)   # 类定义：仅记类名（其方法/参数在 walk 中单独收集）
        elif isinstance(node, ast.Name):
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                bound.add(node.id)
        elif isinstance(node, ast.Import):
            for a in node.names:
                bound.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                bound.add(a.asname or a.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.comprehension):
            for tgt in ast.walk(node.target):
                if isinstance(tgt, ast.Name) and isinstance(tgt.ctx, ast.Store):
                    bound.add(tgt.id)
        elif isinstance(node, ast.Lambda):
            for a in list(node.args.args) + list(node.args.kwonlyargs):
                bound.add(a.arg)
            if node.args.vararg:
                bound.add(node.args.vararg.arg)
            if node.args.kwarg:
                bound.add(node.args.kwarg.arg)
    bound.add("self")
    # 可用的命名空间键 + builtins
    allowed = set(_build_namespace(None).keys())
    allowed.update(dir(builtins))
    # 仅检查 build_ui 函数体
    fn = next((n for n in tree.body
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name == "build_ui"), None)
    if fn is None:
        return (False, "代码未定义 build_ui(self) 函数")
    calls_default = False
    attrs = set()
    undefined = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id not in bound and node.id not in allowed and node.id not in undefined:
                undefined.append(node.id)
        elif isinstance(node, ast.Attribute):
            if (isinstance(node.value, ast.Name) and node.value.id == "self"
                    and isinstance(node.ctx, ast.Store)):
                attrs.add(node.attr)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "build_default_ui":
                calls_default = True
    if undefined:
        return (False, f"使用了未定义的标识符: {', '.join(sorted(undefined))}"
                       "（只能使用 build 命名空间提供的组件/常量/函数）")
    if not calls_default:
        missing = [a for a in _UIUX_REQUIRED_ATTRS if a not in attrs]
        if missing:
            return (False, f"缺少必填属性: self.{', '.join(missing)}")
    return (True, "")


def _safe_build_ui(panel, name: str) -> bool:
    """尝试从指定包加载并执行 build_ui(self)。
    成功返回 True，失败返回 False（面板已回退到默认）。"""
    pkg_dir = _UI_UX_DIR / name
    build_file = pkg_dir / "build_ui.py"
    if not build_file.is_file():
        return False

    code = build_file.read_text(encoding="utf-8", errors="replace")
    if not code.strip():
        return False

    namespace = _build_namespace(panel)

    try:
        exec(code, namespace)
        # 期待命名空间有 build_ui 函数
        fn = namespace.get("build_ui")
        if callable(fn):
            fn(panel)
            _set_last_error("")
            return True
        _set_last_error("build_ui.py 未定义 build_ui(self) 函数")
        return False
    except Exception as e:
        import logging
        msg = f"{type(e).__name__}: {e}"
        _set_last_error(msg)
        logging.getLogger("winapp_migrator.ui_ux").warning(
            "UI/UX 包 '%s' build_ui 执行失败: %s", name, msg)
        return False


def get_panel_qss(name: str) -> str:
    """读取包级样式表 panel.qss（深度自定义按钮形状/滑动条/输入框/对话框等组件样式）。
    无则返回空串。"""
    f = _UI_UX_DIR / name / "panel.qss"
    if not f.is_file():
        return ""
    return f.read_text(encoding="utf-8", errors="replace")


def set_panel_qss(name: str, qss: str) -> tuple:
    """写入包级样式表 panel.qss。返回 (ok, msg)。"""
    d = _UI_UX_DIR / name
    if not d.is_dir():
        return (False, f"包 '{name}' 不存在")
    try:
        (d / "panel.qss").write_text(qss or "", encoding="utf-8")
        return (True, "panel.qss 已更新")
    except OSError as e:
        return (False, f"写入失败: {e}")


def _substitute_theme_vars(qss: str) -> str:
    """把 panel.qss 中的主题变量替换为当前模块级颜色常量实际色值。

    Qt QSS 不支持 CSS 变量：此前契约/AI 提示引导在 panel.qss 里直接写变量名
    （如 `background: BG;`），导致这些声明被 Qt 静默丢弃、且不随深/浅主题换色。
    本函数在应用 QSS 前展开变量：
    - `${KEY}` 显式占位（推荐，无歧义）：如 `background: ${BG};`
    - 裸变量名（兼容旧包/示例）：整词边界匹配大写名；`_` 属词字符，故 `\bBG\b`
      不会命中 BG_BOTTOM 这类复合名里的 BG；小写 QSS 属性关键字（background/
      border/color 等）也不会被误伤。
    值取自当前 agent_panel 模块常量（已是当前深/浅主题的实际色值），
    替换后 QSS 随主题实时换色。"""
    import sys as _sys
    mod = _sys.modules.get("winapp_migrator.ui.agent_panel")
    if mod is None:
        return qss
    vals = {}
    for k in THEME_KEYS:
        v = getattr(mod, k, None)
        if isinstance(v, str) and v:
            vals[k] = v
    if not vals:
        return qss
    out = qss
    # 0) 资源目录绝对路径注入：@RES@ → 活跃包 resources/ 目录（QSS url() 可解析 SVG/PNG）
    try:
        _res_dir = _UI_UX_DIR / get_active_package() / "resources"
        if _res_dir.is_dir():
            out = out.replace("@RES@", str(_res_dir).replace("\\", "/"))
    except Exception:
        pass
    # 1) 显式占位 ${KEY}（先替换，避免与裸名逻辑互相干扰）
    for k, v in vals.items():
        out = out.replace("${" + k + "}", v)
    # 2) 裸变量名：按名字长度降序整词匹配（长名先换，避免子串被短名先行改写）；
    #    替换值用函数形式（避免色值中的反斜杠/$ 被当作替换引用解释），并在每轮
    #    显式绑定 _v = vals[k] 作为默认参捕获，防止闭包捕获上一轮遗留的循环变量。
    for k in sorted(vals, key=len, reverse=True):
        _v = vals[k]
        out = re.sub(r"\b" + re.escape(k) + r"\b", lambda _m, v=_v: v, out)
    return out


def apply_panel_qss(panel) -> bool:
    """把活跃包的 panel.qss 应用到面板（在 _panel_root_qss 之后，供 _retheme 调用）。
    应用前展开主题变量（${KEY} 占位/裸变量名 → 当前主题色值），随主题实时换色。
    无包 QSS 时返回 False。"""
    name = get_active_package()
    if not name or name == _DEFAULT_PACKAGE:
        return False
    qss = get_panel_qss(name)
    if not qss.strip():
        return False
    try:
        qss = _substitute_theme_vars(qss)
        root_qss = panel._panel_root_qss() if hasattr(panel, "_panel_root_qss") else ""
        panel.setStyleSheet(root_qss + "\n" + qss)
        return True
    except Exception:
        return False


def load_and_build_ui(panel) -> bool:
    """加载活跃 UI/UX 包并执行 build_ui。成功返回 True，失败回退默认并返回 False。"""
    name = get_active_package()
    if name == _DEFAULT_PACKAGE or not name:
        return False  # 使用默认内置 _build_ui
    ok = _safe_build_ui(panel, name)
    if not ok and name != _DEFAULT_PACKAGE:
        # 失败则回退到默认
        import logging
        logging.getLogger("winapp_migrator.ui_ux").warning(
            "UI/UX 包 '%s' 加载失败，回退到默认", name)
        _persist_active(_DEFAULT_PACKAGE)
    return ok


def load_and_build_welcome(panel):
    """加载活跃 UI/UX 包的 build_welcome 并返回其 QWidget；无自定义或失败返回 None。"""
    name = get_active_package()
    if name == _DEFAULT_PACKAGE or not name:
        return None
    code = get_welcome_code(name)
    if not code.strip():
        return None
    namespace = _build_namespace(panel)
    try:
        exec(code, namespace)
        fn = namespace.get("build_welcome")
        if callable(fn):
            w = fn(panel)
            if w is not None:
                return w
        return None
    except Exception:
        return None


# ── zip 资源包导入/导出（开发者共享 UI/UX） ─────────────────────

# 允许打包/导入的文件扩展名白名单（仅样式/资源/代码，拒绝可执行文件等风险类型）
_ZIP_ALLOWED_EXTS = {".py", ".json", ".qss", ".css", ".svg", ".png", ".jpg",
                     ".jpeg", ".ico", ".html", ".md", ".txt", ".ttf", ".otf"}

# 必须存在的核心文件
_ZIP_REQUIRED = ("ui_ux.json", "build_ui.py")


def export_package_zip(name: str, dest_path: str) -> tuple:
    """把 UI/UX 包导出为 zip 资源包（含 ui_ux.json/build_ui.py/build_welcome.py/
    panel.qss/theme 及 resources/ 资源文件）。
    若包声明了依赖（plugins/mcp），会把对应插件目录（plugins/<插件名>/...）与
    对应 MCP server 配置（mcp_servers.json）一并打包，对方导入即装即用。
    返回 (ok, zip_path 或错误)。"""
    import zipfile
    d = _UI_UX_DIR / name
    if not d.is_dir():
        return (False, f"包 '{name}' 不存在")
    if not (d / "build_ui.py").is_file():
        return (False, f"包 '{name}' 缺少 build_ui.py，无法导出")
    dest = Path(dest_path)
    if dest.is_dir():
        dest = dest / f"ui_ux_{name}.zip"
    meta = _read_meta(d) or {}
    plugins = [str(x).strip() for x in (meta.get("plugins") or []) if str(x).strip()]
    mcp = [str(x).strip() for x in (meta.get("mcp") or []) if str(x).strip()]
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(str(dest), "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(d.rglob("*")):
                if not f.is_file():
                    continue
                rel = f.relative_to(d).as_posix()
                if f.suffix.lower() not in _ZIP_ALLOWED_EXTS:
                    continue   # 跳过非白名单文件
                if rel == "mcp_servers.json" or rel.startswith("plugins/"):
                    continue   # 包内残留的依赖拷贝不重复打包，统一从全局源打包
                zf.write(str(f), rel)
            # 打包声明的依赖插件
            bundled_plugins = []
            if plugins:
                from winapp_migrator.core import agent_plugins
                pdir = agent_plugins.plugins_dir()
                for pn in plugins:
                    pd = pdir / pn
                    if not pd.is_dir() or not (pd / "plugin.json").is_file():
                        continue
                    for pf in sorted(pd.rglob("*")):
                        if not pf.is_file():
                            continue
                        if pf.suffix.lower() not in _ZIP_ALLOWED_EXTS:
                            continue
                        zf.write(str(pf), f"plugins/{pn}/{pf.relative_to(pd).as_posix()}")
                    bundled_plugins.append(pn)
            # 打包声明的 MCP server 配置（只包含声明的 name，避免泄露无关配置）
            bundled_mcp = []
            if mcp:
                from winapp_migrator.core import agent_skills
                servers = agent_skills.load_mcp_servers()
                selected = [s for s in servers
                            if str(s.get("name") or "").strip() in mcp]
                if selected:
                    zf.writestr("mcp_servers.json",
                                json.dumps(selected, ensure_ascii=False, indent=2))
                    bundled_mcp = [str(s.get("name") or "").strip() for s in selected]
        detail = []
        if bundled_plugins:
            detail.append(f"插件 {', '.join(bundled_plugins)}")
        if bundled_mcp:
            detail.append(f"MCP {', '.join(bundled_mcp)}")
        note = f"（含依赖：{'；'.join(detail)}）" if detail else ""
        return (True, str(dest) + note)
    except OSError as e:
        return (False, f"导出失败: {e}")


def import_package_zip(zip_path: str, overwrite: bool = True) -> tuple:
    """从 zip 资源包导入 UI/UX 包。校验包名安全、文件白名单、防 zip slip。
    返回 (ok, msg)。"""
    import zipfile
    src = Path(zip_path)
    if not src.is_file():
        return (False, f"找不到 zip 文件: {zip_path}")
    try:
        zf = zipfile.ZipFile(str(src))
    except Exception as e:
        return (False, f"无法打开 zip: {e}")
    with zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
        if not names:
            return (False, "zip 为空")
        # 确定包根目录：去掉顶层目录后的公共前缀
        parts = [n.split("/") for n in names]
        top = {p[0] for p in parts}
        strip = 1 if len(top) == 1 else 0   # 单顶层目录时剥掉该层
        rels = ["/".join(p[strip:]) for p in parts]
        # 防 zip slip：拒绝含 .. / 绝对路径 / 反斜杠穿越
        for rel in rels:
            if not rel or rel.startswith("/") or "\\" in rel:
                return (False, "zip 内含非法路径")
            norm = Path(rel)
            if ".." in norm.parts:
                return (False, "zip 内含路径穿越（..），已拒绝")
        # 校验必须文件存在
        relset = set(rels)
        for req in _ZIP_REQUIRED:
            if req not in relset:
                return (False, f"zip 缺少必需文件: {req}（需含 ui_ux.json 与 build_ui.py）")
        # 读取元数据确定包名
        meta_raw = zf.read("/".join([top.pop()] + list(_ZIP_REQUIRED)[0].split("/"))) \
            if strip else zf.read(_ZIP_REQUIRED[0])
        try:
            meta = json.loads(meta_raw.decode("utf-8", errors="replace"))
        except Exception:
            meta = {}
        name = str(meta.get("name") or "").strip() if isinstance(meta, dict) else ""
        if not name:
            return (False, "ui_ux.json 缺少包名 name")
        if not re.match(r'^[a-zA-Z0-9_-]+$', name):
            return (False, f"包名 '{name}' 含非法字符（仅字母数字下划线连字符）")
        if name == _DEFAULT_PACKAGE:
            return (False, f"'{_DEFAULT_PACKAGE}' 是保留内置包名，不可导入覆盖")
        # 文件扩展名白名单
        for rel in rels:
            if rel == "ui_ux.json" or rel == "build_ui.py":
                continue
            ext = Path(rel).suffix.lower()
            if rel.endswith("/"):
                continue
            if ext not in _ZIP_ALLOWED_EXTS:
                return (False, f"zip 内含不允许的文件类型: {rel}（白名单 {', '.join(sorted(_ZIP_ALLOWED_EXTS))}）")
        # 写入目标目录（覆盖需确认由调用方处理；同名存在且不覆盖则拒绝）
        target = _UI_UX_DIR / name
        if target.is_dir() and not overwrite:
            return (False, f"包 '{name}' 已存在；如需覆盖请传 overwrite=true")
        _ensure_dir()
        if target.is_dir():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        for i, rel in enumerate(rels):
            src_in_zip = "/".join(parts[i])   # 原始 zip 内路径
            data = zf.read(src_in_zip)
            out = target / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(data)
    # 强制非内置标记
    try:
        m = _read_meta(target) or {}
        m["is_builtin"] = False
        m["name"] = name
        _write_meta(target, m)
    except Exception:
        pass
    # 恢复依赖：plugins/ 目录拷贝到全局插件目录并登记；mcp_servers.json 合并进全局配置
    imported_plugins = []
    imported_mcp = []
    p_root = target / "plugins"
    if p_root.is_dir():
        from winapp_migrator.core import agent_plugins
        g_root = agent_plugins.plugins_dir()
        for pd in sorted(p_root.iterdir()):
            if not pd.is_dir() or not (pd / "plugin.json").is_file():
                continue
            pn = pd.name
            gd = g_root / pn
            if gd.is_dir():
                shutil.rmtree(gd)
            try:
                shutil.copytree(pd, gd)
                imported_plugins.append(pn)
            except OSError:
                continue
        shutil.rmtree(p_root, ignore_errors=True)
        # 登记插件（MCP + 技能）
        for pn in imported_plugins:
            try:
                agent_plugins._register_shipped_plugin(pn)
            except Exception:
                pass
    mcp_file = target / "mcp_servers.json"
    if mcp_file.is_file():
        try:
            from winapp_migrator.core import agent_skills
            servers = agent_skills.load_mcp_servers()
            names = {str(s.get("name") or "").strip() for s in servers}
            bundled = json.loads(mcp_file.read_text(encoding="utf-8", errors="replace"))
            if isinstance(bundled, list):
                for s in bundled:
                    if not isinstance(s, dict):
                        continue
                    sn = str(s.get("name") or "").strip()
                    if sn and sn not in names:
                        servers.append(s)
                        names.add(sn)
                        imported_mcp.append(sn)
                if imported_mcp:
                    agent_skills.save_mcp_servers(servers)
        except Exception:
            pass
        mcp_file.unlink(missing_ok=True)
    detail = []
    if imported_plugins:
        detail.append(f"插件 {', '.join(imported_plugins)}")
    if imported_mcp:
        detail.append(f"MCP {', '.join(imported_mcp)}")
    note = f"（依赖已安装：{'；'.join(detail)}）" if detail else ""
    return (True, f"UI/UX 包 '{name}' 已导入（来自 {src.name}）{note}")


# ── AI 自然语言生成 UI/UX 包 ────────────────────────────────────

_UIUX_CONTRACT = """UI/UX 包契约：
一、构建函数
- build_ui(self) 是必须的构建函数，参数 self 为 AgentPanel 实例。
- **默认必须完整自定义**：build_ui 自建全部契约控件与侧边窗口，且正确连接已有方法，
  否则面板会报错回退默认。必须创建：self._root_lay / self.title / self.session_combo /
  self.new_btn / self.settings_btn / self.token_label / self.wf_label / self.msg_area /
  self.msg_lay / self._welcome_page / self.msg_stack / self.cmd_list / self.input /
  self.attach_btn / self.optimize_btn / self.model_combo / self.action_btn /
  self.todos_win / self.git_win / self.wt_win / self.code_win，并连接 self._new_session /
  self._open_settings / self._on_session_selected / self._send / self._on_action_clicked /
  self._pick_attachments / self._on_optimize_clicked / self._on_model_combo /
  self._on_cmd_selected / self._clear_chat 等已有方法。
- 仅当用户明确说"在默认基础上微调"时才允许 build_ui 先调用 self.build_default_ui() 再改样式。
- **PyQt6 环境（禁止 import 与 PyQt5 API）**：本项目基于 PyQt6，build_ui 的命名空间已注入
  全部所需组件/常量/函数，**禁止 import/from**。禁止使用 PyQt5 专属写法，例如：
  QFont.Bold → QFont.Weight.Bold、Qt.AlignLeft → Qt.AlignmentFlag.AlignLeft、
  Qt.Checked → Qt.CheckState.Checked、QFontMetrics.width → horizontalAdvance。
  字体/图标/颜色直接用注入的 QFont/QIcon/QColor，颜色一律用主题常量。

二、**必须更改全部 UI/UX 组件（硬性要求）**
- 生成的包必须**深度自定义每一个可见组件**，禁止任何组件保留默认/通用样式：
  - 按钮：形状/圆角/渐变/内边距/字体/悬停/按下/禁用态；
  - 输入框（self.input）：边框/圆角/聚焦高亮/占位符颜色/行高；
  - 下拉框（session_combo/model_combo）：下拉箭头/列表项高亮/滚动条；
  - 滚动条（QScrollBar）：轨道/滑块圆角宽度，随主题换色；
  - 菜单（右键菜单/组合框下拉）、Tooltip、对话框（QMessageBox/QInputDialog）外观；
  - 图标（_line_icon/_svg_icon）尺寸与颜色、字体（标题/正文/辅助）大小粗细；
  - 间距/留白/边框/阴影（QGraphicsDropShadowEffect）、消息气泡、标签、列表项选中态。
- 实现方式二选一（推荐同时用）：
  a) build_ui 内用 setStyleSheet 定制每个控件；
  b) **包根目录 panel.qss（QSS 样式表，必须提供）** 统一覆盖上述全部组件类型，
     面板加载时自动叠加生效。panel.qss 里必须为按钮/输入框/下拉框/滚动条/菜单/工具提示/
     对话框/列表等组件都写自定义样式，不得空泛。
- **panel.qss 中颜色请用主题变量占位 ${KEY}（如 background: ${BG};），应用时自动替换为
  当前主题色值并随深浅主题换色；也可写裸变量名（BG/TEXT/ACCENT 等）。禁止在 QSS 里写
  ${KEY} 之外的裸花括号占位（Qt QSS 不支持 CSS 变量，必须靠占位替换）。
- **必须提供自定义主题色板 theme（dark/light 两套都要）**：ui_ux.json 的 theme 字段为
  {"dark": {常量名: 色值, ...}, "light": {常量名: 色值, ...}}，键必须是下面列出的主题常量名。
- **必须同时考虑深色/浅色**：所有颜色用主题常量（BG/BG_BOTTOM/PANEL/CARD/BORDER/BORDER_SOFT/
  TEXT/TEXT_DIM/ACCENT/ACCENT_HOVER/LINK_COLOR/USER_BG/AI_BG/OK/WARN/ERR/HOVER/CODE_ACCENT）
  而非硬编码色值，这样面板自动跟随全局深/浅主题。

三、**4 个子面板完全自定义（布局/背景/位置）**
- 除主 AI 面板外还有 4 个独立悬浮子窗口：任务清单 self.todos_win、Git 面板 self.git_win、
  工作树 self.wt_win、代码预览 self.code_win。它们可以完全自定义：
  - 替换类：可用注入的类（TodosWindow/GitLogWindow/WorktreeWindow/CodePreviewWindow），
    也可自己定义 QWidget 子类替换，布局/背景（setStyleSheet/QSS）/控件全部由你决定；
  - 位置/大小：默认由守卫自动停靠（todos/git/wt 堆叠在主面板左侧，code 贴主面板右侧）。
    若想自由摆放，对窗口置 `win._ai_managed = True` 并自行 move()/resize()/show()/hide()：
    守卫不再重定位/改大小/按设置开关隐藏；主面板最小化时子窗口仍随其隐藏，恢复后按隐藏前
    状态重新显示。不设置 _ai_managed 则保持自动停靠；
  - 显隐：不想显示某个子面板可直接不 show()（或 _ai_managed 后由 AI 控制）。
- 子面板数据接口（替换类时按需实现，面板会自动调用）：
  - todos_win.update_todos(list)：刷新任务清单（AI 使用任务工具时自动调用）；
  - git_win.refresh() 与 git_win.list（QListWidget）：刷新分支/提交（默认由周期刷新自动
    调用；_ai_managed 后不再自动刷新，需 AI 自行用 QTimer 刷新或直接调用）；
  - wt_win.refresh()：刷新文件树；双击文件时调用主面板 self._open_code_preview(path)
    （窗口内需保存主面板引用，如 self._panel = parent），或复用内置 file_open_requested 信号；
  - code_win.show_file(path)：预览文件内容（双击工作树文件时自动调用）。
- 4 个子面板同样必须深度自定义（背景/边框/列表项/滚动条等），颜色用主题常量，深色/浅色
  两套都要适配。

四、深度自定义组件命名空间（build_ui 内可用）
布局（QVBoxLayout/QHBoxLayout/QGridLayout/QFormLayout/QStackedLayout）、控件（QLabel/
QPushButton/QToolButton/QComboBox/QScrollArea/QStackedWidget/QListWidget/QWidget/QSlider/
QCheckBox/QRadioButton/QLineEdit/QFrame/QMenu/QTabWidget/QSplitter/QProgressBar/QDialog/
QScrollBar/QMessageBox/QButtonGroup/QDialogButtonBox）、绘图（QPixmap/QFont/QIcon/QColor/
QPalette/QPainter/QPen/QBrush/QLinearGradient/QRadialGradient/QTextOption）、动画线程
（QTimer/QPropertyAnimation/QVariantAnimation/QEasingCurve/QThread）、阴影特效
（QGraphicsDropShadowEffect）、主题常量、样式常量（_BTN_GHOST/_BTN_PRIMARY/_QCOMBO 等）、
工具函数（_line_icon/_svg_icon/_scrollbar_css/_file_icon 等）、
**token / 上下文统计浮层**：self.open_token_stats() / self.close_token_stats() /
self.toggle_token_stats() —— 弹出服务商上游用量浮层（上下文占用进度条 + 压缩阈值/硬上限
刻度 + 缓存命中率 + 累计消耗），默认界面的 chart 图标按钮即绑此入口；自定义界面可把它接到
自己的按钮/菜单上，无需自建统计 UI。
**按钮注册接口**：self.register_btn(btn_id, text=..., callback=..., tooltip=..., order=0) /
self.unregister_btn(btn_id) / self.list_registered_btns()。
注册的自定义按钮恒渲染在面板顶部按钮栏，与 UI/UX 解耦：不管当前/以后切换任何包、甚至
完全重构界面，按钮都保持可见且回调可用（回调直接绑定，AI 运行中也可用工具
register_panel_btn op=register 注册）。
- **预览能力（Markdown/HTML/Web 渲染面板）**：build_ui 内可用
  `pv = create_preview_widget(kind='markdown'|'html'|'text'|'web')` 或
  `pv = PreviewPanel(kind=..., parent=self)` 一键创建可自定义预览面板，再将其加入任意布局：
  - pv.render_markdown(text)：把 Markdown 渲染成**当前主题配色**的预览；
  - pv.set_html(html) / pv.set_text(text)：原始 HTML / 纯文本；
  - pv.set_url(url)：跳转真实网页（仅 web 后端，需已安装 QtWebEngine；否则自动回退静态渲染）；
  - 也可用 `render_markdown_html(md, fragment=..., css_extra=...)` 直接在别处取 HTML 文档串，
    `web_engine_available()` / `new_web_view()` 判断/创建浏览器内核。
  预览面板与其它侧窗一样可被完全深度自定义（背景/边框/列表/滚动条等，用主题常量）。

五、依赖打包（插件 / MCP server）
- ui_ux.json 可声明依赖，导出 zip 时会连同打包，对方导入即装即用：
  - "plugins": ["插件名1", "插件名2"]  依赖的插件（~/.winapp_migrator/plugins/ 下的插件名）；
  - "mcp": ["MCP名1", "MCP名2"]       依赖的 MCP server（mcp_servers.json 中的 name）。
- 若生成的面板用到了某个插件提供的 MCP 工具或技能，必须在 plugins/mcp 中声明。

六、其他
- build_welcome(self) 可选，返回一个欢迎页 QWidget。
- 禁止使用 os/subprocess/import/exec/eval/open 等危险能力。
"""

_UIUX_EXAMPLE = """完整自定义示例（默认必须此风格，禁止调用 build_default_ui）：
def build_ui(self):
    root = QVBoxLayout(self)
    root.setContentsMargins(16, 14, 16, 14)
    root.setSpacing(10)
    self._root_lay = root
    # 侧边窗口（工作树/git/任务清单/代码预览）必须创建
    self.todos_win = TodosWindow(self)
    self.git_win = GitLogWindow(self)
    self.wt_win = WorktreeWindow(self)
    self.wt_win.file_open_requested.connect(self._open_code_preview)
    self.code_win = CodePreviewWindow(self)
    # 顶栏：标题 + 会话 + 新对话 + 设置（颜色一律用主题常量，深色浅色自适应）
    top = QHBoxLayout()
    self.title = QLabel("CUSTOM")
    self.title.setStyleSheet(f"color: {ACCENT}; font-size: 18px; font-weight: 800;")
    top.addWidget(self.title)
    self.session_combo = QComboBox()
    self.session_combo.setMinimumWidth(150)
    self.session_combo.setItemDelegate(_SessionStatusDelegate(self))
    self.session_combo.currentIndexChanged.connect(self._on_session_selected)
    top.addWidget(self.session_combo)
    self.new_btn = QPushButton("+")
    self.new_btn.clicked.connect(self._new_session)
    self.new_btn.setStyleSheet(_BTN_ICON)
    top.addWidget(self.new_btn)
    self.settings_btn = QPushButton("设置")
    self.settings_btn.clicked.connect(self._open_settings)
    self.settings_btn.setStyleSheet(_BTN_GHOST)
    top.addWidget(self.settings_btn)
    top.addStretch(1)
    self.token_label = QLabel("0 tk")
    self.token_label.setStyleSheet(f"color: {TEXT_DIM}; font-size: 12px;")
    top.addWidget(self.token_label)
    self.wf_label = QLabel("")
    self.wf_label.setStyleSheet(f"color: {TEXT_DIM}; font-size: 12px;")
    top.addWidget(self.wf_label)
    root.addLayout(top)
    # 聊天区
    self.msg_area = QScrollArea()
    self.msg_area.setWidgetResizable(True)
    self.msg_area.setStyleSheet(
        "QScrollArea { background: transparent; border: none; }" + _scrollbar_css(8, 4, both=True))
    container = QWidget()
    container.setStyleSheet("background: transparent;")
    self.msg_lay = QVBoxLayout(container)
    self.msg_lay.setContentsMargins(6, 6, 6, 6)
    self.msg_lay.addStretch(1)
    self.msg_area.setWidget(container)
    self._welcome_page = self._build_welcome()
    self.msg_stack = QStackedWidget()
    self.msg_stack.addWidget(self.msg_area)
    self.msg_stack.addWidget(self._welcome_page)
    root.addWidget(self.msg_stack, 1)
    self.cmd_list = QListWidget()
    self.cmd_list.hide()
    root.addWidget(self.cmd_list)
    # 输入行
    row = QHBoxLayout()
    self.input = _DropTextEdit()
    self.input.setPlaceholderText("给我描述你的任务吧…")
    self.input.submit.connect(self._send)
    self.input.setStyleSheet(
        f"QPlainTextEdit {{ background: {PANEL}; color: {TEXT}; border: 1px solid {BORDER};"
        "border-radius: 10px; padding: 5px 10px; font-size: 14px; }}"
        f"QPlainTextEdit:focus {{ border: 1px solid {ACCENT}; }}")
    row.addWidget(self.input, 1)
    self.attach_btn = QPushButton("+")
    self.attach_btn.clicked.connect(self._pick_attachments)
    self.attach_btn.setStyleSheet(_BTN_ICON)
    row.addWidget(self.attach_btn)
    self.optimize_btn = QPushButton("优化")
    self.optimize_btn.clicked.connect(self._on_optimize_clicked)
    self.optimize_btn.setStyleSheet(_BTN_GHOST)
    row.addWidget(self.optimize_btn)
    self.model_combo = _ArrowComboBox()
    self.model_combo.setMinimumWidth(150)
    self.model_combo.setStyleSheet(_QCOMBO)
    self.model_combo.currentIndexChanged.connect(self._on_model_combo)
    row.addWidget(self.model_combo)
    self.action_btn = QPushButton("发送")
    self.action_btn.setStyleSheet(_BTN_PRIMARY)
    self.action_btn.clicked.connect(self._on_action_clicked)
    row.addWidget(self.action_btn)
    root.addLayout(row)
    return self

子面板完全自定义示例（放在 build_ui 内；默认可自动停靠，_ai_managed=True 可自由摆放）：
# 方案 A：继承内置类深度改版（推荐，数据接口自动保留）
class _MyGit(GitLogWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("myGitWin")
        self.setStyleSheet(f"QWidget#myGitWin {{ background: {CARD}; "
                           f"border: 2px solid {ACCENT}; border-radius: 10px; }}")
        self.setFixedWidth(320)          # 自定义宽度
        self._ai_managed = True          # 完全接管：守卫不再重定位/改大小
        self.move(30, 80)                # 自由摆放位置
        self.show()

self.git_win = _MyGit(self)

# 方案 B：全新自定义窗口（实现所需接口即可）
class _MyTodos(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._panel = parent             # 保存主面板引用，供触发 _open_code_preview 等
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setObjectName("myTodos")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"QWidget#myTodos {{ background: {CARD}; }}")
        self.setFixedWidth(240)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        self._list = QListWidget()
        self._list.setStyleSheet("QListWidget { border: none; }")
        lay.addWidget(self._list)
    def update_todos(self, todos: list):   # 面板自动调用刷新任务清单
        self._list.clear()
        for t in todos:
            self._list.addItem(str(t))

self.todos_win = _MyTodos(self)
self.todos_win._ai_managed = True          # 自由摆放，守卫不干预
self.todos_win.move(60, 150)
self.todos_win.show()

主题色板示例（JSON，与代码一起输出到 theme 字段，两套都要）：
"theme": {
  "dark": {"BG": "#0B0F1A", "BG_BOTTOM": "#0B0F1A", "PANEL": "#12182B",
           "CARD": "#1A2138", "BORDER": "#000000", "BORDER_SOFT": "#2A3350",
           "TEXT": "#E8EAF6", "TEXT_DIM": "#9AA3C0", "ACCENT": "#6D5DF6",
           "ACCENT_HOVER": "#8B7CF8", "HOVER": "#232C48", "USER_BG": "#6D5DF6"},
  "light": {"BG": "#F6F5FF", "BG_BOTTOM": "#EFEDFC", "PANEL": "#FFFFFF",
            "CARD": "#FFFFFF", "BORDER": "#E0DDF2", "BORDER_SOFT": "#D3CFEB",
            "TEXT": "#1E1B36", "TEXT_DIM": "#6B6790", "ACCENT": "#6D5DF6",
            "ACCENT_HOVER": "#5A4BE0", "HOVER": "#EDEBFC", "USER_BG": "#6D5DF6"}
}

panel.qss 样式表示例（必须提供，统一深度定制全部组件；颜色推荐用主题变量占位
${KEY}（如 ${BG}），应用时自动替换为当前主题色值并随深浅主题换色；也可写裸变量名 BG）：
QPushButton { border-radius: 16px; padding: 8px 20px; font-weight: 700; }
QPushButton:hover { background: ${ACCENT_HOVER}; }
QPushButton#actionBtn { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
    stop:0 ${ACCENT}, stop:1 #9A7BFF); border-radius: 20px; font-weight: 800; }
QLineEdit, QPlainTextEdit { border-radius: 12px; padding: 8px 14px; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView { selection-background-color: ${HOVER}; }
QScrollBar:vertical { background: ${BG}; width: 8px; }
QScrollBar::handle:vertical { background: ${BORDER}; border-radius: 4px; }
QMenu { border-radius: 10px; padding: 6px; }
QMenu::item { padding: 6px 18px; border-radius: 6px; }
QMenu::item:selected { background: ${HOVER}; }
QToolTip { background: ${PANEL}; color: ${TEXT}; border: 1px solid ${BORDER};
    border-radius: 6px; padding: 4px 8px; }
QListWidget::item { border-radius: 6px; }
QListWidget::item:selected { background: ${HOVER}; color: ${ACCENT_HOVER}; }

依赖声明示例（JSON 顶层字段，导出时连同打包）：
"plugins": ["frontend-design-pro"],
"mcp": ["local"]

微调示例（仅当用户明确要求"在默认基础上微调"）：
def build_ui(self):
    self.build_default_ui()
    self.title.setText("MY AGENT")
    self.input.setPlaceholderText("给我描述你的任务吧…")
    return self
"""


def _report(on_status, pct: int, msg: str):
    """安全调用进度回调"""
    if callable(on_status):
        try:
            on_status(int(pct), msg)
        except Exception:
            pass


def _extract_json_from_llm(text: str):
    """从 LLM 输出中稳健提取 JSON 对象：
    1) 剥掉 ```json ... ``` 代码块；2) 全文本直接解析；3) 平衡括号扫描
    （跳过字符串字面量内的 { }，定位最外层对象，容忍前后杂文/截断后的完整对象）。
    失败返回 None。"""
    if not text:
        return None
    # 1) markdown 代码块
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        cand = m.group(1).strip()
        try:
            return json.loads(cand)
        except (json.JSONDecodeError, ValueError):
            pass
    # 2) 全文本
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    # 3) 平衡括号扫描（尊重字符串，找最外层 { ... }）
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except (json.JSONDecodeError, ValueError):
                    return None
    return None


def create_package_from_nl(description: str, on_status=None) -> tuple:
    """自然语言描述 → 调用 LLM 生成 UI/UX 包（真实 API）。
    返回 (ok, msg)。"""
    from winapp_migrator.core import agent_llm
    _report(on_status, 5, "正在分析 UI/UX 需求…")
    cfg = agent_llm.load_model_config()
    client = agent_llm.LLMClient(cfg.get("base_url"), cfg.get("api_key"),
                                 cfg.get("model") or agent_llm.DEFAULT_MODEL)
    sys_p = (
        "你是 Cordis UI/UX 设计专家。根据用户自然语言描述，为 zhuzhu Copilot 的 AI 面板"
        "设计一个自定义 UI/UX 包，输出严格 JSON（不要 markdown 代码块包裹）：\n"
        "{\n"
        '  "name": "包名（英文，字母数字下划线连字符，≤40字符）",\n'
        '  "description": "一句话说明风格（中文）",\n'
        '  "build_ui": "Python 代码（def build_ui(self): …，必须完整自定义，禁止调用 build_default_ui）",\n'
        '  "build_welcome": "可选，Python 代码（def build_welcome(self): 返回 QWidget）",\n'
        '  "theme": {"dark": {"BG": "#000000", "TEXT": "#F5F5F5", "ACCENT": "#7C3AED", ...},'
        ' "light": {"BG": "#F4F6FA", "TEXT": "#1E293B", "ACCENT": "#7C3AED", ...}}'
        '（自定义主题色板，两套都要，键必须是色板常量名，见契约）\n'
        '  "qss": "panel.qss 样式表内容（QSS，必须提供，深度定制全部组件外观：按钮形状/圆角/渐变、'
        '字体、图标、滑动条、输入框、下拉框、菜单、滚动条、Tooltip、对话框等）",\n'
        '  "plugins": ["依赖的插件名", ...],  可选，面板用到的插件\n'
        '  "mcp": ["依赖的MCP server名", ...],  可选，面板用到的 MCP server\n'
        "}\n"
        f"{_UIUX_CONTRACT}\n"
        f"{_UIUX_EXAMPLE}\n"
        "硬性要求：\n"
        "1. build_ui 必须**完整自定义**整个面板（创建全部契约控件与侧边窗口并连接方法），"
        "禁止调用 self.build_default_ui()；除非用户明确说\"在默认基础上微调\"。\n"
        "2. **必须更改全部 UI/UX 组件**：所有按钮/输入框/下拉框/滚动条/菜单/Tooltip/对话框/图标/"
        "字体/间距/边框/阴影/列表选中态都要深度自定义样式，禁止任何组件保留默认外观。\n"
        "3. **必须提供 qss（panel.qss）**：覆盖按钮/输入框/下拉框/滚动条/菜单/Tooltip/对话框/列表"
        "等全部组件类型的自定义样式；颜色推荐用主题变量占位 ${KEY}（如 ${BG}，应用时自动替换为"
        "当前主题色值并随深浅主题换色），也可写裸变量名（BG/TEXT/ACCENT 等）或协调色值。\n"
        "4. **必须提供 theme 的 dark/light 两套色板**；颜色使用主题常量（BG/TEXT/TEXT_DIM/ACCENT/"
        "PANEL/CARD/BORDER 等）而非硬编码色值，深色浅色都能自适应且协调美观。\n"
        "5. **4 个子面板（todos_win/git_win/wt_win/code_win）可完全自定义**：可用内置类或自定义"
        "QWidget 子类替换，改变布局/背景/尺寸；默认自动停靠，置 win._ai_managed=True 后由你自由"
        "摆放位置（守卫不再干预），详见契约第三节；子面板数据接口（update_todos/refresh/show_file）"
        "按需实现，面板会自动调用。\n"
        "6. 若面板用到某插件的 MCP 工具或技能，必须在 plugins/mcp 字段声明，导出时会打包。\n"
        "7. 代码必须真实可运行、无 mock、无占位 TODO。不要修改面板功能逻辑（发送/停止/会话/上传等）。"
    )
    _report(on_status, 30, "正在生成 UI/UX 代码…")
    data = None
    build_ui = ""
    text = ""
    reason = ""
    for attempt in range(2):
        if attempt == 0:
            user_c = description
        else:
            user_c = (description +
                      "\n\n注意：上一次输出未通过校验。这次只输出一个合法 JSON 对象本身："
                      "不要 markdown 代码块、不要解释文字；build_ui 必须完整可运行、覆盖全部契约控件"
                      + (f"；上次问题：{reason}" if reason else "") + "。")
        try:
            resp = client.chat([{"role": "system", "content": sys_p},
                                {"role": "user", "content": user_c}],
                               max_tokens=8192, timeout=180)
            text = (resp or {}).get("text") or ""
        except Exception as e:
            if attempt == 0:
                reason = f"LLM 调用失败: {e}"
                continue
            raise RuntimeError(f"LLM 调用失败: {e}") from e
        data = _extract_json_from_llm(text)
        if not isinstance(data, dict):
            reason = "输出不是合法 JSON"
            continue
        build_ui = str(data.get("build_ui") or "").strip()
        if not build_ui:
            reason = "缺少 build_ui 代码"
            continue
        okv, verr = _validate_build_ui_code(build_ui)
        if not okv:
            reason = verr
            continue
        break
    if not isinstance(data, dict):
        snippet = (text or "").strip()[:160].replace("\n", " ")
        return (False, f"AI 返回无法解析 JSON（可能输出被截断）。返回开头: {snippet}")
    if not build_ui:
        return (False, "AI 未生成有效的 build_ui 代码")
    okv, verr = _validate_build_ui_code(build_ui)
    if not okv:
        return (False, f"AI 生成的 build_ui 未通过校验：{verr}（已自动重试一次仍失败）")
    name = str(data.get("name") or "").strip()
    desc = str(data.get("description") or "").strip()
    if not name:
        return (False, "AI 未生成有效的包名")
    _report(on_status, 70, "正在写入 UI/UX 包…")
    theme = data.get("theme")
    if not isinstance(theme, dict):
        theme = None
    plugins = data.get("plugins")
    if isinstance(plugins, (list, tuple)):
        plugins = [str(x).strip() for x in plugins if str(x).strip()]
    else:
        plugins = None
    mcp = data.get("mcp")
    if isinstance(mcp, (list, tuple)):
        mcp = [str(x).strip() for x in mcp if str(x).strip()]
    else:
        mcp = None
    ok, msg = create_package(name, desc, build_ui_code=build_ui,
                             build_welcome_code=str(data.get("build_welcome") or "").strip(),
                             theme=theme, plugins=plugins, mcp=mcp)
    if not ok:
        return (False, msg)
    # qss 写入 panel.qss
    qss = str(data.get("qss") or "").strip()
    if qss:
        set_panel_qss(name, qss)
    # 用语法检查粗验生成代码
    try:
        compile(build_ui, f"<uiux:{name}>", "exec")
    except SyntaxError as e:
        return (False, f"生成的 build_ui.py 语法错误：{e}")
    has_theme = "yes" if theme else "no"
    deps = []
    if plugins:
        deps.append(f"插件 {len(plugins)} 个")
    if mcp:
        deps.append(f"MCP {len(mcp)} 个")
    dep_txt = f"，依赖 {', '.join(deps)}" if deps else ""
    _report(on_status, 95, "完成")
    return (True, f"UI/UX 包 '{name}' 已生成（自定义主题 {has_theme}{dep_txt}），可在设置中切换预览")