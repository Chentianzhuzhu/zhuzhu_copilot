"""转译层热插拔引擎：插件启用/停用/导入/删除/AI 生成后**全部立即生效，绝不要求重启**。

转译层（`~/.zhuzhu_Copilot/source_overlay`）写入后，Python 里已导入的模块仍是旧代码。
本引擎在运行进程内完成「旧代码 → 新代码」的原子切换，靠以下机制做到**任何插件都能热插拔**：

  1. **启动基线**（`establish_baseline`）：应用启动后在后台线程把所有应用模块当前源码
     的哈希建立基线。没有基线时，进程内第一次启用插件会把变更模块误判为"基线"而跳过
     ——这正是旧实现"第一个插件必须重启"的根因，由启动基线彻底封死。
  2. **原地重载**：重执行模块代码（模块对象不变，`__dict__` 原地更新），所有"调用时
     才查全局名"的旧代码立刻看到新实现；
  3. **活类热修补**：重执行会重建类对象，而已存在的实例仍指向旧类。把新类的全部属性
     就地补进旧类对象，运行中的窗口/服务实例立刻用上新方法；
  4. **旧引用重绑**：用 `id()` 哈希表把 sys.modules 里与旧对象**同一身份**的属性换成新
     对象——解决 `from x import y` 拿到旧函数/旧常量的经典难题（O(1)/属性）；
  5. **进程级状态保留**：对白名单全局量做"快照→原对象放回"，容器（list/dict）做
     **就地灌入**，运行中的登记表/句柄不被清空；
  6. **依赖感知排序**：AST 分析变更模块间的 import 关系，被依赖者先重载，依赖者后
     重载，配合旧引用重绑做到交叉引用一次到位；
  7. **事务化预编译**：动任何模块之前先 `compile()` 完全部变更（纯计算、无副作用），
     任一语法失败立即中止，**一个运行中的模块都不会被执行到一半的新代码污染**；
  8. **死模块摘除**：插件新建的模块在停用/回滚后立即从 sys.modules 摘除，不会以"死代码"
     残留造成"改了没生效"；
  9. **UI 事件桥**：`on_ui_event` 注册的回调（页面/服务/托盘/快捷键的挂摘）在 GUI 线程
     直接执行、在工作线程（AI 工具链路）自动 marshal 到 GUI 线程，保证两条链路都能
     即时热插拔。

高性能（热插拔本身不造成卡顿）：
  · **零读变更检测**：先解析路径再按 (路径, mtime_ns, size) 判签名，没变的模块
    连文件都不读；根目录每轮只算一次（Windows 上 is_file/stat 很贵）；
  · **一轮只编译一次**：源码插件写入时的编译检查顺带把 code 对象预热进缓存，
    热加载直接 exec；同一文件被多条 op 改时也只编译一次；
  · **代码缓存**：内存 LRU(64，容得下一整批插件模块) + 磁盘 marshal（跨重启免编译）；
  · **新模块直接装载**：用已编译 code 造模块并 exec，省掉 importlib 的二次读取+编译；
  · **轻量依赖排序**：行级导入扫描（而不是 AST 全量解析，1.5MB 模块省 ~0.5s）。

失败策略（配合"绝不重启"）：任何模块热加载失败都会如实报告，调用方（核心层
`copilot_plugins.set_enabled`）据此**自动回滚该插件并强制热加载回滚结果**——
要么生效、要么回滚，永远不留"重启一下就好了"的半套变更。

仍不可热重载的只有插件引擎自身（重载等于拆掉热加载的地基）：
`plugin_source` / `copilot_plugins` / `plugin_guard` / `hot_reload` 与包根。
"""

import hashlib
import importlib
import importlib.util
import marshal
import re
import sys
import threading
import time
import types
from collections import OrderedDict
from pathlib import Path

#: 不可热重载：插件引擎自身与包根（重载它们等于拆地基）
_HOT_SKIP = frozenset({
    "zhuzhu_Copilot",                    # 重载包根会让所有子模块引用脱节
    "zhuzhu_Copilot.core.plugin_source",
    "zhuzhu_Copilot.core.copilot_plugins",
    "zhuzhu_Copilot.core.plugin_guard",
    "zhuzhu_Copilot.core.hot_reload",
})

#: 重执行会重跑的模块级**进程状态**：快照 → 重执行 → 原对象放回（保持同一对象）
_STATE_PRESERVE = {
    "zhuzhu_Copilot.ui.main_window": ("_COPILOT_EXT_STATE",),
    # _THEME_VERSION 必须保留：清零会让旧渲染缓存键与新主题版本撞车 → 旧配色残留
    "zhuzhu_Copilot.ui.agent_panel": ("_THEME_VERSION", "_LAST_PALETTE"),
    # agent_tools 是插件最常改的模块（几乎都要往 TOOLS 插工具），启用源码级插件
    # 会重执行它。这些是**运行期状态**，源码里重新声明 {} / "" 会把它们清空：
    #  · WORKDIR —— 清空后工作树与 Git 面板会立刻按空工作目录刷新（表现为「清空」）；
    #  · _FILE_OBSERVERS —— AI 改文件后的差异预览会彻底失效；
    #  · _running_cmds / _cmd_seq —— 后台命令轮询与编号丢失；
    #  · _CUSTOM_REGISTRY —— 工作流自定义工具被取消登记；
    #  · TTS / 下载 / 缓存 / 编辑事务等进程内状态同理。
    # 统一按「保留旧对象」处理（不是灌入新内容）：这里的旧内容本身就是运行成果。
    "zhuzhu_Copilot.core.agent_tools": (
        "WORKDIR",
        "_CUSTOM_REGISTRY", "_CUSTOM_SOURCE", "_CUSTOM_LOCK",
        "_LAZY_IMPORTS", "_BUILTIN_SCHEMAS", "_FILE_OBSERVERS",
        "_running_cmds", "_cmds_lock", "_cmd_seq",
        "_TODO_CACHE", "_TODO_WRITES", "_TODO_LOCK",
        "_SEARCH_CACHE", "_active_download", "_active_dl_lock",
        "_NET_TARGET_POLICY", "_EDIT_DELTA", "_EDIT_DELTA_LOCK",
        "_TTS_PLAYER_LOCK", "_TTS_BUF", "_TTS_STOP_EVT", "_TTS_DONE_EVT",
        "_TTS_FLUSH_EVT", "_TTS_SEG_START", "_TTS_PLAYER_THREAD",
    ),
}

