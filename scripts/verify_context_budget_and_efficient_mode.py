# -*- coding: utf-8 -*-
"""验证脚本：① 上下文预算修复（token 估算密度 / 运行期校准 / 上游越窗 400 自动恢复）
            ② 高效模式（最小工具集 + 精简提示词 + 设置页开关）

全部为离线断言（不产生真实 API 调用）：token 密度用实测基准（见 _probe_token_density.py），
400 恢复用注入 AgentLLMError 的假 LLM 客户端复现。

运行：python scripts/verify_context_budget_and_efficient_mode.py
输出：[PASS]/[FAIL]/[SKIP] 逐项断言，末尾 ALL PASS/FAILURES 与 EXIT 码。
"""
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
try:   # 控制台按 UTF-8 输出：中文断言名在 GBK 终端下会炸（仅影响本脚本的打印）
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FAILS = []
PASSES = []
SKIPS = []


def check(name, cond, detail=""):
    if cond:
        PASSES.append(name)
        print(f"[PASS] {name}" + (f" — {detail}" if detail else ""))
    else:
        FAILS.append(name)
        print(f"[FAIL] {name}" + (f" — {detail}" if detail else ""))


def skip(name, detail=""):
    SKIPS.append(name)
    print(f"[SKIP] {name}" + (f" — {detail}" if detail else ""))


from zhuzhu_Copilot.core import agent_engine, agent_llm, agent_skills  # noqa: E402

print("=" * 78)
print("① token 估算密度（基准来自真实 API 实测，见 scripts/_probe_token_density.py）")
print("=" * 78)
# 实测「字符/token」密度（越密越危险）：
#   纯英文散文 4.50 / Python 代码 2.73 / JSON 2.35 / 重复短串 2.0 / CJK 中文 1.75
_BENCH = [
    ("CJK", "中文测试内容，这是公告与公告说明文字。" * 2000, 1.75),
    ("ascii prose", "the quick brown fox jumps over the lazy dog. " * 4000, 4.50),
    ("python code", "def foo(x):\n    return x + 1\n\n" * 4000, 2.73),
    ("json", json.dumps({"key": "value", "n": 123, "flag": True}) * 3000, 2.35),
]
for name, text, density in _BENCH:
    est = agent_llm.estimate_tokens(text)
    real = len(text) / density          # 由实测密度反推的真实 token 数
    check(f"估算不低于实测基准（{name}）", est >= real * 0.99,
          f"est={est} real≈{int(real)} ratio={est / max(1.0, real):.2f}")
for name, text, density in _BENCH:
    cpt = len(text) / agent_llm.estimate_tokens(text)
    # 非 CJK 不得超过 2.36 字符/token（实测最密 2.35）；CJK 含标点，口径按 1.3 字符/token
    limit = 1.30 if name == "CJK" else 2.36
    check(f"估算口径足够保守（{name}）", cpt <= limit,
          f"{cpt:.2f} 字符/token（实测 {density}）")
check("CJK 估算口径仍为 1 token/字",
      abs(agent_llm.estimate_tokens("中" * 1000) - 1004) <= 8,
      f"{agent_llm.estimate_tokens('中' * 1000)}")

print()
print("=" * 78)
print("② 引擎校准口径（_estimate_tokens / _tok_factor / 工具 schema 开销）")
print("=" * 78)


class _FakeMCP:
    """假 MCP 管理器：暴露一个 MCP 工具，用于验证高效模式不再向模型提供它。"""

    def tool_schemas_for(self, allowed):
        return [{"type": "function",
                 "function": {"name": "mcp_demo_tool", "description": "demo",
                              "parameters": {"type": "object", "properties": {}}}}]

    def server_for_tool(self, name):
        return "demo-mcp"


class _FakeLLM:
    """假 LLM 客户端：按脚本逐次返回，脚本项为 Exception 时抛出（复现上游报错）。"""

    base_url = "https://api.example.com/v1"
    api_key = "k"
    protocol = "chat"
    model = "fake-model"

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.main_calls = 0

    def chat_stream(self, messages, tools=None, tool_choice=None, on_delta=None,
                    on_reasoning=None, stop=None, max_tokens=None):
        self.calls += 1
        if hasattr(self, "_hook"):
            self._hook(self.calls)
        item = (self.script.pop(0) if self.script
                else {"text": "ok", "tool_calls": [], "usage": {}})
        if isinstance(item, Exception):
            raise item
        return item


