# -*- coding: utf-8 -*-
"""Excel 高质量生成器（openpyxl）。

相对旧 create_xlsx 的能力增量：
- 主题统一：表头/隔行/边框/工作表签颜色全部取 office theme（Word/PPT/Excel 一致）；
- 单元格值支持 {v, format} 携带数字格式（金额千分位/百分比/小数位等）；
- 合计行：数据末行以 "~sum" 标记该列求和（生成真实 SUM 公式），首列可写"合计"文本；
- 原生图表：sheet.charts = [{type: bar/column/line/pie, title, labels, values}]，
  图表基于真实单元格数据（Excel 内可点选编辑），主题色着色；
- 列宽可选覆盖 column_widths=[...]，中文感知估算；
- 保留：wordart 大标题行、image 插图、隔行/边框/冻结/筛选。

sheet 字段：name, rows, wordart, image, charts(可选), column_widths(可选)。
style 字段：theme/theme_color/header_fill/header_color/font_name/banded/band_fill/
freeze_header/auto_filter/border_color/header_size/body_size/header_bold。
"""

from pathlib import Path

from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, PieChart, Reference
from openpyxl.drawing.image import Image as XlImage
from openpyxl.formatting.rule import ColorScaleRule, DataBarRule, FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from . import theme, utils

_FILL_CACHE = {}


def _fill(hexc):
    key = theme.hex_color(hexc)
    if key not in _FILL_CACHE:
        _FILL_CACHE[key] = PatternFill("solid", fgColor=key)
    return _FILL_CACHE[key]


def _text_width(s: str) -> float:
    """中文感知列宽估算（CJK 双倍）。"""
    w = 0.0
    for ch in str(s):
        w += 2.0 if ord(ch) > 127 else 1.0
    return w


def _auto_width(texts, header=""):
    mx = _text_width(header)
    for t in texts[:80]:
        mx = max(mx, _text_width(t))
    return max(8.0, min(42.0, mx + 3.0))


def _write_value(ws, row, col, v, font, border):
    value, fmt = utils.cell_value(v)
    c = ws.cell(row=row, column=col, value=value)
    c.border = border
    c.font = font
    if fmt:
        c.number_format = fmt
    if isinstance(value, (int, float)):
        c.alignment = Alignment(horizontal="right", vertical="center")
    else:
        c.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
    return c


def _apply_format(ws, fmt, hdr_row, last_row, tokens):
    """sheet.format 条件格式：data_bars=[列...] / highlight={max:[列],min:[列]} / color_scale=[列]。

    列号为 1 起始的数据列号；仅在数据行区（表头下一行 .. 末行）生效。
    """
    if not isinstance(fmt, dict) or last_row <= hdr_row:
        return
    first = hdr_row + 1
    data_bars = fmt.get("data_bars") or []
    highlight = fmt.get("highlight") if isinstance(fmt.get("highlight"), dict) else {}
    color_scale = fmt.get("color_scale") or []
    bar_color = theme.hex_color(tokens["chart"][0], "638EC6")
    for col in (data_bars or []):
        try:
            ci = int(col)
        except (TypeError, ValueError):
            continue
        rng = f"{get_column_letter(ci)}{first}:{get_column_letter(ci)}{last_row}"
        ws.conditional_formatting.add(rng, DataBarRule(
            start_type="min", end_type="max", color=bar_color, showValue=True))
    for ci in (color_scale or []):
        try:
            ci = int(ci)
        except (TypeError, ValueError):
            continue
        rng = f"{get_column_letter(ci)}{first}:{get_column_letter(ci)}{last_row}"
        ws.conditional_formatting.add(rng, ColorScaleRule(
            start_type="min", start_color="F8696B",
            mid_type="percentile", mid_value=50, mid_color="FFEB84",
            end_type="max", end_color="63BE7B"))
    hmax = highlight.get("max") or []
    hmin = highlight.get("min") or []
    for ci in hmax:
        try:
            ci = int(ci)
        except (TypeError, ValueError):
            continue
        L = get_column_letter(ci)
        rng = f"{L}{first}:{L}{last_row}"
        ws.conditional_formatting.add(rng, FormulaRule(
            formula=[f"AND(ISNUMBER({L}{first}),{L}{first}=MAX(${L}${first}:${L}${last_row}))"],
            fill=_fill("FFE08A")))
    for ci in hmin:
        try:
            ci = int(ci)
        except (TypeError, ValueError):
            continue
        L = get_column_letter(ci)
        rng = f"{L}{first}:{L}{last_row}"
        ws.conditional_formatting.add(rng, FormulaRule(
            formula=[f"AND(ISNUMBER({L}{first}),{L}{first}=MIN(${L}${first}:${L}${last_row}))"],
            fill=_fill("B7E1CD")))


