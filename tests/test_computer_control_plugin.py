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

# 元素级识别与窗口/应用管理工具（UIA 增强后新增，属必备能力）
ELEMENT_TOOLS = {"list_elements", "click_element", "type_element",
                 "scroll_element", "drag_element", "manage_window", "aura"}


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
    # REQUIRED_TOOLS 是「必须具备」的集合；增强会新增工具，故断言为超集而非等集
    assert REQUIRED_TOOLS <= set(data["tools"]), \
        f"缺少必需工具: {sorted(REQUIRED_TOOLS - set(data['tools']))}"
    assert ELEMENT_TOOLS <= set(data["tools"]), \
        f"缺少元素级/窗口管理工具: {sorted(ELEMENT_TOOLS - set(data['tools']))}"
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

# ============================================================================
# 元素级识别（uia.py）与屏幕光环（aura.py）
# ============================================================================

UIA = PLUGIN_DIR / "uia.py"
AURA = PLUGIN_DIR / "aura.py"


def _load_sibling(name: str, filename: str):
    """加载插件目录下的兄弟模块（uia.py / aura.py）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, PLUGIN_DIR / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_uia_and_aura_modules_exist_and_compile():
    """新增的两个零依赖模块必须随包存在且语法正确"""
    for path in (UIA, AURA):
        assert path.is_file(), f"缺少 {path.name}"
    for path in (SERVER, UIA, AURA):
        proc = subprocess.run([sys.executable, "-m", "py_compile", str(path)],
                              capture_output=True, check=False, timeout=60)
        assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")


def test_uia_degrades_gracefully_without_com():
    """UIA 不可用时必须降级而不是抛异常（元素工具据此回退到坐标操控）"""
    uia = _load_sibling("cc_uia", "uia.py")
    # 强制当前线程走「已尝试且失败」分支
    uia._tls.tried = True
    uia._tls.auto = None
    assert uia.available() is False
    assert "不可用" in uia.capability()
    # 降级后仍要能给出元素对象（只是没有 UIA 数据），且可安全序列化
    el = uia.describe_hwnd(0)
    assert el.hwnd == 0 and el.via_uia is False
    assert el.to_dict()["type"]
    assert isinstance(el.describe(), str)
    # 无效句柄不得抛异常
    assert uia.describe_hwnd(0xDEADBEEF).hwnd in (0, 0xDEADBEEF)
    assert uia.list_elements(0) == []


def test_uia_automation_is_thread_local():
    """COM 对象是套间绑定的：跨线程复用 IUIAutomation 会段错误。

    因此每个线程必须各持一份实例（/api/state 在 HTTP 工作线程里也会用到它）。
    """
    import threading
    uia = _load_sibling("cc_uia", "uia.py")
    main_ptr = uia.automation()
    seen = {}

    def _worker():
        # 新线程应拿到**自己**的实例，而不是主线程那个
        seen["ptr"] = uia.automation()
        seen["thread"] = threading.current_thread().ident

    t = threading.Thread(target=_worker)
    t.start()
    t.join()
    if main_ptr is None:
        pytest.skip("UIA 在当前环境不可用")
    assert seen["ptr"] is not None, "工作线程应能独立创建自己的 UIA 实例"
    assert seen["ptr"] != main_ptr, "不得跨线程复用同一 COM 接口指针"
    # 主线程实例不受影响
    assert uia.automation() == main_ptr


def test_uia_element_capability_flags():
    """元素能力标记：可点击/可输入/可滚动/可拖拽的分类必须正确"""
    uia = _load_sibling("cc_uia", "uia.py")
    mk = uia.Element
    button = mk(1, "确定", 50000, (0, 0, 100, 40), True, False, False)
    edit = mk(2, "输入框", 50004, (0, 0, 200, 30), True, False, False)
    slider = mk(3, "音量", 50015, (0, 0, 100, 20), True, False, False)
    listing = mk(4, "文件列表", 50008, (0, 0, 300, 400), True, False, False)
    disabled = mk(5, "灰按钮", 50000, (0, 0, 80, 30), False, False, False)
    offscreen = mk(6, "屏外按钮", 50000, (0, 0, 80, 30), True, False, True)

    assert button.clickable and not button.editable
    assert edit.editable
    assert slider.scrollable and slider.draggable
    assert listing.scrollable
    # 禁用 / 屏外的元素不算可点击（避免点到无效目标）
    assert not disabled.clickable
    assert not offscreen.clickable
    # 几何派生属性
    assert button.center == (50, 20)
    assert (button.width, button.height) == (100, 40)
    assert "可点击" in button.describe()
    assert "禁用" in disabled.describe()


def test_uia_find_elements_filters():
    """find_elements 的 name/type/only 过滤必须生效"""
    uia = _load_sibling("cc_uia", "uia.py")
    items = [uia.Element(1, "安装", 50000, (0, 0, 50, 20)),
             uia.Element(2, "关闭", 50000, (0, 0, 50, 20)),
             uia.Element(3, "文本编辑器", 50004, (0, 0, 200, 30)),
             uia.Element(4, "音量", 50015, (0, 0, 100, 20))]
    uia.list_elements = lambda hwnd, include_offscreen=False, max_count=300: list(items)
    assert [e.label for e in uia.find_elements(0, name="安装")] == ["安装"]
    assert [e.label for e in uia.find_elements(0, type_name="Edit")] == ["文本编辑器"]
    assert {e.label for e in uia.find_elements(0, only="clickable")} == {"安装", "关闭"}
    assert [e.label for e in uia.find_elements(0, only="editable")] == ["文本编辑器"]
    assert [e.label for e in uia.find_elements(0, only="draggable")] == ["音量"]
    # name 大小写不敏感 + 子串匹配
    assert uia.find_elements(0, name="编辑")
    # 无匹配返回空列表（不抛异常）
    assert uia.find_elements(0, name="绝无此项") == []


def test_aura_module_importable_and_reports_capability():
    """光环模块可加载，并如实汇报能力（是否可用）"""
    aura = _load_sibling("cc_aura", "aura.py")
    st = aura.aura_state()
    assert set(st) == {"active", "failed", "capability"}
    assert "光环" in st["capability"]
    # 颜色工具函数：HSL→RGB 必须落在 0..255 且亮度符合预期
    for hue in (0.0, 0.25, 0.5, 0.75, 0.99):
        r, g, b = aura._hsl_to_rgb(hue, 0.85, 0.7)
        assert all(0 <= v <= 255 for v in (r, g, b))
    # 深色端应比浅色端暗（保证在浅色壁纸上也能看见）
    dark = sum(aura._hsl_to_rgb(0.3, 0.85, 0.25))
    light = sum(aura._hsl_to_rgb(0.3, 0.85, 0.9))
    assert dark < light


def test_aura_pixels_cover_all_four_edges():
    """光环像素必须覆盖完整四边（早先实现漏掉左右竖边，导致屏幕两侧无光环）"""
    aura = _load_sibling("cc_aura", "aura.py")
    a = aura.Aura()
    a._width, a._height = 1920, 1080
    pixels = a._build_pixels()
    offs = {off for off, _, _ in pixels}
    w, h, t = 1920, 1080, a.thickness
    # 四条边的中点都必须有像素
    assert (0 * w + 0) in offs                      # 左上角
    assert (540 * w + 0) in offs                    # 左边中点
    assert (540 * w + (w - 1)) in offs              # 右边中点
    assert ((h - 1) * w + 960) in offs               # 下边中点
    assert (0 * w + 960) in offs                     # 上边中点
    # 屏幕中心（内部）绝不能被点亮，否则会遮挡用户视线
    assert (540 * w + 960) not in offs
    # 粗细范围内全覆盖、无缺口
    for y in range(0, h, 137):
        for x in range(0, t):
            assert (y * w + x) in offs, f"左边 ({x},{y}) 缺像素"


def test_new_control_tools_registered():
    """新增的元素级/窗口管理/光环工具必须同时出现在 TOOLS 与 _HANDLERS"""
    mod = _load_module()
    declared = {t["name"] for t in mod.TOOLS}
    handled = set(mod._HANDLERS)
    assert declared == handled, f"未注册={declared - handled} 多余={handled - declared}"
    for name in ("list_elements", "click_element", "type_element",
                 "scroll_element", "drag_element", "manage_window", "aura"):
        assert name in declared, f"缺少工具 {name}"
        assert callable(mod._HANDLERS[name])


def test_control_tools_drive_aura_but_queries_do_not():
    """只有真正改变桌面的工具才点亮光环，查询类不应打扰用户"""
    mod = _load_module()
    assert "click" in mod._CONTROL_TOOLS
    assert "click_element" in mod._CONTROL_TOOLS
    assert "manage_window" in mod._CONTROL_TOOLS
    for query in ("screen_shot", "screen_info", "list_windows", "list_elements",
                  "zoom_in", "web_url", "aura"):
        assert query not in mod._CONTROL_TOOLS, f"{query} 不该点亮光环"


def test_manage_window_state_and_errors():
    """manage_window 的状态查询与参数校验（不真的改动窗口）"""
    mod = _load_module()
    text = mod._HANDLERS["manage_window"]({"action": "state"})
    assert "当前窗口列表" in text
    with pytest.raises(ValueError):
        mod._HANDLERS["manage_window"]({"action": "no_such_action"})


def test_element_tools_require_name_or_type(monkeypatch):
    """元素工具缺少定位参数时必须给出可读错误（而不是静默点错地方）

    这里把「元素识别可用」显式打开，使断言不依赖运行环境的 UIA 状态 ——
    参数校验逻辑与 UIA 是否可用无关，不该因为环境差异被跳过。
    """
    mod = _load_module()
    monkeypatch.setattr(mod, "_uia_ready", lambda: True)
    with pytest.raises(ValueError) as ei:
        mod._HANDLERS["click_element"]({})
    assert "name" in str(ei.value) and "type" in str(ei.value)
    # _pick_element 本身也应在缺参时报错（不需要 UIA）
    with pytest.raises(ValueError):
        mod._pick_element({})


def test_pick_element_relaxes_capability_filter():
    """能力标记只是偏好：筛不到时应退回按名称匹配，而不是直接罢工"""
    mod = _load_module()
    if mod.uia is None:
        pytest.skip("uia 未加载")
    calls = {"only": []}

    def _fake_find(hwnd, name="", type_name="", only="", include_offscreen=False,
                   max_count=300):
        calls["only"].append(only)
        if only:                      # 带能力过滤时返回空（模拟类型不在白名单）
            return []
        return [mod.uia.Element(1, "文本编辑器", 50004, (0, 0, 200, 30))]

    mod.uia.find_elements = _fake_find
    mod._target_hwnd = lambda args, required=True: 123
    el = mod._pick_element({"name": "文本编辑器"}, only="scrollable")
    assert el.label == "文本编辑器"
    assert calls["only"] == ["scrollable", ""], "应先按偏好筛，再放宽重试"


def test_drag_precision_settles_and_exact_lands(monkeypatch):
    """拖拽精度：两条注入路径都必须精确落在目标点

    - 触控路径：最后一帧触点必须在目标坐标
    - 鼠标回退路径：分步数不低于下限（步数太少会被系统当成瞬移而非拖拽）、
      末步精确落点、且顺序为「按下 → 移动 → 抬起」
    """
    mod, calls = _cc_fixture(_load_module(), monkeypatch, touch_ok=True)
    mod.do_drag(100, 100, 400, 300, duration=0.3, hold=0.15)
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


def test_scroll_element_moves_pointer_into_container(monkeypatch):
    """容器内滚动：先把指针移到容器中心再滚，命中该容器而非整屏"""
    mod, calls = _cc_fixture(_load_module(), monkeypatch, touch_ok=False)
    mod.do_scroll_at(300, 400, -240)
    assert (300, 400) in calls["move"], "滚动前应先把指针移进目标容器"
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
    monkeypatch.setattr(mod, "_move_absolute", lambda x, y: calls["move"].append((x, y)))
    monkeypatch.setattr(mod, "_move_human",
                        lambda x, y, duration=0: calls["move"].append((x, y)))
    return mod, calls
