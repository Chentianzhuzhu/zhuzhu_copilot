"""量化校验：爪子与键盘的真实接触关系 + 正交前视/侧视图（不靠肉眼猜）。

在 typing 动作下逐帧求「爪部件最低点」与「键帽最高点」的世界坐标，
输出 z 向间隙与 y/x 覆盖范围，并渲染正前/正侧正交图供比对。
"""
import os

import bpy
import numpy as np
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
CHECK = os.path.join(HERE, "checks")
os.makedirs(CHECK, exist_ok=True)

bpy.ops.wm.open_mainfile(filepath=os.path.join(HERE, "kitty_work.blend"))
arm = bpy.data.objects["KittyWorkRig"]
sc = bpy.context.scene
dg = bpy.context.evaluated_depsgraph_get()

PAW_L = "kitty_paw_l"
PAW_R = "kitty_paw_r"
KEYS = "laptop_keys"
DECK = "laptop_deck"


def world_bounds(obj_name):
    """求对象当前帧的世界空间包围盒（含骨骼变形）。"""
    ob = bpy.data.objects[obj_name]
    ev = ob.evaluated_get(dg)
    me = ev.to_mesh()
    mw = ev.matrix_world
    pts = np.array([list(mw @ v.co) for v in me.vertices], dtype=np.float64)
    ev.to_mesh_clear()
    return pts.min(axis=0), pts.max(axis=0)


def report(action_name, frames):
    arm.animation_data.action = bpy.data.actions[action_name]
    for tr in arm.animation_data.nla_tracks:
        tr.mute = True
    print(f"\n=== {action_name} ===")
    kl, kh = world_bounds(KEYS)
    dl, dh = world_bounds(DECK)
    print("键盘键帽  x[%.3f, %.3f]  y[%.3f, %.3f]  z顶=%.4f"
          % (kl[0], kh[0], kl[1], kh[1], kh[2]))
    print("机身台面  z顶=%.4f" % dh[2])
    for f in frames:
        sc.frame_set(f)
        bpy.context.view_layer.update()
        dg2 = bpy.context.evaluated_depsgraph_get()
        for name in (PAW_L, PAW_R):
            ob = bpy.data.objects[name]
            ev = ob.evaluated_get(dg2)
            me = ev.to_mesh()
            mw = ev.matrix_world
            pts = np.array([list(mw @ v.co) for v in me.vertices])
            ev.to_mesh_clear()
            lo, hi = pts.min(axis=0), pts.max(axis=0)
            gap = lo[2] - kh[2]
            cover_y = (lo[1] <= kh[1] + 1e-6) and (hi[1] >= kl[1] - 1e-6)
            cover_x = (lo[0] <= kh[0]) and (hi[0] >= kl[0])
            print("  f%-3d %-12s 底面z=%.4f 间隙=%+.4f  %s  y[%.3f,%.3f] x[%.3f,%.3f]"
                  % (f, name, lo[2], gap,
                     "贴键" if abs(gap) < 0.02 else ("悬空" if gap > 0 else "穿模"),
                     lo[1], hi[1], lo[0], hi[0]))


def ortho_render(name, loc, rot, target, scale, frame):
    cam_data = bpy.data.cameras.new("OrthoCam_" + name)
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = scale
    cam = bpy.data.objects.new("OrthoCam_" + name, cam_data)
    bpy.context.collection.objects.link(cam)
    cam.location = Vector(loc)
    d = Vector(target) - cam.location
    cam.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    sc.camera = cam
    sc.render.engine = "BLENDER_WORKBENCH"
    sc.display.shading.light = "STUDIO"
    sc.display.shading.color_type = "MATERIAL"
    sc.display.shading.show_cavity = True
    sc.display.render_aa = "8"
    sc.render.resolution_x = 620
    sc.render.resolution_y = 620
    sc.frame_set(frame)
    bpy.context.view_layer.update()
    sc.render.filepath = os.path.join(CHECK, "ortho_%s_f%d.png" % (name, frame))
    bpy.ops.render.render(write_still=True)
    print("ORTHO", name, frame)


report("Kitty_typing", [1, 8, 16])

arm.animation_data.action = bpy.data.actions["Kitty_typing"]
for tr in arm.animation_data.nla_tracks:
    tr.mute = True
sc.frame_set(1)
bpy.context.view_layer.update()

ortho_render("front", (0.0, -3.0, 0.70), (1.5708, 0, 0), (0.0, 0.0, 0.70), 1.75, 1)
ortho_render("side", (3.0, -0.20, 0.70), (1.5708, 0, 1.5708), (0.0, -0.20, 0.70), 1.75, 1)
print("MEASURE_DONE")
