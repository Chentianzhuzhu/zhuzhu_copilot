"""临时探针：直接驱动 aura，检查四条边条是否真的画出了可见像素。"""
import importlib.util
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
p = ROOT / "src" / "zhuzhu_Copilot" / "plugins" / "computer-control" / "aura.py"
spec = importlib.util.spec_from_file_location("aura_probe_mod", p)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

a = m._aura
a.set_active(True)
time.sleep(1.2)
print("active:", a.active, "failed:", a._failed, a._error)
print("hint_error:", a._hint_error)
print("frame_cost:", round(a._frame_cost * 1000, 2), "ms")
print("bands:", [(b.name, b._w, b._h, b._x, b._y, b._visible) for b in a._bands])
for i, b in enumerate(a._bands):
    mv = b._mv
    core = a._band_core[i]
    nz = 0
    maxa = 0
    sample = []
    for off4, pi, di in core[:: max(1, len(core) // 8)]:
        val = (mv[off4], mv[off4 + 1], mv[off4 + 2], mv[off4 + 3])
        sample.append(val)
        if val[3] > 0:
            nz += 1
        maxa = max(maxa, val[3])
    print(f"  {b.name}: core_px={len(core)} 非零样本={nz}/{len(sample)} maxA={maxa}")
    print("     samples:", sample[:4])
print("glow px:", len(a._glow_px), "core px:", len(a._core_px))
a.set_active(False)
time.sleep(0.8)
a.stop()
print("after stop bands:", len(a._bands), "thread alive:", bool(a._thread and a._thread.is_alive()))
