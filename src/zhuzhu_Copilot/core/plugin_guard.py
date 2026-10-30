"""插件校验系统（内置唯一、不可替换的插件安全闸门）

定位：随应用分发的**核心程序**，不是插件。任何插件——包括 Copilot 插件的源码级
变更——在落盘 / 导入 / 应用源码变更之前，都必须通过本模块校验：
  · plugin.json 里没有任何字段可以跳过或替换校验（本模块不读此类开关）；
  · 源码级变更不得修改本文件与安全引擎（见 PROTECTED_CORE_FILES，
    由 plugin_source 在应用前强制检查，静态规则 guard_tamper 亦会命中）。

校验两道，全部真实执行：
  1) 静态规则扫描（离线、必然执行）：危险 API / 系统路径 / 持久化 /
     凭据窃取 / 混淆载荷 / 篡改校验程序等模式匹配；
  2) LLM 源码评审（深度判定）：把插件源码送给当前配置的 LLM 判 safe /
     dangerous。LLM 不可用（未配置/网络失败/解析失败）时降级为仅规则判定，
     并在结论中注明 llm_used=false——**绝不阻断插件链路静默放行**。

判定为危险的处置（强制，不询问）：
  · 危险文件**直接强制删除**；
  · 若 plugin.json 自身危险或删除后插件已无可用代码 → 整体移除插件目录；
  · 删除动作与原因追加写入校验日志（plugins/.guard_log.jsonl），并随返回值
    明确提示用户（哪个插件、哪些文件、哪条规则、LLM 结论）。

对外入口（Agent / Copilot 两套插件体系通用，按插件包目录校验）：
  guard_plugin_dir(pkg_dir, name)     校验已落盘插件包（导入 / AI 生成后调用）
  guard_source_package(pkg_dir, name) 校验 Copilot 插件源码级变更清单（应用前调用）
  guard_plugin(name)                  Agent 插件入口（按名寻址，转调 guard_plugin_dir）
  guard_blobs(blobs, ...)             通用内容校验（dict: 文件名 → 源码文本）
  recent_guard_events(limit)          最近校验事件（供 UI/提示展示）
"""

import json
import re
import threading
import time
from pathlib import Path

from zhuzhu_Copilot.core import agent_plugins

# ---------------------------------------------------------------------------
# 受保护的核心文件（相对应用源码根目录）：Copilot 插件源码级变更一律不得改动
# 校验系统自身、源码变更引擎与整条安全引擎都在此列——这是「唯一不可替换」的落点。
# ---------------------------------------------------------------------------
PROTECTED_CORE_FILES = (
    "zhuzhu_Copilot/core/plugin_guard.py",
    "zhuzhu_Copilot/core/plugin_source.py",
    "zhuzhu_Copilot/core/copilot_plugins.py",
    "zhuzhu_Copilot/core/hot_reload.py",
    "zhuzhu_Copilot/core/security.py",
    "zhuzhu_Copilot/core/security_engine/",
    # 覆盖层导入钩子与应用入口：源码插件改它们等于拆掉「打包态也能源码级定制」
    # 的地基（钩子被拆 → 覆盖层不再生效；入口被换 → 钩子根本不会安装），
    # 与校验系统同属「唯一不可替换」范畴。热加载引擎同理：被改就等于拆掉
    # 「任何插件无需重启即生效」的保证。
    "_copilot_overlay_hook.py",
    "main.py",
)

# 可扫描的文本文件后缀（图标等二进制跳过）
_SCAN_EXTS = {".py", ".md", ".json", ".txt", ".html", ".htm", ".js", ".css",
              ".lua", ".sh", ".bat", ".ps1", ".cfg", ".conf", ".yaml", ".yml"}

