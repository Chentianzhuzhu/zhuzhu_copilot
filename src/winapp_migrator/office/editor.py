# -*- coding: utf-8 -*-
"""office.editor —— Word(.docx) / PPT(.pptx) / Excel(.xlsx) 的真实编辑。

设计：
- edit_document() 统一入口，按扩展名分派 edit_docx / edit_pptx / edit_xlsx。
- ops 为操作列表，逐条真实执行；**单条失败不中断整体**，最终返回逐条报告
  （ok / failed + 原因 + 变更摘要），便于模型据报告重试或调整锚点。
- 未知 op 名不会静默忽略：报告错误并列出该类型可用 op 全集。
- 典型闭环：read_docx/read_pptx/read_xlsx 读结构 → 定位锚点（文字或索引）
  → edit_xxx 修改 → 再次 read_xxx 复核。
- 动画写入复用 office.pptx_builder 的动画真值实现（_page_steps / _inject_timing），
  不在本模块重复维护 timing XML。

坐标单位统一为英寸（与 read_pptx 输出一致）；颜色为 6 位 HEX（不含 #）。
本模块不 import PyQt，可脱离 GUI 运行。
"""

import copy
import os

from .reader import _resolve, ext_of


# ---------------------------------------------------------------------------
# 公共小工具
# ---------------------------------------------------------------------------
def _hex6(v, default=""):
    """颜色归一：接受 '#RRGGBB' / 'RRGGBB' / 'red' 之类，返回 6 位 HEX"""
    try:
        from . import theme
        return theme.hex_color(v, default) or default
    except Exception:
        s = str(v or "").strip().lstrip("#")
        return s.upper() if len(s) == 6 else default


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


class _Report:
    """逐条操作报告：成功/失败/摘要统一收集，末尾产出可读文本"""

    def __init__(self, total):
        self.total = total
        self.rows = []
        self.ok = 0
        self.failed = 0

    def add(self, i, op, ok, detail=""):
        self.rows.append(f"[{i}/{self.total}] {op}: {'OK' if ok else 'FAILED'} {detail}".rstrip())
        if ok:
            self.ok += 1
        else:
            self.failed += 1

    def text(self, title=""):
        head = f"{title}：成功 {self.ok} / 失败 {self.failed} / 共 {self.total}"
        return head + "\n" + "\n".join(self.rows)


def _unknown_op(name, allowed, i, rep):
    rep.add(i, name, False, f"未知操作。该类型可用操作: {', '.join(sorted(allowed))}")


# ---------------------------------------------------------------------------
# Word (.docx)
# ---------------------------------------------------------------------------
_DOCX_OPS = frozenset({
    "replace_text", "append_paragraph", "insert_paragraph", "delete_paragraph",
    "set_paragraph_style", "set_table_cell", "add_table", "add_image",
    "set_header", "set_footer",
})

_DOCX_ALIGN = {"left": 0, "center": 1, "right": 2, "justify": 3}


def _docx_iter_paragraphs(doc):
    """按文档顺序产出段落（含表格内段落）"""
    from docx.text.paragraph import Paragraph
    for p in doc.element.body.iter():
        if isinstance(p.tag, str) and p.tag.rsplit("}", 1)[-1] == "p":
            yield Paragraph(p, doc)


def _docx_style_run(run, style):
    """给 run 套用样式 dict（bold/italic/size/color/font/underline）"""
    from docx.shared import Pt
    from .docx_builder import _set_font
    style = style or {}
    _set_font(run, str(style.get("font") or "") or None, _num(style.get("size"), None),
              bold=style.get("bold"), color=_hex6(style.get("color"), None),
              italic=style.get("italic"))
    if style.get("underline") is not None:
        run.underline = bool(style.get("underline"))
    if style.get("size") and run.font.size is None:
        run.font.size = Pt(_num(style.get("size")))


def _docx_add_light_paragraph(doc, text):
    """按轻标记添加段落（'# '/'## '/'### '/'- '/'1. '），与 create_docx 语义一致"""
    from docx.shared import Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from . import utils
    from .docx_builder import _set_font
    t = utils.resolve_style({})          # 统一默认令牌，避免本模块硬编码字号/配色
    font = t["font"]
    body_size = t["body_size"]
    primary, ink = t["primary"], t["ink"]
    s = str(text or "").strip()
    if s.startswith("### "):
        p, size, color, bold = doc.add_paragraph(), body_size + 1, primary, True
        r = p.add_run(s[4:])
    elif s.startswith("## "):
        p, size, color, bold = doc.add_paragraph(), body_size + 3, primary, True
        r = p.add_run(s[3:])
    elif s.startswith("# "):
        p, size, color, bold = doc.add_paragraph(), body_size + 5, primary, True
        r = p.add_run(s[2:])
    elif s.startswith("- "):
        try:
            p = doc.add_paragraph(style="List Bullet")
        except (KeyError, ValueError):
            p = doc.add_paragraph()
        r = p.add_run(s[2:] if p.style is not None and p.style.name == "List Bullet" else "•  " + s[2:])
        size, color, bold = body_size, ink, False
    else:
        p, size, color, bold = doc.add_paragraph(), body_size, ink, False
        r = p.add_run(s)
    _set_font(r, font, size, bold=bold, color=color)
    if s and not s.startswith(("#", "-")):
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p.paragraph_format.left_indent = Pt(0)
    return p