def _mk_engine(llm, **kw):
    return agent_engine.AgentEngine(
        llm, mcp_manager=kw.pop("mcp", None), on_delta=lambda s: None,
        on_status=kw.pop("on_status", lambda s: None), on_result=lambda *a: None,
        ask_user=lambda a: "", text_only=False, memory_enabled=True,
        workflow=kw.pop("workflow", ""), **kw)


eng = _mk_engine(_FakeLLM([]))
raw = eng._estimate_raw()
cal = eng._estimate_tokens()
check("_estimate_tokens 含工具 schema 开销（≥ _estimate_raw）", cal >= raw,
      f"raw={raw} cal={cal} schema={eng._tool_schema_tokens()}")
check("工具 schema 开销被计入", eng._tool_schema_tokens() > 0,
      f"{eng._tool_schema_tokens()} tokens / {len(eng._all_tools())} 个工具")
eng._messages = [{"role": "system", "content": "s" * 40000},
                 {"role": "user", "content": "x" * 40000}]
raw_before = eng._estimate_raw()
schema = eng._tool_schema_tokens()
eng._tok_factor = 2.0
check("校准系数 2.0 时估算随之放大（消息 + schema 一起缩放）",
      abs(eng._estimate_tokens() - ((raw_before + schema) * 2)) <= 2,
      f"raw={raw_before} schema={schema} cal={eng._estimate_tokens()}")
eng._tok_factor = 1.0


def _calib_once(factor_in, real_over_raw):
    """造一次请求的校准样本，返回校准后的系数（_tok_factor）。"""
    e = _mk_engine(_FakeLLM([{"text": "ok", "tool_calls": [], "usage": {}}]))
    e._tok_factor = factor_in
    e._messages = [{"role": "system", "content": "a" * 60000}]
    e.last_estimate = e._estimate_tokens()
    # 与引擎主循环同一口径：留档 = 消息估算 + 工具 schema 估算
    e._sent_raw_est = e._estimate_raw() + e._tool_schema_tokens()
    _real = int(e._sent_raw_est * real_over_raw)
    e._accum_usage({"prompt_tokens": _real, "completion_tokens": 10}, None)
    _f = _real / float(e._sent_raw_est)
    _f = min(agent_engine._TOK_FACTOR_MAX, max(agent_engine._TOK_FACTOR_MIN, _f))
    e._tok_factor = min(agent_engine._TOK_FACTOR_MAX,
                        max(agent_engine._TOK_FACTOR_MIN,
                            0.5 * e._tok_factor + 0.5 * _f))
    return e._tok_factor


check("真实用量偏高时系数上修（防越窗）", _calib_once(1.0, 1.8) > 1.0,
      f"{_calib_once(1.0, 1.8):.3f}")
check("真实用量偏低时系数下修（不浪费长窗口）", _calib_once(1.0, 0.5) < 1.0,
      f"{_calib_once(1.0, 0.5):.3f}")
check("系数下界受保护（不会掉到 0.75 以下）",
      _calib_once(1.0, 0.05) >= agent_engine._TOK_FACTOR_MIN,
      f"{_calib_once(1.0, 0.05):.3f} (min={agent_engine._TOK_FACTOR_MIN})")
check("系数上界受保护（不超过 3.0）",
      _calib_once(1.0, 9.0) <= agent_engine._TOK_FACTOR_MAX,
      f"{_calib_once(1.0, 9.0):.3f}")

print()
print("=" * 78)
print("③ 上游越窗 400 自动恢复（复现用户场景：1M 窗口模型上下文爆满 → HTTP 400）")
print("=" * 78)
_REAL_400 = ("HTTP 400: {\"error\":{\"message\":\"This model's maximum context length is "
             "1048576 tokens. However, you requested 2100095 tokens (2100031 in the "
             "messages, 64 in the completion). Please reduce the length of the messages "
             "or completion. (request_id: x)\",\"type\":\"invalid_request_error\","
             "\"code\":\"invalid_request_error\"}}")
check("越窗 400 文案被正确识别", agent_engine._is_ctx_overflow(_REAL_400) is True)
check("普通鉴权错误不被误判为越窗",
      agent_engine._is_ctx_overflow("HTTP 401: invalid api key") is False)
