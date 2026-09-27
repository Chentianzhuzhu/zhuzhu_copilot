# -*- coding: utf-8 -*-
"""效果普查：逐个探测 PowerPoint 入场效果编号(1..70)，保存合法者对应 shape，
保存文件后回读 slide XML，抽出每个效果的「官方 clickEffect 规范块」存 JSON。

输出 build/anim_entries.json = { "fade": {presetID, presetSubtype, presetClass,
                                          effects: [ {tag, attrs, ...} 简化] } }
用于让我们手写 timing 完全对齐 PowerPoint 官方编码。
"""
import json, os, re, sys, zipfile
sys.coinit_flags = 0
import pythoncom
import win32com.client as wc
from win32com.client import gencache

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT_DECK = os.path.join(ROOT, "build", "ppt_entries_sweep.pptx")
OUT_JSON = os.path.join(ROOT, "build", "anim_entries.json")

# 已知常见名字兜底（id 与 msoAnimEffect 数值基本一致）
NAME_HINTS = {1: "appear", 10: "fade", 16: "wipe"}


def main():
    pythoncom.CoInitialize()
    ppt = gencache.EnsureDispatch("PowerPoint.Application")
    ppt.Visible = 1
    pres = None
    used = {}
    try:
        pres = ppt.Presentations.Add(WithWindow=False)
        pres.PageSetup.SlideWidth = 1920
        pres.PageSetup.SlideHeight = 1080
        slide = pres.Slides.Add(1, 12)
        seq = slide.TimeLine.MainSequence
        x = y = 40
        for eid in range(1, 71):
            tb = slide.Shapes.AddTextbox(1, x, y, 120, 40)
            tb.TextFrame.TextRange.Text = f"EID{eid}"
            x += 140
            if x > 1800:
                x = 40
                y += 60
            try:
                seq.AddEffect(tb, eid)
                used[eid] = tb.Id
            except Exception:
                pass
        pres.SaveAs(OUT_DECK)
        print("deck saved; valid ids:", sorted(used))
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
    if not used:
        return 1
    # 回读 XML，抽每形状 clickEffect 块
    z = zipfile.ZipFile(OUT_DECK)
    xml_name = [n for n in z.namelist() if n.startswith("ppt/slides/slide") and n.endswith(".xml")][0]
    xml = z.read(xml_name).decode("utf-8")
    xml = xml.replace("<p:timing>", "<p:timing><MARK/>")  # no-op safety
    entries = {}
    # 按文本 EID<n> 找其所属形状，再找引用该 spid 的 p:cTn preset* 块（nodeType=clickEffect）
    for eid, spid in used.items():
        name = NAME_HINTS.get(eid, f"eid{eid}")
        pat = re.compile(
            r'<p:cTn[^>]*presetID="(\d+)"[^>]*presetClass="(\w+)"[^>]*presetSubtype="(\d+)"[^>]*'
            r'fill="hold"[^>]*grpId="(\d+)"[^>]*nodeType="clickEffect">'
            r'<p:stCondLst><p:cond delay="0"/></p:stCondLst>'
            r'<p:childTnLst>(.*?)</p:childTnLst></p:cTn>', re.S)
        m = re.search(r'<p:spTgt spid="%d"' % spid, xml)
        if not m:
            continue
        # 找到该 spTgt 所在 clickEffect 块的起点：从该 spTgt 位置向前找最近的 preset cTn
        head = xml[:m.start()]
        hit = None
        for mm in pat.finditer(xml):
            if mm.start() < m.start() and (hit is None or mm.start() > hit.start()):
                hit = mm
        if not hit:
            continue
        preset_id, cls, subtype, grp, inner = hit.groups()
        # 从 inner 抽出顶层子元素标签与 attrs
        effs = []
        for sm in re.finditer(r"<p:(\w+)([^>]*?)(?:/>|>)", inner):
            tag, attrs_raw = sm.group(1), sm.group(2)
            attrs = dict(re.findall(r'(\S+?)="([^"]*)"', attrs_raw))
            effs.append({"tag": tag, "attrs": attrs})
        entries[name] = {"eid": eid, "presetID": int(preset_id),
                         "presetClass": cls, "presetSubtype": int(subtype),
                         "grpId": int(grp), "effects": effs}
    json.dump(entries, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("entries:", len(entries))
    for k, v in list(entries.items())[:40]:
        print(" ", k, v["presetID"], v["presetClass"], v["effects"][:2])
    return 0

if __name__ == "__main__":
    sys.exit(main())
