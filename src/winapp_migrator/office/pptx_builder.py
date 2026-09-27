# -*- coding: utf-8 -*-
"""PowerPoint 高质量生成器（python-pptx，16:9 宽屏）。

针对"模板感强"的视觉升级原则：
- 统一排版系统而非逐行画：标题=左侧强调竖条+大标题；页码/装饰有固定语义位置；
- 大面积色块只用主题色板（primary/accent/deep），卡片不再整块高饱和填充，
  改为"主题浅底 + 强调色条 + 同色标题"，观感克制高级；
- 深色页面自动将正文切为浅色，保证可读；
- 补全 old schema 声明但未实现的布局：two_col / center_highlight，以及封面作者/日期/副标题；
- **元素动画（默认开启，style.animation=False 关闭）**：按「读序」逐元素出场（标题→
  正文/图形按行带+从左到右，卡片底框先于卡内文字），效果按页序从创意池轮换，既有官方
  位移/缩放类（fly/float/zoom/swivel 等 p:anim 属性动画）也有滤镜类（fade/wipe/box/
  circle/diamond/...）；默认 style.anim_trigger="with"：一次点击后整页按 stagger(默认
  160ms) 流畅级联，click=每元素单独点击，after=衔接式；页级 slide.anim={title/text/
  graphics:效果名, trigger, stagger, max, exit:退场效果, enabled:false} 可覆盖，
  style.animation_effect=全局统一入场效果，style.exit_effect=每页结尾整体退场（逆序）。
  幻灯片切换（transition）新增 fade/push/wipe/split/cover/pull/zoom/dissolve/circle/
  blinds/checker/wheel/comb/plus/newsflash/cut/wedge/diamond/random 真实切换（原生 XML）。

slide 字段：title/bullets/cards/table/chart/diagram/image/wordart/bg_color/title_color/
layout(left_image/right_image/two_col/center_highlight)/columns(两栏用)/highlight(强调页用)/
anim(动画配置对象或效果名)。
style 字段：theme/font_name/cover_style/title_color/bg_color/accent/cover{subtitle,author,date,image}/
footer(页脚文字)/transition/title_size/body_size/animation/animation_effect/anim_trigger/
anim_stagger/exit_effect。
"""

import math

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt

from . import theme, utils

SW, SH = 13.333, 7.5            # 页尺寸（英寸）
MX, CW = 0.62, 12.1             # 左右安全边距 / 正文可用宽
TT_TOP, TT_BOT = 0.5, 1.52      # 标题带
BT_TOP, BT_BOT = 1.72, 7.0      # 正文安全区
TITLE_RULE_Y, TITLE_RULE_H = 1.18, 0.034   # 标题下分隔线（高≈3.3px）
PAGE_X, PAGE_Y, PAGE_W, PAGE_H = 11.90, 7.00, 1.00, 0.40   # 页码块（96×38.4px）
AXIS_LABEL_H = 0.42                        # 轴线图底部类目标签带（预留高度）
_BULLET = {"dot": "•  ", "number": "{}.  ", "arrow": "→  ", "check": "✓  "}


def _c(v, d="1F3864"):
    return RGBColor.from_string(theme.hex_color(v, d))


def _is_dark(c: str) -> bool:
    h = theme.hex_color(c)
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (0.299 * r + 0.587 * g + 0.114 * b) < 128


def _item_text(v):
    """元素文案归一化：dict 取 title/text/name 字段，否则拼接非空值。

    防止 LLM 传入对象（如 compare 型 {title,left,right}）被 str() 渲染成
    "{'title': ...}" 这类未解析字面量。
    """
    if isinstance(v, dict):
        for k in ("title", "text", "name"):
            s = str(v.get(k) or "").strip()
            if s:
                return s
        parts = [str(x).strip() for x in v.values() if str(x).strip()]
        return "  ".join(parts) if parts else ""
    return str(v)


def _lum(hexc) -> float:
    """颜色亮度（0-255，人眼加权）：用于可读性判断。"""
    h = theme.hex_color(hexc)
    if not h:
        return 0.0
    return 0.299 * int(h[0:2], 16) + 0.587 * int(h[2:4], 16) + 0.114 * int(h[4:6], 16)


def _readable(fg, bg, fallback):
    """前景色与底色亮度过于接近（白底白字 / 黑底黑字）时退回可读色。

    只处理「几乎看不清」的极端情形，正常配色原样返回，不干预设计意图。
    """
    if not fg:
        return fallback
    if abs(_lum(fg) - _lum(bg)) >= 90:
        return fg
    return fallback if abs(_lum(fallback) - _lum(bg)) >= abs(_lum(fg) - _lum(bg)) else fg


def _ink_on(bg, dark="1F3864", light="F5F5F5"):
    """给定底色的默认文字色：浅底用深字、深底用浅字。"""
    return dark if _lum(bg) >= 128 else light


def _est_text_h(items, size, w) -> float:
    """估算要点块高度（英寸）：中文按「字号≈字宽」折算每行字数，再加段间距。

    仅用于排版预留空间（判断会不会压到别的元素或落到页面外），不做精确排版。
    """
    texts = [_item_text(i) for i in (items or []) if _item_text(i).strip()]
    if not texts:
        return 0.0
    per_line = max(6.0, w * 72.0 / max(6.0, float(size)))
    lines = sum(max(1, int((len(t) + per_line - 1) // per_line)) for t in texts)
    return lines * float(size) * 1.42 / 72.0 + 0.12 * (len(texts) - 1)


def _font(run, font, size, bold=False, color="2F3642", italic=False):
    run.font.name = font
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.color.rgb = _c(color)
    # 中文字体需同时写 eastAsia
    from pptx.oxml.ns import qn
    rPr = run._r.get_or_add_rPr()
    la = rPr.find(qn("a:latin"))
    if la is None:
        la = rPr.makeelement(qn("a:latin"), {})
        rPr.append(la)
    la.set("typeface", font)
    ea_elem = rPr.find(qn("a:ea"))
    if ea_elem is None:
        ea_elem = rPr.makeelement(qn("a:ea"), {})
        rPr.append(ea_elem)
    ea_elem.set("typeface", font)


def _solid(shape, hexc):
    shape.fill.solid()
    shape.fill.fore_color.rgb = _c(hexc)
    shape.line.fill.background()
    shape.shadow.inherit = False


def _rect(slide, x, y, w, h, color, rounded=False, radius=0.08):
    kind = MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE
    sh = slide.shapes.add_shape(kind, Inches(x), Inches(y), Inches(w), Inches(h))
    if rounded:
        try:
            sh.adjustments[0] = radius
        except Exception:
            pass
    _solid(sh, color)
    return sh


def _textbox(slide, x, y, w, h, text, font, size, color, bold=False,
             align=PP_ALIGN.LEFT, word_wrap=True):
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = word_wrap
    tf.margin_left = tf.margin_right = Inches(0.02)
    tf.margin_top = tf.margin_bottom = Inches(0.01)
    p = tf.paragraphs[0]
    p.alignment = align
    p.space_after = Pt(0)
    r = p.add_run()
    r.text = str(text)
    _font(r, font, size, bold=bold, color=color)
    return tb


def _page_block(slide, page, font, color="7FB8DE"):
    """右下页码块（1.00×0.40in = 96.0×38.4px）；封面与内容页共用，保证页码连续。"""
    if page:
        _textbox(slide, PAGE_X, PAGE_Y, PAGE_W, PAGE_H, str(page), font, 10, color,
                 align=PP_ALIGN.RIGHT)


def _cover(slide, prs, title, t, style, page=""):
    s = style or {}
    cover = s.get("cover") if isinstance(s.get("cover"), dict) else {}
    subtitle = str(cover.get("subtitle") or "")
    author = str(cover.get("author") or "")
    date = str(cover.get("date") or "")
    cover_img = str(cover.get("image") or "")
    cover_style = str(s.get("cover_style") or "solid").lower()
    primary, deep, accent, ink, soft = t["primary"], t["deep"], t["accent"], t["ink"], t["soft"]
    font = t["font"]

    if cover_style == "split":
        _rect(slide, 0, 0, 5.6, SH, primary)
        _rect(slide, 5.6, 0, 0.09, SH, accent)
        # 左块装饰环
        ring = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(-0.7), Inches(5.2), Inches(2.6), Inches(2.6))
        _solid(ring, accent)
        _textbox(slide, 0.6, 6.3, 4.6, 0.5, date or "", font, 13, "FFFFFF", align=PP_ALIGN.LEFT)
        _textbox(slide, 6.0, 2.3, 6.6, 2.0, str(title), font, 38, primary, bold=True)
        if subtitle:
            _textbox(slide, 6.05, 3.55, 6.4, 1.0, subtitle, font, 16, soft)
        if author:
            _textbox(slide, 6.05, 4.9, 6.4, 0.5, author, font, 13, soft)
        if cover_img.strip():
            _place_pic(slide, cover_img, 6.05, 4.35, 6.4, 2.2, None, None)
    elif cover_style == "centered":
        c_size = theme.to_int(cover.get("title_size"), 44, 12, 90)
        c_color = theme.hex_color(cover.get("title_color"), primary)
        _rect(slide, 0, 0, SW, SH, "FFFFFF")
        _rect(slide, 0, 0, SW, 0.12, accent)
        _textbox(slide, 1.0, 1.5, 11.33, 1.7, str(title), font, c_size, c_color, bold=True,
                 align=PP_ALIGN.CENTER)
        _rect(slide, 6.0, 3.55, 1.33, 0.045, accent)
        if subtitle:
            _textbox(slide, 1.0, 3.85, 11.33, 0.9, subtitle, font, 18, ink,
                     align=PP_ALIGN.CENTER)
        _textbox(slide, 1.0, 6.5, 11.33, 0.5, "　".join(x for x in (author, date) if x),
                 font, 13, soft, align=PP_ALIGN.CENTER)
        if cover_img.strip():
            _place_pic(slide, cover_img, 4.67, 4.75, 4.0, 1.6, None, None)
    else:  # solid 主色大底
        _rect(slide, 0, 0, SW, SH, primary)
        ring = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(10.4), Inches(-1.1), Inches(3.9), Inches(3.9))
        _solid(ring, accent)
        ring2 = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(-1.6), Inches(6.4), Inches(3.1), Inches(3.1))
        _solid(ring2, deep)
        _textbox(slide, 1.0, 2.4, 11.33, 1.9, str(title), font, 44, "FFFFFF", bold=True,
                 align=PP_ALIGN.CENTER)
        _rect(slide, 6.0, 4.42, 1.33, 0.05, accent)
        if subtitle:
            _textbox(slide, 1.0, 4.7, 11.33, 0.8, subtitle, font, 17, "E8EAF0",
                     align=PP_ALIGN.CENTER)
        _textbox(slide, 1.0, 6.55, 11.33, 0.5, "　".join(x for x in (author, date) if x),
                 font, 13, "C9D2E0", align=PP_ALIGN.CENTER)
    _page_block(slide, page, font)


def _place_pic(slide, path, x, y, max_w, max_h, workdir, align="center"):
    """等比放图；返回实际占位 (w_in, h_in, x_in)。失败返回 None。"""
    try:
        p = utils.resolve_path(path, workdir)
    except ValueError:
        return None
    if not p.is_file():
        return None
    scale, w, h = utils.fit_scale(str(p), max_w * 96, max_h * 96)
    w_in, h_in = w * scale / 96, h * scale / 96
    if align == "center":
        x = (SW - w_in) / 2 if x is None else x + (max_w - w_in) / 2
    elif align == "right":
        x = x + max_w - w_in
    else:
        x = x if x is not None else MX
    slide.shapes.add_picture(str(p), Inches(x), Inches(y), Inches(w_in), Inches(h_in))
    return w_in, h_in, x


