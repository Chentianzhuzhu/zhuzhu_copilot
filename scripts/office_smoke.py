# -*- coding: utf-8 -*-
"""office.reader / preview / editor 冒烟验证：对真实文档跑一遍读-预览-编辑-回读。

用法：python scripts/office_smoke.py [文档路径...]
不带参数时用桌面上的真实文档（存在才测）。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from winapp_migrator.office import editor, preview, reader  # noqa: E402

DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")
SAMPLE = ["产品介绍PPT.pptx", "大学毕业感言.pptx", "学生暑假满意度评价表.docx",
          "家庭收支明细表.xlsx", "_customer_data.xlsx"]


def probe(path):
    print(f"\n===== {os.path.basename(path)} =====")
    res = reader.read_document(path)
    meta = res.get("meta") or {}
    print(f"[read] {len(res['text'])} 字符  meta={ {k: v for k, v in meta.items() if k != 'anims_by_slide'} }")
    assert res["text"].strip(), "read_document 返回空文本"
    html = preview.render_office_html(path)
    print(f"[preview] {len(html or '')} 字符 HTML")
    assert html and "<!DOCTYPE html>" in html, "render_office_html 未产出完整 HTML"
    if meta.get("ext") in ("pptx", "pptm"):
        slides = preview.extract_slides(path)
        anims = sum(len(s["anims"]) for s in slides)
        print(f"[slides] {len(slides)} 页，动画条目 {anims}")
        assert len(slides) == meta.get("slides"), "extract_slides 页数与 meta 不一致"


def roundtrip(tmp):
    """编辑闭环：生成 → 读 → 改 → 回读断言"""
    from winapp_migrator.office import docx_builder, pptx_builder, xlsx_builder
    d = os.path.join(tmp, "t.docx")
    p = os.path.join(tmp, "t.pptx")
    x = os.path.join(tmp, "t.xlsx")
    docx_builder.build_docx(d, "测试文档", ["# 一级标题", "正文内容", "| A | B |", "| 1 | 2 |"], style={})
    pptx_builder.build_pptx(p, "测试演示", [
        {"title": "第一页", "bullets": ["要点一", "要点二"]},
        {"title": "第二页", "bullets": ["要点三"], "cards": [{"title": "卡片", "desc": "说明"}]},
    ], style={})
    xlsx_builder.build_xlsx(x, [{"name": "数据", "rows": [["名称", "数量"], ["甲", 1], ["乙", 2]]}], style={})

    print(f"\n===== roundtrip docx =====")
    r1 = reader.read_document(d)
    assert "一级标题" in r1["text"] and "| A | B |" in r1["text"], r1["text"][:400]
    e1 = editor.edit_document(d, [
        {"op": "replace_text", "find": "正文内容", "replace": "已修改正文"},
        {"op": "append_paragraph", "text": "## 追加的小节"},
        {"op": "set_header", "text": "页眉文字"},
        {"op": "append_paragraph", "text": "   "},
    ])
    print(e1["text"])
    r2 = reader.read_document(d)
    assert "已修改正文" in r2["text"], "replace_text 未生效"
    assert "追加的小节" in r2["text"], "append_paragraph 未生效"
    assert "页眉文字" in r2["text"], "set_header 未生效"

    print(f"\n===== roundtrip pptx =====")
    r3 = reader.read_document(p)
    assert "要点一" in r3["text"], r3["text"][:400]
    e2 = editor.edit_document(p, [
        {"op": "set_text", "index": 1, "shape": 0, "text": "改后的标题"},
        {"op": "set_notes", "index": 1, "text": "讲解备注"},
        {"op": "add_slide", "title": "新页", "bullets": ["新要点"]},
        {"op": "duplicate_slide", "index": 1},
        {"op": "set_bg_color", "index": 2, "color": "1F3A5F"},
        {"op": "set_animations", "index": 1,
         "effects": [{"shape": "all", "effect": "fade", "trigger": "with", "delay": 0}]},
    ])
    print(e2["text"])
    assert e2["meta"]["failed"] == 0, e2["text"]
    r4 = reader.read_document(p)
    assert "改后的标题" in r4["text"], "set_text 未生效"
    assert "讲解备注" in r4["text"], "set_notes 未生效"
    assert r4["meta"]["slides"] == r3["meta"]["slides"] + 2, \
        f"页数应为 {r3['meta']['slides'] + 2}（原+新增+复制），实际 {r4['meta']['slides']}"
    assert r4["meta"]["anims"] > 0, "set_animations 未写入动画"
    html = preview.render_office_html(p)
    assert "data-anim" in html and "__deckStep" in html, "放映接口缺失"
    assert "改后的标题" in html, "保真 HTML 未包含修改后文字"

    print(f"\n===== roundtrip xlsx =====")
    r5 = reader.read_document(x)
    assert "甲" in r5["text"], r5["text"][:300]
    e3 = editor.edit_document(x, [
        {"op": "set_cell", "sheet": "数据", "cell": "B2", "value": 99},
        {"op": "append_row", "sheet": "数据", "values": ["丙", 3]},
        {"op": "set_style", "sheet": "数据", "range": "A1:B1",
         "style": {"bold": True, "fill": "1F3864", "color": "FFFFFF"}},
        {"op": "merge_cells", "sheet": "数据", "range": "D1:E1"},
        {"op": "set_column_width", "sheet": "数据", "column": "A", "width": 18},
        {"op": "add_condition", "sheet": "数据", "range": "B2:B4", "type": "data_bar"},
    ])
    print(e3["text"])
    assert e3["meta"]["failed"] == 0, e3["text"]
    r6 = reader.read_document(x)
    assert "99" in r6["text"] and "丙" in r6["text"], "xlsx 编辑未生效"
    html2 = preview.render_office_html(x)
    assert "<colgroup>" in html2 and "tabs" in html2, "xlsx 保真渲染结构缺失"


if __name__ == "__main__":
    args = sys.argv[1:]
    targets = args or [os.path.join(DESKTOP, n) for n in SAMPLE]
    for t in targets:
        if os.path.isfile(t):
            probe(t)
    with tempfile.TemporaryDirectory() as tmp:
        roundtrip(tmp)
    print("\n[SMOKE] 全部通过")
