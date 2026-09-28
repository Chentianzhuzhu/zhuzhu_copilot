"""思考模式（think_mode）参数映射单元测试。

覆盖 build_effort_params 在 auto（跟随力度）/on（强制开启）/off（强制关闭）
三种模式下的模型参数映射，重点验证：
- auto 维持原有按力度自动开关行为
- on 强制开启思考（低力度也开启，强度取该模型最低开启档）
- off 尽量关闭思考（强制思考不可关的模型回退最小思考）
"""

import pytest

from zhuzhu_Copilot.core import agent_llm


def _thinking_type(params: dict):
    """取 params 的 thinking.type（无则返回 None / 空串）"""
    t = params.get("thinking")
    return t.get("type") if isinstance(t, dict) else None


def _reasoning(params: dict):
    return params.get("reasoning_effort")


def test_auto_keeps_original_routing():
    """auto 模式 = 原行为：低力度关思考、高力度开思考"""
    # DeepSeek：low → 关；high → 开
    assert _thinking_type(agent_llm.build_effort_params("deepseek-v4-flash", "low", "auto")) == "disabled"
    assert _thinking_type(agent_llm.build_effort_params("deepseek-v4-flash", "high", "auto")) == "enabled"
    # GLM-5.2：low → 关
    assert _thinking_type(agent_llm.build_effort_params("glm-5.2", "low", "auto")) == "disabled"
    # agnes：low → 思考强度 none（关闭）
    assert agent_llm.build_effort_params("agnes-2.5-flash", "low", "auto") == {"reasoning_effort": "none"}


def test_on_forces_enabled_even_low_effort():
    """on 强制开启：低力度也带思考参数"""
    d = agent_llm.build_effort_params("deepseek-v4-flash", "low", "on")
    assert _thinking_type(d) == "enabled"
    assert _reasoning(d) in ("high", "max")
    # GLM-5.2 low + on → enabled（原 auto 是 disabled）
    assert _thinking_type(agent_llm.build_effort_params("glm-5.2", "low", "on")) == "enabled"
    # doubao-seed low + on → enabled
    assert _thinking_type(agent_llm.build_effort_params("doubao-seed-2.1-turbo", "low", "on")) == "enabled"
    # agnes + on → 开启思考（非 none），强度取滑块档位（low=最轻思考）
    assert agent_llm.build_effort_params("agnes-2.5-flash", "low", "on") == {"reasoning_effort": "low"}
    assert agent_llm.build_effort_params("agnes-2.5-flash", "off", "on") == {"reasoning_effort": "low"}
    assert agent_llm.build_effort_params("agnes-2.5-flash", "extreme", "on") == {"reasoning_effort": "max"}
    # MiniMax-M3 + on → adaptive
    assert _thinking_type(agent_llm.build_effort_params("minimax-m3", "low", "on")) == "adaptive"


def test_off_forces_disabled_where_supported():
    """off 强制关闭：支持关闭的模型一律 disabled"""
    # DeepSeek 高力度 + off → 关（原 auto 是开）
    d = agent_llm.build_effort_params("deepseek-v4-pro", "high", "off")
    assert _thinking_type(d) == "disabled"
    assert "reasoning_effort" not in d
    # GLM-4.5 + off → disabled
    assert _thinking_type(agent_llm.build_effort_params("glm-4.5", "high", "off")) == "disabled"
    # agnes + off → none
    assert agent_llm.build_effort_params("agnes-2.5-flash", "medium", "off") == {"reasoning_effort": "none"}
    # doubao-seed + off → disabled
    assert _thinking_type(agent_llm.build_effort_params("doubao-seed-2.1-turbo", "high", "off")) == "disabled"
    # MiniMax-M3 + off → disabled
    assert _thinking_type(agent_llm.build_effort_params("minimax-m3", "high", "off")) == "disabled"
    # Kimi K2.6 + off → disabled
    assert _thinking_type(agent_llm.build_effort_params("kimi-k2.6", "high", "off")) == "disabled"


def test_off_graceful_for_permanent_think_models():
    """off 对「强制思考不可关」的模型尽力维持最小思考，不返回会 400 的参数"""
    # GLM-5.3 强制思考：off 仍 enabled（不可关）
    assert _thinking_type(agent_llm.build_effort_params("glm-5.3", "high", "off")) == "enabled"
    # GLM-4.7 强制思考：off 仍 enabled
    assert _thinking_type(agent_llm.build_effort_params("glm-4.7", "high", "off")) == "enabled"
    # Kimi K2.7 强制思考：off 仍是 keep=all（非法值会 400）
    assert agent_llm.build_effort_params("kimi-k2.7-code", "high", "off") == {
        "thinking": {"type": "enabled", "keep": "all"}}
    # Kimi K3：无完全关闭开关 → 最小思考档
    assert agent_llm.build_effort_params("kimi-k3", "high", "off") == {"reasoning_effort": "low"}
    # o 系列：无完全关闭开关 → 最小档
    assert agent_llm.build_effort_params("gpt-5.4", "high", "off") == {"reasoning_effort": "low"}


