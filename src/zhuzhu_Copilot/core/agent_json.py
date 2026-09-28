"""容错的工具参数 JSON 解析：LLM 偶发返回带代码围栏 / 尾部逗号 / 单引号等瑕疵的
arguments，严格 json.loads 会失败，导致 write/edit 等工具反复报"参数非法 JSON"。
这里按常见瑕疵逐步修复，尽量恢复为合法 dict，彻底失败才返回 None。"""

import json
import re


def parse_tool_args(text):
    """容错解析 tool_calls.arguments。成功返回 dict；彻底无法解析返回 None。
    依序尝试：标准解析 → 转义字符串内原始换行 → 提取花括号内容 → 去尾部逗号 → 单引号转双引号。"""
    # 部分上游（字节 agent plan 等）直接返回已解析的 dict/list 而非 JSON 字符串，
    # 此时直接复用，避免 str() 转成 Python repr（单引号/嵌套结构）后再走容错反而损坏参数
    if isinstance(text, dict) or isinstance(text, list):
        return text
    if not isinstance(text, str):
        text = str(text) if text else ""
    text = text.strip()
    if not text:
        return {}
    candidates = [
        text,
        _escape_raw_breaks(text),
        _extract_braces(text),
        _drop_trailing_commas(text),
        _single_to_double(text),
    ]
    for cand in candidates:
        if not cand:
            continue
        try:
            return json.loads(cand)   # 首个可解析的合法 JSON（可为 dict/列表/标量）
        except (json.JSONDecodeError, ValueError):
            continue
    # 文件类工具载荷兜底：标准 JSON 语义在 write/edit 大段文本内容（含真实换行/未转义
    # 引号，如改 settings.json 新增规则）上常失败，这里按工具形态安全重建参数。
    return _recover_file_payload(text)


def _recover_file_payload(text):
    """write_file/edit_file 等带大段文本载荷的工具参数恢复：标准 JSON 解析全部失败时，
    从原文重建 {path, content/new_text/...}。为容忍内容里的真实换行/未转义双引号，
    把 path 取为干净一行字符串、内容取为『键后首个引号 → 对象收尾前最后一个引号』之间的原文。
    **安全约束**：仅当 content 为对象最后一个键（其后只剩收尾 `}`）且区间非空时才启用，
    否则返回 None（宁可让上游报错，也不写坏内容）。"""
    if not isinstance(text, str):
        return None
    m = re.search(r'"(?:path|file|file_path|target)"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
    if not m:
        return None
    path = m.group(1)
    for key in ("content", "new_text", "text", "data"):
        km = re.search(r'"%s"\s*:\s*"' % re.escape(key), text)
        if not km:
            continue
        start = km.end()                  # 键后引号之后 = 内容起点
        tail = text[start:]
        rb = tail.rfind("}")
        seg = tail[:rb] if rb != -1 else tail
        close = seg.rfind('"')            # content 为末键时的收尾引号
        if close <= 0:
            continue
        after = seg[close + 1:].strip()   # 收尾引号之后必须只剩收尾符
        if after and not after.startswith("}"):
            continue
        raw = seg[:close].strip()
        if not raw:
            continue
        return {"path": path, key: raw}
    return None


def _escape_raw_breaks(text: str) -> str:
    """把**双引号字符串字面量内部**的原始换行/回车/制表符转义为 \\n/\\r/\\t。

    LLM 在 write_file 等工具的 content（多行文本）里常直接写真实换行而不是 \\n，
    严格 JSON 不允许字符串内出现裸 \n\t\r，这会让解析失败并反复报"参数非法 JSON"。
    本函数：逐字符扫描，仅在字符串内替换裸控制符，保留已转义的 \\n 等分隔序列；
    其余位置（花括号/键名/键之间）原样拷贝。尽力而为——若替换后仍非法则按原流程
    放弃该候选，不影响其它容错路径。"""
    out = []
    i, n = 0, len(text)
    in_str = False
    while i < n:
        ch = text[i]
        if not in_str:
            if ch == '"':
                in_str = True
            out.append(ch)
            i += 1
            continue
        # —— 字符串内 ——
        if ch == "\\":
            out.append(ch)
            if i + 1 < n:
                out.append(text[i + 1])
                i += 2
            else:
                i += 1
            continue
        if ch == '"':
            in_str = False
            out.append(ch)
            i += 1
            continue
        if ch == "\n":
            out.append("\\n")
            i += 1
            continue
        if ch == "\r":
            out.append("\\r")
            i += 1
            continue
        if ch == "\t":
            out.append("\\t")
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _extract_braces(text: str) -> str:
    """去掉 markdown 代码围栏，或直接从首个 { 截取到末个 }（容忍前缀说明文字）。"""
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        body = m.group(1).strip()
    else:
        body = text
    a, b = body.find("{"), body.rfind("}")
    if a >= 0 and b > a:
        return body[a:b + 1]
    return body


def _drop_trailing_commas(text: str) -> str:
    """去掉数组/对象末尾多余逗号（C 风格才华）。"""
    return re.sub(r",\s*([}\]])", r"\1", text)


def _single_to_double(text: str) -> str:
    """把单引号字符串 delimiter 转成双引号（保留双引号字符串内部原样，
    避免破坏 write 内容里的撇号）。"""
    out = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            # 复制一段双引号字符串原样保留
            j = i + 1
            seg = ['"']
            closed = False
            while j < n:
                cj = text[j]
                if cj == "\\" and j + 1 < n:
                    seg.append(cj)
                    seg.append(text[j + 1])
                    j += 2
                    continue
                seg.append(cj)
                if cj == '"':
                    closed = True
                    j += 1
                    break
                j += 1
            if not closed:
                j = n
            out.append("".join(seg))
            i = j
            continue
        if ch == "'":
            j = i + 1
            seg = ['"']
            while j < n:
                cj = text[j]
                if cj == "\\" and j + 1 < n:
                    nxt = text[j + 1]
                    if nxt == "'":
                        seg.append("\\'")   # 转义单引号在双引号内可直接保留
                    else:
                        seg.append(cj)
                        seg.append(nxt)
                    j += 2
                    continue
                if cj == "'":
                    seg.append('"')
                    j += 1
                    break
                seg.append(cj)
                j += 1
            out.append("".join(seg))
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out)