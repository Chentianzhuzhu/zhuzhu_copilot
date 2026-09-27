# -*- coding: utf-8 -*-
"""测量 stream 流式节奏：首片耗时、分片数、总耗时、总字节"""
import sys, json, time, base64, urllib.request, urllib.error
sys.path.insert(0, r'C:\Users\zhuzhu\Desktop\my first android app\src')
from winapp_migrator.core import agent_tts

key = agent_tts.load_api_key()
payload = {
    "model": agent_tts.DEFAULT_TARGET_MODEL,
    "input": {"text": "你好，这是流式语音合成测试。今天天气不错，适合出去走走。",
              "voice": agent_tts.load_config().get("voice_id", "")},
    "parameters": {"stream": True},
}
req = urllib.request.Request(
    agent_tts.TTS_URL,
    data=json.dumps(payload).encode('utf-8'),
    headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
             "X-DashScope-SSE": "enable", "Accept": "text/event-stream"})

t0 = time.time()
first_t = None
chunks = 0
total = 0
last_event = ""
try:
    with urllib.request.urlopen(req, timeout=90) as resp:
        for raw in resp:
            if first_t is None:
                first_t = time.time() - t0
            line = raw.decode('utf-8', 'ignore').strip()
            if line.startswith('data:'):
                try:
                    d = json.loads(line[5:].strip())
                    audio = ((d.get('output') or {}).get('audio') or {}).get('data') or ''
                    if audio:
                        chunks += 1
                        total += len(base64.b64decode(audio))
                        last_event = d.get('output', {}).get('event') or last_event
                except Exception:
                    pass
    sys.stdout.write('first_chunk_sec=%.2f\n' % first_t)
    sys.stdout.write('chunks=%d total_wav_bytes=%d last_event=%s\n' % (chunks, total, last_event))
    sys.stdout.write('total_sec=%.2f\n' % (time.time() - t0))
except urllib.error.HTTPError as e:
    sys.stdout.write('HTTP %s: %s\n' % (e.code, e.read().decode('utf-8', 'ignore')[:500]))
except Exception as e:
    sys.stdout.write('ERR: %s\n' % e)
sys.stdout.flush()
