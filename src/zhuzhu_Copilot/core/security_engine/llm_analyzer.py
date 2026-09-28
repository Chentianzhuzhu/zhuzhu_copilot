"""可选 LLM 深度分析：低置信度样本二次判定（OpenAI 兼容 /chat/completions，urllib 真实请求）

配置来源（安全配置 llm 段 → 应用主 LLM 配置兜底）：
1. security.json 的 llm 段显式配置 {enabled, base_url, api_key, model, timeout_s}
2. 未显式启用/缺配置时，回退读取应用主 LLM 连接（agent_llm.load_model_config）的当前服务商
未启用/配置不全/网络失败/解析失败 → 返回 None（调用方回退规则判定），绝不阻断防护链路。
"""
import json
import re
import urllib.error
import urllib.request
from typing import Optional

_SYSTEM_PROMPT = ("你是恶意软件分析助手。根据样本摘要判定恶意程度。"
                  '只输出一个JSON对象：{"verdict":"malicious|suspicious|clean",'
                  '"confidence":0-100,"reason":"简短理由"}。不要输出其他内容。')


def resolve_cfg() -> Optional[dict]:
    """解析安全引擎 LLM 配置：显式配置优先，否则回退应用主 LLM（返回值可直接喂 analyze）。
    显式 enabled=false 时尊重用户关闭选择（不启用）；任何解析失败都返回 None（调用方降级规则判定）。"""
    from zhuzhu_Copilot.core.security_engine.config import config
    cfg = dict(config.get("llm") or {})
    if cfg.get("enabled") is False:
        return None
    if cfg.get("enabled") and (cfg.get("base_url") and cfg.get("model")):
        return cfg
    # 未显式配置：回退应用主 LLM 连接（用户在设置页已配置的服务商）
    try:
        from zhuzhu_Copilot.core import agent_llm
        m = agent_llm.load_model_config() or {}
        if m.get("base_url") and m.get("model"):
            return {
                "enabled": True,
                "base_url": str(m.get("base_url") or ""),
                "api_key": str(m.get("api_key") or ""),
                "model": str(m.get("model") or ""),
                "timeout_s": float(cfg.get("timeout_s") or 15.0),
                "source": "app",
            }
    except Exception:
        return None
    return None


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