#: 重执行会重建的**容器**全局量：保留旧对象、把新内容灌进去（既有引用继续有效）。
#: 典型如 agent_tools.TOOLS——引擎可能持有该 list 的引用，重执行换新 list 会让
#「已登记的工具」在新旧之间分裂。
_INPLACE_CONTAINERS = {
    "zhuzhu_Copilot.core.agent_tools": ("TOOLS",),
    "zhuzhu_Copilot.core.agent_skills": ("_SKILL_KEYWORDS",),
}

#: 旧引用重绑时跳过的模块（系统/解释器内部，动了会出诡异问题）
_REBIND_SKIP = frozenset({"sys", "builtins", "_frozen_importlib",
                          "_frozen_importlib_external", "marshal"})

#: 代码对象内存缓存：(模块名, 源码 sha256) → code。容量 64（LRU）。
#: 必须容得下一整批插件模块：源码插件一次插入几十个新模块时，编译检查已把 code
#: 预热进缓存，容量不够会把先预热的挤出去，热加载阶段再逐个重编译 + 落盘 marshal，
#: 单批 30 个模块就会多花 1s 以上。
_CODE_CACHE = OrderedDict()
_CODE_CACHE_MAX = 64

#: 内容哈希/文件签名缓存：{模块名: (mtime_ns, size, sha256)}——stat 没变就连文件都不读
_STATS = {}
#: 已建基线的模块内容哈希（与 _STATS 同步，变更判定的单一数据源）
_HASHES = {}
_BASELINE_READY = False

#: 事务锁：热插拔/基线建立串行化，防止并发启停互相踩到半成品命名空间
_LOCK = threading.RLock()


# ---------------------------------------------------------------------------
# UI 事件桥：核心层不依赖 UI；UI 层（main_window）注册回调，引擎按线程自动派发
# ---------------------------------------------------------------------------
#: event → {稳定 key: 回调}。同 key 的后注册者替换先注册者（模块被热重载后函数对象
#: 会重建，用稳定 key 去重，避免同一动作挂多份）。
_UI_EVENTS = {"before_disable": {}, "after_enable": {}, "after_change": {}}
#: 工作线程派发器（UI 层注册为 QTimer.singleShot 包装）；None = 无 UI 环境（测试/无头）
_UI_DISPATCHER = None


def on_ui_event(event: str, fn, key: str = "") -> None:
    """注册 UI 事件回调。

    事件：before_disable（停用前摘 UI/停服务）、after_enable（启用后挂 UI/启服务）、
    after_change（回滚/删除/强制重建后统一重建）。回调签名：fn(plugin_name)。
    """
    if event not in _UI_EVENTS:
        return
    _UI_EVENTS[event][key or getattr(fn, "__qualname__", str(id(fn)))] = fn


def set_ui_dispatcher(dispatcher) -> None:
    """注册"把回调投递到 GUI 线程执行"的派发器（UI 层用 QTimer.singleShot）。"""
    global _UI_DISPATCHER
    _UI_DISPATCHER = dispatcher


def _qt_state():
    """返回 (QApplication 实例, 当前是否在 GUI 线程)；无 Qt 环境返回 (None, False)。"""
    try:
        from PyQt6.QtCore import QThread
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            return None, False
        return app, QThread.currentThread() is app.thread()
    except Exception:
        return None, False


def emit_ui(event: str, name: str = "") -> bool:
    """触发 UI 事件。GUI 线程直接同步执行；工作线程经派发器 marshal 到 GUI 线程。

    返回是否已派发。无 UI 环境（测试/无头）时静默跳过——核心事务不依赖 UI 回调成功。
    """
    cbs = list(_UI_EVENTS.get(event, {}).values())
    if not cbs:
        return False

    def _run_all():
        for cb in cbs:
            try:
                cb(name)
            except Exception:
                pass

    app, is_gui = _qt_state()
    if app is None or is_gui:
        _run_all()
        return True
    if _UI_DISPATCHER is not None:
        try:
            _UI_DISPATCHER(_run_all)
            return True
        except Exception:
            pass
    return False


# ---------------------------------------------------------------------------
# 转译层模块清单与源码解析（stat 快路径）
# ---------------------------------------------------------------------------
def _overlay_hook():
    import _copilot_overlay_hook
    _copilot_overlay_hook.install()      # 首次启用时覆盖层目录刚创建
    return _copilot_overlay_hook