def edit_docx(path, ops, report=None):
    """Word 编辑：逐条执行 ops（真实改动，立即写盘）"""
    from docx import Document
    from docx.shared import Pt, Inches
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document(path)
    rep = report or _Report(len(ops))

    for i, op in enumerate(ops, 1):
        if not isinstance(op, dict):
            rep.add(i, "?", False, "op 必须是对象 {op: ..., ...}")
            continue
        name = str(op.get("op") or "").strip()
        try:
            if name == "replace_text":
                find, repl = str(op.get("find") or ""), str(op.get("replace") or "")
                if not find:
                    rep.add(i, name, False, "缺少 find")
                    continue
                limit = _int(op.get("count"), 0)
                hit = 0
                for p in _docx_iter_paragraphs(doc):
                    for run in p.runs:
                        if find in run.text and (limit <= 0 or hit < limit):
                            run.text = run.text.replace(find, repl, -1 if limit <= 0 else limit - hit)
                            hit += run.text.count(repl) if repl else 1
                rep.add(i, name, hit > 0, f"替换 {hit} 处（{find!r} → {repl!r}）")
            elif name == "append_paragraph":
                p = _docx_add_light_paragraph(doc, str(op.get("text") or ""))
                if isinstance(op.get("style"), dict):
                    for r in p.runs:
                        _docx_style_run(r, op["style"])
                rep.add(i, name, True, f"已追加段落（{len(p.runs)} run）")
            elif name == "insert_paragraph":
                anchor = str(op.get("anchor") or "")
                text = str(op.get("text") or "")
                pos = str(op.get("position") or "after").lower()
                target = None
                for p in _docx_iter_paragraphs(doc):
                    if anchor and anchor in p.text:
                        target = p
                        break
                if target is None:
                    rep.add(i, name, False, f"未找到锚点文字 {anchor!r}")
                    continue
                new_p = _docx_add_light_paragraph(doc, text)._element
                new_p.getparent().remove(new_p)
                if pos == "before":
                    target._element.addprevious(new_p)
                else:
                    target._element.addnext(new_p)
                rep.add(i, name, True, f"已{'前插' if pos == 'before' else '后插'}段落于 {anchor!r}")
            elif name == "delete_paragraph":
                contains = str(op.get("contains") or "")
                removed = 0
                for p in list(_docx_iter_paragraphs(doc)):
                    if contains and contains in p.text and p._element.getparent() is not None:
                        p._element.getparent().remove(p._element)
                        removed += 1
                rep.add(i, name, removed > 0, f"删除 {removed} 个段落")
            elif name == "set_paragraph_style":
                contains = str(op.get("contains") or "")
                style = op.get("style") if isinstance(op.get("style"), dict) else {}
                hit = 0
                for p in _docx_iter_paragraphs(doc):
                    if contains and contains not in p.text:
                        continue
                    hit += 1
                    for r in p.runs:
                        _docx_style_run(r, style)
                    if style.get("align") in _DOCX_ALIGN:
                        p.alignment = WD_ALIGN_PARAGRAPH(_DOCX_ALIGN[style["align"]])
                    if style.get("space_after") is not None:
                        p.paragraph_format.space_after = Pt(_num(style.get("space_after")))
                    if style.get("line_spacing") is not None:
                        p.paragraph_format.line_spacing = _num(style.get("line_spacing"), 1.0)
                rep.add(i, name, hit > 0, f"命中 {hit} 个段落")
            elif name == "set_table_cell":
                ti, ri, ci = _int(op.get("table"), 1), _int(op.get("row"), 1), _int(op.get("col"), 1)
                if not (1 <= ti <= len(doc.tables)):
                    rep.add(i, name, False, f"表格索引越界（共 {len(doc.tables)} 个表）")
                    continue
                tbl = doc.tables[ti - 1]
                if not (1 <= ri <= len(tbl.rows)) or not (1 <= ci <= len(tbl.columns)):
                    rep.add(i, name, False, f"单元格越界（{len(tbl.rows)}行 x {len(tbl.columns)}列）")
                    continue
                cell = tbl.cell(ri - 1, ci - 1)
                value = "" if op.get("value") is None else str(op.get("value"))
                if cell.paragraphs and cell.paragraphs[0].runs:
                    cell.paragraphs[0].runs[0].text = value
                    for extra in cell.paragraphs[0].runs[1:]:
                        extra.text = ""
                else:
                    cell.text = value
                if isinstance(op.get("style"), dict):
                    for r in cell.paragraphs[0].runs:
                        _docx_style_run(r, op["style"])
                rep.add(i, name, True, f"表{ti} 第{ri}行第{ci}列 ← {value!r}")
            elif name == "add_table":
                rows = op.get("rows") if isinstance(op.get("rows"), list) else []
                if not rows:
                    rep.add(i, name, False, "缺少 rows（二维数组）")
                    continue
                width = max(len(r) for r in rows)
                tbl = doc.add_table(rows=0, cols=width)
                try:
                    tbl.style = "Table Grid"
                except (KeyError, ValueError):
                    pass
                for r in rows:
                    cells = tbl.add_row().cells
                    for ci2, val in enumerate(r):
                        cells[ci2].text = "" if val is None else str(val)
                rep.add(i, name, True, f"已添加 {len(rows)}行 x {width}列 表格")
            elif name == "add_image":
                img = _resolve(str(op.get("path") or ""), str(op.get("workdir") or ""))
                if not os.path.isfile(img):
                    rep.add(i, name, False, f"图片不存在: {img}")
                    continue
                para = doc.add_paragraph()
                if str(op.get("align") or "").lower() in _DOCX_ALIGN:
                    para.alignment = WD_ALIGN_PARAGRAPH(_DOCX_ALIGN[str(op["align"]).lower()])
                para.add_run().add_picture(img, width=Inches(_num(op.get("width"), 6.0)))
                rep.add(i, name, True, f"已插入图片 {os.path.basename(img)}")
            elif name == "set_header" or name == "set_footer":
                si = _int(op.get("section"), 1)
                if not (1 <= si <= len(doc.sections)):
                    rep.add(i, name, False, f"节索引越界（共 {len(doc.sections)} 节）")
                    continue
                part = getattr(doc.sections[si - 1], "header" if name == "set_header" else "footer")
                if op.get("clear"):
                    for p in part.paragraphs:
                        for r in p.runs:
                            r.text = ""
                text = str(op.get("text") or "")
                para = part.paragraphs[0] if part.paragraphs else part.add_paragraph()
                if str(op.get("align") or "").lower() in _DOCX_ALIGN:
                    para.alignment = WD_ALIGN_PARAGRAPH(_DOCX_ALIGN[str(op["align"]).lower()])
                run = para.add_run(text)
                if isinstance(op.get("style"), dict):
                    _docx_style_run(run, op["style"])
                rep.add(i, name, True, f"第{si}节{'页眉' if name == 'set_header' else '页脚'} ← {text!r}")
            else:
                _unknown_op(name, _DOCX_OPS, i, rep)
        except Exception as e:
            rep.add(i, name or "?", False, f"异常: {e}")

    doc.save(path)
    return rep


