# -*- coding: utf-8 -*-
"""office.reader —— Word(.docx) / PPT(.pptx) / Excel(.xlsx) / PDF 的真实读取。

职责边界：
- read_document：统一入口，按扩展名分派，返回 {"text", "images", "meta"}
  与 core.agent_tools 既有 handler 返回结构一致（上层可原样回给模型）。
- read_docx / read_pptx / read_xlsx / read_pdf：各格式的结构化读取。
- 输出为「结构化 Markdown」：保留标题层级、粗斜体、表格、公式、数字格式、
  形状类型与位置尺寸（英寸）、动画条目、图表类型与系列 —— 便于模型据此
  定位锚点后调用 office.editor.edit_document 做精确修改（读写闭环）。

设计要点：
- 三方库（python-docx / python-pptx / openpyxl / pypdf）惰性导入：缺失时给出
  明确可执行的提示（如 pip install pypdf），不 mock、不抛裸异常。
- 图片一律只标注「[图片 #n ...]」占位，**不内联 base64**，避免污染模型上下文；
  需要图片本体做保真预览时走 office.preview（模块内部直读 media 部件）。
- 扩展点：新增格式只需实现 read_xxx 并登记到 _READERS。

读取一律只读，不修改文件；本模块不 import PyQt，可脱离 GUI 运行。
"""

import os
import re
import zipfile
import xml.etree.ElementTree as ET

# ---------------------------------------------------------------------------
# 常量（扩展点：新增支持格式在此登记即可被 read_document 分派）
# ---------------------------------------------------------------------------
READ_EXTS = frozenset({"docx", "docm", "pptx", "pptm", "xlsx", "xlsm", "pdf"})

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

# docx 内置样式名 → Markdown 级别（用于把 Word 标题层级还原为 # 层级）
_DOCX_HEADING_MD = {"Title": "#", "Heading1": "##", "Heading2": "###",
                    "Heading3": "####", "Heading4": "#####", "Heading5": "######"}

_MIME_BY_EXT = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "gif": "image/gif", "bmp": "image/bmp", "webp": "image/webp",
                "emf": "image/emf", "wmf": "image/wmf", "tif": "image/tiff",
                "tiff": "image/tiff", "svg": "image/svg+xml"}


def ext_of(path: str) -> str:
    """取小写扩展名（不含点）"""
    return os.path.splitext(str(path))[1].lstrip(".").lower()


def norm_hex(value, default: str = "") -> str:
    """颜色归一化为 6 位 HEX（不含 #），无法解析返回 default。

    必须兼容 8 位 ARGB：openpyxl 读回的字体色/填充色是 'RRGGBBAA' 之外的
    'AARRGGBB' 形式（如 '001F3864'），只认 6 位会把**所有 Excel 颜色丢光**
    （预览因此完全没有配色）。8 位一律剥掉前两位 alpha。
    """
    s = str(value or "").strip().lstrip("#").upper()
    if len(s) == 8:
        s = s[2:]
    if len(s) != 6:
        return default
    try:
        int(s, 16)
    except ValueError:
        return default
    return s


def _resolve(path: str, workdir: str = "") -> str:
    """解析文件路径：绝对路径原样；相对路径基于工作目录"""
    p = str(path or "").strip()
    if not p:
        raise ValueError("path 不能为空")
    if os.path.isabs(p):
        return p
    return os.path.join(workdir or os.getcwd(), p)


def _truncate(text: str, limit: int) -> str:
    """按字符数截断并给出明确提示（不静默丢内容）"""
    if limit and len(text) > limit:
        return text[:limit] + f"\n\n[... 内容过长已截断，共 {len(text)} 字符，上限 {limit}；可提高 max_chars 重读]"
    return text


