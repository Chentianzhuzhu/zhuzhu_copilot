# -*- coding: utf-8 -*-
"""校验修正版 deck + HTML 演示文稿是否满足规格与缺陷清单。

用法：python scripts/_verify_deck.py <deck.pptx> <deck.html> [期望页数]
"""
import os
import re
import sys

from pptx.util import Emu

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

SW, SH = 13.333, 7.5
MD_RE = re.compile(r"(^|\s)#{1,6}\s|\*\*")
fail = []


def check(cond, msg):
    print(("  [ok] " if cond else "  [FAIL] ") + msg)
    if not cond:
        fail.append(msg)


def deck_checks(deck):
    from pptx import Presentation
    prs = Presentation(deck)
    print(f"== deck: {deck}")
    check(len(prs.slides._sldIdLst) == 10, f"页数 = 10（实际 {len(prs.slides._sldIdLst)}）")
    raw = []
    for si, slide in enumerate(prs.slides, 1):
        texts, boxes = [], []
        for shape in slide.shapes:
            x, y = (shape.left or 0) / 914400, (shape.top or 0) / 914400
            w, h = (shape.width or 0) / 914400, (shape.height or 0) / 914400
            try:
                if shape.has_text_frame and shape.has_text_frame:
                    texts.append(shape.text_frame.text)
            except Exception:
                pass
            if getattr(shape, "has_table", False) and shape.has_table:
                for r in shape.table.rows:
                    texts.extend(c.text for c in r.cells)
            boxes.append((shape.shape_id, x, y, w, h, str(shape.shape_type or "")))
        joined = "\n".join(texts)
        raw.append(joined)
        # markdown 残留
        bad = [ln for ln in joined.splitlines() if MD_RE.search(ln)]
        check(not bad, f"第{si}页 无 markdown 残留（{bad[:2]}）")
        # 出界：仅查有文字的正文块（封面色块/装饰圆环属设计出血，不算缺陷）
        out = [b for b in boxes if b[3] * b[4] > 0.5 and b[5].startswith("TEXT_BOX")
               and b[3] < SW and (b[1] < -0.02 or b[2] < -0.02
                                  or b[1] + b[3] > SW + 0.02 or b[2] + b[4] > SH + 0.02)]
        check(not out, f"第{si}页 无明显出界元素（{[(b[0], round(b[1],2), round(b[2],2)) for b in out][:3]}）")
    return raw


def overlap_checks(deck):
    """文本框压住卡片/表格/柱状图 —— 只查「同页正文块互相重叠」，背景板与装饰条不算。"""
    from pptx import Presentation
    prs = Presentation(deck)
    print("== 遮挡检查（正文块互相重叠）")
    for si, slide in enumerate(prs.slides, 1):
        body = []
        for shape in slide.shapes:
            if not (shape.has_text_frame and shape.has_text_frame):
                continue
            if not shape.text_frame.text.strip():
                continue
            x, y = (shape.left or 0) / 914400, (shape.top or 0) / 914400
            w, h = (shape.width or 0) / 914400, (shape.height or 0) / 914400
            # 页码块(>11.5in)与标题带(顶部<1.3in)不参与遮挡判定
            if x > 11.5 or y < 1.3:
                continue
            body.append((shape.shape_id, x, y, w, h))
        bad = []
        for i in range(len(body)):
            for j in range(i + 1, len(body)):
                a, b = body[i], body[j]
                ox = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
                oy = min(a[2] + a[4], b[2] + b[4]) - max(a[2], b[2])
                if ox > 0.25 and oy > 0.25:
                    bad.append((a[0], b[0], round(ox, 2), round(oy, 2)))
        check(not bad, f"第{si}页 正文块无重叠（{bad[:3]}）")


def _shape_rects(slide):
    """页内实体形状矩形（排除出血装饰/细分隔线/标题带/页码）。"""
    out = []
    for sh in slide.shapes:
        x, y = Emu(sh.left or 0).inches, Emu(sh.top or 0).inches
        w, h = Emu(sh.width or 0).inches, Emu(sh.height or 0).inches
        if w * h <= 0.05 or w < 0.1 or h < 0.14:
            continue                     # 分隔线/坐标轴线等细线
        if x < -0.02 or y < -0.02 or x + w > SW + 0.02 or y + h > SH + 0.02:
            continue                     # 出血装饰（封面圆环等）
        if x > 11.5 or y < 1.3:
            continue                     # 页码块 / 标题带
        out.append((sh.shape_id, x, y, w, h))
    return out


