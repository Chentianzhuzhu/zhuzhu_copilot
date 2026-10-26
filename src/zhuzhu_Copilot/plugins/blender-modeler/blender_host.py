"""Blender 侧执行器：在 `blender --background` 进程里用 bpy 真实建模/材质/灯光/渲染。

由 server.py 拉起（stdio MCP 进程）后，通过 127.0.0.1 本地 socket 接收 JSON 行协议：

    请求  {"id": 7, "method": "object_add", "params": {...}}
    响应  {"id": 7, "ok": true,  "result": {...}}
          {"id": 7, "ok": false, "error": "..."}

为什么常驻：建模是「几十次小步迭代 + 偶尔渲染」的工作流，每次重启 Blender（2~5 秒）
会让多步建模变得不可用。常驻进程同时保留场景、材质、相机与渲染缓存。
本文件只允许使用 bpy 与标准库，禁止 mock：所有结果都来自真实场景求值。
"""
import json
import math
import os
import select
import socket
import sys
import time
import traceback

import bpy
from mathutils import Vector

# 由命令行注入
OUTPUT_DIR = ""
PRESETS_DIR = ""
IDLE_TIMEOUT = 1800.0
START_TS = time.time()

_LAST_ACTIVITY = time.time()
_IDLE_EXIT = False


# ============================================================================
# 基础工具
# ============================================================================

def _as_list(v, n=3, default=0.0):
    if v is None:
        return [default] * n
    if isinstance(v, (int, float)):
        return [float(v)] * n
    out = [float(x) for x in v][:n]
    while len(out) < n:
        out.append(default)
    return out


def _safe_path(p, default_name="untitled"):
    """相对路径落到输出目录；绝对路径原样使用。"""
    p = str(p or "").strip()
    if not p:
        p = default_name
    p = os.path.expanduser(p)
    if not os.path.isabs(p):
        p = os.path.join(OUTPUT_DIR, p)
    return os.path.abspath(p)


def _ensure_ext(path, ext):
    if not path.lower().endswith("." + ext.lower()):
        path += "." + ext
    return path


def _ensure_dir(path):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)


