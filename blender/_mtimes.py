import os
import time

files = [
    r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\blender\build_kitty_work.py",
    r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\blender\kitty_work.glb",
    r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\blender\kitty_work.blend",
    r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\blender\hero.png",
    r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\blender\hero_face.png",
    r"C:\Users\zhuzhu\.zhuzhu_Copilot\copilot_plugins\kitty-work-pet\assets\kitty_work.npz",
    r"C:\Users\zhuzhu\.zhuzhu_Copilot\copilot_plugins\kitty-work-pet\assets\kitty_work.json",
]
for p in files:
    if os.path.isfile(p):
        st = os.stat(p)
        print("%-22s %10d  %s" % (os.path.basename(p), st.st_size,
                                  time.strftime("%H:%M:%S", time.localtime(st.st_mtime))))
    else:
        print("%-22s MISSING" % os.path.basename(p))
