# -*- coding: utf-8 -*-
"""校验 feature-panel 技能的三面板模板：可编译、可在 offscreen Qt 下构建真实控件。"""
import compileall
import os
import re
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MD = os.path.join(ROOT, "src", "winapp_migrator", "skills", "feature-panel",
                  "references", "panel-templates.md")
md = open(MD, encoding="utf-8").read()

fences = re.findall(r"```python\n(.*?)```", md, re.S)
print("code fences:", len(fences))
mods = []
for i, code in enumerate(fences):
    fn = f"_tmpl_{i}.py"
    fn = os.path.join(tempfile.gettempdir(), fn)
    with open(fn, "w", encoding="utf-8") as f:
        f.write(code)
    compileall.compile_file(fn, quiet=2)
    print("compile OK:", fn, code.count("\n"), "lines")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication  # noqa
app = QApplication.instance() or QApplication([])

# 逐模板 exec 并构建（owner=None 仅用于构建/销毁冒烟）
for i, code in enumerate(fences):
    mod = types.ModuleType(f"tmpl_{i}")
    try:
        exec(compile(code, f"<tmpl_{i}>", "exec"), mod.__dict__)
    except Exception as e:
        print("EXEC FAIL", i, repr(e))
        continue
    factory = getattr(mod, "build_panel", None)
    if not callable(factory):
        print("NO build_panel", i)
        continue
    try:
        w = factory(None)
        print("build OK", getattr(mod, "TITLE", "?"), "->", type(w).__name__)
        w.deleteLater()
    except Exception as e:
        print("BUILD FAIL", i, repr(e))
app.processEvents()
print("DONE")