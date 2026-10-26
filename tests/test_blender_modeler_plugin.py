"""blender-modeler 插件测试：元数据/技能规范、模板数据、MCP 握手与工具表、Blender 发现与输出目录，
以及（本机装有 Blender 时）真实起 Blender 建模 + 小尺寸渲染的集成冒烟。

不做 mock：集成用例真实运行 `server.py --selftest`（拉起 blender --background、建体块、
打光、取景、Cycles 渲染并落盘 PNG）；本机没有 Blender 时自动 skip，不伪造结果。"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

PLUGIN_DIR = (Path(__file__).resolve().parent.parent / "src" / "zhuzhu_Copilot"
              / "plugins" / "blender-modeler")
SERVER = PLUGIN_DIR / "server.py"
PRESETS = PLUGIN_DIR / "presets"

EXPECTED_TOOLS = {
    "blender_status", "blender_start", "blender_stop",
    "preview_start", "preview_stop",
    "scene_new", "scene_info", "scene_save", "scene_open",
    "object_add", "object_transform", "object_duplicate", "object_delete",
    "object_join", "object_shade", "object_modifier", "object_boolean",
    "material_create", "material_assign", "text_add",
    "camera_add", "camera_frame", "light_add", "light_rig", "world_set",
    "render_image", "export_model", "template_list", "template_apply",
}

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")

_MODULE = None


def _load_module():
    """以模块方式加载 server（模块级只有常量与 atexit 注册，无副作用）。"""
    global _MODULE
    if _MODULE is None:
        spec = importlib.util.spec_from_file_location("bm_plugin_server", SERVER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MODULE = mod
    return _MODULE


def _rpc(requests: list, timeout: int = 120, env: dict = None) -> list:
    """以 stdio 拉起插件 server，发一批请求，收齐响应后关 stdin（进程自然退出）。"""
    payload = "\n".join(json.dumps(r, ensure_ascii=False) for r in requests) + "\n"
    full_env = dict(os.environ)
    full_env.update(env or {})
    proc = subprocess.run([sys.executable, str(SERVER)], input=payload.encode("utf-8"),
                          capture_output=True, check=False, timeout=timeout, env=full_env)
    out = []
    for line in (proc.stdout or b"").decode("utf-8", "replace").splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _have_blender() -> bool:
    try:
        return bool(_load_module().find_blender())
    except Exception:
        return False


_needs_blender = pytest.mark.skipif(not _have_blender(), reason="本机未安装 Blender")


# ---------- 元数据与技能规范 ----------

def test_plugin_files_exist():
    for name in ("plugin.json", "SKILL.md", "server.py", "blender_host.py"):
        assert (PLUGIN_DIR / name).is_file(), f"缺少 {name}"
    for name in ("pet.json", "pcb.json", "lighting.json"):
        assert (PRESETS / name).is_file(), f"缺少 presets/{name}"


def test_plugin_metadata_binds_mcp_and_skill():
    meta = json.loads((PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8"))
    assert meta["name"] == "blender-modeler"
    assert meta["kind"] == "combined"
    assert meta["enabled"] is True
    assert meta["mcp_name"] == "blender-modeler-mcp"
    assert meta["skill_name"] == "blender-modeler"
    assert meta["description"]
    assert isinstance(meta.get("revision", 0), int)


def test_skill_md_frontmatter_and_workflow():
    text = (PLUGIN_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---")
    assert "name: blender-modeler" in text
    assert "description:" in text
    # 工作流与纪律必须写进技能：先看状态 → 建场景 → 套模板 → 改 → 布光 → 渲染看图 → 交付
    for key in ("blender_status", "scene_new", "template_apply", "light_rig",
                "render_image", "export_model", "preview_start", "不许伪造"):
        assert key in text, f"SKILL.md 缺少关键约定: {key}"


def test_no_mock_in_plugin_source():
    """用户硬性要求：严禁 mock。插件源码里不得出现任何 mock 框架。"""
    for name in ("server.py", "blender_host.py"):
        src = (PLUGIN_DIR / name).read_text(encoding="utf-8")
        for banned in ("unittest.mock", "MagicMock", "mock.patch", "fake_bpy"):
            assert banned not in src, f"{name} 出现了 mock: {banned}"


# ---------- 模板数据 ----------

def _preset(name: str) -> dict:
    return json.loads((PRESETS / f"{name}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["pet", "pcb"])
def test_preset_structure_and_palette(name):
    data = _preset(name)
    assert data["name"] == name
    assert data.get("parts"), f"{name} 没有 parts"
    assert data.get("root"), f"{name} 没有 root"
    palette = data.get("palette") or {}
    assert palette, f"{name} 没有调色板"
    for key, color in palette.items():
        assert _HEX.match(str(color)), f"{name} 调色板 {key} 不是 #RRGGBB：{color}"
        assert key in (data.get("materials") or {}), f"{name} 材质属性缺少 {key}"


def test_pet_has_selectable_styles():
    data = _preset("pet")
    styles = data.get("styles") or {}
    assert {"cat", "bear", "rabbit"} <= set(styles), f"pet 风格不足：{list(styles)}"
    for name, style in styles.items():
        assert style.get("parts"), f"pet 风格 {name} 没有 parts"


def test_pcb_silkscreen_lays_flat_and_reads_forward():
    """回归：丝印文字必须平贴板面（z 高于板面，避免 z-fighting），且不得绕 Z 轴转 180°
    —— 那会让文字从正面反读（真实踩过的坑）。"""
    data = _preset("pcb")
    texts = [p for p in data["parts"] if p.get("kind") == "text"]
    assert len(texts) >= 7, f"PC 丝印文字过少：{[p['name'] for p in texts]}"
    board = next(p for p in data["parts"] if p["name"] == "board")
    board_top = board["loc"][2] + board["size"] * board["scale"][2] / 2.0
    for t in texts:
        assert t["loc"][2] > board_top, f"{t['name']} 沉在板面以下，会与阻焊层 z-fighting"
        rot = t.get("rotation") or [0, 0, 0]
        assert abs(rot[2]) % 360 != 180, f"{t['name']} 绕 Z 轴转了 180°，文字会反读"


def test_lighting_presets_have_three_point_rigs():
    data = _preset("lighting")
    for name in ("product", "dramatic"):
        preset = data["presets"][name] if "presets" in data else data[name]
        assert preset.get("lights"), f"灯光预设 {name} 没有灯位"
        roles = {str(l.get("name") or l.get("role") or "").lower() for l in preset["lights"]}
        assert {"key", "fill", "rim"} <= roles, f"灯光预设 {name} 不是三点布光：{roles}"


# ---------- Blender 发现与输出目录 ----------

def test_find_blender_missing_raises_with_env(monkeypatch, tmp_path):
    mod = _load_module()
    monkeypatch.setenv("BLENDER_EXE", str(tmp_path / "not-here.exe"))
    with pytest.raises(RuntimeError):
        mod.find_blender(force=True)


def test_output_dir_follows_config(monkeypatch, tmp_path):
    mod = _load_module()
    target = tmp_path / "out"
    monkeypatch.setenv("BLENDER_OUTPUT_DIR", str(target))
    p = mod.output_dir()
    assert p == target and p.is_dir()


# ---------- MCP 协议与工具表 ----------

def test_mcp_initialize_and_tools_list():
    resps = _rpc([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ])
    by_id = {r.get("id"): r for r in resps}
    assert by_id[1]["result"]["serverInfo"]["name"] == "blender-modeler"
    tools = by_id[2]["result"]["tools"]
    names = {t["name"] for t in tools}
    assert names == EXPECTED_TOOLS, f"工具集不匹配，缺：{EXPECTED_TOOLS - names}，多：{names - EXPECTED_TOOLS}"
    for t in tools:
        assert t.get("description"), f"{t['name']} 缺描述"
        schema = t.get("inputSchema") or {}
        assert schema.get("type") == "object", f"{t['name']} schema 非法"
        assert isinstance(schema.get("properties"), dict)


def test_tools_call_unknown_tool_reports_error():
    resps = _rpc([{"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                   "params": {"name": "no_such_tool", "arguments": {}}}])
    assert resps and resps[0]["error"]["code"] == -32601


def test_tools_call_status_is_readonly(tmp_path):
    """blender_status 不启动 Blender：只看可用性/输出目录（测试不碰用户桌面）。"""
    resps = _rpc([{"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                   "params": {"name": "blender_status", "arguments": {}}}],
                 env={"BLENDER_OUTPUT_DIR": str(tmp_path)})
    text = "\n".join(c.get("text", "") for c in resps[0]["result"]["content"])
    assert "输出目录" in text and str(tmp_path) in text
    if _have_blender():
        assert "Blender 已找到" in text
    assert "后台实例未启动" in text


# ---------- 局域网预览页 ----------

def test_preview_page_serves_state_and_blocks_traversal(tmp_path, monkeypatch):
    """真实起 HTTP 服务：页面可打开、/api/state 返回结构化数据、图片路由只认单层 .png。"""
    mod = _load_module()
    monkeypatch.setenv("BLENDER_OUTPUT_DIR", str(tmp_path))
    info = mod.PREVIEW.start(port=0, bind="127.0.0.1")
    base = info["local_url"]
    try:
        assert info["port"] > 0
        with urllib.request.urlopen(base, timeout=10) as r:
            html = r.read().decode("utf-8")
        assert "ZHIZHU BLENDER PREVIEW" in html and "api/state" in html
        with urllib.request.urlopen(base + "api/state", timeout=10) as r:
            state = json.loads(r.read().decode("utf-8"))
        for key in ("running", "images", "output_dir", "now", "bind", "port"):
            assert key in state
        assert Path(state["output_dir"]) == tmp_path
        assert isinstance(state["images"], list)
        # 有真实渲染图时必须能取到（开发机常见；干净机器上列表为空也合法）
        if state["images"]:
            with urllib.request.urlopen(base + state["images"][0]["url"], timeout=10) as r:
                assert r.headers.get("Content-Type") == "image/png"
                assert len(r.read()) > 100
        for bad in ("img/..%2F..%2Fplugin.json", "img/%2Fetc%2Fpasswd", "img/x.txt", "nope"):
            with pytest.raises(urllib.error.HTTPError) as err:
                urllib.request.urlopen(base + bad, timeout=10)
            assert err.value.code == 404
    finally:
        assert mod.PREVIEW.stop() is True
        assert mod.PREVIEW.running() is False


# ---------- 真实集成：起 Blender → 建模 → 渲染 ----------

@_needs_blender
def test_selftest_models_and_renders_real_image(tmp_path):
    """真实跑一次完整链路（预计十几秒到几十秒），验证渲染图真的落盘。"""
    env = dict(os.environ)
    env["BLENDER_OUTPUT_DIR"] = str(tmp_path)
    proc = subprocess.run([sys.executable, str(SERVER), "--selftest"],
                          capture_output=True, check=False, timeout=600, env=env)
    assert proc.returncode == 0, f"自检失败：{(proc.stdout or b'').decode('utf-8', 'replace')}"
    data = json.loads((proc.stdout or b"").decode("utf-8", "replace"))
    assert data["ok"] is True, data.get("error")
    png = Path(data["steps"]["render"]["path"])
    assert png.is_file() and png.stat().st_size > 1000
    assert png.parent == tmp_path
    assert set(data["steps"]["templates"]) >= {"pet", "pcb"}


@_needs_blender
def test_template_apply_creates_expected_objects():
    """真实建模：套 pcb 模板后对象与三角面数落在低模区间。"""
    mod = _load_module()
    bridge = mod.BRIDGE
    try:
        bridge.start()
        bridge.call("scene_new", {})
        info = bridge.call("template_apply", {"template": "pcb", "size": 1.0})
        assert info["root"] == "PCB_Root"
        assert info["count"] >= 20
        scene = bridge.call("scene_info", {"detail": "brief"})
        assert scene["summary"]["triangles"] < 20000, "低模三角面数超标"
        names = {o["name"] for o in scene["objects"]}
        assert {"board", "u1", "c1", "c2", "usb", "silk_title"} <= names
    finally:
        bridge.stop()
