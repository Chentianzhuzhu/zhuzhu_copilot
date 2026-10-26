# -*- coding: utf-8 -*-
"""建模时自动打开实时预览页的回归。

用户要求：Agent 建模时必须打开 web 可视化预览网页，供用户实时查看。

守护契约：
  A. 「会改场景/产出」的工具触发自动预览；只读工具（status/scene_info/…）不触发；
  B. 同一进程只弹一次标签页（每步都开新标签会变成骚扰），preview_stop 后可再次打开；
  C. 自动打开失败绝不影响建模（预览是给人看的，不能挡住干活）；
  D. 工具结果首行带「预览页已自动打开」，模型才能把地址转述给用户；
  E. 页面有「最新渲染」大图区（实时看的关键）。
"""
import importlib.util
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
SERVER = SRC / "zhuzhu_Copilot" / "plugins" / "blender-modeler" / "server.py"


def _load(monkeypatch=None):
    spec = importlib.util.spec_from_file_location("blender_server_auto_preview", SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if monkeypatch is not None:
        monkeypatch.setattr(mod, "_log", lambda *a, **k: None)   # 别写 repo 里的 runtime.log
    return mod


class _FakePreview:
    def __init__(self):
        self.starts = 0

    def running(self):
        return self.starts > 0

    def info(self):
        return {"running": True, "bind": "0.0.0.0", "port": 8787,
                "lan_ip": "10.0.0.2",
                "local_url": "http://127.0.0.1:8787/",
                "url": "http://10.0.0.2:8787/"}

    def start(self, port=None, bind=None):
        self.starts += 1
        return self.info()

    def stop(self):
        was = self.starts > 0
        self.starts = 0
        return was


# ---------- A. 工具分类 ----------
def test_modeling_tools_trigger_preview_but_readonly_does_not():
    mod = _load()
    for name in ("blender_start", "scene_new", "object_add", "material_create",
                 "light_rig", "render_image", "export_model", "template_apply"):
        assert name in mod._MODELING_TOOLS, f"{name} 属于建模动作，必须触发自动预览"
    for name in ("blender_status", "scene_info", "template_list",
                 "preview_start", "preview_stop"):
        assert name not in mod._MODELING_TOOLS, f"{name} 不该触发自动预览"


# ---------- B/C. 只开一次 / 失败不影响 ----------
def test_preview_tab_opens_once_per_process(monkeypatch):
    mod = _load(monkeypatch)
    fake = _FakePreview()
    monkeypatch.setattr(mod, "PREVIEW", fake)
    opened = []
    monkeypatch.setattr(mod, "_open_in_browser", lambda u: opened.append(u) or True)
    monkeypatch.setattr(mod, "_preview_opened", False)

    info, ok = mod.open_preview()
    assert ok and opened == ["http://127.0.0.1:8787/"], "首次建模应打开本机地址"
    assert fake.starts == 1

    info, ok = mod.open_preview()
    assert not ok and len(opened) == 1, "同进程第二次建模不得再弹标签页"


def test_preview_can_reopen_after_stop(monkeypatch):
    mod = _load(monkeypatch)
    fake = _FakePreview()
    monkeypatch.setattr(mod, "PREVIEW", fake)
    opened = []
    monkeypatch.setattr(mod, "_open_in_browser", lambda u: opened.append(u) or True)
    monkeypatch.setattr(mod, "_preview_opened", False)

    mod.open_preview()
    mod.tool_preview_stop({})
    mod.open_preview()
    assert len(opened) == 2, "用户关掉预览后，下次建模应重新打开"


def test_browser_failure_does_not_break_preview(monkeypatch):
    mod = _load(monkeypatch)
    fake = _FakePreview()
    monkeypatch.setattr(mod, "PREVIEW", fake)
    monkeypatch.setattr(mod, "_open_in_browser", lambda u: False)   # 没默认浏览器
    monkeypatch.setattr(mod, "_preview_opened", False)

    info, ok = mod.open_preview()
    assert info and not ok, "打不开浏览器也要返回可用地址，不能让建模失败"


def test_preview_start_failure_is_silent(monkeypatch):
    mod = _load(monkeypatch)

    class _Broken:
        def running(self):
            return False

        def start(self, port=None, bind=None):
            raise OSError("port in use")

    monkeypatch.setattr(mod, "PREVIEW", _Broken())
    info, ok = mod.open_preview()
    assert info is None and ok is False, "预览服务起不来时只记日志，建模照常继续"


# ---------- D. 结果首行提示 ----------
def test_modeling_call_prepends_preview_hint(monkeypatch):
    mod = _load(monkeypatch)
    fake = _FakePreview()
    monkeypatch.setattr(mod, "PREVIEW", fake)
    monkeypatch.setattr(mod, "_open_in_browser", lambda u: True)
    monkeypatch.setattr(mod, "_preview_opened", False)
    monkeypatch.setitem(mod._HANDLERS, "scene_new", lambda args: "已新建空场景")

    resp = mod._handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": "scene_new", "arguments": {}}})
    text = resp["result"]["content"][0]["text"]
    assert text.startswith("预览页已自动打开：http://127.0.0.1:8787/"), text
    assert "已新建空场景" in text, "原有结果不能被吞掉"

    # 只读工具不带提示
    monkeypatch.setitem(mod._HANDLERS, "scene_info", lambda args: "场景摘要")
    resp = mod._handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                        "params": {"name": "scene_info", "arguments": {}}})
    assert resp["result"]["content"][0]["text"] == "场景摘要"


def test_image_content_keeps_order_when_text_added(monkeypatch):
    mod = _load(monkeypatch)
    monkeypatch.setattr(mod, "PREVIEW", _FakePreview())
    monkeypatch.setattr(mod, "_open_in_browser", lambda u: True)
    monkeypatch.setattr(mod, "_preview_opened", False)
    monkeypatch.setitem(mod._HANDLERS, "render_image", lambda args: [
        {"type": "image", "data": "AAA", "mimeType": "image/png"}])

    resp = mod._handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                        "params": {"name": "render_image", "arguments": {}}})
    items = resp["result"]["content"]
    assert items[0]["type"] == "text" and "预览页已自动打开" in items[0]["text"]
    assert items[1]["type"] == "image" and items[1]["data"] == "AAA", "图片内容项不能被破坏"


# ---------- E. 页面有最新渲染大图 ----------
def test_preview_page_has_latest_render_hero():
    mod = _load()
    html = mod._PREVIEW_HTML
    assert 'id="hero"' in html and "最新渲染" in html
    assert "setInterval(tick,2000)" in html, "页面必须自动刷新"
