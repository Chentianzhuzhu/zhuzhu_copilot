"""性能内核（Copilot 插件 perf-turbo）。

四条能力，全部是**旁路增强**，任何一步失败都静默回落原实现，不影响主流程：

  1. Markdown 渲染缓存 `md_html()`
     正文渲染（`ui/agent_panel._md_to_html`）的结果按「文本 + 主题版本」缓存。
     流式期间每段已有块级缓存，这里补的是**跨消息/跨会话**那一层：切会话、回滚历史、
     主题切换后重建富文本时，相同段落（表格、代码块、提示语）不再重复解析。

  2. 流式节拍自适应 `stream_tick()`
     按「档位 + 实测主线程响应间隔」给出本帧的内容刷新间隔：机器吃力时自动放大间隔
     保住主线程，机器轻快时压到应用默认值以下，落字更顺。

  3. 运行时指标采集（install/uninstall）
     包装 `LLMClient.chat_stream` 记首字延迟（TTFT）/总耗时/输出字数/输入体积，
     包装 `execute_tool` 记每个工具耗时，包装 `AgentEngine.run` 记整轮耗时；
     界面侧由服务用 QTimer 心跳采样主线程响应间隔。

  4. 报告与诊断 `report_text()`
     把上述指标整理成可读报告 + 规则化建议（哪一步慢、慢在渲染还是模型还是工具）。

设计约束：本模块**不 import PyQt**（界面侧由插件包内的服务/页面负责），
可脱离 GUI 单独导入与测试；所有共享状态走锁，工具并发执行下同样安全。
"""

import json
import threading
import time
from collections import OrderedDict, deque

# ---------------------------------------------------------------------------
# 档位
# ---------------------------------------------------------------------------
#: 三档预设：tick = 流式内容刷新下限（ms），cache_mb = 渲染缓存预算（MB）
PROFILES = {
    "aggressive": {"label": "激进", "tick": 2, "cache_mb": 12,
                   "desc": "低延迟优先：更密的刷新、更大的渲染缓存（适合高配机）"},
    "balanced": {"label": "均衡", "tick": 4, "cache_mb": 6,
                 "desc": "跟随应用默认值，按实测帧延迟自适应（推荐）"},
    "conservative": {"label": "保守", "tick": 8, "cache_mb": 3,
                     "desc": "省主线程优先：放宽刷新间隔，牺牲一点顺滑度（低配/长文）"},
}

_state = {"profile": "balanced", "installed": False, "hooks": {}}
_lock = threading.Lock()

#: 主线程响应间隔采样（ms，基线 33ms；>66ms 视为一次卡顿）
_FRAME = deque(maxlen=900)
#: 最近 LLM 请求记录
_REQ = deque(maxlen=120)
#: 工具耗时：name -> {"n", "total", "max", "samples"(deque)}
_TOOL = {}
#: 最近任务轮次
_TURN = deque(maxlen=40)
#: Markdown 渲染缓存统计
_MD = {"hit": 0, "miss": 0, "saved_chars": 0, "entries": 0, "chars": 0}

_MD_MIN_CHARS = 160          # 短文本不值得进缓存（命中率低、反而制造噪声）
_MD_CACHE = OrderedDict()    # key=(theme_version, text) -> (html, weight)
_MD_LOCK = threading.Lock()

_PROFILE_FILE = "perf_turbo.json"


# ---------------------------------------------------------------------------
# 档位读写（持久化到用户数据目录，随应用启动恢复）
# ---------------------------------------------------------------------------
def _profile_path():
    try:
        from zhuzhu_Copilot import app_identity
        return app_identity.data_root() / _PROFILE_FILE
    except Exception:
        return None


def get_profile() -> str:
    """当前档位名（aggressive / balanced / conservative）。"""
    return str(_state.get("profile") or "balanced")


def set_profile(name: str) -> str:
    """切换档位并落盘。非法名回落 balanced，返回实际生效的档位名。"""
    key = str(name or "").strip().lower()
    if key not in PROFILES:
        key = "balanced"
    with _lock:
        _state["profile"] = key
    p = _profile_path()
    if p is not None:
        try:
            p.write_text(json.dumps({"profile": key}, ensure_ascii=False),
                         encoding="utf-8")
        except OSError:
            pass
    return key


