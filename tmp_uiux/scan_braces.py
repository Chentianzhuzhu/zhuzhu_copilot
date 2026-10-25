# -*- coding: utf-8 -*-
import ast, sys
path = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\src\zhuzhu_Copilot\ui\agent_panel.py"
src = open(path, encoding="utf-8").read()
tree = ast.parse(src)

class V(ast.NodeVisitor):
    def visit_Constant(self, node):
        if isinstance(node.value, str):
            # 判断是否为 f-string 的一部分：JoinedString 节点会包 FormattedValue / Constant
            # 顶层 Constant 且不在 JoinedStr 内 → 普通字符串
            t = node.value
            if ("}}" in t or "{{" in t):
                print(f"line {node.lineno}: PLAIN string with double-brace: {t[:90]!r}")
    def visit_JoinedStr(self, node):
        # f-string：内部 Constant 的 {{ }} 是正常转义，不报告
        for c in node.values:
            if isinstance(c, ast.FormattedValue):
                self.visit(c)
V().visit(tree)
print("scan done")
