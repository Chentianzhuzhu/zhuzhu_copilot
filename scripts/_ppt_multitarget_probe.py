# -*- coding: utf-8 -*-
"""判定：一个 clickEffect 容器内放多个 spTgt 的 set+animEffect，PowerPoint 视为几次点击。

生成双形状测试文件 -> 用 PowerPoint COM 打开并打印 MainSequence.Count。
若 Count==2 则每个形状必须独立 step（各自点击）；若 Count==1 则同一容器=同一点击。
"""
import os, sys, zipfile, shutil, re
sys.coinit_flags = 0
import pythoncom
import win32com.client as wc
from win32com.client import gencache
from pptx import Presentation
from pptx.util import Inches

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
P = os.path.join(ROOT, "build", "ppt_multitarget_variant.pptx")


def build():
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(10), Inches(5.625)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    a = slide.shapes.add_textbox(Inches(0.5), Inches(0.5), Inches(4), Inches(0.5))
    a.text_frame.text = "目标A"
    b = slide.shapes.add_textbox(Inches(0.5), Inches(2), Inches(4), Inches(0.5))
    b.text_frame.text = "目标B"
    spA, spB = a.shape_id, b.shape_id
    from lxml import etree
    from pptx.oxml.ns import qn

    def sub(parent, tag, **attrs):
        el = etree.SubElement(parent, qn(tag))
        for k, v in attrs.items():
            if v is not None:
                el.set(k, str(v))
        return el

    timing = sub(slide._element, "p:timing")
    tn = sub(timing, "p:tnLst"); par0 = sub(tn, "p:par")
    c0 = sub(par0, "p:cTn", id=1, dur="indefinite", restart="never", nodeType="tmRoot")
    ch0 = sub(c0, "p:childTnLst"); seq = sub(ch0, "p:seq", concurrent=1, nextAc="seek")
    sc = sub(seq, "p:cTn", id=2, dur="indefinite", nodeType="mainSeq")
    scc = sub(sc, "p:childTnLst")
    counter = [10]
    def nid():
        v = counter[0]; counter[0] += 1; return str(v)
    # 一个 step par，内含双 clickEffect（每个 spTgt 一个），都放同一 par childTnLst
    par = sub(scc, "p:par"); ct = sub(par, "p:cTn", id=nid(), fill="hold")
    sub(sub(ct, "p:stCondLst"), "p:cond", delay="indefinite")
    cch = sub(ct, "p:childTnLst")
    for spid in (spA, spB):
        p2 = sub(cch, "p:par"); c2 = sub(p2, "p:cTn", id=nid(), fill="hold")
        sub(sub(c2, "p:stCondLst"), "p:cond", delay=0)
        cc2 = sub(c2, "p:childTnLst")
        p3 = sub(cc2, "p:par")
        c3 = sub(p3, "p:cTn", id=nid(), presetID=10, presetClass="entr", presetSubtype=0,
                 fill="hold", grpId=0, nodeType="clickEffect")
        sub(sub(c3, "p:stCondLst"), "p:cond", delay=0)
        c3c = sub(c3, "p:childTnLst")
        st = sub(c3c, "p:set"); bh = sub(st, "p:cBhvr")
        bc = sub(bh, "p:cTn", id=nid(), dur=1, fill="hold")
        sub(sub(bc, "p:stCondLst"), "p:cond", delay=0)
        sub(sub(bh, "p:tgtEl"), "p:spTgt", spid=str(spid))
        anl = sub(bh, "p:attrNameLst"); sub(anl, "p:attrName").text = "style.visibility"
        sub(sub(st, "p:to"), "p:strVal", val="visible")
        ef = sub(c3c, "p:animEffect", transition="in", filter="fade")
        eb = sub(ef, "p:cBhvr"); sub(eb, "p:cTn", id=nid(), dur=500)
        sub(sub(eb, "p:tgtEl"), "p:spTgt", spid=str(spid))
    # prev/next 条件（照官方：仅 onPrev/onNext）
    pc = sub(seq, "p:prevCondLst")
    sub(sub(sub(pc, "p:cond", evt="onPrev", delay=0), "p:tgtEl"), "p:sldTgt")
    nc = sub(seq, "p:nextCondLst")
    sub(sub(sub(nc, "p:cond", evt="onNext", delay=0), "p:tgtEl"), "p:sldTgt")
    # build 清单（官方 entrance 必带）
    bld = sub(timing, "p:bldLst")
    for spid in (spA, spB):
        sub(bld, "p:bldP", spid=str(spid), grpId=0)
    prs.save(P)
    print("variant saved")


def read_count():
    import shutil
    pythoncom.CoInitialize()
    ppt = gencache.EnsureDispatch("PowerPoint.Application")
    ppt.Visible = 1
    pres = None
    repaired = os.path.join(ROOT, "build", "ppt_multitarget_repaired.pptx")
    try:
        pres = ppt.Presentations.Open(os.path.abspath(P), ReadOnly=0, Untitled=0, WithWindow=0)
        slide = pres.Slides(1)
        n = slide.TimeLine.MainSequence.Count
        print("MainSequence.Count =", n)
        pres.SaveAs(os.path.abspath(repaired))
        # 对比 timing 子树，看 PowerPoint 是否做了修复性改写
        def timing_of(path):
            z = zipfile.ZipFile(path)
            nm = [x for x in z.namelist() if x.startswith("ppt/slides/slide") and x.endswith(".xml")][0]
            xml = z.read(nm).decode("utf-8")
            m = re.search(r"<p:timing>.*?</p:timing>", xml, re.S)
            return m.group(0) if m else ""
        a, b = timing_of(P), timing_of(repaired)
        print("timing identical:", a == b, "| len:", len(a), len(b))
        if a != b:
            open(os.path.join(ROOT, "build", "timing_orig.xml"), "w", encoding="utf-8").write(a)
            open(os.path.join(ROOT, "build", "timing_repaired.xml"), "w", encoding="utf-8").write(b)
        return n
    finally:
        try:
            if pres is not None:
                pres.Close()
        except Exception:
            pass
        try:
            ppt.Quit()
        except Exception:
            pass
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    build()
    read_count()