def load_profile() -> str:
    """从用户数据目录恢复上次选择的档位（缺失/损坏 → balanced）。"""
    p = _profile_path()
    if p is None:
        return get_profile()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        key = str((data or {}).get("profile") or "").strip().lower()
    except (OSError, ValueError):
        key = ""
    return set_profile(key) if key in PROFILES else get_profile()


def _profile_conf() -> dict:
    return PROFILES.get(get_profile()) or PROFILES["balanced"]


# ---------------------------------------------------------------------------
# 1. Markdown 渲染缓存
# ---------------------------------------------------------------------------
def md_html(raw, theme_version, render) -> str:
    """带跨会话缓存的 Markdown 渲染。

    raw            原文（一段 Markdown）
    theme_version  主题版本号（主题切换后整批缓存自动失效，避免旧色板残留）
    render         真正的渲染函数（缓存未命中时调用）
    """
    text = raw if isinstance(raw, str) else str(raw or "")
    if len(text) < _MD_MIN_CHARS:
        return render(text)
    key = (int(theme_version or 0), text)
    with _MD_LOCK:
        hit = _MD_CACHE.get(key)
        if hit is not None:
            _MD_CACHE.move_to_end(key)
            _MD["hit"] += 1
            _MD["saved_chars"] += len(text)
            return hit[0]
    out = render(text)
    weight = len(text) + len(out)
    budget = max(1, int(_profile_conf().get("cache_mb") or 6)) * 1024 * 1024
    with _MD_LOCK:
        _MD["miss"] += 1
        old = _MD_CACHE.get(key)
        if old is not None:                       # 并发下同键已被别的线程写入
            _MD["chars"] -= old[1]
        _MD_CACHE[key] = (out, weight)
        _MD["chars"] += weight
        while _MD["chars"] > budget and len(_MD_CACHE) > 1:
            _k, _v = _MD_CACHE.popitem(last=False)
            _MD["chars"] -= _v[1]
        _MD["entries"] = len(_MD_CACHE)
    return out


def clear_md_cache() -> None:
    """清空渲染缓存（主题大改/内存紧张时调用）。"""
    with _MD_LOCK:
        _MD_CACHE.clear()
        _MD["entries"] = 0
        _MD["chars"] = 0


# ---------------------------------------------------------------------------
# 2. 流式节拍自适应
# ---------------------------------------------------------------------------
def frame_p95() -> float:
    """最近窗口的主线程响应间隔 P95（ms）；无采样返回 0。"""
    with _lock:
        samples = sorted(_FRAME)
    if not samples:
        return 0.0
    return _pct(samples, 0.95)


def stream_tick(default_ms: int, text_len: int = 0) -> int:
    """本帧的流式内容刷新间隔（ms）。

    default_ms 是应用算好的默认节拍（已含「单帧成本 / 长任务区块数」的自适应结果），
    本函数只在它基础上按档位与实测响应间隔做小幅修正，绝不越过 2~60ms 的合理区间。
    """
    base = int(default_ms or 4)
    prof = _profile_conf()
    tick = int(prof.get("tick") or 4)
    if get_profile() == "aggressive":
        base = min(base, tick)          # 只往「更密」方向压
    elif get_profile() == "conservative":
        base = max(base, tick)          # 只往「更疏」方向放
    p95 = frame_p95()
    if p95 > 80:                        # 主线程明显吃力：翻倍拉开间隔
        base = max(base, base * 2)
    elif p95 > 50:
        base = max(base, int(base * 1.5))
    return max(2, min(60, int(base)))


# ---------------------------------------------------------------------------
# 3. 指标采集
# ---------------------------------------------------------------------------
def _pct(sorted_vals, q: float) -> float:
    """已排序列表的分位数（线性插值；空列表返回 0）。"""
    n = len(sorted_vals)
    if not n:
        return 0.0
    if n == 1:
        return float(sorted_vals[0])
    pos = (n - 1) * max(0.0, min(1.0, q))
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return float(sorted_vals[lo]) * (1 - frac) + float(sorted_vals[hi]) * frac


