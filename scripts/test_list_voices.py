# -*- coding: utf-8 -*-
"""临时验证：DashScope customization 是否支持 action=list（真实 API）"""
import sys, json
sys.path.insert(0, r'C:\Users\zhuzhu\Desktop\my first android app\src')
from zhuzhu_Copilot.core import agent_tts

payload = {
    "model": agent_tts.ENROLL_MODEL,
    "input": {"action": "list"},
}
try:
    d = agent_tts._request(agent_tts.CUSTOMIZATION_URL, payload, timeout=60)
    sys.stdout.write('LIST_OK: ' + json.dumps(d, ensure_ascii=False)[:1500] + '\n')
except Exception as e:
    sys.stdout.write('LIST_FAIL: %s\n' % e)
sys.stdout.flush()
