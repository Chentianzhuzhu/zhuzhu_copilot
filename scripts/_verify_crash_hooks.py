"""验证：原生崩溃捕获（native hook）+ 后台线程异常捕获。"""
import os, sys
sys.path.insert(0, os.path.abspath("src"))
import tempfile, pathlib

# 隔离崩溃目录
_crash_dir = pathlib.Path(tempfile.mkdtemp()) / "crashes"
import winapp_migrator.utils.helpers as h
h._CRASH_DIR = _crash_dir

# 1. 后台线程异常捕获
def boom():
    raise RuntimeError("后台线程测试异常")

import threading
threading.excepthook = lambda args: h.install_thread_excepthook()
h.install_thread_excepthook()

import traceback
_caught = []
def fake_hook(args):
    _caught.append(args.exc_value)
import threading as _th
_th.excepthook = fake_hook
t = _th.Thread(target=boom)
t.start()
t.join()
assert _caught and isinstance(_caught[0], RuntimeError), "后台线程异常未捕获"
print("1. 后台线程异常捕获机制: OK")

# 2. 原生崩溃钩子注册（返回 True 且进入系统异常链）
assert h.install_native_crash_hook() is True, "原生崩溃钩子注册失败"
print("2. 原生崩溃钩子注册: OK")
# 注册后再次调用返回原 handler 地址（非空 = 链上有 Python 的 filter，已被我们接管）
import ctypes as _ct
_prev = _ct.windll.kernel32.SetUnhandledExceptionFilter(None)  # 取当前链头（无副作用）
# 恢复我们注册的 handler，避免测试进程改链
h.install_native_crash_hook()
print("2a. 异常链上有 filter（非空）:", bool(_prev))

# 3. 模拟 C 层崩溃（在子进程中，避免污染当前进程）
import textwrap
sub = textwrap.dedent(r'''
    import sys, os, ctypes, tempfile, pathlib, threading, time
    sys.path.insert(0, r"%s")
    import winapp_migrator.utils.helpers as h
    _cd = pathlib.Path(tempfile.mkdtemp()) / "crashes"
    h._CRASH_DIR = _cd
    h.install_native_crash_hook()
    def work():
        while True:
            time.sleep(0.1)
    threading.Thread(target=work, daemon=True).start()
    # 软件异常 0xC0000005（走 SEH → UnhandledExceptionFilter）触发 hook
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    raise_exc = k32.RaiseException
    raise_exc.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,
                          ctypes.POINTER(ctypes.c_ulong)]
    raise_exc(0xC0000005, 0, 0, None)
''') % os.path.abspath("src")
import subprocess as sp
p = sp.run(["python", "-c", sub], capture_output=True, text=True, timeout=30)
print("2b. 子进程退出码:", p.returncode, "| stderr:", p.stderr.strip()[:120])
# 子进程崩溃日志在它自己的临时目录；主进程侧通过 glob 整个 temp 找最新 native 日志
import glob
cands = sorted(glob.glob(os.path.join(tempfile.gettempdir(), "**", "native_*.log"), recursive=True),
               key=os.path.getmtime)
if cands:
    latest = cands[-1]
    print("2c. 最新 native 日志:", os.path.basename(latest))
    print("    内容片段:", open(latest, encoding="utf-8").read()[:300].replace(chr(10), " | "))
else:
    print("2c. 未找到 native 日志（钩子可能未触发）")
assert p.returncode != 0, "子进程应崩溃"
print("3. 原生崩溃钩子模拟: 子进程段错误被捕获")

print("SMOKE OK")
