# -*- coding: utf-8 -*-
"""测试 Qwen-TTS 声音复刻 API"""
import base64
import json
import urllib.request
import pathlib

API_KEY = 'sk-ws-H.EEERIHM.mujM.MEUCIQC8bWtlWQDDg8bo9Rraa8Ggkf5yG7cvd2pRcUff0yqxJQIgBBwcZiene8Yc2CPCZ-ohXN11Pe2CSpXVQPsmRcXttuo'
CUSTOMIZATION_URL = 'https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization'
TTS_URL = 'https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation'
AUDIO_FILE = r'C:\Users\zhuzhu\Desktop\my first android app\src\diede_20s.wav'


def base64_audio(path):
    data = pathlib.Path(path).read_bytes()
    b64 = base64.b64encode(data).decode()
    return 'data:audio/wav;base64,' + b64


def create_voice(audio_b64, target_model='qwen3-tts-vc-2026-01-22', preferred_name='diede'):
    payload = {
        'model': 'qwen-voice-enrollment',
        'input': {
            'action': 'create',
            'target_model': target_model,
            'preferred_name': preferred_name,
            'audio': {'data': audio_b64}
        }
    }
    req = urllib.request.Request(
        CUSTOMIZATION_URL,
        data=json.dumps(payload).encode('utf-8'),
        headers={'Authorization': 'Bearer ' + API_KEY, 'Content-Type': 'application/json'}
    )
    try:
        r = urllib.request.urlopen(req, timeout=60)
        d = json.loads(r.read())
        print('创建音色响应:', json.dumps(d, ensure_ascii=False, indent=2))
        return d
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', 'ignore')
        print('创建音色失败 HTTP %s: %s' % (e.code, body))
        return None


def synthesize_text(voice_id, text='你好，这是音色测试。', model='qwen3-tts-vc-2026-01-22'):
    payload = {
        'model': model,
        'input': {
            'text': text,
            'voice': voice_id
        }
    }
    req = urllib.request.Request(
        TTS_URL,
        data=json.dumps(payload).encode('utf-8'),
        headers={'Authorization': 'Bearer ' + API_KEY, 'Content-Type': 'application/json'}
    )
    try:
        r = urllib.request.urlopen(req, timeout=60)
        d = json.loads(r.read())
        out = d.get('output', d)
        print('合成响应:', json.dumps(out, ensure_ascii=False)[:500])
        return d
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', 'ignore')
        print('合成失败 HTTP %s: %s' % (e.code, body))
        return None


if __name__ == '__main__':
    import os
    audio_path = r'C:\Users\zhuzhu\Desktop\my first android app\src\diede_20s.wav'
    print('音频文件:', audio_path)
    print('文件大小:', os.path.getsize(audio_path), 'bytes')

    print('=== 步骤1: 创建音色 ===')
    b64 = base64_audio(audio_path)
    print('Base64长度:', len(b64))
    result = create_voice(b64, preferred_name='diede')

    if result and result.get('output', {}).get('voice'):
        voice_id = result['output']['voice']
        print('voice_id:', voice_id)
        print('\n=== 步骤2: 使用复刻音色合成语音 ===')
        synthesize_text(voice_id)
    else:
        print('创建音色失败')