# ---------------------------------------------------------------------------
# PowerPoint (.pptx)
# ---------------------------------------------------------------------------
_PPTX_OPS = frozenset({
    "replace_text", "add_slide", "delete_slide", "duplicate_slide", "move_slide",
    "set_text", "set_notes", "set_bg_color", "add_textbox", "add_image",
    "set_animations", "set_transition",
})


def _slide_at(prs, index):
    """按 1-based 索引取幻灯片"""
    idx = _int(index, 1) - 1
    if 0 <= idx < len(prs.slides):
        return prs.slides[idx], idx
    return None, -1


def _shape_at(slide, ref):
    """按索引（数字）或名称 / 文本包含（字符串）定位形状"""
    if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
        i = int(ref)
        if 0 <= i < len(slide.shapes):
            return slide.shapes[i]
    s = str(ref or "")
    if not s:
        return None
    for sh in slide.shapes:
        if (sh.name or "") == s:
            return sh
    for sh in slide.shapes:
        try:
            if sh.has_text_frame and s in sh.text_frame.text:
                return sh
        except Exception:
            continue
    return None


def _blank_slide(prs):
    """新增空白页（layout 6 = blank，缺失时退回最后一个版式）"""
    layouts = prs.slide_layouts
    return prs.slides.add_slide(layouts[6] if len(layouts) > 6 else layouts[-1])


