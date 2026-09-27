# -*- coding: utf-8 -*-
"""Word 文档高质量生成器（python-docx）。

相比旧 create_docx 的能力增量：
- 独立封面（title + subtitle + author/date + 装饰分隔线 + 可选封面图，自动分页到正文）
- 目录域（[toc] 标记，开启 updateFields 打开文档时自动刷新）
- 轻量表格（连续 "| 列1 | 列2 |" 行自动成表，首行主题色表头，隔行浅底）
- 引用块（"> " 段：左侧强调色竖线 + 浅底）
- 分页符（[pagebreak]）与图片图注（caption）
- 页眉（可选 header_text）与页脚居中页码（域字段，Word/WPS 实时页码）
- 统一的主题色板（office/theme），Word/PPT/Excel 视觉一致

输入文本协议（paragraphs 列表）：
  '# ' / '## ' / '### '  一至三级标题（标题前自动防孤行）
  '- '                   项目符号列表
  '1. ' / '2. '          有序列表（按任意 'N. ' 前缀识别）
  '> '                   引用块
  '| a | b |'            表格行（连续多行；首行为表头；允许整行留空中断）
  '[pagebreak]'          分页符
  其余                    正文段落
"""

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from . import theme, utils

_ALIGN = {"left": WD_ALIGN_PARAGRAPH.LEFT, "center": WD_ALIGN_PARAGRAPH.CENTER,
          "right": WD_ALIGN_PARAGRAPH.RIGHT, "justify": WD_ALIGN_PARAGRAPH.JUSTIFY}


def _add_watermark(section, text, color="D9D9D9", size=88):
    """页眉 VML 对角文字水印（Word/WPS 通用），避免遮挡正文。"""
    if not text.strip():
        return
    from lxml import etree
    P, R = qn("w:p"), qn("w:r")
    header = section.header
    p_el = header.paragraphs[0]._p if header.paragraphs else header.add_paragraph()._p
    # 清理历史水印
    for old in p_el.findall(qn("w:r")):
        if old.find(qn("w:pict")) is not None:
            p_el.remove(old)
    # VML 命名空间
    nsmap = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
             "o": "urn:schemas-microsoft-com:office:office",
             "v": "urn:schemas-microsoft-com:vml"}
    w = "{%s}" % nsmap["w"]
    v = "{%s}" % nsmap["v"]
    o = "{%s}" % nsmap["o"]
    r_el = etree.SubElement(p_el, R)
    pict = etree.SubElement(r_el, w + "pict")
    shape = etree.SubElement(pict, v + "shape", nsmap=nsmap)
    shape.set("id", "PowerPlusWaterMarkObject")
    shape.set(o + "spid", "_x0000_s1025")
    shape.set("type", "#_x0000_t136")
    shape.set("style",
              "position:absolute;margin-left:0;margin-top:0;width:480pt;height:300pt;"
              "rotation:315;z-index:-251654144;mso-position-horizontal:center;"
              "mso-position-horizontal-relative:margin;mso-position-vertical:center;"
              "mso-position-vertical-relative:margin")
    shape.set(o + "allowincell", "f")
    shape.set("filled", "t")
    shape.set("fillcolor", color)
    shape.set("stroked", "f")
    tb = etree.SubElement(shape, v + "textbox")
    tb.set("inset", "0pt,0pt,0pt,0pt")
    tbx = etree.SubElement(tb, w + "txbxContent")
    wp = etree.SubElement(tbx, P)
    wr = etree.SubElement(wp, R)
    rpr = etree.SubElement(wr, w + "rPr")
    c = etree.SubElement(rpr, w + "color")
    c.set(w + "val", color)
    sz = etree.SubElement(rpr, w + "sz")
    sz.set(w + "val", str(max(400, int(size) * 10)))
    lt = etree.SubElement(rpr, w + "lang")
    t = etree.SubElement(wr, w + "t")
    t.text = str(text)
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")


def _band_cover(doc, title, cover_d, font, primary, ink, soft, title_size):
    """band 封面：居中标题整段主题色填充（横贯色带）+ 白色大标题，下方副题/作者日期。"""
    for _ in range(4):
        doc.add_paragraph()
    bp = doc.add_paragraph()
    bp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _shade_para(bp, primary)
    bp.paragraph_format.space_before = Pt(90)
    bp.paragraph_format.space_after = Pt(90)
    btr = bp.add_run(str(title))
    _set_font(btr, font, max(30, title_size + 14), bold=True, color="FFFFFF")
    for _ in range(2):
        doc.add_paragraph()
    subtitle = str(cover_d.get("subtitle") or "")
    author = str(cover_d.get("author") or "")
    date = str(cover_d.get("date") or "")
    if subtitle:
        sp = doc.add_paragraph()
        sp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        sr = sp.add_run(str(subtitle))
        _set_font(sr, font, 15, color=ink)
    if author or date:
        mp = doc.add_paragraph()
        mp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        mr = mp.add_run("　".join(x for x in (author, date) if x))
        _set_font(mr, font, 12, color=soft)
    doc.add_page_break()


