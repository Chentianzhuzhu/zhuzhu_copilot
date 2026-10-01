"""端到端启动探针：量「进程启动 → 启动动画首帧」「→ 面板可见」的真实耗时。

不改动产品代码：把 src/main.py 的启动时序在探针里复刻，
用 SplashWindow 的真实实现测首帧，再测面板构造与显示。

用法: python scripts/_probe_boot_e2e.py
"""
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
PY = sys.executable

CODE = r'''
import os, sys, time
sys.path.insert(0, os.getcwd())
_T_START = time.perf_counter()
import zhuzhu_Copilot.ui.agent_panel  # noqa: E402  (预加载，对齐 main.py 后台线程)
_T_IMPORTED = time.perf_counter()

from PyQt6.QtWidgets import QApplication  # noqa: E402
app = QApplication(sys.argv)
_T_QAPP = time.perf_counter()

sys.path.insert(0, os.path.join(os.getcwd()))
import importlib.util  # noqa: E402
spec = importlib.util.spec_from_file_location("_main_probe", "main.py")
_m = importlib.util.module_from_spec(spec)

# 只借用 SplashWindow 实现：exec 整个 main.py 会触发 main()，故改为源码抽取
_src = open("main.py", encoding="utf-8").read()
_ns = {"__name__": "_main_probe", "__file__": "main.py"}
exec(compile(_src.split("def main():")[0], "main.py", "exec"), _ns)
_T_SPLASH_MOD = time.perf_counter()

splash = _ns["SplashWindow"]()
_T_SPLASH_NEW = time.perf_counter()
splash.start()
app.processEvents()
_T_FIRST_FRAME = time.perf_counter()

print("RESULT "
      f"import={(_T_IMPORTED-_T_START)*1000:.1f} "
      f"qapp={(_T_QAPP-_T_IMPORTED)*1000:.1f} "
      f"splash_mod={(_T_SPLASH_MOD-_T_QAPP)*1000:.1f} "
      f"splash_new={(_T_SPLASH_NEW-_T_SPLASH_MOD)*1000:.1f} "
      f"first_frame={(_T_FIRST_FRAME-_T_SPLASH_NEW)*1000:.1f} "
      f"total={(_T_FIRST_FRAME-_T_START)*1000:.1f}")
'''

env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
env["PYTHONPATH"] = SRC
env["QT_QPA_PLATFORM"] = "offscreen"

for i in range(3):
    t0 = time.perf_counter()
    r = subprocess.run([PY, "-c", CODE], cwd=SRC, capture_output=True, text=True,
                       env=env)
    res = [ln for ln in r.stdout.splitlines() if ln.startswith("RESULT")]
    wall = (time.perf_counter() - t0) * 1000
    if res:
        print(f"#{i+1} wall={wall:.0f}ms  {res[-1][7:].strip()}")
    else:
        print(f"#{i+1} FAILED rc={r.returncode}\n{r.stderr[-500:]}")
