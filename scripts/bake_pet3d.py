"""把 FBX 桌宠烘焙成 Qt 3D 能直接用的运行时资产（开发期工具，需要 Blender）。

用法（Blender 3.5，无界面）：
    blender --background --factory-startup --python scripts/bake_pet3d.py -- \
        "C:/path/kitty_pet.fbx" assets/pet3d/kitty

产出（两个文件，随 assets 一起打包）：
    <out>.npz   网格（绑定姿态的位置/法线/索引/骨骼权重）+ 骨骼（父级/静止矩阵/每帧姿态）
    <out>.json  元信息（部件名、材质色、骨骼名、帧率、帧数、包围盒）

为什么不在运行时直接读 FBX：Qt 3D 没有 FBX 解析器（Qt6 已移除 assimp 场景插件），
FBX 也不是能给引擎直接吃的格式。标准做法就是**离线转成引擎资产**：这里把骨骼动画
烘成「每帧骨骼局部姿态」，运行时用 numpy 做线性混合蒙皮（LBS），Qt 3D 只负责画三角形。
这样既保住了原来的骨骼动画与蒙皮，又能在每帧之上叠加「跟随鼠标」的附加旋转。

约定：
- 顶点、法线、骨骼矩阵**全部在 Blender 世界空间（Z 轴朝上）**烘焙；渲染时用一个
  根变换绕 X 轴 -90° 转成 Qt 3D 的 Y 轴朝上，避免逐项换算出错。
- 每个网格部件单独烘焙（材质色不同），无骨骼权重的刚性小件（眼睛/项圈/鼻子）
  按「离质心最近的骨头」绑到单一骨骼上，跟着头/身体动。
"""
import json
import os
import sys

import bpy
import numpy as np
from mathutils import Vector

#: 不导出的对象：地面/相机/灯光（桌宠只要角色本体）
SKIP_NAMES = {"Floor", "Camera", "Key", "Fill", "Rim"}
#: 每个顶点最多保留的骨骼影响数（Qt 侧 LBS 固定 4 路）
MAX_INFLUENCES = 4

#: 部件 → 骨骼 的显式绑定表。
#: 这份 FBX 的网格**没有蒙皮权重**（顶点组为 0，Armature 修改器实际不生效），
#: 也就是原文件里骨骼在动、模型却纹丝不动。桌宠要动起来就必须自己绑：
#: 每个部件都是刚性块，直接按语义绑到对应骨头即可（头/尾/耳朵/四肢各归各位）。
PART_BONE = {
    "kitty_body": "Spine", "kitty_belly": "Spine", "kitty_collar": "Spine",
    "kitty_head": "Head", "kitty_muzzle": "Head", "kitty_nose": "Head",
    "kitty_eye_l": "Head", "kitty_eye_r": "Head",
    "kitty_glint_l": "Head", "kitty_glint_r": "Head",
    "kitty_ear_l": "L_Ear", "kitty_ear_r": "R_Ear",
    "kitty_ear_in_l": "L_Ear", "kitty_ear_in_r": "R_Ear",
    "kitty_arm_l": "L_Foreleg", "kitty_arm_r": "R_Foreleg",
    "kitty_foot_l": "L_Hindleg", "kitty_foot_r": "R_Hindleg",
    "kitty_tail_1": "Tail_1", "kitty_tail_2": "Tail_2", "kitty_tail_3": "Tail_3",
}


def _argv():
    tail = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if len(tail) < 2:
        raise SystemExit("用法: -- <fbx> <out_prefix>")
    return tail[0], tail[1]


def _dump(obj) -> np.ndarray:
    """mathutils 矩阵/向量 → numpy。"""
    return np.array(obj, dtype=np.float64)


