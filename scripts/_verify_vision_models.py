"""验证：多模态模型以用户设置为准（禁私自判断）+ 上游拉取自动检测多模态。"""
import os, sys, json
sys.path.insert(0, os.path.abspath("src"))
from winapp_migrator.core import agent_llm as al

# 1. is_vision_model 仅以用户设置的多模态模型列表为准
cfg = {"providers": [
    {"name": "P", "multimodal_models": ["gpt-4o", "glm-4v-plus", "deepseek-chat"]},
]}
assert al.is_vision_model(cfg, "gpt-4o") is True, "在用户多模态列表应为 True"
assert al.is_vision_model(cfg, "glm-4v-plus") is True
assert al.is_vision_model(cfg, "deepseek-chat") is True, "用户显式设置的文本模型也以用户为准"
assert al.is_vision_model(cfg, "gpt-4o-mini") is False, "名字像视觉但不在列表 → 禁止私自判断"
assert al.is_vision_model(cfg, "qwen-vl-max") is False, "vl 名字不在列表 → 禁止私自判断"
assert al.is_vision_model({}, "gpt-4o") is False, "无多模态设置 → 不视为视觉"
print("1. is_vision_model 以用户设置多模态列表为准: OK")

# 2. detect_vision_models（拉取后自动填写用，独立于运行时判断）
vis = al.detect_vision_models(
    ["gpt-4o", "qwen-vl-max", "glm-4v-plus", "hunyuan-vision",
     "deepseek-chat", "deepseek-v4-flash", "kimi-k2.7-code"])
assert "gpt-4o" in vis and "qwen-vl-max" in vis and "glm-4v-plus" in vis
assert "hunyuan-vision" in vis
assert "deepseek-chat" not in vis and "deepseek-v4-flash" not in vis, "文本模型不应误检"
print("2. detect_vision_models 关键词检测: OK,", vis)

# 3. fetch_provider_models 返回 4 元组 + 上游能力元数据解析
class _FakeResp:
    def __init__(self, body):
        self._b = body
    def read(self):
        return self._b
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
class _FakeURLError(Exception):
    def __init__(self, code):
        self.code = code
        self.reason = f"HTTP {code}"
class _FakeHTTPError(_FakeURLError):
    def read(self):
        return b"{}"
class _FakeRequest:
    def __init__(self, url, headers=None, method=None):
        self.url = url
class _FakeUrlopen:
    def __init__(self, payload):
        self._payload = payload
    def __call__(self, req, timeout=None):
        return _FakeResp(json.dumps(self._payload).encode("utf-8"))
class _FakeMod:
    def __init__(self, payload):
        self.urlopen = _FakeUrlopen(payload)
        self.error = type("E", (Exception,), {})
        self.error.HTTPError = _FakeHTTPError
        self.error.URLError = _FakeURLError
        self.Request = _FakeRequest

# 上游返回带 capabilities 元数据
payload = {"data": [
    {"id": "gpt-4o", "capabilities": {"vision": True}},
    {"id": "glm-4v-plus"},
    {"id": "deepseek-chat"},
    {"id": "qwen-vl-max", "vision": True},
]}
orig_mod = al._request_module
al._request_module = lambda: _FakeMod(payload)
try:
    ok, models, source, vision = al.fetch_provider_models("https://x.example/v1", "k")
    assert ok and source == "upstream"
    assert "gpt-4o" in models and "deepseek-chat" in models
    # 元数据标记 gpt-4o / qwen-vl-max + 名字检测 glm-4v-plus
    assert "gpt-4o" in vision, "capabilities 元数据未识别"
    assert "qwen-vl-max" in vision
    assert "glm-4v-plus" in vision, "名字关键词检测未识别"
    assert "deepseek-chat" not in vision, "文本模型不应进多模态"
    print("3. 上游拉取 4 元组 + 能力元数据/名字检测: OK, vision=", vision)
finally:
    al._request_module = orig_mod

# 4. known 兜底也返回多模态检测
orig_known = al._known_provider_models
al._known_provider_models = lambda base: ["gpt-4o", "deepseek-chat"]
try:
    ok, models, source, vision = al.fetch_provider_models("https://no-net.example/v1", "k")
    assert ok and source == "known" and vision == ["gpt-4o"]
    print("4. known 兜底自动检测多模态: OK")
finally:
    al._known_provider_models = orig_known

print("SMOKE OK")