def _module_names() -> list:
    """转译层里的模块名（按层级由浅到深排序：先重载被依赖的）。"""
    try:
        rels = _overlay_hook().overlay_modules()
    except Exception:
        return []
    names = []
    for rel in rels:
        rel = str(rel).replace("\\", "/")
        if not rel.endswith(".py"):
            continue
        name = rel[:-3].replace("/", ".")
        if name == "zhuzhu_Copilot" or name.startswith("zhuzhu_Copilot."):
            names.append(name)
    names.sort(key=lambda n: (n.count("."), n))
    return names


def _module_path(name: str, ov_root=None, orig_root=None, ov_names=None):
    """模块当前源码落点（只做路径判断，不读文件）：覆盖层优先，回落原版。

    与 `_module_source` 的区别：变更检测只需要路径 + stat，读整份文件是纯浪费
    （大模块 1.5MB，上百个模块时每次热插拔要多读几 MB）。`ov_root` / `orig_root`
    由调用方每轮算一次传入——逐个模块重算根目录（is_dir/is_file）在 Windows 上
    开销很大，是热插拔的主要隐形瓶颈之一。

    ov_names：本轮覆盖层里的模块名集合（已知），命中才去 stat 覆盖层文件；
    其余模块直接查原版，省掉每个模块一次 is_file。
    """
    try:
        hook = _overlay_hook()
        if ov_names is None or name in ov_names:
            root = Path(ov_root) if ov_root is not None else hook.overlay_dir()
            ov = root / (name.replace(".", "/") + ".py")
            if ov.is_file():
                return ov
        base = orig_root if orig_root is not None else hook._original_root()
        if base is not None:
            parts = name.split(".")
            p = Path(base).joinpath(*parts[:-1], parts[-1] + ".py")
            if p.is_file():
                return p
    except Exception:
        pass
    if not name.startswith("zhuzhu_Copilot."):
        # 通用回落：引擎对任意模块可用（单元测试用合成包即走这里）
        mod = sys.modules.get(name)
        origin = getattr(getattr(mod, "__spec__", None), "origin", None)
        if origin and Path(str(origin)).suffix == ".py" and Path(str(origin)).is_file():
            return Path(str(origin))
    return None


def _module_source(name: str, ov_root=None, orig_root=None):
    """解析模块当前的源码落点与内容：返回 (路径或 None, 源码 bytes 或 None)。

    两者皆空 = 模块已死（应从 sys.modules 摘除）。
    """
    path = _module_path(name, ov_root, orig_root)
    if path is None:
        return None, None
    try:
        return path, path.read_bytes()
    except OSError:
        return path, None


def _hash_source(src) -> str:
    """源码内容哈希（统一换行为 LF、剥离 UTF-8 BOM）。

    必须与 importlib 的 SourceFileLoader.get_source() 口径对齐：
      ① get_source() 把 CRLF 转成 LF（tokenize.open 通用换行），而我们读磁盘是
         原始字节——Windows 上不归一化会导致每个文件都"加载时哈希 != 磁盘哈希"，
         无基线时全量重跑；
      ② get_source() 会剥离 UTF-8 BOM（agent_chat_bubbles.py 带 BOM）。
    """
    if not src:
        return ""
    if isinstance(src, str):
        if src.startswith("\ufeff"):
            src = src[1:]
        src = src.encode("utf-8")
    else:
        if src.startswith(b"\xef\xbb\xbf"):
            src = src[3:]
    src = src.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(src).hexdigest()


def _current_hash(name: str, force: bool = False, path=None) -> str:
    """取模块当前源码哈希，stat 没变则复用缓存（热插拔高频路径零文件读取）。

    path 由调用方传入（一次 `_module_path` 结果复用），避免重复解析根目录。
    返回 sha256 或 ""（源码落点消失）；同时刷新 _STATS 签名。
    """
    if path is None:
        path = _module_path(name)
    if path is None:
        _STATS.pop(name, None)
        return ""
    try:
        st = path.stat()
        sig = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        sig = (str(path), 0, 0)
    cached = _STATS.get(name)
    if not force and cached is not None and cached[:3] == sig:
        return cached[3]
    try:
        h = _hash_source(path.read_bytes())
    except OSError:
        _STATS.pop(name, None)
        return ""
    _STATS[name] = (sig[0], sig[1], sig[2], h)
    return h


def _loaded_hash(name: str) -> str:
    """模块**当前实际加载时**的源码哈希（经 loader.get_source 读取）。

    这是精确基线的关键：不依赖启动时是否扫描过，只要"加载时源码 == 磁盘源码"
    就说明运行中的代码就是磁盘版本，无需重跑。加载器拿不到源码（如 C 扩展/
    合成模块）返回空串。
    """
    mod = sys.modules.get(name)
    loader = getattr(getattr(mod, "__spec__", None), "loader", None)
    get_source = getattr(loader, "get_source", None)
    if not callable(get_source):
        return ""
    try:
        s = get_source(name)
    except Exception:
        return ""
    if not s:
        return ""
    return _hash_source(s)


# ---------------------------------------------------------------------------
# 代码对象缓存：内存 LRU + 磁盘 marshal（跨重启免编译）
# ---------------------------------------------------------------------------
def _disk_cache_dir() -> Path | None:
    try:
        from zhuzhu_Copilot import app_identity
        d = app_identity.data_root() / "hot_reload_cache" / str(sys.implementation.cache_tag)
        d.mkdir(parents=True, exist_ok=True)
        return d
    except Exception:
        return None


