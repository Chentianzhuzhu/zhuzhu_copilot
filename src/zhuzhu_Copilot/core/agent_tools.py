"""Agent 内置工具：注册表（LLM function calling schema）+ 执行（接入沙盒评估）

执行结果统一为 {"text": str, "images": [data_url]}：
- text 作为 tool 消息文本返回给模型
- images 中的截图 data URL 由引擎并入下一轮视觉输入（AI 主动截图时作为视觉输入）
"""

from zhuzhu_Copilot import app_identity
import difflib
import hashlib
import html as _html
import ipaddress
import itertools
import json
import locale
import os
import re
import socket
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

# 惰性导入：agent_sandbox/agent_find/agent_browser/agent_tts 仅在对应工具
# 真正执行时加载（agent_browser 导入链含 socket/urllib 约 430ms），
# 避免打开 AI 面板时全部预载拖慢响应。首次调用后由 sys.modules 缓存。
_LAZY_IMPORTS = {}


def _sub(name: str):
    mod = _LAZY_IMPORTS.get(name)
    if mod is None:
        import importlib
        mod = importlib.import_module(f"zhuzhu_Copilot.core.{name}")
        _LAZY_IMPORTS[name] = mod
    return mod


# ------------------------------------------------------------
# Cordis 工作流自定义工具注册表：用户工作流 tools.py 的工具可覆盖内置同名
# 或新增工具（register_custom_tools 由 agent_workflow.apply_tools 调用）
# ------------------------------------------------------------
_CUSTOM_REGISTRY: dict = {}  # 来源工作流名 -> (tools, handler)；按工作流隔离，多会话并发互不覆盖
_CUSTOM_SOURCE = "_default"  # 最近注册的来源工作流名（无工作流上下文时的回退）
_CUSTOM_LOCK = threading.Lock()  # 保护注册表的写（热插拔切换时）与 _CUSTOM_SOURCE 原子更新

# 内置 TOOLS 的深拷贝缓存（进程内只拷一次，供 tool_schemas 高频组装的快路径复用）
_BUILTIN_SCHEMAS: list = None

# 覆盖后仍须受内置沙盒约束的高危内置工具（同名自定义实现不得把"本应 dangerous"的操作
# 变成免确认直行）：覆盖这些名字时，execute_tool 先用内置规则评估，dangerous 不允许被
# 自定义 handler 豁免。
_CUSTOM_SANDBOX_NAMES = frozenset({
    "run_command", "delete_file", "write_file", "edit_file", "search_replace",
    "insert_lines", "new_project", "fast_download", "create_docx", "create_pptx",
    "create_xlsx", "uninstall_app", "migrate_app",
    # 办公文档编辑（写类）：覆盖实现亦不得绕过危险操作确认
    "edit_docx", "edit_pptx", "edit_xlsx",
})

# 内置 WebView 浏览器桥（GUI 侧通过 register_builtin_browser 注入）。
# AI 的 browser_* 工具优先在应用内置多标签浏览器内操作，不再拉起外部浏览器。
_BUILTIN_BROWSER = None


def register_builtin_browser(bridge_or_controller):
    """GUI 装配时注入内置浏览器控制器。传 None 则清除（回退外置浏览器）。"""
    global _BUILTIN_BROWSER
    _BUILTIN_BROWSER = bridge_or_controller


def _builtin_browser_ctl():
    """取内置浏览器控制器对象（未装配/不可用时返回 None）。"""
    c = _BUILTIN_BROWSER
    if c is None:
        return None
    get = getattr(c, "get", None)
    if callable(get):
        try:
            return get()
        except Exception:
            return None
    return c


def _browser_ctl():
    """浏览器控制器统一入口：内置优先，未装配时回退独立（外置）浏览器实例。"""
    b = _builtin_browser_ctl()
    if b is not None:
        return b
    return _sub("agent_browser").controller()


def register_custom_tools(tools: list, handler=None, source: str = "_default") -> tuple:
    """注册工作流自定义工具（同名覆盖内置 + 新增）。
    按来源工作流独立存储：不同工作流的引擎读各自的 tools.py，避免多会话并发相互覆盖。
    返回 (注册数, 来源工作流名)。写侧加锁，保证注册项与 _CUSTOM_SOURCE 原子更新，
    避免多会话并发热插拔切换时读到半状态。"""
    global _CUSTOM_SOURCE
    key = source or "_default"
    with _CUSTOM_LOCK:
        _CUSTOM_REGISTRY[key] = (list(tools or []), handler)
        _CUSTOM_SOURCE = key
        n = len(_CUSTOM_REGISTRY[key][0])
    return n, key


def _wf_current() -> str:
    """当前线程所属工作流（引擎任务线程在 run() 开头设置，未设返回空）。"""
    try:
        return _wf()._current_workflow() or ""
    except Exception:
        return ""


def custom_tool_names(workflow: str = "") -> frozenset:
    """指定/当前工作流已注册的自定义工具名集合（供引擎任务裁剪时保留，保证可调用性）。"""
    entry = _custom_entry(workflow or None)
    if not entry:
        return frozenset()
    tools, _ = entry
    names = []
    for td in (tools or []):
        if not isinstance(td, dict):
            continue
        fn = td.get("function")
        if isinstance(fn, dict) and fn.get("name"):
            names.append(fn["name"])
    return frozenset(names)


def _custom_entry(workflow=None):
    """取指定/当前工作流的自定义工具 (tools, handler)。
    优先显式参数 → 其次引擎任务线程当前工作流 → 否则回退最近注册。
    已明确工作流但未注册 → 返回空（保证不同工作流严格隔离，不泄漏他人工具）。"""
    key = workflow if workflow is not None else _wf_current()
    with _CUSTOM_LOCK:
        if key:
            return _CUSTOM_REGISTRY.get(key, ([], None))
        return _CUSTOM_REGISTRY.get(_CUSTOM_SOURCE, ([], None))


def _custom_names(workflow=None) -> set:
    return {t.get("function", {}).get("name") for t in _custom_entry(workflow)[0]}


def _custom_missing_required(name: str, args: dict, workflow=None) -> list:
    """按自定义工具 schema 校验必填参数"""
    for t in _custom_entry(workflow)[0]:
        fn = t.get("function") or {}
        if fn.get("name") != name:
            continue
        req = ((fn.get("parameters") or {}).get("required")) or []
        return [str(r) for r in req if not args.get(r)]
    return []


def _run_custom_tool(name: str, args: dict, allow_dangerous: bool, workflow=None) -> dict:
    """执行自定义工具（同名覆盖内置 / 新增）；handler 返回 None 或非 dict 视为未处理"""
    _tools, _handler = _custom_entry(workflow)
    if _handler is None:
        return _blocked(f"[自定义工具 {name}] 未注册执行器")
    missing = _custom_missing_required(name, args, workflow)
    if missing:
        return _blocked(f"[工具参数缺失] {name} 缺少必填参数：{', '.join(missing)}")
    try:
        res = _handler(name, args, allow_dangerous)
    except Exception as e:
        return _blocked(f"[自定义工具 {name}] {e}")
    if isinstance(res, dict):
        return res
    return _blocked(f"[自定义工具 {name}] 返回值格式错误")


# 本地记忆文件（AI 长期记忆，markdown 格式）
MEMORY_FILE = app_identity.data_root() / "agent" / "memory.md"

# ------------------------------------------------------------
# 工作目录（面板"选择工作目录"设置，QSettings 持久化）：
# 查找/创建/修改/删除/读取文件与运行命令优先在此目录执行
# ------------------------------------------------------------
WORKDIR: str = ""


def set_workdir(path: str):
    global WORKDIR
    WORKDIR = (path or "").strip()


def get_workdir() -> str:
    return WORKDIR


def _resolve(path: str) -> Path:
    """路径解析：空 → 工作目录；相对 → 工作目录/相对路径；绝对 → 原样"""
    raw = os.path.expandvars(os.path.expanduser((path or "").strip()))
    if not raw:
        return Path(WORKDIR) if WORKDIR else Path.cwd()
    p = Path(raw)
    if p.is_absolute():
        return p
    return (Path(WORKDIR) / p) if WORKDIR else p

# ------------------------------------------------------------
# 后台命令注册表：run_command 超时未结束（未开强制退出）的命令转入后台，
# AI 可用 check_command 轮询进度。reader 线程持续排空管道，防止缓冲填满阻塞进程。
# ------------------------------------------------------------
_running_cmds: dict = {}          # cmd_id -> 记录
_cmds_lock = threading.Lock()     # 保护 _running_cmds 的增删查（多任务线程并发轮询/注册）
_cmd_seq = itertools.count(1)     # 自增命令编号
_MAX_BG_AGE = 3600                # 后台命令最长存活 1 小时（防孤儿进程无限占用资源）
_CREATE_NO_WINDOW = 0x08000000


def _cleanup_running_cmds():
    """清理已退出超龄的后台命令：移除注册表条目并强制结束仍存活的孤儿进程。"""
    now = time.time()
    with _cmds_lock:
        for i, r in list(_running_cmds.items()):
            if r["proc"].poll() is None:
                if now - r.get("t0", now) > _MAX_BG_AGE:
                    _kill_process_tree(r["proc"].pid)   # 超龄仍存活 → 强杀防孤儿
                    del _running_cmds[i]
            else:
                del _running_cmds[i]   # 已退出，仅清理僵尸条目


def _console_encoding() -> str:
    """控制台程序输出编码：GetOEMCP 获取 cmd 实际代码页（中文系统 cp936），
    避免 Python UTF-8 模式下按 utf-8 解码 GBK 输出导致乱码/解码异常"""
    try:
        import ctypes
        cp = ctypes.windll.kernel32.GetOEMCP()
        if cp:
            return f"cp{cp}"
    except Exception:
        pass
    return locale.getpreferredencoding(False)


def _kill_process_tree(pid: int) -> bool:
    """taskkill /T 结束整个进程树（shell 启动的进程常有子进程）"""
    try:
        subprocess.run(f"taskkill /PID {pid} /T /F", shell=True,
                       capture_output=True, text=True, timeout=15)
        return True
    except Exception:
        return False


def _decode_robust(raw: bytes) -> str:
    """子进程/文件字节 → 文本：UTF-8 优先（现代工具主流），失败回退控制台 OEM 代码页
    （中文系统 GBK），避免 UTF-8 输出被按 GBK 解码成乱码、或 GBK 输出解码崩溃"""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig", errors="replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode(_console_encoding(), errors="replace")


def _drain_pipe(pipe, lines: list, lock: threading.Lock):
    """后台线程逐行读取管道（二进制）并存入共享缓冲（防管道填满导致进程阻塞）"""
    try:
        for line in iter(pipe.readline, b""):
            with lock:
                lines.append(line)
    except Exception:
        pass
    finally:
        try:
            pipe.close()
        except Exception:
            pass


def _collect(rec: dict) -> str:
    """汇总后台命令的已收集输出（stdout + stderr，完整返回）"""
    with rec["lock"]:
        out = _decode_robust(b"".join(rec["out"])).strip()
        err = _decode_robust(b"".join(rec["err"])).strip()
    text = out
    if err:
        text += f"\n[stderr] {err}" if text else f"[stderr] {err}"
    return text