def _add_titlebar(slide, text, accent, ink, font, size, footer_txt="", page=""):
    """标题带：左侧强调竖条 + 标题 + 标题下分隔线；右下页码块（1.00×0.40in）。"""
    _rect(slide, MX, TT_TOP + 0.06, 0.09, 0.52, accent)
    _textbox(slide, MX + 0.3, TT_TOP, 11.0, 0.75, text, font, size, ink, bold=True)
    _rect(slide, MX, TITLE_RULE_Y, CW, TITLE_RULE_H, accent)
    if footer_txt:
        _textbox(slide, MX, 7.12, 9.0, 0.3, footer_txt, font, 9.5, "A6AEB8")
    _page_block(slide, page, font)


def _md_prefix(it):
    """解析要点前缀 → (kind, level, 正文)。

    kind: 'title'（Markdown 标题，``#``~``######``）/ 'sub'（``- `` 二级要点）/ 'bullet'。
    历史实现只识别 ``## ``，导致 ``### 行业地位`` 这类输入把 ``#`` 原样排进页面，
    故此处按「任意级数标题」统一归一（``#`` 一律不落到成品文案里）。
    """
    head = len(it) - len(it.lstrip("#"))
    if head and it[head:head + 1] == " ":
        return "title", head, it[head + 1:].strip()
    if it.startswith("- "):
        return "sub", 0, it[2:].strip()
    return "bullet", 0, it


def _render_bullets(slide, bullets, x, y, w, h, t, body_size, bullet_fmt, title_color=None):
    """渲染要点正文（含 '#'*1-6 标题 / '- ' 二级要点层级缩进），自动按字数微调字号防溢出。"""
    items = [_item_text(b) for b in (bullets or []) if _item_text(b).strip()]
    if not items:
        return
    chars = sum(len(it) for it in items)
    size = body_size
    if chars > 380:
        size = max(13, body_size - 4)
    elif chars > 240:
        size = max(14, body_size - 2)
    elif chars > 150 and w < 5.5:
        size = max(13, body_size - 2)
    ink = t["ink"]
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    first = True
    for it in items:
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        kind, head, txt = _md_prefix(it)
        p.level = 0
        if not txt:
            p.space_after = Pt(4)
            continue
        if kind == "title":
            prefix = ""
        elif kind == "sub":
            prefix = "–  "
        else:
            prefix = bullet_fmt.format(len(tf.paragraphs)) if "{}" in bullet_fmt else bullet_fmt
        r = p.add_run()
        r.text = prefix
        _font(r, t["font"], size if kind != "title" else (size + 1 if head <= 2 else size),
              bold=(kind == "title"), color=(title_color or t["primary"]) if kind == "title" else ink)
        # 行内 **粗体**：拆成多个 run，避免星号原样排进成品文案
        for seg_i, seg in enumerate(txt.split("**")):
            if not seg:
                continue
            rs = p.add_run()
            rs.text = seg
            emph = seg_i % 2 == 1
            if kind == "title":
                _font(rs, t["font"], size + 1 if head <= 2 else size, bold=True,
                      color=title_color or t["primary"])
            elif kind == "sub":
                _font(rs, t["font"], max(12, size - 1), color=ink, bold=emph)
            else:
                _font(rs, t["font"], size, color=ink, bold=emph)
        if kind == "title":
            p.space_before = Pt(8)
            p.space_after = Pt(3)
        elif kind == "sub":
            p.level = 1
            p.space_after = Pt(5)
        else:
            p.space_after = Pt(9)
    return tb


def _render_cards(slide, cards, t, font, y=BT_TOP, x=MX, w=CW, max_h=4.4):
    cards = [c for c in (cards or []) if isinstance(c, dict)]
    if not cards:
        return 0.0
    n = len(cards)
    gap = 0.22
    cw = (w - gap * (n - 1)) / n
    hgt = max(1.6, min(max_h, 6.95 - y - 0.1))
    for i, c in enumerate(cards):
        color = theme.hex_color(c.get("color"), t["chart"][i % len(t["chart"])])
        base_bg = theme.lighten(color, 0.87)
        cx = x + i * (cw + gap)
        # 浅底圆角卡
        _rect(slide, cx, y, cw, hgt, base_bg, rounded=True, radius=0.05)
        # 顶部强调条
        _rect(slide, cx + 0.28, y + 0.24, 0.62, 0.09, color)
        # 数字徽章（可选数字标题时更醒目）
        title = str(c.get("title") or "")
        desc = str(c.get("desc") or "")
        tf_box = slide.shapes.add_textbox(Inches(cx + 0.2), Inches(y + 0.45),
                                          Inches(cw - 0.4), Inches(hgt - 0.7))
        tf = tf_box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        r = p.add_run()
        r.text = title
        _font(r, font, 21, bold=True, color=color)
        p.space_after = Pt(8)
        if desc:
            p2 = tf.add_paragraph()
            r2 = p2.add_run()
            r2.text = desc
            _font(r2, font, 13.5, color=t["ink"])
    return hgt + 0.2


def _render_table(slide, spec, t, font, x, y, w, max_h=4.6):
    spec = spec or {}
    header = [str(h) for h in (spec.get("header") or [])]
    rows = [r for r in (spec.get("rows") or []) if isinstance(r, (list, tuple))]
    if not header and not rows:
        return False
    ncols = max(len(header), max((len(r) for r in rows), default=0)) or 1
    nrows = 1 + len(rows)
    title = str(spec.get("title") or "")
    top = y
    if title:
        _textbox(slide, x, y, w, 0.4, title, font, 15, t["primary"], bold=True)
        top = y + 0.5
    hgt = min(max_h, 0.34 * nrows + 0.5)
    # 表格底板（浅色衬底，表内留白）
    _rect(slide, x - 0.12, top - 0.1, w + 0.24, min(max_h, 0.34 * nrows) + 0.5, t["bg"],
          rounded=True, radius=0.03)
    gfx = slide.shapes.add_table(nrows, ncols, Inches(x), Inches(top),
                                 Inches(w), Inches(min(max_h, 0.4 * nrows))).table
    widths = _table_widths(header, rows, w)
    for ci in range(ncols):
        try:
            gfx.columns[ci].width = Inches(widths[ci])
        except Exception:
            pass
    for rn in range(nrows):
        for cn in range(ncols):
            cell = gfx.cell(rn, cn)
            cell.margin_left = cell.margin_right = Inches(0.06)
            cell.margin_top = cell.margin_bottom = Inches(0.03)
            if rn == 0:
                val = header[cn] if cn < len(header) else ""
            else:
                src = rows[rn - 1]
                val = str(src[cn]) if cn < len(src) else ""
            cell.fill.solid()
            if rn == 0:
                cell.fill.fore_color.rgb = _c(t["primary"])
            else:
                cell.fill.fore_color.rgb = _c(t["bg"] if rn % 2 == 1 else "FFFFFF")
            p = cell.text_frame.paragraphs[0]
            p.alignment = PP_ALIGN.CENTER if rn == 0 else PP_ALIGN.LEFT
            rr = p.add_run()
            rr.text = val
            _font(rr, font, 11.5, bold=(rn == 0),
                  color="FFFFFF" if rn == 0 else t["ink"])
    return True


def _table_widths(header, rows, total):
    cols = max(len(header), max((len(r) for r in rows), default=0)) or 1
    weight = [1] * cols
    for ci in range(cols):
        texts = []
        if ci < len(header):
            texts.append(header[ci])
        for r in rows:
            if isinstance(r, (list, tuple)) and ci < len(r):
                texts.append(str(r[ci]))
        weight[ci] = max(1.0, min(10.0, max((len(s) for s in texts), default=1) * 1.15))
    sm = sum(weight)
    return [total * wgt / sm for wgt in weight]


def _draw_chart(slide, chart, t, font, x, y, w, h):
    """手绘图表（column/bar/line/pie/doughnut），色彩来自主题 chart 色板。"""
    ctype = str(chart.get("type") or "column").lower()
    labels = [str(x) for x in (chart.get("labels") or [])]
    values = []
    for v in (chart.get("values") or []):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            try:
                values.append(float(v))
            except (TypeError, ValueError):
                continue
        else:
            values.append(float(v))
    n = min(len(labels), len(values))
    if n == 0:
        return
    labels, values = labels[:n], values[:n]
    title = str(chart.get("title") or "").strip()
    top = y
    if title:
        _textbox(slide, x, y, w, 0.4, title, font, 14.5, t["primary"], bold=True)
        top = y + 0.46
    chart_colors = [theme.hex_color(c, t["chart"][i % 6])
                    for i, c in enumerate(t["chart"])]

    if ctype in ("pie", "doughnut"):
        _draw_pie(slide, labels, values, chart_colors, font, x, top, w, h - 0.46)
        return
    if ctype == "bar":
        _draw_bar(slide, labels, values, chart_colors, font, t, x, top, w, h - 0.46)
        return
    if ctype == "line":
        _draw_line(slide, labels, values, chart_colors, font, t, x, top, w, h - 0.46)
        return
    if ctype == "radar":
        _draw_radar(slide, labels, values, chart_colors, font, t, x, top, w, h - 0.46)
        return
    if ctype == "scatter":
        _draw_scatter(slide, labels, chart.get("points") or values, chart_colors,
                      font, t, x, top, w, h - 0.46)
        return
    if ctype == "combo":
        _draw_combo(slide, labels, values, chart.get("values2") or [], chart_colors,
                    font, t, x, top, w, h - 0.46)
        return
    _draw_column(slide, labels, values, chart_colors, font, t, x, top, w, h - 0.46)


def _axes(slide, t, x, y, w, h, font, base_color):
    """浅色横向网格 + 基线，返回可用绘图宽度。"""
    for k in range(1, 5):
        gy = y + h * k / 5
        ln = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(gy),
                                    Inches(w), Pt(0.6))
        _solid(ln, "EDF1F6")
    base = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y + h),
                                  Inches(w), Pt(1.1))
    _solid(base, base_color)


def _plot_h(h) -> float:
    """轴线图的可绘制高度：底部预留类目标签带，保证标签不越出块（更不越出页面）。"""
    return max(0.7, h - AXIS_LABEL_H)


def _draw_column(slide, labels, values, colors, font, t, x, y, w, h):
    plot_h = _plot_h(h)
    _axes(slide, t, x, y, w, plot_h, font, theme.darken(t["band"], 0.25))
    vmax = max(values) or 1.0
    n = len(values)
    gap_px = 0.45
    bw = min(0.9, (w - gap_px * (n - 1)) / n * 0.62)
    plot_w = bw * n + gap_px * (n - 1)
    x0 = x + (w - plot_w) / 2
    label_skip = 1 if n <= 10 else max(1, (n + 9) // 10)
    pitch = bw + gap_px
    for i, v in enumerate(values):
        bh = max(0.08, plot_h * 0.86 * v / vmax)
        bx = x0 + i * (bw + gap_px)
        by = y + plot_h - bh
        color = colors[i % len(colors)]
        bar = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE,
                                     Inches(bx), Inches(by), Inches(bw), Inches(bh))
        bar.adjustments[0] = 0.18
        _solid(bar, color)
        # 数值/类目标签：宽度收在柱间距内并居中于柱，避免相邻标签框互相压叠
        vw = min(bw + 0.5, pitch * 0.96)
        _textbox(slide, bx + (bw - vw) / 2, max(y, by - 0.3), vw, 0.28, f"{v:g}", font,
                 10.5, t["ink"], bold=True, align=PP_ALIGN.CENTER)
        if i % label_skip == 0 or n <= 10:
            lw = min(bw + 0.9, pitch * 0.96)
            lt = _textbox(slide, bx + (bw - lw) / 2, y + plot_h + 0.04, lw,
                          AXIS_LABEL_H - 0.04, labels[i], font, 9.5, t["soft"],
                          align=PP_ALIGN.CENTER)
            lt.text_frame.word_wrap = False