def note_frame(interval_ms: float) -> None:
    """记录一次主线程响应间隔（由插件的界面心跳定时器调用）。"""
    try:
        v = float(interval_ms)
    except (TypeError, ValueError):
        return
    if v <= 0:
        return
    with _lock:
        _FRAME.append(round(min(v, 5000.0), 1))


def note_request(ttft_ms: float, total_ms: float, out_chars: int,
                 in_chars: int, ok: bool = True, model: str = "") -> None:
    """记录一次 LLM 请求：首字延迟 / 总耗时 / 输出字数 / 输入体积。"""
    try:
        rec = {"ttft": max(0.0, float(ttft_ms or 0)),
               "total": max(0.0, float(total_ms or 0)),
               "out": int(out_chars or 0), "in": int(in_chars or 0),
               "ok": bool(ok), "model": str(model or ""),
               "at": time.time()}
    except (TypeError, ValueError):
        return
    with _lock:
        _REQ.append(rec)


def note_tool(name: str, ms: float) -> None:
    """记录一次工具调用耗时（并发工具线程安全）。"""
    key = str(name or "").strip()
    if not key:
        return
    try:
        v = float(ms or 0.0)
    except (TypeError, ValueError):
        return
    with _lock:
        rec = _TOOL.get(key)
        if rec is None:
            rec = {"n": 0, "total": 0.0, "max": 0.0, "samples": deque(maxlen=60)}
            _TOOL[key] = rec
        rec["n"] += 1
        rec["total"] += v
        rec["max"] = max(rec["max"], v)
        rec["samples"].append(v)


def note_turn(seconds: float, tools: int = 0) -> None:
    """记录一次 Agent 任务（回合）的耗时。"""
    try:
        with _lock:
            _TURN.append({"seconds": round(float(seconds or 0.0), 2),
                          "tools": int(tools or 0), "at": time.time()})
    except (TypeError, ValueError):
        pass


def _messages_chars(messages) -> int:
    """估算请求输入体积（字符数）：只做长度累加，不序列化（大上下文下更快）。"""
    total = 0
    try:
        for m in messages or []:
            if not isinstance(m, dict):
                continue
            c = m.get("content")
            if isinstance(c, str):
                total += len(c)
            elif isinstance(c, list):
                for part in c:
                    if isinstance(part, dict):
                        total += len(str(part.get("text") or ""))
    except Exception:
        return total
    return total


