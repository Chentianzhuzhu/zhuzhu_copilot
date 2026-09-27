"""Agent 沙盒与白名单：工具调用/命令执行前的安全评估

- 命令白名单：只读/诊断类命令与普通文件操作（新建/复制/移动/删除）默认放行
- 危险黑名单：递归/强制删除、格式化/关机/注册表/服务/权限等高风险操作直接拒绝
- 路径限制：读/写放行系统目录（允许安装/更新到 Program Files），仅禁止删除系统关键目录内的内容
- 评估结果分级：safe（放行）/ risky（需用户确认）/ dangerous（拒绝）
"""

import os
import re as _re
from pathlib import Path
from typing import Optional

# 白名单：只读/诊断类命令 + 普通文件操作（默认放行）
SAFE_COMMANDS = {
    "ping", "ipconfig", "tasklist", "netstat", "systeminfo", "whoami", "ver",
    "dir", "type", "echo", "hostname", "tree", "where", "findstr", "path",
    "set", "route", "arp", "nslookup", "tracert", "getmac",
    # 普通文件操作：新建/复制/移动/重命名/删除单文件或空目录
    "mkdir", "md", "copy", "move", "ren", "rename", "del", "erase", "rd", "rmdir",
    # curl：HTTP 请求/下载（管道/重定向等组合仍会降级为 risky 需确认）
    "curl",
    # git：常规提交/推送/拉取/查看（危险子命令由 DANGEROUS_KW 先行拒绝）
    "git",
}

# 危险关键词（拒绝）
DANGEROUS_KW = [
    "format", "diskpart", "bcdedit", "shutdown", "restart", "taskkill /f",
    "reg delete", "reg add", "sc delete", "net user", "net localgroup",
    "takeown", "icacls", "cacls", "attrib /s /d", "rd /s", "rmdir /s",
    "del /s", "del /f", "del /q", "rd /q", "rmdir /q",
    "move /y", "xcopy", "robocopy /e /purge",
    "powershell -enc", "powershell -e", "certutil -urlcache", "bitsadmin",
    "mshta", "wscript", "cscript", "vssadmin delete", "wmic process",
    # git 危险操作：丢弃改动 / 强制覆盖 / 强制删除 / 重写历史
    "git reset --hard", "git clean", "git push --force", "git push -f",
    "git push --delete", "git branch -d", "git checkout --", "git restore .",
    "git filter-branch",
]

# 系统关键目录：禁止写入/修改，防止破坏系统
def _system_dirs() -> list:
    drive = os.environ.get("SystemDrive", "C:")
    root = Path(drive + "\\")   # 盘符根（C:\），避免 "C:xxx" 相对路径
    windir = os.environ.get("WINDIR") or str(root / "Windows")
    cands = [
        root / "Windows", root / "Program Files", root / "Program Files (x86)",
        root / "ProgramData", root / "PerfLogs", root / "System Volume Information",
        Path(windir), Path(windir) / "System32",
    ]
    # 用户启动目录（持久化面）：把删除/覆盖启动项的威胁并入系统保护。
    # 常规 AppData 写入仍放行（属用户数据），仅锁定"开机自启"这类持续影响面。
    up = os.environ.get("USERPROFILE")
    if up:
        cands.append(Path(up) / "AppData" / "Roaming" / "Microsoft" / "Windows"
                     / "Start Menu" / "Programs" / "Startup")
    return [d for d in cands if d.exists()]


# 系统关键进程：禁止 taskkill（防止杀系统进程导致系统崩溃/锁屏/桌面重启）
_SYSTEM_PROCS = frozenset({
    "system", "system idle process", "registry", "smss", "csrss", "wininit",
    "winlogon", "services", "lsass", "lsm", "svchost", "dwm", "explorer",
    "conhost", "spoolsv", "taskhost", "taskhostw", "fontdrvhost",
    "searchindexer", "wmiprvse", "runtimebroker", "sihost",
    "shellexperiencehost", "textinputhost", "startmenuexperiencehost",
    "ctfmon", "audiodg", "securityhealthservice", "msmpeng",
})

