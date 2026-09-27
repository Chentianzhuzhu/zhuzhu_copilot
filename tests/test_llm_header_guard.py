"""请求头非 ASCII 预检回归（"无法接入模型：'latin-1' codec can't encode characters"）。

根因：urllib 按 latin-1 编码 HTTP 请求头。接口地址 / API Key 里混入中文（或全角字符）时，
`http.client.putheader` 直接抛 UnicodeEncodeError，文案与"接入失败"毫无关联，
用户只能看到一句天书。修法：发请求前预检并给出可操作的中文提示；且此类"配置错误"
不触发内置模型回退（回退会掩盖配置问题）。

覆盖：
1. 预检函数：ASCII 通过；中文/全角字符被拦下并指明字段与位置
2. 连通性测试 / 模型列表：返回明确提示，不发起网络请求
3. LLMClient：chat_stream / chat 抛 AgentLLMError("配置错误：…")，且不回退内置模型
4. 直接复现原始 latin-1 报错（证明拦截点的必要性）
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from winapp_migrator.core import agent_llm

CJK_KEY = "这是我的密钥请勿粘贴中文" * 4      # 与用户现场一致：长中文串
ASCII_KEY = "sk-1234567890abcdef"


def test_header_hint_ascii_passes():
    assert agent_llm.header_unsafe_hint("API Key", ASCII_KEY) == ""
    assert agent_llm.header_unsafe_hint("接口地址", "https://api.deepseek.com/v1") == ""
    assert agent_llm.validate_header_fields("https://api.deepseek.com/v1", ASCII_KEY) == ""


def test_header_hint_flags_chinese_field_and_position():
    hint = agent_llm.header_unsafe_hint("API Key", "sk-abc" + CJK_KEY)
    assert "非 ASCII" in hint and "API Key" in hint
    assert "第 7 个字符起" in hint            # "sk-abc" 共 6 字符 → 第 7 个字符起有问题
    assert CJK_KEY[:2] in hint                # 回显起始片段便于用户定位
    # 接口地址优先报告（两处都有问题时依次给出）
    both = agent_llm.validate_header_fields("https://例子.com/v1", CJK_KEY)
    assert "接口地址" in both


def test_header_hint_catches_fullwidth_ascii():
    """全角字符（ord > 255）同样无法作为请求头发送，须一并拦下"""
    assert agent_llm.header_unsafe_hint("API Key", "sk-ＡＢＣ１２３") != ""
    assert agent_llm.header_unsafe_hint("API Key", "sk-\u3000abc") != ""


def test_connection_test_reports_hint_without_network():
    ok, msg = agent_llm.test_provider_connection(
        "https://api.example.com/v1", CJK_KEY, "some-model")
    assert ok is False
    assert msg.startswith("测试失败：") and "非 ASCII" in msg
    assert "latin-1" not in msg               # 不再把编码器报错抛给用户


def test_fetch_models_reports_hint_without_network():
    ok, msg, src, multi, decl = agent_llm.fetch_provider_models(
        "https://api.example.com/v1", CJK_KEY)
    assert ok is False and src == "" and multi == [] and decl == {}
    assert "非 ASCII" in msg


def test_client_raises_clear_error_and_does_not_fall_back():
    client = agent_llm.LLMClient(base_url="https://api.example.com/v1",
                                api_key=CJK_KEY, model="some-model")
    for call in (lambda: client.chat_stream([{"role": "user", "content": "hi"}]),
                 lambda: client.chat([{"role": "user", "content": "hi"}])):
        with pytest.raises(agent_llm.AgentLLMError) as ei:
            call()
        text = str(ei.value)
        assert text.startswith("配置错误") and "非 ASCII" in text
        assert "回退" not in text
    assert client.fell_back is False          # 配置错误不回退内置模型
    assert client.model == "some-model"       # 参数未被回退改写

    # responses 协议路径同样拦截
    c2 = agent_llm.LLMClient(base_url="https://api.example.com/v1", api_key=CJK_KEY,
                             model="some-model", protocol="responses")
    with pytest.raises(agent_llm.AgentLLMError) as ei2:
        c2.chat_stream([{"role": "user", "content": "hi"}])
    assert str(ei2.value).startswith("配置错误")


def test_ascii_key_reaches_transport_stage():
    """ASCII Key 不应被预检拦下（错误应来自网络层，而非配置错误）"""
    client = agent_llm.LLMClient(base_url="https://127.0.0.1:9/v1", api_key=ASCII_KEY,
                                 model="some-model", timeout=0.5)
    client.auto_fallback = False              # 只看一次请求的可读错误
    with pytest.raises(agent_llm.AgentLLMError) as ei:
        client.chat_stream([{"role": "user", "content": "hi"}])
    assert not str(ei.value).startswith("配置错误")


def test_original_latin1_error_is_real():
    """证明拦截点的必要性：含中文的请求头在 http.client 层直接抛 latin-1 编码错误。"""
    import http.client
    conn = http.client.HTTPConnection("api.example.com", timeout=0.001)
    conn.putrequest("POST", "/v1/chat/completions")
    with pytest.raises(UnicodeEncodeError) as ei:
        conn.putheader("Authorization", "Bearer " + CJK_KEY)
    assert "latin-1" in str(ei.value)
    conn.close()