# ---------- 工具定义（LLM 可见） ----------
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "find_app",
            "description": "快速查找已安装应用（扫描开始菜单/桌面快捷方式/注册表，秒查带缓存），"
                           "返回可启动的完整路径候选。当用户要打开某个应用而你不确定其确切名称/"
                           "路径时使用，无需逐层截图找图标。",
            "parameters": {"type": "object",
                           "properties": {
                               "query": {"type": "string", "description": "应用名称，如 微信/记事本/chrome"},
                               "limit": {"type": "integer", "description": "最多返回候选数，默认 10"}},
                           "required": ["query"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "在目录中按文件名模糊查找文件（并行遍历，找到足够结果即停止），"
                           "返回匹配的文件完整路径列表。当需要定位某个文件而不知道确切路径时使用。"
                           "支持扩展名过滤（ext）、大小写（case_sensitive）与排除目录（exclude）"
                           "缩小范围；按内容找代码/关键字请用 grep / search_code。",
            "parameters": {"type": "object",
                           "properties": {
                               "query": {"type": "string", "description": "文件名关键字，如 报告/photo/setup"},
                               "folder": {"type": "string", "description": "限定搜索目录（可选，默认用户常用目录；给定则仅在该目录内）"},
                               "limit": {"type": "integer", "description": "最多返回条数，默认 30"},
                               "ext": {"type": "string", "description": "扩展名过滤（可选，逗号分隔，如 'py,md' 或 '*.py'）；不传不限"},
                               "case_sensitive": {"type": "boolean", "description": "文件名匹配是否区分大小写，默认 false"},
                               "exclude": {"type": "string", "description": "排除关键字（可选，逗号分隔，路径或名称子串命中即跳过，如 'node_modules,build'）"}},
                           "required": ["query"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "按文件【内容】检索（支持正则或普通关键字），返回「路径:行号: 匹配行」清单。"
                           "当需要定位某个词句/代码在哪些文件里、或确认某项实现是否存在时使用；"
                           "若只记得文件名，用 search_files（按文件名）。默认在工作目录内检索。"
                           "context 可附加命中行前后各 N 行的上下文，逐行精确定位代码。",
            "parameters": {"type": "object",
                           "properties": {
                               "pattern": {"type": "string", "description": "检索关键字或正则表达式，如 def foo / TODO / 'class User'"},
                               "folder": {"type": "string", "description": "限定搜索目录（可选，默认工作目录）"},
                               "glob": {"type": "string", "description": "文件过滤（可选，逗号分隔，如 '*.py,*.md'）；不传则检索全部文本文件"},
                               "max_results": {"type": "integer", "description": "最多返回行数，默认 50"},
                               "case_sensitive": {"type": "boolean", "description": "是否区分大小写，默认 false"},
                               "context": {"type": "integer", "description": "命中行前后各附加的行数（可选，默认 0），如 context=2 表示连带前后 2 行一起输出"},
                               "line_numbers": {"type": "boolean", "description": "是否输出行号（可选，默认 true）；false 时仅输出 path: 内容"}},
                           "required": ["pattern"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": "代码检索：按内容定位代码行（相对路径 + 行号 + 上下文），按文件分组输出。"
                           "默认只检索代码/配置类文件（py/kt/java/js/ts/go/rs/c/html/css/json/md 等）；"
                           "name 可选：要求文件名包含该关键字，可缩小到具体文件/模块。"
                           "找文件用 search_files，找任意文本关键字用 grep。",
            "parameters": {"type": "object",
                           "properties": {
                               "pattern": {"type": "string", "description": "检索关键字或正则表达式，如 'def foo' / 'class User' / 'FIXME'"},
                               "folder": {"type": "string", "description": "限定搜索目录（可选，默认工作目录）"},
                               "glob": {"type": "string", "description": "文件过滤（可选，逗号分隔，如 '*.py,*.kt'）；不传则默认只查代码类扩展名"},
                               "max_results": {"type": "integer", "description": "最多返回行数，默认 50"},
                               "case_sensitive": {"type": "boolean", "description": "是否区分大小写，默认 false"},
                               "context": {"type": "integer", "description": "命中行前后各附加的行数（可选，默认 2）"},
                               "name": {"type": "string", "description": "文件名关键字（可选）：只检索文件名包含该词的代码文件"}},
                           "required": ["pattern"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": "当用户需求不明确、缺少关键信息（如目标文件路径、目标对象、期望结果）时，"
                           "用此工具向用户提问并等待回答。禁止在信息不足时猜测执行，必须先提问。",
            "parameters": {"type": "object",
                           "properties": {
                               "question": {"type": "string", "description": "要问用户的问题（简洁明确）"},
                               "options": {"type": "array", "items": {"type": "string"},
                                           "description": "建议选项，用户可直接选择（可为空数组表示自由回答）"},
                               "multi_select": {"type": "boolean",
                                                "description": "是否允许多选，默认 false"}},
                           "required": ["question"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "在系统终端执行命令（受沙盒约束）。危险命令（删除/格式化/关机等）会被拒绝。"
                           "内置沙盒 Node.js/Python 运行时（已随安装程序预置，本地直接可用的便携版，"
                           "与用户环境隔离、不写系统 PATH、不在对话内联网下载）：命令命中 node/npm/npx、"
                           "python/py/pip 时可直接使用，如 node --version、python script.py、"
                           "npm install、pip install requests。"
                           "默认等待 wait 秒（默认 5）：期间持续收集输出；若 wait 秒内未完成，"
                           "force_quit=true 则强制结束进程树，false 则转入后台运行，返回命令 ID，"
                           "之后用 check_command 轮询进度。启动 GUI 应用建议 wait=1。",
            "parameters": {"type": "object",
                           "properties": {
                               "command": {"type": "string", "description": "要执行的命令"},
                               "wait": {"type": "integer",
                                        "description": "等待秒数，默认 5。长任务可调大以等待更多输出"},
                               "force_quit": {"type": "boolean",
                                              "description": "是否开启超时强制退出：wait 秒内未完成则强制结束进程树。"
                                                             "默认 false（转入后台运行，可轮询）。预计会长时间挂起/无输出的命令建议开启"},
                               "cwd": {"type": "string",
                                       "description": "命令执行的工作目录（可选，默认工作目录）"},
                               "stdin": {"type": "string",
                                         "description": "要写入命令标准输入的文本（可选，交互式命令用）"},
                               "max_output": {"type": "integer",
                                              "description": "返回输出最大字符数（可选，默认不限）"}},
                           "required": ["command"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_command",
            "description": "查询后台运行命令的进度与最新输出（run_command 超时未结束且未开强制退出时转入后台的命令）。"
                           "返回是否仍在运行、已产生的输出；若已结束则返回最终输出与退出码。"
                           "不传 cmd_id 时列出全部后台命令。",
            "parameters": {"type": "object",
                           "properties": {
                               "cmd_id": {"type": "integer",
                                          "description": "后台命令 ID（run_command 返回的 id），不传则列出全部"}},
                           "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取文本文件内容（不限制大小）。支持 offset/limit 按行分段读取大文件："
                           "offset 起始行号（从 1 开始）、limit 返回行数。"
                           "number_lines=true 时每行前附行号（如 'L12: ...'），便于精确定位/引用代码行。",
            "parameters": {"type": "object",
                           "properties": {"path": {"type": "string"},
                                          "offset": {"type": "integer", "description": "起始行号（1-based，可选）"},
                                          "limit": {"type": "integer", "description": "最多返回行数（可选）"},
                                          "number_lines": {"type": "boolean", "description": "每行前附行号（可选，默认 false）"}},
                           "required": ["path"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "创建或覆盖写入文本文件（目录不存在自动创建，最大 500KB）。"
                           "参数名必须为 path 与 content：content 是写入文件的完整内容文本"
                           "（写 HTML/代码/任何文本时内容一律放在 content 字段，"
                           "不要用 html、code、source、file 等其他键名）。"
                           "append=true 时追加到文件末尾；内容很长时可分段多次调用"
                           "（先写主体，再用 append=true 追加后续部分），避免单次输出超长被截断。",
            "parameters": {"type": "object",
                           "properties": {"path": {"type": "string",
                                                   "description": "文件路径（相对路径基于工作目录）"},
                                          "content": {"type": "string",
                                                      "description": "要写入文件的完整内容（文本字符串）"},
                                          "append": {"type": "boolean",
                                                     "description": "true 追加写入，默认 false 覆盖"}},
                           "required": ["path", "content"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "编辑文件：把文件中的 old_text 精确替换为 new_text（多行匹配亦可）。"
                           "参数名必须为 path、old_text、new_text（old_text 是文件中已有的原内容，"
                           "new_text 是替换后的新内容，二者都是文本字符串，不是文件名）。"
                           "目标内容在文件中出现多处时会拒绝并提示出现次数，防止误替换；"
                           "多行或上下文匹配请用 search_replace。每次编辑前自动备份，可用 undo_file 回滚。",
            "parameters": {"type": "object",
                           "properties": {"path": {"type": "string",
                                                   "description": "文件路径（相对路径基于工作目录）"},
                                          "old_text": {"type": "string",
                                                       "description": "文件中已有的原内容，将被替换（须与文件精确一致）"},
                                          "new_text": {"type": "string",
                                                       "description": "替换后的新内容"}},
                           "required": ["path", "old_text", "new_text"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_replace",
            "description": "多行精确替换文件内容：把 old_text（可含换行）替换为 new_text。"
                           "参数名必须为 path、old_text、new_text（old_text 是文件中已有的原内容，"
                           "new_text 是替换后的新内容）。"
                           "默认要求唯一匹配（出现多处拒绝并返回次数）；count=all 时替换全部出现。"
                           "自动备份，可用 undo_file 回滚。",
            "parameters": {"type": "object",
                           "properties": {"path": {"type": "string",
                                                   "description": "文件路径（相对路径基于工作目录）"},
                                          "old_text": {"type": "string",
                                                       "description": "文件中已有的原内容（可含换行），将被替换（须与文件精确一致）"},
                                          "new_text": {"type": "string",
                                                       "description": "替换后的新内容"},
                                          "count": {"type": "string",
                                                    "enum": ["once", "all"],
                                                    "description": "once 仅限唯一匹配（默认）；all 替换全部出现"}},
                           "required": ["path", "old_text", "new_text"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "insert_lines",
            "description": "按行号向文件插入文本：在指定行号（1-based）处插入 content（可含多行），"
                           "原该行及之后的内容整体下移。line=文件总行数+1 等价于在末尾追加。"
                           "适合向代码中精确插入函数/导入/配置项（不用重写整个文件）。"
                           "写前自动备份，可用 undo_file 回滚。",
            "parameters": {"type": "object",
                           "properties": {"path": {"type": "string",
                                                   "description": "文件路径（相对路径基于工作目录）"},
                                          "line": {"type": "integer",
                                                   "description": "插入位置的行号（1-based）：新内容插到该行之前，缺省/越界时末尾追加"},
                                          "content": {"type": "string",
                                                      "description": "要插入的内容（可含换行，逐行插入）"}},
                           "required": ["path", "content"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "undo_file",
            "description": "回滚文件到最近一次写操作（write_file/edit_file/search_replace/insert_lines）之前的状态。"
                           "误改文件时使用，返回回滚前的内容摘要。",
            "parameters": {"type": "object",
                           "properties": {"path": {"type": "string"}},
                           "required": ["path"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "删除文件或空目录（工作目录优先）。删除系统关键目录内的内容仍被沙盒拒绝；"
                           "非空目录请用 run_command 精确处理。",
            "parameters": {"type": "object",
                           "properties": {"path": {"type": "string",
                                                   "description": "要删除的文件或空目录路径（相对路径基于工作目录）"}},
                           "required": ["path"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "列出目录内容（仅允许用户目录，最多 200 项，带 DIR/FILE 标记），用于探索文件结构。",
            "parameters": {"type": "object",
                           "properties": {"path": {"type": "string"}},
                           "required": ["path"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": "把任务中的关键信息（用户偏好、重要结论、文件路径、约定等）追加保存到本地记忆文件 "
                           "memory.md（自动带时间戳，单条 ≤8000 字符）。值得长期记住的内容请主动保存。",
            "parameters": {"type": "object",
                           "properties": {"content": {"type": "string"}},
                           "required": ["content"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "load_memory",
            "description": "读取本地记忆文件 memory.md 的完整内容。开始新任务或需要回忆过往信息时，"
                           "由你自行决定是否调用。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    # ---------- 任务清单（TODO） ----------
    {
        "type": "function",
        "function": {
            "name": "update_todo",
            "description": "创建/更新任务清单（多步任务的进度管理）：每次全量提交所有任务（含已完成）。"
                           "持久化到本地，引擎每轮会把未完成任务摘要注入上下文，上下文压缩后进度不丢失。"
                           "开始多步任务时先创建清单，每完成一步更新对应状态。"
                           "status: pending/in_progress/completed。",
            "parameters": {"type": "object",
                           "properties": {
                               "todos": {"type": "array",
                                         "description": "全部任务列表（每次全量提交，缺项即视为已移除）",
                                         "items": {"type": "object",
                                                   "properties": {
                                                       "title": {"type": "string", "description": "任务描述"},
                                                       "status": {"type": "string",
                                                                  "enum": ["pending", "in_progress", "completed"],
                                                                  "description": "状态，默认 pending"}},
                                                   "required": ["title"]}}},
                           "required": ["todos"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_todo",
            "description": "查看当前任务清单及每项状态（多步任务进度）。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    # ---------- 项目脚手架 ----------
    {
        "type": "function",
        "function": {
            "name": "new_project",
            "description": "创建项目脚手架：在指定目录生成基础结构（README.md、.gitignore、src/ 等），"
                           "kind=python/node/web 时附带对应模板文件。开始新项目/新任务目录时使用。",
            "parameters": {"type": "object",
                           "properties": {
                               "path": {"type": "string", "description": "项目目录（绝对路径或基于工作目录）"},
                               "kind": {"type": "string",
                                        "enum": ["generic", "python", "node", "web"],
                                        "description": "项目类型，默认 generic"},
                               "name": {"type": "string", "description": "项目名称（默认取目录名）"}},
                           "required": ["path"]},
        },
    },
    # ---------- 系统信息（原 MCP server 工具内置化） ----------
    {
        "type": "function",
        "function": {
            "name": "system_info",
            "description": "获取本机系统信息：主机名、系统版本（可区分 Win10/Win11）、CPU 核心数、物理内存、Python 版本。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "获取当前系统时间（YYYY-MM-DD HH:MM:SS）。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "env_var",
            "description": "读取指定环境变量的值（如 PATH、SystemRoot）。",
            "parameters": {"type": "object",
                           "properties": {"name": {"type": "string", "description": "环境变量名"}},
                           "required": ["name"]},
        },
    },
    # ---------- zhuzhu_Copilot 能力内置化 ----------
    {
        "type": "function",
        "function": {
            "name": "optimize_memory",
            "description": "一键清理系统内存：终止可安全退出的后台进程、压缩工作集并清理内存。"
                           "ask/edit 模式执行前会请用户确认；YOLO 模式直接执行。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "uninstall_app",
            "description": "卸载已安装应用（优先调用应用自带卸载器，再清理数据目录/注册表/快捷方式）。"
                           "需要先扫描已安装应用并匹配名称；ask/edit 模式执行前会请用户确认，"
                           "YOLO 模式直接执行（不受限制）。",
            "parameters": {"type": "object",
                           "properties": {"name": {"type": "string", "description": "应用名称（支持模糊匹配）"}},
                           "required": ["name"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "migrate_app",
            "description": "把已安装应用迁移到其他盘符（移动主目录/数据目录并更新注册表、快捷方式）。"
                           "需要先扫描应用匹配名称；ask/edit 模式执行前会请用户确认，"
                           "YOLO 模式直接执行（不受限制）。",
            "parameters": {"type": "object",
                           "properties": {
                               "name": {"type": "string", "description": "应用名称（支持模糊匹配）"},
                               "target": {"type": "string", "description": "目标路径，如 D:\\Apps\\微信"}},
                           "required": ["name", "target"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fast_download",
            "description": "多段并发高速下载文件到指定目录（自动探测文件名，支持断点续传）。",
            "parameters": {"type": "object",
                           "properties": {
                               "url": {"type": "string", "description": "下载地址"},
                               "dest_dir": {"type": "string",
                                            "description": "保存目录，留空用工作目录"}},
                           "required": ["url"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "联网请求指定 URL（网页 HTML / JSON 接口 / raw 文件 / REST API），返回响应文本。"
                           "默认 GET；可指定 method/headers/body 发起 POST/PUT/DELETE 等调用 API。"
                           "keyword 指定后只返回页面内包含该关键词的段落（页面内精确检索），"
                           "适合搜索抓到链接后直接定位所需信息。",
            "parameters": {"type": "object",
                           "properties": {
                               "url": {"type": "string", "description": "http/https 地址"},
                               "method": {"type": "string",
                                          "description": "请求方法：GET/POST/PUT/DELETE/PATCH，默认 GET"},
                               "headers": {"type": "object",
                                           "description": "请求头字典，如 {\"Authorization\": \"Bearer xxx\"}"},
                               "body": {"type": "string",
                                        "description": "请求体（POST/PUT/PATCH 时使用），JSON 字符串或原始文本"},
                               "keyword": {"type": "string",
                                           "description": "（可选）页面内检索关键词：返回文本只保留包含该词的段落，便于从长页/文档中精确提取信息"},
                               "max_chars": {"type": "integer",
                                             "description": "返回内容最大字符数，默认 8000"}},
                           "required": ["url"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_open",
            "description": "在内置浏览器（应用内多标签 WebView）打开并接管浏览，用户可直接在应用里看到 AI 的浏览与操作，"
                           "绝不拉起外部浏览器。浏览器任务（打开网页/登录/填表/抓取/自动操作网页）"
                           "第一步先 browser_open，之后用 browser_navigate/browser_snapshot/browser_click/"
                           "browser_type/browser_eval/browser_html 完成操作。",
            "parameters": {"type": "object",
                           "properties": {
                               "engine": {"type": "string",
                                          "description": "（可选）优先使用 edge 或 chrome，留空自动找可用浏览器"},
                               "headless": {"type": "boolean",
                                            "description": "（可选）无头模式（不显示窗口），默认 false"}},
                           "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_navigate",
            "description": "在内置浏览器当前标签页打开网页（自动补全 http/https）。用 browser_open 接管后使用。",
            "parameters": {"type": "object",
                           "properties": {"url": {"type": "string",
                                                  "description": "要打开的网址，如 https://www.baidu.com"}},
                           "required": ["url"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_snapshot",
            "description": "截取内置浏览器当前页面并返回可交互元素语义清单 [id] (标签) 文字。"
                           "每步操作前/操作后先 browser_snapshot 看页面状态与元素，"
                           "点击/输入用 browser_click(id) / browser_type(text, id) 按编号精确操作。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_click",
            "description": "在内置浏览器中点击元素。支持三种定位（任选其一）："
                           "① id=browser_snapshot 清单里的元素编号；② text=元素文字（按钮/链接/输入框/选项）；"
                           "③ selector=CSS 选择器精确定位（如 #submit / .btn-primary / form button）。"
                           "点击后返回最新页面截图。",
            "parameters": {"type": "object",
                           "properties": {
                               "id": {"type": "integer",
                                      "description": "（推荐）browser_snapshot 清单里的元素编号 [id]"},
                               "text": {"type": "string",
                                        "description": "（推荐）目标元素文字，与 id/selector 三选一"},
                               "selector": {"type": "string",
                                            "description": "（推荐）CSS 选择器精确定位元素，与 id/text 三选一"},
                               "button": {"type": "string",
                                          "description": "鼠标键：left/right/middle，默认 left"}},
                           "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_type",
            "description": "在内置浏览器的输入框中输入文本。支持三种定位（任选其一）："
                           "① id=browser_snapshot 清单编号；② target=输入框文字（占位符/标签）；"
                           "③ selector=CSS 选择器精确定位输入框。先点击聚焦再输入。中文/英文/数字均可。",
            "parameters": {"type": "object",
                           "properties": {
                               "text": {"type": "string", "description": "要输入的文本"},
                               "id": {"type": "integer",
                                      "description": "（推荐）browser_snapshot 清单里的输入框编号 [id]"},
                               "target": {"type": "string",
                                          "description": "（推荐）输入框文字（占位符/标签），与 id/selector 三选一"},
                               "selector": {"type": "string",
                                            "description": "（推荐）CSS 选择器定位输入框，与 id/target 三选一"}},
                           "required": ["text"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_scroll",
            "description": "滚动内置浏览器页面或指定容器。direction=up/down/left/right/top/bottom"
                           "（top 滚到顶部、bottom 滚到底部，默认 down）；amount 指定像素步长"
                           "（默认滚动一屏的 80%）；eid/selector 可指定滚动容器（留空滚动整个页面）。"
                           "页面内容超出屏幕（列表/长文/评论区）需查看更多时使用。",
            "parameters": {"type": "object",
                           "properties": {
                               "direction": {"type": "string",
                                             "description": "滚动方向：up/down/left/right/top/bottom，默认 down"},
                               "amount": {"type": "integer",
                                          "description": "（可选）滚动像素步长，默认一屏 80% 高度"},
                               "id": {"type": "integer",
                                      "description": "（可选）要滚动的容器编号 [id]"},
                               "selector": {"type": "string",
                                            "description": "（可选）要滚动的容器 CSS 选择器"}},
                           "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_eval",
            "description": "在内置浏览器页面执行 JavaScript 并返回结果。可直接读取/修改 DOM（解析 HTML/CSS）、"
                           "调用页面函数、抓取数据、模拟操作。返回结果文本。",
            "parameters": {"type": "object",
                           "properties": {"js": {"type": "string",
                                                 "description": "要执行的 JavaScript 代码，return 值会作为结果返回"}},
                           "required": ["js"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_html",
            "description": "读取内置浏览器当前页面的 HTML/文本内容。传 selector（CSS 选择器）可只读取指定区域，"
                           "留空返回整页文本摘要。用于分析网页内容、确认操作结果、抓取数据。",
            "parameters": {"type": "object",
                           "properties": {"selector": {"type": "string",
                                                       "description": "（可选）CSS 选择器，如 #content / .price / form"}},
                           "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_close",
            "description": "结束浏览器会话。内置浏览器与应用内预览面板共用同一多标签 WebView，无需真正关闭，"
                           "浏览器任务完成后调用以收尾。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_tabs",
            "description": "列出独立浏览器内所有页面标签（含 window.open 弹出的新窗口/新标签）。"
                           "当页面弹窗/新窗口里的元素（如关闭按钮）在 browser_snapshot 中找不到时，"
                           "先 browser_tabs 查看弹窗标签，再用 browser_switch_tab 切过去操作。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_switch_tab",
            "description": "切换到指定编号的页面标签（弹窗/新窗口）。切换后 browser_snapshot/browser_click 等"
                           "操作都针对该标签。弹窗里的关闭按钮：切到弹窗标签后 browser_snapshot 找到关闭按钮再点击。",
            "parameters": {"type": "object",
                           "properties": {
                               "id": {"type": "integer",
                                      "description": "browser_tabs 返回的标签页编号（从 1 开始）"}},
                           "required": ["id"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clipboard",
            "description": "读写系统剪贴板：read 读取当前剪贴板文本，write 把指定文本写入剪贴板"
                           "（复制/粘贴场景，或读取用户已复制的内容）。",
            "parameters": {"type": "object",
                           "properties": {
                               "action": {"type": "string", "description": "read 读取 / write 写入"},
                               "text": {"type": "string", "description": "action=write 时要写入的文本"}},
                           "required": ["action"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extract_text",
            "description": "提取文档纯文本：支持 txt/md/log/json/csv/docx/pptx/xlsx"
                           "（docx/pptx/xlsx 直接解析 zip+XML，无需第三方库），PDF 需先 pip install pypdf。"
                           "读取文档内容、分析表格数据时使用。",
            "parameters": {"type": "object",
                           "properties": {
                               "path": {"type": "string", "description": "文档文件路径（相对路径基于工作目录）"}},
                           "required": ["path"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_docx",
            "description": "生成 Word 文档（.docx）。paragraphs 段落支持轻量标记：'# '/'## '/'### ' 分级标题、"
                           "'- '项目符号、'1. '编号列表、'> '引用块、连续 '| 列1 | 列2 |' 行自动成表（首行为表头）、"
                           "'[toc]' 插入目录（打开时自动更新）、'[pagebreak]' 分页。"
                           "style.cover={subtitle, author, date, image} 自动生成独立封面并分页到正文；"
                           "style.header_text 显示页眉文字，页脚自动带页码。"
                           "适合正式报告/项目方案/合同/说明文档等文字型文档。支持 style 自定义配色/字体/行距/纸张方向，"
                           "不传则用默认商务风。",
            "parameters": {"type": "object",
                           "properties": {
                               "path": {"type": "string", "description": "保存路径（.docx，相对路径基于工作目录）"},
                               "title": {"type": "string", "description": "文档大标题（可选）"},
                               "paragraphs": {"type": "array",
                                              "description": "段落文本列表，每项一个字符串，支持轻量标记："
                                                              "'# '一级标题 / '## '二级标题 / '### '三级标题 / '- '项目符号",
                                              "items": {"type": "string"}},
                               "images": {"type": "array",
                                          "description": "图片列表（可选，相对路径基于工作目录）。每项为路径字符串，"
                                                          "或 {path: 图片路径, align: left/center/right 水平对齐, width: 宽(英寸)}；"
                                                          "默认居中、宽6英寸，自动等比缩放防溢出页面",
                                          "items": {"type": "object",
                                                    "properties": {
                                                        "path": {"type": "string", "description": "图片路径"},
                                                        "align": {"type": "string", "description": "水平对齐：left/center/right，默认center"},
                                                        "width": {"type": "number", "description": "图片宽度（英寸），默认6"}},
                                                    "required": ["path"]}},
                               "wordart": {"type": "array",
                                          "description": "艺术字（样式化大字）列表（可选）：每项 {text: 文字, "
                                                          "size: 字号(默认36), color: 颜色HEX, font: 字体名, "
                                                          "align: left/center/right}，大幅加粗彩色文字，用于标题/强调",
                                          "items": {"type": "object",
                                                    "properties": {
                                                        "text": {"type": "string", "description": "艺术字文字"},
                                                        "size": {"type": "integer", "description": "字号，默认36"},
                                                        "color": {"type": "string", "description": "颜色HEX，默认主题主色"},
                                                        "font": {"type": "string", "description": "字体名，默认正文用字体"},
                                                        "align": {"type": "string", "description": "水平对齐：left/center/right，默认center"}},
                                                    "required": ["text"]}},
                               "style": {"type": "object",
                                         "description": "样式配置（可选）。不传则用默认商务风；传了可大胆自定义："
                                                         "theme=配色主题(business深蓝/green墨绿/warm橙棕/purple紫/"
                                                         "tech科技蓝/dark暗色/pastel浅蓝/red朱红/black-gold黑金)，"
                                                         "base_color=主色HEX，heading_color=标题色HEX，"
                                                         "text_color=正文色HEX，font_name=字体名(如'宋体'/'仿宋'/'楷体')，"
                                                         "align=正文对齐(left/center/right/justify)，"
                                                         "line_spacing=行距倍数(1.0-2.0)，title_size=大标题字号，"
                                                         "body_size=正文字号，page=纸张方向(portrait/landscape)，"
                                                         "header_text=页眉文字，watermark=文字水印"
                                                         "{text: 水印文字, color: 颜色HEX, size: 字号(24-200)}，"
                                                         "cover=封面信息{subtitle副标题, author作者, date日期, "
                                                         "image封面图路径, style: 封面样式(centered居中(默认)/band横贯色带)}"}},
                           "required": ["path", "paragraphs"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": "AI 文生图：生成与主题匹配的图片素材，下载到本地并返回本地路径。"
                           "生成 Word/PPT/Excel 文档需要配图（汇报/产品介绍/感言/总结/宣传等）时，"
                           "**必须**先用本工具生成素材图，再把返回的本地路径作为 image 参数"
                           "传入 create_docx/create_pptx/create_xlsx。",
            "parameters": {"type": "object",
                           "properties": {
                               "prompt": {"type": "string",
                                          "description": "要生成的画面描述（英文/中文均可，写清主体、场景、风格、配色、构图）"},
                               "ratio": {"type": "string",
                                         "enum": ["1:1", "3:4", "4:3", "16:9", "9:16"],
                                         "description": "画面比例，默认 1:1"},
                               "dest_dir": {"type": "string",
                                            "description": "保存目录（可选，相对路径基于工作目录，默认工作目录/images）"}},
                           "required": ["prompt"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_pptx",
            "description": "生成 PowerPoint 演示文稿（.pptx）：首页标题 + 多页内容页。"
                           "**页数要够、内容要详**：主题类 PPT 建议 12-25 页（重要主题可更多），"
                           "每个分论点至少一页、页内要点 3-6 条完整句子，不要只写短语。"
                           "每页支持要点列表、彩色卡片、表格、图表(column/bar/line/pie)、图形排版(mindmap/flow/compare/cycle)、"
                           "插图、艺术字。layout 可选 left_image/right_image(图文分栏)、two_col(左右两栏，配 columns=[{title,bullets}...])、"
                           "center_highlight(居中大字强调页，配 highlight 大字)。"
                           "**配色要多元、页面要有图形排版**：不要每页都白底黑字，用 bg_color 换页底色、"
                           "用 cards 分区、用 table/chart/diagram 呈现结构化内容，需要配图时先用 generate_image 生成素材图再传 image；"
                           "深色页面会自动切换为浅色文字保证可读。"
                           "style.cover={subtitle, author, date, image} 补充封面副标题/作者/日期；style.footer 显示页脚文字。"
                           "**动画默认开启**：每页按页序轮换创意入场动画（fade淡入/wipe擦除/box方盒/circle圆/diamond菱形/"
                           "dissolve溶解/plus加号/checkerboard棋盘/randombar随机条/blinds百叶窗/wedge楔入/wheel风车/"
                           "fly飞入(可带_top/_bottom/_left/_right方向)/float浮入(_up/_down/_left/_right)/zoom缩放/"
                           "faded_zoom缩放淡入/"
                           "grow_turn旋转增长/pinwheel风车/swivel回旋/split劈裂/strips_upleft左上条纹/appear出现），"
                           "标题/图形/正文分层逐元素播放，默认 with 触发（一次点击后流畅级联），"
                           "可用 style.anim_trigger=click/after 与 style.anim_stagger=级联间隔ms 调节奏；"
                           "页级用 slide.anim={title/text/graphics: 效果名, trigger, stagger, max, "
                           "exit: 退场效果, enabled: false} 定制或整页关闭，整份关闭用 style.animation=false；"
                           "**退场**：style.exit_effect=效果名（如 fly_bottom/fade/zoom）让每页元素按逆序一次点击清场；"
                           "全局统一效果用 style.animation_effect=效果名。"
                           "bullets/cards.title/desc/diagram.items/chart.labels 等文本一律传字符串，禁止传对象，"
                           "避免生成未解析字面量；compare 的每个 items 元素为该方案面板 {title: 方案名, left: 优点一, right: 优点二}。"
                           "支持 style 自定义主题配色/封面布局/切换动画，不传则用默认商务风。",
            "parameters": {"type": "object",
                           "properties": {
                               "path": {"type": "string", "description": "保存路径（.pptx）"},
                               "title": {"type": "string", "description": "演示文稿标题（可选，用作首页）"},
                               "slides": {"type": "array",
                                          "description": "幻灯片列表，每项 {title, bullets(要点列表), "
                                                          "cards(彩色卡片数组), table(表格), chart(图表), "
                                                          "diagram(思维导图/流程图/对比图), image(插图), "
                                                          "wordart(艺术字), bg_color, title_color, "
                                                          "layout(left_image/right_image/two_col/center_highlight)}",
                                          "items": {"type": "object",
                                                    "properties": {
                                                        "title": {"type": "string", "description": "页标题"},
                                                        "bullets": {"type": "array",
                                                                    "description": "本页要点列表（每项为完整句子/段落，"
                                                                                    "可含'## '子标题、'- '子要点）",
                                                                    "items": {"type": "string"}},
                                                        "cards": {"type": "array",
                                                                  "description": "本页彩色卡片数组（关键词/数据并排展示），"
                                                                                  "每项 {title, desc, color}",
                                                                  "items": {"type": "object",
                                                                            "properties": {
                                                                                "title": {"type": "string", "description": "卡片标题"},
                                                                                "desc": {"type": "string", "description": "卡片说明文字"},
                                                                                "color": {"type": "string", "description": "卡片底色HEX（可选，默认主题浅色）"}},
                                                                            "required": ["title"]}},
                                                        "table": {"type": "object",
                                                                  "description": "本页表格（结构化数据）：{header: [列名], "
                                                                                  "rows: [[值,...],...], title: 表标题(可选)}",
                                                                  "properties": {
                                                                      "header": {"type": "array", "items": {"type": "string"},
                                                                                 "description": "表头列名"},
                                                                      "rows": {"type": "array",
                                                                               "description": "数据行二维数组",
                                                                               "items": {"type": "array", "items": {}}},
                                                                      "title": {"type": "string", "description": "表标题（可选）"}},
                                                                  "required": ["header", "rows"]},
                                                        "chart": {"type": "object",
                                                                  "description": "本页图表：{type: bar/column/line/pie/doughnut/"
                                                                                  "radar/scatter/combo, "
                                                                                  "labels: [分类], values: [数值], "
                                                                                  "title: 图标题(可选)}；scatter 用 points=[[x,y],..]；"
                                                                                  "combo 用 values2=[] 叠加折线",
                                                                  "properties": {
                                                                      "type": {"type": "string",
                                                                               "enum": ["bar", "column", "line", "pie", "doughnut",
                                                                                        "radar", "scatter", "combo"],
                                                                               "description": "bar/column/line/pie/doughnut/radar/scatter/combo"},
                                                                      "labels": {"type": "array", "items": {"type": "string"},
                                                                                 "description": "分类/点名称（散点图为点标签）"},
                                                                      "values": {"type": "array", "items": {"type": "number"},
                                                                                 "description": "数值（bar/column/line/pie/radar/combo 主序列）"},
                                                                      "values2": {"type": "array", "items": {"type": "number"},
                                                                                  "description": "combo 折线副序列（可选）"},
                                                                      "points": {"type": "array", "items": {"type": "array"},
                                                                                 "description": "scatter 数据点 [[x,y],...]（可选）"},
                                                                      "title": {"type": "string", "description": "图标题（可选）"}},
                                                                  "required": ["type", "labels", "values"]},
                                                        "diagram": {"type": "object",
                                                                    "description": "本页图形排版：{type: mindmap/flow/compare/cycle/"
                                                                                    "timeline, "
                                                                                    "center: 中心主题(可选), items: [节点/步骤/对比项], "
                                                                                    "title: 附加标题(可选)}",
                                                                    "properties": {
                                                                        "type": {"type": "string",
                                                                                 "enum": ["mindmap", "flow", "compare", "cycle",
                                                                                          "timeline"],
                                                                                 "description": "mindmap/flow/compare/cycle/timeline"},
                                                                        "center": {"type": "string", "description": "中心主题（mindmap 用）"},
                                                                        "items": {"type": "array",
                                                                                  "description": "节点/步骤，必须是字符串；"
                                                                                                  "compare 为方案面板数组 {title: 方案名, "
                                                                                                  "left: 优点一, right: 优点二}，"
                                                                                                  "第1/3/5..项落左栏、第2/4..项落右栏；"
                                                                                                  "timeline 为里程碑数组 {date: 日期, text: 描述}",
                                                                                  "items": {}},
                                                                        "title": {"type": "string", "description": "附加标题（可选）"}},
                                                                    "required": ["type", "items"]},
                                                        "image": {"type": "object",
                                                                  "description": "本页插图（可选）：路径字符串或 {path, "
                                                                                  "align: left/center/right, width: 宽(英寸)}",
                                                                  "properties": {
                                                                      "path": {"type": "string", "description": "图片路径"},
                                                                      "align": {"type": "string", "description": "水平对齐：left/center/right，默认center"},
                                                                      "width": {"type": "number", "description": "图片宽度（英寸），默认8"}},
                                                                  "required": ["path"]},
                                                        "wordart": {"type": "object",
                                                                    "description": "本页艺术字（可选）：{text, size, color}，"
                                                                                    "大幅加粗彩色装饰文字",
                                                                    "properties": {
                                                                        "text": {"type": "string", "description": "艺术字文字"},
                                                                        "size": {"type": "integer", "description": "字号，默认44"},
                                                                        "color": {"type": "string", "description": "颜色HEX，默认主题主色"}},
                                                                    "required": ["text"]},
                                                        "bg_color": {"type": "string",
                                                                     "description": "本页背景色 HEX（可选，覆盖全局背景）"},
                                                        "title_color": {"type": "string",
                                                                        "description": "本页标题色 HEX（可选，覆盖全局标题色）"},
                                                        "layout": {"type": "string",
                                                                   "enum": ["left_image", "right_image", "two_col",
                                                                            "center_highlight", "hero_stats"],
                                                                   "description": "本页布局：left_image/right_image（图文左右分栏）"
                                                                                   "、two_col（左右两栏）、center_highlight（居中大字强调）"
                                                                                   "、hero_stats（大数字指标卡，配 stats）"},
                                                        "stats": {"type": "array",
                                                                  "description": "layout=hero_stats 时大数字指标数组，每项 "
                                                                                  "{value: 大数字字符串, label: 指标名, color: 颜色HEX(可选)}",
                                                                  "items": {"type": "object",
                                                                            "properties": {
                                                                                "value": {"type": "string", "description": "大数字，如 \"42%\"、\"327万\""},
                                                                                "label": {"type": "string", "description": "指标名"},
                                                                                "color": {"type": "string", "description": "颜色HEX（可选）"}},
                                                                            "required": ["value", "label"]}},
                                                        "columns": {"type": "array",
                                                                    "description": "layout=two_col 时两栏内容，每项 "
                                                                                    "{title(可选), bullets}；缺省自动将 bullets 均分两栏",
                                                                    "items": {"type": "object",
                                                                              "properties": {
                                                                                  "title": {"type": "string",
                                                                                            "description": "栏标题（可选）"},
                                                                                  "bullets": {"type": "array",
                                                                                              "items": {"type": "string"}}},
                                                                              "required": ["bullets"]}},
                                                        "highlight": {"type": "string",
                                                                      "description": "layout=center_highlight 时页面中央的大字内容"
                                                                                      "（可选，缺省取第一条要点）"},
                                                        "anim": {"type": "object",
                                                                 "description": "本页动画定制（可选）：{title/text/graphics: "
                                                                                 "效果名(fade/wipe_down/wipe_up/wipe_left/"
                                                                                 "wipe_right/fly_top/fly_bottom/fly_left/fly_right/"
                                                                                 "zoom/faded_zoom/grow_turn/pinwheel/swivel/float_up/"
                                                                                 "float_down/float_left/float_right/box/circle/"
                                                                                 "diamond/dissolve/plus/"
                                                                                 "checkerboard/randombar_h/randombar_v/blinds/wedge/"
                                                                                 "wheel/split/strips_upleft/appear)，"
                                                                                 "trigger: click/with/after(缺省with=一次点击后流畅级联)，"
                                                                                 "stagger: 组内级联间隔ms(默认160)，"
                                                                                 "max: 每页最多动画元素数(默认12)，"
                                                                                 "exit: 本页整体退场效果名(如 fly_bottom/fade/zoom，"
                                                                                 "按逆序同一下点击清空页面)，"
                                                                                 "exit_effect: exit 的别名，"
                                                                                 "enabled: false 整页关闭动画}"}},
                                                    "required": ["title"]}},
                               "style": {"type": "object",
                                         "description": "样式配置（可选）。不传则用默认商务风；传了可大胆自定义："
                                                         "theme=配色方案(business深蓝/black-gold黑金/green墨绿/warm暖橙/"
                                                         "tech科技蓝/vivid明快/dark暗色/pastel浅色/red朱红/purple紫)，"
                                                         "cover_style=封面布局(solid纯色底/split左右分屏/centered居中)，"
                                                         "title_color=标题色HEX，bg_color=内容页背景色HEX，"
                                                         "accent=强调色HEX，font_name=字体名，"
                                                         "bullet_style=要点符号(dot/number/arrow/check)，"
                                                         "transition=页面切换动画(fade/push/wipe/split/cover/pull/zoom/"
                                                         "dissolve/circle/diamond/blinds/checker/wheel/comb/plus/"
                                                         "newsflash/cut/wedge/random)，"
                                                         "animation=元素动画总开关(默认true，false关闭整份)，"
                                                         "animation_effect=全局统一元素动画效果(fade/wipe_down/"
                                                         "fly_bottom/fly_left/zoom/faded_zoom/grow_turn/float_up/"
                                                         "float_left/float_right/"
                                                         "dissolve等，缺省按页轮换)，"
                                                         "anim_trigger=动画触发(click/with/after，默认with"
                                                         "=一次点击后流畅级联)，anim_stagger=级联间隔ms(默认160)，"
                                                         "exit_effect=每页整体退场效果名(如 fly_bottom/fade/zoom，"
                                                         "按逆序一次点击清空页面)，exit_trigger=退场触发方式，"
                                                         "title_size=页标题字号，body_size=要点字号，"
                                                         "footer=页脚文字，cover=封面信息"
                                                         "{subtitle副标题, author作者, date日期, image封面图路径}"}},
                           "required": ["path", "slides"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_xlsx",
            "description": "生成 Excel 工作簿（.xlsx）：多个工作表，每表 {name, rows, charts(可选), image(可选), "
                           "wordart(可选), column_widths(可选), format(可选)}。rows 二维数组：首行作表头；单元格可用 {v, format} "
                           "指定数字格式（如 {v:1234.5, format:'#,##0.00'}）；数据末行放 '~sum' 标记该列自动求和。"
                           "每表 charts=[{type: bar/column/line/pie, title, labels, values}] 生成可编辑原生图表；"
                           "format={data_bars:[列号], highlight:{max:[列号],min:[列号]}, color_scale:[列号]} 加数据条/最大最小高亮/色阶。"
                           "适合数据表、统计报表、预算表、清单等。支持 style 自定义表头配色/隔行/边框，不传则默认商务风。",
            "parameters": {"type": "object",
                           "properties": {
                               "path": {"type": "string", "description": "保存路径（.xlsx）"},
                               "sheets": {"type": "array",
                                          "description": "工作表列表，每项 {name: 表名, rows: [[单元格,...],...], "
                                                          "image: 表插图(可选，路径字符串或 {path, width}), "
                                                          "wordart: 表标题艺术字(可选，{text,size,color})}",
                                          "items": {"type": "object",
                                                    "properties": {
                                                        "name": {"type": "string", "description": "工作表名"},
                                                        "rows": {"type": "array",
                                                                 "description": "数据行二维数组",
                                                                 "items": {"type": "array",
                                                                           "items": {}}},
                                                        "image": {"type": "object",
                                                                  "description": "表插图（可选）：路径字符串或 "
                                                                                  "{path, width: 宽(像素,默认800)}",
                                                                  "properties": {
                                                                      "path": {"type": "string", "description": "图片路径"},
                                                                      "width": {"type": "number", "description": "图片宽度(像素)，默认800"}},
                                                                  "required": ["path"]},
                                                        "wordart": {"type": "object",
                                                                    "description": "表标题艺术字（可选）：{text, size, "
                                                                                    "color}，顶部大号加粗彩色标题行",
                                                                    "properties": {
                                                                        "text": {"type": "string", "description": "标题文字"},
                                                                        "size": {"type": "integer", "description": "字号，默认16"},
                                                                        "color": {"type": "string", "description": "颜色HEX，默认主题主色"}},
                                                                    "required": ["text"]},
                                                        "format": {"type": "object",
                                                                   "description": "条件格式（可选）：{data_bars: [列号...], "
                                                                                   "highlight: {max: [列号], min: [列号]}, "
                                                                                   "color_scale: [列号]}，列号为数据列 1 起编号，"
                                                                                   "对数值列加数据条/最大最小高亮/红绿黄色阶",
                                                                   "properties": {
                                                                       "data_bars": {"type": "array", "items": {"type": "integer"},
                                                                                     "description": "加数据条的列号数组，如 [2,3]"},
                                                                       "highlight": {"type": "object",
                                                                                     "properties": {
                                                                                         "max": {"type": "array", "items": {"type": "integer"},
                                                                                                 "description": "标黄每列最大值的列号"},
                                                                                         "min": {"type": "array", "items": {"type": "integer"},
                                                                                                 "description": "标绿每列最小值的列号"}},
                                                                                     "description": "最大/最小高亮"},
                                                                       "color_scale": {"type": "array", "items": {"type": "integer"},
                                                                                       "description": "加红黄绿色阶的列号数组"}}}},
                                                    "required": ["name", "rows"]}},
                               "style": {"type": "object",
                                         "description": "样式配置（可选）。不传则用默认商务风；传了可大胆自定义："
                                                         "theme_color=主题色HEX，header_fill=表头填充色HEX，"
                                                         "header_color=表头字色HEX，font_name=字体名，"
                                                         "banded=隔行变色(true/false，默认true)，band_fill=隔行填充色HEX，"
                                                         "freeze_header=冻结首行(true/false，默认true)，"
                                                         "auto_filter=自动筛选(true/false，默认true)，"
                                                         "border_color=边框色HEX，header_size=表头字号，"
                                                         "body_size=数据字号，header_bold=表头加粗(true/false，默认true)，"
                                                         "bg_color=工作表背景色HEX(填充数据区域)"}},
                           "required": ["path", "sheets"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "beautify_docx",
            "description": "美化已有 Word 文档（.docx）：统一正文/标题字体、配色（标题按主题上色）、"
                           "行距与字号。用于把已有或粗糙的 docx 一键排版美化，使文档更专业美观。"
                           "修改原文件（可先用 read_file 或先复制再美化）。",
            "parameters": {"type": "object",
                           "properties": {
                               "path": {"type": "string", "description": "待美化的 .docx 路径"},
                               "style": {"type": "object",
                                         "description": "样式配置（可选）："
                                                         "theme=配色方案(business/black-gold/green/warm/tech/vivid/purple/"
                                                         "pastel/dark/red)，theme_color=主色HEX，font_name=字体名，"
                                                         "body_size=正文字号(默认12)，dark_color=正文颜色HEX，"
                                                         "align=正文对齐(justify/left/right/center，默认justify)，"
                                                         "line_spacing=行距倍数(1.0-3.0，默认1.5)"}},
                           "required": ["path"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "beautify_pptx",
            "description": "美化已有 PowerPoint（.pptx）：统一各页标题/正文的字体与字号，标题按主题主色"
                           "上色，让整套 PPT 风格一致、更专业。修改原文件（建议先复制再美化）。",
            "parameters": {"type": "object",
                           "properties": {
                               "path": {"type": "string", "description": "待美化的 .pptx 路径"},
                               "style": {"type": "object",
                                         "description": "样式配置（可选）："
                                                         "theme=配色方案，theme_color=主色HEX，font_name=字体名，"
                                                         "body_size=正文/标题字号基数(默认18)，"
                                                         "bg_color=统一底色HEX(默认浅色主题色，保证深色文字可读)"}},
                           "required": ["path"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "beautify_xlsx",
            "description": "美化已有 Excel（.xlsx）：表头主题色底白字加粗、数据行隔行变色、单元格边框、"
                           "自动列宽、冻结首行与自动筛选，让数据表整洁专业。修改原文件（建议先复制再美化）。",
            "parameters": {"type": "object",
                           "properties": {
                               "path": {"type": "string", "description": "待美化的 .xlsx 路径"},
                               "style": {"type": "object",
                                         "description": "样式配置（可选）："
                                                         "theme=配色方案，theme_color=主题色HEX，header_fill=表头底色HEX，"
                                                         "header_color=表头字色HEX，font_name=字体名，"
                                                         "banded=隔行变色(true/false,默认true)，freeze_header=冻结首行(默认true)，"
                                                         "auto_filter=自动筛选(默认true)，border_color=边框色HEX"}},
                           "required": ["path"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_docx",
            "description": "读取 Word（.docx/.docm）真实结构，返回结构化 Markdown，用于「先读后改」定位锚点。"
                           "\n读出内容：标题层级(#/##/###)、粗**斜*下划线、每段字号与对齐标注(<!-- size=12pt align=justify -->)、"
                           "表格(| 列 | 列 |)、内嵌图片占位([图片 #1 media/xxx.png])、图表占位、页眉页脚、可用样式名清单。"
                           "\n典型用法：改文字前先 read_docx 找到目标段落原文 → edit_docx(replace_text/insert_paragraph)。"
                           "\n注意：图片只标注不内联 base64（避免上下文膨胀）；读取不修改文件。",
            "parameters": {"type": "object",
                           "properties": {
                               "path": {"type": "string", "description": "文档路径（.docx/.docm，相对路径基于工作目录）"},
                               "max_chars": {"type": "integer", "description": "返回文本上限（默认 60000 字符），超出截断并提示"}},
                           "required": ["path"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_pptx",
            "description": "读取 PPT（.pptx/.pptm）真实结构，返回结构化 Markdown，用于「先读后改」定位形状。"
                           "\n逐页输出：=== 第 N 页 ===、标题、背景色、每个形状 [z序] 类型 name= 位置(英寸) 尺寸(英寸) "
                           "字体/字号/颜色/粗体、文本行、表格、图表(类型+分类+数值)、图片、备注、动画条目"
                           "(shape_id / 效果名 / preset_class / trigger / delay)。"
                           "\n坐标单位英寸，可直接用于 edit_pptx 的 x/y/w/h；形状引用用索引 [z序] 或名称。"
                           "\n典型用法：read_pptx 看页面结构与动画 → edit_pptx(set_text/set_animations/set_bg_color)。",
            "parameters": {"type": "object",
                           "properties": {
                               "path": {"type": "string", "description": "演示文稿路径（.pptx/.pptm，相对路径基于工作目录）"},
                               "max_chars": {"type": "integer", "description": "返回文本上限（默认 60000 字符）"}},
                           "required": ["path"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_xlsx",
            "description": "读取 Excel（.xlsx/.xlsm）真实结构，返回结构化 Markdown，用于「先读后改」定位单元格。"
                           "\n逐工作表输出：=== 工作表 '名称' R行 x C列 ===、列宽、合并单元格、图表(类型+系列+数据范围)、"
                           "条件格式(范围:类型)、以及 | 行号 | A | B | ... 数据表（含公式原样 =SUM(...)、"
                           "每格样式标注 <!-- B2 bold fill=#1F3864 fmt=0.00% -->）。"
                           "\n典型用法：read_xlsx 确认行列结构与现有格式 → edit_xlsx(set_cell/set_style/add_condition)。"
                           "\n注意：公式显示原文不做重算（避免给出错误数值）。",
            "parameters": {"type": "object",
                           "properties": {
                               "path": {"type": "string", "description": "工作簿路径（.xlsx/.xlsm，相对路径基于工作目录）"},
                               "max_rows": {"type": "integer", "description": "每表最多读取行数（默认 300）"},
                               "max_cols": {"type": "integer", "description": "每表最多读取列数（默认 60）"}},
                           "required": ["path"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_pdf",
            "description": "读取 PDF：页数、文档元数据(标题/作者/创建时间等)、逐页文本；多段空白分隔的行会线性化为 "
                           "Markdown 表格(| 列 | 列 |)，便于提取表格内容。需已安装 pypdf（缺失时给出 pip install 提示）。"
                           "\n典型用法：读合同/报告/论文提取要点、核对数据。PDF 只读，不支持编辑。",
            "parameters": {"type": "object",
                           "properties": {
                               "path": {"type": "string", "description": "PDF 路径（相对路径基于工作目录）"},
                               "max_pages": {"type": "integer", "description": "最多读取页数（默认 200）"},
                               "max_chars": {"type": "integer", "description": "返回文本上限（默认 60000 字符）"}},
                           "required": ["path"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_docx",
            "description": "编辑 Word（.docx/.docm）：按 ops 列表逐条真实修改并写盘，单条失败不中断，返回逐条报告。"
                           "\nops 每项 {op: 名称, ...参数}，可用 op："
                           "\n- replace_text{find, replace, count=0}: 全文替换文字（count=0 不限次数）"
                           "\n- append_paragraph{text, style?}: 文末追加段落，text 支持轻标记 '# '/'## '/'### '/'1. '/'- '"
                           "\n- insert_paragraph{anchor, text, position=after|before}: 在含 anchor 文字的段落前后插入"
                           "\n- delete_paragraph{contains}: 删除含指定文字的段落（可批量）"
                           "\n- set_paragraph_style{contains, style:{bold,italic,size,color,align,space_after,line_spacing}}: 改段落样式"
                           "\n- set_table_cell{table=1, row=1, col=1, value, style?}: 改第 N 个表第 R 行第 C 列（1 起）"
                           "\n- add_table{rows:[[...],[...]]}: 文末新增表格"
                           "\n- add_image{path, width=6, align?}: 文末插入图片（width 英寸）"
                           "\n- set_header{text, section=1, align?, clear?} / set_footer{...}: 设置页眉页脚"
                           "\n示例：edit_docx(path=\"报告.docx\", ops=[{\"op\":\"replace_text\",\"find\":\"旧标题\",\"replace\":\"新标题\"},"
                           "{\"op\":\"append_paragraph\",\"text\":\"## 结论\"}])"
                           "\n建议先 read_docx 确认 anchor 原文再编辑；改完可再 read_docx 复核。颜色为 6 位 HEX（不含 #）。",
            "parameters": {"type": "object",
                           "properties": {
                               "path": {"type": "string", "description": "目标 .docx 路径（相对路径基于工作目录）"},
                               "ops": {"type": "array",
                                       "description": "操作列表，每项 {op: 名称, ...该 op 的参数}",
                                       "items": {"type": "object"}}},
                           "required": ["path", "ops"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_pptx",
            "description": "编辑 PPT（.pptx/.pptm）：按 ops 列表逐条真实修改并写盘，单条失败不中断，返回逐条报告。"
                           "\n所有 index 均为页码（1 起）；x/y/w/h 单位为英寸（与 read_pptx 输出一致）；颜色为 6 位 HEX。"
                           "\nops 每项 {op: 名称, ...参数}，可用 op："
                           "\n- replace_text{find, replace, count=0}: 全篇替换文字"
                           "\n- add_slide{title, bullets:[...], x?,y?,w?,h?,title_size?,body_size?,title_color?,body_color?}: 新页"
                           "\n- delete_slide{index} / duplicate_slide{index}（含图片关系重映射）/ move_slide{index, to}"
                           "\n- set_text{index, shape, text, style:{size,bold,color}}: shape 可为形状索引(0 起)、名称或所含文字片段"
                           "\n- set_notes{index, text}: 备注   - set_bg_color{index, color}: 页面背景色"
                           "\n- add_textbox{index, text, x, y, w, h, size?, color?, bold?} / add_image{index, path, x, y, w, h}"
                           "\n- set_transition{index, name=fade}: 页面切换效果"
                           "\n- set_animations{index, effects:[{shape, effect, trigger=with|click|after, delay, mode=in|out}], stagger?}:"
                           " 元素动画（复用生成侧动画时序实现）；shape 可传 'all' 表示本页全部形状"
                           "\n- 动画效果名可用：fade/wipe_down/wipe_up/wipe_left/wipe_right/fly_top/fly_bottom/fly_left/fly_right/"
                           "zoom/faded_zoom/float_up/float_down/float_left/float_right/dissolve/box/circle/diamond/plus/"
                           "blinds/wheel/split/wedge/checkerboard/grow_turn/pinwheel/randombar_h/randombar_v/strips_upleft/appear"
                           "\n示例：edit_pptx(path=\"方案.pptx\", ops=[{\"op\":\"set_text\",\"index\":1,\"shape\":0,\"text\":\"新标题\"},"
                           "{\"op\":\"set_animations\",\"index\":1,\"effects\":[{\"shape\":\"all\",\"effect\":\"fade\",\"trigger\":\"with\",\"delay\":0}]}])",
            "parameters": {"type": "object",
                           "properties": {
                               "path": {"type": "string", "description": "目标 .pptx 路径（相对路径基于工作目录）"},
                               "ops": {"type": "array",
                                       "description": "操作列表，每项 {op: 名称, ...该 op 的参数}",
                                       "items": {"type": "object"}}},
                           "required": ["path", "ops"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_xlsx",
            "description": "编辑 Excel（.xlsx/.xlsm）：按 ops 列表逐条真实修改并写盘，单条失败不中断，返回逐条报告。"
                           "\nsheet 为工作表名（留空用活动表）；cell 为 A1 式引用；range 为 A1:D10 式区域；颜色为 6 位 HEX。"
                           "\nops 每项 {op: 名称, ...参数}，可用 op："
                           "\n- set_cell{sheet, cell, value, style?}      - set_formula{sheet, cell, formula=\"SUM(B2:B9)\"}"
                           "\n- append_row{sheet, values:[...], style?}   - insert_row{sheet, at, values?}  - delete_row{sheet, at, count?}"
                           "\n- set_style{sheet, range, style:{bold,italic,size,color,fill,align,wrap,number_format,border,border_color}}"
                           "\n- merge_cells{sheet, range} / unmerge_cells{sheet, range}"
                           "\n- set_column_width{sheet, column=\"A\"或序号, width}   - set_row_height{sheet, row, height}"
                           "\n- add_sheet{name} / rename_sheet{sheet, new_name} / delete_sheet{sheet}"
                           "\n- add_chart{sheet, type=column|bar|line|pie, title, labels:[...], values:[...], anchor=\"E2\"}"
                           "\n- add_condition{sheet, range, type=data_bar|color_scale|highlight_max|highlight_min, color?...}"
                           "\n示例：edit_xlsx(path=\"报表.xlsx\", ops=[{\"op\":\"set_cell\",\"sheet\":\"汇总\",\"cell\":\"B2\",\"value\":1280},"
                           "{\"op\":\"set_style\",\"sheet\":\"汇总\",\"range\":\"A1:D1\",\"style\":{\"bold\":true,\"fill\":\"1F3864\",\"color\":\"FFFFFF\"}}])",
            "parameters": {"type": "object",
                           "properties": {
                               "path": {"type": "string", "description": "目标 .xlsx 路径（相对路径基于工作目录）"},
                               "ops": {"type": "array",
                                       "description": "操作列表，每项 {op: 名称, ...该 op 的参数}",
                                       "items": {"type": "object"}}},
                           "required": ["path", "ops"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_info",
            "description": "执行只读 git 查询命令（查看仓库状态/日志/差异/分支等），"
                           "危险命令（commit/push/reset/checkout/clean/merge 等改写操作）会被拒绝。"
                           "cwd 为 git 仓库目录（默认工作目录）。",
            "parameters": {"type": "object",
                           "properties": {
                               "command": {"type": "string",
                                           "description": "git 子命令及参数，如 status --short / log --oneline -5 / diff --stat"},
                               "cwd": {"type": "string", "description": "git 仓库目录（可选，默认工作目录）"}},
                           "required": ["command"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "联网搜索：在 Bing 上搜索关键词（失败自动回退 DuckDuckGo），返回结果列表（标题/URL/摘要）。"
                           "需要查询实时信息、新闻、文档或知识范围外内容时使用。"
                           "must_include 指定后只保留标题/摘要包含该词的结果，实现精确检索；"
                           "取得链接后可再用 web_fetch(url, keyword=...) 抓取并定位详情。",
            "parameters": {"type": "object",
                           "properties": {
                               "query": {"type": "string", "description": "搜索关键词"},
                               "must_include": {"type": "string",
                                                "description": "（可选）结果过滤词：仅保留标题或摘要中包含该词的结果（精确检索）"},
                               "max_results": {"type": "integer",
                                               "description": "返回结果条数，默认 8，最大 15"}},
                           "required": ["query"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_skill",
            "description": "以用户自然语言描述为基础，自动生成市场标准 SKILL.md 技能文件并加载（写入 "
                           "skills/<name>/SKILL.md，创建后立即生效，AI 与用户均可通过 /技能名 调用）。"
                           "若当前对话使用非默认工作流，技能会自动绑定到该工作流（仅该工作流加载）。"
                           "涉及图形化操作界面/可视化交互（仪表盘/可视化面板等）时，必须走 web 型插件"
                           "（见 create_plugin，本地 web 服务器 + 浏览器界面），浏览器操作者必须是用户本人，"
                           "严禁 AI 自我演示或与脚本自动化程序互演。",
            "parameters": {"type": "object",
                           "properties": {
                               "name": {"type": "string",
                                        "description": "技能名（仅字母/数字/下划线/连字符，≤50 字符）"},
                               "description": {"type": "string", "description": "技能用途一句话简介"},
                               "instruction": {"type": "string",
                                               "description": "技能执行流程/规则正文（markdown，写清触发条件与步骤）"}},
                           "required": ["name", "description", "instruction"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_plugin",
            "description": "用自然语言描述创建可运行的插件（统一存入插件目录）：根据用户描述用 AI 生成 "
                           "可运行的 MCP server 脚本（本地 stdio / 远程 SSE）+ 标准 SKILL.md 技能，"
                           "并自动生成运行脚本、依赖、示例文件，自动登记到技能与 MCP 配置，创建后即时生效。"
                           "需要图形化/可视化交互的能力（仪表盘/可视化面板/画布表单类工具）应选 web 型："
                           "生成本地 HTTP Server + 浏览器界面（用户在浏览器直观操作）+ 同名 MCP 工具"
                           "（LLM 与浏览器共用同一份状态）。web 型创建后必须立即调用 web_url + browser_open "
                           "把浏览器界面打开给用户；操作者必须是用户本人，严禁 AI 自我演示、自问自答或与脚本/"
                           "自动化程序互演。设计时优先把能力拆成 LLM 可以直接调用的工具"
                           "（动作/查询粒度，如把流程拆成创建/推进校验/读取状态/回退），而不是生成"
                           "LLM 无法直接操控的独立 GUI 应用。当用户想做一个独立功能/工具/能力并希望"
                           "沉淀为插件时使用。",
            "parameters": {"type": "object",
                           "properties": {
                               "description": {"type": "string",
                                               "description": "用户自然语言描述：插件要做什么、提供哪些能力"},
                               "kind": {"type": "string",
                                        "description": "插件类型：mcp（仅工具）/ skill（仅技能）/ "
                                                       "combined（技能+工具，默认）/ web（本地Server+浏览器界面，"
                                                       "图形化交互类用）",
                                        "enum": ["mcp", "skill", "combined", "web"]}},
                           "required": ["description"]},
        },
    },
    # ---------- 子 Agent 工具（主 Agent 派发只读子任务，执行由引擎调度） ----------
    {
        "type": "function",
        "function": {
            "name": "dispatch_sub_agents",
            "description": "把多个互不依赖的子任务分发给并行运行的子 Agent，各自独立执行后汇总返回。"
                           "多个子任务由线程池并发执行、互不等待；可重复派发同名子 Agent（如跨多个文件"
                           "各派一个并行处理）。适合大规模读取/搜索/探索、以及多文件并行创建/写入/编辑/"
                           "查找（子 Agent 可创建/编辑/删除项目文件，但不能执行命令）。"
                           "★准入限制：仅当任务确实非常复杂（跨多模块重构、大规模并发读写、大范围"
                           "跨目录搜索）时才使用本工具；简单与中等任务请自己直接动手，不要为图省事"
                           "组队（组队有固定开销，小事组队只会更慢更贵）。本工具是否可用由任务"
                           "复杂度分档决定，被禁用时调用会被系统直接拒绝。"
                           "子任务数量不限，一次可派发任意多个（全部并发执行）；任务越多总体耗时越长。"
                           "★每个子任务必须携带 context=<上下文>：把主 Agent 已读取的文件内容、关键代码片段、"
                           "搜索结果、约束要求等完成任务所必需的信息随任务传给子 Agent——子 Agent 是独立上下文，"
                           "看不到主对话和此前工具结果；不带关键上下文会令它盲目猜测或重复读取，降低质量与速度。"
                           "派发前对尚未读取而子任务需要的内容，先用 read_file 等工具取到再打包进 context。"
                           "任务项可选 agent=\"<工作流名>\"：把该子任务派发到「其他工作流的主 Agent」执行"
                           "（目标工作流 agent.py 人格 + tools.py 工具 + llm.py），由主 Agent 引领/监督；"
                           "可选 sub=\"<子Agent名>\"：指定当前工作流已注册的自定义子 Agent 执行"
                           "（同名子 Agent 可多任务重复派发，各任务独立并行）；"
                           "顶层可选 agents={\"<工作流名>\":\"<能力描述>\"}（预登记可派发的工作流主 Agent 清单）；"
                           "顶层可选 subagents={\"<名>\":{goal,description,allowed}}（按任务覆盖子 Agent 配置）。",
            "parameters": {"type": "object",
                           "properties": {
                               "shared_context": {"type": "boolean",
                                                  "description": "可选：是否让本批子 Agent 加入共同上下文空间"
                                                                 "（缺省 false=各自独立上下文）。true 时子 Agent 与主 Agent、"
                                                                 "其他开启共享的成员共用统一上下文：可读到彼此产出与主 Agent 议题，"
                                                                 "并把最终结论写回空间供后续成员复用。需先用 shared_context 工具 "
                                                                 "op=open 开启空间（未开启会自动创建默认空间）。"
                                                                 "任务项可用 shared_context 单独覆盖，实现逐任务决策。"},
                               "space": {"type": "string",
                                         "description": "可选：目标共同上下文空间 id（缺省用活跃空间）"},
                               "tasks": {"type": "array",
                                         "description": "子任务列表（每项含 title 标题与 goal 目标；"
                                                        "★每个子任务必须带 context：把完成任务所需的关键信息"
                                                        "（已读取的文件内容/关键代码片段/搜索结果/约束要求）传给"
                                                        "子 Agent——其上下文独立于主对话，缺 context 会盲猜或重复读取；"
                                                        "可选 agent 派发到其他工作流主 Agent；"
                                                        "可选 sub 指定注册子 Agent）",
                                         "items": {"type": "object",
                                                   "properties": {
                                                       "title": {"type": "string",
                                                                 "description": "子任务标题"},
                                                       "goal": {"type": "string",
                                                                "description": "子任务目标与要求（写清要做什么、输出什么）"},
                                                       "context": {"type": "string",
                                                                   "description": "必需：主 Agent 传给子 Agent 的上下文"
                                                                                  "（已读取的文件内容、关键代码片段、搜索结果、"
                                                                                  "约束要求等，子 Agent 直接使用、无需重复读取/搜索。" 
                                                                                  "子 Agent 上下文独立于主对话，不带关键上下文会盲猜"
                                                                                  "或重复读取；未读取到的内容先 read_file 再打包）"},
                                                       "agent": {"type": "string",
                                                                 "description": "可选：派发到的目标工作流名"
                                                                                "（list_workflow_agents 查看可用）"},
                                                       "sub": {"type": "string",
                                                               "description": "可选：当前工作流已注册子 Agent 名"
                                                                              "（list_sub_agents 查看）"},
                                                       "tools": {"type": "string",
                                                                 "description": "可选：子任务可用工具白名单"
                                                                                "（逗号分隔，默认全部子 Agent 白名单）"},
                                                       "shared_context": {"type": "boolean",
                                                                          "description": "可选：该子任务是否加入共同上下文空间"
                                                                                         "（覆盖顶层设置；true=与主 Agent/其他成员共用统一上下文，"
                                                                                         "false=独立上下文）"}},
                                                   "required": ["title", "goal"]}},
                               "agents": {"type": "object",
                                          "description": "可选：预登记可派发的工作流主 Agent "
                                                         "{工作流名: 能力描述}，供模型选择",
                                          "additionalProperties": {"type": "string"}},
                               "async": {"type": "boolean",
                                         "description": "可选（默认 true）：agent= 跨工作流任务是否异步后台执行。"
                                                        "true=派发即返回、成员后台独立运行（可随时 look_context 监督、"
                                                        "chat_with 催促，结论回写共同上下文空间供汇总）；"
                                                        "false=同步等待成员完成并直接汇总。"},
                               "subagents": {"type": "object",
                                             "description": "可选：按任务覆盖自定义子 Agent 配置 "
                                                            "{名: {goal, description, allowed}}",
                                             "additionalProperties": {"type": "object"}}},
                           "required": ["tasks"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "explore_project",
            "description": "探索并理解一个项目/目录（Explorer 子 Agent）：生成目录结构、读取 README 与关键入口文件，"
                           "输出项目概览（用途、技术栈、模块结构、入口、构建/运行方式）。接手新项目时先用它快速了解。",
            "parameters": {"type": "object",
                           "properties": {
                               "directory": {"type": "string",
                                             "description": "要探索的项目目录（绝对路径）"},
                               "context": {"type": "string",
                                           "description": "可选：主 Agent 传给该子 Agent 的上下文（已读取的文件内容/"
                                                          "已知约束等），子 Agent 上下文独立于主对话，带上可免重复读取"},
                               "shared_context": {"type": "boolean",
                                                  "description": "可选：该子 Agent 是否加入共同上下文空间"
                                                                 "（true=与主 Agent/其他成员共用统一上下文并可写回结论；"
                                                                 "缺省 false=独立上下文）"},
                               "space": {"type": "string",
                                         "description": "可选：目标共同上下文空间 id（缺省用活跃空间）"}},
                           "required": ["directory"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_large",
            "description": "大规模搜索（Search 子 Agent）：在多个目录范围内搜索关键词并汇总命中（跨目录、多轮搜索、"
                           "重要命中读取上下文确认）。比 search_files 更适合范围大、文件多的搜索。",
            "parameters": {"type": "object",
                           "properties": {
                               "query": {"type": "string", "description": "搜索关键词"},
                               "directories": {"type": "array", "items": {"type": "string"},
                                               "description": "可选：搜索目录列表；留空用工作目录/用户常用目录"},
                               "max_results": {"type": "integer",
                                               "description": "可选：最多保留命中条数，默认 20"},
                               "context": {"type": "string",
                                           "description": "可选：主 Agent 传给该子 Agent 的上下文"
                                                          "（已知约束/已定位范围等），免其重复搜索"},
                               "shared_context": {"type": "boolean",
                                                  "description": "可选：该子 Agent 是否加入共同上下文空间"
                                                                 "（true=与主 Agent/其他成员共用统一上下文并可写回结论；"
                                                                 "缺省 false=独立上下文）"},
                               "space": {"type": "string",
                                         "description": "可选：目标共同上下文空间 id（缺省用活跃空间）"}},
                           "required": ["query"]},
        },
    },
    # ---------- TTS 语音合成（Qwen-TTS 声音复刻，DashScope 真实 API） ----------
    {
        "type": "function",
        "function": {
            "name": "tts_create_voice",
            "description": "上传参考音频创建自定义音色（声音复刻）：支持 wav 等音频（推荐 10~20s、"
                           "≥24kHz 单声道、≤10MB），返回 voice_id 供 tts_speak 使用。"
                           "创建后音色长期有效，同一 target_model 可反复创建。",
            "parameters": {"type": "object",
                           "properties": {
                               "audio_path": {"type": "string",
                                              "description": "参考音频文件路径（绝对路径或基于工作目录的相对路径）"},
                               "preferred_name": {"type": "string",
                                                  "description": "音色名称（字母/数字/下划线，默认 diede）"},
                               "target_model": {"type": "string",
                                                "description": "绑定模型（默认 qwen3-tts-vc-2026-01-22）"}},
                           "required": ["audio_path"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tts_query_voice",
            "description": "查询音色详情（创建时间/语言/绑定模型）。需传入已有 voice_id。",
            "parameters": {"type": "object",
                           "properties": {
                               "voice_id": {"type": "string", "description": "音色 ID"},
                               "target_model": {"type": "string",
                                                "description": "绑定模型（默认 qwen3-tts-vc-2026-01-22）"}},
                           "required": ["voice_id"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tts_delete_voice",
            "description": "删除指定音色（不可恢复）。需传入已有 voice_id。",
            "parameters": {"type": "object",
                           "properties": {
                               "voice_id": {"type": "string", "description": "要删除的音色 ID"}},
                           "required": ["voice_id"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tts_speak",
            "description": "用指定音色把文本合成为语音，流式边生成边自动播放，并下载保存到本地，返回音频文件路径。"
                           "适合朗读回复、生成语音文件；voice_id 留空使用设置面板选中的音色；"
                           "output_path 留空自动保存到工作目录 tts_output/；play=false 可关闭自动播放。",
            "parameters": {"type": "object",
                           "properties": {
                               "text": {"type": "string", "description": "要合成的文本"},
                               "voice_id": {"type": "string",
                                            "description": "音色 ID（留空使用设置面板选中音色）"},
                               "model": {"type": "string",
                                         "description": "合成模型（默认 qwen3-tts-vc-2026-01-22）"},
                               "output_path": {"type": "string",
                                               "description": "输出音频文件路径（可选，留空自动生成）"},
                               "play": {"type": "boolean",
                                        "description": "是否边生成边自动播放（默认 true）"}},
                           "required": ["text"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_workflows",
            "description": "列出全部 Agent 工作流（Cordis 自定义）：名称/描述/是否默认/是否激活/包含的核心文件。"
                           "用户可自建工作流（agent.py/llm.py/tools.py/skills/plugins/mcp.json）并热插拔切换，"
                           "默认工作流 _default 只读不可删。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_workflow",
            "description": "创建用户自定义 Agent 工作流（Cordis）。files 留空生成全部核心文件（agent.py/llm.py/tools.py/"
                           "skills/plugins/mcp.json）；只传单个文件（如 [\"tools.py\"]）则只创建该核心文件，其余模块回退"
                           "内置默认——用于单独定制某一能力并加入默认工作流。"
                           "preset 可选：一键从内置工作流预设创建（如 preset=\"sansheng_liubu\" 即「三省六部制度」，"
                           "含 agent.py/tools.py/技能与多个注册式子 Agent），此时 name 可留空用预设默认名；"
                           "可用 list_builtin_workflows 查看全部内置预设。",
            "parameters": {"type": "object",
                           "properties": {
                               "name": {"type": "string",
                                        "description": "工作流名（字母/数字/下划线/中划线）；preset 模式下留空用预设默认名"},
                               "description": {"type": "string", "description": "工作流说明（可选）"},
                               "preset": {"type": "string",
                                          "description": "内置工作流预设 id（list_builtin_workflows 查看），如 sansheng_liubu"},
                               "files": {"type": "array",
                                         "items": {"type": "string"},
                                         "description": "要创建的核心文件，如 [\"tools.py\"]；留空创建全部（preset 模式忽略）"}},
                           "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_builtin_workflows",
            "description": "列出应用内置的 Agent 工作流预设（随包提供、可一键创建）。每项含 id / 名称 / 用途说明 / "
                           "创建后的工作流名 / 是否已创建 / 内含注册式子 Agent。创建用 create_workflow(preset=<id>)。"
                           "当前内置：三省六部制度（中书省主 Agent 草制决策、门下省驳议封还、尚书省督课调度、"
                           "吏户礼兵刑工六部分承办事；全编队以文言文奏对，共用共同上下文空间，并可调用 "
                           "create-cordis 技能扩展核心文件与注册式子 Agent）。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "switch_workflow",
            "description": "切换激活的 Agent 工作流（热插拔，立即重建引擎生效，无需重启应用）。切换后本工具会返回"
                           "rebuild=true，引擎随即用新工作流重建（新的 LLM/工具/技能立即生效）。",
            "parameters": {"type": "object",
                           "properties": {
                               "name": {"type": "string", "description": "目标工作流名（list_workflows 查看）"}},
                           "required": ["name"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_workflow_agents",
            "description": "列出所有可调用的其他工作流 Agent 能力（agent 名称/说明/技能/自定义工具/MCP/插件），"
                           "供主 Agent 判断哪个工作流的 agent 适合当前任务，再配合 use_workflow_agent 切换调用。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "use_workflow_agent",
            "description": "默认主 Agent 直接在当前对话内临时切换调用其他工作流的 Agent 能力（人格/技能/工具），"
                           "无需用户在设置里改激活工作流、也不影响其他对话。name 留空或传 _default 则切回默认主 Agent。"
                           "切换后立即重建引擎，后续对话按目标工作流的 agent 人格与能力继续执行，可随时再切回。"
                           "示例：use_workflow_agent(name=\"数据分析\") 后用该工作流 agent 完成数据分析，"
                           "完成后再 use_workflow_agent(name=\"_default\") 切回。",
            "parameters": {"type": "object",
                           "properties": {
                               "name": {"type": "string",
                                        "description": "目标工作流名（list_workflow_agents 查看；_default 切回默认）"},
                               "reason": {"type": "string", "description": "切换原因（可选，仅用于说明）"}},
                           "required": ["name"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_agents",
            "description": "列出全部自定义 Agent（独立于工作流的轻量人格）：名称/绑定工作流/是否已绑定。"
                           "每个 agent = system_prompt 人格 + 可选 bound_workflow（提供 tools/skill/llm）。"
                           "配合 create_agent 创建、delete_agent 删除；用户也可在输入框用 @agent 切换。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_agent",
            "description": "创建/更新自定义 Agent（独立于工作流的人格）。@agent 可在输入框会话级切换该人格。"
                           "★仅用于「切换/自定义主 Agent 人格」：它是人格，不是下属，主 Agent 无法调用它。"
                           "用户要求「创建子 agent / 能被主 Agent 调用的 agent」时必须用 register_sub_agent。"
                           "bound_workflow 留空则沿用当前会话工作流的工具/技能；指定则切换 agent 时一并切到该工作流"
                           "（获得其 tools/skill，实现跨工作流调用）。",
            "parameters": {"type": "object",
                           "properties": {
                               "name": {"type": "string",
                                        "description": "agent 名（字母/数字/下划线/中划线，1-64）"},
                               "system_prompt": {"type": "string",
                                                 "description": "完整人格/系统提示词（不能为空）"},
                               "bound_workflow": {"type": "string",
                                                  "description": "绑定工作流名（可选；提供 tools/skill/llm；留空沿用当前）"}},
                           "required": ["name", "system_prompt"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_agent",
            "description": "删除自定义 Agent（独立于工作流的人格）。删除后使用该 agent 的会话仍保留历史，"
                           "重启/下次切换时回退当前工作流人格。",
            "parameters": {"type": "object",
                           "properties": {
                               "name": {"type": "string", "description": "要删除的 agent 名"}},
                           "required": ["name"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_workflow",
            "description": "查看指定工作流的结构详情：核心文件存在性/大小/内容头部摘要、元数据、是否激活。",
            "parameters": {"type": "object",
                           "properties": {
                               "name": {"type": "string", "description": "工作流名"}},
                           "required": ["name"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_agent_file",
            "description": "读写工作流核心文件（agent.py/llm.py/tools.py/mcp.json）。action=read 返回文件全文；"
                           "action=write 用 content 覆盖写入（默认工作流 _default 禁止修改）。改完需 switch_workflow"
                           "重新激活生效。",
            "parameters": {"type": "object",
                           "properties": {
                               "name": {"type": "string", "description": "工作流名"},
                               "file": {"type": "string",
                                        "description": "核心文件：agent.py / llm.py / tools.py / mcp.json"},
                               "action": {"type": "string", "description": "read 或 write，默认 read"},
                               "content": {"type": "string", "description": "write 时写入的完整文件内容"}},
                           "required": ["name", "file"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_workflow",
            "description": "删除用户工作流（默认工作流 _default 拒绝删除）。删除后若其为激活工作流，自动回退默认。",
            "parameters": {"type": "object",
                           "properties": {
                               "name": {"type": "string", "description": "要删除的工作流名"}},
                           "required": ["name"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_uiux",
            "description": "管理自定义 UI/UX 包（Cordis 热插拔）。op 缺省默认 list。"
                           "op=list 列出全部包；"
                           "op=create 创建（name/description/build_ui 完整自定义代码，"
                           "build_welcome 可选，theme 可选 {dark:{...},light:{...}} 自定义主题色板，"
                           "qss 可选 panel.qss 样式表深度定制组件外观，"
                           "plugins 可选 [\"插件名\"]、mcp 可选 [\"MCP名\"] 依赖声明）；"
                           "op=read 读取包内容（name + part=build_ui/build_welcome/theme/qss/meta）；"
                           "op=update 更新 build_ui（name+build_ui）；"
                           "op=set_qss 更新样式表（name+qss）；"
                           "op=update_theme 更新主题色板（name+theme JSON）；"
                           "op=deps 更新依赖（name+plugins/mcp 列表，传 [] 清空）；"
                           "op=duplicate 复制包为新包（name+new_name）；"
                           "op=export 导出 zip 资源包（name+dest 目标目录或文件路径，"
                           "声明了依赖会含 plugins/ 与 mcp_servers.json）；"
                           "op=import 导入 zip 资源包（zip_path，同名覆盖，自动安装依赖）；"
                           "op=activate 切换（name，'default' 切回默认）；op=delete 删除用户包"
                           "（内置包拒绝）；op=get 获取当前活跃包。",
            "parameters": {"type": "object",
                           "properties": {
                               "op": {"type": "string",
                                      "enum": ["list", "create", "read", "update", "set_qss",
                                               "update_theme", "deps", "duplicate", "export",
                                               "import", "activate", "delete", "get"],
                                      "description": "操作类型"},
                               "name": {"type": "string", "description": "包名"},
                               "new_name": {"type": "string", "description": "duplicate 时新包名"},
                               "part": {"type": "string",
                                        "description": "read 时读取部分：build_ui/build_welcome/theme/qss/meta"},
                               "description": {"type": "string", "description": "create 时的描述"},
                               "build_ui": {"type": "string", "description": "create/update 时的 build_ui.py 代码（完整自定义）"},
                               "build_welcome": {"type": "string", "description": "create 时可选 build_welcome.py 代码"},
                               "qss": {"type": "string",
                                       "description": "panel.qss 样式表（QSS，深度定制按钮/滑块/输入框/对话框等组件外观）"},
                               "theme": {"type": "string",
                                         "description": "自定义主题色板 JSON：{\"dark\":{...},\"light\":{...}}，"
                                                        "键为色板常量名 BG/TEXT/ACCENT 等"},
                               "plugins": {"type": "string",
                                           "description": "依赖的插件名列表 JSON，如 [\"frontend-design-pro\"]（导出时打包）"},
                               "mcp": {"type": "string",
                                       "description": "依赖的 MCP server 名列表 JSON，如 [\"local\"]（导出时打包）"},
                               "dest": {"type": "string", "description": "export 导出目标目录或文件路径（.zip）"},
                               "zip_path": {"type": "string", "description": "import 要导入的 zip 文件路径"}},
                           "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "register_panel_btn",
            "description": "注册/注销 AI 面板顶部按钮栏的自定义按钮（与 UI/UX 解耦：不管当前/以后切换任何"
                           "UI/UX 包、甚至完全重构界面，按钮都保持可见且点击生效）。"
                           "op=register 注册或更新（id 唯一；text 按钮文字；action 点击触发哪个内置功能；"
                           "tooltip 可选悬浮提示）；op=unregister 注销（id）。"
                           "action 可选：send(发送/停止当前任务)/new_session(新建对话)/settings(打开设置)/"
                           "attach(选择附件)/optimize(优化提示词)/clear_chat(清空并删除当前对话)。",
            "parameters": {"type": "object",
                           "properties": {
                               "op": {"type": "string",
                                      "enum": ["register", "unregister"],
                                      "description": "操作类型"},
                               "id": {"type": "string", "description": "按钮唯一标识（英文/数字/下划线）"},
                               "text": {"type": "string", "description": "按钮文字（默认用 id）"},
                               "action": {"type": "string",
                                          "description": "点击触发：send/new_session/settings/attach/optimize/clear_chat"},
                               "tooltip": {"type": "string", "description": "可选悬浮提示"}},
                           "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_customization",
            "description": "盘点当前全部可深度自定义维度（Cordis 理念：一切皆可替换/热插拔）："
                           "工作流（agent.py/llm.py/tools.py/skills/plugins/mcp.json）、技能、插件、"
                           "UI/UX 包、面板自定义按钮、MCP 服务器。返回各维度现状与对应自定义工具，"
                           "供引导用户选择要自定义/新增的功能（信息不足先用 ask_user 问清需求）。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_sub_agents",
            "description": "列出全部可用子 Agent：内置（dispatch_sub_agents/explore_project/search_large）+ "
                           "当前工作流已注册自定义子 Agent（name/description/goal/allowed，工具名为 sub_<name>）。"
                           "新增子 agent 前先调用查看现状。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "register_sub_agent",
            "description": "★用户要求「创建一个子 agent / 新增一个能干活的下属 agent / 加一个可被调用的 "
                           "agent」时，必须用本工具：注册式子 Agent 是唯一同时具备三种调用方式的形态 ——"
                           "① 用户输入框 @<name> 直接调用；② 主 Agent 调用 sub_<name> 工具；"
                           "③ 可按主 Agent 分配加入共同上下文空间（shared_context）。"
                           "它与 register_agent_network(op=create_agent) 完全不同：自定义 Agent 只是"
                           "「会话级切换主 Agent 人格」，主 Agent 无法调用它，禁止用它来创建子 agent。"
                           "注册进当前工作流（小功能注入现有工作流，绝不新建工作流），不新建工作流。"
                           "name 唯一标识；description 用途说明；goal 是给该子 Agent 的任务指令模板"
                           "（写清要做什么、输出什么）；allowed 可选，该子 Agent 可用工具白名单子集"
                           "（如 read_file,search_files,grep，留空用全部子 Agent 白名单）；"
                           "persona 可选，自定义子 Agent 人格/系统提示词（设定后不再套用内置"
                           "「我是子 Agent…」规则文本，直接以该人格运行）。"
                           "op=register 注册/更新（name/description/goal/allowed/persona/"
                           "shared_context/allow_chat/share_context）；op=unregister 注销（name）。"
                           "allow_chat：是否允许该子 Agent 参与 Agent 间聊天（chat_with 讨论）；"
                           "share_context：是否允许领导者用 look_context 查看其完整上下文（消息/命令/"
                           "文件/工具轨迹）。共享全开策略：缺省全开，显式传 false 可关闭。",
            "parameters": {"type": "object",
                           "properties": {
                               "op": {"type": "string",
                                      "enum": ["register", "unregister"],
                                      "description": "操作类型，缺省 register"},
                               "name": {"type": "string", "description": "子 Agent 唯一标识（字母/数字/下划线/中划线）"},
                               "description": {"type": "string", "description": "用途说明（可选）"},
                               "goal": {"type": "string", "description": "子 Agent 任务指令模板（register 必填）"},
                               "persona": {"type": "string",
                                           "description": "自定义子 Agent 人格/系统提示词（可选，设定后覆盖内置通用规则文本）"},
                               "shared_context": {"type": "boolean",
                                                  "description": "该子 Agent 是否参与共同上下文空间的默认开关（可选）："
                                                                 "缺省 true=共用统一上下文（含 @调用时）；"
                                                                 "显式 false=保持独立上下文"},
                               "allow_chat": {"type": "boolean",
                                              "description": "是否允许该子 Agent 参与 Agent 间聊天（chat_with 讨论），缺省 true"},
                               "share_context": {"type": "boolean",
                                                 "description": "是否允许领导者用 look_context 查看其完整上下文，缺省 true"},
                               "allowed": {"type": "string",
                                           "description": "可用工具白名单逗号分隔，可选，如 read_file,search_files,grep"}},
                           "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "chat_with",
            "description": "工作团沟通：向指定 Agent（主 Agent main / 注册子 Agent 名 / 工作流名）发送聊天消息。"
                           "目标空闲时在其下一轮任务收到，忙碌时入队暂存。开启聊天权限（allow_chat）的子 Agent"
                           "才能接收。用于团队讨论、跨工作流沟通、给成员补充信息。",
            "parameters": {"type": "object",
                           "properties": {
                               "to": {"type": "string",
                                      "description": "目标 Agent：main（主 Agent）/ 注册子 Agent 名 / 工作流名"},
                               "text": {"type": "string", "description": "消息内容"}},
                           "required": ["to", "text"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "look_context",
            "description": "工作团监督：查看指定 Agent 的上下文轨迹（消息/命令/文件/工具/skill/mcp/plugin 分类），"
                           "并附当前共同上下文空间条目。仅可查看已开启上下文共享（share_context=true）的"
                           "子 Agent；主 Agent 与工作流主 Agent 默认可见。用于领导者持续监督成员工作质量。",
            "parameters": {"type": "object",
                           "properties": {
                               "agent": {"type": "string",
                                         "description": "目标 Agent：main / 注册子 Agent 名 / 工作流名"},
                               "types": {"type": "string",
                                         "description": "只看哪些类型（逗号分隔）：message,command,file,tool,"
                                                        "skill,mcp,plugin；留空=全部"},
                               "limit": {"type": "integer",
                                         "description": "最多显示最近多少条轨迹（缺省 80）"}},
                           "required": ["agent"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pause_agent",
            "description": "工作团管控：暂停指定 Agent 执行（运行中的在下一检查点挂起，空闲的标记为暂停，"
                           "其下次任务启动即被拦截）。团队领导者（如产品经理）用它叫停成员以纠偏；"
                           "恢复用 resume_agent。",
            "parameters": {"type": "object",
                           "properties": {
                               "agent": {"type": "string",
                                         "description": "目标 Agent：main / 注册子 Agent 名 / 工作流名"}},
                           "required": ["agent"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resume_agent",
            "description": "工作团管控：恢复被 pause_agent 暂停的 Agent，任务继续执行。",
            "parameters": {"type": "object",
                           "properties": {
                               "agent": {"type": "string",
                                         "description": "目标 Agent：main / 注册子 Agent 名 / 工作流名"}},
                           "required": ["agent"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "warn_agent",
            "description": "工作团管控：向指定 Agent 发送警告提醒（在其下一任务检查点注入上下文），"
                           "用于纠偏成员（如要求重做、注意质量、补充验证），配合 look_context 监督使用。",
            "parameters": {"type": "object",
                           "properties": {
                               "agent": {"type": "string",
                                         "description": "目标 Agent：main / 注册子 Agent 名 / 工作流名"},
                               "text": {"type": "string", "description": "警告内容（具体要求/纠偏意见）"}},
                           "required": ["agent", "text"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_session_name",
            "description": "给当前对话起名/改名（会话标题）：概括本次对话主题，随时可再次调用覆盖。"
                           "用户说「给这个对话起个名字/重命名对话/改成 XXX」时必须调用；"
                           "长对话主题明确后（如完成一轮多步任务）也应主动起一个有信息量的名字，"
                           "便于用户在对话列表里辨认。标题只写主题本身，不加引号与标点。",
            "parameters": {"type": "object",
                           "properties": {
                               "title": {"type": "string",
                                         "description": "新的对话标题（2-16 字，概括主题，不含标点/引号）"}},
                           "required": ["title"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shared_context",
            "description": "共同上下文空间（Shared Context Space）：主 Agent / 注册式子 Agent / 临时子 Agent 共用的"
                           "统一上下文总线，用于编队协同（主 Agent 决策是否开启）。"
                           "op=open 开启（seed 议题注入空间，供成员对齐目标；缺省设为活跃空间，子 Agent 缺省解析它）；"
                           "op=append 写回条目（source 缺省为当前成员，kind 如 result/artifact/note）；"
                           "op=read 读取渲染（exclude 可排除某来源，子 Agent 读取时排除自己产出）；"
                           "op=status 查看指定/活跃空间统计；op=list 列出全部空间；op=close 关闭并回收。"
                           "开启后：dispatch_sub_agents 传 shared_context=true（或任务项单独设置）、"
                           "调用 sub_<name> 传 shared_context=true，即让对应子 Agent 加入共享——"
                           "成员可读到彼此产出与议题，并把结论写回空间供后续成员与主 Agent 复用。"
                           "注意：共享上下文会互相暴露信息，仅在同一编队任务需要协同时开启。",
            "parameters": {"type": "object",
                           "properties": {
                               "op": {"type": "string",
                                      "enum": ["open", "append", "read", "status", "list", "close"],
                                      "description": "操作类型，缺省 status"},
                               "space": {"type": "string",
                                         "description": "空间 id（缺省用活跃空间；op=open 时留空自动生成）"},
                               "seed": {"type": "string",
                                        "description": "op=open：议题/任务背景（成员据此对齐目标）"},
                               "text": {"type": "string",
                                        "description": "op=append：写回的条目正文（结论/产物路径/待办等）"},
                               "source": {"type": "string",
                                          "description": "op=append：写入者标签（缺省当前成员名）"},
                               "kind": {"type": "string",
                                        "description": "op=append：条目类型，如 result/artifact/plan/note（缺省 note）"},
                               "exclude": {"type": "string",
                                           "description": "op=read：排除该来源的条目"},
                               "activate": {"type": "boolean",
                                            "description": "op=open：是否设为活跃空间，缺省 true"},
                               "max_entries": {"type": "integer",
                                               "description": "op=read：最多读取最近条目数（缺省全部）"}},
                           "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_agents_network",
            "description": "统一查看当前 Agent 编队全貌：①工作流主 Agent（各工作流 agent.py，可被主 Agent "
                           "经 use_workflow_agent 切换调用、或经 dispatch_sub_agents 的 agent 参数派发）；"
                           "②自定义 Agent（独立人格，@agent 会话级切换，可绑定工作流）；"
                           "③子 Agent（内置 dispatch_sub_agents/explore_project/search_large + 各工作流注册的 "
                           "sub_<name> 自定义子 Agent）。管理/新增/派发 agent 前先调用了解现状。",
            "parameters": {"type": "object",
                           "properties": {
                               "workflow": {"type": "string",
                                            "description": "可选：只看指定工作流的主 Agent 与其子 Agent"}},
                           "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "register_agent_network",
            "description": "统一管理全部自定义 Agent 的核心工具（编队指挥台）："
                           "op=register_subagent 向指定/当前工作流注册自定义子 Agent（name/description/goal/"
                           "allowed/persona，注册后立即作为 sub_<name> 工具被主 Agent 调用；persona 为可选"
                           "自定义人格，设定后覆盖内置通用规则文本）；"
                           "op=unregister_subagent 注销子 Agent；"
                           "op=create_agent 创建/更新自定义 Agent 人格（name/system_prompt/bound_workflow，"
                           "输入框 @agent 会话级切换）——★仅用于「切换/自定义主 Agent 人格」这一种场景，"
                           "创建出来的只是人格，主 Agent 无法调用它；用户要求「创建子 agent / 新增一个下属 "
                           "agent」时必须用 op=register_subagent（或 register_sub_agent），禁止用 create_agent"
                           "顶替，否则结果不可被主 Agent 调用、也不可被 @<子Agent名> 直接调用；"
                           "op=delete_agent 删除自定义 Agent。"
                           "默认操作当前工作流；workflow 参数可显式指定目标工作流（需已存在）。",
            "parameters": {"type": "object",
                           "properties": {
                               "op": {"type": "string",
                                      "enum": ["register_subagent", "unregister_subagent",
                                               "create_agent", "delete_agent"],
                                      "description": "操作类型，缺省 register_subagent"},
                               "name": {"type": "string", "description": "agent/子 Agent 名（唯一标识）"},
                               "description": {"type": "string", "description": "子 Agent 用途说明（可选）"},
                               "goal": {"type": "string",
                                        "description": "子 Agent 任务指令模板（register_subagent 必填）"},
                               "allowed": {"type": "string",
                                           "description": "子 Agent 可用工具白名单逗号分隔，可选"},
                               "persona": {"type": "string",
                                           "description": "子 Agent 自定义人格/系统提示词（可选，设定后覆盖内置通用规则文本）"},
                               "shared_context": {"type": "boolean",
                                                  "description": "子 Agent 参与共同上下文空间的默认开关（可选）："
                                                                 "true=默认共用统一上下文；false=默认独立；"
                                                                 "不传=由主 Agent 每次调用时决策"},
                               "system_prompt": {"type": "string",
                                                 "description": "自定义 Agent 完整人格/系统提示词（create_agent 必填）"},
                               "bound_workflow": {"type": "string",
                                                  "description": "自定义 Agent 绑定工作流（可选）"},
                               "workflow": {"type": "string",
                                            "description": "目标工作流（子 Agent 操作时指定；缺省当前工作流）"}},
                           "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "register_feature_panel",
            "description": "向当前工作流注册一个功能面板（小功能注入现有工作流，绝不新建 UI/UX 包）。"
                           "生成 <当前工作流>/panel.py，扫描后成为可拖拽扩展浮窗。"
                           "python 需提供 build_panel(owner)->QWidget 的完整代码（module 级暴露）："
                           "TITLE（面板标题，缺省用 name）；WIDTH（默认 300）；HEIGHT（默认 240）；"
                           "build_panel(owner) 返回一个 QWidget 控件（owner 为 AgentPanel 实例）。"
                           "已存在同名面板会覆盖。所有控件请用纯黑+淡灰+白+深蓝极简风格，禁止 emoji。",
            "parameters": {"type": "object",
                           "properties": {
                               "name": {"type": "string", "description": "面板唯一标识（字母/数字/下划线/中划线，英文）"},
                               "title": {"type": "string", "description": "面板标题（显示在浮窗顶部，可中文）"},
                               "width": {"type": "integer", "description": "面板固定宽度（默认 300）"},
                               "height": {"type": "integer", "description": "面板初始高度（默认 240）"},
                               "python": {"type": "string",
                                          "description": "完整 panel.py 代码：TITLE/WIDTH/HEIGHT/build_panel(owner) 返回值"}},
                           "required": ["name"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_feature_deps",
            "description": "为当前工作流的 AI 自定义代码（tools.py/agent.py/llm.py/panel.py）声明并安装第三方依赖。"
                           "自定义代码允许顶部带第三方 import；先把需要的库写成 requirements.txt 声明，"
                           "加载自定义代码时才会自动安装。"
                           "op=declare 声明/合并依赖（deps=[""pandas"",""requests""]，[] 清空，写入 <当前工作流>/requirements.txt）；"
                           "op=install 立即安装已声明依赖并校验；op=list 查看当前已声明依赖与安装状态。",
            "parameters": {"type": "object",
                           "properties": {
                               "op": {"type": "string",
                                      "enum": ["declare", "install", "list"],
                                      "description": "操作类型，缺省 declare"},
                               "deps": {"type": "array",
                                        "items": {"type": "string"},
                                        "description": "pip 依赖名列表，可用版本约束如 pandas>=2.0；declare 时使用"}},
                           "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_mcp",
            "description": "在全局 MCP 服务器注册表（mcp_servers.json）新增或更新一个 MCP 服务器配置。"
                           "type=stdio 用 command+args（本地子进程，args 为空则命令后无参）；"
                           "type=sse 用 url（远程事件流）。name 唯一，同名覆盖更新。"
                           "enabled 可选（false 则该服务器不连接）。新增后发起下一轮任务应用生效。",
            "parameters": {"type": "object",
                           "properties": {
                               "name": {"type": "string", "description": "服务器唯一标识（字母/数字/下划线/中划线）"},
                               "type": {"type": "string", "enum": ["stdio", "sse"], "description": "传输类型，缺省 stdio"},
                               "command": {"type": "string", "description": "stdio 启动命令"},
                               "args": {"type": "array", "items": {"type": "string"}, "description": "stdio 启动参数（可选）"},
                               "url": {"type": "string", "description": "sse 端点 URL（type=sse 时必填）"},
                               "enabled": {"type": "boolean", "description": "是否启用，缺省 true"}},
                           "required": ["name"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_mcp",
            "description": "列出全局 MCP 服务器配置（name/type/command 或 url/启用状态），"
                           "并标注当前工作流是否允许暴露其工具（allowed_mcp_servers）。"
                           "新增/管理 MCP 前先调用查看现状。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_mcp",
            "description": "更新或删除全局 MCP 服务器配置。"
                           "op=update 修改指定字段（type/command/args/url/enabled，传你给的字段即为新值）；"
                           "op=delete 删除该服务器。name 定位。改完发起下一轮任务应用生效。",
            "parameters": {"type": "object",
                           "properties": {
                               "op": {"type": "string", "enum": ["update", "delete"], "description": "操作类型，缺省 update"},
                               "name": {"type": "string", "description": "要操作的服务器名"},
                               "type": {"type": "string", "enum": ["stdio", "sse"], "description": "传输类型"},
                               "command": {"type": "string", "description": "stdio 启动命令"},
                               "args": {"type": "array", "items": {"type": "string"}, "description": "启动参数"},
                               "url": {"type": "string", "description": "sse 端点 URL"},
                               "enabled": {"type": "boolean", "description": "是否启用"}},
                           "required": ["name"]},
        },
    },
]

# 子 Agent 工具名（由 agent_engine 拦截调度，携带 LLM 客户端执行；不在此直接实现）
SUB_AGENT_TOOLS = ("dispatch_sub_agents", "explore_project", "search_large")


# ------------------------------------------------------------
# TTS 播放器（整句累积 + 后台播放线程，Windows 系统原生 winsound 播放）
#
# 实现说明：
# 1. 合成线程把整句 mono PCM(24k/16bit) 累积进缓冲，句合成完成后
#    flush 通知播放线程一次性播放整句（句内无切块，无爆音）；
# 2. 播放线程把整句 PCM 做句首淡入+句尾淡出后写临时 WAV 文件，
#    用 winsound.PlaySound 系统原生解码播放（WASAPI）。
#    不用 pygame/SDL：其 mixer 初始化与 24kHz 重采样在 Windows 上
#    反复产生设备打开爆音/周期性咚咚声，系统原生播放最干净。
# ------------------------------------------------------------
_TTS_PLAYER_LOCK = threading.Lock()
_TTS_BUF = bytearray()          # 当前句 mono PCM 累积（24kHz/16bit）
_TTS_STOP_EVT = threading.Event()  # 播放线程停止（用户停止/合成失败）
_TTS_DONE_EVT = threading.Event()  # 合成方声明无更多数据（播完缓冲后退出）
_TTS_FLUSH_EVT = threading.Event()  # 合成方提示"有整句可播"
_TTS_SEG_START = True           # 保留兼容：每句播放统一做淡入淡出
_TTS_PLAYER_THREAD = None


def _tts_play_start() -> bool:
    """开始播放（winsound 系统原生，无需初始化）：停掉上次残音并启动播放线程。
    返回 True 表示播放可用。"""
    global _TTS_BUF, _TTS_SEG_START, _TTS_STOP_EVT, _TTS_DONE_EVT, _TTS_FLUSH_EVT
    global _TTS_PLAYER_THREAD
    with _TTS_PLAYER_LOCK:
        _TTS_BUF.clear()
        _TTS_SEG_START = True
        _TTS_STOP_EVT.clear()
        _TTS_DONE_EVT.clear()
        _TTS_FLUSH_EVT.clear()
        try:
            import winsound
            winsound.PlaySound(None, winsound.SND_PURGE)   # 停掉上次未播完的残音
        except Exception:
            pass
        if _TTS_PLAYER_THREAD is None or not _TTS_PLAYER_THREAD.is_alive():
            _TTS_PLAYER_THREAD = threading.Thread(
                target=_tts_player_loop, daemon=True)
            _TTS_PLAYER_THREAD.start()
    return True


def _tts_play_segment_start():
    """标记一段新合成的开始：下一句从静默恢复时句首淡入，消除句间爆音"""
    global _TTS_SEG_START
    with _TTS_PLAYER_LOCK:
        _TTS_SEG_START = True


def _tts_play_chunk(pcm: bytes):
    """把合成返回的 PCM 分片（mono 24kHz/16bit，已剥 WAV 头）追加进当前句缓冲"""
    if not pcm:
        return
    with _TTS_PLAYER_LOCK:
        if _TTS_STOP_EVT.is_set():
            return
        _TTS_BUF.extend(pcm)


def _tts_play_flush():
    """当前句合成完成：通知播放线程把整句一次性 play（句内无切块）"""
    global _TTS_FLUSH_EVT
    with _TTS_PLAYER_LOCK:
        if _TTS_BUF:
            _TTS_FLUSH_EVT.set()


def _tts_player_loop():
    """后台播放线程：只在合成方 flush 提示"整句就绪"时取缓冲整句播放；
    否则空等。合成是流式的，chunk 逐个到达，若不等 flush 会把一句切成
    多个小段播放，段间爆音形成周期性"咚咚"声。
    播放方式：整句 PCM 做句首淡入+句尾淡出后写临时 WAV，用 winsound
    系统原生同步播放（WASAPI 解码，无 SDL 重采样/设备初始化爆音）。"""
    import os as _os
    import tempfile as _tmp
    import time as _time
    import winsound
    seq = 0
    while not _TTS_STOP_EVT.is_set():
        _TTS_FLUSH_EVT.wait(timeout=0.1)
        if _TTS_STOP_EVT.is_set():
            break
        with _TTS_PLAYER_LOCK:
            if not _TTS_FLUSH_EVT.is_set():
                # 仅是轮询超时唤醒，合成方未声明整句就绪 → 继续等（不取缓冲）
                if _TTS_DONE_EVT.is_set():
                    break
                continue
            if not _TTS_BUF:
                _TTS_FLUSH_EVT.clear()
                if _TTS_DONE_EVT.is_set():
                    break
                continue
            blk = bytes(_TTS_BUF)
            _TTS_BUF.clear()
            _TTS_FLUSH_EVT.clear()
        try:
            seq += 1
            # 句首淡入+句尾淡出：合成端音频开头/结尾直接是非零波形，
            # 不处理则系统播放器从静音突变到语音会爆"咚"
            pcm = _sub("agent_tts")._fade_edges(blk)
            wav = _sub("agent_tts")._pcm_to_wav(pcm)             # 24k mono 16bit WAV
            path = _os.path.join(_tmp.gettempdir(), f"tts_play_{seq}.wav")
            with open(path, "wb") as f:
                f.write(wav)
            winsound.PlaySound(path, winsound.SND_FILENAME)   # 同步播放（系统解码）
            try:
                _os.remove(path)
            except OSError:
                pass
        except Exception:
            _time.sleep(0.03)


def _tts_play_finish():
    """合成方声明全部数据已入缓冲：通知播放线程播完剩余整句后退出"""
    global _TTS_DONE_EVT, _TTS_FLUSH_EVT
    with _TTS_PLAYER_LOCK:
        _TTS_FLUSH_EVT.set()   # 最后一句也触发播放
        _TTS_DONE_EVT.set()


def _tts_play_stop():
    """立即停止播放：PURGE 停掉当前系统播放、清空缓冲并通知播放线程退出
    （用户停止/合成失败时调用）。SND_PURGE 会中断播放线程中阻塞的 PlaySound。"""
    global _TTS_BUF, _TTS_SEG_START
    with _TTS_PLAYER_LOCK:
        try:
            import winsound
            winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:
            pass
        _TTS_BUF.clear()
        _TTS_SEG_START = True
        _TTS_STOP_EVT.set()
        _TTS_DONE_EVT.set()
        _TTS_FLUSH_EVT.set()


# 沙盒拒绝返回（无截图）
def _blocked(text: str) -> dict:
    return {"text": text, "images": []}



def _flag(v, default: bool = False) -> bool:
    """布尔解析：兼容字符串 true/false/1/0 与中文 是/开。"""
    if v is None:
        return default
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on", "是", "开")
    return bool(v)

def _require_list(args: dict, key: str, tool: str):
    """校验三件套必填列表参数：缺失或类型不对时返回清晰报错（供 AI 改正）。
    避免静默用空列表生成空/损坏文档（即“生成结果与预期不符”的常见根因）。"""
    v = args.get(key)
    if v is None:
        return None, (f"[{tool}] 缺少必填参数 {key}。它是数组(array)："
                      f"{key}=[{{...}}, ...]。请用方括号 [] 传数组，不要传单个对象。")
    if isinstance(v, list):
        return v, None
    return None, (f"[{tool}] 参数 {key} 类型应为数组(array)，实际收到 "
                  f"{type(v).__name__}。请改用 [{key}=[{{...}}, ...]] 传递列表。")


def _require_dicts(items: list, tool: str, key: str):
    """校验列表内每一项是否为对象(dict)：含非 dict 项时返回清晰报错。"""
    bad = [i for i in items if not isinstance(i, dict)]
    if bad:
        return (f"[{tool}] 参数 {key} 的每一项都应为对象 {{...}}，发现 "
                f"{len(bad)} 项不是对象（如字符串/数字）。请每项都用 {{...}} 包裹。")
    return None


def _ask_user(args: dict, ask_user_cb) -> dict:
    """向用户提问（需求不明确时强制提问，禁止猜测执行）"""
    question = str(args.get("question", "")).strip()
    if not question:
        return _blocked("[ask_user] 缺少问题")
    if not ask_user_cb:
        return _blocked("[ask_user] 未接入提问面板，请基于已有信息继续")
    try:
        answer = ask_user_cb({
            "question": question,
            "options": args.get("options") or [],
            "multi_select": bool(args.get("multi_select", False)),
        })
        return {"text": f"用户回答：{answer}", "images": []}
    except Exception as e:
        return _blocked(f"[ask_user] 提问失败: {e}")


# 文件写/改工具的位置参数顺序（部分上游/模型以数组形式传参，如 write_file=["路径","内容"]）
_POSITIONAL_ARGS = {
    "write_file": ("path", "content", "append"),
    "edit_file": ("path", "old_text", "new_text"),
    "search_replace": ("path", "old_text", "new_text"),
    "insert_lines": ("path", "line", "content"),
}


def _positional_args(name: str, lst) -> dict:
    """把列表形式参数按工具签名顺序映射为字典：
    write_file=[path, content] / edit_file=[path, old, new] / search_replace 同理。
    仅当全部元素为可转字符串的非空标量且数量不超过签名参数数时启用；
    否则返回 {}（保持原缺参提示，交由模型按提示自纠）。
    供引擎解析 tool_calls.arguments 得到列表时调用——此前列表被一律置空参，
    导致模型以数组传参时反复报「缺少 path、content」且无法自纠，5 轮后被迫停止。"""
    keys = _POSITIONAL_ARGS.get(name)
    if not keys or not isinstance(lst, list) or not lst:
        return {}
    out = {}
    for k, v in zip(keys, lst):
        if isinstance(v, (dict, list)):
            return {}   # 嵌套结构无法按位置解释：放弃，保持缺参提示
        sv = str(v).strip() if v is not None else ""
        if sv:
            out[k] = sv
    return out


def _normalize_file_args(name: str, args: dict) -> dict:
    """文件写/改工具的参数名归一化：兼容 AI 常见的参数名漂移（别名），
    避免因参数名不一致（如 text/data 代替 content、file 代替 path、
    old/new 代替 old_text/new_text）被 _missing_required 误判为缺参。"""
    if name not in ("write_file", "edit_file", "search_replace", "insert_lines"):
        return dict(args or {})
    out = dict(args or {})
    # 别名表：标准参数名 -> 候选别名（按优先级）
    # content 特意覆盖网页/代码场景常见漂移键（html/code/source/contents 等），
    # 避免模型写网页时把完整内容放到 html/code 键而导致 content 被误判缺失。
    aliases = {
        "path": ("file", "file_path", "filepath", "filename", "target",
                 "filePath", "destination", "dest"),
        "content": ("text", "data", "file_content", "file_content_text", "body",
                    "payload", "html", "code", "source", "contents", "fileContents",
                    "file_text", "content_text", "markup", "page", "file_content_text"),
        "old_text": ("old", "old_content", "old_content_text", "before", "find", "original",
                     "search", "oldText", "old_text_content"),
        "new_text": ("new", "new_content", "new_content_text", "after", "replace",
                     "replacement", "replace_with", "newText", "new_text_content"),
        "append": ("is_append", "append_mode"),
        "line": ("line_number", "lineno", "at_line", "insert_at", "before_line", "position"),
    }
    for std, cands in aliases.items():
        if std in out and out[std] not in (None, ""):
            continue
        for c in cands:
            v = out.get(c)
            if v not in (None, ""):
                out[std] = v
                break
    # content 值解包：模型偶发把内容嵌套成对象/数组（如 content 为 {"html": "..."}
    # 或 {"content": "..."}），直接 str(dict) 会把 repr 垃圾写入文件。
    # 这里尝试从嵌套结构中取出真正的文本，仅取其中一段字符串（保序优先 html/text/content）。
    cv = out.get("content")
    if isinstance(cv, dict):
        for _k in ("html", "text", "content", "body", "code", "markup", "source"):
            _v = cv.get(_k)
            if isinstance(_v, str) and _v:
                out["content"] = _v
                break
        else:
            _strs = [_v for _v in cv.values() if isinstance(_v, str) and _v]
            if len(_strs) == 1:
                out["content"] = _strs[0]
    elif isinstance(cv, list):
        _strs = [_v for _v in cv if isinstance(_v, str) and _v]
        if _strs:
            out["content"] = "\n".join(_strs)
    return out


def _param_hint(name: str, missing: list) -> str:
    """缺参时给 AI 的纠错提示：明确正确参数名与用途，帮助其自纠后重新调用。
    write_file 缺 content 时额外点明：内容必须放在 content 键（写 HTML/代码也不例外），
    避免模型用 html/code/source 等键名导致反复缺参。"""
    if name not in ("write_file", "edit_file", "search_replace", "insert_lines"):
        return ""
    desc = {
        "path": "path=文件路径",
        "content": ("content=要写入的完整内容（务必使用 content 作为键名并放入完整内容，"
                    "写 HTML/代码/任何文本时亦然，不要用 html、code、source 等其他键名）"),
        "old_text": "old_text=待替换的原内容（须与文件中精确一致）",
        "new_text": "new_text=替换后的新内容",
        "line": "line=插入位置的行号（1-based，可选，缺省/越界为末尾追加）",
    }
    hints = [desc.get(m, m) for m in missing]
    return "正确参数：" + "，".join(hints) + "。"


def _missing_required(name: str, args: dict) -> list:
    """按工具 schema 的 required 字段校验必填参数（缺失/空值视为缺）。
    缺参直接拦截返回提示，避免空参误调用导致返工/失败。"""
    for t in TOOLS:
        fn = t.get("function") or {}
        if fn.get("name") != name:
            continue
        req = ((fn.get("parameters") or {}).get("required")) or []
        return [str(r) for r in req if not args.get(r)]
    return []


def _browser_retry_call(fn):
    """浏览器操作加固：fn 抛出「断开/崩溃」类异常时，重建浏览器实例后重试一次。
    页面崩溃/标签被关/连接断开等通常靠一次重建即可恢复，避免任务中断。
    返回 (ok, value)：ok=True 时 value 为 fn() 结果；ok=False 时 value 为错误文本。"""
    import re as _re

    def _run():
        try:
            return True, fn()
        except Exception as e:   # noqa: BLE001
            return False, str(e)

    ok, val = _run()
    if ok:
        return ok, val
    if not _re.search(r"(disconnect|closed|crash|Target closed|Cannot find context|connection)",
                      val, _re.I):
        return ok, val   # 非断开类错误（如元素找不到/业务失败）：不重试，交由模型换方案
    try:
        _browser_ctl().stop()   # 释放旧实例后重建
        time.sleep(0.6)
    except Exception:
        pass
    return _run()


# 文件变更观察者：AI 写/改文件成功后回调 fn(path, old_content, new_content)。
# 供 UI（代码预览面板）展示增删差异高亮；fn 可能在工作线程被调用，须自行切主线程。
_FILE_OBSERVERS = []


def register_file_observer(fn) -> None:
    """注册文件变更观察者（幂等）。"""
    if callable(fn) and fn not in _FILE_OBSERVERS:
        _FILE_OBSERVERS.append(fn)


def _notify_file_changed(path, old_text: str, new_text: str) -> None:
    for fn in list(_FILE_OBSERVERS):
        try:
            fn(str(path), old_text, new_text)
        except Exception:
            pass
    _record_edit_delta(path, old_text, new_text)


# ------------------------------------------------------------
# 每轮任务的文件变更统计（气泡末尾「-N +M」徽章数据源）
# 线程局部累计：任务线程内 write/edit/search_replace 成功即累加，
# 引擎在 run() 结束（同一线程）取快照挂到实例属性供 UI 主线程读取；
# 未处于任务上下文（无 reset）时不统计，多会话并发互不串扰。
# ------------------------------------------------------------
_EDIT_DELTA = threading.local()
# 并发编辑保护：同一轮多个写操作并行时，各 worker 线程共享同一个累计桶，
# 累加（读改写）必须加锁，否则 added/removed/files 会因竞争丢失更新。
_EDIT_DELTA_LOCK = threading.Lock()


def reset_edit_delta() -> dict:
    """任务开始：开启本轮文件变更统计，返回累计桶 dict（供引擎持有一份跨线程引用）。
    注意：工具实际在 _call_with_stop 派生的 worker 线程执行，线程局部不穿透，
    引擎须经 execute 包装调用 bind_edit_bucket 把 worker 线程绑定到同一桶。"""
    d = {"added": 0, "removed": 0, "files": {}}
    _EDIT_DELTA.d = d
    return d


def bind_edit_bucket(bucket):
    """把当前执行线程绑定到本轮文件变更桶（worker 线程入口调用；bucket=None 关闭统计）。"""
    _EDIT_DELTA.d = bucket


def take_edit_delta() -> dict:
    """取走本轮累计并关闭统计（引擎任务结束时调用），返回 {added, removed, files{path:[增,删]}}。
    取走后回到「未开启」状态：任务外的文件写入不再累计（下一轮由 reset_edit_delta 开启）。"""
    d = getattr(_EDIT_DELTA, "d", None)
    _EDIT_DELTA.d = None
    if d is None:
        return {"added": 0, "removed": 0, "files": {}}
    return d


def _diff_line_delta(old_text: str, new_text: str):
    """按行 diff 出 (新增行数, 删除行数)；超大文件退化为行数差防卡顿。"""
    old_l = (old_text or "").splitlines()
    new_l = (new_text or "").splitlines()
    if len(old_l) + len(new_l) > 50000:
        return len(new_l), len(old_l)
    sm = difflib.SequenceMatcher(None, old_l, new_l, autojunk=False)
    added = removed = 0
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "insert":
            added += j2 - j1
        elif op == "delete":
            removed += i2 - i1
        elif op == "replace":
            added += j2 - j1
            removed += i2 - i1
    return added, removed


def _record_edit_delta(path, old_text: str, new_text: str) -> None:
    """成功写/改文件后累计行数变更（任务线程内）。

    并发编辑（同一轮多个写操作并行）时各 worker 线程共享同一累计桶，
    因此 diff 计算放在锁外（耗时），仅累加段加锁，保证多文件并行计数不丢失。
    """
    d = getattr(_EDIT_DELTA, "d", None)
    if d is None:
        return
    added, removed = _diff_line_delta(old_text, new_text)
    with _EDIT_DELTA_LOCK:
        d["added"] += added
        d["removed"] += removed
        f = d["files"].setdefault(str(path), [0, 0])
        f[0] += added
        f[1] += removed


def execute_tool(name: str, args: dict, allow_dangerous: bool = False,
                 reject_risky: bool = False,
                 ask_user_cb=None, status_cb=None, workflow=None) -> dict:
    """执行工具，返回 {"text", "images"}。

    allow_dangerous=True 时放行危险操作（AskBeforeEdit 模式下用户显式确认后的授权）；
    False 时危险操作硬拒绝（YOLO 自动放行场景的安全底线）。
    reject_risky=True 时"需确认(risky)"的工具一并硬拒绝：供子代理等无用户确认通道
    的执行上下文使用——否则 risky 工具会绕过主引擎的确认门控被静默执行（如 web/
    剪贴板读取）。
    ask_user_cb: Callable[[dict], str] 提问回调（阻塞式，返回用户回答文本）。
    status_cb: Callable[[str], None] 可选状态回调（透传给 run_command 的沙盒运行时下载进度）。
    workflow: 目标工作流；缺省取引擎任务线程当前工作流（工作流隔离自定义工具）。
    """
    _wf = workflow if workflow is not None else _wf_current()
    args = _normalize_file_args(name, args or {})
    missing = _missing_required(name, args)
    if missing:
        hint = _param_hint(name, missing)
        return _blocked(f"[工具参数缺失] {name} 缺少必填参数：{', '.join(missing)}。"
                        f"{hint}请补充后重新调用")
    # Cordis：自定义工作流工具同名优先（覆盖内置实现）
    if name in _custom_names(_wf):
        # 覆盖高危内置工具时，先用内置沙盒规则评估：dangerous 不允许被自定义实现豁免
        # （否则同名覆盖会在 execute_tool 内跳过 assess_tool，把本应拒绝的操作放行）。
        if name in _CUSTOM_SANDBOX_NAMES:
            _clvl, _crs = _sub("agent_sandbox").assess_tool(name, args)
            if _clvl == "dangerous" and not allow_dangerous:
                return _blocked(f"[沙盒拒绝] {_crs}（内置危险操作不可被自定义工具覆盖豁免）")
        return _run_custom_tool(name, args, allow_dangerous, _wf)
    if name == "ask_user":
        return _ask_user(args, ask_user_cb)
    if name in SUB_AGENT_TOOLS:
        return _blocked(f"[{name}] 子 Agent 工具由主引擎调度执行")
    level, reason = _sub("agent_sandbox").assess_tool(name, args)
    if level == "dangerous" and not allow_dangerous:
        return _blocked(f"[沙盒拒绝] {reason}")
    if level == "risky" and reject_risky:
        # 无用户确认通道（如子代理）不得执行需确认的工具，避免绕过主引擎确认门控
        return _blocked(f"[沙盒拒绝] {reason}（当前为无确认执行上下文）")

    try:
        # Cordis 工作流管理工具
        if name == "list_workflows":
            return _list_workflows()
        if name == "create_workflow":
            return _create_workflow(str(args.get("name", "")),
                                    str(args.get("description", "")),
                                    args.get("files") if isinstance(args.get("files"), list) else None,
                                    str(args.get("preset", "") or ""))
        if name == "list_builtin_workflows":
            return _list_builtin_workflows()
        if name == "switch_workflow":
            return _switch_workflow(str(args.get("name", "")))
        if name == "list_workflow_agents":
            return _list_workflow_agents()
        if name == "use_workflow_agent":
            return _use_workflow_agent(str(args.get("name", "")))
        if name == "inspect_workflow":
            return _inspect_workflow(str(args.get("name", "")))
        if name == "edit_agent_file":
            return _edit_agent_file(str(args.get("name", "")), str(args.get("file", "")),
                                    str(args.get("action", "read")), args.get("content"))
        if name == "delete_workflow":
            return _delete_workflow(str(args.get("name", "")))
        if name == "manage_uiux":
            return _manage_uiux(args)
        if name == "register_panel_btn":
            return _manage_panel_btn(args)
        if name == "inspect_customization":
            return _inspect_customization()
        if name == "list_sub_agents":
            return _list_sub_agents()
        if name == "register_sub_agent":
            return _register_sub_agent(args)
        if name == "shared_context":
            return _shared_context(args)
        if name == "set_session_name":
            return _set_session_name(args)
        if name == "chat_with":
            return _chat_with(args)
        if name == "look_context":
            return _look_context(args)
        if name == "pause_agent":
            return _pause_agent(args)
        if name == "resume_agent":
            return _resume_agent(args)
        if name == "warn_agent":
            return _warn_agent(args)
        if name == "list_agents_network":
            return _list_agents_network(args)
        if name == "register_agent_network":
            return _register_agent_network(args)
        if name == "register_feature_panel":
            return _register_feature_panel(args)
        if name == "set_feature_deps":
            return _set_feature_deps(args)
        if name == "create_mcp":
            return _create_mcp(args)
        if name == "list_mcp":
            return _list_mcp()
        if name == "set_mcp":
            return _set_mcp(args)
        if name == "list_agents":
            return _list_agents()
        if name == "create_agent":
            return _create_agent(args)
        if name == "delete_agent":
            return _delete_agent(str(args.get("name", "")))
        if name == "find_app":
            return {"text": _sub("agent_find").find_app(
                str(args.get("query", "")),
                _sub("agent_sandbox").to_int(args.get("limit", 10))), "images": []}
        if name == "search_files":
            folder = str(args.get("folder", "")).strip()
            if not folder and WORKDIR:
                folder = WORKDIR   # 未指定目录时默认在工作目录内查找
            return {"text": _sub("agent_find").search_files(
                str(args.get("query", "")), folder,
                _sub("agent_sandbox").to_int(args.get("limit", 30)),
                str(args.get("ext", "")),
                bool(args.get("case_sensitive", False)),
                str(args.get("exclude", ""))), "images": []}
        if name == "grep":
            folder = str(args.get("folder", "")).strip()
            if not folder and WORKDIR:
                folder = WORKDIR   # 未指定目录时默认在工作目录内检索
            return {"text": _sub("agent_find").grep_contents(
                str(args.get("pattern", "")), folder,
                str(args.get("glob", "")),
                _sub("agent_sandbox").to_int(args.get("max_results", 50)),
                bool(args.get("case_sensitive", False)),
                _sub("agent_sandbox").to_int(args.get("context", 0)),
                bool(args.get("line_numbers", True))), "images": []}
        if name == "search_code":
            folder = str(args.get("folder", "")).strip()
            if not folder and WORKDIR:
                folder = WORKDIR   # 未指定目录时默认在工作目录内检索
            return {"text": _sub("agent_find").search_code(
                str(args.get("pattern", "")), folder,
                str(args.get("glob", "")),
                _sub("agent_sandbox").to_int(args.get("max_results", 50)),
                bool(args.get("case_sensitive", False)),
                _sub("agent_sandbox").to_int(args.get("context", 2)),
                str(args.get("name", ""))), "images": []}
        if name == "run_command":
            return _run_command(str(args.get("command", "")),
                                _sub("agent_sandbox").to_int(args.get("wait", 5)),
                                bool(args.get("force_quit", False)),
                                str(args.get("cwd", "")),
                                str(args.get("stdin", "")),
                                _sub("agent_sandbox").to_int(args.get("max_output", 0)) or None,
                                status_cb)
        if name == "check_command":
            return _check_command(_sub("agent_sandbox").to_int(args.get("cmd_id", 0)) or None)
        if name == "read_file":
            return _read_file(str(args.get("path", "")),
                              _sub("agent_sandbox").to_int(args.get("offset", 0)) or None,
                              _sub("agent_sandbox").to_int(args.get("limit", 0)) or None,
                              allow_dangerous,
                              bool(args.get("number_lines", False)))
        if name == "write_file":
            return _write_file(str(args.get("path", "")), str(args.get("content", "")),
                               _flag(args.get("append", False)), allow_dangerous)
        if name == "edit_file":
            return _edit_file(str(args.get("path", "")),
                              str(args.get("old_text", "")),
                              str(args.get("new_text", "")), allow_dangerous)
        if name == "search_replace":
            return _search_replace(str(args.get("path", "")),
                                   str(args.get("old_text", "")),
                                   str(args.get("new_text", "")),
                                   str(args.get("count", "once") or "once"), allow_dangerous)
        if name == "insert_lines":
            return _insert_lines(str(args.get("path", "")),
                                 _sub("agent_sandbox").to_int(args.get("line", 0)),
                                 str(args.get("content", "")), allow_dangerous)
        if name == "undo_file":
            return _undo_file(str(args.get("path", "")), allow_dangerous)
        if name == "update_todo":
            return _update_todo(args.get("todos") if isinstance(args.get("todos"), list) else [])
        if name == "list_todo":
            return _list_todo()
        if name == "new_project":
            return _new_project(str(args.get("path", "")),
                                str(args.get("kind", "generic") or "generic"),
                                str(args.get("name", "")))
        if name == "delete_file":
            return _delete_file(str(args.get("path", "")), allow_dangerous)
        if name == "list_directory":
            return _list_directory(str(args.get("path", "")), allow_dangerous)
        if name == "save_memory":
            return _save_memory(str(args.get("content", "")))
        if name == "load_memory":
            return _load_memory()
        # 系统信息（原 MCP server 工具内置化）
        if name == "system_info":
            return _system_info()
        if name == "get_time":
            return _get_time()
        if name == "env_var":
            return _env_var(str(args.get("name", "")))
        # zhuzhu_Copilot 能力
        if name == "optimize_memory":
            return _optimize_memory()
        if name == "uninstall_app":
            return _uninstall_app(str(args.get("name", "")))
        if name == "migrate_app":
            return _migrate_app(str(args.get("name", "")), str(args.get("target", "")))
        if name == "fast_download":
            return _fast_download(str(args.get("url", "")), str(args.get("dest_dir", "")))
        if name == "web_fetch":
            return _web_fetch(str(args.get("url", "")),
                              str(args.get("method", "GET")),
                              args.get("headers") if isinstance(args.get("headers"), dict) else None,
                              str(args.get("body", "")),
                              _sub("agent_sandbox").to_int(args.get("max_chars", 8000)))
        # ---- 浏览器操控（内置优先，未装配时回退独立外部实例） ----
        if name == "browser_open":
            ok, msg = _browser_ctl().start(
                str(args.get("engine", "")), bool(args.get("headless", False)))
            return {"text": msg, "images": []}
        if name == "browser_navigate":
            def _nav():
                ok, msg = _browser_ctl().navigate(str(args.get("url", "")))
                if not ok:
                    return {"text": f"[browser_navigate] {msg}", "images": []}
                try:
                    shot = _browser_ctl().screenshot()
                    return {"text": msg, "images": [shot]}
                except Exception:
                    return {"text": msg, "images": []}
            ok, val = _browser_retry_call(_nav)
            return val if ok else {"text": f"[browser_navigate] {val}", "images": []}
        if name == "browser_snapshot":
            def _snap():
                ctl = _browser_ctl()
                return {"text": "页面元素清单（按 [编号] 或文字引用操作）：\n" + ctl.summarize(),
                        "images": [ctl.screenshot()]}
            ok, val = _browser_retry_call(_snap)
            if not ok:
                return {"text": f"[browser_snapshot] {val}", "images": []}
            return val
        if name == "browser_click":
            eid = args.get("id")
            text = str(args.get("text", "")).strip()
            def _clk():
                ctl = _browser_ctl()
                return ctl.click(eid=_sub("agent_sandbox").to_int(eid) if eid is not None else None,
                                 text=text or None,
                                 button=str(args.get("button", "left")))
            ok, val = _browser_retry_call(_clk)
            if not ok:
                return {"text": f"[browser_click] {val}", "images": []}
            ok2, msg, shot = val
            if not ok2:
                return {"text": f"[browser_click] {msg}", "images": []}
            return {"text": msg, "images": [shot] if shot else []}
        if name == "browser_type":
            eid = args.get("id")
            def _typ():
                ctl = _browser_ctl()
                return ctl.type_text(
                    str(args.get("text", "")),
                    eid=_sub("agent_sandbox").to_int(eid) if eid is not None else None,
                    target=str(args.get("target", "")).strip() or None)
            ok, val = _browser_retry_call(_typ)
            if not ok:
                return {"text": f"[browser_type] {val}", "images": []}
            ok2, msg, shot = val
            if not ok2:
                return {"text": f"[browser_type] {msg}", "images": []}
            return {"text": msg, "images": [shot] if shot else []}
        if name == "browser_eval":
            def _evl():
                return _browser_ctl().eval(str(args.get("js", ""))).get("text", "")
            ok, val = _browser_retry_call(_evl)
            if not ok:
                return {"text": f"[browser_eval] {val}", "images": []}
            return {"text": val, "images": []}
        if name == "browser_html":
            def _html():
                return _browser_ctl().html(str(args.get("selector", ""))).get("text", "")
            ok, val = _browser_retry_call(_html)
            if not ok:
                return {"text": f"[browser_html] {val}", "images": []}
            return {"text": val, "images": []}
        if name == "browser_scroll":
            eid = args.get("id")
            def _scr():
                ctl = _browser_ctl()
                return ctl.scroll(str(args.get("direction", "down")),
                                  _sub("agent_sandbox").to_int(args.get("amount", 0)) or None,
                                  eid=_sub("agent_sandbox").to_int(eid) if eid is not None else None,
                                  selector=str(args.get("selector", "")).strip() or None)
            ok, val = _browser_retry_call(_scr)
            if not ok:
                return {"text": f"[browser_scroll] {val}", "images": []}
            ok2, msg, shot = val
            if not ok2:
                return {"text": f"[browser_scroll] {msg}", "images": []}
            return {"text": msg, "images": [shot] if shot else []}
        if name == "browser_close":
            ok, msg = _browser_ctl().stop()
            return {"text": msg, "images": []}
        if name == "browser_tabs":
            pages = _browser_ctl().list_pages()
            if not pages:
                return {"text": "[browser_tabs] 当前无可用页面标签（浏览器未启动？）", "images": []}
            rows = [f"[{p['id']}] {p['title']} | {p['url']}" for p in pages]
            return {"text": "页面标签清单（用 browser_switch_tab(id) 切换）：\n" + "\n".join(rows),
                    "images": []}
        if name == "browser_switch_tab":
            ok, msg = _browser_ctl().switch_tab(
                _sub("agent_sandbox").to_int(args.get("id", 0)))
            return {"text": f"[browser_switch_tab] {msg}", "images": []}
        if name == "git_info":
            return _git_info(str(args.get("command", "")), str(args.get("cwd", "")))
        if name == "web_search":
            return _web_search(str(args.get("query", "")),
                               _sub("agent_sandbox").to_int(args.get("max_results", 8)),
                               str(args.get("must_include", "")))
        if name == "clipboard":
            return _clipboard(str(args.get("action", "read")), str(args.get("text", "")))
        if name == "extract_text":
            return _extract_text(str(args.get("path", "")))
        if name == "generate_image":
            return _generate_image(str(args.get("prompt", "")),
                                   str(args.get("ratio", "1:1")),
                                   str(args.get("dest_dir", "")))
        if name == "create_docx":
            _paras, _err = _require_list(args, "paragraphs", "create_docx")
            if _err:
                return _blocked(_err)
            # images/wordart 为可选：缺失按空列表，提供但类型错则报错
            _images = args.get("images")
            if _images is not None:
                if not isinstance(_images, list):
                    return _blocked(f"[create_docx] 参数 images 类型应为数组(array)，实际收到 "
                                    f"{type(_images).__name__}。请用 images=[...] 传递列表。")
            else:
                _images = []
            _words = args.get("wordart")
            if _words is not None:
                if not isinstance(_words, list):
                    return _blocked(f"[create_docx] 参数 wordart 类型应为数组(array)，实际收到 "
                                    f"{type(_words).__name__}。请用 wordart=[{{...}},...] 传递列表。")
                _e = _require_dicts(_words, "create_docx", "wordart")
                if _e:
                    return _blocked(_e)
            else:
                _words = []
            try:
                from zhuzhu_Copilot.office import build_docx as _office_build
                _res = _office_build(str(args.get("path", "")), str(args.get("title", "")),
                                     _paras, _images,
                                     args.get("style") if isinstance(args.get("style"), dict) else {},
                                     _words, workdir=get_workdir())
                _res["text"] += _office_verify(str(args.get("path", "")), get_workdir())
                return _res
            except Exception as _e:
                return _blocked(f"[create_docx] 生成失败: {_e}")
        if name == "create_pptx":
            _slides, _err = _require_list(args, "slides", "create_pptx")
            if _err:
                return _blocked(_err)
            _e = _require_dicts(_slides, "create_pptx", "slides")
            if _e:
                return _blocked(_e)
            try:
                from zhuzhu_Copilot.office import build_pptx as _office_build
                _res = _office_build(str(args.get("path", "")), str(args.get("title", "")),
                                     _slides,
                                     args.get("style") if isinstance(args.get("style"), dict) else {},
                                     workdir=get_workdir())
                _res["text"] += _office_verify(str(args.get("path", "")), get_workdir())
                return _res
            except Exception as _e:
                return _blocked(f"[create_pptx] 生成失败: {_e}")
        if name == "create_xlsx":
            _sheets, _err = _require_list(args, "sheets", "create_xlsx")
            if _err:
                return _blocked(_err)
            _e = _require_dicts(_sheets, "create_xlsx", "sheets")
            if _e:
                return _blocked(_e)
            try:
                from zhuzhu_Copilot.office import build_xlsx as _office_build
                _res = _office_build(str(args.get("path", "")), _sheets,
                                     args.get("style") if isinstance(args.get("style"), dict) else {},
                                     workdir=get_workdir())
                _res["text"] += _office_verify(str(args.get("path", "")), get_workdir())
                return _res
            except Exception as _e:
                return _blocked(f"[create_xlsx] 生成失败: {_e}")
        if name == "beautify_docx":
            try:
                from zhuzhu_Copilot.office import beautify_docx as _office_bd
                return _office_bd(str(args.get("path", "")),
                                  args.get("style") if isinstance(args.get("style"), dict) else {},
                                  workdir=get_workdir())
            except Exception as _e:
                return _blocked(f"[beautify_docx] 美化失败: {_e}")
        if name == "beautify_pptx":
            try:
                from zhuzhu_Copilot.office import beautify_pptx as _office_bp
                return _office_bp(str(args.get("path", "")),
                                  args.get("style") if isinstance(args.get("style"), dict) else {},
                                  workdir=get_workdir())
            except Exception as _e:
                return _blocked(f"[beautify_pptx] 美化失败: {_e}")
        if name == "beautify_xlsx":
            try:
                from zhuzhu_Copilot.office import beautify_xlsx as _office_bx
                return _office_bx(str(args.get("path", "")),
                                  args.get("style") if isinstance(args.get("style"), dict) else {},
                                  workdir=get_workdir())
            except Exception as _e:
                return _blocked(f"[beautify_xlsx] 美化失败: {_e}")
        # ---- 办公文档读取 / 编辑（office.reader / office.editor 真实实现）----
        if name in ("read_docx", "read_pptx", "read_xlsx", "read_pdf"):
            _kw = {}
            for _k in ("max_chars", "max_rows", "max_cols", "max_pages"):
                if args.get(_k) is not None:
                    _kw[_k] = _sub("agent_sandbox").to_int(args.get(_k))
            try:
                from zhuzhu_Copilot.office import read_document as _office_read
                _res = _office_read(str(args.get("path", "")), workdir=get_workdir(), **_kw)
                # 字段名对齐 handler 返回契约（read_document 已返回 text/images/meta）
                return {"text": _res.get("text", ""), "images": _res.get("images") or [],
                        "meta": _res.get("meta") or {}}
            except Exception as _e:
                return _blocked(f"[{name}] 读取失败: {_e}")
        if name in ("edit_docx", "edit_pptx", "edit_xlsx"):
            _ops = args.get("ops")
            if not isinstance(_ops, list):
                return _blocked(f"[{name}] 参数 ops 类型应为数组(array)，实际收到 "
                                f"{type(_ops).__name__}。请用 ops=[{{\"op\":\"...\",...}}, ...] 传递操作列表。")
            _bad = _require_dicts(_ops, name, "ops")
            if _bad:
                return _blocked(_bad)
            if not _ops:
                return _blocked(f"[{name}] ops 为空：请至少提供一条操作（可用 op 见工具说明）。")
            try:
                from zhuzhu_Copilot.office import edit_document as _office_edit
                _res = _office_edit(str(args.get("path", "")), _ops, workdir=get_workdir())
                _res["text"] += _office_verify(str(args.get("path", "")), get_workdir())
                return _res
            except Exception as _e:
                return _blocked(f"[{name}] 编辑失败: {_e}")
        if name == "create_skill":
            return _create_skill(str(args.get("name", "")),
                                 str(args.get("description", "")),
                                 str(args.get("instruction", "")))
        if name == "create_plugin":
            return _create_plugin(str(args.get("description", "")),
                                  str(args.get("kind", "combined")))
        # ---- TTS 语音合成（Qwen-TTS 声音复刻，DashScope 真实 API） ----
        if name == "tts_create_voice":
            try:
                vid = _sub("agent_tts").create_voice(
                    str(args.get("audio_path", "")),
                    str(args.get("target_model", _sub("agent_tts").DEFAULT_TARGET_MODEL)),
                    str(args.get("preferred_name", "diede")))
                return {"text": f"音色创建成功：{vid}", "images": []}
            except Exception as e:
                return _blocked(f"[tts_create_voice] {e}")
        if name == "tts_query_voice":
            try:
                d = _sub("agent_tts").query_voice(
                    str(args.get("voice_id", "")),
                    str(args.get("target_model", _sub("agent_tts").DEFAULT_TARGET_MODEL)))
                return {"text": json.dumps(d, ensure_ascii=False, indent=2), "images": []}
            except Exception as e:
                return _blocked(f"[tts_query_voice] {e}")
        if name == "tts_delete_voice":
            try:
                _sub("agent_tts").delete_voice(str(args.get("voice_id", "")))
                return {"text": f"音色已删除：{args.get('voice_id', '')}", "images": []}
            except Exception as e:
                return _blocked(f"[tts_delete_voice] {e}")
        if name == "tts_speak":
            try:
                play = bool(args.get("play", True))
                play_ok = True
                if play:
                    play_ok = _tts_play_start()   # 播放器不可用时不再静默：明确反馈给 AI/用户
                def _on_chunk(pcm):
                    if play and play_ok:
                        _tts_play_chunk(pcm)
                out = _sub("agent_tts").synthesize_stream(
                    str(args.get("text", "")),
                    str(args.get("voice_id", "")),
                    str(args.get("model", _sub("agent_tts").DEFAULT_TARGET_MODEL)),
                    on_chunk=_on_chunk,
                    output_path=str(args.get("output_path", "")))
                if play and play_ok:
                    _tts_play_flush()    # 整句累积完成：一次性播放
                    _tts_play_finish()   # 无后续数据：播完即退出
                note = "（已自动播放）" if (play and play_ok) else \
                       ("（合成成功，但自动播放不可用：pygame 未安装或音频初始化失败，"
                        "已保存音频文件，可用其他播放器打开）" if play else "")
                return {"text": f"语音合成完成，已保存：{out}" + note, "images": []}
            except Exception as e:
                _tts_play_stop()
                return _blocked(f"[tts_speak] {e}")
    except Exception as e:
        return _blocked(f"[工具执行错误] {name}: {e}")
    # Cordis：未命中内置时交给工作流自定义工具（新增工具）
    if name in _custom_names(_wf):
        return _run_custom_tool(name, args, allow_dangerous, _wf)
    return _blocked(f"[未知工具] {name}")


def _run_command(command: str, wait: int = 5, force_quit: bool = False,
                 cwd: str = "", stdin_text: str = "", max_output: int = None,
                 status_cb=None) -> dict:
    """执行命令，AI 自主选择等待/强制退出策略。

    - wait 秒内完成：返回完整输出与退出码
    - wait 秒内未完成：
      · force_quit=True  → taskkill /T 强制结束进程树，返回已收集输出
      · force_quit=False → 转入后台注册表（可 check_command 轮询进度），返回命令 ID
    cwd 指定工作目录（默认工作目录）；stdin_text 写入标准输入（交互命令）；
    max_output 限制返回文本长度（默认不限）。
    reader 线程持续排空 stdout/stderr，轮询期间可拿到增量进度。

    沙盒运行时：命令命中 node/npm/npx、python/py/pip 时，运行时已随安装程序预置
    （便携版），首次使用从安装目录本地复制到 ~/.zhuzhu_Copilot/runtime/ 并前置到
    **子进程级** PATH（不写系统 PATH、不在对话内联网下载），npm/pip 缓存与依赖
    落在沙盒内，与用户环境隔离。
    status_cb：可选状态回调，用于透传运行时部署/安装进度。
    """
    # 沙盒运行时透明拦截：先确保缺失的运行时就绪，再注入进程级环境
    run_env = None
    try:
        from zhuzhu_Copilot.core import agent_runtime
        for rt in agent_runtime.needed_runtimes(command):
            if not agent_runtime.is_installed(rt):
                _ok, _msg = agent_runtime.ensure_runtime(rt, status_cb)
                if _ok and status_cb:
                    status_cb(_msg)
        run_env = agent_runtime.sandbox_env(command)
    except Exception:
        run_env = None   # 安装异常时回退系统环境（best effort）
    try:
        wait = max(0, int(wait))   # 不做上限：长命令可无限等待，由 stop/转后台机制兜底
        run_cwd = str(_resolve(cwd)) if (cwd or "").strip() else (WORKDIR or None)
        # Windows 工作环境统一使用 PowerShell（兼容中文路径/命令），并强制 UTF-8 输入输出编码；
        # 二进制模式读取管道，统一由 _decode_robust 智能解码（UTF-8 优先，回退 OEM 代码页）。
        if os.name == "nt":
            ps_cmd = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
                      "[Console]::InputEncoding=[Text.Encoding]::UTF8;"
                      "$OutputEncoding=[Text.Encoding]::UTF8;"
                      + command)
            proc = subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-NonInteractive",
                 "-ExecutionPolicy", "Bypass", "-Command", ps_cmd],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.PIPE,
                cwd=run_cwd, env=run_env, creationflags=_CREATE_NO_WINDOW)
        else:
            proc = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, stdin=subprocess.PIPE,
                                    cwd=run_cwd, env=run_env)
    except Exception as e:
        return _blocked(f"[沙盒] 命令执行失败: {e}")

    if (stdin_text or "").strip():
        enc = "utf-8" if os.name == "nt" else _console_encoding()
        try:
            proc.stdin.write((stdin_text + "\n").encode(enc, "replace"))
        except Exception:
            pass
    try:
        proc.stdin.close()
    except Exception:
        pass

    out_lines, err_lines = [], []
    lock = threading.Lock()
    threads = [
        threading.Thread(target=_drain_pipe, args=(proc.stdout, out_lines, lock), daemon=True),
        threading.Thread(target=_drain_pipe, args=(proc.stderr, err_lines, lock), daemon=True),
    ]
    for t in threads:
        t.start()

    # 轮询等待：wait 秒内每 1s 检查一次进程是否退出（期间输出持续被 reader 线程收集）
    deadline = time.time() + wait
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        time.sleep(1.0)

    with lock:
        out = _decode_robust(b"".join(out_lines)).strip()
        err = _decode_robust(b"".join(err_lines)).strip()
    code = proc.poll()

    def _compose() -> str:
        text = out
        if err:
            text += f"\n[stderr] {err}" if text else f"[stderr] {err}"
        if not text:
            text = f"（命令完成，退出码 {code}）"
        if max_output and max_output > 0 and len(text) > max_output:
            text = text[:max_output] + f"\n…（输出过长，已截断至 {max_output} 字符）"
        return text

    if code is not None:
        for t in threads:
            t.join(1.0)   # 等 reader 线程排空剩余输出
        return {"text": _compose(), "images": []}

    if force_quit:
        _kill_process_tree(proc.pid)
        # 留出清理时间后再读一次输出
        time.sleep(0.8)
        for t in threads:
            t.join(1.0)
        with lock:
            out = _decode_robust(b"".join(out_lines)).strip()
            err = _decode_robust(b"".join(err_lines)).strip()
        code = proc.poll()
        return {"text": f"命令在 {wait}s 内未完成，已按 force_quit 强制结束（退出码 {code}）。\n" + _compose(),
                "images": []}

    # 转入后台运行：注册并返回 ID，AI 用 check_command 轮询进度
    cmd_id = next(_cmd_seq)
    with _cmds_lock:
        _running_cmds[cmd_id] = {
            "proc": proc, "command": command, "out": out_lines, "err": err_lines,
            "lock": lock, "threads": threads, "start": time.strftime("%H:%M:%S"),
            "t0": time.time(),
        }
    _cleanup_running_cmds()   # 顺带清理已退出/超龄的后台命令（防注册表堆积与孤儿进程）
    text = (f"命令已转入后台运行（ID {cmd_id}，{wait}s 内未完成）。可用 "
            f"check_command(cmd_id={cmd_id}) 轮询进度。当前输出：\n" + _compose())
    return {"text": text, "images": []}


def _check_command(cmd_id: int = None) -> dict:
    """轮询后台命令进度：cmd_id 为空时列出全部；指定 ID 时返回最新输出，
    已结束则返回最终输出与退出码并从注册表移除。"""
    if cmd_id is None:
        # 顺手清理已结束的命令，避免注册表堆积僵尸条目
        for i in [i for i, r in _running_cmds.items() if r["proc"].poll() is not None]:
            del _running_cmds[i]
        if not _running_cmds:
            return {"text": "当前没有后台运行中的命令", "images": []}
        lines = [f"ID {i}: {rec['command'][:80]}（{rec['start']} 启动）"
                 for i, rec in _running_cmds.items()]
        return {"text": "后台运行中的命令：\n" + "\n".join(lines), "images": []}

    rec = _running_cmds.get(cmd_id)
    if rec is None:
        return {"text": f"未找到后台命令 ID {cmd_id}（可能已结束或被清理）", "images": []}
    code = rec["proc"].poll()
    if code is None:
        return {"text": f"命令 ID {cmd_id} 仍在运行（{time.strftime('%H:%M:%S')}）。当前输出：\n"
                        + _collect(rec), "images": []}
    for t in rec.get("threads", []):
        t.join(1.0)   # 等 reader 线程排空剩余输出
    del _running_cmds[cmd_id]
    return {"text": f"命令 ID {cmd_id} 已结束，退出码 {code}。最终输出：\n" + _collect(rec), "images": []}


# 文件操作撤销备份目录：write/edit/search_replace 写前备份，undo_file 回滚
_UNDO_DIR = app_identity.data_root() / "agent" / "undo"


def _undo_key(p: Path) -> str:
    import hashlib
    return hashlib.md5(str(p.resolve()).encode("utf-8", "replace")).hexdigest()[:12]


def _backup_for_undo(p: Path):
    """写操作前把原文件内容备份（同名文件序号递增），供 undo_file 回滚"""
    try:
        if p.is_file():
            _UNDO_DIR.mkdir(parents=True, exist_ok=True)
            key = _undo_key(p)
            seq = len(list(_UNDO_DIR.glob(f"{key}_*.bak")))
            (_UNDO_DIR / f"{key}_{seq}.bak").write_bytes(p.read_bytes())
    except OSError:
        pass


def _read_file(path: str, offset: int = None, limit: int = None,
               allow_dangerous: bool = False, number_lines: bool = False) -> dict:
    """读取文本文件：支持 offset（起始行，1-based）/limit（行数）分段读大文件；
    number_lines=True 时每行前附行号（L{n}: ），便于精确定位/引用代码行。
    allow_dangerous=True（如 YOLO 直行模式）时跳过内部路径沙盒检查。"""
    p = _resolve(path)
    level, reason = _sub("agent_sandbox").assess_path(str(p), "read")
    if not allow_dangerous and level != "safe":
        return _blocked(f"[沙盒拒绝] {reason}")
    try:
        with open(p, "rb") as f:
            text = _decode_robust(f.read())
        start = (offset - 1) if (offset and offset > 1) else 0
        if number_lines:
            lines = text.splitlines(keepends=True)
            seg = lines[start:start + limit] if (limit and limit > 0) else lines[start:]
            width = len(str(start + len(seg)))
            text = "".join(
                f"L{start + i + 1:<{width}}: {ln}" if not ln.endswith("\n")
                else f"L{start + i + 1:<{width}}: {ln[:-1]}\n"
                for i, ln in enumerate(seg))
        elif (offset and offset > 1) or (limit and limit > 0):
            lines = text.splitlines(keepends=True)
            text = "".join(lines[start:start + limit]) if (limit and limit > 0) \
                else "".join(lines[start:])
        return {"text": text, "images": []}
    except Exception as e:
        return _blocked(f"[沙盒] 读取失败: {e}")


def _write_file(path: str, content: str, append: bool = False,
                allow_dangerous: bool = False) -> dict:
    """创建/覆盖/追加写入文件（工作目录优先）；覆盖/追加前自动备份，可 undo_file 回滚。
    allow_dangerous=True（如 YOLO 直行模式）时跳过内部路径沙盒检查。"""
    p = _resolve(path)
    level, reason = _sub("agent_sandbox").assess_path(str(p), "write")
    if not allow_dangerous and level != "safe":
        return _blocked(f"[沙盒拒绝] {reason}")
    if len(content) > 500 * 1024:
        return _blocked("[沙盒] 内容过大（>500KB）")
    try:
        old_text = ""
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    old_text = f.read()
            except Exception:
                old_text = ""
        os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
        _backup_for_undo(p)
        mode = "a" if append else "w"
        with open(p, mode, encoding="utf-8") as f:
            f.write(content)
        _invalidate_config_cache_if_modified(p)   # 改到规则/设置文件立即刷新缓存
        verb = "追加" if append else "写入"
        # 通知 UI 展示增删差异（append 时 old=原文，new=原文+追加内容）
        _notify_file_changed(p, old_text, content if not append else old_text + content)
        return {"text": f"已{verb} {len(content)} 字符到 {p}", "images": []}
    except Exception as e:
        return _blocked(f"[沙盒] 写入失败: {e}")


def _invalidate_config_cache_if_modified(path) -> None:
    """改写 settings.json / agents.json 后失效设置/助手配置缓存，使 AI 在当前对话里
    立即重新读取新增/修改的自定义规则（load_settings 有缓存，不改失效会读到旧规则）。"""
    try:
        bn = str(path).replace("\\", "/").rsplit("/", 1)[-1].lower()
        if bn in ("settings.json", "agents.json"):
            from zhuzhu_Copilot.core import agent_skills
            agent_skills.invalidate_settings_cache()
    except Exception:
        pass


def _edit_file(path: str, old_text: str, new_text: str,
               allow_dangerous: bool = False) -> dict:
    """编辑文件：精确替换唯一匹配的 old_text（多处拒绝防误替换）；写前备份。
    allow_dangerous=True（如 YOLO 直行模式）时跳过内部路径沙盒检查。"""
    p = _resolve(path)
    level, reason = _sub("agent_sandbox").assess_path(str(p), "write")
    if not allow_dangerous and level != "safe":
        return _blocked(f"[沙盒拒绝] {reason}")
    try:
        # 严格 UTF-8 读取：非 UTF-8 文件（GBK 等）若用 errors=replace 转码再回写会把
        # 替换字符永久写回、破坏原文件。此处检测到经严格解码有损即拒绝，改用手动工具。
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = f.read()
        except UnicodeDecodeError:
            return _blocked("[沙盒] 文件不是有效 UTF-8 编码（可能为 GBK 等），"
                            "为免损坏跳过编辑，请用 read_file/手动工具处理")
        n = data.count(old_text)
        if n == 0:
            return _blocked("[沙盒] 未找到要替换的内容。常见原因与对策："
                            "1) old_text 须与文件内容精确一致（含空格/换行/缩进，多行内容请用 search_replace）；"
                            "2) 可先用 read_file 读取文件确认实际内容后再编辑；"
                            "3) 若需重写整个文件请改用 write_file。")
        if n > 1:
            return _blocked(f"[沙盒] 目标内容出现 {n} 处，为避免误替换请提供包含上下文的"
                            "更长匹配串，或用 search_replace(count=all) 替换全部")
        _backup_for_undo(p)
        old_data = data
        data = data.replace(old_text, new_text, 1)
        with open(p, "w", encoding="utf-8") as f:
            f.write(data)
        _invalidate_config_cache_if_modified(p)
        _notify_file_changed(p, old_data, data)
        return {"text": f"已替换 1 处内容到 {p}", "images": []}
    except Exception as e:
        return _blocked(f"[沙盒] 编辑失败: {e}")


def _insert_lines(path: str, line: int, content: str,
                  allow_dangerous: bool = False) -> dict:
    """按行号插入文本：在第 line 行（1-based）之前插入 content（可含多行），
    其余行整体下移；line 越界/缺省/≤0 时追加到文件末尾。写前备份，可 undo_file 回滚。
    allow_dangerous=True（如 YOLO 直行模式）时跳过内部路径沙盒检查。"""
    p = _resolve(path)
    level, reason = _sub("agent_sandbox").assess_path(str(p), "write")
    if not allow_dangerous and level != "safe":
        return _blocked(f"[沙盒拒绝] {reason}")
    if len(content) > 500 * 1024:
        return _blocked("[沙盒] 内容过大（>500KB）")
    try:
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = f.read()
        except UnicodeDecodeError:
            return _blocked("[沙盒] 文件不是有效 UTF-8 编码（可能为 GBK 等），"
                            "为免损坏跳过编辑，请用 read_file/手动工具处理")
        lines = data.splitlines(keepends=True)
        n = int(line or 0)
        if n < 1 or n > len(lines) + 1:
            n = len(lines) + 1   # 越界/缺省 → 末尾追加
        insert = content
        if not insert.endswith("\n"):
            insert += "\n"   # 补尾换行，避免插入块与后文粘连
        old_data = data
        lines.insert(n - 1, insert)
        _backup_for_undo(p)
        new_data = "".join(lines)
        with open(p, "w", encoding="utf-8") as f:
            f.write(new_data)
        _invalidate_config_cache_if_modified(p)
        _notify_file_changed(p, old_data, new_data)
        nrows = content.count("\n") + (0 if content.endswith("\n") else 1)
        return {"text": f"已在 {p} 第 {n} 行前插入 {nrows} 行内容", "images": []}
    except Exception as e:
        return _blocked(f"[沙盒] 插入失败: {e}")


def _search_replace(path: str, old_text: str, new_text: str, count: str = "once",
                    allow_dangerous: bool = False) -> dict:
    """多行精确替换：默认要求唯一匹配；count=all 替换全部出现。写前备份。
    allow_dangerous=True（如 YOLO 直行模式）时跳过内部路径沙盒检查。"""
    p = _resolve(path)
    level, reason = _sub("agent_sandbox").assess_path(str(p), "write")
    if not allow_dangerous and level != "safe":
        return _blocked(f"[沙盒拒绝] {reason}")
    try:
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = f.read()
        except UnicodeDecodeError:
            return _blocked("[沙盒] 文件不是有效 UTF-8 编码（可能为 GBK 等），"
                            "为免损坏跳过替换，请用 read_file/手动工具处理")
        n = data.count(old_text)
        if n == 0:
            return _blocked("[沙盒] 未找到要替换的内容。常见原因与对策："
                            "1) old_text 须与文件内容精确一致（含空格/换行/缩进）；"
                            "2) 可先用 read_file 读取文件确认实际内容后再替换；"
                            "3) 若需重写整个文件请改用 write_file。")
        if count == "all":
            _backup_for_undo(p)
            old_data = data
            data = data.replace(old_text, new_text)
            with open(p, "w", encoding="utf-8") as f:
                f.write(data)
            _invalidate_config_cache_if_modified(p)
            _notify_file_changed(p, old_data, data)
            return {"text": f"已替换 {n} 处内容到 {p}", "images": []}
        if n > 1:
            return _blocked(f"[沙盒] 目标内容出现 {n} 处，为避免误替换请包含更多上下文，"
                            "或明确 count=all 替换全部")
        _backup_for_undo(p)
        old_data = data
        data = data.replace(old_text, new_text, 1)
        with open(p, "w", encoding="utf-8") as f:
            f.write(data)
        _invalidate_config_cache_if_modified(p)
        _notify_file_changed(p, old_data, data)
        return {"text": f"已替换 1 处内容到 {p}", "images": []}
    except Exception as e:
        return _blocked(f"[沙盒] 替换失败: {e}")


def _undo_file(path: str, allow_dangerous: bool = False) -> dict:
    """回滚文件到最近一次写操作（write_file/edit_file/search_replace）之前的状态"""
    p = _resolve(path)
    level, reason = _sub("agent_sandbox").assess_path(str(p), "write")
    if not allow_dangerous and level != "safe":
        return _blocked(f"[沙盒拒绝] {reason}")
    key = _undo_key(p)
    try:
        baks = sorted(_UNDO_DIR.glob(f"{key}_*.bak"))
        if not baks:
            return _blocked(f"[undo_file] 没有可回滚的备份：{p}")
        bak = baks[-1]
        old_bytes = bak.read_bytes()
        _backup_for_undo(p)   # 当前版本也入备份，可再次回滚
        p.write_bytes(old_bytes)
        preview = _decode_robust(old_bytes)[:200]
        return {"text": f"已回滚 {p} 到最近一次写操作前。当前内容开头：\n{preview}", "images": []}
    except Exception as e:
        return _blocked(f"[undo_file] 回滚失败: {e}")


def _delete_file(path: str, allow_dangerous: bool = False) -> dict:
    """删除文件或空目录（工作目录优先；默认删除系统关键目录内容被沙盒拒绝，
    allow_dangerous=True（如 YOLO 直行模式）时允许删除任意路径含系统关键目录）"""
    p = _resolve(path)
    level, reason = _sub("agent_sandbox").assess_path(str(p), "delete")
    if not allow_dangerous and level != "safe":
        return _blocked(f"[沙盒拒绝] {reason}")
    try:
        if not p.exists():
            return _blocked(f"[沙盒] 路径不存在: {p}")
        if p.is_dir():
            if any(p.iterdir()):
                # 默认禁止递归删除；YOLO 直行（allow_dangerous）时允许递归删除任意目录
                # （含系统关键目录），交由模型自担后果
                if not allow_dangerous:
                    return _blocked(f"[沙盒] 目录非空，禁止递归删除: {p}（可用 run_command 精确处理）")
                import shutil as _sh
                _sh.rmtree(p)
            else:
                p.rmdir()
        else:
            p.unlink()
        _invalidate_config_cache_if_modified(p)
        return {"text": f"已删除: {p}", "images": []}
    except Exception as e:
        return _blocked(f"[沙盒] 删除失败: {e}")


def _list_directory(path: str, allow_dangerous: bool = False) -> dict:
    """列出目录内容（工作目录优先，最多 200 项）"""
    p = _resolve(path)
    level, reason = _sub("agent_sandbox").assess_path(str(p), "read")
    if not allow_dangerous and level != "safe":
        return _blocked(f"[沙盒拒绝] {reason}")
    try:
        entries = sorted(os.listdir(p))
        lines = []
        for e in entries[:200]:
            full = os.path.join(p, e)
            mark = "DIR " if os.path.isdir(full) else "FILE"
            lines.append(f"{mark}\t{e}")
        text = f"{p} 共 {len(entries)} 项" + (f"（仅显示前 200）" if len(entries) > 200 else "") + "：\n"
        text += "\n".join(lines)
        return {"text": text, "images": []}
    except Exception as e:
        return _blocked(f"[沙盒] 读取失败: {e}")


_MEMORY_MAX_TOTAL = 50 * 1024   # 记忆文件总上限 50KB（超出后截断旧部分）
_MEMORY_MAX_ENTRY = 8000       # 单条记忆上限 8000 字符


def _save_memory(content: str) -> dict:
    """把关键信息追加写入本地记忆文件（markdown，自动带时间戳）"""
    content = (content or "").strip()
    if not content:
        return _blocked("[记忆] 内容为空，未保存")
    if len(content) > _MEMORY_MAX_ENTRY:
        return _blocked(f"[记忆] 单条内容过长（{len(content)} 字符 > {_MEMORY_MAX_ENTRY}）")
    try:
        MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        # 超限时截断：保留尾部内容
        if MEMORY_FILE.exists() and MEMORY_FILE.stat().st_size > _MEMORY_MAX_TOTAL:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                old = f.read()
            old = old[-(_MEMORY_MAX_TOTAL // 2):]   # 保留最近 ~25KB
            with open(MEMORY_FILE, "w", encoding="utf-8") as f:
                f.write("<!-- 记忆已达上限，旧内容已截断 -->\n" + old)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        with open(MEMORY_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n## {stamp}\n{content}\n")
        return {"text": f"已保存到记忆文件 memory.md（{len(content)} 字符）", "images": []}
    except Exception as e:
        return _blocked(f"[记忆] 保存失败: {e}")


def _load_memory() -> dict:
    """读取本地记忆文件完整内容"""
    try:
        if not MEMORY_FILE.exists():
            return {"text": "（记忆文件为空，暂无历史记忆。可在遇到值得记住的关键信息时调用 save_memory 保存。）",
                    "images": []}
        size = MEMORY_FILE.stat().st_size
        if size > _MEMORY_MAX_TOTAL:
            return _blocked(f"[记忆] 记忆文件过大（{size // 1024}KB > {_MEMORY_MAX_TOTAL // 1024}KB），请人工清理")
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            text = f.read().strip() or "（记忆为空）"
        return {"text": text, "images": []}
    except Exception as e:
        return _blocked(f"[记忆] 读取失败: {e}")


# ---------- 任务清单（TODO：**按会话隔离**，会话之间互不共享；独立于对话上下文持久化，压缩后进度不丢失） ----------
# 存储：每个会话独享一份清单文件 ~/.zhuzhu_Copilot/agent/todos/<会话 slug>.json
#   —— 与共同上下文空间（agent_context）同一隔离思路：会话 id 即作用域，只有该会话的
#   Agent（主 Agent 与它派发的子 Agent）读写得到自己这一份，其他会话既看不到也改不到。
#   作用域解析优先级：显式传入 > 当前会话（线程局部 → UI 当前会话）。
# 兼容：无会话作用域时（无 UI 进程 / 单元测试）沿用旧的全局文件 todos.json。
TODO_DIR = app_identity.data_root() / "agent" / "todos"
TODO_FILE = app_identity.data_root() / "agent" / "todos.json"
_TODO_STATUS = ("pending", "in_progress", "completed")
_TODO_MAX_CACHE = 128            # 缓存的文件数上限（超出按插入顺序淘汰，防长跑进程内存累积）
_TODO_LOCK = threading.RLock()   # 并发会话/引擎线程同时读写时保护缓存与落盘
_TODO_CACHE: dict = {}           # 文件路径 -> {"fp": 指纹, "list": 解析结果}（引擎每轮读取，读盘热点）


def _current_conversation() -> str:
    """当前会话 id（线程局部 > UI 当前会话 > 空），惰性导入避免 core 模块循环依赖"""
    try:
        from zhuzhu_Copilot.core import agent_context
        return agent_context.current_conversation()
    except Exception:
        return ""


def todo_scope(conv_id=None) -> str:
    """解析任务清单作用域（会话 id）：显式传入 > 当前会话 > 空。
    空 = 无会话作用域（无 UI 进程/单元测试），此时使用兼容的全局清单文件。"""
    if conv_id is None:
        conv_id = _current_conversation()
    return str(conv_id or "").strip()


def _todo_slug(conv_id: str) -> str:
    """会话 id → 文件名安全片段（非法字符归一化；被替换过则附哈希，防不同会话撞同一文件）"""
    slug = re.sub(r"[^0-9A-Za-z_.-]", "_", conv_id)[:80]
    if slug != conv_id:
        slug += "-" + hashlib.md5(conv_id.encode("utf-8")).hexdigest()[:8]
    return slug or "default"


def todo_file(conv_id=None) -> Path:
    """该会话独享的任务清单文件路径；无会话作用域时返回兼容的全局文件。"""
    scope = todo_scope(conv_id)
    if not scope:
        return TODO_FILE
    return TODO_DIR / f"{_todo_slug(scope)}.json"


def _read_todo_list(path: Path) -> list:
    """解析清单文件（容错：文件缺失/非法 JSON/结构不符一律返回空列表）"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [t for t in data if isinstance(t, dict) and str(t.get("title") or "").strip()]


def load_todos(conv_id=None) -> list:
    """读取**该会话独享**的任务清单：返回 [{title, status}]，文件缺失/损坏返回空。

    性能：引擎每轮都要读它生成清单消息（长任务热点，每次读盘 ~1.5ms），故按
    (mtime, size) 缓存；任何写入方（update_todo/clear_todos/UI 清空）落盘后
    指纹自然变化，无需额外通知即可读到最新内容。缓存按文件分键，
    多会话并发时各读自己那一份、互不覆盖。"""
    path = todo_file(conv_id)
    key = str(path)
    with _TODO_LOCK:
        try:
            st = path.stat()
            fp = (st.st_mtime_ns, st.st_size)
        except OSError:
            _TODO_CACHE.pop(key, None)
            return []
        cached = _TODO_CACHE.get(key)
        if cached and cached["fp"] == fp:
            return list(cached["list"])
        out = _read_todo_list(path)
        _TODO_CACHE[key] = {"fp": fp, "list": out}
        for stale in list(_TODO_CACHE)[:-_TODO_MAX_CACHE]:   # 只保留最近 _TODO_MAX_CACHE 个文件
            _TODO_CACHE.pop(stale, None)
        return list(out)


def save_todos(todos: list, conv_id=None) -> None:
    """把任务清单全量写入**该会话独享**的文件；写入失败抛 OSError（由调用方决定如何提示）"""
    path = todo_file(conv_id)
    with _TODO_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(list(todos or []), ensure_ascii=False, indent=2),
                        encoding="utf-8")


def clear_todos(conv_id=None) -> bool:
    """清空**该会话**的任务清单（不影响其他会话）；返回是否成功（失败不抛，便于 UI 忽略）"""
    try:
        save_todos([], conv_id)
        return True
    except OSError:
        return False


def _update_todo(todos: list) -> dict:
    """创建/更新当前会话的任务清单：全量提交，status 限定 pending/in_progress/completed"""
    clean = []
    for t in todos:
        if not isinstance(t, dict):
            continue
        title = str(t.get("title") or "").strip()
        if not title:
            continue
        st = str(t.get("status") or "pending").strip().lower()
        if st not in _TODO_STATUS:
            st = "pending"
        clean.append({"title": title, "status": st})
    try:
        save_todos(clean)
    except OSError as e:
        return _blocked(f"[update_todo] 保存失败: {e}")
    active = [t for t in clean if t["status"] != "completed"]
    if clean and not active:
        # 全部完成：自动清空本会话任务清单（面板回到提示语状态）
        clear_todos()
        return {"text": "任务清单已全部完成，已自动清空。", "images": []}
    text = f"任务清单已更新：共 {len(clean)} 项，未完成 {len(active)} 项。"
    if clean:
        text += "\n" + "\n".join(f"- [{t['status']}] {t['title']}" for t in clean)
    return {"text": text, "images": []}


def _list_todo() -> dict:
    """查看当前会话的任务清单（会话独享，不含其他会话的清单）"""
    todos = load_todos()
    if not todos:
        return {"text": "（当前没有任务清单。开始多步任务时可用 update_todo 创建并跟踪进度。）",
                "images": []}
    active = [t for t in todos if t["status"] != "completed"]
    text = f"任务清单（共 {len(todos)} 项，未完成 {len(active)} 项）：\n"
    text += "\n".join(f"- [{t['status']}] {t['title']}" for t in todos)
    return {"text": text, "images": []}


# ---------- 系统信息（原 MCP server 工具内置化，真实 API） ----------

def _os_name() -> str:
    """注册表读取真实系统产品名（Win10/Win11 内核同为 10.0，按 build>=22000 修正为 Win11）"""
    try:
        import platform
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as k:
            name = winreg.QueryValueEx(k, "ProductName")[0]
            display = winreg.QueryValueEx(k, "DisplayVersion")[0]
            build = int(winreg.QueryValueEx(k, "CurrentBuildNumber")[0])
        if build >= 22000 and name.startswith("Windows 10"):
            name = name.replace("Windows 10", "Windows 11")
        return f"{name}（{display}，内部版本 {build}）"
    except (OSError, ValueError):
        import platform
        return f"{platform.system()} {platform.release()}"


def _total_memory_mb() -> int:
    """读取物理内存总量（MB）"""
    try:
        import ctypes
        class _MS(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        ms = _MS()
        ms.dwLength = ctypes.sizeof(_MS)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):
            return ms.ullTotalPhys // (1024 * 1024)
    except Exception:
        pass
    return 0


def _system_info() -> dict:
    import platform
    import socket
    import sys
    text = (f"主机名: {socket.gethostname()}\n"
            f"系统: {_os_name()}\n"
            f"架构: {platform.machine()}\n"
            f"CPU 核心数: {os.cpu_count()}\n"
            f"物理内存: {_total_memory_mb()} MB\n"
            f"Python: {sys.version.split()[0]}")
    return {"text": text, "images": []}


def _get_time() -> dict:
    return {"text": time.strftime("%Y-%m-%d %H:%M:%S"), "images": []}


def _env_var(name: str) -> dict:
    name = (name or "").strip()
    if not name:
        return _blocked("[env_var] 缺少环境变量名（name）")
    v = os.environ.get(name)
    return {"text": f"{name} = {v}" if v is not None else f"环境变量 {name} 不存在",
            "images": []}


# ---------- zhuzhu_Copilot 能力内置化 ----------

def _fmt_size(n: int) -> str:
    n = int(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _find_app(name: str):
    """按名称模糊匹配已安装应用；返回 AppInfo 或 None"""
    from zhuzhu_Copilot.core.app_scanner import AppScanner
    key = (name or "").strip().lower()
    apps = AppScanner().scan_all()
    for a in apps:
        if key in a.name.lower():
            return a
    return None


def _optimize_memory() -> dict:
    from zhuzhu_Copilot.core.memory_optimizer import optimize_memory
    result = optimize_memory()
    details = result.get("details") or []
    text = f"内存优化完成：{result.get('message', '')}"
    if details:
        text += "\n" + "\n".join(f"  - {d}" for d in details[:20])
    return {"text": text, "images": []}


def _uninstall_app(name: str) -> dict:
    from zhuzhu_Copilot.core.uninstaller import Uninstaller
    if not (name or "").strip():
        return _blocked("[uninstall_app] 缺少应用名称（name）")
    app = _find_app(name)
    if not app:
        return _blocked(f"未找到应用「{name}」。可先用 find_app 或 /screenshot 查看已装应用")
    plan = Uninstaller().build_plan(app)
    result = Uninstaller().uninstall(plan)
    return {"text": f"应用「{app.name}」卸载：{result.get('message', '')}", "images": []}


def _migrate_app(name: str, target: str) -> dict:
    from zhuzhu_Copilot.core.orchestrator import MigrationOrchestrator
    if not (name or "").strip() or not (target or "").strip():
        return _blocked("[migrate_app] 需要 name 与 target 参数")
    app = _find_app(name)
    if not app:
        return _blocked(f"未找到应用「{name}」。可先用 find_app 查询")
    result = MigrationOrchestrator().migrate(app, Path(target))
    state = "成功" if result.get("success") else "失败"
    return {"text": f"应用「{app.name}」迁移{state}：{result.get('message', '')}", "images": []}


# 当前活跃下载任务（供 UI 轮询快照渲染进度条）。
# 用锁保护读写：多个任务可能并发下发（后台多任务），set/clear/cancel 需原子，
# 且 clear 只清理自己关心的任务，避免误清并发新下发的下载导致 UI 进度失真。
_active_download = None
_active_dl_lock = threading.Lock()


def get_active_download():
    with _active_dl_lock:
        return _active_download


def set_active_download(task):
    global _active_download
    with _active_dl_lock:
        _active_download = task


def clear_active_download(task=None):
    """清除活跃下载。task 为空则无条件清除；否则仅当当前活跃者确实是该 task 才清除，
    防止并发下先后两次下载相互误清（保留新任务进度）。"""
    global _active_download
    with _active_dl_lock:
        if task is None or _active_download is task:
            _active_download = None


def cancel_active_download():
    """停止按钮触发时取消后台下载任务（原子取出并清空）"""
    global _active_download
    with _active_dl_lock:
        t, _active_download = _active_download, None
    if t is not None:
        try:
            t.cancel()
        except Exception:
            pass


def _ssrf_blocked(parsed) -> bool:
    """SSRF 防护：host 解析到回环/链路本地/保留/内网地址，或端口为常见内部服务端口则拦截。
    解析失败（无 host/DNS 异常）按拦截处理。"""
    host = (parsed.hostname or "").strip()
    if not host:
        return True
    try:
        port = parsed.port
    except ValueError:
        port = None
    # 常见内部管理端口（避免访问本地服务/元数据）
    if port in (8080, 8000, 8888, 3000, 5000, 5001, 8443, 6379, 22, 3306, 5432, 23, 5985, 5986):
        return True
    # 本机/内网 host（含 127.x、10.x、192.168.x、169.254.x、域名 localhost）
    low_host = host.lower()
    if low_host in ("localhost", "localhost.localdomain") or low_host.endswith(".local"):
        return True
    try:
        infos = socket.getaddrinfo(host, port or (80 if parsed.scheme != "https" else 443),
                                   proto=socket.IPPROTO_TCP)
    except Exception:
        return True   # DNS 解析失败按拦截处理
    for af, _st, _pr, _cn, sockaddr in infos:
        try:
            ip = ipaddress.ip_address(str(sockaddr[0]))
        except ValueError:
            continue
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified
                or ip.is_global is False):
            return True
    return False


def _http_request(url: str, timeout: int = 15, max_bytes: int = 512 * 1024,
                  method: str = "GET", headers: dict = None, body: str = None) -> str:
    """真实 HTTP 请求：urllib 标准库，支持 GET/POST/PUT/DELETE/PATCH + 请求头/请求体。
    自动探测响应编码（响应头 charset → HTML meta → utf-8），保证中文页（含 GBK）解码准确。"""
    import gzip
    import re as _re
    import urllib.request
    from urllib.parse import urlparse
    method = (method or "GET").upper()
    # SSRF/内网防护：仅允许 http/https，并拦截回环/链路本地/云元数据/内网保留网段与常见内部端口。
    # 缺省拒绝，避免 AI 以用户身份无约束访问本地/内网/云元数据接口。
    _scheme = (urlparse(url or "").scheme or "").lower()
    if _scheme not in ("http", "https"):
        raise urllib.error.URLError(f"仅允许 http/https 目标，已拒绝: {url}")
    if _ssrf_blocked(urlparse(url or "")):
        raise urllib.error.URLError(f"目标为内网/回环/保留地址，已拒绝: {url}")
    hdrs = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/126.0 Safari/537.36"),
        "Accept": "text/html,application/json,text/plain,*/*",
        "Accept-Encoding": "identity",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    if headers:
        hdrs.update({str(k): str(v) for k, v in headers.items()
                     if k and v is not None})
    data = None
    if body:
        data = body.encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    # 网络抖动 / 5xx / 429 自动指数退避重试（最多 2 次），提升联网任务成功率；
    # 4xx（403/404 等）与业务性错误不重试，避免无意义重复请求
    import time as _time
    raw, charset = None, None
    for _attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read(max_bytes + 1)
                # 编码探测 1：响应头 Content-Type charset
                charset = None
                try:
                    m = _re.search(r"charset=([\w-]+)",
                                   resp.headers.get("Content-Type", ""), _re.I)
                    if m:
                        charset = m.group(1)
                except Exception:
                    pass
            break
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
            code = getattr(e, "code", None)
            retryable = code in (429, 500, 502, 503, 504) or code is None
            if _attempt >= 2 or not retryable:
                raise
            _time.sleep(0.8 * (2 ** _attempt))
    if raw is None:
        raise urllib.error.URLError("请求失败")
    if len(raw) > max_bytes:
        raw = raw[:max_bytes]
    # 少数服务器无视 Accept-Encoding 仍返回 gzip：按魔数判断解压
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    # 编码探测 2：HTML <meta charset>（GBK 等老站点常见，硬编码 utf-8 会乱码）
    if not charset:
        m = _re.search(rb'<meta[^>]+charset=["\']?([\w-]+)', raw[:2048], _re.I)
        if m:
            charset = m.group(1).decode("ascii", "ignore")
    try:
        return raw.decode(charset or "utf-8", errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _html_to_text(html: str, max_chars: int = 8000) -> str:
    """HTML → 可读文本：提取标题 + 剥离 script/style/nav 等噪音 + 去标签压缩空白，
    让截断窗口内尽量是有效正文信息。"""
    import re as _re
    m = _re.search(r"<title[^>]*>(.*?)</title>", html, _re.S | _re.I)
    title = _re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else ""
    body = _re.sub(r"(?is)<(script|style|nav|footer|header|aside|iframe|svg|noscript)[^>]*>.*?</\1>",
                   " ", html)
    body = _re.sub(r"(?s)<!--.*?-->", " ", body)
    body = _re.sub(r"(?i)</(p|div|h[1-6]|li|tr|br|section|article)>", "\n", body)
    body = _re.sub(r"<[^>]+>", "", body)
    body = _html.unescape(body)
    body = _re.sub(r"[ \t\r\f\v]+", " ", body)
    body = _re.sub(r"\n\s*\n+", "\n", body).strip()
    out = title if title else ""
    if body:
        out = (out + "\n" if out else "") + body
    return out[:max_chars]


def _filter_by_keyword(text: str, keyword: str, max_chars: int) -> tuple:
    """在抓取文本中按关键词精确检索：保留包含关键词的段落/行，返回 (过滤后文本, 命中段数)。
    无关键词时原样返回（命中数 -1 表示未启用过滤）。"""
    kw = (keyword or "").strip().lower()
    if not kw:
        return text, -1
    segs = [s.strip() for s in text.splitlines() if s.strip()]
    if not segs:
        segs = [s.strip() for s in re.split(r"[。！？；\n]", text) if s.strip()]
    hits = [s for s in segs if kw in s.lower()]
    if not hits:
        return (f"未在页面内容中找到包含关键词「{keyword}」的段落"
                f"（页面共 {len(segs)} 段，可放宽关键词或改搜其他 URL）。\n"
                f"页面开头预览：\n{text[:600]}"), 0
    buf, total = [], 0
    for s in hits:
        if total + len(s) > max_chars:
            break
        buf.append(s)
        total += len(s)
    return "\n".join(buf), len(hits)


def _web_fetch_render(url: str, max_chars: int = 8000, wait_secs: float = 3.0) -> str:
    """JS 渲染兜底：独立 headless 浏览器加载页面（SPA/动态渲染站 HTTP 直连抓不到正文时用）。

    与用户浏览器操控完全隔离（独立 BrowserController + 独立临时 profile + headless），
    不干扰正在进行的 browser_* 操作；渲染后提取 body 文本并等待网络空闲。
    返回正文文本；失败/超时返回空串（由调用方保持原错误信息）。
    """
    try:
        from zhuzhu_Copilot.core import agent_browser
        from pathlib import Path as _P
        import tempfile
        ctl = agent_browser.BrowserController()
        # 独立临时 profile：不与用户浏览器 profile 共用登录态/锁（避免并发互斥）
        tmp = tempfile.mkdtemp(prefix="wam_fetch_")
        ctl._profile_dir = tmp
        try:
            ok, msg = ctl.start(headless=True,
                                window_pos=(-32000, -32000))
            if not ok:
                return ""
            ctl.navigate(url)
            # 等首帧渲染 + 网络空闲：SPA 需 JS 执行时间
            deadline = time.time() + wait_secs
            while time.time() < deadline:
                time.sleep(0.5)
                try:
                    st = ctl._eval("document.readyState")
                    if str(st.get("text", "")).strip() == "complete":
                        break
                except Exception:
                    pass
            # 提取正文文本（innerText 含动态渲染内容；标题/正文分开取）
            res = ctl.html("")
            data = {}
            try:
                data = json.loads(res.get("text") or "{}")
            except Exception:
                pass
            text = str(data.get("text") or "").strip()
            html_txt = str(data.get("html") or "")
            if not text and html_txt:
                text = _html_to_text(html_txt, max_chars=max_chars)
            if text:
                return text[:max_chars]
            return ""
        finally:
            try:
                ctl.stop()
            except Exception:
                pass
            try:
                import shutil
                shutil.rmtree(tmp, ignore_errors=True)
            except Exception:
                pass
    except Exception:
        return ""


def _web_fetch(url: str, method: str = "GET", headers: dict = None,
               body: str = "", max_chars: int = 8000, keyword: str = "") -> dict:
    """联网请求 URL 文本内容（网页/JSON/raw/REST API）。
    HTML 页面自动提取标题+正文文本（去脚本/标签噪音），JSON/纯文本按原样返回。
    keyword 指定后只保留包含该关键词的段落（页面内精确检索）。
    HTTP 直连失败或正文过短（SPA 空壳）时，自动用独立 headless 浏览器渲染兜底，
    提取 JS 动态渲染后的正文 —— 提高现代站点抓取成功率。"""
    url = (url or "").strip()
    if not url:
        return _blocked("[web_fetch] 缺少 URL")
    if not url.lower().startswith(("http://", "https://")):
        return _blocked("[web_fetch] 仅支持 http/https 地址")
    # SSRF 预检：直连与浏览器渲染兜底共用同一防护，内网/回环/保留地址一律拒绝
    from urllib.parse import urlparse as _up
    if _ssrf_blocked(_up(url)):
        return _blocked(f"[web_fetch] 目标为内网/回环/保留地址，已拒绝: {url}")
    text = ""
    http_err = ""
    try:
        text = _http_request(url, method=method, headers=headers, body=body or None)
    except Exception as e:
        http_err = str(e)
    text = (text or "").strip()
    # 正文过短阈值：HTML 空壳（SPA 仅挂载点）/ 直连失败 → 走浏览器渲染兜底
    need_render = bool(http_err) or (len(text) < 400
                                     and text.lstrip().startswith(("<",)))
    if need_render and method.upper() == "GET" and not body:
        rendered = _web_fetch_render(url, max_chars=max_chars)
        if rendered:
            text = rendered
            http_err = ""
    if http_err:
        return _blocked(f"[web_fetch] 请求失败: {http_err}")
    text = text.strip()
    if not text:
        return _blocked("[web_fetch] 返回内容为空")
    # HTML 页面：提取正文文本，确保截断窗口内是有效信息
    if text.lstrip().startswith(("<",)):
        text = _html_to_text(text, max_chars=max_chars)
    else:
        text = _html.unescape(text)
    # 关键词精确检索：只保留包含关键词的段落（供"抓取后定位信息"场景）
    if (keyword or "").strip():
        text, hits = _filter_by_keyword(text, keyword, max_chars)
        if hits == 0:
            return {"text": f"[web_fetch] {method} {url}\n{text}", "images": []}
        text = f"（页面内按关键词「{keyword}」过滤，命中 {hits} 段）\n{text}"
    elif len(text) > max_chars:
        text = text[:max_chars] + f"\n…（内容过长，已截断至 {max_chars} 字符）"
    return {"text": f"[web_fetch] {method} {url}\n{text}", "images": []}


def _clipboard(action: str = "read", text: str = "") -> dict:
    """读写系统剪贴板（真实 Win32 API，Unicode 文本）"""
    import ctypes
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    # 64 位句柄必须显式声明类型，否则默认 c_int 会截断 HGLOBAL 导致访问无效内存
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    user32.OpenClipboard.restype = ctypes.c_int
    user32.EmptyClipboard.argtypes = []
    user32.GetClipboardData.argtypes = [ctypes.c_uint]
    user32.GetClipboardData.restype = ctypes.c_void_p
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p
    user32.CloseClipboard.argtypes = []
    kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]

    CF_UNICODETEXT = 13
    if not user32.OpenClipboard(None):
        return _blocked("[clipboard] 打开剪贴板失败（可能被其他程序占用）")
    try:
        if action == "write":
            data = (text or "").encode("utf-16-le") + b"\x00\x00"
            # 先分配再清空：避免分配失败后留下已被清空的剪贴板（数据丢失）。
            # SetClipboardData 后句柄由系统接管，成功时不得释故；失败时需 GlobalFree 防泄漏。
            h = kernel32.GlobalAlloc(0x0042, len(data))
            if not h:
                return _blocked("[clipboard] 内存分配失败")
            try:
                ptr = kernel32.GlobalLock(h)
                if not ptr:
                    return _blocked("[clipboard] 锁定内存失败")
                ctypes.memmove(ptr, data, len(data))
                kernel32.GlobalUnlock(h)
                user32.EmptyClipboard()
                if user32.SetClipboardData(CF_UNICODETEXT, h):
                    h = None   # 已移交系统，勿再释放
                    return {"text": f"已写入剪贴板（{len(text)} 字符）", "images": []}
                return _blocked("[clipboard] 写入剪贴板失败")
            finally:
                if h:
                    kernel32.GlobalFree(h)   # 未移交系统：释放句柄防泄漏
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return {"text": "（剪贴板为空或非文本内容）", "images": []}
        # 有界读取：避免 wstring_at 对超大剪贴板分配整段内存
        ptr = kernel32.GlobalLock(h)
        if not ptr:
            return _blocked("[clipboard] 锁定剪贴板失败")
        try:
            value = ctypes.wstring_at(ptr, 4001)   # 最多取 4000 字符 + 结尾
        finally:
            kernel32.GlobalUnlock(h)
        return {"text": f"[clipboard] 当前剪贴板内容：\n{value[:4000]}", "images": []}
    finally:
        user32.CloseClipboard()


# 提取文档时的资源边界（防 zip 炸弹 / 超长文件无界读入内存弹爆）：
_MAX_TXT_CHARS = 10000     # 纯文本最多读取字符数（超额截断，不整文件读入）
_MAX_ZIP_READ = 2 * 1024 * 1024  # 每个 zip 项最多解压读取字节，防解压炸弹
_MAX_CSV_ROWS = 300        # CSV 最多读取行数


def _read_zip_entry(z, name: str, cap: int = _MAX_ZIP_READ) -> bytes:
    """安全读取 zip 条目：流式读取到 cap 字节即截断，防 zip 炸弹无界解压内存膨胀。
    读满 cap 字节时静默截断（仅截尾，不影响前面文本解析）。"""
    with z.open(name) as ef:
        return ef.read(cap + 1)[:cap]


def _extract_text(path: str) -> dict:
    """提取文档纯文本：txt/md/log/json/csv 直接读，docx/xlsx 解析 zip+XML（标准库），
    pdf 提示先 pip install pypdf。所有读取均设上限，避免超大/畸形文件撑爆内存。"""
    import csv as _csv
    import html as _html
    import re as _re
    import zipfile
    p = _resolve(path)
    if not p.is_file():
        return _blocked(f"[extract_text] 文件不存在: {p}")
    # 原始文件大小硬上限（100MB），超出直接拒绝，避免碰都别碰超大文件
    try:
        if os.path.getsize(p) > 100 * 1024 * 1024:
            return _blocked("[extract_text] 文件过大（>100MB），拒绝读取")
    except OSError:
        return _blocked(f"[extract_text] 无法访问文件: {p}")
    suffix = p.suffix.lower()
    try:
        if suffix in (".txt", ".md", ".log", ".json"):
            with p.open(encoding="utf-8", errors="replace") as f:
                text = f.read(_MAX_TXT_CHARS)
            return {"text": text[:8000], "images": []}
        if suffix == ".csv":
            with p.open(encoding="utf-8-sig", errors="replace", newline="") as f:
                rows = []
                for i, r in enumerate(_csv.reader(f)):
                    if i >= _MAX_CSV_ROWS:
                        break
                    rows.append(r)
            return {"text": "\n".join(" | ".join(r) for r in rows)[:8000], "images": []}
        if suffix == ".docx":
            # docx = zip + word/document.xml，段落 <w:p> 内 <w:t> 为文本
            with zipfile.ZipFile(p) as z:
                xml = _read_zip_entry(z, "word/document.xml").decode("utf-8", errors="replace")
            paras = ["".join(_re.findall(r"<w:t[^>]*>(.*?)</w:t>", seg, _re.S))
                     for seg in xml.split("</w:p>")]
            return {"text": "\n".join(_html.unescape(x) for x in paras if x.strip())[:8000], "images": []}
        if suffix == ".xlsx":
            # xlsx = zip + xl/sharedStrings.xml（共享字符串）+ xl/worksheets/sheetN.xml（单元格）
            with zipfile.ZipFile(p) as z:
                names = z.namelist()
                shared = []
                if "xl/sharedStrings.xml" in names:
                    sx = _read_zip_entry(z, "xl/sharedStrings.xml").decode("utf-8", errors="replace")
                    shared = ["".join(_re.findall(r"<t[^>]*>(.*?)</t>", seg, _re.S))
                              for seg in sx.split("</si>")]
                out = []
                for sh in sorted(n for n in names
                                 if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")):
                    wx = _read_zip_entry(z, sh).decode("utf-8", errors="replace")
                    out.append(f"[{sh}]")
                    for row in wx.split("</row>"):
                        cells = []
                        for cm in _re.finditer(r'<c r="([A-Z]+\d+)"([^>]*)>(.*?)</c>', row, _re.S):
                            ref, attrs, inner = cm.group(1), cm.group(2), cm.group(3)
                            vm = _re.search(r"<v>(.*?)</v>", inner, _re.S)
                            val = vm.group(1) if vm else ""
                            if 't="s"' in attrs and val:
                                try:
                                    val = shared[int(val)]
                                except (ValueError, IndexError):
                                    pass
                            elif 't="inlineStr"' in attrs:
                                ism = _re.search(r"<t[^>]*>(.*?)</t>", inner, _re.S)
                                val = ism.group(1) if ism else ""
                            cells.append(f"{ref}:{_html.unescape(val)}")
                        if cells:
                            out.append(" ".join(cells))
                return {"text": "\n".join(out)[:8000], "images": []}
        if suffix == ".pptx":
            # pptx = zip + ppt/slides/slideN.xml，文本在 <a:t>，标题文字带 txBody
            with zipfile.ZipFile(p) as z:
                names = z.namelist()
                out = []
                for sn in sorted(n for n in names
                                 if n.startswith("ppt/slides/slide") and n.endswith(".xml")):
                    sx = _read_zip_entry(z, sn).decode("utf-8", errors="replace")
                    texts = _re.findall(r"<a:t>(.*?)</a:t>", sx, _re.S)
                    out.append(f"[{sn}]")
                    out.append(" | ".join(_html.unescape(t) for t in texts))
                return {"text": "\n".join(out)[:8000], "images": []}
        if suffix == ".pdf":
            return _blocked("[extract_text] PDF 文本提取需 pypdf：先 run_command 执行 "
                            "pip install pypdf 后重试（pip 已在命令白名单）")
        return _blocked(f"[extract_text] 不支持格式 {suffix}（支持 txt/csv/docx/pptx/xlsx，pdf 需装 pypdf）")
    except Exception as e:
        return _blocked(f"[extract_text] 解析失败: {e}")


def _office_verify(path: str, workdir: str = "") -> str:
    """create_* 生成后自动复核：读回文本，确认非空且无未解析字面量，返回追加提示。

    硬性"生成后必须复核"约束的工具层落地：结果异常时返回告警，供上层判断重生成。
    """
    try:
        from zhuzhu_Copilot.office.utils import resolve_path as _rp
        p = _rp(path, workdir)
        if not p.is_file() or os.path.getsize(p) <= 0:
            return "；自动复核失败：文件为空或不存在，请检查后重生成"
        r = _extract_text(str(p))
        txt = str(r.get("text") or "")
        if not txt or txt.startswith("[extract_text]"):
            return "；自动复核失败：无法读取内容，请检查生成结果"
        bad = []
        for w in ("{'", '\\n', "**", "## ", "…None"):
            if w in txt[:6000]:
                bad.append(w)
        return "；已自动复核内容正常" if not bad else f"；自动复核发现未解析标记({','.join(bad)})，请修正参数后重生成"
    except Exception:
        return ""


# 内置文生图：调用 agnes images/generations（真实 API，非 mock）
_GEN_IMAGE_URL = "https://api.agnes-ai.cn/v1/images/generations"
_GEN_IMAGE_MODEL = "agnes-image-2.1-flash"
_GEN_RATIOS = ("1:1", "3:4", "4:3", "16:9", "9:16")


def _generate_image(prompt: str, ratio: str = "1:1", dest_dir: str = "") -> dict:
    """AI 文生图：调用内置 agnes 图片生成 API，下载到本地并返回本地路径。

    返回文本含本地路径，供 create_docx/create_pptx/create_xlsx 的 image 参数直接引用。
    """
    import base64
    import urllib.error
    import urllib.request
    prompt = (prompt or "").strip()
    if not prompt:
        return _blocked("[generate_image] 缺少 prompt（要生成的画面描述）")
    ratio = (ratio or "1:1").strip().lower()
    if ratio not in _GEN_RATIOS:
        ratio = "1:1"
    from zhuzhu_Copilot.core import agent_llm
    payload = {"model": _GEN_IMAGE_MODEL, "prompt": prompt,
               "size": "1K", "ratio": ratio,
               "extra_body": {"response_format": "url"}}
    req = urllib.request.Request(
        _GEN_IMAGE_URL, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "User-Agent": "zhuzhu_Copilot/1.0 AgentClient",
                 "Authorization": f"Bearer {agent_llm.DEFAULT_API_KEY}"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return _blocked(f"[generate_image] 生成失败 HTTP {e.code}: "
                        f"{e.read().decode('utf-8', 'replace')[:300]}")
    except Exception as e:
        return _blocked(f"[generate_image] 生成失败: {e}")
    items = [d for d in (data.get("data") or []) if isinstance(d, dict)]
    if not items:
        return _blocked(f"[generate_image] API 未返回图片: {str(data)[:200]}")
    # 保存目录：显式指定 → 工作目录相对解析；否则 工作目录/images（无则桌面/images）
    if (dest_dir or "").strip():
        base = _resolve(dest_dir)
    elif WORKDIR:
        base = Path(WORKDIR) / "images"
    else:
        base = Path.home() / "Desktop" / "images"
    try:
        base.mkdir(parents=True, exist_ok=True)
    except Exception:
        base = Path.home() / "Desktop" / "images"
        base.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    saved = []
    for i, d in enumerate(items[:4], 1):
        url = d.get("url") or ""
        b64 = d.get("b64_json") or ""
        try:
            if b64:
                raw = base64.b64decode(b64)
            else:
                from urllib.parse import urlparse as _up
                if (_up(url).scheme or "").lower() != "https" or _ssrf_blocked(_up(url)):
                    raise ValueError(f"图片源非 https 或为内网/回环地址，已拒绝: {url}")
                with urllib.request.urlopen(url, timeout=60) as rr:
                    raw = rr.read()
            path = base / f"img_{stamp}_{i}.png"
            path.write_bytes(raw)
            saved.append(str(path))
        except Exception as e:
            saved.append(f"(下载失败 {url}: {e})")
    if not any(not s.startswith("(下载失败") for s in saved):
        return _blocked("[generate_image] 图片下载失败：" + "；".join(saved))
    return {"text": "已生成图片素材（本地路径，供 image 参数引用）：\n"
                    + "\n".join(saved)
                    + "\n把这些路径作为 create_docx/create_pptx/create_xlsx 的 image 参数传入。",
            "images": []}



# 只读 git 白名单：仅允许查询类子命令，改写/推送/回退等危险操作一律拒绝
_GIT_SAFE = ("status", "log", "diff", "branch", "remote", "show", "tag",
             "rev-parse", "describe", "ls-files", "ls-tree", "symbolic-ref")


def _git_info(command: str, cwd: str = "") -> dict:
    """执行只读 git 查询命令（status/log/diff/branch/remote 等），危险命令被拒绝"""
    import shlex
    import subprocess as _sp
    cmd = (command or "").strip()
    if not cmd:
        return _blocked("[git_info] 缺少 command（如 status --short / log --oneline -5）")
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return _blocked(f"[git_info] 命令格式错误: {cmd}")
    head = parts[0].lower() if parts else ""
    if not head or head not in _GIT_SAFE:
        return _blocked(f"[git_info] 仅允许只读查询命令（{'/'.join(_GIT_SAFE)}），已拒绝: {cmd}")
    base = str(_resolve(cwd)) if (cwd or "").strip() else (WORKDIR or os.getcwd())
    try:
        proc = _sp.run(["git", "-C", base] + parts, capture_output=True, timeout=20,
                       creationflags=_CREATE_NO_WINDOW)
    except _sp.TimeoutExpired:
        return _blocked("[git_info] 命令超时（20 秒）")
    except Exception as e:
        return _blocked(f"[git_info] 执行失败: {e}")
    out = _decode_robust(proc.stdout).strip()
    err = _decode_robust(proc.stderr).strip()
    text = out or err or "（无输出）"
    if proc.returncode != 0:
        text = f"退出码 {proc.returncode}：{err or out}"
    return {"text": text[:8000], "images": []}


# web_search 单次最多返回条数（可配置，避免一次调用撑爆上下文）
_WEB_SEARCH_MAX_RESULTS = 15
# 搜索结果缓存：同一关键词 5 分钟内复用，避免多轮任务重复请求拖慢任务
_SEARCH_CACHE_TTL = 300
_SEARCH_CACHE: dict = {}
# 垃圾/低质域名黑名单（搜索结果过滤）：链接农场、镜像聚合站、自动生成站等
# 收录标准：聚合转载/自动生成站，结果质量差且污染搜索。正式媒体主站不收录
# （避免误杀有价值来源）。
_SEARCH_JUNK_DOMAINS = frozenset({
    "ahradio.com.cn", "qingcaigc.com", "ipaddress.com", "webmasterhome.cn",
})


def _web_search(query: str, max_results: int = 8, must_include: str = "") -> dict:
    """联网搜索：Bing 优先，失败/无结果自动回退 DuckDuckGo 与 Google，
    多引擎结果聚合去重，解析结果列表。
    must_include 指定后仅保留标题或摘要中包含该词的结果（精确检索）。
    同一关键词 5 分钟内结果缓存复用，避免重复请求；已知垃圾/低质域名自动过滤。"""
    import re as _re
    import urllib.parse
    query = (query or "").strip()
    if not query:
        return _blocked("[web_search] 缺少搜索关键词 query")
    max_results = max(1, min(int(max_results or 8), _WEB_SEARCH_MAX_RESULTS))

    def _bing() -> list:
        url = f"https://cn.bing.com/search?q={urllib.parse.quote(query)}&mkt=zh-CN"
        html = _http_request(url)
        items = []
        for m in _re.finditer(r'<li class="b_algo"[^>]*>(.*?)</li>', html, _re.S):
            block = m.group(1)
            am = _re.search(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, _re.S)
            if not am:
                continue
            href = am.group(1).strip()
            title = _re.sub(r"<[^>]+>", "", am.group(2)).strip()
            if not title:
                continue
            pm = _re.search(r"<p[^>]*>(.*?)</p>", block, _re.S)
            snippet = _re.sub(r"<[^>]+>", "", pm.group(1)).strip() if pm else ""
            items.append((title, href, snippet))
            if len(items) >= max_results:
                break
        return items

    def _ddg() -> list:
        url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
        html = _http_request(url)
        items = []
        for m in _re.finditer(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, _re.S):
            href = _re.sub(r"&amp;", "&", m.group(1).strip())
            title = _re.sub(r"<[^>]+>", "", m.group(2)).strip()
            if not title:
                continue
            sn = _re.search(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', html[m.start():], _re.S)
            snippet = _re.sub(r"<[^>]+>", "", sn.group(1)).strip() if sn else ""
            items.append((title, href, snippet))
            if len(items) >= max_results:
                break
        return items

    def _google() -> list:
        # Google 桌面搜索：解析 g 结果块（title/url/snippet）。反爬严格，
        # 失败静默回退，不影响主流程。
        url = f"https://www.google.com/search?q={urllib.parse.quote(query)}&num={max_results}&hl=zh-CN"
        html = _http_request(url)
        items = []
        for m in _re.finditer(r'<a[^>]+href="(https?://[^"&]+)"[^>]*>(.*?)</a>', html, _re.S):
            href = m.group(1).strip()
            if "google.com" in href:
                continue
            title = _re.sub(r"<[^>]+>", "", m.group(2)).strip()
            if not title:
                continue
            # 摘要：紧跟链接的 span
            sn = _re.search(r'<span[^>]*>(.*?)</span>', html[m.end():m.end() + 600], _re.S)
            snippet = _re.sub(r"<[^>]+>", "", sn.group(1)).strip() if sn else ""
            items.append((title, href, snippet))
            if len(items) >= max_results:
                break
        return items

    def _clean(items: list) -> list:
        """跨引擎结果清洗：剔除空项 + 垃圾域名 + URL 去重"""
        out = []
        seen = set()
        for _t, _h, _s in items:
            _h = _h.strip()
            if not _h:
                continue
            try:
                host = urllib.parse.urlparse(_h).hostname or ""
            except Exception:
                host = ""
            host = host.lower()
            # 垃圾/低质域名黑名单：链接农场/镜像站/聚合爬虫站，结果质量差且常见
            if host in _SEARCH_JUNK_DOMAINS or any(
                    host.endswith("." + d) for d in _SEARCH_JUNK_DOMAINS):
                continue
            if _h in seen:
                continue
            seen.add(_h)
            out.append((_t, _h, _s))
        return out

    # 结果缓存：同一 query 5 分钟内复用（多轮任务重复搜索同一词时减少网络请求）
    cache_key = (query, max_results)
    now = time.time()
    cached = _SEARCH_CACHE.get(cache_key)
    if cached and now - cached[0] < _SEARCH_CACHE_TTL:
        items, source = cached[1], cached[2]
    else:
        items, source = [], "Bing"
        for fn, src in ((_bing, "Bing"), (_ddg, "DuckDuckGo"), (_google, "Google")):
            try:
                got = _clean(fn())
            except Exception:
                got = []
            if got:
                items, source = got, src
                break
        _SEARCH_CACHE[cache_key] = (now, items, source)
        # 防缓存无限膨胀：超过 64 条清除最旧一半
        if len(_SEARCH_CACHE) > 64:
            for _k in list(_SEARCH_CACHE)[: len(_SEARCH_CACHE) // 2]:
                _SEARCH_CACHE.pop(_k, None)
    if not items:
        return {"text": f"[web_search] 搜索失败或未解析到结果（关键词：{query}）。"
                        "可改用 web_fetch 直接抓取搜索页分析。", "images": []}
    _kw = (must_include or "").strip().lower()
    if _kw:
        items = [(t, h, s) for t, h, s in items if _kw in (t + " " + s).lower()]
    if not items:
        return {"text": f"[web_search] 关键词「{query}」未找到" +
                        (f"且标题/摘要包含「{must_include}」的" if _kw else "") +
                        "结果，可放宽条件重试。", "images": []}
    import html as _html_mod
    lines = [f"搜索结果（{len(items)} 条，来源 {source}"
             + (f"，已过滤仅含「{must_include}」" if _kw else "") + "）："]
    for i, (t, h, s) in enumerate(items, 1):
        lines.append(f"{i}. {_html_mod.unescape(t)}")
        lines.append(f"   {h}")
        if s:
            lines.append(f"   摘要：{_html_mod.unescape(s)[:200]}")
    return {"text": "\n".join(lines), "images": []}


def _fast_download(url: str, dest_dir: str) -> dict:
    from urllib.parse import urlparse
    from zhuzhu_Copilot.core.fast_download import DownloadTask
    url = (url or "").strip()
    if not url:
        return _blocked("[fast_download] 缺少下载地址（url）")
    # SSRF/协议校验：仅允许 http/https，拦截内网/回环/元数据/敏感端口
    if (urlparse(url).scheme or "").lower() not in ("http", "https") \
            or _ssrf_blocked(urlparse(url)):
        return _blocked(f"[fast_download] 目标协议或地址不合法（内网/回环/元数据已拦截）: {url}")
    dest = (dest_dir or "").strip() or WORKDIR or os.getcwd()
    try:
        os.makedirs(dest, exist_ok=True)
        task = DownloadTask(url, dest)
        set_active_download(task)   # 注册为活跃下载，UI 轮询快照渲染进度条
        try:
            task.start()
            task.join()                 # 等待完成/失败/取消，不做提前放弃
        finally:
            # 无论结果如何，该任务结束后即退出活跃注册（仅清理自身，避免误清并发新下载）
            clear_active_download(task)
        snap = task.snapshot()
        if snap.get("status") == "done":
            return {"text": f"下载完成：{snap.get('path')}（{_fmt_size(snap.get('total'))}）",
                    "images": []}
        err = snap.get("error") or "进行中（可稍后重试）"
        return {"text": f"下载未完成：状态 {snap.get('status')}，{err}", "images": []}
    except Exception as e:
        clear_active_download()
        return _blocked(f"[fast_download] 下载失败: {e}")


def _create_skill(name: str, description: str, instruction: str) -> dict:
    """生成市场标准 SKILL.md 技能并注册（创建后立即生效）。
    自动绑定到当前会话工作流（@工作流 切换后仅该工作流加载），
    实现"对话中描述即可一键生成并配置到当前工作流"。"""
    from zhuzhu_Copilot.core import agent_skills
    from zhuzhu_Copilot.core import agent_workflow
    wf = agent_workflow.active_workflow()
    # 仅绑定到非默认工作流：默认工作流为全局，无需绑定
    bind_wf = wf if wf and wf != agent_workflow.DEFAULT_WORKFLOW else ""
    ok, msg = agent_skills.create_md_skill(name, description, instruction, workflow=bind_wf)
    return ({"text": msg, "images": []} if ok else _blocked(msg))


def _create_plugin(description: str, kind: str) -> dict:
    """用自然语言描述创建插件（AI 生成可运行 MCP server + SKILL.md + 脚本/资源/示例），
    统一存入插件目录并登记技能/MCP 配置，创建后即时生效。"""
    from zhuzhu_Copilot.core import agent_plugins
    ok, msg = agent_plugins.create_plugin_from_nl(description, kind)
    return ({"text": msg, "images": []} if ok else _blocked(msg))


def _new_project(path: str, kind: str = "generic", name: str = "") -> dict:
    """创建项目脚手架：README.md / .gitignore / src/，kind 附带对应模板文件"""
    p = _resolve(path)
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return _blocked(f"[new_project] 创建目录失败: {e}")
    name = (name or "").strip() or p.name
    kind = (kind or "generic").strip().lower()
    if kind not in ("generic", "python", "node", "web"):
        kind = "generic"
    created = []
    try:
        readme = (f"# {name}\n\n项目说明：TODO\n\n## 结构\n\n- `src/` 源码\n")
        (p / "README.md").write_text(readme, encoding="utf-8")
        created.append("README.md")
        (p / ".gitignore").write_text("__pycache__/\n*.pyc\nnode_modules/\ndist/\nbuild/\n.env\n",
                                      encoding="utf-8")
        created.append(".gitignore")
        src = p / "src"
        src.mkdir(parents=True, exist_ok=True)
        if kind == "python":
            (src / "main.py").write_text(
                "def main():\n    print(\"Hello from %s\")\n\n\nif __name__ == \"__main__\":\n    main()\n" % name,
                encoding="utf-8")
            (p / "requirements.txt").write_text("", encoding="utf-8")
            created.extend(["src/main.py", "requirements.txt"])
        elif kind == "node":
            import json as _json
            (p / "package.json").write_text(_json.dumps(
                {"name": name, "version": "0.1.0", "main": "src/index.js",
                 "scripts": {"start": "node src/index.js"}}, ensure_ascii=False, indent=2),
                encoding="utf-8")
            (src / "index.js").write_text("console.log('Hello from %s');\n" % name, encoding="utf-8")
            created.extend(["package.json", "src/index.js"])
        elif kind == "web":
            (src / "index.html").write_text(
                "<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n<meta charset=\"UTF-8\">\n"
                f"<title>{name}</title>\n</head>\n<body>\n<h1>{name}</h1>\n</body>\n</html>\n",
                encoding="utf-8")
            created.append("src/index.html")
        return {"text": f"项目脚手架已创建：{p}\n" + "\n".join(f"- {c}" for c in created),
                "images": []}
    except Exception as e:   # 收口：任何生成异常均转 blocked，不外抛导致工具调用崩溃
        return _blocked(f"[new_project] 生成失败: {e}")


def _builtin_schemas() -> list:
    """内置工具 schema 的深拷贝（进程内只做一次）。

    原实现每次调用都 json.loads(json.dumps(TOOLS)) —— 长任务每轮都要组装工具列表，
    纯拷贝即成为固定开销；TOOLS 为模块常量，拷贝一次即可，返回浅拷贝列表由调用方
    自由增删（schema dict 只读，不做就地修改）。"""
    global _BUILTIN_SCHEMAS
    if _BUILTIN_SCHEMAS is None:
        _BUILTIN_SCHEMAS = json.loads(json.dumps(TOOLS))
    return list(_BUILTIN_SCHEMAS)


def tool_schemas(workflow=None) -> list:
    """供 LLM tools 参数的完整 schema 列表（内置 + 指定/当前工作流自定义，同名覆盖）。
    workflow：目标工作流；缺省取引擎任务线程当前工作流，无工作流上下文则回退最近注册。"""
    builtin = _builtin_schemas()
    custom_tools, _ = _custom_entry(workflow)
    if not custom_tools:
        out = builtin
    else:
        custom_names = {t.get("function", {}).get("name") for t in custom_tools}
        out = [t for t in builtin
               if t.get("function", {}).get("name") not in custom_names]
        out.extend(json.loads(json.dumps(custom_tools)))
    # 工作流自定义子 Agent：动态并入 sub_<name> 工具 schema，同名跳过（内置优先）
    try:
        sub_schemas = _sub("agent_subagent").subagent_schemas(
            workflow if workflow is not None else _wf_current())
        seen = {t.get("function", {}).get("name") for t in out}
        for t in sub_schemas:
            nm = t.get("function", {}).get("name")
            if nm and nm not in seen:
                out.append(t)
                seen.add(nm)
    except Exception:
        pass
    return out


# ------------------------------------------------------------
# Cordis 工作流管理工具实现（agent_workflow 惰性加载）
# ------------------------------------------------------------
def _wf():
    return _sub("agent_workflow")


def _list_workflows() -> dict:
    try:
        wfs = _wf().list_workflows()
        if not wfs:
            return {"text": "暂无工作流。", "images": []}
        lines = []
        for w in wfs:
            if w["is_default"]:
                tag = "[默认只读]"
            elif w["active"]:
                tag = "[激活]"
            elif not w.get("enabled", True):
                tag = "[禁用]"
            else:
                tag = ""
            lines.append(f"- {w['name']} {tag}  {w['description']}")
            if w["core_files"]:
                lines.append(f"    核心文件: {', '.join(w['core_files'])}")
        return {"text": "\n".join(lines), "images": []}
    except Exception as e:
        return _blocked(f"[list_workflows] {e}")


def _create_workflow(name: str, description: str, files, preset: str = "") -> dict:
    try:
        if preset:
            ok, msg = _wf().create_builtin_workflow(preset, name=name, description=description)
        else:
            ok, msg = _wf().create_workflow(name, description, files)
        return {"text": msg, "images": []} if ok else _blocked(msg)
    except Exception as e:
        return _blocked(f"[create_workflow] {e}")


def _list_builtin_workflows() -> dict:
    """list_builtin_workflows：列出应用内置工作流预设（一键创建）"""
    try:
        presets = _wf().list_builtin_workflows()
        if not presets:
            return {"text": "当前没有可用的内置工作流预设。", "images": []}
        lines = ["# 内置工作流预设（随包提供，可一键创建）"]
        for p in presets:
            state = "已创建" if p.get("created") else "未创建"
            lines.append(f"- {p.get('display_name') or p.get('id')}（id={p.get('id')}，{state}）"
                         f"  工作流名: {p.get('workflow_name')}")
            if p.get("description"):
                lines.append(f"    用途: {p['description']}")
            agents = p.get("agents") or []
            if agents:
                lines.append(f"    注册式子 Agent（{len(agents)} 个）: {', '.join(agents)}")
        lines.append("")
        lines.append("创建：create_workflow(preset=\"<id>\")（name 可留空用预设默认名，"
                     "创建后可 switch_workflow 激活）。")
        return {"text": "\n".join(lines), "images": []}
    except Exception as e:
        return _blocked(f"[list_builtin_workflows] {e}")


def _switch_workflow(name: str) -> dict:
    try:
        ok, msg = _wf().set_active(name)
        if not ok:
            return _blocked(msg)
        # 热插拔：重新注册工作流工具 + 失效技能缓存，并标记引擎重建
        _wf().apply_tools()
        try:
            _sub("agent_skills").invalidate_skills_cache()
        except Exception:
            pass
        return {"text": msg + "（引擎已重建，立即生效）", "images": [],
                "__rebuild_engine__": True}
    except Exception as e:
        return _blocked(f"[switch_workflow] {e}")


def _list_workflow_agents() -> dict:
    """列出可被主 Agent 在当前对话内切换调用的其他工作流 Agent 能力"""
    try:
        agents = _wf().workflow_agents()
        if not agents:
            return {"text": "当前没有其他可用工作流 Agent（请先创建/启用工作流）。", "images": []}
        lines = []
        for a in agents:
            head = f"- {a['name']}（{a['agent_name']}）"
            if a.get("description"):
                head += f": {a['description']}"
            lines.append(head)
            parts = []
            if a.get("skills"):
                parts.append("技能: " + ", ".join(a["skills"]))
            if a.get("tools"):
                parts.append("自定义工具")
            if a.get("mcp"):
                parts.append("MCP: " + ", ".join(a["mcp"]))
            if a.get("plugins"):
                parts.append("插件: " + ", ".join(a["plugins"]))
            if parts:
                lines.append("    " + "；".join(parts))
        lines.append("")
        lines.append("要切换调用某个 agent，请调用 use_workflow_agent(name=\"<工作流名>\")；"
                     "完成后调用 use_workflow_agent(name=\"_default\") 切回默认主 Agent。")
        return {"text": "\n".join(lines), "images": []}
    except Exception as e:
        return _blocked(f"[list_workflow_agents] {e}")


def _use_workflow_agent(name: str) -> dict:
    """默认主 Agent 在当前对话内临时切换调用其他工作流的 Agent 能力（人格/技能/工具）。
    返回 __use_workflow__ 标记，由引擎把当前对话的工作流改为目标并重建引擎热插拔；
    name 为空或 _default 则切回默认主 Agent。"""
    name = str(name or "").strip()
    wf = _wf()
    if not name or name == wf.DEFAULT_WORKFLOW:
        return {"text": "已切回默认主 Agent（_default）：后续对话使用内置主 Agent 的人格与能力。",
                "images": [], "__use_workflow__": ""}
    # 关键校验：name 会用于拼文件/目录路径（workflow_dir/_read_meta/_agent_name/apply_tools），
    # 必须走 _safe_name 白名单拒绝路径穿越，否则 name="..\\..\\某目录" 会把目录解析到
    # workflows_root 之外并加载执行其 tools.py/agent.py（进程内代码）。
    if not wf._safe_name(name):
        return _blocked("[use_workflow_agent] 工作流名非法（仅字母/数字/下划线/中划线）")
    if not wf.workflow_dir(name).is_dir():
        return _blocked(f"[use_workflow_agent] 工作流不存在: {name}（list_workflow_agents 查看可用 agent）")
    meta = wf._read_meta(name)
    if not meta.get("enabled", True):
        return _blocked(f"[use_workflow_agent] 工作流已被禁用: {name}")
    try:
        agent_name = wf._agent_name(name)
    except Exception:
        agent_name = name
    # 热插拔：注册该工作流自定义工具 + 技能目录，并失效技能缓存
    try:
        wf.apply_tools(name)
    except Exception:
        pass
    try:
        _sub("agent_skills").invalidate_skills_cache()
    except Exception:
        pass
    return {"text": f"已切换调用工作流「{name}」的 Agent（{agent_name}）："
                    "本对话后续按该 agent 的人格/技能/工具执行，可随时再切换或切回默认。",
            "images": [], "__use_workflow__": name}


def _inspect_workflow(name: str) -> dict:
    try:
        info = _wf().inspect_workflow(name)
        if "error" in info:
            return _blocked(info["error"])
        lines = [f"工作流: {info['name']}"
                 + ("（默认/只读）" if info["is_default"] else "")
                 + ("（激活中）" if info["active"] else "")]
        meta = info.get("meta") or {}
        if meta.get("description"):
            lines.append(f"说明: {meta['description']}")
        for f, d in (info.get("files") or {}).items():
            if d.get("type") == "file":
                head = (d.get("head") or "").replace("\n", " ")[:120]
                lines.append(f"- {f}（{d.get('size')}B）: {head}")
            else:
                lines.append(f"- {f}/（目录）: {', '.join(d.get('children') or [])[:150]}")
        return {"text": "\n".join(lines), "images": []}
    except Exception as e:
        return _blocked(f"[inspect_workflow] {e}")


def _edit_agent_file(name: str, file: str, action: str, content) -> dict:
    try:
        if (action or "read").lower() == "write":
            if content is None:
                return _blocked("[edit_agent_file] write 需要 content 参数")
            ok, msg = _wf().write_core_file(name, file, content)
            return {"text": msg, "images": []} if ok else _blocked(msg)
        ok, res = _wf().read_core_file(name, file)
        return {"text": res, "images": []} if ok else _blocked(res)
    except Exception as e:
        return _blocked(f"[edit_agent_file] {e}")


def _delete_workflow(name: str) -> dict:
    try:
        ok, msg = _wf().delete_workflow(name)
        return {"text": msg, "images": []} if ok else _blocked(msg)
    except Exception as e:
        return _blocked(f"[delete_workflow] {e}")


def _uiux_list_arg(v):
    """解析 manage_uiux 的列表参数（plugins/mcp）：list/tuple 直接使用；字符串按 JSON 解析。
    非法时返回 {"error": 描述}；空值返回 None。"""
    if v is None or v == "":
        return None
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, str):
        try:
            arr = json.loads(v)
        except Exception:
            return {"error": "不是合法 JSON 列表"}
        if isinstance(arr, list):
            return [str(x).strip() for x in arr if str(x).strip()]
        return {"error": "不是合法 JSON 列表"}
    return None


def _manage_uiux(args: dict) -> dict:
    """manage_uiux 工具：UI/UX 包管理
    （list/create/read/update/set_qss/update_theme/duplicate/export/import/activate/delete/get）"""
    from zhuzhu_Copilot.core import agent_ui_ux
    op = str(args.get("op") or "list").strip().lower()  # op 缺省默认 list（只读安全）
    name = str(args.get("name", "")).strip()
    try:
        if op == "list":
            pkgs = agent_ui_ux.list_packages()
            active = agent_ui_ux.get_active_package()
            lines = [f"当前活跃: {active}", "已有包:"]
            for p in pkgs:
                mark = " ●当前" if p.get("name") == active else ""
                builtin = "（内置）" if p.get("is_builtin") else ""
                themed = "（自定义主题）" if p.get("theme") else ""
                desc = (p.get("description") or "").strip()
                lines.append(f"- {p.get('name')}{mark}{builtin}{themed}：{desc or '无描述'}")
            return {"text": "\n".join(lines), "images": []}
        if op == "get":
            return {"text": f"当前活跃 UI/UX 包: {agent_ui_ux.get_active_package()}", "images": []}
        if op == "activate":
            if not name:
                return _blocked("[manage_uiux] activate 需要 name 参数")
            ok, msg = agent_ui_ux.set_active_package(name)
            if not ok:
                return _blocked(msg)
            return {"text": msg + "（面板热插拔立即生效）", "images": [],
                    "rebuild_uiux": True}
        if op == "create":
            if not name:
                return _blocked("[manage_uiux] create 需要 name 参数")
            theme = None
            raw_theme = args.get("theme")
            if raw_theme:
                try:
                    theme = json.loads(raw_theme) if isinstance(raw_theme, str) else raw_theme
                except Exception:
                    return _blocked("[manage_uiux] theme 参数不是合法 JSON：{\"dark\":{...},\"light\":{...}}")
            plugins = _uiux_list_arg(args.get("plugins"))
            mcp = _uiux_list_arg(args.get("mcp"))
            if isinstance(plugins, dict) or isinstance(mcp, dict):
                return _blocked("[manage_uiux] plugins/mcp 参数不是合法 JSON 列表，如 [\"name\"]")
            ok, msg = agent_ui_ux.create_package(
                name,
                str(args.get("description", "") or ""),
                build_ui_code=str(args.get("build_ui", "") or ""),
                build_welcome_code=str(args.get("build_welcome", "") or ""),
                theme=theme, plugins=plugins, mcp=mcp)
            if not ok:
                return _blocked(msg)
            extra = ""
            if args.get("qss"):
                okq, msgq = agent_ui_ux.set_panel_qss(name, str(args.get("qss", "")))
                if okq:
                    extra = "，已写入 panel.qss"
                else:
                    extra = f"，panel.qss 写入失败：{msgq}"
            return {"text": msg + extra + "（可 op=read 查看，op=activate 切换生效）", "images": []}
        if op == "deps":
            if not name:
                return _blocked("[manage_uiux] deps 需要 name 参数")
            plugins = _uiux_list_arg(args.get("plugins"))
            mcp = _uiux_list_arg(args.get("mcp"))
            if isinstance(plugins, dict) or isinstance(mcp, dict):
                return _blocked("[manage_uiux] plugins/mcp 参数不是合法 JSON 列表，如 [\"name\"]")
            ok, msg = agent_ui_ux.update_package_deps(name, plugins=plugins, mcp=mcp)
            if not ok:
                return _blocked(msg)
            return {"text": msg, "images": []}
        if op == "read":
            if not name:
                return _blocked("[manage_uiux] read 需要 name 参数")
            part = str(args.get("part", "") or "meta").strip()
            if part == "qss":
                return {"text": agent_ui_ux.get_panel_qss(name), "images": []}
            ok, text = agent_ui_ux.read_package_part(name, part)
            if not ok:
                return _blocked(text)
            return {"text": text, "images": []}
        if op == "update":
            if not name:
                return _blocked("[manage_uiux] update 需要 name 参数")
            code = str(args.get("build_ui", "") or "")
            if not code.strip():
                return _blocked("[manage_uiux] update 需要 build_ui 参数")
            ok, msg = agent_ui_ux.set_build_ui_code(name, code)
            if not ok:
                return _blocked(msg)
            return {"text": msg + ("（当前已激活，面板将热插拔刷新）" if
                                   agent_ui_ux.get_active_package() == name else
                                   "（未激活，切换后生效）"), "images": [],
                    "rebuild_uiux": agent_ui_ux.get_active_package() == name}
        if op == "set_qss":
            if not name:
                return _blocked("[manage_uiux] set_qss 需要 name 参数")
            qss = str(args.get("qss", "") or "")
            ok, msg = agent_ui_ux.set_panel_qss(name, qss)
            if not ok:
                return _blocked(msg)
            return {"text": msg + ("（当前已激活，面板将热插拔刷新）" if
                                   agent_ui_ux.get_active_package() == name else
                                   "（未激活，切换后生效）"), "images": [],
                    "rebuild_uiux": agent_ui_ux.get_active_package() == name}
        if op == "update_theme":
            if not name:
                return _blocked("[manage_uiux] update_theme 需要 name 参数")
            raw_theme = args.get("theme")
            if not raw_theme:
                return _blocked("[manage_uiux] update_theme 需要 theme 参数（JSON）")
            try:
                theme = json.loads(raw_theme) if isinstance(raw_theme, str) else raw_theme
            except Exception:
                return _blocked("[manage_uiux] theme 参数不是合法 JSON：{\"dark\":{...},\"light\":{...}}")
            ok, msg = agent_ui_ux.update_package_theme(name, theme)
            if not ok:
                return _blocked(msg)
            return {"text": msg + ("（当前已激活，面板将热插拔刷新）" if
                                   agent_ui_ux.get_active_package() == name else
                                   "（未激活，切换后生效）"), "images": [],
                    "rebuild_uiux": agent_ui_ux.get_active_package() == name}
        if op == "duplicate":
            new_name = str(args.get("new_name", "") or "").strip()
            if not name or not new_name:
                return _blocked("[manage_uiux] duplicate 需要 name 与 new_name 参数")
            ok, msg = agent_ui_ux.duplicate_package(name, new_name)
            if not ok:
                return _blocked(msg)
            return {"text": msg, "images": []}
        if op == "export":
            if not name:
                return _blocked("[manage_uiux] export 需要 name 参数")
            dest = str(args.get("dest", "") or "").strip()
            if not dest:
                dest = str(agent_tools.get_workdir() or "")   # 默认导出到工作目录
            ok, msg = agent_ui_ux.export_package_zip(name, dest)
            if not ok:
                return _blocked(msg)
            return {"text": f"已导出 {name} 到: {msg}", "images": []}
        if op == "import":
            zip_path = str(args.get("zip_path", "") or "").strip()
            if not zip_path:
                return _blocked("[manage_uiux] import 需要 zip_path 参数")
            ok, msg = agent_ui_ux.import_package_zip(zip_path, overwrite=True)
            if not ok:
                return _blocked(msg)
            return {"text": msg + "（可 op=activate 切换启用）", "images": []}
        if op == "delete":
            if not name:
                return _blocked("[manage_uiux] delete 需要 name 参数")
            ok, msg = agent_ui_ux.delete_package(name)
            if not ok:
                return _blocked(msg)
            return {"text": msg + ("（已回退默认界面）" if
                                   agent_ui_ux.get_active_package() == "default" else ""),
                    "images": [], "rebuild_uiux": True}
        return _blocked(f"[manage_uiux] 未知操作 {op}，可选 list/create/read/update/set_qss/"
                        f"update_theme/duplicate/export/import/activate/delete/get")
    except Exception as e:
        return _blocked(f"[manage_uiux] {e}")


# register_panel_btn 允许的 action → 说明（与面板 agent_panel._BTN_ACTIONS 的 action
# 集合保持一致，新增 action 需同时更新两侧；引擎只把 action 名透传，由面板在主线程
# 解析成已有方法，杜绝任意方法名调用。面板侧解析失败会跳过注册并告警，不会产生死按钮）
_PANEL_BTN_ACTIONS = {
    "send": "发送/停止当前任务",
    "new_session": "新建对话",
    "settings": "打开设置",
    "attach": "选择附件",
    "optimize": "优化提示词",
    "clear_chat": "清空并删除当前对话",
}


def _manage_panel_btn(args: dict) -> dict:
    """register_panel_btn 工具：注册/注销面板顶部按钮栏的自定义按钮。
    仅返回待应用载荷（btn_reg_pending），由引擎回调解发到 UI 主线程真正注册。"""
    try:
        op = str(args.get("op") or "").strip().lower()
        if not op:
            return _blocked("[register_panel_btn] 请补充 op 参数：op=register 或 op=unregister。"
                            "register 需 id/action/text/tooltip（action 可选：send/new_session/"
                            "settings/attach/optimize/clear_chat），unregister 需 id")
        if op in ("register", "update"):
            bid = str(args.get("id") or "").strip()
            if not bid:
                return _blocked("[register_panel_btn] register 需要 id 参数（英文/数字/下划线）")
            action = str(args.get("action") or "").strip()
            if not action or action not in _PANEL_BTN_ACTIONS:
                return _blocked("[register_panel_btn] action 必须是以下之一："
                                + "、".join(f"{k}({v})" for k, v in _PANEL_BTN_ACTIONS.items()))
            return {
                "text": (f"面板按钮「{bid or action}」已注册：点击触发「{_PANEL_BTN_ACTIONS[action]}」。"
                         "按钮显示在面板顶部按钮栏，与 UI/UX 解耦，切换/重构界面仍保持可见可用。"),
                "images": [],
                "btn_reg_pending": {"op": "register", "id": bid,
                                    "text": str(args.get("text") or bid),
                                    "action": action,
                                    "tooltip": str(args.get("tooltip") or "")},
            }
        if op == "unregister":
            bid = str(args.get("id") or "").strip()
            if not bid:
                return _blocked("[register_panel_btn] unregister 需要 id 参数")
            return {"text": f"面板按钮「{bid}」已注销",
                    "images": [],
                    "btn_reg_pending": {"op": "unregister", "id": bid}}
        return _blocked("[register_panel_btn] 未知 op，可选 register/unregister")
    except Exception as e:
        return _blocked(f"[register_panel_btn] {e}")


def _inspect_customization() -> dict:
    """盘点当前全部可深度自定义维度（Cordis：一切皆可替换/热插拔），
    返回各维度现状与对应自定义工具，供 agent 引导用户选择自定义/新增功能。"""
    try:
        from zhuzhu_Copilot.core import agent_ui_ux, agent_plugins
        from zhuzhu_Copilot.core import agent_skills
        _skills = _sub("agent_skills")
        lines = ["# 可深度自定义维度盘点（Cordis 一切皆可替换/热插拔）"]
        # 1) 工作流
        try:
            wfs = _wf().list_workflows()
            wf_lines = [f"- {w['name']}{'[默认只读]' if w['is_default'] else ('[激活]' if w['active'] else '')}"
                        for w in (wfs or [])]
            lines.append(f"## 工作流（{len(wfs or [])} 个）")
            lines.append("  " + ("；".join(wf_lines) if wf_lines else "仅默认工作流"))
            lines.append("  工具: list_workflows / create_workflow / inspect_workflow / edit_agent_file /"
                         " switch_workflow / use_workflow_agent —— 定制 agent.py 人格/llm.py 客户端/"
                         "tools.py 工具/skills/plugins/mcp.json，热插拔生效")
            presets = _wf().list_builtin_workflows()
            if presets:
                p_items = [f"{p.get('display_name') or p.get('id')}"
                           f"({p.get('id')}{'，已创建' if p.get('created') else ''})"
                           for p in presets]
                lines.append(f"  内置工作流预设（{len(presets)} 个，可用 create_workflow(preset=<id>) 一键创建）: "
                             + "；".join(p_items))
        except Exception as e:
            lines.append(f"## 工作流（读取失败: {e}）")
        # 2) 技能
        try:
            skills = _skills.list_skills_for_workflow("")
            names = sorted(s.get("name", "") for s in skills if s.get("name"))
            lines.append(f"## 技能（{len(names)} 个）")
            lines.append("  " + ("，".join(names) if names else "暂无"))
            lines.append("  工具: create_skill（新建技能）/ skill-mgmt（导入/启停/删除），技能为 SKILL.md 市场标准")
        except Exception as e:
            lines.append(f"## 技能（读取失败: {e}）")
        # 3) 插件
        try:
            plugins = agent_plugins.list_plugins()
            p_lines = [f"{p.get('name')}({p.get('kind', 'mcp')})"
                       for p in plugins if p.get("name")]
            lines.append(f"## 插件（{len(p_lines)} 个）")
            lines.append("  " + ("；".join(p_lines) if p_lines else "暂无"))
            lines.append("  工具: create_plugin（mcp/skill/combined/web 四型，AI 生成或 zip 导入）")
        except Exception as e:
            lines.append(f"## 插件（读取失败: {e}）")
        # 4) UI/UX 包
        try:
            pkgs = agent_ui_ux.list_packages()
            active = agent_ui_ux.get_active_package()
            ux_lines = [f"{p.get('name')}{'●' if p.get('name') == active else ''}"
                        for p in pkgs if p.get("name")]
            lines.append(f"## UI/UX 包（{len(ux_lines)} 个，活跃: {active}）")
            lines.append("  " + ("；".join(ux_lines) if ux_lines else "仅默认"))
            lines.append("  工具: manage_uiux（create/read/update/set_qss/update_theme/deps/duplicate/"
                         "export/import/activate/delete）—— 完整自定义 PyQt6 面板 + 主题色板 + panel.qss")
        except Exception as e:
            lines.append(f"## UI/UX 包（读取失败: {e}）")
        # 5) 面板按钮
        lines.append("## 面板自定义按钮（与 UI/UX 解耦，恒显示）")
        lines.append("  工具: register_panel_btn（register/unregister）—— 注册顶部按钮栏按钮")
        # 6) MCP 服务器
        try:
            servers = agent_skills.load_mcp_servers()
            m_lines = [f"{s.get('name')}({s.get('type', '?')})" for s in servers if s.get("name")]
            lines.append(f"## MCP 服务器（{len(m_lines)} 个）")
            lines.append("  " + ("；".join(m_lines) if m_lines else "暂无"))
        except Exception as e:
            lines.append(f"## MCP 服务器（读取失败: {e}）")
        # 7) 工作流子 Agent（小功能注入现有工作流的落地通道）
        try:
            subs = _sub("agent_subagent").registered_subagents("")
            s_lines = [f"{s.get('name')}({s.get('description') or '-'})" for s in subs] \
                if subs else []
            wf_name = _wf().active_workflow() or "默认"
            lines.append(f"## 子 Agent（当前工作流「{wf_name}」注册 {len(s_lines)} 个，另有内置 "
                         "dispatch_sub_agents/explore_project/search_large）")
            lines.append("  " + ("；".join(s_lines) if s_lines else "暂无自定义子 Agent"))
            lines.append("  工具: list_sub_agents / register_sub_agent（注册进当前工作流，不新建工作流）")
        except Exception as e:
            lines.append(f"## 子 Agent（读取失败: {e}）")
        # 8) 扩展功能面板（小 UI 面板注入现有工作流的落地通道）
        try:
            names = [p["name"] for p in _sub("agent_panels").list_panels()] \
                if _sub("agent_panels").list_panels() else []
            lines.append(f"## 扩展功能面板（{len(names)} 个）")
            lines.append("  " + ("，".join(names) if names else "暂无（用 register_feature_panel 注入当前工作流）"))
            lines.append("  工具: register_feature_panel（写入当前工作流 panel.py，不新建 UI/UX 包）")
        except Exception as e:
            lines.append(f"## 扩展功能面板（读取失败: {e}）")
        lines.append("")
        lines.append("优先级（必须遵守）：小功能的改动——新增功能面板/子 agent/工具/小 UI/小钩子——"
                     "一律注入当前工作流或现有 UI/UX（register_sub_agent / register_feature_panel / "
                     "edit_agent_file tools.py / manage_uiux read+set_qss/update_theme）；"
                     "仅大而独立的功能才新建工作流/UI/UX 包。")
        lines.append("引导：先用 ask_user 问清用户想自定义/新增的功能，再按上述维度调用对应工具落地，"
                     "创建后提示用户立即生效（工作流/UI/UX 热插拔，插件/技能即时登记）。")
        return {"text": "\n".join(lines), "images": []}
    except Exception as e:
        return _blocked(f"[inspect_customization] {e}")


def _list_sub_agents() -> dict:
    """list_sub_agents：列出内置 + 当前工作流自定义子 Agent 及调用方式"""
    try:
        _sub_agent = _sub("agent_subagent")
        wf = _wf().active_workflow() or "默认"
        lines = [f"# 子 Agent（当前工作流「{wf}」）"]
        lines.append("内置（通用并发派发，任一任务可用）：dispatch_sub_agents / explore_project（了解项目）"
                     " / search_large（大规模搜索）")
        subs = _sub_agent.registered_subagents("")
        if not subs:
            lines.append("当前工作流暂无自定义子 Agent。要新增一个小子 Agent，用 register_sub_agent "
                         "（name/description/goal/allowed）注册进当前工作流，立即作为 sub_<name> 工具可用，"
                         "不必新建工作流。")
        else:
            lines.append("当前工作流已注册自定义子 Agent：")
            for s in subs:
                head = f"- sub_{s['name']}：{s['description'] or '(无说明)'}"
                if s.get("allowed"):
                    head += f"（工具: {', '.join(s['allowed'])}）"
                sc = s.get("shared_context")
                if sc is True:
                    head += "（默认加入共同上下文空间）"
                lines.append(head)
            lines.append("注册式子 Agent 的三种用法：① 用户输入框 @<名> 直接调用；"
                         "② 主 Agent 调用 sub_<name> 工具（可传 goal/context/shared_context/space）；"
                         "③ 由主 Agent 分配是否加入共同上下文空间。")
            lines.append("主 Agent 逐次分配：调用 sub_<name> 或 dispatch_sub_agents 时传 shared_context="
                         "true/false 覆盖注册默认值决定该子 Agent 是否加入共享空间，传 context="
                         "把主 Agent 已读取的文件内容/结论等上下文交给子 Agent（临时子任务同样支持）；"
                         "shared_context 工具 op=open 开启空间。")
        return {"text": "\n".join(lines), "images": []}
    except Exception as e:
        return _blocked(f"[list_sub_agents] {e}")


def _register_sub_agent(args: dict) -> dict:
    """register_sub_agent：向当前工作流注册/注销自定义子 Agent（不新建工作流）"""
    try:
        _sub_agent = _sub("agent_subagent")
        op = str(args.get("op") or "register").strip().lower()
        name = str(args.get("name") or "").strip()
        if not name:
            return _blocked("[register_sub_agent] 缺少 name 参数（子 Agent 唯一标识）")
        if op in ("unregister", "remove", "delete"):
            ok, msg = _sub_agent.unregister_subagent(name)
            return {"text": msg, "images": []} if ok else _blocked(msg)
        if op != "register":
            return _blocked("[register_sub_agent] 未知 op，可选 register/unregister")
        _sc = args.get("shared_context")
        _al = args.get("allow_chat")
        _sx = args.get("share_context")
        ok, msg = _sub_agent.register_subagent(
            name,
            str(args.get("description") or ""),
            str(args.get("goal") or ""),
            str(args.get("allowed") or ""),
            str(args.get("persona") or ""),
            shared_context=_sc if _sc is not None else True,
            allow_chat=bool(_al) if _al is not None else True,
            share_context=bool(_sx) if _sx is not None else True)
        return {"text": msg, "images": []} if ok else _blocked(msg)
    except Exception as e:
        return _blocked(f"[register_sub_agent] {e}")


def _set_session_name(args: dict) -> dict:
    """set_session_name：为当前对话命名/改名（返回 __session_name__ 标记，
    由引擎通知 UI 在主线程写入会话列表——AI Agent 也能给对话起名）。"""
    title = _sub("agent_llm").clean_title(str(args.get("title") or ""), 0)
    if not title:
        return _blocked("[set_session_name] 缺少 title（对话标题，概括主题即可）")
    return {"text": f"已把当前对话命名为「{title}」", "images": [],
            "__session_name__": title}


# ------------------------------------------------------------
# 工作团通信与监督：chat_with / look_context / pause_agent / resume_agent / warn_agent
# ------------------------------------------------------------
def _resolve_agent_id(name: str) -> str:
    """把模型/用户给的名字解析为控制注册表 agent_id。
    main→main；注册子 Agent→sub:<名>；工作流→wf:<名>；已带前缀原样返回。"""
    n = str(name or "").strip()
    if not n:
        return ""
    if n in ("main", "主Agent", "主agent", "当前"):
        return "main"
    if n.startswith("sub:") or n.startswith("wf:"):
        return n
    try:
        from zhuzhu_Copilot.core import agent_subagent
        if agent_subagent.subagent_tool(n, _wf()) is not None:
            return "sub:" + n
        if _wf().is_workflow(n):
            return "wf:" + n
    except Exception:
        pass
    return ""


def _self_agent_id() -> str:
    """当前执行上下文归属的 agent id：子 Agent 循环用其来源标签，否则主 Agent（main）。"""
    try:
        from zhuzhu_Copilot.core import agent_context, agent_subagent
        s = agent_context.current_source()
        if s:
            if s.startswith(("sub:", "wf:", "main")):
                return s
            if agent_subagent.subagent_tool(s, _wf()) is not None:
                return "sub:" + s
            return s
    except Exception:
        pass
    return "main"


def _chat_with(args: dict) -> dict:
    """chat_with：向指定 Agent 发送消息（目标空闲时下轮任务收到，忙碌时入队暂存）。"""
    try:
        to = _resolve_agent_id(args.get("to"))
        text = str(args.get("text") or "").strip()
        if not to:
            return _blocked("[chat_with] 未识别目标 Agent，可用：main / 注册子 Agent 名 / 工作流名")
        if not text:
            return _blocked("[chat_with] 缺少 text 消息内容")
        if to.startswith("sub:"):
            from zhuzhu_Copilot.core import agent_subagent
            conf = agent_subagent.subagent_tool(to[4:], _wf())
            if not (conf and conf.get("allow_chat")):
                return _blocked(f"[chat_with] 子 Agent「{to[4:]}」未开启聊天权限"
                                "（allow_chat=false），无法接收聊天消息")
        from zhuzhu_Copilot.core import agent_bus
        me = _self_agent_id()
        if agent_bus.send_message(me, to, text, "chat"):
            agent_bus.ledger(me).add("message", f"chat_with -> {to}", text[:200])
            return {"text": f"消息已发送给 Agent「{to}」，其将在下一个任务检查点收到。", "images": []}
        return _blocked("[chat_with] 消息发送失败")
    except Exception as e:
        return _blocked(f"[chat_with] {e}")


def _look_context(args: dict) -> dict:
    """look_context：查看指定 Agent 的上下文轨迹（消息/命令/文件/工具/skill/mcp/plugin）。"""
    try:
        target = _resolve_agent_id(args.get("agent"))
        if not target:
            return _blocked("[look_context] 未识别目标 Agent，可用：main / 注册子 Agent 名 / 工作流名")
        from zhuzhu_Copilot.core import agent_bus, agent_context
        if target.startswith("sub:"):
            from zhuzhu_Copilot.core import agent_subagent
            conf = agent_subagent.subagent_tool(target[4:], _wf())
            if not (conf and conf.get("share_context")):
                return _blocked(f"[look_context] 子 Agent「{target[4:]}」未开启上下文共享"
                                "（share_context=false），无法查看其上下文")
        kinds = args.get("types")
        if isinstance(kinds, str) and str(kinds).strip():
            kinds = [k.strip() for k in str(kinds).replace("，", ",").split(",") if k.strip()]
        elif not isinstance(kinds, (list, tuple)):
            kinds = None
        limit = max(0, int(args.get("limit") or 80))
        text = agent_bus.render_ledger(target, kinds, limit)
        # 工作团监督：附加成员后台运行状态（运行中/已完成/空闲/异常），让领导者
        # 一眼看出成员是否在独立工作（agent_team_run 后台运行器）。
        # 异常时附上失败原因、运行中附开始时间，避免"只看状态名无法诊断"。
        try:
            from zhuzhu_Copilot.core import agent_team_run
            _st = agent_team_run.team_status_label(target)
            _extra = ""
            try:
                _detail = agent_team_run.team_status(target).get(target)
                if _detail and _detail.get("error") and agent_team_run.team_result(target):
                    _extra = f"（失败原因：{agent_team_run.team_result(target)[:200]}）"
                elif _detail and _detail.get("running") and _detail.get("started"):
                    _extra = f"（开始于 {_detail['started']}）"
            except Exception:
                _extra = ""
            _state = f"[成员状态] {_st}{_extra}"
            text = f"{_state}\n" + text if text else _state
        except Exception:
            pass
        me = _self_agent_id()
        sid = agent_context.active_space()
        if sid and agent_context.has_space(sid):
            sp = agent_context.render(sid, viewer=me)
            if sp and sp.strip():
                text = (text + "\n\n" if text else "") + sp
        if not text:
            return {"text": f"Agent「{target}」暂无可见上下文（未运行或尚无轨迹记录）。",
                    "images": []}
        return {"text": f"【{target} 上下文】\n{text}", "images": []}
    except Exception as e:
        return _blocked(f"[look_context] {e}")


def _pause_agent(args: dict) -> dict:
    """pause_agent：暂停指定 Agent（运行中的在下一检查点挂起，空闲的标记暂停）。"""
    try:
        target = _resolve_agent_id(args.get("agent"))
        if not target:
            return _blocked("[pause_agent] 未识别目标 Agent")
        from zhuzhu_Copilot.core import agent_control
        ok, msg = agent_control.pause_agent(target)
        return {"text": msg, "images": []} if ok else _blocked(msg)
    except Exception as e:
        return _blocked(f"[pause_agent] {e}")


def _resume_agent(args: dict) -> dict:
    """resume_agent：恢复指定 Agent（解除暂停，任务继续）。"""
    try:
        target = _resolve_agent_id(args.get("agent"))
        if not target:
            return _blocked("[resume_agent] 未识别目标 Agent")
        from zhuzhu_Copilot.core import agent_control
        ok, msg = agent_control.resume_agent(target)
        return {"text": msg, "images": []} if ok else _blocked(msg)
    except Exception as e:
        return _blocked(f"[resume_agent] {e}")


def _warn_agent(args: dict) -> dict:
    """warn_agent：向指定 Agent 发送警告提醒（下一任务检查点注入其上下文）。"""
    try:
        target = _resolve_agent_id(args.get("agent"))
        text = str(args.get("text") or "").strip()
        if not target:
            return _blocked("[warn_agent] 未识别目标 Agent")
        if not text:
            return _blocked("[warn_agent] 缺少 text 警告内容")
        from zhuzhu_Copilot.core import agent_control
        ok, msg = agent_control.warn_agent(target, text)
        return {"text": msg, "images": []} if ok else _blocked(msg)
    except Exception as e:
        return _blocked(f"[warn_agent] {e}")


def _shared_context(args: dict) -> dict:
    """shared_context：共同上下文空间（主 Agent 开启 / 成员双向读写）"""
    try:
        ctx = _sub("agent_context")
        op = str(args.get("op") or "status").strip().lower()
        space = str(args.get("space") or "").strip()
        if op == "open":
            sid = ctx.open_space(space, str(args.get("seed") or ""),
                                 owner="主Agent",
                                 activate=bool(args.get("activate", True)))
            return {"text": (f"已开启共同上下文空间 {sid}（活跃空间）。"
                             "后续可用 dispatch_sub_agents(shared_context=true) 或 "
                             "sub_<name>(shared_context=true) 让子 Agent 加入共享；"
                             "成员可经本工具 op=read/append 读写统一上下文。")
                            + (f"\n议题: {ctx.seed_text(sid)}" if ctx.seed_text(sid) else ""),
                    "images": []}
        if op == "close":
            ok, msg = ctx.close_space(space)
            return {"text": msg, "images": []} if ok else _blocked(f"[shared_context] {msg}")
        if op == "append":
            sid = space or ctx.current()
            if not sid or not ctx.has_space(sid):
                return _blocked("[shared_context] 尚未开启共同上下文空间，请先 op=open")
            src = str(args.get("source") or "").strip() or ctx.current_source() or "主Agent"
            if not ctx.append(sid, src, str(args.get("text") or ""),
                              str(args.get("kind") or "note")):
                return _blocked("[shared_context] 写入失败（text 为空或空间不存在）")
            st = ctx.stats(sid)
            return {"text": f"已写入共同上下文空间 {sid}（来自 {src}，"
                            f"当前 {st['entries']} 条/{st['chars']} 字）。", "images": []}
        if op == "read":
            sid = space or ctx.current()
            if not sid or not ctx.has_space(sid):
                return _blocked("[shared_context] 尚未开启共同上下文空间，请先 op=open")
            limit = args.get("max_entries")
            body = ctx.render(sid, exclude=str(args.get("exclude") or ""),
                              limit=_sub("agent_sandbox").to_int(limit) if limit else 0)
            return {"text": body or f"共同上下文空间 {sid} 当前为空（暂无成员写入）。",
                    "images": []}
        if op == "list":
            items = ctx.list_spaces()
            if not items:
                return {"text": "当前没有任何共同上下文空间。", "images": []}
            lines = ["# 共同上下文空间"]
            for s in items:
                lines.append(f"- {s['space']}（{'活跃' if s['active'] else '未激活'}）"
                             f"  条目 {s['entries']}，{s['chars']} 字")
            lines.append("用 op=read/status + space 查看详情；op=close + space 关闭回收。")
            return {"text": "\n".join(lines), "images": []}
        # op=status
        st = ctx.stats(space or ctx.current())
        if not st.get("exists"):
            return {"text": (f"共同上下文空间 {st['space'] or '(无)'} 未开启。"
                             "用 op=open 开启（可选 seed 注入议题）。"), "images": []}
        return {"text": (f"共同上下文空间 {st['space']}：条目 {st['entries']}，{st['chars']} 字，"
                         f"{'活跃' if st['active'] else '未激活'}。"
                         + (f"议题: {ctx.seed_text(st['space'])}" if ctx.seed_text(st['space']) else "")),
                "images": []}
    except Exception as e:
        return _blocked(f"[shared_context] {e}")


def _register_feature_panel(args: dict) -> dict:
    """register_feature_panel：把功能面板 panel.py 写入当前工作流（不新建 UI/UX 包）。
    写好文件后可被 agent_panels.scan_panels 自动发现；返回 rebuild_uiux 标记，
    由引擎在任务结束后通知 UI 重建扩展面板浮窗。"""
    try:
        from zhuzhu_Copilot.core import agent_workflow
        name = str(args.get("name") or "").strip()
        if any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
               for c in name):
            return _blocked("[register_feature_panel] 面板名非法（仅字母/数字/下划线/中划线，英文）")
        wf = agent_workflow.active_workflow() or agent_workflow.DEFAULT_WORKFLOW
        try:
            d = agent_workflow.workflow_dir(wf)
        except Exception as e:
            return _blocked(f"[register_feature_panel] 无法定位当前工作流: {e}")
        if not d.is_dir():
            return _blocked("[register_feature_panel] 当前工作流目录不可用")
        python = str(args.get("python") or "").strip()
        if not python:
            return _blocked("[register_feature_panel] 缺少 python（build_panel(owner) 代码）")
        if "build_panel" not in python:
            return _blocked("[register_feature_panel] python 需定义 build_panel(owner) 函数并返回一个 QWidget")
        title = str(args.get("title") or "").strip()
        title_line = (f'TITLE = "{title}"\n' if title else "")
        width = int(args.get("width") or 300)
        height = int(args.get("height") or 240)
        body = (
            f'# 功能面板「{name}」——注册进工作流「{wf}」的扩展面板浮窗。\n'
            f'# 由 register_feature_panel 生成；面板名 {name}，源 {wf}。\n'
            + title_line
            + f'WIDTH = {max(120, width)}\n'
            + f'HEIGHT = {max(80, height)}\n'
            + "\n"
            + python.rstrip() + "\n")
        panel_path = d / "panel.py"
        try:
            panel_path.write_text(body, encoding="utf-8")
        except Exception as e:
            return _blocked(f"[register_feature_panel] 写入失败: {e}")
        return {"text": f"功能面板「{name}」已写入工作流「{wf}」的 panel.py（扩展浮窗）。"
                        "任务结束后自动扫描挂载，或让我立即重建界面查看。"
                        "提示：该面板已注入现有工作流，未新建 UI/UX 包。"
                        "若面板代码含第三方 import，请用 set_feature_deps 声明并安装依赖。",
                "images": [], "rebuild_uiux": True}
    except Exception as e:
        return _blocked(f"[register_feature_panel] {e}")


def _set_feature_deps(args: dict) -> dict:
    """set_feature_deps：为当前工作流的 AI 自定义代码声明/安装第三方依赖。
    依赖写入 <当前工作流>/requirements.txt，加载自定义代码时自动补齐。"""
    try:
        from zhuzhu_Copilot.core import agent_workflow
        deps_m = _sub("agent_deps")
        wf = agent_workflow.active_workflow() or agent_workflow.DEFAULT_WORKFLOW
        d = agent_workflow.workflow_dir(wf)
        req = deps_m.requirements_of(d)
        op = str(args.get("op") or "declare").strip().lower()
        if op == "list":
            lines = [f"当前工作流「{wf}」依赖清单:"]
            if req.is_file():
                for ln in req.read_text(encoding="utf-8", errors="replace").splitlines():
                    if ln.strip():
                        lines.append(f"  {ln.strip()}")
            else:
                lines.append("  （未声明，用 op=declare 添加）")
            lines.append("安装状态见 requirements.txt 同目录 .deps.stamp（内容未变则已装好，不必重复安装）。")
            return {"text": "\n".join(lines), "images": []}
        if op == "install":
            deps_m.ensure_dir_deps(d)
            n = sum(1 for x in (req.read_text(encoding="utf-8", errors="replace")
                                .splitlines() if req.is_file() else []) if x.strip())
            return {"text": f"已为工作流「{wf}」安装 {n} 条依赖。重启自定义功能或发起下一轮任务后生效。",
                    "images": []}
        # declare：合并覆盖写 requirements.txt
        deps = [str(x).strip() for x in (args.get("deps") or []) if str(x or "").strip()]
        if not deps:
            return _blocked("[set_feature_deps] declare 需要 deps 列表（可为 [] 清空）")
        lines = [x.strip() for x in
                 (req.read_text(encoding="utf-8", errors="replace").splitlines()
                  if req.is_file() else [])]
        head = {ln.split("==")[0].split(">=")[0].split("<=")[0].split("<")[0].split(">")[0].split("~=")[0].split("!=")[0].strip().lower()
                for ln in lines if ln.strip()}
        for dep in deps:
            root = dep.split("==")[0].split(">=")[0].split("<=")[0].split("<")[0].split(">")[0].split("~=")[0].split("!=")[0].strip()
            if root.lower() not in head:
                lines.append(dep)
                head.add(root.lower())
        req.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        return {"text": f"已为工作流「{wf}」声明依赖并写入 requirements.txt，共 {len(lines)} 条。"
                        "op=install 可立即安装，或等加载自定义代码时自动补齐。"
                        "提示：自定义代码已注入现有工作流，未新建资源。",
                "images": []}
    except Exception as e:
        return _blocked(f"[set_feature_deps] {e}")


def _mcp_store() -> tuple:
    """返回 (servers 列表, 保存函数)，操作全局 MCP 服务器注册表（mcp_servers.json）"""
    from zhuzhu_Copilot.core import agent_skills
    return agent_skills.load_mcp_servers(), agent_skills.save_mcp_servers


_MCP_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,50}$")


def _mcp_entry(args: dict, base: dict) -> dict:
    """按参数构造/更新一条 MCP 服务器配置；返回 None 表示参数非法（调用方做拦截）。"""
    typ = str(args.get("type") or base.get("type") or "stdio").strip().lower()
    if typ not in ("stdio", "sse"):
        return None
    entry = dict(base)
    entry["name"] = str(args.get("name") or base.get("name") or "").strip()
    entry["type"] = typ
    if "enabled" in args and args["enabled"] is not None:
        entry["enabled"] = bool(args["enabled"])
    if typ == "sse":
        url = str(args.get("url") or base.get("url") or "").strip()
        if not url:
            return None
        entry["url"] = url
        entry.pop("command", None)
        entry.pop("args", None)
    else:
        command = str(args.get("command") or base.get("command") or "").strip()
        if not command:
            return None
        entry["command"] = command
        if "args" in args and args["args"] is not None:
            entry["args"] = [str(x) for x in (args["args"] if isinstance(args["args"], list) else [str(args["args"])])]
        elif not entry.get("args"):
            entry["args"] = []
        entry.pop("url", None)
    return entry


def _create_mcp(args: dict) -> dict:
    """create_mcp：全局 MCP 注册表新增/更新一个服务器（同名覆盖）。"""
    try:
        name = str(args.get("name") or "").strip()
        if not _MCP_NAME_RE.match(name):
            return _blocked("[create_mcp] name 非法（仅字母/数字/下划线/中划线，≤50 字）")
        servers, save = _mcp_store()
        base = next((s for s in servers if s.get("name") == name), {})
        entry = _mcp_entry(args, base)
        if entry is None:
            return _blocked("[create_mcp] 参数不完整：type=sse 需 url；type=stdio 需 command")
        entry["name"] = name
        if base:
            servers[:] = [entry if s.get("name") == name else s for s in servers]
        else:
            servers.append(entry)
        if not save(servers):
            return _blocked("[create_mcp] 写入 mcp_servers.json 失败")
        return {"text": f"MCP 服务器「{name}」已{'更新' if base else '新增'}到全局注册表"
                        f"（type={entry['type']}，enabled={entry.get('enabled', True)}）。"
                        "发起下一轮任务后连接生效。", "images": [], "reconnect_mcp": True}
    except Exception as e:
        return _blocked(f"[create_mcp] {e}")


def _list_mcp() -> dict:
    """list_mcp：列出全局 MCP 服务器 + 当前工作流是否允许暴露其工具。"""
    try:
        from zhuzhu_Copilot.core import agent_workflow
        servers, _ = _mcp_store()
        wf = agent_workflow.active_workflow()
        allowed = agent_workflow.allowed_mcp_servers(wf)
        if not servers:
            return {"text": "全局 MCP 服务器为空。用 create_mcp 新建（stdio 或 sse）。", "images": []}
        lines = [f"全局 MCP 服务器（当前工作流「{wf}」允许工具暴露者标 ●）:"]
        for s in servers:
            name = str(s.get("name") or "?")
            mark = " ●" if name in allowed else ("   ")
            typ = str(s.get("type") or "stdio")
            target = s.get("url") or s.get("command") or ""
            enabled = "启用" if s.get("enabled", True) else "停用"
            lines.append(f"  [{name}]{mark} {typ} {target}（{enabled}）")
        return {"text": "\n".join(lines), "images": []}
    except Exception as e:
        return _blocked(f"[list_mcp] {e}")


def _set_mcp(args: dict) -> dict:
    """set_mcp：更新或删除全局 MCP 服务器配置。"""
    try:
        name = str(args.get("name") or "").strip()
        if not name:
            return _blocked("[set_mcp] 需要 name 参数")
        servers, save = _mcp_store()
        idx = next((i for i, s in enumerate(servers) if s.get("name") == name), -1)
        op = str(args.get("op") or "update").strip().lower()
        if op == "delete":
            if idx < 0:
                return _blocked(f"[set_mcp] 服务器「{name}」不存在")
            servers.pop(idx)
            if not save(servers):
                return _blocked("[set_mcp] 写入 mcp_servers.json 失败")
            return {"text": f"MCP 服务器「{name}」已删除。发起下一轮任务后断开生效。",
                    "images": [], "reconnect_mcp": True}
        if idx < 0:
            return _blocked(f"[set_mcp] 服务器「{name}」不存在，用 create_mcp 新建")
        base = servers[idx]
        entry = _mcp_entry(args, base)
        if entry is None:
            return _blocked("[set_mcp] 参数不完整：type=sse 需 url；type=stdio 需 command")
        if not save([entry if i == idx else s for i, s in enumerate(servers)]):
            return _blocked("[set_mcp] 写入 mcp_servers.json 失败")
        return {"text": f"MCP 服务器「{name}」已更新（type={entry['type']}，"
                        f"enabled={entry.get('enabled', True)}）。发起下一轮任务后生效。",
                "images": [], "reconnect_mcp": True}
    except Exception as e:
        return _blocked(f"[set_mcp] {e}")


# ------------------------------------------------------------
# 自定义 Agent 管理工具实现（agent_agents 惰性加载）
# ------------------------------------------------------------
def _ag():
    return _sub("agent_agents")


def _agent_desc(ag: dict) -> str:
    """格式化单个自定义 agent 用于列表展示"""
    bound = ag.get("bound_workflow") or ""
    tag = f"[绑定工作流: {bound}]" if bound else "[工具/技能沿用当前工作流]"
    prompt = (ag.get("system_prompt") or "").strip().replace("\n", " ")
    desc = prompt[:40] + ("…" if len(prompt) > 40 else "")
    return f"- {ag.get('name')} {tag}  {desc}"


def _list_agents_network(args: dict) -> dict:
    """list_agents_network：统一查看 Agent 编队全貌（工作流主 Agent / 自定义人格 / 子 Agent）"""
    try:
        scope_wf = str(args.get("workflow") or "").strip()
        lines = ["# Agent 编队"]
        try:
            wfs = _wf().list_workflows()
            lines.append("## 工作流主 Agent（各工作流 agent.py，可被主 Agent 切换调用或派发）")
            for w in wfs:
                nm = w.get("name") or ""
                if scope_wf and nm != scope_wf:
                    continue
                dsc = (w.get("description") or "").strip()
                tag = " [默认]" if w.get("is_default") else ""
                if not w.get("enabled", True):
                    tag += " [已禁用]"
                lines.append(f"- {nm}{tag}" + (f"：{dsc}" if dsc else ""))
        except Exception:
            lines.append("（工作流列表读取失败）")
        agents = _ag().list_agents()
        if not agents:
            lines.append("## 自定义 Agent（@agent 会话级切换人格）\n"
                         "暂无。用 register_agent_network(op=create_agent) 创建。")
        else:
            lines.append("## 自定义 Agent（@agent 会话级切换人格）")
            for a in agents:
                bound = a.get("bound_workflow") or ""
                tag = f"[绑定工作流: {bound}]" if bound else "[工具/技能沿用当前工作流]"
                lines.append(f"- {a.get('name')} {tag}")
        lines.append("## 子 Agent（可被主 Agent 调用 + 可 @直接调用）")
        try:
            subs = _sub("agent_subagent").registered_subagents(scope_wf)
            lines.append("内置：dispatch_sub_agents（批量并发，agent 参数可派发其他工作流主 Agent）"
                         " / explore_project / search_large")
            for s in subs:
                head = f"- sub_{s['name']}：{s['description'] or '(无说明)'}"
                if s.get("allowed"):
                    head += f"（工具: {', '.join(s['allowed'])}）"
                lines.append(head)
            if not subs:
                lines.append("该工作流暂无自定义子 Agent（register_sub_agent 注册）。")
            lines.append("主 Agent 可对每个子 Agent 逐次分配：shared_context=true/false（是否加入共同"
                         "上下文空间）+ context=<上下文>（把已读到的文件内容/结论交给它）；"
                         "注册式与临时子任务均支持。")
        except Exception:
            lines.append("（子 Agent 列表读取失败）")
        lines.append("（管理/新增：register_sub_agent 注册式子 Agent；register_agent_network(op=create_agent) "
                     "只创建「主 Agent 人格」（@agent 会话级切换，主 Agent 调不到它）；@_default 切回默认）")
        return {"text": "\n".join(lines), "images": []}
    except Exception as e:
        return _blocked(f"[list_agents_network] {e}")


_CREATE_AGENT_HINT = ("\n\n[形态提醒] 以上创建的是「自定义 Agent 人格」：只能由用户在输入框 @<名> "
                      "切换人格，主 Agent 无法调用它、也不会出现在主 Agent 的工具列表里。"
                      "若需求是「创建一个子 agent / 一个能被主 Agent 调用的下属 agent」，"
                      "请改用 register_sub_agent（注册式子 Agent：@调用 + 主 Agent 调 sub_<name> + "
                      "可按主 Agent 分配参与共同上下文空间）。")


def _register_agent_network(args: dict) -> dict:
    """register_agent_network：统一管理全部自定义 Agent 的核心工具（编队指挥台）。
    op=register_subagent/unregister_subagent 管理子 Agent（目标/当前工作流隔离）；
    op=create_agent/delete_agent 管理自定义人格 Agent（只做会话级人格切换，不可被调用）。"""
    op = str(args.get("op") or "register_subagent").strip().lower()
    name = str(args.get("name") or "").strip()
    try:
        if op in ("unregister_subagent", "remove_subagent"):
            if not name:
                return _blocked("[register_agent_network] 缺少 name 参数")
            ok, msg = _sub("agent_subagent").unregister_subagent(
                name, workflow=str(args.get("workflow") or "").strip())
            return {"text": msg, "images": []} if ok else _blocked(msg)
        if op == "register_subagent":
            if not name:
                return _blocked("[register_agent_network] 缺少 name 参数（子 Agent 唯一标识）")
            ok, msg = _sub("agent_subagent").register_subagent(
                name,
                str(args.get("description") or ""),
                str(args.get("goal") or ""),
                str(args.get("allowed") or ""),
                str(args.get("persona") or ""),
                workflow=str(args.get("workflow") or "").strip(),
                shared_context=args.get("shared_context"))
            return {"text": msg, "images": []} if ok else _blocked(msg)
        if op == "create_agent":
            if not name:
                return _blocked("[register_agent_network] 缺少 name 参数（Agent 唯一标识）")
            ok, msg = _ag().save_agent(
                name,
                str(args.get("system_prompt") or ""),
                str(args.get("bound_workflow") or ""))
            if not ok:
                return _blocked(msg)
            # 结构性纠偏：自定义 Agent 只是「会话级切换主 Agent 人格」，主 Agent 无法把它当子
            # Agent 调用。模型误用它顶替「创建子 agent」时，把差异回给模型，下一轮改走注册通道。
            return {"text": msg + _CREATE_AGENT_HINT, "images": []}
        if op == "delete_agent":
            if not name:
                return _blocked("[register_agent_network] 缺少 name 参数")
            ok, msg = _ag().delete_agent(name)
            return {"text": msg, "images": []} if ok else _blocked(msg)
        return _blocked("[register_agent_network] 未知 op：可选 register_subagent / "
                        "unregister_subagent / create_agent / delete_agent")
    except Exception as e:
        return _blocked(f"[register_agent_network] {e}")


def _list_agents() -> dict:
    try:
        agents = _ag().list_agents()
        # 聚合视图：工作流主 Agent + 自定义人格 Agent + 子 Agent，编队全貌一屏可见
        lines = ["# Agent 编队（含工作流主 Agent / 自定义人格 / 子 Agent）"]
        try:
            wfs = _wf().list_workflows()
            lines.append("## 工作流主 Agent（可被主 Agent 切换调用或派发）")
            for w in wfs:
                nm = w.get("name") or ""
                dsc = (w.get("description") or "").strip()
                tag = " [默认]" if w.get("is_default") else ""
                if not w.get("enabled", True):
                    tag += " [已禁用]"
                lines.append(f"- {nm}{tag}" + (f"：{dsc}" if dsc else ""))
        except Exception:
            lines.append("（工作流列表读取失败）")
        if not agents:
            lines.append("## 自定义 Agent（@agent 会话级切换人格；不是子 Agent，主 Agent 调不到它）\n"
                         "暂无。用 create_agent 创建，或在输入框 @agent 切换人格。")
        else:
            lines.append("## 自定义 Agent（@agent 会话级切换人格；不是子 Agent，主 Agent 调不到它）")
            for a in agents:
                bound = a.get("bound_workflow") or ""
                tag = f"[绑定工作流: {bound}]" if bound else "[工具/技能沿用当前工作流]"
                prompt = (a.get("system_prompt") or "").strip().replace("\n", " ")
                desc = prompt[:40] + ("…" if len(prompt) > 40 else "")
                lines.append(f"- {a.get('name')} {tag}  {desc}")
        lines.append("## 子 Agent（可被主 Agent 调用 + 可 @直接调用）")
        try:
            subs = _sub("agent_subagent").registered_subagents("")
            lines.append("内置：dispatch_sub_agents（批量并发）/ explore_project（了解项目）"
                         " / search_large（大规模搜索）")
            for s in subs:
                head = f"- sub_{s['name']}：{s['description'] or '(无说明)'}"
                if s.get("allowed"):
                    head += f"（工具: {', '.join(s['allowed'])}）"
                lines.append(head)
            if not subs:
                lines.append("当前工作流暂无自定义子 Agent（register_sub_agent 注册）。"
                             "要一个「能被主 Agent 调用的 agent」，用 register_sub_agent，"
                             "不要用 create_agent。")
            lines.append("主 Agent 可对每个子 Agent 逐次分配：shared_context=true/false（是否加入共同"
                         "上下文空间）+ context=<上下文>（把已读到的文件内容/结论交给它）；"
                         "注册式与临时子任务均支持。")
        except Exception:
            lines.append("（子 Agent 列表读取失败）")
        lines.append("（输入框 @agent 可会话级切换人格；@_default 切回内置默认）")
        return {"text": "\n".join(lines), "images": []}
    except Exception as e:
        return _blocked(f"[list_agents] {e}")


def _create_agent(args: dict) -> dict:
    try:
        ok, msg = _ag().save_agent(
            str(args.get("name", "")),
            str(args.get("system_prompt", "")),
            str(args.get("bound_workflow", "")))
        if not ok:
            return _blocked(msg)
        # 与 register_agent_network(op=create_agent) 同一纠偏：人格 Agent 不可被主 Agent 调用
        return {"text": msg + _CREATE_AGENT_HINT, "images": []}
    except Exception as e:
        return _blocked(f"[create_agent] {e}")


def _delete_agent(name: str) -> dict:
    try:
        ok, msg = _ag().delete_agent(name)
        return {"text": msg, "images": []} if ok else _blocked(msg)
    except Exception as e:
        return _blocked(f"[delete_agent] {e}")