def _draw_bar(slide, labels, values, colors, font, t, x, y, w, h):
    _axes(slide, t, x, y, w, h, font, theme.darken(t["band"], 0.25))
    vmax = max(values) or 1.0
    n = len(values)
    bh = min(0.52, (h - 0.35) / n * 0.6)
    gap = (h - 0.4 - bh * n) / max(n - 1, 1)
    for i, v in enumerate(values):
        bw = max(0.35, w * 0.8 * v / vmax)
        by = y + (h - 0.45) - (i + 1) * (bh + gap)
        color = colors[i % len(colors)]
        bar = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE,
                                     Inches(x), Inches(by), Inches(bw), Inches(bh))
        bar.adjustments[0] = 0.5
        _solid(bar, color)
        _textbox(slide, x + bw + 0.08, by - 0.06, w - bw - 0.1, 0.5,
                 f"{labels[i]}  {v:g}", font, 10.5, t["ink"], bold=True)


def _draw_radar(slide, labels, values, colors, font, t, x, y, w, h):
    """雷达图：n 顶点均布，多层参考环 + 数据多边形 + 顶点数值。"""
    nums = [float(v) for v in values[:16]]
    if not nums:
        return
    vmax = max(nums) or 1.0
    n = len(nums)
    cx, cy = x + w * 0.42, y + h / 2
    r = min(h * 0.42, w * 0.30)
    line_c = theme.darken(t["band"], 0.25)
    for k in (1, 2, 3):
        rr = r * k / 3
        for i in range(n):
            a0 = -90 + i * 360.0 / n
            a1 = -90 + (i + 1) * 360.0 / n
            p0 = (cx + rr * math.cos(math.radians(a0)), cy + rr * math.sin(math.radians(a0)))
            p1 = (cx + rr * math.cos(math.radians(a1)), cy + rr * math.sin(math.radians(a1)))
            seg = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT,
                                             Inches(p0[0]), Inches(p0[1]),
                                             Inches(p1[0]), Inches(p1[1]))
            seg.line.color.rgb = _c(line_c)
            seg.line.width = Pt(0.8)
            seg.shadow.inherit = False
    color = colors[0]
    pts = []
    for i, v in enumerate(nums):
        a = math.radians(-90 + i * 360.0 / n)
        px = cx + r * v / vmax * math.cos(a)
        py = cy + r * v / vmax * math.sin(a)
        pts.append((px, py))
        dot = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(px - 0.07), Inches(py - 0.07),
                                     Inches(0.15), Inches(0.15))
        _solid(dot, color)
    for i in range(n):
        px, py = pts[i]
        nx, ny = pts[(i + 1) % n]
        conn = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT,
                                          Inches(px), Inches(py), Inches(nx), Inches(ny))
        conn.line.color.rgb = _c(color)
        conn.line.width = Pt(2.2)
        conn.shadow.inherit = False
    for i, v in enumerate(nums):
        a = math.radians(-90 + i * 360.0 / n)
        px = cx + r * v / vmax * math.cos(a)
        py = cy + r * v / vmax * math.sin(a)
        _textbox(slide, px - 0.3, py - 0.38, 0.9, 0.3, f"{v:g}", font, 10, t["ink"],
                 bold=True, align=PP_ALIGN.CENTER)
        lx = cx + (r + 0.32) * math.cos(a) - 0.45
        ly = cy + (r + 0.32) * math.sin(a) - 0.15
        _textbox(slide, lx, ly, 0.9, 0.3, labels[i] if i < len(labels) else "", font, 9,
                 t["soft"], align=PP_ALIGN.CENTER)


def _draw_scatter(slide, labels, points, colors, font, t, x, y, w, h):
    """散点图：labels=点名称，points=[[x,y],...]（或 values 折行解析）。"""
    pts = []
    for p in points[:40]:
        if isinstance(p, (list, tuple)) and len(p) >= 2 and isinstance(p[0], (int, float)):
            pts.append((float(p[0]), float(p[1])))
        elif isinstance(p, (int, float)):
            pts.append((float(p), float(p)))
    if not pts:
        return
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    xspan = (x1 - x0) or 1.0
    yspan = (y1 - y0) or 1.0
    x_off, y_off = 0.7, 0.5
    pw, ph = w - x_off - 0.6, h - y_off - 0.5
    base_c = theme.darken(t["band"], 0.25)
    _axes(slide, t, x + x_off, y, pw, ph, font, base_c)
    color = colors[0]
    for i, (pxv, pyv) in enumerate(pts):
        bx = x + x_off + (pxv - x0) / xspan * pw
        by = y + ph - (pyv - y0) / yspan * ph
        dot = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(bx - 0.09), Inches(by - 0.09),
                                     Inches(0.18), Inches(0.18))
        _solid(dot, color)
        if i < len(labels) and str(labels[i]).strip():
            _textbox(slide, bx + 0.12, by - 0.13, 1.8, 0.3, labels[i], font, 9,
                     t["ink"], align=PP_ALIGN.LEFT)


def _draw_combo(slide, labels, values, values2, colors, font, t, x, y, w, h):
    """组合图：柱状(主) + 折线(副 values2)，双量纲对比例。"""
    bars = [float(v) for v in values]
    line = [float(v) for v in values2]
    if not bars:
        return
    plot_h = _plot_h(h)
    _axes(slide, t, x, y, w, plot_h, font, theme.darken(t["band"], 0.25))
    vmax = max(bars) or 1.0
    n = len(bars)
    gap_px, bw = 0.45, 0.5
    x0 = x + (w - (bw * n + gap_px * (n - 1))) / 2
    for i, v in enumerate(bars):
        bh = max(0.06, plot_h * 0.8 * v / vmax)
        bx = x0 + i * (bw + gap_px)
        bar = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE,
                                     Inches(bx), Inches(y + plot_h - bh), Inches(bw), Inches(bh))
        bar.adjustments[0] = 0.2
        _solid(bar, colors[i % len(colors)])
    if line:
        pts = []
        for i, v in enumerate(line[:n]):
            px = x0 + i * (bw + gap_px) + bw / 2
            py = y + plot_h - (v - min(line)) / (max(line) - min(line) or 1.0) * (plot_h * 0.8)
            pts.append((px, py))
        lc = colors[-1] if len(colors) > 1 else theme.hex_color(t["accent"], "E0A02E")
        for i in range(len(pts) - 1):
            conn = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT,
                                              Inches(pts[i][0]), Inches(pts[i][1]),
                                              Inches(pts[i + 1][0]), Inches(pts[i + 1][1]))
            conn.line.color.rgb = _c(lc)
            conn.line.width = Pt(2.6)
            conn.shadow.inherit = False
        for px, py in pts:
            dot = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(px - 0.07), Inches(py - 0.07),
                                         Inches(0.15), Inches(0.15))
            _solid(dot, lc)
    label_skip = 1 if n <= 10 else max(1, (n + 9) // 10)
    for i in range(n):
        if i % label_skip == 0 or n <= 10:
            _textbox(slide, x0 + i * (bw + gap_px) - 0.3, y + plot_h + 0.04, bw + 0.9,
                     AXIS_LABEL_H - 0.04, labels[i] if i < len(labels) else "", font, 9.5,
                     t["soft"], align=PP_ALIGN.CENTER)


def _draw_line(slide, labels, values, colors, font, t, x, y, w, h):
    plot_h = _plot_h(h)
    _axes(slide, t, x, y, w, plot_h, font, theme.darken(t["band"], 0.25))
    vmax = max(values) or 1.0
    vmin = min(values + [0.0])
    span = (vmax - vmin) or 1.0
    n = len(values)
    step = w / max(n - 1, 1)
    pts = []
    for i, v in enumerate(values):
        px = x + i * step
        py = y + plot_h - (v - vmin) / span * (plot_h * 0.86)
        pts.append((px, py, v))
    color = colors[0]
    for i in range(len(pts) - 1):
        x1, y1, _ = pts[i]
        x2, y2, _ = pts[i + 1]
        conn = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT,
                                          Inches(x1), Inches(y1),
                                          Inches(x2), Inches(y2))
        conn.line.color.rgb = _c(color)
        conn.line.width = Pt(2.6)
        conn.shadow.inherit = False
    label_skip = 1 if n <= 9 else max(1, (n + 7) // 8)
    for i, (px, py, v) in enumerate(pts):
        dot = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(px - 0.07), Inches(py - 0.07),
                                     Inches(0.15), Inches(0.15))
        _solid(dot, color)
        _textbox(slide, px - 0.25, max(y, py - 0.34), 0.8, 0.28, f"{v:g}", font, 10,
                 t["ink"], bold=True, align=PP_ALIGN.CENTER)
        if i % label_skip == 0 or n <= 9:
            _textbox(slide, px - 0.5, y + plot_h + 0.04, 1.4, AXIS_LABEL_H - 0.04,
                     labels[i], font, 9.5, t["soft"], align=PP_ALIGN.CENTER)


def _draw_pie(slide, labels, values, colors, font, x, y, w, h):
    total = sum(values) or 1.0
    dia = min(h * 0.92, w * 0.55)
    cx = x + dia / 2 + 0.35
    cy = y + (h - dia) / 2 + dia / 2
    start = 0.0
    for i, v in enumerate(values):
        sweep = v / total * 360.0
        color = colors[i % len(colors)]
        sh = slide.shapes.add_shape(MSO_SHAPE.PIE,
                                    Inches(cx - dia / 2), Inches(cy - dia / 2),
                                    Inches(dia), Inches(dia))
        try:
            sh.adjustments[0] = int(start * 60000)
            sh.adjustments[1] = int((start + sweep) * 60000)
        except Exception:
            pass
        sh.fill.solid()
        sh.fill.fore_color.rgb = _c(color)
        sh.line.color.rgb = RGBColor.from_string("FFFFFF")
        sh.line.width = Pt(1.5)
        sh.shadow.inherit = False
        if sweep >= 12:  # 扇区足够大才显示百分比
            ang = math.radians(start + sweep / 2)
            lx = cx + (dia * 0.62) * math.cos(ang) - 0.4
            ly = cy + (dia * 0.62) * math.sin(ang) - 0.15
            _textbox(slide, lx, ly, 0.8, 0.3, f"{v / total * 100:.0f}%", font, 10,
                     "FFFFFF", bold=True, align=PP_ALIGN.CENTER)
        start += sweep
    # 图例
    ly = y + 0.1
    for i, lb in enumerate(labels):
        dot = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(x + dia + 0.9), Inches(ly + 0.04),
                                     Inches(0.16), Inches(0.16))
        _solid(dot, colors[i % len(colors)])
        _textbox(slide, x + dia + 1.15, ly, 4.6, 0.3, f"{lb}  {values[i]:g}", font, 11,
                 "3A4150")
        ly += 0.36


