# -*- coding: utf-8 -*-
"""office.compat —— 无 WebEngine 环境下的「降级保样式」渲染。

为什么需要：保真预览依赖 QtWebEngine（Qt 的浏览器内核）。当环境无法初始化
WebEngine（例如在受限沙箱/无 GPU 环境启动、或未安装 PyQt6-WebEngine）时，
预览只能退到 QTextBrowser。若此时仍按纯文本渲染，用户就完全看不到文档样式
（历史问题：Excel 表头底纹、字色、边框全部丢失，界面只剩文字）。

本模块用 QTextDocument 支持的 HTML 子集（表格 + 内联 color/background-color/
font-size/font-weight/text-align/border）重新渲染，保留能保留的样式。

边界（如实说明，不假装是保真预览）：
- 保留：Excel 表头底纹/字色/字重/字号/边框/数字格式/多表；Word 标题层级/字色/
  字号/粗斜体/表格；PPT 每页各形状的文字、字号、颜色、位置尺寸、表格；PDF 分页文本。
- 不保留：绝对定位版式、页面背景块、动画、图表图形（这些必须有浏览器引擎）。
"""

import os

from .preview import THEME_DARK, _esc, _fmt_number, _hexc
from .reader import ext_of

# ---------------------------------------------------------------------------
# 降级渲染（无 WebEngine 环境）：QTextDocument 友好的保样式 HTML
# ---------------------------------------------------------------------------
# QTextDocument（QTextBrowser）只支持 HTML/CSS 的一个子集：表格、内联
# color/background-color/font-size/font-weight/font-style/text-align/border 可用；
# flex / 绝对定位 / 动画 / 外部 CSS 类不可用。本函数据此产出「尽量保留样式」的
# 降级页面，避免 WebEngine 不可用时只剩纯文本（历史上就是这样丢样式的）。
_COMPAT_FONT_PX = {"pt": 4.0 / 3.0}      # pt -> px（96dpi）


def _px(pt_value, default=12.0):
    """pt → px（QTextDocument 的字号按 px 处理更稳定）"""
    try:
        return round(float(pt_value) * _COMPAT_FONT_PX["pt"], 1)
    except (TypeError, ValueError):
        return default


def _compat_wrap(inner: str, theme: dict) -> str:
    return ("<div style=\"font-family:'Microsoft YaHei';font-size:13px;color:%s\">"
            % theme.get("text", "#1A1A1A")) + inner + "</div>"


def _compat_xlsx(path: str, theme: dict) -> str:
    """Excel 降级渲染：多表 + 表头底纹 + 字色/字重/字号 + 边框 + 数字格式"""
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter

    wb = load_workbook(path, data_only=False)
    out = []
    for ws in wb.worksheets:
        out.append(f"<p style='font-size:14px;font-weight:bold;margin:10px 0 4px'>"
                   f"工作表：{_esc(ws.title)}</p>")
        rows = []
        max_r = min(ws.max_row or 0, 300)
        max_c = min(ws.max_column or 0, 40)
        for r in range(1, max_r + 1):
            tds = []
            for c in range(1, max_c + 1):
                cell = ws.cell(row=r, column=c)
                css = ["padding:2px 6px", "border:1px solid #BFBFBF"]
                f = cell.font
                if f is not None:
                    if f.bold:
                        css.append("font-weight:bold")
                    if f.italic:
                        css.append("font-style:italic")
                    if f.size:
                        css.append("font-size:%spx" % _px(f.size, 12))
                    col = _hexc(getattr(f.color, "rgb", ""))
                    if col:
                        css.append("color:#" + col)
                fill = cell.fill
                if fill is not None and fill.fill_type == "solid":
                    fhex = _hexc(getattr(fill.start_color, "rgb", ""))
                    if fhex:
                        css.append("background-color:#" + fhex)
                al = getattr(cell.alignment, "horizontal", None)
                if al in ("left", "center", "right"):
                    css.append("text-align:" + al)
                v = cell.value
                if isinstance(v, str) and v.startswith("="):
                    txt = v
                else:
                    txt = _fmt_number(v, "" if v is None else str(cell.number_format or ""))
                tds.append('<td style="%s">%s</td>' % (";".join(css), _esc(txt)))
            rows.append("<tr>" + "".join(tds) + "</tr>")
        out.append('<table border="0" cellspacing="0" cellpadding="0" width="100%">'
                   + "".join(rows) + "</table>")
    return _compat_wrap("".join(out), theme)


