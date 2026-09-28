# -*- coding: utf-8 -*-
"""office.preview —— Word / PPT / Excel 的**保真** HTML 预览（供预览面板与放映使用）。

与其他模块的分工：
- reader：给「模型」读的结构化文本（不内联图片 base64）。
- preview：给「人」看的还原渲染（还原样式/图片/图标/图表/条件格式/动画），
  图片一律内联为 data URI，HTML 自包含、无外部依赖，直接 setHtml 即可显示。

对外契约（被 ui/agent_panel.py 与放映控制调用，勿随意改签名）：
- render_office_html(path, ext="", theme=None) -> str | None
    ext 为空时按扩展名自动判断；无法渲染返回 None（调用方回退到 reader 的文本渲染）。
    theme 可选 dict：{bg, card, text, dim, border, accent}（面板主题色，缺省内置深色）。
- extract_slides(path) -> list[dict]：放映用的逐页结构（含形状/图片/动画）。
- office_meta(path) -> dict：文件概况（页数/表数/图片数/图表数等）。

PPT 放映接口（写在导出 HTML 内的脚本，面板用 runJavaScript 调用）：
- window.__deckCount  : 总页数
- window.__deckSlide  : 当前页号（1-based）
- window.__deckShowPage(n) / __deckNext() / __deckPrev() / __deckReset() / __deckAll()
- window.__deckStep() : 播放当前页的下一步动画 → 返回 {slide, step, total, done}

本模块不 import PyQt；样式一律内联/内嵌 <style>，保证 QWebEngineView 与浏览器一致。
"""

import datetime
import html as _html
import os

from .reader import (READ_EXTS, all_elems, chart_summary, elems, ext_of,
                     media_data_uri, norm_hex, rels_map, series_name, zip_bytes)

# ---------------------------------------------------------------------------
# 主题（面板主色：纯黑 + 淡灰 + 白 + 深蓝；禁止 emoji）
# ---------------------------------------------------------------------------
THEME_DARK = {"bg": "#000000", "card": "#1E1E1E", "text": "#F5F5F5",
              "dim": "#9A9A9A", "border": "#2A2A2A", "accent": "#1F3A5F"}
THEME_LIGHT = {"bg": "#FFFFFF", "card": "#F7F8FA", "text": "#1A1A1A",
               "dim": "#6B7280", "border": "#DDE1E6", "accent": "#1F3A5F"}

# 每英寸像素（版面基准，外层容器负责等比缩放，保证与 PowerPoint 版式一致）
PX_PER_IN = 96.0


def _esc(v) -> str:
    return _html.escape("" if v is None else str(v), quote=True)


def _hexc(v, default="") -> str:
    """颜色归一为 6 位 HEX（无 #）；兼容 6 位与 8 位 ARGB（openpyxl 常用后者）"""
    return norm_hex(v, default)


def _pt(v, default=None):
    """字号：Pt 对象 → float；缺失用默认"""
    try:
        return float(v.pt)
    except (AttributeError, TypeError, ValueError):
        return default


def _css_color(hexc, opacity=None):
    c = _hexc(hexc)
    if not c:
        return None
    if opacity is not None and opacity < 1:
        return f"rgba({int(c[0:2],16)},{int(c[2:4],16)},{int(c[4:6],16)},{opacity:g})"
    return "#" + c


# ---------------------------------------------------------------------------
# 放映/动画：效果名 → CSS 关键帧（名称与 office.pptx_builder._ANIM 对齐）
# ---------------------------------------------------------------------------
# 扩展点：新增效果只需在此登记「CSS 关键帧名」，未登记的效果统一回退 fade。
_ANIM_CSS = {
    "appear": "offa-appear", "fade": "offa-fade", "dissolve": "offa-fade",
    "wipe_down": "offa-wipe-down", "wipe_up": "offa-wipe-up",
    "wipe_left": "offa-wipe-left", "wipe_right": "offa-wipe-right",
    "split": "offa-wipe-right", "blinds": "offa-blinds", "checkerboard": "offa-blinds",
    "randombar_h": "offa-wipe-down", "randombar_v": "offa-wipe-right",
    "strips_upleft": "offa-wipe-right", "strips_downright": "offa-wipe-down",
    "box": "offa-zoom", "circle": "offa-zoom", "diamond": "offa-zoom",
    "plus": "offa-zoom", "wedge": "offa-zoom", "wheel": "offa-spin",
    "zoom": "offa-zoom", "faded_zoom": "offa-faded-zoom",
    "grow_turn": "offa-grow-turn", "pinwheel": "offa-spin", "swivel": "offa-spin",
    "fly_top": "offa-fly-top", "fly_bottom": "offa-fly-bottom",
    "fly_left": "offa-fly-left", "fly_right": "offa-fly-right",
    "float_up": "offa-float-up", "float_down": "offa-float-down",
    "float_left": "offa-float-left", "float_right": "offa-float-right",
}

_KEYFRAMES = """
@keyframes offa-appear{from{opacity:0}to{opacity:1}}
@keyframes offa-fade{from{opacity:0}to{opacity:1}}
@keyframes offa-wipe-down{from{clip-path:inset(0 0 100% 0)}to{clip-path:inset(0 0 0 0)}}
@keyframes offa-wipe-up{from{clip-path:inset(100% 0 0 0)}to{clip-path:inset(0 0 0 0)}}
@keyframes offa-wipe-left{from{clip-path:inset(0 100% 0 0)}to{clip-path:inset(0 0 0 0)}}
@keyframes offa-wipe-right{from{clip-path:inset(0 0 0 100%)}to{clip-path:inset(0 0 0 0)}}
@keyframes offa-blinds{from{opacity:.15;clip-path:inset(0 0 0 0)}to{opacity:1;clip-path:inset(0 0 0 0)}}
@keyframes offa-zoom{from{opacity:0;transform:scale(.55)}to{opacity:1;transform:scale(1)}}
@keyframes offa-faded-zoom{from{opacity:0;transform:scale(.8)}to{opacity:1;transform:scale(1)}}
@keyframes offa-grow-turn{from{opacity:0;transform:scale(.4) rotate(-70deg)}to{opacity:1;transform:scale(1) rotate(0)}}
@keyframes offa-spin{from{opacity:0;transform:scale(.3) rotate(180deg)}to{opacity:1;transform:scale(1) rotate(0)}}
@keyframes offa-fly-top{from{opacity:0;transform:translateY(-90px)}to{opacity:1;transform:translateY(0)}}
@keyframes offa-fly-bottom{from{opacity:0;transform:translateY(90px)}to{opacity:1;transform:translateY(0)}}
@keyframes offa-fly-left{from{opacity:0;transform:translateX(-120px)}to{opacity:1;transform:translateX(0)}}
@keyframes offa-fly-right{from{opacity:0;transform:translateX(120px)}to{opacity:1;transform:translateX(0)}}
@keyframes offa-float-up{from{opacity:0;transform:translateY(-40px) rotate(-6deg)}to{opacity:1;transform:none}}
@keyframes offa-float-down{from{opacity:0;transform:translateY(40px) rotate(6deg)}to{opacity:1;transform:none}}
@keyframes offa-float-left{from{opacity:0;transform:translateX(-40px)}to{opacity:1;transform:none}}
@keyframes offa-float-right{from{opacity:0;transform:translateX(40px)}to{opacity:1;transform:none}}
"""

# PPT 每页元素的通用样式（放映：初始隐藏，播放时加 .offa-in 触发动画）
_DECK_CSS = """
.deck{position:relative;margin:0 auto 18px;background:#FFF;box-shadow:0 2px 14px rgba(0,0,0,.35);
      overflow:hidden;display:none}
.deck.on{display:block}
.sg{position:absolute;overflow:hidden;box-sizing:border-box}
.sg .tb{width:100%;height:100%;overflow:hidden}
.sg img{width:100%;height:100%;object-fit:contain}
body.play .sg[data-anim]{opacity:0}
body.play .sg.offa-in{opacity:1;animation-fill-mode:both;animation-timing-function:ease}
body.play .sg.offa-in{animation-duration:.6s}
.slidenum{position:absolute;right:6px;bottom:4px;font-size:11px;color:#999}
"""

