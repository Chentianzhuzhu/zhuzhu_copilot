# -*- coding: utf-8 -*-
"""国际化（i18n）核心：语言状态、语言包加载、翻译查表。

设计要点
--------
1. **零依赖、不导入 Qt**：本模块位于 ``core/`` 且会被 Cython 编译为 .pyd，同时被
   agent 引擎（后台线程、无 Qt 事件循环）引用。若在此 ``import PyQt6.QtCore``，
   后台线程首次访问语言时就会因未初始化 QApplication 而炸掉。故语言状态用
   ``app_identity.qsettings()`` 惰性读取（QSettings 本身可在无 GUI 进程使用），
   且所有失败都静默回退到中文，保证国际化永远不能影响主功能。
2. **语言包为随包数据文件**：放在 ``locales/``（打包时随 skills/plugins 一起
   以数据文件注入，见 scripts/generate_spec.py），而非写成 Python 字面量 ——
   这样新增/修改译文不需要重新编译 .pyd，也便于非开发者维护文案。
3. **缺失即回退**：任何 key 在当前语言缺失时回退到 ``zh_CN``，再回退到 key 本身，
   绝不抛异常、绝不返回空串（空串会让 UI 控件标题消失）。

语言优先级（后者覆盖前者）
--------------------------
命令行 ``--lang`` > 环境变量 ``ZHUZHU_LANG`` > QSettings（用户在设置里选的）
> 安装器写入的 ``app_lang.ini`` > 系统区域（``zh`` 前缀 → 中文，否则英文）。
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading

# ---------------------------------------------------------------- 语言常量
ZH_CN = "zh_CN"
EN_US = "en_US"

#: 支持的语言 → 该语言在语言选择器里的自描述名称（用**本语言**书写，
#: 这样无论当前界面是什么语言，用户都能认出母语项）
LANGUAGES: tuple[tuple[str, str], ...] = (
    (ZH_CN, "简体中文"),
    (EN_US, "English"),
)

#: 回退语言：所有查表未命中时的兜底（中文是本项目的源语言，内容最全）
DEFAULT_LANG = ZH_CN

#: 界面语言（UI 文案）与提示词语言（喂给 LLM 的英文）解耦：
#: 选中文界面时，提示词仍可选英文（部分用户偏好英文 prompt），反之亦然。
UI_LANGS = (ZH_CN, EN_US)
PROMPT_LANGS = (ZH_CN, EN_US)

#: CJK 表意文字区（含扩展 A 的常见字）。预编译是因为 html() 会对每个
#: 文本节点调用它，而预览面板在流式输出时会高频刷新。
_CJK = re.compile(r"[\u4e00-\u9fff]")

_lock = threading.RLock()
_state: dict = {
    "ui": None,          # 当前界面语言（None=未解析）
    "prompt": None,      # 当前提示词语言
    "packs": {},         # {lang: {...}}  已加载语言包
    "prompt_packs": {},  # {lang: {...}}  已加载提示词语言包
    "listeners": [],     # 语言变更回调（UI 侧重绘用）
}


# ---------------------------------------------------------------- 语言包定位
def _locales_dir() -> str:
    """语言包所在目录。

    源码态：``src/zhuzhu_Copilot/locales``（与 core 同级）；
    打包态：PyInstaller 把 ``locales`` 作为数据目录放在 ``_MEIPASS/locales``，
    而技能/插件等数据目录历史上都按 ``_MEIPASS`` 平铺，故两处都探测。
    """
    here = os.path.dirname(os.path.abspath(__file__))     # .../core
    root = os.path.dirname(here)                          # .../zhuzhu_Copilot
    for cand in (os.path.join(root, "locales"),
                 os.path.join(sys._MEIPASS, "locales") if hasattr(sys, "_MEIPASS") else ""):
        if cand and os.path.isdir(cand):
            return cand
    # 源码包未随包（异常场景）：退到 core 同级，用户可在此放置语言包
    return os.path.join(root, "locales")


def _prompt_locales_dir() -> str:
    """提示词语言包目录（与 UI 语言包同目录，文件名加 ``prompt_`` 前缀）。"""
    return _locales_dir()


def _read_json(path: str) -> dict:
    """读 JSON 语言包；任何异常都返回空字典（绝不因语言包损坏而影响主功能）。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


# ---------------------------------------------------------------- 语言检测
def _normalize(raw: str) -> str:
    """把任意语言标识归一到受支持的语言码。

    兼容 ``zh`` / ``zh-CN`` / ``zh_CN`` / ``zh-Hans`` / ``en`` / ``en-US`` 等写法。
    """
    s = (raw or "").strip().replace("-", "_").lower()
    if not s:
        return ""
    if s.startswith("zh") or s.startswith("cmn") or s.startswith("yue"):
        return ZH_CN          # 简繁中文都走中文包（本项目源语言为简体中文）
    if s.startswith("en"):
        return EN_US
    return ""


