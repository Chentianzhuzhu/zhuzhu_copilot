# -*- coding: utf-8 -*-
"""切换效果普查：逐值探测 EntryEffect 0..60，保存各 slide 的 p:transition 子元素。"""
import os, re, sys, zipfile, json
sys.coinit_flags = 0
import pythoncom
import win32com.client as wc
from win32com.client import gencache

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT = os.path.join(ROOT, "build", "ppt_trans_sweep.pptx")


def main():
    pythoncom.CoInitialize()
    ppt = gencache.EnsureDispatch("PowerPoint.Application")
    ppt.Visible = 1
    pres = None
    ok = []
    try:
        pres = ppt.Presentations.Add(WithWindow=False)
        for eid in range(0, 61):
            slide = pres.Slides.Add(eid + 1, 12)
            try:
                slide.SlideShowTransition.EntryEffect = eid
                ok.append(eid)
            except Exception:
                pass
        pres.SaveAs(OUT)
        print("valid transition ids:", ok)
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
    if not ok:
        return
    z = zipfile.ZipFile(OUT)
    mapping = {}
    for eid in ok:
        name = "ppt/slides/slide%d.xml" % (eid + 1)
        xml = z.read(name).decode("utf-8")
        m = re.search(r"<p:transition[^>]*>(.*?)</p:transition>", xml, re.S)
        if m:
            inner = m.group(1).strip()
            tg = re.match(r"<p:(\w+)([^>]*?)/?>", inner)
            if tg:
                tag = tg.group(1)
                attrs = dict(re.findall(r"([\w:]+)=\"([^\"]*)\"", tg.group(2)))
                mapping[eid] = {"tag": tag, "attrs": attrs}
    json.dump(mapping, open(os.path.join(ROOT, "build", "trans_entries.json"), "w"),
              ensure_ascii=False, indent=1)
    print("mapping entries:", len(mapping))
    for k, v in mapping.items():
        print(k, v)


if __name__ == "__main__":
    sys.exit(main())