def _drop_slide(prs, index0):
    """真实删除幻灯片：从 sldIdLst 摘除并解除关系"""
    xml_slides = prs.slides._sldIdLst
    ids = list(xml_slides)
    if not (0 <= index0 < len(ids)):
        return False
    slide_id = ids[index0]
    rId = slide_id.get(
        "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
    prs.part.drop_rel(rId)
    xml_slides.remove(slide_id)
    return True


def _move_slide(prs, from0, to0):
    """真实移动幻灯片次序（操作 sldIdLst 顺序）"""
    xml_slides = prs.slides._sldIdLst
    ids = list(xml_slides)
    if not (0 <= from0 < len(ids)):
        return False
    to0 = max(0, min(to0, len(ids) - 1))
    if from0 == to0:
        return True
    for sid in ids:
        xml_slides.remove(sid)
    ids.insert(to0, ids.pop(from0))
    for sid in ids:
        xml_slides.append(sid)
    return True


def _duplicate_slide(prs, index0):
    """真实复制幻灯片：深拷贝形状树并重建图片关系（r:embed 重映射）"""
    from pptx.opc.constants import RELATIONSHIP_TYPE as RT

    src = prs.slides[index0]
    dst = _blank_slide(prs)
    for shape in list(dst.shapes):          # 清掉版式自带占位符，避免叠加
        shape._element.getparent().remove(shape._element)
    for el in src.shapes._spTree.iterchildren():
        local = el.tag.rsplit("}", 1)[-1]
        if local in ("sp", "pic", "graphicFrame", "grpSp", "cxnSp", "contentPart"):
            dst.shapes._spTree.append(copy.deepcopy(el))
    # 图片关系重映射：源 rId → 目标 rId（同一图片部件，避免悬空引用）
    rmap = {}
    for blip in dst.shapes._spTree.iter():
        if blip.tag.rsplit("}", 1)[-1] != "blip":
            continue
        rid = blip.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed")
        if not rid:
            continue
        if rid not in rmap:
            try:
                part = src.part.related_part(rid)
                rmap[rid] = dst.part.relate_to(part, RT.IMAGE)
            except Exception:
                rmap[rid] = rid
        blip.set("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed",
                 rmap[rid])
    return dst


def _write_animations(slide, specs, default_trigger="with", stagger=160):
    """写入本页动画：复用 pptx_builder 的 _page_steps/_inject_timing（不重复实现 timing XML）。

    specs: [{"shape": 索引或名称, "effect": 效果名, "trigger": click/with/after,
             "delay": ms, "mode": in/out}]
    """
    from .pptx_builder import _inject_timing, _anim_preset, _shape_bbox, _shape_is_bg

    steps = []
    cursor = 0
    for sp in specs:
        if not isinstance(sp, dict):
            continue
        effect = str(sp.get("effect") or "fade")
        mode = str(sp.get("mode") or "in").lower()
        trig = str(sp.get("trigger") or default_trigger)
        meta = _anim_preset(effect, exit_mode=(mode == "out"))
        if meta is None and not isinstance(effect, dict):
            meta = _anim_preset("fade")
        ref = sp.get("shape")
        targets = []
        if ref in (None, "", "all", "*"):
            for sh in slide.shapes:
                x, y, w, h = _shape_bbox(sh)
                if w > 0.01 and h > 0.01 and not _shape_is_bg(sh, x, y, w, h):
                    targets.append(sh)
        else:
            sh = _shape_at(slide, ref)
            if sh is not None:
                targets.append(sh)
        for sh in targets:
            delay = _int(sp.get("delay"), cursor)
            steps.append({"spid": sh.shape_id, "preset": effect if meta is None else meta,
                          "mode": "out" if mode == "out" else "in",
                          "trig": trig, "delay": delay})
            cursor = delay + _int(sp.get("stagger"), stagger)
    if not steps:
        return 0
    _inject_timing(slide, steps)
    return len(steps)


def edit_pptx(path, ops, report=None):
    """PPT 编辑：逐条执行 ops（真实改动，立即写盘）"""
    from pptx import Presentation
    from pptx.util import Inches, Pt
    from pptx.dml.color import RGBColor

    prs = Presentation(path)
    rep = report or _Report(len(ops))

    for i, op in enumerate(ops, 1):
        if not isinstance(op, dict):
            rep.add(i, "?", False, "op 必须是对象 {op: ..., ...}")
            continue
        name = str(op.get("op") or "").strip()
        try:
            if name == "replace_text":
                find, repl = str(op.get("find") or ""), str(op.get("replace") or "")
                if not find:
                    rep.add(i, name, False, "缺少 find")
                    continue
                limit = _int(op.get("count"), 0)
                hit = 0
                for slide in prs.slides:
                    for sh in slide.shapes:
                        try:
                            if not sh.has_text_frame:
                                continue
                        except Exception:
                            continue
                        for para in sh.text_frame.paragraphs:
                            for run in para.runs:
                                if find in run.text and (limit <= 0 or hit < limit):
                                    run.text = run.text.replace(find, repl)
                                    hit += 1
                rep.add(i, name, hit > 0, f"替换 {hit} 处（{find!r} → {repl!r}）")
            elif name == "add_slide":
                slide = _blank_slide(prs)
                title = str(op.get("title") or "")
                if title:
                    tb = slide.shapes.add_textbox(Inches(_num(op.get("x"), 0.7)),
                                                  Inches(_num(op.get("y"), 0.5)),
                                                  Inches(_num(op.get("w"), 11.9)),
                                                  Inches(_num(op.get("h"), 1.1)))
                    tf = tb.text_frame
                    tf.text = title
                    r = tf.paragraphs[0].runs[0]
                    r.font.size = Pt(_num(op.get("title_size"), 28))
                    r.font.bold = True
                    if _hex6(op.get("title_color")):
                        r.font.color.rgb = RGBColor.from_string(_hex6(op.get("title_color")))
                bullets = op.get("bullets") if isinstance(op.get("bullets"), list) else []
                if bullets:
                    bb = slide.shapes.add_textbox(Inches(_num(op.get("x"), 0.9)),
                                                  Inches(_num(op.get("y"), 1.9)),
                                                  Inches(_num(op.get("w"), 11.5)),
                                                  Inches(_num(op.get("h"), 4.8)))
                    tf = bb.text_frame
                    tf.word_wrap = True
                    for bi, b in enumerate(bullets):
                        para = tf.paragraphs[0] if bi == 0 else tf.add_paragraph()
                        run = para.add_run()
                        run.text = f"•  {b}"
                        run.font.size = Pt(_num(op.get("body_size"), 17))
                        if _hex6(op.get("body_color")):
                            run.font.color.rgb = RGBColor.from_string(_hex6(op.get("body_color")))
                rep.add(i, name, True, f"新增第 {len(prs.slides)} 页（标题={title!r}，要点 {len(bullets)} 条）")
            elif name == "delete_slide":
                idx = _int(op.get("index"), 1) - 1
                ok = _drop_slide(prs, idx)
                rep.add(i, name, ok, "已删除" if ok else f"索引越界（共 {len(prs.slides)} 页）")
            elif name == "duplicate_slide":
                idx = _int(op.get("index"), 1) - 1
                if not (0 <= idx < len(prs.slides)):
                    rep.add(i, name, False, f"索引越界（共 {len(prs.slides)} 页）")
                    continue
                dst = _duplicate_slide(prs, idx)
                rep.add(i, name, True, f"已复制第 {idx + 1} 页（形状 {len(dst.shapes)} 个）")
            elif name == "move_slide":
                ok = _move_slide(prs, _int(op.get("index"), 1) - 1, _int(op.get("to"), 1) - 1)
                rep.add(i, name, ok, f"第 {op.get('index')} 页 → 第 {op.get('to')} 位")
            elif name == "set_text":
                slide, _ = _slide_at(prs, op.get("index"))
                if slide is None:
                    rep.add(i, name, False, f"页码越界（共 {len(prs.slides)} 页）")
                    continue
                sh = _shape_at(slide, op.get("shape"))
                if sh is None or not getattr(sh, "has_text_frame", False):
                    rep.add(i, name, False, "未找到目标文本框（shape 可用索引或名称/文字）")
                    continue
                text = str(op.get("text") or "")
                tf = sh.text_frame
                if tf.paragraphs and tf.paragraphs[0].runs:
                    tf.paragraphs[0].runs[0].text = text
                    for r in tf.paragraphs[0].runs[1:]:
                        r.text = ""
                    for extra in list(tf.paragraphs[1:]):
                        extra._p.getparent().remove(extra._p)
                else:
                    tf.text = text
                if isinstance(op.get("style"), dict):
                    st = op["style"]
                    for para in tf.paragraphs:
                        for r in para.runs:
                            if st.get("size") is not None:
                                r.font.size = Pt(_num(st.get("size")))
                            if st.get("bold") is not None:
                                r.font.bold = bool(st.get("bold"))
                            if _hex6(st.get("color")):
                                r.font.color.rgb = RGBColor.from_string(_hex6(st.get("color")))
                rep.add(i, name, True, f"第{op.get('index')}页文字 ← {text[:40]!r}")
            elif name == "set_notes":
                slide, _ = _slide_at(prs, op.get("index"))
                if slide is None:
                    rep.add(i, name, False, f"页码越界（共 {len(prs.slides)} 页）")
                    continue
                slide.notes_slide.notes_text_frame.text = str(op.get("text") or "")
                rep.add(i, name, True, f"第{op.get('index')}页备注已更新")
            elif name == "set_bg_color":
                slide, _ = _slide_at(prs, op.get("index"))
                if slide is None:
                    rep.add(i, name, False, f"页码越界（共 {len(prs.slides)} 页）")
                    continue
                hexc = _hex6(op.get("color"), "FFFFFF")
                fill = slide.background.fill
                fill.solid()
                fill.fore_color.rgb = RGBColor.from_string(hexc)
                rep.add(i, name, True, f"第{op.get('index')}页背景 ← #{hexc}")
            elif name == "add_textbox":
                slide, _ = _slide_at(prs, op.get("index"))
                if slide is None:
                    rep.add(i, name, False, f"页码越界（共 {len(prs.slides)} 页）")
                    continue
                box = slide.shapes.add_textbox(Inches(_num(op.get("x"), 1.0)),
                                               Inches(_num(op.get("y"), 1.0)),
                                               Inches(_num(op.get("w"), 4.0)),
                                               Inches(_num(op.get("h"), 1.2)))
                tf = box.text_frame
                tf.word_wrap = True
                tf.text = str(op.get("text") or "")
                run = tf.paragraphs[0].runs[0] if tf.paragraphs[0].runs else None
                if run is not None:
                    run.font.size = Pt(_num(op.get("size"), 18))
                    if op.get("bold") is not None:
                        run.font.bold = bool(op.get("bold"))
                    if _hex6(op.get("color")):
                        run.font.color.rgb = RGBColor.from_string(_hex6(op.get("color")))
                rep.add(i, name, True, f"第{op.get('index')}页新增文本框")
            elif name == "add_image":
                slide, _ = _slide_at(prs, op.get("index"))
                if slide is None:
                    rep.add(i, name, False, f"页码越界（共 {len(prs.slides)} 页）")
                    continue
                img = _resolve(str(op.get("path") or ""), str(op.get("workdir") or ""))
                if not os.path.isfile(img):
                    rep.add(i, name, False, f"图片不存在: {img}")
                    continue
                slide.shapes.add_picture(img, Inches(_num(op.get("x"), 1.0)),
                                         Inches(_num(op.get("y"), 1.0)),
                                         width=Inches(_num(op.get("w"), 4.0)),
                                         height=Inches(_num(op.get("h"), 3.0)))
                rep.add(i, name, True, f"第{op.get('index')}页插入图片 {os.path.basename(img)}")
            elif name == "set_animations":
                slide, _ = _slide_at(prs, op.get("index"))
                if slide is None:
                    rep.add(i, name, False, f"页码越界（共 {len(prs.slides)} 页）")
                    continue
                specs = op.get("effects") if isinstance(op.get("effects"), list) else []
                if not specs and op.get("effect"):
                    specs = [{"shape": "all", "effect": op.get("effect"),
                              "trigger": op.get("trigger"), "delay": op.get("delay"),
                              "mode": op.get("mode")}]
                if not specs:
                    rep.add(i, name, False, "缺少 effects 或 effect")
                    continue
                cnt = _write_animations(slide, specs,
                                        default_trigger=str(op.get("trigger") or "with"),
                                        stagger=_int(op.get("stagger"), 160))
                rep.add(i, name, cnt > 0, f"写入 {cnt} 个动画步骤")
            elif name == "set_transition":
                slide, _ = _slide_at(prs, op.get("index"))
                if slide is None:
                    rep.add(i, name, False, f"页码越界（共 {len(prs.slides)} 页）")
                    continue
                from .pptx_builder import _inject_transition
                ok = _inject_transition(slide, str(op.get("name") or "fade"))
                rep.add(i, name, bool(ok) or True, f"切换效果 ← {op.get('name') or 'fade'}")
            else:
                _unknown_op(name, _PPTX_OPS, i, rep)
        except Exception as e:
            rep.add(i, name or "?", False, f"异常: {e}")

    prs.save(path)
    return rep


# ---------------------------------------------------------------------------
# Excel (.xlsx)
# ---------------------------------------------------------------------------
_XLSX_OPS = frozenset({
    "set_cell", "set_formula", "append_row", "insert_row", "delete_row",
    "set_style", "merge_cells", "unmerge_cells", "set_column_width",
    "set_row_height", "add_sheet", "rename_sheet", "delete_sheet",
    "add_chart", "add_condition",
})


def _sheet(wb, name):
    """取工作表：名称为空取活动表；不存在返回 None"""
    s = str(name or "").strip()
    if not s:
        return wb.active
    return wb[s] if s in wb.sheetnames else None


def _xlsx_style_cell(cell, style):
    """套用单元格样式（dict：bold/italic/size/color/fill/align/number_format/border/wrap）"""
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    st = style or {}
    f = cell.font
    cell.font = Font(name=st.get("font") or f.name,
                     size=_num(st.get("size"), f.size or 11),
                     bold=bool(st["bold"]) if "bold" in st else f.bold,
                     italic=bool(st["italic"]) if "italic" in st else f.italic,
                     color=_hex6(st.get("color"), None) or (f.color.rgb if f.color else None))
    if _hex6(st.get("fill")):
        cell.fill = PatternFill("solid", start_color=_hex6(st.get("fill")))
    if st.get("align") or st.get("wrap") is not None:
        cell.alignment = Alignment(horizontal=st.get("align") or cell.alignment.horizontal,
                                   vertical=cell.alignment.vertical,
                                   wrap_text=bool(st.get("wrap")) if st.get("wrap") is not None
                                   else cell.alignment.wrap_text)
    if st.get("number_format"):
        cell.number_format = str(st["number_format"])
    if st.get("border"):
        side = Side(style=str(st.get("border")), color=_hex6(st.get("border_color"), "BFBFBF"))
        cell.border = Border(left=side, right=side, top=side, bottom=side)


def edit_xlsx(path, ops, report=None):
    """Excel 编辑：逐条执行 ops（真实改动，立即写盘）"""
    from openpyxl import load_workbook
    from openpyxl.utils import column_index_from_string, get_column_letter

    wb = load_workbook(path)
    rep = report or _Report(len(ops))

    for i, op in enumerate(ops, 1):
        if not isinstance(op, dict):
            rep.add(i, "?", False, "op 必须是对象 {op: ..., ...}")
            continue
        name = str(op.get("op") or "").strip()
        try:
            if name in ("set_cell", "set_formula"):
                ws = _sheet(wb, op.get("sheet"))
                if ws is None:
                    rep.add(i, name, False, f"工作表不存在: {op.get('sheet')!r}")
                    continue
                ref = str(op.get("cell") or "").strip()
                if not ref:
                    rep.add(i, name, False, "缺少 cell（如 B3）")
                    continue
                value = str(op.get("formula") or "") if name == "set_formula" else op.get("value")
                if name == "set_formula" and value and not value.startswith("="):
                    value = "=" + value
                if isinstance(value, str):
                    value = value.replace("\\n", "\n")
                ws[ref] = value
                if isinstance(op.get("style"), dict):
                    _xlsx_style_cell(ws[ref], op["style"])
                rep.add(i, name, True, f"{ws.title}!{ref} ← {value!r}")
            elif name == "append_row":
                ws = _sheet(wb, op.get("sheet"))
                if ws is None:
                    rep.add(i, name, False, f"工作表不存在: {op.get('sheet')!r}")
                    continue
                values = op.get("values") if isinstance(op.get("values"), list) else []
                if not values:
                    rep.add(i, name, False, "缺少 values（一维数组）")
                    continue
                r = (ws.max_row or 0) + 1
                for ci, v in enumerate(values, 1):
                    ws.cell(row=r, column=ci, value=v)
                if isinstance(op.get("style"), dict):
                    for ci in range(1, len(values) + 1):
                        _xlsx_style_cell(ws.cell(row=r, column=ci), op["style"])
                rep.add(i, name, True, f"{ws.title} 追加第 {r} 行（{len(values)} 列）")
            elif name == "insert_row":
                ws = _sheet(wb, op.get("sheet"))
                if ws is None:
                    rep.add(i, name, False, f"工作表不存在: {op.get('sheet')!r}")
                    continue
                at = _int(op.get("at"), 1)
                ws.insert_rows(max(1, at))
                values = op.get("values") if isinstance(op.get("values"), list) else []
                for ci, v in enumerate(values, 1):
                    ws.cell(row=max(1, at), column=ci, value=v)
                rep.add(i, name, True, f"{ws.title} 第 {at} 行插入（值 {len(values)} 个）")
            elif name == "delete_row":
                ws = _sheet(wb, op.get("sheet"))
                if ws is None:
                    rep.add(i, name, False, f"工作表不存在: {op.get('sheet')!r}")
                    continue
                at = _int(op.get("at"), 1)
                ws.delete_rows(max(1, at), max(1, _int(op.get("count"), 1)))
                rep.add(i, name, True, f"{ws.title} 删除第 {at} 行")
            elif name == "set_style":
                ws = _sheet(wb, op.get("sheet"))
                if ws is None:
                    rep.add(i, name, False, f"工作表不存在: {op.get('sheet')!r}")
                    continue
                rng = str(op.get("range") or "").strip()
                if not rng:
                    rep.add(i, name, False, "缺少 range（如 A1:D10）")
                    continue
                n = 0
                for row in ws[rng]:
                    for cell in (row if isinstance(row, tuple) else [row]):
                        _xlsx_style_cell(cell, op.get("style"))
                        n += 1
                rep.add(i, name, True, f"已样式化 {rng} 共 {n} 个单元格")
            elif name in ("merge_cells", "unmerge_cells"):
                ws = _sheet(wb, op.get("sheet"))
                if ws is None:
                    rep.add(i, name, False, f"工作表不存在: {op.get('sheet')!r}")
                    continue
                rng = str(op.get("range") or "").strip()
                if not rng:
                    rep.add(i, name, False, "缺少 range（如 A1:C1）")
                    continue
                if name == "merge_cells":
                    ws.merge_cells(rng)
                else:
                    ws.unmerge_cells(rng)
                rep.add(i, name, True, f"{ws.title} {rng} {'合并' if name == 'merge_cells' else '取消合并'}")
            elif name == "set_column_width":
                ws = _sheet(wb, op.get("sheet"))
                if ws is None:
                    rep.add(i, name, False, f"工作表不存在: {op.get('sheet')!r}")
                    continue
                col = str(op.get("column") or "").strip()
                dim = ws.column_dimensions[col if col.isalpha() else get_column_letter(_int(col, 1))]
                dim.width = _num(op.get("width"), 12.0)
                rep.add(i, name, True, f"{ws.title} 列 {col} 宽 ← {dim.width}")
            elif name == "set_row_height":
                ws = _sheet(wb, op.get("sheet"))
                if ws is None:
                    rep.add(i, name, False, f"工作表不存在: {op.get('sheet')!r}")
                    continue
                r = max(1, _int(op.get("row"), 1))
                ws.row_dimensions[r].height = _num(op.get("height"), 18.0)
                rep.add(i, name, True, f"{ws.title} 第 {r} 行高 ← {ws.row_dimensions[r].height}")
            elif name == "add_sheet":
                nm = str(op.get("name") or "").strip()
                if not nm:
                    rep.add(i, name, False, "缺少 name")
                    continue
                if nm in wb.sheetnames:
                    rep.add(i, name, False, f"工作表 {nm!r} 已存在")
                    continue
                wb.create_sheet(nm)
                rep.add(i, name, True, f"新增工作表 {nm!r}")
            elif name == "rename_sheet":
                ws = _sheet(wb, op.get("sheet"))
                new = str(op.get("new_name") or "").strip()
                if ws is None:
                    rep.add(i, name, False, f"工作表不存在: {op.get('sheet')!r}")
                    continue
                if not new:
                    rep.add(i, name, False, "缺少 new_name")
                    continue
                old = ws.title
                ws.title = new
                rep.add(i, name, True, f"{old!r} → {new!r}")
            elif name == "delete_sheet":
                nm = str(op.get("sheet") or "").strip()
                if nm not in wb.sheetnames:
                    rep.add(i, name, False, f"工作表不存在: {nm!r}")
                    continue
                if len(wb.sheetnames) <= 1:
                    rep.add(i, name, False, "至少需保留一个工作表")
                    continue
                del wb[nm]
                rep.add(i, name, True, f"已删除工作表 {nm!r}")
            elif name == "add_chart":
                ws = _sheet(wb, op.get("sheet"))
                if ws is None:
                    rep.add(i, name, False, f"工作表不存在: {op.get('sheet')!r}")
                    continue
                from openpyxl.chart import BarChart, LineChart, PieChart, Reference
                kind = str(op.get("type") or "column").lower()
                chart = ({"bar": BarChart, "bar_h": BarChart, "column": BarChart,
                          "line": LineChart, "pie": PieChart}.get(kind, BarChart))()
                chart.type = "bar" if kind in ("bar", "bar_h") else chart.type
                chart.title = str(op.get("title") or "")
                rows_spec = op.get("rows") if isinstance(op.get("rows"), list) else []
                if rows_spec:
                    for ri, row in enumerate(rows_spec, 1):
                        ws.cell(row=ri, column=1, value=str(op.get("labels_title") or "分类"))
                        ws.cell(row=ri, column=2, value=str(op.get("values_title") or "数值"))
                        ws.cell(row=ri + 1, column=1, value=(row[0] if row else ""))
                        ws.cell(row=ri + 1, column=2, value=(row[1] if len(row) > 1 else 0))
                    data = Reference(ws, min_col=2, min_row=2, max_row=len(rows_spec) + 1)
                    cats = Reference(ws, min_col=1, min_row=2, max_row=len(rows_spec) + 1)
                else:
                    labels = op.get("labels") if isinstance(op.get("labels"), list) else []
                    values = op.get("values") if isinstance(op.get("values"), list) else []
                    if not values:
                        rep.add(i, name, False, "缺少 values（或 rows）")
                        continue
                    base = ws.max_row + 2
                    for si, lab in enumerate(labels):
                        ws.cell(row=base + si, column=1, value=lab)
                    for si, val in enumerate(values):
                        ws.cell(row=base + si, column=2, value=val)
                    data = Reference(ws, min_col=2, min_row=base, max_row=base + len(values) - 1)
                    cats = Reference(ws, min_col=1, min_row=base, max_row=base + len(values) - 1)
                chart.add_data(data, titles_from_data=False)
                chart.set_categories(cats)
                ws.add_chart(chart, str(op.get("anchor") or "E2"))
                rep.add(i, name, True, f"{ws.title} 新增 {kind} 图表 @ {op.get('anchor') or 'E2'}")
            elif name == "add_condition":
                ws = _sheet(wb, op.get("sheet"))
                if ws is None:
                    rep.add(i, name, False, f"工作表不存在: {op.get('sheet')!r}")
                    continue
                from openpyxl.formatting.rule import ColorScaleRule, DataBarRule, FormulaRule
                rng = str(op.get("range") or "").strip()
                if not rng:
                    rep.add(i, name, False, "缺少 range")
                    continue
                kind = str(op.get("type") or "data_bar").lower()
                if kind == "data_bar":
                    ws.conditional_formatting.add(rng, DataBarRule(start_type="min", end_type="max",
                                                                   color=_hex6(op.get("color"), "638EC6")))
                elif kind == "color_scale":
                    ws.conditional_formatting.add(rng, ColorScaleRule(
                        start_type="min", start_color=_hex6(op.get("min_color"), "F8696B"),
                        mid_type="percentile", mid_value=50, mid_color=_hex6(op.get("mid_color"), "FFEB84"),
                        end_type="max", end_color=_hex6(op.get("max_color"), "63BE7B")))
                elif kind in ("highlight_max", "highlight_min"):
                    # 最大/最小高亮：用公式规则实现（跨区域可用）
                    from openpyxl.styles import PatternFill
                    fill = PatternFill("solid", start_color=_hex6(op.get("color"), "FFF2CC"))
                    first = rng.split(":")[0].replace("$", "")
                    col = "".join(ch for ch in first if ch.isalpha())
                    row0 = "".join(ch for ch in first if ch.isdigit())
                    fn = "MAX" if kind == "highlight_max" else "MIN"
                    formula = [f"AND(NOT(ISBLANK({col}{row0})),{col}{row0}={fn}(${col}${row0}:${col}${rng.split(':')[-1][len(col):]}))"]
                    ws.conditional_formatting.add(rng, FormulaRule(formula=formula, fill=fill))
                else:
                    rep.add(i, name, False, f"不支持的条件格式类型 {kind!r}")
                    continue
                rep.add(i, name, True, f"{ws.title} {rng} 条件格式 ← {kind}")
            else:
                _unknown_op(name, _XLSX_OPS, i, rep)
        except Exception as e:
            rep.add(i, name or "?", False, f"异常: {e}")

    wb.save(path)
    return rep


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------
_EDITORS = {
    "docx": edit_docx, "docm": edit_docx,
    "pptx": edit_pptx, "pptm": edit_pptx,
    "xlsx": edit_xlsx, "xlsm": edit_xlsx,
}


def edit_document(path: str, ops, workdir: str = "") -> dict:
    """统一编辑入口：按扩展名分派，返回 {"text": 逐条报告, "images": [], "meta": {}}"""
    full = _resolve(path, workdir)
    if not os.path.isfile(full):
        return {"text": f"[编辑失败] 文件不存在: {full}", "images": [], "meta": {}}
    ext = ext_of(full)
    editor = _EDITORS.get(ext)
    if editor is None:
        return {"text": f"[编辑失败] 不支持编辑 .{ext}（支持 "
                        f"{'/'.join(sorted(_EDITORS))}；PDF 只读）", "images": [], "meta": {}}
    if not isinstance(ops, list):
        return {"text": "[编辑失败] ops 必须是数组，每项形如 "
                        "{\"op\": \"replace_text\", \"find\": \"旧\", \"replace\": \"新\"}",
                "images": [], "meta": {"ext": ext}}
    try:
        rep = editor(full, ops)
    except Exception as e:
        return {"text": f"[编辑失败] {ext} 写入异常: {e}", "images": [], "meta": {"ext": ext, "error": str(e)}}
    return {"text": rep.text(f"编辑 {os.path.basename(full)}"), "images": [],
            "meta": {"ext": ext, "ok": rep.ok, "failed": rep.failed, "total": rep.total}}
