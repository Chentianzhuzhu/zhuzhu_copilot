"""校验烘焙资产与 GLB 的真实结构（不猜，直接解析字节）。

1. 插件的 kitty_work.json / .npz：clips 段数、骨骼数、部件数、帧数是否自洽；
2. blender/kitty_work.glb：GLB 容器头 + JSON chunk 里的 animations / meshes / nodes。
"""
import json
import os
import struct

NPZ = os.path.join(os.path.expanduser("~"), ".zhuzhu_Copilot", "copilot_plugins",
                   "kitty-work-pet", "assets", "kitty_work.npz")
META = NPZ[:-4] + ".json"
GLB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kitty_work.glb")

meta = json.load(open(META, encoding="utf-8"))
clips = meta["clips"]
total = sum(c["count"] for c in clips)
print("== 烘焙资产 ==")
print("fps:", meta["fps"], "| 骨骼:", len(meta["bones"]), "| 部件:", len(meta["parts"]))
print("顶点:", meta["total_verts"], "| 三角面:", meta["total_tris"])
print("clips:", [(c["name"], c["start"], c["count"], c["loop"]) for c in clips])
print("总帧数:", total)

import numpy as np
d = np.load(NPZ)
for key in ("parents", "rest", "basis_loc", "basis_rot", "basis_scl"):
    print(f"  {key}: {d[key].shape}")
ok = (d["basis_rot"].shape[0] == total == d["basis_loc"].shape[0] == d["basis_scl"].shape[0])
print("帧数自洽:", ok)
assert ok, "clips 帧数与 basis 数组不一致！"
for c in clips:
    if c["start"] + c["count"] > total:
        raise SystemExit(f"clip {c['name']} 越界")

print("\n== GLB ==")
raw = open(GLB, "rb").read()
magic, version, length = struct.unpack("<4sII", raw[:12])
print("magic:", magic, "version:", version, "length:", length,
      "| 实际文件大小:", len(raw), "| 一致:", length == len(raw))
chunk_len, chunk_type = struct.unpack("<II", raw[12:20])
gltf = json.loads(raw[20:20 + chunk_len].decode("utf-8"))
anims = [a.get("name") for a in gltf.get("animations", [])]
print("animations:", anims)
print("meshes:", len(gltf.get("meshes", [])), "| nodes:", len(gltf.get("nodes", [])),
      "| skins:", len(gltf.get("skins", [])))
skins = gltf.get("skins", [])
if skins:
    print("skin joints:", len(skins[0].get("joints", [])))
need = {"typing", "idle", "wave", "excited", "sleep", "stretch"}
got = set(a.split("|")[-1] for a in anims)
print("六段动画齐全:", need.issubset(got), "| 缺少:", sorted(need - got))
print("\nVALIDATE_OK")
