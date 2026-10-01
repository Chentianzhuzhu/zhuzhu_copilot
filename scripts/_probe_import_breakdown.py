"""判定 agent_panel 的导入耗时究竟花在「编译」还是「模块体执行」。

做法（不改动产品代码）：
  A. 预导入全部依赖后，直接从 .pyc 解出 code object，只 exec，计时。
  B. 同进程内再走一次正常 import（从 sys.modules 删掉后），计时。
两者差即为 import 机制开销（找文件 / 校验 pyc / marshal）。
"""
import marshal
import os
import sys
import time

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
os.chdir(SRC)
sys.path.insert(0, SRC)

DEPS = """
from PyQt6.QtWidgets import QApplication
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QIcon, QFont
from PyQt6.QtSvg import QSvgRenderer
import zhuzhu_Copilot.core.agent_agents, zhuzhu_Copilot.core.agent_engine
import zhuzhu_Copilot.core.agent_llm, zhuzhu_Copilot.core.agent_skills
import zhuzhu_Copilot.core.agent_tools, zhuzhu_Copilot.core.agent_workflow
import zhuzhu_Copilot.core.agent_plugins, zhuzhu_Copilot.core.agent_subagent
import zhuzhu_Copilot.core.agent_ui_ux, zhuzhu_Copilot.core.agent_tts
import zhuzhu_Copilot.core.agent_screen, zhuzhu_Copilot.core.agent_sandbox
import zhuzhu_Copilot.core.agent_panels, zhuzhu_Copilot.core.app_wallpaper
import zhuzhu_Copilot.core.agent_mcp
import zhuzhu_Copilot.ui.agent_chat_bubbles, zhuzhu_Copilot.ui.tool_icons
import zhuzhu_Copilot.ui.widgets, zhuzhu_Copilot.ui.tokens
"""

t = time.perf_counter()
exec(DEPS)
print(f"A. 依赖预导入            {(time.perf_counter()-t)*1000:8.1f} ms")

pyc = os.path.join("zhuzhu_Copilot", "ui", "__pycache__",
                   "agent_panel.cpython-313.pyc")
data = open(pyc, "rb").read()

t = time.perf_counter()
code = marshal.loads(data[16:])
print(f"B. marshal.loads(pyc)    {(time.perf_counter()-t)*1000:8.1f} ms  "
      f"(pyc {len(data)/1024:.0f} KiB)")

import types
m = types.ModuleType("zhuzhu_Copilot.ui.agent_panel")
m.__file__ = "zhuzhu_Copilot/ui/agent_panel.py"
sys.modules["zhuzhu_Copilot.ui.agent_panel"] = m
t = time.perf_counter()
exec(code, m.__dict__)
print(f"C. exec 模块体           {(time.perf_counter()-t)*1000:8.1f} ms")

del sys.modules["zhuzhu_Copilot.ui.agent_panel"]
t = time.perf_counter()
import zhuzhu_Copilot.ui.agent_panel  # noqa: E402
print(f"D. 正常 import(命中pyc)  {(time.perf_counter()-t)*1000:8.1f} ms")