def _path_tokens(low: str) -> list:
    """从命令行抽取"路径型" token（引号包裹，或含盘符/斜杠的裸 token）。
    仅用于删除/覆盖系统目录判定；纯命令/参数（-f、--verbose）不会被误抽。"""
    toks = [t.strip() for t in _re.findall(r'["\']([^"\']+)["\']', low)]
    for t in _re.findall(r'[^\s;&|<>"\']+', low):
        if "\"" in t or "'" in t:
            continue
        if (len(t) >= 2 and t[1:2] == ":" and t[0].isalpha()) or "\\" in t or "/" in t:
            toks.append(t.strip('"\''))
    return [t for t in toks if t]


def _cmd_targets_system(low: str) -> bool:
    """命令里是否含指向系统/启动关键目录的路径 token（含点-点反斜杠、短名、大小写混合）。
    逐 token 做 abspath/realpath/normcase 归一化后在系统目录下做包含判断，
    规避原始子串匹配被 'C:..\\Windows' 这类归一化路径逃过。"""
    sys_norm = [os.path.normcase(os.path.realpath(str(d))) for d in _system_dirs()]
    for t in _path_tokens(low):
        try:
            ab = os.path.abspath(os.path.expandvars(os.path.expanduser(t)))
            abr = os.path.normcase(os.path.realpath(ab))
        except Exception:
            continue
        for sd in sys_norm:
            try:
                Path(abr).relative_to(Path(sd))
                return True
            except ValueError:
                continue
    return False


# 卸载/清理流程可豁免的危险词：目标安全（非系统进程/非系统目录）时降级为 risky
# （ask/edit 弹确认、YOLO 放行），目标为系统对象时仍按 dangerous 硬拒绝。
# 注册表/服务删除（reg delete / sc delete）等高风险操作不豁免，引导走 uninstall_app 工具。
_EXEMPT_KW = frozenset({
    "taskkill /f", "rd /s", "rmdir /s", "del /s", "del /f", "del /q",
    "rd /q", "rmdir /q", "move /y",
})

# PowerShell 原生破坏性 cmdlet（执行端为 powershell.exe -Command，需与执行语言一致）：
# 这些不在 cmd 语法的 DANGEROUS_KW 中，若不单独命中会在 YOLO 下直行 → 破坏系统/持久化。
# 统一判为 dangerous 硬拒绝（不豁免；卸载/清理请走专用 uninstall_app 工具）。
PS_DANGEROUS_KW = [
    "remove-item", "rm -recurse", "rmdir", "rd -recurse", "del -recurse",
    "clear-content", "format-volume", "format-disk", "initialize-disk",
    "set-disk", "new-partition", "remove-partition",
    "stop-process", "kill ", "taskkill", "stop-service", "set-service",
    "restart-service", "new-service", "remove-service",
    "set-itemproperty", "remove-itemproperty", "new-itemproperty",
    "set-acl", "set-owner", "takeown", "icacls",
    "invoke-expression", "iex ", "start-process", "start-job",
    "new-item", "new-symlink", "cmd /c", "cmd /k", "wsl ",
    "bcdedit", "diskpart", "format", "assign-driveletter",
]


def _exempt_uninstall_op(low: str, kw: str) -> bool:
    """豁免判断：taskkill /f 目标必须是非系统进程；
    强制/递归删除与覆盖移动（rd /s、del /f、move /y 等）目标必须不含系统关键目录。"""
    if kw == "taskkill /f":
        m = _re.search(r"/im\s+([\w\-.]+)", low)
        proc = m.group(1).lower().rstrip(".exe") if m else ""
        return bool(proc and proc not in _SYSTEM_PROCS)
    # 目标含系统/启动关键目录（含 `..\` 归一化路径）→ 不豁免；否则可降级为 risky
    return not _cmd_targets_system(low)


def to_int(v) -> int:
    """健壮数值转换：容忍 LLM 返回的 '16, 980' 等字符串，取第一个数字"""
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip().replace(",", " ").replace("，", " ").replace("px", "")
    for tok in s.split():
        try:
            return int(float(tok))
        except ValueError:
            continue
    return 0


def _custom_safe() -> set:
    """用户自定义 bash 白名单命令（settings.json custom_safe_commands，追加放行）"""
    try:
        from winapp_migrator.core import agent_skills
        s = agent_skills.load_settings()
        return {str(c).strip().lower() for c in (s.get("custom_safe_commands") or [])
                if str(c).strip()}
    except Exception:
        return set()