def test_unknown_model_returns_empty():
    """不支持思考参数的模型：任何 think_mode 都返回空 dict（不发送参数）"""
    for tm in ("auto", "on", "off"):
        assert agent_llm.build_effort_params("ark-code-latest", "medium", tm) == {}


def test_invalid_think_mode_falls_back_to_auto():
    """非法 think_mode 按 auto 处理（保持向后兼容）"""
    # 非法值等价 auto：与显式 auto 结果一致
    m = "deepseek-v4-flash"
    assert (agent_llm.build_effort_params(m, "low", "bogus")
            == agent_llm.build_effort_params(m, "low", "auto"))
    assert (agent_llm.build_effort_params(m, "high", "bogus")
            == agent_llm.build_effort_params(m, "high", "auto"))


def test_load_model_config_passthrough(monkeypatch):
    """load_model_config 透传 think_mode / force_think（含默认值与异常兜底）"""
    from zhuzhu_Copilot.core import agent_skills as _sk

    fake = {
        "model": {
            "providers": [{"name": "X", "base_url": "http://x/v1", "api_key": "k",
                           "models": ["m1"]}],
            "think_mode": "on",
            "force_think": True,
        }
    }
    monkeypatch.setattr(_sk, "load_settings", lambda: fake)
    cfg = agent_llm.load_model_config()
    assert cfg["think_mode"] == "on"
    assert cfg["force_think"] is True

    # 未配置时默认 auto / False
    fake2 = {"model": {"providers": [{"name": "X", "base_url": "http://x/v1",
                                      "api_key": "k", "models": ["m1"]}]}}
    monkeypatch.setattr(_sk, "load_settings", lambda: fake2)
    cfg2 = agent_llm.load_model_config()
    assert cfg2["think_mode"] == "auto"
    assert cfg2["force_think"] is False

    # 非法值回退 auto
    fake3 = {"model": {"providers": [{"name": "X", "base_url": "http://x/v1",
                                      "api_key": "k", "models": ["m1"]}],
                       "think_mode": "always"}}
    monkeypatch.setattr(_sk, "load_settings", lambda: fake3)
    cfg3 = agent_llm.load_model_config()
    assert cfg3["think_mode"] == "auto"


# ---------- 8 档力度（关闭/低/中/高/超高/最高/极致/超级） ----------
def test_effort_levels_and_labels():
    assert agent_llm.EFFORTS == ("off", "low", "medium", "high",
                                 "very_high", "max", "ultra", "extreme")
    assert agent_llm.effort_label("off") == "关闭"
    assert agent_llm.effort_label("very_high") == "超高"
    assert agent_llm.effort_label("extreme") == "超级"
    assert agent_llm.effort_label("bogus") == "中"      # 非法值回退 medium 中文名
    assert agent_llm.effort_index("off") == 0 and agent_llm.effort_index("extreme") == 7
    assert agent_llm.effort_index("bogus") == agent_llm.effort_index("medium")
    assert agent_llm.effort_by_index(-1) == "off"
    assert agent_llm.effort_by_index(99) == "extreme"


def test_auto_off_level_disables_thinking():
    """auto 模式：关闭/低/中档 = 不思考；高及以上 = 开启（带力度换算）"""
    # DeepSeek：off → disabled；high → enabled + high；extreme → enabled + max
    assert _thinking_type(agent_llm.build_effort_params("deepseek-v4-flash", "off", "auto")) == "disabled"
    assert _thinking_type(agent_llm.build_effort_params("deepseek-v4-flash", "medium", "auto")) == "disabled"
    d = agent_llm.build_effort_params("deepseek-v4-pro", "very_high", "auto")
    assert _thinking_type(d) == "enabled" and d["reasoning_effort"] == "high"
    d = agent_llm.build_effort_params("deepseek-v4-pro", "extreme", "auto")
    assert d["reasoning_effort"] == "max"
    # GLM-5.2：off → disabled；very_high → high；max → xhigh 档位
    assert _thinking_type(agent_llm.build_effort_params("glm-5.2", "off", "auto")) == "disabled"
    d = agent_llm.build_effort_params("glm-5.2", "very_high", "auto")
    assert d["reasoning_effort"] == "high"
    d = agent_llm.build_effort_params("glm-5.2", "max", "auto")
    assert d["reasoning_effort"] == "xhigh"
    # OpenAI 无关闭开关：off → 最低档 low；medium → medium
    assert agent_llm.build_effort_params("gpt-5.4", "off", "auto") == {"reasoning_effort": "low"}
    assert agent_llm.build_effort_params("gpt-5.4", "medium", "auto") == {"reasoning_effort": "medium"}
    assert agent_llm.build_effort_params("gpt-5.4", "extreme", "auto") == {"reasoning_effort": "high"}