def _system_lang() -> str:
    """按系统区域推断初始语言。"""
    # 优先环境变量（安装器/便携运行可借此指定），再退 Qt/系统区域
    for env in ("ZHUZHU_LANG", "LANG", "LC_ALL", "LC_MESSAGES"):
        got = _normalize(os.environ.get(env, ""))
        if got:
            return got
    # Windows：GetUserDefaultUILanguage 返回语言 ID（如 0x0804=zh-CN, 0x0409=en-US）
    try:
        import ctypes
        lid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        got = _normalize({0x0804: "zh-CN", 0x0404: "zh-TW", 0x0409: "en-US"}.get(lid, ""))
        if got:
            return got
    except Exception:
        pass
    try:
        import locale as _locale
        got = _normalize(_locale.getdefaultlocale()[0] or "")
        if got:
            return got
    except Exception:
        pass
    return DEFAULT_LANG


def _installed_lang() -> str:
    """读取安装器写入的语言选择（Setup 在安装时按用户选择落盘到程序目录）。

    安装器与程序可能装在不同盘且用户无写权限，故同时探测 exe 同级与数据目录。
    """
    names = ("app_lang.ini",)
    cands = []
    try:
        base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) \
            else os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__)))))
        cands.append(base)
    except Exception:
        pass
    try:
        from zhuzhu_Copilot import app_identity
        cands.append(str(app_identity.data_root()))
    except Exception:
        pass
    for d in cands:
        for n in names:
            p = os.path.join(d, n)
            if not os.path.isfile(p):
                continue
            try:
                with open(p, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("Lang="):
                            got = _normalize(line.split("=", 1)[1])
                            if got:
                                return got
            except Exception:
                pass
    return ""


def _settings_lang(key: str) -> str:
    """读取用户在设置里选择的语言（QSettings，UI 与提示词语言各一个 key）。"""
    try:
        from zhuzhu_Copilot import app_identity
        return _normalize(str(app_identity.qsettings().value(key, "") or ""))
    except Exception:
        return ""


def detect_lang(force: str = "") -> str:
    """按优先级解析最终语言：force > 环境变量 > QSettings > 安装器 > 系统区域。"""
    got = _normalize(force)
    if got:
        return got
    return _settings_lang("ui_lang") or _installed_lang() or _system_lang()


def detect_prompt_lang() -> str:
    """解析提示词语言（喂给 LLM 的语言，与界面语言独立）。

    未显式设置时**跟随界面语言**：用户在英文界面下通常也期望英文 prompt；
    界面语言仍为中文但显式设过 prompt_lang 时以显式值为准。
    """
    got = _settings_lang("prompt_lang")
    if got:
        return got
    return current_lang()


# ---------------------------------------------------------------- 语言包加载
def _pack(lang: str, cache: dict) -> dict:
    """按需加载并缓存语言包；加载失败返回空字典。"""
    with _lock:
        hit = cache.get(lang)
        if hit is not None:
            return hit
    prefix = "prompt_" if cache is _state["prompt_packs"] else ""
    data = _read_json(os.path.join(_prompt_locales_dir(), f"{prefix}{lang}.json"))
    with _lock:
        cache[lang] = data
    return data


def _dict_pack(section: str) -> dict:
    """语言包可按 section 分组（``ui`` / ``tools`` / ``prompt``），此处取指定段。

    纯字符串键的语言包（无 section）整体作为 ``""`` 段返回，二者都能用。
    """
    whole = _pack(current_lang(), _state["packs"])
    if section in whole and isinstance(whole[section], dict):
        return whole[section]
    return {k: v for k, v in whole.items()
            if not isinstance(v, (dict, list))} if section else whole


# ---------------------------------------------------------------- 语言状态
def current_lang() -> str:
    """当前界面语言（进程内缓存；set_lang 后由调用方负责广播刷新）。"""
    with _lock:
        v = _state["ui"]
    if v:
        return v
    v = detect_lang()
    with _lock:
        _state["ui"] = v
    return v


def current_prompt_lang() -> str:
    """当前提示词语言。"""
    with _lock:
        v = _state["prompt"]
    if v:
        return v
    v = detect_prompt_lang()
    with _lock:
        _state["prompt"] = v
    return v


def is_english() -> bool:
    """当前是否为英文界面（高频分支的快捷判断，避免每次查表）。"""
    return current_lang() == EN_US


def set_lang(lang: str, persist: bool = True, prompt_lang: str = "") -> str:
    """切换界面语言（可同时指定提示词语言），返回归一后的界面语言。

    persist=True 时写入 QSettings（下次启动沿用）；UI 侧切换语言后应重建界面，
    故本函数**不负责**刷新界面，只负责状态与广播通知。
    """
    norm = _normalize(lang) or DEFAULT_LANG
    pnorm = _normalize(prompt_lang) if prompt_lang else ""
    with _lock:
        if pnorm:
            _state["prompt"] = pnorm
        elif _state["prompt"] and not persist:
            pass
        else:
            # 未显式给提示词语言：跟随界面语言（未显式设过 prompt_lang 时）
            _state["prompt"] = pnorm or norm
        _state["ui"] = norm
        listeners = list(_state["listeners"])
    if persist:
        try:
            from zhuzhu_Copilot import app_identity
            st = app_identity.qsettings()
            st.setValue("ui_lang", norm)
            if prompt_lang:
                st.setValue("prompt_lang", pnorm)
            st.sync()
        except Exception:
            pass
    for fn in listeners:
        try:
            fn(norm)
        except Exception:
            pass
    return norm


def on_change(fn):
    """注册语言变更回调，返回注销函数（UI 侧用于重绘/重建）。"""
    with _lock:
        _state["listeners"].append(fn)

    def _off():
        with _lock:
            if fn in _state["listeners"]:
                _state["listeners"].remove(fn)
    return _off


# ---------------------------------------------------------------- 翻译查表
def ui(zh: str) -> str:
    """翻译 UI 文案 —— **UI 层唯一入口**，以中文原文为 key。

    为什么用中文原文当 key（而不是 ``ui.settings.save_btn`` 这类自造 id）：
    1. **缺译文天然安全**：任何漏翻的字符串在英文界面下仍显示中文原文，
       而会显示一个突兀的 key 或空串（空串会让控件标题直接消失）。
    2. **可批量自动化**：用脚本把全量 UI 字面量机械替换为 ``ui("原文")``
       是安全的（见 scripts/_i18n_wrap_ui.py），而自造 id 需要为 1600+ 条
       文案逐条起名并维护映射表，漏一条就是一处永久中文残留。
    3. 中文即源语言，key 与 fallback 是同一份文本，无需额外映射表。

    性能：中文（默认语言）下走「语言标志位」快路径直接返回原文，
    不查字典、不加锁 —— UI 层每秒可能调用上百次。
    """
    if not zh:
        return zh
    if _fast_zh():          # 中文界面：原文即结果
        return zh
    pack = _dict_pack("ui")
    val = pack.get(zh) if isinstance(pack, dict) else None
    return val if isinstance(val, str) and val else zh


def _fast_zh() -> bool:
    """当前是否为源语言（中文）—— UI 层高频分支的零开销判断。"""
    return current_lang() == DEFAULT_LANG


def uif(zh: str, /, **kw) -> str:
    """翻译**带变量**的 UI 文案（``{name}`` 命名占位符）。

    为什么需要单独一个 API：``ui()`` 只接受纯文案，而带变量的句子
    （``f"已思考 {n} 秒"``）**不能**用 f-string 直接查表 —— 英文语序与中文
    不同（``Thought for {n}s`` vs ``已思考 {n} 秒``），机械包裹只会得到
    拼接错乱的译文。

    用法（占位符两侧的**静态**部分才是 key，变量用 ``str.format`` 注入）::

        _uif("已思考 {n} 秒", n=sec)          ->  "Thought for 12s"
        _uif("共扫描到 {n} 个应用", n=cnt)    ->  "241 apps found"

    语言包 value 里可自由调整占位符顺序（``"已扫描 {n} 个应用":
    "Found {n} apps"``），这正是 f-string 做不到的。

    约定：
      1. key 里**只允许静态文案 + ``{占位符名}``**，不含 f-string 表达式；
      2. 占位符名用 ``\\w+``，避免与 JSON 转义冲突；
      3. 译文缺该 key 时回退中文原文（变量照常注入）；
      4. 占位符在译文中缺失/多余时**不抛异常** —— 退回中文原文优先于崩溃。
    """
    if not zh:
        return zh
    if _fast_zh() and not kw:
        return zh
    return _tr_zh(zh, kw)[1]


def html(zh_html: str, /, **kw) -> str:
    """翻译 **HTML 片段里的人类可读文本**，保留标签与属性。

    为什么需要单独一个：预览面板、崩溃说明、缺依赖提示等都用
    ``QLabel.setHtml`` / ``QTextBrowser.setHtml`` 展示带 ``<p>``/``<b>``/
    ``<br>`` 的富文本。整段塞进 :func:`ui` 查表不可行 —— 译文得逐个文本
    节点对应，且极易漏掉标签导致样式丢失。

    两级策略（顺序不可颠倒）：

    1. **整段查表优先**：语言包里可给整段 key，译文自带标签与完整语序
       （``"<p>安装 <b>X</b> 后…</p>"`` -> ``"<p>Install <b>X</b> then…</p>"``）。
       这是译文质量最高的形态。
    2. **逐文本节点兜底**：整段未收录时，用正则切出「标签」「文本」两类
       片段，只对文本片段查表再拼回，标签/属性/实体原样保留。

    为什么不能只用第 2 步：正则遇 ``<b>`` 就断句，``安装 <b>X</b> 后…``
       会被切成「安装」+「后…」两半分别查表 —— 句子被割裂，任何整句译文
       都匹配不上，只能得到支离的碎片译文。

    用法::

        _html('<p>读取中 <b>{name}</b>…</p>', name="a.txt")

    文本节点含 ``{占位符}`` 时译文可自由调整顺序；非法裸 ``<``/``>``
    片段保守跳过不译（正则无状态，无法真正解析 HTML）。
    """
    if not zh_html:
        return zh_html
    if _fast_zh() and not kw:
        return zh_html
    if _CJK.search(zh_html):
        hit, text = _tr_zh(zh_html, kw)
        if hit:
            return text
    parts = re.split(r"(<[^>]*>)", zh_html)
    for i in range(0, len(parts), 2):        # 偶数位=文本，奇数位=标签
        seg = parts[i]
        if not seg or "<" in seg or ">" in seg:
            continue
        if not _CJK.search(seg):
            continue
        parts[i] = _ui_seg(seg, kw)
    return "".join(parts)


def _tr_zh(zh: str, kw: dict) -> tuple[bool, str]:
    """查表 + 注入变量。返回 ``(语言包是否命中, 文本)``。

    单独拆出来是因为 :func:`html` 需要区分「整段命中」与「整段未收录」：
    未收录时要退到逐节点翻译，而不是把原文当成译文直接返回。

    ``命中`` 只在语言包里真的查到译文时为 True —— 中文模式（源语言）
    也算未命中，因为那只是原文本身。
    """
    if not zh:
        return False, ""
    if not _fast_zh():
        pack = _dict_pack("ui")
        val = pack.get(zh) if isinstance(pack, dict) else None
        if isinstance(val, str) and val:
            try:
                return True, val.format(**kw) if kw else val
            except (KeyError, IndexError, ValueError):
                # 译文占位符与实参不匹配（漏翻/写错）：退回原文，
                # 宁可显示中文也不要抛异常中断 UI 构建。
                pass
    try:
        return False, zh.format(**kw) if kw else zh
    except Exception:
        return False, zh


def _ui_seg(seg: str, kw: dict) -> str:
    """翻译单个 HTML 文本节点（保留首尾空白，避免拼出多余空隙）。"""
    lead = seg[:len(seg) - len(seg.lstrip())]
    tail = seg[len(seg.rstrip()):]
    core = seg.strip()
    if not core:
        return seg
    _, text = _tr_zh(core, kw)
    return lead + text + tail


def label_width(labels, base: int = 74, pad: int = 14,
                max_w: int = 200) -> int:
    """按当前语言的实际文案宽度，给设置页标签列算一个自适应宽度。

    为什么不能写死：标签列宽是按中文短标签量出来的（"界面主题" ≈ 52px，
    加上留白写死 70px）。英文 "System Prompt Language" 需 286px，
    写死就会被 Qt 裁成 "System Pr…" —— 即用户说的**文字挤压遮挡**，
    且换字体/DPI 后更糟。

    做法：
      · 逐个用当前字体度量**译文实际宽度**，取最大值 + 留白；
      · 下限 = base（中文观感完全不变，零视觉回归）；
      · 上限 = max_w（超长文案不再无限撑宽，避免挤掉右侧控件 ——
        再长由 QLabel 自身省略号兜底，见调用处的 elide 处理）。

    需要 QApplication 已存在（QFontMetrics），故只在构建界面时调用，
    不在后台线程路径上。
    """
    try:
        from PyQt6.QtGui import QFontMetrics
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            return base
        fm = QFontMetrics(app.font())
    except Exception:
        return base

    best = 0
    for t in labels:
        if not t:
            continue
        try:
            best = max(best, fm.horizontalAdvance(str(t)))
        except Exception:
            continue
    if not best:
        return base
    # 只在**确实放不下时**放宽；放得下就保持 base —— 这样中文界面宽度与
    # 原先写死的 70px 完全一致（零视觉回归），而不是被 +pad 顶到 74px。
    return max(base, min(max_w, best + pad))


def uim(zh: str) -> str:
    """翻译**多行 / 带变量**的 UI 文案（占位提示、多行确认框、空态说明）。

    与 :func:`ui` 同源（中文原文作 key），差别只在于允许换行 —— ``ui()`` 侧
    的机械包装会跳过含 ``\\n`` 的串（切错会破坏代码），这类文案数量不多
    （约 25 处）故单独手工登记在这里。

    约定：**译文必须与原文的换行结构一致**（行数相同）。否则 Qt 控件高度
    预估会与实际内容不符，出现截断或大片空白。登记时请逐行对应。
    """
    if not zh:
        return zh
    if _fast_zh():
        return zh
    pack = _dict_pack("ui")
    val = pack.get(zh) if isinstance(pack, dict) else None
    return val if isinstance(val, str) and val else zh


def tr(key: str, default: str = "", lang: str = "") -> str:
    """翻译 UI 文案。

    查表顺序：当前语言 → 回退语言（中文）→ default → key。
    **任何层级未命中都返回非空字符串**：UI 控件标题若为空会直接消失，
    比显示 key 更糟，故 default/key 均作为最后兜底。
    """
    if not key:
        return default
    lg = _normalize(lang) or current_lang()
    pack = _dict_pack("ui") if lg == current_lang() else _pack(lg, _state["packs"]).get("ui", {})
    val = pack.get(key) if isinstance(pack, dict) else None
    if isinstance(val, str) and val:
        return val
    if lg != DEFAULT_LANG:
        fb = _pack(DEFAULT_LANG, _state["packs"]).get("ui", {})
        v2 = fb.get(key) if isinstance(fb, dict) else None
        if isinstance(v2, str) and v2:
            return v2
    return default if default else key


def tp(key: str, default: str = "", lang: str = "") -> str:
    """翻译提示词文案（system prompt / 工具描述）。

    与 :func:`tr` 同一套回退逻辑，但读的是 ``prompt_<lang>.json`` 的 ``prompt`` 段。
    """
    if not key:
        return default
    lg = _normalize(lang) or current_prompt_lang()
    whole = _pack(lg, _state["prompt_packs"])
    pack = whole.get("prompt") if isinstance(whole.get("prompt"), dict) else \
        {k: v for k, v in whole.items() if not isinstance(v, (dict, list))}
    val = pack.get(key) if isinstance(pack, dict) else None
    if isinstance(val, str) and val:
        return val
    if lg != DEFAULT_LANG:
        fb = _pack(DEFAULT_LANG, _state["prompt_packs"]).get("prompt", {})
        v2 = fb.get(key) if isinstance(fb, dict) else None
        if isinstance(v2, str) and v2:
            return v2
    return default if default else key


def tpf(key: str, default: str, /, **kw) -> str:
    """翻译**带变量**的提示词文案（``{name}`` 命名占位符）。

    与 :func:`tp` 同一套回退逻辑，但支持占位符注入。英文语序与中文不同
    （``「{wf}」工作流`` vs ``the "{wf}" workflow``），机械 f-string 拼中文原文
    再整体查表会得到拼接错乱的译文，故译文自带占位符、由 ``str.format`` 注入。

    占位符与实参不匹配（漏翻/写错）时**不抛异常**：退回中文原文优先于崩溃。
    """
    out = tp(key, default)
    if not kw or not out:
        return out
    try:
        return out.format(**kw)
    except (KeyError, IndexError, ValueError):
        return default


def tool_desc(name: str, default: str) -> str:
    """翻译单个工具的 description（提示词语言；缺失回退传入的中文原文）。"""
    return tp(f"tool.{name}", default)


def param_desc(name: str, param: str, default: str) -> str:
    """翻译工具参数描述（键 ``tool.<name>.param.<param>``）。"""
    return tp(f"tool.{name}.param.{param}", default)


# ---------------------------------------------------------------- 启动预热
def init(lang: str = "") -> str:
    """进程启动时调用：按优先级解析语言并预热语言包（避免首帧再读盘）。"""
    norm = _normalize(lang) or detect_lang()
    pnorm = detect_prompt_lang() or norm
    with _lock:
        _state["ui"] = norm
        _state["prompt"] = pnorm
    _pack(norm, _state["packs"])          # 预热 UI 语言包
    _pack(pnorm, _state["prompt_packs"])  # 预热提示词语言包
    return norm