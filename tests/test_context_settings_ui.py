"""上下文设置 UI 回归：服务商声明持久化 + 模型接入页 1M 开关与来源说明。

覆盖：
- `_ProviderDialog.provider_data()` 把「上游声明的上下文能力」写进 provider（只保留当前
  模型列表内的条目，避免列表换掉后残留无意义声明）；
- 「声明的上下文」只读行随模型列表变化同步；
- 模型接入页的窗口说明与实际引擎走同一条决策链（1M 开关 / 上游声明 / 手填 / 内置表 / 推断）；
- 接线守卫：1M 开关写入配置、引擎创建时消费 context 决策（源码级断言，防改坏键名）。
"""
import inspect
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PyQt6.QtWidgets import QApplication, QCheckBox

_app = QApplication.instance() or QApplication([])

from zhuzhu_Copilot.core import agent_engine, agent_llm
from zhuzhu_Copilot.ui import agent_panel as ap


# ---------- 1. 服务商声明持久化 ----------

def test_provider_dialog_persists_declared_limits():
    """声明随服务商配置保存；模型列表之外的声明不写入（列表变更后不残留）"""
    dlg = ap._ProviderDialog(provider={
        "name": "P", "base_url": "https://api.deepseek.com/v1", "api_key": "k",
        "models": ["declared-200k"],
        "model_limits": {"declared-200k": {"context_window": 200000,
                                           "max_output_tokens": 64000},
                         "stale-model": {"context_window": 100000}},
    })
    data = dlg.provider_data()
    assert set(data["model_limits"]) == {"declared-200k"}
    assert data["model_limits"]["declared-200k"]["context_window"] == 200000

    # 上游拉到新声明 + 列表变化 → 只读行同步反映匹配结果
    dlg._declarations["vllm-style"] = {"context_window": 32768, "max_output_tokens": 0}
    dlg.models_edit.setText("declared-200k, vllm-style")
    assert "declared-200k" in dlg.declared_lbl.text()
    assert "vllm-style" in dlg.declared_lbl.text()
    dlg.close()


def test_provider_dialog_hint_when_no_declaration():
    """上游未声明时给出可操作提示（而不是空白）"""
    dlg = ap._ProviderDialog(provider={"name": "P", "base_url": "https://x.local",
                                       "api_key": "k", "models": ["m1"]})
    text = dlg.declared_lbl.text()
    assert "未声明" in text and "从上游获取" in text
    dlg.close()


# ---------- 2. 模型接入页：1M 开关与来源说明 ----------

class _SettingsProxy:
    """只带 _context_hint_text 需要的属性的轻代理（避免构建整页设置界面）"""

    def __init__(self, providers, checked=False):
        self._providers = providers
        self.context_1m_check = QCheckBox()
        self.context_1m_check.setChecked(checked)


def test_settings_hint_follows_priority_chain():
    """说明文案与实际引擎同源：无声明→内置表/推断；有声明→上游；开 1M→1M 开关"""
    hint = ap._AgentSettingsDialog._context_hint_text

    known = hint(_SettingsProxy([{"base_url": "https://api.deepseek.com/v1",
                                  "models": ["deepseek-chat"]}]))
    assert "内置已知服务商表" in known
    assert ap._fmt_tokens(131072) in known and "75%" in known
    assert "输入+输出" in known

    inferred = hint(_SettingsProxy([{"base_url": "https://self-hosted.local/v1",
                                     "models": ["mystery"]}]))
    assert "模型名推断" in inferred

    upstream = hint(_SettingsProxy([{"base_url": "https://x.local/v1", "models": ["m1"],
                                     "model_limits": {"m1": {"context_window": 200000,
                                                             "max_output_tokens": 64000}}}]))
    assert "上游服务商声明" in upstream
    assert ap._fmt_tokens(200000) in upstream
    assert ap._fmt_tokens(200000 - 64000) in upstream      # 可用输入预算

    one_m = hint(_SettingsProxy([{"base_url": "https://x.local/v1", "models": ["m1"]}],
                                checked=True))
    assert "1M 开关" in one_m
    assert ap._fmt_tokens(agent_llm.ONE_M_CONTEXT) in one_m
    assert "80%" in one_m, "1M 模式压缩阈值应为 0.80"
    assert "输入+输出" in one_m


