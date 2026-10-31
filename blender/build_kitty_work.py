"""打工猫桌宠：精细化建模 + 骨骼绑定 + 多段动画 + 烘焙运行时资产 + 导出 GLB。

Blender 3.5 无界面运行：
    blender --background --factory-startup --python build_kitty_work.py

产出：
    blender/kitty_work.blend        可继续编辑的工程（含 6 段动作 + NLA）
    blender/kitty_work.glb          带动画的模型交付件（6 个 glTF 动画）
    blender/checks/*.png            Workbench 姿态校对图
    blender/hero.png                Cycles 成品图
    <out_prefix>.npz / .json        运行时资产（QOpenGL 桌宠直渲用，含 6 段动画）

坐标约定：Blender 世界空间（Z 轴朝上），小猫面朝 -Y（渲染侧根变换绕 X 轴 -90°
后 -Y 朝向相机）。单位：米。高度约 1.35 m。
"""
import json
import math
import os

import bpy
import numpy as np
from mathutils import Euler, Quaternion, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_BLEND = os.path.join(HERE, "kitty_work.blend")
OUT_GLB = os.path.join(HERE, "kitty_work.glb")
CHECK_DIR = os.path.join(HERE, "checks")
HERO_PNG = os.path.join(HERE, "hero.png")
RUNTIME_PREFIX = os.environ.get("KITTY_RUNTIME_PREFIX", os.path.join(
    os.path.expanduser("~"), ".zhuzhu_Copilot", "copilot_plugins",
    "kitty-work-pet", "assets", "kitty_work"))

FPS = 24
X, Y, Z = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)

#: 网格密度档位（精细化后：球体 28×16、四肢 16 边、细节件中等分段）
SEG_BIG = (28, 16)     # 躯干/脑袋这种大件
SEG_MID = (20, 12)     # 中件：肚皮/口鼻/大腿/爪子
SEG_SMALL = (14, 9)    # 小件：眼睛/关节球/铃铛
LIMB_V = 16            # 四肢圆柱边数
CONE_V = 12            # 耳朵等锥体边数


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #


def srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def hex_rgba(h: str) -> tuple:
    h = h.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    return (srgb_to_linear(r), srgb_to_linear(g), srgb_to_linear(b), 1.0)


def make_material(name, color, roughness=0.62, metallic=0.0, emission=None,
                  emission_strength=0.0):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    rgb = hex_rgba(color)
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = rgb
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    if emission:
        bsdf.inputs["Emission"].default_value = hex_rgba(emission)
        bsdf.inputs["Emission Strength"].default_value = emission_strength
    mat.diffuse_color = rgb          # 运行时资产取这个当部件色（线性空间）
    mat.roughness = roughness
    mat.metallic = metallic
    return mat


def _finish(obj, name, material, bevel=0.0, segments=3, smooth=True):
    obj.name = name
    obj.data.name = name
    if obj.data.materials:
        obj.data.materials[0] = material
    else:
        obj.data.materials.append(material)
    if bevel > 0:
        mod = obj.modifiers.new("bevel", "BEVEL")
        mod.width = bevel
        mod.segments = segments
        mod.limit_method = "ANGLE"
        mod.angle_limit = math.radians(40)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.modifier_apply(modifier=mod.name)
    for p in obj.data.polygons:
        p.use_smooth = smooth
    return obj


def _apply(obj):
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    obj.select_set(False)
    return obj


def add_sphere(name, loc, radius, scale=(1, 1, 1), seg=SEG_MID,
               material=None, rot=(0, 0, 0), smooth=True):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=seg[0], ring_count=seg[1],
                                         radius=radius, location=loc)
    obj = bpy.context.active_object
    obj.scale = scale
    obj.rotation_euler = Euler([math.radians(a) for a in rot], "XYZ")
    _apply(obj)
    return _finish(obj, name, material, smooth=smooth)


def add_box(name, loc, size, rot=(0, 0, 0), material=None, bevel=0.0,
            smooth=False):
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=loc)
    obj = bpy.context.active_object
    obj.scale = size
    obj.rotation_euler = Euler([math.radians(a) for a in rot], "XYZ")
    _apply(obj)
    return _finish(obj, name, material, bevel=bevel, smooth=smooth)


def add_torus(name, loc, major, minor, rot=(0, 0, 0), mseg=24, nseg=10,
              material=None, smooth=True):
    bpy.ops.mesh.primitive_torus_add(major_radius=major, minor_radius=minor,
                                     major_segments=mseg, minor_segments=nseg,
                                     location=loc)
    obj = bpy.context.active_object
    obj.rotation_euler = Euler([math.radians(a) for a in rot], "XYZ")
    _apply(obj)
    return _finish(obj, name, material, smooth=smooth)


def add_cone(name, loc, r1, r2, depth, rot=(0, 0, 0), vertices=CONE_V,
             material=None, smooth=True):
    bpy.ops.mesh.primitive_cone_add(vertices=vertices, radius1=r1, radius2=r2,
                                    depth=depth, location=loc)
    obj = bpy.context.active_object
    obj.rotation_euler = Euler([math.radians(a) for a in rot], "XYZ")
    _apply(obj)
    return _finish(obj, name, material, smooth=smooth)


def add_cylinder(name, loc, radius, depth, rot=(0, 0, 0), vertices=LIMB_V,
                 material=None, smooth=True):
    bpy.ops.mesh.primitive_cylinder_add(vertices=vertices, radius=radius,
                                        depth=depth, location=loc)
    obj = bpy.context.active_object
    obj.rotation_euler = Euler([math.radians(a) for a in rot], "XYZ")
    _apply(obj)
    return _finish(obj, name, material, smooth=smooth)


def add_limb(name, p0, p1, r0, r1=None, vertices=LIMB_V, material=None,
             smooth=True):
    """两点之间的锥形圆柱（四肢/尾巴段）。两端球关节配合，消灭接缝。"""
    p0, p1 = Vector(p0), Vector(p1)
    d = p1 - p0
    r1 = r0 if r1 is None else r1
    bpy.ops.mesh.primitive_cone_add(vertices=vertices, radius1=r0, radius2=r1,
                                    depth=d.length, location=(p0 + p1) * 0.5)
    obj = bpy.context.active_object
    obj.rotation_mode = "QUATERNION"
    obj.rotation_quaternion = Vector((0, 0, 1)).rotation_difference(
        d.normalized())
    _apply(obj)
    return _finish(obj, name, material, smooth=smooth)


def clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for block in (bpy.data.meshes, bpy.data.materials, bpy.data.armatures,
                  bpy.data.actions, bpy.data.curves, bpy.data.objects):
        for item in list(block):
            if item.users == 0:
                block.remove(item)


# --------------------------------------------------------------------------- #
# 建模
# --------------------------------------------------------------------------- #

PARTS = []          # [(object, bone)]


def reg(obj, bone):
    PARTS.append((obj, bone))
    return obj


def build_laptop(m, deck_m, glow_m, accent_m):
    """笔记本电脑：机身 / 屏幕 / 键帽 / 触控板 / 屏幕 UI 发光块。"""
    reg(add_box("laptop_base", (0.0, -0.440, 0.018), (0.56, 0.40, 0.036),
                bevel=0.012, material=m), "Root")
    reg(add_box("laptop_deck", (0.0, -0.440, 0.040), (0.505, 0.300, 0.010),
                bevel=0.004, material=deck_m), "Root")
    reg(add_box("laptop_hinge", (0.0, -0.632, 0.036), (0.52, 0.024, 0.020),
                bevel=0.006, material=m), "Root")
    reg(add_box("laptop_lid", (0.0, -0.650, 0.215), (0.555, 0.018, 0.365),
                rot=(14, 0, 0), bevel=0.012, material=m), "Root")
    # 屏幕边框 + 发光显示区（随盖子 14° 后仰）
    reg(add_box("laptop_bezel", (0.0, -0.6415, 0.215), (0.510, 0.008, 0.318),
                rot=(14, 0, 0), bevel=0.004, material=deck_m), "Root")
    reg(add_box("laptop_display", (0.0, -0.6385, 0.215), (0.478, 0.008, 0.288),
                rot=(14, 0, 0), bevel=0.002, material=glow_m), "Root")
    # 屏幕 UI：三行发光的「代码行」小条
    for i, (w, xoff) in enumerate(((0.30, -0.06), (0.20, -0.11), (0.34, -0.04),
                                  (0.14, -0.14), (0.26, -0.08))):
        reg(add_box("screen_ui_%d" % i,
                    (xoff, -0.6335, 0.300 - i * 0.033), (w, 0.006, 0.014),
                    rot=(14, 0, 0), bevel=0.002, material=accent_m), "Root")
    reg(add_torus("laptop_logo", (0.0, -0.663, 0.212), 0.040, 0.009,
                  rot=(104, 0, 0), mseg=20, nseg=8, material=deck_m), "Root")
    # 触控板
    reg(add_box("laptop_pad", (0.0, -0.290, 0.0455), (0.150, 0.078, 0.005),
                bevel=0.002, material=m), "Root")
    # 键帽：4 行 x 10 列（合并成一块）+ 独立空格键
    keys = []
    for row in range(4):
        for col in range(10):
            o = add_box("k_%d_%d" % (row, col),
                        (-0.225 + col * 0.050, -0.352 - row * 0.042, 0.0475),
                        (0.040, 0.030, 0.009), bevel=0.0015)
            keys.append(o)
    bpy.ops.object.select_all(action="DESELECT")
    for o in keys:
        o.select_set(True)
    bpy.context.view_layer.objects.active = keys[0]
    bpy.ops.object.join()
    keyobj = bpy.context.active_object
    keyobj.name = "laptop_keys"
    keyobj.data.name = "laptop_keys"
    keyobj.data.materials.clear()
    keyobj.data.materials.append(m)
    for p in keyobj.data.polygons:
        p.use_smooth = False
    reg(keyobj, "Root")
    reg(add_box("laptop_space", (0.0, -0.492, 0.0475), (0.240, 0.028, 0.009),
                bevel=0.0015, material=deck_m), "Root")


def build_desktop_props(mug_m, mug_in_m, mouse_m, pad_m):
    """桌面小物：马克杯（带把手）+ 鼠标垫 + 鼠标。绑 Root（不随猫动）。"""
    reg(add_cylinder("mug_body", (-0.395, -0.315, 0.058), 0.052, 0.092,
                     material=mug_m), "Root")
    reg(add_cylinder("mug_inner", (-0.395, -0.315, 0.101), 0.042, 0.006,
                     material=mug_in_m), "Root")
    reg(add_torus("mug_handle", (-0.443, -0.315, 0.062), 0.032, 0.009,
                  rot=(0, 90, 0), mseg=20, nseg=8, material=mug_m), "Root")
    reg(add_box("mouse_pad", (0.392, -0.352, 0.0025), (0.190, 0.230, 0.005),
                bevel=0.004, material=pad_m), "Root")
    reg(add_sphere("mouse_body", (0.392, -0.362, 0.030), 0.050,
                   scale=(0.72, 1.28, 0.62), seg=SEG_SMALL, material=mouse_m),
        "Root")