_DECK_JS = """
var __sl = document.querySelectorAll('.deck');
var __cur = 1;
var __steps = [];
var __idx = 0;
function __collect(n){
  var els = __sl[n-1].querySelectorAll('.sg[data-anim]');
  __steps = [];
  for (var i=0;i<els.length;i++){ __steps.push(els[i]); }
}
function __deckCount(){ return __sl.length; }
function __deckSlide(){ return __cur; }
function __deckPlaying(){ return document.body.classList.contains('play'); }
function __reset(n){
  __collect(n||__cur);
  for (var i=0;i<__steps.length;i++){ __steps[i].classList.remove('offa-in'); }
  __idx = 0;
}
function __deckShowPage(n){
  n = Math.max(1, Math.min(__sl.length, n|0));
  __cur = n;
  for (var i=0;i<__sl.length;i++){ __sl[i].classList.toggle('on', i===n-1); }
  __reset(n);
  if (typeof __syncSlots === 'function'){ __syncSlots(); }   // 翻页后同步槽位占位
  return {slide:n, total:__sl.length, playing:__deckPlaying()};
}
function __deckNext(){ return __deckShowPage(__cur+1); }
function __deckPrev(){ return __deckShowPage(__cur-1); }
/* 从头放映：进入放映态（带动画元素先隐藏），回到第 1 页等待逐步播放 */
function __deckPlay(n){
  if (typeof __fitMode === 'function'){ __fitMode('screen'); }
  document.body.classList.add('play');
  return __deckShowPage(n||1);
}
/* 下一步：未在放映态时自动进入放映（并播第一个元素），之后每步播一个 */
function __deckStep(){
  if (!document.body.classList.contains('play')){
    document.body.classList.add('play');
    __reset(__cur);
  }
  __collect(__cur);
  if (__idx >= __steps.length){
    return {slide:__cur, step:__idx, total:__steps.length, done:true, playing:true};
  }
  var el = __steps[__idx];
  var fx = el.getAttribute('data-anim') || 'offa-fade';
  el.style.animationName = fx;
  el.style.animationDuration = el.getAttribute('data-dur') || '0.6s';
  el.style.animationTimingFunction = el.getAttribute('data-ease') || 'ease';
  el.style.animationFillMode = 'both';
  el.classList.add('offa-in');
  __idx++;
  return {slide:__cur, step:__idx, total:__steps.length,
          done:__idx>=__steps.length, playing:true};
}
/* 全部显示：退出放映态，本页所有元素一次性完整呈现（预览默认态） */
function __deckAll(){
  document.body.classList.remove('play');
  __collect(__cur);
  for (var i=0;i<__steps.length;i++){
    __steps[i].style.animationName = 'none';
    __steps[i].style.animationFillMode = 'both';
    __steps[i].classList.add('offa-in');
  }
  __idx = __steps.length;
  return {slide:__cur, total:__steps.length, playing:false};
}
"""


def _anim_class(effect: str) -> str:
    """效果名 → CSS 关键帧名（未登记回退淡入）"""
    return _ANIM_CSS.get(str(effect or "").strip().lower(), "offa-fade")


# ---------------------------------------------------------------------------
# Word 保真渲染
# ---------------------------------------------------------------------------
def _docx_para_style(p, default_size=11.0):
    """段落级样式：对齐 / 缩进 / 行距 / 段间距（还原 Word 版面）"""
    css = ["margin:0 0 4px 0"]
    try:
        al = p.alignment
        if al is not None:
            key = str(al).split()[0].lower()
            css.append("text-align:" + {"left": "left", "center": "center",
                                        "right": "right", "justify": "justify"}.get(key, "left"))
    except Exception:
        pass
    pf = p.paragraph_format
    try:
        if pf.left_indent is not None:
            css.append(f"margin-left:{pf.left_indent.pt:g}pt")
        if pf.first_line_indent is not None:
            css.append(f"text-indent:{pf.first_line_indent.pt:g}pt")
        if pf.line_spacing is not None:
            ls = float(pf.line_spacing)
            css.append(f"line-height:{ls:.2f}" if ls < 4 else f"line-height:{ls:g}pt")
        else:
            css.append("line-height:1.5")
        if pf.space_after is not None:
            css[0] = f"margin:0 0 {pf.space_after.pt:g}pt 0"
    except Exception:
        css.append("line-height:1.5")
    return ";".join(css)


def _docx_run_html(run, default_size):
    """run 级样式：粗/斜/下划线/删除线/字号/颜色/字体/底纹/上下标"""
    size = _pt(run.font.size, default_size)
    css = [f"font-size:{size:g}pt"]
    try:
        if run.font.name:
            css.append(f"font-family:'{run.font.name}','Microsoft YaHei',sans-serif")
    except Exception:
        pass
    if run.bold:
        css.append("font-weight:700")
    if run.italic:
        css.append("font-style:italic")
    deco = []
    if run.underline:
        deco.append("underline")
    try:
        if run.font.strike:
            deco.append("line-through")
    except Exception:
        pass
    if deco:
        css.append("text-decoration:" + " ".join(deco))
    col = None
    try:
        if run.font.color is not None and run.font.color.rgb:
            col = str(run.font.color.rgb)
    except Exception:
        col = None
    if _hexc(col):
        css.append("color:#" + _hexc(col))
    try:
        vert = str(run.font.superscript) if hasattr(run.font, "superscript") else ""
    except Exception:
        vert = ""
    if vert == "True":
        css.append("vertical-align:super;font-size:.8em")
    try:
        if run.font.subscript:
            css.append("vertical-align:sub;font-size:.8em")
    except Exception:
        pass
    # 字符底纹（Word w:highlight / rPr shd）
    for shd in all_elems(run._element, "shd"):
        fill = _hexc(shd.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}fill"))
        if fill:
            css.append("background:#" + fill)
    text = run.text or ""
    if not text:
        return ""
    text = _esc(text).replace("\t", "&nbsp;&nbsp;&nbsp;&nbsp;")
    return f"<span style=\"{';'.join(css)}\">{text}</span>"


def _docx_paragraph_html(p, doc, default_size):
    """段落 → HTML（含图片：按 drawing extent 还原宽高，图片内联 data URI）"""
    style = (p.style.name if p.style is not None else "") or ""
    blocks = []
    for run in p.runs:
        blocks.append(_docx_run_html(run, default_size))
    # 内嵌图片（drawing / pict）：按真实显示尺寸还原
    rels = getattr(p, "_parent_doc_rels", None)
    for drawing in all_elems(p._element, "drawing"):
        for blip in all_elems(drawing, "blip"):
            rid = blip.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed")
            member = (rels or {}).get(rid, "")
            if not member:
                continue
            uri = media_data_uri(doc, member)
            if not uri:
                continue
            cx = cy = 0
            for ext in all_elems(drawing, "extent"):
                try:
                    cx, cy = int(ext.get("cx") or 0), int(ext.get("cy") or 0)
                except ValueError:
                    cx = cy = 0
            w = max(24, round(cx / 914400 * PX_PER_IN)) if cx else 320
            blocks.append(f"<img src='{uri}' style='width:{w}px;height:auto;display:block;margin:6px 0'>")
    head = _DOCX_HEADING_CSS.get(style.replace(" ", ""))
    inner = "".join(blocks) if any(blocks) else "&nbsp;"
    tag = "h3" if head else "p"
    return (f"<{tag} style=\"{_docx_para_style(p, default_size)};"
            f"{head or ''}\">{inner}</{tag}>")


# 标题样式 → 渲染 CSS（Word 标题层级在预览中的视觉还原）
_DOCX_HEADING_CSS = {
    "Title": "font-size:26pt;font-weight:700;color:#1F3864;margin:10px 0 8px",
    "Heading1": "font-size:18pt;font-weight:700;color:#1F3864;margin:12px 0 6px",
    "Heading2": "font-size:15pt;font-weight:700;color:#1F3864;margin:10px 0 4px",
    "Heading3": "font-size:13pt;font-weight:700;color:#2F3642;margin:8px 0 4px",
    "Heading4": "font-size:12pt;font-weight:700;color:#2F3642;margin:6px 0 3px",
    "Heading5": "font-size:11.5pt;font-weight:700;color:#4A5568;margin:6px 0 3px",
}


