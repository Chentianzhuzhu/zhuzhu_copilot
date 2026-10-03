"""zhuzhu Copilot 启动 / 交互性能基线探针（真实计时，非 mock）。

用法：
    python scripts/perf_bench.py                 # 全量
    python scripts/perf_bench.py --stage boot    # boot|imports|panel|hot|inter
    python scripts/perf_bench.py --repeat 5

设计要点：
1. 每个阶段跑在**独立子进程**（冷启动、无缓存互相加热），取 min/median/p95。
2. 面板构造走**真实**代码路径：不注入任何 shim/桩，缺符号即真实抛错 ——
   探针因此同时充当「面板能否构造」的 smoke 检查。
3. imports 阶段用 -X importtime 做真实的导入耗时归因（累计耗时 Top N）。

输出：毫秒。
"""
import argparse
import os
import re
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
PY = sys.executable

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# --------------------------------------------------------------------------
# 子进程内执行的代码片段
# --------------------------------------------------------------------------

_SNIPPETS = {
    "interpreter": r"""
import time
_T0 = time.perf_counter()
print("RESULT", f"{time.perf_counter()-_T0:.4f}")
""",
    "import_pyqt6": r"""
import time
_T0 = time.perf_counter()
from PyQt6.QtWidgets import QApplication
from PyQt6.QtWebEngineWidgets import QWebEngineView
print("RESULT", f"{time.perf_counter()-_T0:.4f}")
""",
    "import_agent_panel": r"""
import time
_T0 = time.perf_counter()
import zhuzhu_Copilot.ui.agent_panel
print("RESULT", f"{time.perf_counter()-_T0:.4f}")
""",
    "qapp": r"""
import time, sys
from PyQt6.QtWidgets import QApplication
_T0 = time.perf_counter()
app = QApplication(sys.argv)
app.processEvents()
print("RESULT", f"{time.perf_counter()-_T0:.4f}")
""",
    "decrypt_config": r"""
import time
_T0 = time.perf_counter()
from zhuzhu_Copilot.core import agent_llm, agent_skills
agent_skills.load_settings()
agent_llm.load_model_config()
print("RESULT", f"{time.perf_counter()-_T0:.4f}")
""",
    "panel_construct": r"""
import time, sys
from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv)
import zhuzhu_Copilot.ui.agent_panel as ap
_T0 = time.perf_counter()
p = ap.AgentPanel(None)
_T1 = time.perf_counter()
p.show(); app.processEvents()
_T2 = time.perf_counter()
print("RESULT", f"{_T1-_T0:.4f} {_T2-_T1:.4f}")
""",
    "prewarm": r"""
import time, sys
from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv)
import zhuzhu_Copilot.ui.agent_panel as ap
p = ap.AgentPanel(None)
p.show(); app.processEvents()
_T0 = time.perf_counter()
try:
    p.prewarm_copilot_panel()
except Exception as e:
    print(f"ERR {e!r}", file=sys.stderr)
app.processEvents()
print("RESULT", f"{time.perf_counter()-_T0:.4f}")
""",
    "startup_init": r"""
import time, sys
from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv)
import zhuzhu_Copilot.ui.agent_panel as ap
p = ap.AgentPanel(None)
p.show(); app.processEvents()
# _finish_startup_init 由 QTimer.singleShot(0) 排入，此处驱动它并计时
_T0 = time.perf_counter()
for _ in range(6):
    app.processEvents()
_T1 = time.perf_counter()
# 会话初始化：_init_sessions（文档扫描 + 恢复上次会话）
_T2 = time.perf_counter()
try:
    p._init_sessions()
except Exception as e:
    print(f"ERR {e!r}", file=sys.stderr)
_T3 = time.perf_counter()
print("RESULT", f"{_T1-_T0:.4f} {_T3-_T2:.4f}")
""",
    "interactions": r"""
import time, sys
from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv)
import zhuzhu_Copilot.ui.agent_panel as ap
p = ap.AgentPanel(None)
p.show(); app.processEvents()
try:
    p._init_sessions()
except Exception:
    pass
app.processEvents()
out = []

def _t(name, fn, n=3):
    best = 1e9
    for _ in range(n):
        _a = time.perf_counter()
        try:
            fn()
        except Exception as e:
            out.append(f"{name}=ERR:{type(e).__name__}")
            return
        app.processEvents()
        best = min(best, (time.perf_counter() - _a) * 1000)
    out.append(f"{name}={best:.1f}")

# 1) 气泡渲染：20 段混合内容（思考/操作/正文）
from zhuzhu_Copilot.ui import agent_chat_bubbles as cb
segs = []
for i in range(20):
    segs.append({"type": "text", "raw": f"正文段落 {i} " + "内容" * 40})
def _render():
    w = cb.ChatTurn(p._chat_style(), segs, p)
    w.render(segs)
_t("ChatTurn.render x20段", _render)

# 2) 打开设置对话框
def _dlg():
    d = ap._AgentSettingsDialog(p)
    d.deleteLater()
_t("设置对话框构造", _dlg, 2)

# 3) 主题切换（就地重建）
def _retheme():
    try:
        p._retheme()
    except Exception:
        raise
_t("主题切换 _retheme", _retheme, 2)

# 4) 会话切换
def _sw():
    try:
        p._switch_to(p._session_id)
    except Exception:
        raise
_t("会话切换 _switch_to", _sw, 2)

# 5) 气泡宽度重算（resize 路径）
def _rs():
    try:
        p._rebuild_bubbles_after_resize()
    except Exception:
        raise
_t("resize 气泡重建", _rs)

print("RESULT", " ".join(out))
""",
}