def _draw_diagram(slide, diagram, t, font, x, y, w, h):
    dtype = str(diagram.get("type") or "mindmap").lower()
    items = [it for it in (diagram.get("items") or [])]
    title = str(diagram.get("title") or "").strip()
    top = y
    if title:
        _textbox(slide, x, y, w, 0.4, title, font, 14.5, t["primary"], bold=True)
        top = y + 0.5
    colors = [theme.hex_color(c, t["chart"][i % 6]) for i, c in enumerate(t["chart"])]
    if dtype == "flow":
        n = len(items)
        if not n:
            return
        gap = 0.5
        bw = min(2.5, (w - gap * (n - 1)) / n)
        total_w = n * bw + (n - 1) * gap
        fx = x + (w - total_w) / 2
        for i, it in enumerate(items):
            _rect(slide, fx, top + 0.3, bw, 1.15, colors[i % len(colors)],
                  rounded=True, radius=0.12)
            _textbox(slide, fx + 0.08, top + 0.62, bw - 0.16, 0.6, _item_text(it),
                     font, 12.5, "FFFFFF", bold=True, align=PP_ALIGN.CENTER)
            if i < n - 1:
                ar = slide.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW,
                                            Inches(fx + bw + 0.05), Inches(top + 0.66),
                                            Inches(gap - 0.1), Inches(0.42))
                _solid(ar, t["primary"])
            fx += bw + gap
    elif dtype == "compare":
        n = len(items)
        if not n:
            return
        col_w = (w - 0.9) / 2
        for idx in (0, 1):
            color = colors[idx % len(colors)]
            bx = x + idx * (col_w + 0.9)
            _rect(slide, bx, top + 0.2, col_w, min(h, 4.1), color, rounded=True, radius=0.05)
            # 每项 {title,left,right} 视为一个「方案对比面板」：第1/3/5…项→左栏，
            # 第2/4…项→右栏；每栏单一文本框按段落排布，避免文字重叠
            tb = slide.shapes.add_textbox(Inches(bx + 0.3), Inches(top + 0.45),
                                          Inches(col_w - 0.6), Inches(min(h, 4.1) - 0.7))
            tf = tb.text_frame
            tf.word_wrap = True
            first_p = True
            for it in items[idx::2]:
                title = str(it.get("title") or "") if isinstance(it, dict) else _item_text(it)
                subs = ([str(it.get("left") or ""), str(it.get("right") or "")]
                        if isinstance(it, dict) else [])
                if not title.strip() and not subs:
                    continue
                if title.strip():
                    p = tf.paragraphs[0] if first_p else tf.add_paragraph()
                    if not first_p:
                        p.space_before = Pt(14)
                    p.space_after = Pt(5)
                    rr = p.add_run()
                    rr.text = title
                    _font(rr, font, 15, bold=True, color="FFFFFF")
                    first_p = False
                for s in subs:
                    if not s.strip():
                        continue
                    p = tf.paragraphs[0] if first_p else tf.add_paragraph()
                    first_p = False
                    rr = p.add_run()
                    rr.text = s
                    p.space_after = Pt(4)
                    _font(rr, font, 12.5, color="F2F5F9")
        vs = slide.shapes.add_shape(MSO_SHAPE.OVAL,
                                    Inches(x + col_w - 0.32), Inches(top + 1.8),
                                    Inches(0.62), Inches(0.62))
        _solid(vs, t["accent"])
        _textbox(slide, x + col_w - 0.24, top + 1.9, 0.5, 0.4, "VS", font, 12, "FFFFFF",
                 bold=True, align=PP_ALIGN.CENTER)
    elif dtype == "cycle":
        n = len(items)
        if not n:
            return
        cx, cy = x + w / 2, top + min(h, 4.4) / 2 + 0.3
        radius = min(1.9, w / 4.6)
        for i, it in enumerate(items):
            ang = -90 + i * (360.0 / n)
            bx = cx + radius * math.cos(math.radians(ang)) - 1.05
            by = cy + radius * math.sin(math.radians(ang)) - 0.6
            _rect(slide, bx, by, 2.1, 1.2, colors[i % len(colors)], rounded=True, radius=0.16)
            _textbox(slide, bx + 0.05, by + 0.38, 2.0, 0.6, _item_text(it), font, 11.5,
                     "FFFFFF", bold=True, align=PP_ALIGN.CENTER)
        _rect(slide, cx - 0.9, cy - 0.75, 1.8, 1.5, t["primary"], rounded=True, radius=0.2)
        _textbox(slide, cx - 0.9, cy - 0.32, 1.8, 0.5, str(diagram.get("center") or "核心"),
                 font, 12.5, "FFFFFF", bold=True, align=PP_ALIGN.CENTER)
    elif dtype == "timeline":
        # 横向时间轴：{date,text} 或 "2023 事件"（首空格切分为日期+描述），沿主轴交替上下排布
        items = []
        for it in (diagram.get("items") or [])[:8]:
            if isinstance(it, dict):
                d = str(it.get("date") or "")
                txt = str(it.get("text") or it.get("title") or "")
            else:
                s = str(it).strip()
                d, _, txt = s.partition(" ") if s.count(" ") >= 1 else (s, "", "")
            items.append((d.strip(), txt.strip()))
        if not items:
            return
        ax_y = top + min(h, 4.4) / 2 + 0.35
        n = len(items)
        gap = 0.35
        stab = min(1.8, (w - gap * (n - 1)) / n)
        total = n * stab + (n - 1) * gap
        fx = x + (w - total) / 2
        _rect(slide, x, ax_y - 0.02, w, 0.03, theme.darken(t["band"], 0.3))
        for i, (d, txt) in enumerate(items):
            cxm = fx + stab / 2
            on_top = i % 2 == 0
            dot = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(cxm - 0.09), Inches(ax_y - 0.11),
                                         Inches(0.22), Inches(0.22))
            _solid(dot, colors[i % len(colors)])
            bar_h = 0.62 + 0.32 * (1 if not txt else min(2.0, len(txt) / 10.0))
            if on_top:
                box_y = ax_y - 0.28 - bar_h
                line_y = ax_y - 0.15
                txt_top = box_y + 0.1
            else:
                box_y = ax_y + 0.18
                line_y = ax_y + 0.08
                txt_top = box_y + 0.1
            _rect(slide, cxm - stab / 2 + 0.08, box_y, stab - 0.16, bar_h,
                  t["bg"], rounded=True, radius=0.08)
            conn = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT,
                                              Inches(cxm), Inches(line_y),
                                              Inches(cxm), Inches(box_y + (0.1 if on_top else bar_h - 0.1)))
            conn.line.color.rgb = _c(colors[i % len(colors)])
            conn.line.width = Pt(1.2)
            conn.shadow.inherit = False
            if d:
                _textbox(slide, cxm - stab / 2 + 0.14, txt_top, stab - 0.28, 0.3, d, font, 10.5,
                         colors[i % len(colors)], bold=True, align=PP_ALIGN.CENTER)
            if txt:
                _textbox(slide, cxm - stab / 2 + 0.14, txt_top + 0.3, stab - 0.28, bar_h - 0.4,
                         txt, font, 9.5, t["ink"], align=PP_ALIGN.CENTER)
            fx += stab + gap
    else:  # mindmap
        center = str(diagram.get("center") or "主题")
        n = len(items)
        if not n:
            return
        cx, cy = x + w / 2, top + 2.2
        _rect(slide, cx - 1.15, cy - 0.42, 2.3, 0.84, t["primary"], rounded=True, radius=0.2)
        _textbox(slide, cx - 1.15, cy - 0.2, 2.3, 0.5, center, font, 13.5, "FFFFFF",
                 bold=True, align=PP_ALIGN.CENTER)
        pos = [(cx - 2.4, cy - 1.7), (cx - 2.4, cy + 1.0),
               (cx + 1.4, cy - 1.7), (cx + 1.4, cy + 1.0),
               (cx - 2.4, cy - 0.55), (cx + 1.4, cy - 0.55),
               (cx - 2.4, cy + 0.3), (cx + 1.4, cy + 0.3)]
        for i, it in enumerate(items[:len(pos)]):
            px, py = pos[i]
            if px < cx - 0.5:
                bx, bw = px - 1.1, 1.15
            else:
                bx, bw = px, 1.15
            _rect(slide, bx, py, 1.15, 0.62, colors[i % len(colors)], rounded=True, radius=0.2)
            _textbox(slide, bx + 0.05, py + 0.12, 1.1, 0.4, _item_text(it), font, 10.5,
                     "FFFFFF", align=PP_ALIGN.CENTER)


# ================= 动画与切换：PowerPoint 官方参数表 =================
# 数据来源：以本机 PowerPoint 自动化逐一生成效果/切换后回读其 OOXML 得到
# （_ppt_probe/sweep*.py 生成、_ppt_probe/ref/sweep*.pptx 回读，方向/配方原件见
#   _ppt_probe/ref_raw2.txt），杜绝手写猜字段。
#
# _ANIM: 效果名 -> 官方 preset 元数据 + 行为配方
#   pid/sub = presetID / presetSubtype（presetClass 由入场/退场自动决定）
#   dur     = 效果时长(ms)
#   fx      = animEffect 滤镜文本（None 表示该效果不用滤镜，而用属性动画）
#   fxAttrs = animEffect 内部 cTn 的额外属性（如 decel 缓动）
#   anims   = 属性动画配方：p:anim 对 ppt_x/ppt_y/ppt_w/ppt_h/style.rotation 插值
#             v0/v1 为数字时写 <p:fltVal>，字符串时写 <p:strVal>；"#ppt_x" 表示"当前位置"引用
# 退场 = 入场配方镜像：起终点互换，并把 #ppt_x/#ppt_y 引用改写为 ppt_x/ppt_y
# （与实测 OUT_fly_bottom 完全一致）。未实测的方向/变体（zoom_out_slight、bounce、
# glide 等）刻意不入表，避免猜写出错误动画。
def _av(attr, v0, v1, **kw):
    """构造一条属性动画配方（p:anim）。"""
    d = {"attr": attr, "v0": v0, "v1": v1}
    d.update(kw)
    return d