def _disk_code_path(d: Path, name: str, sha: str) -> Path:
    safe = name.replace(".", "__").replace("/", "__")
    return d / f"{safe}.{sha[:16]}.marshal"


def _cache_put(name: str, sha: str, code, persist: bool = True) -> None:
    """写入代码缓存：内存 LRU（+ 可选磁盘 marshal，跨重启免编译）。"""
    key = (name, sha)
    _CODE_CACHE[key] = code
    _CODE_CACHE.move_to_end(key)
    while len(_CODE_CACHE) > _CODE_CACHE_MAX:
        _CODE_CACHE.popitem(last=False)
    if not persist:
        return
    d = _disk_cache_dir()
    if d is None:
        return
    cf = _disk_code_path(d, name, sha)
    try:
        tmp = cf.with_suffix(".tmp")
        tmp.write_bytes(marshal.dumps(code))
        tmp.replace(cf)
    except OSError:
        pass


def _code_for(name: str, src: bytes, path) -> object:
    """编译源码为 code 对象：内存 LRU → 磁盘 marshal → 现场 compile。"""
    key = (name, _hash_source(src))
    code = _CODE_CACHE.get(key)
    if code is not None:
        _CODE_CACHE.move_to_end(key)
        return code
    d = _disk_cache_dir()
    if d is not None:
        cf = _disk_code_path(d, name, key[1])
        try:
            if cf.is_file():
                code = marshal.loads(cf.read_bytes())
                _cache_put(name, key[1], code, persist=False)
                return code
        except Exception:
            code = None
    code = compile(src, str(path), "exec")
    _cache_put(name, key[1], code)
    return code


def prime_code(name: str, src: bytes, path=None, code=None, persist: bool = True) -> None:
    """预热代码缓存：源码级插件插入时的编译检查顺带把 code 对象缓存下来。

    键与 `_code_for` 完全一致（模块名 + 归一化源码 sha256），热加载时直接命中，
    同一份源码不再被编译两次（大型插件插入的最大单笔 CPU 开销）。
    persist=False：只缓存在内存（写入磁盘 marshal 每个文件要数毫秒，插入链路
    几十个文件就是几百毫秒；跨重启重新编译一次可以接受）。
    """
    if code is None:
        try:
            code = compile(src, str(path or name), "exec")
        except Exception:
            return
    _cache_put(name, _hash_source(src), code, persist=persist)


def _exec_module_code(mod, code) -> None:
    """等价于 reload 的"重执行"：exec 进**同一个**模块命名空间（不清空旧全局）。

    这样旧函数/旧类方法里"调用时查全局名"的引用立即看到新实现；
    进程级状态由 `_preserve_state` 在执行后恢复。
    """
    exec(code, mod.__dict__)


def _import_with_code(name: str, code, path) -> bool:
    """用**已编译的 code** 直接装载新模块（插件新建的模块走这条）。

    标准 `import_module` 会让 SourceFileLoader 再读一遍源码并再编译一次（大插件
    几十个新模块就是几十毫秒×N）。这里用 spec 造模块对象、直接 exec 我们已编译好的
    code，语义与正常导入一致（先入 sys.modules 再执行，父包挂同名属性）。
    失败返回 False，由调用方回落到标准导入。
    """
    try:
        spec = importlib.util.spec_from_file_location(name, str(path))
        if spec is None:
            return False
        mod = importlib.util.module_from_spec(spec)
        if mod is None:
            return False
    except Exception:
        return False
    sys.modules[name] = mod
    try:
        exec(code, mod.__dict__)
    except Exception:
        sys.modules.pop(name, None)
        return False
    parent, _, child = name.rpartition(".")
    parent_mod = sys.modules.get(parent)
    if parent_mod is not None and child and not child.startswith("__"):
        try:
            setattr(parent_mod, child, mod)
        except Exception:
            pass
    return True


# ---------------------------------------------------------------------------
# 活类热修补：新类的属性就地补进旧类对象
# ---------------------------------------------------------------------------
def _patch_live_class(live_cls, new_cls) -> int:
    """把 new_cls 的属性补进 live_cls（已存在实例仍指向 live_cls）。

    跳过 `__dict__` / `__weakref__` / `__module__`（CPython 类型槽，不能 setattr）；
    其余一律 setattr：方法、类属性、property、`__init__` 全部换成新实现。
    返回补上的属性数。
    """
    n = 0
    for k, v in list(vars(new_cls).items()):
        if k in ("__dict__", "__weakref__", "__module__"):
            continue
        try:
            setattr(live_cls, k, v)
            n += 1
        except Exception:
            continue
    return n


def _patch_live_classes(mod, old_ns: dict) -> list:
    """对模块里"同名但对象不同"的类做就地热修补。返回 [类名, ...]。"""
    patched = []
    for name, new_obj in list(vars(mod).items()):
        old_obj = old_ns.get(name)
        if old_obj is new_obj or old_obj is None:
            continue
        if isinstance(new_obj, type) and isinstance(old_obj, type):
            if _patch_live_class(old_obj, new_obj):
                patched.append(name)
    return patched


