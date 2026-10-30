"""zoom_in 定位回归：服务商压图时必须拒绝未校准绝对像素，并支持窗口/候选/归一化三种定位。

只测决策与参数传递（monkeypatch 抓图函数），不真实截屏、不注入键鼠。
"""
import importlib.util
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

PLUGIN_DIR = (Path(__file__).resolve().parent.parent / "src" / "zhuzhu_Copilot"
              / "plugins" / "computer-control")
SERVER = PLUGIN_DIR / "server.py"

_MOD = None


def _load():
    global _MOD
    if _MOD is None:
        spec = importlib.util.spec_from_file_location("cc_plugin_zoom_in", SERVER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MOD = mod
    return _MOD


@pytest.fixture()
def mod(monkeypatch):
    m = _load()
    m._VIEW.update({"x0": 0.0, "y0": 0.0, "w": 1920.0, "h": 1080.0,
                    "img_w": 1280.0, "img_h": 720.0, "ts": time.time(),
                    "fg": 0, "fg_rect": None})
    m._STATE["cands"] = {}
    m._STATE["view_target"] = 0
    calls = {"zoom": [], "region": [], "view": []}

    def fake_zoom(cx, cy, size, factor, **kw):
        calls["zoom"].append((cx, cy, size, factor))
        return b"PNG", 1200, 1200, 0, 0, 400   # 与真实 grab_zoom 一致：6 元组

    def fake_region(x0, y0, w, h, factor=1, **kw):
        calls["region"].append((x0, y0, w, h, factor))
        return b"PNG", 400, 300, 100, 100, 400, 300

    monkeypatch.setattr(m, "_settle_after_action", lambda: None)
    monkeypatch.setattr(m, "grab_zoom", fake_zoom)
    monkeypatch.setattr(m, "grab_region", fake_region)
    monkeypatch.setattr(m, "_set_view", lambda *a: calls["view"].append(a))
    monkeypatch.setattr(m, "find_window", lambda title, hwnd: int(hwnd or 0))
    monkeypatch.setattr(m, "window_rect",
                        lambda h: SimpleNamespace(left=100, top=100,
                                                  right=500, bottom=400))
    monkeypatch.setattr(m, "window_title", lambda h: "记事本")
    m._calls = calls
    return m


def _text(ret) -> str:
    return "\n".join(x.get("text", "") for x in ret if x.get("type") == "text")


def test_zoom_in_rejects_uncalibrated_pixels(mod):
    """未校准绝对像素（agnes 压图场景）→ 拒绝放大，且不抓任何图"""
    ret = mod.tool_zoom_in({"x": 380, "y": 680})
    assert mod._calls["zoom"] == [] and mod._calls["region"] == []
    text = _text(ret)
    assert "已拒绝本次放大" in text
    assert "nx" in text and "cand" in text


def test_zoom_in_window_mode_needs_no_coordinates(mod):
    """只给 hwnd → 按窗口矩形抓图，并把后续点击目标锁定到该窗口"""
    ret = mod.tool_zoom_in({"hwnd": 0x1234})
    assert mod._calls["region"] == [(100, 100, 400, 300, 1)]
    assert mod._STATE["view_target"] == 0x1234
    assert mod._calls["view"][-1][:4] == (100, 100, 400, 300)
    assert "记事本" in _text(ret)


def test_zoom_in_candidate_mode_uses_plugin_coordinates(mod):
    """cand 编号 → 坐标由插件按候选记录计算（免疫压图）"""
    mod._STATE["cands"] = {"ts": time.time(),
                           "items": [{"i": 2, "x": 300, "y": 200, "w": 20, "h": 10,
                                      "kind": "按钮"}]}
    ret = mod.tool_zoom_in({"cand": 2})
    assert mod._calls["zoom"] == [(300, 200, 400, 3)]
    assert "候选 #2" in _text(ret)


def test_zoom_in_normalized_mode_scales_to_screen(mod):
    """nx/ny 与图片尺寸无关：0.5/0.5 → 屏幕中心"""
    ret = mod.tool_zoom_in({"nx": 0.5, "ny": 0.5})
    assert mod._calls["zoom"][0][:2] == (960, 540)
    assert "已放大屏幕" in _text(ret)


def test_zoom_in_window_with_uncalibrated_pixels_still_rejected(mod):
    """同时给 hwnd 与未校准像素 → 仍按像素路径拦截（不静默偏移）"""
    ret = mod.tool_zoom_in({"hwnd": 0x1234, "x": 380, "y": 680})
    assert mod._calls["zoom"] == [] and mod._calls["region"] == []
    assert "已拒绝本次放大" in _text(ret)
