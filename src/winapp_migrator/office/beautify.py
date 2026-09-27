# -*- coding: utf-8 -*-
"""已有三件套文档的一键精修（beautify_*）。

定位：agent 生成或用户已有文档"不够好看"时的快速出路 —— 就地统一字体/配色/行距/底纹，
不改动内容文本。全部参数（theme/字体/字号/底色）来自 style，复用 office theme 保证与
新生成文档视觉一致。为保险起见美化前请先复制副本（调用方可自行处理）。
"""

from . import theme, utils


def _docx_rfonts(run, qn):
    try:
        rpr = run._element.get_or_add_rPr()
        rf = rpr.rFonts
        if rf is not None:
            rf.set(qn("w:eastAsia"), run.font.name)
    except Exception:
        pass


def beautify_docx(path, style=None, workdir=""):
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor

    t = utils.resolve_style(style)
    s = style or {}
    body_size = theme.to_int(s.get("body_size"), 12, 6, 40)
    line_spacing = theme.to_float(s.get("line_spacing"), 1.5, 1.0, 3.0)
    align = str(s.get("align") or "justify").lower()
    align_map = {"center": WD_ALIGN_PARAGRAPH.CENTER, "right": WD_ALIGN_PARAGRAPH.RIGHT,
                 "justify": WD_ALIGN_PARAGRAPH.JUSTIFY}
    body_align = align_map.get(align, WD_ALIGN_PARAGRAPH.JUSTIFY)

    p = utils.resolve_path(path, workdir)
    if not p.is_file():
        raise ValueError(f"[beautify_docx] 文件不存在: {p}")
    doc = Document(str(p))
    # 统一样式表（Normal + Heading1-4 上主色）
    styles = doc.styles
    try:
        normal = styles["Normal"]
        normal.font.name = t["font"]
        normal.font.size = Pt(body_size)
        normal.font.color.rgb = RGBColor.from_string(theme.hex_color(t["ink"], "404040"))
        _docx_rfonts(normal.element, qn)
    except Exception:
        pass
    for n in range(1, 5):
        try:
            st = styles[f"Heading {n}"]
            st.font.color.rgb = RGBColor.from_string(theme.hex_color(t["primary"]))
            st.font.name = t["font"]
            try:
                st.element.rPr.rFonts.set(qn("w:eastAsia"), t["font"])
            except Exception:
                pass
        except Exception:
            continue
    # 逐段覆盖（应对未用样式的裸段落）
    first_text = next((par for par in doc.paragraphs if par.text.strip()), None)
    for idx, para in enumerate(doc.paragraphs):
        is_head = bool(para.style and para.style.name
                       and para.style.name.lower().startswith("heading"))
        is_first = para is first_text
        if is_head or is_first:
            try:
                para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            except Exception:
                pass
        elif para.text.strip():
            if body_align != WD_ALIGN_PARAGRAPH.LEFT:
                para.alignment = body_align
            try:
                para.paragraph_format.line_spacing = line_spacing
                para.paragraph_format.space_after = Pt(6)
            except Exception:
                pass
        for run in para.runs:
            run.font.name = t["font"]
            _docx_rfonts(run, qn)
            try:
                if is_head or is_first:
                    run.font.size = Pt(body_size + 6 if is_head else body_size + 12)
                    run.font.bold = True
                    run.font.color.rgb = RGBColor.from_string(theme.hex_color(t["primary"]))
                else:
                    run.font.size = Pt(body_size)
                    run.font.color.rgb = RGBColor.from_string(theme.hex_color(t["ink"], "404040"))
            except Exception:
                pass
    doc.save(str(p))
    return {"text": f"已美化 Word 文档：{p}（主色 {t['primary']}，字体 {t['font']}）",
            "images": []}


