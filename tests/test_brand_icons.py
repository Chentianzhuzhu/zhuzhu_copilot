# -*- coding: utf-8 -*-
"""品牌厂商矢量图标的回归测试。

守四条不变式：
1. 识别：10 个内置品牌在「服务商名 / 接口地址 / 模型名」三类线索下都能被识别，
   且模型名线索优先级最高（聚合平台同挂多家模型时各归各家）；
2. 绘制：每个品牌（含未知品牌的通用兜底）都真实画出非空像素，图标绝不留白；
   且各品牌图形两两不同（防止误把多个品牌画成同一个图形）；
3. 接线：模型下拉、服务商卡片、添加服务商预设下拉三处真实挂上对应图标；
4. 数据：PRESET_PROVIDERS 里声明的 brand 必须是已知品牌，且与推断结果一致。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from zhuzhu_Copilot.core import agent_llm
from zhuzhu_Copilot.ui import brand_icons as bi


# ---------- 1. 识别 ----------
def test_brand_by_name():
    cases = {
        "agnes": ["Agnes", "agnes 默认"],
        "deepseek": ["DeepSeek", "deepseek 官方"],
        "doubao": ["豆包", "火山方舟（Coding Plan）", "VolcEngine", "ark"],
        "qwen": ["千问", "通义千问", "DashScope", "Qwen"],
        "zhipu": ["智谱清言", "智谱（GLM Coding Plan）", "GLM", "bigmodel"],
        "minimax": ["MiniMax", "minimax-m3 平台", "海螺"],
        "openai": ["OpenAI", "GPT 中转", "ChatGPT"],
        "gemini": ["Gemini", "Google AI", "谷歌"],
        "grok": ["Grok", "xAI", "x.ai"],
        "claude": ["Anthropic (Claude)", "Claude", "克劳德"],
    }
    for brand, names in cases.items():
        for n in names:
            assert bi.brand_for(provider_name=n) == brand, (brand, n)


def test_brand_by_url():
    cases = {
        "agnes": ["https://api.agnes-ai.cn/v1"],
        "deepseek": ["https://api.deepseek.com/v1"],
        "doubao": ["https://ark.cn-beijing.volces.com/api/coding/v3"],
        "qwen": ["https://dashscope.aliyuncs.com/v1"],
        "zhipu": ["https://open.bigmodel.cn/api/paas/v4"],
        "minimax": ["https://api.minimax.io/v1"],
        "openai": ["https://api.openai.com/v1"],
        "gemini": ["https://generativelanguage.googleapis.com/v1beta"],
        "grok": ["https://api.x.ai/v1"],
        "claude": ["https://api.anthropic.com"],
    }
    for brand, urls in cases.items():
        for u in urls:
            assert bi.brand_for(base_url=u) == brand, (brand, u)


def test_brand_by_model():
    cases = {
        "agnes": ["agnes-2.5-flash"],
        "deepseek": ["deepseek-v4-pro", "deepseek-ai/DeepSeek-V4-Flash"],
        "doubao": ["doubao-seed-2.1-turbo", "ark-code-latest"],
        "qwen": ["qwen-max", "Qwen/Qwen3-VL-32B-Instruct"],
        "zhipu": ["glm-5.3", "zai-org/GLM-5.1", "glm-4v-plus"],
        "minimax": ["minimax-m3", "abab6.5s-chat"],
        "openai": ["gpt-5.4", "gpt-5.4-mini"],
        "gemini": ["gemini-2.5-pro"],
        "grok": ["grok-4"],
        "claude": ["claude-sonnet-4-5"],
    }
    for brand, models in cases.items():
        for m in models:
            assert bi.brand_for(model=m) == brand, (brand, m)


def test_model_cue_beats_provider_name():
    """火山方舟卡片下挂的第三方模型在下拉里按模型名归各家，而非跟平台走豆包"""
    assert bi.brand_for(model="deepseek-v4-flash",
                        provider_name="火山方舟（Coding Plan）") == "deepseek"
    assert bi.brand_for(model="glm-5.3",
                        provider_name="火山方舟（Agent Plan）") == "zhipu"
    assert bi.brand_for(model="minimax-m3",
                        provider_name="火山方舟（Coding Plan）") == "minimax"
    # 平台自有模型仍归豆包
    assert bi.brand_for(model="doubao-seed-2.1-turbo",
                        provider_name="火山方舟（通用）") == "doubao"


def test_case_insensitive_and_unknown():
    assert bi.brand_for(model="DEEPSEEK-V4") == "deepseek"
    assert bi.brand_for(provider_name="DEEPSEEK") == "deepseek"
    assert bi.brand_for(model="kimi-k3", provider_name="Kimi（月之暗面）",
                        base_url="https://api.moonshot.cn/v1") == ""
    assert bi.brand_for() == ""


def test_brand_for_provider():
    # 显式 brand 优先
    assert bi.brand_for_provider({"name": "随便起名", "brand": "grok"}) == "grok"
    # 非法显式 brand 被忽略，回落推断
    assert bi.brand_for_provider({"name": "DeepSeek", "brand": "nope"}) == "deepseek"
    # 名称 → URL → 模型名 依次兜底
    assert bi.brand_for_provider(
        {"name": "我的中转", "base_url": "https://api.agnes-ai.cn/v1"}) == "agnes"
    assert bi.brand_for_provider(
        {"name": "我的中转", "base_url": "", "models": ["gemini-2.5-pro"]}) == "gemini"
    assert bi.brand_for_provider({"name": "Kimi", "models": ["kimi-k3"]}) == ""
    assert bi.brand_for_provider(None) == ""


# ---------- 2. 绘制 ----------
def test_official_svg_registry_valid():
    """10 个品牌均有官方 SVG，源串可被 QSvgRenderer 解析，且用 currentColor 占位"""
    from PyQt6.QtCore import QByteArray
    from PyQt6.QtSvg import QSvgRenderer
    from zhuzhu_Copilot.ui import brand_svg_data

    assert set(brand_svg_data.BRAND_SVG) == bi.KNOWN_BRANDS
    for b, svg in brand_svg_data.BRAND_SVG.items():
        assert "currentColor" in svg, b
        assert svg.lstrip().startswith("<svg") and svg.rstrip().endswith("</svg>"), b
        assert QSvgRenderer(QByteArray(svg.encode("utf-8"))).isValid(), b


def _ink_ratio(brand: str, size: int = 32) -> float:
    pm = bi.brand_icon(brand, size, "#9BA3B0").pixmap(size, size)
    img = pm.toImage()
    total = img.width() * img.height()
    painted = 0
    for y in range(img.height()):
        for x in range(img.width()):
            if (img.pixelColor(x, y).alpha() or 0) > 20:
                painted += 1
    return painted / total


def test_every_glyph_paints_and_is_distinct():
    brands = sorted(bi.KNOWN_BRANDS)
    assert brands == sorted(["agnes", "deepseek", "doubao", "qwen", "zhipu",
                             "minimax", "openai", "gemini", "grok", "claude"])
    sigs = {}
    for b in brands + [""]:   # "" = 未知品牌通用兜底
        ratio = _ink_ratio(b)
        assert 0.02 < ratio < 0.6, (b, ratio)   # 既不留白也不糊满
        pm = bi.brand_icon(b, 32, "#9BA3B0").pixmap(32, 32).toImage()
        sigs[b or "__generic__"] = tuple(
            (pm.pixelColor(x, y).red(), pm.pixelColor(x, y).green(),
             pm.pixelColor(x, y).blue(), pm.pixelColor(x, y).alpha())
            for y in range(pm.height()) for x in range(pm.width()))
    # 两两图形不得完全相同
    keys = list(sigs)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            assert sigs[keys[i]] != sigs[keys[j]], (keys[i], keys[j])


def _opaque_pixel(img):
    """取笔画核心（完全不透明）像素，避开抗锯齿边缘的过渡色"""
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.alpha() == 255:
                return c
    raise AssertionError("图标内无完全不透明像素")


def test_icon_color_injected():
    """颜色由调用方注入：图标笔画核心就是传入色（模块自身不含颜色字面量）"""
    a = bi.brand_icon("grok", 32, "#FFFFFF").pixmap(32, 32).toImage()
    b = bi.brand_icon("grok", 32, "#1E40AF").pixmap(32, 32).toImage()
    assert _opaque_pixel(a).name().upper() == "#FFFFFF"
    assert _opaque_pixel(b).name().upper() == "#1E40AF"


# ---------- 3. 真实 UI 接线 ----------
def test_provider_dialog_preset_combo_has_brand_icons():
    """添加服务商弹窗：带品牌的预设逐项有非空矢量图标，「自定义」项无图标"""
    from zhuzhu_Copilot.ui import agent_panel as ap
    dlg = ap._ProviderDialog()
    try:
        assert dlg.preset_combo.count() == len(agent_llm.PRESET_PROVIDERS) + 1
        assert dlg.preset_combo.itemIcon(0).isNull()   # （自定义 / 手动填写）
        branded = 0
        for i in range(1, dlg.preset_combo.count()):
            p = dlg.preset_combo.itemData(i)
            if bi.brand_for_provider(p):
                branded += 1
                assert not dlg.preset_combo.itemIcon(i).isNull(), p["name"]
        assert branded >= 8
    finally:
        dlg.close()


def test_provider_card_gets_brand_icon():
    """服务商卡片：图标列存在、品牌识别正确、像素非空"""
    from zhuzhu_Copilot.ui import agent_panel as ap

    class _CardStub:
        _PANEL = "#181B21"
        _TEXT = "#F3F5F9"
        _DIM = "#9BA3B0"

    p = {"name": "DeepSeek", "base_url": "https://api.deepseek.com/v1",
         "models": ["deepseek-v4-flash"]}
    card = ap._AgentSettingsDialog._make_provider_card(_CardStub(), p)
    assert card._icon_brand == "deepseek"
    pm = card._icon_lbl.pixmap()
    assert not pm.isNull() and pm.width() == 24
    img = pm.toImage()
    assert any(img.pixelColor(x, y).alpha() > 0
               for y in range(img.height()) for x in range(img.width()))


def test_model_combo_wiring_source_guard():
    """模型下拉逐行挂品牌图标的接线守卫（防重构时被静默删掉）"""
    import inspect
    from zhuzhu_Copilot.ui import agent_panel as ap
    src = inspect.getsource(ap.AgentPanel._sync_model_combo)
    assert "brand_for(" in src and "brand_icon(" in src
    card_src = inspect.getsource(ap._AgentSettingsDialog._make_provider_card)
    assert "brand_for_provider(" in card_src and "brand_icon(" in card_src


# ---------- 4. 预设数据 ----------
def test_preset_brand_fields_valid():
    branded = 0
    for p in agent_llm.PRESET_PROVIDERS:
        b = p.get("brand", "")
        assert b == "" or b in bi.KNOWN_BRANDS, (p["name"], b)
        # 显式声明必须与推断一致，防止两处漂移
        if b:
            branded += 1
            assert bi.brand_for_provider(p) == b, p["name"]
    assert branded >= 8   # 当前 9 个平台预设带品牌（Kimi/硅基流动为通用图标）