def build_cat(m):
    fur, fur_white, fur_dark = m["fur"], m["white"], m["dark"]
    eye_m, nose_m, blush_m, metal_m = m["eye"], m["nose"], m["blush"], m["metal"]

    # ---------------- 躯干 ----------------
    reg(add_sphere("kitty_body", (0.0, 0.055, 0.455), 0.295,
                   scale=(1.02, 0.94, 1.10), seg=SEG_BIG, material=fur), "Spine")
    reg(add_sphere("kitty_belly", (0.0, -0.100, 0.420), 0.178,
                   scale=(0.95, 0.52, 1.05), seg=SEG_MID,
                   material=fur_white), "Spine")
    reg(add_sphere("kitty_chest", (0.0, 0.0, 0.700), 0.228,
                   scale=(1.0, 0.92, 0.90), seg=SEG_BIG, material=fur), "Chest")
    # 项圈 + 铃铛
    reg(add_torus("kitty_collar", (0.0, -0.020, 0.812), 0.152, 0.022,
                  rot=(10, 0, 0), mseg=28, nseg=10, material=fur_dark), "Chest")
    reg(add_sphere("kitty_bell", (0.0, -0.163, 0.786), 0.030,
                   scale=(1.0, 0.85, 1.0), seg=SEG_SMALL, material=metal_m),
        "Chest")

    # ---------------- 头 ----------------
    reg(add_sphere("kitty_head", (0.0, -0.095, 0.995), 0.288,
                   scale=(1.0, 0.93, 0.905), seg=SEG_BIG, material=fur), "Head")
    reg(add_sphere("kitty_muzzle", (0.0, -0.318, 0.940), 0.100,
                   scale=(1.30, 0.58, 0.86), seg=SEG_MID,
                   material=fur_white), "Head")
    reg(add_sphere("kitty_nose", (0.0, -0.386, 0.972), 0.029,
                   scale=(1.30, 0.80, 0.80), seg=SEG_SMALL,
                   material=nose_m), "Head")

    for side, sx in (("l", -1.0), ("r", 1.0)):
        ear_bone = "L_Ear" if side == "l" else "R_Ear"
        # 眼睛 + 高光
        reg(add_sphere("kitty_eye_%s" % side, (sx * 0.113, -0.298, 1.048), 0.056,
                       scale=(0.88, 0.52, 1.02), seg=SEG_SMALL,
                       material=eye_m), "Head")
        reg(add_sphere("kitty_glint_%s" % side,
                       (sx * 0.128, -0.330, 1.070), 0.017,
                       scale=(0.9, 0.5, 1.0), seg=(10, 7),
                       material=fur_white), "Head")
        # 腮红
        reg(add_sphere("kitty_blush_%s" % side,
                       (sx * 0.196, -0.246, 0.972), 0.040,
                       scale=(0.75, 0.35, 0.60), seg=SEG_SMALL,
                       material=blush_m), "Head")
        # 耳朵（外壳 + 深色内耳）
        reg(add_cone("kitty_ear_%s" % side, (sx * 0.176, -0.048, 1.292),
                     0.107, 0.0, 0.240, rot=(0, -sx * 18.0, 0),
                     material=fur), ear_bone)
        reg(add_cone("kitty_erin_%s" % side, (sx * 0.172, -0.082, 1.286),
                     0.066, 0.0, 0.180, rot=(0, -sx * 18.0, 0),
                     material=fur_dark), ear_bone)
        # 胡须（每侧两根细锥）
        for k, (dy, dz, tilt) in enumerate(((-0.010, 0.012, 12.0),
                                            (-0.012, -0.010, -10.0))):
            reg(add_limb("kitty_whisker_%s%d" % (side, k),
                         (sx * 0.086, -0.352 + dy, 0.952 + dz),
                         (sx * 0.255, -0.392 + dy, 0.985 + dz),
                         0.0055, 0.0022, vertices=6, material=fur_white),
                "Head")

    # 圆框眼镜（两个环 + 鼻梁）——精致小细节
    for side, sx in (("l", -1.0), ("r", 1.0)):
        reg(add_torus("kitty_glass_%s" % side,
                      (sx * 0.113, -0.352, 1.048), 0.070, 0.0075,
                      rot=(90, 0, 0), mseg=24, nseg=8, material=fur_dark),
            "Head")
    reg(add_box("kitty_glass_bridge", (0.0, -0.352, 1.052),
                (0.055, 0.008, 0.0075), bevel=0.002, material=fur_dark), "Head")

    # ---------------- 前腿：肩球 → 上臂（外张）→ 肘球 → 前臂（内收落键盘）→ 爪 ----------------
    for side, sx in (("l", -1.0), ("r", 1.0)):
        b = "L_" if side == "l" else "R_"
        sh = (sx * 0.243, -0.020, 0.680)
        el = (sx * 0.330, -0.148, 0.452)
        wr = (sx * 0.156, -0.330, 0.152)
        reg(add_sphere("kitty_shoulder_%s" % side, sh, 0.086, seg=SEG_MID,
                       material=fur), b + "Arm")
        reg(add_limb("kitty_arm_%s" % side, sh, el, 0.082, 0.072,
                     material=fur), b + "Arm")
        reg(add_sphere("kitty_elbow_%s" % side, el, 0.072, seg=SEG_MID,
                       material=fur), b + "Forearm")
        reg(add_limb("kitty_forearm_%s" % side, el, wr, 0.068, 0.052,
                     material=fur), b + "Forearm")
        # 爪：掌心朝下压在键帽上（键帽顶面 z=0.052）。
        # 抬高 2cm：实测绑定姿态爪底比键帽低 2cm（measure_paws.py），
        # 抬到 0.123 后静止时刚好贴键、敲击时下压约 1cm，不再穿进键帽。
        reg(add_sphere("kitty_paw_%s" % side, (sx * 0.132, -0.352, 0.123), 0.076,
                       scale=(1.0, 1.15, 0.62), seg=SEG_MID,
                       material=fur_white), b + "Paw")
        # 爪上的三个小趾垫（提高精致度）
        for k, dx in enumerate((-0.030, 0.0, 0.030)):
            reg(add_sphere("kitty_toe_%s%d" % (side, k),
                           (sx * 0.132 + dx, -0.404, 0.106), 0.019,
                           scale=(0.9, 1.1, 0.7), seg=(10, 7),
                           material=blush_m), b + "Paw")

    # ---------------- 后腿（坐姿） ----------------
    for side, sx in (("l", -1.0), ("r", 1.0)):
        bone = "L_Hindleg" if side == "l" else "R_Hindleg"
        reg(add_sphere("kitty_thigh_%s" % side, (sx * 0.252, 0.085, 0.270), 0.160,
                       scale=(0.85, 1.00, 0.96), seg=SEG_MID,
                       material=fur), bone)
        reg(add_sphere("kitty_foot_%s" % side, (sx * 0.205, -0.018, 0.080), 0.100,
                       scale=(0.92, 1.20, 0.58), seg=SEG_MID,
                       material=fur_white), bone)

    # ---------------- 尾巴（三段 + 关节球 + 环纹 + 白尾尖） ----------------
    t1 = (0.0, 0.255, 0.395)
    t2 = (0.060, 0.418, 0.312)
    t3 = (0.180, 0.478, 0.418)
    t4 = (0.252, 0.412, 0.640)
    reg(add_limb("kitty_tail_1", t1, t2, 0.090, 0.080, material=fur), "Tail_1")
    reg(add_sphere("kitty_tail_j2", t2, 0.080, seg=SEG_SMALL, material=fur),
        "Tail_2")
    reg(add_limb("kitty_tail_2", t2, t3, 0.079, 0.066, material=fur), "Tail_2")
    reg(add_sphere("kitty_tail_j3", t3, 0.066, seg=SEG_SMALL, material=fur),
        "Tail_3")
    reg(add_limb("kitty_tail_3", t3, t4, 0.065, 0.040, material=fur), "Tail_3")
    reg(add_sphere("kitty_tail_tip", t4, 0.043, seg=SEG_SMALL,
                   material=fur_white), "Tail_3")
    # 两道环纹（绕尾巴的细环）
    reg(add_torus("kitty_tail_ring1", (0.118, 0.452, 0.372), 0.070, 0.012,
                  rot=(58, 0, -34), mseg=20, nseg=8, material=fur_dark),
        "Tail_2")
    reg(add_torus("kitty_tail_ring2", (0.216, 0.442, 0.545), 0.053, 0.010,
                  rot=(-28, 0, -30), mseg=20, nseg=8, material=fur_dark),
        "Tail_3")