def cmd_in_custom_safe(cmd: str) -> bool:
    """命令是否命中自定义白名单（免确认直接放行）：
    支持「整条精确匹配」与「前缀整词匹配」两种形态：
    - 精确：白名单项与命令完全一致（空格+回车加入白名单的完整命令；大小写与首尾空格容忍）
    - 前缀：命令以白名单项开头且随后是空格边界 —— 如白名单写 `git push`，命中
      `git push origin main`；写 `pip install`，命中 `pip install numpy`。
    安全边界（防绕过）：
    - 前缀按完整参数词切分（`p + " "`），避免 `pip` 误配 `pipx install`
    - 仅「多 token 白名单项」（含子命令/参数，如 `git push`、`pip install`）启用前缀匹配；
      单 token 项（如 `python`、`node`）仍只精确整条匹配，避免 `python -c "...任意代码..."`
      被首词白名单放行
    - 含管道/重定向/多命令的命令仍由 assess_command 先行降级为 risky，不会因前缀放行。"""
    low = (cmd or "").strip().lower()
    if not low:
        return False
    custom_safe = _custom_safe()
    if not custom_safe:
        return False
    for p in custom_safe:
        p = p.rstrip()
        if not p:
            continue
        if low == p:
            return True
        # 仅多 token 白名单项放开前缀匹配（单 token 项精确匹配即可，避免注入绕过）
        if " " in p and low.startswith(p + " "):
            return True
    return False


def disabled_tools() -> frozenset:
    """用户手动禁用的工具名集合（settings.json disabled_tools，统一小写）。
    支持逗号/空格/换行分隔的字符串，或列表/元组/集合。引擎在 schema 与执行层
    双重剔除：即使提示词干预诱导调用，也会被 _execute 硬拦截，不会真正执行。"""
    try:
        from winapp_migrator.core import agent_skills
        v = agent_skills.load_settings().get("disabled_tools")
    except Exception:
        return frozenset()
    if isinstance(v, (list, tuple, set)):
        items = [str(x) for x in v]
    elif isinstance(v, (int, float)):
        items = [str(v)]
    else:
        items = _re.split(r"[\s,，;；]+", str(v or ""))
    return frozenset(x.strip().lower() for x in items if str(x).strip())


def tools_disabled_all() -> bool:
    """是否禁用全部工具调用（settings.json disable_all_tools）：开启后 AI 只能纯对话。"""
    try:
        from winapp_migrator.core import agent_skills
        v = agent_skills.load_settings().get("disable_all_tools")
    except Exception:
        v = ""
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _assess_powershell(low: str, low_nourl: str) -> Optional[tuple]:
    """执行端为 powershell.exe -Command，需对 PowerShell 原生破坏性语法单独评估
    （cmd 语法的 DANGEROUS_KW 覆盖不到）。命中即返回 (level, reason)，否则 None。

    - 任意脚本执行原语（iex/invoke-expression/start-process/start-job/...）→ dangerous
    - 强破坏/系统持久化（format/服务/注册表/磁盘/ACL/cmd 包裹）→ dangerous
    - 目标相对安全的删除/清理（remove-item 非系统路径）→ risky（需确认）
    - 脚本块（$()、反引号、管道串联解释器）→ dangerous（可能就是任意代码执行）
    """
    # 脚本块/命令替换/反引号插值：往往是解释型代码注入面，一律硬拒
    if ("$(" in low) or ("`" in low) or _re.search(r"\b(iex|invoke-expression)\b", low):
        return "dangerous", "PowerShell 脚本块/命令替换/求值，禁止执行"
    # 硬危险：格式化/磁盘/服务/注册表/ACL/计划/下载执行等
    for kw in (
        "format-volume", "format-disk", "initialize-disk", "new-partition",
        "remove-partition", "new-service", "remove-service", "set-service",
        "set-itemproperty", "remove-itemproperty", "new-itemproperty",
        "set-acl", "takeown", "icacls", "start-process", "start-job",
        "new-symlink", "cmd /c", "cmd /k", "wsl ", "bcdedit", "diskpart",
    ):
        if kw in low:
            return "dangerous", f"PowerShell 高危操作: {kw}"
    # 目标安全可清理的删除/进程操作：目标含系统目录/系统进程 → dangerous，否则 risky 需确认
    is_delete = any(w in low for w in (
        "remove-item", "rm -recurse", "rd -recurse", "del -recurse", "clear-content"))
    is_proc = _re.search(r"\b(stop-process|kill|taskkill)\b", low)
    if is_delete:
        if _cmd_targets_system(low):
            return "dangerous", "禁止删除系统关键目录（PowerShell，含归一化路径）"
        return "risky", "PowerShell 删除/清理命令，需确认"
    if is_proc:
        # 抽取实际进程名：兼容 -Name/-ProcessName 取值、/im、`进程名.exe`、裸名。
        # 旧实现把 `-Name` 误当进程名，导致 stop-process -Name explorer 这类系统进程
        # 操作被降级为 risky（可确认放行）；应正确解析取值，系统进程判定为 dangerous。
        m = _re.search(
            r"(?:-(?:process)?name\s+[\"']?([\w\-.$]+)"
            r"|\/(?:im|pid)\s+([\w\-.$]+)"
            r"|\b(?:stop-process|kill|taskkill)\b\s+([\w\-.$]+\.exe|\w+))",
            low, _re.I)
        # m 可能为 None：命令含进程关键词但未匹配到可解析的进程名格式
        # （如 stop-process $p、带空格路径等）→ 无法识别目标 → 按"需确认"保守处理
        proc = (m.group(1) or m.group(2) or m.group(3) or "").lower().rstrip(".exe") if m else ""
        if proc and proc not in _SYSTEM_PROCS and proc != "powershell":
            return "risky", "PowerShell 进程操作，需确认"
        return "dangerous", f"禁止操作系统关键进程（PowerShell）"
    return None


