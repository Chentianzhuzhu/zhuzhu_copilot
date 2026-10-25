"""生成并校验 perf-turbo 插件的 source_ops.json（构建期工具，不随插件分发）。

做四件事：
  1. 读取 source/perf_turbo.py，生成 source_ops.json（create_file / replace_module /
     insert_after 三类 op 混用）；
  2. 在 src/ 的**临时副本**上真实执行每条 op（用应用自己的源码变更引擎），
     再逐文件 py_compile —— 等价于「启用插件」时的编译检查；
  3. 用插件校验系统的静态规则扫描所有待写入内容（危险规则必须 0 命中）；
  4. 单独导入新建的性能内核，跑一遍缓存 / 节拍 / 报告 / 挂钩幂等。
"""

import json
import os
import py_compile
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # zhuzhu Copilot/
SRC = ROOT / "src"
PKG = SRC / "zhuzhu_Copilot" / "copilot_plugins_examples" / "perf-turbo"
KERNEL = PKG / "source" / "perf_turbo.py"

PANEL = "zhuzhu_Copilot/ui/agent_panel.py"
TOOLS = "zhuzhu_Copilot/core/agent_tools.py"

# --- op 内容：包装 _md_to_html（替换定义行，原实现改名保留） -----------------
MD_WRAPPER = '''def _md_to_html(raw: str) -> str:
    """块级 Markdown → HTML（性能插件 perf-turbo：结果按「文本 + 主题版本」缓存）。

    缓存只省「重复解析」：同一段落（表格、代码块、常用提示语）在切会话、回滚历史、
    主题切换后重建富文本时直接复用；未命中就调用原实现，输出与原来完全一致。
    """
    try:
        from zhuzhu_Copilot.core import perf_turbo as _perf
        return _perf.md_html(raw, _THEME_VERSION, _md_to_html_uncached)
    except Exception:
        return _md_to_html_uncached(raw)


def _md_to_html_uncached(raw: str) -> str:'''

# --- op 内容：流式节拍自适应（插在应用算好 delay 之后，只做小幅修正） --------
STREAM_TICK = '''        # ---- 性能插件 perf-turbo：按档位与实测主线程响应间隔微调内容节拍 ----
        # 应用自身已按「单帧成本 / 长任务区块数」算好 delay；这里只在其基础上做小幅
        # 修正（机器吃力时放大、轻快时压密），任何异常都保留应用原值。
        try:
            from zhuzhu_Copilot.core import perf_turbo as _perf
            delay = _perf.stream_tick(delay, n)
        except Exception:
            pass'''

# --- op 内容：工具 schema ---------------------------------------------------
SCHEMA = '''    # ---- 性能插件 perf-turbo：性能报告工具 ----
    {
        "type": "function",
        "function": {
            "name": "perf_report",
            "description": "查看当前性能画像：主线程响应间隔、模型首字延迟与输出吞吐、"
                           "各工具耗时排名、正文渲染缓存命中率，并给出优化建议。"
                           "用户问「为什么这么慢」「卡在哪一步」或你想自评执行效率时调用。",
            "parameters": {"type": "object",
                           "properties": {
                               "section": {"type": "string",
                                           "enum": ["ui", "llm", "tools", "turns", "advice"],
                                           "description": "可选：只看某一节"}},
                           "required": []},
        },
    },'''

# --- op 内容：工具分发接线 ---------------------------------------------------
DISPATCH = '''        # ---- 性能插件 perf-turbo：性能报告工具接线 ----
        if name == "perf_report":
            from zhuzhu_Copilot.core import perf_turbo
            return perf_turbo.tool_report(args)'''


def build_ops() -> list:
    kernel_src = KERNEL.read_text(encoding="utf-8")
    return [
        {"op": "create_file",
         "path": "zhuzhu_Copilot/core/perf_turbo.py",
         "content": kernel_src},
        {"op": "replace_module",
         "path": PANEL,
         "start": "def _md_to_html(raw: str) -> str:",
         "end": "def _md_to_html(raw: str) -> str:",
         "content": MD_WRAPPER},
        {"op": "insert_after",
         "path": PANEL,
         "anchor": "        delay = _STREAM_TICK_MS if n < _STREAM_BIG_CHARS else _STREAM_TICK_BIG_MS",
         "content": STREAM_TICK},
        {"op": "insert_after",
         "path": TOOLS,
         "anchor": "TOOLS = [",
         "content": SCHEMA},
        {"op": "insert_after",
         "path": TOOLS,
         "anchor": '            return _revert_copilot_plugin(str(args.get("name", "")))',
         "content": DISPATCH},
    ]


def write_ops(ops: list) -> None:
    (PKG / "source_ops.json").write_text(
        json.dumps(ops, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[1] source_ops.json 已生成：%d 条 op，%d 个目标文件"
          % (len(ops), len({o["path"] for o in ops})))


def apply_to_temp(ops: list) -> bool:
    """在 src 的临时副本上真实执行 op（走应用自己的引擎），再编译检查。"""
    tmp = Path(tempfile.mkdtemp(prefix="perfops_"))
    root = tmp / "src"
    for rel in {o["path"] for o in ops if o["op"] != "create_file"}:
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SRC / rel, dst)
    ok_all = True
    try:
        sys.path.insert(0, str(SRC))
        from zhuzhu_Copilot.core import plugin_source
        for i, op in enumerate(ops):
            try:
                rec = plugin_source._apply_op(root, op)
                print("    op%-2d %-14s %s" % (i + 1, op["op"], rec["path"]))
            except Exception as e:
                print("    op%-2d 失败：%s" % (i + 1, e))
                ok_all = False
        for rel in sorted({o["path"] for o in ops}):
            f = root / rel
            try:
                py_compile.compile(str(f), doraise=True)
            except py_compile.PyCompileError as e:
                print("    编译失败 %s：%s" % (rel, str(e)[:400]))
                ok_all = False
    finally:
        sys.path.remove(str(SRC))
    print("[2] 临时副本执行 + 编译检查：%s" % ("通过" if ok_all else "失败"))
    if ok_all:
        _check_merged(root)
    return ok_all


