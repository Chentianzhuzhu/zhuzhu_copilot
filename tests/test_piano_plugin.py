"""piano-studio 插件测试：记谱解析、MCP 协议、局域网 HTTP/SSE、音色数据与元数据。

不做 mock：HTTP/SSE 用例真实起 ThreadingHTTPServer 并用 socket 读取事件流；
MCP 用例真实以 stdio 拉起 server.py 收发 JSON-RPC。
"""
import importlib.util
import http.client
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

PLUGIN_DIR = (Path(__file__).resolve().parent.parent / "src" / "zhuzhu_Copilot"
              / "plugins" / "piano-studio")
SERVER = PLUGIN_DIR / "server.py"
WEB = PLUGIN_DIR / "web"

_MODULE = None


def _load_module():
    global _MODULE
    if _MODULE is None:
        spec = importlib.util.spec_from_file_location("piano_studio_server", SERVER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MODULE = mod
    return _MODULE


@pytest.fixture()
def server():
    mod = _load_module()
    mod._LOOKAHEAD_MS = 150
    port = mod._start_http(0)
    assert port, "HTTP 服务启动失败"
    yield mod, port
    mod._stop_playback("test_teardown")
    mod._stop_http()


# ---------- 元数据与数据 ----------

def test_plugin_metadata_and_files():
    meta = json.loads((PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8"))
    assert meta["name"] == "piano-studio"
    assert meta["kind"] == "web"
    assert meta["enabled"] is True
    assert meta["mcp_name"] == "piano-studio-mcp"
    assert meta["skill_name"] == "piano-studio"
    for name in ("plugin.json", "config.json", "SKILL.md", "server.py"):
        assert (PLUGIN_DIR / name).is_file(), f"缺少 {name}"
    for name in ("index.html", "style.css", "app.js", "voices.json"):
        assert (WEB / name).is_file(), f"缺少 web/{name}"


def test_skill_md_frontmatter_and_workflow():
    text = (PLUGIN_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---")
    assert "name: piano-studio" in text
    for key in ("piano_status", "piano_play", "piano_stop", "piano_voices",
                "开始聆听", "octave", "interval_ms", "duration_ms"):
        assert key in text, f"SKILL.md 缺少关键约定: {key}"


def test_voices_data_is_complete():
    data = json.loads((WEB / "voices.json").read_text(encoding="utf-8"))
    assert data["default"] in {voice["id"] for voice in data["voices"]}
    assert len(data["voices"]) >= 8
    assert set(data["registers"]) == {"low", "mid", "high"}
    for voice in data["voices"]:
        assert voice["id"] and voice["label"]
        assert voice.get("partials"), f"{voice['id']} 没有泛音配置"
        envelope = voice.get("envelope") or {}
        for key in ("attack", "decay", "sustain", "release"):
            assert key in envelope, f"{voice['id']} 包络缺少 {key}"


# ---------- 记谱解析 ----------

def test_parse_basic_intervals_and_total():
    mod = _load_module()
    events, total = mod._parse_notes("C4@400 D4@400 E4@800", 4, 320, 0, 0.85)
    assert [event["at"] for event in events] == [0, 400, 800]
    assert [event["midis"] for event in events] == [[60], [62], [64]]
    assert total == 1600
    assert events[0]["names"] == ["C4"]


def test_parse_register_chord_rest_and_modifiers():
    mod = _load_module()
    events, total = mod._parse_notes("C E G C4+E4@600:500!0.7 R@200", 3, 300, 0, 0.85)
    assert [event["midis"] for event in events][:3] == [[48], [52], [55]]
    chord = events[3]
    assert chord["midis"] == [60, 64]
    assert chord["dur"] == 500
    assert chord["velocity"] == 0.7
    assert chord["at"] == 900
    assert total == 1700


def test_parse_midi_and_flat_names():
    mod = _load_module()
    events, _ = mod._parse_notes("60 Db5", 4, 200, 100, 0.8)
    assert events[0]["names"] == ["C4"]
    assert events[1]["midis"] == [73]
    assert events[1]["names"] == ["C#5"]


def test_parse_rejects_out_of_range_and_unknown_tokens():
    mod = _load_module()
    with pytest.raises(ValueError):
        mod._parse_notes("C9", 4, 320, 0, 0.85)
    with pytest.raises(ValueError):
        mod._parse_notes("H4", 4, 320, 0, 0.85)
    with pytest.raises(ValueError):
        mod._parse_notes("R@400", 4, 320, 0, 0.85)
    with pytest.raises(ValueError):
        mod._parse_notes([], 4, 320, 0, 0.85)


def test_parse_note_objects():
    mod = _load_module()
    events, total = mod._parse_notes(
        [{"note": "C4", "ms": 250, "dur": 200, "velocity": 0.6},
         {"rest": 250},
         {"notes": "E4+G4", "ms": 500}],
        4, 320, 0, 0.85)
    assert [event["at"] for event in events] == [0, 500]
    assert events[1]["midis"] == [64, 67]
    assert events[0]["dur"] == 200
    assert total == 1000


def test_register_aliases():
    mod = _load_module()
    assert mod._parse_register("low")[:2] == ("low", 3)
    assert mod._parse_register("中音")[:2] == ("mid", 4)
    assert mod._parse_register("5")[:2] == ("custom", 5)
    with pytest.raises(ValueError):
        mod._parse_register("超高音")


# ---------- HTTP ----------

def _get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as response:
        return response.status, response.read(), dict(response.headers)


def test_http_page_and_apis(server):
    _, port = server
    status, body, headers = _get(port, "/")
    assert status == 200
    assert b"<canvas" in body and "Piano Studio".encode() in body
    assert "text/html" in headers.get("Content-Type", "")
    assert headers.get("X-Content-Type-Options") == "nosniff"

    status, body, _ = _get(port, "/api/voices")
    data = json.loads(body)
    assert status == 200 and data["ok"] is True
    assert len(data["voices"]) == 10
    assert len(data["definitions"]) == 10
    assert data["registers"]["mid"]["octave"] == 4

    status, body, _ = _get(port, "/api/state")
    state = json.loads(body)
    assert status == 200 and state["ok"] is True
    assert state["server"]["running"] is True
    assert state["playback"]["playing"] is False
    assert state["defaults"]["lookahead_ms"] > 0

    status, body, _ = _get(port, "/app.js")
    assert status == 200 and b"AudioContext" in body


def test_http_is_read_only_and_blocks_traversal(server):
    _, port = server
    request = urllib.request.Request(f"http://127.0.0.1:{port}/api/play",
                                     data=b"{}", method="POST")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(request, timeout=5)
    assert exc.value.code == 405

    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request("GET", "/../server.py")
    response = connection.getresponse()
    assert response.status == 404
    connection.close()


# ---------- SSE 与演奏调度 ----------

def _open_sse(port, audio=1):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=8)
    connection.request("GET", f"/api/events?audio={audio}")
    response = connection.getresponse()
    assert response.status == 200
    assert "text/event-stream" in response.getheader("Content-Type", "")
    return connection, response


def _read_events(response, wanted, limit=40):
    """读取 SSE 帧，返回 {event: data}（重复事件以后者为准），直到 wanted 全部出现。"""
    found = {}
    current = None
    for _ in range(limit * 3):
        line = response.readline()
        if not line:
            break
        text = line.decode("utf-8", "replace").strip()
        if text.startswith("event:"):
            current = text.split(":", 1)[1].strip()
        elif text.startswith("data:") and current:
            found.setdefault(current, json.loads(text.split(":", 1)[1].strip()))
            if all(name in found for name in wanted):
                break
    return found


def test_sse_play_flow(server):
    mod, port = server
    connection, response = _open_sse(port)
    try:
        hello = _read_events(response, {"hello"})
        assert hello["hello"]["state"]["server"]["running"] is True
        assert len(hello["hello"]["definitions"]) == 10

        result = mod._handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": "piano_play",
                                         "arguments": {"notes": "C4@120 D4@120 E4@120",
                                                       "voice": "grand_piano",
                                                       "octave": "mid",
                                                       "title": "测试旋律"}}})
        text = result["result"]["content"][0]["text"]
        assert "已开始演奏" in text and "测试旋律" in text

        events = _read_events(response, {"start", "note", "end"}, limit=60)
        assert events["start"]["title"] == "测试旋律"
        assert events["note"]["notes"][0]["name"] == "C4"
        assert 0 <= events["note"]["in_ms"] <= 200
        assert events["end"]["playback_id"] == events["start"]["playback_id"]
    finally:
        connection.close()