def test_settings_missing_widgets_is_silent():
    """懒加载/重建期间缺控件时必须静默（不得抛异常打断设置页构建）"""

    class _Empty:
        _providers = []

    ap._AgentSettingsDialog._sync_context_hint(_Empty())   # 不抛异常即通过
    ap._AgentSettingsDialog._sync_context_hint(object())   # 连 _providers 都没有时同样静默


# ---------- 2.5 思考强度（工作力度）8 档滑块与上游声明映射 ----------

def _model_page_dlg():
    """构建设置对话框并只构建「模型接入」页（拿到思考强度滑块等控件）；
    页面 widget 需挂在对话框上防 GC（真实流程由设置页栈持有引用）"""
    dlg = ap._AgentSettingsDialog()
    dlg._stash_page = dlg._build_model_page({"model": {}})
    return dlg


def test_effort_slider_eight_levels_chinese_labels():
    """8 档滑块（关闭/低/中/高/超高/最高/极致/超级），当前档位中文显示"""
    dlg = _model_page_dlg()
    try:
        assert dlg.effort_slider.maximum() == len(agent_llm.EFFORTS) - 1 == 7
        assert dlg.effort_slider.minimum() == 0
        # 初始 8 档刻度文案
        for lbl in ("关闭", "低", "中", "高", "超高", "最高", "极致", "超级"):
            assert lbl in dlg.effort_hint.text()
        # 拖动 → 标签变中文
        dlg.effort_slider.setValue(agent_llm.EFFORTS.index("ultra"))
        dlg._on_effort_slider()
        assert dlg._effort == "ultra" and dlg.effort_label.text() == "极致"
    finally:
        dlg.close()


def test_effort_slider_auto_maps_upstream_declared_levels():
    """上游声明力度级别 → 自动映射滑块挡位：提示声明档位，并把当前力度就近收敛"""
    dlg = _model_page_dlg()
    try:
        dlg._providers = [{"models": ["m1"]}]            # 多服务商卡片（供力度声明查找）
        dlg._effort = "extreme"                       # 当前档位不在声明内
        dlg._declarations = {"m1": {"effort_levels": ["low", "high", "max"]}}
        dlg._sync_effort_declared()
        assert dlg._declared_efforts == ["low", "high", "max"]
        assert "低 / 高 / 最高" in dlg.effort_hint.text()   # 声明档位中文提示
        assert dlg._effort == "max"                   # 就近收敛到声明内最近档
        assert dlg.effort_label.text() == "最高"
        assert dlg.effort_slider.value() == agent_llm.EFFORTS.index("max")
        # 无声明 → 回退全档位 + 按模型类型映射提示
        dlg._declarations = {}
        dlg._sync_effort_declared()
        assert dlg._declared_efforts is None
        assert "按模型类型自动映射" in dlg.effort_hint.text()
    finally:
        dlg.close()


# ---------- 3. 接线守卫（防键名/参数名改坏） ----------

def test_settings_and_engine_wiring_for_context():
    """1M 开关必须写入配置；引擎创建必须消费 context 决策（窗口/预留输出/模式）"""
    save_src = inspect.getsource(ap._AgentSettingsDialog._save)
    assert '"context_1m"' in save_src
    assert "self.context_1m_check.isChecked()" in save_src

    panel_src = inspect.getsource(ap.AgentPanel)
    assert 'context_window=cfg.get("context_window")' in panel_src
    assert 'max_output_tokens=(cfg.get("context") or {}).get("max_output")' in panel_src
    assert 'long_context_1m=bool((cfg.get("context") or {}).get("long_1m"))' in panel_src
    assert 'window_source=str((cfg.get("context") or {}).get("source") or "")' in panel_src


# ---------- 4. 上限热更新：设置保存 / 发送前路由都必须落到既有引擎 ----------

class _BudgetEngine:
    """只记录 set_context_budget 入参的假引擎"""

    def __init__(self):
        self.calls = []

    def set_context_budget(self, **kw):
        self.calls.append(kw)
        return True


def _cfg_1m():
    """load_model_config() 在 1M 开关打开时的关键字段（真跑 resolve_context 得到）"""
    provider = {"base_url": "https://api.deepseek.com/v1", "models": ["deepseek-chat"]}
    ctx = agent_llm.resolve_context(provider["base_url"], "deepseek-chat",
                                    provider=provider, long_1m=True)
    return {"providers": [provider], "context": ctx, "context_1m": True,
            "context_window": ctx["window"]}