def _compat_docx(path: str, theme: dict) -> str:
    """Word 降级渲染：标题层级 + 字色/字号/粗斜体 + 表格边框底纹"""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document(path)
    align_map = {WD_ALIGN_PARAGRAPH.CENTER: "center",
                 WD_ALIGN_PARAGRAPH.RIGHT: "right",
                 WD_ALIGN_PARAGRAPH.JUSTIFY: "justify"}
    out = []
    for p in doc.paragraphs:
        style = (p.style.name if p.style is not None else "") or ""
        spans = []
        for run in p.runs:
            if not run.text:
                continue
            css = []
            if run.bold:
                css.append("font-weight:bold")
            if run.italic:
                css.append("font-style:italic")
            if run.font.size is not None:
                css.append("font-size:%spx" % _px(run.font.size.pt, 12))
            col = ""
            try:
                if run.font.color is not None and run.font.color.rgb:
                    col = _hexc(str(run.font.color.rgb))
            except Exception:
                col = ""
            if col:
                css.append("color:#" + col)
            spans.append('<span style="%s">%s</span>' % (";".join(css), _esc(run.text)))
        text = "".join(spans)
        if not text.strip():
            out.append("<p>&nbsp;</p>")
            continue
        psize = 20 if style.startswith(("Title", "Heading1")) else (
            16 if style.startswith("Heading") else 13)
        head = "font-weight:bold;" if style.startswith(("Title", "Heading")) else ""
        al = align_map.get(p.alignment)
        style_css = "%sfont-size:%spx" % (head, psize) + ((";text-align:" + al) if al else "")
        out.append('<p style="margin:4px 0;%s">%s</p>' % (style_css, text))
    for tbl in doc.tables:
        rows = []
        for row in tbl.rows:
            tds = []
            for cell in row.cells:
                txt = " ".join(cell.text.split())
                tds.append('<td style="border:1px solid #BFBFBF;padding:3px 6px">%s</td>'
                           % _esc(txt))
            rows.append("<tr>" + "".join(tds) + "</tr>")
        out.append('<table border="0" cellspacing="0" cellpadding="0" width="100%">'
                   + "".join(rows) + "</table>")
    return _compat_wrap("".join(out), theme)