# 单阶段子进程超时（秒）：预热链路含全盘应用扫描，实测可达数分钟。
_SNIPPET_TIMEOUT_S = 120


def _stat(samples):
    s = sorted(samples)
    return {
        "min": s[0],
        "median": statistics.median(s),
        "p95": s[min(len(s) - 1, int(len(s) * 0.95))],
        "max": s[-1],
    }


def _fmt(name, samples, unit="ms"):
    st = _stat(samples)
    return (f"  {name:<32} min {st['min']:>8.1f}  median {st['median']:>8.1f}  "
            f"p95 {st['p95']:>8.1f}  max {st['max']:>8.1f} {unit}")


def _clean_env():
    """测量环境：剔除宿主注入的 PYTHONPATH 噪声。

    本会话的 PYTHONPATH 默认指向 WorkBuddy 的 sitecustomize shim，每次解释器启动
    都会额外导入它（实测 ~790ms，见 -X importtime 的 sitecustomize 行）。打包后的
    真实 exe 不存在该路径，若不剔除会把基线整体抬高约 0.8-1.2s。
    """
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = SRC
    return env


def _run_snippet(key, repeat):
    code = _SNIPPETS[key]
    samples = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        try:
            r = subprocess.run(
                [PY, "-c", code], cwd=SRC, capture_output=True, text=True,
                env=_clean_env(), timeout=_SNIPPET_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            # 预热链路含全盘应用扫描，实测可达数分钟，不能让探针无限等待
            print(f"  [{key}] TIMEOUT >{_SNIPPET_TIMEOUT_S}s"
                  f"（该阶段可能触发全盘扫描，已跳过）")
            continue
        wall = (time.perf_counter() - t0) * 1000
        # 应用自身会往 stdout 打日志，只认带 RESULT 标记的那一行
        res = [ln for ln in r.stdout.splitlines() if ln.startswith("RESULT")]
        if r.returncode != 0 or not res:
            print(f"  [{key}] FAILED rc={r.returncode}")
            print("   " + (r.stderr or "").strip()[-600:].replace("\n", "\n   "))
            continue
        samples.append((wall, res[-1][len("RESULT"):].strip()))
    return samples


def stage_boot(repeat):
    print("\n=== [boot] 冷启动各阶段（独立进程；wall = 含解释器/进程开销）===")
    for key in ("interpreter", "import_pyqt6", "import_agent_panel", "qapp",
                "decrypt_config"):
        s = _run_snippet(key, repeat)
        if not s:
            continue
        inner = [float(x.split()[0]) * 1000 for _, x in s]
        wall = [w for w, _ in s]
        print(_fmt(f"{key} (内部)", inner))
        print(_fmt(f"{key} (wall)", wall))


def stage_imports(repeat):
    """-X importtime 归因：找出导入链上真正的耗时大户。"""
    print("\n=== [imports] 导入耗时归因 -X importtime（累计 Top 25）===")
    agg = {}
    for _ in range(repeat):
        r = subprocess.run(
            [PY, "-X", "importtime", "-c",
             "import zhuzhu_Copilot.ui.agent_panel"],
            cwd=SRC, capture_output=True, text=True,
            env=_clean_env(),
        )
        for line in r.stderr.splitlines():
            m = re.match(r"import time:\s+(\d+)\s+\|\s+(\d+)\s+\|\s*(\S+)", line)
            if not m:
                continue
            self_us, cum_us, name = int(m.group(1)), int(m.group(2)), m.group(3)
            a = agg.setdefault(name, {"self": [], "cum": []})
            a["self"].append(self_us / 1000.0)
            a["cum"].append(cum_us / 1000.0)
    rows = [(n, statistics.median(v["cum"]), statistics.median(v["self"]))
            for n, v in agg.items()]
    rows.sort(key=lambda x: -x[1])
    print(f"  {'module':<52}{'cumulative':>12}{'self':>10}")
    for n, cum, slf in rows[:25]:
        print(f"  {n:<52}{cum:>10.1f}ms{slf:>8.1f}ms")


def stage_panel(repeat):
    print("\n=== [panel] 面板构造 / 首帧 / 延迟初始化（独立进程）===")
    s = _run_snippet("panel_construct", repeat)
    if s:
        con = [float(x.split()[0]) * 1000 for _, x in s]
        paint = [float(x.split()[1]) * 1000 for _, x in s if len(x.split()) > 1]
        print(_fmt("AgentPanel 构造", con))
        print(_fmt("show + 首帧", paint))
    s = _run_snippet("startup_init", repeat)
    if s:
        d0 = [float(x.split()[0]) * 1000 for _, x in s]
        d1 = [float(x.split()[1]) * 1000 for _, x in s if len(x.split()) > 1]
        print(_fmt("singleShot(0) 延迟初始化", d0))
        print(_fmt("_init_sessions", d1))


def stage_hot(repeat):
    print("\n=== [hot] 预热（独立进程）===")
    s = _run_snippet("prewarm", repeat)
    if s:
        print(_fmt("prewarm_copilot_panel",
                   [float(x.split()[0]) * 1000 for _, x in s]))


def stage_inter(repeat):
    print("\n=== [inter] 交互操作（面板内，取多次最优值）===")
    s = _run_snippet("interactions", max(1, repeat - 2))
    if not s:
        return
    keys = {}
    for _, line in s:
        for tok in line.split():
            if "=" in tok:
                k, v = tok.split("=", 1)
                keys.setdefault(k, []).append(float(v) if v[:4] != "ERR:" else v)
    for k, v in keys.items():
        nums = [x for x in v if isinstance(x, float)]
        errs = [x for x in v if isinstance(x, str)]
        if nums:
            print(f"  {k:<32} best {min(nums):>8.1f} ms   max {max(nums):>8.1f} ms")
        if errs:
            print(f"  {k:<32} {errs[0]}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--stage", default="all")
    p.add_argument("--repeat", type=int, default=3)
    args = p.parse_args()

    print(f"python : {PY}")
    print(f"repeat : {args.repeat}")
    print(f"platform: QT_QPA_PLATFORM={os.environ.get('QT_QPA_PLATFORM')}")

    stages = {
        "boot": lambda: stage_boot(args.repeat),
        "imports": lambda: stage_imports(max(1, args.repeat - 1)),
        "panel": lambda: stage_panel(args.repeat),
        "hot": lambda: stage_hot(args.repeat),
        "inter": lambda: stage_inter(args.repeat),
    }
    todo = ([k for k in stages] if args.stage == "all"
            else [k.strip() for k in args.stage.split(",") if k.strip() in stages])
    # 各阶段子进程相互独立 → 并行跑，缩短墙上时间
    with ThreadPoolExecutor(max_workers=len(todo)) as ex:
        list(ex.map(lambda k: stages[k](), todo))


if __name__ == "__main__":
    main()