def test_apply_context_budget_pushes_1m_to_existing_engine():
    """设置保存后既有引擎必须切到 1M 口径（引擎为保留上下文而复用，窗口需显式热更新）"""
    panel = ap.AgentPanel.__new__(ap.AgentPanel)     # 轻代理：只测预算注入，不建整页 UI
    eng = _BudgetEngine()
    panel._apply_context_budget(eng, _cfg_1m())
    assert len(eng.calls) == 1
    call = eng.calls[0]
    assert call["window"] == agent_llm.ONE_M_CONTEXT
    assert call["long_1m"] is True and call["source"] == "1m"
    assert call["max_output"] == _cfg_1m()["context"]["max_output"]

    # 再用真实引擎跑一遍：统计面板口径（窗口/来源/阈值）必须整体切到 1M
    real = agent_engine.AgentEngine(object(), text_only=True, context_window=131072,
                                    max_output_tokens=8192, window_source="known")
    panel._apply_context_budget(real, _cfg_1m())
    s = real.context_stats()
    assert s["window"] == agent_llm.ONE_M_CONTEXT, "面板显示的必须是 1M 上限"
    assert s["window_source"] == "1m" and s["long_1m"] is True
    assert s["thresholds"]["compress"] == int(s["budget"] * 0.80)
    assert s["thresholds"]["compress"] > int(131072 - 8192) * 0.75


def test_apply_context_budget_re_resolves_for_routed_model():
    """按模型路由时用该模型所属服务商的声明重新解析（多服务商窗口不同）"""
    panel = ap.AgentPanel.__new__(ap.AgentPanel)
    provider = {"name": "P2", "base_url": "https://x.local/v1",
                "models": ["m-declared"],
                "model_limits": {"m-declared": {"context_window": 400000,
                                                "max_output_tokens": 16000}}}
    cfg = _cfg_1m()
    cfg["providers"] = [provider]
    cfg["context_1m"] = False                        # 未开 1M → 用上游声明
    cfg["context"] = agent_llm.resolve_context(provider["base_url"], "m-declared",
                                               provider=provider, long_1m=False)
    eng = _BudgetEngine()
    panel._apply_context_budget(eng, cfg, model="m-declared", provider=provider)
    call = eng.calls[0]
    assert call["window"] == 400000 and call["source"] == "upstream"
    assert call["long_1m"] is False and call["max_output"] == 16000


def test_apply_context_budget_tolerates_legacy_engine():
    """老引擎/测试替身没有 set_context_budget 时必须静默跳过（不得抛异常打断设置保存）"""
    panel = ap.AgentPanel.__new__(ap.AgentPanel)
    panel._apply_context_budget(object(), _cfg_1m())          # 无该方法
    panel._apply_context_budget(None, _cfg_1m())              # 无引擎
    class _Boom:
        def set_context_budget(self, **_kw):
            raise RuntimeError("boom")
    panel._apply_context_budget(_Boom(), _cfg_1m())           # 内部异常被吞掉


def test_save_and_launch_paths_call_apply_context_budget():
    """接线守卫：设置保存与发送前路由两处都必须调用（漏一处就会出现"1M 不生效"）"""
    apply_save = inspect.getsource(ap.AgentPanel._apply_agent_settings)
    assert "_apply_context_budget(eng, cfg)" in apply_save
    launch = inspect.getsource(ap.AgentPanel._launch_task)
    assert "_apply_context_budget(engine, cfg, model=model, provider=sel)" in launch


def test_settings_1m_copy_uses_input_output_wording():
    """1M 文案统一口径：勾选框/悬浮提示/动态说明都按「输入+输出=1M上下文窗口」表述"""
    src = inspect.getsource(ap._AgentSettingsDialog)
    assert "模型支持1M上下文窗口，输入+输出=1M上下文窗口" in src   # 勾选框 + 悬浮提示
    assert "窗口由输入+输出共用" in src                          # 动态说明（_context_hint_text）


# ---------- 2.9 外观·玻璃页：不得把右侧内容区顶出可视范围 ----------

