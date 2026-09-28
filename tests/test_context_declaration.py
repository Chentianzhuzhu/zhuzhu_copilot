"""上游服务商声明（provider declaration）回归测试。

覆盖：
- /models 声明的多字段兼容解析（OpenAI context_length / OpenRouter top_provider /
  vLLM max_model_len / max_input_tokens 等）与噪声守卫；
- 内置已知服务商表兜底（离线）；
- 窗口决策优先级链：1M 开关 > 上游声明 > 服务商手填 > 内置已知表 > 模型名推断；
- 真实 HTTP 端到端：本地起一个 /models 服务，用真实 urllib 请求验证声明确实被读回
  （不使用任何网络打桩）；
- load_model_config 集成：1M 开关与声明落到 context / context_window。
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from zhuzhu_Copilot.core import agent_llm, agent_skills


# ---------- 1. 声明解析 ----------

def test_declared_context_field_compat():
    """同一份声明在不同服务商有不同字段名，必须都能归一化到 context_window/max_output"""
    assert agent_llm.declared_context_from_item(
        {"id": "a", "context_length": 200000, "max_output_tokens": 64000}
    ) == {"context_window": 200000, "max_output_tokens": 64000}
    assert agent_llm.declared_context_from_item(
        {"id": "b", "top_provider": {"context_length": 1000000,
                                     "max_completion_tokens": 32000}}
    ) == {"context_window": 1000000, "max_output_tokens": 32000}
    assert agent_llm.declared_context_from_item(
        {"id": "c", "max_model_len": 32768}
    ) == {"context_window": 32768, "max_output_tokens": 0}
    assert agent_llm.declared_context_from_item(
        {"id": "d", "max_input_tokens": "65536", "max_output_tokens": "8192"}
    ) == {"context_window": 65536, "max_output_tokens": 8192}


def test_declared_context_guards_noise():
    """噪声守卫：窗口过小、输出过小或 ≥ 窗口、非法值一律视为"未声明"（不猜）"""
    assert agent_llm.declared_context_from_item({"context_length": 100}) == \
        {"context_window": 0, "max_output_tokens": 0}
    assert agent_llm.declared_context_from_item({"context_length": 8192,
                                                 "max_output_tokens": 8192}) == \
        {"context_window": 8192, "max_output_tokens": 0}
    assert agent_llm.declared_context_from_item({"context_length": "abc"}) == \
        {"context_window": 0, "max_output_tokens": 0}
    assert agent_llm.declared_context_from_item(None) == \
        {"context_window": 0, "max_output_tokens": 0}


def test_known_provider_declaration_matching():
    """内置已知表：地址 + 模型名双条件命中才采信；未命中返回空（交给模型名推断）"""
    deepseek = agent_llm.known_context_declaration("https://api.deepseek.com/v1",
                                                   "deepseek-chat")
    assert deepseek["context_window"] == 131072 and deepseek["source"] == "known"
    claude = agent_llm.known_context_declaration("https://api.anthropic.com",
                                                 "claude-sonnet-4")
    assert claude["context_window"] == 200000 and claude["max_output_tokens"] == 64000
    assert agent_llm.known_context_declaration("https://my-own-host.local", "mystery") == {}


# ---------- 2. 窗口决策优先级链 ----------

def test_resolve_context_priority_chain():
    """1M 开关 > 上游声明 > 服务商手填 > 内置已知表 > 模型名推断"""
    declared_provider = {"context_window": 64000,
                         "model_limits": {"m1": {"context_window": 400000,
                                                 "max_output_tokens": 16000}}}
    got = agent_llm.resolve_context("https://x.example", "m1", declared_provider)
    assert (got["window"], got["max_output"], got["source"]) == (400000, 16000, "upstream")

    got = agent_llm.resolve_context("https://x.example", "m1", {"context_window": 64000})
    assert (got["window"], got["source"]) == (64000, "configured")

    got = agent_llm.resolve_context("https://api.deepseek.com/v1", "deepseek-chat", {})
    assert (got["window"], got["source"]) == (131072, "known")

    got = agent_llm.resolve_context("https://unknown.example", "some-256k-model", {})
    assert (got["window"], got["source"]) == (262144, "inferred")


def test_resolve_context_1m_overrides_everything():
    """1M 开关开启后一律 1,048,576，覆盖上游声明/手填/已知表/推断"""
    provider = {"context_window": 64000,
                "model_limits": {"declared-1m": {"context_window": 1_000_000}}}
    got = agent_llm.resolve_context("https://api.deepseek.com/v1", "declared-1m",
                                    provider, long_1m=True)
    assert got["window"] == agent_llm.ONE_M_CONTEXT == 1048576
    assert got["source"] == "1m" and got["long_1m"] is True and got["max_output"] > 0


# ---------- 3. 真实 HTTP 端到端 ----------

def _serve_models(payload: dict):
    """起一个真实本地 /models 服务（ThreadingHTTPServer），返回 (base_url, server)"""

    class _H(BaseHTTPRequestHandler):
        def do_GET(self):   # noqa: N802  (BaseHTTPRequestHandler 约定)
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):   # 静默访问日志
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}", srv


MODELS_PAYLOAD = {
    "object": "list",
    "data": [
        {"id": "declared-200k", "context_length": 200000, "max_output_tokens": 64000},
        {"id": "openrouter-style",
         "top_provider": {"context_length": 1000000, "max_completion_tokens": 32000}},
        {"id": "vllm-style", "max_model_len": 32768},
        {"id": "vision-model", "context_length": 131072,
         "input_modalities": ["text", "image"]},
        {"id": "no-declaration"},
    ],
}


def test_fetch_provider_models_reads_declarations_over_http():
    """真实请求 /models：模型列表、多模态识别与上下文声明都要读回（声明为上游权威来源）"""
    base, srv = _serve_models(MODELS_PAYLOAD)
    try:
        ok, models, source, vision, declared = agent_llm.fetch_provider_models(base)
    finally:
        srv.shutdown()
        srv.server_close()
    assert ok is True and source == "upstream"
    assert models == ["declared-200k", "openrouter-style", "vllm-style",
                      "vision-model", "no-declaration"]
    assert "vision-model" in vision
    # 声明只收集真正声明了能力的模型（未声明的模型不入表，避免覆盖既有声明）
    assert "no-declaration" not in declared
    assert declared["declared-200k"] == {"context_window": 200000,
                                        "max_output_tokens": 64000}
    assert declared["openrouter-style"]["context_window"] == 1000000
    assert declared["vllm-style"]["context_window"] == 32768


def test_fetch_provider_models_empty_base_url_returns_full_tuple():
    """空地址等提前返回路径也必须给出与成功路径同长度的元组（历史上少一个元素）"""
    out = agent_llm.fetch_provider_models("", "")
    assert len(out) == 5 and out[0] is False and out[4] == {}


# ---------- 4. 配置集成 ----------

def _settings(monkeypatch, model_cfg: dict):
    monkeypatch.setattr(agent_skills, "load_settings", lambda: {"model": model_cfg})


def test_load_model_config_uses_declaration_then_1m(monkeypatch):
    """声明落到 context/context_window；打开 1M 开关后整条决策链改按 1M 计算"""
    cfg = {
        "providers": [{"name": "P", "base_url": "https://api.deepseek.com/v1",
                       "api_key": "k", "models": ["deepseek-chat"],
                       "model_limits": {"deepseek-chat": {"context_window": 200000,
                                                          "max_output_tokens": 64000}}}],
        "model": "deepseek-chat",
    }
    _settings(monkeypatch, cfg)
    out = agent_llm.load_model_config()
    assert out["context_window"] == 200000
    assert out["context"]["source"] == "upstream"
    assert out["context"]["max_output"] == 64000
    assert out["context_1m"] is False

    cfg["context_1m"] = True
    out = agent_llm.load_model_config()
    assert out["context_window"] == agent_llm.ONE_M_CONTEXT
    assert out["context"]["source"] == "1m" and out["context"]["long_1m"] is True


def test_load_model_config_without_declaration_falls_back(monkeypatch):
    """无任何声明时按内置已知表 → 模型名推断，旧配置（单服务商）仍然可用"""
    _settings(monkeypatch, {"base_url": "https://api.deepseek.com/v1",
                            "api_key": "k", "model": "deepseek-chat"})
    out = agent_llm.load_model_config()
    assert out["context"]["source"] == "known"
    assert out["context_window"] == 131072

    _settings(monkeypatch, {"base_url": "https://self-hosted.local/v1",
                            "api_key": "k", "model": "mystery-model"})
    out = agent_llm.load_model_config()
    assert out["context"]["source"] == "inferred"
    assert out["context_window"] == 131072