# ---------------------------------------------------------------------------
# 4. 快照与报告
# ---------------------------------------------------------------------------
def snapshot() -> dict:
    """当前性能画像（界面页与工具共用）。"""
    with _lock:
        frames = sorted(_FRAME)
        reqs = list(_REQ)
        tools = {k: {"n": v["n"], "total": v["total"], "max": v["max"],
                     "samples": list(v["samples"])} for k, v in _TOOL.items()}
        turns = list(_TURN)
    with _MD_LOCK:
        md = dict(_MD)
    hit = int(md.get("hit") or 0)
    miss = int(md.get("miss") or 0)
    ui = {
        "samples": len(frames),
        "p50": round(_pct(frames, 0.50), 1),
        "p95": round(_pct(frames, 0.95), 1),
        "max": round(frames[-1], 1) if frames else 0.0,
        "slow_ratio": round(
            (sum(1 for v in frames if v > 66) / len(frames) * 100.0), 1
        ) if frames else 0.0,
    }
    ttfts = sorted([r["ttft"] for r in reqs if r.get("ok")])
    totals = sorted([r["total"] for r in reqs if r.get("ok")])
    outs = [r["out"] for r in reqs if r.get("ok") and r.get("out")]
    ins = [r["in"] for r in reqs if r.get("ok")]
    llm = {
        "count": len(reqs),
        "failed": sum(1 for r in reqs if not r.get("ok")),
        "ttft_p50": round(_pct(ttfts, 0.50), 0),
        "ttft_p95": round(_pct(ttfts, 0.95), 0),
        "total_p50": round(_pct(totals, 0.50), 0),
        "in_avg": int(sum(ins) / len(ins)) if ins else 0,
        "in_max": max(ins) if ins else 0,
        "out_avg": int(sum(outs) / len(outs)) if outs else 0,
        # 输出吞吐（字/秒）：用「总耗时 - 首字延迟」作分母，衡量真正在吐字的速度
        "cps": round(sum(outs) / max(0.001, sum(max(0.0, r["total"] - r["ttft"])
                                                for r in reqs if r.get("ok"))
                               / 1000.0), 1) if outs else 0.0,
    }
    tool_rows = []
    for name, rec in tools.items():
        samples = sorted(rec["samples"])
        tool_rows.append({
            "name": name, "n": rec["n"],
            "avg": round(rec["total"] / rec["n"], 1) if rec["n"] else 0.0,
            "p95": round(_pct(samples, 0.95), 1),
            "max": round(rec["max"], 1),
        })
    tool_rows.sort(key=lambda r: (-r["p95"], -r["n"]))
    return {
        "profile": get_profile(),
        "profile_label": _profile_conf().get("label") or get_profile(),
        "ui": ui,
        "llm": llm,
        "tools": tool_rows[:12],
        "tool_calls": sum(r["n"] for r in tool_rows),
        "md": {
            "hit": hit, "miss": miss,
            "hit_ratio": round(hit / (hit + miss) * 100.0, 1) if (hit + miss) else 0.0,
            "saved_chars": int(md.get("saved_chars") or 0),
            "entries": int(md.get("entries") or 0),
            "chars": int(md.get("chars") or 0),
        },
        "turns": turns[-10:],
        "installed": bool(_state.get("installed")),
    }


def _suggestions(snap: dict) -> list:
    """按指标给出可执行的优化建议（规则化，不猜）。"""
    out = []
    ui = snap.get("ui") or {}
    llm = snap.get("llm") or {}
    md = snap.get("md") or {}
    if ui.get("samples", 0) >= 30:
        if ui.get("p95", 0) > 66 or ui.get("slow_ratio", 0) > 5:
            out.append("主线程响应 P95 %.0fms、卡顿占比 %.1f%%：界面侧有长任务占着主线程。"
                       "可切「保守」档放宽刷新间隔，或减少同屏超长输出（子 Agent/命令结果）。"
                       % (ui.get("p95", 0), ui.get("slow_ratio", 0)))
        elif ui.get("p95", 0) < 40 and get_profile() != "aggressive":
            out.append("主线程响应 P95 %.0fms，余量充足：可切「激进」档让落字更密。"
                       % ui.get("p95", 0))
    if llm.get("count", 0) >= 3:
        if llm.get("ttft_p95", 0) > 3000:
            out.append("首字延迟 P95 %.1fs：主要由「输入体积 + 上游排队」决定，"
                       "平均输入 %s 字符。压缩上下文（少贴大文件全文、多用定位读）能直接降下来。"
                       % (llm.get("ttft_p95", 0) / 1000.0,
                          format(llm.get("in_avg", 0), ",")))
        if llm.get("cps", 0) and llm.get("cps", 0) < 15:
            out.append("输出吞吐仅 %.1f 字/秒：低于常见水平，多为上游限速或模型档位偏高，"
                       "换更快的模型/降低思考档位可提速。" % llm.get("cps", 0))
        if llm.get("in_avg", 0) > 120000:
            out.append("平均单请求输入 %s 字符（偏大）：长会话建议新开会话或让 Agent 先总结再继续。"
                       % format(llm.get("in_avg", 0), ","))
    slow = [t for t in (snap.get("tools") or []) if t.get("p95", 0) > 3000]
    if slow:
        out.append("慢工具：" + "、".join("%s（P95 %.1fs）" % (t["name"], t["p95"] / 1000.0)
                                       for t in slow[:3])
                   + "。同类任务可换更轻的等价工具（如用 grep/search_code 代替读整文件）。")
    if md.get("hit", 0) + md.get("miss", 0) >= 50:
        if md.get("hit_ratio", 0) < 25:
            out.append("渲染缓存命中率 %.1f%%（偏低）：会话内容每次都在变属正常；"
                       "若频繁切会话，命中率会显著上升。" % md.get("hit_ratio", 0))
        else:
            out.append("渲染缓存命中率 %.1f%%，已省下 %s 字符的重复解析。"
                       % (md.get("hit_ratio", 0), format(md.get("saved_chars", 0), ",")))
    if not out:
        out.append("暂无明显瓶颈：继续用一段时间后回来看趋势更有参考价值。")
    return out