def _appearance_page_dlg():
    """构建设置对话框并构建「外观·玻璃」页（真实流程由设置页栈持有引用）"""
    dlg = ap._AgentSettingsDialog()
    dlg._stash_page = dlg._build_appearance_page()
    return dlg


def test_appearance_page_fits_dialog_width():
    """回归：外观页里「液态流动动效（持续重织，较耗 CPU，默认关闭）」这类长文案
    会把单行最小宽撑得比右侧内容区还宽 —— QScrollArea 不会小于最小宽，只能被
    裁掉，表现为右侧 UI 被挤压遮挡。现在长说明一律走 tooltip + 可换行 QLabel。"""
    dlg = _appearance_page_dlg()
    try:
        page = dlg._stash_page
        page.resize(dlg.width() - 200, 600)     # 模拟右侧内容区实际可用宽
        available = page.width()
        for w in page.findChildren(ap.QWidget):
            if not w.isVisibleTo(page) or w.width() <= 0:
                continue
            # 任何直接铺在页面里的行容器都不应要求比可用宽更多的宽度
            assert w.minimumSizeHint().width() <= available + 2, (
                f"{type(w).__name__} 最小宽 {w.minimumSizeHint().width()} "
                f"超过可用宽 {available}，右侧会被裁掉")
    finally:
        dlg.deleteLater()


def test_appearance_page_sliders_cover_all_spec_keys():
    """五个可调维度必须各有一根滑杆，且由 PARAM_SPECS 派生（内核加维度自动多滑杆）"""
    from zhuzhu_Copilot.core import app_glass
    dlg = _appearance_page_dlg()
    try:
        keys = set(dlg._glass_sliders.keys())
        assert keys == set(app_glass.param_keys()), f"滑杆与规格不一致：{keys}"
    finally:
        dlg.deleteLater()


# ---------- 2.10 壁纸根绘制：必须真的接管 ----------

def test_paint_glass_root_takes_over_when_wallpaper_set(tmp_path):
    """回归：`_paint_glass_root` 曾把 QRectF 误从 QtGui 导入 → 每次都 ImportError
    被吞掉 → 恒返回 False，壁纸从未画上屏（用户反馈"图片无法正常显示"）。"""
    from PyQt6.QtGui import QPainter
    from PyQt6.QtCore import Qt
    from zhuzhu_Copilot.core import app_glass

    img = tmp_path / "wall.png"
    pm = ap.QPixmap(64, 64)
    pm.fill(ap.QColor("#2F52D8"))
    assert pm.save(str(img), "PNG")
    app_glass.set_fields(bg_image=str(img), persist=False)

    host = ap.QWidget()
    host.resize(120, 80)
    skin = app_glass.install(host, app_glass.RADIUS_WINDOW)
    assert skin.background_blurred() is not None, "壁纸应能加载成位图"

    out = ap.QPixmap(120, 80)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    try:
        took_over = ap._paint_glass_root(host, painter, out.rect(),
                                         app_glass.RADIUS_WINDOW)
    finally:
        painter.end()
    assert took_over is True, "壁纸生效时根绘制必须被接管（否则壁纸永远不上屏）"

    # 磨砂纱必须真实画上去：接管后的画面不能等于"只有壁纸"
    bare = ap.QPixmap(120, 80)
    bare.fill(Qt.GlobalColor.transparent)
    painter = QPainter(bare)
    bg = skin.background_blurred()
    painter.drawPixmap(bare.rect(), bg, bg.rect())
    painter.end()
    assert out.toImage() != bare.toImage(), "磨砂纱没有画上去，正文会压在照片上"
    host.deleteLater()


def test_glass_root_bg_transparent_only_when_usable(tmp_path):
    """根底转透明的**前提**是壁纸真的可用；否则必须保持主题底色（避免透明黑块）。"""
    from zhuzhu_Copilot.core import app_glass
    img = tmp_path / "w.png"
    pm = ap.QPixmap(32, 32)
    pm.fill(ap.QColor("#2F52D8"))
    assert pm.save(str(img), "PNG")
    app_glass.set_fields(bg_image=str(img), persist=False)
    assert ap._glass_root_bg("#101216") == "transparent"

    junk = tmp_path / "broken.png"
    junk.write_bytes(b"broken")
    app_glass.set_fields(bg_image=str(junk), persist=False)
    assert ap._glass_root_bg("#101216") == "#101216", "坏图必须回落主题底色"