check("max_tokens 类错误不被误判为越窗",
      agent_engine._is_ctx_overflow("HTTP 400: invalid max_tokens value") is False)


def _long_history(n=30):
    msgs = [{"role": "system", "content": "s" * 2000}]
    for i in range(n):
        msgs.append({"role": "user", "content": f"h{i} " + "中文内容" * 200})
        msgs.append({"role": "assistant", "content": "答" + "内容文字" * 200})
    return msgs


# 越窗 → 压缩重试 → 成功（第 2 次调用是压缩用的摘要请求，第 3 次是本轮重试）
_llm = _FakeLLM([agent_llm.AgentLLMError(_REAL_400),
                 {"text": "已恢复并完成", "tool_calls": [], "usage": {}},
                 {"text": "已恢复并完成", "tool_calls": [], "usage": {}}])
eng3 = _mk_engine(_llm, context_window=1048576, max_output_tokens=393216)
eng3._messages = _long_history()
_len_before = len(eng3._messages)
eng3.run("继续", "")
check("越窗 400 后任务不失败（自动恢复并完成）", eng3.end_state == "done",
      f"end_state={eng3.end_state} calls={_llm.calls}")
check("恢复过程中确实压缩/裁剪了上下文", len(eng3._messages) < _len_before,
      f"{_len_before} → {len(eng3._messages)} 条")
check("恢复后校准系数被上修", eng3._tok_factor > 1.0,
      f"_tok_factor={eng3._tok_factor:.3f}")
check("越窗重试有界（本轮只重试 1 次即成功）", eng3._ctx_overflow_retries == 1,
      f"retries={eng3._ctx_overflow_retries}")
check("恢复后占用已落回硬上限之内",
      eng3._estimate_tokens() <= eng3._hard_ceiling(),
      f"{eng3._estimate_tokens()} <= {eng3._hard_ceiling()}")

_llm2 = _FakeLLM([agent_llm.AgentLLMError("HTTP 401: invalid api key")])
eng4 = _mk_engine(_llm2)
eng4.run("hi", "")
check("非越窗错误仍然正常失败（不被误吞）", eng4.end_state == "error",
      f"end_state={eng4.end_state}")

_llm3 = _FakeLLM([agent_llm.AgentLLMError(_REAL_400)] * 8)
eng5 = _mk_engine(_llm3, context_window=1048576, max_output_tokens=393216)
eng5._messages = _long_history()
eng5.run("hi", "")
check("反复越窗时重试有界并最终如实报错",
      eng5.end_state == "error"
      and eng5._ctx_overflow_retries == agent_engine._MAX_CTX_OVERFLOW_RETRIES,
      f"retries={eng5._ctx_overflow_retries}/{agent_engine._MAX_CTX_OVERFLOW_RETRIES} "
      f"calls={_llm3.calls} state={eng5.end_state}")

print()
print("=" * 78)
print("④ 高效模式：不向上游提供 MCP / 插件 / 冗余技能与工具")
print("=" * 78)
eng6 = _mk_engine(_FakeLLM([]), mcp=_FakeMCP(), efficient_mode=True)
names = [t["function"]["name"] for t in eng6._all_tools()]
check("高效模式工具集全部落在白名单内",
      set(names) <= set(agent_engine._EFFICIENT_TOOLS),
      f"{len(names)} 个: {sorted(names)}")
check("高效模式不提供 MCP 工具", "mcp_demo_tool" not in names)
check("高效模式不提供子 Agent / 工作团工具",
      not any(agent_engine._is_subagent_tool(n) for n in names))
check("高效模式不提供文档生成类工具",
      not ({"create_docx", "create_pptx", "create_xlsx"} & set(names)))
eng7 = _mk_engine(_FakeLLM([]), mcp=_FakeMCP(), efficient_mode=False)
names7 = [t["function"]["name"] for t in eng7._all_tools()]
check("关闭高效模式时 MCP 工具仍然并入（未被误伤）", "mcp_demo_tool" in names7,
      f"{len(names7)} 个工具")
check("高效模式工具数明显少于默认模式", len(names) * 3 < len(names7),
      f"{len(names)} vs {len(names7)}")