def build_scene():
    clear_scene()
    PARTS.clear()
    sc = bpy.context.scene
    sc.render.fps = FPS
    sc.unit_settings.system = "METRIC"

    mats = {
        "fur": make_material("kitty_fur", "#A9B7CD", 0.68),
        "white": make_material("kitty_white", "#FFFFFF", 0.62),
        "dark": make_material("kitty_dark", "#1E2E44", 0.55),
        "eye": make_material("kitty_eye", "#0F1720", 0.28),
        "nose": make_material("kitty_nose", "#E08C8C", 0.48),
        "blush": make_material("kitty_blush", "#EFA0A8", 0.55),
        "metal": make_material("kitty_bell_gold", "#E8B64C", 0.24, metallic=0.9),
        "chassis": make_material("laptop_chassis", "#9AA5B6", 0.32, metallic=0.65),
        "deck": make_material("laptop_deck", "#141B27", 0.52),
        "glow": make_material("laptop_screen", "#0A1018", 0.28,
                              emission="#3D7DD0", emission_strength=1.6),
        "accent": make_material("laptop_ui", "#8FE3FF", 0.25,
                                emission="#8FE3FF", emission_strength=4.5),
        "mug": make_material("mug_body", "#E4E9F0", 0.35),
        "mug_in": make_material("mug_coffee", "#3A2418", 0.30),
        "mouse": make_material("mouse_body", "#2A3444", 0.42),
        "pad": make_material("mouse_pad", "#3C4A5E", 0.72),
    }

    build_laptop(mats["chassis"], mats["deck"], mats["glow"], mats["accent"])
    build_desktop_props(mats["mug"], mats["mug_in"], mats["mouse"], mats["pad"])
    build_cat(mats)
    return PARTS


# --------------------------------------------------------------------------- #
# 骨架
# --------------------------------------------------------------------------- #

BONES = [
    ("Root", (0.0, 0.0, 0.0), (0.0, 0.0, 0.18), None),
    ("Spine", (0.0, 0.04, 0.20), (0.0, 0.02, 0.52), "Root"),
    ("Chest", (0.0, 0.02, 0.52), (0.0, -0.03, 0.74), "Spine"),
    ("Neck", (0.0, -0.03, 0.74), (0.0, -0.06, 0.84), "Chest"),
    ("Head", (0.0, -0.06, 0.84), (0.0, -0.10, 1.20), "Neck"),
    ("L_Ear", (-0.160, -0.048, 1.230), (-0.208, -0.040, 1.400), "Head"),
    ("R_Ear", (0.160, -0.048, 1.230), (0.208, -0.040, 1.400), "Head"),
    ("L_Arm", (-0.243, -0.020, 0.680), (-0.330, -0.148, 0.452), "Chest"),
    ("L_Forearm", (-0.330, -0.148, 0.452), (-0.156, -0.330, 0.152), "L_Arm"),
    ("L_Paw", (-0.156, -0.330, 0.152), (-0.132, -0.352, 0.050), "L_Forearm"),
    ("R_Arm", (0.243, -0.020, 0.680), (0.330, -0.148, 0.452), "Chest"),
    ("R_Forearm", (0.330, -0.148, 0.452), (0.156, -0.330, 0.152), "R_Arm"),
    ("R_Paw", (0.156, -0.330, 0.152), (0.132, -0.352, 0.050), "R_Forearm"),
    ("L_Hindleg", (-0.240, 0.080, 0.340), (-0.205, -0.018, 0.090), "Spine"),
    ("R_Hindleg", (0.240, 0.080, 0.340), (0.205, -0.018, 0.090), "Spine"),
    ("Tail_1", (0.0, 0.255, 0.395), (0.060, 0.418, 0.312), "Spine"),
    ("Tail_2", (0.060, 0.418, 0.312), (0.180, 0.478, 0.418), "Tail_1"),
    ("Tail_3", (0.180, 0.478, 0.418), (0.252, 0.412, 0.640), "Tail_2"),
]


def build_armature():
    arm_data = bpy.data.armatures.new("KittyWorkRig")
    arm = bpy.data.objects.new("KittyWorkRig", arm_data)
    bpy.context.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    for name, h, t, _p in BONES:
        eb = arm_data.edit_bones.new(name)
        eb.head = Vector(h)
        eb.tail = Vector(t)
        eb.roll = 0.0
    for name, _h, _t, parent in BONES:
        if parent:
            arm_data.edit_bones[name].parent = arm_data.edit_bones[parent]
    bpy.ops.object.mode_set(mode="OBJECT")
    return arm


def bind_parts(parts, arm):
    """每个部件整块绑到一根骨头（刚性蒙皮）：同名顶点组权重 1 + Armature 修改器。"""
    for obj, bone in parts:
        vg = obj.vertex_groups.new(name=bone)
        vg.add(list(range(len(obj.data.vertices))), 1.0, "REPLACE")
        mod = obj.modifiers.new("armature", "ARMATURE")
        mod.object = arm
        mod.use_vertex_groups = True
        obj.parent = arm


# --------------------------------------------------------------------------- #
# 动画：绕世界轴的姿态（自动换算到骨骼局部轴），每段一个 action
# --------------------------------------------------------------------------- #