def _set_font(run, font, size=None, bold=None, color=None, italic=None):
    run.font.name = font
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = rpr.makeelement(qn("w:rFonts"), {})
        rpr.insert(0, rfonts)
    rfonts.set(qn("w:eastAsia"), font)
    if size:
        run.font.size = Pt(size)
    if bold is not None:
        run.font.bold = bold
    if italic is not None:
        run.font.italic = italic
    if color:
        run.font.color.rgb = RGBColor.from_string(theme.hex_color(color))


def _shade_cell(cell, hex_color):
    """单元格底纹（w:shd）。"""
    tcpr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), theme.hex_color(hex_color))
    tcpr.append(shd)


def _para_border_left(para, hex_color, size=18):
    """段落下加粗左竖线（引用块）。"""
    ppr = para._p.get_or_add_pPr()
    pbdr = OxmlElement("w:pBdr")
    left = OxmlElement("w:left")
    left.set(qn("w:val"), "single")
    left.set(qn("w:sz"), str(size))
    left.set(qn("w:space"), "8")
    left.set(qn("w:color"), theme.hex_color(hex_color))
    pbdr.append(left)
    ppr.append(pbdr)


def _shade_para(para, hex_color):
    ppr = para._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), theme.hex_color(hex_color))
    ppr.append(shd)


def _add_page_field(para, instr="PAGE"):
    run = para.add_run()
    fld = OxmlElement("w:fldChar")
    fld.set(qn("w:fldCharType"), "begin")
    run._r.append(fld)
    run2 = para.add_run()
    it = OxmlElement("w:instrText")
    it.set(qn("xml:space"), "preserve")
    it.text = f" {instr} "
    run2._r.append(it)
    run3 = para.add_run()
    sep = OxmlElement("w:fldChar")
    sep.set(qn("w:fldCharType"), "separate")
    run3._r.append(sep)
    run4 = para.add_run("1")
    run5 = para.add_run()
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run5._r.append(end)
    return [run, run2, run3, run4, run5]


def _enable_update_fields(doc):
    settings = doc.settings.element
    if settings.find(qn("w:updateFields")) is None:
        uf = OxmlElement("w:updateFields")
        uf.set(qn("w:val"), "true")
        settings.append(uf)


def _add_toc(doc, levels="1-3"):
    para = doc.add_paragraph()
    para.paragraph_format.space_before = Pt(6)
    para.paragraph_format.space_after = Pt(6)
    r = para.add_run()
    fld = OxmlElement("w:fldChar")
    fld.set(qn("w:fldCharType"), "begin")
    r._r.append(fld)
    r2 = para.add_run()
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = f' TOC \\o "{levels}" \\h \\z \\u '
    r2._r.append(instr)
    r3 = para.add_run()
    sep = OxmlElement("w:fldChar")
    sep.set(qn("w:fldCharType"), "separate")
    r3._r.append(sep)
    r4 = para.add_run("（目录：文档打开时将自动更新；如未更新请全选后按 F9）")
    r5 = para.add_run()
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    r5._r.append(end)
    _enable_update_fields(doc)


