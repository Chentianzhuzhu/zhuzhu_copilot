# -*- coding: utf-8 -*-
"""首次安装新手指南弹出的回归测试。

真实缺陷（用户报「首次安装打开程序新手指南无法弹出」）：
  「已看过指南」标记此前只写注册表 `HKCU\\Software\\zhuzhu_Copilot\\zhuzhu_Copilot`
  的 `agent_first_run_done`，而卸载流程只清用户数据目录 `%USERPROFILE%\\.zhuzhu_Copilot`
  （见 installer/zhuzhu_Copilot.iss）——注册表残留使「卸载 → 重新安装」被判定为
  「已看过」，指南从此永不弹出（已用安装版程序实测：清掉该标记即可正常弹出）。

修法：标记改存用户数据目录内的文件（随卸载一起删除），并以「进程启动时数据目录是否存在」
区分「全新安装（含卸载重装）」与「老用户升级」；同时保证判定失败不静默、showEvent
调度不依赖方法尾部。本文件锁住这几条行为。
"""
import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest                                       # noqa: E402
from PyQt6.QtWidgets import QApplication            # noqa: E402

from zhuzhu_Copilot import app_identity            # noqa: E402
from zhuzhu_Copilot.ui import onboarding           # noqa: E402

_app = QApplication.instance() or QApplication([])

# 在 conftest 的会话级打桩（把 _maybe_show_onboarding 换成 no-op）之前抓住原函数
from zhuzhu_Copilot.ui.agent_panel import AgentPanel  # noqa: E402

_MAYBE_SHOW = AgentPanel._maybe_show_onboarding
_IS_REAL = getattr(getattr(_MAYBE_SHOW, "__code__", None), "co_argcount", 0) >= 2


def _pump(ms: int):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        _app.processEvents()
        time.sleep(0.005)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """数据目录指向临时目录，并屏蔽真实注册表读写。"""
    data_root = tmp_path / app_identity.DATA_DIR_NAME
    monkeypatch.setattr(onboarding, "_DATA_ROOT", data_root)
    state = {"legacy_done": False, "writes": 0}

    class _StubSettings:
        def __init__(self, *a, **k):
            pass

        def contains(self, key):
            return state["legacy_done"]

        def value(self, key, default=""):
            return "1" if state["legacy_done"] else default

        def setValue(self, key, val):
            state["legacy_done"] = str(val) == "1"
            state["writes"] += 1

    # 注册表访问统一经 app_identity.qsettings()，此处整体打桩
    monkeypatch.setattr(app_identity, "qsettings", _StubSettings)
    return data_root, state


# ---------------- 判定逻辑 ----------------

def test_fresh_install_is_first_run(home):
    """全新安装（启动时数据目录不存在、无任何标记）→ 判定为首次，指南应弹出。"""
    onboarding._startup_state["data_dir_existed"] = False
    assert onboarding.is_first_run() is True


def test_marker_file_lives_in_user_data_dir(home):
    """标记写进用户数据目录（卸载器删除该目录即等于清标记），而非只写注册表。"""
    data_root, _ = home
    onboarding._startup_state["data_dir_existed"] = False
    onboarding.mark_first_run_done()
    marker = data_root / "agent" / "onboarding_done"
    assert marker.exists()
    # 数据目录仍在（未卸载）→ 不再重复弹
    assert onboarding.is_first_run() is False
    # 模拟卸载：数据目录被删除 → 判定重新回到「首次」，重装后指南会再弹一次
    import shutil
    shutil.rmtree(data_root)
    onboarding._startup_state["data_dir_existed"] = False
    assert onboarding.is_first_run() is True


def test_reinstall_with_stale_registry_marker_is_first_run(home):
    """本 bug 的核心场景：注册表残留「已看过」+ 数据目录为全新（卸载重装）→ 首次。"""
    _, state = home
    state["legacy_done"] = True                     # 旧版遗留标记
    onboarding._startup_state["data_dir_existed"] = False   # 启动时无用户数据目录
    assert onboarding.is_first_run() is True


