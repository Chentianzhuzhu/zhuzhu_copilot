"""统一深度分析：规则引擎 + YARA + 可选 LLM 二次判定，覆盖 文件 / 进程 / 脚本。

分析链路（样本 → 判定 verdict）：
1. 文件静态特征（file_intel）生成样本；进程/脚本样本由调用方构造
2. 规则引擎（rules_engine）按类别匹配，取最高置信度；YARA 命中即记入命中信号
3. 判定策略（阈值来自安全配置 action_policy）：
   - 置信度 ≥ high        → malicious（无需 LLM）
   - mid ≤ 置信度 < high  → suspicious → 低置信样本交 LLM 二次判定（可升级/降级）
   - 其他 + 签名可信厂商   → clean
4. LLM 不可用/未配置/请求失败 → 保持规则判定结果，绝不阻断防护链路
5. 同一文件按 sha256 缓存 LLM 结论，避免重复请求；单轮调度 callback 由上层控制

verdict 归一化为 malicious / suspicious / clean，供下载防护 / 进程防护等模块消费。
"""
import os
from typing import Dict, List, Optional

from zhuzhu_Copilot.core.security_engine import file_intel, llm_analyzer, signature
from zhuzhu_Copilot.core.security_engine.rules import rules_engine
from zhuzhu_Copilot.core.security_engine.yara_engine import yara_engine