def _compat_pptx(path: str, theme: dict) -> str:
    """PPT 降级渲染：逐页列出形状（类型/位置/字号/颜色/文本），保留配色可读性。

    说明：QTextDocument 不支持绝对定位，故不做版式还原（那需要浏览器引擎）；
    但每页形状的文字、字号、颜色、位置尺寸都会给出，比纯文本更可用。
    """
    from pptx import Presentation

    prs = Presentation(path)
    out = []
    for si, slide in enumerate(prs.slides, 1):
        bg = ""
        try:
            if slide.background.fill.type is not None and slide.background.fill.type == 1:
                bg = _hexc(str(slide.background.fill.fore_color.rgb))
        except Exception:
            bg = ""
        box = 'background-color:#%s;' % bg if bg else ""
        parts = ['<p style="margin:6px 0 2px;font-weight:bold">第 %d 页</p>' % si]
        for shape in slide.shapes:
            x = round((shape.left or 0) / 914400, 2)
            y = round((shape.top or 0) / 914400, 2)
            w = round((shape.width or 0) / 914400, 2)
            h = round((shape.height or 0) / 914400, 2)
            head = ('<p style="margin:2px 0;color:%s;font-size:11px">'
                    '[%s inch]</p>' % (theme.get("dim", "#808080"), f"{x},{y} · {w}×{h}"))
            if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    for run in para.runs:
                        if not (run.text or "").strip():
                            continue
                        css = []
                        if run.font.size is not None:
                            css.append("font-size:%spx" % _px(run.font.size.pt, 18))
                        if run.font.bold:
                            css.append("font-weight:bold")
                        col = ""
                        try:
                            if run.font.color is not None and run.font.color.type is not None:
                                col = _hexc(str(run.font.color.rgb))
                        except Exception:
                            col = ""
                        if col:
                            css.append("color:#" + col)
                        parts.append('<p style="margin:1px 0 1px 12px;%s">%s%s</p>'
                                     % (";".join(css), _esc(run.text), head or ""))
                        head = ""
                if head:
                    parts.append(head)
            elif getattr(shape, "has_table", False) and shape.has_table:
                rows = []
                for r in shape.table.rows:
                    tds = "".join('<td style="border:1px solid #BFBFBF;padding:2px 5px">%s</td>'
                                  % _esc(c.text) for c in r.cells)
                    rows.append("<tr>" + tds + "</tr>")
                parts.append('<table border="0" cellspacing="0" cellpadding="0" width="100%">'
                             + "".join(rows) + "</table>")
            else:
                parts.append(head)
        out.append('<div style="%smargin:0 0 10px;padding:6px 8px">%s</div>'
                   % (box, "".join(parts)))
    return _compat_wrap("".join(out), theme)


def _compat_pdf(path: str, theme: dict) -> str:
    """PDF 降级渲染：逐页文本（分页标注），表格行保持列对齐"""
    from .reader import read_pdf
    text = str((read_pdf(path) or {}).get("text") or "")
    out, cur = [], None
    for line in text.splitlines():
        if line.startswith("=== 第") and line.endswith("==="):
            if cur is not None:
                out.append("<p>%s</p>" % _esc(cur))
            cur = line.strip("= ")
            out.append('<p style="margin:8px 0 2px;font-weight:bold">%s</p>' % _esc(cur))
            continue
        if line.startswith("<!--") or line.startswith("元数据:"):
            if line.startswith("元数据:"):
                out.append('<p style="color:%s;font-size:11px">%s</p>'
                           % (theme.get("dim", "#808080"), _esc(line)))
            continue
        out.append("<p style='margin:1px 0'>%s</p>" % _esc(line))
    return _compat_wrap("".join(out), theme)


_COMPAT_RENDERERS = {"docx": _compat_docx, "docm": _compat_docx,
                     "pptx": _compat_pptx, "pptm": _compat_pptx,
                     "xlsx": _compat_xlsx, "xlsm": _compat_xlsx,
                     "pdf": _compat_pdf}


def render_office_compat_html(path: str, ext: str = "", theme=None) -> str:
    """降级渲染：给没有 WebEngine 的环境也保留颜色/字体/底纹/边框（不返回 None）。

    与 render_office_html 的区别：只使用 QTextDocument 支持的 HTML 子集，
    因此**不含绝对定位版式与动画**（那必须有浏览器引擎）；但 Excel 表头底纹、
    字色、边框、数字格式，Word 标题层级/字色/表格，PPT 各形状文字与配色信息
    都能保留 —— 兜底可用远优于纯文本。
    """
    if not path or not os.path.isfile(path):
        return ""
    e = (ext or ext_of(path)).lstrip(".").lower()
    fn = _COMPAT_RENDERERS.get(e)
    if fn is None:
        return ""
    t = dict(THEME_DARK)
    if isinstance(theme, dict):
        t.update({k: v for k, v in theme.items() if k in t and v})
    try:
        return fn(path, t)
    except Exception as e2:
        return ('<p style="color:#9A9A9A">简化渲染不可用：%s</p>' % _esc(e2))
