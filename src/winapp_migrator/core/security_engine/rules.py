"""规则引擎：加载外置规则库 security_rules.json，样本匹配返回命中规则与最高置信度。

规则库路径解析：
1. 内置默认: <项目根>/config/security_rules.json（打包后 _MEIPASS/config/security_rules.json）
2. 用户覆盖: %USERPROFILE%\\.winapp_migrator\\config\\security_rules.json（顶层键合并覆盖内置）
匹配器按类型注册式扩展（add_matcher），全部规则外置、零硬编码。
"""
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = __import__("winapp_migrator.utils.helpers", fromlist=["setup_logging"]).setup_logging()

_USER_RULES = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".winapp_migrator" / "config" / "security_rules.json"

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


# 模块级单例：供 deep_analyze / download_guard 等组合模块复用
rules_engine = RuleEngine()