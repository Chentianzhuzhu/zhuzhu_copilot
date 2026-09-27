"""agent_json 容错解析回归测试：验证 write/edit 工具参数在 LLM 常见格式瑕疵下仍可解析"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from winapp_migrator.core.agent_json import parse_tool_args


def test_valid():
    assert parse_tool_args('{"path": "a.py", "content": "hi"}') == {
        "path": "a.py", "content": "hi"}


def test_code_fence():
    s = '```json\n{"path": "a.py", "content": "hi"}\n```'
    assert parse_tool_args(s) == {"path": "a.py", "content": "hi"}


def test_trailing_comma():
    assert parse_tool_args('{"path": "a.py", "content": "hi",}') == {
        "path": "a.py", "content": "hi"}
    assert parse_tool_args('{"a": [1, 2,],}') == {"a": [1, 2]}


def test_single_quote():
    assert parse_tool_args("{'path': 'a.py', 'content': 'hi'}") == {
        "path": "a.py", "content": "hi"}


def test_prefix_text():
    s = 'tool_call: {"path": "a.py", "content": "hi"}'
    assert parse_tool_args(s) == {"path": "a.py", "content": "hi"}


def test_write_multiline():
    # write 常用场景：content 含换行与引号（严格 JSON 合法）
    s = ('{"path": "src/x.py", "content": "def f():\\n'
         '    return \\"hello\\"\\n"}')
    assert parse_tool_args(s) == {"path": "src/x.py",
                                  "content": 'def f():\n    return "hello"\n'}


def test_apostrophe_inside_double():
    # 双引号字符串内的撇号不应被破坏
    s = '{"content": "it\'s a test"}'
    assert parse_tool_args(s) == {"content": "it's a test"}


def test_unparseable_returns_none():
    assert parse_tool_args("not json at all {") is None


if __name__ == "__main__":
    for fn in [test_valid, test_code_fence, test_trailing_comma, test_single_quote,
               test_prefix_text, test_write_multiline, test_apostrophe_inside_double,
               test_unparseable_returns_none]:
        fn()
        print(f"[OK] {fn.__name__}")
    print("\n全部通过")