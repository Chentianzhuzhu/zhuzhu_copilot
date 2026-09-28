# -*- coding: utf-8 -*-
"""汇总验证：PPT 新能力回归 + Word 水印/封面 + Excel 条件格式 + skill 结构校验 + 自定义工具保活 + 自动复核。"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append((name, ok, detail))
    print(("PASS  " if ok else "FAIL  ") + name + ("  -> " + detail if detail else ""))


# 1. PPT 回归（生成+内容+XML+真机打开）
r = subprocess.run([sys.executable, "-u", os.path.join(ROOT, "scripts", "_ppt_regress_test.py")],
                   capture_output=True, text=True, timeout=180)
check("PPT 全布局回归(含 hero_stats/radar/timeline/combo 新能力)", r.returncode == 0,
      (r.stdout + r.stderr).strip().splitlines()[-1] if r.returncode else "PowerPoint 打开成功")

# 2. Word 水印 + band 封面
from zhuzhu_Copilot.office.docx_builder import build_docx  # noqa
wpath = os.path.join(ROOT, "build", "vfy.docx")
try:
    build_docx(wpath, title="验证文档", style={"theme": "business",
                                             "watermark": {"text": "机密", "color": "C9CFD8"},
                                             "cover": {"style": "band", "subtitle": "副题", "author": "A", "date": "2026"}},
               paragraphs=["# 标题", "正文测试", "| a | b |", "| 1 | 2 |"])
    import zipfile
    z = zipfile.ZipFile(wpath)
    hd = [n for n in z.namelist() if "header" in n]
    has_wm = any("PowerPlusWaterMarkObject" in z.read(n).decode("utf-8", "ignore") for n in hd)
    check("Word 文字水印生成", has_wm, wpath)
except Exception as e:
    check("Word 文字水印生成", False, repr(e))

# 3. Excel 条件格式
from zhuzhu_Copilot.office.xlsx_builder import build_xlsx  # noqa
xpath = os.path.join(ROOT, "build", "vfy.xlsx")
try:
    build_xlsx(xpath, [{"name": "数据", "rows": [["店", "v1", "v2"],
                                                 ["A", 10, 20], ["B", 30, 5]],
                        "format": {"data_bars": [2], "highlight": {"max": [2]}, "color_scale": [3]}}],
               {"theme": "business"})
    from openpyxl import load_workbook
    wb = load_workbook(xpath)
    n_rules = sum(len(rf.rules) for rf in wb["数据"].conditional_formatting)
    check("Excel 条件格式(数据条/高亮/色阶)", n_rules >= 3, f"{n_rules} 条规则")
except Exception as e:
    check("Excel 条件格式(数据条/高亮/色阶)", False, repr(e))

# 4. skill 结构校验
from zhuzhu_Copilot.core import agent_skills as AS  # noqa
w = AS._skill_structure_warning("当用户要求做文档时使用本技能。流程：1、规划 2、生成。")
check("SKILL 结构校验(缺参数契约/缺禁用 → 告警)", w == ["参数契约", "禁用约束"], str(w))
w2 = AS._skill_structure_warning("触发：做PPT。步骤：规划→生成。参数：path必填。禁止编造数据。")
check("SKILL 结构校验(齐全 → 无告警)", w2 == [], str(w2))

# 5. 自定义工具保活：注册后 custom_tool_names 可见，且模拟 engine 裁剪保留
from zhuzhu_Copilot.core import agent_tools as AT  # noqa
try:
    AT.register_custom_tools([{"function": {"name": "my_custom_x", "parameters": {"type": "object", "properties": {}}}}],
                             handler=lambda *a, **k: {"text": "ok"}, source="_default")
    names = AT.custom_tool_names("_default")
    check("自定义工具注册+查询", "my_custom_x" in names, str(names))
except Exception as e:
    check("自定义工具注册+查询", False, repr(e))

# 6. 生成后自动复核
try:
    r6 = AT._office_verify(os.path.join(ROOT, "build", "vfy.docx"), ROOT)
    check("create_* 生成后自动复核", "自动复核" in r6, r6.strip())
except Exception as e:
    check("create_* 生成后自动复核", False, repr(e))

# 7. 工具调用硬约束注入（system prompt 片段）
try:
    hard = AS._tool_call_hard_rules()
    check("工具调用硬约束注入(mock/真实API/参数/复核)", all(k in hard for k in ("mock", "真实", "参数", "复核")))
except Exception as e:
    check("工具调用硬约束注入(mock/真实API/参数/复核)", False, repr(e))

failed = [n for n, ok, _ in CHECKS if not ok]
print("\n==== 汇总:", "ALL PASS" if not failed else "FAILED: %s" % failed)
sys.exit(1 if failed else 0)