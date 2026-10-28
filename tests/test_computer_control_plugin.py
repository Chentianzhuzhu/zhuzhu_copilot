"""computer-control 插件测试：元数据/技能规范、MCP 握手与工具集、接管状态机、离线自检、
随包登记、Web UI 页面与坐标换算。

插件 server 以真实子进程（stdio JSON-RPC）拉起；用例只调用只读/状态类工具，
**绝不注入键鼠输入**（避免测试干扰用户桌面）。"""
import importlib.util
import json
import subprocess
import sys
import time
import time
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
                  "scroll", "key_press", "control"}

# 窗口/应用管理工具（属必备能力）
MANAGE_TOOLS = {"manage_window", "open_app", "verify_point", "click_window"}

# 已彻底移除的元素罗列能力：插件改为纯坐标定位（screen_shot 取坐标 + click），
# 这些工具名不得再出现在 TOOLS / _HANDLERS 中（回归护栏）。
REMOVED_ELEMENT_TOOLS = {"list_elements", "click_element", "type_element",
                         "scroll_element", "drag_element", "inspect_point",
                         "list_menus", "list_rows"}


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
    for key in ("browser_open", "用户已接管", "resume",
                "open_virtual_desktop", "安全红线"):
        assert key in text, f"SKILL.md 缺少关键约定: {key}"