_ANIM = {
    # ---- 滤镜类：set + animEffect ----
    "appear":       {"pid": 1,  "sub": 0,  "dur": 0},
    "fade":         {"pid": 10, "sub": 0,  "dur": 500,  "fx": "fade"},
    "blinds":       {"pid": 3,  "sub": 10, "dur": 500,  "fx": "blinds(horizontal)"},
    "box":          {"pid": 4,  "sub": 16, "dur": 2000, "fx": "box(in)"},
    "checkerboard": {"pid": 5,  "sub": 10, "dur": 500,  "fx": "checkerboard(across)"},
    "circle":       {"pid": 6,  "sub": 16, "dur": 2000, "fx": "circle(in)"},
    "diamond":      {"pid": 8,  "sub": 16, "dur": 2000, "fx": "diamond(in)"},
    "dissolve":     {"pid": 9,  "sub": 0,  "dur": 500,  "fx": "dissolve"},
    "plus":         {"pid": 13, "sub": 16, "dur": 2000, "fx": "plus(in)"},
    "randombar_h":  {"pid": 14, "sub": 10, "dur": 500,  "fx": "randombar(horizontal)"},
    "randombar_v":  {"pid": 14, "sub": 5,  "dur": 500,  "fx": "randombar(vertical)"},
    "split":        {"pid": 16, "sub": 21, "dur": 500,  "fx": "barn(inVertical)"},
    "strips_upleft":    {"pid": 18, "sub": 9, "dur": 500, "fx": "strips(upLeft)"},
    "strips_downright": {"pid": 18, "sub": 6, "dur": 500, "fx": "strips(downRight)"},
    "wedge":        {"pid": 20, "sub": 0,  "dur": 2000, "fx": "wedge"},
    "wheel":        {"pid": 21, "sub": 1,  "dur": 2000, "fx": "wheel(1)"},
    "wipe_down":    {"pid": 22, "sub": 4,  "dur": 500,  "fx": "wipe(down)"},
    "wipe_up":      {"pid": 22, "sub": 1,  "dur": 500,  "fx": "wipe(up)"},
    "wipe_left":    {"pid": 22, "sub": 8,  "dur": 500,  "fx": "wipe(left)"},
    "wipe_right":   {"pid": 22, "sub": 2,  "dur": 500,  "fx": "wipe(right)"},
    # ---- 位移/缩放类：set + p:anim 属性插值（无 animEffect；实测自 fly/zoom/float）----
    "fly_top":    {"pid": 2, "sub": 1, "dur": 500, "anims": [
        _av("ppt_x", "#ppt_x", "#ppt_x"), _av("ppt_y", "0-#ppt_h/2", "#ppt_y")]},
    "fly_bottom": {"pid": 2, "sub": 4, "dur": 500, "anims": [
        _av("ppt_x", "#ppt_x", "#ppt_x"), _av("ppt_y", "1+#ppt_h/2", "#ppt_y")]},
    "fly_left":   {"pid": 2, "sub": 8, "dur": 500, "anims": [
        _av("ppt_x", "0-#ppt_w/2", "#ppt_x"), _av("ppt_y", "#ppt_y", "#ppt_y")]},
    "fly_right":  {"pid": 2, "sub": 2, "dur": 500, "anims": [
        _av("ppt_x", "1+#ppt_w/2", "#ppt_x"), _av("ppt_y", "#ppt_y", "#ppt_y")]},
    "zoom":       {"pid": 23, "sub": 16, "dur": 500, "anims": [
        _av("ppt_w", 0, "#ppt_w"), _av("ppt_h", 0, "#ppt_h")]},
    "faded_zoom": {"pid": 53, "sub": 16, "dur": 500, "fx": "fade", "anims": [
        _av("ppt_w", 0, "#ppt_w"), _av("ppt_h", 0, "#ppt_h")]},
    "grow_turn":  {"pid": 31, "sub": 0, "dur": 1000, "fx": "fade", "anims": [
        _av("ppt_w", 0, "#ppt_w"), _av("ppt_h", 0, "#ppt_h"),
        _av("style.rotation", 90, 0)]},
    "pinwheel":   {"pid": 35, "sub": 0, "dur": 2000, "fx": "fade", "anims": [
        _av("style.rotation", 720, 0), _av("ppt_h", 0, "#ppt_h"),
        _av("ppt_w", 0, "#ppt_w")]},
    "swivel":     {"pid": 19, "sub": 10, "dur": 5000, "anims": [
        _av("ppt_w", 0, 1, fmla0="#ppt_w*sin(2.5*pi*$)"),
        _av("ppt_h", "#ppt_h", "#ppt_h")]},
    "float_up":   {"pid": 30, "sub": 0, "dur": 1000, "fx": "fade",
                   "fxAttrs": {"decel": 100000}, "anims": [
        _av("style.rotation", -90, 0, dur=800, decel=100000),
        _av("ppt_x", "#ppt_x+0.4", "#ppt_x-0.05", dur=800, decel=100000),
        _av("ppt_y", "#ppt_y-0.4", "#ppt_y+0.1", dur=800, decel=100000),
        _av("ppt_x", "#ppt_x-0.05", "#ppt_x", dur=200, delay=800, accel=100000),
        _av("ppt_y", "#ppt_y+0.1", "#ppt_y", dur=200, delay=800, accel=100000)]},
    "float_down": {"pid": 30, "sub": 0, "dur": 1000, "fx": "fade",
                   "fxAttrs": {"decel": 100000}, "anims": [
        _av("style.rotation", 90, 0, dur=800, decel=100000),
        _av("ppt_x", "#ppt_x+0.4", "#ppt_x-0.05", dur=800, decel=100000),
        _av("ppt_y", "#ppt_y+0.4", "#ppt_y-0.1", dur=800, decel=100000),
        _av("ppt_x", "#ppt_x-0.05", "#ppt_x", dur=200, delay=800, accel=100000),
        _av("ppt_y", "#ppt_y-0.1", "#ppt_y", dur=200, delay=800, accel=100000)]},
    "float_left":  {"pid": 30, "sub": 0, "dur": 1000, "fx": "fade",
                    "fxAttrs": {"decel": 100000}, "anims": [
        _av("ppt_x", "#ppt_x-0.4", "#ppt_x+0.05", dur=800, decel=100000),
        _av("ppt_y", "#ppt_y-0.12", "#ppt_y+0.04", dur=800, decel=100000),
        _av("ppt_x", "#ppt_x+0.05", "#ppt_x", dur=200, delay=800, accel=100000),
        _av("ppt_y", "#ppt_y+0.04", "#ppt_y", dur=200, delay=800, accel=100000)]},
    "float_right": {"pid": 30, "sub": 0, "dur": 1000, "fx": "fade",
                    "fxAttrs": {"decel": 100000}, "anims": [
        _av("ppt_x", "#ppt_x+0.4", "#ppt_x-0.05", dur=800, decel=100000),
        _av("ppt_y", "#ppt_y-0.12", "#ppt_y+0.04", dur=800, decel=100000),
        _av("ppt_x", "#ppt_x-0.05", "#ppt_x", dur=200, delay=800, accel=100000),
        _av("ppt_y", "#ppt_y+0.04", "#ppt_y", dur=200, delay=800, accel=100000)]},
}
_ANIM_ALIAS = {}
for _n in _ANIM:
    _ANIM_ALIAS[_n] = _n
_ANIM_ALIAS.update({
    "飞入": "fly_bottom", "飞入自下": "fly_bottom", "飞入自上": "fly_top",
    "飞入自左": "fly_left", "飞入自右": "fly_right", "fly": "fly_bottom",
    "fly_in": "fly_bottom", "淡入": "fade",
    "擦除": "wipe_down", "wipe": "wipe_down", "擦除自下": "wipe_down",
    "擦除自上": "wipe_up", "擦除自左": "wipe_left", "擦除自右": "wipe_right",
    "方盒": "box", "揭开": "box", "圆形扩展": "circle", "圆形": "circle", "光圈": "circle",
    "菱形": "diamond", "溶解": "dissolve", "加号": "plus",
    "棋盘": "checkerboard", "随机线条": "randombar_h", "randombar": "randombar_h",
    "百叶窗": "blinds", "楔入": "wedge", "风车": "wheel",
    "劈裂": "split", "条纹": "strips_upleft",
    "缩放": "zoom", "zoom_in": "zoom", "淡入缩放": "faded_zoom",
    "放大旋转": "grow_turn", "旋转": "swivel", "车轮": "pinwheel",
    "上浮": "float_up", "下沉": "float_down", "浮入自左": "float_left",
    "浮入自右": "float_right", "平滑浮入": "float_up", "出现": "appear",
})
# 默认轮换池：以"平滑、不刺眼"的效果为主（用户反馈需要更多流畅动画）
_ANIM_POOL = ["fade", "wipe_down", "wipe_right", "fly_bottom", "fly_left", "zoom",
              "faded_zoom", "dissolve", "float_up", "float_left", "split",
              "strips_upleft", "circle", "box", "grow_turn", "randombar_h",
              "blinds", "pinwheel"]

# _TRANS: 切换效果名 -> (p:transition 子元素 tag, 子元素 attrs)。全部为 PowerPoint 实测写法。
_TRANS = {
    "fade": ("fade", {}), "cut": ("cut", {}), "push": ("push", {}), "wipe": ("wipe", {}),
    "split": ("split", {}), "cover": ("cover", {}), "pull": ("pull", {}),
    "dissolve": ("dissolve", {}), "random": ("random", {}), "blinds": ("blinds", {}),
    "checker": ("checker", {}), "wheel": ("wheel", {}), "comb": ("comb", {}),
    "plus": ("plus", {}), "newsflash": ("newsflash", {}), "wedge": ("wedge", {}),
    "circle": ("circle", {}), "zoom": ("zoom", {}),
}
_TRANS_ALIAS = {}
for _n in _TRANS:
    _TRANS_ALIAS[_n] = _n
for _k, _v in {"淡入": "fade", "切入": "cut", "推入": "push", "擦除": "wipe",
               "劈裂": "split", "覆盖": "cover", "拉出": "pull", "溶解": "dissolve",
               "随机": "random", "百叶窗": "blinds", "棋盘": "checker", "风车": "wheel",
               "梳理": "comb", "加号": "plus", "新闻快报": "newsflash", "楔入": "wedge",
               "圆形": "circle", "缩放": "zoom"}.items():
    _TRANS_ALIAS[_k] = _v


def _exit_value(v):
    """退场配方的取值改写：数字原样；#ppt_w/#ppt_h 保留；其余 #ppt_ 引用去掉 #。

    依据实测：OUT_fly_bottom 写作 "1+ppt_h/2" / "ppt_y"（无 #），
    而 IN_fly_bottom 用 "1+#ppt_h/2" / "#ppt_y"；宽度高度引用则保留 #ppt_w。
    """
    if isinstance(v, bool) or isinstance(v, (int, float)):
        return v
    s = str(v)
    if s in ("#ppt_w", "#ppt_h"):
        return s
    return s.replace("#ppt_", "ppt_")


def _anim_preset(name, exit_mode=False):
    """把效果名(支持中英别名/None/none)解析为规范元数据 dict；未知一律回退 fade。

    返回 {"presetID", "presetClass", "presetSubtype", "dur", "fx", "fxAttrs",
          "anims", "mode", "total"}；exit_mode=True 时 presetClass=exit，
    并把配方镜像为退场（起点/终点互换 + ppt 引用改写），d 总时长 total 用于
    计算退场 set(hidden) 的 delay = total-1（实测真值）。
    """
    n = str(name or "").strip().lower()
    if not n or n in ("none", "off", "无"):
        return None
    key = _ANIM_ALIAS.get(n, n)
    spec = _ANIM.get(key) or _ANIM["fade"]
    anims = []
    for a in spec.get("anims") or ():
        b = dict(a)
        if not b.get("dur"):
            b["dur"] = spec["dur"]
        anims.append(b)
    if exit_mode:
        for a in anims:
            a["v0"], a["v1"] = _exit_value(a["v1"]), _exit_value(a["v0"])
            a["fmla0"] = None          # 公式型动画（swivel）退场不镜像公式，退化为数值插值
    total = [int(spec["dur"])]
    for a in anims:
        total.append(int(a.get("delay") or 0) + int(a.get("dur") or 0))
    return {"presetID": spec["pid"], "presetClass": "exit" if exit_mode else "entr",
            "presetSubtype": spec["sub"], "dur": spec["dur"], "fx": spec.get("fx"),
            "fxAttrs": dict(spec.get("fxAttrs") or {}), "anims": anims,
            "mode": "out" if exit_mode else "in", "total": max(total)}


# 触发方式：click=逐个点击（原有行为）；with=一次点击后同组级联（默认，最流畅）；
# after=上一动画之后自动衔接；本机 PowerPoint 实测写法见 ref_raw2.txt 的
# AFTER_fade / WITH_fade / AFTER_flyout（withEffect / afterEffect + stCond delay）。
_TRIG_ALIAS = {"click": "click", "点击": "click", "on_click": "click", "onclick": "click",
               "with": "with", "同时": "with", "级联": "with", "cascade": "with",
               "with_previous": "with", "after": "after", "之后": "after",
               "依次": "after", "after_previous": "after"}