def test_sse_stop_flow(server):
    mod, port = server
    connection, response = _open_sse(port)
    try:
        _read_events(response, {"hello"})
        mod._handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                     "params": {"name": "piano_play",
                                "arguments": {"notes": "C4@4000", "interval_ms": 4000}}})
        events = _read_events(response, {"note"}, limit=30)
        assert events["note"]["notes"][0]["name"] == "C4"
        mod._handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                     "params": {"name": "piano_stop", "arguments": {}}})
        stopped = _read_events(response, {"stop"}, limit=30)
        assert stopped["stop"]["reason"] == "stopped_by_ai"
        assert mod._playback_state()["playing"] is False
    finally:
        connection.close()


# ---------- MCP stdio ----------

def test_mcp_stdio_protocol_and_tools():
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "piano_voices", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "piano_server", "arguments": {"action": "status"}}},
    ]
    payload = "\n".join(json.dumps(item, ensure_ascii=False) for item in requests) + "\n"
    env = dict(os.environ)
    env.update({"PIANO_AUTO_START": "false", "PIANO_HOST": "127.0.0.1", "PIANO_PORT": "0"})
    proc = subprocess.run([sys.executable, str(SERVER)], input=payload.encode("utf-8"),
                          capture_output=True, timeout=30, env=env, check=False)
    lines = [line for line in (proc.stdout or b"").decode("utf-8", "replace").splitlines()
             if line.strip()]
    messages = [json.loads(line) for line in lines]   # stdout 必须全是 JSON-RPC
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    by_id = {message.get("id"): message for message in messages}
    assert by_id[1]["result"]["serverInfo"]["name"] == "piano-studio"
    names = {tool["name"] for tool in by_id[2]["result"]["tools"]}
    assert names == {"piano_play", "piano_stop", "piano_status",
                     "piano_voices", "piano_server"}
    assert "grand_piano" in by_id[3]["result"]["content"][0]["text"]
    assert "未运行" in by_id[4]["result"]["content"][0]["text"]