# ---------------------------------------------------------------------------
# OpenXML 通用底层（供 reader / preview 共用，避免重复解析实现）
# ---------------------------------------------------------------------------
def zip_bytes(path: str, member: str):
    """从 OpenXML 包中读取指定成员字节；不存在返回 None"""
    try:
        with zipfile.ZipFile(path) as z:
            if member in z.namelist():
                return z.read(member)
    except (zipfile.BadZipFile, OSError):
        return None
    return None


def zip_names(path: str, prefix: str) -> list:
    """列出包内以 prefix 开头的成员名（按自然序排序，便于 slide2 < slide10）"""
    try:
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.startswith(prefix)]
    except (zipfile.BadZipFile, OSError):
        return []
    return sorted(names, key=lambda n: int(re.sub(r"\D", "", n.rsplit("/", 1)[-1]) or 0))


def xml_root(path: str, member: str):
    """读取并解析包内 XML 成员为 Element；失败返回 None"""
    data = zip_bytes(path, member)
    if not data:
        return None
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        return None


def elems(parent, local: str) -> list:
    """按 localname 取直接子元素（忽略命名空间差异）"""
    if parent is None:
        return []
    return [e for e in parent if isinstance(e.tag, str) and e.tag.rsplit("}", 1)[-1] == local]


def elem_text(parent, local: str) -> str:
    """取首个匹配后代的文本"""
    node = next(iter(elems(parent, local)), None)
    return (node.text or "") if node is not None else ""


def all_elems(root, local: str) -> list:
    """递归取所有 localname 匹配的元素（顺序即文档序）"""
    if root is None:
        return []
    return [e for e in root.iter() if isinstance(e.tag, str) and e.tag.rsplit("}", 1)[-1] == local]


def rels_map(path: str, part: str) -> dict:
    """读取 part 对应的 .rels，返回 {rId: 目标部件相对路径}（已解析为包内绝对路径）"""
    base = part.rsplit("/", 1)[0] if "/" in part else ""
    rels_part = f"{base}/_rels/{part.rsplit('/', 1)[-1]}.rels"
    root = xml_root(path, rels_part)
    out = {}
    for rel in all_elems(root, "Relationship"):
        rid = rel.get("Id") or ""
        target = rel.get("Target") or ""
        if not rid or not target or rel.get("TargetMode") == "External":
            continue
        if target.startswith("/"):
            out[rid] = target.lstrip("/")
        else:
            out[rid] = os.path.normpath(os.path.join(base, target)).replace("\\", "/")
    return out


def media_data_uri(path: str, member: str) -> str:
    """把包内图片成员转为 data: URI（供保真预览内联，避免相对路径失效）"""
    import base64
    data = zip_bytes(path, member)
    if not data:
        return ""
    suffix = member.rsplit(".", 1)[-1].lower()
    mime = _MIME_BY_EXT.get(suffix, "application/octet-stream")
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


# ---------------------------------------------------------------------------
# Word (.docx)
# ---------------------------------------------------------------------------
def _docx_run_md(run) -> str:
    """把 Word run 还原为带轻标记的文本（**粗** *斜* 下划线用 <u>）"""
    text = run.text or ""
    if not text.strip():
        return text
    if run.bold:
        text = f"**{text}**"
    if run.italic:
        text = f"*{text}*"
    if run.underline:
        text = f"<u>{text}</u>"
    return text


def _docx_para_md(p, out: list, indent: int = 0):
    """段落 → Markdown 行（含样式/对齐/缩进/字号标注）"""
    text = "".join(_docx_run_md(r) for r in p.runs)
    style = (p.style.name if p.style is not None else "") or ""
    pad = "  " * indent
    head = _DOCX_HEADING_MD.get(style.replace(" ", ""), "")
    # 字号/对齐等细节以行尾标注给出，供模型判断「这是标题/正文/强调」
    marks = []
    if p.alignment is not None:
        marks.append(f"align={str(p.alignment).split()[0].lower()}")
    sizes = {r.font.size.pt for r in p.runs if r.font.size is not None}
    if sizes:
        marks.append("size=" + "/".join(f"{s:g}pt" for s in sorted(sizes)))
    tail = f"  <!-- {' '.join(marks)} -->" if marks else ""
    if head:
        out.append(f"{head} {text}{tail}")
    elif text.strip():
        out.append(f"{pad}{text}{tail}")
    elif out and out[-1] != "":
        out.append("")