def _axis_local(pb, axis_world):
    m = pb.bone.matrix_local.to_3x3()
    return (m.inverted() @ Vector(axis_world)).normalized()


def set_bone(pb, rots=None, loc_world=None):
    q = Quaternion((1.0, 0.0, 0.0, 0.0))
    for axis, deg in (rots or []):
        q = q @ Quaternion(_axis_local(pb, axis), math.radians(deg))
    pb.rotation_mode = "QUATERNION"
    pb.rotation_quaternion = q
    if loc_world:
        pb.location = pb.bone.matrix_local.to_3x3().inverted() @ Vector(loc_world)
    else:
        pb.location = (0.0, 0.0, 0.0)


def env(p, a, b, c, d):
    """包络：0→1 (a..b)，保持 (b..c)，1→0 (c..d)，两端 smoothstep 过渡。"""
    def ss(x):
        x = max(0.0, min(1.0, x))
        return x * x * (3.0 - 2.0 * x)
    if p <= a or p >= d:
        return 0.0
    if p < b:
        return ss((p - a) / max(1e-6, b - a))
    if p <= c:
        return 1.0
    return 1.0 - ss((p - c) / max(1e-6, d - c))


def base_pose():
    """所有动画共用的坐姿：微前倾、低头看屏幕；双爪本来就在键帽上，不做额外平移。"""
    return {
        "Spine": ([(X, 3.0)], None),
        "Chest": ([(X, 1.0)], None),
        "Neck": ([(X, 3.0)], None),
        "Head": ([(X, 8.0)], None),
        "Tail_1": ([(Z, -8.0)], None),
    }


def merge(base, extra):
    out = {k: (list(v[0]) if v[0] else [], Vector(v[1]) if v[1] else None)
           for k, v in base.items()}
    for name, (rots, loc) in extra.items():
        cur_r, cur_l = out.get(name, ([], None))
        if rots:
            cur_r = list(cur_r) + list(rots)
        if loc:
            cur_l = (cur_l or Vector((0, 0, 0))) + Vector(loc)
        out[name] = (cur_r, cur_l)
    return out


def pose_typing(p):
    """双爪交替敲击键盘（相位错开 180°），配合身体起伏、耳朵抖动与甩尾。"""
    def tap(ph):
        return max(0.0, math.sin(2.0 * math.pi * ph)) ** 1.7
    tl, tr = tap(p * 4.0), tap(p * 4.0 + 0.5)
    bob = math.sin(2.0 * math.pi * p * 2.0)
    swing = math.sin(2.0 * math.pi * p)
    return merge(base_pose(), {
        "Spine": ([(X, 1.4 * bob)], (0.0, 0.0, 0.005 * bob)),
        "Chest": ([(X, 1.0 * bob)], None),
        "Head": ([(X, 2.0 + 1.8 * bob), (Z, 1.2 * swing)], None),
        # 抬爪 4°、落爪 -4°，配合下压 1.2cm 表现"按到键"
        "L_Arm": ([(X, -4.0 * tl)], None),
        "L_Forearm": ([(X, -7.0 * tl)], None),
        "L_Paw": ([(X, -6.0 + 13.0 * tl)], (0.0, 0.0, -0.013 * tl)),
        "R_Arm": ([(X, -4.0 * tr)], None),
        "R_Forearm": ([(X, -7.0 * tr)], None),
        "R_Paw": ([(X, -6.0 + 13.0 * tr)], (0.0, 0.0, -0.013 * tr)),
        "L_Ear": ([(Y, -5.0 * tl)], None),
        "R_Ear": ([(Y, 5.0 * tr)], None),
        "Tail_1": ([(Z, 7.0 * swing)], None),
        "Tail_2": ([(Z, 9.0 * math.sin(2.0 * math.pi * p - 0.5))], None),
        "Tail_3": ([(Z, 11.0 * math.sin(2.0 * math.pi * p - 1.0))], None),
    })


def pose_idle(p):
    breath = math.sin(2.0 * math.pi * p)
    look = math.sin(2.0 * math.pi * p - 0.8)
    flick = max(0.0, math.sin(2.0 * math.pi * p * 3.0)) ** 6
    return merge(base_pose(), {
        "Spine": ([(X, 1.0 * breath)], (0.0, 0.0, 0.004 * breath)),
        "Chest": ([(X, 1.6 * breath)], None),
        "Neck": ([(Z, 2.0 * look)], None),
        "Head": ([(Z, 8.0 * look), (X, -2.0 + 1.5 * breath)], None),
        "L_Ear": ([(Y, -9.0 * flick)], None),
        "R_Ear": ([(Y, 6.0 * flick)], None),
        "Tail_1": ([(Z, 8.0 * math.sin(2.0 * math.pi * p - 0.3))], None),
        "Tail_2": ([(Z, 10.0 * math.sin(2.0 * math.pi * p - 0.9))], None),
        "Tail_3": ([(Z, 12.0 * math.sin(2.0 * math.pi * p - 1.5))], None),
    })


def pose_wave(p):
    """抬起右爪打招呼 + 小幅弹跳。"""
    up = env(p, 0.0, 0.22, 0.76, 1.0)
    wave = math.sin(2.0 * math.pi * p * 3.0) * up
    hop = math.sin(2.0 * math.pi * min(1.0, p * 2.0)) * up
    return merge(base_pose(), {
        "Spine": ([(Z, -3.0 * up), (X, -2.0 * up)], (0.0, 0.0, 0.012 * hop)),
        "Chest": ([(Z, -2.0 * up)], None),
        "Neck": ([(Z, -4.0 * up)], None),
        "Head": ([(Z, -7.0 * up), (X, -6.0 * up + 2.0 * wave)], None),
        "R_Arm": ([(Y, -120.0 * up), (X, 10.0 * wave)], None),
        "R_Forearm": ([(Y, -20.0 * up), (X, 14.0 * wave)], None),
        "R_Paw": ([(Z, 18.0 * wave), (X, -10.0 * up)], None),
        "L_Ear": ([(Y, -12.0 * up)], None),
        "R_Ear": ([(Y, 4.0 * up + 6.0 * wave)], None),
        "Tail_1": ([(Z, 14.0 * up * math.sin(2.0 * math.pi * p * 2.0))], None),
        "Tail_2": ([(Z, 18.0 * up * math.sin(2.0 * math.pi * p * 2.0 - 0.6))], None),
        "Tail_3": ([(Z, 22.0 * up * math.sin(2.0 * math.pi * p * 2.0 - 1.2))], None),
    })


