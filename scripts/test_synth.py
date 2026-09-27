# -*- coding: utf-8 -*-
"""端到端验证：synthesize 不传 voice_id -> 回退设置面板音色 -> 真实 API 合成"""
import sys, os
sys.path.insert(0, r'C:\Users\zhuzhu\Desktop\my first android app\src')
from winapp_migrator.core import agent_tts

try:
    out = agent_tts.synthesize("语音合成测试", "")
    sys.stdout.write('SYNTH_OK: %s\n' % out)
    if os.path.exists(out):
        sys.stdout.write('FILE_SIZE: %d\n' % os.path.getsize(out))
except Exception as e:
    sys.stdout.write('SYNTH_FAIL: %s\n' % e)
sys.stdout.flush()