def _norm_trigger(v):
    """触发方式归一化；缺省 with（一次点击后整页流畅级联）。"""
    return _TRIG_ALIAS.get(str(v or "").strip().lower()) or "with"


def _resolve_transition(name):
    """切换效果名 -> (子元素tag, attrs)；未知回退 fade。"""
    n = str(name or "").strip().lower()
    if not n or n in ("none", "无", "off"):
        return None
    key = _TRANS_ALIAS.get(n, n)
    return _TRANS.get(key) or _TRANS["fade"]


def _shape_bbox(shape):
    try:
        return (shape.left / 914400, shape.top / 914400,
                shape.width / 914400, shape.height / 914400)
    except (TypeError, AttributeError, ValueError):
        return (0.0, 0.0, 0.0, 0.0)


def _shape_text(shape):
    try:
        if shape.has_text_frame:
            return "".join(p.text for p in shape.text_frame.paragraphs)
    except Exception:
        pass
    return ""


def _shape_is_bg(shape, x, y, w, h):
    if w >= SW * 0.97 and h >= SH * 0.97 and x <= 0.05 and y <= 0.05:
        return True
    if (x < -0.5 or y < -0.5 or x + w > SW + 0.5 or y + h > SH + 0.5) and w * h > 4.0:
        return True
    return False


def _page_steps(slide, page_index, anim_on, effect_override=None, exit_effect=None):
    """规划本页动画：按「读序」逐元素出场；可选一次点击整体退场。

    · 顺序：标题（最上方宽文字块）→ 其余元素按「行带(0.85in) → 从左到右」排序；
      若某元素被某块非文字底板包含（卡片底框 + 卡内文字），则借用底板位置参与排序，
      底板先出场、文字紧随其后，修掉"文字先出现、底板后浮现"的顺序/排版错乱。
    · 级联：effect_override 可带 trigger=click/with/after 与 stagger(ms)（由
      style.anim_trigger / slide.anim.trigger 传入）。click=每元素一次点击（原行为）；
      with=一次点击后组内按 stagger 流畅级联（默认）；after=衔接式级联；
      max 控制每页最多动画元素数（默认 12）。
    · 退场：exit_effect 给出效果名时，本页入场元素按逆序整体退场（默认一次点击完成）。
    返回 [{spid, preset, mode, trig, delay}] 交给 _inject_timing。
    """
    if not anim_on:
        return []
    ev = effect_override if isinstance(effect_override, dict) else {}
    trigger = _norm_trigger(ev.get("trigger") or ev.get("anim_trigger"))
    try:
        stagger = max(0, min(3000, int(ev.get("stagger") if ev.get("stagger") is not None
                                    else 160)))
    except (TypeError, ValueError):
        stagger = 160
    exit_trigger = _norm_trigger(ev.get("exit_trigger")) if ev.get("exit_trigger") else \
        ("with" if trigger == "click" else trigger)
    try:
        max_steps = max(1, min(24, int(ev.get("max") or 12)))
    except (TypeError, ValueError):
        max_steps = 12

    shapes = []
    for sh in slide.shapes:
        x, y, w, h = _shape_bbox(sh)
        if w <= 0.01 or h <= 0.01 or _shape_is_bg(sh, x, y, w, h):
            continue
        if w < 0.12 or h < 0.12 or y >= 6.95 or x >= 12.6:
            continue
        shapes.append([sh, (x, y, w, h), bool(_shape_text(sh).strip())])
    if not shapes:
        return []

    # 标题：最上方的宽文字块
    title = None
    for it in sorted(shapes, key=lambda t: t[1][1]):
        if it[2] and it[1][1] <= 1.6 and it[1][3] >= 3.0:
            title = it[0]
            break

    def _anchor(bbox):
        """排序锚点：若被某块非文字底板包含，则借用底板位置（保证底板+文字相邻出场）。

        未被包含时，再兜底锚定到「紧贴其下方的大色块」：图表数值标签正好悬在所属
        柱体上方，若不借用柱体位置，数值会按自身 y 排到柱体之前，造成"数值先于
        柱体出现"的顺序混乱。仅当文字小块与下方图形 x 重叠且间距极小时才借用。"""
        bx, by, bw, bh = bbox
        cx, cy = bx + bw / 2, by + bh / 2
        best, best_area = None, None
        for sh2, (x2, y2, w2, h2), is_txt2 in shapes:
            if is_txt2 or ((w2, h2) == (bw, bh) and (x2, y2) == (bx, by)):
                continue
            if x2 - 0.06 <= cx <= x2 + w2 + 0.06 and y2 - 0.06 <= cy <= y2 + h2 + 0.06:
                area = w2 * h2
                if best is None or area < best_area:
                    best, best_area = (x2, y2, w2, h2), area
        if best is None:
            # 兜底：小块文字正下方紧邻的图形（图表数值标签 -> 柱体）
            for sh2, (x2, y2, w2, h2), is_txt2 in shapes:
                if is_txt2 or min(w2, h2) < 0.22:
                    continue
                if (x2 - 0.08 <= cx <= x2 + w2 + 0.08
                        and y2 - 0.25 <= by + bh <= y2 + 0.35):
                    area = w2 * h2
                    if best is None or area < best_area:
                        best, best_area = (x2, y2, w2, h2), area
        return best or bbox

    def _key(item):
        ax, ay, _w, _h = _anchor(item[1])
        return (int(max(0.0, ay - 0.5) / 0.85), round(ax, 2), 0 if not item[2] else 1)

    others = [it for it in shapes if it[0] is not title]
    others.sort(key=_key)
    ordered = ([it for it in shapes if it[0] is title] or []) + others
    ordered = ordered[:max_steps]

    def _kind_name(kind, role_shift=0):
        if ev.get(kind):
            return ev[kind]
        return _ANIM_POOL[(page_index + role_shift) % len(_ANIM_POOL)]

    def _role(it):
        if it[0] is title:
            return "title", 0
        if it[2]:
            return "text", 1
        return "graphics", 2

    def _build(seq, mode, trig, preset_of):
        """生成步骤：组内首个 click 触发，其余按 trigger 级联（with 叠加 / after 衔接）。"""
        out, elapsed = [], 0
        for i, it in enumerate(seq):
            preset = preset_of(it, i)
            if i == 0 or trig == "click":
                trig_i, delay = "click", 0
            elif trig == "after":
                trig_i, delay = "after", elapsed + stagger
            else:
                trig_i, delay = "with", i * stagger
            out.append({"spid": it[0].shape_id, "preset": preset, "mode": mode,
                        "trig": trig_i, "delay": delay})
            meta = _anim_preset(preset, exit_mode=(mode == "out")) or {}
            elapsed = delay + int(meta.get("total") or 0)
        return out

    steps = _build(ordered, "in", trigger, lambda it, i: _kind_name(*_role(it)))
    # 退场（可选）：按入场逆序整体退场（首元素点击触发、其余级联，一次点击清空页面）
    if exit_effect:
        pe = _anim_preset(exit_effect, exit_mode=True)
        if pe and steps:
            exits, elapsed = [], 0
            for i, s in enumerate(reversed(steps)):
                if i == 0 or exit_trigger == "click":
                    trig_i, delay = "click", 0
                elif exit_trigger == "after":
                    trig_i, delay = "after", elapsed + stagger
                else:
                    trig_i, delay = "with", i * stagger
                exits.append({"spid": s["spid"], "preset": exit_effect, "mode": "out",
                              "trig": trig_i, "delay": delay})
                elapsed = delay + int(pe.get("total") or 0)
            steps = steps + exits
    return steps


def _sub_elt(parent, tag, **attrs):
    from lxml import etree
    from pptx.oxml.ns import qn
    el = etree.SubElement(parent, qn(tag))
    for k, v in attrs.items():
        if v is not None:
            el.set(k if ":" not in k else qn(k), str(v))
    return el


def _inject_transition(slide, name):
    """写入 PowerPoint 兼容的 p:transition（随机名每次取创意池其一）。"""
    import random as _rnd
    if name in ("random", "随机"):
        name = _rnd.choice(["fade", "push", "wipe", "split", "dissolve", "circle"])
    spec = _resolve_transition(name)
    if not spec:
        return
    tag, attrs = spec
    trans = _sub_elt(slide._element, "p:transition", spd="med")
    _sub_elt(trans, "p:" + tag, **attrs)