def pose_excited(p):
    """兴奋爆肝：高频敲击 + 弹跳 + 耳朵立起。"""
    def tap(ph):
        return max(0.0, math.sin(2.0 * math.pi * ph)) ** 1.4
    tl, tr = tap(p * 6.0), tap(p * 6.0 + 0.5)
    bounce = abs(math.sin(2.0 * math.pi * p * 2.0))
    return merge(base_pose(), {
        "Spine": ([(X, -2.0 - 3.0 * bounce)], (0.0, 0.0, 0.014 * bounce)),
        "Chest": ([(X, -2.0 * bounce)], None),
        "Neck": ([(X, -4.0)], None),
        "Head": ([(X, -10.0 - 2.0 * bounce)], None),
        "L_Arm": ([(X, -5.0 * tl)], None),
        "L_Forearm": ([(X, -8.0 * tl)], None),
        "L_Paw": ([(X, -6.0 + 15.0 * tl)], (0.0, 0.0, -0.011 * tl)),
        "R_Arm": ([(X, -5.0 * tr)], None),
        "R_Forearm": ([(X, -8.0 * tr)], None),
        "R_Paw": ([(X, -6.0 + 15.0 * tr)], (0.0, 0.0, -0.011 * tr)),
        "L_Ear": ([(Y, 10.0)], None),
        "R_Ear": ([(Y, -10.0)], None),
        "Tail_1": ([(Z, 16.0 * math.sin(2.0 * math.pi * p * 2.0))], None),
        "Tail_2": ([(Z, 20.0 * math.sin(2.0 * math.pi * p * 2.0 - 0.7))], None),
        "Tail_3": ([(Z, 24.0 * math.sin(2.0 * math.pi * p * 2.0 - 1.4))], None),
    })


def pose_sleep(p):
    """趴在键盘上打瞌睡：后半仰、低头、耳朵下垂、呼吸起伏。"""
    breath = math.sin(2.0 * math.pi * p)
    return merge(base_pose(), {
        "Spine": ([(X, 4.0)], (0.0, 0.0, -0.008 + 0.005 * breath)),
        "Chest": ([(X, 8.0 + 1.2 * breath)], None),
        "Neck": ([(X, 14.0)], None),
        "Head": ([(X, 26.0 + 1.0 * breath), (Z, 4.0)], None),
        "L_Arm": ([(X, 4.0)], None),
        "R_Arm": ([(X, 4.0)], None),
        "L_Paw": ([(X, 4.0)], (0.0, -0.01, 0.004)),
        "R_Paw": ([(X, 4.0)], (0.0, -0.01, 0.004)),
        "L_Ear": ([(Y, -26.0)], None),
        "R_Ear": ([(Y, 26.0)], None),
        "Tail_1": ([(Z, -14.0)], None),
        "Tail_2": ([(Z, -18.0), (X, 6.0)], None),
        "Tail_3": ([(Z, -22.0)], None),
    })


def pose_stretch(p):
    """伸懒腰：双爪举高、身体后仰、尾巴翘起。"""
    up = env(p, 0.0, 0.28, 0.68, 1.0)
    tremble = math.sin(2.0 * math.pi * p * 5.0) * up
    return merge(base_pose(), {
        "Spine": ([(X, -9.0 * up)], (0.0, 0.0, 0.018 * up)),
        "Chest": ([(X, -7.0 * up)], None),
        "Neck": ([(X, -6.0 * up)], None),
        "Head": ([(X, -18.0 * up + 2.0 * tremble)], None),
        "L_Arm": ([(Y, 134.0 * up), (X, 6.0 * tremble)], None),
        "R_Arm": ([(Y, -134.0 * up), (X, 6.0 * tremble)], None),
        "L_Forearm": ([(Y, 18.0 * up)], None),
        "R_Forearm": ([(Y, -18.0 * up)], None),
        "L_Paw": ([(X, -20.0 * up)], None),
        "R_Paw": ([(X, -20.0 * up)], None),
        "L_Ear": ([(Y, -14.0 * up)], None),
        "R_Ear": ([(Y, 14.0 * up)], None),
        "Tail_1": ([(Z, 6.0 * tremble), (X, -8.0 * up)], None),
        "Tail_2": ([(Z, 8.0 * tremble)], None),
        "Tail_3": ([(Z, 10.0 * tremble)], None),
    })


#: (名字, 帧数, 姿态函数, 是否循环, 说明)
CLIPS = [
    ("typing", 40, pose_typing, True, "双爪交替敲键盘"),
    ("idle", 72, pose_idle, True, "歇一会：呼吸、张望、甩尾"),
    ("wave", 48, pose_wave, False, "抬爪打招呼"),
    ("excited", 30, pose_excited, True, "兴奋爆肝：快速敲击 + 弹跳"),
    ("sleep", 96, pose_sleep, True, "趴下打瞌睡"),
    ("stretch", 60, pose_stretch, False, "伸懒腰"),
]


def bake_actions(arm):
    sc = bpy.context.scene
    if arm.animation_data is None:
        arm.animation_data_create()
    made = []
    for name, count, fn, loop, _d in CLIPS:
        act = bpy.data.actions.new("Kitty_" + name)
        act.use_fake_user = True
        arm.animation_data.action = act
        for f in range(1, count + 1):
            p = (f - 1) / float(count) if loop else (f - 1) / float(max(1, count - 1))
            pose = fn(p)
            for pb in arm.pose.bones:
                rots, loc = pose.get(pb.name, (None, None))
                set_bone(pb, rots, loc)
                pb.keyframe_insert("rotation_quaternion", frame=f)
                pb.keyframe_insert("location", frame=f)
        for fc in act.fcurves:
            for kp in fc.keyframe_points:
                kp.interpolation = "BEZIER"
                kp.handle_left_type = "AUTO_CLAMPED"
                kp.handle_right_type = "AUTO_CLAMPED"
        made.append((name, count, act))
    sc.frame_set(1)
    return made


