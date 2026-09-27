# -*- coding: utf-8 -*-
"""验证 pygame.mixer 流式播放可行性（dummy 驱动，验证构造/排队不报错）"""
import sys, os, json, base64, urllib.request, struct, threading, time
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')
sys.path.insert(0, r'C:\Users\zhuzhu\Desktop\my first android app\src')

import pygame

def make_wav(pcm: bytes, rate=24000, channels=1, bits=16) -> bytes:
    """PCM -> 完整 WAV 内存字节（供 pygame Sound(buffer=) 播放）"""
    data_size = len(pcm)
    byte_rate = rate * channels * bits // 8
    block_align = channels * bits // 8
    hdr = struct.pack('<4sI4s4sIHHIIHH4sI',
                      b'RIFF', 36 + data_size, b'WAVE', b'fmt ', 16,
                      1, channels, rate, byte_rate, block_align, bits, b'data', data_size)
    return hdr + pcm

try:
    pygame.mixer.pre_init(24000, -16, 1, 4096)
    pygame.mixer.init()
    sys.stdout.write('MIXER_INIT_OK\n')
except Exception as e:
    sys.stdout.write('MIXER_INIT_FAIL: %s\n' % e); sys.exit()

# 拉真实流式分片
from winapp_migrator.core import agent_tts
key = agent_tts.load_api_key()
payload = {
    "model": agent_tts.DEFAULT_TARGET_MODEL,
    "input": {"text": "流式播放验证。一二三四五。", "voice": agent_tts.load_config().get("voice_id", "")},
    "parameters": {"stream": True},
}
req = urllib.request.Request(agent_tts.TTS_URL,
    data=json.dumps(payload).encode('utf-8'),
    headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
             "X-DashScope-SSE": "enable", "Accept": "text/event-stream"})

chunks = []
with urllib.request.urlopen(req, timeout=60) as resp:
    for raw in resp:
        line = raw.decode('utf-8', 'ignore').strip()
        if line.startswith('data:'):
            try:
                d = json.loads(line[5:].strip())
                a = ((d.get('output') or {}).get('audio') or {}).get('data') or ''
                if a:
                    chunks.append(base64.b64decode(a))
            except Exception:
                pass
sys.stdout.write('chunks=%d\n' % len(chunks))

# 首片剥离 44 字节头，后续直接 PCM
chan = pygame.mixer.Channel(0)
queued = 0
for i, c in enumerate(chunks):
    pcm = c[44:] if i == 0 else c
    try:
        snd = pygame.mixer.Sound(buffer=make_wav(pcm))
        if queued == 0:
            chan.play(snd)
        else:
            chan.queue(snd)
        queued += 1
    except Exception as e:
        sys.stdout.write('PLAY_FAIL chunk%d: %s\n' % (i, e))
sys.stdout.write('queued=%d busy=%s\n' % (queued, chan.get_busy()))
time.sleep(1.0)
sys.stdout.write('after 1s busy=%s\n' % chan.get_busy())
pygame.mixer.quit()
sys.stdout.write('PYGAME_STREAM_OK\n')
sys.stdout.flush()