def _inject_timing(slide, steps):
    """按 PowerPoint 官方编码写入元素动画时序（含 preset 元数据、触发分组、build 清单）。

    结构（与 PowerPoint 保存产物逐节点一致，避免打开触发"修复"）：
      p:timing > p:tnLst > p:par > p:cTn(tmRoot) > p:childTnLst
        > p:seq > p:cTn(mainSeq) > p:childTnLst
            {每个"点击组"} p:par > p:cTn(fill=hold) [stCond delay=indefinite]
                 > p:childTnLst
                    {组内首元素} p:par > p:cTn(fill=hold,delay=0)
                                 > p:par > p:cTn(preset*, nodeType=clickEffect, delay=0)
                    {组内级联元素} p:par > p:cTn(preset*, nodeType=withEffect|afterEffect,
                                                  delay=级联毫秒)
                 > 行为列表：p:set / p:animEffect / p:anim...
        > p:prevCondLst / p:nextCondLst
      p:timing > p:bldLst(参与动画的每个 shape 一个 p:bldP)

    退场元素（真值实测）：效果行为在前（animEffect transition="out" 或 p:anim），
    p:set(style.visibility=hidden, delay=效果总时长-1) 在最后，presetClass=exit。
    cTn id 从 3 起按文档顺序连续递增（跳号会被 PowerPoint 重排——即触发"修复"的根因）。
    """
    if not steps:
        return
    timing = _sub_elt(slide._element, "p:timing")
    tn_lst = _sub_elt(timing, "p:tnLst")
    par0 = _sub_elt(tn_lst, "p:par")
    _sub_elt(par0, "p:cTn", id=1, dur="indefinite", restart="never", nodeType="tmRoot")
    root_tn = par0[0]
    ch0 = _sub_elt(root_tn, "p:childTnLst")
    seq = _sub_elt(ch0, "p:seq", concurrent=1, nextAc="seek")
    seq_ctn = _sub_elt(seq, "p:cTn", id=2, dur="indefinite", nodeType="mainSeq")
    seq_ch = _sub_elt(seq_ctn, "p:childTnLst")

    counter = [3]

    def _nid():
        v = counter[0]
        counter[0] += 1
        return str(v)

    def _set_visibility(host, spid, val, delay):
        """p:set 可见性行为：入场 visible（delay=0）；退场 hidden（delay=总时长-1）。"""
        st_el = _sub_elt(host, "p:set")
        bh = _sub_elt(st_el, "p:cBhvr")
        bc = _sub_elt(bh, "p:cTn", id=_nid(), dur=1, fill="hold")
        _sub_elt(_sub_elt(bc, "p:stCondLst"), "p:cond", delay=delay)
        _sub_elt(_sub_elt(bh, "p:tgtEl"), "p:spTgt", spid=str(spid))
        anl = _sub_elt(bh, "p:attrNameLst")
        _sub_elt(anl, "p:attrName").text = "style.visibility"
        _sub_elt(_sub_elt(st_el, "p:to"), "p:strVal", val=val)

    def _anim_effect(host, spid, meta):
        """p:animEffect 滤镜行为（fade/wipe/fly 之外的滤镜类效果）。"""
        ef = _sub_elt(host, "p:animEffect",
                      transition="out" if meta.get("mode") == "out" else "in",
                      filter=meta["fx"])
        eb = _sub_elt(ef, "p:cBhvr")
        fx_attrs = meta.get("fxAttrs") or {}
        _sub_elt(eb, "p:cTn", id=_nid(), dur=meta.get("dur") or 500, **fx_attrs)
        _sub_elt(_sub_elt(eb, "p:tgtEl"), "p:spTgt", spid=str(spid))

    def _tav_value(parent, v):
        """tav 取值：数字 -> p:fltVal；字符串 -> p:strVal（#ppt_x 等引用走 strVal）。"""
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            _sub_elt(parent, "p:strVal", val=str(v))
        else:
            _sub_elt(parent, "p:fltVal", val=("%g" % v))

    def _attr_anim(host, spid, a, out):
        """p:anim 属性动画（fly/zoom/float 等位移缩放，官方用属性插值而非滤镜）。"""
        el = _sub_elt(host, "p:anim", calcmode="lin", valueType="num")
        bh = _sub_elt(el, "p:cBhvr", additive="base")
        at = {"id": _nid(), "dur": int(a.get("dur") or 500)}
        for k in ("decel", "accel", "tmFilter"):
            if a.get(k):
                at[k] = a[k]
        if not out or a.get("fill"):
            at["fill"] = "hold"      # 实测：入场 anim 带 fill=hold，退场默认不带
        ctn = _sub_elt(bh, "p:cTn", **at)
        if a.get("delay"):
            _sub_elt(_sub_elt(ctn, "p:stCondLst"), "p:cond", delay=int(a["delay"]))
        _sub_elt(_sub_elt(bh, "p:tgtEl"), "p:spTgt", spid=str(spid))
        anl = _sub_elt(bh, "p:attrNameLst")
        _sub_elt(anl, "p:attrName").text = a["attr"]
        tav = _sub_elt(el, "p:tavLst")
        _tav_value(_sub_elt(tav, "p:tav", tm=0, fmla=a.get("fmla0")), a["v0"])
        _tav_value(_sub_elt(tav, "p:tav", tm=100000), a["v1"])

    # ── 链(chain)/组(group) 结构严格对齐 PowerPoint 真值（_ppt_probe/ref 实测）：
    #   · 链 = 一次点击（一个 clickEffect 一步）：独占一条顶层 p:par，其 cTn 用
    #     cond delay="indefinite" 等点击；链内所有组共享这条 par。
    #   · 组 = 链内同一时刻起步的一批元素：组 cTn 的 cond delay = 本组在链内的
    #     相对起点（毫秒）；组内元素 cond delay 相对本组起点。
    #   · click 开新链；after 在同链内开新组（延迟 = 上一组结束时刻）；with 并入
    #     当前组（与同组元素同时起步）。
    #   实测样例（_ppt_probe/ref_raw2.txt 的 AFTER_fade / WITH_fade）：
    #     par/cTn(delay=indefinite)                    ← 链（一次点击）
    #       par/cTn(delay=0)   → cTn(preset,clickEffect, delay=0)  → set+animEffect
    #       par/cTn(delay=500) → cTn(preset,afterEffect, delay=200) → set+animEffect
    #                          + cTn(preset,withEffect,  delay=0)   → set+animEffect
    #   旧实现把每个"组"都写成顶层 delay=indefinite 的 par，等于每个元素都要点一次，
    #   且 after/with 级联节点被孤立在各自的点击里 —— 这正是"顺序错乱、后续元素
    #   不按编排播放、退场整段不生效"的根因。
    chains = []          # [[group, ...], ...]；每条链 = 一次点击，链内 = [step, ...]
    for st in steps:
        trig = str(st.get("trig") or "with")
        if not chains or trig == "click":
            chains.append([[st]])          # 新链：新的 clickEffect
        elif trig == "after":
            chains[-1].append([st])        # 新组：等上一组结束后自动衔接
        else:
            chains[-1][-1].append(st)      # 并入当前组：与该组元素同时起步

    for groups in chains:
        chain = _sub_elt(seq_ch, "p:par")
        cctn = _sub_elt(chain, "p:cTn", id=_nid(), fill="hold")
        _sub_elt(_sub_elt(cctn, "p:stCondLst"), "p:cond", delay="indefinite")
        cch = _sub_elt(cctn, "p:childTnLst")
        cursor = 0                         # 链内累计毫秒：下一组的组延迟
        for group in groups:
            outer = _sub_elt(cch, "p:par")
            ctn = _sub_elt(outer, "p:cTn", id=_nid(), fill="hold")
            _sub_elt(_sub_elt(ctn, "p:stCondLst"), "p:cond", delay=cursor)
            gch = _sub_elt(ctn, "p:childTnLst")
            g_end = cursor
            for st in group:
                meta = st.get("preset")
                if not isinstance(meta, dict):
                    meta = _anim_preset(meta, exit_mode=(st.get("mode") == "out")) \
                        or _anim_preset("fade")
                spid, out = st["spid"], meta.get("mode") == "out"
                trig = str(st.get("trig") or "with")
                node = {"click": "clickEffect", "after": "afterEffect"}.get(
                    trig, "withEffect")
                # 组内偏移 = 步骤延迟 - 本组起点（clickEffect 恒为 0，照实测真值）
                delay = 0 if node == "clickEffect" else \
                    max(0, int(st.get("delay") or 0) - cursor)
                p3 = _sub_elt(gch, "p:par")
                c3 = _sub_elt(p3, "p:cTn", id=_nid(),
                              presetID=meta["presetID"], presetClass=meta["presetClass"],
                              presetSubtype=meta["presetSubtype"], fill="hold", grpId=0,
                              nodeType=node)
                _sub_elt(_sub_elt(c3, "p:stCondLst"), "p:cond", delay=delay)
                c3c = _sub_elt(c3, "p:childTnLst")
                # 行为顺序（实测真值，勿改）：入场 set(visible) 在前；退场 animEffect/
                # anim 在前、set(hidden, delay=总时长-1) 在后 —— 顺序反了退场会被丢弃。
                if not out:
                    _set_visibility(c3c, spid, "visible", 0)
                if meta.get("fx"):
                    _anim_effect(c3c, spid, meta)
                for a in meta.get("anims") or ():
                    _attr_anim(c3c, spid, a, out)
                if out:
                    _set_visibility(c3c, spid, "hidden",
                                    max(int(meta.get("total") or 0) - 1, 0))
                g_end = max(g_end, cursor + delay + int(meta.get("total") or 0))
            cursor = g_end                 # 下一组从本组结束时刻起步（after 语义）

    # prev/next 条件（照官方：仅 onPrev / onNext）
    pc = _sub_elt(seq, "p:prevCondLst")
    _sub_elt(_sub_elt(_sub_elt(pc, "p:cond", evt="onPrev", delay=0), "p:tgtEl"), "p:sldTgt")
    nc = _sub_elt(seq, "p:nextCondLst")
    _sub_elt(_sub_elt(_sub_elt(nc, "p:cond", evt="onNext", delay=0), "p:tgtEl"), "p:sldTgt")
    # build 清单（动画对象登记，缺失会被 PowerPoint 忽略/修复）
    bld = _sub_elt(timing, "p:bldLst")
    seen = set()
    for st in steps:
        if st["spid"] not in seen:
            seen.add(st["spid"])
            _sub_elt(bld, "p:bldP", spid=str(st["spid"]), grpId=0)


def _block_natural_h(kind, spec) -> float:
    """结构化块的自然高度（英寸）：表格按行数折算，图表/图形用整块高度。"""
    if kind == "table":
        rows = len((spec or {}).get("rows") or []) + 1
        return 0.34 * rows + 0.85
    return 5.1


def _draw_block(slide, kind, spec, t, font, x, y, w, h):
    """按类型分发结构化块渲染（表格/图表/图形），统一入口便于同页堆叠。"""
    if kind == "table":
        return _render_table(slide, spec, t, font, x, y, w, max_h=h)
    if kind == "chart":
        return _draw_chart(slide, spec, t, font, x, y, w, h)
    return _draw_diagram(slide, spec, t, font, x, y, w, h)