def _auto_table(doc, rows, font, primary, band):
    """rows: list[list[str]]，首行为表头。生成主题表头 + 隔行表格。"""
    ncols = max(len(r) for r in rows)
    nrows = len(rows)
    table = doc.add_table(rows=nrows, cols=ncols)
    table.style = doc.styles["Table Grid"]
    table.autofit = True
    for ri, row in enumerate(rows):
        for ci in range(ncols):
            cell = table.cell(ri, ci)
            text = row[ci] if ci < len(row) else ""
            cell.paragraphs[0].text = ""
            run = cell.paragraphs[0].add_run(str(text))
            if ri == 0:
                _shade_cell(cell, primary)
                _set_font(run, font, 10.5, bold=True, color="FFFFFF")
                cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            else:
                if ri % 2 == 0:
                    _shade_cell(cell, band)
                _set_font(run, font, 10.5, color="333333")
            cell.paragraphs[0].paragraph_format.space_before = Pt(1)
            cell.paragraphs[0].paragraph_format.space_after = Pt(1)
    # 表后留白
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def _add_image(doc, item, font, workdir, soft):
    img_src, ialign, iwidth, caption = "", "center", 6.0, ""
    if isinstance(item, str):
        img_src = item
    elif isinstance(item, dict):
        img_src = str(item.get("path") or "")
        ialign = str(item.get("align") or "center").lower()
        try:
            iwidth = float(item.get("width") or 6.0)
        except (TypeError, ValueError):
            iwidth = 6.0
        caption = str(item.get("caption") or "")
    if not img_src.strip():
        return False
    try:
        img_p = utils.resolve_path(img_src, workdir)
    except ValueError:
        return False
    if not img_p.is_file():
        return False
    para = doc.add_paragraph()
    para.alignment = _ALIGN.get(ialign, WD_ALIGN_PARAGRAPH.CENTER)
    run = para.add_run()
    try:
        run.add_picture(str(img_p), width=Inches(min(iwidth, 6.0)))
    except Exception:
        return False
    if caption:
        cap = doc.add_paragraph()
        cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
        cr = cap.add_run(str(caption))
        _set_font(cr, font, 9, color=soft)
    return True


