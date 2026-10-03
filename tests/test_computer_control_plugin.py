"""computer-control 插件测试：元数据/技能规范、MCP 握手与工具集、接管状态机、离线自检、
随包登记、Web UI 页面与坐标换算。

插件 server 以真实子进程（stdio JSON-RPC）拉起；用例只调用只读/状态类工具，
**绝不注入键鼠输入**（避免测试干扰用户桌面）。"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from zhuzhu_Copilot.core import agent_plugins, agent_runtime, agent_skills

PLUGIN_DIR = (Path(__file__).resolve().parent.parent / "src" / "zhuzhu_Copilot"
              / "plugins" / "computer-control")
SERVER = PLUGIN_DIR / "server.py"

# 用户要求必备的操控工具（点击/拖拽/长按/输入/选择/复制/滑动/粘贴/虚拟桌面）
REQUIRED_TOOLS = {"click", "drag", "long_press", "type_text", "select",
                  "copy", "swipe", "paste", "open_virtual_desktop",
                  "screen_shot", "zoom_in", "screen_info", "list_windows",
                  "scroll", "key_press", "control", "web_url"}


def _rpc(requests: list, timeout: int = 60) -> list:
    """以 stdio 拉起插件 server，发一批请求，收齐响应后关闭 stdin（进程自然退出）。"""
    payload = "\n".join(json.dumps(r, ensure_ascii=False) for r in requests) + "\n"
    proc = subprocess.run([sys.executable, str(SERVER)], input=payload.encode("utf-8"),
                          capture_output=True, check=False, timeout=timeout)
    out = []
    for line in (proc.stdout or b"").decode("utf-8", "replace").splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


_MODULE = None


def _load_module():
    """以模块方式加载插件 server（模块级无副作用：不启服务、不起钩子），
    用于离线校验页面内容与坐标换算等纯逻辑。"""
    global _MODULE
    if _MODULE is None:
        spec = importlib.util.spec_from_file_location("cc_plugin_server", SERVER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MODULE = mod
    return _MODULE


def _result_text(resp: dict) -> str:
    """取 tools/call 响应里的文本内容。"""
    content = (resp.get("result") or {}).get("content") or []
    return "\n".join(c.get("text", "") for c in content if c.get("type") == "text")


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    """隔离插件目录 / 技能目录 / MCP 配置，固定解释器（随包登记用例用）"""
    monkeypatch.setattr(agent_plugins, "PLUGINS_DIR", tmp_path / "plugins")
    monkeypatch.setattr(agent_skills, "CONFIG_DIR", tmp_path / "agent")
    monkeypatch.setattr(agent_runtime, "python_interpreter", lambda: "python-test")
    agent_skills._MCP_CACHE["data"] = None
    agent_skills.invalidate_skills_cache()
    return tmp_path


# ---------- 元数据与技能规范 ----------

def test_plugin_files_exist():
    for name in ("plugin.json", "SKILL.md", "server.py"):
        assert (PLUGIN_DIR / name).is_file(), f"缺少 {name}"


def test_plugin_metadata_binds_mcp_and_skill():
    meta = json.loads((PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8"))
    assert meta["name"] == "computer-control"
    assert meta["enabled"] is True
    assert meta["mcp_name"] == "computer-control-mcp"
    assert meta["skill_name"] == "computer-control"
    assert meta["description"]


def test_skill_md_frontmatter_and_protocol():
    text = (PLUGIN_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---")
    assert "name: computer-control" in text
    assert "description:" in text
    # 硬性协议必须写进技能规范：可视化先行 / 接管后停手 / 恢复需用户同意 / 虚拟桌面返回
    for key in ("web_url", "browser_open", "用户已接管", "resume",
                "open_virtual_desktop", "安全红线"):
        assert key in text, f"SKILL.md 缺少关键约定: {key}"


# ---------- MCP 协议 ----------

def test_server_compiles():
    proc = subprocess.run([sys.executable, "-m", "py_compile", str(SERVER)],
                          capture_output=True, check=False, timeout=60)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")


def test_mcp_handshake_exposes_required_tools():
    resps = _rpc([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ])
    by_id = {r.get("id"): r for r in resps}
    assert (by_id.get(1, {}).get("result") or {}).get("serverInfo", {}).get("name") \
        == "computer-control"
    tools = (by_id.get(2, {}).get("result") or {}).get("tools") or []
    names = {t["name"] for t in tools}
    assert REQUIRED_TOOLS <= names, f"缺少工具: {REQUIRED_TOOLS - names}"
    for t in tools:
        assert t.get("inputSchema", {}).get("type") == "object"


def test_mcp_calls_are_readonly_safe():
    """只读调用：环境信息 / 状态 / 空参数校验（均不注入输入）"""
    resps = _rpc([
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "screen_info", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "control", "arguments": {"action": "status"}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "scroll", "arguments": {"delta": 0}}},
    ])
    by_id = {r.get("id"): r for r in resps}
    assert "屏幕物理分辨率" in _result_text(by_id[1])
    assert "操控状态" in _result_text(by_id[2])
    # delta=0 属参数错误：守卫通过后由参数校验拦下（未注入任何输入）
    assert (by_id[3].get("result") or {}).get("isError") is True


def test_take_over_blocks_actions_until_resume():
    """用户接管/暂停后：操控工具被拒绝（返回拒绝文本，不执行注入）；resume 后恢复"""
    resps = _rpc([
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "control", "arguments": {"action": "take_over"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "click", "arguments": {"x": 10, "y": 10}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "control", "arguments": {"action": "resume"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "control", "arguments": {"action": "status"}}},
    ])
    by_id = {r.get("id"): r for r in resps}
    assert "交还控制权" in _result_text(by_id[1])
    refused = _result_text(by_id[2])
    assert "用户已接管" in refused and "本次操作未执行" in refused
    assert "已清除接管" in _result_text(by_id[3])
    assert "正常" in _result_text(by_id[4])


def test_unknown_tool_and_method_errors():
    resps = _rpc([
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "no_such_tool", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 2, "method": "no/such/method", "params": {}},
    ])
    by_id = {r.get("id"): r for r in resps}
    assert by_id[1]["error"]["code"] == -32601
    assert by_id[2]["error"]["code"] == -32601


# ---------- 离线自检 ----------

def test_selftest_passes():
    """python server.py --selftest：页面 + 端点 + 接管状态机（不做输入注入）"""
    proc = subprocess.run([sys.executable, str(SERVER), "--selftest"],
                          capture_output=True, check=False, timeout=120)
    line = [x for x in (proc.stdout or b"").decode("utf-8", "replace").splitlines()
            if x.strip().startswith("{")]
    assert line, f"未拿到自检输出: {proc.stderr.decode('utf-8', 'replace')[-400:]}"
    data = json.loads(line[-1])
    assert data["ok"] is True, data
    assert data["page_bytes"] >= 200
    assert all(v == "ok" for v in data["endpoints"].values()), data["endpoints"]
    assert len(data["tools"]) == len(REQUIRED_TOOLS)
    assert proc.returncode == 0


# ---------- Web UI 页面与纯逻辑（离线，不注入输入） ----------

def test_webui_page_has_control_affordances_and_no_cdn():
    page = _load_module()._PAGE
    for marker in ("/api/state", "/api/screenshot", "/api/control",
                   "btnTakeOver", "btnPause", "btnResume", "电脑操控"):
        assert marker in page, f"页面缺少 {marker}"
    low = page.lower()
    assert "cdn" not in low and "https://" not in low and "unpkg" not in low


def test_view_mapping_image_to_physical():
    """坐标换算：AI 读到的图像坐标 → 屏幕物理坐标（视觉基准的核心正确性）"""
    mod = _load_module()
    mod._set_view(100, 50, 1920, 1080, 1280, 720, "full")
    assert mod.to_physical(0, 0) == (100, 50)
    assert mod.to_physical(640, 360) == (1060, 590)
    assert mod.to_physical(1280, 720) == (2020, 1130)
    # 无基准（img_w=0）时按物理坐标原样透传
    mod._set_view(0, 0, 0, 0, 0, 0, "none")
    assert mod.to_physical(300, 200) == (300, 200)


def test_num_and_key_parsing_tolerates_model_inputs():
    mod = _load_module()
    assert mod._num("x=100, y=200") == 100
    assert mod._num("100px") == 100
    assert mod._num(None, 7) == 7
    assert mod.key_vk("win") == 0x5B
    assert mod.key_vk("ctrl") == 0x11
    assert mod.key_vk("f5") == 0x74
    assert mod.key_vk("A") == ord("A")
    with pytest.raises(ValueError):
        mod.key_vk("no_such_key")


# ---------- 触控能力判定与鼠标回退（桩掉真实注入，不触碰用户桌面） ----------

@pytest.fixture()
def cc(monkeypatch):
    """加载插件模块并桩掉全部真实输入注入（不触碰用户桌面）；返回 (模块, 记录器)。"""
    mod = _load_module()
    calls = {"touch": [], "mouse": [], "move": [], "log": []}
    monkeypatch.setattr(mod, "_touch", {"state": "ok"})      # 假定初始判定为可用
    monkeypatch.setattr(mod, "_last_touch_err", 0)
    monkeypatch.setattr(mod, "_log", lambda who, text: calls["log"].append(text))

    def _fail_inject(x, y, flags):
        """模拟本机真实情况：注入失败并留下错误码（实测为 87 参数无效）"""
        calls["touch"].append((x, y, flags))
        mod._last_touch_err = 87
        return False

    monkeypatch.setattr(mod, "_inject_touch", _fail_inject)
    monkeypatch.setattr(mod, "_move_human", lambda x, y, duration=0: calls["move"].append((x, y)))
    monkeypatch.setattr(mod, "_move_absolute", lambda x, y: calls["move"].append((x, y)))
    monkeypatch.setattr(mod, "_send_mouse",
                        lambda flags, dx=0, dy=0, data=0: calls["mouse"].append(flags))
    return mod, calls


def test_touch_injection_failure_falls_back_to_mouse(cc):
    """触控注入失败（如本机错误码 87）→ 自动回退鼠标，点击不丢失"""
    mod, calls = cc
    mod.do_click(600, 400)
    assert calls["touch"], "应先尝试触控注入"
    # 回退到鼠标：按下+抬起各一次
    assert calls["mouse"] == [mod.MOUSEEVENTF_LEFTDOWN, mod.MOUSEEVENTF_LEFTUP]
    # 能力状态转为 broken 并如实汇报原因
    assert mod._touch["state"] == "broken"
    assert mod.touch_capability().startswith("鼠标")
    assert "错误码 87" in mod.touch_capability()
    assert any("回退鼠标" in t for t in calls["log"])


def test_touch_success_does_not_touch_mouse(cc, monkeypatch):
    """触控注入成功时不应产生任何鼠标事件（保持触控优先语义）"""
    mod, calls = cc
    monkeypatch.setattr(mod, "_inject_touch",
                        lambda x, y, flags: calls["touch"].append((x, y, flags)) or True)
    mod.do_click(600, 400)
    assert calls["touch"], "应有触控注入"
    assert calls["mouse"] == [], "触控成功时不应再走鼠标"
    assert mod._touch["state"] == "ok"
    assert mod.touch_capability() == "触控"


def test_later_frame_touch_failure_does_not_double_click(cc, monkeypatch):
    """多点点击中后续帧失败：不能再回退鼠标（否则已经点过的会又点一次）"""
    mod, calls = cc
    seen = {"n": 0}

    def _flaky(x, y, flags):
        if flags & mod.POINTER_FLAG_DOWN:
            seen["n"] += 1
            return seen["n"] == 1          # 第一帧成功，第二帧失败
        return True

    monkeypatch.setattr(mod, "_inject_touch", _flaky)
    mod.do_click(600, 400, clicks=2)
    assert calls["mouse"] == [], "后续帧失败不应回退鼠标（防重复点击）"
    assert mod._touch["state"] == "broken"


def test_touch_ready_false_when_no_digitizer(cc, monkeypatch):
    """无触控数字化仪时直接判不可用，不尝试注入"""
    mod, calls = cc
    mod._touch["state"] = "unknown"
    monkeypatch.setattr(mod.user32, "GetSystemMetrics", lambda i: 0)   # 无触控设备
    assert mod.touch_ready() is False
    assert mod._touch["state"] == "broken"
    mod.do_click(600, 400)
    assert calls["touch"] == [], "已知无触控时不应尝试注入"
    assert calls["mouse"] == [mod.MOUSEEVENTF_LEFTDOWN, mod.MOUSEEVENTF_LEFTUP]


def test_virtual_desktop_back_allowed_after_takeover(cc, monkeypatch):
    """被接管后仍允许 back/prev 返回用户桌面 —— 否则用户被留在空虚拟桌面上退不出来"""
    mod, _calls = cc
    switched = []
    monkeypatch.setattr(mod, "switch_virtual_desktop",
                        lambda a: switched.append(a) or "combo")
    monkeypatch.setattr(mod, "_refresh_env", lambda: None)
    mod._STATE["interrupted"] = True
    mod._STATE["paused"] = False
    try:
        refused = mod.tool_open_virtual_desktop({"action": "new"})
        assert "用户已接管" in refused
        assert switched == [], "接管中不应新建/切换桌面"
        msg = mod.tool_open_virtual_desktop({"action": "back"})
        assert switched == ["back"], "接管中仍应允许返回用户桌面"
        assert "已返回用户桌面" in msg
    finally:
        mod._STATE["interrupted"] = False


# ---------- 与主程序 MCP 客户端的端到端链路 ----------

def test_app_mcp_client_receives_screenshot_image():
    """用主程序真实 McpClient 连插件 server（stdio）：工具清单可见，截屏以 MCP image
    内容项返回为 data URL（喂视觉模型的关键链路）。无桌面会话的环境截不到图时容忍失败。"""
    from zhuzhu_Copilot.core import agent_mcp
    client = agent_mcp.McpClient("computer-control-mcp", {
        "name": "computer-control-mcp", "type": "stdio",
        "command": sys.executable, "args": [str(SERVER)]})
    try:
        schemas = client.list_tools()
        assert {"screen_shot", "click"} <= {s["function"]["name"] for s in schemas}
        text, images = client.call_tool("screen_shot", {"mode": "full"})
        assert "网格" in text or "[MCP 工具错误]" in text
        if images:
            assert images[0].startswith("data:image/png;base64,")
            assert len(images[0]) > 1000          # 真图而非占位
        else:
            assert "[MCP 工具错误]" in text        # 无桌面会话：如实返回错误
    finally:
        client.close()


# ---------- 随包分发登记 ----------

def test_shipped_plugin_registers_mcp_and_skill(iso):
    """插件随包分发：首次启动复制到用户插件目录，并自动登记 MCP 与技能"""
    agent_plugins.ensure_shipped_plugins()
    target = iso / "plugins" / "computer-control"
    assert (target / "server.py").is_file() and (target / "SKILL.md").is_file()
    servers = agent_skills.load_mcp_servers()
    entry = [s for s in servers if s.get("name") == "computer-control-mcp"]
    assert entry and entry[0]["type"] == "stdio"
    assert entry[0]["command"] == "python-test"
    assert entry[0]["args"] == [str(target / "server.py")]
    skill = iso / "agent" / "skills" / "computer-control" / "SKILL.md"
    assert skill.is_file()
    assert "电脑操控" in skill.read_text(encoding="utf-8")