"""agent_web_llm 单元测试：prompt 序列化 / UI 杂音清洗 / /v1/models（纯函数，无需浏览器）。"""
import sys
import tempfile
import json as _json
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from winapp_migrator.core import agent_web_llm as W, agent_skills as S


def test_serialize_prompt():
    msgs = [{"role": "system", "content": "你是助手"},
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！"}]
    out = W.serialize_prompt(msgs)
    assert "[系统指令]" in out and "你是助手" in out
    assert "[用户]" in out and "你好" in out
    assert "[助手]" in out and "你好！" in out


def test_serialize_injects_tool_messages():
    """工具调用历史与执行结果注入 prompt（大型任务流多轮上下文连贯的必要条件）。"""
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "查天气"}]},
        {"role": "assistant", "content": None,
         "tool_calls": [{"function": {"name": "get_weather", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "x", "content": "晴"}]
    out = W.serialize_prompt(msgs)
    assert "查天气" in out
    assert "get_weather" in out                 # 工具调用历史注入
    assert "[工具结果]" in out and "晴" in out   # 工具执行结果注入


def test_serialize_truncates_long_prompt():
    """超长 prompt 裁剪：保留头部（系统指令/工具清单）+ 尾部最近消息，中段截断。"""
    big = "长内容" * 20000                          # 60k 字符，远超输入框上限
    msgs = [{"role": "system", "content": "你是助手"},
            {"role": "user", "content": "开始"},
            {"role": "assistant", "content": big},
            {"role": "user", "content": "最近的问题"}]
    out = W.serialize_prompt(msgs)
    assert "你是助手" in out and "开始" in out     # 头部保留
    assert "最近的问题" in out                     # 尾部最近消息保留
    assert len(out) <= W._CFG["max_prompt_chars"]
    assert "历史过长已截断" in out                 # 截断标记存在
    assert "长内容" in out                         # 尾部内容仍在


def test_serialize_keeps_short_prompt():
    """短 prompt 不做任何裁剪（行为不变）。"""
    msgs = [{"role": "user", "content": "你好"}]
    out = W.serialize_prompt(msgs)
    assert out == "[用户]\n你好"
    assert "历史过长已截断" not in out


def test_strip_ui_noise():
    raw = "成功\n\n浏览器桥测试成功。\n\n深度思考\n智能搜索\n内容由 AI 生成，请仔细甄别"
    out = W.strip_ui_noise(raw)
    assert "浏览器桥测试成功" in out
    assert "内容由 AI 生成" not in out
    assert not out.endswith("请仔细甄别")


def test_strip_no_noise_keeps_text():
    raw = "正常回复内容"
    assert W.strip_ui_noise(raw) == "正常回复内容"


def test_models_endpoint():
    W.ensure_web_proxy(kind_check=False)
    base = W._PROXY.base_url
    with urllib.request.urlopen(base + "/models", timeout=5) as r:
        d = _json.loads(r.read().decode("utf-8"))
    ids = [x["id"] for x in d.get("data", [])]
    # 三档模式模型名（快速/专家/视图）
    assert {"deepseek-chat-quick", "deepseek-chat-expert", "deepseek-chat-view"} <= set(ids)
    W.stop_proxy()


def test_load_config_injects_proxy():
    """kind=deepseek_web 服务商被注入为本地代理 base_url（不依赖已登录，代理恒可启）。"""
    import winapp_migrator.core.agent_llm as L

    orig = S.load_settings

    def fake_load():
        return {"model": {"providers": [
            {"name": "DeepSeek 网页版（免费）", "base_url": "http://127.0.0.1:0/v1",
             "api_key": "", "models": ["deepseek-chat"], "protocol": "chat",
             "kind": "deepseek_web"}]}}

    S.load_settings = fake_load
    try:
        cfg = L.load_model_config()
        assert cfg["providers"][0]["base_url"].startswith("http://127.0.0.1:")
        assert "127.0.0.1:0" not in cfg["base_url"]
    finally:
        S.load_settings = orig
        W.stop_proxy()


REACT_SAMPLE = """收到。我将按照规范探索这个 Android 项目，并生成 Markdown 格式的探索报告保存到项目根目录。

执行计划
1. 创建任务清单 – 记录探索步骤
2. 探索项目结构 – 使用 explore_project 工具生成目录结构

Action: update_todo
Action Parameters: {"todos": [{"title": "探索项目目录结构并生成树状图", "status": "in_progress"}]}
"""


def test_react_compat_updates_todo():
    """网页版模型旧式 ReAct 输出（Action + Action Parameters）应解析为工具调用（真实失败样例）。"""
    r = W.extract_tool_call(REACT_SAMPLE, ["update_todo", "read_file"])
    assert r is not None
    text, call = r
    assert call["name"] == "update_todo"
    assert call["arguments"]["todos"][0]["title"].startswith("探索项目目录结构")
    assert "Action" not in text          # 正文不含 Action 标记
    assert "执行计划" in text            # Action 行前的计划正文被保留


def test_react_action_input_variant():
    """经典 ReAct Action Input 变体也应识别。"""
    raw = "先查文件。\nAction: search_files\nAction Input: {\"path\": \"C:\\\\Users\"}"
    r = W.extract_tool_call(raw, ["search_files"])
    assert r is not None
    _, call = r
    assert call["name"] == "search_files"
    assert call["arguments"].get("path") == "C:\\Users"


def test_react_unknown_tool_ignored():
    """Action 名不在白名单 → 视为正文，不误判（避免闲聊内容触发工具）。"""
    raw = "我建议 Action: 提升用户体验。"
    assert W.extract_tool_call(raw, ["search_files", "update_todo"]) is None


def test_react_requires_allowed_when_provided():
    """传入白名单时未命中严格 JSON 且无合法 Action → None。"""
    raw = "正常回答，不调用工具。"
    assert W.extract_tool_call(raw, ["update_todo"]) is None


def test_strict_json_still_works():
    """严格 JSON 协议解析行为不回退。"""
    raw = '开头说明。\n{"tool": "read_file", "arguments": {"path": "a.txt"}}'
    r = W.extract_tool_call(raw, ["read_file"])
    assert r is not None and r[1]["name"] == "read_file"


def test_strict_preferred_over_react():
    """同一回复中同时存在严格 JSON 与 Action 时，以严格 JSON 为准。"""
    raw = 'Action: update_todo\nAction Parameters: {"todos": []}\n{"tool": "read_file", "arguments": {"path": "b"}}'
    r = W.extract_tool_call(raw, ["update_todo", "read_file"])
    assert r is not None and r[1]["name"] == "read_file"


def _snap_collect(chunks, allowed):
    """模拟 ask 的 on_full 快照协议喂给 _BodyStreamer，返回 (实时发出正文, 是否含工具尾)。"""
    out = []
    st = W._BodyStreamer(out.append, allowed)
    full = ""
    for c in chunks:
        full += c
        st.snapshot(full)
    st.finish(full, found=bool(W.extract_tool_call(full, allowed)))
    return "".join(out), st.artifact


def test_streamer_body_live_react_tail_hidden():
    """正文边渲染边发；结尾 Action 调用块不向 UI 漏出；finish 判定含工具尾。"""
    body = "收到，我先搜索桌面上的 txt 文件。\n\n稍等。\n"
    tail1 = "Action: search_files\n"
    tail2 = 'Action Parameters: {"path": "C:\\\\Users\\\\zhuzhu\\\\Desktop"}\n'
    emitted, has_tail = _snap_collect(
        [body, tail1, tail2], ["search_files", "update_todo"])
    assert has_tail is True
    assert "Action:" not in emitted and "Action Parameters" not in emitted
    assert "先搜索桌面上的 txt 文件" in emitted          # 正文已实时发出
    full = body + tail1 + tail2
    r = W.extract_tool_call(full, ["search_files", "update_todo"])
    assert r is not None and r[1]["name"] == "search_files"


def test_streamer_whole_action_block_arrives_at_once():
    """Action 块随一个增量整体到达（页面块渲染）：正文照发、尾巴仍被抑制。"""
    body = "开始执行。\n"
    block = ("Action: update_todo\n"
             "Action Parameters: {\"todos\": [{\"title\": \"x\", \"status\": \"pending\"}]}\n")
    emitted, has_tail = _snap_collect([body + block], ["update_todo"])
    assert has_tail is True
    assert "Action" not in emitted
    assert emitted == body.strip("\n") or emitted == body  # 仅正文
    r = W.extract_tool_call(body + block, ["update_todo"])
    assert r is not None and r[1]["name"] == "update_todo"


def test_streamer_plain_text_no_artifact():
    """无工具尾巴的普通回答：整段实时发出，finish 判 false。"""
    text = "好的，这是一个普通回答。\n第二段。"
    emitted, has_tail = _snap_collect([text], ["search_files"])
    assert has_tail is False
    assert emitted == text


def test_streamer_json_tail_hidden():
    """严格 JSON 协议尾巴同样被抑制、可解析。"""
    body = "我直接执行。\n"
    tail = '{"tool": "search_files", "arguments": {"path": "C:\\\\Users"}}\n'
    emitted, has_tail = _snap_collect([body, tail], ["search_files"])
    assert has_tail is True
    assert '"tool"' not in emitted
    assert "直接执行" in emitted
    assert W.extract_tool_call(body + tail, ["search_files"])[1]["name"] == "search_files"


def test_call_marker_format_parsed():
    """实测变体：[调用工具] 工具名{...} 必须解析为工具调用（此前直接漏掉 → 不执行）。"""
    raw = ("现在开始执行。我使用 explore_project 工具来快速了解这个 Android 项目。\n"
           '[调用工具] explore_project{"directory": "C:/Users/zhuzhu/Desktop/my first android app"}')
    r = W.extract_tool_call(raw, ["explore_project", "read_file"])
    assert r is not None
    text, call = r
    assert call["name"] == "explore_project"
    assert "my first android app" in call["arguments"]["directory"]
    assert "[调用工具]" not in text          # 标记不进入正文
    assert "现在开始执行" in text


def test_call_marker_no_bracket_variant():
    """调用工具：工具名 {...}（无方括号）同样识别。"""
    raw = '准备就绪。\n调用工具：read_file {"path": "README.md"}'
    r = W.extract_tool_call(raw, ["read_file"])
    assert r is not None and r[1]["name"] == "read_file"
    assert "调用工具" not in r[0]


def test_artifact_start_covers_marker():
    """流式抑制起点应落在「[调用工具]」标记处，避免标记与 JSON 外泄到界面。"""
    raw = "探索中。\n[调用工具] explore_project{\"directory\": \".\"}"
    s = W._artifact_start(raw, ["explore_project"])
    assert s == raw.index("[调用工具]")


def test_call_marker_unknown_name_forwarded():
    """臆造工具名也要回传（引擎回「未知工具」促使模型纠正），不能静默当正文。"""
    raw = '好。\n[调用工具] make_report{"path": "a.md"}'
    r = W.extract_tool_call(raw, ["explore_project"])
    assert r is not None and r[1]["name"] == "make_report"
    assert r[0] == "好。"


def test_html_to_markdown_rich_text():
    """网页渲染 HTML → Markdown：标题/加粗/代码块/列表/表格结构还原（富文本）。"""
    html = ("<h2>项目概览</h2><p>这是 <strong>PyQt6</strong> 桌面应用，"
            "入口 <code>src/main.py</code></p>"
            "<pre><code class=\"language-python\">print(1)\nprint(2)</code></pre>"
            "<ul><li>核心：agent_engine</li><li>界面：agent_panel</li></ul>"
            "<table><tr><th>模块</th><th>说明</th></tr>"
            "<tr><td>core</td><td>引擎</td></tr></table>")
    md = W.html_to_markdown(html)
    assert md.startswith("## 项目概览")
    assert "**PyQt6**" in md and "`src/main.py`" in md
    assert "```python" in md and "print(1)" in md
    assert "- 核心：agent_engine" in md and "- 界面：agent_panel" in md
    assert "| 模块 | 说明 |" in md and "| core | 引擎 |" in md


for _n in ("test_serialize_prompt", "test_serialize_injects_tool_messages",
           "test_serialize_truncates_long_prompt", "test_serialize_keeps_short_prompt",
           "test_strip_ui_noise", "test_strip_no_noise_keeps_text"):
    if _n in globals():
        globals()[_n]()
        print(f"PASS {_n}")

for _n in ("test_react_compat_updates_todo", "test_react_action_input_variant",
           "test_react_unknown_tool_ignored", "test_react_requires_allowed_when_provided",
           "test_strict_json_still_works", "test_strict_preferred_over_react"):
    if _n in globals():
        globals()[_n]()
        print(f"PASS {_n}")

for _n in ("test_call_marker_format_parsed", "test_call_marker_no_bracket_variant",
           "test_artifact_start_covers_marker", "test_call_marker_unknown_name_forwarded",
           "test_html_to_markdown_rich_text"):
    if _n in globals():
        globals()[_n]()
        print(f"PASS {_n}")

for _n in ("test_streamer_body_live_react_tail_hidden",
           "test_streamer_whole_action_block_arrives_at_once",
           "test_streamer_plain_text_no_artifact",
           "test_streamer_json_tail_hidden"):
    if _n in globals():
        globals()[_n]()
        print(f"PASS {_n}")

for _n in ("test_models_endpoint",):
    if _n in globals():
        globals()[_n]()
        print(f"PASS {_n}")

for _n in ("test_load_config_injects_proxy",):
    if _n in globals():
        globals()[_n]()
        print(f"PASS {_n}")