# ---------------------------------------------------------------------------
# 旧引用重绑：`from x import y` 的旧对象 → 新对象（id() 哈希表，O(1)/属性）
# ---------------------------------------------------------------------------
def _rebind_stale_refs(pairs: list) -> int:
    if not pairs:
        return 0
    # 只重绑函数/类/模块/可变容器等"身份唯一"的对象。
    # 绝不能把 int/bool/None/str/bytes 等不可变标量纳入映射：CPython 缓存小整数
    # 与部分字符串（id(0)、id("...") 全进程固定），按 id 重绑会遍历全部
    # sys.modules 把所有同名常量误改（例如把 re._compiler 里的整数 opcode 改掉，
    # 导致整个进程 re.compile 抛 RuntimeError: invalid SRE code）。
    # 标量常量的 from-import 不支持热更新（这是 importlib.reload 的同样边界）。
    rebindable = (
        types.FunctionType,
        types.BuiltinFunctionType,
        types.MethodType,
        types.ModuleType,
        type,
        list,
        dict,
        set,
    )
    mapping = {}
    for old_obj, new_obj in pairs:
        if (isinstance(old_obj, rebindable)
                and isinstance(new_obj, rebindable)
                and old_obj is not new_obj):
            mapping[id(old_obj)] = new_obj
    if not mapping:
        return 0
    n = 0
    for mname, mod in list(sys.modules.items()):
        if mname in _REBIND_SKIP or mod is None:
            continue
        ns = getattr(mod, "__dict__", None)
        if not ns:
            continue
        for attr, val in list(ns.items()):
            new_obj = mapping.get(id(val))
            if new_obj is not None:
                try:
                    setattr(mod, attr, new_obj)
                    n += 1
                except Exception:
                    pass
    return n


# ---------------------------------------------------------------------------
# 进程状态 / 容器保留
# ---------------------------------------------------------------------------
def _preserve_state(mod, old_ns: dict) -> None:
    name = getattr(mod, "__name__", "")
    for key in _STATE_PRESERVE.get(name, ()):
        if key in old_ns:
            mod.__dict__[key] = old_ns[key]          # 同一对象放回
    for key in _INPLACE_CONTAINERS.get(name, ()):
        old_obj = old_ns.get(key)
        new_obj = mod.__dict__.get(key)
        if old_obj is None or new_obj is None or old_obj is new_obj:
            continue
        if isinstance(old_obj, list) and isinstance(new_obj, list):
            old_obj[:] = new_obj                     # 原地灌入，引用不换
            mod.__dict__[key] = old_obj
        elif isinstance(old_obj, dict) and isinstance(new_obj, dict):
            old_obj.clear()
            old_obj.update(new_obj)
            mod.__dict__[key] = old_obj


def _ensure_parents(name: str) -> None:
    """保证父包已导入：重执行子模块前缺父包会直接 ImportError。"""
    parts = name.split(".")
    for i in range(1, len(parts)):
        parent = ".".join(parts[:i])
        if parent not in sys.modules:
            try:
                importlib.import_module(parent)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 依赖感知排序：轻量导入扫描，被依赖者先重载
# ---------------------------------------------------------------------------
#: `from .x import y` / `import a.b` 行级匹配（只用于调度顺序，不求完备）
_IMPORT_RE = re.compile(
    r"^[ \t]*(?:from[ \t]+(?P<from>[.\w]+)[ \t]+import|import[ \t]+(?P<plain>[.\w][\w., \t]*))",
    re.MULTILINE)


def _import_dependencies(src: bytes, name: str, names_set: set) -> set:
    """从源码里提取该模块在本次变更集合内的依赖模块（轻量正则扫描）。

    只决定**重载顺序**（被依赖者先执行）：顺序不完美也只会退化为"依赖方先执行、
    随后由旧引用重绑补正"，正确性由重绑与正常依赖导入兜底。
    相比 AST 全量解析（1.5MB 模块约 480ms），行级扫描一个模块只需几毫秒，
    这是大型插件热插拔耗时的主要优化点之一。
    """
    try:
        text = (src or b"").decode("utf-8", "replace")
    except Exception:
        return set()
    pkg = name.split(".")[:-1]
    deps = set()
    for m in _IMPORT_RE.finditer(text):
        from_mod, plain = m.group("from"), m.group("plain")
        if from_mod:
            if from_mod.startswith("."):
                level = len(from_mod) - len(from_mod.lstrip("."))
                rest = from_mod.lstrip(".")
                base = pkg[:len(pkg) - level + 1]
                full = ".".join(base + ([rest] if rest else []))
            else:
                full = from_mod
            if full in names_set:
                deps.add(full)
            continue
        for part in (plain or "").split(","):
            full = part.strip().split(" as ")[0].strip()
            if full and full in names_set:
                deps.add(full)
    deps.discard(name)
    return deps


def _dependency_order(items: dict) -> list:
    """items: {模块名: 源码 bytes}。返回拓扑序（依赖在前），同序按层级/字母稳定排序。

    items 中不含源码（落点消失）的条目按无依赖处理。环依赖安全打破（回退字母序）。
    """
    names_set = set(items)
    graph = {}
    for name, src in items.items():
        graph[name] = _import_dependencies(src or b"", name, names_set)
    ordered = []
    visited = set()
    visiting = set()

    def visit(n: str):
        if n in visited or n in visiting:
            return
        visiting.add(n)
        for dep in sorted(graph.get(n, ()), key=lambda x: (x.count("."), x)):
            visit(dep)
        visiting.discard(n)
        visited.add(n)
        ordered.append(n)

    for n in sorted(names_set, key=lambda x: (x.count("."), x)):
        visit(n)
    return ordered


