"""静态扫描 agent_panel.py：找出「被引用但未定义」的全局名。

用途：工作区处于半途回退状态（core/app_glass.py 已删、agent_panel 里回退了 1253 行），
残留引用会在运行时抛 NameError。本脚本一次性列出全部可疑名字，供：
  1) 评估工作区损坏范围；
  2) 性能探针用统一 stub 补齐，从而能量到真实的构造耗时。

做法（保守近似）：
  已定义集合 = 所有赋值目标 / 函数与类定义名 / 参数 / for·with·comprehension 目标 /
                导入名 / global 声明 / 注解中的名字
  可疑集合   = 所有 Load 上下文的 Name − 已定义 − builtins
因不做作用域分析，已定义集合是超集（会漏报局部作用域缺名），
但「从未在任何位置被赋值」的模块级缺名一定会被抓到。
"""
import ast
import builtins
import sys
from pathlib import Path

TARGET = Path(sys.argv[1] if len(sys.argv) > 1
              else "src/zhuzhu_Copilot/ui/agent_panel.py")

tree = ast.parse(TARGET.read_text(encoding="utf-8"))

defined = set()


def _targets(node):
    if isinstance(node, ast.Name):
        defined.add(node.id)
    elif isinstance(node, (ast.Tuple, ast.List)):
        for e in node.elts:
            _targets(e)
    elif isinstance(node, ast.Starred):
        _targets(node.value)


for node in ast.walk(tree):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        defined.add(node.name)
        a = node.args
        for arg in (list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs)
                    + ([a.vararg] if a.vararg else [])
                    + ([a.kwarg] if a.kwarg else [])):
            defined.add(arg.arg)
    elif isinstance(node, ast.ClassDef):
        defined.add(node.name)
    elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
        defined.add(node.id)
    elif isinstance(node, (ast.Import, ast.ImportFrom)):
        for al in node.names:
            defined.add((al.asname or al.name).split(".")[0])
    elif isinstance(node, ast.ExceptHandler) and node.name:
        defined.add(node.name)
    elif isinstance(node, (ast.Global, ast.Nonlocal)):
        defined.update(node.names)
    elif isinstance(node, ast.alias):
        defined.add((node.asname or node.name).split(".")[0])

# 属性目标（self.x = ...）不算，但要覆盖 `for x in ...` 与 comprehension
for node in ast.walk(tree):
    if isinstance(node, (ast.For, ast.AsyncFor)):
        _targets(node.target)
    elif isinstance(node, (ast.withitem,)):
        if node.optional_vars is not None:
            _targets(node.optional_vars)
    elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp,
                           ast.GeneratorExp)):
        for g in node.generators:
            _targets(g.target)

used = {}
for node in ast.walk(tree):
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
        used.setdefault(node.id, 0)
        used[node.id] += 1

missing = {n: c for n, c in used.items()
           if n not in defined and not hasattr(builtins, n)}

print(f"文件: {TARGET}")
print(f"引用名 {len(used)}  已定义 {len(defined)}  可疑未定义 {len(missing)}\n")
print(f"{'名字':<40}{'引用次数':>8}")
for n, c in sorted(missing.items(), key=lambda kv: -kv[1]):
    print(f"{n:<40}{c:>8}")
