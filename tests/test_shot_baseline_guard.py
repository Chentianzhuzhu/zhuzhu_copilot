"""computer-control 截图基准防漂移：截图后前台被关/最小化/换窗口 → 拒绝旧图坐标。

只测纯逻辑 `_set_view` / `_shot_drift_note`（monkeypatch Win32 查询），
不注入键鼠、不拉起真实截图。
"""
import importlib.util
import sys
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
        spec = importlib.util.spec_from_file_location("cc_plugin_shot_guard", SERVER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MOD = mod
    return _MOD


@pytest.fixture()
def mod(monkeypatch):
    m = _load()
    monkeypatch.setattr(m, "foreground_window", lambda: 0xAAAA)
    monkeypatch.setattr(m, "window_rect",
                        lambda h: SimpleNamespace(left=0, top=0, right=800, bottom=600))
    m._set_view(0, 0, 1920, 1080, 1280, 720, "full")
    return m


def _fake_user32(alive=True, iconic=False):
    return SimpleNamespace(IsWindow=lambda h: alive, IsIconic=lambda h: iconic)


def test_set_view_records_foreground(mod):
    """截图基准必须记录当时的前台窗口与矩形"""
    assert mod._VIEW["fg"] == 0xAAAA
    assert mod._VIEW["fg_rect"] == (0, 0, 800, 600)


def test_drift_note_empty_when_foreground_unchanged(mod, monkeypatch):
    """前台没换：看到的就是截图里的画面，直接放行"""
    monkeypatch.setattr(mod, "user32", _fake_user32())
    assert mod._shot_drift_note(100, 100, 0, None) == ""


def test_drift_note_rejects_when_shot_window_closed(mod, monkeypatch):
    """截图后前台窗口被关闭 → 拒绝"""
    monkeypatch.setattr(mod, "user32", _fake_user32(alive=False))
    note = mod._shot_drift_note(100, 100, 0, None)
    assert "已拒绝" in note and "screen_shot" in note


def test_drift_note_rejects_when_shot_window_minimized(mod, monkeypatch):
    """截图后前台窗口被最小化 → 拒绝"""
    monkeypatch.setattr(mod, "user32", _fake_user32(alive=True, iconic=True))
    note = mod._shot_drift_note(100, 100, 0, None)
    assert "已拒绝" in note


def test_drift_note_rejects_when_point_moved_to_other_window(mod, monkeypatch):
    """前台换成了别的窗口，且落点不在原窗口/目标窗口上 → 拒绝并指出两边窗口"""
    monkeypatch.setattr(mod, "user32", _fake_user32())
    monkeypatch.setattr(mod, "foreground_window", lambda: 0xBBBB)
    monkeypatch.setattr(mod, "window_at", lambda x, y: 0xCCCC)
    monkeypatch.setattr(mod, "window_title",
                        lambda h: {0xAAAA: "记事本", 0xCCCC: "浏览器"}.get(int(h), ""))
    monkeypatch.setattr(mod, "_root_owner", lambda h: int(h))
    note = mod._shot_drift_note(10, 10, 0, None)
    assert "已拒绝" in note and "记事本" in note and "浏览器" in note


def test_drift_note_allows_same_window_under_point(mod, monkeypatch):
    """落点仍在截图时的前台窗口上（只是被遮挡）→ 放行，交给 _ensure_click_target"""
    monkeypatch.setattr(mod, "user32", _fake_user32())
    monkeypatch.setattr(mod, "foreground_window", lambda: 0xBBBB)
    monkeypatch.setattr(mod, "window_at", lambda x, y: 0xAAAA)
    monkeypatch.setattr(mod, "_root_owner", lambda h: int(h))
    assert mod._shot_drift_note(10, 10, 0, None) == ""


def test_drift_note_allows_explicit_target_window(mod, monkeypatch):
    """落点是用户显式指定的目标窗口 → 放行"""
    monkeypatch.setattr(mod, "user32", _fake_user32())
    monkeypatch.setattr(mod, "foreground_window", lambda: 0xBBBB)
    monkeypatch.setattr(mod, "window_at", lambda x, y: 0xDDDD)
    monkeypatch.setattr(mod, "_root_owner", lambda h: int(h))
    assert mod._shot_drift_note(10, 10, 0xDDDD, None) == ""


def test_drift_note_skips_physical_coordinates(mod, monkeypatch):
    """space=screen（物理坐标）不依赖截图基准 → 跳过校验"""
    monkeypatch.setattr(mod, "user32", _fake_user32(alive=False))
    assert mod._shot_drift_note(10, 10, 0, {"space": "screen"}) == ""


def test_drift_note_skips_points_outside_shot_window(mod, monkeypatch):
    """落点不在截图时前台窗口矩形内 → 内容不由该窗口产生，不做漂移判定"""
    monkeypatch.setattr(mod, "user32", _fake_user32(alive=False))
    assert mod._shot_drift_note(1200, 900, 0, None) == ""