# ---------------------------------------------------------------------------
# 死模块清理（回滚 create_file 后的残留）
# ---------------------------------------------------------------------------
def drop_dead_modules(ov_root=None, orig_root=None, ov_names=None) -> list:
    """摘除"源码落点已消失"的 zhuzhu_Copilot.* 模块（插件新建模块被回滚）。

    不摘的话，死代码会一直活在 sys.modules 里：再次启用同名插件时会命中旧对象，
    看起来"改了没生效"。只用路径判断（_module_path），不读文件内容。
    """
    dropped = []
    for name in list(sys.modules):
        if not name.startswith("zhuzhu_Copilot.") or name in _HOT_SKIP:
            continue
        mod = sys.modules.get(name)
        if getattr(mod, "__spec__", None) is None and name not in _HASHES:
            continue
        if _module_path(name, ov_root, orig_root, ov_names) is None:
            sys.modules.pop(name, None)
            _STATS.pop(name, None)
            _HASHES.pop(name, None)
            dropped.append(name)
    return dropped


# ---------------------------------------------------------------------------
# UI 刷新：主题 / 派生 QSS / 活实例 / 插件自定义热刷新钩子
# ---------------------------------------------------------------------------
def _refresh_ui(names: list, patched: list) -> list:
    """UI 模块热重载后重建主题与派生 QSS，并刷新活实例。返回刷新动作清单。

    必须在 GUI 线程调用（reload_overlay 负责线程判断与 marshal）。
    """
    done = []
    ui_mods = [n for n in names if n.startswith("zhuzhu_Copilot.ui.")]
    if not ui_mods:
        return done
    try:
        from PyQt6.QtWidgets import QApplication
    except Exception:
        return done
    app = QApplication.instance()
    if app is None:
        return done

    try:
        from zhuzhu_Copilot.ui import styles
    except Exception:
        styles = None

    theme = None
    for w in app.allTopLevelWidgets():
        fn = getattr(w, "_current_theme", None)
        if callable(fn):
            try:
                theme = fn()
            except Exception:
                theme = None
            if theme:
                break

    if styles is not None:
        try:
            if "zhuzhu_Copilot.ui.styles" in ui_mods or \
                    "zhuzhu_Copilot.ui.agent_panel" in ui_mods:
                styles.set_palette(str(theme or "dark"))     # 重建 GLOBAL_QSS
                if theme:
                    styles.apply_palette(app)
                done.append("styles:QSS")
        except Exception:
            pass
        try:
            if "zhuzhu_Copilot.ui.agent_panel" in ui_mods:
                from zhuzhu_Copilot.ui import agent_panel as ap
                ap._apply_colors(dict(styles.PALETTE), force=True, bump_version=True)
                done.append("agent_panel:colors")
        except Exception:
            pass

    n = 0
    hot_hook = 0
    patched_mods = {m.split(".")[-1] for m in patched}
    for w in app.allTopLevelWidgets():
        cls = type(w)
        if cls.__module__ in ui_mods:
            # 插件自定义热刷新钩子：活实例的类被热修补后，Qt 信号槽里旧绑定方法
            # 无法被通用机制重连，控件可在 _on_hot_reloaded 里自行重连/重建。
            if cls.__name__ in patched_mods:
                hook = getattr(w, "_on_hot_reloaded", None)
                if callable(hook):
                    try:
                        hook()
                        hot_hook += 1
                    except Exception:
                        pass
            for meth in ("_apply_theme", "refresh_theme"):
                fn = getattr(w, meth, None)
                if callable(fn):
                    try:
                        fn()
                        n += 1
                    except Exception:
                        pass
                    break
    if n:
        done.append(f"{n} 个窗口已重刷")
    if hot_hook:
        done.append(f"{hot_hook} 个实例热刷新")
    return done


# ---------------------------------------------------------------------------
# 单模块热加载
# ---------------------------------------------------------------------------
def reload_module(name: str, force: bool = False, src=None, path=None) -> dict:
    """热加载单个模块。返回 {ok, patched, rebound, dropped, imported, error}。

    src/path 可由调用方传入（_reload_locked 已经把源码读进内存、路径也算好），
    省掉重复的路径解析与整份文件读取。
    """
    out = {"ok": False, "patched": [], "rebound": 0, "dropped": False,
           "imported": False, "error": ""}
    _ensure_parents(name)
    if path is None or src is None:
        path2, src2 = _module_source(name)
        path = path if path is not None else path2
        src = src if src is not None else src2
    if path is None or src is None:
        # 源码落点消失（插件新建模块被回滚）→ 从 sys.modules 摘除死模块
        sys.modules.pop(name, None)
        out["dropped"] = True
        out["ok"] = True
        return out

    try:
        code = _code_for(name, src, path)
    except Exception as e:
        out["error"] = f"编译失败 {type(e).__name__}: {e}"
        return out

    mod = sys.modules.get(name)
    if mod is None:
        # 新模块：用已编译的 code 直接装载（省掉 importlib 的二次读取+编译）
        if _import_with_code(name, code, path):
            out["ok"] = True
            out["imported"] = True
            _HASHES[name] = _hash_source(src)
            return out
        try:
            importlib.import_module(name)
            out["ok"] = True
            out["imported"] = True
            _HASHES[name] = _hash_source(src)
            return out
        except Exception as e:
            out["error"] = f"{type(e).__name__}: {e}"
            return out

    old_ns = dict(mod.__dict__)                     # 快照（旧类对象靠实例续命）
    try:
        _exec_module_code(mod, code)
    except Exception as e:
        # 执行到一半失败：把快照里缺失的名字补回去，尽量保住旧实现
        for k, v in old_ns.items():
            mod.__dict__.setdefault(k, v)
        out["error"] = f"{type(e).__name__}: {e}"
        return out

    new_ns = mod.__dict__
    out["patched"] = _patch_live_classes(mod, old_ns)
    pairs = []
    for k, new_obj in list(new_ns.items()):
        old_obj = old_ns.get(k)
        if old_obj is None or old_obj is new_obj:
            continue
        pairs.append((old_obj, new_obj))
    out["rebound"] = _rebind_stale_refs(pairs)
    _preserve_state(mod, old_ns)
    out["ok"] = True
    return out