def assess_command(cmd: str) -> tuple:
    """评估命令。返回 (level, reason)，level ∈ safe/risky/dangerous"""
    cmd = (cmd or "").strip()
    low = cmd.lower()
    if not low:
        return "risky", "空命令"
    first_tok = low.split()[0]
    del_base = os.path.basename(first_tok.replace("\\", "/"))
    is_curl = del_base == "curl"
    # 剥离 URL token：URL 只是访问目标，避免 format/shutdown 等词及 & 查询参数误伤
    # （如 curl https://x/api/format-report、curl "…?a=1&b=2" 均属正常请求）
    low_nourl = _re.sub(r'https?://[^\s"\'<>]+', "URL", low)
    # 执行端为 PowerShell：先做 PowerShell 原生危险语法评估（cmd 词表覆盖不到）
    _ps = _assess_powershell(low, low_nourl)
    if _ps is not None:
        return _ps
    if is_curl:
        # curl 的 URL/请求头/表单数据（-d/-H/--data-urlencode 等）里的任意字符串
        # 不构成当地危险操作（curl 无删除/格式化能力），跳过危险词子串匹配；
        # 危险面仅在：管道执行、写入系统目录
        if _re.search(r"\|\s*(sh|bash|cmd(\.exe)?|powershell|pwsh|python(3)?|perl|ruby)\b",
                      low_nourl):
            return "dangerous", "禁止 curl 管道执行（下载即执行高危模式）"
    else:
        # 危险优先；卸载/清理类命令（杀应用进程、删非系统目录）在目标安全时
        # 降级为 risky（ask/edit 弹确认、YOLO 放行），其余危险词硬拒绝
        for kw in DANGEROUS_KW:
            if kw in low_nourl:
                if kw in _EXEMPT_KW and _exempt_uninstall_op(low, kw):
                    return "risky", f"卸载/清理类命令: {kw}（目标非系统，需确认）"
                return "dangerous", f"命令含危险操作: {kw}"
    # 删除命令 + 目标位于系统/启动关键目录 → 拒绝（含 `..\`、短名、大小写等归一化路径）
    if del_base in ("del", "erase", "rd", "rmdir", "rm", "deltree"):
        if _cmd_targets_system(low):
            return "dangerous", "禁止删除系统关键目录（含归一化路径）"
    # curl 下载到系统关键目录 → 拒绝（防止覆盖系统文件）
    if is_curl:
        if _cmd_targets_system(low):
            return "dangerous", "禁止写入系统关键目录（含归一化路径）"
    # 含管道/重定向/多命令串联 → risky：即使首命令在白名单
    # （如 `git add .; git commit`、`echo hi > file`），避免绕过单命令白名单
    if any(s in low_nourl for s in ("|", ">", "&", "&&", ";")):
        return "risky", "命令含管道/重定向/多命令，非单条白名单命令"
    base = del_base
    if base in SAFE_COMMANDS or cmd_in_custom_safe(cmd):
        return "safe", ""
    return "risky", "非白名单命令"


