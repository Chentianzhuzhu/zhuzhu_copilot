# -*- coding: utf-8 -*-
"""验证流式分片结构：首片 WAV 头 / 后续分片格式 / 采样率位深 / 帧对齐"""
import sys, json, base64, urllib.request, struct
sys.path.insert(0, r'C:\Users\zhuzhu\Desktop\my first android app\src')
from zhuzhu_Copilot.core import agent_tts

key = agent_tts.load_api_key()
payload = {
    "model": agent_tts.DEFAULT_TARGET_MODEL,
    "input": {"text": "你好，流式分片结构测试。", "voice": agent_tts.load_config().get("voice_id", "")},
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
for i, c in enumerate(chunks[:6]):
    head = c[:12].hex(' ')
    sys.stdout.write('chunk[%d] len=%d head=%s ascii=%r\n' % (i, len(c), head, c[:12].decode('latin1', 'ignore')))

# 解析首片 WAV 头
if chunks:
    c0 = chunks[0]
    sys.stdout.write('--- 首片解析 ---\n')
    sys.stdout.write('riff=%r size=%d\n' % (c0[:4], struct.unpack('<I', c0[4:8])[0] if len(c0) >= 8 else -1))
    sys.stdout.write('wave=%r\n' % c0[8:12])
    # fmt 块
    pos = 12
    while pos + 8 <= len(c0):
        cid = c0[pos:pos+4]
        csz = struct.unpack('<I', c0[pos+4:pos+8])[0]
        if cid == b'fmt ':
            fmt = c0[pos+8:pos+8+csz]
            sys.stdout.write('fmt: format=%d channels=%d rate=%d bits=%d block_align=%d byte_rate=%d\n' % (
                struct.unpack('<H', fmt[0:2])[0], struct.unpack('<H', fmt[2:4])[0],
                struct.unpack('<I', fmt[4:8])[0], struct.unpack('<H', fmt[14:16])[0],
                struct.unpack('<H', fmt[12:14])[0], struct.unpack('<I', fmt[8:12])[0]))
        elif cid == b'data':
            sys.stdout.write('data: size=%d\n' % csz)
        pos += 8 + csz + (1 if csz % 2 else 0)
        if pos > 200:
            break
    # 检查后续分片是否是裸 PCM（无 RIFF 头）
    for i in (1, 2, 3):
        if i < len(chunks):
            sys.stdout.write('chunk[%d] starts_with_RIFF=%s\n' % (i, chunks[i][:4] == b'RIFF'))
sys.stdout.flush()
