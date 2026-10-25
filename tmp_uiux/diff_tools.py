# -*- coding: utf-8 -*-
import difflib
a = open(r"C:\Users\zhuzhu\.zhuzhu_Copilot\source_overlay\zhuzhu_Copilot\core\agent_tools.py", encoding="utf-8").read().splitlines()
b = open(r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\src\zhuzhu_Copilot\core\agent_tools.py", encoding="utf-8").read().splitlines()
d = list(difflib.unified_diff(b, a, lineterm="", n=1))
for line in d[:120]:
    print(line)
print("...total diff lines:", len(d))