def test_always_think_uses_slider_level():
    """始终思考（on）：以当前滑动档位换算思考强度，关闭档也取该模型最轻思考"""
    # DeepSeek：off + on → enabled（不关闭），档位折算 high
    d = agent_llm.build_effort_params("deepseek-v4-flash", "off", "on")
    assert _thinking_type(d) == "enabled" and d["reasoning_effort"] == "high"
    d = agent_llm.build_effort_params("deepseek-v4-flash", "ultra", "on")
    assert d["reasoning_effort"] == "max"
    # GLM-5.2：off + on → enabled + minimal（该模型最低思考档）
    d = agent_llm.build_effort_params("glm-5.2", "off", "on")
    assert _thinking_type(d) == "enabled" and d["reasoning_effort"] == "minimal"
    # GLM-5.3：off + on → enabled + low（GLM-5.3 最低思考档）
    d = agent_llm.build_effort_params("glm-5.3", "off", "on")
    assert d == {"thinking": {"type": "enabled"}, "reasoning_effort": "low"}
    # OpenAI：on 按档位映射
    assert agent_llm.build_effort_params("gpt-5.4", "very_high", "on") == {"reasoning_effort": "high"}


# ---------- 上游声明的力度级别（真实 /models 解析 + 归一化映射） ----------
def test_normalize_declared_efforts():
    assert agent_llm.normalize_declared_efforts(["none", "high", "extreme"]) == \
        ["off", "high", "extreme"]
    assert agent_llm.normalize_declared_efforts(["low", "medium", "high", "xhigh", "max"]) == \
        ["low", "medium", "high", "very_high", "max"]
    assert agent_llm.normalize_declared_efforts({"low": True, "high": True}) == ["low", "high"]
    assert agent_llm.normalize_declared_efforts("medium") == ["medium"]
    assert agent_llm.normalize_declared_efforts("高") == ["high"]
    assert agent_llm.normalize_declared_efforts(["low", "bogus_level"]) is None   # 未知不猜
    assert agent_llm.normalize_declared_efforts([]) is None
    assert agent_llm.normalize_declared_efforts(None) is None


def test_declared_efforts_from_item():
    # 列表声明
    assert agent_llm.declared_efforts_from_item(
        {"id": "m1", "effort_levels": ["minimal", "high", "max"]}) == ["low", "high", "max"]
    # capabilities 内声明
    assert agent_llm.declared_efforts_from_item(
        {"id": "m1", "capabilities": {"effort_levels": ["low", "highest"]}}) == ["low", "very_high"]
    # 标量默认值不是"可选级别集合"，忽略
    assert agent_llm.declared_efforts_from_item({"id": "m1", "reasoning_effort": "high"}) is None
    # 无法识别的取值整体放弃
    assert agent_llm.declared_efforts_from_item({"id": "m1", "effort_levels": ["deep"]}) is None
    assert agent_llm.declared_efforts_from_item("not-dict") is None


def _serve_models_http(payload: dict):
    """起一个真实本地 HTTP /models 端点（真实网络请求，非 mock 桩）"""
    import json as _json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class _H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.rstrip("/").endswith("/models"):
                body = _json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *a):     # noqa: D401 - 静默日志
            pass

    srv = HTTPServer(("127.0.0.1", 0), _H)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv


def test_fetch_models_parses_upstream_effort_levels():
    """真实 GET /models：上游声明的力度级别被归一化为 EFFORTS 子集带回（非 mock）"""
    srv = _serve_models_http({"data": [
        {"id": "deepseek-v4", "capabilities": {"effort_levels": ["none", "high", "max"]}},
        {"id": "plain-model"},
    ]})
    try:
        base = f"http://127.0.0.1:{srv.server_port}"
        ok, models, source, vision, decls = agent_llm.fetch_provider_models(base)
        assert ok and source == "upstream" and "deepseek-v4" in models
        assert decls["deepseek-v4"]["effort_levels"] == ["off", "high", "max"]
        assert agent_llm.declared_effort_levels(decls, "deepseek-v4") == ["off", "high", "max"]
        assert agent_llm.declared_effort_levels(decls, "plain-model") is None
    finally:
        srv.shutdown()
        srv.server_close()


def test_resolve_model_off_bucket_light():
    """off 档计入轻量档位路由（与原 low/medium 一致）"""
    cfg = {"models": ["flash-m1", "pro-m2"], "effort_models": {}}
    assert agent_llm.resolve_model(cfg, "off") in ("flash-m1", "pro-m2")
    # effort_models 优先生效
    cfg2 = {"models": ["flash-m1", "pro-m2"],
            "effort_models": {"off": "flash-m1"}}
    assert agent_llm.resolve_model(cfg2, "off") == "flash-m1"


if __name__ == "__main__":
    pytest.main([__file__, "-q"])