def _append_chart(ws, sheet, spec, tokens, ncols, header_fill, header_font,
                  body_size, font_name, last_data_row, first_data_row):
    """把 chart 数据写入表尾空闲列，再生成 openpyxl 原生图表。"""
    if not isinstance(spec, dict):
        return
    ctype = str(spec.get("type") or "column").lower()
    labels = [str(x) for x in (spec.get("labels") or [])]
    raw = spec.get("values") or []
    values = []
    for v in raw:
        try:
            if not isinstance(v, bool):
                values.append(float(v))
        except (TypeError, ValueError):
            continue
    n = min(len(labels), len(values))
    if n == 0:
        return
    labels, values = labels[:n], values[:n]
    # 数据区放表尾左侧 2 列（避免与表格内容冲突），区块内带小标题
    base_col = ncols + 2
    title = str(spec.get("title") or "数据")
    c = ws.cell(row=1, column=base_col, value=title)
    c.font = Font(name=font_name, size=11, bold=True, color=header_fill)
    c2 = ws.cell(row=1, column=base_col + 1, value="数值")
    c2.font = Font(name=font_name, size=10, bold=True, color=header_fill)
    for i in range(n):
        lc = ws.cell(row=i + 2, column=base_col, value=labels[i])
        lc.font = Font(name=font_name, size=10)
        vc = ws.cell(row=i + 2, column=base_col + 1, value=values[i])
        vc.font = Font(name=font_name, size=10)
        vc.number_format = "#,##0.##"
    nrows = n
    data = Reference(ws, min_col=base_col + 1, min_row=1, max_row=1 + nrows)
    cats = Reference(ws, min_col=base_col, min_row=2, max_row=1 + nrows)
    primary = tokens["primary"]
    if ctype == "pie":
        chart = PieChart()
        chart.title = title
        chart.add_data(data, titles_from_data=True)
        chart.set_categories(cats)
    elif ctype == "line":
        chart = LineChart()
        chart.title = title
        chart.add_data(data, titles_from_data=True)
        chart.set_categories(cats)
        if chart.series:
            chart.series[0].graphicalProperties.line.solidFill = primary
            chart.series[0].graphicalProperties.line.width = 22000
            chart.series[0].smooth = False
    elif ctype == "bar":
        chart = BarChart()
        chart.type = "bar"
        chart.title = title
        chart.add_data(data, titles_from_data=True)
        chart.set_categories(cats)
        if chart.series:
            chart.series[0].graphicalProperties.solidFill = primary
    else:
        chart = BarChart()
        chart.type = "col"
        chart.title = title
        chart.add_data(data, titles_from_data=True)
        chart.set_categories(cats)
        if chart.series:
            chart.series[0].graphicalProperties.solidFill = primary
    chart.height = 7.5
    chart.width = 13
    chart.legend.position = "r"
    chart.legend.includeInLayout = False
    anchor_row = max(3, last_data_row + 3)
    ws.add_chart(chart, f"{get_column_letter(base_col - 2)}{anchor_row}")


