# -*- coding: utf-8 -*-
"""重建蝶-三角洲音色（delete 测试误删后重新创建）并输出 voice_id"""
import base64
import json
import pathlib
import urllib.request

API_KEY = 'sk-ws-H.EEERIHM.mujM.MEUCIQC8bWtlWQDDg8bo9Rraa8Ggkf5yG7cvd2pRcUff0yqxJQIgBBwcZiene8Yc2CPCZ-ohXN11Pe2CSpXVQPsmRcXttuo'
CUSTOMIZATION_URL = 'https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization'
AUDIO_FILE = r'C:\Users\zhuzhu\Desktop\my first android app\src\diede_20s.wav'
TARGET_MODEL = 'qwen3-tts-vc-2026-01-22'

b64 = 'data:audio/wav;base64,' + base64.b64encode(pathlib.Path(AUDIO_FILE).read_bytes()).decode()
payload = {
    'model': 'qwen-voice-enrollment',
    'input': {
        'action': 'create',
        'target_model': TARGET_MODEL,
        'preferred_name': 'diede',
        'audio': {'data': b64}
    }
}
req = urllib.request.Request(
    CUSTOMIZATION_URL,
    data=json.dumps(payload).encode('utf-8'),
    headers={'Authorization': 'Bearer ' + API_KEY, 'Content-Type': 'application/json'})
try:
    r = urllib.request.urlopen(req, timeout=60)
    d = json.loads(r.read())
    voice_id = d.get('output', {}).get('voice', '')
    print('新 voice_id:', voice_id)
    with open(r'C:\Users\zhuzhu\Desktop\my first android app\scripts\voice_id.txt', 'w', encoding='utf-8') as f:
        f.write(voice_id)
except urllib.error.HTTPError as e:
    print('失败 HTTP %s: %s' % (e.code, e.read().decode('utf-8', 'ignore')))
