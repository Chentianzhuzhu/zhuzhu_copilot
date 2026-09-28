# -*- coding: utf-8 -*-
"""真实测试：DashScope multimodal-generation 是否支持 stream 流式返回音频分片"""
import sys, json, urllib.request, urllib.error, os
sys.path.insert(0, r'C:\Users\zhuzhu\Desktop\my first android app\src')
from zhuzhu_Copilot.core import agent_tts

key = agent_tts.load_api_key()
if not key:
    sys.stdout.write('NO_KEY\n'); sys.stdout.flush(); sys.exit()

url = agent_tts.TTS_URL
payload = {
    "model": agent_tts.DEFAULT_TARGET_MODEL,
    "input": {"text": "你好，这是一段流式语音合成测试。", "voice": agent_tts.load_config().get("voice_id", "")},
    "parameters": {"stream": True},
}
req = urllib.request.Request(
    url,
    data=json.dumps(payload).encode('utf-8'),
    headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
             "X-DashScope-SSE": "enable", "Accept": "text/event-stream"})
try:
    with urllib.request.urlopen(req, timeout=60) as resp:
        sys.stdout.write('STATUS: %s\n' % resp.status)
        sys.stdout.write('CONTENT-TYPE: %s\n' % resp.headers.get('Content-Type'))
        raw = resp.read(6000).decode('utf-8', 'ignore')
        sys.stdout.write('FIRST 6000 BYTES:\n%s\n' % raw[:6000])
except urllib.error.HTTPError as e:
    sys.stdout.write('HTTP %s: %s\n' % (e.code, e.read().decode('utf-8', 'ignore')[:800]))
except Exception as e:
    sys.stdout.write('ERR: %s\n' % e)
sys.stdout.flush()