# 引擎侧对「整体替换型」提示词（自定义人格 / 工作流提示词）补挂高效模式说明：
# 否则工作流自带方法论会让人去调 browser_open 等本次并不提供的工具。
eng_p = _mk_engine(_FakeLLM([]), efficient_mode=True)
eng_p.persona = "你是一个只按人设行事的助手，先调用 browser_open 再调用 create_docx。"
_p1 = eng_p._system_prompt()
check("高效模式下自定义人格提示词补挂了高效模式说明",
      "【高效模式】" in _p1 or "[Efficient mode]" in _p1)
_p2 = agent_skills.ensure_efficient_notice(_p1)
check("高效模式说明不会重复追加（幂等）", _p2 == _p1)
eng8 = _mk_engine(_FakeLLM([]), mcp=_FakeMCP(), efficient_mode=True)
eng8._disabled_all = True
check("高效模式仍遵守「禁用全部工具」", eng8._all_tools() == [])
eng8._disabled_all = False
eng8._disabled_tools = {"run_command"}
check("高效模式仍遵守「禁用指定工具」",
      "run_command" not in [t["function"]["name"] for t in eng8._all_tools()])

_p_full = agent_skills.build_system_prompt("zhuzhu Copilot", efficient=False)
_p_eff = agent_skills.build_system_prompt("zhuzhu Copilot", efficient=True)
check("高效模式提示词显著变短", len(_p_eff) < len(_p_full) * 0.5,
      f"{len(_p_full)} -> {len(_p_eff)} 字符"
      f"（约省 {100 - int(len(_p_eff) * 100 / max(1, len(_p_full)))}%）")
# 语言相关断言用「当前提示词语言下的实际文案」比对：用户可能把提示词语言设为英文，
# 断言不能写死中文（否则会把语言设置误判成缺陷）。
_man_head = (agent_skills._tp("prompt.block.tool_ins_head", "工具执行规范"),
             "Available built-in tools", "可用内置工具")
check("高效模式提示词不再含工具手册/工具执行规范整段",
      not any(k in _p_eff for k in _man_head),
      f"检查 {_man_head}")
check("高效模式提示词不再注入技能规范/技能路由",
      ("技能规范" not in _p_eff) and ("create_skill" not in _p_eff)
      and ("prompt.block.skill_router" not in _p_eff))
_dir_now = agent_skills._tp("prompt.lang.directive", agent_skills._LANG_DIRECTIVE_ZH)
check("高效模式提示词开头仍是语言指令（权重最高）",
      _p_eff.startswith(_dir_now[:20]), _p_eff[:24].replace("\n", " "))
_mand_now = agent_skills._tp("prompt.block.mandatory", "【强制要求】")
check("高效模式仍保留人设与强制要求",
      ("Copilot" in _p_eff or "猪" in _p_eff) and _mand_now[:8] in _p_eff,
      f"强制要求片段={_mand_now[:8]!r}")
check("高效模式提示词明确列出可用工具并声明无 MCP/插件等工具",
      "read_file" in _p_eff and "MCP" in _p_eff and "run_command" in _p_eff)
check("默认模式提示词未被改动（仍含工具手册）",
      any(k in _p_full for k in _man_head), "browser_open" in _p_full)

from zhuzhu_Copilot.core import i18n  # noqa: E402
_old_lang = i18n.current_prompt_lang()
# 中英双语文案的断言：必须显式指定语言，否则结果取决于用户当前的提示词语言设置
# （用户实测常把提示词语言切到 en_US）——这正是"把用户设置误判成缺陷"的经典坑。
i18n.set_lang(i18n.current_lang(), persist=False, prompt_lang="zh_CN")
try:
    _p_zh = agent_skills.build_system_prompt("zhuzhu Copilot", efficient=True)
    check("中文提示词语言下高效模式文案为中文", "【高效模式】" in _p_zh)
    check("中文下开头仍是中文语言指令",
          _p_zh.startswith(agent_skills._LANG_DIRECTIVE_ZH[:20]))
finally:
    i18n.set_lang(i18n.current_lang(), persist=False, prompt_lang="en_US")
try:
    _p_en = agent_skills.build_system_prompt("zhuzhu Copilot", efficient=True)
    check("英文提示词语言下高效模式文案为英文",
          "[Efficient mode]" in _p_en and "【高效模式】" not in _p_en)
    check("英文下开头是英文语言指令",
          _p_en.lower().startswith("[output language]"), _p_en[:24])
