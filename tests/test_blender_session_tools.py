# -*- coding: utf-8 -*-
"""blender-modeler 的「接续/导入/导出」契约回归。

用户需求：重启实例要能打开上一次的工作文件、能导入现有 .blend 继续工作、
能导出 fbx / glb 给应用与游戏引擎。

守护契约：
  A. 新工具已注册（blend_list / scene_import），导出参数齐全；
  B. 接续语义：scene_open 是「载入干净文件」，不得被标记为 dirty
     （否则每次打开文件都会被当成有未保存改动，重启时用恢复文件覆盖它）；
  C. 恢复判定不能依赖文件 mtime —— 整套建模可能在一秒内跑完，精度不够；
     必须用 dirty 语义标志（实测踩过：5 个对象的恢复文件被判负）；
  D. 换文件后预览页要刷新（scene_open 不在实时帧跳过名单里）。
"""
import ast
import importlib.util
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
PLUGIN = SRC / "zhuzhu_Copilot" / "plugins" / "blender-modeler"
SERVER = PLUGIN / "server.py"
HOST = PLUGIN / "blender_host.py"


def _load_server():
    spec = importlib.util.spec_from_file_location("blender_server_session_tools", SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _host_ast():
    return ast.parse(HOST.read_text(encoding="utf-8"))


def _const_set(tree, name):
    """取出模块级 `NAME = {...}` / `NAME = frozenset({...})` 的字面量元素集合。"""
    def _elts(node):
        if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
            return {e.value for e in node.elts if isinstance(e, ast.Constant)}
        if isinstance(node, ast.Call) and node.args:
            return _elts(node.args[0])
        return set()

    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == name:
                    return _elts(node.value)
    return set()


def _dict_keys(tree, name):
    """取出模块级 `NAME = {...}` 字典的字符串键。"""
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            if isinstance(node.value, ast.Dict):
                return {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
    return set()


def _func_source(tree, name):
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(HOST.read_text(encoding="utf-8"), node) or ""
    return ""


# ---------- A. 工具注册与参数 ----------
def test_new_tools_are_exposed():
    mod = _load_server()
    names = {t["name"] for t in mod.TOOLS}
    for name in ("blend_list", "scene_import", "scene_open", "scene_save",
                 "export_model"):
        assert name in names, f"{name} 未出现在工具表里"
    for name in ("blend_list", "scene_import"):
        assert name in mod._HANDLERS, f"{name} 未注册处理器"


def test_export_tool_has_engine_friendly_options():
    mod = _load_server()
    schema = next(t for t in mod.TOOLS if t["name"] == "export_model")
    props = schema["inputSchema"]["properties"]
    assert {"format", "apply_modifiers", "embed_textures", "selected_only"} <= set(props)
    assert set(props["format"]["enum"]) == {"glb", "fbx", "obj", "stl"}


def test_import_tool_supports_selective_and_prefix():
    mod = _load_server()
    schema = next(t for t in mod.TOOLS if t["name"] == "scene_import")
    props = schema["inputSchema"]["properties"]
    assert {"path", "names", "prefix", "collection", "limit"} <= set(props)


# ---------- B/C. 接续语义 ----------
def test_scene_open_is_not_marked_dirty():
    tree = _host_ast()
    dirty_methods = _const_set(tree, "_DIRTY_METHODS")
    assert dirty_methods, "找不到 _DIRTY_METHODS"
    assert "scene_open" not in dirty_methods, \
        "scene_open 载入的是已保存的干净文件，标脏会让重启时用恢复文件盖掉它"
    for name in ("object_add", "material_assign", "scene_import", "template_apply"):
        assert name in dirty_methods, f"{name} 改动场景，必须标脏"


def test_restore_uses_dirty_flag_not_mtime():
    src = _func_source(_host_ast(), "h_session_restore")
    assert src, "找不到 h_session_restore"
    assert "getmtime" not in src, \
        "mtime 精度不足以区分「刚存的工作文件」与「刚写的恢复文件」，必须改用 dirty 标志"
    assert "dirty" in src


def test_session_handlers_registered_in_host():
    keys = _dict_keys(_host_ast(), "HANDLERS")
    for name in ("session_park", "session_restore", "blend_list", "scene_import"):
        assert name in keys, f"host 未注册 {name}"


def test_park_is_called_on_both_exit_paths():
    src = _func_source(_host_ast(), "_run")
    assert src.count("_park_before_exit") >= 2, \
        "闲置退出与服务端断开都要先存恢复文件，否则关掉应用就丢未保存的建模"


# ---------- D. 预览联动 ----------
def test_scene_open_refreshes_live_view():
    mod = _load_server()
    assert "scene_open" in mod._MODELING_TOOLS
    assert "scene_open" not in mod._LIVE_SKIP, \
        "换文件后预览页应立刻出新帧，否则用户看到的是上一个场景"
