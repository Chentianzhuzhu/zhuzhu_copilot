import os, sys, types
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
SRC = r"C:/Users/zhuzhu/Desktop/zhuzhu Copilot/src"
PAGE = r"C:/Users/zhuzhu/Desktop/zhuzhu Copilot/src/zhuzhu_Copilot/copilot_plugins_examples/perf-turbo/pages/perf_page.py"
sys.path.insert(0, SRC)

# 注入带真实数据形状的内核替身：验证指标卡 / 慢工具表 / 建议 / 档位按钮全路径
stub = types.ModuleType("zhuzhu_Copilot.core.perf_turbo")
stub.PROFILES = {"aggressive": {"label": "激进", "desc": "低延迟优先"},
                 "balanced": {"label": "均衡", "desc": "跟随默认值"},
                 "conservative": {"label": "保守", "desc": "省主线程优先"}}
stub.snapshot = lambda: {
    "profile": "balanced", "profile_label": "均衡",
    "ui": {"samples": 120, "p50": 33.1, "p95": 78.4, "max": 210.0, "slow_ratio": 6.2},
    "llm": {"count": 4, "failed": 1, "ttft_p50": 900, "ttft_p95": 3600,
            "total_p50": 5200, "in_avg": 48000, "in_max": 96000, "out_avg": 1200,
            "cps": 12.5},
    "tools": [{"name": "run_command", "n": 3, "avg": 4200.0, "p95": 6100.0, "max": 7000.0},
              {"name": "read_file", "n": 9, "avg": 80.0, "p95": 150.0, "max": 300.0}],
    "tool_calls": 12,
    "md": {"hit": 40, "miss": 12, "hit_ratio": 76.9, "saved_chars": 128000,
           "entries": 30, "chars": 2097152},
    "turns": [{"seconds": 9.5, "tools": 3, "at": 0}],
    "installed": True,
}
stub.report_text = lambda: "【性能报告】档位：均衡（balanced）\n...\n五、建议\n  · x"
stub._suggestions = lambda s: ["主线程响应 P95 78ms，建议切保守档", "慢工具：run_command（P95 6.1s）"]
stub.set_profile = lambda k: k
stub.reset = lambda: None
sys.modules["zhuzhu_Copilot.core.perf_turbo"] = stub

from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])

import importlib.util
spec = importlib.util.spec_from_file_location("perf_page_probe2", PAGE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

page = mod.PerfPage()
page.show()
app.processEvents()
page.refresh()
assert page.tiles["ui"].lbl_value.text() == "78 ms", page.tiles["ui"].lbl_value.text()
assert page.tiles["ttft"].lbl_value.text() == "3.60s", page.tiles["ttft"].lbl_value.text()
assert "12 字/秒" in page.tiles["cps"].lbl_value.text(), page.tiles["cps"].lbl_value.text()
assert page.tiles["md"].lbl_value.text() == "77%", page.tiles["md"].lbl_value.text()
assert "run_command" in page.lbl_tools.text()
assert "建议切保守档" in page.lbl_advice.text()
assert "档位「均衡」" in page.lbl_hint.text(), page.lbl_hint.text()
page._pick_profile("conservative")
page._copy()
page.hide()
print("ui-data OK")
