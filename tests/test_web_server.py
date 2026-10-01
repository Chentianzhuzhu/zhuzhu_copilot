# -*- coding: utf-8 -*-
"""本地数据服务（`zhuzhu_Copilot.web.server`）的契约测试。

这些接口是 Web 前端唯一的真实数据来源，所以断言的是**结构契约**：字段必须在、
会话列表要按 updated 倒序、消息行要能还原成 user/ai 两种、路径穿越要被挡住。
"""
import json
import re
import sys
from pathlib import Path

import pytest
from PyQt6.QtGui import QColor

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zhuzhu_Copilot.web import server                  # noqa: E402


@pytest.fixture()
def fake_home(tmp_path, monkeypatch):
    """把用户数据目录整体换成临时目录，并写入一份**真实结构**的会话数据。"""
    from zhuzhu_Copilot import app_identity
    from zhuzhu_Copilot.core import agent_skills

    home = tmp_path / "home"
    (home / "agent" / "sessions").mkdir(parents=True)
    monkeypatch.setattr(app_identity, "_home", lambda: home)
    monkeypatch.setattr(app_identity, "_migrated", True)
    monkeypatch.setattr(agent_skills, "CONFIG_DIR", home / "agent")

    sessions = [
        {"id": "old", "name": "旧对话", "created": 1.0, "updated": 10.0, "workdir": ""},
        {"id": "new", "name": "新对话", "created": 2.0, "updated": 99.0,
         "workdir": "C:/work"},
    ]
    (home / "agent" / "sessions" / "sessions.json").write_text(
        json.dumps(sessions, ensure_ascii=False), encoding="utf-8")
    (home / "agent" / "sessions" / "new.ui.json").write_text(json.dumps({
        "rows": [
            {"type": "user", "text": "你好"},
            {"type": "ai", "cost": 1.5, "meta": "10:00 → 10:01", "segs": [
                {"type": "think", "html": "先看上下文"},
                {"type": "op", "name": "read_file", "html": "▎read_file",
                 "tip": "读文件", "out": "ok"},
                {"type": "text", "raw": "完成"},
                {"type": "result", "html": "done", "cmd": "ls"},
                {"type": "unknown_kind", "html": "x"},
            ]},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    return home


def test_theme_payload_shape():
    """`GET /api/theme`：色板 + 壁纸两块都要在，色板必须能直接当 CSS 变量用。"""
    payload = server.theme_payload()
    assert set(payload) == {"palette", "wallpaper"}
    # 色板来自桌面端模块常量，不允许为空
    assert payload["palette"]["text"] and payload["palette"]["accent"]
    for name, value in payload["palette"].items():
        assert re.fullmatch(r"#[0-9a-fA-F]{6}", value), f"{name}={value!r}"


def test_theme_palette_comes_from_desktop_constants():
    """色板必须等于桌面端那批模块常量的**实时**结果（不能是另写一份配色）。"""
    ap = server._agent_panel()
    got = server.theme_payload()["palette"]
    assert got["text"] == ap.TEXT
    assert got["accent"] == ap.ACCENT
    assert got["bg"] == ap.BG


def test_sessions_payload_is_sorted_by_updated(fake_home):
    lst = server.sessions_payload()
    assert [s["id"] for s in lst] == ["new", "old"]
    assert lst[0]["name"] == "新对话" and lst[0]["workdir"] == "C:/work"
    # 字段齐全（前端直接用）
    assert set(lst[0]) == {"id", "name", "created", "updated", "workdir"}


def test_sessions_payload_tolerates_broken_file(fake_home):
    (server.sessions_dir() / "sessions.json").write_text("{ not json", encoding="utf-8")
    assert server.sessions_payload() == []


def test_session_payload_normalizes_every_segment_kind(fake_home):
    payload = server.session_payload("new")
    assert payload["name"] == "新对话" and payload["workdir"] == "C:/work"
    assert [m["role"] for m in payload["messages"]] == ["user", "ai"]
    assert payload["messages"][0]["text"] == "你好"
    ai = payload["messages"][1]
    assert ai["cost"] == 1.5 and ai["meta"] == "10:00 → 10:01"
    kinds = [s["kind"] for s in ai["segs"]]
    assert kinds == ["think", "op", "text", "result", "other"]
    text_seg = ai["segs"][2]
    assert text_seg["html"] == "完成", "回复正文取自 raw 字段"
    assert ai["segs"][1]["name"] == "read_file" and ai["segs"][1]["out"] == "ok"


def test_session_payload_rejects_path_traversal(fake_home):
    for sid in ("../secret", "..", "a/b", "a\\b"):
        assert server.session_payload(sid) is None, sid
    assert server.session_payload("nope") is None


def test_http_routes_answer_json(fake_home, monkeypatch):
    """/api/* 必须真的能通（用真实 socket 起一次，避免只测函数漏掉 HTTP 层）。"""
    import threading
    import urllib.request

    httpd = server.serve("127.0.0.1", 0)          # 0 = 让系统分配空闲端口
    host, port = httpd.server_address[:2]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        base = f"http://{host}:{port}"
        for path, kind in (("/api/health", dict), ("/api/theme", dict),
                           ("/api/sessions", list)):
            with urllib.request.urlopen(base + path) as r:
                assert r.status == 200
                assert r.headers["Access-Control-Allow-Origin"] == "*"
                assert isinstance(json.loads(r.read().decode("utf-8")), kind), path
        with urllib.request.urlopen(base + "/api/session/new") as r:
            assert json.loads(r.read().decode("utf-8"))["id"] == "new"
        try:
            urllib.request.urlopen(base + "/api/session/missing")
            assert False, "不存在的会话应回 404"
        except urllib.error.HTTPError as e:
            assert e.code == 404
        try:
            urllib.request.urlopen(base + "/api/nope")
            assert False, "未知路径应回 404"
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_corrupt_wallpaper_is_treated_as_missing(fake_home, tmp_path):
    """损坏/被截断的背景图必须判成「没有壁纸」，**不能让进程崩掉**。

    回归：`QPixmap(坏图)` 在部分进程状态下会直接终止进程
    （libpng 报 bad header 后触发 STATUS_STACK_BUFFER_OVERRUN）—— 实测写这条用例时就崩过。
    现在 `_source_pixmap` 先用 `QImage` 试解码，坏图只返回空图。
    """
    w = server._app_wallpaper()
    saved = w.params()
    bad = tmp_path / "broken.png"
    bad.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)      # 只有签名，不是合法 PNG
    try:
        w.set_fields(persist=False, bg_image=str(bad))
        assert w.active() is False
        assert server.wallpaper_info() is None
        assert server.wallpaper_file() is None
    finally:
        w.set_params(saved, persist=False)


def test_wallpaper_route_serves_the_real_file(tmp_path):
    """壁纸路由要按原图字节转发（前端整页背景靠它）。"""
    import threading
    import urllib.request

    from PyQt6.QtGui import QColor, QImage

    w = server._app_wallpaper()
    saved = w.params()
    img = tmp_path / "w.png"
    real = QImage(16, 12, QImage.Format.Format_ARGB32)
    real.fill(QColor("#336699"))
    assert real.save(str(img)), "测试夹具必须写出一张真正能解码的图"
    try:
        w.set_fields(persist=False, bg_image=str(img))
        info = server.wallpaper_info()
        assert info and info["name"] == "w.png" and info["fit"]
        assert info["url"].startswith("/api/wallpaper?v=")
        httpd = server.serve("127.0.0.1", 0)
        host, port = httpd.server_address[:2]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            with urllib.request.urlopen(f"http://{host}:{port}/api/wallpaper") as r:
                assert r.status == 200
                assert r.headers["Content-Type"].startswith("image/")
                assert r.read() == img.read_bytes()
        finally:
            httpd.shutdown()
            httpd.server_close()
    finally:
        w.set_params(saved, persist=False)
