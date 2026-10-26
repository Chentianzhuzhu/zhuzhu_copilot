"""桌宠 3D 资产：骨骼姿态求解 + 蒙皮（numpy 向量化，Qt 3D 只管画三角形）。

资产由 ``scripts/bake_pet3d.py``（Blender）离线烘焙：
  <name>.npz  网格绑定姿态 / 骨骼层级与静止矩阵 / 每帧骨骼局部姿态
  <name>.json 元信息（部件、材质色、骨骼名、帧率、包围盒）

坐标系：全部按 Blender 世界空间（Z 轴朝上）烘焙；渲染侧用一个根变换绕 X 轴 -90°
转成 Qt 3D 的 Y 轴朝上，避免逐项换算出错。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


def _quat_to_mat(q: np.ndarray) -> np.ndarray:
    """(w,x,y,z) 四元数 → 3×3 旋转矩阵（批量：N×4 → N×3×3）。"""
    q = np.asarray(q, dtype=np.float64)
    q = q / np.maximum(np.linalg.norm(q, axis=-1, keepdims=True), 1e-9)
    w, x, y, z = q[..., 0], q[..., 1], q[..., 2], q[..., 3]
    out = np.empty(q.shape[:-1] + (3, 3), dtype=np.float64)
    out[..., 0, 0] = 1 - 2 * (y * y + z * z)
    out[..., 0, 1] = 2 * (x * y - z * w)
    out[..., 0, 2] = 2 * (x * z + y * w)
    out[..., 1, 0] = 2 * (x * y + z * w)
    out[..., 1, 1] = 1 - 2 * (x * x + z * z)
    out[..., 1, 2] = 2 * (y * z - x * w)
    out[..., 2, 0] = 2 * (x * z - y * w)
    out[..., 2, 1] = 2 * (y * z + x * w)
    out[..., 2, 2] = 1 - 2 * (x * x + y * y)
    return out


def _axis_angle(axis: str, deg: float) -> np.ndarray:
    """绕单轴旋转的 4×4 矩阵（附加姿态用：正负 30° 级别的小旋转）。"""
    rad = np.deg2rad(deg)
    c, s = np.cos(rad), np.sin(rad)
    m = np.eye(4, dtype=np.float64)
    if axis == "x":
        m[1, 1], m[1, 2], m[2, 1], m[2, 2] = c, -s, s, c
    elif axis == "y":
        m[0, 0], m[0, 2], m[2, 0], m[2, 2] = c, s, -s, c
    else:
        m[0, 0], m[0, 1], m[1, 0], m[1, 1] = c, -s, s, c
    return m


@dataclass
class PetPart:
    """一个网格部件：绑定姿态数据 + 材质色 + 绑定的骨骼。"""

    key: str
    name: str
    color: tuple
    indices: np.ndarray
    joints: np.ndarray
    weights: np.ndarray
    pos: np.ndarray
    nrm: np.ndarray
    bones: list = field(default_factory=list)

    @property
    def rigid(self) -> bool:
        """只受一根骨头影响（这份 FBX 全是刚性块）→ 整块矩阵变换，不做逐顶点混合。"""
        return bool(np.all(self.weights[:, 1:] == 0.0)) and bool(
            np.unique(self.joints[:, 0]).size == 1)


class PetRig:
    """骨骼动画 + 蒙皮。每帧只做矩阵乘与向量加，14k 顶点也在毫秒级。"""

    def __init__(self, asset_prefix: str | Path):
        prefix = Path(asset_prefix)
        meta = json.loads(prefix.with_suffix(".json").read_text(encoding="utf-8"))
        data = np.load(prefix.with_suffix(".npz"))
        self.meta = meta
        self.fps = float(meta.get("fps") or 24)
        self.frame_count = int(meta.get("frames") or 1)
        self.bone_names = list(meta.get("bones") or [])
        self.bone_index = {n: i for i, n in enumerate(self.bone_names)}

        self.parents = data["parents"].astype(np.int32)
        self.rest = data["rest"].astype(np.float64)
        self.basis_loc = data["basis_loc"].astype(np.float64)
        self.basis_rot = data["basis_rot"].astype(np.float64)
        self.basis_scl = data["basis_scl"].astype(np.float64)
        self.inv_rest = np.linalg.inv(self.rest)

        # 骨骼局部静止变换：inv(rest_parent) @ rest_i，父级为 -1 时就是 rest_i 本身
        self.local_rest = np.empty_like(self.rest)
        for i in range(len(self.rest)):
            p = int(self.parents[i])
            self.local_rest[i] = self.rest[i] if p < 0 else self.inv_rest[p] @ self.rest[i]

        self.parts: list[PetPart] = []
        for p in meta.get("parts") or []:
            k = p["key"]
            self.parts.append(PetPart(
                key=k, name=p["name"], color=tuple(p.get("color") or (0.8, 0.8, 0.8)),
                indices=data[k + "_idx"].astype(np.int64),
                joints=data[k + "_joints"].astype(np.int64),
                weights=data[k + "_weights"].astype(np.float64),
                pos=data[k + "_pos"].astype(np.float64),
                nrm=data[k + "_nrm"].astype(np.float64),
                bones=list(p.get("bound_to") or []),
            ))
        b = meta.get("bounds") or {}
        self.bounds_min = np.array(b.get("min") or (-1, -1, 0), dtype=np.float64)
        self.bounds_max = np.array(b.get("max") or (1, 1, 2), dtype=np.float64)

    # ---------- 姿态 ----------
    def pose(self, t: float, extra_rot: dict | None = None,
             extra_shift: dict | None = None) -> np.ndarray:
        """返回每根骨骼的蒙皮矩阵（B×4×4 = pose @ inv(rest)）。

        t：动画时间（秒，自动回环）。
        extra_rot：{骨骼名: 4×4 附加旋转}——绕骨头自身轴，适合扭头。
        extra_shift：{骨骼名: 世界空间 (dx,dy,dz)}——直接平移该骨骼（连带其部件）。

        为什么需要 shift：这份 FBX 的每个部件都建在**自己骨头的原点上**，绕骨头
        旋转等于原地打转（实测 25° 只让质心偏 0.05，肉眼几乎看不出）。想让四肢、
        尾巴真的"甩起来"，平移比旋转有效得多。世界空间平移与骨头朝向无关，
        方向可预期，不必猜每根骨头的局部轴。
        """
        n = self.frame_count
        f = (float(t) * self.fps) % n
        i0 = int(np.floor(f)) % n
        i1 = (i0 + 1) % n
        a = f - np.floor(f)

        # 四元数按权重做 nlerp（相邻帧夹角很小，视觉上与 slerp 无差）
        q0, q1 = self.basis_rot[i0], self.basis_rot[i1]
        q = q0 * (1.0 - a) + q1 * a
        q = q / np.maximum(np.linalg.norm(q, axis=1, keepdims=True), 1e-9)
        loc = self.basis_loc[i0] * (1.0 - a) + self.basis_loc[i1] * a
        scl = self.basis_scl[i0] * (1.0 - a) + self.basis_scl[i1] * a
        rot = _quat_to_mat(q)

        nb = len(self.rest)
        basis = np.tile(np.eye(4, dtype=np.float64), (nb, 1, 1))
        basis[:, :3, :3] = rot * scl[:, None, :]
        basis[:, :3, 3] = loc
        if extra_rot:
            for name, m in extra_rot.items():
                bi = self.bone_index.get(name)
                if bi is not None:
                    basis[bi] = basis[bi] @ m

        # 逐骨骼求世界姿态：pose_i = pose_parent @ local_rest_i @ basis_i
        world = np.empty((nb, 4, 4), dtype=np.float64)
        for i in range(nb):
            p = int(self.parents[i])
            base = self.local_rest[i] if p < 0 else world[p] @ self.local_rest[i]
            world[i] = base @ basis[i]
        if extra_shift:
            for name, d in extra_shift.items():
                bi = self.bone_index.get(name)
                if bi is not None and d:
                    world[bi] = world[bi].copy()
                    world[bi][:3, 3] += np.asarray(d, dtype=np.float64)
        return world @ self.inv_rest

    # ---------- 蒙皮 ----------
    def skin(self, part: PetPart, mats: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """返回该部件当前帧的世界空间顶点/法线（float32，直接喂 QBuffer）。"""
        if part.rigid:
            m = mats[int(part.joints[0, 0])]
            r = m[:3, :3]
            pos = part.pos[:, :3] @ r.T + m[:3, 3]
            nrm = part.nrm[:, :3] @ r.T
        else:
            pos = np.zeros((len(part.pos), 3), dtype=np.float64)
            nrm = np.zeros((len(part.pos), 3), dtype=np.float64)
            for k in range(part.weights.shape[1]):
                w = part.weights[:, k]
                if not np.any(w):
                    continue
                mm = mats[part.joints[:, k]]                     # N×4×4
                pos += w[:, None] * np.einsum("nij,nj->ni", mm[:, :3, :], part.pos)[:, :3]
                nrm += w[:, None] * np.einsum("nij,nj->ni", mm[:, :3, :], part.nrm)[:, :3]
        n = np.linalg.norm(nrm, axis=1, keepdims=True)
        nrm = nrm / np.where(n < 1e-9, 1.0, n)
        return (np.ascontiguousarray(pos, dtype=np.float32),
                np.ascontiguousarray(nrm, dtype=np.float32))