def push_to_nla(arm, made):
    """每个 action 推到独立 NLA 轨道（Blender 3.5 的 glTF 导出按 NLA 出动画）。"""
    ad = arm.animation_data
    for track in list(ad.nla_tracks):
        ad.nla_tracks.remove(track)
    for name, count, act in made:
        track = ad.nla_tracks.new()
        track.name = name
        strip = track.strips.new(name, 1, act)
        strip.name = name
        strip.frame_end = count
        track.mute = False
    ad.action = None


def _solo_action(arm, act):
    arm.animation_data.action = act
    for track in arm.animation_data.nla_tracks:
        track.mute = True


def sample_action(arm, act, count):
    sc = bpy.context.scene
    _solo_action(arm, act)
    nb = len(arm.pose.bones)
    loc = np.zeros((count, nb, 3), dtype=np.float64)
    rot = np.zeros((count, nb, 4), dtype=np.float64)
    scl = np.ones((count, nb, 3), dtype=np.float64)
    rot[..., 0] = 1.0
    for i in range(count):
        sc.frame_set(i + 1)
        bpy.context.view_layer.update()
        for bi, pb in enumerate(arm.pose.bones):
            l, r, s = pb.matrix_basis.decompose()
            loc[i, bi] = (l.x, l.y, l.z)
            rot[i, bi] = (r.w, r.x, r.y, r.z)
            scl[i, bi] = (s.x, s.y, s.z)
    return loc, rot, scl


# --------------------------------------------------------------------------- #
# 烘焙运行时资产（npz + json）
# --------------------------------------------------------------------------- #