def test_upgrade_for_existing_user_keeps_quiet(home):
    """老用户升级（数据目录早已存在 + 旧版标记）→ 不打扰，并补齐标记文件。"""
    data_root, state = home
    state["legacy_done"] = True
    onboarding._startup_state["data_dir_existed"] = True
    assert onboarding.is_first_run() is False
    assert (data_root / "agent" / "onboarding_done").exists()


def test_no_marker_and_no_legacy_is_first_run(home):
    """无标记文件、无旧版标记（数据目录存在）→ 首次。"""
    onboarding._startup_state["data_dir_existed"] = True
    assert onboarding.is_first_run() is True


# ---------------- 弹出触发 ----------------

@pytest.mark.skipif(not _IS_REAL, reason="_maybe_show_onboarding 已被测试桩替换")
def test_judgement_failure_retries_and_logs(home, monkeypatch, capsys):
    """判定失败不允许静默放弃：必须留痕并退避重试，最终弹出。

    说明：这里把「重试调度」替换为同步调用（不引入真实定时器）——
    PyQt6 的 QTimer 回调在本测试进程退出时会被已知的 Qt 析构顺序问题带崩
    （0xC0000409，见 conftest 顶部说明），故不依赖真实定时器计时。
    """
    calls = {"n": 0}

    def _flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("模拟判定失败")
        return True

    monkeypatch.setattr(onboarding, "is_first_run", _flaky)
    opened = []

    class _Stub:
        # 复用真实方法（重试回调会自我调度），仅替换 _open_onboarding 记录是否弹出
        _maybe_show_onboarding = AgentPanel._maybe_show_onboarding

        def _open_onboarding(self):
            opened.append(1)

        def _schedule_onboarding_retry(self, attempt):
            # 同步重试：模拟定时器到点后再次判定
            _MAYBE_SHOW(self, attempt)

    _MAYBE_SHOW(_Stub())              # 第 1 次失败 → 调度重试（同步执行到成功）
    assert opened == [1], "判定连续失败后必须重试并最终弹出新手指南"
    assert calls["n"] >= 3
    assert "首次运行判定失败" in capsys.readouterr().out


@pytest.mark.skipif(not _IS_REAL, reason="_maybe_show_onboarding 已被测试桩替换")
def test_already_opened_is_not_reopened(home, monkeypatch):
    """自动弹出只做一次（设置里「重新查看」走 _open_onboarding，不受此限制）。"""
    monkeypatch.setattr(onboarding, "is_first_run", lambda: True)
    opened = []

    class _Stub:
        _maybe_show_onboarding = AgentPanel._maybe_show_onboarding

        def _open_onboarding(self):
            opened.append(1)

    one = _Stub()
    _MAYBE_SHOW(one)
    _MAYBE_SHOW(one)
    assert opened == [1]


def test_onboarding_scheduled_at_show_event_head():
    """调度必须位于 AgentPanel.showEvent 开头。

    此前它在 showEvent 末尾（前面是 DWM 圆角/任务栏图标/停靠面板同步/管理员拖放等原生调用），
    任一环节抛异常，引导调度就被跳过 → 安装后指南静默不弹。
    """
    src = (Path(__file__).resolve().parents[1] / "src" / "zhuzhu_Copilot" / "ui"
           / "agent_panel.py").read_text(encoding="utf-8")
    needle = "QTimer.singleShot(650, self._maybe_show_onboarding)"
    lines = src.splitlines()
    assert src.count(needle) == 1
    idx = next(i for i, ln in enumerate(lines) if needle in ln)
    head = max(i for i, ln in enumerate(lines[:idx]) if ln.startswith("    def showEvent(self, e):"))
    assert idx - head <= 10, "新手指南调度应放在 showEvent 开头，不能放在方法尾部"
    tail = next(i for i, ln in enumerate(lines) if i > idx and "_apply_window_round()" in ln)
    assert idx < tail, "调度须先于 showEvent 中的原生初始化调用"
