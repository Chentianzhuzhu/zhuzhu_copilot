# -*- coding: utf-8 -*-
"""PPT 生成回归测试：全布局/全动画覆盖生成 → 内容(无未解析字面量) → XML 结构 → 真机打开。

用法: python scripts/_ppt_regress_test.py [输出目录]
"""
import os
import re
import shutil
import sys
import zipfile
from lxml import etree

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

OUT_DIR = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "build", "ppt_regress")
os.makedirs(OUT_DIR, exist_ok=True)
OUT = os.path.join(OUT_DIR, "regress.pptx")
PML = "http://schemas.openxmlformats.org/presentationml/2006/main"

SLIDES = [
    {"title": "目录", "bullets": ["一、市场综述", "二、竞争格局", "三、增长策略"], "anim": False},
    {"title": "核心结论", "cards": [
        {"title": "增长 42%", "desc": "AI 产品线营收同比"},
        {"title": "份额第一", "desc": "国内市场份额 27%"}],
     "anim": {"graphics": "circle"}},
    {"title": "营收趋势", "chart": {"type": "column", "labels": ["Q1", "Q2", "Q3", "Q4"],
                                    "values": [120, 180, 240, 320]},
     "anim": {"graphics": "dissolve"}},
    {"title": "产品演进", "diagram": {"type": "flow", "items": [
        {"title": "2023", "left": "公司成立", "right": "研发团队组建"},
        {"title": "2024 Q1", "left": "V1 发布", "right": "技术验证"},
        {"title": "2025", "left": "V3 发布", "right": "性能突破"}]}},
    {"title": "方案对比", "diagram": {"type": "compare", "items": [
        {"title": "自研方案", "left": "成本可控", "right": "定制灵活"},
        {"title": "采购方案", "left": "上线快", "right": "依赖厂商"}]},
     "anim": {"text": "wipe", "exit": "fade"}},
    {"title": "增长循环", "diagram": {"type": "cycle", "center": "增长飞轮",
                                      "items": ["获客", "激活", "留存", "变现"]}},
    {"title": "市场心智", "diagram": {"type": "mindmap", "center": "AI 市场",
                                      "items": ["云服务", "大模型", "智能体", "数据", "算力", "安全"]}},
    {"title": "份额数据", "table": {"title": "2025 全年份额", "header": ["厂商", "份额", "增速"],
                                    "rows": [["A", "27%", "+42%"], ["B", "18%", "+12%"], ["C", "12%", "+8%"]]}},
    {"title": "两大主线", "layout": "two_col", "columns": [
        {"title": "产品线", "bullets": ["AI 平台持续迭代", "智能体生态扩张"]},
        {"title": "商业化", "bullets": ["订阅制渗透率翻倍", "政企大单落地"]}]},
    {"title": "行动号召", "layout": "center_highlight", "highlight": "以 AI 重塑增长曲线",
     "bullets": ["一季度验证、二季度放量", "生态伙伴计划同步启动"]},
    {"title": "深色提醒", "bg_color": "16233E", "bullets": ["深色页正文自动切浅色", "保持可读性优先"]},
    {"title": "核心指标", "layout": "hero_stats", "stats": [
        {"value": "42%", "label": "营收增长"},
        {"value": "327万", "label": "活跃用户"},
        {"value": "99.99%", "label": "可用性"}]},
    {"title": "能力雷达", "chart": {"type": "radar",
                                    "labels": ["性能", "成本", "体验", "安全"],
                                    "values": [92, 78, 88, 95]}},
    {"title": "发展历程", "diagram": {"type": "timeline", "items": [
        {"date": "2023", "text": "公司成立，组建核心研发团队"},
        {"date": "2024", "text": "V1 发布，完成技术验证"}]}},
    {"title": "营收与增速", "chart": {"type": "combo", "labels": ["Q1", "Q2", "Q3", "Q4"],
                                     "values": [120, 180, 240, 320],
                                     "values2": [10, 22, 18, 30]}},
    {"title": "本章小结", "bullets": ["统一平台，一套数据", "AI 贯穿高频事务", "三期交付，风险可控"],
     "anim": {"text": "wipe", "exit": "fade"}},
]
STYLE = {"theme": "business", "transition": "push", "animation": True,
         "cover": {"subtitle": "AI 驱动增长新引擎", "author": "战略研究部", "date": "2026-03-31"}}


def main():
    from winapp_migrator.office.pptx_builder import build_pptx
    build_pptx(OUT, "2026 Q1 市场分析报告", SLIDES, STYLE)
    print("[ok] 生成:", OUT)
    failures = check_content(OUT)
    failures += check_timing(OUT)
    if failures:
        print("FAILED:", failures)
        sys.exit(1)
    print("[ok] 内容与 XML 结构校验通过")
    rc = check_powerpoint_open(OUT)
    sys.exit(rc)


def check_content(path):
    bad = []
    z = zipfile.ZipFile(path)
    pat = re.compile(r"\{'[^{}]*'|\{\{|\}\}|\bt\[\"[a-z_]+\"\]")
    for n in sorted(x for x in z.namelist() if x.startswith("ppt/slides/slide") and x.endswith(".xml")):
        data = z.read(n).decode("utf-8", "ignore")
        for m in re.findall(r"<a:t>([^<]+)</a:t>", data):
            if pat.search(m) and len(m) < 120:
                bad.append((n, m))
    if bad:
        print("[FAIL] 未解析字面量:", bad[:5])
    return bad


def check_timing(path):
    """timing 结构 = PowerPoint 修复模板：3 层 par + set(attrNameLst 在 cBhvr, to>strVal) + bldLst"""
    fails = []
    z = zipfile.ZipFile(path)
    for n in sorted(x for x in z.namelist() if x.startswith("ppt/slides/slide") and x.endswith(".xml")):
        root = etree.fromstring(z.read(n))
        for t in root.findall(".//{%s}timing" % PML):
            ids = [int(c.get("id")) for c in t.iter("{%s}cTn" % PML) if c.get("id")]
            if ids != list(range(1, len(ids) + 1)):
                fails.append((n, "cTn id 不连续: %s" % ids[:8]))
            for st in t.iter("{%s}set" % PML):
                if st.find(".//{%s}attrNameLst" % PML) is None:
                    fails.append((n, "set 缺 attrNameLst"))
                to = st.find("./{p}to".format(p="{%s}" % PML))
                if (to is None or to.find("./{p}strVal".format(p="{%s}" % PML)) is None
                        or to.find("./{p}attrNameLst".format(p="{%s}" % PML)) is not None):
                    fails.append((n, "p:to 结构异常"))
            bld = t.find("./{%s}bldLst" % PML)
            if bld is None:
                fails.append((n, "缺 p:bldLst"))
    if fails:
        print("[FAIL] timing XML:", fails[:6])
    return fails


def check_powerpoint_open(path):
    """真机打开：PowerPoint COM 无异常即视为不触发修复。无 Office 时跳过。"""
    try:
        import win32com.client  # noqa
    except Exception:
        print("[skip] 无 pywin32，跳过 PowerPoint 打开校验")
        return 0
    app = None
    try:
        app = win32com.client.DispatchEx("PowerPoint.Application")
        pres = app.Presentations.Open(path, ReadOnly=True, Untitled=True, WithWindow=False)
        n = pres.Slides.Count
        pres.Close()
        print("[ok] PowerPoint 打开成功，页数:", n)
        return 0
    except Exception as e:
        print("[FAIL] PowerPoint 打开报错(可能触发修复):", repr(e))
        return 1
    finally:
        if app is not None:
            try:
                app.Quit()
            except Exception:
                pass


if __name__ == "__main__":
    main()