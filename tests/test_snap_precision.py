"""元素坐标定位精度回归：密排候选决胜、空心边框淘汰、默认点击校验。

只测纯函数与 click 决策链（monkeypatch 注入/探测），不真实截屏、不注入键鼠。
"""
import importlib.util
import time
from pathlib import Path

import pytest

PLUGIN_DIR = (Path(__file__).resolve().parent.parent / "src" / "zhuzhu_Copilot"
              / "plugins" / "computer-control")
SERVER = PLUGIN_DIR / "server.py"

_MOD = None


def _load():
    global _MOD
    if _MOD is None:
        spec = importlib.util.spec_from_file_location("cc_plugin_snap_precision", SERVER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MOD = mod
    return _MOD


def _comp(x0, y0, x1, y1, area=None):
    return {"minx": x0, "miny": y0, "maxx": x1, "maxy": y1,
            "area": (x1 - x0 + 1) * (y1 - y0 + 1) if area is None else area}


# ---------- 1. 密排候选：2px 分档 + 中心距离决胜 ----------

def test_rank_key_tie_bucket_prefers_nearer_center():
    """两个候选与估计点的外接框距离几乎相同（同 2px 档）→ 中心更近的胜出"""
    mod = _load()
    left = _comp(0, 0, 10, 10)          # 中心 (5,5)，距离 1
    right = _comp(12, 0, 30, 10)        # 中心 (21,5)，距离 1
    k_left = mod._snap_rank_key(left, 11, 5, False, 1.0)
    k_right = mod._snap_rank_key(right, 11, 5, False, 1.0)
    assert k_left < k_right, (k_left, k_right)


def test_rank_key_keeps_distance_priority_across_buckets():
    """距离差超过一档（≥2px）时仍以「离外接框近」优先，不被中心距离逆转"""
    mod = _load()
    near = _comp(40, 0, 50, 10)         # 距离 1（距右边缘），中心 45
    farther = _comp(39, 0, 49, 10)      # 距离 2，中心 44（离估计点中心更近）
    k_near = mod._snap_rank_key(near, 51, 5, False, 1.0)
    k_farther = mod._snap_rank_key(farther, 51, 5, False, 2.0)
    assert k_near < k_farther, (k_near, k_farther)


def test_rank_key_penalizes_slender_components():
    """同档距离下，细长条（分隔线/滚动条）必须让位于实心控件"""
    mod = _load()
    line = _comp(48, 0, 52, 200)        # 5×201 细长
    solid = _comp(48, 0, 62, 20)        # 15×21 实心
    k_line = mod._snap_rank_key(line, 47, 5, False, 1.0)
    k_solid = mod._snap_rank_key(solid, 47, 5, False, 1.0)
    assert k_solid < k_line, (k_solid, k_line)


# ---------- 2. 空心边框 / 阴影环淘汰 ----------

def test_hollow_component_rejected():
    """40×40 外接框里只有 120 个前景像素（填充率 7.5%）→ 判定为边框/阴影，不可吸附"""
    mod = _load()
    assert mod._component_usable(_comp(5, 5, 44, 44, area=120), 100, 100) is False


def test_solid_component_accepted():
    mod = _load()
    assert mod._component_usable(_comp(5, 5, 44, 44, area=1600), 100, 100) is True


def test_small_checkbox_border_not_rejected():
    """16×16 复选框边框（填充率低，但整块可点）不受填充率规则影响"""
    mod = _load()
    assert mod._component_usable(_comp(4, 4, 19, 19, area=60), 100, 100) is True


def test_component_touching_region_edge_rejected():
    mod = _load()
    assert mod._component_usable(_comp(0, 10, 30, 40, area=900), 100, 100) is False


# ---------- 3. 默认点击校验 ----------

def _prepare_click(mod, monkeypatch, clicked):
    mod._STATE["interrupted"] = mod._STATE["paused"] = False
    mod._VIEW.update({"x0": 0, "y0": 0, "w": 1920, "h": 1080,
                      "img_w": 1280, "img_h": 720, "ts": time.time(),
                      "fg": 0, "fg_rect": None})
    monkeypatch.setattr(mod, "_resolve_point", lambda a, *k: (500, 400))
    monkeypatch.setattr(mod, "_apply_snap", lambda a, x, y, default_on=True: (510, 402, "已吸附"))
    monkeypatch.setattr(mod, "_ensure_click_target", lambda x, y, a: "")
    monkeypatch.setattr(mod, "_input_mode", lambda a: "mouse")
    monkeypatch.setattr(mod, "_pre_click_probe", lambda x, y, h: ("probe", ()))
    monkeypatch.setattr(mod, "_click_verify_ex", lambda *a: (False, 0.0, "落点没有变化"))
    monkeypatch.setattr(mod, "_cursor_pos", lambda: (510, 402))
    monkeypatch.setattr(mod, "_refresh_env", lambda: None)
    monkeypatch.setattr(mod, "_log", lambda who, text: None)
    monkeypatch.setattr(mod, "do_click",
                        lambda x, y, b, c, mode="auto": clicked.append((x, y)) or True)
    monkeypatch.setattr(mod, "_alt_snap_point",
                        lambda *a: pytest.fail("默认校验不得自动重试（可能重复点击）"))


def test_click_verifies_by_default(monkeypatch):
    """未显式传 verify 时也会做效果校验，并以「已生效但无反馈」的口吻回传"""
    mod = _load()
    clicked = []
    _prepare_click(mod, monkeypatch, clicked)
    out = mod._HANDLERS["click"]({"raw": True, "x": 500, "y": 400})
    assert clicked == [(510, 402)], "默认校验时只点一次，不自动换候选"
    assert "未观察到可见变化" in out and "可能已生效" in out


def test_click_verify_false_skips_probe(monkeypatch):
    """显式 verify=false → 跳过探测，保持低延迟"""
    mod = _load()
    clicked = []
    _prepare_click(mod, monkeypatch, clicked)
    monkeypatch.setattr(mod, "_pre_click_probe",
                        lambda *a: pytest.fail("verify=false 不得做点击前探测"))
    out = mod._HANDLERS["click"]({"raw": True, "x": 500, "y": 400, "verify": False})
    assert clicked == [(510, 402)]
    assert "未观察到可见变化" not in out
