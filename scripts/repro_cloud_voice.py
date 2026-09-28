# -*- coding: utf-8 -*-
"""复现：list_voices 结构 + tts_speak 用云端音色 ID / 空 voice_id 的行为"""
import sys, os, json
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')
sys.path.insert(0, r'C:\Users\zhuzhu\Desktop\my first android app\src')
from zhuzhu_Copilot.core import agent_tts, agent_tools

# 1) list_voices 返回结构
sys.stdout.write('===== list_voices =====\n')
try:
    vs = agent_tts.list_voices()
    sys.stdout.write('count=%d\n' % len(vs))
    for v in vs[:5]:
        sys.stdout.write('  keys=%s\n' % list(v.keys()))
        sys.stdout.write('  %r\n' % v)
except Exception as e:
    sys.stdout.write('list_voices ERR: %s\n' % e)

# 2) tts_speak 传云端第一个 voice_id
vid = None
try:
    vs = agent_tts.list_voices()
    if vs:
        vid = str(vs[0].get('voice') or vs[0].get('voice_id') or '').strip()
except Exception:
    pass
sys.stdout.write('\n===== tts_speak(cloud voice_id=%r) =====\n' % vid)
if vid:
    try:
        out = agent_tools.execute_tool('tts_speak', {'text': '云端音色调用测试。', 'voice_id': vid, 'play': False})
        sys.stdout.write('OK: %s\n' % out.get('text'))
    except Exception as e:
        sys.stdout.write('ERR: %s\n' % e)

# 3) 模拟"删除本地音色"（voice_id 空）当前行为
sys.stdout.write('\n===== tts_speak(空 voice_id，模拟删除本地音色) =====\n')
try:
    out = agent_tools.execute_tool('tts_speak', {'text': '无本地音色测试。', 'play': False})
    sys.stdout.write('OK: %s\n' % out.get('text'))
except Exception as e:
    sys.stdout.write('ERR: %s\n' % e)
sys.stdout.flush()