def _srgb_to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _hex_rgb(value, default=(0.8, 0.8, 0.8)):
    """'#RRGGBB' / '#RGB' / [r,g,b(,a)] → sRGB 浮点（0~1）。"""
    if value is None or value == "":
        return list(default)
    if isinstance(value, str):
        s = value.strip().lstrip("#")
        if len(s) == 3:
            s = "".join(ch * 2 for ch in s)
        if len(s) not in (6, 8):
            raise ValueError(f"颜色格式应为 #RRGGBB：{value}")
        rgb = [int(s[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
        if len(s) == 8:
            rgb.append(int(s[6:8], 16) / 255.0)
        return rgb
    return [float(x) for x in value][:4] if len(value) >= 4 else [float(x) for x in value][:3]


def _linear_rgba(value, default=(0.8, 0.8, 0.8)):
    rgb = _hex_rgb(value, default)
    a = rgb[3] if len(rgb) > 3 else 1.0
    return tuple(_srgb_to_linear(c) for c in rgb[:3]) + (a,)


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return int(default)


def _obj(name):
    o = bpy.data.objects.get(str(name or ""))
    if o is None:
        names = ", ".join(sorted(o.name for o in bpy.data.objects)[:20])
        raise ValueError(f"找不到对象「{name}」。当前对象：{names or '（空场景）'}")
    return o


def _names(value):
    if isinstance(value, str):
        raw = value.replace("，", ",").split(",")
    elif isinstance(value, (list, tuple)):
        raw = list(value)
    else:
        raw = [value]
    return [str(x).strip() for x in raw if str(x).strip()]


def _objs(value):
    return [_obj(n) for n in _names(value)]


def _step_all():
    """推进一次 depsgraph，让 bpy.ops 修改后的状态可被读取。"""
    try:
        bpy.context.view_layer.update()
    except Exception:
        pass


def _scene():
    return bpy.context.scene


def _set_input(node, name, value):
    sock = node.inputs.get(name)
    if sock is not None:
        try:
            sock.default_value = value
        except (TypeError, ValueError):
            pass


# ============================================================================
# 材质 / 集合 / 层级
# ============================================================================

def _make_material(name, color=None, metallic=0.0, roughness=0.5,
                   emission_color=None, emission_strength=0.0, alpha=1.0):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
        out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None) or nt.nodes.new("ShaderNodeOutputMaterial")
        nt.links.new(bsdf.outputs[0], out.inputs[0])
    rgba = _linear_rgba(color, default=(0.8, 0.8, 0.8)) if color not in (None, "") else (0.8, 0.8, 0.8, 1.0)
    _set_input(bsdf, "Base Color", rgba)
    _set_input(bsdf, "Metallic", float(metallic))
    _set_input(bsdf, "Roughness", float(roughness))
    _set_input(bsdf, "Alpha", float(alpha))
    if emission_color not in (None, ""):
        _set_input(bsdf, "Emission", _linear_rgba(emission_color))
    _set_input(bsdf, "Emission Strength", float(emission_strength))
    try:
        mat.blend_method = "BLEND" if alpha < 1.0 else "OPAQUE"
    except Exception:
        pass
    # 视口显示色（预览页的实时快照用 Workbench 渲染，它读的是 diffuse_color，
    # 不读节点里的 Base Color；不设就会一片灰，看不出材质进度）
    try:
        mat.diffuse_color = rgba if len(rgba) == 4 else (rgba[0], rgba[1], rgba[2], 1.0)
        mat.metallic = float(metallic)
        mat.roughness = float(roughness)
    except Exception:
        pass
    return mat


def _assign_material(obj, mat):
    if obj.type not in ("MESH", "CURVE", "FONT"):
        return
    obj.data.materials.clear()
    obj.data.materials.append(mat)
    if obj.type == "MESH":
        for poly in obj.data.polygons:
            poly.material_index = 0


def _collection(name, create=True):
    name = str(name or "").strip()
    if not name:
        return None
    coll = bpy.data.collections.get(name)
    if coll is None and create:
        coll = bpy.data.collections.new(name)
        _scene().collection.children.link(coll)
    return coll


def _move_to_collection(obj, coll):
    if coll is None:
        return
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    coll.objects.link(obj)


def _set_shading(obj, mode="flat", angle_deg=30.0):
    if obj.type != "MESH":
        return
    mode = str(mode or "flat").lower()
    smooth = mode in ("smooth", "auto")
    for poly in obj.data.polygons:
        poly.use_smooth = smooth
    if mode == "auto":
        try:
            obj.data.use_auto_smooth = True
            obj.data.auto_smooth_angle = math.radians(float(angle_deg))
        except AttributeError:
            pass


def _add_bevel(obj, width, segments=2, angle_deg=30.0):
    if obj.type != "MESH" or _num(width) <= 0:
        return None
    mod = obj.modifiers.new("Bevel", "BEVEL")
    mod.width = _num(width)
    mod.segments = max(1, _int(segments, 2))
    mod.limit_method = "ANGLE"
    mod.angle_limit = math.radians(_num(angle_deg, 30.0))
    return mod


def _apply_modifier(obj, mod):
    bpy.context.view_layer.objects.active = obj
    try:
        bpy.ops.object.modifier_apply(modifier=mod.name)
    except Exception as e:
        raise ValueError(f"应用修改器 {mod.name} 失败：{e}")


def _recalc_normals(obj):
    if obj.type != "MESH":
        return
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.object.mode_set(mode="OBJECT")
    obj.select_set(False)


def _parent(child, parent):
    child.parent = parent
    child.matrix_parent_inverse = parent.matrix_world.inverted()
    return child


# ============================================================================
# 对象创建
# ============================================================================

_PRIMITIVES = {"cube", "uv_sphere", "ico_sphere", "cylinder", "cone",
               "torus", "plane", "capsule", "text", "empty"}

#: 常见别称 → 真实几何体名。模型（和用户）口语里就是 sphere / box / 球，
#: 硬报「不支持的几何体」会让它白烧一轮再重试；这里直接映射掉。
_PRIMITIVE_ALIAS = {
    "sphere": "ico_sphere", "ball": "ico_sphere", "round": "ico_sphere",
    "uvsphere": "uv_sphere", "uv-sphere": "uv_sphere",
    "box": "cube", "block": "cube",
    "cyl": "cylinder", "column": "cylinder", "tube": "cylinder",
    "quad": "plane", "floor": "plane", "ground": "plane",
    "ring": "torus", "donut": "torus",
    "pill": "capsule",
    "球": "ico_sphere", "球体": "ico_sphere",
    "立方体": "cube", "方块": "cube",
    "圆柱": "cylinder", "圆柱体": "cylinder",
    "圆锥": "cone", "圆环": "torus", "平面": "plane", "胶囊": "capsule",
}


def _create_primitive(kind, size, segments, vertices):
    kind = kind.lower()
    size = max(_num(size, 1.0), 1e-4)
    seg = max(3, _int(segments, 16))
    # vertices 缺省/为 0 时必须回落到 segments：直接 _int(vertices, seg) 会把
    # 0 当成合法值，圆柱就被建成三棱柱（真实踩过的坑）。
    vtx = max(3, _int(vertices, 0) or seg)
    if kind == "cube":
        bpy.ops.mesh.primitive_cube_add(size=size)
    elif kind == "plane":
        bpy.ops.mesh.primitive_plane_add(size=size)
    elif kind == "uv_sphere":
        bpy.ops.mesh.primitive_uv_sphere_add(segments=seg, ring_count=max(2, seg // 2), radius=size / 2.0)
    elif kind == "ico_sphere":
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=max(1, _int(vertices, 0) or 2), radius=size / 2.0)
    elif kind == "cylinder":
        bpy.ops.mesh.primitive_cylinder_add(vertices=vtx, radius=size / 2.0, depth=size)
    elif kind == "cone":
        bpy.ops.mesh.primitive_cone_add(vertices=vtx, radius1=size / 2.0,
                                        radius2=0.0, depth=size)
    elif kind == "torus":
        bpy.ops.mesh.primitive_torus_add(major_radius=size / 2.0, minor_radius=size / 8.0,
                                         major_segments=max(6, _int(segments, 24)),
                                         minor_segments=max(3, seg // 4))
    elif kind == "capsule":
        r = size * 0.25
        bpy.ops.mesh.primitive_cylinder_add(vertices=vtx,
                                            radius=r, depth=size - 2 * r)
        body = bpy.context.object
        caps = []
        for z in (-(size - 2 * r) / 2.0, (size - 2 * r) / 2.0):
            bpy.ops.mesh.primitive_uv_sphere_add(segments=seg, ring_count=max(2, seg // 2), radius=r,
                                                 location=(0, 0, z))
            caps.append(bpy.context.object)
        bpy.ops.object.select_all(action="DESELECT")
        body.select_set(True)
        for c in caps:
            c.select_set(True)
        bpy.context.view_layer.objects.active = body
        bpy.ops.object.join()
        return bpy.context.object
    elif kind == "empty":
        bpy.ops.object.empty_add(type="PLAIN_AXES", radius=size / 2.0)
    else:
        bpy.ops.mesh.primitive_cube_add(size=size)
    return bpy.context.object


def _make_text(body, size, align="LEFT"):
    curve = bpy.data.curves.new(name="TextCurve", type="FONT")
    curve.body = str(body or "Text")
    curve.size = max(_num(size, 0.2), 1e-4)
    curve.align_x = str(align or "LEFT").upper()
    curve.align_y = "CENTER"
    obj = bpy.data.objects.new("Text", curve)
    _scene().collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    try:
        bpy.ops.object.convert(target="MESH")
    except Exception:
        pass
    obj.select_set(False)
    return obj


# ============================================================================
# 几何信息
# ============================================================================

def _world_bbox(objs):
    """求包围盒；摄影棚地面（bm_floor）不参与，否则取景会被几十米的地面拉远。"""
    pts = []
    for o in objs:
        if o.get("bm_floor"):
            continue
        if o.type not in ("MESH", "CURVE", "FONT", "EMPTY"):
            continue
        for corner in o.bound_box:
            pts.append(o.matrix_world @ Vector(corner))
    if not pts:
        return Vector((0, 0, 0)), 1.0, (Vector((0, 0, 0)), Vector((0, 0, 0)))
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    center = (lo + hi) / 2.0
    radius = max((hi - lo).length / 2.0, 1e-4)
    return center, radius, (lo, hi)


def _obj_info(o, detail="brief"):
    info = {
        "name": o.name,
        "type": o.type,
        "location": [round(v, 4) for v in o.location],
        "rotation_deg": [round(math.degrees(v), 2) for v in o.rotation_euler],
        "scale": [round(v, 4) for v in o.scale],
    }
    if o.type == "MESH":
        info["verts"] = len(o.data.vertices)
        info["faces"] = len(o.data.polygons)
    info["materials"] = [m.name for m in (o.data.materials if hasattr(o.data, "materials") else []) if m]
    if o.parent:
        info["parent"] = o.parent.name
    if detail == "full":
        info["dimensions"] = [round(v, 4) for v in o.dimensions]
        info["modifiers"] = [{"name": m.name, "type": m.type} for m in o.modifiers]
        info["collections"] = [c.name for c in o.users_collection]
    return info


def _obj_summary(o):
    s = f"{o.name}（{o.type}"
    if o.type == "MESH":
        s += f"，{len(o.data.vertices)} 顶点/{len(o.data.polygons)} 面"
    mats = [m.name for m in (o.data.materials if hasattr(o.data, "materials") else []) if m]
    if mats:
        s += "，材质 " + "/".join(mats)
    return s + "）"


def _scene_summary():
    sc = _scene()
    meshes = [o for o in sc.objects if o.type == "MESH"]
    center, radius, (lo, hi) = _world_bbox(meshes)
    tris = sum(len(o.data.loop_triangles) if o.data.loop_triangles else len(o.data.polygons) for o in meshes)
    return {
        "blender": bpy.app.version_string,
        "file": bpy.data.filepath or "",
        "scene": sc.name,
        "objects": len(sc.objects),
        "meshes": len(meshes),
        "triangles": tris,
        "materials": len(bpy.data.materials),
        "engine": sc.render.engine,
        "camera": sc.camera.name if sc.camera else "",
        "resolution": [sc.render.resolution_x, sc.render.resolution_y],
        "bounds": {"min": [round(v, 4) for v in lo], "max": [round(v, 4) for v in hi],
                   "center": [round(v, 4) for v in center], "radius": round(radius, 4)},
        "output_dir": OUTPUT_DIR,
        "uptime_s": round(time.time() - START_TS, 1),
    }


# ============================================================================
# 处理器：状态 / 场景
# ============================================================================

def h_status(params):
    return _scene_summary()


def h_scene_new(params):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = _scene()
    sc.unit_settings.scale_length = _num(params.get("unit_scale"), 1.0)
    world = bpy.data.worlds.new("World")
    world.use_nodes = True
    bg = next((n for n in world.node_tree.nodes if n.type == "BACKGROUND"), None)
    if bg:
        bg.inputs[0].default_value = _linear_rgba(params.get("background_color"), default=(0.02, 0.02, 0.025))
        bg.inputs[1].default_value = _num(params.get("strength"), 1.0)
    sc.world = world
    return {"message": "已新建空场景（默认黑色世界背景）", "scene": _scene_summary()}


def h_scene_info(params):
    detail = str(params.get("detail") or "brief").lower()
    objs = [_obj_info(o, detail) for o in _scene().objects]
    return {"summary": _scene_summary(), "objects": objs}


def h_scene_save(params):
    sc = _scene()
    name = str(params.get("name") or sc.name or "scene").strip()
    path = _safe_path(params.get("path") or f"{name}.blend")
    path = _ensure_ext(path, "blend")
    _ensure_dir(path)
    bpy.ops.wm.save_as_mainfile(filepath=path, compress=bool(params.get("compress", True)))
    return {"path": path, "message": f"场景已保存：{path}"}


def h_scene_open(params):
    path = _safe_path(params.get("path"))
    if not os.path.isfile(path):
        raise ValueError(f"文件不存在：{path}")
    bpy.ops.wm.open_mainfile(filepath=path)
    return {"path": path, "message": f"已打开场景：{path}", "scene": _scene_summary()}


# ============================================================================
# 处理器：对象
# ============================================================================

def h_object_add(params):
    kind = str(params.get("kind") or "cube").strip().lower()
    kind = _PRIMITIVE_ALIAS.get(kind, kind)   # sphere/box/球 等口语别名先归一
    if kind not in _PRIMITIVES:
        raise ValueError(f"不支持的几何体：{kind}（可用：{', '.join(sorted(_PRIMITIVES))}）")
    name = str(params.get("name") or "").strip()
    loc = _as_list(params.get("location"), 3)
    rot = [math.radians(v) for v in _as_list(params.get("rotation"), 3)]
    scl = _as_list(params.get("scale"), 3, 1.0) or [1.0, 1.0, 1.0]
    size = _num(params.get("size"), 1.0)
    seg = _int(params.get("segments"), 16)
    verts = _int(params.get("vertices"), 0)

    if kind == "text":
        obj = _make_text(params.get("body", ""), size, params.get("align", "LEFT"))
    else:
        obj = _create_primitive(kind, size, seg, verts)
    if name:
        obj.name = name
        if obj.data and getattr(obj.data, "name", None):
            obj.data.name = name
    obj.location = loc
    obj.rotation_euler = rot
    obj.scale = scl

    coll = _collection(params.get("collection"))
    _move_to_collection(obj, coll)

    shade = str(params.get("shade") or "flat").lower()
    _set_shading(obj, shade, _num(params.get("smooth_angle"), 30.0))

    bevel = params.get("bevel")
    if bevel:
        if isinstance(bevel, dict):
            _add_bevel(obj, bevel.get("width", 0.01), bevel.get("segments", 2), bevel.get("angle", 30))
        elif _num(bevel) > 0:
            _add_bevel(obj, _num(bevel), 2, 30.0)

    mat_name, color = params.get("material"), params.get("color")
    if mat_name or color:
        mname = str(mat_name or f"{obj.name}_mat")
        mat = bpy.data.materials.get(mname) or _make_material(mname, color)
        if color:
            mat = _make_material(mname, color)
        _assign_material(obj, mat)

    _step_all()
    return {"created": [obj.name], "object": _obj_info(obj, "full"),
            "message": f"已创建 {_obj_summary(obj)}"}


def h_object_transform(params):
    names = _names(params.get("names") or params.get("name"))
    if not names:
        raise ValueError("缺少 names")
    relative = bool(params.get("relative", False))
    loc = params.get("location")
    rot = params.get("rotation")
    scl = params.get("scale")
    delta = params.get("delta")
    out = []
    for n in names:
        o = _obj(n)
        if relative:
            if delta is not None:
                d = _as_list(delta, 3)
                o.location = (o.location.x + d[0], o.location.y + d[1], o.location.z + d[2])
            if loc is not None:
                d = _as_list(loc, 3)
                o.location = (o.location.x + d[0], o.location.y + d[1], o.location.z + d[2])
            if rot is not None:
                d = [math.radians(v) for v in _as_list(rot, 3)]
                o.rotation_euler = (o.rotation_euler.x + d[0], o.rotation_euler.y + d[1],
                                    o.rotation_euler.z + d[2])
            if scl is not None:
                d = _as_list(scl, 3, 1.0)
                o.scale = (o.scale.x * d[0], o.scale.y * d[1], o.scale.z * d[2])
        else:
            if loc is not None:
                o.location = _as_list(loc, 3)
            if rot is not None:
                o.rotation_euler = [math.radians(v) for v in _as_list(rot, 3)]
            if scl is not None:
                o.scale = _as_list(scl, 3, 1.0)
        out.append(_obj_info(o))
    _step_all()
    return {"updated": [o["name"] for o in out], "objects": out}


def h_object_duplicate(params):
    src = _obj(params.get("name"))
    count = max(1, _int(params.get("count"), 1))
    base_name = str(params.get("new_name") or src.name).strip()
    offset = _as_list(params.get("offset"), 3)
    mirror = str(params.get("mirror") or "").upper()
    out = []
    for i in range(count):
        dup = src.copy()
        if src.data:
            dup.data = src.data.copy()
        dup.name = f"{base_name}_{i + 1}" if count > 1 or base_name == src.name else base_name
        if dup.data and getattr(dup.data, "name", None):
            dup.data.name = dup.name
        _scene().collection.objects.link(dup)
        dup.location = (src.location.x + offset[0] * (i + 1),
                        src.location.y + offset[1] * (i + 1),
                        src.location.z + offset[2] * (i + 1))
        dup.rotation_euler = src.rotation_euler.copy()
        dup.scale = src.scale.copy()
        if src.parent:
            _parent(dup, src.parent)
        if mirror in ("X", "Y", "Z"):
            ax = "xyz".index(mirror.lower())
            s = list(dup.scale)
            s[ax] = -abs(s[ax])
            dup.scale = s
            bpy.context.view_layer.objects.active = dup
            bpy.ops.object.select_all(action="DESELECT")
            dup.select_set(True)
            bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
            _recalc_normals(dup)
            dup.select_set(False)
        out.append(_obj_info(dup))
    _step_all()
    return {"created": [o["name"] for o in out], "objects": out}


def h_object_delete(params):
    names = _names(params.get("names") or params.get("name"))
    removed = []
    for n in names:
        o = bpy.data.objects.get(n)
        if o is None:
            continue
        bpy.data.objects.remove(o, do_unlink=True)
        removed.append(n)
    _step_all()
    return {"removed": removed, "message": f"已删除 {len(removed)} 个对象" if removed else "没有匹配的对象"}


def h_object_join(params):
    names = _names(params.get("names"))
    objs = [_obj(n) for n in names]
    if len(objs) < 2:
        raise ValueError("object_join 至少需要两个对象")
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    bpy.ops.object.join()
    merged = bpy.context.object
    new_name = str(params.get("name") or "").strip()
    if new_name:
        merged.name = new_name
    _step_all()
    return {"name": merged.name, "object": _obj_info(merged, "full"),
            "message": f"已合并 {len(objs)} 个对象 → {merged.name}"}


def h_object_shade(params):
    names = _names(params.get("names") or params.get("name"))
    mode = str(params.get("mode") or "flat").lower()
    angle = _num(params.get("angle"), 30.0)
    for n in names:
        _set_shading(_obj(n), mode, angle)
    _step_all()
    return {"updated": names, "mode": mode, "angle": angle}


def _modifier_from_params(obj, kind, params):
    kind = kind.lower()
    if kind == "bevel":
        return _add_bevel(obj, params.get("width", 0.01), params.get("segments", 2), params.get("angle", 30))
    if kind == "solidify":
        m = obj.modifiers.new("Solidify", "SOLIDIFY")
        m.thickness = _num(params.get("thickness"), 0.05)
        m.offset = _num(params.get("offset"), -1.0)
        return m
    if kind == "array":
        m = obj.modifiers.new("Array", "ARRAY")
        m.count = max(1, _int(params.get("count"), 2))
        m.use_relative_offset = False
        m.use_constant_offset = True
        m.constant_offset_displace = _as_list(params.get("offset"), 3)
        return m
    if kind == "mirror":
        m = obj.modifiers.new("Mirror", "MIRROR")
        axis = str(params.get("axis") or "X").upper()
        for i, a in enumerate(("X", "Y", "Z")):
            setattr(m, f"use_axis{i}", a == axis)
        m.use_clip = bool(params.get("use_clip", True))
        return m
    if kind == "weighted_normal":
        m = obj.modifiers.new("WeightedNormal", "WEIGHTED_NORMAL")
        m.keep_sharp = True
        return m
    if kind == "triangulate":
        m = obj.modifiers.new("Triangulate", "TRIANGULATE")
        m.keep_custom_normals = True
        return m
    if kind == "subsurf":
        m = obj.modifiers.new("Subsurf", "SUBSURF")
        m.levels = max(1, _int(params.get("levels"), 1))
        m.render_levels = m.levels
        return m
    raise ValueError(f"不支持的修改器：{kind}（bevel/solidify/array/mirror/weighted_normal/triangulate/subsurf）")


def h_object_modifier(params):
    o = _obj(params.get("name"))
    kinds = _names(params.get("modifier") or params.get("type"))
    applied = []
    for kind in kinds:
        mod = _modifier_from_params(o, kind, params)
        if mod is None:
            continue
        if bool(params.get("apply", False)):
            _apply_modifier(o, mod)
            applied.append(kind)
    _step_all()
    return {"object": o.name, "modifiers": [{"name": m.name, "type": m.type} for m in o.modifiers],
            "applied": applied}


def h_object_boolean(params):
    target = _obj(params.get("target"))
    operand = _obj(params.get("operand"))
    op = str(params.get("op") or "DIFFERENCE").upper()
    if op not in ("DIFFERENCE", "UNION", "INTERSECT"):
        raise ValueError("op 只能是 DIFFERENCE / UNION / INTERSECT")
    mod = target.modifiers.new(f"Bool_{operand.name}", "BOOLEAN")
    mod.operation = op
    mod.object = operand
    if bool(params.get("apply", True)):
        _apply_modifier(target, mod)
        if bool(params.get("delete_operand", True)):
            bpy.data.objects.remove(operand, do_unlink=True)
    _step_all()
    return {"target": target.name, "object": _obj_info(target, "full"),
            "message": f"布尔 {op} 完成：{target.name}"}


# ============================================================================
# 处理器：材质 / 文本
# ============================================================================

def h_material_create(params):
    name = str(params.get("name") or "").strip()
    if not name:
        raise ValueError("缺少材质名 name")
    mat = _make_material(name, params.get("base_color"), _num(params.get("metallic"), 0.0),
                         _num(params.get("roughness"), 0.5), params.get("emission_color"),
                         _num(params.get("emission_strength"), 0.0), _num(params.get("alpha"), 1.0))
    return {"name": mat.name, "message": f"材质已创建/更新：{mat.name}"}


def h_material_assign(params):
    mat_name = str(params.get("material") or "").strip()
    if not mat_name:
        raise ValueError("缺少 material")
    mat = bpy.data.materials.get(mat_name)
    if mat is None:
        raise ValueError(f"材质不存在：{mat_name}（先用 material_create 创建）")
    objs = _objs(params.get("targets") or params.get("target"))
    for o in objs:
        _assign_material(o, mat)
    _step_all()
    return {"material": mat.name, "assigned": [o.name for o in objs]}


def h_text_add(params):
    body = str(params.get("body") or params.get("text") or "Text")
    o = _make_text(body, _num(params.get("size"), 0.2), params.get("align", "LEFT"))
    if params.get("name"):
        o.name = str(params.get("name"))
    o.location = _as_list(params.get("location"), 3)
    o.rotation_euler = [math.radians(v) for v in _as_list(params.get("rotation"), 3)]
    ext = _num(params.get("extrude"), 0.0)
    if ext > 0 and o.type == "MESH":
        # 文字已转为网格：沿法线加厚用 Solidify，保证导出与渲染都真实有厚度
        m = o.modifiers.new("Solidify", "SOLIDIFY")
        m.thickness = ext
        m.offset = 0.0
    mat_name = params.get("material")
    color = params.get("color")
    if mat_name or color:
        mname = str(mat_name or f"{o.name}_mat")
        _assign_material(o, bpy.data.materials.get(mname) or _make_material(mname, color))
    _step_all()
    return {"created": [o.name], "object": _obj_info(o, "full")}


# ============================================================================
# 处理器：相机 / 灯光 / 世界
# ============================================================================

def _look_at(obj, point):
    d = Vector(point) - obj.location
    if d.length < 1e-6:
        return
    obj.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()


def h_camera_add(params):
    name = str(params.get("name") or "Camera")
    bpy.ops.object.camera_add(location=_as_list(params.get("location"), 3, 3.0))
    cam = bpy.context.object
    cam.name = name
    cam.data.lens = _num(params.get("lens"), 50.0)
    if params.get("ortho"):
        cam.data.type = "ORTHO"
        cam.data.ortho_scale = _num(params.get("ortho_scale"), 4.0)
    if params.get("look_at") is not None:
        _look_at(cam, _as_list(params.get("look_at"), 3))
    if bool(params.get("set_active", True)):
        _scene().camera = cam
    _step_all()
    return {"name": cam.name, "location": [round(v, 3) for v in cam.location],
            "lens": cam.data.lens, "active": _scene().camera == cam}


_VIEW_DIRS = {
    "hero": (1.15, -1.35, 0.85), "iso": (1.0, -1.0, 0.75),
    "front": (0.0, -1.0, 0.10), "back": (0.0, 1.0, 0.10),
    "left": (-1.0, 0.0, 0.10), "right": (1.0, 0.0, 0.10),
    "top": (0.0, -0.002, 1.0), "bottom": (0.0, -0.002, -1.0),
}


def h_camera_frame(params):
    view = str(params.get("view") or "hero").lower()
    meshes = [o for o in _scene().objects if o.type == "MESH"]
    center, radius, _ = _world_bbox(meshes)
    cam = _scene().camera
    cam_name = str(params.get("camera") or "").strip()
    if cam_name:
        cam = _obj(cam_name)
    if cam is None or cam.type != "CAMERA":
        bpy.ops.object.camera_add()
        cam = bpy.context.object
        cam.name = "Camera"
        _scene().camera = cam
    cam.data.lens = _num(params.get("lens"), 50.0)
    if bool(params.get("ortho")):
        cam.data.type = "ORTHO"
        cam.data.ortho_scale = radius * 2.0 * _num(params.get("margin"), 1.25)
    else:
        cam.data.type = "PERSP"
    d = Vector(_VIEW_DIRS.get(view, _VIEW_DIRS["hero"]))
    if d.length < 1e-6:
        d = Vector(_VIEW_DIRS["hero"])
    d.normalize()
    fov = 2.0 * math.atan(18.0 / max(cam.data.lens, 1e-3))   # 36mm 传感器半宽
    dist = radius / max(math.sin(fov / 2.0), 1e-3) * _num(params.get("margin"), 1.25)
    cam.location = center + d * dist
    _look_at(cam, center)
    if bool(params.get("set_active", True)):
        _scene().camera = cam
    _step_all()
    return {"camera": cam.name, "location": [round(v, 3) for v in cam.location],
            "center": [round(v, 3) for v in center], "radius": round(radius, 3),
            "view": view, "lens": cam.data.lens}


def h_light_add(params):
    kind = str(params.get("kind") or "AREA").upper()
    if kind not in ("POINT", "SUN", "AREA", "SPOT"):
        raise ValueError("kind 只能是 POINT / SUN / AREA / SPOT")
    bpy.ops.object.light_add(type=kind, location=_as_list(params.get("location"), 3, 3.0))
    light = bpy.context.object
    if params.get("name"):
        light.name = str(params.get("name"))
    data = light.data
    data.energy = _num(params.get("energy"), 400.0)
    data.color = _hex_rgb(params.get("color"), default=(1.0, 1.0, 1.0))[:3]
    if kind == "AREA":
        data.size = _num(params.get("size"), 2.0)
    elif kind == "SPOT":
        data.spot_size = math.radians(_num(params.get("angle"), 45.0))
        data.spot_blend = _num(params.get("blend"), 0.3)
    if params.get("look_at") is not None:
        _look_at(light, _as_list(params.get("look_at"), 3))
    _step_all()
    return {"name": light.name, "type": kind, "energy": data.energy}


def _load_presets():
    out = {}
    try:
        for fn in sorted(os.listdir(PRESETS_DIR)):
            if not fn.lower().endswith(".json"):
                continue
            try:
                with open(os.path.join(PRESETS_DIR, fn), "r", encoding="utf-8") as f:
                    out[os.path.splitext(fn)[0]] = json.load(f)
            except (OSError, ValueError):
                continue
    except OSError:
        pass
    return out


def h_light_rig(params):
    preset_name = str(params.get("preset") or "product").lower()
    presets = _load_presets().get("lighting") or {}
    rig = presets.get(preset_name)
    if not rig:
        raise ValueError(f"没有灯光预设「{preset_name}」（可用：{', '.join(presets) or '无'}）")
    meshes = [o for o in _scene().objects if o.type == "MESH"]
    center, radius, (lo, hi) = _world_bbox(meshes)
    center = center if params.get("center") is None else Vector(_as_list(params.get("center"), 3))
    radius = max(radius, 0.5) * _num(params.get("scale"), 1.0)
    energy_k = max(0.15, (radius / 2.0) ** 2)

    world = rig.get("world") or {}
    if world:
        sc = _scene()
        sc.world = sc.world or bpy.data.worlds.new("World")
        sc.world.use_nodes = True
        bg = next((n for n in sc.world.node_tree.nodes if n.type == "BACKGROUND"), None)
        if bg:
            bg.inputs[0].default_value = _linear_rgba(world.get("color"), default=(0.02, 0.02, 0.025))
            bg.inputs[1].default_value = _num(world.get("strength"), 0.3)

    floor = rig.get("floor") or {}
    created = []
    if floor.get("enabled"):
        name = str(floor.get("name") or "Floor")
        if bpy.data.objects.get(name):
            bpy.data.objects.remove(bpy.data.objects[name], do_unlink=True)
        bpy.ops.mesh.primitive_plane_add(size=_num(floor.get("size"), 20.0) * max(radius / 2.0, 1.0),
                                         location=(center.x, center.y, lo.z))
        fl = bpy.context.object
        fl.name = name
        fl["bm_floor"] = True
        mat = _make_material(str(floor.get("material") or "FloorMat"), floor.get("color", "#0E1116"),
                             _num(floor.get("metallic"), 0.0), _num(floor.get("roughness"), 0.9))
        _assign_material(fl, mat)
        created.append(fl.name)

    for i, L in enumerate(rig.get("lights") or []):
        loc = Vector(_as_list(L.get("loc"), 3)) * radius + center
        name = str(L.get("name") or f"Light{i + 1}")
        if bpy.data.objects.get(name):
            bpy.data.objects.remove(bpy.data.objects[name], do_unlink=True)
        bpy.ops.object.light_add(type=str(L.get("kind") or "AREA").upper(), location=loc)
        lo = bpy.context.object
        lo.name = name
        lo.data.energy = _num(L.get("energy"), 500.0) * energy_k
        lo.data.color = _hex_rgb(L.get("color"), default=(1.0, 1.0, 1.0))[:3]
        if lo.data.type == "AREA":
            lo.data.size = _num(L.get("size"), 2.0) * radius
        _look_at(lo, center + Vector(_as_list(L.get("target", [0, 0, 0]), 3)) * radius)
        created.append(lo.name)
    _step_all()
    return {"preset": preset_name, "created": created,
            "center": [round(v, 3) for v in center], "radius": round(radius, 3)}


def h_world_set(params):
    sc = _scene()
    sc.world = sc.world or bpy.data.worlds.new("World")
    sc.world.use_nodes = True
    bg = next((n for n in sc.world.node_tree.nodes if n.type == "BACKGROUND"), None)
    if bg is None:
        bg = sc.world.node_tree.nodes.new("ShaderNodeBackground")
        out = next((n for n in sc.world.node_tree.nodes if n.type == "OUTPUT_WORLD"), None)
        if out:
            sc.world.node_tree.links.new(bg.outputs[0], out.inputs[0])
    bg.inputs[0].default_value = _linear_rgba(params.get("color"), default=(0.02, 0.02, 0.025))
    bg.inputs[1].default_value = _num(params.get("strength"), 1.0)
    _step_all()
    return {"color": params.get("color"), "strength": _num(params.get("strength"), 1.0)}


# ============================================================================
# 处理器：渲染 / 导出
# ============================================================================

def _engine_from_env():
    return os.environ.get("BLENDER_RENDER_ENGINE", "CYCLES").upper()


def h_render_image(params):
    sc = _scene()
    view = str(params.get("view") or "hero").lower()
    if view != "current" or sc.camera is None:
        h_camera_frame({"view": view, "camera": params.get("camera"),
                        "lens": params.get("lens", 50), "margin": params.get("margin", 1.25),
                        "ortho": params.get("ortho", False)})
    engine = str(params.get("engine") or _engine_from_env()).upper()
    if engine in ("EEVEE", "BLENDER_EEVEE"):
        engine = "BLENDER_EEVEE"
    if engine not in ("CYCLES", "BLENDER_EEVEE", "BLENDER_WORKBENCH"):
        raise ValueError("engine 只能是 CYCLES / BLENDER_EEVEE / BLENDER_WORKBENCH")
    sc.render.engine = engine
    res = params.get("resolution", 960)
    if isinstance(res, (list, tuple)) and len(res) >= 2:
        sc.render.resolution_x, sc.render.resolution_y = int(res[0]), int(res[1])
    else:
        sc.render.resolution_x = sc.render.resolution_y = max(64, _int(res, 960))
    sc.render.resolution_percentage = 100
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGBA" if params.get("transparent") else "RGB"
    sc.render.film_transparent = bool(params.get("transparent", False))
    samples = max(1, _int(params.get("samples"), 32))
    if engine == "CYCLES":
        sc.cycles.device = "CPU"
        sc.cycles.samples = samples
        sc.cycles.use_denoising = bool(params.get("denoise", True))
        sc.render.use_persistent_data = True
    elif engine == "BLENDER_EEVEE":
        sc.eevee.taa_render_samples = samples
    name = str(params.get("name") or sc.name or "render").strip()
    path = _safe_path(params.get("path") or f"{name}_{view}.png")
    path = _ensure_ext(path, "png")
    _ensure_dir(path)
    sc.render.filepath = path
    t = time.time()
    bpy.ops.render.render(write_still=True)
    return {"path": path, "engine": engine, "samples": samples,
            "resolution": [sc.render.resolution_x, sc.render.resolution_y],
            "seconds": round(time.time() - t, 2), "bytes": os.path.getsize(path) if os.path.exists(path) else 0}


#: 预览页「实时视图」的出图参数：Workbench 360px 实测 ~0.4s/帧（Cycles 同尺寸要
#: 1.4~2.8s，太慢；每步都出图会把建模节奏拖垮）。可用参数逐次覆盖。
LIVE_ENGINE = "BLENDER_WORKBENCH"
LIVE_PX = 360


def h_live_snapshot(params):
    """给预览页出一张**快速实时图**，让用户能一步步看着建模过程。

    两个硬约束：
      1. **必须完整恢复渲染设置**：引擎/分辨率/采样/文件路径/透明/Workbench 着色
         全部存起来，出图后原样写回 —— 否则模型自己设的 960px Cycles 会被改成
         360px Workbench，最终成品直接变糊。
      2. 场景为空（还没加对象）直接跳过，省一次渲染。
    """
    sc = _scene()
    if not sc.objects:
        return {"skipped": True, "reason": "场景为空", "objects": 0}
    if sc.camera is None:
        h_camera_frame({"view": str(params.get("view") or "hero")})
    px = max(160, _int(params.get("resolution"), LIVE_PX))
    engine = str(params.get("engine") or LIVE_ENGINE).upper()
    path = _safe_path(params.get("path") or os.path.join("_live", "view.png"))
    _ensure_dir(path)
    r = sc.render
    st = r.image_settings
    saved = {"engine": r.engine, "x": r.resolution_x, "y": r.resolution_y,
             "pct": r.resolution_percentage, "filepath": r.filepath,
             "fmt": st.file_format, "mode": st.color_mode,
             "transparent": r.film_transparent}
    shading = None
    try:
        sh = sc.display.shading
        shading = {"type": sh.type, "light": sh.light, "color_type": sh.color_type,
                   "show_shadows": sh.show_shadows, "show_cavity": sh.show_cavity,
                   "cavity_type": sh.cavity_type, "background_type": sh.background_type}
        saved["shading"] = shading
    except Exception:
        shading = None
    t = time.time()
    try:
        r.engine = engine
        r.resolution_x = r.resolution_y = px
        r.resolution_percentage = 100
        st.file_format = "PNG"
        st.color_mode = "RGB"
        if engine == "BLENDER_WORKBENCH" and shading is not None:
            # 让实时图接近成品观感：材质色 + 棚拍光 + 阴影/腔体 + 世界背景
            sh = sc.display.shading
            sh.type = "SOLID"
            sh.light = "STUDIO"
            sh.color_type = "MATERIAL"
            sh.show_shadows = True
            sh.show_cavity = True
            sh.background_type = "WORLD"
        r.filepath = path
        bpy.ops.render.render(write_still=True)
        return {"path": path, "resolution": px, "engine": engine,
                "seconds": round(time.time() - t, 3),
                "bytes": os.path.getsize(path) if os.path.exists(path) else 0}
    finally:
        # 无论成功失败都恢复，绝不让「看预览」污染正式渲染参数
        r.engine = saved["engine"]
        r.resolution_x, r.resolution_y = saved["x"], saved["y"]
        r.resolution_percentage = saved["pct"]
        r.filepath = saved["filepath"]
        st.file_format = saved["fmt"]
        st.color_mode = saved["mode"]
        r.film_transparent = saved["transparent"]
        if shading:
            try:
                sh = sc.display.shading
                sh.type = shading["type"]
                sh.light = shading["light"]
                sh.color_type = shading["color_type"]
                sh.show_shadows = shading["show_shadows"]
                sh.show_cavity = shading["show_cavity"]
                sh.cavity_type = shading["cavity_type"]
                sh.background_type = shading["background_type"]
            except Exception:
                pass


def h_export_model(params):
    fmt = str(params.get("format") or "glb").lower()
    name = str(params.get("name") or "model").strip()
    path = _safe_path(params.get("path") or f"{name}.{('glb' if fmt == 'glb' else fmt)}")
    _ensure_dir(path)
    selected_only = bool(params.get("selected_only", False))
    meshes = [o for o in _scene().objects if o.type == "MESH"]
    if selected_only:
        # 没有显式选择时退回「全部网格」，避免导出一个空文件
        if not any(o.select_get() for o in _scene().objects):
            for o in meshes:
                o.select_set(True)
    else:
        bpy.ops.object.select_all(action="DESELECT")
        for o in meshes:
            o.select_set(True)
    if fmt == "glb":
        path = _ensure_ext(path, "glb")
        bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=selected_only)
    elif fmt == "fbx":
        path = _ensure_ext(path, "fbx")
        bpy.ops.export_scene.fbx(filepath=path, use_selection=selected_only, apply_scale_options="FBX_SCALE_ALL")
    elif fmt == "obj":
        path = _ensure_ext(path, "obj")
        bpy.ops.wm.obj_export(filepath=path, export_selected_objects=selected_only) if hasattr(bpy.ops.wm, "obj_export") \
            else bpy.ops.export_scene.obj(filepath=path, use_selection=selected_only)
    elif fmt == "stl":
        path = _ensure_ext(path, "stl")
        bpy.ops.export_mesh.stl(filepath=path, use_selection=selected_only)
    else:
        raise ValueError("format 只能是 glb / fbx / obj / stl")
    _step_all()
    return {"path": path, "format": fmt, "bytes": os.path.getsize(path) if os.path.exists(path) else 0}


# ============================================================================
# 处理器：模板（数据驱动，新增模板只需加 presets/*.json）
# ============================================================================

def h_template_list(params):
    items = []
    for name, data in _load_presets().items():
        if name == "lighting" or not isinstance(data, dict):
            continue
        items.append({
            "template": name,
            "description": data.get("description", ""),
            "styles": sorted((data.get("styles") or {}).keys()),
            "params": data.get("params", {}),
        })
    return {"templates": items}


def _apply_preset_part(part, ctx, created):
    """按预设条目创建一个部件（含 repeat / bevel / boolean）。"""
    kind = str(part.get("kind") or "cube").lower()
    prefix = ctx["prefix"]
    base = str(part.get("name") or kind)
    count = max(1, _int((part.get("repeat") or {}).get("count"), 1))
    step = _as_list((part.get("repeat") or {}).get("step"), 3)
    for i in range(count):
        suffix = "" if count == 1 else f"_{i + 1}"
        name = f"{prefix}{base}{suffix}"
        base_loc = _as_list(part.get("loc"), 3)
        loc = [base_loc[k] * ctx["scale"] + step[k] * ctx["scale"] * i for k in range(3)]
        size = _num(part.get("size"), 1.0) * ctx["scale"]
        scl = _as_list(part.get("scale"), 3, 1.0)
        params = {
            "kind": kind, "name": name, "location": loc,
            "rotation": part.get("rotation", [0, 0, 0]), "scale": scl, "size": size,
            "segments": part.get("segments", 16), "vertices": part.get("vertices", 0),
            "shade": part.get("shade", "flat"), "body": part.get("body", ""),
            "align": part.get("align", "CENTER"),
        }
        out = h_object_add(params)
        obj = _obj(out["created"][0])
        if part.get("mat"):
            mat = ctx["materials"].get(str(part["mat"]))
            if mat:
                _assign_material(obj, mat)
        bevel = part.get("bevel")
        if bevel:
            if isinstance(bevel, dict):
                _add_bevel(obj, _num(bevel.get("width"), 0.01) * ctx["scale"],
                           bevel.get("segments", 2), bevel.get("angle", 30))
            elif _num(bevel) > 0:
                _add_bevel(obj, _num(bevel) * ctx["scale"], 2, 30)
        created.append(obj.name)
        boolean = part.get("boolean")
        if boolean and boolean.get("target"):
            target = _obj(f"{prefix}{boolean['target']}")
            mod = target.modifiers.new(f"Bool_{obj.name}", "BOOLEAN")
            mod.operation = str(boolean.get("op") or "DIFFERENCE").upper()
            mod.object = obj
            if bool(boolean.get("apply", True)):
                hole_name = obj.name
                _apply_modifier(target, mod)
                bpy.data.objects.remove(obj, do_unlink=True)
                if hole_name in created:
                    created.remove(hole_name)
    return created


def h_template_apply(params):
    tpl = str(params.get("template") or "").strip().lower()
    data = _load_presets().get(tpl)
    if not data:
        raise ValueError(f"没有模板「{tpl}」（可用：{', '.join(k for k in _load_presets() if k != 'lighting')}）")
    prefix = str(params.get("prefix") or "").strip()
    scale = _num(params.get("size"), 1.0) * _num(data.get("scale"), 1.0)
    colors = dict(data.get("palette") or {})
    colors.update({str(k): v for k, v in (params.get("colors") or {}).items()})
    props = data.get("materials") or {}
    materials = {}
    for key, color in colors.items():
        p = props.get(key) or {}
        materials[key] = _make_material(f"{prefix}{key}", color, _num(p.get("metallic"), 0.0),
                                        _num(p.get("roughness"), 0.55),
                                        emission_color=p.get("emission_color"),
                                        emission_strength=_num(p.get("emission_strength"), 0.0))
    # 根空物体：整只桌宠/整块电路板可以一次移动、缩放
    root_name = f"{prefix}{data.get('root') or (tpl + 'Root')}"
    if bpy.data.objects.get(root_name):
        bpy.data.objects.remove(bpy.data.objects[root_name], do_unlink=True)
    bpy.ops.object.empty_add(type="PLAIN_AXES", radius=0.5)
    root = bpy.context.object
    root.name = root_name

    style = str(params.get("style") or (data.get("styles") and next(iter(data["styles"]))) or "").lower()
    parts = list(data.get("parts") or [])
    styles = data.get("styles") or {}
    if style:
        if style not in styles:
            raise ValueError(f"模板 {tpl} 没有风格「{style}」（可用：{', '.join(styles)}）")
        parts += list(styles[style].get("parts") or [])

    ctx = {"prefix": prefix, "scale": scale, "materials": materials}
    created = []
    for part in parts:
        _apply_preset_part(part, ctx, created)
    for name in created:
        o = bpy.data.objects.get(name)
        if o:
            _parent(o, root)
    _step_all()
    return {"template": tpl, "style": style, "root": root_name, "scale": scale,
            "created": created, "count": len(created),
            "message": f"已生成模板 {tpl}（{len(created)} 个部件，根对象 {root_name}）"}


# ============================================================================
# 分发与主循环
# ============================================================================

HANDLERS = {
    "status": h_status,
    "live_snapshot": h_live_snapshot,
    "scene_new": h_scene_new,
    "scene_info": h_scene_info,
    "scene_save": h_scene_save,
    "scene_open": h_scene_open,
    "object_add": h_object_add,
    "object_transform": h_object_transform,
    "object_duplicate": h_object_duplicate,
    "object_delete": h_object_delete,
    "object_join": h_object_join,
    "object_shade": h_object_shade,
    "object_modifier": h_object_modifier,
    "object_boolean": h_object_boolean,
    "material_create": h_material_create,
    "material_assign": h_material_assign,
    "text_add": h_text_add,
    "camera_add": h_camera_add,
    "camera_frame": h_camera_frame,
    "light_add": h_light_add,
    "light_rig": h_light_rig,
    "world_set": h_world_set,
    "render_image": h_render_image,
    "export_model": h_export_model,
    "template_list": h_template_list,
    "template_apply": h_template_apply,
}


def _read_line(conn, buf: bytearray):
    """从 conn 读一行 → (状态, 内容)。

    状态语义：
      "eof"  → 对端关闭（服务端进程没了 / 主动停止），调用方应退出
      "idle" → 本轮空闲（未到 IDLE_TIMEOUT），调用方继续等
      "line" → 一行 JSON（不含换行）
      "blank"→ 空行（忽略即可；**不能**当成 EOF，否则凭空退出一次）

    为什么不 `conn.settimeout(5)` + `makefile().readline()`：socket 超时会让文件
    对象的内部缓冲处于不一致状态（官方文档明确警告），半行数据可能被丢弃；表现为
    读到 EOF 直接退出 → 服务端看到 Blender 消失 → 下一次调用又拉起 → 用户侧
    「Blender 实例频繁重启」。用 select + 自己缓冲则完全避开这个坑。
    """
    while True:
        nl = buf.find(b"\n")
        if nl >= 0:
            line = bytes(buf[:nl])
            del buf[:nl + 1]
            return ("line", line) if line else ("blank", b"")
        try:
            ready, _, _ = select.select([conn], [], [], 1.0)
        except (OSError, ValueError):
            return ("eof", b"")
        if not ready:
            return ("idle", b"")
        try:
            chunk = conn.recv(65536)
        except OSError:
            return ("eof", b"")
        if not chunk:
            return ("eof", b"")
        buf.extend(chunk)


def _send_all(conn, payload: bytes, timeout: float = 120.0) -> bool:
    """整包发送（select 控写就绪）：对端失联时不至于永久阻塞。"""
    view = memoryview(payload)
    deadline = time.monotonic() + float(timeout)
    while view:
        remain = deadline - time.monotonic()
        if remain <= 0:
            return False
        try:
            _, ready, _ = select.select([], [conn], [], min(remain, 1.0))
        except (OSError, ValueError):
            return False
        if not ready:
            continue
        try:
            n = conn.send(view)
        except OSError:
            return False
        if n <= 0:
            return False
        view = view[n:]
    return True


def _run(port):
    global _LAST_ACTIVITY
    conn = socket.create_connection(("127.0.0.1", port), timeout=30)
    conn.settimeout(None)          # 全阻塞：读超时由 select 控制（见 _read_line）
    buf = bytearray()
    if not _send_all(conn, json.dumps(
            {"hello": True, "blender": bpy.app.version_string}).encode() + b"\n"):
        return
    while True:
        kind, line = _read_line(conn, buf)
        if kind == "eof":
            break                      # 对端关闭 → 正常退出（服务端会自行决定是否重启）
        if kind == "idle":             # 空闲：到点就退出，避免长期占着 Blender
            if IDLE_TIMEOUT > 0 and time.time() - _LAST_ACTIVITY > IDLE_TIMEOUT:
                break
            continue
        if kind == "blank" or not line.strip():
            continue                   # 空行：忽略（既不响应也不退出）
        _LAST_ACTIVITY = time.time()
        try:
            msg = json.loads(line.decode("utf-8", "replace"))
        except ValueError:
            continue
        rid = msg.get("id")
        method = str(msg.get("method") or "")
        fn = HANDLERS.get(method)
        if fn is None:
            resp = {"id": rid, "ok": False, "error": f"未知方法: {method}"}
        else:
            try:
                resp = {"id": rid, "ok": True, "result": fn(msg.get("params") or {})}
            except Exception as e:
                resp = {"id": rid, "ok": False,
                        "error": f"{type(e).__name__}: {e}",
                        "trace": traceback.format_exc(limit=4)}
        _LAST_ACTIVITY = time.time()
        if not _send_all(conn, json.dumps(resp, ensure_ascii=False).encode("utf-8") + b"\n"):
            break


def main():
    global OUTPUT_DIR, PRESETS_DIR, IDLE_TIMEOUT
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    args = {}
    for i in range(0, len(argv) - 1, 2):
        args[argv[i].lstrip("-")] = argv[i + 1]
    OUTPUT_DIR = os.path.abspath(args.get("output") or os.getcwd())
    PRESETS_DIR = os.path.abspath(args.get("presets") or "")
    IDLE_TIMEOUT = _num(args.get("idle"), 1800.0)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    try:
        _run(_int(args.get("port"), 0))
    except Exception:
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