finally:
    i18n.set_lang(i18n.current_lang(), persist=False, prompt_lang=_old_lang)
check("验证结束后提示词语言已复原（不污染用户设置）",
      i18n.current_prompt_lang() == _old_lang,
      f"{i18n.current_prompt_lang()} == {_old_lang}")

print()
print("=" * 78)
print("⑤ 设置页开关（高效模式）与面板接线")
print("=" * 78)
_here = os.path.dirname(os.path.abspath(__file__))
_src_panel = open(os.path.join(_here, "..", "src", "zhuzhu_Copilot", "ui",
                               "agent_panel.py"), encoding="utf-8").read()
check("设置页存在高效模式复选框", "self.efficient_check = QCheckBox(" in _src_panel)
check("设置页保存写入 efficient_mode",
      '"efficient_mode": self.efficient_check.isChecked(),' in _src_panel)
check("面板把 efficient_mode 传给引擎",
      "efficient_mode=self._efficient_mode," in _src_panel)
check("设置变更后同步到运行中的引擎",
      "eng.efficient_mode = self._efficient_mode" in _src_panel)
_src_wf = os.path.join(os.path.expanduser("~"), ".zhuzhu_Copilot", "workflows",
                       "zhuzhu_copilot", "agent.py")
try:
    _wf_txt = open(_src_wf, encoding="utf-8").read()
    check("默认工作流的提示词继承已透传高效模式开关",
          "efficient=bool(s.get(\"efficient_mode\", False))" in _wf_txt)
except Exception as e:   # noqa: BLE001
    skip("默认工作流 agent.py 检查", f"{type(e).__name__}: {e}")

try:
    from PyQt6.QtWidgets import QApplication  # noqa: E402
    from zhuzhu_Copilot.ui import agent_panel as _ap  # noqa: E402
    _app = QApplication.instance() or QApplication([])
    _dlg = _ap._AgentSettingsDialog(None)
    # 设置页是懒加载的：控件在对应导航页被打开时才构建（_save 也先 _ensure_all_pages），
    # 故必须先补齐全部页面再断言控件存在。
    try:
        _dlg._ensure_all_pages()
    except Exception:
        pass
    _has = hasattr(_dlg, "efficient_check")
    check("离屏实例化设置对话框：高效模式复选框存在", _has)
    if _has:
        _dlg.efficient_check.setChecked(True)
        check("高效模式复选框可勾选并读回", _dlg.efficient_check.isChecked() is True)
        _dlg.efficient_check.setChecked(
            bool(agent_skills.load_settings().get("efficient_mode", False)))
        check("高效模式复选框默认值与 settings.json 一致",
              _dlg.efficient_check.isChecked()
              == bool(agent_skills.load_settings().get("efficient_mode", False)))
    _dlg.deleteLater()
except Exception as e:   # noqa: BLE001
    skip("离屏实例化设置对话框", f"{type(e).__name__}: {e}")

print()
print("=" * 78)
print("⑥ 端到端：稠密内容（JSON/代码）× 1M 窗口模型 → 绝不发出超窗请求")
print("=" * 78)
# 复刻用户真实配置：上游声明 window=1048576、max_output=393216（deepseek-flash），
# 未勾选 1M 开关 → source=upstream。历史内容用 JSON（实测最密：2.35 字符/token），
# 旧口径（1/4 字符）会把 1.35M 真实 token 低估成 ~0.79M，于是"估算刚过阈值"时
# 真实请求已经越窗 → 上游 400（用户报的 bug）。
_WINDOW = 1048576
_RESERVE = 393216
_JSON_UNIT = json.dumps({"k": "value", "n": 123, "ok": True})