def _nearest_bone(arm, point_world) -> int:
    """刚性小件用它：找离质心最近的骨头（按骨头头部到质心的距离）。"""
    best, best_d = 0, 1e18
    mw = arm.matrix_world
    for i, b in enumerate(arm.data.bones):
        head = mw @ b.head_local
        mid = mw @ ((b.head_local + b.tail_local) * 0.5)
        d = min((Vector(point_world) - head).length, (Vector(point_world) - mid).length)
        if d < best_d:
            best, best_d = i, d
    return best


def main():
    fbx_path, out_prefix = _argv()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=fbx_path)
    sc = bpy.context.scene
    fps = int(sc.render.fps or 24)

    arm = next((o for o in bpy.data.objects if o.type == "ARMATURE"), None)
    if arm is None:
        raise SystemExit("FBX 里没有骨架，无法烘焙骨骼动画")
    bones = list(arm.data.bones)
    bone_index = {b.name: i for i, b in enumerate(bones)}
    nb = len(bones)

    # ---- 骨骼：父级 + 静止矩阵（armature 空间 → 世界空间）----
    parents = np.array([bone_index.get(b.parent.name, -1) if b.parent else -1
                        for b in bones], dtype=np.int16)
    mw = _dump(arm.matrix_world)
    rest = np.stack([mw @ _dump(b.matrix_local) for b in bones]).astype(np.float64)

    # ---- 动画：取骨架上的 action，逐帧记录 matrix_basis（局部姿态）----
    act_bone = None
    ad = getattr(arm, "animation_data", None)
    if ad is not None and ad.action is not None:
        act_bone = ad.action.name
    if act_bone is None:
        raise SystemExit("骨架没有绑定 action，没有内置动画可烘焙")
    action = bpy.data.actions[act_bone]
    f0, f1 = int(round(action.frame_range[0])), int(round(action.frame_range[1]))
    frames = list(range(f0, f1 + 1))

    nbasis_loc = np.zeros((len(frames), nb, 3), dtype=np.float64)
    nbasis_rot = np.zeros((len(frames), nb, 4), dtype=np.float64)   # 四元数 wxyz
    nbasis_scl = np.ones((len(frames), nb, 3), dtype=np.float64)
    nbasis_rot[..., 0] = 1.0
    for fi, frame in enumerate(frames):
        sc.frame_set(frame)
        for bi, pb in enumerate(arm.pose.bones):
            basis = pb.matrix_basis
            loc, rot, scl = basis.decompose()
            nbasis_loc[fi, bi] = (loc.x, loc.y, loc.z)
            nbasis_rot[fi, bi] = (rot.w, rot.x, rot.y, rot.z)
            nbasis_scl[fi, bi] = (scl.x, scl.y, scl.z)
    sc.frame_set(f0)

    # ---- 网格部件 ----
    arrays, parts = {}, []
    for o in sorted([x for x in bpy.data.objects if x.type == "MESH"], key=lambda x: x.name):
        if o.name in SKIP_NAMES or o.hide_render:
            continue
        me = o.data
        me.calc_loop_triangles()
        omw = _dump(o.matrix_world)
        co = np.array([list(v.co) for v in me.vertices], dtype=np.float64)
        pos = np.concatenate([co, np.ones((len(co), 1))], axis=1) @ omw.T   # N×4
        nrm3 = np.array([list(v.normal) for v in me.vertices], dtype=np.float64)
        nrm3 = nrm3 @ omw[:3, :3].T
        ln = np.linalg.norm(nrm3, axis=1, keepdims=True)
        nrm3 = nrm3 / np.where(ln < 1e-9, 1.0, ln)
        nrm = np.concatenate([nrm3, np.zeros((len(co), 1))], axis=1)
        if len(me.loop_triangles) == 0:
            continue
        idx = np.array([list(t.vertices) for t in me.loop_triangles],
                       dtype=np.int32).reshape(-1)

        # 权重：有顶点组就取前 4 个，没有就绑到最近的骨头（刚性小件）
        joints = np.zeros((len(me.vertices), MAX_INFLUENCES), dtype=np.uint8)
        weights = np.zeros((len(me.vertices), MAX_INFLUENCES), dtype=np.float64)
        groups = {g.index: g.name for g in o.vertex_groups}
        has_weights = any(len(v.groups) for v in me.vertices)
        if has_weights:
            for vi, v in enumerate(me.vertices):
                pairs = [(bone_index[groups[g.group]], g.weight) for g in v.groups
                         if groups.get(g.group) in bone_index and g.weight > 0]
                pairs.sort(key=lambda t: -t[1])
                pairs = pairs[:MAX_INFLUENCES]
                total = sum(w for _, w in pairs) or 1.0
                for si, (bi, w) in enumerate(pairs):
                    joints[vi, si] = bi
                    weights[vi, si] = w / total
        else:
            # 没有蒙皮权重：按语义表绑到骨头，表里没有就退回「离质心最近的骨头」
            bone_name = PART_BONE.get(o.name, "")
            if bone_name in bone_index:
                bi = bone_index[bone_name]
            else:
                centroid = Vector(pos[:, :3].mean(axis=0))
                bi = _nearest_bone(arm, centroid)
            joints[:, 0] = bi
            weights[:, 0] = 1.0

        mat = me.materials[0] if me.materials else None
        color = list(mat.diffuse_color)[:3] if mat is not None else [0.8, 0.8, 0.8]
        name = f"p{len(parts)}"
        arrays[name + "_pos"] = pos.astype(np.float32)
        arrays[name + "_nrm"] = nrm.astype(np.float32)
        arrays[name + "_idx"] = idx
        arrays[name + "_joints"] = joints
        arrays[name + "_weights"] = weights.astype(np.float32)
        bound = sorted({bones[int(j)].name for j in joints[:, 0][weights[:, 0] > 0]})
        parts.append({"key": name, "name": o.name,
                      "material": mat.name if mat is not None else "",
                      "color": [round(float(c), 4) for c in color],
                      "verts": int(len(me.vertices)), "tris": int(len(me.loop_triangles)),
                      "skinned": bool(has_weights), "bound_to": bound})

    arrays["parents"] = parents
    arrays["rest"] = rest.astype(np.float32)
    arrays["basis_loc"] = nbasis_loc.astype(np.float32)
    arrays["basis_rot"] = nbasis_rot.astype(np.float32)
    arrays["basis_scl"] = nbasis_scl.astype(np.float32)

    all_pos = np.concatenate([arrays[p["key"] + "_pos"][:, :3] for p in parts])
    lo, hi = all_pos.min(axis=0), all_pos.max(axis=0)

    os.makedirs(os.path.dirname(os.path.abspath(out_prefix)) or ".", exist_ok=True)
    np.savez_compressed(out_prefix + ".npz", **arrays)
    meta = {
        "source": os.path.basename(fbx_path),
        "fps": fps,
        "frames": len(frames),
        "frame_start": f0,
        "frame_end": f1,
        "action": act_bone,
        "bones": [b.name for b in bones],
        "parts": parts,
        "bounds": {"min": [round(float(v), 4) for v in lo],
                   "max": [round(float(v), 4) for v in hi]},
        "total_verts": int(sum(p["verts"] for p in parts)),
        "total_tris": int(sum(p["tris"] for p in parts)),
    }
    with open(out_prefix + ".json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print("BAKE_OK")
    print(json.dumps({k: meta[k] for k in ("fps", "frames", "action",
                                           "total_verts", "total_tris", "bounds")},
                     ensure_ascii=False))
    print("parts:", [(p["name"], p["verts"], p["bound_to"]) for p in parts])
    npz = os.path.getsize(out_prefix + ".npz")
    print(f"assets: {out_prefix}.npz {npz // 1024} KB, {out_prefix}.json "
          f"{os.path.getsize(out_prefix + '.json') // 1024} KB")


if __name__ == "__main__":
    main()