def test_selftest_entrypoint():
    proc = subprocess.run([sys.executable, str(SERVER), "--selftest"],
                          capture_output=True, timeout=30, check=False)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    result = json.loads(proc.stdout.decode("utf-8").strip().splitlines()[-1])
    assert result["ok"] is True
    assert result["page_bytes"] > 200
    assert result["voices"] == 10


# ---------- 真实导入链路 ----------

def test_zip_import_registers_skill_and_mcp(tmp_path, monkeypatch):
    """真实走 agent_plugins.import_plugin_zip：解压 → 校验 → 登记技能与 MCP。"""
    import zipfile
    from zhuzhu_Copilot.core import agent_plugins, agent_skills, plugin_guard

    zip_path = tmp_path / "piano-studio.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(PLUGIN_DIR.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                archive.write(path, "piano-studio/" + path.relative_to(PLUGIN_DIR).as_posix())

    plugins_root = tmp_path / "plugins"
    skills_root = tmp_path / "skills"
    monkeypatch.setattr(agent_plugins, "PLUGINS_DIR", plugins_root)
    monkeypatch.setattr(agent_skills, "_skills_dir", lambda: skills_root)
    monkeypatch.setattr(agent_skills, "load_mcp_servers", lambda: [])
    saved = {}
    monkeypatch.setattr(agent_skills, "save_mcp_servers",
                        lambda servers: saved.setdefault("servers", list(servers)))
    monkeypatch.setattr(plugin_guard, "_llm_review",
                        lambda blobs: {"verdict": "safe", "reason": "pytest"})

    ok, message = agent_plugins.import_plugin_zip(str(zip_path))
    assert ok, message
    installed = plugins_root / "piano-studio"
    for rel in ("plugin.json", "config.json", "SKILL.md", "server.py",
                "web/index.html", "web/style.css", "web/app.js", "web/voices.json"):
        assert (installed / rel).is_file(), f"导入后缺少 {rel}"
    assert (skills_root / "piano-studio" / "SKILL.md").is_file()
    names = {server["name"] for server in saved.get("servers", [])}
    assert "piano-studio-mcp" in names
