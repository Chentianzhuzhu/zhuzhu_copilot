"""电脑操控任务上下文膨胀回归测试（修复「AI 操作电脑插件没走几步上下文就爆」）。

根因（实测数据，128K 窗口口径）：
1. **任务裁剪漏配「电脑操控」类别**（主因）。`_TASK_GROUPS` 里没有 desktop 类，
   操控类措辞（"操作电脑""点这个按钮""帮我点一下屏幕上的确定"）命中 None，
   于是走「保留全部工具」的保守分支 —— 105 个内置工具的 schema 单独就占
   38.3K tokens，加MCP 24 个工具 5.5K、系统提示 9.2K，**还没动手就吃掉 53.0K
   （41.4%）**。操控任务每轮都要重发工具表（截图 → 读元素 → 操作 → 再截图验证），
   剩余空间被瞬间挤干。
   注：图片体积**不是**本问题 —— 截图早已治理为 174K 字符有界 JPEG，
   本地估765 tok 反而高于真实视觉 543 tok（高估 41%）。

2. **MCP 读取类工具结果零压缩**（次因）。`_READ_CONDENSE_TOOLS` 只含几个内置
   读取工具，MCP 的 list_windows（窗口列表，窗口多时约数千 tok）/browser_html
   等结构化枚举全部原文进上下文，且每步操控都要读一次。
   注：元素树（list_elements）已随「改为纯坐标定位」整体移除，不再参与压缩考量。

修复：
- 新增 `desktop` 任务类别（仅用桌面专属措辞，不收"点一下/双击"等泛化词）；
- 新增 `_desktop_intent()`：对桌面与网页都成立的点选措辞做消歧判定；
- 新增 `_MCP_READ_TOOLS` 纳入压缩白名单（走既有 LLM 结构化摘要，非硬截断）。

本文件覆盖：
- 各类操控措辞都能命中 desktop，且不误伤网页/文档/闲聊任务；
- 命中 desktop 后工具 schema 显著小于不裁剪（量化收益，防回归）；
- MCP 读取类工具已进入压缩白名单，且正常体量不会误触发压缩。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from zhuzhu_Copilot.core import agent_engine, agent_llm, agent_tools


def _groups(text):
    """归一化命中组（顺序无关，便于断言）。"""
    g = agent_engine._detect_task_groups(text)
    return set(g) if g else None


# --------------------------------------------------------------------------
# 一、桌面操控措辞必须命中 desktop
# --------------------------------------------------------------------------
def test_desktop_specific_phrasings_hit_desktop():
    for q in ("帮我操作电脑打开微信", "操作桌面上的记事本", "操控电脑点确定",
              "用鼠标点一下", "看屏幕", "截图看看现在什么状态",
              "在界面上点那个按钮", "切换窗口", "打开软件"):
        assert _groups(q) == {"desktop"}, f"{q!r} 未命中 desktop：{_groups(q)}"


def test_generic_pointing_words_resolved_to_desktop():
    """泛化点选措辞（点一下/双击/点击）靠 _desktop_intent 消歧到 desktop。"""
    for q in ("点击这个按钮", "帮我点一下屏幕上的确定", "双击桌面图标",
              "点一下这个窗口"):
        assert _groups(q) == {"desktop"}, f"{q!r} 未命中 desktop：{_groups(q)}"


# --------------------------------------------------------------------------
# 二、消歧：网页语境下点选措辞必须归browser，不能被 desktop 抢走
# --------------------------------------------------------------------------
def test_web_context_pointing_stays_browser():
    for q in ("帮我点一下网页上的链接", "浏览网页并点赞", "打开网页登录",
              "帮我打开网站"):
        assert _groups(q) == {"browser"}, f"{q!r} 被误判：{_groups(q)}"


def test_non_gui_tasks_not_captured():
    """纯聊天/编码任务不得被 desktop 捕获（否则白带一整套操控工具）。"""
    for q in ("你好", "帮我写个脚本", "读取这个文件的内容", "解释一下这段代码"):
        assert _groups(q) is None, f"{q!r} 被误判为 {_groups(q)}"


def test_other_task_groups_unaffected():
    assert _groups("做个ppt") == {"doc"}
    assert _groups("优化内存") == {"sys"}
    assert _groups("帮我看这张图片") == {"image"}


# --------------------------------------------------------------------------
# 三、量化收益：命中 desktop 后工具 schema 必须显著变小（防回归的核心断言）
# --------------------------------------------------------------------------
def _schema_tokens_for(groups):
    """按 groups 裁剪后的工具 schema token；groups=None 表示**完全不裁剪**。

    对应 `_all_tools` 的真实语义：命中任务类别才裁剪到「核心+该类」，
    未命中则保留全部工具（保守分支）—— 那正是本次要修掉的膨胀源。
    """
    import json
    all_tools = agent_tools.tool_schemas("default")
    if groups is None:
        return agent_llm.estimate_tokens(json.dumps(all_tools, ensure_ascii=False))
    keep = set(agent_engine._CORE_TOOLS)
    for g in groups:
        keep |= agent_engine._TASK_GROUP_TOOLS.get(g, frozenset())
    trimmed = [t for t in all_tools if t["function"]["name"] in keep]
    return agent_llm.estimate_tokens(json.dumps(trimmed, ensure_ascii=False))


def test_desktop_task_trims_tool_schema_significantly():
    """不裁剪 38.3K → 裁剪后必须降到 20K 以下（实测 16.1K，省 22.2K）。"""
    full = _schema_tokens_for(None)
    trimmed = _schema_tokens_for(["desktop"])
    assert full > 30000, f"全量工具 schema 异常小({full})，基准失效"
    assert trimmed < 20000, f"裁剪后仍偏高({trimmed})，裁剪未生效"
    # 至少省下 20K —— 这正是「没走几步就爆」的直接原因
    assert full - trimmed > 20000, f"节省不足({full - trimmed})"


def test_desktop_group_exposes_control_capable_tools():
    """desktop 组必须带上操控/截图相关工具，否则裁剪后模型无法操作。"""
    tools = agent_engine._TASK_GROUP_TOOLS["desktop"]
    for name in ("screen_info", "screen_shot", "ask_user"):
        assert name in tools, f"desktop 组缺少 {name}"


# --------------------------------------------------------------------------
# 四、MCP 读取类工具进入压缩白名单
# --------------------------------------------------------------------------
def test_mcp_read_tools_in_condense_allowlist():
    for name in ("list_windows", "browser_html",
                 "browser_snapshot", "manage_window", "screen_info"):
        assert name in agent_engine._READ_CONDENSE_TOOLS, \
            f"{name} 未纳入压缩白名单，MCP 结构化枚举会原文堆进上下文"


def test_normal_sized_mcp_results_do_not_trigger_condense():
    """实测的真实返回体量（数百字符）远低于阈值，不应误触发压缩。"""
    real_sizes = {"screen_info": 324, "list_windows": 415,
                  "manage_window": 344, "browser_snapshot": 370}
    for name, size in real_sizes.items():
        assert size <= agent_engine._READ_CONDENSE_CHARS, \
            f"{name} 正常返回({size})竟超阈值，会造成无谓的 LLM 摘要调用"


def test_oversized_window_list_would_be_condensed():
    """窗口列表爆炸（几百个窗口的枚举）必须越过阈值触发压缩。"""
    body = "\n".join(
        f"[{2885710 + i * 4}] 「窗口标题 {i}」 位置({100 + i * 20},{200 + i * 15}) "
        f"尺寸 1200×800 类别=main 进程=app{i}.exe 置顶=否" for i in range(1, 301))
    assert len(body) > agent_engine._READ_CONDENSE_CHARS, \
        "最坏窗口列表未超阈值，多轮累积仍会顶穿上下文"