def _check_merged(root: Path) -> None:
    """静态确认改动落位正确（新符号存在、原实现仍在、节拍钩子在方法体内）。"""
    panel = (root / PANEL).read_text(encoding="utf-8")
    tools = (root / TOOLS).read_text(encoding="utf-8")
    checks = [
        ("_md_to_html 包装存在", "def _md_to_html(raw: str) -> str:" in panel),
        ("原实现已改名保留", "def _md_to_html_uncached(raw: str) -> str:" in panel),
        ("原实现文档串仍在", '"""块级 Markdown → HTML：代码块、标题、列表、段落、表格"""' in panel),
        ("节拍钩子已插入", "_perf.stream_tick(delay, n)" in panel),
        ("perf_report schema 已插入", '"name": "perf_report"' in tools),
        ("perf_report 分发已接线", 'if name == "perf_report":' in tools),
    ]
    bad = [n for n, ok in checks if not ok]
    print("[3] 落位检查：%s" % ("全部通过" if not bad else "缺失 " + "、".join(bad)))
    if bad:
        raise SystemExit(1)


def guard_scan(ops: list) -> None:
    """插件校验系统的静态规则必须 0 命中（危险规则一命中插件会被整体删除）。"""
    sys.path.insert(0, str(SRC))
    try:
        from zhuzhu_Copilot.core import plugin_guard
        hits = []
        for i, op in enumerate(ops):
            for f in plugin_guard.static_scan(op["content"],
                                             str(op["path"])):
                hits.append((i + 1, f))
        for name in ("service/perf_service.py", "pages/perf_page.py"):
            text = (PKG / name).read_text(encoding="utf-8")
            for f in plugin_guard.static_scan(text, name):
                hits.append((name, f))
    finally:
        sys.path.remove(str(SRC))
    print("[4] 静态规则扫描：%d 处命中" % len(hits))
    for where, f in hits:
        print("    %s → %s（%s）第 %s 行：%s"
              % (where, f["rule"], f["severity"], f["line"], f["snippet"][:70]))
    if any(f["severity"] == "danger" for _w, f in hits):
        raise SystemExit("存在 danger 级命中，插件会被校验系统删除")


def kernel_selftest() -> None:
    """在**独立子进程**里导入性能内核，跑一遍缓存 / 节拍 / 报告 / 挂钩幂等。

    独立进程是必须的：本进程此前已导入过 src 下的 zhuzhu_Copilot，
    同进程内换路径导入会命中已缓存的包对象，测不到临时副本。
    """
    import subprocess
    tmp = Path(tempfile.mkdtemp(prefix="perfker_"))
    pkg = tmp / "zhuzhu_Copilot" / "core"
    pkg.mkdir(parents=True)
    (tmp / "zhuzhu_Copilot" / "__init__.py").write_text("", encoding="utf-8")
    shutil.copy2(KERNEL, pkg / "perf_turbo.py")
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    script = tmp / "selftest.py"
    script.write_text(_SELFTEST, encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(tmp))
    r = subprocess.run([sys.executable, str(script)], capture_output=True,
                       text=True, env=env, cwd=str(tmp))
    out = (r.stdout or "") + (r.stderr or "")
    print(out.strip()[-3000:])
    if r.returncode != 0:
        raise SystemExit("内核自测失败")
    print("[5] 内核自测通过（缓存/节拍/统计/报告/挂钩）")


_SELFTEST = '''
from zhuzhu_Copilot.core import perf_turbo as k

calls = {"n": 0}
def render(text):
    calls["n"] += 1
    return "<p>%d</p>" % len(text)

body = "x" * 300
a = k.md_html(body, 1, render)
b = k.md_html(body, 1, render)
assert a == b and calls["n"] == 1, "缓存未命中或结果不一致"
k.md_html(body, 2, render)
assert calls["n"] == 2, "主题版本变更后应重渲染"
assert k.md_html("short", 1, render) and calls["n"] == 3, "短文本应直通渲染"

k.note_frame(33.0)
k.note_frame(180.0)
assert k.stream_tick(4) >= 2
assert k.set_profile("conservative") == "conservative"
assert k.stream_tick(4) >= 8, "保守档应放大节拍"
assert k.set_profile("aggressive") == "aggressive"
assert k.stream_tick(16) <= 16, "激进档只应往更密方向压"
k.set_profile("balanced")
assert k.set_profile("nonsense") == "balanced", "非法档位应回落均衡"

k.note_request(800, 4200, 900, 52000, ok=True, model="m")
k.note_tool("read_file", 120)
k.note_tool("run_command", 6400)
k.note_turn(9.5, 3)
snap = k.snapshot()
assert snap["llm"]["count"] == 1 and snap["tool_calls"] == 2
assert snap["tools"][0]["name"] == "run_command", "慢工具应排第一"
text = k.report_text()
assert "性能报告" in text and "建议" in text
assert k.tool_report({"section": "llm"})["text"]
assert k.tool_report({})["text"]
assert k.tool_report({"section": "bogus"})["text"]
k.reset()
assert k.snapshot()["tool_calls"] == 0, "reset 应清空统计"
print("OK")
'''


if __name__ == "__main__":
    ops = build_ops()
    write_ops(ops)
    ok = apply_to_temp(ops)
    guard_scan(ops)
    kernel_selftest()
    if not ok:
        raise SystemExit("临时副本校验失败")
    print("\n全部校验通过。插件包：%s" % PKG)