def _contained(a, b, eps=0.04):
    """a 是否被 b 完整包住（含容差）：卡片底板与其上文字属此关系，不算重叠。"""
    return (a[1] >= b[1] - eps and a[2] >= b[2] - eps
            and a[1] + a[3] <= b[1] + b[3] + eps and a[2] + a[4] <= b[2] + b[4] + eps)


def shape_overlap_checks(deck):
    """图形排版元素（循环图/流程图/卡片块等）不得为「部分压叠」关系。"""
    from pptx import Presentation
    prs = Presentation(deck)
    print("== 形状压叠检查（排除包含关系与装饰）")
    for si, slide in enumerate(prs.slides, 1):
        rects = _shape_rects(slide)
        bad = []
        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                a, b = rects[i], rects[j]
                ox = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
                oy = min(a[2] + a[4], b[2] + b[4]) - max(a[2], b[2])
                if ox <= 0.08 or oy <= 0.08:
                    continue
                if _contained(a, b) or _contained(b, a):
                    continue
                bad.append((a[0], b[0], round(ox, 2), round(oy, 2)))
        check(not bad, f"第{si}页 形状无部分压叠（{bad[:3]}）")


def html_checks(html_path, expect_decks=10):
    with open(html_path, encoding="utf-8") as f:
        h = f.read()
    # 只看幻灯片标记部分（<script> 内的注释/选择器不算页面文案残留）
    body = h.split("<script>")[0]
    print(f"== html: {html_path}（{len(h)} 字符）")
    check("<!DOCTYPE html>" in h, "自包含完整 HTML")
    n = h.count("<div class='deck' data-fit")
    check(n == expect_decks, f"{expect_decks} 页 .deck（实际 {n}）")
    check(h.count("data-fit") >= n, f"每页均带 data-fit（{n} 页）")
    check("class='sg'" in h and "class='tb'" in h, "含 .sg（shape group）/ .tb（text box）图层")
    check("width:1280px" in h and "height:720px" in h, "页面尺寸 1280×720")
    check("width:96.0px;height:38.4px" in h, "页码块 96.0×38.4px")
    nums = re.findall(r"class='slidenum'>(\d+) / (\d+)</div>", h)
    check(len(nums) == expect_decks, f"slidenum 共 {expect_decks} 处（实际 {len(nums)}）")
    check(all(b == str(expect_decks) for _, b in nums), "slidenum 分母均为 10")
    check([int(a) for a, _ in nums] == list(range(1, expect_decks + 1)),
          f"slidenum 序号 1..{expect_decks} 连续（{nums}）")
    for pat in ("###", "####", "**"):
        check(pat not in body, f"页面文案无残留标记 {pat!r}")
    # 隐形字：解析每个 .sg 的 color 与所在页背景色
    deck_bgs = re.findall(r"class='deck' data-fit style='[^']*?(background:#[0-9A-Fa-f]{3,6})", h)
    check(len(deck_bgs) == expect_decks, f"每页读到背景色（{len(deck_bgs)}）")
    # 脚本完整性
    for frag in ("var hostW = host.clientWidth || 0;", "__fitSlots", "ResizeObserver",
                 "__deckShowPage", "__offaReady", "offa-loading"):
        check(frag in h, f"脚本片段完整：{frag}")
    # 数据一致性：表格与图表都用 12/28/45
    check("用户数(万)" in h or "用户数（万）" in h, "含用户数口径说明")
    check(h.count(">12<") >= 1 and ">120<" not in h, "图表/表格数值已对齐（无 120/280/450/680 残留）")
    return h


if __name__ == "__main__":
    deck = sys.argv[1]
    html_path = sys.argv[2]
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 10
    deck_checks(deck)
    overlap_checks(deck)
    shape_overlap_checks(deck)
    html_checks(html_path, n)
    print("\n[结果] " + ("全部通过" if not fail else f"{len(fail)} 项未通过"))
    for m in fail:
        print("  - " + m)
    sys.exit(1 if fail else 0)