def _docx_table_md(tbl, out: list):
    """表格 → Markdown 表格（首行作表头）"""
    rows = []
    for tr in elems(tbl, "tr"):
        cells = []
        for tc in elems(tr, "tc"):
            cell = " ".join((t.text or "") for t in all_elems(tc, "t")).strip()
            cells.append(cell.replace("|", "\\|") or " ")
        rows.append(cells)
    if not rows:
        return
    width = max(len(r) for r in rows)
    rows = [r + [" "] * (width - len(r)) for r in rows]
    out.append("| " + " | ".join(rows[0]) + " |")
    out.append("|" + "---|" * width)
    for r in rows[1:]:
        out.append("| " + " | ".join(r) + " |")
    out.append("")


def read_docx(path: str, max_chars: int = 60000) -> dict:
    """读取 Word：标题层级 / 粗斜体下划线 / 表格 / 图表 / 内嵌图片 / 页眉页脚"""
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(path)
    body = doc.element.body
    out, stats = [], {"paragraphs": 0, "tables": 0, "images": 0, "charts": 0}
    rels = rels_map(path, "word/document.xml")
    media_seen = []

    for child in body.iterchildren():
        local = child.tag.rsplit("}", 1)[-1] if isinstance(child.tag, str) else ""
        if local == "p":
            para = Paragraph(child, doc)
            # 图片：r:embed → media 部件（仅计数与标注，不内联 base64）
            for blip in all_elems(child, "blip"):
                rid = blip.get(f"{_R}embed") or ""
                member = rels.get(rid, "")
                if member:
                    stats["images"] += 1
                    media_seen.append(member)
                    out.append(f"[图片 #{stats['images']}] {os.path.basename(member)}")
            for chart in all_elems(child, "chart"):
                stats["charts"] += 1
                out.append(f"[图表 #{stats['charts']}] (Word 内嵌图表: {chart.get(f'{_R}id') or ''})")
            _docx_para_md(para, out)
            if (para.text or "").strip():
                stats["paragraphs"] += 1
        elif local == "tbl":
            stats["tables"] += 1
            out.append(f"<!-- 表格 #{stats['tables']} -->")
            _docx_table_md(child, out)

    # 页眉 / 页脚（逐节，模板类文档的版式信息常在此）
    hf = []
    for si, section in enumerate(doc.sections, 1):
        for kind, container in (("页眉", section.header), ("页脚", section.footer)):
            text = " / ".join(p.text.strip() for p in container.paragraphs if p.text.strip())
            if text:
                hf.append(f"- 第{si}节{kind}: {text}")
    if hf:
        out.append("\n<!-- 页眉页脚 -->")
        out.extend(hf)

    # 样式清单（模型改样式时需要知道可用样式名）
    try:
        styles = [s.name for s in doc.styles if s.type is not None][:60]
    except Exception:
        styles = []
    meta = {"ext": "docx", "size": os.path.getsize(path), **stats,
            "images_list": media_seen, "styles": styles,
            "sections": len(doc.sections)}
    head = (f"<!-- Word 文档 {os.path.basename(path)}：段落 {stats['paragraphs']} / "
            f"表格 {stats['tables']} / 图片 {stats['images']} / 节 {len(doc.sections)} -->\n")
    return {"text": _truncate(head + "\n".join(out), max_chars), "images": [], "meta": meta}