# 静态规则：(规则ID, 正则, 级别, 说明, 适用后缀或 None=全部)
#   danger = 直接判定危险（无需 LLM）；warn = 存疑，交 LLM 复审裁定
_RULES = [
    # 只拦「裸调用」eval(...) / exec(...)：Qt 的 dlg.exec()、os.execv 之类方法调用不算
    ("dynamic_exec", r"(?<![\.\w])(?:eval|exec)\s*\(", "danger",
     "动态执行代码（eval/exec）", None),
    ("import_hook", r"__import__\s*\(", "danger", "动态导入（__import__）", None),
    ("os_system", r"\bos\.system\s*\(", "danger", "直接执行系统命令（os.system）", None),
    ("shell_true", r"shell\s*=\s*True", "danger", "shell=True 子进程（可拼任意命令）", None),
    ("registry",
     r"winreg\.(?:SetValue\w*|CreateKey\w*|DeleteKey\w*|SaveKey)"
     r"|\breg(?:\.exe)?\s+add\b|\bReg(?:SetValue|DeleteKey|DeleteValue)\w*",
     "danger", "注册表写操作（持久化常用手法）", None),
    ("system_path",
     r"(?:rmtree|os\.remove|os\.unlink|shutil\.[a-z]+|del\s+/[Ff]|format\s+[A-Za-z]:)"
     r"[^\n]{0,120}(?:[A-Za-z]:\\?[Ww]indows|/etc/(?:passwd|shadow|sudoers))"
     r"|(?:[A-Za-z]:\\?[Ww]indows|/etc/(?:passwd|shadow|sudoers))[^\n]{0,120}"
     r"(?:rmtree|os\.remove|os\.unlink|del\s+/[Ff]|format\s+[A-Za-z]:)",
     "danger", "破坏操作系统关键路径", None),
    ("persistence", r"(?i)schtasks|CurrentVersion\\\\?Run|\\\\Startup\\\\|StartupApproved",
     "danger", "开机自启动/计划任务持久化", None),
    ("credential", r"(?i)Login Data|keylog|pynput|keyboard\.hook|GetKeyboardState\s*\(.*log",
     "danger", "凭据窃取/键盘记录", None),
    ("mass_delete", r"shutil\.rmtree|\bformat\s+[A-Za-z]:|\bdel\s+/[Ff]\b|rm\s+-rf\s+/(?![A-Za-z])",
     "danger", "大规模删除/格式化", None),
    ("defender_off", r"(?i)Set-MpPreference|netsh\s+advfirewall|DisableAntiSpyware",
     "danger", "关闭系统安全防护", None),
    ("guard_tamper", r"(?i)plugin_guard|plugin_source|copilot_plugins|hot_reload|security_engine|quarantine\.py|yara_engine|overlay_hook|source_overlay",
     "danger", "试图改动内置校验/安全程序", {".py"}),
    ("obfuscation", r"base64\.b64decode|codecs\.decode|bytes\.fromhex|marshal\.loads",
     "warn", "编码/混淆解码（可能是恶意载荷）", None),
    ("raw_socket", r"socket\.socket\s*\(", "warn", "原始网络套接字（数据外发可疑）", None),
    ("external_http", r"requests\.post|urlopen\s*\(\s*[\"']https?://(?!127\.|localhost)",
     "warn", "外部网络请求", None),
    ("ctypes_api", r"ctypes\.windll|ctypes\.WinDLL", "warn", "直接调用 Win32 API", None),
]

_COMPILED = [(rid, re.compile(pat), sev, desc, exts)
             for rid, pat, sev, desc, exts in _RULES]