def assess_path(path: str, operation: str = "write") -> tuple:
    """评估文件路径（operation ∈ read/write/delete）。

    读/写放行系统目录：安装/更新到 Program Files、读取系统配置等属正常需求，
    不再一刀切拒绝（YOLO/确认模式均可执行）。
    删除（delete）系统关键目录内的内容仍一律拒绝，守住安全底线。
    采用 realpath + normcase 判定，规避 junction/符号链接/短路径(8.3)/大小写混合绕过。
    返回 (level, reason)
    """
    try:
        abs_p = os.path.abspath(os.path.expandvars(os.path.expanduser(path)))
        p = Path(os.path.normcase(os.path.realpath(abs_p)))
    except Exception:
        return "dangerous", "路径解析失败"
    if operation == "delete":
        for d in _system_dirs():
            try:
                p.relative_to(Path(os.path.normcase(os.path.realpath(d))))
                return "dangerous", f"系统关键目录禁止删除: {d}"
            except ValueError:
                continue
    return "safe", ""


def assess_tool(name: str, args: dict) -> tuple:
    """工具级评估：命令/路径分级。返回 (level, reason)"""
    if name == "run_command":
        return assess_command(str(args.get("command", "")))
    # 重量级操作：卸载/迁移/内存优化涉及删改系统与应用，需用户确认
    if name in ("uninstall_app", "migrate_app", "optimize_memory"):
        return "risky", f"{name} 为重量级操作，需用户确认"
    # 对象级工具：文件/网络/下载/浏览器/TTS 上传均有真实改删/外发风险，按对象评估，
    # 避免仅 run_command 被门控而其它高操作面工具裸放行（木桶效应）。
    path_tools = {
        "write_file": "write", "edit_file": "write", "search_replace": "write",
        "insert_lines": "write", "delete_file": "delete",
        "create_docx": "write", "create_pptx": "write",
        "create_xlsx": "write", "new_project": "write", "generate_image": "write",
        "fast_download": "write",
        # 办公文档读取（只读，但涉及磁盘路径访问）/ 编辑（真实改写文件）
        "read_docx": "read", "read_pptx": "read", "read_xlsx": "read", "read_pdf": "read",
        "edit_docx": "write", "edit_pptx": "write", "edit_xlsx": "write",
    }
    if name in path_tools:
        return assess_path(str(args.get("path") or args.get("dest_dir") or ""),
                            path_tools[name])
    # 网络/浏览器/TTS 上传：可访问内网/元数据、持久化登录态执行 JS、外发本地文件 →
    # 一律需要用户确认，避免被当作 safe 免确认执行
    # 注意把 browser_open/click/type 与 navigate/eval 一起纳入 risky：click 点链接跳转、
    # type 填表单提交，效果与 navigate 等价（可无确认访问任意站点/提交数据）；open 会
    # 复用持久 profile 恢复登录态。只读快照/取 DOM（snapshot/html）保持 safe 以免过度打扰。
    if name in ("web_fetch", "web_search",
                "browser_open", "browser_navigate", "browser_click",
                "browser_type", "browser_eval"):
        return ("risky", f"{name} 涉及网络/浏览器操作，需用户确认")
    if name == "tts_create_voice":
        return ("risky", "tts_create_voice 会上传本地音频到三方接口，需确认源文件")
    # 工作流/插件核心代码：创建/编辑/删除/切换会写入引擎进程内执行的可执行 Python /
    # 原生代码（agent.py、tools.py、llm.py、MCP server 脚本）。此类自定义代码在进程内
    # 运行，可完全绕开内置沙盒（assess_command/白名单/路径保护），属高信任写操作，
    # 必须由用户显式确认，禁止 AI 无确认静默注入。
    if name in ("create_workflow", "delete_workflow", "edit_agent_file", "switch_workflow",
                "use_workflow_agent",
                "create_plugin", "create_plugin_from_nl",
                "manage_uiux"):
        return ("risky", f"{name} 涉及工作流/插件/UI/UX 核心代码写入或加载，需用户确认")
    return "safe", ""