# ---------------------------------------------------------------------------
# PowerPoint (.pptx)
# ---------------------------------------------------------------------------
def _anim_index() -> dict:
    """反向索引 pptx_builder._ANIM：presetID/presetClass/subtype → 效果名。

    复用生成侧的动画真值表，避免读取侧重复维护一份常量（同一处修改两端同步）。
    """
    idx = {}
    try:
        from . import pptx_builder as pb
    except Exception:
        return idx
    for name, meta in getattr(pb, "_ANIM", {}).items():
        key = (int(meta.get("pid", -1)), int(meta.get("sub", -1)))
        idx.setdefault(key, name)
    return idx


def _pptx_slide_anims(root) -> list:
    """解析幻灯片 p:timing：返回 [{shape_id, effect, preset_class, delay, trigger}]"""
    idx = _anim_index()
    out = []
    for ctn in all_elems(root, "cTn"):
        cls = (ctn.get("presetClass") or "").strip()
        if cls not in ("entr", "exit", "emph"):
            continue
        try:
            pid = int(ctn.get("presetID") or -1)
            sub = int(ctn.get("presetSubtype") or -1)
        except ValueError:
            continue
        name = idx.get((pid, sub)) or (idx.get((pid, -1)) if sub < 0 else None) or f"preset{pid}"
        delay = 0
        cond = next(iter(all_elems(ctn, "cond")), None)
        if cond is not None and (cond.get("delay") or "").isdigit():
            delay = int(cond.get("delay") or 0)
        trigger = {"clickEffect": "click", "afterEffect": "after"}.get(ctn.get("nodeType") or "", "with")
        # 该 cTn 作用的目标 shape（p:spTgt spid=...）
        tgt = next(iter(all_elems(ctn, "spTgt")), None)
        spid = (tgt.get("spid") if tgt is not None else "") or ""
        out.append({"shape_id": spid, "effect": name, "preset_class": cls,
                    "delay": delay, "trigger": trigger})
    return out


def _shape_kind(shape) -> str:
    """形状类型归类（供模型判断可编辑性）"""
    try:
        if shape.shape_type is not None:
            return str(shape.shape_type).split()[0]
    except Exception:
        pass
    return "UNKNOWN"