def beautify_pptx(path, style=None, workdir=""):
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.util import Pt

    t = utils.resolve_style(style)
    s = style or {}
    body_size = theme.to_int(s.get("body_size"), 18, 8, 60)
    dark_theme = theme.hex_color(s.get("bg_color"), "FFFFFF")
    if str(s.get("bg_color") or "").strip():
        bg = theme.hex_color(s.get("bg_color"))
    else:
        bg = theme.hex_color(theme.lighten(t["primary"], 0.94)) if t["theme_name"] != "dark" \
            else t["bg"]
    p = utils.resolve_path(path, workdir)
    if not p.is_file():
        raise ValueError(f"[beautify_pptx] 文件不存在: {p}")
    prs = Presentation(str(p))
    for slide in prs.slides:
        try:
            slide.background.fill.solid()
            slide.background.fill.fore_color.rgb = RGBColor.from_string(theme.hex_color(bg))
        except Exception:
            pass
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            tf = shape.text_frame
            for para in tf.paragraphs:
                for run in para.runs:
                    try:
                        run.font.name = t["font"]
                    except Exception:
                        pass
                    try:
                        if para is tf.paragraphs[0] and len(tf.paragraphs) == 1:
                            run.font.size = Pt(body_size + 8)
                            run.font.bold = True
                            run.font.color.rgb = RGBColor.from_string(theme.hex_color(t["primary"]))
                        else:
                            run.font.size = Pt(body_size)
                            run.font.color.rgb = RGBColor.from_string(theme.hex_color(t["ink"], "404040"))
                    except Exception:
                        pass
    prs.save(str(p))
    return {"text": f"已美化 PPT 文稿：{p}（主色 {t['primary']}，底色统一 {bg}）", "images": []}


def beautify_xlsx(path, style=None, workdir=""):
    from openpyxl import load_workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    t = utils.resolve_style(style)
    s = style or {}
    header_fill = theme.hex_color(s.get("header_fill"), t["primary"])
    header_color = theme.hex_color(s.get("header_color"), "FFFFFF")
    font_name = str(s.get("font_name") or t["font"])
    banded = utils.to_flag(s.get("banded"), True)
    band = theme.hex_color(s.get("band_fill"), theme.lighten(header_fill, 0.9))
    freeze_header = utils.to_flag(s.get("freeze_header"), True)
    auto_filter = utils.to_flag(s.get("auto_filter"), True)
    border_color = theme.hex_color(s.get("border_color"), "D6DCE5")
    header_size = theme.to_int(s.get("header_size"), 11, 6, 24)
    body_size = theme.to_int(s.get("body_size"), 10, 6, 24)
    p = utils.resolve_path(path, workdir)
    if not p.is_file():
        raise ValueError(f"[beautify_xlsx] 文件不存在: {p}")
    wb = load_workbook(str(p))
    thin = Side(style="thin", color=border_color)
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for ws in wb.worksheets:
        if ws.max_row < 1 or ws.max_column < 1:
            continue
        for c in ws[1]:
            if c.value is not None:
                c.font = Font(name=font_name, size=header_size, bold=True, color=header_color)
                c.fill = PatternFill("solid", fgColor=header_fill)
                c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                c.border = border
        ws.row_dimensions[1].height = 22
        for r_idx, row in enumerate(ws.iter_rows(min_row=2), 2):
            for c in row:
                if c.value is None:
                    c.border = border
                    continue
                c.font = Font(name=font_name, size=body_size, color="404040")
                c.border = border
                c.alignment = Alignment(
                    horizontal="right" if isinstance(c.value, (int, float)) else "left",
                    vertical="center")
                if banded and r_idx % 2 == 0:
                    c.fill = PatternFill("solid", fgColor=band)
        for ci in range(1, ws.max_column + 1):
            texts = []
            for row in ws.iter_rows(min_col=ci, max_col=ci):
                v = row[0].value
                if v is not None:
                    texts.append(str(v))
                if len(texts) >= 50:
                    break
            mx = 8.0
            for txt in texts:
                width = 0.0
                for ch in txt:
                    width += 2.0 if ord(ch) > 127 else 1.0
                mx = max(mx, width + 3.0)
            ws.column_dimensions[get_column_letter(ci)].width = min(42.0, mx)
        if freeze_header:
            ws.freeze_panes = "A2"
        if auto_filter and ws.dimensions != "A1":
            ws.auto_filter.ref = ws.dimensions
    wb.save(str(p))
    return {"text": f"已美化 Excel 工作簿：{p}（表头色 {t['primary']}，字体 {t['font']}）",
            "images": []}
