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


# ---------------- 启动时序：指南先于面板 ----------------

def _main_src() -> str:
    return (Path(__file__).resolve().parents[1] / "src" / "main.py").read_text(encoding="utf-8")


def test_startup_order_is_splash_then_guide_then_panel():
    """启动顺序钉死为「启动动画 → 新手指南 → 主面板」。

    此前是面板先弹、指南再盖上去，两个窗口一并出现在屏幕上，主次混乱。
    """
    block = _main_src().split("def _close_splash_then_open_panel")[1]
    assert "splash.finish_and_close()" in block, "应先关闭启动动画窗口"
    assert block.index("_show_onboarding_first(app)") < block.index("_open_panel()"), \
        "必须先把新手指南走完，再打开 AI 面板"


def test_show_onboarding_first_runs_guide_and_marks_done(monkeypatch):
    """首次运行：指南先单独走完并立刻落标记（面板的自动弹出判定随之落空，不会二次弹）。"""
    import main

    events = []

    class _StubDlg:
        _theme_changed = False

        def exec(self):
            events.append("guide")
            return 1

    monkeypatch.setattr(onboarding, "is_first_run", lambda: True)
    monkeypatch.setattr(onboarding, "build_wizard", lambda parent=None: _StubDlg())
    monkeypatch.setattr(onboarding, "mark_first_run_done", lambda: events.append("mark"))

    main._show_onboarding_first(None)

    assert events == ["guide", "mark"], f"应先弹指南、结束后立刻落标记，实际 {events}"


def test_show_onboarding_first_skips_existing_user(monkeypatch):
    """非首次运行不得再弹指南（老用户升级 / 已看过）。"""
    import main

    built = []
    monkeypatch.setattr(onboarding, "is_first_run", lambda: False)
    monkeypatch.setattr(onboarding, "build_wizard", lambda parent=None: built.append(1))

    main._show_onboarding_first(None)

    assert built == [], "非首次运行不应构造指南"


# ---------------- 卡片外观 ----------------

def test_guide_line_cards_have_no_border():
    """引导页的文字卡片不描边。

    用户反馈：每行说明各套一层边框，整页被切得很碎、影响美观。
    """
    from PyQt6.QtWidgets import QWidget
    # 父控件必须留引用：临时 QWidget() 被回收时会连带销毁子卡片
    parent = QWidget()
    card = onboarding._build_card(parent, "标题", ["一行说明"])
    qss = card.styleSheet()
    assert "border" not in qss.replace("border-radius", ""), f"卡片不应有描边：{qss}"


# ---------------- 翻页动画（上一步 / 下一步） ----------------

def _shown_wizard():
    w = onboarding.OnboardingWizard()
    w.resize(760, 560)
    w.show()
    _pump(100)                      # 让布局生效，舞台拿到真实尺寸
    return w


def _pos_anim(wizard):
    """翻页动画第一段里的位移动画（_segment 里最先加入的就是 pos）。"""
    return wizard._page_anim.animationAt(0).animationAt(0)


def test_transition_duration_comes_from_constant():
    """时长取自 _PAGE_ANIM_MS（调快慢只改这一个常量），不得写死。"""
    w = _shown_wizard()
    try:
        w._on_next()
        assert _pos_anim(w).duration() == onboarding.OnboardingWizard._PAGE_ANIM_MS, \
            "翻页时长必须引用 _PAGE_ANIM_MS"
        assert onboarding.OnboardingWizard._PAGE_ANIM_MS >= 200, \
            "翻页过快会看不清滑动与模糊过程"
    finally:
        w.close()


def test_next_transition_slides_left_out():
    """下一步：旧页向左滑出（模糊递增、淡出），播完后新页归位、特效全部复位。"""
    w = _shown_wizard()
    try:
        assert w._idx == 0
        assert w._stage.width() > 100, "舞台未拿到尺寸，位移会退化成 1px"
        w._on_next()
        # 导航立即响应（不等动画）
        assert w._idx == 1
        assert w._back_btn.isVisible(), "第二步应出现「上一步」"
        assert w._page_anim.state().name == "Running", "应开始播放翻页动画"
        assert _pos_anim(w).endValue().x() < 0, "下一步应向左滑出"
        assert w._stage.blur_effect.blurRadius() == 0.0, "起始应为清晰"

        _pump(900)                  # 两段动画跑完
        assert w._page_anim.state().name != "Running"
        assert w._stack.currentIndex() == 1, "换页应发生在滑出与滑入之间"
        assert (w._stack.x(), w._stack.y()) == (0, 0), "动画结束后内容必须归位"
        assert w._stage.blur_effect.blurRadius() == 0.0, "结束后应恢复清晰"
        assert abs(w._stage.opacity_effect.opacity() - 1.0) < 1e-6, "结束后应恢复不透明"
    finally:
        w.close()


def test_back_transition_slides_right_out():
    """上一步：滑动方向相反（向右滑出），且同样能正确换页。"""
    w = _shown_wizard()
    try:
        w._on_next()
        _pump(900)
        assert w._idx == 1

        w._on_back()
        assert w._idx == 0
        assert not w._back_btn.isVisible(), "回到第一步应隐藏「上一步」"
        assert _pos_anim(w).endValue().x() > 0, "上一步应向右滑出"

        _pump(900)
        assert w._stack.currentIndex() == 0
        assert (w._stack.x(), w._stack.y()) == (0, 0)
        assert w._stage.blur_effect.blurRadius() == 0.0
    finally:
        w.close()


def test_rapid_clicks_do_not_leave_residue():
    """连点「下一步」：旧动画被终止，最终仍停在正确页且特效复位。"""
    w = _shown_wizard()
    try:
        w._on_next()
        w._on_next()                # 动画播放中就再点一次
        assert w._idx == 2
        _pump(1200)
        assert w._stack.currentIndex() == 2
        assert (w._stack.x(), w._stack.y()) == (0, 0)
        assert w._stage.blur_effect.blurRadius() == 0.0
        assert abs(w._stage.opacity_effect.opacity() - 1.0) < 1e-6
    finally:
        w.close()


def test_effects_are_layered_for_fade_and_blur():
    """淡出与模糊必须分挂两层：一个控件只能有一个 QGraphicsEffect。

    若两者挂到同一个控件上，Qt 会静默丢弃前一个 —— 表现就是「只有模糊没有渐变」。
    """
    w = _shown_wizard()
    try:
        from PyQt6.QtWidgets import QGraphicsBlurEffect, QGraphicsOpacityEffect
        outer, inner = w._stage.graphicsEffect(), w._stack.graphicsEffect()
        assert isinstance(outer, QGraphicsOpacityEffect), "舞台外层应挂不透明度"
        assert isinstance(inner, QGraphicsBlurEffect), "内层应挂模糊"
        assert outer is not inner, "不透明度与模糊不能挂在同一控件"
    finally:
        w.close()