def read_pptx(path: str, max_chars: int = 60000) -> dict:
    """读取 PPT：逐页形状（类型/位置/尺寸/字体/字号/颜色/填充）+ 图片 + 表格 + 图表 + 备注 + 动画"""
    from pptx import Presentation
    from pptx.util import Emu

    prs = Presentation(path)
    slide_w = prs.slide_width / 914400 if prs.slide_width else 13.333
    slide_h = prs.slide_height / 914400 if prs.slide_height else 7.5
    out = ["<!-- PPT 每页形状按 z 序列出；坐标为英寸，可直接用于 edit_pptx 的 x/y/w/h -->"]
    stats = {"slides": 0, "shapes": 0, "images": 0, "charts": 0, "tables": 0, "anims": 0}
    anims_by_slide = {}

    for si, slide in enumerate(prs.slides, 1):
        stats["slides"] = si
        out.append(f"\n=== 第 {si} 页 ===")
        title = ""
        try:
            if slide.shapes.title is not None:
                title = slide.shapes.title.text or ""
        except Exception:
            title = ""
        if title:
            out.append(f"标题: {title}")
        bg = ""
        try:
            if slide.background.fill.type is not None and slide.background.fill.type == 1:
                bg = str(slide.background.fill.fore_color.rgb)
        except Exception:
            bg = ""
        if bg:
            out.append(f"背景色: #{bg}")

        for zi, shape in enumerate(slide.shapes):
            stats["shapes"] += 1
            x = round(shape.left / 914400, 2) if shape.left is not None else 0
            y = round(shape.top / 914400, 2) if shape.top is not None else 0
            w = round(shape.width / 914400, 2) if shape.width is not None else 0
            h = round(shape.height / 914400, 2) if shape.height is not None else 0
            kind = _shape_kind(shape)
            nm = shape.name or ""
            head = f"[{zi}] {kind} name={nm!r} pos=({x},{y}) size=({w}x{h})in"

            if shape.shape_type is not None and "PICTURE" in kind:
                stats["images"] += 1
                out.append(f"{head} 图片")
                continue
            if getattr(shape, "has_table", False) and shape.has_table:
                stats["tables"] += 1
                rows = []
                for r in shape.table.rows:
                    rows.append([c.text.strip() for c in r.cells])
                out.append(f"{head} 表格 {len(rows)}行")
                for r in rows:
                    out.append("    | " + " | ".join(r) + " |")
                continue
            if getattr(shape, "has_chart", False) and shape.has_chart:
                stats["charts"] += 1
                ch = shape.chart
                labels = [str(c) for c in (ch.plots[0].categories or [])] if ch.plots else []
                values = list(ch.plots[0].series[0].values) if ch.plots and ch.plots[0].series else []
                out.append(f"{head} 图表 {ch.chart_type} 分类={labels} 值={values}")
                continue
            if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
                fonts, sizes, colors, bolds = set(), set(), set(), set()
                lines = []
                for p in shape.text_frame.paragraphs:
                    t = "".join(r.text for r in p.runs)
                    if t.strip():
                        lines.append(t)
                    for r in p.runs:
                        if r.font.name:
                            fonts.add(r.font.name)
                        if r.font.size is not None:
                            sizes.add(round(r.font.size.pt, 1))
                        try:
                            if r.font.color is not None and r.font.color.type is not None:
                                colors.add(str(r.font.color.rgb))
                        except Exception:
                            pass
                        if r.font.bold:
                            bolds.add(True)
                attr = []
                if fonts:
                    attr.append("font=" + "/".join(sorted(fonts)))
                if sizes:
                    attr.append("size=" + "/".join(f"{s:g}pt" for s in sorted(sizes)))
                if colors:
                    attr.append("color=#" + "/#".join(sorted(colors)))
                if bolds:
                    attr.append("bold")
                out.append(f"{head} {' '.join(attr)}".rstrip())
                for t in lines:
                    out.append(f"    文本: {t}")
                continue
            out.append(f"{head}")

        notes = ""
        try:
            if slide.has_notes_slide:
                notes = (slide.notes_slide.notes_text_frame.text or "").strip()
        except Exception:
            notes = ""
        if notes:
            out.append(f"备注: {notes}")

        root = slide._element
        anims = _pptx_slide_anims(root)
        if anims:
            stats["anims"] += len(anims)
            anims_by_slide[si] = anims
            out.append("动画:")
            for a in anims:
                out.append(f"    shape_id={a['shape_id']} {a['effect']} "
                           f"({a['preset_class']}, {a['trigger']}, delay={a['delay']}ms)")

    meta = {"ext": "pptx", "size": os.path.getsize(path), "slide_width_in": round(slide_w, 3),
            "slide_height_in": round(slide_h, 3), **stats, "anims_by_slide": anims_by_slide}
    head = (f"<!-- PPT {os.path.basename(path)}：{stats['slides']} 页 / 形状 {stats['shapes']} / "
            f"图片 {stats['images']} / 表格 {stats['tables']} / 图表 {stats['charts']} / "
            f"动画 {stats['anims']}；页面尺寸 {slide_w:g}x{slide_h:g} in -->\n")
    return {"text": _truncate(head + "\n".join(out), max_chars), "images": [], "meta": meta}


# ---------------------------------------------------------------------------
# Excel (.xlsx)
# ---------------------------------------------------------------------------
def _cell_fmt(cell) -> str:
    """单元格样式摘要（供模型知道「改了值要不要同步改样式」）"""
    bits = []
    try:
        if cell.number_format and cell.number_format != "General":
            bits.append(f"fmt={cell.number_format}")
        f = cell.font
        if f is not None:
            if f.bold:
                bits.append("bold")
            if f.italic:
                bits.append("italic")
            if f.size:
                bits.append(f"size={f.size:g}pt")
            fcol = norm_hex(getattr(f.color, "rgb", ""))
            if fcol:
                bits.append(f"color=#{fcol}")
        fill = cell.fill
        if fill is not None and fill.fill_type == "solid" and fill.start_color is not None:
            fhex = norm_hex(getattr(fill.start_color, "rgb", ""))
            if fhex:
                bits.append(f"fill=#{fhex}")
        if cell.alignment is not None and cell.alignment.horizontal:
            bits.append(f"align={cell.alignment.horizontal}")
        if cell.hyperlink is not None:
            bits.append("hyperlink")
    except Exception:
        pass
    return ("  <!-- " + " ".join(bits) + " -->") if bits else ""