def build_docx(path, title="", paragraphs=None, images=None, style=None,
               wordart=None, workdir="") -> dict:
    """生成 Word 文档。返回 {"text": 结果描述}；失败抛 ValueError/OSError 由调用方转错误。"""
    t = utils.resolve_style(style)
    font = t["font"]
    primary, accent, ink, soft, band = t["primary"], t["accent"], t["ink"], t["soft"], t["band"]
    body_size = t["body_size"]
    ls = t["line_spacing"]
    _sd = style if isinstance(style, dict) else {}
    align = _ALIGN.get(str(_sd.get("align") or "justify"), WD_ALIGN_PARAGRAPH.JUSTIFY)

    doc = Document()
    # 纸张方向
    if t["page"] == "landscape":
        sec = doc.sections[0]
        sec.orientation = WD_ORIENT.LANDSCAPE
        sec.page_width, sec.page_height = sec.page_height, sec.page_width
    # 页脚页码 / 页眉（可选文本）
    footer = doc.sections[0].footer
    fp = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _add_page_field(fp)
    for r in fp.runs:
        _set_font(r, font, 9, color=soft)
    htxt = ""
    if isinstance(style, dict):
        htxt = str(style.get("header_text") or "").strip()
    if htxt:
        hp = doc.sections[0].header.paragraphs[0]
        hp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        hr = hp.add_run(htxt)
        _set_font(hr, font, 9, color=soft)
    # 文字水印（style.watermark={text,color,size}，倾斜平铺不挡正文）
    _wm = isinstance(style, dict) and style.get("watermark") or None
    _wm_d = _wm if isinstance(_wm, dict) else {}
    _wm_text = str(_wm_d.get("text") or "")
    if _wm_text:
        _add_watermark(doc.sections[0], _wm_text,
                       color=theme.hex_color(_wm_d.get("color"), "D9D9D9"),
                       size=theme.to_int(_wm_d.get("size"), 88, 24, 200))

    # ---- 封面 ----
    cover = isinstance(style, dict) and isinstance(style.get("cover"), dict)
    cover_d = (style or {}).get("cover") if cover else {}
    cover_style = str(cover_d.get("style") or "").lower() if isinstance(cover_d, dict) else ""
    subtitle = str(cover_d.get("subtitle") or "")
    author = str(cover_d.get("author") or "")
    date = str(cover_d.get("date") or "")
    cover_on = bool((title or "").strip()) and (cover or bool(subtitle or author or date))
    if cover_on and cover_style == "band":
        _band_cover(doc, title, cover_d, font, primary, ink, soft, t["title_size"])
    else:
        if cover_on:
            for _ in range(4):
                doc.add_paragraph()
            big = doc.add_paragraph()
            big.alignment = WD_ALIGN_PARAGRAPH.CENTER
            br = big.add_run(str(title))
            _set_font(br, font, max(26, t["title_size"] + 10), bold=True, color=primary)
            if subtitle:
                sp = doc.add_paragraph()
                sp.alignment = WD_ALIGN_PARAGRAPH.CENTER
                sr = sp.add_run(str(subtitle))
                _set_font(sr, font, 15, color=ink)
            line = doc.add_paragraph()
            line.alignment = WD_ALIGN_PARAGRAPH.CENTER
            lr = line.add_run("-" * 46)
            _set_font(lr, font, 10, color=accent)
            for _ in range(2):
                doc.add_paragraph()
            if author or date:
                mp = doc.add_paragraph()
                mp.alignment = WD_ALIGN_PARAGRAPH.CENTER
                mr = mp.add_run("　".join(x for x in (author, date) if x))
                _set_font(mr, font, 12, color=soft)
            # 可选封面图
            if isinstance(cover_d, dict) and str(cover_d.get("image") or "").strip():
                _add_image(doc, cover_d.get("image"), font, workdir, soft)
            doc.add_page_break()

    # ---- 正文渲染 ----
    def _head(text, level):
        p = doc.add_paragraph()
        r = p.add_run(text)
        sizes = {1: body_size + 5, 2: body_size + 3, 3: body_size + 1}
        _set_font(r, font, sizes.get(level, body_size + 1), bold=True, color=primary)
        pf = p.paragraph_format
        pf.space_before = Pt(14 if level == 1 else 10)
        pf.space_after = Pt(6)
        pf.keep_with_next = True
        return p

    rows_buf = []
    for raw in (paragraphs or []):
        para = str(raw)
        # 表格：连续 '|' 开头行累积
        if para.lstrip().startswith("|") and para.rstrip().endswith("|"):
            cells = [c.strip() for c in para.strip().strip("|").split("|")]
            rows_buf.append(cells)
            continue
        if rows_buf:
            _auto_table(doc, rows_buf, font, primary, band)
            rows_buf = []
        para = para.strip()
        if not para:
            doc.add_paragraph()
            continue
        if para == "[pagebreak]" or para.startswith("[pagebreak] "):
            doc.add_page_break()
            para = para.replace("[pagebreak]", "", 1).strip()
            if not para:
                continue
        if para.startswith("### "):
            _head(para[4:], 3)
        elif para.startswith("## "):
            _head(para[3:], 2)
        elif para.startswith("# "):
            _head(para[2:], 1)
        elif para.startswith(("> ", ">　")):
            q = doc.add_paragraph()
            qr = q.add_run(para[2:].strip())
            _set_font(qr, font, body_size, color=ink)
            _shade_para(q, band)
            _para_border_left(q, accent)
            q.paragraph_format.left_indent = Pt(14)
        elif para.startswith("[toc]"):
            _add_toc(doc)
        elif para.startswith("- "):
            try:
                li = doc.add_paragraph(style="List Bullet")
                li.style = doc.styles["List Bullet"]
            except (KeyError, ValueError):
                li = doc.add_paragraph()
                para = "•  " + para[2:]
            lr = li.add_run(para[2:] if li.style.name == "List Bullet" else para)
            _set_font(lr, font, body_size, color=ink)
        elif len(para) > 2 and para[0].isdigit() and para[1] == "." and para[2] == " ":
            try:
                li = doc.add_paragraph(style="List Number")
                li.style = doc.styles["List Number"]
            except (KeyError, ValueError):
                li = doc.add_paragraph()
            lr = li.add_run(para[3:])
            _set_font(lr, font, body_size, color=ink)
        else:
            body = doc.add_paragraph()
            br = body.add_run(para)
            _set_font(br, font, body_size, color=ink)
            pf = body.paragraph_format
            pf.space_after = Pt(6)
            pf.line_spacing_rule = WD_LINE_SPACING.MULTIPLE
            pf.line_spacing = ls
            if align != WD_ALIGN_PARAGRAPH.LEFT:
                body.alignment = align
    if rows_buf:
        _auto_table(doc, rows_buf, font, primary, band)

    # 艺术字（大号加粗彩色文字，可作章节过渡页强调）
    missing = []
    for wa in (wordart or []):
        if not isinstance(wa, dict) or not str(wa.get("text") or "").strip():
            continue
        wp = doc.add_paragraph()
        wp.alignment = _ALIGN.get(str(wa.get("align") or "center").lower(),
                                  WD_ALIGN_PARAGRAPH.CENTER)
        wr = wp.add_run(str(wa["text"]))
        _set_font(wr, font, theme.to_int(wa.get("size"), 30, 8, 80), bold=True,
                  color=theme.hex_color(wa.get("color"), accent))
        wp.paragraph_format.space_before = Pt(12)
    # 图片
    for item in (images or []):
        if not _add_image(doc, item, font, workdir, soft):
            if isinstance(item, dict):
                missing.append(str(item.get("path") or ""))
            elif isinstance(item, str):
                missing.append(item)

    out = utils.resolve_path(path, workdir)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))
    msg = f"已生成 Word 文档：{out}"
    if missing:
        msg += f"（{len(missing)} 张图片不存在已跳过）"
    return {"text": msg, "images": []}
