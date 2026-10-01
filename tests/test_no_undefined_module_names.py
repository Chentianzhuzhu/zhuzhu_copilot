"""回归守卫：模块级「被引用但从未定义」的名字必须为 0。

为什么要它：删除一整个特性（如磨砂玻璃材质）时，极易只删掉定义而漏掉散落各处的
调用点 —— 编译期不报错，运行到那一行才 NameError，而那条路径往往要用户点好几下才
走到（真实教训：删完玻璃后 `build_default_ui` 里的 `_harden_combo_popup` 直接让
AgentPanel 构造失败）。

做法与 `scripts/_probe_undefined_names.py` 同源（保守近似，不做作用域分析）：
已定义集合 = 赋值目标 / 函数·类定义名 / 参数 / for·with·推导式目标 / 导入名 /
             global 声明 / except 别名
可疑集合   = 所有 Load 上下文的 Name − 已定义 − builtins

已定义集合是超集（局部作用域缺名会漏报），但「从未在任何位置被赋值」的模块级缺名
一定会被抓到 —— 这正是误删定义留下的痕迹。
"""
import ast
import builtins
from pathlib import Path

import pytest

# 允许的白名单：静态扫描的已知假阳性（注解里的 typing 名、lambda 形参等），
# 新增条目必须在此写明理由，否则视为漏删定义的回归。
_KNOWN_FALSE_POSITIVES = {
    "Optional": "第 6173 行 `self._turn: Optional[QWidget]` 注解（typing 名未显式导入）",
    "_wf": "lambda 形参（默认参数写法，AST 未计入 defined）",
    "im": "lambda 形参",
    "__file__": "模块内置名（部分 builtins 判定未覆盖）",
}

# 受保护的模块：改动面最大、最容易被「删一半」波及的文件
_TARGETS = (
    "src/zhuzhu_Copilot/ui/agent_panel.py",
    "src/zhuzhu_Copilot/core/agent_tools.py",
    "src/zhuzhu_Copilot/core/app_wallpaper.py",
)


def _targets(node, defined):
    if isinstance(node, ast.Name):
        defined.add(node.id)
    elif isinstance(node, (ast.Tuple, ast.List)):
        for e in node.elts:
            _targets(e, defined)
    elif isinstance(node, ast.Starred):
        _targets(node.value, defined)


def _undefined_names(path: Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    defined = set()
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

    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.AsyncFor)):
            _targets(node.target, defined)
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            _targets(node.optional_vars, defined)
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp,
                               ast.GeneratorExp)):
            for g in node.generators:
                _targets(g.target, defined)

    used = {n.id for n in ast.walk(tree)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    return {n for n in used if n not in defined and not hasattr(builtins, n)}


@pytest.mark.parametrize("rel", _TARGETS)
def test_no_undefined_module_level_names(rel):
    path = Path(__file__).resolve().parent.parent / rel
    assert path.is_file(), f"受保护模块不存在：{rel}"
    missing = _undefined_names(path) - set(_KNOWN_FALSE_POSITIVES)
    assert not missing, (
        f"{rel} 存在「被引用但从未定义」的名字（多半是删特性时漏删了调用点）："
        f"{sorted(missing)}")
