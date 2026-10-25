# -*- coding: utf-8 -*-
"""cProfile 定位 load_skills 冷启动热点。"""
import cProfile
import io
import os
import pstats
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zhuzhu_Copilot.core import agent_skills as S   # noqa: E402

pr = cProfile.Profile()
pr.enable()
skills = S.load_skills("")
pr.disable()
print("技能数:", len(skills))

s = io.StringIO()
ps = pstats.Stats(pr, stream=s).sort_stats("cumulative")
ps.print_stats(28)
print(s.getvalue())
