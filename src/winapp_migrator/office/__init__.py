# -*- coding: utf-8 -*-
"""office —— zhuzhu Copilot 办公套件高质量生成 / 读取 / 编辑 / 保真预览包。

职责边界：
- theme:        设计令牌（主题色板、颜色与数值工具）
- utils:        style 合并解析、路径解析、图片适配
- docx_builder: Word 文档生成（封面/目录/表格/页眉页脚）
- pptx_builder: PPT 生成（封面/统一排版/卡片/图表/图形/两栏布局/动画时序）
- xlsx_builder: Excel 生成（合计/图表/数字格式/专业表样/条件格式）
- beautify:     已有三件套的一键精修
- reader:       真实读取（Word/PPT/Excel/PDF → 结构化 Markdown，供模型定位锚点）
- editor:       真实编辑（word/ppt/excel 的 op 化增删改，单条失败不中断）
- preview:      保真 HTML 预览（样式/图片/图标/图表/条件格式/动画 + PPT 放映接口）

对外保持与旧 agent_tools.create_*/beautify_* 一致的参数契约，仅追加可选的 workdir
参数（相对路径解析基准），由 core.agent_tools 在工具执行时注入。
"""

from .beautify import beautify_docx, beautify_pptx, beautify_xlsx
from .compat import render_office_compat_html
from .docx_builder import build_docx
from .editor import edit_document
from .pptx_builder import build_pptx
from .preview import extract_slides, office_meta, render_office_html
from .reader import READ_EXTS, read_document
from .xlsx_builder import build_xlsx

__all__ = ["build_docx", "build_pptx", "build_xlsx",
           "beautify_docx", "beautify_pptx", "beautify_xlsx",
           "read_document", "edit_document", "render_office_html",
           "render_office_compat_html",
           "extract_slides", "office_meta", "READ_EXTS"]
