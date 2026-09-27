# -*- coding: utf-8 -*-
"""用本机 PowerPoint 生成"官方标准"动画/切换样本（枚举值动态探测）。"""
import os, sys
sys.coinit_flags = 0
import pythoncom
import win32com.client as wc
from win32com.client import gencache

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT = os.path.join(ROOT, "build", "ppt_anim_canonical.pptx")

def _pick_consts(mod, needles):
    """在 gencache 生成的 PowerPoint 模块上找枚举常量。"""
    found = {}
    try:
        names = dir(mod)
    except Exception:
        return found
    for n in names:
        if not n.startswith(("pp", "mso")):
            continue
        for key, pats in needles.items():
            if key in found:
                continue
            if any(p in n for p in pats):
                try:
                    found[key] = getattr(mod, n)
                except Exception:
                    pass
    return found


def main():
    pythoncom.CoInitialize()
    ppt = gencache.EnsureDispatch("PowerPoint.Application")
    mod = sys.modules[ppt.__class__.__module__]
    ppt.Visible = 1
    # 探测常量
    consts = _pick_consts(mod, {
        "fade": ["msoAnimEffectFade", "msoAnimEffectFade "],
        "wipe": ["msoAnimEffectWipe"],
        "appear": ["msoAnimEffectAppear"],
        "zoom": ["msoAnimEffectZoom", "msoAnimEffectGrow"],
        "trans_fade": ["ppEffectFade", "ppEffectFadeThroughBlack"],
        "trans_wipe": ["ppEffectWipeLeft", "ppEffectWipe"],
        "trans_random": ["ppEffectRandom"],
    })
    print("CONSTS", {k: v for k, v in consts.items()})
    pres = None
    try:
        pres = ppt.Presentations.Add(WithWindow=False)
        pres.PageSetup.SlideWidth = 960
        pres.PageSetup.SlideHeight = 540
        slide = pres.Slides.Add(1, 12)
        tb1 = slide.Shapes.AddTextbox(1, 40, 40, 400, 60)
        tb1.TextFrame.TextRange.Text = "Fade 目标"
        seq = slide.TimeLine.MainSequence
        fade_id = consts.get("fade") or 10
        seq.AddEffect(tb1, fade_id)
        if consts.get("trans_fade") is not None:
            slide.SlideShowTransition.EntryEffect = consts["trans_fade"]
        pres.SaveAs(OUT)
        print("SAVED", OUT)
        return 0
    except Exception as e:
        print("ERR", repr(e))
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

if __name__ == "__main__":
    sys.exit(main())