# ---------------------------------------------------------------------------
# 启动基线（封死"第一个插件必须重启"）
# ---------------------------------------------------------------------------
def establish_baseline(scan_all: bool = True) -> dict:
    """建立启动基线：记录所有应用模块当前源码哈希，**不执行任何代码**。

    应用启动后在后台线程调用一次。之后 reload_overlay 只处理真正发生变化的模块。
    覆盖范围：转译层全部模块 ∪ 已导入的 zhuzhu_Copilot.* ∪（scan_all）原版源码树
    里全部模块——后者保证运行中才懒加载的模块也有基线，首次启用插件即可精准增量。
    幂等：基线建立后重复调用直接返回。
    """
    with _LOCK:
        if _BASELINE_READY:
            return {"baseline": True, "modules": len(_HASHES)}
        names = set(_module_names())
        for name in list(sys.modules):
            if name.startswith("zhuzhu_Copilot.") and name not in _HOT_SKIP:
                names.add(name)
        if scan_all:
            try:
                hook = _overlay_hook()
                root = hook._original_root()
                if root is not None:
                    base = root / "zhuzhu_Copilot"
                    if base.is_dir():
                        for p in base.rglob("*.py"):
                            rel = p.relative_to(root).as_posix()
                            n = rel[:-3].replace("/", ".")
                            if n and n not in _HOT_SKIP:
                                names.add(n)
            except Exception:
                pass
        n = 0
        for name in sorted(names, key=lambda x: (x.count("."), x)):
            try:
                h = _current_hash(name)
            except Exception:
                continue
            if h:
                _HASHES[name] = h
                n += 1
        globals()["_BASELINE_READY"] = True
        return {"baseline": True, "modules": n}


# ---------------------------------------------------------------------------
# 全量入口（事务化 + 增量 + 高性能）
# ---------------------------------------------------------------------------
def reload_overlay(force: bool = False, run_ui: bool | None = None) -> dict:
    """热加载转译层全部变更。

    返回 {reloaded, imported, patched, rebound, dropped, refreshed, skipped,
    failed, duration_ms}。

    事务语义：先对全部变更模块**预编译**（compile 无副作用），任一失败立即中止，
    不执行任何模块代码；运行期 exec 失败则停止后续模块并上报，由核心层自动回滚。

    高性能：① stat 快路径（没变的模块不读文件）；② 内存/磁盘代码缓存（同一内容
    只编译一次）；③ 启动基线已建立时只处理真正变化的模块。

    run_ui：True=本线程直接刷 UI；False=不刷（自动 marshal 到 GUI 线程）；
    None=在 GUI 线程则直接刷，否则自动 marshal。
    """
    t0 = time.perf_counter()
    with _LOCK:
        return _reload_locked(force, run_ui, t0)


