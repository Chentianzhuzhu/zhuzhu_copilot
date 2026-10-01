"""定位 agent_panel.py 解析（PEG）耗时黑洞。

做法：用 tokenize（只做词法，很快）把源码切成「顶层逻辑块」，
再对每个块单独 ast.parse 计时，找出秒级耗时的具体定义。

用法: python scripts/_probe_parse_hotspot.py [文件路径]
"""
import ast
import io
import sys
import time
import tokenize


def top_level_spans(src: str):
    """返回顶层块的 (start_line, end_line) 列表（1-based，含尾）。

    用 tokenize 维护缩进层级：只在 level==0 且行首非空白时开新块。
    装饰器 / 注释先并入下一块（不影响定位）。
    """
    lines = src.splitlines()
    spans = []
    start = 1
    level = 0
    pending = 0          # 已看到的 INDENT 深度增量
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.INDENT:
            level += 1
        elif tok.type == tokenize.DEDENT:
            level = max(0, level - 1)
        elif tok.type in (tokenize.NEWLINE, tokenize.NL):
            continue
        elif tok.type == tokenize.ENDMARKER:
            break
        else:
            if level == 0 and tok.start[0] > start and tok.type not in (
                    tokenize.COMMENT, tokenize.DEDENT, tokenize.INDENT):
                # 遇到下一个顶层 token → 上一块在此前一行结束
                spans.append((start, tok.start[0] - 1))
                start = tok.start[0]
    spans.append((start, len(lines)))
    return spans


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else \
        "src/zhuzhu_Copilot/ui/agent_panel.py"
    src = open(path, encoding="utf-8").read()

    t = time.perf_counter()
    spans = top_level_spans(src)
    print(f"tokenize + 切块: {(time.perf_counter()-t)*1000:.1f} ms, {len(spans)} 块")

    lines = src.splitlines()
    rows = []
    total = 0.0
    for a, b in spans:
        seg = "\n".join(lines[a - 1:b])
        if not seg.strip():
            continue
        t = time.perf_counter()
        try:
            ast.parse(seg)
        except SyntaxError:
            continue          # 切块边界落在多行结构内，跳过（不影响定位）
        dt = (time.perf_counter() - t) * 1000
        total += dt
        head = lines[a - 1][:78]
        rows.append((dt, a, b, head))

    rows.sort(reverse=True)
    print(f"\n累计（可解析块）: {total:.1f} ms\n")
    print(f"{'ms':>9}  {'行范围':>16}  首行")
    for dt, a, b, head in rows[:25]:
        print(f"{dt:>9.1f}  {f'{a}-{b}':>16}  {head}")


if __name__ == "__main__":
    main()