def build_xlsx(path, sheets=None, style=None, workdir="") -> dict:
    t = utils.resolve_style(style)
    s = style or {}
    primary = theme.hex_color(s.get("theme_color") or s.get("base_color"), t["primary"])
    header_fill = theme.hex_color(s.get("header_fill"), primary)
    header_color = theme.hex_color(s.get("header_color"), "FFFFFF")
    font_name = str(s.get("font_name") or t["font"])
    banded = utils.to_flag(s.get("banded"), True)
    band = theme.hex_color(s.get("band_fill"), t["band"])
    freeze_header = utils.to_flag(s.get("freeze_header"), True)
    auto_filter = utils.to_flag(s.get("auto_filter"), True)
    border_color = theme.hex_color(s.get("border_color"), "D6DCE5")
    header_size = theme.to_int(s.get("header_size"), 11, 6, 24)
    body_size = theme.to_int(s.get("body_size"), 10, 6, 24)
    header_bold = utils.to_flag(s.get("header_bold"), True)

    wb = Workbook()
    wb.remove(wb.active)
    thin = Side(style="thin", color=border_color)
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    missing = []
    for sheet in (sheets or []):
        if not isinstance(sheet, dict):
            continue
        ws = wb.create_sheet(title=str(sheet.get("name") or "Sheet")[:31])
        ws.sheet_properties.tabColor = header_fill
        wa = sheet.get("wordart") or {}
        title_row = str(wa.get("text") or "").strip() if isinstance(wa, dict) else ""
        rows = [r for r in (sheet.get("rows") or []) if isinstance(r, (list, tuple))]
        # 写入数据行（含 ~sum 合计行先占位）
        data_rows = list(rows)
        for row in data_rows:
            ws.append([utils.cell_value(v)[0] for v in row])
        if not data_rows and not title_row:
            continue
        offset = 1 if title_row else 0
        if title_row:
            ncols_hdr = max((len(r) for r in data_rows), default=1) or 1
            ws.insert_rows(1)
            a1 = ws.cell(row=1, column=1, value=title_row)
            if ncols_hdr > 1:
                ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncols_hdr)
            a1.font = Font(name=font_name, size=theme.to_int(wa.get("size"), 15, 8, 40),
                           bold=True, color=theme.hex_color(wa.get("color"), primary))
            a1.alignment = Alignment(horizontal="center", vertical="center")
            ws.row_dimensions[1].height = max(24, theme.to_int(wa.get("size"), 15, 8, 40) + 9)
        hdr = offset + 1
        ncols = max((len(r) for r in data_rows), default=0)
        nrows_data = len(data_rows)
        # 表头样式
        header_font = Font(name=font_name, size=header_size, bold=header_bold,
                           color=header_color)
        for c in ws[hdr]:
            c.font = header_font
            c.fill = _fill(header_fill)
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            c.border = border
        ws.row_dimensions[hdr].height = 22
        # 合计行替换为 SUM 公式（值单元格含 ~sum 标记）
        sum_rows = []
        body_font = Font(name=font_name, size=body_size, color="404040")
        for r_idx, row in enumerate(ws.iter_rows(min_row=hdr + 1, max_row=hdr + nrows_data),
                                    hdr + 1):
            mark = False
            for c in row:
                if c.value == "~sum":
                    mark = True
                    break
            if mark:
                sum_rows.append(r_idx)
            for c in row:
                if c.value is None and not mark:
                    c.border = border
                    continue
                if c.value != "~sum":
                    c.font = body_font
                    c.border = border
                    c.alignment = Alignment(
                        horizontal="right" if isinstance(c.value, (int, float)) else "left",
                        vertical="center")
                    if banded and (r_idx - offset) % 2 == 0 and not mark:
                        c.fill = _fill(band)
        for r_idx in sum_rows:
            for cn in range(1, ncols + 1):
                c = ws.cell(row=r_idx, column=cn)
                if str(c.value or "").strip() == "~sum":
                    col_l = get_column_letter(cn)
                    c.value = f"=SUM({col_l}{hdr + 1}:{col_l}{r_idx - 1})"
                    c.font = Font(name=font_name, size=body_size, bold=True, color="404040")
                    c.number_format = "#,##0.##"
                    c.border = border
                    c.alignment = Alignment(horizontal="right", vertical="center")
                    if banded:
                        c.fill = _fill(band)
                elif r_idx in sum_rows and c.value is None:
                    c.border = border
        # 列宽：优先 column_widths 显式覆盖
        cw = sheet.get("column_widths")
        if isinstance(cw, (list, tuple)) and cw:
            for ci in range(ncols):
                try:
                    ws.column_dimensions[get_column_letter(ci + 1)].width = max(
                        6.0, min(60.0, float(cw[ci]) if ci < len(cw) else 10.0))
                except (TypeError, ValueError):
                    pass
        else:
            texts_all = [list(r) for r in data_rows]
            for ci in range(ncols):
                texts = [r[ci] if ci < len(r) else "" for r in texts_all]
                header_txt = str(data_rows[0][ci]) if data_rows and ci < len(data_rows[0]) else ""
                ws.column_dimensions[get_column_letter(ci + 1)].width = _auto_width(
                    texts[1:], header_txt)
        if freeze_header and nrows_data:
            ws.freeze_panes = f"A{hdr + 1}"
        if auto_filter and nrows_data and ncols:
            ws.auto_filter.ref = f"{get_column_letter(1)}{hdr}:{get_column_letter(ncols)}{hdr + nrows_data}"
        # 条件格式（数据条/色阶/最大最小高亮）
        _apply_format(ws, sheet.get("format"), hdr, hdr + nrows_data, t)
        # 图表
        last_row = hdr + nrows_data
        for spec in (sheet.get("charts") or []):
            try:
                _append_chart(ws, sheet, spec, t, ncols, header_fill, header_font,
                              body_size, font_name, last_row, hdr + 1)
            except Exception:
                continue
        # 插图
        img = sheet.get("image") or ""
        if img:
            img_src = img if isinstance(img, str) else str(img.get("path") or "")
            try:
                iwidth = 800.0 if isinstance(img, str) else float(img.get("width") or 800.0)
            except (TypeError, ValueError):
                iwidth = 800.0
            try:
                img_p = utils.resolve_path(img_src, workdir)
            except ValueError:
                img_p = Path(img_src)
            if not img_p.is_file():
                missing.append(img_src)
            else:
                xl = XlImage(str(img_p))
                scale, w, h = utils.fit_scale(str(img_p), max(iwidth, 1.0), 500)
                xl.width = int(w * scale)
                xl.height = int(h * scale)
                ws.add_image(xl, f"A{last_row + 2}")
    out = utils.resolve_path(path, workdir)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(out))
    msg = f"已生成 Excel 工作簿：{out}"
    if missing:
        msg += f"（{len(missing)} 张图片不存在已跳过）"
    return {"text": msg, "images": []}
