# -*- coding: utf-8 -*-
"""端到端：execute_tool("tts_speak") 流式播放（真实 API + pygame dummy）"""
import sys, os, py_compile
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')

for f in (r'C:\Users\zhuzhu\Desktop\my first android app\src\zhuzhu_Copilot\core\agent_tts.py',
          r'C:\Users\zhuzhu\Desktop\my first android app\src\zhuzhu_Copilot\core\agent_tools.py',
          r'C:\Users\zhuzhu\Desktop\my first android app\src\zhuzhu_Copilot\ui\agent_panel.py'):
    try:
        py_compile.compile(f, doraise=True)
        sys.stdout.write('COMPILE_OK: %s\n' % os.path.basename(f))
    except Exception as e:
        sys.stdout.write('COMPILE_FAIL: %s -> %s\n' % (f, e))

sys.path.insert(0, r'C:\Users\zhuzhu\Desktop\my first android app\src')
from zhuzhu_Copilot.core import agent_tools

out = agent_tools.execute_tool("tts_speak", {"text": "自动播放测试，流式合成语音。", "play": True})
sys.stdout.write('TTS_PLAY: %s\n' % out.get('text'))
out2 = agent_tools.execute_tool("tts_speak", {"text": "不播放测试。", "play": False})
sys.stdout.write('TTS_NO_PLAY: %s\n' % out2.get('text'))
# voice_id 留空（回退设置面板音色）也应工作
out3 = agent_tools.execute_tool("tts_speak", {"text": "默认音色测试。"})
sys.stdout.write('TTS_DEFAULT_VOICE: %s\n' % out3.get('text'))
sys.stdout.flush()
