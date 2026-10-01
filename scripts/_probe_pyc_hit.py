"""判定 .pyc 是否真的被命中：对比「有 pyc」「无 pyc」「pyc 被破坏」三种情况的导入耗时。

若 pyc 命中，导入应≈模块体执行时间（~0.5s）；
若 pyc 未命中，导入≈源码编译 + 执行（~4.3s），且与「无 pyc」几乎无差别。
"""
import os
import shutil
import struct
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
PYC = os.path.join(SRC, "zhuzhu_Copilot", "ui", "__pycache__",
                   "agent_panel.cpython-313.pyc")
BAK = PYC + ".bak"
PY = sys.executable

CODE = ("import time;t=time.perf_counter();"
        "import zhuzhu_Copilot.ui.agent_panel;"
        "print('%.1f'%((time.perf_counter()-t)*1000))")


def run():
    r = subprocess.run([PY, "-c", CODE], cwd=SRC, capture_output=True, text=True,
                       env={k: v for k, v in os.environ.items()
                            if k != "PYTHONPATH"})
    return r.stdout.strip() or f"ERR {r.stderr.strip()[-200:]}"


def measure(label, n=2):
    vals = []
    for _ in range(n):
        vals.append(run())
    print(f"  {label:<28} {', '.join(vals)} ms")


print("1) pyc 存在（原始）")
measure("import")

shutil.copy2(PYC, BAK)
try:
    print("2) pyc 删除（强制从源码编译）")
    os.remove(PYC)
    measure("import")
finally:
    shutil.copy2(BAK, PYC)

    print("3) pyc 存在但 magic 破坏（回退到源码编译）")
    with open(PYC, "r+b") as f:
        f.seek(0)
        f.write(b"\x00\x00\x00\x00")
    measure("import")
    shutil.copy2(BAK, PYC)

print("4) 恢复后复测")
measure("import")

# 顺带确认 pyc 头部信息
with open(PYC, "rb") as f:
    head = f.read(16)
magic, flags, mtime, size = head[:4], struct.unpack("<I", head[4:8])[0], \
    struct.unpack("<I", head[8:12])[0], struct.unpack("<I", head[12:16])[0]
import importlib.util
st = os.stat(os.path.join(SRC, "zhuzhu_Copilot", "ui", "agent_panel.py"))
print(f"\npyc magic ok={magic == importlib.util.MAGIC_NUMBER} flags={flags} "
      f"mtime_match={mtime == int(st.st_mtime)} size_match={size == st.st_size}")
os.remove(BAK)