#: 规则的廉价前置关键词（**任一**出现才需要跑对应正则）。
#: 纯性能优化，不改变判定：关键词都是该正则命中时必然出现的字面量（取其分支字面量
#: 的并集，任一命中即可继续用正则精确判定）。大型插件（1~2MB）静态扫描因此从
#: 秒级降到几十毫秒——大部分规则在普通代码里连关键词都不出现，直接被跳过。
_RULE_HINTS = {
    "dynamic_exec": ("eval", "exec"),
    "import_hook": ("__import__",),
    "os_system": ("os.system",),
    "shell_true": ("shell",),
    "registry": ("winreg", "reg.", "reg ", "regsetvalue", "regdeletekey",
                 "regdeletevalue", "regsavekey"),
    "system_path": ("windows", "etc/passwd", "etc/shadow", "etc/sudoers"),
    "persistence": ("schtasks", "currentversion", "startup", "startupapproved"),
    "credential": ("login data", "keylog", "pynput", "keyboard.hook",
                   "getkeyboardstate"),
    "mass_delete": ("rmtree", "format", "del ", "rm "),
    "defender_off": ("set-mppreference", "advfirewall", "disableantispyware"),
    "guard_tamper": ("plugin_guard", "plugin_source", "copilot_plugins", "hot_reload",
                     "security_engine", "quarantine", "yara_engine", "overlay_hook",
                     "source_overlay"),
    "obfuscation": ("b64decode", "codecs.decode", "fromhex", "marshal.loads"),
    "raw_socket": ("socket",),
    "external_http": ("requests.post", "urlopen"),
    "ctypes_api": ("ctypes",),
}

_LLM_TIMEOUT_S = 60.0        # 单次 LLM 源码评审超时（区别于生成类的长超时档）
_MAX_FILE_CHARS = 6000       # 单文件送审上限
_MAX_TOTAL_CHARS = 24000     # 整包送审上限

_LLM_SYSTEM = (
    "你是插件安全评审专家。审查给定插件源码，判定其是否包含危险行为："
    "窃取凭据/隐私数据、破坏系统、植入持久化、关闭安全软件、外发数据、"
    "混淆载荷、篡改校验程序等。仅输出一个 JSON 对象："
    '{"verdict":"safe|dangerous","reason":"简短中文理由（dangerous 时点名文件与行为）"}，'
    "不要输出其他内容。对正常的文件读写/工具实现应判 safe。"
)


def is_protected_path(rel_path: str) -> bool:
    """相对应用源码根目录的路径是否受保护（源码插件不得改动）。"""
    rel = str(rel_path or "").replace("\\", "/").lstrip("./")
    return any(rel == p.rstrip("/") or rel.startswith(p)
               for p in PROTECTED_CORE_FILES)


def _guard_log_path() -> Path:
    return agent_plugins.plugins_dir() / ".guard_log.jsonl"


def _log_event(entry: dict) -> None:
    """校验事件落盘（jsonl，追加）。失败静默——日志不能影响校验本身。"""
    try:
        entry = dict(entry)
        entry.setdefault("ts", time.strftime("%Y-%m-%d %H:%M:%S"))
        with _guard_log_path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def recent_guard_events(limit: int = 20) -> list:
    """最近校验事件（新的在前）。供 UI/提示展示「校验系统做过什么」。"""
    p = _guard_log_path()
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
        if len(out) >= limit:
            break
    return out