def build_pptx(path, title="", slides=None, style=None, workdir="") -> dict:
    """生成 PPT 文稿。slides 为列表（每项 {title, ...}），style 见模块 docstring。"""
    t = utils.resolve_style(style)
    s = style or {}
    font = t["font"]
    body_size = theme.to_int(s.get("body_size"), 17, 10, 40)
    title_size = theme.to_int(s.get("title_size"), 25, 12, 60)
    accent = theme.hex_color(s.get("accent"), t["accent"])
    accent = accent or t["accent"]
    page_bg = theme.hex_color(s.get("bg_color"), "")
    if not page_bg:
        page_bg = t["bg"] if t["theme_name"] == "dark" else "FFFFFF"
    title_color = theme.hex_color(s.get("title_color"), t["primary"])
    footer_txt = str(s.get("footer") or "")
    bullet_fmt = _BULLET.get(str(s.get("bullet_style") or "dot").lower(), "•  ")
    transition = str(s.get("transition") or "fade").lower()

    prs = Presentation()
    prs.slide_width = Inches(SW)
    prs.slide_height = Inches(SH)
    blank = prs.slide_layouts[6]

    missing = []
    # 动画开关：style.animation=True(默认)/False/off；可全局统一效果 animation_effect
    style_anim = s.get("animation")
    if isinstance(style_anim, str):
        style_anim = style_anim.strip().lower() not in ("0", "false", "off", "无", "none", "no", "不")
    if style_anim is None:
        style_anim = True
    master_effect = s.get("animation_effect") or s.get("effect")
    # 动画节奏全局配置：style.anim_trigger=click/with/after（默认 with=一次点击后流畅级联）、
    # style.anim_stagger=组内级联间隔(ms，默认160)、style.exit_effect=每页整体退场效果名、
    # style.exit_trigger=退场触发方式（默认跟随 anim_trigger）
    anim_extra = {}
    if s.get("anim_trigger"):
        anim_extra["trigger"] = s.get("anim_trigger")
    if s.get("anim_stagger") is not None:
        anim_extra["stagger"] = s.get("anim_stagger")
    if s.get("exit_effect"):
        anim_extra["exit_effect"] = s.get("exit_effect")
    if s.get("exit_trigger"):
        anim_extra["exit_trigger"] = s.get("exit_trigger")

    cover_added = bool((title or "").strip())
    if cover_added:
        s0 = prs.slides.add_slide(blank)
        _inject_transition(s0, transition)
        _cover(s0, prs, str(title), t, style, page="1")
        # 封面仅动文字（标题/副题/作者日期），跳过装饰
        cov_ov = {"title": master_effect, "text": master_effect, "graphics": master_effect} \
            if master_effect else {}
        cov_ov.update(anim_extra)
        _inject_timing(s0, _page_steps(s0, 0, style_anim, cov_ov, cov_ov.get("exit_effect")))

    # ---- 内容页 ----
    for i, item in enumerate(slides or [], 1):
        if not isinstance(item, dict):
            continue
        slide = prs.slides.add_slide(blank)
        _inject_transition(slide, transition)
        # ---- 本页动画配置 ----
        item_anim = item.get("anim", item.get("animation"))
        page_anim_on = style_anim
        eov = dict(anim_extra)                      # 全局节奏（trigger/stagger）
        exit_eff = anim_extra.get("exit_effect")    # 全局每页整体退场（页级可覆盖）
        if isinstance(item_anim, dict):
            page_anim_on = item_anim.get("enabled", True) is not False
            eov.update({k: v for k, v in item_anim.items() if k in (
                "title", "text", "graphics", "trigger", "anim_trigger", "stagger",
                "exit_trigger", "max")})
            exit_eff = item_anim.get("exit", item_anim.get("exit_effect", exit_eff))
        elif item_anim is False or (isinstance(item_anim, str) and item_anim.strip().lower()
                                    in ("off", "无", "false", "0", "none", "no", "不")):
            page_anim_on = False
        elif isinstance(item_anim, str) and item_anim.strip():
            page_anim_on = True
            eov.update({"title": item_anim, "text": item_anim, "graphics": item_anim})
        if master_effect:
            merged = {"title": master_effect, "text": master_effect, "graphics": master_effect}
            merged.update(eov)
            eov = merged
        page_idx = i - 1
        pg_bg = theme.hex_color(item.get("bg_color"), page_bg)
        pg_ink = t["ink"]
        if pg_bg.upper() != "FFFFFF":
            _rect(slide, 0, 0, SW, SH, pg_bg)
            if _is_dark(pg_bg):
                pg_ink = theme.lighten("FFFFFF", 0.12)
        # 深色页：整页正文/表格/图表统一使用浅色副本主题，避免深底灰字不可读
        page_t = t
        if pg_bg.upper() != "FFFFFF" and _is_dark(pg_bg):
            page_t = dict(t, ink=pg_ink, soft=theme.lighten("FFFFFF", 0.62),
                          bg=theme.lighten(pg_bg, 0.42))
        # 底色与正文色过于接近（如深色主题配白底）时改用可读色，避免整页文字不可见
        if abs(_lum(page_t["ink"]) - _lum(pg_bg)) < 90:
            page_t = dict(page_t, ink=_ink_on(pg_bg))
        pg_title = _readable(theme.hex_color(item.get("title_color"), title_color),
                             pg_bg, t["primary"])
        layout = str(item.get("layout") or "").lower()
        # 页码 = 绝对页序（封面为 1），与 HTML 的 slidenum "n / total" 保持一致
        _add_titlebar(slide, str(item.get("title") or ""), accent,
                      _readable(pg_ink, pg_bg, _ink_on(pg_bg)), font,
                      title_size, footer_txt=footer_txt, page=str(i + 1 if cover_added else i))

        bullets = [b for b in (item.get("bullets") or []) if str(b).strip()]

        # two_col 两栏布局
        if layout == "two_col":
            cols = item.get("columns")
            if not isinstance(cols, list) or not cols:
                half = (len(bullets) + 1) // 2
                cols = [{"title": "", "bullets": bullets[:half]},
                        {"title": "", "bullets": bullets[half:]}]
            cw = (CW - 0.5) / 2
            for ci, col in enumerate(cols[:2]):
                if not isinstance(col, dict):
                    continue
                bx = MX + ci * (cw + 0.5)
                _rect(slide, bx, BT_TOP - 0.05, cw, 5.0, page_t["bg"], rounded=True, radius=0.05)
                _rect(slide, bx, BT_TOP + 0.05, cw, 0.07, accent if ci == 0 else page_t["primary"])
                cy = BT_TOP + 0.35
                ctitle = str(col.get("title") or "")
                if ctitle:
                    _textbox(slide, bx + 0.3, cy, cw - 0.6, 0.4, ctitle, font, 17,
                             page_t["primary"], bold=True)
                    cy += 0.55
                _render_bullets(slide, col.get("bullets") or [], bx + 0.3, cy,
                                cw - 0.6, BT_BOT - cy, page_t, max(14, body_size - 2),
                                bullet_fmt)
            _inject_timing(slide, _page_steps(slide, page_idx, page_anim_on, eov, exit_eff))
            continue

        # center_highlight 居中大字强调页
        if layout == "center_highlight":
            hl = str(item.get("highlight") or "").strip() or (bullets[0] if bullets else "")
            if hl:
                _textbox(slide, MX + 0.5, BT_TOP + 0.6, CW - 1.0, 1.8, hl, font, 33,
                         pg_ink, bold=True, align=PP_ALIGN.CENTER)
            rest = bullets[1:] if (str(item.get("highlight") or "").strip() or bullets) else bullets
            if rest:
                _render_bullets(slide, rest, MX, 4.3, CW, 2.6, page_t, max(14, body_size - 1),
                                bullet_fmt)
            _inject_timing(slide, _page_steps(slide, page_idx, page_anim_on, eov, exit_eff))
            continue

        # hero_stats 大数字指标页：stats=[{value,label,color}]，2-4 张大数字卡
        if layout == "hero_stats":
            stats = [s for s in (item.get("stats") or []) if isinstance(s, dict)]
            if stats:
                n = len(stats[:4])
                gap = 0.3
                cw = (CW - gap * (n - 1)) / n
                top = BT_TOP + 1.1
                for i, s in enumerate(stats[:4]):
                    color = theme.hex_color(s.get("color"), t["chart"][i % len(t["chart"])])
                    cx = MX + i * (cw + gap)
                    _rect(slide, cx, top, cw, 2.7, theme.lighten(color, 0.88), rounded=True,
                          radius=0.05)
                    _rect(slide, cx + 0.3, top + 0.28, 0.62, 0.09, color)
                    value = str(s.get("value") or "")
                    label = str(s.get("label") or "")
                    vbox = slide.shapes.add_textbox(Inches(cx + 0.25), Inches(top + 0.7),
                                                    Inches(cw - 0.5), Inches(1.2))
                    vp = vbox.text_frame.paragraphs[0]
                    vp.alignment = PP_ALIGN.CENTER
                    vr = vp.add_run()
                    vr.text = value
                    _font(vr, font, 34, bold=True, color=color)
                    if label:
                        lb = slide.shapes.add_textbox(Inches(cx + 0.25), Inches(top + 2.0),
                                                      Inches(cw - 0.5), Inches(0.4))
                        lp = lb.text_frame.paragraphs[0]
                        lp.alignment = PP_ALIGN.CENTER
                        lr = lp.add_run()
                        lr.text = label
                        _font(lr, font, 13, color=page_t["soft"])
                if bullets:
                    _render_bullets(slide, bullets, MX, top + 3.1, CW, 2.6, page_t,
                                    max(13, body_size - 2), bullet_fmt)
            else:
                _render_bullets(slide, bullets, MX, BT_TOP, CW, BT_BOT - BT_TOP, page_t,
                                body_size, bullet_fmt)
            _inject_timing(slide, _page_steps(slide, page_idx, page_anim_on, eov, exit_eff))
            continue

        # 图文分栏（image + bullets 并排）
        img = item.get("image") or ""
        img_is_file = (isinstance(img, dict) and str(img.get("path") or "").strip()) \
            or (isinstance(img, str) and img.strip())
        if layout in ("left_image", "right_image") and img_is_file:
            iw, ih = 5.6, 5.0
            ix = MX if layout == "left_image" else SW - MX - iw
            bx = MX if layout == "right_image" else MX + iw + 0.6
            bw = CW - iw - 0.6
            fit = _place_pic(slide, img.get("path") if isinstance(img, dict) else img,
                             ix, BT_TOP + 0.25, iw, ih, workdir)
            if fit is None:
                missing.append(str(img.get("path") if isinstance(img, dict) else img))
            _render_bullets(slide, bullets, bx, BT_TOP, bw, BT_BOT - BT_TOP, page_t,
                            max(14, body_size - 1), bullet_fmt)
            _inject_timing(slide, _page_steps(slide, page_idx, page_anim_on, eov, exit_eff))
            continue

        # cards 区（要点接在卡片下方，按估算高度收口：既不压字也不出页）
        if item.get("cards"):
            cy = BT_TOP - 0.15
            card_body = max(13, body_size - 2)
            need = _est_text_h(bullets, card_body, CW)
            avail = BT_BOT - cy - (need + 0.35 if need else 0.0)
            used = _render_cards(slide, item.get("cards"), page_t, font,
                                 y=cy, x=MX, w=CW, max_h=max(2.0, min(4.4, avail)))
            if bullets:
                y2 = cy + used + 0.05
                _render_bullets(slide, bullets, MX, y2, CW,
                                min(need, max(0.6, BT_BOT - y2)), page_t,
                                card_body, bullet_fmt)
            _inject_timing(slide, _page_steps(slide, page_idx, page_anim_on, eov, exit_eff))
            continue

        # 结构化块（table/chart/diagram）：可多个同页纵向堆叠，右侧留要点栏
        blocks = [(k, item.get(k)) for k in ("table", "chart", "diagram") if item.get(k)]
        if blocks:
            bz_w, bz_h = (CW, 5.2) if not bullets else (8.15, 5.1)
            zone_y = BT_TOP - 0.15
            gaps = 0.15 * (len(blocks) - 1)
            nat = [_block_natural_h(k, s) for k, s in blocks]
            total = sum(nat) or 1.0
            scale = min(1.0, (bz_h - gaps) / total)
            ys = zone_y
            for (kind, spec), nh in zip(blocks, nat):
                bh = max(1.5, nh * scale)
                _draw_block(slide, kind, spec, page_t, font, MX, ys, bz_w, bh)
                ys += bh + 0.15
            if bullets:
                _render_bullets(slide, bullets, MX + bz_w + 0.55, BT_TOP + 0.1,
                                13.333 - (MX + bz_w + 0.55) - MX, 5.0, page_t,
                                max(13, body_size - 2), bullet_fmt, title_color=pg_title)
            _inject_timing(slide, _page_steps(slide, page_idx, page_anim_on, eov, exit_eff))
            continue

        # 纯要点页（可能有底部插图 / 艺术字）
        foot_img = img_is_file and layout not in ("left_image", "right_image")
        wa = item.get("wordart") or {}
        wa_text = str(wa.get("text") or "").strip() if isinstance(wa, dict) else ""
        wa_h = 1.35 if wa_text else 0.0
        bullets_bottom = BT_BOT - (1.7 if foot_img else 0.0) - wa_h
        bh = 0.0
        if bullets:
            room = max(1.2, bullets_bottom - BT_TOP)
            est = _est_text_h(bullets, body_size, CW)
            bh = max(0.8, min(est, room))
            _render_bullets(slide, bullets, MX, BT_TOP, CW, bh, page_t, body_size, bullet_fmt)
        # 底部插图
        if foot_img:
            iw2, ih2 = CW, 1.5
            fit = _place_pic(slide, img.get("path") if isinstance(img, dict) else img,
                             MX, BT_BOT - 0.15 - ih2 + 0.15, iw2, ih2, workdir)
            if fit is None:
                missing.append(str(img.get("path") if isinstance(img, dict) else img))
        # 本页艺术字：排在要点实际占用高度之后（取其下沿或理想位置，谁更低用谁），
        # 避免与正文重叠压字；同时不越出正文安全区
        if wa_text:
            wy = max(BT_TOP + bh, min(BT_TOP + (_est_text_h(bullets, body_size, CW) if bullets
                                                else 0.0) + 0.18, BT_BOT - wa_h))
            _textbox(slide, MX, wy, CW, wa_h - 0.1, wa_text, font,
                     theme.to_int(wa.get("size"), 40, 12, 90),
                     _readable(theme.hex_color(wa.get("color"), accent), pg_bg, accent),
                     bold=True, align=PP_ALIGN.CENTER)
        _inject_timing(slide, _page_steps(slide, page_idx, page_anim_on, eov, exit_eff))

    prs.save(str(utils.resolve_path(path, workdir)))
    msg = f"已生成 PPT：{utils.resolve_path(path, workdir)}"
    if missing:
        msg += f"（{len(missing)} 张图片不存在已跳过）"
    return {"text": msg, "images": []}
