# -*- coding: utf-8 -*-
"""分组/触发规范探针：三形状（点击触发 / 与上一同 / 上一后），打印官方 timing。"""
import os, re, sys, zipfile
sys.coinit_flags = 0
import pythoncom
import win32com.client as wc
from win32com.client import gencache

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT = os.path.join(ROOT, "build", "ppt_group_canonical.pptx")


def main():
    pythoncom.CoInitialize()
    ppt = gencache.EnsureDispatch("PowerPoint.Application")
    ppt.Visible = 1
    pres = None
    try:
        pres = ppt.Presentations.Add(WithWindow=False)
        pres.PageSetup.SlideWidth = 960
        pres.PageSetup.SlideHeight = 540
        slide = pres.Slides.Add(1, 12)
        shapes = []
        for i, txt in enumerate(("OnClick", "WithPrev", "AfterPrev")):
            tb = slide.Shapes.AddTextbox(1, 40, 60 + i * 90, 320, 50)
            tb.TextFrame.TextRange.Text = txt
            shapes.append(tb)
        seq = slide.TimeLine.MainSequence
        seq.AddEffect(shapes[0], 10, 1)            # OnPageClick(1)
        seq.AddEffect(shapes[1], 10, 2)            # WithPrevious(2)
        seq.AddEffect(shapes[2], 10, 3)            # AfterPrevious(3)
        pres.SaveAs(OUT)
        print("saved")
    except Exception as e:
        import traceback
        traceback.print_exc()
        return 1
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
    z = zipfile.ZipFile(OUT)
    xml_name = [n for n in z.namelist() if n.startswith("ppt/slides/slide") and n.endswith(".xml")][0]
    xml = z.read(xml_name).decode("utf-8")
    m = re.search(r"<p:timing>.*?</p:timing>", xml, re.S)
    txt = m.group(0) if m else ""
    # 美化输出（简单缩进便于人读）
    indent = 0
    for tok in re.findall(r"<[^>]+>", txt):
        print("  " * max(0, indent) + tok)
        if tok.startswith("</"):
            indent = max(0, indent - 1)
        elif tok.startswith("<") and not tok.startswith("</") and not tok.endswith("/>"):
            indent += 1
    return 0

if __name__ == "__main__":
    sys.exit(main())
