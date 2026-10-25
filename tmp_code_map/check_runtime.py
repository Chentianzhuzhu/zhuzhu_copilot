"""code-map 运行时校验：转译层生效后的内核 / 工具 / 页面全链路。

前置：插件已启用（overlay 里有 zhuzhu_Copilot/core/code_map.py）。
跑法：PYTHONPATH=src QT_QPA_PLATFORM=offscreen python tmp_code_map/check_runtime.py
"""

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
SRC = str(Path(__file__).resolve().parents[1] / "src")
PAGE = str(Path(__file__).resolve().parents[1] / "src" / "zhuzhu_Copilot" /
           "copilot_plugins_examples" / "code-map" / "pages" / "code_map_page.py")
sys.path.insert(0, SRC)

import _copilot_overlay_hook as hook          # noqa: E402
print("overlay 生效：", hook.install(), hook.overlay_modules())

from zhuzhu_Copilot.core import code_map as k     # noqa: E402

tree = Path(tempfile.mkdtemp(prefix="cm_rt_"))
(tree / "src").mkdir()
(tree / "src" / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
(tree / "src" / "b.ts").write_text("let a = 1;\n", encoding="utf-8")
(tree / "docs").mkdir()
(tree / "docs" / "readme.md").write_text("# t\n\nbody\n", encoding="utf-8")
(tree / "node_modules").mkdir()
(tree / "node_modules" / "junk.js").write_text("a\nb\nc\n", encoding="utf-8")
(tree / "bin.dat").write_bytes(b"\x00\x01\x02")
(tree / "empty.py").write_text("", encoding="utf-8")

d = k.scan(tree)
assert (d["files"], d["dirs"], d["code_files"], d["code_lines"]) == (5, 3, 4, 6), d
print("[1] 内核统计：文件 %d / 目录 %d / 代码 %d 行 · %.2fs"
      % (d["files"], d["dirs"], d["code_lines"], d["elapsed"]))

from zhuzhu_Copilot.core import agent_tools          # noqa: E402
names = [t.get("function", {}).get("name") for t in agent_tools.TOOLS]
assert "code_map" in names, "code_map 未登记"
res = agent_tools.execute_tool("code_map", {"path": str(tree), "top": 3})
assert "工作区代码地图" in res["text"], res["text"][:200]
print("[2] 工具 code_map 可用（TOOLS 共 %d 个）：%s"
      % (len(names), res["text"].splitlines()[1][:60]))

# 真实仓库（浅扫，验证大目录不卡死）
real = Path(SRC).parent
t0 = time.perf_counter()
d2 = k.scan(str(real), max_depth=2)
print("[3] 真实仓库浅扫：文件 %s / 代码 %s 行 / 截断 %s / 用时 %.2fs（含扫描 %.2fs）"
      % (format(d2["files"], ","), format(d2["code_lines"], ","),
         d2["truncated"], time.perf_counter() - t0, d2["elapsed"]))

# ---- 页面 ----
from PyQt6.QtWidgets import QApplication             # noqa: E402
app = QApplication.instance() or QApplication([])

import importlib.util                                 # noqa: E402
spec = importlib.util.spec_from_file_location("cm_page_probe", PAGE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

k.clear_cache()
page = mod.CodeMapPage()
page.show()
app.processEvents()
page._start_scan(str(tree))
deadline = time.time() + 20
while page._busy and time.time() < deadline:
    app.processEvents()
    time.sleep(0.02)
app.processEvents()
assert not page._busy, "扫描未结束"
assert page.tiles["files"].lbl_value.text() == "5", page.tiles["files"].lbl_value.text()
assert page.tiles["lines"].lbl_value.text() == "6", page.tiles["lines"].lbl_value.text()
assert ".py" in page.lbl_ext.text() and "readme.md" in page.lbl_files.text()
assert "src" in page.lbl_dirs.text()
print("[4] 页面扫描渲染：文件 %s / 行 %s / 类型表与目录条已填充"
      % (page.tiles["files"].lbl_value.text(), page.tiles["lines"].lbl_value.text()))

# 缓存共享：新页面应直接采纳缓存，不再扫
page2 = mod.CodeMapPage()
app.processEvents()
assert page2._data and page2._data["root"] == d["root"], "新页面未采纳缓存"
assert page2.tiles["files"].lbl_value.text() == "5"
print("[5] 缓存共享：新页面直接显示缓存结果，未重复扫描")

page._copy()
page.hide()
page2.hide()
print("\n全链路校验通过。")
