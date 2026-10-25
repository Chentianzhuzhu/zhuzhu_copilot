# -*- coding: utf-8 -*-
import sys, os
SRC = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\src"
sys.path.insert(0, SRC)
from PyQt6.QtWidgets import QApplication
from PIL import Image
import zhuzhu_Copilot.ui.agent_panel as ap

OUT = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\tmp_uiux"
app = QApplication(sys.argv)
for name, svg in (("undo", ap._UNDO_SVG), ("redo", ap._REDO_SVG)):
    pm = ap._svg_pixmap(svg, 48, "#F3F5F9")
    pm.save(os.path.join(OUT, f"icon_{name}.png"))
    im = Image.open(os.path.join(OUT, f"icon_{name}.png")).convert("RGBA")
    bg = Image.new("RGBA", im.size, (24, 27, 33, 255))
    bg.alpha_composite(im)
    bg.resize((192, 192), Image.NEAREST).save(os.path.join(OUT, f"icon_{name}_big.png"))
print("done")
app.quit()