def bake_runtime(arm, made):
    bones = list(arm.data.bones)
    bone_index = {b.name: i for i, b in enumerate(bones)}
    parents = np.array([bone_index[b.parent.name] if b.parent else -1
                        for b in bones], dtype=np.int16)
    mw = np.array(arm.matrix_world, dtype=np.float64)
    rest = np.stack([mw @ np.array(b.matrix_local, dtype=np.float64)
                     for b in bones])
    arrays = {"parents": parents, "rest": rest.astype(np.float32)}

    loop_map = {c[0]: c[3] for c in CLIPS}
    desc_map = {c[0]: c[4] for c in CLIPS}
    locs, rots, scls, clip_meta = [], [], [], []
    start = 0
    for name, count, act in made:
        l, r, s = sample_action(arm, act, count)
        locs.append(l)
        rots.append(r)
        scls.append(s)
        clip_meta.append({"name": name, "start": start, "count": count,
                          "loop": bool(loop_map[name]), "desc": desc_map[name]})
        start += count
    arrays["basis_loc"] = np.concatenate(locs).astype(np.float32)
    arrays["basis_rot"] = np.concatenate(rots).astype(np.float32)
    arrays["basis_scl"] = np.concatenate(scls).astype(np.float32)

    parts_meta, all_pos = [], []
    for idx, obj in enumerate(sorted([o for o in bpy.data.objects
                                      if o.type == "MESH"], key=lambda o: o.name)):
        me = obj.data
        me.calc_loop_triangles()
        omw = np.array(obj.matrix_world, dtype=np.float64)
        co = np.array([list(v.co) for v in me.vertices], dtype=np.float64)
        pos = np.concatenate([co, np.ones((len(co), 1))], axis=1) @ omw.T
        nrm3 = np.array([list(v.normal) for v in me.vertices], dtype=np.float64)
        nrm3 = nrm3 @ omw[:3, :3].T
        ln = np.linalg.norm(nrm3, axis=1, keepdims=True)
        nrm3 = nrm3 / np.where(ln < 1e-9, 1.0, ln)
        nrm = np.concatenate([nrm3, np.zeros((len(co), 1))], axis=1)
        tri = np.array([list(t.vertices) for t in me.loop_triangles],
                       dtype=np.int32).reshape(-1)

        groups = {g.index: g.name for g in obj.vertex_groups}
        joints = np.zeros((len(me.vertices), 4), dtype=np.uint8)
        weights = np.zeros((len(me.vertices), 4), dtype=np.float64)
        used = set()
        for vi, v in enumerate(me.vertices):
            pairs = [(bone_index[groups[g.group]], g.weight) for g in v.groups
                     if groups.get(g.group) in bone_index and g.weight > 0]
            pairs.sort(key=lambda t: -t[1])
            pairs = pairs[:4]
            total = sum(w for _, w in pairs) or 1.0
            for si, (bi, w) in enumerate(pairs):
                joints[vi, si] = bi
                weights[vi, si] = w / total
                used.add(bi)
        key = "p%d" % idx
        arrays[key + "_pos"] = pos.astype(np.float32)
        arrays[key + "_nrm"] = nrm.astype(np.float32)
        arrays[key + "_idx"] = tri.astype(np.int32)
        arrays[key + "_joints"] = joints
        arrays[key + "_weights"] = weights.astype(np.float32)
        mat = me.materials[0] if me.materials else None
        color = list(mat.diffuse_color)[:3] if mat else [0.8, 0.8, 0.8]
        emissive = False
        if mat is not None and mat.use_nodes:
            node = mat.node_tree.nodes.get("Principled BSDF")
            if node is not None:
                try:
                    emissive = float(node.inputs["Emission Strength"]
                                     .default_value) > 0.01
                except Exception:
                    emissive = False
        parts_meta.append({
            "key": key, "name": obj.name,
            "material": mat.name if mat else "",
            "color": [round(float(c), 5) for c in color],
            "emissive": bool(emissive),
            "verts": int(len(me.vertices)),
            "tris": int(len(me.loop_triangles)),
            "bound_to": sorted(bones[i].name for i in used),
        })
        all_pos.append(pos[:, :3])

    pts = np.concatenate(all_pos)
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    meta = {
        "source": "kitty_work.blend",
        "fps": FPS,
        "clips": clip_meta,
        "bones": [b.name for b in bones],
        "parts": parts_meta,
        "bounds": {"min": [round(float(v), 4) for v in lo],
                   "max": [round(float(v), 4) for v in hi]},
        "total_verts": int(sum(p["verts"] for p in parts_meta)),
        "total_tris": int(sum(p["tris"] for p in parts_meta)),
    }
    os.makedirs(os.path.dirname(os.path.abspath(RUNTIME_PREFIX)), exist_ok=True)
    np.savez_compressed(RUNTIME_PREFIX + ".npz", **arrays)
    with open(RUNTIME_PREFIX + ".json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return meta


# --------------------------------------------------------------------------- #
# 灯光 / 相机 / 校对渲染
# --------------------------------------------------------------------------- #


def add_light_rig():
    def add_light(name, loc, energy, size, color, look_at=(0, -0.2, 0.6)):
        data = bpy.data.lights.new(name, "AREA")
        data.energy = energy
        data.size = size
        data.color = hex_rgba(color)[:3]
        obj = bpy.data.objects.new(name, data)
        bpy.context.collection.objects.link(obj)
        obj.location = Vector(loc)
        obj.rotation_euler = (Vector(look_at) - obj.location).to_track_quat(
            "-Z", "Y").to_euler()
        return obj

    add_light("Key", (1.5, -1.7, 2.2), 340.0, 1.6, "#FFF4E2")
    add_light("Fill", (-1.9, -1.1, 1.2), 120.0, 2.0, "#CFE0FF")
    add_light("Rim", (-0.7, 1.9, 1.8), 210.0, 1.2, "#9FC4FF")


def add_check_camera(loc=(1.30, -1.95, 1.02), target=(0.0, -0.24, 0.62),
                     lens=46.0):
    sc = bpy.context.scene
    cam_data = bpy.data.cameras.new("CheckCam")
    cam = bpy.data.objects.new("CheckCam", cam_data)
    bpy.context.collection.objects.link(cam)
    cam.location = Vector(loc)
    cam.rotation_euler = (Vector(target) - cam.location).to_track_quat(
        "-Z", "Y").to_euler()
    cam_data.lens = lens
    sc.camera = cam
    return cam


def render_checks(made):
    sc = bpy.context.scene
    os.makedirs(CHECK_DIR, exist_ok=True)
    sc.render.engine = "BLENDER_WORKBENCH"
    sc.display.shading.light = "STUDIO"
    sc.display.shading.color_type = "MATERIAL"
    sc.display.shading.show_cavity = True
    sc.display.render_aa = "8"
    sc.render.resolution_x = 560
    sc.render.resolution_y = 560
    sc.render.film_transparent = False
    if sc.world and sc.world.use_nodes:
        sc.world.node_tree.nodes["Background"].inputs[0].default_value = (
            0.055, 0.055, 0.065, 1)
    picks = {"typing": 3, "idle": 20, "wave": 22, "excited": 6,
             "sleep": 48, "stretch": 26}
    acts = {n: a for n, _c, a in made}
    arm_obj = bpy.data.objects["KittyWorkRig"]
    for name, frame in picks.items():
        _solo_action(arm_obj, acts[name])
        sc.frame_set(frame)
        bpy.context.view_layer.update()
        sc.render.filepath = os.path.join(CHECK_DIR,
                                         "pose_%s_f%d.png" % (name, frame))
        bpy.ops.render.render(write_still=True)
        print("CHECK", name, frame)


def render_hero(made):
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.samples = 48
    sc.cycles.use_denoising = True
    sc.cycles.device = "CPU"
    sc.render.resolution_x = 860
    sc.render.resolution_y = 860
    sc.render.film_transparent = False
    if sc.world and sc.world.use_nodes:
        sc.world.node_tree.nodes["Background"].inputs[0].default_value = (
            0.032, 0.035, 0.043, 1)
        sc.world.node_tree.nodes["Background"].inputs[1].default_value = 0.7
    bpy.ops.mesh.primitive_plane_add(size=9.0, location=(0, 0, -0.003))
    floor = bpy.context.active_object
    floor.name = "Floor"
    floor.data.materials.append(make_material("studio_floor", "#2A313C", 0.5))
    acts = {n: a for n, _c, a in made}
    _solo_action(bpy.data.objects["KittyWorkRig"], acts["typing"])
    sc.frame_set(4)
    bpy.context.view_layer.update()
    sc.render.filepath = HERO_PNG
    bpy.ops.render.render(write_still=True)
    print("HERO", HERO_PNG)
    # 顺手出一张正面特写（检查脸/眼镜/胡须细节）
    cam = sc.camera
    old_loc = cam.location.copy()
    old_rot = cam.rotation_euler.copy()
    cam.location = Vector((0.42, -1.28, 1.14))
    cam.rotation_euler = (Vector((0.0, -0.22, 1.10)) - cam.location).to_track_quat(
        "-Z", "Y").to_euler()
    cam.data.lens = 62.0
    sc.render.resolution_x = 720
    sc.render.resolution_y = 720
    sc.cycles.samples = 40
    sc.render.filepath = os.path.join(HERE, "hero_face.png")
    bpy.ops.render.render(write_still=True)
    print("HERO_FACE", sc.render.filepath)
    cam.location = old_loc
    cam.rotation_euler = old_rot


def main():
    parts = build_scene()
    arm = build_armature()
    bind_parts(parts, arm)
    made = bake_actions(arm)
    bpy.context.scene.frame_start = 1
    bpy.context.scene.frame_end = max(c for _n, c, _a in made)
    add_light_rig()
    add_check_camera()
    render_checks(made)
    meta = bake_runtime(arm, made)
    push_to_nla(arm, made)
    bpy.ops.wm.save_as_mainfile(filepath=OUT_BLEND)
    try:
        bpy.ops.export_scene.gltf(filepath=OUT_GLB, export_format="GLB",
                                  export_apply=True, export_animations=True,
                                  export_nla_strips=True, export_yup=True,
                                  export_frame_range=False)
        print("GLB_OK", os.path.getsize(OUT_GLB))
    except Exception as exc:
        print("GLB_EXPORT_FAILED", exc)
    render_hero(made)
    print("KITTY_BUILD_OK")
    print(json.dumps({k: meta[k] for k in ("total_verts", "total_tris",
                                           "bounds", "fps")}, ensure_ascii=False))
    print("clips:", [(c["name"], c["count"], c["loop"]) for c in meta["clips"]])
    print("parts:", len(meta["parts"]))
    print("assets:", RUNTIME_PREFIX + ".npz")


if __name__ == "__main__":
    main()