def chart_summary(chart) -> str:
    """图表摘要：系列名与数据范围（供文字读取与保真预览共用）"""
    parts = []
    for s in getattr(chart, "series", None) or []:
        ref = getattr(getattr(s, "val", None), "numRef", None)
        rng = getattr(ref, "f", "") if ref is not None else ""
        parts.append(f"{series_name(s)}={rng}" if rng else series_name(s))
    return " ; ".join(p for p in parts if p) or "(无系列)"


def series_name(series) -> str:
    """图表系列名（优先字符串引用缓存，其次字面量）"""
    try:
        tx = getattr(series, "tx", None)
        if tx is None:
            return ""
        if getattr(tx, "v", None):
            return str(tx.v)
        ref = getattr(tx, "strRef", None)
        return str(getattr(ref, "f", "") or "")
    except Exception:
        return ""


def read_xlsx(path: str, max_rows: int = 300, max_cols: int = 60) -> dict:
    """读取 Excel：多工作表 / 公式 / 数字格式 / 样式 / 合并 / 列宽行高 / 条件格式 / 图表"""
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter

    wb = load_workbook(path, data_only=False)
    out, stats = [], {"sheets": 0, "rows": 0, "cells": 0, "charts": 0, "merged": 0,
                      "formulas": 0, "cond_formats": 0}
    for ws in wb.worksheets:
        stats["sheets"] += 1
        max_r = min(ws.max_row or 0, max_rows)
        max_c = min(ws.max_column or 0, max_cols)
        out.append(f"\n=== 工作表 '{ws.title}' {ws.max_row}行 x {ws.max_column}列"
                   f"{'（已按上限截取）' if (ws.max_row or 0) > max_r or (ws.max_column or 0) > max_c else ''} ===")
        widths = {k: round(v.width, 1) for k, v in (ws.column_dimensions or {}).items()
                  if getattr(v, "width", None)}
        if widths:
            out.append("列宽: " + ", ".join(f"{k}={v}" for k, v in list(widths.items())[:max_cols]))
        merged = [str(r) for r in ws.merged_cells.ranges]
        if merged:
            stats["merged"] += len(merged)
            out.append("合并单元格: " + ", ".join(merged[:40]))
        charts = getattr(ws, "_charts", []) or []
        if charts:
            stats["charts"] += len(charts)
            out.append(f"图表 {len(charts)} 个:")
            for ci, ch in enumerate(charts, 1):
                out.append(f"    [{ci}] {type(ch).__name__} {chart_summary(ch)}")
        cf_ranges = []
        try:
            for rng in ws.conditional_formatting:
                rng_s = str(rng.sqref)
                for rule in rng.rules:
                    cf_ranges.append(f"{rng_s}:{rule.type}")
        except Exception:
            cf_ranges = []
        if cf_ranges:
            stats["cond_formats"] += len(cf_ranges)
            out.append("条件格式: " + ", ".join(cf_ranges[:40]))
        out.append("| 行 | " + " | ".join(get_column_letter(c) for c in range(1, max_c + 1)) + " |")
        out.append("|" + "---|" * (max_c + 1))
        for r in range(1, max_r + 1):
            stats["rows"] += 1
            cells, notes = [], []
            for c in range(1, max_c + 1):
                cell = ws.cell(row=r, column=c)
                v = cell.value
                stats["cells"] += 1
                if isinstance(v, str) and v.startswith("="):
                    stats["formulas"] += 1
                txt = "" if v is None else str(v).replace("|", "\\|").replace("\n", " ")
                cells.append(txt or " ")
                n = _cell_fmt(cell)
                if n:
                    notes.append(f"{get_column_letter(c)}{r}{n}")
            out.append(f"| {r} | " + " | ".join(cells) + " |")
            if notes:
                out.append("    <!-- 样式: " + " ".join(notes[:12]) + " -->")
    meta = {"ext": "xlsx", "size": os.path.getsize(path), **stats,
            "sheet_names": [w.title for w in wb.worksheets]}
    head = (f"<!-- Excel {os.path.basename(path)}：{stats['sheets']} 表 / {stats['rows']} 行 / "
            f"公式 {stats['formulas']} / 合并 {stats['merged']} / 图表 {stats['charts']} -->\n")
    return {"text": _truncate(head + "\n".join(out), 200000), "images": [], "meta": meta}


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
def read_pdf(path: str, max_pages: int = 200, max_chars: int = 60000) -> dict:
    """读取 PDF：元数据 + 逐页文本（pypdf 缺失时给出明确安装提示，不 mock）"""
    try:
        from pypdf import PdfReader
    except ImportError:
        return {"text": "[read_pdf] 缺少 pypdf：请先 pip install pypdf（已在命令白名单）后重试。",
                "images": [], "meta": {"ext": "pdf", "available": False}}
    reader = PdfReader(path)
    info = {}
    try:
        info = {k: str(v) for k, v in (reader.metadata or {}).items()}
    except Exception:
        info = {}
    total = len(reader.pages)
    out = [f"<!-- PDF {os.path.basename(path)}：{total} 页 -->"]
    if info:
        out.append("元数据: " + ", ".join(f"{k}={v}" for k, v in list(info.items())[:12]))
    tables = 0
    for i, page in enumerate(reader.pages[:max_pages], 1):
        try:
            text = page.extract_text() or ""
        except Exception as e:
            out.append(f"\n=== 第 {i} 页 ===\n[提取失败: {e}]")
            continue
        out.append(f"\n=== 第 {i} 页 ===")
        for line in text.splitlines():
            if line.strip():
                # 表格特征（多段连续空白分隔）线性化为 Markdown 行
                if re.search(r"\S\s{3,}\S", line):
                    cells = re.split(r"\s{3,}", line.strip())
                    tables += 1 if len(cells) > 1 else 0
                    out.append("| " + " | ".join(c.strip() for c in cells) + " |")
                else:
                    out.append(line.rstrip())
    meta = {"ext": "pdf", "size": os.path.getsize(path), "pages": total,
            "metadata": info, "linearized_rows": tables}
    return {"text": _truncate("\n".join(out), max_chars), "images": [], "meta": meta}


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------
_READERS = {
    "docx": read_docx, "docm": read_docx,
    "pptx": read_pptx, "pptm": read_pptx,
    "xlsx": read_xlsx, "xlsm": read_xlsx,
    "pdf": read_pdf,
}


def read_document(path: str, workdir: str = "", **kw) -> dict:
    """统一读取入口：按扩展名分派，返回 {"text", "images", "meta"}。

    kw 透传给具体 reader（如 max_chars / max_rows / max_cols）。
    """
    full = _resolve(path, workdir)
    if not os.path.isfile(full):
        return {"text": f"[读取失败] 文件不存在: {full}", "images": [], "meta": {}}
    ext = ext_of(full)
    reader = _READERS.get(ext)
    if reader is None:
        return {"text": f"[读取失败] 不支持的格式 .{ext}（支持: "
                        f"{'/'.join(sorted(READ_EXTS))}）", "images": [], "meta": {}}
    try:
        return reader(full, **kw)
    except Exception as e:
        return {"text": f"[读取失败] {ext} 解析异常: {e}", "images": [],
                "meta": {"ext": ext, "error": str(e)}}