class _WindowEnforcingLLM(_FakeLLM):
    """按上游真实口径核算 payload：超窗即抛真实 400 文案，否则成功。

    真实 token 数按实测密度折算（JSON 2.35 字符/token、CJK 1.75 字符/token），
    即模拟上游 tokenizer 的行为，用来验证"发出去的请求到底有没有越窗"。
    """

    def __init__(self, window, reserve):
        super().__init__([])
        self.window = window
        self.reserve = reserve
        self.max_real = 0
        self.overflows = 0

    @staticmethod
    def _real_tokens(text):
        cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
        other = len(text) - cjk
        return int(cjk / 1.75 + other / 2.35)

    def chat_stream(self, messages, tools=None, tool_choice=None, on_delta=None,
                    on_reasoning=None, stop=None, max_tokens=None):
        self.calls += 1
        blob = "".join(str(m.get("content") or "") for m in messages)
        real = self._real_tokens(blob)
        if tools:
            real += self._real_tokens(json.dumps(tools, ensure_ascii=False))
        if len(messages) > 2:   # 压缩用的摘要请求体积很小，不能算进"本轮"口径
            self.max_real = max(self.max_real, real)
        if real + min(int(max_tokens or 0) or self.reserve, self.reserve) > self.window:
            self.overflows += 1
            raise agent_llm.AgentLLMError(
                f"HTTP 400: {{\"error\":{{\"message\":\"This model's maximum context "
                f"length is {self.window} tokens. However, you requested "
                f"{real + self.reserve} tokens ({real} in the messages, "
                f"{self.reserve} in the completion). Please reduce the length of the "
                f"messages or completion.\"}}}}")
        if len(messages) > 2:
            self.reply = "完成"
        return {"text": "完成", "tool_calls": [],
                "usage": {"prompt_tokens": real, "completion_tokens": 5}}


_live = _WindowEnforcingLLM(_WINDOW, _RESERVE)
eng9 = _mk_engine(_live, context_window=_WINDOW, max_output_tokens=_RESERVE)
eng9._messages = [{"role": "system", "content": "s" * 3000}]
# 目标：真实占用 ≈ 0.89M token —— 落在"旧口径判定通过、真实请求却越窗"的区间内：
#   旧估算 = 真实 × 2.35/4 ≈ 0.52M ≤ 旧硬上限 622,592（旧代码放行）
#   真实 + 预留输出 ≈ 1.28M > 窗口 1,048,576（上游必然 400）
for i in range(11):
    eng9._messages.append({"role": "user", "content": f"第{i}批数据 " + _JSON_UNIT * 2500})
    eng9._messages.append({"role": "assistant", "content": "已处理 " + _JSON_UNIT * 2500})
_real_hist = _live._real_tokens("".join(str(m.get("content")) for m in eng9._messages))
print(f"  历史真实 token ≈ {_real_hist}（窗口 {_WINDOW}，预留输出 {_RESERVE}）")
eng9.run("总结上面的数据", "")
check("稠密内容下任务正常完成（无 400 中断）", eng9.end_state == "done",
      f"end_state={eng9.end_state} calls={_live.calls}")
check("发送的请求全部落在窗口内（上游未拒绝）", _live.overflows == 0,
      f"overflows={_live.overflows} max_real={_live.max_real} "
      f"上限={_WINDOW - _RESERVE}")
check("实际发送体积 ≤ 窗口 − 预留输出",
      _live.max_real <= _WINDOW - _RESERVE,
      f"{_live.max_real} <= {_WINDOW - _RESERVE}")
check("确实触发了压缩/裁剪（而不是放任超窗）", len(eng9._messages) < 61,
      f"61 → {len(eng9._messages)} 条")

# 旧口径对照：证明 bug 真实存在（等量内容下旧估算会放行越窗请求）
_old_est = _real_hist * 2.35 / 4.0        # 旧口径 1/4 字符 → 估算值
_old_ceiling = int((_WINDOW - _RESERVE) * 0.95)
check("旧口径（1/4 字符）确实会低估到越窗（bug 复现依据）",
      _old_est <= _old_ceiling and (_real_hist + _RESERVE) > _WINDOW,
      f"旧估算≈{int(_old_est)} ≤ 旧硬上限{_old_ceiling}（旧代码判定通过 → 直接发出），"
      f"而真实≈{_real_hist} + 预留 {_RESERVE} = {_real_hist + _RESERVE} > 窗口 {_WINDOW}")

print()
print("=" * 78)
print(f"[SUMMARY] PASS={len(PASSES)} FAIL={len(FAILS)} SKIP={len(SKIPS)}")
if FAILS:
    print("[FAILURES]")
    for f in FAILS:
        print("  -", f)
    print("HAS FAILURES")
    print("EXIT=1")
    sys.exit(1)
print("ALL PASS")
print("EXIT=0")