def _reload_locked(force: bool, run_ui, t0: float) -> dict:
    global _BASELINE_READY
    names = _module_names()
    result = {"reloaded": [], "imported": [], "patched": [], "rebound": 0,
              "dropped": [], "refreshed": [], "skipped": [], "failed": [],
              "duration_ms": 0}

    # 0) 根目录每轮只算一次：Windows 上 is_dir/is_file 很贵，逐个模块重算会成倍拖慢
    try:
        hook = _overlay_hook()
        ov_root = hook.overlay_dir()
        orig_root = hook._original_root()
    except Exception:
        ov_root, orig_root = None, None

    # 1) 死模块清理（回滚 create_file 后的残留）
    try:
        result["dropped"] = drop_dead_modules(ov_root, orig_root, set(names))
    except Exception:
        pass

    # 2) 待处理清单 = 覆盖层模块 ∪ 已导入的应用模块（后者覆盖"回滚后回落原版"）
    cand = list(names)
    for name in list(sys.modules):
        if name.startswith("zhuzhu_Copilot.") and name not in cand \
                and name not in _HOT_SKIP:
            cand.append(name)
    cand.sort(key=lambda n: (n.count("."), n))

    # 3) 变更检测（精确基线 + stat 快路径）。
    #    只做「路径解析 + stat」：签名没变的模块**连文件都不读**（大模块 1.5MB），
    #    只有真正变更的模块才在下一步读源码。
    #    对已导入但从未记录过基线的模块，直接用"加载时实际源码"（loader.get_source）
    #    与磁盘当前源码比对：相同则未变更，跳过；不同才进 todo。
    #    这样即使启动基线尚未建立（进程内第一次启停），也能精准识别变更，
    #    既不重跑未变更模块（避免扰动运行态），也绝不再漏掉真正的变更
    #    （旧实现把首次启停的变更误判为基线跳过，逼用户重启）。
    todo = []
    sources = {}
    paths = {}
    ov_names = set(names)
    for name in cand:
        if name in _HOT_SKIP:
            result["skipped"].append(name)
            continue
        path = _module_path(name, ov_root, orig_root, ov_names)
        if path is not None and name not in sys.modules and name not in _HASHES:
            # 覆盖层新增、从未装载过的模块：没有基线可比，必然要装载 →
            # 跳过哈希（省掉一次整份读取 + sha256，大插件几十个新模块很可观）
            paths[name] = path
            todo.append((name, ""))
            continue
        h_disk = _current_hash(name, path=path)
        if name not in _HASHES and name in sys.modules:
            h_loaded = _loaded_hash(name)
            if h_loaded:
                _HASHES[name] = h_loaded
                if h_loaded == h_disk:
                    continue
        if not force and _HASHES.get(name) == h_disk:
            continue
        paths[name] = path
        todo.append((name, h_disk))

    _BASELINE_READY = True

    if not todo:
        result["duration_ms"] = int((time.perf_counter() - t0) * 1000)
        return result

    # 4) 事务预编译：任何一个模块编译失败 → 整体中止，运行中的模块一个都不碰
    codes = {}
    for name, h in todo:
        path = paths.get(name)
        if path is None:
            continue                       # 死模块（已在上面摘除）
        try:
            src = path.read_bytes()
        except OSError:
            continue
        sources[name] = src
        try:
            codes[name] = _code_for(name, src, path)
        except Exception as e:
            result["failed"].append(f"{name}（预编译失败：{type(e).__name__}: {e}）")
            result["duration_ms"] = int((time.perf_counter() - t0) * 1000)
            return result

    # 5) 依赖感知排序：被依赖的变更模块先重载。
    #    只对**已在 sys.modules** 的模块做拓扑（新模块靠正常 import 解析依赖，
    #    无需扫描其源码），省掉大型插件最贵的解析开销。排序仅决定执行顺序，
    #    不完美也由旧引用重绑兜底。
    existing = {n: sources[n] for n in sources if n in sys.modules}
    order = _dependency_order(existing) if existing else []
    # 排序结果只覆盖有源码的模块；追加遗漏（新模块按层级/字母序）
    for name in sorted((n for n in sources if n not in order),
                       key=lambda x: (x.count("."), x)):
        order.append(name)

    touched = []
    for name in order:
        # 依赖导入可能已经把"待导入的新模块"加载了（它的内容就是磁盘版本）→ 补基线跳过
        if name in sys.modules and name not in result["reloaded"] \
                and name not in result["imported"]:
            h_now = _current_hash(name, path=paths.get(name))
            if h_now and _HASHES.get(name) == h_now:
                result["imported"].append(name)
                continue
        r = reload_module(name, src=sources.get(name), path=paths.get(name))
        if r.get("dropped"):
            result["dropped"].append(name)
            _HASHES.pop(name, None)
        elif r["ok"]:
            if r.get("imported"):
                result["imported"].append(name)
            else:
                result["reloaded"].append(name)
            result["patched"].extend(f"{name}.{c}" for c in r["patched"])
            result["rebound"] += r["rebound"]
            _HASHES[name] = _current_hash(name, path=paths.get(name))
            touched.append(name)
        else:
            result["failed"].append(f"{name}（{r['error']}）")
            break                    # 事务：一个失败，后续模块不再触碰，等核心层回滚

    # 6) UI 刷新（按线程自动决定直接执行还是 marshal 到 GUI 线程）
    changed = result["reloaded"] + result["imported"]

    def _do_ui():
        try:
            result["refreshed"] = _refresh_ui(changed, result["patched"])
        except Exception:
            pass

    if changed or result["dropped"]:
        app, is_gui = _qt_state()
        want_now = run_ui is True or (run_ui is None and (app is None or is_gui))
        if app is None:
            pass                                  # 无 UI 环境：跳过
        elif want_now:
            _do_ui()
        else:
            # 工作线程：marshal 到 GUI 线程执行，绝不在非 GUI 线程操作 QWidget
            if _UI_DISPATCHER is not None:
                try:
                    _UI_DISPATCHER(_do_ui)
                except Exception:
                    pass
    result["duration_ms"] = int((time.perf_counter() - t0) * 1000)
    return result


def needs_restart(result: dict = None) -> bool:
    """恒为 False：本引擎的设计目标就是"任何插件变更都不需要重启"。

    保留这个入口是为了让调用方语义清晰——如果热加载失败，策略是**自动回滚**，
    而不是留一个"重启后才生效"的半套变更。
    """
    return False


def summary(result: dict) -> str:
    """把 reload_overlay 的结果整理成给用户看的提示（绝不再提"重启"）。"""
    result = result or {}
    reloaded = result.get("reloaded") or []
    imported = result.get("imported") or []
    patched = result.get("patched") or []
    rebound = int(result.get("rebound") or 0)
    dropped = result.get("dropped") or []
    refreshed = result.get("refreshed") or []
    failed = result.get("failed") or []
    parts = []
    if reloaded:
        parts.append(f"热加载 {len(reloaded)} 个模块")
    if imported:
        parts.append(f"载入 {len(imported)} 个新模块")
    if patched:
        cls_names = sorted({c.rsplit(".", 1)[-1] for c in patched})
        parts.append("热修补 " + "、".join(cls_names[:4])
                     + (" 等类" if len(cls_names) > 4 else ""))
    if rebound:
        parts.append(f"重绑 {rebound} 处旧引用")
    if dropped:
        parts.append(f"清理 {len(dropped)} 个已回滚模块")
    if refreshed:
        parts.append("；".join(refreshed[:3]))
    if failed:
        parts.append("失败：" + "；".join(failed[:3]))
    return "；".join(parts) if parts else "无需热加载"