def test_skill_md_mandates_open_app_before_hunting_windows():
    """回归用户反馈：「应用在后台或最小化时 agent 疯狂调用工具查找位置」。

    修复的**一半**在代码（taskbar_open 点图标切窗口 / open_app 启动恢复校验），
    另一半在技能规范 —— 模型得知道「先截图看任务栏 → 有图标就点 → 没有才 open_app，
    全程别用 list_windows 找窗口」。这条规范一旦被删掉，问题就会复发，所以固化成断言。
    """
    text = (PLUGIN_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "open_app" in text, "技能规范必须把 open_app 写进流程"
    assert "taskbar_open" in text, "技能规范必须把任务栏点图标写进流程"
    assert "找窗口" in text, "必须明确禁止「自己找窗口」"
    # 顺序契约：截图 → 任务栏 → 后台面板 → open_app 兜底
    assert text.index("screen_shot(mode=\"full\")") < text.index("taskbar_open(index=N)") \
        < text.index("taskbar_open(where=\"panel\")") < text.index('open_app(name="微信")'), \
        "第 0 步的四级顺序必须是：截图 → 任务栏 → 「↑」面板 → open_app"
    # 复杂任务剧本（打开微信 → 给第 N 个人发消息 → 发文件 → 引用消息）三要素都要在
    for key in ("第 N 个人", "引用", "文件选择对话框"):
        assert key in text, f"SKILL.md 缺少复杂任务剧本要素: {key}"


def test_skill_md_prefers_full_screen_capture():
    """回归用户反馈：「Agent 有时只截窗口不用整屏，窗口被遮挡时就点偏」。

    截图策略必须写明**默认整屏**，并且不得再出现「优先截窗口」这类反向引导 ——
    模型完全按技能文档行动，文档写错方向，代码再准也救不回来。
    """
    text = (PLUGIN_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "优先截窗口" not in text, "不得再引导模型优先截窗口（会被遮挡物误导）"
    assert "默认截整屏" in text, "必须明确默认整屏"
    assert 'screen_shot(mode="full")' in text
    # 窗口截图只允许出现在「大窗口 / GPU 自绘需要 1:1」这两个受限场景
    assert "远大于屏幕" in text and "GPU 自绘界面" in text


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
    # REQUIRED_TOOLS 是「必须具备」的集合；增强会新增工具，故断言为超集而非等集
    assert REQUIRED_TOOLS <= set(data["tools"]), \
        f"缺少必需工具: {sorted(REQUIRED_TOOLS - set(data['tools']))}"
    assert MANAGE_TOOLS <= set(data["tools"]), \
        f"缺少窗口/应用管理工具: {sorted(MANAGE_TOOLS - set(data['tools']))}"
    leaked = REMOVED_ELEMENT_TOOLS & set(data["tools"])
    assert not leaked, f"元素罗列类工具应已删除，但仍注册着: {sorted(leaked)}"
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
    # 假光标：_move_absolute 会真的「移动」它，_cursor_pos 读回来的就是它。
    # 这样 _settle_at 的「按键前回读确认」才能被真实验证（真机上是 GetCursorPos）。
    pos = {"x": 0, "y": 0}

    def _move_abs(x, y):
        calls["move"].append((x, y))
        pos["x"], pos["y"] = int(x), int(y)

    monkeypatch.setattr(mod, "_move_human", lambda x, y, duration=0: calls["move"].append((x, y)))
    monkeypatch.setattr(mod, "_move_absolute", _move_abs)
    monkeypatch.setattr(mod, "_cursor_pos", lambda: (pos["x"], pos["y"]))
    monkeypatch.setattr(mod, "_send_mouse",
                        lambda flags, dx=0, dy=0, data=0: calls["mouse"].append(flags))
    return mod, calls


def test_touch_injection_failure_falls_back_to_mouse(cc):
    """显式请求触控但注入失败（如本机错误码 87）→ 自动回退鼠标，点击不丢失"""
    mod, calls = cc
    mod.do_click(600, 400, mode="touch")
    assert calls["touch"], "应先尝试触控注入"
    # 回退到鼠标：按下+抬起各一次
    assert calls["mouse"] == [mod.MOUSEEVENTF_LEFTDOWN, mod.MOUSEEVENTF_LEFTUP]
    # 能力状态转为 broken 并如实汇报原因
    assert mod._touch["state"] == "broken"
    assert mod.touch_capability().startswith("鼠标")
    assert "错误码 87" in mod.touch_capability()
    assert any("回退鼠标" in t for t in calls["log"])


def test_touch_success_does_not_touch_mouse(cc, monkeypatch):
    """显式请求触控且注入成功时不应产生任何鼠标事件（触控语义完整）"""
    mod, calls = cc
    monkeypatch.setattr(mod, "_inject_touch",
                        lambda x, y, flags: calls["touch"].append((x, y, flags)) or True)
    mod.do_click(600, 400, mode="touch")
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
    mod.do_click(600, 400, clicks=2, mode="touch")
    assert calls["mouse"] == [], "后续帧失败不应回退鼠标（防重复点击）"
    assert mod._touch["state"] == "broken"


def test_touch_ready_false_when_no_digitizer(cc, monkeypatch):
    """无触控数字化仪时直接判不可用，不尝试注入"""
    mod, calls = cc
    mod._touch["state"] = "unknown"
    monkeypatch.setattr(mod.user32, "GetSystemMetrics", lambda i: 0)   # 无触控设备
    assert mod.touch_ready() is False
    assert mod._touch["state"] == "broken"
    mod.do_click(600, 400, mode="touch")
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
        assert "截取" in text or "[MCP 工具错误]" in text
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

# ============================================================================
# 屏幕光环（aura.py）与「元素罗列能力已移除」回归护栏
# ============================================================================

AURA = PLUGIN_DIR / "aura.py"


def _load_sibling(name: str, filename: str):
    """加载插件目录下的兄弟模块（aura.py）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, PLUGIN_DIR / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_uia_module_is_gone_and_server_compiles():
    """uia.py 必须已删除；server.py 与 aura.py 语法必须正确。

    插件已改为纯坐标定位（screen_shot 取坐标 → click），不再读取控件树，
    因此承载元素罗列能力的 uia.py 不应再存在于包内。窗口原语已搬进 server.py。
    """
    assert not (PLUGIN_DIR / "uia.py").exists(), \
        "uia.py 应已删除（元素罗列能力已移除，窗口原语已搬进 server.py）"
    for path in (SERVER, AURA):
        proc = subprocess.run([sys.executable, "-m", "py_compile", str(path)],
                              capture_output=True, check=False, timeout=60)
        assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")


def test_server_has_no_uia_dependency():
    """server.py 不得再 import 或引用 uia（否则删模块后运行期会 NameError）。"""
    src = SERVER.read_text(encoding="utf-8")
    assert "import uia" not in src
    assert "uia." not in src, "server.py 里仍有 uia.* 调用残留"


def test_element_tools_are_not_registered():
    """回归护栏：8 个元素罗列类工具不得出现在 TOOLS / _HANDLERS 中。"""
    mod = _load_module()
    declared = {t["name"] for t in mod.TOOLS}
    handled = set(mod._HANDLERS)
    leaked = REMOVED_ELEMENT_TOOLS & (declared | handled)
    assert not leaked, f"以下工具应已删除但仍被注册: {sorted(leaked)}"
    # 护栏自身要够严：确认这些名字确实不在
    assert mod._HANDLERS, "handlers 不应为空"
    assert "click" in mod._HANDLERS and "click_window" in mod._HANDLERS


def test_coordinate_tools_still_have_their_helpers():
    """坐标类工具依赖的通用辅助函数必须存在（删除元素块时曾误删过它们）。

    _join_notes / _owner_window / _hit_check 不属于元素能力，是被保留的坐标
    工具共用的；一旦缺失，click_window / verify_point 等会在运行期 NameError。
    """
    mod = _load_module()
    for fn in ("_join_notes", "_owner_window", "_hit_check", "_target_hwnd",
               "_is_popup", "_focus_warning", "_prepare_target",
               "_window_sig", "_sig_change_ratio", "_click_verify",
               "_region_snapshot", "_region_diff", "_window_rel_to_screen"):
        assert callable(getattr(mod, fn, None)), f"缺少辅助函数 {fn}"
    # _owner_window 取不到时也必须安全返回 0，不得抛异常
    assert mod._owner_window(-1, -1) in (0,) or isinstance(mod._owner_window(-1, -1), int)
    # _join_notes 跳过空串并补句号
    assert mod._join_notes("", "  ", "甲") == "甲。 "
    assert mod._join_notes("甲。", "乙") == "甲。乙。 "


def test_aura_module_importable_and_reports_capability():
    """提示层模块可加载，并如实汇报能力（光带 + 胶囊提示窗）"""
    aura = _load_sibling("cc_aura", "aura.py")
    st = aura.aura_state()
    assert set(st) == {"active", "failed", "error", "capability"}
    cap = st["capability"]
    assert "光带" in cap and "泛光" in cap, cap
    assert "胶囊" in cap, cap
    # 颜色工具函数：HSL→RGB 必须落在 0..255 且亮度符合预期
    for hue in (0.0, 0.25, 0.5, 0.75, 0.99):
        r, g, b = aura._hsl_to_rgb(hue, 0.85, 0.7)
        assert all(0 <= v <= 255 for v in (r, g, b))
    # 深色端应比浅色端暗（保证在浅色壁纸上也能看见）
    dark = sum(aura._hsl_to_rgb(0.3, 0.85, 0.25))
    light = sum(aura._hsl_to_rgb(0.3, 0.85, 0.9))
    assert dark < light


def test_aura_status_line_explains_why_the_overlay_is_missing():
    """提示层不出现时必须有**可读的线索**，不能再让用户和模型一起猜。"""
    aura = _load_sibling("cc_aura_st", "aura.py")
    # aura_status_line 读的是模块级单例，失败态要在单例上模拟
    aura._aura._failed = True
    aura._aura._error = "CreateWindowExW 失败 err=5"
    assert "CreateWindowExW" in aura.aura_status_line()
    assert "不可用" in aura.aura_status_line()
    aura._aura._failed = False


def test_aura_failure_does_not_lock_the_overlay_out_forever():
    """一次建窗失败不能让提示层**永久失明**（早先 _failed 会锁死 start()）。"""
    aura = _load_sibling("cc_aura_lock", "aura.py")
    a = aura.Aura(fps=2)
    a._failed = True
    a._error = "建窗失败"
    started = []
    a._run_ring = lambda: started.append("ring")
    a._run_hint = lambda: started.append("hint")
    a.start()
    time.sleep(0.1)
    assert started, "失败后应允许重试"
    assert a._failed is False, "重试前应清掉失败标记"


def test_mouse_move_needs_a_real_displacement_to_take_over(monkeypatch):
    """鼠标轻微移动不得把 AI 踢下线（那会让任务直接卡住）。"""
    mod = _load_module()
    mod._STATE["interrupted"] = False
    mod._STATE["active_until"] = time.time() + 60
    pos = {"x": 100, "y": 100}
    monkeypatch.setattr(mod, "_cursor_pos", lambda: (pos["x"], pos["y"]))
    # 别让上一次注入的余波把事件判成 AI 自己发的
    monkeypatch.setattr(mod, "_injecting", lambda: False)
    monkeypatch.setattr(mod, "_notify_user_takeover", lambda reason: None)   # 不弹真窗口
    w = mod.InputWatcher()      # 构造时也要读到假坐标（否则 _last_xy 差几百像素）
    # 每次移动 3px，累计不到阈值 → 不接管
    for _ in range(5):
        pos["x"] += 3
        w._hit(0, mod.WM_MOUSEMOVE)
    assert mod._STATE["interrupted"] is False, "轻微移动不应判定接管"
    # 累计位移超过阈值 → 接管
    pos["x"] += 200
    w._hit(0, mod.WM_MOUSEMOVE)
    assert mod._STATE["interrupted"] is True
    assert "鼠标移动" in (mod._STATE.get("interrupt_reason") or "")


def test_mouse_drift_across_a_long_window_does_not_take_over(monkeypatch):
    """零星手抖/触控板噪声跨几分钟累加，不得把用户误判成「正在操控电脑」。"""
    mod = _load_module()
    mod._STATE.update({"interrupted": False, "active_until": time.time() + 60})
    pos = {"x": 100, "y": 100}
    monkeypatch.setattr(mod, "_cursor_pos", lambda: (pos["x"], pos["y"]))
    monkeypatch.setattr(mod, "_injecting", lambda: False)
    monkeypatch.setattr(mod, "_notify_user_takeover", lambda reason: None)
    monkeypatch.setattr(mod, "_log", lambda who, text: None)
    w = mod.InputWatcher()
    for _ in range(20):                   # 每次 40px，但每次之间都隔了远超时间窗
        pos["x"] += 40
        w._last_move_ts = time.time() - mod.MOVE_ACC_WINDOW_S - 1
        w._hit(0, mod.WM_MOUSEMOVE)
    assert mod._STATE["interrupted"] is False, "跨时间窗的零星位移不应累计成接管"


def test_stale_takeover_flag_auto_clears_for_a_new_task(monkeypatch):
    """跨任务残留的「已接管」必须自动失效：否则新任务每个工具都回「用户已接管」。"""
    mod = _load_module()
    monkeypatch.setattr(mod, "_log", lambda who, text: None)
    now = time.time()
    mod._STATE.update({"interrupted": True,
                       "interrupt_reason": "检测到真人鼠标移动",
                       "interrupt_ts": now - mod.INTERRUPT_STALE_S - 5,
                       "active_until": now - 10})
    mod._touch_session()
    assert mod._STATE["interrupted"] is False, "上一个任务的接管标记不得污染新任务"
    assert mod._STATE["interrupt_reason"] == ""
    # 同一任务内的接管照旧有效（会话仍在有效期 / 接管刚发生）
    mod._STATE.update({"interrupted": True, "interrupt_reason": "检测到真人键盘输入",
                       "interrupt_ts": now, "active_until": now + 30})
    mod._touch_session()
    assert mod._STATE["interrupted"] is True


def test_aura_notify_is_available_and_rejects_empty_text():
    """接管提示窗的入口必须存在；空文本不得建窗（钩子线程里不该做无意义的事）。"""
    aura = _load_sibling("cc_aura_notify", "aura.py")
    a = aura.Aura()
    assert hasattr(a, "notify") and hasattr(aura, "notify")
    assert a.notify("") is False
    assert a.notify("   ") is False


def test_aura_fades_after_the_control_session_expires():
    """提示层必须随会话过期淡出。

    实测发现：任务结束后没有任何地方关掉光带，会话过期很久屏幕四边还在闪，
    用户会误以为 AI 还在操作。这里固化成「过期 + 宽限 3s 就该淡出」。
    """
    mod = _load_module()
    now = time.time()
    try:
        mod._STATE["aura"] = True
        mod._STATE["active_until"] = now + 30
        assert mod._aura_should_fade(now) is False, "会话有效期内不能淡出"
        assert mod._aura_should_fade(now + 30 + mod.AURA_IDLE_GRACE_S + 1) is True
        mod._STATE["aura"] = False
        assert mod._aura_should_fade(now + 9999) is False, "未点亮时无需淡出"
        mod._STATE["aura"] = True
        mod._STATE["active_until"] = 0.0
        assert mod._aura_should_fade(now) is False, "从未开启会话时不能误判"
    finally:
        mod._STATE["aura"] = False
        mod._STATE["active_until"] = 0.0


def test_aura_pixels_cover_all_four_edges():
    """光带像素必须覆盖完整四边（早先实现漏掉左右竖边，导致屏幕两侧无光带）"""
    aura = _load_sibling("cc_aura", "aura.py")
    a = aura.Aura()
    a._width, a._height = 1920, 1080
    pixels = a._build_pixels()
    offs = {off // 4 for off, _, _ in pixels}       # 清单里存的是字节偏移 = 像素*4
    w, h, t = 1920, 1080, a.thickness
    # 四条边的中点都必须有像素
    assert (0 * w + 0) in offs                      # 左上角
    assert (540 * w + 0) in offs                    # 左边中点
    assert (540 * w + (w - 1)) in offs              # 右边中点
    assert ((h - 1) * w + 960) in offs              # 下边中点
    assert (0 * w + 960) in offs                    # 上边中点
    # 屏幕中心（内部）绝不能被点亮，否则会遮挡用户视线
    assert (540 * w + 960) not in offs
    # 粗细范围内全覆盖、无缺口
    for y in range(0, h, 137):
        for x in range(0, t):
            assert (y * w + x) in offs, f"左边 ({x},{y}) 缺像素"
    # 每帧重写的"核心带"必须严格小于整条光带（泛光层只在建窗时写一次，
    # 否则每帧多写 1 倍像素，12fps 下直接把 CPU 打满）
    assert 0 < len(a._core_px) < len(pixels)
    assert len(a._glow_px) > 0


def test_aura_hue_is_continuous_around_the_corners():
    """色相必须沿整圈周长连续 —— 转角不能出现颜色断层。

    早先横边用 x/w、竖边用 y/h 各自归一化，两条边在四个角上色相对不上，
    屏幕上能直接看到一条生硬的颜色切缝。
    """
    aura = _load_sibling("cc_aura", "aura.py")
    a = aura.Aura()
    a._width, a._height = 1920, 1080
    pixels = a._build_pixels()
    pos = {off // 4: pi for off, pi, _ in pixels}
    w, h = 1920, 1080
    for cx, cy, dx, dy in ((1, 1, 1, 1), (w - 2, 1, -1, 1),
                           (w - 2, h - 2, -1, -1), (1, h - 2, 1, -1)):
        # 转角处：沿上/下边一个像素 vs 沿左/右边一个像素，色相索引必须相邻
        a_edge = pos[cy * w + cx + dx]
        b_edge = pos[(cy + dy) * w + cx]
        d = abs(a_edge - b_edge)
        assert min(d, 256 - d) <= 2, (
            f"转角 ({cx},{cy}) 色相断层：{a_edge} vs {b_edge}")


def test_new_control_tools_registered():
    """窗口/应用管理工具必须同时出现在 TOOLS 与 _HANDLERS"""
    mod = _load_module()
    declared = {t["name"] for t in mod.TOOLS}
    handled = set(mod._HANDLERS)
    assert declared == handled, f"未注册={declared - handled} 多余={handled - declared}"
    for name in ("click_window", "verify_point", "open_app", "manage_window"):
        assert name in declared, f"缺少工具 {name}"
        assert callable(mod._HANDLERS[name])


def test_control_tools_drive_hint_but_queries_do_not():
    """只有真正改变桌面的工具才点亮提示层，查询类不应打扰用户"""
    mod = _load_module()
    assert "click" in mod._CONTROL_TOOLS
    assert "click_window" in mod._CONTROL_TOOLS
    assert "manage_window" in mod._CONTROL_TOOLS
    for query in ("screen_shot", "screen_info", "list_windows", "zoom_in"):
        assert query not in mod._CONTROL_TOOLS, f"{query} 不该点亮提示层"


def test_redundant_aura_and_web_preview_tools_are_gone():
    """回归护栏：光环开关与 web 预览工具都必须移除。

    提示层现在是**自动**的（操控动作一发生就亮），不该再让模型手动调 `aura`；
    也绝不能往用户浏览器里塞一个可视化页面 —— 用户明确要求「不要打开 web 预览」。
    """
    mod = _load_module()
    declared = {t["name"] for t in mod.TOOLS}
    handled = set(mod._HANDLERS)
    for gone in ("aura", "web_url"):
        assert gone not in declared, f"{gone} 工具应已移除"
        assert gone not in handled, f"{gone} handler 应已移除"
        assert not hasattr(mod, f"tool_{gone}"), f"tool_{gone} 实现应已删除"
    # 明确写死「不要打开 web 预览」这条源头：进程不再自动起 HTTP 可视化服务
    assert not hasattr(mod, "tool_aura")
    assert not hasattr(mod, "tool_web_url")
    # 进程启动不得自动起 HTTP 可视化服务（页面只有 --selftest 才起）
    src = SERVER.read_text(encoding="utf-8")
    main_src = src.split("def main():", 1)[1].split("def _selftest", 1)[0]
    assert "ensure_http(" not in main_src, "main() 不该启动本地可视化服务"
    # 技能规范里也不能再要求模型去开网页（只允许出现「不需要 web_url」这类反向说明）
    skill = (PLUGIN_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "调用 `web_url`" not in skill
    assert "preview_open(url=该地址)" not in skill


def test_overlays_are_excluded_from_screen_capture():
    """提示层必须被排除在截图之外，且三条抓屏路径都套了这道保护。"""
    aura_src = AURA.read_text(encoding="utf-8")
    assert "SetWindowDisplayAffinity" in aura_src
    assert "WDA_EXCLUDEFROMCAPTURE" in aura_src
    assert "def capture_guard" in aura_src
    src = SERVER.read_text(encoding="utf-8")
    assert "def _no_overlay" in src
    # 整屏截图 / 矩形抓取 / 区域快照 / 窗口屏幕兜底 —— 四条路径都要有
    assert src.count("with _no_overlay():") >= 4, "有抓屏路径没套截图排除"


def test_start_revives_the_hint_thread_independently():
    """回归：胶囊线程先退出后，再点亮必须能把胶囊补回来（不能只亮光带）。

    两个渲染线程生命周期不一致 —— 淡出到底时胶囊线程先退出。早先 `start()` 只看
    光带线程，于是出现过「之后每次点亮都只有光带、永远没有胶囊」的故障。
    这里用桩函数替掉真实渲染，完全不建窗口，纯测线程自愈逻辑。
    """
    aura = _load_sibling("cc_aura_rt", "aura.py")
    a = aura.Aura(fps=2)
    started = []

    def fake_ring():
        started.append("ring")
        a._stop.wait()

    def fake_hint():
        started.append("hint")          # 立刻返回，模拟"胶囊线程已退出"

    a._run_ring = fake_ring
    a._run_hint = fake_hint
    try:
        a.set_active(True)
        for _ in range(100):
            if started.count("hint") == 1 and "ring" in started:
                break
            time.sleep(0.01)
        assert started == ["ring", "hint"], started
        # 下一次操控动作（set_active(True)）必须把死掉的胶囊线程补回来
        a.set_active(True)
        for _ in range(100):
            if started.count("hint") == 2:
                break
            time.sleep(0.01)
        assert started == ["ring", "hint", "hint"], started
        assert started.count("ring") == 1, "光带线程还活着，不该被重复拉起"
    finally:
        a.stop()


def test_overlay_windows_report_native_capture_exclusion():
    """分层窗口建出来就该带上 WDA_EXCLUDEFROMCAPTURE；做不到时 capture_guard 必须真藏窗口。

    两条路径都要覆盖：原生可用 → 截图期间窗口照常显示（不闪），只是不进画面；
    原生不可用 → 截前 ShowWindow(SW_HIDE)、截后恢复。
    """
    aura = _load_sibling("cc_aura_cap", "aura.py")
    w = aura._LayeredWindow("zhuzhu-aura-selftest")
    if not w.create(0, 0, 64, 32):
        pytest.skip("当前会话无法创建分层窗口（无桌面），跳过截图排除校验")
    try:
        if w._excluded:
            assert aura.capture_exclude_supported() is True
            with aura.capture_guard():
                assert w._visible is True, "原生排除可用时不该隐藏窗口（会闪一下）"
            assert w._visible is True
        else:
            assert aura.capture_exclude_supported() is False
            with aura.capture_guard():
                assert w._visible is False, "退化路径必须真的把窗口藏起来"
            assert w._visible is True, "截图结束后必须恢复显示"
    finally:
        w.destroy()


def test_overlay_windows_are_hidden_from_window_list():
    """自己的提示层绝不能出现在 list_windows 里（否则模型会看到一扇覆盖整屏的窗口）。

    过滤按**进程归属**（is_own_window）而不是窗口标题前缀：光带/胶囊的窗口线程只
    跑渲染循环、不泵 Windows 消息，对它取标题会永久阻塞调用方 —— 必须在取标题之前
    就用 GetWindowThreadProcessId 判断并跳过。
    """
    mod = _load_module()
    assert callable(mod.is_own_window)
    # 建窗名字仍应区分边条（光带是四条独立边条：top/bottom/left/right）
    aura_src = AURA.read_text(encoding="utf-8")
    assert '"zhuzhu-aura-hint"' in aura_src
    assert 'zhuzhu-aura-{name}' in aura_src, "光带窗口名应由边名拼出"
    for side in ("top", "bottom", "left", "right"):
        assert f'"{side}"' in aura_src, f"缺少 {side} 边条"
    wins = mod.list_windows()
    leaked = [w for w in wins if mod.is_own_window(int(w.get("hwnd") or 0))]
    assert not leaked, f"提示层泄漏进窗口清单: {leaked}"


def test_aura_ring_wraps_the_whole_monitor_not_the_work_area(monkeypatch):
    """光带必须贴**物理整屏**（rcMonitor）四边。

    回归：旧实现围的是 rcWork（可用桌面区域），底部/侧边在任务栏处直接缺一段，
    用户看到的是「光带只框住任务栏上方，框不住整个屏幕」。
    """
    aura = _load_sibling("cc_aura_geom", "aura.py")
    monkeypatch.setattr(aura, "_monitor_rect", lambda: (0, 0, 1920, 1080))
    monkeypatch.setattr(aura, "_work_area", lambda: (0, 0, 1920, 1040))

    class _FakeLayer:
        def __init__(self, name):
            self.name = name
            self._x = self._y = self._w = self._h = 0
            self._mv = None

        def create(self, x, y, w, h):
            self._x, self._y, self._w, self._h = x, y, w, h
            return True

        def push(self, sca: int = 0):
            pass

        def destroy(self):
            pass

    frames = []

    def _mk(name):
        frames.append(_FakeLayer(name))
        return frames[-1]

    monkeypatch.setattr(aura, "_LayeredWindow", _mk)
    a = aura.Aura()
    assert a._create_ring(), a._error
    by_side = {f.name.rsplit("-", 1)[-1]: f for f in frames}
    assert by_side["top"]._y == 0
    assert by_side["top"]._h == a.thickness
    # 关键：底边贴到屏幕物理下沿（1080，而不是工作区 1040）
    assert by_side["bottom"]._y + by_side["bottom"]._h == 1080
    assert by_side["right"]._x + by_side["right"]._w == 1920
    assert by_side["left"]._x == 0


def test_hint_capsule_is_topmost_and_reasserted_frequently():
    """胶囊与光带都必须周期性重申置顶（任务栏是 topmost band，会抢到前面）。"""
    aura = _load_sibling("cc_aura_top", "aura.py")
    assert 0 < aura.AURA_TOP_REFRESH_S <= 1.0, "置顶重申间隔过大，任务栏会长时间盖住底边"
    src = AURA.read_text(encoding="utf-8")
    assert src.count("AURA_TOP_REFRESH_S") >= 3, "光带/胶囊两条渲染循环都要按该间隔重申置顶"


def test_aura_dpi_aware_and_self_heals_missing_windows(monkeypatch):
    """提示层：进程自带 DPI 感知；窗口丢失/上次失败时必须自愈重建。

    回归：光带/胶囊「莫名不再出现」只发生在**线程还活着但窗口没建出来**的情形
    （create 偶发失败、显示器切换）—— 旧实现只补死掉的线程，于是永远不再重建。
    """
    aura = _load_sibling("cc_aura_heal", "aura.py")
    src = AURA.read_text(encoding="utf-8")
    assert "def _ensure_dpi_awareness" in src
    assert "SetProcessDpiAwarenessContext" in src
    # DPI 感知生效：监视器矩形必须等于物理分辨率（缩放屏上未感知进程这里会变小）
    assert aura._monitor_rect()[2:] == aura._screen_size()

    class _Alive:
        def is_alive(self):
            return True

        def join(self, _t=None):
            pass

    a = aura.Aura(fps=2)
    calls = []
    a.start = lambda: calls.append(1) or True
    a._failed = True
    a._bands = []
    a._hint = None
    a._thread, a._hint_thread = _Alive(), _Alive()
    a.set_active(True)
    assert calls == [1], "窗口丢失时必须自愈重建（不能只依靠补死线程）"

    calls.clear()
    a._failed = False
    a._bands = [object()]
    a._hint = type("_H", (), {"_win": object()})()
    a.set_active(True)
    assert calls == [], "健康状态不应反复重建（避免闪屏）"


def test_hint_capsule_renders_text_and_eight_same_size_dots():
    """胶囊提示窗：白色胶囊底 + 深色文字 + 8 个等大圆点组成的转圈环。"""
    aura = _load_sibling("cc_aura", "aura.py")
    assert aura.HINT_TEXT == "Agent正在操控您的电脑，移动鼠标/按任意键打断"
    assert aura.AURA_HINT_DOTS == 8
    h = aura.HintCapsule()
    if not h.build(aura.HINT_TEXT):
        pytest.skip("当前会话无法创建分层窗口（无桌面），跳过渲染校验")
    try:
        mv = h._win._mv
        w = h._w
        pad = h._pad
        # 胶囊底必须接近白色（用户要求），不再是无障碍涂黑的旧底色
        mid = (h._h // 2) * w + pad + 4
        assert mv[mid * 4] > 235 and mv[mid * 4 + 1] > 235 and mv[mid * 4 + 2] > 235, \
            f"胶囊底不是白色：{tuple(mv[mid * 4:mid * 4 + 4])}"
        # 贴边高光：最上沿内侧一圈必须比底色更白（纯白 255）
        rim_y = pad + 1
        rim_o = (rim_y * w + w // 2) * 4
        assert min(mv[rim_o], mv[rim_o + 1], mv[rim_o + 2]) >= 250, \
            f"边缘没有高光：{tuple(mv[rim_o:rim_o + 4])}"
        # 胶囊外侧还要有一圈淡蓝白高光晕（透明边距里，alpha>0 且偏亮）
        halo_o = (pad - 1) * w * 4 + (w // 2) * 4
        assert mv[halo_o + 3] > 20, "胶囊外侧没有高光晕"
        assert min(mv[halo_o], mv[halo_o + 1], mv[halo_o + 2]) >= 215, \
            f"高光晕颜色不亮：{tuple(mv[halo_o:halo_o + 4])}"
        # 文字区必须出现**深色**像素（白底胶囊上的深灰蓝文字）
        x0, y0, x1, y1 = h._box
        text_dark = 0
        for py in range(h._h):
            for px in range(0, x0):
                o = (py * w + px) * 4
                if mv[o + 3] > 200 and mv[o] < 150 and mv[o + 1] < 150 and mv[o + 2] < 150:
                    text_dark += 1
        assert text_dark > 200, f"文字没渲染成深色（深色像素 {text_dark}）"
        # 转圈区域：圆点必须是 8 个互不相连的连通块，且尺寸一致。
        # 角度用相对 _angle0 的固定偏移，避免"跑测试的时刻"让相位随机化。
        bg = (aura.HINT_BG[2], aura.HINT_BG[1], aura.HINT_BG[0])

        def _components(spin_phase):
            h.draw_dots(h._angle0 + spin_phase)
            marks = set()
            for py in range(y0, y1):
                for px in range(x0, x1):
                    o = (py * w + px) * 4
                    if (abs(mv[o] - bg[0]) + abs(mv[o + 1] - bg[1])
                            + abs(mv[o + 2] - bg[2])) > 12:
                        marks.add((px, py))
            out = []
            while marks:
                stack = [marks.pop()]
                comp = [stack[0]]
                while stack:
                    cx, cy = stack.pop()
                    for nb in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                        if nb in marks:
                            marks.discard(nb)
                            stack.append(nb)
                            comp.append(nb)
                out.append(comp)
            return out

        # 转 4 个不同相位都要数出 8 个点（任何角度下圆点都不能糊成一团）
        for phase in (0.0, 0.28, 0.57, 0.86):
            comps = _components(phase)
            assert len(comps) == 8, (
                f"相位 {phase} 应看到 8 个圆点，实际连出 {len(comps)} 块")
            for comp in comps:
                bw = max(p[0] for p in comp) - min(p[0] for p in comp) + 1
                bh = max(p[1] for p in comp) - min(p[1] for p in comp) + 1
                assert 3 <= bw <= 6 and 3 <= bh <= 6, f"圆点尺寸不一致: {bw}x{bh}"
    finally:
        h.destroy()


def test_click_does_not_retry_when_the_window_changed_at_all(monkeypatch):
    """整窗只要有**任何**采样点变化，就不许再点一次。

    这是「明明已经关掉了桌宠，AI 又点了一大片到桌面」的根因修复：副作用发生在
    界面别处时，落点那一小块可能看不出变化（桌宠消失后那块变成壁纸，颜色接近 →
    变化很小），而整窗指纹是有变化的。此时再换候选点一次，落点必然落到别处。
    """
    mod = _load_module()
    mod._STATE["interrupted"] = mod._STATE["paused"] = False
    clicks = []
    monkeypatch.setattr(mod, "_resolve_point", lambda a, *k: (500, 400))
    monkeypatch.setattr(mod, "_apply_snap", lambda a, x, y, default_on=True: (510, 402, ""))
    monkeypatch.setattr(mod, "_ensure_click_target", lambda x, y, a: "")
    monkeypatch.setattr(mod, "_input_mode", lambda a: "mouse")
    monkeypatch.setattr(mod, "_pre_click_probe", lambda x, y, h: ())
    monkeypatch.setattr(mod, "_cursor_pos", lambda: (510, 402))
    monkeypatch.setattr(mod, "_refresh_env", lambda: None)
    monkeypatch.setattr(mod, "do_click",
                        lambda x, y, b, c, mode="auto": clicks.append((x, y)) or True)
    monkeypatch.setattr(mod, "_alt_snap_point", lambda *a: (530, 404))
    # 局部没变化，但整窗变了 1 个采样点（0.17%，没到阈值）
    monkeypatch.setattr(mod, "_click_verify_ex", lambda *a: (False, 0.002, "没到阈值"))
    out = mod._HANDLERS["click"]({"x": 500, "y": 400, "verify": True})
    assert clicks == [(510, 402)], f"界面有变化时不得重复点击，实得 {clicks}"
    assert "没到阈值" in out


def test_resolve_point_autofixes_out_of_range_physical_coordinates(monkeypatch):
    """坐标超出截图范围 → 判定为屏幕物理坐标，直接用（治「点偏一大片」）。

    典型误用：refine_point / verify_point 返回的是**物理坐标**，模型直接抄进
    click(x,y) 却没带 space="screen"，于是被按图像坐标再缩放一次
    （1280 图在 1920 屏上放大 1.5 倍）—— 表现就是「偏了一大片」。
    """
    mod = _load_module()
    mod._STATE["interrupted"] = mod._STATE["paused"] = False
    mod._VIEW.update({"x0": 0, "y0": 0, "w": 1920, "h": 1080,
                      "img_w": 1280, "img_h": 720, "ts": time.time()})
    mod._take_coord_note()
    # 1600 超出 1280 的图像宽度 → 必须按物理坐标
    x, y = mod._resolve_point({"x": 1600, "y": 500})
    assert (x, y) == (1600, 500), (x, y)
    note = mod._take_coord_note()
    assert "自动纠正" in note and "物理坐标" in note
    # 范围内的仍然是图像坐标（会被缩放换算）
    x2, y2 = mod._resolve_point({"x": 640, "y": 360})
    assert (x2, y2) == (960, 540), (x2, y2)
    assert mod._take_coord_note() == ""


def test_tool_execution_is_serial_and_reports_errors(monkeypatch):
    """工具在主循环线程**直接串行执行**，异常返回 isError 而不是让通道卡死。

    这是「MCP 工具频繁超时」的根因修复：早先用一个 max_workers=1 的线程池做看门狗，
    `fut.result(timeout=…)` 超时返回后那个工具仍在 worker 里跑，worker 被它占着，
    下一个请求只能排队 → 一个工具偶然卡一下，后面所有工具全部跟着排队超时（雪崩）。
    现在恢复直接串行：单个工具耗时即其耗时，绝不排队。
    """
    mod = _load_module()
    calls = []

    def _ok(args):
        calls.append(("ok", args))
        return "done"

    def _boom(args):
        calls.append(("boom", args))
        raise ValueError("bad arg")

    mod._HANDLERS["_serial_ok"] = _ok
    mod._HANDLERS["_serial_boom"] = _boom

    r = mod._handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                     "params": {"name": "_serial_ok", "arguments": {"a": 1}}})
    assert r["result"]["isError"] is False
    assert r["result"]["content"][0]["text"] == "done"

    r2 = mod._handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                      "params": {"name": "_serial_boom", "arguments": {}}})
    assert r2["result"]["isError"] is True
    assert "ValueError" in r2["result"]["content"][0]["text"]
    assert calls == [("ok", {"a": 1}), ("boom", {})]


def test_no_worker_thread_pool_in_server(monkeypatch):
    """源码里不得再出现单 worker 线程池看门狗 —— 它是「频繁超时」雪崩的根源。"""
    src = SERVER.read_text(encoding="utf-8")
    assert "ThreadPoolExecutor" not in src, \
        "server.py 里出现了线程池：单 worker + 超时会让后续工具全部排队超时"
    assert "concurrent.futures" not in src


def test_window_sig_no_longer_uses_printwindow():
    """整窗指纹不得再用 PrintWindow —— 它跨进程渲染一帧要 600ms+，是超时的主因。"""
    src = SERVER.read_text(encoding="utf-8")
    i = src.index("def _window_sig(")
    j = src.index("def _sig_change_ratio(", i)
    body = src[i:j]
    assert "user32.PrintWindow(" not in body, "整窗指纹还在用 PrintWindow（慢 600ms+）"
    assert "StretchBlt" in body
    assert "COLORON" in body, "指纹缩放应走最近邻"


def test_manage_window_state_and_errors():
    """manage_window 的状态查询与参数校验（不真的改动窗口）"""
    mod = _load_module()
    text = mod._HANDLERS["manage_window"]({"action": "state"})
    assert "当前窗口列表" in text
    with pytest.raises(ValueError):
        mod._HANDLERS["manage_window"]({"action": "no_such_action"})


def test_click_window_requires_verbose_errors_not_silent_misclick(monkeypatch):
    """坐标类工具的参数校验必须给出可读错误（而不是静默点错地方）

    click_window 无 hwnd/title 时回退到当前前台窗口（这是设计行为），因此这里用
    「显式传了一个找不到的 hwnd」来触发校验分支，并断言错误里给了排查线索。
    """
    mod = _load_module()
    monkeypatch.setattr(mod, "find_window", lambda **kw: 0)
    monkeypatch.setattr(mod, "foreground_window", lambda: 0)
    with pytest.raises(ValueError) as ei:
        mod._HANDLERS["click_window"]({"hwnd": 999999})
    assert "list_windows" in str(ei.value) or "窗口" in str(ei.value)


def test_screenshot_is_clean_by_default():
    """发给 AI 的截图默认纯净：不画坐标网格/刻度/准星（坐标轴只在显式要求时才叠加）"""
    import inspect
    mod = _load_module()
    for fn in (mod.grab_screen, mod.grab_window, mod.grab_zoom):
        params = inspect.signature(fn).parameters
        assert params["grid"].default is False, f"{fn.__name__} 默认不应叠加网格"
    assert inspect.signature(mod.grab_screen).parameters["cursor"].default is False
    assert inspect.signature(mod.grab_zoom).parameters["cursor"].default is False
    schema = next(t for t in mod.TOOLS if t["name"] == "screen_shot")
    assert "纯净" in schema["description"]
    assert schema["inputSchema"]["properties"]["grid"]["type"] == "boolean"
    assert "_draw_grid" not in inspect.getsource(mod.tool_screen_shot), "工具层不应再画网格"


def test_drag_precision_settles_and_exact_lands(monkeypatch):
    """拖拽精度：两条注入路径都必须精确落在目标点

    - 触控路径：最后一帧触点必须在目标坐标
    - 鼠标回退路径：分步数不低于下限（步数太少会被系统当成瞬移而非拖拽）、
      末步精确落点、且顺序为「按下 → 移动 → 抬起」
    """
    mod, calls = _cc_fixture(_load_module(), monkeypatch, touch_ok=True)
    mod.do_drag(100, 100, 400, 300, duration=0.3, hold=0.15, mode="touch")
    assert calls["touch"][-1][:2] == (400, 300), "触控末帧必须落在目标点"

    mod, calls = _cc_fixture(_load_module(), monkeypatch, touch_ok=False)
    mod.do_drag(100, 100, 400, 300, duration=0.3, hold=0.15)
    assert (400, 300) in calls["move"], "末步必须精确落在目标点"
    assert len(calls["move"]) >= mod.DRAG_MIN_STEPS
    assert calls["mouse"].index(mod.MOUSEEVENTF_LEFTDOWN) < \
        calls["mouse"].index(mod.MOUSEEVENTF_LEFTUP)


def test_click_keeps_down_up_at_same_pixel(monkeypatch):
    """点击精度：鼠标路径下按下与抬起之间不得移动指针（否则会变成拖拽）"""
    mod, calls = _cc_fixture(_load_module(), monkeypatch, touch_ok=False)
    calls["move"].clear()
    mod.do_click(640, 480)
    assert calls["mouse"].count(mod.MOUSEEVENTF_LEFTDOWN) == 1
    assert calls["mouse"].count(mod.MOUSEEVENTF_LEFTUP) == 1
    # 全部移动都应落在点击点（按下期间零漂移）
    assert set(calls["move"]) == {(640, 480)}


def test_long_press_does_not_drift(monkeypatch):
    """长按精度：鼠标路径按住期间指针必须零漂移（漂移会被识别为拖动）"""
    mod, calls = _cc_fixture(_load_module(), monkeypatch, touch_ok=False)
    mod.do_long_press(300, 300, duration=999.0)     # 超长时长须被上限保护
    assert mod.MOUSEEVENTF_LEFTDOWN in calls["mouse"]
    assert mod.MOUSEEVENTF_LEFTUP in calls["mouse"]
    assert set(calls["move"]) == {(300, 300)}


def test_scroll_moves_pointer_into_target_area(monkeypatch):
    """按点滚动：先把指针移到目标位置再滚，命中该区域而非整屏"""
    mod, calls = _cc_fixture(_load_module(), monkeypatch, touch_ok=False)
    mod.do_scroll_at(300, 400, -240)
    assert (300, 400) in calls["move"], "滚动前应先把指针移进目标区域"
    assert mod.MOUSEEVENTF_WHEEL in calls["mouse"]


def _cc_fixture(mod, monkeypatch, touch_ok=True):
    """构造一个注入桩的插件模块：记录鼠标/触点事件，绝不触碰真实桌面。

    touch_ok=True 走触控路径，False 强制走鼠标回退路径（两条路径都要验证）。
    必须走 monkeypatch.setattr —— 用例结束后自动还原，否则桩会污染同模块的后续用例。
    """
    calls = {"touch": [], "mouse": [], "move": []}
    monkeypatch.setattr(mod, "_touch", {"state": "ok" if touch_ok else "broken"})
    monkeypatch.setattr(mod, "_last_touch_err", 0)
    inject = (lambda x, y, flags: calls["touch"].append((x, y, flags)) or True) \
        if touch_ok else (lambda x, y, flags: False)
    monkeypatch.setattr(mod, "_inject_touch", inject)
    monkeypatch.setattr(mod, "_send_mouse",
                        lambda flags, dx=0, dy=0, data=0: calls["mouse"].append(flags))
    # 假光标：_move_absolute 会真的「移动」它，_cursor_pos 读回来的就是它 ——
    # 这样「按键前回读确认落点」(_settle_at) 才能被真实验证（真机上是 GetCursorPos）。
    pos = {"x": 0, "y": 0}

    def _move_abs(x, y):
        calls["move"].append((x, y))
        pos["x"], pos["y"] = int(x), int(y)

    monkeypatch.setattr(mod, "_move_absolute", _move_abs)
    monkeypatch.setattr(mod, "_cursor_pos", lambda: (pos["x"], pos["y"]))
    monkeypatch.setattr(mod, "_move_human",
                        lambda x, y, duration=0: calls["move"].append((x, y)))
    return mod, calls


def test_new_precision_tools_registered_and_classified():
    """精确点击工具必须同时出现在 TOOLS 与 _HANDLERS，并归类正确"""
    mod = _load_module()
    names = {t["name"] for t in mod.TOOLS}
    for name in ("click_window", "verify_point", "zoom_in"):
        assert name in names, f"TOOLS 缺少 {name}"
        assert name in mod._HANDLERS and callable(mod._HANDLERS[name])
    # 纯查询类不点光环；click_window 是真正的操控
    for name in ("zoom_in", "screen_shot"):
        assert name not in mod._CONTROL_TOOLS
    assert "click_window" in mod._CONTROL_TOOLS


def test_click_window_uses_window_relative_coordinates(monkeypatch):
    """窗口相对坐标点击：落点 = 窗口左上角 + 偏移；窗口移动也不会点错"""
    mod = _load_module()
    mod._STATE["interrupted"] = mod._STATE["paused"] = False

    class _R:
        left, top, right, bottom = 1000, 500, 1200, 700

    monkeypatch.setattr(mod, "window_rect", lambda h: _R)
    monkeypatch.setattr(mod, "_target_hwnd", lambda args, required=True: 42)
    monkeypatch.setattr(mod, "_prepare_target", lambda hwnd: "")
    monkeypatch.setattr(mod, "_hit_check", lambda root, x, y: "")
    # 本用例只验「窗口相对坐标 → 屏幕坐标」的映射，把元素吸附停掉，
    # 否则它会去读真实屏幕像素、把落点挪走（吸附本身另有专门用例覆盖）
    monkeypatch.setattr(mod, "_snap_to_element",
                        lambda x, y, reach=48, max_shift=None: (x, y, ""))
    clicked = []
    monkeypatch.setattr(mod, "do_click",
                        lambda x, y, b="left", c=1, **k: clicked.append((x, y, b, c)) or True)

    out = mod._HANDLERS["click_window"]({"hwnd": 42, "rel_x": 10, "rel_y": 20})
    assert clicked == [(1010, 520, "left", 1)]
    assert "1010" in out and "520" in out

    clicked.clear()
    mod._HANDLERS["click_window"]({"hwnd": 42})            # 不传偏移 → 窗口正中心
    assert clicked == [(1100, 600, "left", 1)]

    with pytest.raises(ValueError):
        mod._HANDLERS["click_window"]({"hwnd": 42, "rel_x": 9999, "rel_y": 0})


def test_window_line_marks_overlay_and_menu():
    """窗口行渲染：类别/进程/类名/置顶都要标出来，无标题也要可读"""
    mod = _load_module()
    line = mod._window_line({"hwnd": 9, "title": "", "x": 1, "y": 2, "w": 240, "h": 240,
                             "kind": "overlay", "class": "Qt6111QWindowToolSaveBits",
                             "process": "python.exe", "topmost": True}, 1)
    assert "工具型浮层" in line and "python.exe" in line and "置顶" in line
    assert "（无标题）" in line and "[9]" in line
    assert "弹出菜单" in mod._window_line({"hwnd": 1, "title": "python", "x": 0, "y": 0,
                                          "w": 107, "h": 52, "kind": "menu"})


# ---------- 生产级软件（Blender 类 GPU 自绘界面）的精准操控 ----------


def test_input_mode_parsing_and_touch_gate():
    """注入方式解析：默认 auto（=鼠标），只有显式 touch 才走触控"""
    mod = _load_module()
    assert mod._input_mode({}) == "auto"
    assert mod._input_mode({"input": "touch"}) == "touch"
    assert mod._input_mode({"input": "MOUSE"}) == "mouse"
    assert mod._input_mode({"input": "乱写"}) == "auto"
    assert mod._touch_allowed("auto") is False, "auto 必须走鼠标（精准优先）"
    assert mod._touch_allowed("mouse") is False
    assert mod._touch_allowed("touch") is True


def test_click_defaults_to_mouse_not_touch(cc, monkeypatch):
    """默认必须走鼠标注入，而不是触控

    实测证据（原生 OpenGL 窗口、125% DPI、带触控数字化仪的本机）：
      鼠标注入落点偏差 (+0,+0)；触控注入偏差在 0~428 像素之间且不可预测。
    生产级软件（Blender/CAD）的按钮常常只有十几像素宽，触控的偏差等于点错。
    """
    mod, calls = cc
    monkeypatch.setattr(mod, "_inject_touch",
                        lambda x, y, flags: calls["touch"].append((x, y, flags)) or True)
    mod.do_click(600, 400)                       # 默认 auto
    assert calls["touch"] == [], "默认不得走触控"
    assert calls["mouse"] == [mod.MOUSEEVENTF_LEFTDOWN, mod.MOUSEEVENTF_LEFTUP]

    calls["touch"].clear()
    calls["mouse"].clear()
    mod.do_long_press(300, 300, duration=0.3)    # 默认 auto
    assert calls["touch"] == []
    assert mod.MOUSEEVENTF_LEFTDOWN in calls["mouse"]

    calls["touch"].clear()
    calls["mouse"].clear()
    mod.do_drag(100, 100, 300, 300, duration=0.2)   # 默认 auto
    assert calls["touch"] == []
    assert mod.MOUSEEVENTF_LEFTDOWN in calls["mouse"]


def test_move_absolute_uses_virtual_desk_flag(monkeypatch):
    """绝对坐标必须带 MOUSEEVENTF_VIRTUALDESK，否则多显示器下副屏落点全错"""
    mod = _load_module()
    assert mod.MOUSEEVENTF_VIRTUALDESK == 0x4000
    seen = []
    monkeypatch.setattr(mod, "_send_mouse",
                        lambda f, dx=0, dy=0, data=0: seen.append((f, dx, dy)))
    monkeypatch.setattr(mod, "_virtual_screen", lambda: (-1920, 0, 3840, 1080))
    mod._move_absolute(1000, 500)
    assert seen, "应发出一次绝对移动"
    flags, nx, ny = seen[0]
    assert flags & mod.MOUSEEVENTF_ABSOLUTE
    assert flags & mod.MOUSEEVENTF_VIRTUALDESK, "缺此标志时副屏坐标会被按主屏换算"
    assert 0 <= nx <= 65535 and 0 <= ny <= 65535
    # 归一化基准是虚拟屏：x=-1920 应映射到 0，x=1920 映射到 65535
    seen.clear()
    mod._move_absolute(-1920, 0)
    assert seen[0][1] == 0


def test_bits_are_blank_detects_blank_but_keeps_dark_theme():
    """空图判定：纯黑/全同色算空白；Blender 那种深灰主题不能误判为空白"""
    mod = _load_module()
    w = h = 40
    n = w * h
    assert mod._bits_are_blank(bytes([0, 0, 0, 255]) * n, w, h) is True
    # Blender 主题色 #303030（48）是正常界面，绝不能判成空白
    assert mod._bits_are_blank(bytes([0x30, 0x30, 0x30, 255]) * n, w, h) is False
    # 近黑（16）与近白（255）的整块空表面才算空白
    assert mod._bits_are_blank(bytes([16, 16, 16, 255]) * n, w, h) is True
    assert mod._bits_are_blank(bytes([255, 255, 255, 255]) * n, w, h) is True
    half = n // 2
    mixed = bytes([0, 0, 0, 255]) * half + bytes([200, 200, 200, 255]) * (n - half)
    assert mod._bits_are_blank(mixed, w, h) is False
    assert mod._bits_are_blank(b"", w, h) is True


def test_region_diff_detects_ui_response():
    """点击生效校验：逐像素比对能抓到细线级变化，也能滤掉噪声"""
    mod = _load_module()
    w = h = 8
    base = bytearray([30, 30, 30, 255] * (w * h))
    same = bytes(base)
    changed, total = mod._region_diff(bytes(base), same)
    assert (changed, total) == (0, w * h)
    # 只改 2 个像素（模拟 2px 宽的标记线）也必须被抓到
    after = bytearray(base)
    after[0:4] = bytes([255, 60, 60, 255])
    after[4:8] = bytes([255, 60, 60, 255])
    changed, total = mod._region_diff(bytes(base), bytes(after))
    assert changed == 2 and total == w * h
    # 轻微噪声（差值 <= 阈值）不算变化
    noisy = bytearray(base)
    for i in range(0, len(noisy), 4):
        noisy[i] += 8
    assert mod._region_diff(bytes(base), bytes(noisy))[0] == 0
    # 尺寸不一致/空输入 → 不做判断
    assert mod._region_diff(b"", bytes(base)) == (0, 0)
    assert mod._region_diff(bytes(base), b"\x00" * 4) == (0, 0)


def test_grab_window_falls_back_to_screen_when_printwindow_blank(monkeypatch):
    """PrintWindow 抓到空白（GPU 自绘/独占表面界面）→ 自动改用屏幕抓取并如实标注"""
    mod = _load_module()

    class _R:
        left, top, right, bottom = 0, 0, 100, 80

    monkeypatch.setattr(mod, "_ensure_window_capturable", lambda h: "")
    monkeypatch.setattr(mod, "_ensure_window_capturable", lambda h: "")
    monkeypatch.setattr(mod, "window_rect", lambda h: _R)
    monkeypatch.setattr(mod, "_grab_window_printwindow",
                        lambda *a, **k: (None, "PrintWindow 抓到的是空白画面"))
    monkeypatch.setattr(mod, "_grab_window_screen",
                        lambda *a, **k: (b"PNG-SCREEN", "屏幕抓取"))
    monkeypatch.setattr(mod, "_GRAB_INFO", {"strategy": "", "blank": False,
                                           "img_w": 100, "img_h": 80})
    png, iw, ih, x, y, w, h = mod.grab_window(123)
    assert png == b"PNG-SCREEN"
    assert mod._GRAB_INFO["strategy"] == "屏幕抓取"
    assert (w, h) == (100, 80)


def test_grab_window_falls_back_to_full_screen_when_occluded(monkeypatch):
    """窗口被遮挡（屏幕抓取会截到遮挡窗口）→ 自动改用整屏，且基准切成整屏。

    用户反馈：明明点的是目标窗口，点下去却打到遮挡窗口上 —— 因为窗口截图给出的是
    遮挡窗口的画面，坐标却按目标窗口换算。整屏兜底保证「图 = 用户眼前的画面」。
    """
    mod = _load_module()

    class _R:
        left, top, right, bottom = 10, 20, 110, 100

    def _occluded_screen(*a, **k):
        mod._GRAB_INFO["occluded"] = "别的窗口"
        return None, ""

    monkeypatch.setattr(mod, "_ensure_window_capturable", lambda h: "")
    monkeypatch.setattr(mod, "window_rect", lambda h: _R)
    monkeypatch.setattr(mod, "_grab_window_printwindow",
                        lambda *a, **k: (None, "PrintWindow 抓到的是空白画面"))
    monkeypatch.setattr(mod, "_grab_window_screen", _occluded_screen)
    monkeypatch.setattr(mod, "grab_screen",
                        lambda **k: (b"PNG-FULL", 1280, 720, -1920, 0, 3840, 2160))
    monkeypatch.setattr(mod, "_log", lambda who, text: None)
    png, iw, ih, x, y, w, h = mod.grab_window(123)
    assert png == b"PNG-FULL"
    assert mod._GRAB_INFO["full"] is True
    assert (x, y, w, h) == (-1920, 0, 3840, 2160), "整屏兜底时坐标基准必须是整屏"
    assert mod._GRAB_INFO["occluded"] == "别的窗口"


def test_grab_window_prefers_printwindow_when_usable(monkeypatch):
    """PrintWindow 正常时不得绕道屏幕抓取（它不受遮挡影响，更可靠）"""
    mod = _load_module()

    class _R:
        left, top, right, bottom = 5, 6, 105, 86

    monkeypatch.setattr(mod, "_ensure_window_capturable", lambda h: "")
    monkeypatch.setattr(mod, "_ensure_window_capturable", lambda h: "")
    monkeypatch.setattr(mod, "window_rect", lambda h: _R)
    monkeypatch.setattr(mod, "_grab_window_printwindow",
                        lambda *a, **k: (b"PNG-PW", "PrintWindow"))
    called = []
    monkeypatch.setattr(mod, "_grab_window_screen",
                        lambda *a, **k: called.append(1) or (b"X", "屏幕抓取"))
    monkeypatch.setattr(mod, "_GRAB_INFO", {"strategy": "", "blank": False,
                                           "img_w": 100, "img_h": 80})
    png, iw, ih, x, y, w, h = mod.grab_window(123)
    assert png == b"PNG-PW" and not called
    assert (x, y) == (5, 6)


def test_verify_point_reports_zero_deviation(monkeypatch):
    """精度自检：光标实际落点与目标一致时明确报告「完全一致」"""
    mod = _load_module()
    mod._STATE["interrupted"] = mod._STATE["paused"] = False
    monkeypatch.setattr(mod, "_VIEW", {"x0": 0.0, "y0": 0.0, "w": 1920.0, "h": 1080.0,
                                       "img_w": 1920.0, "img_h": 1080.0})
    moved = []
    monkeypatch.setattr(mod, "_move_absolute", lambda x, y: moved.append((x, y)))
    monkeypatch.setattr(mod, "_cursor_pos", lambda: moved[-1] if moved else (0, 0))
    monkeypatch.setattr(mod, "_owner_window", lambda x, y: 777)
    monkeypatch.setattr(mod, "window_title", lambda h: "精度靶场")
    out = mod._HANDLERS["verify_point"]({"x": 500, "y": 400})
    assert moved == [(500, 400)]
    assert "完全一致" in out and "偏差 (+0,+0)" in out
    assert "777" in out and "精度靶场" in out

    # 有偏差时必须如实报告，而不是假装精准
    monkeypatch.setattr(mod, "_cursor_pos", lambda: (505, 396))
    out2 = mod._HANDLERS["verify_point"]({"x": 500, "y": 400})
    assert "⚠" in out2 and "(+5,-4)" in out2


def test_verify_point_without_move_does_not_touch_cursor(monkeypatch):
    """move=false 时只读不回写：不打扰用户当前光标位置"""
    mod = _load_module()
    mod._STATE["interrupted"] = mod._STATE["paused"] = False
    monkeypatch.setattr(mod, "_VIEW", {"x0": 0.0, "y0": 0.0, "w": 1920.0, "h": 1080.0,
                                       "img_w": 1920.0, "img_h": 1080.0})
    moved = []
    monkeypatch.setattr(mod, "_move_absolute", lambda x, y: moved.append((x, y)))
    monkeypatch.setattr(mod, "_cursor_pos", lambda: (10, 20))
    monkeypatch.setattr(mod, "_owner_window", lambda x, y: 0)
    out = mod._HANDLERS["verify_point"]({"x": 500, "y": 400, "move": False})
    assert moved == [], "move=false 不得移动光标"
    assert "偏差 (-490,-380)" in out


def test_query_tools_do_not_arm_takeover_session():
    """纯查询工具不得开启操控会话

    会话期内「真人鼠标输入 = 用户接管」。若 screen_info / screen_shot 这类只读查询
    也开启会话，用户只是在 AI 看一眼时随手动了下鼠标，就会被误判成接管 ——
    之后所有点击都被拒绝，而用户根本没打算接管。
    """
    mod = _load_module()
    mod._STATE["active_until"] = 0.0
    mod._STATE["session_started"] = 0.0
    # 模拟派发：查询类不续期，操控类续期
    for name in ("screen_info", "screen_shot", "list_windows", "zoom_in"):
        assert name not in mod._CONTROL_TOOLS, f"{name} 是只读查询，不该开启操控会话"
    # verify_point 默认会把光标移过去（真实动作），故算操控；click 系同理
    for name in ("click", "click_window", "verify_point", "drag",
                 "type_text", "manage_window", "key_press"):
        assert name in mod._CONTROL_TOOLS, f"{name} 是操控动作，应开启操控会话"


def test_ensure_click_target_activates_covered_window(monkeypatch):
    """坐标点击前必须确保目标窗口在最前：截图能穿透遮挡，鼠标点击不能

    实测踩过的坑：窗口截图走 PrintWindow，窗口被遮挡也照样能截到内容；但鼠标点击
    只落到该点最顶层的窗口上。于是「照着截图点」时若目标被盖住，点击就打到了遮挡
    窗口上（微信任务就是这么失败的）。
    """
    mod = _load_module()
    activated = []

    class _R:
        left, top, right, bottom = 100, 100, 400, 400

    class _U:
        @staticmethod
        def IsWindow(h):
            return True

    monkeypatch.setattr(mod, "user32", _U)
    monkeypatch.setattr(mod, "window_rect", lambda h: _R)
    monkeypatch.setattr(mod, "window_title",
                        lambda h: {999: "遮挡者", 42: "目标"}.get(h, str(h)))
    monkeypatch.setattr(mod, "_STATE", dict(mod._STATE, view_target=42))
    state = {"fg": 999}
    monkeypatch.setattr(mod, "window_at", lambda x, y: state["fg"])
    monkeypatch.setattr(mod, "activate_window",
                        lambda h: activated.append(h) or state.update(fg=42) or True)

    note = mod._ensure_click_target(200, 200, {})
    assert activated == [42], "应激活目标窗口"
    assert "盖住" in note and "遮挡者" in note and "目标" in note

    # 落点本来就在目标窗口上 → 什么都不做、也不打扰
    activated.clear()
    state["fg"] = 42
    assert mod._ensure_click_target(200, 200, {}) == ""
    assert activated == []

    # 落点不在目标窗口矩形内 → 不擅自换窗口
    state["fg"] = 999
    assert mod._ensure_click_target(900, 900, {}) == ""
    assert activated == []

    # 抢不到前台 → 必须拒绝点击（照点会点到遮挡窗口上，可能误触别的应用）
    activated.clear()
    monkeypatch.setattr(mod, "activate_window", lambda h: True)     # 置顶无效
    monkeypatch.setattr(mod, "_raise_topmost", lambda h: True)      # 临时置顶也无效
    with pytest.raises(RuntimeError) as ei:
        mod._ensure_click_target(200, 200, {})
    assert "已拒绝点击" in str(ei.value) and "遮挡者" in str(ei.value)


def test_ensure_input_target_refuses_when_cannot_focus(monkeypatch):
    """键盘注入前必须确保目标窗口在前台；抢不到焦点就拒绝，绝不把文字打进别的应用

    实测致命场景：目标窗口被遮挡时，AI 照着「只截该窗口」的截图操作；若点击没把焦点
    交给目标窗口，随后的 type_text / enter 会打进**当前前台应用**（比如 AI 助手自己的
    输入框）—— 文字发错地方，比点不到严重得多。
    """
    mod = _load_module()
    monkeypatch.setattr(mod, "window_kind", lambda h: "main")
    monkeypatch.setattr(mod, "window_title",
                        lambda h: {42: "微信", 999: "AI 助手"}.get(h, str(h)))
    monkeypatch.setattr(mod, "user32", type("_U", (), {
        "IsWindow": staticmethod(lambda h: True)}))

    # ① 目标已在前台 → 直接放行
    monkeypatch.setattr(mod, "_STATE", dict(mod._STATE, view_target=42))
    monkeypatch.setattr(mod, "foreground_window", lambda: 42)
    assert mod._ensure_input_target({}, "输入文本") == ""

    # ② 不在前台但能切过去 → 切过去并如实说明
    calls = {"fg": 999}
    monkeypatch.setattr(mod, "foreground_window", lambda: calls["fg"])
    monkeypatch.setattr(mod, "activate_window",
                        lambda h: calls.update(fg=42) or True)
    note = mod._ensure_input_target({}, "输入文本")
    assert "不在前台" in note and "微信" in note

    # ③ 抢不到前台 → 拒绝注入（而不是"先打了再说"）
    monkeypatch.setattr(mod, "foreground_window", lambda: 999)
    monkeypatch.setattr(mod, "activate_window", lambda h: True)
    monkeypatch.setattr(mod, "_raise_topmost", lambda h: True)
    with pytest.raises(RuntimeError) as ei:
        mod._ensure_input_target({}, "输入文本")
    msg = str(ei.value)
    assert "已拒绝输入文本" in msg and "AI 助手" in msg and "没有执行" in msg

    # ④ 没有已知目标窗口时不强加约束（例如纯键盘快捷键场景）
    monkeypatch.setattr(mod, "_STATE", dict(mod._STATE, view_target=0))
    assert mod._ensure_input_target({}, "输入文本") == ""


def test_type_text_refuses_when_target_not_focused(monkeypatch):
    """type_text 在抢不到目标焦点时必须报错，且**一个字符都不注入**"""
    mod = _load_module()
    mod._STATE["interrupted"] = mod._STATE["paused"] = False
    monkeypatch.setattr(mod, "_ensure_input_target",
                        lambda args, action: (_ for _ in ()).throw(
                            RuntimeError("已拒绝输入文本：目标窗口未获得焦点")))
    typed = []
    monkeypatch.setattr(mod, "do_type_text", lambda t, interval=0.012: typed.append(t))
    with pytest.raises(RuntimeError):
        mod._HANDLERS["type_text"]({"text": "这条消息绝不能发错地方"})
    assert typed == [], "被拒绝时不得注入任何字符"



def test_ensure_window_capturable_handles_minimized(monkeypatch):
    """最小化窗口必须先恢复；仍不可用则拒绝，绝不交出一张会让坐标全错的废图

    实测踩过的坑：窗口最小化后 GetWindowRect 变成 (-32000,-32000,160,28) 哨兵值，
    PrintWindow 也只给 160×28 的废图 —— 但调用方看起来「截图成功了」，
    照着它算出来的坐标全在屏幕外，点击永远落不到目标上。
    """
    mod = _load_module()
    shown = []

    class _U:
        @staticmethod
        def IsWindow(h):
            return True

        @staticmethod
        def IsIconic(h):
            return state["iconic"]

        @staticmethod
        def ShowWindow(h, cmd):
            shown.append((h, cmd))
            state["iconic"] = False
            return True

    state = {"iconic": True}

    class _R:
        left, top, right, bottom = 504, 219, 1448, 866

    monkeypatch.setattr(mod, "user32", _U)
    monkeypatch.setattr(mod, "window_rect", lambda h: _R)
    monkeypatch.setattr(mod, "window_title", lambda h: "微信")
    monkeypatch.setattr(mod, "time", mod.time)
    note = mod._ensure_window_capturable(123)
    assert shown and shown[0][1] == mod.SW_RESTORE, "应调用 ShowWindow(SW_RESTORE)"
    assert "最小化" in note

    # 恢复失败（矩形仍是哨兵值）→ 必须拒绝
    state["iconic"] = True

    class _R2:
        left, top, right, bottom = -32000, -32000, -31840, -31972

    monkeypatch.setattr(mod, "window_rect", lambda h: _R2)
    with pytest.raises(RuntimeError) as ei:
        mod._ensure_window_capturable(123)
    assert "最小化" in str(ei.value) and "坐标会全错" in str(ei.value)


# ============================================================================
# open_app / 应用启动 / 窗口前置（「应用在后台或最小化时疯狂找窗口」的修复）
# ============================================================================

def test_open_app_registered_and_is_control_tool():
    """open_app 必须注册，且属「会改变桌面状态」的工具（要点亮光环）"""
    mod = _load_module()
    declared = {t["name"] for t in mod.TOOLS}
    assert "open_app" in declared
    assert "open_app" in mod._HANDLERS
    assert "open_app" in mod._CONTROL_TOOLS
    schema = next(t for t in mod.TOOLS if t["name"] == "open_app")
    assert schema["inputSchema"]["required"] == ["name"]
    # 描述里要明确「不要用 list_windows 反复找窗口」，这是修复的核心
    assert "list_windows" in schema["description"]


def test_app_index_covers_start_menu_and_system_exes():
    """应用索引要能覆盖开始菜单快捷方式（这是「打开微信」能被解析的前提）"""
    mod = _load_module()
    idx = mod._app_index(refresh=True)
    assert idx, "应用索引不应为空"
    kinds = {i["kind"] for i in idx}
    assert "lnk" in kinds, "应包含开始菜单 .lnk 条目"
    for it in idx:
        assert it["name"] and it["target"], "索引条目必须有名字与目标"
    # 卸载器/官网这类噪音入口不该进索引（否则模糊匹配会挑中它们）
    assert not any("卸载" in i["name"] for i in idx)


def test_app_alias_and_scoring():
    """别名展开与打分：精确 > 前缀 > 包含 > 相似度"""
    mod = _load_module()
    keys = mod._app_keys("微信")
    assert "wechat" in keys and "微信" in keys, "「微信」应展开出进程名关键字"
    # 未收录的名字原样返回，不能抛异常
    assert mod._app_keys("某个没收录的软件") == ("某个没收录的软件",)
    assert mod._app_score(keys, "微信") == 1.0
    assert mod._app_score(keys, "WeChat") == 1.0          # 归一化后忽略大小写
    assert mod._app_score(keys, "Weixin.exe") > 0.7       # 前缀
    assert mod._app_score(("notepad",), "完全无关的名字") < 0.62


def test_resolve_app_finds_system_tool_by_exe():
    """系统自带工具（记事本）只有 exe、没有快捷方式，必须靠系统目录探测兜住"""
    mod = _load_module()
    res = mod._resolve_app("记事本")
    assert res["target"], "「记事本」应能被解析出启动目标"
    assert res["target"].lower().endswith(".exe")


def test_resolve_app_settings_uses_protocol_uri():
    """「设置」是 UWP，没有 exe —— 必须走协议 URI（否则会解析失败）"""
    mod = _load_module()
    res = mod._resolve_app("设置")
    assert res["target"] == "ms-settings:"
    assert res["kind"] == "uri"


def test_resolve_app_unknown_returns_candidates_not_crash():
    """找不到时必须给出候选名字（让模型一次改对），而不是抛异常或空手而归"""
    mod = _load_module()
    res = mod._resolve_app("绝不可能存在的软件名zzz")
    assert res["target"] == ""
    assert isinstance(res["candidates"], list)


def test_is_launch_uri_does_not_mistake_drive_path_for_uri():
    """盘符 `C://…` 也匹配 `^[a-z]:`，绝不能被当成协议 URI（否则启动目标会被跳过存在性校验）"""
    mod = _load_module()
    assert mod._is_launch_uri("ms-settings:")
    assert mod._is_launch_uri(r"shell:AppsFolder\Foo!App")
    assert not mod._is_launch_uri(r"C:/Windows/System32/calc.exe")
    assert not mod._is_launch_uri(r"D:/some/path/app.exe")


def test_launch_app_rejects_missing_target():
    """启动不存在的目标必须明确报错，不能静默什么都不做"""
    mod = _load_module()
    with pytest.raises(RuntimeError) as ei:
        mod.launch_app(r"C:/definitely/not/here/nope.exe")
    assert "不存在" in str(ei.value)
    with pytest.raises(ValueError):
        mod.launch_app("")


def test_open_app_requires_name():
    """缺 name 时必须报可读错误，而不是去猜一个应用"""
    mod = _load_module()
    with pytest.raises(ValueError) as ei:
        mod._HANDLERS["open_app"]({})
    assert "name" in str(ei.value)


def test_open_app_unknown_app_lists_candidates():
    """未知应用：报错里必须带「已安装应用里最接近的」候选，帮模型一次改对"""
    mod = _load_module()
    with pytest.raises(RuntimeError) as ei:
        mod._HANDLERS["open_app"]({"name": "绝不可能存在的软件名zzz"})
    assert "没找到" in str(ei.value)


def test_raise_topmost_undoes_topmost(monkeypatch):
    """回归：曾经 WS_EX_TOPMOST 未定义 → NameError 被 except 吞掉，
    「取消置顶」那一步从不执行，用户窗口被**永久**留在 always-on-top。
    这里断言：临时置顶之后必须真的撤销置顶。"""
    mod = _load_module()
    calls = []

    class _U:
        def IsWindow(self, h):
            return True

        def SetWindowPos(self, hwnd, after, *rest):
            calls.append(after)

    monkeypatch.setattr(mod, "user32", _U())
    monkeypatch.setattr(mod, "_ex_style", lambda h: 0)      # 原本不是置顶窗口
    assert mod._raise_topmost(123) is True
    assert calls == [mod.HWND_TOPMOST, mod.HWND_NOTOPMOST], \
        f"必须先置顶再取消置顶，实际 {calls}"


def test_raise_topmost_keeps_original_topmost_window(monkeypatch):
    """本来就是置顶的窗口，不能把它的置顶属性改掉"""
    mod = _load_module()
    calls = []

    class _U:
        def IsWindow(self, h):
            return True

        def SetWindowPos(self, hwnd, after, *rest):
            calls.append(after)

    monkeypatch.setattr(mod, "user32", _U())
    monkeypatch.setattr(mod, "_ex_style", lambda h: mod.WS_EX_TOPMOST)
    assert mod._raise_topmost(123) is True
    assert calls == [mod.HWND_TOPMOST], f"不该撤销原有的置顶属性，实际 {calls}"


def test_raise_topmost_undoes_even_if_raise_fails(monkeypatch):
    """即使「置顶」这一步抛异常，也必须走完「取消置顶」——绝不把窗口留在最前面"""
    mod = _load_module()
    calls = []

    class _U:
        def IsWindow(self, h):
            return True

        def SetWindowPos(self, hwnd, after, *rest):
            calls.append(after)
            if after == mod.HWND_TOPMOST:
                raise OSError("boom")

    monkeypatch.setattr(mod, "user32", _U())
    monkeypatch.setattr(mod, "_ex_style", lambda h: 0)
    assert mod._raise_topmost(123) is False
    assert mod.HWND_NOTOPMOST in calls, "置顶失败也必须撤销，避免窗口卡在最前"


def test_raise_topmost_rejects_invalid_hwnd(monkeypatch):
    mod = _load_module()

    class _U:
        def IsWindow(self, h):
            return False

    monkeypatch.setattr(mod, "user32", _U())
    assert mod._raise_topmost(0) is False


def test_ex_style_reads_extended_style_without_uia(monkeypatch):
    """_ex_style 必须直接读扩展样式（否则 _raise_topmost 的「还原置顶」保护会失效）

    曾经这个常量借自 uia.py：模块被删/改名后函数一执行就 NameError，又被
    `except Exception` 吞掉，于是「置顶成功、取消置顶抛异常」——窗口被永久留在
    置顶状态，调用方却收到「置顶失败」。这里锁定「不依赖任何兄弟模块」。
    """
    mod = _load_module()

    class _U:
        def GetWindowLongPtrW(self, hwnd, idx):
            assert idx == mod.GWL_EXSTYLE
            return mod.WS_EX_TOPMOST

    monkeypatch.setattr(mod, "user32", _U())
    assert mod._ex_style(123) == mod.WS_EX_TOPMOST
    # 读样式失败必须安全退回 0，不得让调用方中断
    class _Boom:
        def GetWindowLongPtrW(self, hwnd, idx):
            raise OSError("boom")

    monkeypatch.setattr(mod, "user32", _Boom())
    assert mod._ex_style(123) == 0


def test_bring_to_front_reports_invalid_window():
    """无效窗口要如实返回失败，而不是谎报已就绪"""
    mod = _load_module()
    ok, note = mod._bring_to_front(0)
    assert ok is False
    assert "不存在" in note


def test_activate_window_rejects_invalid_hwnd():
    mod = _load_module()
    assert mod.activate_window(0) is False


def test_activate_window_attaches_to_foreground_thread(monkeypatch):
    """前台锁修复：必须 AttachThreadInput(前台窗口线程) —— 只接目标窗口的线程无效，
    这正是「窗口在后台时怎么都切不到前台」的根因。"""
    mod = _load_module()
    attached = []

    class _U:
        def IsWindow(self, h):
            return True

        def IsIconic(self, h):
            return False

        def GetWindowThreadProcessId(self, h, _p):
            return 111 if h == 42 else 222       # 目标线程 111 / 前台线程 222

        def AttachThreadInput(self, a, b, on):
            attached.append((a, b, bool(on)))
            return 1

        def SetForegroundWindow(self, h):
            return 1

        def BringWindowToTop(self, h):
            return 1

        def SetWindowPos(self, *a, **k):
            return 1

    monkeypatch.setattr(mod, "user32", _U())
    monkeypatch.setattr(mod, "kernel32", type("K", (), {"GetCurrentThreadId": staticmethod(lambda: 999)})())
    monkeypatch.setattr(mod, "foreground_window", lambda: 77)
    monkeypatch.setattr(mod, "time", mod.time)
    assert mod.activate_window(42) is True
    ths = {a for a, _b, on in attached if on}
    assert 222 in ths, "必须把**前台窗口线程**接进当前线程，否则前台锁不放行"
    assert 111 in ths, "目标窗口线程也要接"
    assert all(b == 999 for _a, b, _on in attached), "应接到当前线程"


# ============================================================================
# 坐标精度：换算数学 / 坐标系开关 / 像素级元素吸附 / 精修工具
# ============================================================================

def test_to_physical_rounds_instead_of_truncating():
    """换算必须四舍五入而不是截断。

    截图缩放比常是 1.5 / 2.5 这类非整数，int() 向下截断会产生**系统性负偏差**：
    图像越靠右下的点偏得越多（4K 屏缩到 1280 时最大 3px），足以把点击带到隔壁控件。
    """
    mod = _load_module()
    # 1920 -> 1280 即 1.5 倍；x=1 应得 1.5 -> 2（旧实现 int() 截断会得 1）
    mod._set_view(0, 0, 1920, 1080, 1280, 720, "full")
    assert mod.to_physical(1, 1) == (2, 2), "小数缩放必须四舍五入，不能截断"
    assert mod.to_physical(5, 5) == (8, 8), "7.5 应进位到 8（截断会得 7）"
    assert mod.to_physical(0, 0) == (0, 0)
    assert mod.to_physical(640, 360) == (960, 540)
    # 非整数缩放比下，误差必须 <= 0.5 像素
    for ix in range(0, 1281, 37):
        px, _ = mod.to_physical(ix, 0)
        assert abs(px - ix * 1.5) <= 0.5, f"图像 x={ix} 换算偏差超过 0.5px"


def test_to_physical_honours_nonzero_origin():
    """虚拟桌面原点不为 0（副屏在主屏左侧/上方）时，换算必须加上原点。"""
    mod = _load_module()
    mod._set_view(-1920, -200, 3840, 1280, 1920, 640, "full")
    assert mod.to_physical(0, 0) == (-1920, -200)
    assert mod.to_physical(960, 320) == (0, 440)     # 1:2 缩放


def test_resolve_point_space_screen_bypasses_view_mapping():
    """space="screen" 必须直通，不再被当前截图基准二次换算。

    模型手里握着物理坐标（上一轮 click 的返回值、list_windows 给的窗口位置）时，
    若被当前截图基准再换算一次就会整体偏移；有了这个开关才不用靠「再截一张图」
    对齐基准（而新图又会把基准换成别的窗口）。
    """
    mod = _load_module()
    mod._set_view(100, 50, 1920, 1080, 1280, 720, "full")
    assert mod._resolve_point({"x": 640, "y": 360}) == (1060, 590)          # 默认按图像坐标
    assert mod._resolve_point({"x": 640, "y": 360, "space": "screen"}) == (640, 360)
    assert mod._resolve_point({"x": 640, "y": 360, "space": "物理"}) == (640, 360)
    # drag 用 x1/y1/x2/y2 键名
    assert mod._resolve_point({"x1": 640, "y1": 360}, "x1", "y1") == (1060, 590)


def test_transform_line_reports_exact_formula():
    """截图元信息必须写出精确换算公式（原点 + 缩放比），供模型换算与人工排障。"""
    mod = _load_module()
    mod._set_view(0, 0, 1920, 1080, 1280, 720, "full")
    line = mod._transform_line()
    assert "1.5000" in line and "缩放" in line or "×" in line
    assert "(0,0)" in line
    # 1:1 时必须明说「读数即坐标」
    mod._set_view(10, 20, 1280, 720, 1280, 720, "window")
    one = mod._transform_line()
    assert "1:1" in one and "(10,20)" in one


def test_stretch_mode_picks_crisp_for_magnification():
    """放大要看精确像素时用最近邻（COLORONCOLOR），缩小一律用 HALFTONE 抗锯齿。"""
    mod = _load_module()
    assert mod._stretch_mode(enlarging=True, smooth=False) == mod.COLORONCOLOR
    assert mod._stretch_mode(enlarging=True, smooth=True) == mod.HALFTONE
    assert mod._stretch_mode(enlarging=False, smooth=True) == mod.HALFTONE
    assert mod._stretch_mode(enlarging=False, smooth=False) == mod.HALFTONE, \
        "缩小用最近邻会丢像素、产生锯齿，反而更难读数"
    # grab_zoom 默认走精确模式
    import inspect
    assert inspect.signature(mod.grab_zoom).parameters["smooth"].default is False


def _synth_region(bg=(48, 48, 48), radius=48):
    """造一块纯背景的 BGRA 区域缓冲（模拟屏幕局部像素）。"""
    w = h = radius * 2
    return bytearray(bytes((bg[2], bg[1], bg[0], 255)) * (w * h)), w, h


def _paint_rect(buf, w, x0, y0, x1, y1, color):
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            o = (y * w + x) * 4
            buf[o:o + 4] = bytes((color[2], color[1], color[0], 255))


def _use_region(mod, monkeypatch, buf, w, h):
    """把区域抓取替换成固定缓冲：区域始终以估计点为中心，故 x0 = x - w/2。"""
    monkeypatch.setattr(
        mod, "_region_snapshot_at",
        lambda x, y, radius=48: (bytes(buf), x - w // 2, y - h // 2, w, h))


def test_snap_centres_click_inside_a_button(monkeypatch):
    """估计点在按钮内但不在中心 → 吸附到按钮几何中心。"""
    mod = _load_module()
    buf, w, h = _synth_region()
    _paint_rect(buf, w, 20, 30, 70, 70, (72, 72, 72))     # 中心 (45,50)，估计点在 (48,48)
    _use_region(mod, monkeypatch, buf, w, h)
    sx, sy, note = mod._snap_to_element(1000, 2000)
    assert (sx, sy) == (997, 2002), f"应吸附到 (997,2002)，实得 {(sx, sy)}"
    assert "吸附" in note and "51×41" in note


def test_snap_recovers_estimate_that_missed_the_button(monkeypatch):
    """估计点落在按钮外（差 12px）→ 仍要吸附到该按钮中心。

    这正是「点了没反应」的主因：模型估计偏了几像素，落到按钮边缘之外的背景上。
    """
    mod = _load_module()
    buf, w, h = _synth_region()
    _paint_rect(buf, w, 60, 30, 90, 70, (72, 72, 72))     # 左边缘在局部 x=60，估计点在 48
    _use_region(mod, monkeypatch, buf, w, h)
    sx, sy, note = mod._snap_to_element(1000, 2000)
    assert sx == 1027, f"应吸附到按钮中心 x=1027，实得 {sx}"
    assert "吸附" in note


def test_snap_picks_the_nearest_of_several_candidates(monkeypatch):
    """多个候选时取离估计点最近的那个（而不是最大/最先遇到的）。"""
    mod = _load_module()
    buf, w, h = _synth_region()
    _paint_rect(buf, w, 10, 30, 30, 70, (72, 72, 72))     # 中心 x=20，距离 28
    _paint_rect(buf, w, 60, 30, 84, 70, (72, 72, 72))     # 中心 x=72，距离 12 ← 更近
    _use_region(mod, monkeypatch, buf, w, h)
    sx, _sy, _note = mod._snap_to_element(1000, 2000)
    assert sx == 1024, f"应取更近的右侧按钮（中心 1024），实得 {sx}"


def test_snap_refuses_when_there_is_no_element(monkeypatch):
    """纯背景 / 渐变背景 / 噪点：必须**原样返回**，绝不乱改坐标。"""
    mod = _load_module()
    buf, w, h = _synth_region()
    _use_region(mod, monkeypatch, buf, w, h)
    assert mod._snap_to_element(1000, 2000) == (1000, 2000, "")

    grad = bytearray()
    for _y in range(h):
        for x in range(w):
            v = 40 + (x * 60) // w
            grad += bytes((v, v, v, 255))
    _use_region(mod, monkeypatch, grad, w, h)
    assert mod._snap_to_element(1000, 2000)[:2] == (1000, 2000), "渐变背景不得被当成元素"

    import random
    random.seed(7)
    noisy, _w, _h = _synth_region()
    for i in range(w * h):
        if random.random() < 0.05:
            o = i * 4
            noisy[o:o + 4] = bytes((random.randrange(256),) * 3 + (255,))
    _use_region(mod, monkeypatch, noisy, w, h)
    assert mod._snap_to_element(1000, 2000)[:2] == (1000, 2000), "噪点不得被当成元素"


def test_snap_refuses_element_cut_off_by_region_edge(monkeypatch):
    """触到区域边界的连通域（真实尺寸未知 ⇒ 中心不可信）必须被淘汰。

    否则「一大片面板」会被当成一个巨大元素，吸附到它的中心 —— 可能把点击带到几百
    像素之外。
    """
    mod = _load_module()
    buf, w, h = _synth_region()
    _paint_rect(buf, w, 0, 0, w - 1, h - 1, (72, 72, 72))    # 铺满整块 = 触边界
    _use_region(mod, monkeypatch, buf, w, h)
    assert mod._snap_to_element(1000, 2000)[:2] == (1000, 2000)


def test_snap_handles_low_contrast_professional_ui(monkeypatch):
    """低对比界面（Blender 按钮与面板只差十几级）也要能吸附到。

    阈值定得太高会让专业软件的按钮**完全找不到** —— 实测按钮填充 58 vs 背景 48
    （每通道仅差 10 级）必须仍能识别。
    """
    mod = _load_module()
    buf, w, h = _synth_region((48, 48, 48))
    _paint_rect(buf, w, 20, 30, 70, 70, (58, 58, 58))
    _use_region(mod, monkeypatch, buf, w, h)
    sx, sy, note = mod._snap_to_element(1000, 2000)
    assert (sx, sy) == (997, 2002), f"低对比按钮应能识别，实得 {(sx, sy)}"
    assert "吸附" in note


def test_snap_prefers_small_control_inside_a_list_row(monkeypatch):
    """列表行里的小控件（复选框/开关）优先：不能被整行中心拽走。

    实测反馈「点任意元素都不精准」的典型现场：模型按行内小控件估坐标，旧实现走
    行/带模式把 y 对齐到**整行中心**，小控件又不在行正中 → 点下去变成选中整行或
    完全落空。现在估计点落在哪个连通域里，就直接吸到那个控件自己的落点。
    """
    mod = _load_module()
    buf, w, h = _synth_region()
    _paint_rect(buf, w, 4, 34, 36, 62, (72, 72, 72))     # 行内左侧文字块
    _paint_rect(buf, w, 64, 34, 92, 62, (72, 72, 72))    # 行内右侧文字块
    _paint_rect(buf, w, 44, 36, 56, 46, (120, 120, 120))  # 行内小控件（中心 50,41）
    _use_region_at(mod, monkeypatch, buf, w, h, 48, 41)   # 估计点落在小控件内
    # 整行中心在 y=48（控件之外）；新实现必须留在控件自己的落点
    sx, sy, note = mod._snap_to_element(1000, 1993)
    assert (sx, sy) == (1002, 1993), f"应吸到小控件中心 (1002,1993)，实得 {(sx, sy)}"
    assert "控件落点" in note and "13×11" in note, note


def test_band_mode_pulls_x_into_row_content_when_estimate_is_beside_it(monkeypatch):
    """行模式下估计点 x 落在内容之外（空白/滚动条）→ 把 x 收进该行内容范围。"""
    mod = _load_module()
    buf, w, h = _synth_region()
    _paint_rect(buf, w, 4, 34, 62, 62, (72, 72, 72))     # 行内容横跨 59px（≥60% 区域宽）
    _use_region_at(mod, monkeypatch, buf, w, h, 90, 48)   # local x=90 在内容右侧之外
    sx, sy, note = mod._snap_to_element(1090, 2000)
    # 区域原点 x0 = 1090-90 = 1000；内容右沿 62，留 2px → local 60 → 1060
    assert sx == 1060, f"x 应收进内容右沿内 2px（local 60 → 1060），实得 {sx}"
    assert sy == 2000
    assert "收进" in note, note


def test_enum_top_hwnds_actually_enumerates(monkeypatch):
    """窗口枚举不能因为一个「没导入的名字」就被 `except` 吞成空表。

    回归（真实踩过）：`_enum_top_hwnds` 里写成裸 `c_void_p`（模块只 import 了
    `ctypes` 和 `wintypes`），NameError 被 `except Exception: pass` 静默吞掉 ⇒ 该
    函数**永远返回空表** ⇒ list_windows / find_window / screen_shot(mode="window") /
    open_app / 点击前的前台校验全部失效，现象是「当前没有可见窗口」，而窗口明明就在。
    这类失败没有异常、没有日志，只有「工具不好用」，最难查。
    """
    import ctypes
    from ctypes import wintypes
    mod = _load_module()
    CB = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    hits = []

    def _fake_enum(cb_ptr, _lparam):
        cb = ctypes.cast(cb_ptr, CB)          # 还原成可调用指针再调
        for hwnd in (111, 222, 333):
            assert cb(hwnd, 0)
        return 1

    monkeypatch.setattr(mod.user32, "EnumWindows", _fake_enum)
    monkeypatch.setattr(mod, "_log", lambda who, text: hits.append(text))
    got = mod._enum_top_hwnds()
    # 枚举结果必须在最前（前台窗口作为兜底会被并到末尾，见下一条测试）
    assert got[:3] == [111, 222, 333], got
    assert hits == [], f"正常枚举不该产生错误日志，实得 {hits}"


def test_enum_always_includes_foreground_window(monkeypatch):
    """枚举无论如何都要并上前台窗口 —— 用户正看着的那扇窗不能缺席。"""
    import ctypes
    from ctypes import wintypes
    mod = _load_module()
    CB = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _fake_enum(cb_ptr, _lparam):
        cb = ctypes.cast(cb_ptr, CB)
        for hwnd in (111, 222):
            cb(hwnd, 0)
        return 1

    monkeypatch.setattr(mod.user32, "EnumWindows", _fake_enum)
    monkeypatch.setattr(mod.user32, "GetForegroundWindow", lambda: 999)
    assert mod._enum_top_hwnds() == [111, 222, 999]


def test_enum_falls_back_to_window_chain_when_enum_windows_yields_nothing(monkeypatch):
    """EnumWindows 一扇窗都没给时，必须走 GetTopWindow/GW_HWNDNEXT 链式兜底。

    这正是用户看到的「有时能截屏、有时 list_windows 全是空」那条故障链：
    枚举为空会让四条能力集体静默失效，所以必须有第二条完全独立的路径。
    """
    mod = _load_module()
    monkeypatch.setattr(mod.user32, "EnumWindows", lambda *_a: 1)   # 一个回调都不调
    chain = {0: 501, 501: 502, 502: 503, 503: 0}
    monkeypatch.setattr(mod.user32, "GetTopWindow", lambda _h: chain.get(0, 0))
    monkeypatch.setattr(mod.user32, "GetWindow",
                        lambda h, _cmd: chain.get(int(h), 0))
    monkeypatch.setattr(mod.user32, "GetForegroundWindow", lambda: 0)
    assert mod._enum_top_hwnds() == [501, 502, 503]
    assert mod._ENUM_DIAG["enum"] == 0 and mod._ENUM_DIAG["fallback"] == 3


def test_enum_health_explains_an_empty_desktop():
    """枚举全空时，工具输出必须说清原因，而不是丢一句「当前没有可见窗口」。"""
    mod = _load_module()
    mod._ENUM_DIAG.update({"enum": 0, "fallback": 0, "foreground": 0, "error": ""})
    msg = mod._enum_health()
    for key in ("EnumWindows", "链式", "交互式桌面"):
        assert key in msg, msg
    # 有结果时只报计数，不必吓人
    mod._ENUM_DIAG.update({"enum": 12, "fallback": 0, "foreground": 42})
    assert "12 扇" in mod._enum_health()


def test_window_not_found_hint_suggests_close_titles(monkeypatch):
    """找不到窗口时要给出**可执行**的诊断：像的标题 + 别把会话名当窗口标题。"""
    mod = _load_module()
    monkeypatch.setattr(mod, "list_windows", lambda **kw: [
        {"title": "QQ"}, {"title": "微信"}, {"title": "xiaojiang - 聊天"}])
    msg = mod._window_not_found_hint("xiaojiang", 0)
    assert "你是不是想找" in msg and "xiaojiang - 聊天" in msg
    assert "窗口标题" in msg and "联系人" in msg
    # 完全没有相近标题时退化成「列出当前可用标题」
    msg2 = mod._window_not_found_hint("zzzz-not-exist", 0)
    assert "当前可用的窗口标题" in msg2


def test_keyboard_and_window_services_do_not_swallow_name_errors():
    """兜底：源码里不得再出现裸 `c_void_p` 之类「未限定前缀」的名字。"""
    src = SERVER.read_text(encoding="utf-8")
    import re
    bad = [ln for ln in src.splitlines()
           if re.search(r"(?<![\w.])c_void_p\b", ln) and "ctypes.c_void_p" not in ln]
    assert bad == [], f"发现未限定的 c_void_p：{bad}"


def test_snap_is_skipped_gracefully_when_capture_fails(monkeypatch):
    """抓不到像素时必须原样返回（不能因为吸附失败而让点击整体报错）。"""
    mod = _load_module()
    monkeypatch.setattr(mod, "_region_snapshot_at",
                        lambda x, y, radius=48: (b"", x, y, 0, 0))
    assert mod._snap_to_element(123, 456) == (123, 456, "")


def _synth_two_panels(radius=48, left=(40, 40, 40), right=(230, 230, 230), split=40):
    """造一块「左右两个面板并排」的区域：模拟侧栏 | 内容区在交界处的真实版面。"""
    w = h = radius * 2
    buf = bytearray(w * h * 4)
    for y in range(h):
        for x in range(w):
            c = left if x < split else right
            o = (y * w + x) * 4
            buf[o:o + 4] = bytes((c[2], c[1], c[0], 255))
    return buf, w, h


def test_snap_finds_element_flush_against_a_neighbour_panel(monkeypatch):
    """区域横跨两个面板、目标就贴在交界上时，仍要吸附到目标本身。

    这是「点击精度不足」的真实主因：单色背景模型会把**另一个面板整片**判成前景，
    目标与它相邻 ⇒ 并进同一块连通域 ⇒ 触到区域边界被淘汰 ⇒ 吸附彻底失效。
    逐列背景模型把两个面板各自认成背景，目标就独立出来了。
    """
    mod = _load_module()
    buf, w, h = _synth_two_panels()
    _paint_rect(buf, w, 40, 30, 72, 70, (170, 170, 170))   # 紧贴 x=40 交界右侧的按钮
    _use_region_at(mod, monkeypatch, buf, w, h, 52, 48)
    sx, sy, _note = mod._snap_to_element(1000, 2000)
    # 原点 (948,1952)；按钮局部 40~72（中心 56）/30~70（中心 50）→ (1004,2002)
    assert (sx, sy) == (1004, 2002), f"应吸附到按钮 (1004,2002)，实得 {(sx, sy)}"


def test_snap_recovers_element_on_a_vertically_graded_panel(monkeypatch):
    """深色主题的竖向渐变面板（同一列自上而下变亮）上也要能吸附到按钮。"""
    mod = _load_module()
    w = h = 96
    buf = bytearray(w * h * 4)
    for y in range(h):
        v = 40 + (y * 60) // h
        for x in range(w):
            o = (y * w + x) * 4
            buf[o:o + 4] = bytes((v, v, v, 255))
    _paint_rect(buf, w, 30, 30, 66, 70, (190, 190, 190))
    _use_region_at(mod, monkeypatch, buf, w, h, 48, 48)
    sx, sy, _note = mod._snap_to_element(1000, 2000)
    # 原点 (952,1952)；按钮局部 30~66（中心 48）/30~70（中心 50）→ (1000,2002)
    assert (sx, sy) == (1000, 2002), f"渐变面板上的按钮应能吸附，实得 {(sx, sy)}"


def test_snap_finds_element_below_a_header_strip(monkeypatch):
    """上下分层（标题栏 | 内容区）也要能吸附 —— 逐行背景模型负责这一种。"""
    mod = _load_module()
    w = h = 96
    buf = bytearray(w * h * 4)
    for y in range(h):
        c = (60, 60, 60) if y < 30 else (215, 215, 215)
        for x in range(w):
            o = (y * w + x) * 4
            buf[o:o + 4] = bytes((c[2], c[1], c[0], 255))
    _paint_rect(buf, w, 30, 32, 66, 68, (140, 140, 140))    # 紧贴分界下方的按钮
    _use_region_at(mod, monkeypatch, buf, w, h, 48, 48)
    sx, sy, _note = mod._snap_to_element(1000, 2000)
    assert (sx, sy) == (1000, 2002), f"标题栏下方的按钮应能吸附，实得 {(sx, sy)}"


def test_adaptive_mask_keeps_single_colour_path_for_plain_regions(monkeypatch):
    """普通区域（单色背景 + 一个按钮）不许走多候选模型 —— 既有行为必须原样，也不该白花 CPU。"""
    mod = _load_module()
    buf, w, h = _synth_region()
    _paint_rect(buf, w, 20, 30, 70, 70, (72, 72, 72))
    calls = []
    orig = mod._region_bg_candidates

    def _spy(*a, **k):
        calls.append(1)
        return orig(*a, **k)

    monkeypatch.setattr(mod, "_region_bg_candidates", _spy)
    _use_region(mod, monkeypatch, buf, w, h)
    assert mod._snap_to_element(1000, 2000)[:2] == (997, 2002)
    assert calls == [], "单色背景解释得通时不该启用多候选模型"


def test_adaptive_mask_refuses_to_snap_on_photographic_areas(monkeypatch):
    """整片前景又找不到任何背景结构 ⇒ 这是照片/壁纸/图像视口，放弃吸附（返回 None）。

    照片里「像控件」的块全是画面内容（人脸、屋顶、云），按它吸附等于把点击随机挪走。
    """
    mod = _load_module()
    buf, w, h = _synth_region()
    full = bytearray(b"\x01" * (w * h))
    monkeypatch.setattr(mod, "_region_bg_candidates", lambda *a, **k: [])
    assert mod._adaptive_fg_mask(bytes(buf), w, h, full, (48, 48, 48)) is None


def test_adaptive_mask_keeps_the_single_colour_mask_when_models_do_not_help(monkeypatch):
    """新模型并不比单色模型更能解释背景时，必须原样保留单色掩码；前景没超阈值时不动。"""
    mod = _load_module()
    buf, w, h = _synth_region()
    mask = mod._fg_mask(bytes(buf), w, h, (48, 48, 48), mod.SNAP_BG_DIST)
    # 每 3 个像素置 1（≈33% 前景，刚好不过触发线）—— 必须原样返回，且不调用候选模型
    calls = []
    monkeypatch.setattr(mod, "_region_bg_candidates",
                        lambda *a, **k: calls.append(1) or [])
    part = bytearray(w * h)
    for i in range(0, w * h, 3):
        part[i] = 1
    assert mod._adaptive_fg_mask(bytes(buf), w, h, part, (48, 48, 48)) is part
    assert calls == [], "前景占比没超阈值时压根不该尝试换模型"
    # 超了阈值但没有候选模型能胜过单色模型 ⇒ 保留原掩码
    half = bytearray(w * h)
    for i in range(0, w * h // 2):
        half[i] = 1
    assert mod._adaptive_fg_mask(bytes(buf), w, h, half, (48, 48, 48)) is half
    assert mod._adaptive_fg_mask(bytes(buf), w, h, mask, (48, 48, 48)) is mask




def test_apply_snap_defaults_on_and_can_be_disabled(monkeypatch):
    """click/drag 默认吸附；显式 snap=false 时一个像素都不许动。"""
    mod = _load_module()
    calls = []

    def _fake(x, y, reach=mod.SNAP_REACH_DEFAULT, max_shift=None):
        calls.append((x, y))
        return x + 5, y + 7, "已吸附"

    monkeypatch.setattr(mod, "_snap_to_element", _fake)
    assert mod._apply_snap({}, 10, 20) == (15, 27, "已吸附")        # 默认开
    assert mod._apply_snap({"snap": "auto"}, 10, 20) == (15, 27, "已吸附")
    assert mod._apply_snap({"snap": False}, 10, 20) == (10, 20, "")  # 显式关闭
    assert calls == [(10, 20), (10, 20)], "关闭时不得调用吸附"
    # 滑动/选择默认关闭
    assert mod._apply_snap({}, 10, 20, default_on=False) == (10, 20, "")


def test_click_applies_snap_and_reports_it(monkeypatch):
    """click 默认把坐标修正到元素中心，并在返回文本里如实说明位移。"""
    mod = _load_module()
    mod._STATE["interrupted"] = mod._STATE["paused"] = False
    monkeypatch.setattr(mod, "_resolve_point", lambda a, *k: (500, 400))
    monkeypatch.setattr(mod, "_snap_to_element",
                        lambda x, y, reach=48, max_shift=None: (503, 398, "已吸附到元素中心，位移 3.6px。"))
    monkeypatch.setattr(mod, "_ensure_click_target", lambda x, y, a=None: "")
    clicked = []
    monkeypatch.setattr(mod, "do_click",
                        lambda x, y, b="left", c=1, **k: clicked.append((x, y)) or True)
    out = mod._HANDLERS["click"]({"x": 500, "y": 400})
    assert clicked == [(503, 398)], "必须点吸附后的坐标"
    assert "吸附" in out and "503" in out


def test_click_with_snap_false_keeps_exact_pixel(monkeypatch):
    """snap=false 时必须精确点在给定坐标（三维视口等需要像素级精确的场景）。"""
    mod = _load_module()
    mod._STATE["interrupted"] = mod._STATE["paused"] = False
    monkeypatch.setattr(mod, "_resolve_point", lambda a, *k: (500, 400))
    monkeypatch.setattr(mod, "_snap_to_element",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不该调用吸附")))
    monkeypatch.setattr(mod, "_ensure_click_target", lambda x, y, a=None: "")
    clicked = []
    monkeypatch.setattr(mod, "do_click",
                        lambda x, y, b="left", c=1, **k: clicked.append((x, y)) or True)
    mod._HANDLERS["click"]({"x": 500, "y": 400, "snap": False})
    assert clicked == [(500, 400)]


def test_refine_point_returns_text_and_annotated_image(monkeypatch):
    """refine_point 必须同时给出修正坐标与对照图（黄=原估计、绿=吸附）。"""
    mod = _load_module()
    mod._STATE["interrupted"] = mod._STATE["paused"] = False
    monkeypatch.setattr(mod, "_resolve_point", lambda a, *k: (500, 400))
    monkeypatch.setattr(mod, "_snap_core",
                        lambda x, y, reach=mod.SNAP_REACH_DEFAULT, max_shift=None,
                        cands=None: (503, 398, "已吸附到元素中心，位移 3.6px。", []))
    seen = {}

    def _fake_zoom(cx, cy, size, factor, grid=False, cursor=False, smooth=False,
                   marks=None, boxes=None):
        seen.update({"cx": cx, "cy": cy, "marks": marks, "boxes": boxes})
        return b"PNG", 300, 300, 400, 300, 100

    monkeypatch.setattr(mod, "grab_zoom", _fake_zoom)
    out = mod._HANDLERS["refine_point"]({"x": 500, "y": 400})
    assert isinstance(out, list) and len(out) == 2
    assert out[0]["type"] == "text" and out[1]["type"] == "image"
    text = out[0]["text"]
    assert "503" in text and "398" in text and "吸附" in text
    # 放大图必须画两个标记：原估计点与吸附点
    # 注意：放大图是**以估计点为中心**的，不是以吸附点为中心 —— 候选都在估计点附近，
    # 围着它放大才能把所有候选框都收进画面，编号才对得上。
    assert (seen["cx"], seen["cy"]) == (500, 400), \
        f"放大图应以估计点为中心，实得 ({seen['cx']},{seen['cy']})"
    pts = {(m[0], m[1]) for m in seen["marks"]}
    assert pts == {(500, 400), (503, 398)}, f"应标出两个点，实得 {pts}"


def test_refine_point_says_so_when_nothing_snapped(monkeypatch):
    """没有可信元素时必须明说「未吸附」，而不是假装精修过。"""
    mod = _load_module()
    mod._STATE["interrupted"] = mod._STATE["paused"] = False
    monkeypatch.setattr(mod, "_resolve_point", lambda a, *k: (500, 400))
    monkeypatch.setattr(mod, "_snap_core",
                        lambda x, y, reach=mod.SNAP_REACH_DEFAULT, max_shift=None,
                        cands=None: (x, y, "", []))
    monkeypatch.setattr(mod, "grab_zoom",
                        lambda *a, **k: (b"PNG", 300, 300, 400, 300, 100))
    text = mod._HANDLERS["refine_point"]({"x": 500, "y": 400})[0]["text"]
    assert "未吸附" in text


def test_refine_point_is_registered_and_readonly():
    """refine_point 必须注册，且属纯查询（不点亮光环、不开操控会话）。"""
    mod = _load_module()
    assert "refine_point" in {t["name"] for t in mod.TOOLS}
    assert callable(mod._HANDLERS["refine_point"])
    assert "refine_point" not in mod._CONTROL_TOOLS


# ---------------------------------------------------------------------------
# 列表行对齐（横向内容带）
#
# 背景：用户反馈「点微信联系人列表总是点偏」。实测复现出两个根因 ——
#   ① 估计点落在行内空白区（一行几百像素宽，内容只在左边）时，搜索区域里什么都
#      没有，旧算法直接放弃吸附，用不精确的原坐标点下去 → 点到隔壁行；
#   ② 有内容时只吸到**单个汉字**（15×15），垂直容差只有半个字高。
# 补上「行/带」这一层后：y 对齐到该行内容带的中心，x 不硬拽（整行可点）。
# ---------------------------------------------------------------------------

def _use_region_at(mod, monkeypatch, buf, w, h, lx, ly):
    """把区域抓取换成固定缓冲，并指定估计点落在区域内的局部坐标 (lx,ly)。"""
    monkeypatch.setattr(
        mod, "_region_snapshot_at",
        lambda x, y, radius=48: (bytes(buf), x - lx, y - ly, w, h))


def _paint_list(buf, w, rows=3, row_h=32, content_x=(4, 28)):
    """画一个「列表」：每行左边一小块内容（头像/名字），行底一条 1px 分隔线。

    内容只占左边 —— 这正是微信联系人列表的几何（右边一大片空白）。
    """
    for i in range(rows):
        top = i * row_h
        _paint_rect(buf, w, content_x[0], top + 6,
                    content_x[1], top + row_h - 8, (90, 160, 90))
        _paint_rect(buf, w, 0, top + row_h - 1, w - 1, top + row_h - 1, (235, 235, 235))


def test_fg_bands_finds_rows_and_ignores_separator_lines():
    """行几何必须能从 y 投影里读出来，且 1px 分隔线不能被当成一行。

    分隔线横贯整行、前景像素很多，很容易被误当成一条带 —— 吸到它上面等于点在两行
    交界处，比不吸还糟（实测踩过这个坑）。
    """
    mod = _load_module()
    buf, w, h = _synth_region((255, 255, 255))
    _paint_list(buf, w, rows=3, row_h=32)
    bands = mod._fg_bands(bytes(buf), w, h, (255, 255, 255), mod.SNAP_BG_DIST)
    assert len(bands) == 3, f"应检测到 3 条行内容带（分隔线不算），实得 {len(bands)}: {bands}"
    for b in bands:
        assert b["ymax"] >= b["ymin"], f"带的 ymax 不得小于 ymin：{b}"
        assert b["ymax"] - b["ymin"] + 1 >= mod.SNAP_BAND_MIN_SPAN


def test_snap_aligns_y_to_the_row_even_when_content_is_far_in_x(monkeypatch):
    """**微信联系人列表的核心场景**：估计点落在行内空白区，离内容很远。

    旧算法在这种情况下区域里什么都没有 → 放弃吸附 → 点到隔壁行。
    现在必须仍然把 y 对齐到这一行的中心，同时 x 保持不动（整行都可点，最安全）。
    """
    mod = _load_module()
    buf, w, h = _synth_region((255, 255, 255))
    _paint_list(buf, w, rows=3, row_h=32)
    # 估计点放在行内右侧空白处：局部 x=80，内容只到 x=28（差 52px > reach/2）
    _use_region_at(mod, monkeypatch, buf, w, h, 80, 48)
    sx, sy, note = mod._snap_to_element(1000, 2000)
    # 第 2 行（局部 y 32~63）的内容带是 38~56，中心 47 → 屏幕 1952+47
    assert sy == 1999, f"y 应对齐到第 2 行的中心，实得 {sy}"
    assert sx == 1000, f"内容太远时 x 必须原样保留，实得 {sx}"
    assert "吸附" in note and "整行" in note


def test_snap_row_alignment_beats_single_glyph_snapping(monkeypatch):
    """有内容时也要吸到**行**的中心，而不是吸到某个汉字上。

    吸到单个字上时垂直容差只有半个字高；对齐到行中心才是列表该有的行为。
    """
    mod = _load_module()
    buf, w, h = _synth_region((255, 255, 255))
    _paint_list(buf, w, rows=3, row_h=32)
    _use_region_at(mod, monkeypatch, buf, w, h, 12, 40)     # 落在头像上
    sx, sy, note = mod._snap_to_element(1000, 2000)
    # 区域原点 y0 = 2000-40 = 1960；第 2 行内容带 38~56，中心 47
    assert sy == 1960 + 47, f"应对齐到行中心 2007，实得 {sy}"
    assert sx == 1000 - 12 + (4 + 28 + 1) // 2, f"x 应落在头像中心，实得 {sx}"
    assert "列表行" in note


def test_snap_keeps_x_on_the_button_in_a_toolbar_row(monkeypatch):
    """工具条（一排同高的按钮）：x 必须留在估计点所在的**那个**按钮上。

    这是「按整条带的 x 中心吸附」会引入的回归 —— 那样会点到隔壁按钮。
    """
    mod = _load_module()
    buf, w, h = _synth_region((255, 255, 255))
    for x0 in (4, 36, 68):                                   # 三个按钮，各 28 宽
        _paint_rect(buf, w, x0, 36, x0 + 27, 60, (200, 200, 200))
    _use_region_at(mod, monkeypatch, buf, w, h, 20, 48)       # 落在第一个按钮上
    sx, sy, note = mod._snap_to_element(1000, 2000)
    # 区域原点 x0 = 1000-20 = 980；第一个按钮局部 4~31，中心 18
    assert sx == 980 + 18, f"x 应吸附到第一个按钮的中心 998，实得 {sx}"
    # 若按整条带（局部 4~95）的 x 中心吸附会得到 1029 —— 那就是点到隔壁按钮了
    assert sx != 980 + (4 + 95 + 1) // 2
    assert sy == 2000, f"y 应吸附到按钮行的中心 2000，实得 {sy}"


def test_snap_never_drags_to_region_centre_on_continuous_content(monkeypatch):
    """连续内容（Blender 视口/画布）不得被当成一行。

    否则「对齐到带中心」会把点击拖到区域正中间 —— 纯粹的伤害。必须退回连通域逻辑，
    而那块连续内容又会因面积过大被淘汰，最终原样返回。
    """
    mod = _load_module()
    buf, w, h = _synth_region((48, 48, 48))
    _paint_rect(buf, w, 2, 2, w - 3, h - 3, (72, 72, 72))     # 几乎铺满 = 连续画面
    _use_region_at(mod, monkeypatch, buf, w, h, 48, 20)
    sx, sy, note = mod._snap_to_element(1000, 2000)
    assert (sx, sy) == (1000, 2000), f"连续画面不得被当成行，实得 {(sx, sy)}"


def test_snap_still_prefers_the_element_inside_a_wide_band(monkeypatch):
    """带内的候选仍按「离估计点最近」挑，且估计点在候选内时距离为 0。"""
    mod = _load_module()
    buf, w, h = _synth_region((255, 255, 255))
    _paint_rect(buf, w, 6, 36, 30, 60, (200, 200, 200))       # 左块
    _paint_rect(buf, w, 60, 36, 88, 60, (200, 200, 200))      # 右块
    _use_region_at(mod, monkeypatch, buf, w, h, 70, 48)       # 落在右块内
    sx, sy, note = mod._snap_to_element(1000, 2000)
    # 区域原点 x0 = 1000-70 = 930；右块局部 60~88，中心 74；左块中心只有 18
    assert sx == 930 + 74, f"应选右块中心 1004，实得 {sx}"


def test_refine_point_emits_a_row_ruler(monkeypatch):
    """列表场景下 refine_point 必须给出「行标尺」：本区域每条带的中心 y。

    模型看图是**估**不出第几行在哪里的 —— 这把标尺才是解决「点偏」的关键。
    """
    mod = _load_module()
    mod._STATE["interrupted"] = mod._STATE["paused"] = False
    monkeypatch.setattr(mod, "_resolve_point", lambda a, *k: (500, 400))
    monkeypatch.setattr(mod, "_snap_core",
                        lambda x, y, reach=mod.SNAP_REACH_DEFAULT, max_shift=None, cands=None:
                        (500, 400, "", [{"y": 372, "h": 15}, {"y": 404, "h": 15}]))
    monkeypatch.setattr(mod, "grab_zoom",
                        lambda *a, **k: (b"PNG", 300, 300, 400, 300, 100))
    text = mod._HANDLERS["refine_point"]({"x": 500, "y": 400})[0]["text"]
    assert "行标尺" in text and "372" in text and "404" in text
    assert "不在任何一条带上" not in text, "y 明明落在带上，不该报空隙警告"


def test_refine_point_warns_when_y_falls_in_the_gap_between_rows(monkeypatch):
    """y 落在两行之间的空隙里时要点名 —— 这正是「点偏」最常见的原因。"""
    mod = _load_module()
    mod._STATE["interrupted"] = mod._STATE["paused"] = False
    monkeypatch.setattr(mod, "_resolve_point", lambda a, *k: (500, 500))
    monkeypatch.setattr(mod, "_snap_core",
                        lambda x, y, reach=mod.SNAP_REACH_DEFAULT, max_shift=None, cands=None:
                        (500, 500, "", [{"y": 372, "h": 15}, {"y": 404, "h": 15}]))
    monkeypatch.setattr(mod, "grab_zoom",
                        lambda *a, **k: (b"PNG", 300, 300, 400, 300, 100))
    text = mod._HANDLERS["refine_point"]({"x": 500, "y": 500})[0]["text"]
    assert "不在任何一条带上" in text


def test_band_snapping_is_deterministic(monkeypatch):
    """同样输入必得同样输出：吸附结果必须是确定性的。"""
    mod = _load_module()
    buf, w, h = _synth_region((255, 255, 255))
    _paint_list(buf, w, rows=3, row_h=32)
    _use_region_at(mod, monkeypatch, buf, w, h, 80, 48)
    first = mod._snap_to_element(1000, 2000)
    assert all(mod._snap_to_element(1000, 2000) == first for _ in range(5))


# ---------------------------------------------------------------------------
# 落点算法：质心 + 空洞纠正 + 细长块惩罚（2026-10-25 精度升级）
#
# 背景：纯坐标方案「点得不准」的剩余三类根因（都在真实 QQ/Blender 界面上复现过）：
#   ① 外接框中心对不对称形状（气泡带尾巴、播放键、箭头）偏离视觉质量中心；
#   ② 环形/凹形元素（圆环图标、文字行里的空格、U 形支架）的框中心落在**洞里**，
#      点击直接打空 —— 最隐蔽的一类点偏；
#   ③ 分隔线/滚动条等一维细条夹在按钮之间时，按「中心距离」会赢过按钮本体。
# 修法：落点改取前景质量质心，且必须通过「该像素确实是前景」的检验；不合格按
# 框心 → 螺旋外扩降级；排序里同距离时非细长块优先。
# ---------------------------------------------------------------------------

def test_snap_lands_on_centroid_for_asymmetric_shape(monkeypatch):
    """不对称形状（方块 + 尾巴）：落点取质心，被尾巴拽偏的框中心不再用它。

    几何：大方块 (20,10)-(60,60) + 小尾巴 (60,61)-(70,90)。整体外接框
    (20,10)-(70,90) 的旧框心是 (45,50)，被尾巴拽到下方；质心靠近方块主体
    (≈43,40)。带高 81 > 区域高的 62% ⇒ 行带模式被拒，走连通域模式。
    """
    mod = _load_module()
    buf, w, h = _synth_region((48, 48, 48))
    _paint_rect(buf, w, 20, 10, 60, 60, (200, 120, 90))     # 主体
    _paint_rect(buf, w, 60, 61, 70, 90, (200, 120, 90))     # 尾巴（与主体相接）
    _use_region(mod, monkeypatch, buf, w, h)                # 估计点落在局部 (48,48)
    sx, sy, note = mod._snap_to_element(1000, 2000)
    # 区域原点 = 估计点 - 48；期望落点局部 (43,41) → 屏幕 (995,1993)
    assert (sx, sy) == (995, 1993), f"应吸附到主体质心 (995,1993)，实得 {(sx, sy)}"
    assert "吸附" in note
    # 旧框心是 (997,2002)：质心必须明显更靠近主体（y 至少少 8px）
    assert sy <= 1994


def test_snap_escapes_holes_for_ring_shapes(monkeypatch):
    """环形/凹形元素：框中心落在洞里（背景像素）会点空 —— 落点必须落在元素上。

    几何：圆角方环 (20,10)-(76,86)，洞 (38,28)-(58,68)。估计点恰好在洞心
    (48,48)。旧算法取框心 = 洞心 = 背景，点下去打空；新算法螺旋外扩到环上
    最近的前景像素（洞左缘 x=38）。
    """
    mod = _load_module()
    buf, w, h = _synth_region((48, 48, 48))
    _paint_rect(buf, w, 20, 10, 76, 86, (210, 210, 210))     # 外圈
    _paint_rect(buf, w, 38, 28, 58, 68, (48, 48, 48))        # 挖洞（背景色）
    _use_region(mod, monkeypatch, buf, w, h)
    sx, sy, note = mod._snap_to_element(1000, 2000)
    # 洞心局部 (48,48) → 屏幕 (1000,2000)；洞占 38..58×28..68，前 10 环都在洞里，
    # 第 11 环（外框 [37,59]×[37,59] 的边界）首次出现前景 → 环序第一个命中 (37,37)
    assert (sx, sy) == (989, 1989), f"落点应螺旋外扩到环上，实得 {(sx, sy)}"
    assert "吸附" in note
    # 「落点必须落在元素自身上」由 _landing_point 单元测试直接复核掩码；
    # 这里只锁端到端行为：吸附确实发生了，且落点在环的外接框内。
    assert 952 + 20 <= sx <= 952 + 76 and 1952 + 10 <= sy <= 1952 + 86


def test_snap_prefers_button_over_slender_separator(monkeypatch):
    """同距离时按钮块胜过细长条（分隔线/滚动条不会再把点击拽走）。

    几何：按钮 (30,30)-(60,60) 与竖向滚动条 (70,10)-(78,90)。估计点 (65,48)
    到两者外接框的距离都是 5。旧排序按「中心距离」：滚动条中心 (74,50) 比按钮
    中心 (45,45) 更近 ⇒ 点击被拽到滚动条上；新排序同距离时非细长块优先 ⇒ 按钮。
    """
    mod = _load_module()
    buf, w, h = _synth_region((48, 48, 48))
    _paint_rect(buf, w, 30, 30, 60, 60, (200, 200, 200))     # 按钮
    _paint_rect(buf, w, 70, 10, 78, 90, (120, 120, 120))     # 细长滚动条
    _use_region_at(mod, monkeypatch, buf, w, h, 65, 48)      # 估计点局部 (65,48)
    sx, sy, note = mod._snap_to_element(1000, 2000)
    # 区域原点 = (1000-65, 2000-48) = (935,1952)；按钮中心局部 (45,45)
    assert (sx, sy) == (980, 1997), f"应吸附到按钮中心 (980,1997)，实得 {(sx, sy)}"
    assert "吸附" in note


def test_landing_point_requires_pixel_on_the_element(monkeypatch):
    """_landing_point 单元测试：质心在洞里 → 框心在洞里 → 螺旋外扩落到元素上；
    实心形状下质心与旧框心完全一致（既有行为不变）。"""
    mod = _load_module()
    w = h = 96
    bg = (48, 48, 48)
    mask = bytearray(w * h)
    # 环形：外圈 (20,10)-(76,86)，洞 (38,28)-(58,68)
    for y in range(10, 87):
        for x in range(20, 77):
            if not (38 <= x <= 58 and 28 <= y <= 68):
                mask[y * w + x] = 1
    comp = {"area": sum(mask), "minx": 20, "miny": 10, "maxx": 76, "maxy": 86,
            "sx": sum(x for y in range(10, 87) for x in range(20, 77)
                      if mask[y * w + x]),
            "sy": sum(y for y in range(10, 87) for x in range(20, 77)
                      if mask[y * w + x])}
    px, py = mod._landing_point(comp, mask, w, h)
    assert mask[py * w + px] == 1, "落点必须是前景像素"
    assert 20 <= px <= 76 and 10 <= py <= 86, "落点不许离开外接框"
    assert px < 48, "洞在 38..58，落点应落在洞的左缘"
    # 实心矩形：质心（四舍五入）必须与旧框心 (min+max+1)//2 一致
    solid = bytearray(w * h)
    for y in range(30, 61):
        for x in range(20, 61):
            solid[y * w + x] = 1
    scomp = {"area": sum(solid), "minx": 20, "miny": 30, "maxx": 60, "maxy": 60,
             "sx": sum(x for y in range(30, 61) for x in range(20, 61)),
             "sy": sum(y for y in range(30, 61) for x in range(20, 61))}
    assert mod._landing_point(scomp, solid, w, h) == (40, 45), \
        "实心矩形下质心必须等于旧框心，保证既有行为不变"


def test_merge_carries_centroid_mass(monkeypatch):
    """碎片合并后 sx/sy 必须相加：落点要按合并后的整体质心算。

    几何：一个不对称目标被高光切成左右两半（中缝 2px）。旧代码只合并外接框，
    落点退化为框心；合并后质心偏向质量大的左半。
    """
    mod = _load_module()
    comps = [{"area": 400, "sx": 400 * 25, "sy": 400 * 40,
              "minx": 10, "miny": 30, "maxx": 34, "maxy": 69},
             {"area": 100, "sx": 100 * 45, "sy": 100 * 40,
              "minx": 37, "miny": 30, "maxx": 46, "maxy": 39}]
    merged = mod._merge_components(comps)
    assert len(merged) == 1
    m = merged[0]
    assert m["sx"] == 400 * 25 + 100 * 45 and m["sy"] == 400 * 40 + 100 * 40
    # 合并后质心 x = (10000+4500)/500 = 29（偏向质量大的左半），框心则是 28
    assert (m["sx"] + m["area"] // 2) // m["area"] == 29


def test_snap_candidate_centers_use_landing_points(monkeypatch):
    """refine_point 候选菜单的坐标必须用落点（质心），不是外接框中心。

    候选坐标是模型唯一会照抄/按编号点的数字，必须是最准的那个。
    """
    mod = _load_module()
    buf, w, h = _synth_region((48, 48, 48))
    # 单个不对称目标：方块 + 右下小尾巴
    _paint_rect(buf, w, 20, 20, 60, 55, (200, 200, 200))
    _paint_rect(buf, w, 60, 55, 68, 68, (200, 200, 200))
    _use_region(mod, monkeypatch, buf, w, h)
    cands = []
    mod._snap_core(1000, 2000, cands=cands)
    blocks = [c for c in cands if c["kind"] == "块"]
    assert blocks, "应至少有一个块候选"
    c = blocks[0]
    # 区域原点 (952,1952)；框心 (44,44) → (996,1996)；质心偏左上
    assert (c["x"], c["y"]) != (996, 1996), "候选坐标不应还是旧框心"
    assert c["x"] < 996 and c["y"] < 1996, f"候选应取质心，实得 {(c['x'], c['y'])}"


def test_bands_carry_their_horizontal_extent(monkeypatch):
    """行带必须带水平范围（xmin/xmax）—— 候选菜单要把每一行**框出来**给模型看。

    没有 x 范围就只能画一条通栏横线，「按编号挑行」就无从谈起。
    """
    mod = _load_module()
    buf, w, h = _synth_region((255, 255, 255))
    _paint_list(buf, w, rows=3, row_h=32)
    bands = mod._fg_bands(bytes(buf), w, h, (255, 255, 255), mod.SNAP_BG_DIST)
    assert len(bands) == 3
    for b in bands:
        assert b["xmax"] >= b["xmin"]
        assert b["xmin"] < 8 <= b["xmax"], f"水平范围应覆盖左侧内容区，实得 {b}"


def test_candidate_menu_numbers_every_target_with_screen_coords(monkeypatch):
    """候选菜单必须把区域内每个「像控件的目标」编号列出，并给出屏幕坐标与尺寸。

    这是解决「按钮密排时吸附到隔壁」的关键：吸附只挑**最近**的那一个，估计偏一点
    就会稳稳点错；这里给出全部候选，让模型「认目标」而不是「估位置」。
    """
    mod = _load_module()
    buf, w, h = _synth_region((48, 48, 48))
    # 三个并排按钮（工具栏几何），中间留背景缝隙——缝隙把它们切成三个连通域。
    # 注意 x 范围必须留在 96 宽的区域内：画到界外会写到下一行，把区域数据写坏。
    for x0 in (6, 35, 64):
        _paint_rect(buf, w, x0, 40, x0 + 25, 58, (72, 72, 72))
    # 估计点落在**中间**那个按钮的中心：局部 (48,49)
    _use_region_at(mod, monkeypatch, buf, w, h, 48, 49)
    cands = []
    sx, sy, _note, _ruler = mod._snap_core(1000, 2000, mod.SNAP_REACH_DEFAULT,
                                            cands=cands)

    assert len(cands) == 4, f"应给出 3 个按钮 + 1 条行带，实得 {len(cands)}: {cands}"
    assert [c["i"] for c in cands] == list(range(1, len(cands) + 1)), "编号必须从 1 连续排下来"
    ds = [c["d"] for c in cands]
    assert ds == sorted(ds), f"候选必须按离估计点由近到远排列，实得 {ds}"
    blocks = [c for c in cands if c["kind"] == "块"]
    assert len(blocks) == 3, f"三个按钮应各自成为一个候选，实得 {len(blocks)}"
    for c in blocks:
        assert (c["w"], c["h"]) == (26, 19), f"候选尺寸应等于按钮本身，实得 {c}"
    # 「行」类候选只对齐 y：x 必须保持估计点的 x（否则会把点击拽到带中心，点到隔壁按钮）
    for c in cands:
        if c["kind"] == "行":
            assert c["x"] == 1000, f"行候选的 x 必须是估计点的 x，实得 {c['x']}"
    # 屏幕坐标 = 区域原点 + 局部坐标。原点 = (1000-48, 2000-49) = (952,1951)
    left = min(blocks, key=lambda c: c["x"])
    assert left["x"] == 952 + 19 == 971, f"最左按钮中心的屏幕 x 应为 971，实得 {left['x']}"
    assert (sx, sy) == (1000, 2000), f"估计点本就在中间按钮中心，吸附结果不应移动：{(sx, sy)}"


def test_refine_point_lets_the_model_pick_a_neighbouring_candidate(monkeypatch):
    """模型必须能说「不，是隔壁那个」。

    这是本工具存在的理由：吸附只挑最近的一个，估计偏一点就点到隔壁。这里必须让
    模型用 pick=N 改选，并且**明确指出要点就得带 snap=false**。
    """
    mod = _load_module()
    mod._STATE["interrupted"] = mod._STATE["paused"] = False
    buf, w, h = _synth_region((48, 48, 48))
    for x0 in (6, 35, 64):
        _paint_rect(buf, w, x0, 40, x0 + 25, 58, (72, 72, 72))
    _use_region_at(mod, monkeypatch, buf, w, h, 48, 49)   # 估计在中间按钮
    seen = {}

    def _fake_zoom(cx, cy, size, factor, grid=False, cursor=False, smooth=False,
                   marks=None, boxes=None):
        seen.update({"cx": cx, "cy": cy, "boxes": boxes, "marks": marks})
        return b"PNG", 300, 300, 400, 300, 216

    monkeypatch.setattr(mod, "grab_zoom", _fake_zoom)
    # 先看菜单，拿到最左边那个按钮的编号
    out = mod._HANDLERS["refine_point"]({"x": 1000, "y": 2000, "space": "screen"})
    text = out[0]["text"]
    cands = []
    mod._snap_core(1000, 2000, mod.SNAP_REACH_DEFAULT, cands=cands)
    target = min(cands, key=lambda c: c["x"])
    idx = target["i"]

    # 再指定那个编号
    out2 = mod._HANDLERS["refine_point"]({"x": 1000, "y": 2000, "space": "screen",
                                          "pick": idx})
    text2 = out2[0]["text"]
    assert str(target["x"]) in text2 and str(target["y"]) in text2, \
        f"指定 #{idx} 后目标点应变成该候选的中心 ({target['x']},{target['y']})"
    assert f"#{idx}" in text2
    # 必须教模型下一步怎么点：带 snap=false 的 click
    assert 'snap=false' in text2 and 'space="screen"' in text2, \
        "必须给出可直接照抄的 click 调用，并强调它带 snap=false"
    # 图上必须把候选逐个框出来并编号
    boxes = seen["boxes"] or []
    labels = {int(b[5]) for b in boxes}
    assert labels == {c["i"] for c in cands}, f"候选框编号应覆盖全部候选，实得 {labels}"
    picked = [b for b in boxes if b[5] == str(idx)]
    assert picked and picked[0][4] == 0x00FF00FF, "选中的候选应画成洋红色以区别于其它候选"


def test_snap_normalisation_is_the_exact_inverse_of_windows_rounding():
    """注入归一化必须与 Windows 的回程换算互为逆运算，否则个别像素系统性偏 1px。

    Windows 把归一化值换算回像素用的是 floor(nx * 屏宽 / 65536)（本机 1920 宽屏
    实测 80 个点验证）。旧公式 round(v*65535/(w-1)) 求逆方向不一致，1920 宽屏上有
    7 个 x（1,2,3,8,9,16,23）会偏 1px。本测试不用鼠标，纯算术全量验证新公式。
    """
    mod = _load_module()
    for vw in (1920, 1366, 2560, 3840, 5120):
        bad = [v for v in range(vw)
               if (int(mod._norm_abs(v, 0, vw)) * vw // 65536) != v]
        assert bad == [], f"屏宽 {vw} 下归一化不精确，出错的像素：{bad[:10]}"
    # 本机 1920 宽屏上，旧公式实测有 7 个像素偏 1px —— 这条就是这次修复的量化依据。
    old_bad = [v for v in range(1920)
               if (max(0, min(65535, int(round(v * 65535 / 1919)))) * 1920 // 65536) != v]
    assert old_bad == [1, 2, 3, 8, 9, 16, 23], f"旧公式的出错像素应与本机实测一致，实得 {old_bad}"


def test_click_reports_the_actual_cursor_landing(monkeypatch):
    """点击必须**回读真实落点**并报告 —— 否则注入链路有偏差时是静默点空。"""
    mod = _load_module()
    mod._STATE["interrupted"] = mod._STATE["paused"] = False
    monkeypatch.setattr(mod, "_resolve_point", lambda a, *k: (500, 400))
    monkeypatch.setattr(mod, "_apply_snap", lambda a, x, y, default_on=True: (x, y, ""))
    monkeypatch.setattr(mod, "_ensure_click_target", lambda x, y, a: "")
    monkeypatch.setattr(mod, "_input_mode", lambda a: "mouse")
    monkeypatch.setattr(mod, "_pre_click_probe", lambda x, y, h: ())
    monkeypatch.setattr(mod, "_click_verify", lambda x, y, before, h: "")
    monkeypatch.setattr(mod, "do_click", lambda x, y, b, c, mode="auto": True)

    # 一致 → 明确告知 0 偏差
    monkeypatch.setattr(mod, "_cursor_pos", lambda: (500, 400))
    monkeypatch.setattr(mod, "_refresh_env", lambda: None)
    ok = mod._HANDLERS["click"]({"x": 500, "y": 400})
    assert "落点回读" in ok and "0 偏差" in ok

    # 不一致 → 必须显式警告，而不是让模型以为点对了
    monkeypatch.setattr(mod, "_cursor_pos", lambda: (512, 396))
    bad = mod._HANDLERS["click"]({"x": 500, "y": 400})
    assert "落点回读" in bad and "512" in bad and "396" in bad
    assert "verify_point" in bad, "落点有偏差时必须提示用 verify_point 自检"


def test_do_scroll_splits_a_big_delta_into_ticks(monkeypatch):
    """滚轮必须按格分段发送：一次甩大 delta 会被很多应用 clamp 成一格。"""
    mod = _load_module()
    sent = []
    monkeypatch.setattr(mod, "_send_mouse",
                        lambda flag, dx=0, dy=0, data=0: sent.append((flag, data)))
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    mod.do_scroll(-600)
    assert len(sent) == 5, sent
    assert sum(d if d < 2 ** 31 else d - 2 ** 32 for _f, d in sent) == -600
    # 显式 ticks 覆盖
    sent.clear()
    mod.do_scroll(240, ticks=2)
    assert len(sent) == 2
    assert sum(d for _f, d in sent) == 240


def test_scroll_strength_maps_to_wheel_delta(monkeypatch):
    """strength 是「力度」：1~10 映射到 1~10 格，方向由 direction 决定。"""
    mod = _load_module()
    mod._STATE["interrupted"] = mod._STATE["paused"] = False
    got = []
    monkeypatch.setattr(mod, "do_scroll", lambda d: got.append(d))
    monkeypatch.setattr(mod, "_refresh_env", lambda: None)
    mod._HANDLERS["scroll"]({"strength": 5, "direction": "down"})
    assert got == [-600], got
    mod._HANDLERS["scroll"]({"strength": 10})
    assert got[-1] == 1200
    mod._HANDLERS["scroll"]({"strength": 1})
    assert got[-1] == 120
    # 超范围要夹紧
    mod._HANDLERS["scroll"]({"strength": 99})
    assert got[-1] == 1200
    # delta 优先级更高（精确格数场景）
    mod._HANDLERS["scroll"]({"delta": -240, "strength": 9})
    assert got[-1] == -240
    with pytest.raises(ValueError):
        mod._HANDLERS["scroll"]({})


def test_drag_file_profile_uses_slow_presets_and_disables_snap(monkeypatch):
    """拖文件 = 慢速 + 按下停顿 + 落点悬停 + 越界回位，且不做元素吸附。

    资源管理器的 OLE 拖放要靠拖放目标的 DragEnter/DragOver 才能释放成功，
    默认那套「500ms 拖过去就松手」在 QQ/微信上经常放不下去。
    """
    mod = _load_module()
    mod._STATE["interrupted"] = mod._STATE["paused"] = False
    snaps = []

    def _fake_snap(args, x, y, default_on=True):
        snaps.append(default_on)
        return x, y, ""

    rec = {}

    def _fake_drag(x1, y1, x2, y2, duration, hold=0.15, mode="auto",
                   steps=0, hover=None, overshoot=0):
        rec.update({"x1": x1, "y1": y1, "x2": x2, "y2": y2, "dur": duration,
                    "hold": hold, "steps": steps, "hover": hover,
                    "overshoot": overshoot})
        return True

    monkeypatch.setattr(mod, "_resolve_point",
                        lambda a, kx="x", ky="y": (100, 200) if kx == "x1" else (900, 600))
    monkeypatch.setattr(mod, "_apply_snap", _fake_snap)
    monkeypatch.setattr(mod, "_ensure_click_target", lambda x, y, a: "")
    monkeypatch.setattr(mod, "_input_mode", lambda a: "mouse")
    monkeypatch.setattr(mod, "_refresh_env", lambda: None)
    monkeypatch.setattr(mod, "_log", lambda who, text: None)
    monkeypatch.setattr(mod, "do_drag", _fake_drag)

    out = mod._HANDLERS["drag"]({"x1": 100, "y1": 200, "x2": 900, "y2": 600,
                                 "profile": "file"})
    assert snaps == [False, False], "拖文件必须关掉吸附"
    assert rec["hold"] == pytest.approx(mod.DRAG_FILE_HOLD_MS / 1000.0)
    assert rec["hover"] == pytest.approx(mod.DRAG_FILE_HOVER_MS / 1000.0)
    assert rec["steps"] == mod.DRAG_FILE_STEPS
    assert rec["overshoot"] == mod.DRAG_FILE_OVERSHOOT
    assert rec["dur"] == pytest.approx(mod.DRAG_FILE_DURATION_MS / 1000.0)
    assert "拖文件模式" in out and "paste" in out

    # 普通拖拽仍走「吸附 + 500ms」，别把拖文件的重节奏带到普通拖拽上
    snaps.clear()
    mod._HANDLERS["drag"]({"x1": 100, "y1": 200, "x2": 900, "y2": 600})
    assert snaps == [True, True]
    assert rec["hold"] == pytest.approx(0.15)
    assert rec["steps"] == 0 and rec["overshoot"] == 0
    assert rec["dur"] == pytest.approx(0.5)


def test_click_retries_the_next_candidate_when_nothing_changed(monkeypatch):
    """verify 判定没点中时，自动换同一目标的次优候选再点一次。

    吸附偶尔会挑到隔壁那个连通域（密排列表里最常见），现象就是「点了没反应」。
    这类情况只差十几像素，让插件自己换候选重试一次，比让模型重新截图重新估坐标
    既准得多又快得多（还省一轮 token）。
    """
    mod = _load_module()
    mod._STATE["interrupted"] = mod._STATE["paused"] = False
    clicks = []
    monkeypatch.setattr(mod, "_resolve_point", lambda a, *k: (500, 400))
    monkeypatch.setattr(mod, "_apply_snap", lambda a, x, y, default_on=True: (510, 402, "已吸附"))
    monkeypatch.setattr(mod, "_ensure_click_target", lambda x, y, a: "")
    monkeypatch.setattr(mod, "_input_mode", lambda a: "mouse")
    monkeypatch.setattr(mod, "_pre_click_probe", lambda x, y, h: ())
    monkeypatch.setattr(mod, "_cursor_pos", lambda: (510, 402))
    monkeypatch.setattr(mod, "_refresh_env", lambda: None)
    monkeypatch.setattr(mod, "do_click",
                        lambda x, y, b, c, mode="auto": clicks.append((x, y)) or True)
    monkeypatch.setattr(mod, "_alt_snap_point", lambda ex, ey, ux, uy, r: (530, 404))
    seq = [(False, 0.0, "落点没有变化"), (True, 0.0, "界面已响应")]

    def _fake_verify(x, y, before, hwnd=0):
        return seq.pop(0) if seq else (True, 0.0, "")

    monkeypatch.setattr(mod, "_click_verify_ex", _fake_verify)

    out = mod._HANDLERS["click"]({"x": 500, "y": 400, "verify": True})
    assert clicks == [(510, 402), (530, 404)], clicks
    assert "自动改点同一目标的次优候选" in out
    assert "530" in out and "404" in out


def test_click_retry_can_be_disabled_and_is_bounded(monkeypatch):
    """retry=false 时不重试；两个候选都没效果时也只重试一次（不无限点）。"""
    mod = _load_module()
    mod._STATE["interrupted"] = mod._STATE["paused"] = False
    clicks = []
    monkeypatch.setattr(mod, "_resolve_point", lambda a, *k: (500, 400))
    monkeypatch.setattr(mod, "_apply_snap", lambda a, x, y, default_on=True: (510, 402, ""))
    monkeypatch.setattr(mod, "_ensure_click_target", lambda x, y, a: "")
    monkeypatch.setattr(mod, "_input_mode", lambda a: "mouse")
    monkeypatch.setattr(mod, "_pre_click_probe", lambda x, y, h: ())
    monkeypatch.setattr(mod, "_cursor_pos", lambda: (510, 402))
    monkeypatch.setattr(mod, "_refresh_env", lambda: None)
    monkeypatch.setattr(mod, "do_click",
                        lambda x, y, b, c, mode="auto": clicks.append((x, y)) or True)
    monkeypatch.setattr(mod, "_alt_snap_point", lambda *a: (530, 404))
    monkeypatch.setattr(mod, "_click_verify_ex", lambda *a: (False, 0.0, "落点没有变化"))

    mod._HANDLERS["click"]({"x": 500, "y": 400, "verify": True, "retry": False})
    assert clicks == [(510, 402)]
    clicks.clear()
    out = mod._HANDLERS["click"]({"x": 500, "y": 400, "verify": True})
    assert clicks == [(510, 402), (530, 404)], "最多只重试一次"
    assert "已停止重试" in out


def test_grab_screen_reports_virtual_desktop_origin(monkeypatch):
    """整屏截图必须覆盖虚拟桌面并返回其原点（多显示器时原点可能为负）。"""
    mod = _load_module()
    monkeypatch.setattr(mod, "_virtual_screen", lambda: (-1920, -300, 3840, 1380))
    monkeypatch.setattr(mod, "_cursor_pos", lambda: (0, 0))
    seen = {}

    def _fake_capture(src_dc, sx, sy, sw, sh, tw, cells, cursor_pt=None,
                      smooth=True, marks=None, level=6):
        seen.update({"sx": sx, "sy": sy, "sw": sw, "sh": sh})
        return b"PNG", 1280, 460

    monkeypatch.setattr(mod, "_capture_scaled", _fake_capture)
    monkeypatch.setattr(mod.user32, "GetDC", lambda h: 1)
    monkeypatch.setattr(mod.user32, "ReleaseDC", lambda h, d: 1)
    png, iw, ih, ox, oy, w, h = mod.grab_screen()
    assert (ox, oy, w, h) == (-1920, -300, 3840, 1380)
    assert (seen["sx"], seen["sy"], seen["sw"], seen["sh"]) == (-1920, -300, 3840, 1380), \
        "必须从虚拟桌面原点抓取，而不是主屏 (0,0)"
    assert (iw, ih) == (1280, 460)


def test_screen_info_mentions_snap_and_transform(monkeypatch):
    """环境信息要交代吸附能力与当前换算公式 —— 模型据此才知道有精修手段可用。"""
    mod = _load_module()
    mod._STATE["interrupted"] = mod._STATE["paused"] = False
    monkeypatch.setattr(mod, "_virtual_screen", lambda: (0, 0, 1920, 1080))
    out = mod._HANDLERS["screen_info"]({})
    assert "refine_point" in out and "吸附" in out
    assert "虚拟桌面" in out


def test_click_window_also_snaps_by_default(monkeypatch):
    """窗口相对点击是「窗口内精确点控件」的主路径，也必须默认吸附。"""
    mod = _load_module()
    mod._STATE["interrupted"] = mod._STATE["paused"] = False

    class _R:
        left, top, right, bottom = 1000, 500, 1200, 700

    monkeypatch.setattr(mod, "window_rect", lambda h: _R)
    monkeypatch.setattr(mod, "_target_hwnd", lambda args, required=True: 42)
    monkeypatch.setattr(mod, "_prepare_target", lambda hwnd: "")
    monkeypatch.setattr(mod, "_hit_check", lambda root, x, y: "")
    monkeypatch.setattr(mod, "_snap_to_element",
                        lambda x, y, reach=48, max_shift=None: (x + 3, y + 4, "已吸附"))
    clicked = []
    monkeypatch.setattr(mod, "do_click",
                        lambda x, y, b="left", c=1, **k: clicked.append((x, y)) or True)
    out = mod._HANDLERS["click_window"]({"hwnd": 42, "rel_x": 10, "rel_y": 20})
    assert clicked == [(1013, 524)], "窗口相对点击也必须落在吸附后的坐标"
    assert "吸附" in out


def test_verify_point_accepts_screen_space(monkeypatch):
    """verify_point 传 space=screen 时不得再按截图基准换算（否则自检会报假偏差）。"""
    mod = _load_module()
    mod._STATE["interrupted"] = mod._STATE["paused"] = False
    mod._set_view(100, 50, 1920, 1080, 1280, 720, "full")
    moved = []
    monkeypatch.setattr(mod, "_move_absolute", lambda x, y: moved.append((x, y)))
    monkeypatch.setattr(mod, "_cursor_pos", lambda: moved[-1] if moved else (0, 0))
    monkeypatch.setattr(mod, "_owner_window", lambda x, y: 0)
    out = mod._HANDLERS["verify_point"]({"x": 500, "y": 400, "space": "screen"})
    assert moved == [(500, 400)], "space=screen 应直通物理坐标"
    assert "完全一致" in out


def test_process_name_returns_the_real_executable_name(monkeypatch):
    """process_name 必须真的返回进程名（曾是静默故障：裸 byref 抛 NameError 被 try 吞掉）。

    实测故障形态：前台窗口本该返回 "WorkBuddyAI.exe"，实际返回 ""。因为
    list_windows 用进程名给窗口打标，静默返回空串会让模型丢掉「这个窗口到底是
    哪个程序」这一条识别依据。本测试只打桩 Win32 调用，不依赖真实桌面。
    """
    mod = _load_module()
    # byref(pid) 在桩里直接返回 pid 本体，这样桩函数才能直接写 pid.value
    monkeypatch.setattr(mod.ctypes, "byref", lambda o: o)
    monkeypatch.setattr(mod.user32, "GetWindowThreadProcessId",
                        lambda h, p: (setattr(p, "value", 4242), 1)[1])
    monkeypatch.setattr(mod.kernel32, "OpenProcess", lambda a, b, c: 77)
    monkeypatch.setattr(mod.kernel32, "CloseHandle", lambda h: 1)

    def _fake_query(handle, flags, buf, size):
        buf.value = "C:\\Program Files\\WorkBuddyAI\\WorkBuddyAI.exe"
        size.value = len(buf.value)
        return 1

    monkeypatch.setattr(mod.kernel32, "QueryFullProcessImageNameW", _fake_query)
    assert mod.process_name(12345) == "WorkBuddyAI.exe"


def test_no_bare_byref_call_remains():
    """反向保险：模块里不许再有裸 byref(...) 调用。

    这是上面那个静默故障的另一半保障 —— 运行时那条路径被 try 吃掉了，所以只能用
    AST 静态检查：本模块只 import ctypes，裸 byref 会在任何用到它的函数里抛
    NameError，然后被各自的 try 吞成空返回值。
    """
    import ast
    from pathlib import Path

    src = Path("src/zhuzhu_Copilot/plugins/computer-control/server.py").read_text(
        encoding="utf-8")
    tree = ast.parse(src)
    bare = [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "byref"]
    assert bare == [], (
        f"server.py 里有裸 byref 调用（行 {bare}）：本模块只 import ctypes，"
        "裸 byref 会抛 NameError，且很可能被对应的 try 吞掉，导致静默返回空值")


# ============================================================================
# 任务栏引擎：通栏检测 / 双向渐变背景 / 列活动度分段 / 编号点击
# ============================================================================
def _synth_taskbar(w=1200, band_h=40, strip_h=200, icons=(), bars=(), chevron=None):
    """造一条带**双向渐变**的合成任务栏（BGRA），返回 (bits, w, strip_h, top)。

    为什么背景要做成双向渐变：这是本机实测的真实形态 —— 横向因为壁纸/亚克力透出来
    左亮右暗（实测左 (80,38,22)、右 (43,35,31)，差 40+ 级），竖向越往下越亮。
    旧模型「区域一个背景色 + 色差阈值」在这种背景上会把左半条整片判成前景
    （实测 100% 像素「像前景」，图标一个都切不出来）。

    横向刻意做成**分段平色**（左 35% 一块、右边一块）而不是连续渐变：真实任务栏就是
    这样（壁纸透出来是一整块，不是平滑斜坡），而且只有平色区占比 ≥45% 时「逐行众数色」
    才能成立 —— 连续渐变会让每行众数色的覆盖率掉到 30% 以下，通栏根本检不出来。

    icons: [(x0, x1, (r,g,b), 高)] —— 高度填 0 时按 band_h 的 60% 居中放。
    bars:  [(x0, x1)] —— 图标下方那条「已启动指示条」（厚 3px，贴条带底部）。
    chevron: (x0, x1) —— 「↑」那种又小又扁的字形（实测 12×7）。
    """
    buf = bytearray(bytes((150, 150, 150, 255)) * (w * strip_h))   # 壁纸（与任务栏色差极大）
    top = strip_h - band_h
    split = int(w * 0.35)
    for y in range(top, strip_h):
        v = int(10 * (y - top) / max(1, band_h - 1))   # 竖向渐变
        for x in range(w):
            if x < split:                              # 左区：壁纸透出来，明显更亮
                r, g, b = 78, 46, 24
            else:
                r, g, b = 44, 34, 29
            o = (y * w + x) * 4
            buf[o:o + 4] = bytes((b + v, g + v, r + v, 255))
    for x0, x1, col, ih in icons:
        ih = ih or int(band_h * 0.6)
        y0 = top + (band_h - ih) // 2
        _paint_rect(buf, w, x0, y0, x1, y0 + ih - 1, col)
    for x0, x1 in bars:
        _paint_rect(buf, w, x0, strip_h - 4, x1, strip_h - 2, (60, 160, 240))
    if chevron:
        x0, x1 = chevron
        y0 = top + band_h // 2 - 3
        _paint_rect(buf, w, x0, y0, x1, y0 + 6, (190, 190, 190))
    return bytes(buf), w, strip_h, top


def _use_taskbar(mod, monkeypatch, bits, w, strip_h):
    monkeypatch.setattr(mod, "_virtual_screen", lambda: (0, 0, w, 1080))
    monkeypatch.setattr(
        mod, "_grab_rect_bits",
        lambda x0, y0, ww, hh: (bytes(bits), x0, y0, ww, hh))


def test_taskbar_band_detects_the_strip_and_its_height(monkeypatch):
    """底部通栏要能被切出来，高度、原点都要对。"""
    mod = _load_module()
    bits, w, sh, top = _synth_taskbar()
    _use_taskbar(mod, monkeypatch, bits, w, sh)
    scan = mod._taskbar_scan()
    assert scan["ok"], scan
    assert scan["top"] == top and scan["bottom"] == sh - 1
    assert scan["gy"] + scan["top"] == 1080 - sh + top, "通栏的屏幕 y 要落在条带顶部"
    assert scan["gw"] == w


def test_taskbar_band_rejects_a_uniform_screen(monkeypatch):
    """整屏一个颜色时**不能**硬认成任务栏（那会把整个桌面当图标条去点）。"""
    mod = _load_module()
    buf = bytearray(bytes((40, 40, 40, 255)) * (800 * 200))
    monkeypatch.setattr(mod, "_virtual_screen", lambda: (0, 0, 800, 1080))
    monkeypatch.setattr(mod, "_grab_rect_bits",
                        lambda x0, y0, ww, hh: (bytes(buf), x0, y0, ww, hh))
    scan = mod._taskbar_scan()
    assert not scan["ok"] and scan["reason"] == "no_band"


def test_band_base_recovers_a_horizontal_gradient(monkeypatch):
    """逐列背景必须跟着横向渐变走 —— 否则左半条整片会被当成前景。"""
    mod = _load_module()
    bits, w, sh, top = _synth_taskbar()
    band = bits[top * w * 4:]
    base = mod._band_base_cols(band, w, sh - top)
    assert base[5][2] - base[w - 6][2] >= 20, "左侧背景应明显亮于右侧（R 通道差 20+）"
    assert abs(base[5][2] - 78) <= 4 and abs(base[w - 6][2] - 44) <= 4


def test_band_base_survives_a_polluted_top_row(monkeypatch):
    """顶行被图标/窗口边缘压到时，中值滤波要把那几列的背景色救回来。"""
    mod = _load_module()
    bits, w, sh, top = _synth_taskbar()
    buf = bytearray(bits)
    _paint_rect(buf, w, 500, top, 501, top + 1, (255, 0, 255))   # 两列被污染
    base = mod._band_base_cols(bytes(buf), w, sh - top)
    ref = base[480]
    for x in (500, 501):
        assert abs(base[x][0] - ref[0]) + abs(base[x][1] - ref[1]) \
            + abs(base[x][2] - ref[2]) <= 12, f"x={x} 的背景色被图标污染了：{base[x]} vs {ref}"


def test_band_runs_splits_on_real_gaps_only():
    """图标之间 3px 以上的缝才切开；字形内部 1~2px 的缝不能切（否则一个字裂成两块）。"""
    mod = _load_module()
    assert mod.TASKBAR_SPLIT_GAP == 3
    act = [0] * 5 + [1] * 10 + [0] * 2 + [1] * 10 + [0] * 5
    assert mod._band_runs(act, len(act)) == [(5, 26)], "2px 内缝不该切开"
    act2 = [0] * 5 + [1] * 10 + [0] * 3 + [1] * 10
    assert mod._band_runs(act2, len(act2)) == [(5, 14), (18, 27)], "3px 缝必须切开"


def test_taskbar_slots_do_not_merge_adjacent_icons(monkeypatch):
    """相邻的两个任务栏按钮**绝不能**并成一个槽位。

    这是「点了没反应」最典型的成因：落点落在两个图标之间的缝上。旧实现用
    「连通域 + 12px 间隙合并」，而 Win11 相邻图标之间只有 6~8px 缝 —— 必然并错。
    """
    mod = _load_module()
    bits, w, sh, top = _synth_taskbar(
        icons=[(300, 322, (240, 90, 60), 0), (330, 352, (70, 200, 120), 0)])
    _use_taskbar(mod, monkeypatch, bits, w, sh)
    slots = mod._taskbar_scan()["slots"]
    assert len(slots) == 2, f"两个图标应给出两个槽位，实得 {[(s['x'], s['w']) for s in slots]}"
    assert slots[0]["x"] == 311 and slots[1]["x"] == 341
    assert all(s["w"] <= 26 for s in slots), "槽位宽度不能横跨两个图标"


def test_taskbar_marks_running_apps_by_the_indicator_bar(monkeypatch):
    """图标下方那条小横条 = 该应用正在运行（点它等于切窗口，不会重开一个）。"""
    mod = _load_module()
    bits, w, sh, top = _synth_taskbar(
        icons=[(300, 322, (240, 90, 60), 0), (330, 352, (70, 200, 120), 0)],
        bars=[(305, 318)])
    _use_taskbar(mod, monkeypatch, bits, w, sh)
    slots = mod._taskbar_scan()["slots"]
    assert slots[0]["running"] is True, "第一个图标下方有指示条 → 正在运行"
    assert slots[1]["running"] is False, "第二个没有指示条"


def test_taskbar_keeps_the_small_chevron(monkeypatch):
    """「↑ 隐藏图标」只有 12×7，不能被最小尺寸过滤掉（否则面板流程找不到入口）。"""
    mod = _load_module()
    assert mod.TASKBAR_SLOT_MIN_H <= 7, "最小高度必须 ≤7，否则「↑」会被丢掉"
    bits, w, sh, top = _synth_taskbar(
        icons=[(300, 322, (240, 90, 60), 0)], chevron=(1100, 1111))
    _use_taskbar(mod, monkeypatch, bits, w, sh)
    slots = mod._taskbar_scan()["slots"]
    chev = [s for s in slots if s["x"] >= 1000]
    assert len(chev) == 1, f"应识别出「↑」，实得 {[(s['x'], s['w'], s['h']) for s in slots]}"
    assert chev[0]["h"] == 7 and chev[0]["w"] == 12
    # 面板流程按「右侧托盘区里最左的小图标」认「↑」，必须正好落到它身上
    right = [s for s in slots
             if s["x"] >= w - mod.TASKBAR_RIGHT_ZONE and s["w"] <= mod.TASKBAR_CHEVRON_MAX_W]
    assert right and right[0]["x"] == 1106


def test_taskbar_zone_partitions_the_bar():
    """左/中/右三个区要按几何切干净（模型据此避免点到输入法/时钟）。"""
    mod = _load_module()
    assert "最左区" in mod._taskbar_zone(100, 0, 1920)
    assert mod._taskbar_zone(900, 0, 1920) == "应用区"
    assert "托盘区" in mod._taskbar_zone(1800, 0, 1920)


def test_taskbar_slot_lines_are_clickable_and_flag_running():
    """槽位文字要能直接照抄成 click 调用，并且标明「已启动」。"""
    mod = _load_module()
    slots = [{"i": 1, "x": 311, "y": 1052, "w": 23, "h": 24, "box": (300, 1040, 322, 1063),
              "running": True},
             {"i": 2, "x": 341, "y": 1052, "w": 23, "h": 24, "box": (330, 1040, 352, 1063),
              "running": False}]
    lines = mod._taskbar_slot_lines(slots, 0, 1200, pick=2)
    assert "1. 中心(311,1052)" in lines[0] and "23×24" in lines[0]
    assert "已启动" in lines[0] and "已启动" not in lines[1]
    assert "本次点击" in lines[1]


def test_taskbar_images_carry_no_markers(monkeypatch):
    """任务栏/面板返给模型的图必须是纯净原图：不画编号框、不画准星、无任何标记。

    槽位信息全部走文字表（编号/中心坐标/尺寸），图上不叠加任何东西 —— 标记会盖住
    图标本身，且框线在缩放的条带图上反而干扰认图。
    """
    mod = _load_module()
    seen = []

    def _capture(src_dc, sx, sy, sw, sh, tw, cells=0, cursor_pt=None,
                 smooth=True, marks=None, level=6, boxes=None):
        seen.append({"boxes": boxes, "marks": marks, "cells": cells,
                     "cursor": cursor_pt})
        return b"PNG", 900, 40

    monkeypatch.setattr(mod, "_capture_scaled", _capture)
    mod._STATE["interrupted"] = mod._STATE["paused"] = False
    monkeypatch.setattr(mod, "_guard", lambda what: "")
    monkeypatch.setattr(mod, "_refresh_env", lambda: None)
    monkeypatch.setattr(mod, "_log", lambda who, text: None)
    monkeypatch.setattr(mod.user32, "GetDC", lambda h: 1)
    monkeypatch.setattr(mod.user32, "ReleaseDC", lambda h, d: 1)
    bits, w, sh, top = _synth_taskbar(
        icons=[(300, 322, (240, 90, 60), 0), (330, 352, (70, 200, 120), 0)])
    _use_taskbar(mod, monkeypatch, bits, w, sh)
    out = mod.tool_taskbar_open({})
    assert out[1]["type"] == "image"
    assert seen and all(not s["boxes"] and not s["marks"] and not s["cells"]
                        and s["cursor"] is None for s in seen), \
        f"任务栏图必须纯净，实得叠加参数 {seen}"
    assert "无任何标记" in out[0]["text"]


def test_taskbar_open_reports_bad_index_and_switches_foreground(monkeypatch):
    """按编号点击：编号越界要如实说；点完要回读落点并核对前台窗口。"""
    mod = _load_module()
    mod._STATE["interrupted"] = mod._STATE["paused"] = False
    bits, w, sh, top = _synth_taskbar(
        icons=[(300, 322, (240, 90, 60), 0), (330, 352, (70, 200, 120), 0)])
    _use_taskbar(mod, monkeypatch, bits, w, sh)
    monkeypatch.setattr(mod, "_guard", lambda what: "")
    monkeypatch.setattr(mod, "_refresh_env", lambda: None)
    monkeypatch.setattr(mod, "_log", lambda who, text: None)
    monkeypatch.setattr(mod.user32, "GetDC", lambda h: 1)
    monkeypatch.setattr(mod.user32, "ReleaseDC", lambda h, d: 1)
    monkeypatch.setattr(mod, "_capture_scaled", lambda *a, **k: (b"PNG", 900, 40))

    out = mod.tool_taskbar_open({})
    assert "个图标槽位" in out[0]["text"] and out[1]["type"] == "image"

    bad = mod.tool_taskbar_open({"index": 99})
    assert "没有编号 99" in bad[0]["text"] and len(bad) == 1

    clicked = []
    fg = {"h": 1}

    def _click(x, y, b, c, mode="mouse"):
        clicked.append((x, y))
        fg["h"] += 1          # 模拟「点了任务栏图标 → 前台窗口换成那个应用」
        return True

    monkeypatch.setattr(mod, "do_click", _click)
    # 条带 200 高、通栏在其最后 40 行 → 通栏顶 y = 1080-200+160 = 1040；
    # 图标 24 高居中放（局部行 8~31），中位 y=20 → 屏幕 y=1060。
    monkeypatch.setattr(mod, "_cursor_pos", lambda: (341, 1060))
    monkeypatch.setattr(mod, "foreground_window", lambda: fg["h"])
    monkeypatch.setattr(mod, "window_title", lambda h: f"win{h}")
    out2 = mod.tool_taskbar_open({"index": 2})
    assert clicked == [(341, 1060)], f"应点第二个图标的中心，实得 {clicked}"
    assert "落点回读" in out2[0]["text"] and "0 偏差" in out2[0]["text"]
    assert "前台窗口已切换" in out2[0]["text"], "切窗口成功必须明确回报"


def test_taskbar_click_warns_when_foreground_does_not_change(monkeypatch):
    """点了但前台窗口没变 → 必须警告「别盲目重复点」，而不是报成功。"""
    mod = _load_module()
    mod._STATE["interrupted"] = mod._STATE["paused"] = False
    monkeypatch.setattr(mod, "do_click", lambda x, y, b, c, mode="mouse": True)
    monkeypatch.setattr(mod, "_cursor_pos", lambda: (500, 500))
    monkeypatch.setattr(mod, "foreground_window", lambda: 7)
    monkeypatch.setattr(mod, "window_title", lambda h: "微信")
    note = mod._taskbar_click(500, 500, "任务栏第 3 个槽位", wait_ms=60)
    assert "仍未变化" in note
    assert "不要重复点同一个图标" in note, "必须明确劝阻重复点击"
    assert "timeout=3000" in note, "要告诉模型怎么等更久"


def test_taskbar_click_polls_until_the_foreground_changes(monkeypatch):
    """点击后必须**轮询**等前台切换，而不是睡死一段再只看一眼。

    任务栏点击有两类：切到已在运行的应用（几十毫秒）与启动未运行的应用（1~3 秒）。
    固定 sleep 250ms 再看一眼，慢启动的必定被判「没反应」，模型于是反复点同一个图标，
    整体耗时越积越长 —— 用户看到的就是「taskbar_open 超时」。
    """
    import time as _t
    mod = _load_module()
    calls = {"n": 0}

    def _fg():
        calls["n"] += 1
        return 7 if calls["n"] <= 2 else 99      # 第 3 次查询才切过去

    monkeypatch.setattr(mod, "do_click", lambda x, y, b, c, mode="mouse": True)
    monkeypatch.setattr(mod, "_cursor_pos", lambda: (500, 500))
    monkeypatch.setattr(mod, "foreground_window", _fg)
    monkeypatch.setattr(mod, "window_title", lambda h: f"win{h}")
    t0 = _t.perf_counter()
    note = mod._taskbar_click(500, 500, "槽位", wait_ms=3000)
    dt = _t.perf_counter() - t0
    assert "前台窗口已切换" in note
    assert 99 in [calls["n"]] or calls["n"] >= 3
    assert dt < 1.0, f"一旦切换就该立刻返回，不该睡满 3s（实际 {dt:.2f}s）"


def test_change_bbox_skips_identical_rows_quickly(monkeypatch):
    """未变化的整行必须被跳过（C 层 bytes 比较），否则面板没弹出时要白扫几十万像素。"""
    import time as _t
    mod = _load_module()
    w, h = 380, 480
    a = bytes(w * h * 4)                       # 全 0
    b = bytearray(a)
    # 只改中间一小块（模拟面板）
    for y in range(200, 260):
        off = (y * w + 100) * 4
        b[off:off + 40 * 4] = b"\xff" * (40 * 4)
    t0 = _t.perf_counter()
    bb, changed = mod._change_bbox(a, bytes(b), w, h)
    dt = _t.perf_counter() - t0
    assert bb is not None and changed > 0
    assert bb[0] <= 100 and bb[1] <= 200
    assert dt < 0.25, f"逐行跳过没生效（{dt:.3f}s）"
    # 两份完全相同 → 不花时间
    t0 = _t.perf_counter()
    assert mod._change_bbox(a, a, w, h) == (None, 0)
    assert _t.perf_counter() - t0 < 0.1


def test_merge_parts_still_merges_inside_a_panel():
    """面板（网格）里图标碎片仍要按间隙合并 —— 这条路径不能被任务栏改动带走。"""
    mod = _load_module()
    parts = [{"minx": 10, "maxx": 30, "miny": 5, "maxy": 25, "area": 200},
             {"minx": 33, "maxx": 50, "miny": 5, "maxy": 25, "area": 150},
             {"minx": 120, "maxx": 140, "miny": 5, "maxy": 25, "area": 180}]
    merged = mod._merge_parts(parts, 300, 40)
    assert len(merged) == 2, f"3px 缝应合并、70px 缝应分开，实得 {merged}"
    assert (merged[0]["minx"], merged[0]["maxx"]) == (10, 50)


def test_taskbar_open_is_registered_and_wired():
    """工具必须出现在工具表与分发表里（否则模型看不到 / 调用不了）。"""
    mod = _load_module()
    names = [t["name"] for t in mod.TOOLS]
    assert "taskbar_open" in names
    assert "taskbar_open" in mod._HANDLERS
    spec = next(t for t in mod.TOOLS if t["name"] == "taskbar_open")
    props = spec["inputSchema"]["properties"]
    assert set(props) >= {"where", "index", "chevron", "name", "timeout"}


def test_click_refuses_to_press_when_the_cursor_is_stolen(monkeypatch):
    """真实鼠标抢走光标时，宁可**不点**，也不能按在错误的位置上。

    真机实测过这一幕：请求点任务栏 (1683,1055)，回读却是 (1231,510) —— 用户的手正在
    移动鼠标，SendInput 的绝对定位被立刻覆盖，「按下」落在了完全不同的窗口上。
    这类「点偏」坐标算得再准也救不回来，只能在按键前回读确认、对不上就不按。
    """
    mod = _load_module()
    mod._STATE["interrupted"] = mod._STATE["paused"] = False
    sent = []
    monkeypatch.setattr(mod, "_move_human", lambda x, y, duration=0: None)
    monkeypatch.setattr(mod, "_move_absolute", lambda x, y: None)      # 注入被实时覆盖
    monkeypatch.setattr(mod, "_cursor_pos", lambda: (1231, 510))
    monkeypatch.setattr(mod, "_send_mouse",
                        lambda f, dx=0, dy=0, data=0: sent.append(f))
    monkeypatch.setattr(mod, "_log", lambda who, text: None)
    assert mod.do_click(1683, 1055) is False
    assert sent == [], "落点确认不到时绝不能按下鼠标（可能误触删除/发送/关闭）"


def test_click_tool_tells_the_model_it_did_not_click(monkeypatch):
    """拒绝点击时必须**明确告诉模型**，否则它会以为点过了继续往下走。"""
    mod = _load_module()
    mod._STATE["interrupted"] = mod._STATE["paused"] = False
    monkeypatch.setattr(mod, "_resolve_point", lambda a, *k: (500, 400))
    monkeypatch.setattr(mod, "_apply_snap", lambda a, x, y, default_on=True: (x, y, ""))
    monkeypatch.setattr(mod, "_ensure_click_target", lambda x, y, a: "")
    monkeypatch.setattr(mod, "_move_human", lambda x, y, duration=0: None)
    monkeypatch.setattr(mod, "_move_absolute", lambda x, y: None)
    monkeypatch.setattr(mod, "_cursor_pos", lambda: (864, 498))
    monkeypatch.setattr(mod, "_send_mouse", lambda f, dx=0, dy=0, data=0: None)
    monkeypatch.setattr(mod, "_log", lambda who, text: None)
    monkeypatch.setattr(mod, "_refresh_env", lambda: None)
    out = mod._HANDLERS["click"]({"x": 500, "y": 400})
    assert ("没有完成" in out or "动作没有执行" in out)
    assert "真实鼠标" in out, "必须告诉模型『可能是真实鼠标』这类可执行线索"
    assert "不要重复重试" in out, "必须劝阻盲目重试，否则就是新一轮死循环"


def test_settle_at_retries_once_then_succeeds(monkeypatch):
    """被抢走一次后重试能落位 → 正常点击（不能因为一次抖动就放弃）。"""
    mod = _load_module()
    pos, n = {"x": 0, "y": 0}, {"i": 0}

    def _move(x, y):
        n["i"] += 1
        if n["i"] > 1:                     # 第一次被覆盖，第二次生效
            pos["x"], pos["y"] = int(x), int(y)

    monkeypatch.setattr(mod, "_move_absolute", _move)
    monkeypatch.setattr(mod, "_cursor_pos", lambda: (pos["x"], pos["y"]))
    assert mod._settle_at(700, 900) is True
    assert n["i"] == 2, "应重试一次后成功"


def test_long_press_and_drag_also_confirm_before_pressing(monkeypatch):
    """长按/拖拽的按下点同样要确认 —— 按住后漂移会被识别成拖拽，更糟。"""
    mod = _load_module()
    sent = []
    monkeypatch.setattr(mod, "_move_human", lambda x, y, duration=0: None)
    monkeypatch.setattr(mod, "_move_absolute", lambda x, y: None)
    monkeypatch.setattr(mod, "_cursor_pos", lambda: (0, 0))
    monkeypatch.setattr(mod, "_send_mouse", lambda f, dx=0, dy=0, data=0: sent.append(f))
    monkeypatch.setattr(mod, "_log", lambda who, text: None)
    assert mod.do_long_press(500, 500, 0.25) is False
    assert mod.do_drag(100, 100, 400, 300, duration=0.2) is False
    assert sent == [], "起点确认不到时不能按下"


# ---------- 高级鼠标动作 / 回车发送 / 吸附增强 / 随包插件升级 ----------

def test_click_action_maps_advanced_mouse_gestures():
    """click 是统一入口：action 必须能映射到拖拽/滑动/长按/拖选/双击/右键。

    模型最容易犯的错不是「不会拖」，而是「忘了还有 drag 工具」于是拆成点击+移动+点击。
    统一入口后，这些名字都要能认出来。
    """
    mod = _load_module()
    assert mod._click_action({}) == "click"
    assert mod._click_action({"action": "double"}) == "double"
    assert mod._click_action({"action": "double_click"}) == "double"
    assert mod._click_action({"action": "right"}) == "right"
    assert mod._click_action({"action": "long-press"}) == "long_press"
    assert mod._click_action({"action": "drag"}) == "drag"
    assert mod._click_action({"action": "swipe"}) == "swipe"
    assert mod._click_action({"action": "select"}) == "select"
    assert mod._click_action({"action": "no_such_action"}) == "click"


def test_click_drag_reuses_start_point_and_swipe_stays_drag_semantics():
    """action=drag 允许只传 x/y 起点 + x2/y2 终点，不用记两套参数名。"""
    mod = _load_module()
    got = mod._as_drag_args({"x": 10, "y": 20, "x2": 30, "y2": 40})
    assert (got["x1"], got["y1"], got["x2"], got["y2"]) == (10, 20, 30, 40)


def test_type_text_submit_presses_enter_after_typing(monkeypatch):
    """type_text(submit=true) 输入完直接回车发送，不再让模型去点发送按钮。"""
    mod = _load_module()
    monkeypatch.setattr(mod, "_guard", lambda name: "")
    monkeypatch.setattr(mod, "_ensure_input_target", lambda args, action: "")
    monkeypatch.setattr(mod, "_input_target_hwnd", lambda args: 42)
    monkeypatch.setattr(mod, "foreground_window", lambda: 42)
    monkeypatch.setattr(mod, "window_title", lambda h: "微信")
    monkeypatch.setattr(mod, "_log", lambda who, text: None)
    monkeypatch.setattr(mod, "_refresh_env", lambda: None)
    note = "键盘焦点已在可输入控件（类名 Edit）—— 直接 type_text 输入即可，**不要再点击输入框**。"
    monkeypatch.setattr(mod, "focus_state", lambda hwnd=0: {
        "ok": True, "focus": 7, "cls": "Edit", "caret": True,
        "editable": True, "note": note})
    typed, keys = [], []
    monkeypatch.setattr(mod, "do_type_text", lambda t, interval=0.012: typed.append(t))
    monkeypatch.setattr(mod, "do_key", lambda k: keys.append(k))
    out = mod._HANDLERS["type_text"]({"text": "你好", "submit": True})
    assert typed == ["你好"], "文本必须先完整输入"
    assert keys == ["enter"], "submit=true 时必须补一个 Enter"
    assert "Enter" in out and "发送" in out
    assert "不要再点击输入框" in out, "焦点已在输入框时必须明确告诉模型直接输入"


def test_type_text_without_submit_never_presses_enter(monkeypatch):
    """默认不发送：只输入文本时绝不能被回车误发出去。"""
    mod = _load_module()
    monkeypatch.setattr(mod, "_guard", lambda name: "")
    monkeypatch.setattr(mod, "_ensure_input_target", lambda args, action: "")
    monkeypatch.setattr(mod, "_input_target_hwnd", lambda args: 42)
    monkeypatch.setattr(mod, "foreground_window", lambda: 42)
    monkeypatch.setattr(mod, "window_title", lambda h: "记事本")
    monkeypatch.setattr(mod, "_log", lambda who, text: None)
    monkeypatch.setattr(mod, "_refresh_env", lambda: None)
    monkeypatch.setattr(mod, "focus_state", lambda hwnd=0: {
        "ok": False, "focus": 0, "cls": "", "caret": False,
        "editable": False, "note": ""})
    monkeypatch.setattr(mod, "do_type_text", lambda t, interval=0.012: None)
    keys = []
    monkeypatch.setattr(mod, "do_key", lambda k: keys.append(k))
    mod._HANDLERS["type_text"]({"text": "草稿"})
    assert keys == [], "没传 submit 时不得发送"


def test_focus_detection_recognises_editable_controls():
    """焦点判定只看类名/插入点，用于决定「要不要再点输入框」；判错不影响注入。"""
    mod = _load_module()
    assert mod._editable_class("Edit")
    assert mod._editable_class("RichEdit50W")
    assert mod._editable_class("Chrome_RenderWidgetHostHWND")
    assert mod._editable_class("Windows.UI.Core.CoreWindow")
    assert not mod._editable_class("Shell_TrayWnd")
    assert not mod._editable_class("")
    assert not mod._editable_class("Button")


def test_merge_components_joins_fragments_of_one_target():
    """被高光切开的同一控件碎片要并回一整块，否则中心会落在半块上。"""
    mod = _load_module()
    a = {"area": 60, "minx": 100, "miny": 200, "maxx": 109, "maxy": 205}
    b = {"area": 50, "minx": 111, "miny": 200, "maxx": 119, "maxy": 205}
    far = {"area": 40, "minx": 200, "miny": 200, "maxx": 209, "maxy": 205}
    out = mod._merge_components([a, b, far])
    assert len(out) == 2, "相邻碎片合并，远离的不合并"
    merged = min(out, key=lambda c: c["minx"])
    assert (merged["minx"], merged["maxx"]) == (100, 119)
    assert merged["area"] == 110


def test_snap_core_widens_search_when_default_reach_misses(monkeypatch):
    """默认半径内没找到元素时自动扩大一次，而不是直接放弃吸附。"""
    mod = _load_module()
    seen = []

    def _fake(x, y, reach=88, max_shift=None, cands=None):
        seen.append(reach)
        if reach >= 40:
            return 105, 106, "扩大后命中", ""
        return int(x), int(y), "", ""

    monkeypatch.setattr(mod, "_snap_region", _fake)
    sx, sy, note, _ruler = mod._snap_core(100, 100, 16)
    assert (sx, sy) == (105, 106)
    assert seen == [16, 40]
    assert "扩大" in note


def test_shipped_plugin_update_replaces_old_version_and_drops_obsolete_files(
        tmp_path, monkeypatch):
    """随包插件升级：revision 变大就覆盖同步，并删除 obsolete_files 里的历史文件。

    真实踩过的坑：ensure_shipped_plugins 以前「目录已存在就跳过」，导致用户运行目录
    永远停在旧版本（仍在用已删掉的 uia.py 元素工具）。
    """
    shipped, installed = tmp_path / "shipped", tmp_path / "installed"
    src, dst = shipped / "demo", installed / "demo"
    src.mkdir(parents=True)
    dst.mkdir(parents=True)
    (src / "plugin.json").write_text(
        json.dumps({"name": "demo", "revision": 2, "obsolete_files": ["uia.py"]}),
        encoding="utf-8")
    (src / "server.py").write_text("NEW", encoding="utf-8")
    (dst / "plugin.json").write_text(json.dumps({"name": "demo", "revision": 1}),
                                     encoding="utf-8")
    (dst / "server.py").write_text("OLD", encoding="utf-8")
    (dst / "uia.py").write_text("legacy", encoding="utf-8")
    (dst / "user_notes.txt").write_text("keep", encoding="utf-8")
    cache = dst / "__pycache__"
    cache.mkdir()
    (cache / "uia.pyc").write_bytes(b"x")
    monkeypatch.setattr(agent_plugins, "plugins_dir", lambda: installed)
    monkeypatch.setattr(agent_plugins, "_shipped_plugins_dir", lambda: shipped)
    monkeypatch.setattr(agent_plugins, "_register_shipped_plugin", lambda name: None)
    monkeypatch.setattr(agent_plugins, "invalidate_index", lambda: None)
    agent_plugins.ensure_shipped_plugins()
    assert (dst / "server.py").read_text(encoding="utf-8") == "NEW"
    assert not (dst / "uia.py").exists(), "obsolete_files 必须被删除"
    assert not cache.exists(), "旧字节码缓存必须清掉"
    assert (dst / "user_notes.txt").read_text(encoding="utf-8") == "keep", \
        "用户自己加的文件不能被覆盖删除"
    assert json.loads((dst / "plugin.json").read_text(encoding="utf-8"))["revision"] == 2
