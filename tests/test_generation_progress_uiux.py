# -*- coding: utf-8 -*-
"""弹窗崩溃回归 + UI/UX/插件生成工具与实时进度的守护契约。

背景（用户报告的真实缺陷）：设置页三个入口——「自然语言生成插件」「编辑 agent」
「生成 UI/UX」——弹出的多行输入框 `_MultiLineInputDialog` 构造即抛
    AttributeError: '_MultiLineInputDialog' object has no attribute '_DIM'
（复制粘贴遗留：误把设置对话框 `_AgentSettingsDialog` 的 self._DIM / self._open_guide
写进了多行输入框）。三处入口都经 `_MultiLineInputDialog.get()` → 全部弹窗即崩。

同时沉淀本任务新增能力的契约：
  · set_app_background / set_generation_progress 两个工具已登记（真实实现，非 mock）；
  · set_generation_progress 把 (percent, message) 规范化为「生成进度:<pct>:<msg>」；
  · 生成回调适配把 on_status(pct, msg) 转成同一前缀，与面板 _on_status 分支对齐；
  · 任务裁剪保护：进度工具恒可用，生成类工具属 cordis 分组不被剔除。
"""
import inspect
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PyQt6.QtWidgets import QApplication                                   # noqa: E402

_app = QApplication.instance() or QApplication([])

from zhuzhu_Copilot.core import agent_engine, agent_tools                  # noqa: E402
from zhuzhu_Copilot.ui import agent_panel as ap                            # noqa: E402

PREFIX = "生成进度:"


# ---------------- A. 弹窗崩溃回归 ----------------

def test_multiline_input_dialog_constructs():
    """回归：三个设置入口共用的多行输入框必须能正常构造（曾被 _DIM 打崩）。"""
    dlg = ap._MultiLineInputDialog("标题", "说明", "初始文本")
    try:
        assert dlg.get_value() == "初始文本"
    finally:
        dlg.close()
        dlg.deleteLater()


def test_multiline_input_dialog_has_no_settings_only_attrs():
    """多行输入框源码不得再引用设置页专有属性（崩因）。"""
    src = inspect.getsource(ap._MultiLineInputDialog)
    assert "self._DIM" not in src
    assert "self._open_guide" not in src


def test_settings_dialog_keeps_guide_entry():
    """「重新查看新手指南」入口仍保留在设置对话框（其内部真实定义了 _DIM/_open_guide）。"""
    src = inspect.getsource(ap._AgentSettingsDialog)
    assert "重新查看新手指南" in src
    assert "def _open_guide" in src


# ---------------- B. 工具 schema 契约 ----------------

def _tool_names() -> set:
    return {t.get("function", {}).get("name") for t in agent_tools.TOOLS}


def test_generation_tools_registered():
    names = _tool_names()
    assert "set_app_background" in names
    assert "set_generation_progress" in names


# ---------------- C. 进度上报契约 ----------------

def test_set_generation_progress_reports_via_status_cb():
    seen = []
    out = agent_tools._set_generation_progress(
        {"percent": 70, "message": "正在写入文件"}, status_cb=seen.append)
    assert seen == [f"{PREFIX}70:正在写入文件"]
    assert "70%" in out.get("text", "")


def test_set_generation_progress_clamps_out_of_range():
    seen = []
    agent_tools._set_generation_progress({"percent": 250}, status_cb=seen.append)
    agent_tools._set_generation_progress({"percent": -5}, status_cb=seen.append)
    assert seen == [f"{PREFIX}100:", f"{PREFIX}0:"]


def test_set_generation_progress_rejects_non_numeric():
    out = agent_tools._set_generation_progress({"percent": "abc"})
    assert "percent" in out.get("text", "")


def test_progress_prefix_matches_panel_branch():
    """上报前缀必须与面板 _on_status 的分支前缀严格一致，否则进度不会显示。"""
    src = inspect.getsource(ap.AgentPanel._on_status)
    assert f's.startswith("{PREFIX}")' in src


def test_generation_status_adapter_none_without_cb():
    assert agent_tools._generation_status_adapter(None) is None


def test_generation_status_adapter_formats_pct_and_msg():
    seen = []
    rep = agent_tools._generation_status_adapter(seen.append)
    rep(30, " 设计主题 ")
    rep("70", None)
    assert seen == [f"{PREFIX}30:设计主题", f"{PREFIX}70:"]


def test_set_app_background_requires_image():
    """op=set 缺图直接拒绝，绝不发起网络/落盘副作用。"""
    out = agent_tools._set_app_background({"op": "set"})
    assert "image" in out.get("text", "")


def test_set_app_background_rejects_non_http_url():
    """url 仅允许 http/https：file:// 之类必须被挡下。"""
    out = agent_tools._set_app_background({"op": "set", "url": "file:///C:/x.png"})
    assert "http" in out.get("text", "")


def test_set_app_background_params_require_values():
    """op=params 不带任何参数时应给出可用参数清单，而不是静默成功。"""
    out = agent_tools._set_app_background({"op": "params"})
    assert "blur" in out.get("text", "")


# ---------------- D. 任务裁剪保护 ----------------

def test_progress_tool_always_available():
    assert "set_generation_progress" in agent_engine._CORE_TOOLS


def test_cordis_group_keeps_generation_tools():
    tools = agent_engine._TASK_GROUP_TOOLS.get("cordis", frozenset())
    for name in ("create_uiux", "manage_uiux", "create_plugin"):
        assert name in tools, f"cordis 分组缺少 {name}，自定义任务裁剪后会丢失"