def report_text() -> str:
    """可读性能报告（工具返回 / 弹窗 / 剪贴板共用）。"""
    snap = snapshot()
    ui, llm, md = snap["ui"], snap["llm"], snap["md"]
    lines = ["【性能报告】档位：%s（%s）"
             % (snap.get("profile_label"), snap.get("profile")),
             "",
             "一、界面流畅度（主线程响应间隔，基线 33ms）"]
    if ui.get("samples"):
        lines.append("  · 采样 %d 次：P50 %.0fms / P95 %.0fms / 最大 %.0fms，卡顿(>66ms)占比 %.1f%%"
                     % (ui["samples"], ui["p50"], ui["p95"], ui["max"], ui["slow_ratio"]))
    else:
        lines.append("  · 暂无采样（界面心跳未运行）")
    lines.append("  · Markdown 渲染缓存：命中 %d / 未命中 %d（命中率 %.1f%%），"
                 "省下 %s 字符重复解析，缓存 %d 段 / %.1fMB"
                 % (md["hit"], md["miss"], md["hit_ratio"],
                    format(md["saved_chars"], ","), md["entries"],
                    md["chars"] / 1024.0 / 1024.0))
    lines += ["", "二、Agent 输出（最近 %d 次请求）" % llm["count"]]
    if llm["count"]:
        lines.append("  · 首字延迟：P50 %.2fs / P95 %.2fs"
                     % (llm["ttft_p50"] / 1000.0, llm["ttft_p95"] / 1000.0))
        lines.append("  · 单次总耗时：P50 %.2fs；输出吞吐 %.1f 字/秒"
                     % (llm["total_p50"] / 1000.0, llm["cps"]))
        lines.append("  · 输入体积：平均 %s 字符 / 最大 %s 字符；平均输出 %s 字符"
                     % (format(llm["in_avg"], ","), format(llm["in_max"], ","),
                        format(llm["out_avg"], ",")))
        if llm["failed"]:
            lines.append("  · 失败/中断请求：%d 次" % llm["failed"])
    else:
        lines.append("  · 暂无请求记录（发一轮对话后再看）")
    lines += ["", "三、工具耗时（共 %d 次调用）" % snap["tool_calls"]]
    if snap["tools"]:
        for t in snap["tools"][:8]:
            lines.append("  · %-22s %3d 次  avg %7.0fms  P95 %7.0fms  max %7.0fms"
                         % (t["name"], t["n"], t["avg"], t["p95"], t["max"]))
    else:
        lines.append("  · 暂无工具调用")
    if snap["turns"]:
        lines += ["", "四、最近任务"]
        for t in snap["turns"][-3:]:
            lines.append("  · %.1fs（工具 %d 次）" % (t["seconds"], t["tools"]))
    lines += ["", "五、建议"] + ["  · " + s for s in _suggestions(snap)]
    return "\n".join(lines)


def reset() -> None:
    """清空全部统计（缓存保留，避免清完立刻掉速）。"""
    with _lock:
        _FRAME.clear()
        _REQ.clear()
        _TOOL.clear()
        _TURN.clear()
    with _MD_LOCK:
        _MD["hit"] = 0
        _MD["miss"] = 0
        _MD["saved_chars"] = 0


