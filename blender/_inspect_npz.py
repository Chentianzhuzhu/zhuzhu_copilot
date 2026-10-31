import json
import numpy as np

d = np.load(r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\assets\pet3d\kitty.npz")
m = json.load(open(r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\assets\pet3d\kitty.json", encoding="utf-8"))
want = {"kitty_nose", "kitty_muzzle", "kitty_tail_1", "kitty_ear_l", "kitty_foot_l",
        "kitty_head", "kitty_eye_l", "kitty_body"}
for p in m["parts"]:
    if p["name"] in want:
        c = d[p["key"] + "_pos"][:, :3].mean(axis=0)
        print(p["name"], np.round(c, 3))
print("bones:", m["bones"])
print("rest root:", np.round(d["rest"][0], 3))
