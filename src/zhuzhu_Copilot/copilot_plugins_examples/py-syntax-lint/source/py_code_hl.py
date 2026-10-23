"""Python 代码块语法高亮 + 语法纠错（Copilot 插件 py-syntax-lint）。

接入方式（源码级插入，不改动原有代码行）：
  · 插件在 `agent_panel` 模块末尾追加一个 `_md_to_html` 包装函数，调用
    `highlight_html()` 把已生成的 `<pre>` 代码块重新着色；
  · 插件在 `agent_tools` 里登记工具 `check_python_syntax`，调用
    `check_python()` 给出语法错误位置与原因——只做 ast 解析，**不执行用户代码**。

本文件是插件 `source/` 下的可读副本；插件实际执行的是 source_ops.json 里
create_file 的 content（两者内容一致）。
"""

import ast
import html as _html
import re

#: Python 关键字（着色用；True/False/None 走常量色）
_KEYWORDS = frozenset((
    "and", "as", "assert", "async", "await", "break", "class", "continue",
    "def", "del", "elif", "else", "except", "finally", "for", "from",
    "global", "if", "import", "in", "is", "lambda", "nonlocal", "not", "or",
    "pass", "raise", "return", "try", "while", "with", "yield", "match", "case",
))
_CONSTS = frozenset(("True", "False", "None", "self", "cls"))

# 着色（与面板代码块底色配套的柔和色，深浅主题都可读）
_C_KW = "#7C3AED"      # 关键字：紫
_C_STR = "#0E7C66"     # 字符串：青绿
_C_CMT = "#8A8F98"     # 注释：灰
_C_NUM = "#B45309"     # 数字：琥珀
_C_FN = "#1D4ED8"      # 函数/类名：蓝
_C_OP = "#B91C1C"      # 运算符：红

# 顺序敏感：注释 → 字符串（含三引号/前缀）→ 数字 → 标识符 → 运算符
_TOKEN_RE = re.compile(
    r"(?P<cmt>#[^\n]*)"
    r"|(?P<str>[rbfuRBFU]{0,2}(?:'{3}[\s\S]*?'{3}|\"{3}[\s\S]*?\"{3}"
    r"|'(?:\\[\s\S]|[^'\\\n])*'|\"(?:\\[\s\S]|[^\"\\\n])*\"))"
    r"|(?P<num>\b(?:0[xXbBoO][0-9a-fA-F_]+|\d[\d_]*(?:\.\d*)?(?:[eE][+-]?\d+)?)\b)"
    r"|(?P<name>[A-Za-z_]\w*)"
    r"|(?P<op>[+\-*/%=<>!&|^~@]+)"
)


def _esc(text: str) -> str:
    """HTML 转义（不转引号：代码块内单双引号保持原样更易读）。"""
    return _html.escape(str(text), quote=False)


def highlight_code(code: str, lang: str = "python") -> str:
    """把纯文本代码着色为 HTML 片段（非 Python 语言只做转义）。"""
    text = str(code or "")
    if lang and not str(lang).lower().startswith(("py", "python")):
        return _esc(text)
    out = []
    pos = 0
    prev_word = ""
    for m in _TOKEN_RE.finditer(text):
        out.append(_esc(text[pos:m.start()]))
        tok = m.group(0)
        kind = m.lastgroup
        if kind == "cmt":
            out.append('<span style="color:%s;font-style:italic">%s</span>'
                       % (_C_CMT, _esc(tok)))
        elif kind == "str":
            out.append('<span style="color:%s">%s</span>' % (_C_STR, _esc(tok)))
        elif kind == "num":
            out.append('<span style="color:%s">%s</span>' % (_C_NUM, _esc(tok)))
        elif kind == "op":
            out.append('<span style="color:%s">%s</span>' % (_C_OP, _esc(tok)))
        else:
            if tok in _KEYWORDS:
                out.append('<span style="color:%s;font-weight:600">%s</span>'
                           % (_C_KW, tok))
            elif tok in _CONSTS:
                out.append('<span style="color:%s">%s</span>' % (_C_NUM, tok))
            elif prev_word in ("def", "class") or text[m.end():m.end() + 1] == "(":
                out.append('<span style="color:%s;font-weight:600">%s</span>'
                           % (_C_FN, tok))
            else:
                out.append(_esc(tok))
            prev_word = tok
        pos = m.end()
    out.append(_esc(text[pos:]))
    return "".join(out)


#: 匹配 _md_to_html 产出的代码块：<pre style='...'>…</pre>
_PRE_RE = re.compile(r"(<pre\b[^>]*>)([\s\S]*?)(</pre>)", re.IGNORECASE)


def highlight_html(html_text: str) -> str:
    """给整篇正文 HTML 里的代码块着色（Python 代码才处理，其余原样返回）。"""
    src = str(html_text or "")
    if "<pre" not in src.lower():
        return src

    def _repl(m):
        head, body, tail = m.group(1), m.group(2), m.group(3)
        raw = _html.unescape(body)
        colored = highlight_code(raw, "python")
        if colored == _esc(raw):
            return m.group(0)      # 没有可着色 token（非 Python 片段）→ 原样
        return head + colored + tail

    try:
        return _PRE_RE.sub(_repl, src)
    except Exception:
        return src


def check_python(code: str) -> list:
    """语法检查：返回 [{line, col, message}]（空列表 = 通过）。不执行代码。"""
    text = str(code or "")
    if not text.strip():
        return []
    try:
        ast.parse(text)
        return []
    except SyntaxError as e:
        return [{"line": int(e.lineno or 0), "col": int(e.offset or 0),
                 "message": str(e.msg or e)}]
    except (ValueError, MemoryError) as e:      # 空字节/超长等非语法错误
        return [{"line": 0, "col": 0, "message": str(e)}]


def format_issues(issues: list) -> str:
    """把 check_python 的结果整理成给用户/模型看的文本。"""
    lines = []
    for it in issues or []:
        loc = "第 %d 行第 %d 列" % (it.get("line") or 0, it.get("col") or 0)
        lines.append("%s：%s" % (loc, it.get("message") or ""))
    return "\n".join(lines)


def lint_report(code: str) -> str:
    """语法检查 + 结论（供工具直接返回）。"""
    issues = check_python(code)
    if not issues:
        return "语法检查通过：代码可被 Python 正常解析。"
    return "发现 %d 处语法问题：\n%s" % (len(issues), format_issues(issues))