# ---------------------------------------------------------------------------
# 5. 运行时挂钩（安装/卸载，幂等）
# ---------------------------------------------------------------------------
def _make_chat_stream(orig):
    """包装 LLMClient.chat_stream：量首字延迟、总耗时、输出字数、输入体积。"""

    def chat_stream(self, messages=None, tools=None, tool_choice="auto",
                    on_delta=None, on_reasoning=None, stop=None,
                    max_tokens=None):
        t0 = time.perf_counter()
        first = [0.0]
        chars = [0]

        def _delta(s):
            if not first[0]:
                first[0] = time.perf_counter()
            chars[0] += len(s or "")
            if on_delta is not None:
                on_delta(s)

        in_chars = _messages_chars(messages)
        ok = False
        try:
            res = orig(self, messages, tools, tool_choice, _delta,
                       on_reasoning, stop, max_tokens)
            ok = True
            return res
        finally:
            now = time.perf_counter()
            note_request(
                ttft_ms=((first[0] or now) - t0) * 1000.0,
                total_ms=(now - t0) * 1000.0,
                out_chars=chars[0], in_chars=in_chars, ok=ok,
                model=str(getattr(self, "model", "") or ""))

    return chat_stream


def _make_execute_tool(orig):
    """包装工具执行入口：按工具名记耗时（并发安全）。"""

    def execute_tool(name, *args, **kwargs):
        t0 = time.perf_counter()
        try:
            return orig(name, *args, **kwargs)
        finally:
            note_tool(str(name), (time.perf_counter() - t0) * 1000.0)

    return execute_tool


def _make_run(orig):
    """包装 AgentEngine.run：记整轮耗时。"""

    def run(self, *args, **kwargs):
        t0 = time.perf_counter()
        try:
            return orig(self, *args, **kwargs)
        finally:
            note_turn(time.perf_counter() - t0)

    return run


def install() -> bool:
    """安装运行时挂钩（幂等）。返回是否已安装。"""
    if _state.get("installed"):
        return True
    try:
        from zhuzhu_Copilot.core import agent_engine, agent_llm, agent_tools
    except Exception:
        return False
    hooks = _state.get("hooks") or {}
    try:
        hooks["chat_stream"] = agent_llm.LLMClient.chat_stream
        agent_llm.LLMClient.chat_stream = _make_chat_stream(hooks["chat_stream"])
        hooks["execute_tool"] = agent_tools.execute_tool
        agent_tools.execute_tool = _make_execute_tool(hooks["execute_tool"])
        hooks["run"] = agent_engine.AgentEngine.run
        agent_engine.AgentEngine.run = _make_run(hooks["run"])
    except Exception:
        uninstall()
        return False
    _state["hooks"] = hooks
    _state["installed"] = True
    return True


def uninstall() -> None:
    """还原运行时挂钩（停用插件/退出时调用）。"""
    hooks = _state.get("hooks") or {}
    try:
        from zhuzhu_Copilot.core import agent_engine, agent_llm, agent_tools
        if hooks.get("chat_stream") is not None:
            agent_llm.LLMClient.chat_stream = hooks["chat_stream"]
        if hooks.get("execute_tool") is not None:
            agent_tools.execute_tool = hooks["execute_tool"]
        if hooks.get("run") is not None:
            agent_engine.AgentEngine.run = hooks["run"]
    except Exception:
        pass
    _state["hooks"] = {}
    _state["installed"] = False


def installed() -> bool:
    return bool(_state.get("installed"))


# ---------------------------------------------------------------------------
# 6. 工具层入口（供 agent_tools 接线调用）
# ---------------------------------------------------------------------------
def tool_report(args=None) -> dict:
    """工具 `perf_report` 的实现：返回当前性能报告（可限定只看某一节）。"""
    section = str((args or {}).get("section") or "").strip().lower()
    text = report_text()
    if section:
        marks = {"ui": "一、", "llm": "二、", "tools": "三、", "turns": "四、",
                 "advice": "五、"}
        key = marks.get(section)
        if key:
            keep, buf = [], []
            for line in text.splitlines():
                if line.startswith(("一、", "二、", "三、", "四、", "五、")):
                    keep.append(buf)
                    buf = [line]
                else:
                    buf.append(line)
            keep.append(buf)
            picked = [b for b in keep if b and b[0].startswith(key)]
            head = text.splitlines()[0]
            text = "\n".join([head, ""] + (picked[0] if picked else ["（该节暂无数据）"]))
    return {"text": text, "images": []}