class DeepAnalyzer:
    """组合判定器：规则 + YARA + LLM 的流水线"""

    def __init__(self, rules=None, yara=None):
        self._rules = rules or rules_engine
        self._yara = yara or yara_engine
        self._llm_cache: Dict[str, dict] = {}   # sha256 -> LLM 判定（缓存避免重复计费）
        self._llm_calls = 0                     # 单轮扫描 LLM 调用计数（end_scan 重置）

    # ---------- 阈值 ----------
    @property
    def _high(self) -> int:
        return int(self._rules.policy.get("high") or 80)

    @property
    def _mid(self) -> int:
        return int(self._rules.policy.get("mid") or 40)

    # ---------- 采样 ----------
    def sample_file(self, path: str) -> dict:
        """文件 → 规则样本（含静态特征）"""
        return file_intel.analyze_file(path)

    def sample_process(self, proc: dict) -> dict:
        """进程信息（pid/name/path）→ 规则样本；可执行文件缺失时只保留表层信息"""
        sample = {
            "kind": "process", "pid": proc.get("pid"),
            "name": proc.get("name") or os.path.basename(str(proc.get("path") or "")),
            "path": str(proc.get("path") or ""),
            "ext": os.path.splitext(str(proc.get("path") or ""))[1].lower(),
            "size": 0, "sha256": "", "pe": False,
            "sections": [], "imports": [], "entropy": 0.0, "strings": "",
        }
        p = str(proc.get("path") or "")
        if os.path.isfile(p):
            sample.update({k: v for k, v in file_intel.analyze_file(p).items()
                           if k not in ("kind", "path", "name")})
        return sample

    def sample_script(self, script: str, path: str = "", name: str = "") -> dict:
        """脚本文本 → 规则样本（category=script）"""
        return {
            "kind": "script", "script": (script or "")[:65536],
            "path": str(path or ""), "name": str(name or os.path.basename(str(path))),
            "ext": os.path.splitext(str(path))[1].lower() if path else "",
            "size": len(script or ""), "sha256": file_intel.sha256_of(str(path)) if path else "",
        }

    # ---------- 判定入口 ----------
    def analyze(self, sample: dict, category: str = None,
                extra_signals: List[str] = None) -> dict:
        """统一判定：返回 {'verdict','confidence','signals':[命中信号...],'reason'}"""
        signals = list(extra_signals or [])
        hits = self._rules.match(sample, category)
        conf = max((int(r.get("confidence") or 0) for r, _ in hits), default=0)
        signals = sorted({*(s.lower() for s in signals),
                          *(str(r.get("id") or "").lower() for r, _ in hits)})

        # 签名可信厂商 → 直接 clean（跳过 LLM，避免误报与浪费）
        p = str(sample.get("path") or "")
        signer = ""
        if p and os.path.isfile(p) and sample.get("pe"):
            signer = signature.signer_subject(p).lower()
        trusted = [str(x).lower() for x in self._rules.data.get("trusted_signers") or []]
        if signer and any(x in signer for x in trusted):
            return {"verdict": "clean", "confidence": 0, "signals": signals,
                    "reason": f"签名厂商可信：{signer}"}

        if conf >= self._high or self._yara_hit(sample, signals):
            verdict, reason = "malicious", "规则高置信命中" if conf >= self._high else "YARA 命中"
            return {"verdict": verdict, "confidence": conf, "signals": signals,
                    "reason": reason + (" / " + "，".join(signals[:3]) if signals else "")}

        # 低置信样本：LLM 二次判定（缓存 + 失败降级）
        if conf >= self._mid and not self._llm_degrade(sample):
            llm = self._llm_refine(sample)
            if llm and llm.get("verdict") in ("malicious", "clean"):
                rev = "malicious" if llm["verdict"] == "malicious" else "suspicious"
                return {"verdict": rev, "confidence": conf, "signals": signals,
                        "reason": f"LLM 判定 {llm['verdict']}（{llm.get('reason') or ''}）"}
        if conf >= self._mid:
            return {"verdict": "suspicious", "confidence": conf, "signals": signals,
                    "reason": "规则低置信命中，未触发 LLM 或 LLM 未给出明确结论"}
        return {"verdict": "clean", "confidence": conf, "signals": signals,
                "reason": "未命中高置信规则"}

    def analyze_file(self, path: str) -> dict:
        return self.analyze(self.sample_file(path), category="file")

    def analyze_process(self, proc: dict) -> dict:
        return self.analyze(self.sample_process(proc), category="file",
                            extra_signals=[f"pid:{proc.get('pid')}"])

    def analyze_script(self, script: str, path: str = "", name: str = "") -> dict:
        return self.analyze(self.sample_script(script, path, name), category="script")

    # ---------- 内部 ----------
    def end_scan(self) -> None:
        """一轮扫描结束：重置 LLM 调用预算（供下载目录等批量扫描调用）"""
        self._llm_calls = 0

    @property
    def _llm_budget(self) -> int:
        """一轮扫描内 LLM 调用上限（配置 llm.max_calls_per_scan，防批量文件触发过高 API 费用）"""
        from zhuzhu_Copilot.core.security_engine.config import config
        return max(0, int(config.get("llm.max_calls_per_scan", 10)))

    @staticmethod
    def _yara_hit(sample: dict, signals: list) -> bool:
        p = str(sample.get("path") or "")
        if not p:
            return False
        names = yara_engine.match_file(p)
        for n in names:
            if n not in signals:
                signals.append(n)
        return bool(names)

    @staticmethod
    def _llm_degrade(sample: dict) -> bool:
        """LLM 配置缺失/显式禁用 → 返回 True 表示降级（不打 LLM）"""
        cfg = llm_analyzer.resolve_cfg()
        return not cfg or not cfg.get("enabled")

    def _llm_refine(self, sample: dict) -> Optional[dict]:
        h = str(sample.get("sha256") or "")
        if h and h in self._llm_cache:
            return self._llm_cache[h]
        if self._llm_calls >= self._llm_budget:
            return None   # 单轮扫描 LLM 调用预算已用尽，降级为规则判定
        cfg = llm_analyzer.resolve_cfg()
        if not cfg or not cfg.get("enabled"):
            return None
        self._llm_calls += 1
        res = llm_analyzer.analyze(sample, cfg)
        if res:
            if h:
                if len(self._llm_cache) > 512:   # 防缓存无限膨胀
                    self._llm_cache.clear()
                self._llm_cache[h] = res
        return res


deep_analyzer = DeepAnalyzer()