def _docx_cell_style(tc):
    """单元格：底纹 + 边框 + 垂直对齐（还原表格观感）"""
    css = ["padding:4px 8px", "vertical-align:top"]
    shade = ""
    for shd in elems(tc, "tcPr"):
        for s in elems(shd, "shd"):
            shade = _hexc(s.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}fill"))
    if shade:
        css.append("background:#" + shade)
    for cell in elems(tc, "tcPr"):
        for jc in elems(cell, "vAlign"):
            v = _hexc(jc.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val"))
            if v:
                css.append("vertical-align:" + ("middle" if v == "CENTER" else "bottom"))
    return ";".join(css)


def render_docx_html(path: str, theme: dict) -> str:
    """Word 保真 HTML：纸张版面 + 段落样式 + 表格 + 内嵌图片 + 页眉页脚"""
    from docx import Document
    from docx.shared import Emu

    doc = Document(path)
    rels = rels_map(path, "word/document.xml")
    sec = doc.sections[0]
    try:
        page_w = sec.page_width / 914400 if sec.page_width else 8.27
        page_h = sec.page_height / 914400 if sec.page_height else 11.69
        ml = sec.left_margin / 914400 if sec.left_margin is not None else 1.0
        mr = sec.right_margin / 914400 if sec.right_margin is not None else 1.0
    except Exception:
        page_w, page_h, ml, mr = 8.27, 11.69, 1.0, 1.0
    # 正文字号默认值：Normal 样式字号（缺失用 11pt）
    default_size = 11.0
    try:
        default_size = _pt(doc.styles["Normal"].font.size, 11.0)
    except Exception:
        default_size = 11.0

    body = doc.element.body
    parts = []
    for child in body.iterchildren():
        local = child.tag.rsplit("}", 1)[-1] if isinstance(child.tag, str) else ""
        if local == "p":
            from docx.text.paragraph import Paragraph
            p = Paragraph(child, doc)
            p._parent_doc_rels = rels           # 供段落内图片解析 relationships
            parts.append(_docx_paragraph_html(p, path, default_size))
        elif local == "tbl":
            parts.append(_docx_table_html(child, rels, path, default_size))
        elif local == "sectPr":
            continue
    header = " / ".join(q.text.strip() for q in sec.header.paragraphs if q.text.strip())
    footer = " / ".join(q.text.strip() for q in sec.footer.paragraphs if q.text.strip())
    page_w_px = round(page_w * PX_PER_IN)
    pad = round(ml * PX_PER_IN)
    return (
        f"<div class='docwrap'>"
        + (f"<div class='hf'>{_esc(header)}</div>" if header else "")
        + f"<div class='page' data-fit style='width:{page_w_px}px;"
          f"min-height:{round(page_h*PX_PER_IN)}px;padding:48px {pad}px 56px;"
          f"background:#FFF;color:#2F3642;box-shadow:0 2px 14px rgba(0,0,0,.3);"
          f"font-family:'Microsoft YaHei',serif;'>" + "".join(parts) + "</div>"
        + (f"<div class='hf'>{_esc(footer)}</div>" if footer else "")
        + "</div>")


def _docx_table_html(tbl_el, rels, path, default_size):
    """表格保真渲染：边框 + 底纹 + 单元格段落样式（纯 XML 解析，避免依赖父对象）"""
    rows = []
    for tr in elems(tbl_el, "tr"):
        tds = []
        for tc in elems(tr, "tc"):
            spans = ""
            for grid in elems(tc, "tcPr"):
                for gs in elems(grid, "gridSpan"):
                    n = gs.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val")
                    if n and str(n).isdigit() and int(n) > 1:
                        spans = f" colspan='{int(n)}'"
            paras = []
            for p_el in elems(tc, "p"):
                from docx.text.paragraph import Paragraph
                para = Paragraph(p_el, None)
                spans_html = "".join(_docx_run_html(r, default_size) for r in para.runs)
                if spans_html:
                    paras.append(f"<p style='{_docx_para_style(para, default_size)}'>{spans_html}</p>")
            tds.append(f"<td{spans} style='border:1px solid #C9CDD4;{_docx_cell_style(tc)}'>"
                       + ("".join(paras) or "&nbsp;") + "</td>")
        rows.append("<tr>" + "".join(tds) + "</tr>")
    return ("<table style='border-collapse:collapse;width:100%;margin:8px 0;table-layout:fixed'>"
            + "".join(rows) + "</table>")


# ---------------------------------------------------------------------------
# Excel 保真渲染
# ---------------------------------------------------------------------------
def _fmt_number(v, fmt: str) -> str:
    """按 Excel 数字格式近似格式化（% / 千分位 / 小数位 / 货币 / 日期）。

    Excel 格式描述极复杂，此处覆盖生成侧（office.xlsx_builder）与本工具链
    实际产出的常见格式；未识别格式回退原始值（不做假数据）。
    """
    if v is None:
        return ""
    f = str(fmt or "").strip()
    if f in ("", "General"):
        return str(v)
    if isinstance(v, (datetime.datetime, datetime.date)):
        try:
            if "m" in f and "d" in f and "y" in f:
                sep = "/" if "/" in f else ("-" if "-" in f else "")
                return v.strftime("%Y{}%m{}%d".format(sep, sep)) if sep else v.strftime("%Y-%m-%d")
            return v.strftime("%Y-%m-%d")
        except Exception:
            return str(v)
    try:
        num = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f.endswith("%"):
        dec = len(f.split(".")[-1].rstrip("%")) if "." in f else 0
        return f"{num * 100:.{dec}f}%"
    if '"¥"' in f or "$" in f or "￥" in f:
        dec = len(f.split(".")[-1]) if "." in f else 2
        return f"¥{num:,.{dec}f}"
    if "#,##0" in f or "," in f:
        dec = len(f.split(".")[-1]) if "." in f else 0
        return f"{num:,.{dec}f}"
    if "0." in f:
        dec = len(f.split(".")[-1])
        return f"{num:.{dec}f}"
    if f.startswith("0"):
        return f"{num:.0f}"
    return str(v)


def _xlsx_cond_lookup(ws) -> dict:
    """收集条件格式信息：{(row,col): {'bar':ratio}} 与高亮集合（供单元格渲染用）"""
    info = {}
    highlights = set()
    try:
        ranges = list(ws.conditional_formatting)
    except Exception:
        return info, highlights
    for cf in ranges:
        for rule in cf.rules:
            kind = str(getattr(rule, "type", "") or "")
            cells = []
            try:
                for rng in cf.sqref.ranges:
                    for r in range(rng.min_row, rng.max_row + 1):
                        for c in range(rng.min_col, rng.max_col + 1):
                            cells.append((r, c))
            except Exception:
                continue
            if kind == "dataBar":
                vals = []
                for (r, c) in cells:
                    v = ws.cell(row=r, column=c).value
                    if isinstance(v, (int, float)):
                        vals.append((r, c, float(v)))
                if not vals:
                    continue
                hi = max(abs(v) for _, _, v in vals) or 1.0
                # 数据条颜色取规则自带色（Color 对象的 rgb），缺失回退主题蓝
                barcolor = "#638EC6"
                try:
                    c = getattr(getattr(rule, "dataBar", None), "color", None)
                    if c is not None and getattr(c, "rgb", None):
                        barcolor = "#" + (_hexc(str(c.rgb)) or "638EC6")
                except Exception:
                    pass
                for r, c2, v in vals:
                    info.setdefault((r, c2), {})["bar"] = min(1.0, abs(v) / hi)
                    info[(r, c2)]["barcolor"] = barcolor
            elif kind == "colorScale":
                nums = [(r, c, ws.cell(row=r, column=c).value) for (r, c) in cells]
                nums = [(r, c, float(v)) for r, c, v in nums if isinstance(v, (int, float))]
                if not nums:
                    continue
                lo = min(v for _, _, v in nums)
                hi = max(v for _, _, v in nums)
                for r, c, v in nums:
                    ratio = 0.5 if hi == lo else (v - lo) / (hi - lo)
                    red = int(248 + (99 - 248) * ratio)
                    grn = int(105 + (190 - 105) * ratio)
                    blu = int(107 + (123 - 107) * ratio)
                    info.setdefault((r, c), {})["fill"] = f"#{red:02X}{grn:02X}{blu:02X}"
            elif kind in ("expression", "cellIs"):
                # 最大/最小高亮由 edit_xlsx 以公式规则写入（含 MAX/MIN）
                text = " ".join(str(x) for x in (getattr(rule, "formula", None) or []))
                if "MAX" in text.upper() or "MIN" in text.upper():
                    highlights.update(cells)
    return info, highlights


def _sheet_ref(f: str) -> str:
    """图表区域引用 "'汇总'!$E$2:$E$5" -> "E2:E5"。

    openpyxl 图表的 numRef.f / strRef.f 带**工作表前缀与 $ 绝对标记**；
    直接交给 ws[...] 取数会因限定符取不到单元格（图表预览因此显示"无可用数值"）。
    统一在访问前剥离 sheet 前缀与 $。
    """
    f = str(f or "")
    if "!" in f:
        f = f.rsplit("!", 1)[1]
    return f.replace("$", "").strip()


def _xlsx_chart_svg(ws, chart, w=560, h=300) -> str:
    """原生图表 → 内联 SVG（柱/线/饼，按真实单元格数值绘制）。

    说明：这是「解析渲染」，用于预览还原图表数据形状；不做 3D/动画等高级特性。
    """
    kind = type(chart).__name__.lower()
    labels, values = [], []
    for s in getattr(chart, "series", None) or []:
        ref = getattr(getattr(s, "val", None), "numRef", None)
        rng = _sheet_ref(getattr(ref, "f", "")) if ref is not None else ""
        if not rng:
            continue
        try:
            for row in ws[rng]:
                for c in (row if isinstance(row, tuple) else [row]):
                    values.append(c.value if isinstance(c.value, (int, float)) else 0)
        except Exception:
            continue
        cat = getattr(getattr(s, "cat", None), "numRef", None) or getattr(getattr(s, "cat", None), "strRef", None)
        crng = _sheet_ref(getattr(cat, "f", "")) if cat is not None else ""
        if crng:
            try:
                for row in ws[crng]:
                    for c in (row if isinstance(row, tuple) else [row]):
                        labels.append(c.value)
            except Exception:
                pass
    title = ""
    try:
        if chart.title is not None:
            title = " ".join(str(t) for t in all_elems(chart._element, "t"))
    except Exception:
        title = ""
    if not values:
        return f"<div class='chartph'>图表（{type(chart).__name__}）：无可用数值</div>"
    n = len(values)
    vmax = max(values + [0.0001])
    vmin = min(values + [0])
    span = (vmax - vmin) or 1.0
    if "pie" in kind:
        total = sum(abs(v) for v in values) or 1.0
        cx, cy, r, angle, paths, colors = w / 2, h / 2, min(w, h) / 2 - 40, -90.0, [], PALETTE_SVG
        for i, v in enumerate(values):
            sweep = 360.0 * abs(v) / total
            a1, a2 = angle, angle + sweep
            x1, y1 = cx + r * _cos(a1), cy + r * _sin(a1)
            x2, y2 = cx + r * _cos(a2), cy + r * _sin(a2)
            large = 1 if sweep > 180 else 0
            paths.append(f"<path d='M{cx:g},{cy:g} L{x1:.1f},{y1:.1f} A{r:g},{r:g} 0 {large} 1 "
                         f"{x2:.1f},{y2:.1f} Z' fill='{colors[i % len(colors)]}'/>")
            angle = a2
        legend = "".join(
            f"<g><rect x='{w-160}' y='{20+i*18}' width='10' height='10' fill='{PALETTE_SVG[i%6]}'/>"
            f"<text x='{w-144}' y='{29+i*18}' font-size='11' fill='#333'>"
            f"{_esc(labels[i] if i < len(labels) else f'系列{i+1}')}</text></g>"
            for i in range(n))
        return (f"<svg width='{w}' height='{h}' viewBox='0 0 {w} {h}'>"
                f"<text x='10' y='16' font-size='12' fill='#333'>{_esc(title)}</text>"
                + "".join(paths) + legend + "</svg>")
    bars = []
    bw = (w - 60) / max(1, n)
    for i, v in enumerate(values):
        bh = (abs(v) - min(0, vmin)) / span * (h - 60)
        x = 40 + i * bw + bw * 0.15
        y = h - 30 - bh
        cx, cy, rw, rh = x, y, bw * 0.7, bh
        if "line" in kind:
            bars.append("")
        else:
            bars.append(f"<rect x='{cx:.1f}' y='{cy:.1f}' width='{rw:.1f}' height='{rh:.1f}' "
                        f"fill='{PALETTE_SVG[i % len(PALETTE_SVG)]}'/>")
        if i < len(labels):
            bars.append(f"<text x='{cx + rw/2:.1f}' y='{h-14}' font-size='10' fill='#555' "
                        f"text-anchor='middle'>{_esc(str(labels[i])[:6])}</text>")
        bars.append(f"<text x='{cx + rw/2:.1f}' y='{cy-3:.1f}' font-size='10' fill='#333' "
                    f"text-anchor='middle'>{v:g}</text>")
    if "line" in kind:
        pts = []
        for i, v in enumerate(values):
            px = 40 + i * bw + bw * 0.5
            py = h - 30 - (abs(v) - min(0, vmin)) / span * (h - 60)
            pts.append(f"{px:.1f},{py:.1f}")
        bars.append(f"<polyline points='{' '.join(pts)}' fill='none' stroke='{PALETTE_SVG[0]}' "
                    f"stroke-width='2'/>")
    return (f"<svg width='{w}' height='{h}' viewBox='0 0 {w} {h}'>"
            f"<text x='10' y='16' font-size='12' fill='#333'>{_esc(title)} ({type(chart).__name__})</text>"
            + "".join(bars) + "</svg>")


PALETTE_SVG = ["#1F3864", "#D6A23C", "#2E7D8A", "#6B7FD7", "#C65D4B", "#4C9F70"]


def _cos(deg):
    import math
    return math.cos(deg * math.pi / 180)


def _sin(deg):
    import math
    return math.sin(deg * math.pi / 180)


def render_xlsx_html(path: str, theme: dict) -> str:
    """Excel 保真 HTML：多表 tab + 样式 + 数字格式 + 合并 + 列宽行高 + 条件格式 + 图表"""
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter

    wb = load_workbook(path, data_only=False)
    tabs, sheets_html = [], []
    for si, ws in enumerate(wb.worksheets):
        cond, hi = _xlsx_cond_lookup(ws)
        merged_index = {}
        for rng in ws.merged_cells.ranges:
            merged_index[(rng.min_row, rng.min_col)] = (rng.max_row - rng.min_row + 1,
                                                        rng.max_col - rng.min_col + 1)
        skip = set()
        for rng in ws.merged_cells.ranges:
            for r in range(rng.min_row, rng.max_row + 1):
                for c in range(rng.min_col, rng.max_col + 1):
                    if (r, c) != (rng.min_row, rng.min_col):
                        skip.add((r, c))
        cols, rows_html = [], []
        max_c = min(ws.max_column or 1, 80)
        for c in range(1, max_c + 1):
            letter = get_column_letter(c)
            dim = ws.column_dimensions.get(letter)
            width = (dim.width if dim is not None and dim.width else 8.43)
            cols.append(f"<col style='width:{max(26, round(width * 7 + 5))}px'>")
        max_r = min(ws.max_row or 1, 400)
        for r in range(1, max_r + 1):
            dim = ws.row_dimensions.get(r)
            height = (dim.height if dim is not None and dim.height else 15.0)
            tds = []
            for c in range(1, max_c + 1):
                if (r, c) in skip:
                    continue
                cell = ws.cell(row=r, column=c)
                span = merged_index.get((r, c))
                attr = f" rowspan='{span[0]}' colspan='{span[1]}'" if span else ""
                css = ["padding:1px 5px", "white-space:nowrap"]
                f = cell.font
                if f is not None:
                    if f.bold:
                        css.append("font-weight:700")
                    if f.italic:
                        css.append("font-style:italic")
                    if f.size:
                        css.append(f"font-size:{float(f.size):g}pt")
                    if f.name:
                        css.append(f"font-family:'{f.name}','Microsoft YaHei'")
                    if f.color is not None and f.color.rgb:
                        col = _hexc(str(f.color.rgb))
                        if col:
                            css.append("color:#" + col)
                fill = cell.fill
                fill_hex = ""
                if fill is not None and fill.fill_type == "solid" and fill.start_color is not None:
                    # 仅 solid 才是真填充：openpyxl 未设填充时 fill_type 为 None，
                    # 其 start_color.rgb 为 '00000000' 哨兵值，若不过滤会把整表涂黑
                    fill_hex = _hexc(getattr(fill.start_color, "rgb", ""))
                al = cell.alignment
                if al is not None:
                    if al.horizontal:
                        css.append("text-align:" + al.horizontal)
                    if al.vertical:
                        css.append("vertical-align:" + al.vertical)
                    if al.wrap_text:
                        css.append("white-space:normal;word-break:break-word")
                bd = cell.border
                if bd is not None and any(getattr(bd, s).style for s in ("left", "right", "top", "bottom")):
                    for side in ("left", "right", "top", "bottom"):
                        sd = getattr(bd, side)
                        if sd is not None and sd.style:
                            bcol = _hexc(str(getattr(sd.color, "rgb", "") or ""), "BFBFBF")
                            pxs = {"thin": "1px", "medium": "2px", "thick": "3px"}.get(
                                str(sd.style).lower(), "1px")
                            css.append(f"border-{side}:{pxs} solid #{bcol}")
                info = cond.get((r, c)) or {}
                if info.get("fill") and not fill_hex:
                    fill_hex = info["fill"].lstrip("#")
                if fill_hex:
                    css.append("background:#" + fill_hex)
                if (r, c) in hi:
                    css.append("background:#FFF2CC")
                value = cell.value
                if isinstance(value, str) and value.startswith("="):
                    # 公式：显示公式本身（预览不做重算，避免给出错误数值）
                    disp, extra_css = _esc(value), "color:#8A6D1F"
                else:
                    fmt = "" if value is None else str(cell.number_format or "")
                    disp = _esc(_fmt_number(value, fmt))
                    extra_css = ""
                bar = info.get("bar")
                if bar is not None:
                    bcolor = info.get("barcolor") or "#638EC6"
                    tds.append(f"<td{attr} style=\"{';'.join(css)};position:relative\">"
                               f"<span style='position:absolute;left:0;top:1px;bottom:1px;"
                               f"width:{bar*100:.0f}%;background:{bcolor};opacity:.35'></span>"
                               f"<span style='position:relative'>{disp}</span></td>")
                else:
                    tds.append(f"<td{attr} style=\"{';'.join(css)};{extra_css}\">{disp}</td>")
            rows_html.append(f"<tr style='height:{round(float(height) * 1.33)}px'>" + "".join(tds) + "</tr>")
        charts = getattr(ws, "_charts", []) or []
        chart_html = "".join(f"<div class='chart'>{_xlsx_chart_svg(ws, ch)}</div>" for ch in charts)
        sheets_html.append(
            f"<section class='sheet' id='sheet-{si+1}' style='display:{'block' if si == 0 else 'none'}'>"
            f"<div class='xwrap'><table class='xls' style='border-collapse:collapse;background:#FFF;'>"
            f"<colgroup>{''.join(cols)}</colgroup><tbody>{''.join(rows_html)}</tbody></table></div>"
            f"{chart_html}</section>")
        tabs.append(f"<button class='tab{' on' if si == 0 else ''}' data-i='{si+1}'>{_esc(ws.title)}</button>")
    return ("<div class='xlsbox'>"
            f"<div class='tabs'>{''.join(tabs)}</div>" + "".join(sheets_html) + "</div>")


# ---------------------------------------------------------------------------
# PPT 保真渲染（+ 放映）
# ---------------------------------------------------------------------------
def _pptx_run_span(run, default_size=18.0):
    """PPT run → span（字号/颜色/粗斜体/字体/下划线）"""
    css = []
    size = _pt(run.font.size, default_size)
    css.append(f"font-size:{size:g}pt")
    try:
        if run.font.name:
            css.append(f"font-family:'{run.font.name}','Microsoft YaHei'")
    except Exception:
        pass
    if run.font.bold:
        css.append("font-weight:700")
    if run.font.italic:
        css.append("font-style:italic")
    if run.font.underline:
        css.append("text-decoration:underline")
    try:
        if run.font.color is not None and run.font.color.type is not None:
            col = _hexc(str(run.font.color.rgb))
            if col:
                css.append("color:#" + col)
    except Exception:
        pass
    if not (run.text or "").strip():
        return ""
    return f"<span style=\"{';'.join(css)}\">{_esc(run.text).replace(chr(10), '<br>')}</span>"


def _pptx_para_div(para, default_size=18.0):
    """PPT 段落 → div（对齐/行距/项目符号）"""
    css = ["margin:0"]
    try:
        al = str(para.alignment).split()[0].lower() if para.alignment is not None else ""
        css.append("text-align:" + {"left": "left", "center": "center",
                                    "right": "right", "justify": "justify"}.get(al, "left"))
    except Exception:
        pass
    try:
        if para.line_spacing is not None:
            css.append(f"line-height:{float(para.line_spacing):.2f}")
    except Exception:
        pass
    inner = "".join(_pptx_run_span(r, default_size) for r in para.runs)
    if not inner:
        return ""
    return f"<div style=\"{';'.join(css)}\">{inner}</div>"


def _pptx_chart_svg(chart, w=560.0, h=300.0) -> str:
    """python-pptx **原生图表** → 内联 SVG（柱/条形/线/饼环/雷达）。

    供外部 PPT（含原生图表）保真预览；doc-gen builder 手绘图表是形状、走形状渲染。
    仅做解析级还原（不做 3D/动画）；读取失败回退占位，不抛错。
    """
    def _nums(seq):
        out = []
        for v in seq:
            try:
                out.append(float(v))
            except (TypeError, ValueError):
                out.append(0.0)
        return out

    try:
        ct = str(chart.chart_type)
    except Exception:
        ct = ""
    labels, series = [], []
    try:
        plot = list(chart.plots)[0]
        try:
            labels = [str(x) for x in plot.categories]
        except Exception:
            labels = []
        for s in chart.series:
            nm = ""
            try:
                nm = str(s.name or "")
            except Exception:
                pass
            try:
                vals = _nums(s.values)
            except Exception:
                vals = []
            if vals:
                series.append((nm, vals))
    except Exception:
        pass
    title = ""
    try:
        if chart.has_title:
            title = chart.chart_title.text_frame.text or ""
    except Exception:
        pass
    if not series:
        return f"<div class='ph'>图表（{_esc(ct.split(' ')[0])}）</div>"

    allv = [v for _, vs in series for v in vs]
    vmax = max(allv + [0.0001])
    vmin = min(0.0, min(allv))
    span = (vmax - vmin) or 1.0
    multi = len(series) > 1
    plotL, plotT, plotB = 52.0, 36.0, h - 34.0
    plotR = w - (150 if multi else 18)
    plotW, plotH = plotR - plotL, plotB - plotT

    def _legend():
        return "".join(
            f"<g><rect x='{w-140}' y='{40+k*18}' width='11' height='11' "
            f"fill='{PALETTE_SVG[k%len(PALETTE_SVG)]}'/><text x='{w-124}' "
            f"y='{49+k*18}' font-size='11' fill='#333'>{_esc(nm or f'系列{k+1}')}</text></g>"
            for k, (nm, _) in enumerate(series))

    head = (f"<text x='10' y='18' font-size='13' font-weight='bold' fill='#1F3864'>"
            f"{_esc(title)}</text>")

    # 饼 / 环：单系列，按占比切扇区；环用白色圆心覆盖
    if "PIE" in ct or "DOUGHNUT" in ct:
        nm, vals = series[0]
        labs = labels or [str(i + 1) for i in range(len(vals))]
        cx, cy, r = w / 2 - (60 if multi else 0), h / 2 + 4, min(w, h) / 2 - 52
        total = sum(abs(v) for v in vals) or 1.0
        angle, paths = -90.0, []
        for i, v in enumerate(vals):
            sweep = 360.0 * abs(v) / total
            a2 = angle + sweep
            x1, y1 = cx + r * _cos(angle), cy + r * _sin(angle)
            x2, y2 = cx + r * _cos(a2), cy + r * _sin(a2)
            large = 1 if sweep > 180 else 0
            paths.append(
                f"<path d='M{cx:g},{cy:g} L{x1:.1f},{y1:.1f} A{r:g},{r:g} 0 {large} 1 "
                f"{x2:.1f},{y2:.1f} Z' fill='{PALETTE_SVG[i%len(PALETTE_SVG)]}'/>")
            if i < len(labs):
                ma = (angle + a2) / 2
                paths.append(
                    f"<text x='{cx + (r+12)*_cos(ma):.1f}' y='{cy + (r+12)*_sin(ma):.1f}' "
                    f"font-size='10' fill='#333' text-anchor='middle'>{_esc(labs[i])}</text>")
            angle = a2
        if "DOUGHNUT" in ct:
            paths.append(f"<circle cx='{cx:g}' cy='{cy:g}' r='{r*0.52:g}' fill='#FFF'/>")
        return (f"<svg width='{w:g}' height='{h:g}' viewBox='0 0 {w:g} {h:g}'>"
                f"{head}{''.join(paths)}{_legend()}</svg>")

    # 雷达：同心网格 + 各系列多边形
    if "RADAR" in ct:
        n = max(max(len(v) for _, v in series), 3)
        cx, cy, r = plotL + plotW / 2 - (60 if multi else 0), plotT + plotH / 2, \
            min(plotW, plotH) / 2 - 8
        rings = []
        for g in range(1, 5):
            rr = r * g / 4
            pp = []
            for j in range(n):
                a = -90 + 360.0 * j / n
                pp.append(f"{cx+rr*_cos(a):.1f},{cy+rr*_sin(a):.1f}")
            rings.append(f"<polygon points='{' '.join(pp)}' fill='none' "
                         f"stroke='#E3E8EF' stroke-width='1'/>")
        polys = []
        for k, (nm, vals) in enumerate(series):
            pp = []
            for j in range(n):
                v = vals[j] if j < len(vals) else 0
                rr = r * (v - vmin) / span
                a = -90 + 360.0 * j / n
                pp.append(f"{cx+rr*_cos(a):.1f},{cy+rr*_sin(a):.1f}")
            col = PALETTE_SVG[k % len(PALETTE_SVG)]
            polys.append(f"<polygon points='{' '.join(pp)}' fill='{col}' "
                         f"fill-opacity='.18' stroke='{col}' stroke-width='2'/>")
        axis_lab = []
        for j in range(n):
            a = -90 + 360.0 * j / n
            t = labels[j] if j < len(labels) else f"{j+1}"
            axis_lab.append(
                f"<text x='{cx+(r+12)*_cos(a):.1f}' y='{cy+(r+12)*_sin(a):.1f}' "
                f"font-size='9' fill='#555' text-anchor='middle'>{_esc(str(t)[:5])}</text>")
        return (f"<svg width='{w:g}' height='{h:g}' viewBox='0 0 {w:g} {h:g}'>"
                f"{head}{''.join(rings)}{''.join(polys)}{''.join(axis_lab)}"
                f"{_legend()}</svg>")

    n = max(len(v) for _, v in series)
    m = len(series)
    out = []
    for g in range(5):
        yy = plotB - plotH * g / 4
        out.append(f"<line x1='{plotL:g}' y1='{yy:.1f}' x2='{plotR:g}' y2='{yy:.1f}' "
                   f"stroke='#EEF1F5' stroke-width='1'/>")
    horizontal = "BAR" in ct and "COLUMN" not in ct
    is_line = "LINE" in ct

    if horizontal:
        group_h = plotH / n
        bar_h = group_h * 0.68 / m
        for j in range(n):
            gy = plotT + j * group_h
            for k, (nm, vals) in enumerate(series):
                v = vals[j] if j < len(vals) else 0
                bw = (v - vmin) / span * plotW
                y = gy + group_h * 0.16 + k * bar_h
                out.append(
                    f"<rect x='{plotL:g}' y='{y:.1f}' width='{bw:.1f}' height='{bar_h*0.92:.1f}' "
                    f"fill='{PALETTE_SVG[k%len(PALETTE_SVG)]}'/>")
            if j < len(labels):
                out.append(
                    f"<text x='{plotL-6:g}' y='{gy+group_h/2+3:.1f}' font-size='10' fill='#555' "
                    f"text-anchor='end'>{_esc(str(labels[j])[:8])}</text>")
    elif is_line:
        for k, (nm, vals) in enumerate(series):
            pts, dots = [], []
            for j, v in enumerate(vals):
                px = plotL + (plotW * (j / (n - 1) if n > 1 else 0))
                py = plotB - (v - vmin) / span * plotH
                pts.append(f"{px:.1f},{py:.1f}")
                dots.append(f"<circle cx='{px:.1f}' cy='{py:.1f}' r='2.6' "
                            f"fill='{PALETTE_SVG[k%len(PALETTE_SVG)]}'/>")
            out.append(f"<polyline points='{' '.join(pts)}' fill='none' "
                       f"stroke='{PALETTE_SVG[k%len(PALETTE_SVG)]}' stroke-width='2.2'/>")
            out.append("".join(dots))
        for j in range(n):
            if j < len(labels):
                px = plotL + (plotW * (j / (n - 1) if n > 1 else 0))
                out.append(
                    f"<text x='{px:.1f}' y='{plotB+15:.1f}' font-size='10' fill='#555' "
                    f"text-anchor='middle'>{_esc(str(labels[j])[:6])}</text>")
    else:
        group_w = plotW / n
        bar_w = group_w * 0.7 / m
        for j in range(n):
            gx = plotL + j * group_w
            for k, (nm, vals) in enumerate(series):
                v = vals[j] if j < len(vals) else 0
                bh = (v - vmin) / span * plotH
                x = gx + group_w * 0.15 + k * bar_w
                y = plotB - bh
                out.append(
                    f"<rect x='{x:.1f}' y='{y:.1f}' width='{bar_w*0.94:.1f}' "
                    f"height='{bh:.1f}' fill='{PALETTE_SVG[k%len(PALETTE_SVG)]}'/>")
                if not multi:
                    out.append(
                        f"<text x='{x+bar_w/2:.1f}' y='{y-3:.1f}' font-size='9' fill='#444' "
                        f"text-anchor='middle'>{v:g}</text>")
            if j < len(labels):
                out.append(
                    f"<text x='{gx+group_w/2:.1f}' y='{plotB+15:.1f}' font-size='10' fill='#555' "
                    f"text-anchor='middle'>{_esc(str(labels[j])[:6])}</text>")
    out.append(f"<line x1='{plotL:g}' y1='{plotB:g}' x2='{plotR:g}' y2='{plotB:g}' "
               f"stroke='#9AA3AF' stroke-width='1.2'/>")
    return (f"<svg width='{w:g}' height='{h:g}' viewBox='0 0 {w:g} {h:g}'>"
            f"{head}{''.join(out)}{_legend()}</svg>")


def _pptx_shape_html(shape, path, default_size=18.0, anim=None):
    """单个形状 → 绝对定位 div（文字框/图片/表格/图形；含动画 data 属性）"""
    x = (shape.left or 0) / 914400 * PX_PER_IN
    y = (shape.top or 0) / 914400 * PX_PER_IN
    w = (shape.width or 0) / 914400 * PX_PER_IN
    h = (shape.height or 0) / 914400 * PX_PER_IN
    css = [f"left:{x:.1f}px", f"top:{y:.1f}px", f"width:{w:.1f}px", f"height:{h:.1f}px"]
    # 形状填充（矩形/椭圆等实心块：还原底色与圆角）
    try:
        if shape.fill.type is not None and shape.fill.type == 1:
            col = _hexc(str(shape.fill.fore_color.rgb))
            if col:
                css.append("background:#" + col)
        if shape.shape_type is not None and "ROUNDED" in str(shape.shape_type):
            css.append("border-radius:8px")
    except Exception:
        pass
    try:
        if shape.line is not None and shape.line.fill.type is not None and shape.line.fill.type == 1:
            col = _hexc(str(shape.line.color.rgb))
            if col:
                css.append("border:1px solid #" + col)
    except Exception:
        pass
    anim_attr = ""
    if anim:
        fx = _anim_class(anim.get("effect"))
        anim_attr = (f" data-anim='{fx}' data-effect='{_esc(anim.get('effect'))}'"
                     f" data-delay='{int(anim.get('delay') or 0)}'")

    body = ""
    kind = str(shape.shape_type or "")
    if "PICTURE" in kind:
        # 图片：从 r:embed 解析 media 部件并内联
        rid = ""
        for blip in all_elems(shape._element, "blip"):
            rid = blip.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed") or ""
            if rid:
                break
        member = ""
        try:
            if rid:
                member = shape.part.rels[rid].target_part.partname.lstrip("/")
        except Exception:
            member = ""
        uri = media_data_uri(path, member) if member else ""
        body = f"<img src='{uri}' alt=''>" if uri else "<div class='ph'>图片</div>"
    elif getattr(shape, "has_table", False) and shape.has_table:
        rows = []
        for tr in shape.table.rows:
            tds = []
            for cell in tr.cells:
                inner = "".join(_pptx_para_div(p, 14.0) for p in cell.text_frame.paragraphs)
                tds.append(f"<td style='border:1px solid #C9CDD4;padding:2px 6px'>{inner or '&nbsp;'}</td>")
            rows.append("<tr>" + "".join(tds) + "</tr>")
        body = ("<table style='border-collapse:collapse;width:100%;color:#2F3642'>"
                + "".join(rows) + "</table>")
    elif getattr(shape, "has_chart", False) and shape.has_chart:
        body = (f"<div class='tb' style='overflow:hidden;padding:2px'>"
                f"{_pptx_chart_svg(shape.chart)}</div>")
    elif getattr(shape, "has_text_frame", False) and shape.has_text_frame:
        tf = shape.text_frame
        wrap = "word-wrap:break-word;overflow:hidden;"
        if tf.word_wrap is False:
            wrap += "white-space:nowrap;"
        anchor = {"MIDDLE": "center", "BOTTOM": "flex-end"}.get(str(tf.vertical_anchor or ""), "flex-start")
        body = (f"<div class='tb' style='display:flex;flex-direction:column;justify-content:{anchor};"
                f"{wrap}color:#2F3642'>"
                + "".join(_pptx_para_div(p, default_size) for p in tf.paragraphs) + "</div>")
    return f"<div class='sg'{anim_attr} style=\"{';'.join(css)}\">{body}</div>"


def _pptx_slide_shapes(slide, anims, path, theme):
    """一页的所有形状 HTML（按 z 序）；动画按 shape_id 关联到对应形状"""
    order = {}
    for a in anims:
        order.setdefault(str(a.get("shape_id")), a)
    out = []
    for shape in slide.shapes:
        anim = order.get(str(shape.shape_id))
        out.append(_pptx_shape_html(shape, path, anim=anim))
    return "".join(out)


def render_pptx_html(path: str, theme: dict) -> str:
    """PPT 保真 HTML：页面尺寸/背景色/形状位置字体图片表格 + 动画（放映可逐步播放）"""
    from pptx import Presentation
    from .reader import _pptx_slide_anims
    from .reader import rels_map as _rels  # noqa: F401 （保留：供图片关系解析的显式引用）

    prs = Presentation(path)
    sw = (prs.slide_width or 12192000) / 914400 * PX_PER_IN
    sh = (prs.slide_height or 6858000) / 914400 * PX_PER_IN
    decks = []
    for si, slide in enumerate(prs.slides, 1):
        bg = "background:#FFF"
        try:
            if slide.background.fill.type is not None and slide.background.fill.type == 1:
                col = _hexc(str(slide.background.fill.fore_color.rgb))
                if col:
                    bg = "background:#" + col
        except Exception:
            pass
        anims = _pptx_slide_anims(slide._element)
        decks.append(
            f"<div class='deck' data-fit style='width:{sw:.0f}px;height:{sh:.0f}px;{bg}'>"
            + _pptx_slide_shapes(slide, anims, path, theme)
            + f"<div class='slidenum'>{si} / {len(prs.slides._sldIdLst)}</div></div>")
    return ("<div class='deckbox'>" + "".join(decks) + "</div>")


def render_pdf_html(path: str, theme: dict) -> str:
    """PDF 保真页：文档元数据 + 逐页文本（表格行保留为表格），页间分隔清晰。

    PDF 是版式固化格式，无法还原原始排版；此处按「文字+分页」还原可读内容，
    并明确标注为文本级还原（不伪造版式）。
    """
    from .reader import read_pdf
    res = read_pdf(path)
    meta = res.get("meta") or {}
    pages = []
    cur = None
    for line in str(res.get("text") or "").splitlines():
        if line.startswith("=== 第") and line.endswith("==="):
            if cur is not None:
                pages.append(cur)
            cur = [f"<div class='pgttl'>{_esc(line.strip('= '))}</div>"]
            continue
        if line.startswith("<!--") or line.startswith("元数据:"):
            continue
        if cur is None:
            cur = []
        if line.startswith("| ") and line.endswith(" |"):
            cells = [c.strip() for c in line.strip("| ").split("|")]
            cur.append("<table class='pdftbl'><tr>"
                       + "".join(f"<td>{_esc(c)}</td>" for c in cells) + "</tr></table>")
        else:
            cur.append(f"<p>{_esc(line)}</p>")
    if cur is not None:
        pages.append(cur)
    info = meta.get("metadata") or {}
    head = f"<div class='pdfmeta'>共 {meta.get('pages', '?')} 页" + (
        " · " + " · ".join(f"{_esc(k.lstrip('/'))}: {_esc(v)}" for k, v in list(info.items())[:4])
        if info else "") + "</div>"
    return (head + "<div class='pdfdoc'>"
            + "".join(f"<div class='pdfpage'>{''.join(p)}</div>" for p in pages)
            + "</div>")



def extract_slides(path: str, theme=None) -> list:
    """抽取放映用逐页结构：[{index,title,notes,shapes:[...],images:[...],anims:[...]}]"""
    ext = ext_of(path)
    if ext not in ("pptx", "pptm"):
        return []
    from pptx import Presentation
    from .reader import _pptx_slide_anims

    prs = Presentation(path)
    out = []
    for si, slide in enumerate(prs.slides, 1):
        shapes, images, anims = [], [], _pptx_slide_anims(slide._element)
        for zi, shape in enumerate(slide.shapes):
            x = round((shape.left or 0) / 914400, 2)
            y = round((shape.top or 0) / 914400, 2)
            w = round((shape.width or 0) / 914400, 2)
            h = round((shape.height or 0) / 914400, 2)
            item = {"z": zi, "id": shape.shape_id, "name": shape.name or "",
                    "kind": str(shape.shape_type or ""), "x": x, "y": y, "w": w, "h": h}
            try:
                if shape.has_text_frame and shape.has_text_frame:
                    item["text"] = shape.text_frame.text
            except Exception:
                item["text"] = ""
            shapes.append(item)
            if "PICTURE" in item["kind"]:
                images.append(item)
        notes = ""
        try:
            if slide.has_notes_slide:
                notes = slide.notes_slide.notes_text_frame.text or ""
        except Exception:
            notes = ""
        title = ""
        try:
            if slide.shapes.title is not None:
                title = slide.shapes.title.text or ""
        except Exception:
            title = ""
        out.append({"index": si, "title": title, "notes": notes,
                    "shapes": shapes, "images": images, "anims": anims})
    return out


def office_meta(path: str) -> dict:
    """文件概况：类型/大小/页数或表数/图片/图表（用于面板标题与放映控制）"""
    ext = ext_of(path)
    meta = {"ext": ext, "size": os.path.getsize(path) if os.path.isfile(path) else 0}
    try:
        if ext in ("docx", "docm"):
            from docx import Document
            d = Document(path)
            meta.update({"paragraphs": len(d.paragraphs), "tables": len(d.tables),
                         "sections": len(d.sections)})
        elif ext in ("pptx", "pptm"):
            from pptx import Presentation
            p = Presentation(path)
            meta.update({"slides": len(p.slides._sldIdLst),
                         "slide_width_in": round((p.slide_width or 0) / 914400, 2),
                         "slide_height_in": round((p.slide_height or 0) / 914400, 2)})
        elif ext in ("xlsx", "xlsm"):
            from openpyxl import load_workbook
            w = load_workbook(path, read_only=True)
            meta.update({"sheets": len(w.sheetnames), "sheet_names": list(w.sheetnames)})
            w.close()
        elif ext == "pdf":
            try:
                from pypdf import PdfReader
                meta["pages"] = len(PdfReader(path).pages)
            except ImportError:
                meta["pages"] = None
    except Exception as e:
        meta["error"] = str(e)
    return meta


# ---------------------------------------------------------------------------
# 统一入口：完整 HTML 页
# ---------------------------------------------------------------------------
_RENDERERS = {"docx": render_docx_html, "docm": render_docx_html,
              "pptx": render_pptx_html, "pptm": render_pptx_html,
              "xlsx": render_xlsx_html, "xlsm": render_xlsx_html,
              "pdf": render_pdf_html}


def _page_css(theme: dict) -> str:
    """整页 CSS：面板主题配色（纯黑+淡灰+白+深蓝）+ 文档/表格/放映基础样式"""
    return f"""
html,body{{margin:0;padding:10px;background:{theme['bg']};color:{theme['text']};
  font-family:'Microsoft YaHei',-apple-system,Segoe UI,sans-serif;font-size:13px}}
.docwrap{{margin:0 auto}}
.hf{{color:{theme['dim']};font-size:11px;text-align:center;margin:2px 0 6px}}
.page{{margin:0 auto;box-sizing:border-box}}
.xlsbox{{background:{theme['bg']};color:{theme['text']}}}
.tabs{{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 8px}}
.tab{{background:{theme['card']};color:{theme['text']};border:1px solid {theme['border']};
  border-radius:5px;padding:3px 10px;cursor:pointer;font-size:12px}}
.tab.on{{background:{theme['accent']};border-color:{theme['accent']};color:#FFF}}
.xwrap{{overflow:auto;border:1px solid {theme['border']};border-radius:6px;max-height:78vh}}
table.xls{{font-size:12px;color:{theme['text']}}}
.chart{{margin:12px 0;background:{theme['card']};border:1px solid {theme['border']};
  border-radius:6px;padding:8px}}
.chartph,.ph{{color:{theme['dim']};font-size:12px;text-align:center;padding:4px}}
.pdfmeta{{color:{theme['dim']};font-size:11px;margin:2px 0 8px}}
.pdfdoc{{display:flex;flex-direction:column;align-items:center;gap:12px}}
.pdfpage{{width:100%;max-width:820px;background:#FFF;color:#2F3642;padding:24px 28px;
  box-shadow:0 2px 12px rgba(0,0,0,.3);font-size:12.5px;line-height:1.6}}
.pdfpage .pgttl{{color:{theme['accent']};font-weight:700;font-size:12px;margin:0 0 6px}}
.pdfpage p{{margin:2px 0}}
table.pdftbl{{border-collapse:collapse;margin:3px 0}}
table.pdftbl td{{border:1px solid #C9CDD4;padding:2px 8px}}
.deckbox{{display:flex;flex-direction:column;align-items:center;gap:6px}}
.deckbox .deck{{transform-origin:top center}}
/* 自适应缩放槽位：承载缩放后的占位尺寸，保证布局不溢出、不残留空白 */
.fitslot{{position:relative;margin:0 auto 10px;overflow:hidden}}
.fitslot > *{{position:absolute;left:0;top:0}}
/* 全屏放映模式：深色底、禁滚动、画布按视口 contain 适配（可放大） */
body.screen{{background:#000;padding:10px;overflow:hidden}}
body.screen .hf,body.screen .pdfmeta,body.screen .tabs{{display:none}}
body.screen .docwrap{{width:auto !important;min-height:0 !important}}
body.screen .fitslot{{margin:0 auto}}
body.screen .deck{{box-shadow:none}}
body.screen .deckbox{{gap:0}}
/* 全屏查看模式（Word/Excel/PDF）：深色底 + 可滚动，画布同样 contain 适配 */
body.fullview{{background:#000;padding:8px;overflow:auto}}
body.fullview .hf,body.fullview .pdfmeta{{display:none}}
body.fullview .docwrap{{width:auto !important;min-height:0 !important}}
body.fullview .fitslot{{margin:0 auto}}
body.fullview .deckbox{{gap:0}}
body.fullview .xlsbox{{min-height:0}}
body.fullview .xwrap{{max-height:none}}
/* 正在渲染提示：覆盖层（大文件加载期间给明确反馈，避免"空白"观感） */
#offa-loading{{position:fixed;left:0;right:0;top:0;padding:8px 0;text-align:center;
  background:rgba(0,0,0,.55);color:#F5F5F5;font-size:12px;z-index:99;
  transition:opacity .24s ease;pointer-events:none}}
#offa-loading .sp{{display:inline-block;width:9px;height:9px;margin-right:7px;
  border:1.6px solid rgba(245,245,245,.35);border-top-color:#F5F5F5;border-radius:50%;
  vertical-align:-1px;animation:offa-spin 0.8s linear infinite}}
@keyframes offa-spin{{to{{transform:rotate(360deg)}}}}
{_KEYFRAMES}
{_DECK_CSS}
"""


def _page_js() -> str:
    """放映脚本 + 自适应缩放 + 多工作表 tab 切换（纯前端，无外部依赖）。

    自适应缩放：所有带 data-fit 的「页面画布」（PPT 每一页、Word 页面）按容器
    宽度等比缩放（只缩小不放大），避免出现固定像素画布在窄面板里溢出、需要
    用户手动缩小；全屏放映模式下改按视口 contain 适配（可放大）。
    """
    return ("<script>" + _DECK_JS + """
/* ---------- 自适应缩放（适宽 / 全屏 contain） ---------- */
function __fitContain(){
  return document.body.classList.contains('screen')
      || document.body.classList.contains('fullview');
}
var __fitJunk = {};                 // 元素已包过槽位（避免重复包裹）
function __slotOf(el){
  if (el.__fitslot){ return el.__fitslot; }
  var slot = document.createElement('div');
  slot.className = 'fitslot';
  el.parentNode.insertBefore(slot, el);
  slot.appendChild(el);
  el.style.transformOrigin = 'top left';
  el.__fitslot = slot;
  return slot;
}
function __fitSlots(){
  var contain = __fitContain();
  var els = document.querySelectorAll('[data-fit]');
  var needRetry = false;
  for (var i=0;i<els.length;i++){
    var el = els[i];
    var slot = __slotOf(el);
    var nw = el.offsetWidth || 0;              // transform 不影响 offsetWidth → 原始尺寸
    var nh = el.offsetHeight || 0;
    if (!nw || !nh){ needRetry = true; continue; }
    var host = slot.parentNode || document.body;
    var hostW = host.clientWidth || 0;
    // 【关键】宿主尚未布局完成（宽度为 0/极小）时**不做缩放**：否则会算出
    // 0.09 之类的比例，把画布缩成一个小白块，看起来就是"预览空白"
    // （历史上此处用 Math.max(120, …) 兜底，正是空白预览的根因）。
    if (hostW < 40 && !contain){ needRetry = true; continue; }
    var k;
    if (contain){
      var vw = Math.max(200, window.innerWidth - 24);
      var vh = Math.max(160, window.innerHeight - 24);
      k = Math.min(vw / nw, vh / nh);          // contain：完整装下（可放大）
    } else {
      k = Math.min(1, (hostW - 4) / nw);       // 适宽：只缩小，避免糊化
    }
    if (!isFinite(k) || k <= 0){ needRetry = true; continue; }
    el.style.transform = 'scale(' + k + ')';
    slot.style.width = Math.round(nw * k) + 'px';
    slot.style.height = Math.round(nh * k) + 'px';
  }
  // 多页画布只保留当前页占位：PPT 一次只看一页，其余页若保留槽位高度会在
  // 下方堆出大片空白（观感即"预览空白/要一直往下滚"）。Word/Excel 的 data-fit
  // 页不属于 .deck，不受影响，仍按序完整排布。
  __syncSlots();
  if (needRetry){ __fitLater(); }
  return !needRetry;
}
/* 槽位可见性：仅当前页占位（翻页与缩放共用，避免两处逻辑漂移） */
function __syncSlots(){
  for (var m=0;m<__sl.length;m++){
    var s2 = __sl[m].__fitslot;
    if (s2){ s2.style.display = __sl[m].classList.contains('on') ? '' : 'none'; }
  }
}
var __fitRetryTimer = null;
function __fitLater(){                    // 布局未就绪时的重试（合并成一次）
  if (__fitRetryTimer){ return; }
  __fitRetryTimer = setTimeout(function(){
    __fitRetryTimer = null;
    if (typeof requestAnimationFrame === 'function'){
      requestAnimationFrame(function(){ __fitSlots(); });
    } else {
      __fitSlots();
    }
  }, 60);
}
function __fitWatch(){                    // 容器尺寸真正就绪时自动重算（最可靠）
  try {
    if (typeof ResizeObserver === 'function'){
      if (window.__fitRO){ window.__fitRO.disconnect(); }
      window.__fitRO = new ResizeObserver(function(){ __fitSlots(); });
      window.__fitRO.observe(document.documentElement);
      if (document.body){ window.__fitRO.observe(document.body); }
    }
  } catch (e) { /* 环境不支持则退化为 resize 监听 + 重试 */ }
  for (var i=0;i<10;i++){ setTimeout(__fitSlots, 80 * (i + 1)); }   // 兜底轮询若干次
}
function __fitAll(){ __fitWatch(); return __fitSlots(); }
/* 呈现模式：'' 普通预览 / 'screen' 放映（禁滚动，逐条动画）/ 'fullview' 全屏查看（可滚动） */
function __fitMode(mode){
  document.body.classList.remove('screen');
  document.body.classList.remove('fullview');
  if (mode === 'screen' || mode === 'fullview'){ document.body.classList.add(mode); }
  __fitSlots();
  window.scrollTo(0, 0);
  return {mode: mode, slide: __cur, total: __sl.length};
}
function __deckScreen(on){ return __fitMode(on === false ? '' : 'screen'); }
function __deckFullView(on){ return __fitMode(on === false ? '' : 'fullview'); }
/* 放映下一页：当前页动画未播完则先播完剩余，再切下一页（符合演示习惯） */
function __deckAdvance(){
  var r = __deckStep();
  if (r && r.done){ return __deckNext(); }
  return r;
}
/* 正在渲染提示：默认显示，首帧渲染完成后淡出（大文件给用户明确反馈） */
function __offaReady(){
  var el = document.getElementById('offa-loading');
  if (!el){ return; }
  el.style.opacity = '0';
  setTimeout(function(){ if (el && el.parentNode){ el.parentNode.removeChild(el); } }, 260);
}
(function(){
  var tabs = document.querySelectorAll('.tab');
  for (var i=0;i<tabs.length;i++){
    tabs[i].addEventListener('click', function(){
      var n = this.getAttribute('data-i');
      var sheets = document.querySelectorAll('.sheet');
      for (var j=0;j<sheets.length;j++){ sheets[j].style.display = 'none'; }
      var target = document.getElementById('sheet-' + n);
      if (target) { target.style.display = 'block'; }
      for (var k=0;k<tabs.length;k++){ tabs[k].classList.remove('on'); }
      this.classList.add('on');
      __fitSlots();
    });
  }
  // 幻灯片：默认展示第 1 页；随后按容器宽度自适应缩放（无需手动缩小）
  if (__sl.length){ __deckShowPage(1); }
  __fitWatch();                       // 容器尺寸就绪后自动重算（消除"小白块"空白）
  __fitSlots();
  window.addEventListener('resize', function(){ __fitSlots(); });
  if (document.readyState === 'complete'){ __offaReady(); }
  else { window.addEventListener('load', function(){ __offaReady(); }); }
  setTimeout(__offaReady, 2500);      // 兜底：任何情况下不残留提示
  // 放映态下点击推进下一步（演示习惯）；普通预览与全屏查看不改变状态
  document.addEventListener('click', function(e){
    if (!document.body.classList.contains('screen')) { return; }
    if (e.target && e.target.closest && e.target.closest('.tab')) { return; }
    __deckAdvance();
  });
})();
</script>""")


def render_office_html(path: str, ext: str = "", theme=None) -> str:
    """保真预览主入口：返回自包含 HTML 页；不支持的类型返回 None（调用方回退文本预览）。

    theme 为面板主题色 dict（可选）：{bg, card, text, dim, border, accent}
    """
    if not path or not os.path.isfile(path):
        return None
    e = (ext or ext_of(path)).lstrip(".").lower()
    renderer = _RENDERERS.get(e)
    if renderer is None:
        return None
    t = dict(THEME_DARK)
    if isinstance(theme, dict):
        t.update({k: v for k, v in theme.items() if k in t and v})
    try:
        inner = renderer(path, t)
    except Exception as e2:
        # 保真渲染失败：返回带原因的最简页，调用方据此回退到文本预览
        return ("<!DOCTYPE html><html><head><meta charset='utf-8'></head><body "
                f"style='background:{t['bg']};color:{t['dim']};font-family:Microsoft YaHei;padding:12px'>"
                f"保真渲染不可用：{_esc(e2)}</body></html>")
    # PPT 默认**不**进入放映态：所有元素完整可见（预览要能看到完整样式与内容）；
    # 只有用户点「从头放映 / 下一步」时才加 body.play 让带动画元素先隐藏再逐条播放。
    # 加载提示覆盖层：页面首帧完成后由脚本淡出（大文件加载期间给明确反馈）。
    loading = ("<div id='offa-loading'><span class='sp'></span>正在渲染预览…</div>")
    return ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<style>{_page_css(t)}</style></head><body>{loading}{inner}{_page_js()}</body></html>")
