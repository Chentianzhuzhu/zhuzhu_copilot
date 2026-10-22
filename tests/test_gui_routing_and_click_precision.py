"""GUI 操控任务的路由与点击精度回归测试。

背景（用户实测反馈的两条问题，根因是同一个链条）：

1. **输入「控制电脑 GUI 自动化」类提示词，系统却调起了浏览器操控**。
   `_SKILL_KEYWORDS["browser-control"]` 用「登录 / 点一下 / 帮我点 / 帮我打开」这类
   **桌面与网页都成立**的泛化词当触发词，而 `computer-control` 根本没有关键词条目，
   于是 GUI 操控提示词只能匹配到浏览器技能；`_TASK_GROUPS` 的 browser 组也把「登录」
   当触发词，桌面任务因此被裁成「只有浏览器工具」。

2. **点击极其不精准、不先截屏、不用任务栏**。
   更深一层的原因是 `ensure_md_skills()` 每次启动都 `rmtree` 掉 `computer-control`
   技能目录（2026-08 移除旧功能时留下的清理逻辑）—— 而该技能现在由随包插件提供。
   技能目录一没，SKILL.md 就不进技能手册、也不会被自动匹配，模型拿不到
   「先截图 / 任务栏优先 / refine_point 挑编号」这些纪律，只能对着工具 schema 猜坐标。

本文件覆盖：
- 路由：GUI 类提示词命中 desktop + computer-control，且**不再**命中 browser-control；
  纯网页任务仍走 browser；写脚本类任务不被误判；
- 技能可用性：插件提供的技能不会被启动清理删掉，缺失时按插件 SKILL.md 自愈补回；
- 点击精度：无新鲜截图基准时坐标工具**拒绝执行**（先截屏硬门槛）；
  refine_point 的候选可被 `click(cand=N)` 直接取用（不再手抄坐标）；
- 应用打开：open_app 暴露 index/where/launch，按编号点任务栏图标一步切应用。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import importlib.util
import time
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from zhuzhu_Copilot.core import agent_engine, agent_plugins, agent_runtime, agent_skills

PLUGIN_DIR = (Path(__file__).resolve().parent.parent / "src" / "zhuzhu_Copilot"
              / "plugins" / "computer-control")
SERVER = PLUGIN_DIR / "server.py"

_MODULE = None


def _cc():
    """以模块方式加载插件 server（模块级无副作用）。"""
    global _MODULE
    if _MODULE is None:
        spec = importlib.util.spec_from_file_location("cc_precision_server", SERVER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MODULE = mod
    return _MODULE


def _groups(text):
    g = agent_engine._detect_task_groups(text)
    return set(g) if g else None


# ===========================================================================
# 一、路由：GUI 操控提示词必须落到 desktop / computer-control
# ===========================================================================

GUI_PROMPTS = (
    "帮我用GUI自动化控制微信给第二个人发消息",
    "帮我自动化操作这个软件的界面，点击登录按钮",
    "控制电脑帮我打开记事本并输入文字",
    "帮我点一下屏幕上的确定按钮",
    "用鼠标自动化操作QQ",
    "帮我操作电脑打开微信",
    "帮我自动化完成这个软件的登录流程",
    "帮我在桌面上自动点击登录按钮",
    "帮我控制电脑完成这个GUI任务",
    "用 gui 帮我点确定",
)


def test_gui_prompts_route_to_desktop_and_computer_control():
    """GUI 操控类提示词：任务裁剪命中 desktop，技能命中 computer-control。"""
    for q in GUI_PROMPTS:
        assert _groups(q) == {"desktop"}, f"{q!r} 任务分组错误：{_groups(q)}"
        skills = agent_skills.auto_skill_names(q)
        assert "computer-control" in skills, f"{q!r} 未命中 computer-control：{skills}"
        assert "browser-control" not in skills, \
            f"{q!r} 误命中浏览器技能（会被带去调 browser_*）：{skills}"


def test_login_alone_is_not_a_web_signal():
    """「登录」不是网页专属：桌面应用的登录界面极常见，不能因此判成网页任务。"""
    assert _groups("帮我自动化完成这个软件的登录流程") == {"desktop"}
    assert _groups("打开网页登录") == {"browser"}


def test_web_prompts_still_route_to_browser():
    """纯网页任务不能被 desktop 抢走（桌面工具对网页无用）。"""
    for q in ("帮我点一下网页上的链接", "浏览网页并点赞", "打开网页登录",
              "帮我打开网站"):
        assert _groups(q) == {"browser"}, f"{q!r} 被误判：{_groups(q)}"
        assert "browser-control" in agent_skills.auto_skill_names(q)


def test_code_tasks_are_not_gui_automation():
    """「写个自动点击脚本」是编码任务，不该灌入整套操控纪律。"""
    assert _groups("帮我写个自动点击脚本") is None
    assert "computer-control" not in agent_skills.auto_skill_names("帮我写个自动点击脚本")


def test_non_gui_tasks_unaffected():
    assert _groups("你好") is None
    assert _groups("读取这个文件的内容") is None
    assert _groups("做个ppt") == {"doc"}
    assert _groups("优化内存") == {"sys"}


def test_gui_and_web_both_explicit_keeps_both():
    """两者都有明确信号时都保留，由模型按技能规范分工。"""
    skills = agent_skills.auto_skill_names("在浏览器里操作网页并点击登录")
    assert "browser-control" in skills


# ===========================================================================
# 二、技能可用性：插件技能不能被启动清理删掉，缺失要自愈
# ===========================================================================

@pytest.fixture()
def iso(tmp_path, monkeypatch):
    """隔离插件目录 / 技能目录 / MCP 配置。"""
    monkeypatch.setattr(agent_plugins, "PLUGINS_DIR", tmp_path / "plugins")
    monkeypatch.setattr(agent_skills, "CONFIG_DIR", tmp_path / "agent")
    monkeypatch.setattr(agent_runtime, "python_interpreter", lambda: "python-test")
    agent_skills._MCP_CACHE["data"] = None
    agent_skills.invalidate_skills_cache()
    return tmp_path


def test_plugin_skill_survives_startup_cleanup(iso):
    """回归：ensure_md_skills 曾把 computer-control 技能目录整个删掉。

    技能目录没了 → SKILL.md 不进技能手册/不被自动匹配 → 模型拿不到操控纪律，
    表现为乱点坐标、不用任务栏。这里断言插件在提供该技能时**不许删**。
    """
    agent_plugins.ensure_shipped_plugins()
    skill = iso / "agent" / "skills" / "computer-control" / "SKILL.md"
    assert skill.is_file(), "随包插件登记后应有 computer-control 技能"

    agent_skills.ensure_md_skills()
    assert skill.is_file(), "启动清理不得删除插件提供的 computer-control 技能"
    assert "先截屏" in skill.read_text(encoding="utf-8") or \
           "screen_shot" in skill.read_text(encoding="utf-8")


def test_missing_plugin_skill_is_healed(iso):
    """存量安装：旧版本已经把技能删掉了（插件 revision 未变，不会再登记）→ 自愈补回。"""
    agent_plugins.ensure_shipped_plugins()
    skill = iso / "agent" / "skills" / "computer-control" / "SKILL.md"
    assert skill.is_file()
    skill.unlink()
    skill.parent.rmdir()

    agent_skills.ensure_md_skills()
    assert skill.is_file(), "技能缺失时必须按插件 SKILL.md 自愈补回"


def test_legacy_skills_still_cleaned(iso):
    """真正废弃的旧技能目录仍然要被清掉（别为了修 computer-control 把清理逻辑废掉）。"""
    legacy = iso / "agent" / "skills" / "ui-automation" / "SKILL.md"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("---\nname: ui-automation\ndescription: x\n---\n\n旧技能\n",
                      encoding="utf-8")
    agent_skills.ensure_md_skills()
    assert not legacy.parent.exists()


# ===========================================================================
# 三、点击精度：先截屏硬门槛 + 候选编号点击
# ===========================================================================

def _fresh_view(mod):
    mod._set_view(0, 0, 1920, 1080, 1280, 720, "full")


def test_click_refused_without_screenshot_baseline(monkeypatch):
    """没有截图基准时坐标无从换算 → 必须拒绝执行并提示先 screen_shot。"""
    mod = _cc()
    mod._STATE["interrupted"] = mod._STATE["paused"] = False
    mod._VIEW.update({"x0": 0, "y0": 0, "w": 0, "h": 0, "img_w": 0, "img_h": 0, "ts": 0})
    clicked = []
    monkeypatch.setattr(mod, "do_click",
                        lambda x, y, b="left", c=1, **k: clicked.append((x, y)) or True)
    with pytest.raises(ValueError) as ei:
        mod._HANDLERS["click"]({"x": 100, "y": 200})
    assert "screen_shot" in str(ei.value)
    assert clicked == [], "拒绝时绝不能真的点下去"


def test_click_refused_when_baseline_is_stale(monkeypatch):
    """截图超过有效期（界面已变）同样拒绝，逼模型重新看图。"""
    mod = _cc()
    mod._STATE["interrupted"] = mod._STATE["paused"] = False
    _fresh_view(mod)
    mod._VIEW["ts"] = 1.0                     # 很久以前
    monkeypatch.setattr(mod, "do_click",
                        lambda x, y, b="left", c=1, **k: True)
    with pytest.raises(ValueError) as ei:
        mod._HANDLERS["click"]({"x": 100, "y": 200})
    assert "有效期" in str(ei.value)


def test_space_screen_bypasses_screenshot_gate(monkeypatch):
    """物理坐标自带基准（如 list_windows 给的窗口位置），不需要截图。"""
    mod = _cc()
    mod._STATE["interrupted"] = mod._STATE["paused"] = False
    mod._VIEW.update({"x0": 0, "y0": 0, "w": 0, "h": 0, "img_w": 0, "img_h": 0, "ts": 0})
    clicked = []
    monkeypatch.setattr(mod, "_ensure_click_target", lambda x, y, a=None: "")
    monkeypatch.setattr(mod, "do_click",
                        lambda x, y, b="left", c=1, **k: clicked.append((x, y)) or True)
    mod._HANDLERS["click"]({"x": 100, "y": 200, "space": "screen", "snap": False})
    assert clicked == [(100, 200)]


def test_click_by_candidate_number_uses_exact_center(monkeypatch):
    """click(cand=N) 必须点在该候选框的几何中心，且不再二次吸附。"""
    mod = _cc()
    mod._STATE["interrupted"] = mod._STATE["paused"] = False
    _fresh_view(mod)
    cands = [
        {"i": 1, "x": 120, "y": 287, "w": 43, "h": 13, "d": 13.0, "kind": "行",
         "box": (99, 281, 142, 293)},
        {"i": 2, "x": 120, "y": 317, "w": 51, "h": 11, "d": 17.0, "kind": "行",
         "box": (95, 312, 146, 322)},
    ]
    mod._STATE["cands"] = {"ts": time.time(), "items": cands}
    monkeypatch.setattr(mod, "_apply_snap",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("cand 定位后不该再吸附")))
    monkeypatch.setattr(mod, "_ensure_click_target", lambda x, y, a=None: "")
    clicked = []
    monkeypatch.setattr(mod, "do_click",
                        lambda x, y, b="left", c=1, **k: clicked.append((x, y)) or True)
    out = mod._HANDLERS["click"]({"cand": 2})
    assert clicked == [(120, 317)], f"应点第 2 个候选的中心，实际 {clicked}"
    assert "候选 #2" in out


def test_click_by_missing_candidate_reports_available_numbers(monkeypatch):
    """编号不存在时要给出可用编号，而不是静默点到别处。"""
    mod = _cc()
    mod._STATE["interrupted"] = mod._STATE["paused"] = False
    _fresh_view(mod)
    mod._STATE["cands"] = {"ts": time.time(),
                           "items": [{"i": 1, "x": 10, "y": 20, "w": 5, "h": 5,
                                      "d": 0.0, "kind": "块", "box": (8, 18, 12, 22)}]}
    with pytest.raises(ValueError) as ei:
        mod._HANDLERS["click"]({"cand": 9})
    assert "没有编号 9" in str(ei.value)


def test_refine_point_stores_candidates_for_later_click(monkeypatch):
    """refine_point 必须把候选落库，否则 click(cand=N) 无从取值。"""
    mod = _cc()
    mod._STATE["interrupted"] = mod._STATE["paused"] = False
    _fresh_view(mod)
    one = {"i": 1, "x": 120, "y": 287, "w": 43, "h": 13, "d": 3.0, "kind": "行",
           "box": (99, 281, 142, 293)}

    def fake_snap_core(x, y, reach=None, max_shift=None, cands=None):
        if cands is not None:
            cands.append(dict(one))
        return 120, 287, "已吸附", []

    monkeypatch.setattr(mod, "_snap_core", fake_snap_core)
    monkeypatch.setattr(mod, "grab_zoom",
                        lambda *a, **k: (b"PNG", 300, 300, 0, 0, 100))
    mod._HANDLERS["refine_point"]({"x": 118, "y": 285})
    items = mod._STATE["cands"]["items"]
    assert items and items[0]["i"] == 1 and items[0]["x"] == 120


def test_click_without_coordinates_raises():
    mod = _cc()
    mod._STATE["interrupted"] = mod._STATE["paused"] = False
    _fresh_view(mod)
    with pytest.raises(ValueError) as ei:
        mod._HANDLERS["click"]({})
    assert "cand" in str(ei.value)


# ===========================================================================
# 四、打开应用：任务栏 / 托盘优先
# ===========================================================================

def test_open_app_schema_exposes_taskbar_priority_args():
    mod = _cc()
    schema = next(t for t in mod.TOOLS if t["name"] == "open_app")
    props = schema["inputSchema"]["properties"]
    for key in ("index", "where", "launch"):
        assert key in props, f"open_app 缺少 {key} 参数（任务栏/托盘优先依赖它）"
    assert schema["inputSchema"]["required"] == ["name"]


def test_open_app_index_clicks_taskbar_slot(monkeypatch):
    """open_app(index=N) 必须转成任务栏编号点击，而不是启动新实例。"""
    mod = _cc()
    mod._STATE["interrupted"] = mod._STATE["paused"] = False
    seen = {}

    def fake_taskbar(args):
        seen.update(args)
        return [{"type": "text", "text": "任务栏编号图"}]

    monkeypatch.setattr(mod, "tool_taskbar_open", fake_taskbar)
    out = mod._HANDLERS["open_app"]({"name": "微信", "index": 3})
    assert seen.get("index") == 3, f"未按编号点任务栏：{seen}"
    text = out[0]["text"] if isinstance(out, list) else str(out)
    assert "第 3 个" in text


def test_open_app_tray_does_not_launch_duplicate(monkeypatch):
    """进程在跑但没有可见窗口（托盘）→ 不许启动第二个实例，回编号图。"""
    mod = _cc()
    mod._STATE["interrupted"] = mod._STATE["paused"] = False
    monkeypatch.setattr(mod, "_find_app_window", lambda keys: (0, "", ""))
    monkeypatch.setattr(mod, "_resolve_app",
                        lambda name: {"target": r"C:\fake\wechat.exe", "kind": "lnk",
                                      "display": "微信", "score": 1.0, "candidates": []})
    monkeypatch.setattr(mod, "_process_running", lambda keys: "WeChat")
    monkeypatch.setattr(mod, "launch_app",
                        lambda t: (_ for _ in ()).throw(
                            AssertionError("托盘应用不该再启动一个实例")))
    monkeypatch.setattr(mod, "tool_taskbar_open",
                        lambda args: [{"type": "text", "text": "编号图"}])
    out = mod._HANDLERS["open_app"]({"name": "微信"})
    text = out[0]["text"] if isinstance(out, list) else str(out)
    assert "托盘" in text and "index" in text


def test_open_app_launches_when_really_not_running(monkeypatch):
    """进程确实没在跑 → 照旧启动，并给出 hwnd。"""
    mod = _cc()
    mod._STATE["interrupted"] = mod._STATE["paused"] = False
    monkeypatch.setattr(mod, "_find_app_window", lambda keys: (0, "", ""))
    monkeypatch.setattr(mod, "_resolve_app",
                        lambda name: {"target": r"C:\fake\app.exe", "kind": "lnk",
                                      "display": "记事本", "score": 1.0, "candidates": []})
    monkeypatch.setattr(mod, "_process_running", lambda keys: "")
    launched = []
    monkeypatch.setattr(mod, "launch_app", lambda t: launched.append(t))
    monkeypatch.setattr(mod, "_wait_app_window", lambda keys, **k: (4242, "记事本", "窗口标题"))
    monkeypatch.setattr(mod, "_bring_to_front", lambda h: (True, ""))
    monkeypatch.setattr(mod, "window_rect",
                        lambda h: type("R", (), {"left": 10, "top": 20, "right": 810,
                                                 "bottom": 620})())
    out = mod._HANDLERS["open_app"]({"name": "记事本"})
    assert launched == [r"C:\fake\app.exe"]
    assert "4242" in out
