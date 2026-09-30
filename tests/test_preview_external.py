# -*- coding: utf-8 -*-
"""可视化预览：必须送到**用户自己的浏览器**，并且能刷新。

用户要求：
  · 是否打开浏览器展示**由模型按任务内容判断**，禁止用本地关键词表预判；
  · 必须打开用户外部浏览器（而不是只在应用内的面板里看）；
  · agent 每完成一步可调用工具刷新，或程序自动刷新。

守护契约：
  A. 本地托管端点可用：/ 给出预览页面（内含版本轮询脚本）、/v 给出内容版本号；
  B. 版本号变化 = 用户浏览器里的页面会自刷新；托管源文件被改写时程序侧自动跟随；
  C. 只在托管源变化时自动刷新（无关文件的变化不得打扰用户正在看的页面）；
  D. 预览工具已注册且恒可用；提示词要求模型自行判断并调用。
"""
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest                                                        # noqa: E402

from zhuzhu_Copilot.core import agent_engine, agent_preview, agent_tools  # noqa: E402

HTML_A = "<html><body><h1>预览A</h1></body></html>"
HTML_B = "<html><body><h1>预览B</h1></body></html>"


@pytest.fixture()
def hub(monkeypatch):
    """每个用例换一个全新 Hub（注入到模块单例的位置），用完关掉服务。

    预览状态在模块里是单例（进程级），直接复用会串味；换实例同时覆盖了
    「服务端绑定具体 Hub」这一契约。
    """
    h = agent_preview.PreviewHub()
    monkeypatch.setattr(agent_preview, "_HUB", h)
    agent_preview._OPENED_URL = ""
    yield h
    h.shutdown()
    agent_preview._OPENED_URL = ""


def _get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.read().decode("utf-8")


# ---------- A. 托管端点 ----------
def test_hub_serves_page_and_version(hub):
    hub.set_html(HTML_A)
    port = hub.ensure_server()
    assert port, "本地预览服务未能启动"
    url = hub.url()
    assert url.startswith("http://127.0.0.1:"), "预览必须只绑定回环地址"

    page = _get(url)
    assert "预览A" in page
    assert f"setInterval(tick,{agent_preview.PREVIEW_POLL_MS})" in page, \
        "页面缺少版本轮询脚本 → 用户在浏览器里看到的永远是旧内容"

    v1 = int(_get(url + "v"))
    hub.bump()
    assert int(_get(url + "v")) == v1 + 1, "版本号未随刷新递增"


def test_hub_without_content_serves_placeholder(hub):
    assert hub.ensure_server()
    assert "还没有可预览的内容" in _get(hub.url())
    assert hub.has_content() is False


# ---------- B / C. 源文件跟随与自动刷新边界 ----------
def test_source_change_marks_and_bumps_version(hub, tmp_path):
    src = tmp_path / "page.html"
    src.write_text(HTML_A, encoding="utf-8")
    ok, msg = hub.set_source(str(src))
    assert ok, msg
    assert "预览A" in hub.page()

    v1 = hub.version
    assert agent_preview.notify_source_changed(str(src)) is False, \
        "文件没变化时不得刷新（否则只是读一次文件也会打扰用户正在看的页面）"
    assert hub.version == v1

    src.write_text(HTML_B, encoding="utf-8")            # AI 重写了产物
    assert agent_preview.notify_source_changed(str(src)) is True
    assert hub.version == v1 + 1, "源文件变化必须 bump 版本（页面据此自刷新）"
    assert "预览B" in hub.page()

    other = tmp_path / "other.html"
    other.write_text(HTML_A, encoding="utf-8")
    assert agent_preview.notify_source_changed(str(other)) is False, \
        "只有托管源的变化才该刷新，别的文件不能让用户眼前的页面跳走"


def test_source_change_detected_when_mtime_and_size_equal(hub, tmp_path):
    """等长改写 + mtime 未变也必须算「变化」（判定必须以内容为准）。

    CI 实测：HTML_A / HTML_B 字节数完全相同，两次写入又落在同一 mtime 刻度
    （Windows 文件时间的时钟刻度约 15.6ms）→ 只看 mtime+size 会被判成「没变」，
    表现就是「AI 改完产物，用户浏览器里还是旧的」。这里把时间戳显式还原到改写前，
    锁住「内容变了就必须刷新」。
    """
    src = tmp_path / "page.html"
    src.write_text(HTML_A, encoding="utf-8")
    ok, msg = hub.set_source(str(src))
    assert ok, msg
    st = src.stat()
    v1 = hub.version

    src.write_text(HTML_B, encoding="utf-8")
    os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns))      # 时间戳保持改写前
    assert src.stat().st_size == st.st_size, "本例前提：改写前后字节数相同（仅内容变）"

    assert agent_preview.notify_source_changed(str(src)) is True, \
        "内容变了却没刷新（mtime+size 相同被误判为没变）"
    assert hub.version == v1 + 1
    assert "预览B" in hub.page()


# ---------- 打开与刷新（浏览器启动被替换，测试不真的拉起浏览器）----------
def test_open_preview_requires_content(hub):
    ok, msg = agent_preview.open_preview()
    assert ok is False and "没有可预览的内容" in msg


def test_open_preview_and_refresh_use_external_browser(hub, tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(agent_preview, "_open_external",
                        lambda url: (opened.append(url) or (True, "")))
    src = tmp_path / "page.html"
    src.write_text(HTML_A, encoding="utf-8")

    ok, msg = agent_preview.open_preview(path=str(src))
    assert ok and opened and opened[-1].startswith("http://127.0.0.1:"), msg
    assert agent_preview.is_open() is True

    v1 = hub.version
    ok, msg = agent_preview.refresh_preview()
    assert ok and hub.version == v1 + 1, msg


def test_refresh_without_open_reports_clearly(hub):
    ok, msg = agent_preview.refresh_preview()
    assert ok is False and "没有已打开的预览" in msg


def test_open_preview_accepts_plain_url(hub, monkeypatch):
    opened = []
    monkeypatch.setattr(agent_preview, "_open_external",
                        lambda url: (opened.append(url) or (True, "")))
    ok, _msg = agent_preview.open_preview(url="https://example.com/demo")
    assert ok and opened == ["https://example.com/demo"], "网址预览应直接交给外部浏览器"


# ---------- D. 工具与提示词 ----------
def test_preview_tools_registered_and_always_available():
    names = {t["function"]["name"] for t in agent_tools.TOOLS}
    assert {"preview_open", "preview_refresh"} <= names
    assert {"preview_open", "preview_refresh"} <= agent_engine._CORE_TOOLS, \
        "可视化预览工具必须恒可用（任务类别裁剪不得把它裁掉）"


def test_preview_tool_returns_real_result(hub, tmp_path, monkeypatch):
    monkeypatch.setattr(agent_preview, "_open_external", lambda url: (True, ""))
    src = tmp_path / "page.html"
    src.write_text(HTML_A, encoding="utf-8")
    res = agent_tools.execute_tool("preview_open", {"path": str(src)})
    assert "已在你的浏览器打开预览" in res["text"]
    res2 = agent_tools.execute_tool("preview_refresh", {})
    assert "已刷新浏览器中的预览" in res2["text"]
