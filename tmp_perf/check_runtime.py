"""perf-turbo 集成校验（构建期工具）：运行时挂钩 + 界面模块冒烟。

挂钩测试用假的 agent_llm / agent_tools / agent_engine 模块注入 sys.modules，
只验证包装逻辑（首字延迟、输出字数、工具耗时、失败留痕、还原），不拉起真实引擎。
界面测试在 offscreen Qt 下真建页面，确认无异常、指标能刷新。
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
PKG = SRC / "zhuzhu_Copilot" / "copilot_plugins_examples" / "perf-turbo"
KERNEL = PKG / "source" / "perf_turbo.py"

HOOKS_TEST = r'''
import sys, types
sys.path.insert(0, r"%(kernel_dir)s")

from zhuzhu_Copilot.core import perf_turbo as k

class FakeLLM:
    fail = False

    def chat_stream(self, messages=None, tools=None, tool_choice="auto",
                    on_delta=None, on_reasoning=None, stop=None, max_tokens=None):
        if FakeLLM.fail:
            raise RuntimeError("上游超时")
        if on_delta:
            on_delta("hello ")
            on_delta("world")
        return {"text": "hello world"}

class FakeEngine:
    def run(self, *a, **kw):
        return "done"

def fake_execute_tool(name, args, allow_dangerous=False, **kw):
    return {"text": "ok", "images": []}

llm = types.ModuleType("zhuzhu_Copilot.core.agent_llm"); llm.LLMClient = FakeLLM
tools = types.ModuleType("zhuzhu_Copilot.core.agent_tools"); tools.execute_tool = fake_execute_tool
eng = types.ModuleType("zhuzhu_Copilot.core.agent_engine"); eng.AgentEngine = FakeEngine
sys.modules["zhuzhu_Copilot.core.agent_llm"] = llm
sys.modules["zhuzhu_Copilot.core.agent_tools"] = tools
sys.modules["zhuzhu_Copilot.core.agent_engine"] = eng

orig_chat, orig_tool, orig_run = FakeLLM.chat_stream, tools.execute_tool, FakeEngine.run
assert k.install() is True and k.installed() is True
assert k.install() is True, "install 必须幂等"
assert FakeLLM.chat_stream is not orig_chat, "chat_stream 未被包装"

client = FakeLLM()
seen = []
res = client.chat_stream([{"role": "user", "content": "x" * 500}],
                         on_delta=lambda s: seen.append(s))
assert res["text"] == "hello world" and seen == ["hello ", "world"], "delta 必须原样透传"
snap = k.snapshot()
assert snap["llm"]["count"] == 1, "请求未被记录"
assert snap["llm"]["out_avg"] == 11, "输出字数应累计为 11（6+5），实际 %%s" %% snap["llm"]["out_avg"]
assert snap["llm"]["in_avg"] == 500, "输入体积应记为 500，实际 %%s" %% snap["llm"]["in_avg"]

tools.execute_tool("read_file", {"path": "a"})
tools.execute_tool("read_file", {"path": "b"})
FakeEngine().run("hi")
snap = k.snapshot()
assert snap["tool_calls"] == 2, "工具调用未记录"
assert snap["tools"][0]["name"] == "read_file" and snap["tools"][0]["n"] == 2
assert snap["turns"] and snap["turns"][-1]["seconds"] >= 0, "回合未记录"

# 失败/中断也要留痕（ok=False），异常必须原样抛出
FakeLLM.fail = True
try:
    FakeLLM().chat_stream([{"role": "user", "content": "y"}])
    raise AssertionError("异常未抛出")
except RuntimeError:
    pass
FakeLLM.fail = False
assert k.snapshot()["llm"]["failed"] == 1, "失败请求应计入 failed"
assert k.snapshot()["llm"]["count"] == 2, "失败请求也应计入总数"

k.uninstall()
assert k.installed() is False
assert FakeLLM.chat_stream is orig_chat, "uninstall 未还原 chat_stream"
assert tools.execute_tool is orig_tool, "uninstall 未还原工具入口"
assert FakeEngine.run is orig_run, "uninstall 未还原引擎入口"
print("hooks OK")
'''


def run_hooks_test() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="perfhooks_"))
    pkg = tmp / "zhuzhu_Copilot" / "core"
    pkg.mkdir(parents=True)
    (tmp / "zhuzhu_Copilot" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "perf_turbo.py").write_text(KERNEL.read_text(encoding="utf-8"),
                                       encoding="utf-8")
    script = tmp / "t.py"
    script.write_text(HOOKS_TEST % {"kernel_dir": str(tmp)}, encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(tmp))
    r = subprocess.run([sys.executable, str(script)], capture_output=True,
                       text=True, env=env, cwd=str(tmp))
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    print("[6] 运行时挂钩测试：%s" % (out.splitlines()[-1] if out else "(无输出)"))
    if r.returncode != 0:
        print(out[-2500:])
        raise SystemExit("挂钩测试失败")


UI_TEST = r'''
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, r"%(src)s")
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])

import importlib.util
spec = importlib.util.spec_from_file_location("perf_page_probe", r"%(page)s")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

page = mod.PerfPage()
page.refresh()
page.show()
app.processEvents()
page.refresh()
page._pick_profile("conservative")
page._pick_profile("balanced")
page._reset()
page.hide()
print("ui OK:", mod._theme()["accent"])
'''


def run_ui_test() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="perfui_"))
    script = tmp / "u.py"
    script.write_text(UI_TEST % {"src": str(SRC),
                                 "page": str(PKG / "pages" / "perf_page.py")},
                      encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(SRC), QT_QPA_PLATFORM="offscreen")
    r = subprocess.run([sys.executable, str(script)], capture_output=True,
                       text=True, env=env, cwd=str(tmp), timeout=240)
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    print("[7] 性能页冒烟（offscreen）：%s"
          % (out.splitlines()[-1] if out else "(无输出)"))
    if r.returncode != 0:
        print(out[-3000:])
        raise SystemExit("性能页冒烟失败")


if __name__ == "__main__":
    run_hooks_test()
    run_ui_test()
    print("\n集成校验通过。")
