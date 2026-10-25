# -*- coding: utf-8 -*-
"""定位 list_skills_for_workflow / load_skills / is_builtin_skill 的耗时。"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zhuzhu_Copilot.core import agent_skills as S   # noqa: E402

def timed(label, fn, *a, **kw):
    t0 = time.monotonic()
    r = fn(*a, **kw)
    print(f"{label}: {(time.monotonic()-t0)*1000:.0f}ms")
    return r

skills = timed("load_skills('')", S.load_skills, "")
print("  技能数:", len(skills))
for s in skills:
    t0 = time.monotonic()
    S.is_builtin_skill(s.get("name"))
    dt = (time.monotonic() - t0) * 1000
    if dt > 50:
        print(f"  is_builtin_skill({s.get('name')}): {dt:.0f}ms  <== 慢")

t0 = time.monotonic()
out = S.list_skills_for_workflow("")
print(f"list_skills_for_workflow(''): {(time.monotonic()-t0)*1000:.0f}ms, {len(out)} 项")

# 目录规模
try:
    d = S._skills_dir()
    print("skills 目录:", d, "存在:", d.is_dir())
    if d.is_dir():
        subs = [p for p in d.iterdir()]
        print("  子目录/文件数:", len(subs))
except Exception as e:
    print("skills dir err:", e)
try:
    sh = S._shipped_skills_dir()
    print("shipped 目录:", sh, "存在:", sh.is_dir())
    if sh.is_dir():
        subs = [p for p in sh.iterdir()]
        print("  子目录数:", len(subs))
except Exception as e:
    print("shipped dir err:", e)
