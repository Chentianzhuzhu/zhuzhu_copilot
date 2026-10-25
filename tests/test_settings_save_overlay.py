# -*- coding: utf-8 -*-
"""设置页保存遮罩的弹出时机（用户硬性要求：点保存当帧弹出，不得过 500ms）。

2026-10-25 用户反馈：「点击设置页保存时，过 500ms 之后才弹出高斯模糊遮罩层和
圆点转圈动画」。根因有两层：
  1. `_LoadingOverlay.start()` 把**全尺寸高斯模糊**放在 `show()` 之前同步做
     （QGraphicsBlurEffect 半径 22 渲染整幅对话框，几百毫秒）；
  2. `_save()` 把页面补齐（_ensure_all_pages）与全量数据收集放在遮罩创建之前。
修法：遮罩先抓图 → show → **同步 repaint 一帧** → 再做重活；模糊降采样 1/4
后放到下一事件回合异步算完再 update。

本测试锁定三条不变量：
  A. `_save()` 返回时遮罩已显示且已绘制（首绘在 500ms 内）；
  B. 首绘不晚于重活（_ensure_all_pages）开始 —— 即绘制没被重活推迟；
  C. 遮罩铺满对话框；模糊层在泵事件后异步就位（_blur 非空、_raw 释放）。
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest                                        # noqa: E402
from PyQt6.QtWidgets import QApplication              # noqa: E402
from PyQt6.QtCore import QEvent                       # noqa: E402

_app = QApplication.instance() or QApplication([])

from zhuzhu_Copilot.ui import agent_panel as ap       # noqa: E402


def _pump(ms: int = 120):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        _app.processEvents()


@pytest.fixture()
def dlg(monkeypatch):
    """构造真实设置对话框；屏蔽所有落盘/系统副作用（避免测试写用户配置）。"""
    mp = pytest.MonkeyPatch()
    mp.setattr(ap.agent_skills, "save_settings", lambda data: True)
    mp.setattr(ap.agent_skills, "save_mcp_servers", lambda servers: True)
    mp.setattr(ap.agent_tts, "save_config", lambda **kw: None)
    try:
        import zhuzhu_Copilot.core.agent_proxy_service as _aps
        mp.setattr(_aps, "apply", lambda: None)
        mp.setattr(_aps, "status", lambda: {})
    except Exception:
        pass
    d = ap._AgentSettingsDialog()
    d.resize(1040, 860)
    d.show()
    _pump(150)
    yield d
    try:
        d.close()
        _pump(40)
    except Exception:
        pass
    mp.undo()


def test_save_overlay_paints_before_heavy_work(dlg, monkeypatch):
    """遮罩首绘必须当帧发生、且不被页面补齐/数据收集推迟。"""
    painted = []
    orig_paint = ap._LoadingOverlay.paintEvent

    def _rec_paint(self, e):
        painted.append(time.monotonic())
        orig_paint(self, e)

    monkeypatch.setattr(ap._LoadingOverlay, "paintEvent", _rec_paint)

    marks = {}
    # 重活入口：分帧补建设置页（_build_pages_step，每帧 2 页）与数据收集
    # （_collect_and_save）。首绘必须早于它们，否则遮罩仍被重活推迟（旧 bug）。
    orig_step = dlg._build_pages_step

    def _rec_step():
        marks.setdefault("heavy", time.monotonic())
        orig_step()

    monkeypatch.setattr(dlg, "_build_pages_step", _rec_step)

    t0 = time.monotonic()
    dlg._save()
    t_end = time.monotonic()

    ov = getattr(dlg, "_overlay", None)
    assert ov is not None and ov.isVisible(), "点保存后遮罩必须已显示"
    assert painted, "遮罩必须已经绘制过（repaint 同步帧）"
    first_paint_ms = (painted[0] - t0) * 1000.0
    assert first_paint_ms < 500, f"遮罩首绘延迟 {first_paint_ms:.0f}ms，超过 500ms"
    # 首绘不得晚于重活开始（绘制被 _ensure_all_pages 推迟 = 旧 bug 复萌）
    assert painted[0] <= marks.get("heavy", t_end) + 1e-6, \
        "遮罩首绘必须早于页面补齐等重活（否则用户仍要等几百ms）"
    # 遮罩铺满整个对话框
    assert ov.geometry() == dlg.rect(), "遮罩必须铺满对话框"


def test_blur_arrives_async_after_overlay_visible(dlg, monkeypatch):
    """磨砂底纹异步补算：遮罩已可见之后 _blur 才就位，且原图被释放。"""
    monkeypatch.setattr(ap.agent_skills, "save_settings", lambda data: True)
    monkeypatch.setattr(ap.agent_skills, "save_mcp_servers", lambda servers: True)
    monkeypatch.setattr(ap.agent_tts, "save_config", lambda **kw: None)
    dlg._save()
    ov = dlg._overlay
    assert ov is not None and ov.isVisible()
    # start() 当帧：只抓了原图，模糊尚未算（_blur 为 None 不阻碍显示）
    assert ov._blur is None and ov._raw is not None, \
        "start() 应只抓原图；模糊必须异步（这是当帧可见的前提）"
    _pump(400)
    assert ov._blur is not None, "泵事件后高斯模糊层必须已就位"
    assert ov._raw is None, "模糊算完后原图应释放（内存）"
    assert ov._blur.size() == ov.parent().grab().size() or ov._blur.width() > 0