def _scannable_files(d: Path) -> list:
    """插件目录里可扫描的文本文件（跳过二进制/缓存/备份/校验日志）。"""
    out = []
    try:
        for f in sorted(d.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(d)
            if any(seg in ("__pycache__", "backups", "applied.json")
                   for seg in rel.parts):
                continue
            if f.name.startswith(".") or f.suffix.lower() not in _SCAN_EXTS:
                continue
            out.append(f)
    except OSError:
        pass
    return out


def static_scan(text: str, filename: str = "") -> list:
    """静态规则扫描，返回命中列表 [{rule, severity, desc, line, snippet}]。

    先按 `_RULE_HINTS` 做一次廉价关键词过滤（大小写不敏感），命不中关键词的规则
    根本不跑正则——这是大型插件（1~2MB）静态扫描的主要提速点。
    """
    findings = []
    text = text or ""
    ext = Path(filename).suffix.lower()
    low = text.lower()          # 只算一次（所有关键词都是小写字面量）
    lines = None
    for rid, rx, sev, desc, exts in _COMPILED:
        if exts and ext not in exts:
            continue
        hints = _RULE_HINTS.get(rid)
        if hints and not any(h in low for h in hints):
            continue
        for m in rx.finditer(text):
            line_no = text.count("\n", 0, m.start()) + 1
            if lines is None:
                lines = text.splitlines()   # 仅在真的有命中时才切行（大文件也便宜）
            ln = lines[line_no - 1] if 0 <= line_no - 1 < len(lines) else ""
            findings.append({"rule": rid, "severity": sev, "desc": desc,
                             "line": line_no, "snippet": ln.strip()[:120]})
    return findings


def _llm_review(blobs: dict) -> dict:
    """LLM 源码评审（可被测试注入替身）。任何失败返回 {}（调用方降级）。

    返回 {"verdict": "safe"|"dangerous", "reason": str}；不可用/失败返回 {}。
    """
    try:
        from zhuzhu_Copilot.core import agent_llm
        cfg = agent_llm.load_model_config()
        client = agent_llm.LLMClient(cfg.get("base_url"), cfg.get("api_key"),
                                     cfg.get("model") or agent_llm.DEFAULT_MODEL)
        budget = _MAX_TOTAL_CHARS
        parts = []
        for name, text in blobs.items():
            chunk = f"===== {name} =====\n{str(text)[:_MAX_FILE_CHARS]}"
            if budget - len(chunk) < 0:
                break
            parts.append(chunk)
            budget -= len(chunk)
        if not parts:
            return {}
        res = client.chat(
            [{"role": "system", "content": _LLM_SYSTEM},
             {"role": "user", "content": "\n\n".join(parts)}],
            max_tokens=400, timeout=_LLM_TIMEOUT_S)
        text = (res.get("text") or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
        obj = json.loads(text)
        verdict = str(obj.get("verdict") or "").lower()
        if verdict not in ("safe", "dangerous"):
            return {}
        return {"verdict": verdict, "reason": str(obj.get("reason") or "")[:300]}
    except Exception:
        return {}


def _findings_reason(findings: list) -> str:
    seen, parts = set(), []
    for f in findings:
        key = (f["rule"], f["line"])
        if key in seen:
            continue
        seen.add(key)
        parts.append(f"{f['desc']}（{f['rule']}，第 {f['line']} 行）")
    return "；".join(parts[:6]) + (" 等" if len(parts) > 6 else "")


def guard_blobs(blobs: dict, plugin: str = "", llm_review=None) -> tuple:
    """通用内容校验：blobs = {文件名: 源码文本}。

    返回 (ok, message, detail)。detail = {"dangerous_files": {名: 原因},
    "llm_used": bool, "llm": {...}, "static": {名: [findings]}}。
    先跑静态规则；有 danger 命中直接判危险，否则把全部内容交 LLM 复审。
    """
    review = llm_review or _llm_review
    static = {}
    dangerous = {}
    for name, text in (blobs or {}).items():
        findings = static_scan(text, name)
        static[name] = findings
        hard = [f for f in findings if f["severity"] == "danger"]
        if hard:
            dangerous[name] = "静态规则命中：" + _findings_reason(hard)
    llm, llm_used, llm_state = {}, False, "skipped"
    if not dangerous and blobs:
        # 无硬命中才需要 LLM 深审（有硬命中已可直接处置，省一次往返）
        try:
            llm = review({k: v for k, v in blobs.items()}) or {}
        except Exception:
            llm = {}
        llm_used = bool(llm)
        llm_state = "used" if llm_used else "unavailable"
        if llm.get("verdict") == "dangerous":
            for name in blobs:
                dangerous.setdefault(name, "LLM 源码评审判定危险："
                                    + str(llm.get("reason") or "未给出理由"))
    detail = {"dangerous_files": dangerous, "llm_used": llm_used,
              "llm": llm, "llm_state": llm_state, "static": static}
    if dangerous:
        files = "、".join(sorted(dangerous))
        why = "；".join(f"{k}: {v}" for k, v in sorted(dangerous.items()))
        msg = (f"插件校验系统拦截插件「{plugin or '(未命名)'}」："
               f"危险代码已强制删除并提示用户——{files}。原因：{why}")
        if llm_used:
            msg += f"。LLM 复审：{llm.get('reason') or '判定危险'}"
        elif llm_state == "skipped":
            msg += "（静态规则已直接判定危险，未送 LLM）"
        else:
            msg += "（本次 LLM 不可用，结论来自静态规则）"
        return False, msg, detail
    ok_msg = f"插件「{plugin or '(未命名)'}」通过插件校验系统"
    ok_msg += ("（静态规则 + LLM 源码评审）" if llm_used
               else "（静态规则；LLM 不可用未做深度评审）")
    return True, ok_msg, detail


def _force_remove(path: Path) -> bool:
    try:
        if path.is_file():
            path.unlink()
            return True
        if path.is_dir():
            import shutil
            shutil.rmtree(path, ignore_errors=True)
            return True
    except OSError:
        pass
    return False


#: 插件入口/核心文件：校验**不得删除**（删了插件直接不可用，用户会以为导入失败）。
#: 这些文件被判危险时改为「停用插件 + 明确告知」，由用户自己决定怎么处理。
_NON_DELETABLE_FILES = frozenset({
    "server.py", "SKILL.md", "plugin.json", "panel.py", "agent.py",
    "tools.py", "llm.py", "mcp.json", "requirements.txt", "config.json",
})


def _is_non_deletable(rel: str) -> bool:
    base = str(rel or "").replace("\\", "/").rsplit("/", 1)[-1]
    return base in _NON_DELETABLE_FILES


def _disable_plugin(d: Path) -> bool:
    """把插件标记为停用（不删文件），让危险插件无法继续运行。"""
    mf = d / "plugin.json"
    try:
        import json as _json
        meta = _json.loads(mf.read_text(encoding="utf-8")) if mf.is_file() else {}
        if not isinstance(meta, dict):
            meta = {}
        meta["enabled"] = False
        mf.write_text(_json.dumps(meta, ensure_ascii=False, indent=2),
                      encoding="utf-8")
        return True
    except (OSError, ValueError):
        return False


def _apply_verdict(pkg_dir, name: str, detail: dict) -> str:
    """按校验结论强制处置：危险文件删除；插件入口文件只停用不删除；
    无可信代码剩余时整体移除插件目录。返回给用户的提示语。"""
    d = Path(pkg_dir)
    dangerous = detail.get("dangerous_files") or {}
    removed, failed, kept = [], [], []
    for rel in sorted(dangerous):
        if _is_non_deletable(rel):
            # 入口文件：保留文件、停用插件（用户可自行检查/修复后再启用）
            kept.append(rel)
            continue
        if _force_remove(d / rel):
            removed.append(rel)
        else:
            failed.append(rel)
    if kept:
        _disable_plugin(d)
    # plugin.json 自身危险，或删除后已无任何**代码**（仅剩元数据/图标）→ 整体移除
    remaining = [f for f in _scannable_files(d) if f.name != "plugin.json"] \
        if d.is_dir() else []
    whole = (not d.is_dir()) or "plugin.json" in dangerous or not remaining
    removed_whole = False
    if whole and d.is_dir():
        removed_whole = _force_remove(d)
    _log_event({"plugin": name, "action": "force_delete",
                "removed_files": removed, "failed_files": failed,
                "whole_plugin_removed": removed_whole,
                "reasons": dangerous,
                "llm_used": detail.get("llm_used"),
                "llm_reason": (detail.get("llm") or {}).get("reason", "")})
    parts = [f"插件校验系统：插件「{name}」包含危险代码，已处置并提示用户。"]
    if removed:
        parts.append("已删除危险文件：" + "、".join(removed))
    if failed:
        parts.append("删除失败（请手动检查）：" + "、".join(failed))
    if kept:
        parts.append("入口文件已保留并**停用插件**（未删除文件，请检查后手动启用）："
                     + "、".join(kept))
    if removed_whole:
        parts.append("该插件已无可用代码，已整体移除。")
    parts.append("原因：" + "；".join(f"{k} → {v}" for k, v in sorted(dangerous.items())))
    parts.append(f"校验日志：{_guard_log_path()}")
    parts.append("插件校验系统为内置唯一程序，不可跳过、不可替换。")
    return "\n".join(parts)


def guard_plugin_dir(pkg_dir, name: str, llm_review=None) -> tuple:
    """校验已落盘的插件包（按包目录，Agent / Copilot 插件体系通用）。

    危险 → 强制删除危险文件（必要时整体移除插件）并写日志，返回 (False, 提示)。
    安全 → 返回 (True, 摘要)。无代码可扫（如纯配置插件）视为通过。
    """
    d = Path(pkg_dir)
    if not d.is_dir():
        return False, f"插件「{name}」不存在，无法校验"
    blobs = {}
    for f in _scannable_files(d):
        try:
            blobs[str(f.relative_to(d))] = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    if not blobs:
        return True, f"插件「{name}」无可扫描代码，校验通过"
    ok, msg, detail = guard_blobs(blobs, plugin=name, llm_review=llm_review)
    if not ok:
        return False, _apply_verdict(d, name, detail)
    _log_event({"plugin": name, "action": "pass",
                "llm_used": detail.get("llm_used"),
                "files": sorted(blobs)})
    return True, msg


def guard_source_package(pkg_dir, name: str, llm_review=None) -> tuple:
    """校验 Copilot 插件包的源码级变更（按包目录）：扫描 source_ops 里
    所有待写入/待修改的代码内容，以及插件包自带的辅助文件。

    在 apply 之前调用；危险 → 强制删除整个插件包并提示（Copilot 插件源码级变更的
    全部价值就是要落到转译层，任何危险内容都不可容忍）。

    同步版本：静态规则与 LLM 深审依次执行。apply 链路用 `SourceReview` 走并行版
    （LLM 网络往返与本地写入/编译重叠），本函数保留给「必须一次拿到结论」的调用方。
    """
    review = SourceReview(pkg_dir, name, llm_review=llm_review)
    blocked = review.blocked()
    return (False, blocked) if blocked else review.finish()


class SourceReview:
    """源码插件校验事务：静态规则同步判定，LLM 深审在后台线程并行。

    为什么要并行：LLM 往返是 1~3s 的网络耗时，而写入转译层 + 编译是纯本地 CPU 工作，
    两者互不依赖（LLM 只看待写入的内容，不看落盘结果），并行后总耗时 ≈ max(两者)，
    大型插件插入才可能稳定在 3s 内。

    安全性不变：LLM 结论出来之前**不会热加载**（不做任何 exec）；判危则调用方回滚
    已写入的变更、本对象删除整个插件包——危险代码永远不会被执行。
    """

    def __init__(self, pkg_dir, name: str, llm_review=None):
        self.pkg_dir = Path(pkg_dir)
        self.name = str(name)
        self.detail = {"dangerous_files": {}, "llm_used": False, "llm": {},
                       "llm_state": "skipped", "static": {}}
        self._review = llm_review or _llm_review
        self._llm = {}
        self._thread = None
        self.blobs = _source_package_blobs(self.pkg_dir)

    def blocked(self) -> str:
        """静态阶段：返回非空提示 = 已判危并处置（调用方立即返回，不写盘）。

        安全时**启动后台 LLM 评审**并返回空串，调用方可在评审期间写盘/编译。
        """
        if not self.pkg_dir.is_dir():
            return f"插件「{self.name}」不存在，无法校验"
        for rel, text in self.blobs.items():
            findings = static_scan(text, rel)
            self.detail["static"][rel] = findings
            hard = [f for f in findings if f["severity"] == "danger"]
            if hard:
                self.detail["dangerous_files"][rel] = "静态规则命中：" + _findings_reason(hard)
        if self.detail["dangerous_files"]:
            return self._reject()
        if self.blobs:
            self._thread = threading.Thread(target=self._run_llm,
                                            name="plugin-guard-llm", daemon=True)
            self._thread.start()
        return ""

    def _run_llm(self) -> None:
        try:
            self._llm = self._review(dict(self.blobs)) or {}
        except Exception:
            self._llm = {}

    def finish(self, timeout: float | None = None) -> tuple:
        """等 LLM 结论并处置（静态阶段已拒绝时不要调用）。返回 (ok, message)。"""
        if self._thread is not None:
            self._thread.join(timeout if timeout is not None else _LLM_TIMEOUT_S + 10)
        llm = self._llm if isinstance(self._llm, dict) else {}
        used = bool(llm)
        self.detail.update({"llm": llm, "llm_used": used,
                            "llm_state": "used" if used else
                            ("unavailable" if self.blobs else "skipped")})
        if llm.get("verdict") == "dangerous":
            for rel in self.blobs:
                self.detail["dangerous_files"].setdefault(
                    rel, "LLM 源码评审判定危险：" + str(llm.get("reason") or "未给出理由"))
        if self.detail["dangerous_files"]:
            return False, self._reject()
        _log_event({"plugin": self.name, "action": "pass_source",
                    "ops": len(self.blobs), "llm_used": used})
        msg = f"插件「{self.name}」通过插件校验系统"
        msg += ("（静态规则 + LLM 源码评审）" if used
                else "（静态规则；LLM 不可用未做深度评审）")
        return True, msg

    def _reject(self) -> str:
        """处置：整体删除插件包 + 写校验日志，返回给用户的提示语。"""
        dangerous = self.detail["dangerous_files"]
        removed = _force_remove(self.pkg_dir)
        _log_event({"plugin": self.name, "action": "force_delete_source_plugin",
                    "whole_plugin_removed": removed, "reasons": dangerous,
                    "llm_used": self.detail.get("llm_used"),
                    "llm_reason": (self.detail.get("llm") or {}).get("reason", "")})
        files = "、".join(sorted(dangerous))
        why = "；".join(f"{k}: {v}" for k, v in sorted(dangerous.items()))
        parts = [f"插件校验系统：源码插件「{self.name}」包含危险代码，"
                 "已强制删除整个插件包并提示用户。",
                 f"危险内容：{files}。原因：{why}"]
        if (self.detail.get("llm") or {}).get("verdict") == "dangerous":
            parts.append("LLM 复审：" + str(self.detail["llm"].get("reason") or "判定危险"))
        parts.append(f"校验日志：{_guard_log_path()}")
        parts.append("插件校验系统为内置唯一程序，不可跳过、不可替换。")
        return "\n".join(parts)


def _source_package_blobs(pkg_dir: Path) -> dict:
    """源码插件送审内容：source_ops 待写入代码 + 包内辅助文件（跳过元数据/清单）。"""
    from zhuzhu_Copilot.core import plugin_source
    blobs = {}
    try:
        ops = plugin_source.load_source_ops_from(pkg_dir)
    except Exception:
        ops = []
    for i, op in enumerate(ops):
        content = str(op.get("content") or "")
        if content.strip():
            blobs[f"source_ops[{i}].{op.get('op')}:{op.get('path') or ''}"] = content
    for f in _scannable_files(pkg_dir):
        rel = str(f.relative_to(pkg_dir))
        if rel in ("plugin.json", "source_ops.json"):
            continue
        try:
            blobs[rel] = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return blobs


def guard_plugin(name: str, llm_review=None) -> tuple:
    """（Agent 插件入口）校验已落盘插件（导入/AI 生成后、登记前调用）。"""
    return guard_plugin_dir(agent_plugins.plugin_dir(name), name,
                            llm_review=llm_review)
