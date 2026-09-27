# -*- coding: utf-8 -*-
"""完整流程：创建音色（若已存在则直接使用）→ 合成 → 下载音频"""
import base64
import json
import urllib.request
import pathlib
import os

API_KEY = 'sk-ws-H.EEERIHM.mujM.MEUCIQC8bWtlWQDDg8bo9Rraa8Ggkf5yG7cvd2pRcUff0yqxJQIgBBwcZiene8Yc2CPCZ-ohXN11Pe2CSpXVQPsmRcXttuo'
CUSTOMIZATION_URL = 'https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization'
TTS_URL = 'https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation'
AUDIO_FILE = r'C:\Users\zhuzhu\Desktop\my first android app\src\diede_20s.wav'
OUT_FILE = r'C:\Users\zhuzhu\Desktop\my first android app\src\tts_test_output.wav'
LOG_FILE = r'C:\Users\zhuzhu\Desktop\my first android app\scripts\full_tts_log.txt'

log = []


def log_print(*args):
    s = ' '.join(str(a) for a in args)
    log.append(s)
    print(s)


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
        return d
    except urllib.error.HTTPError as e:
        log_print('创建音色失败 HTTP %s: %s' % (e.code, e.read().decode('utf-8', 'ignore')))
        return None


def synthesize_and_download(voice_id, text='你好，我是蝶三角洲，很高兴认识你。', model='qwen3-tts-vc-2026-01-22'):
    payload = {
        'model': model,
        'input': {'text': text, 'voice': voice_id}
    }
    req = urllib.request.Request(
        TTS_URL,
        data=json.dumps(payload).encode('utf-8'),
        headers={'Authorization': 'Bearer ' + API_KEY, 'Content-Type': 'application/json'}
    )
    try:
        r = urllib.request.urlopen(req, timeout=120)
        d = json.loads(r.read())
        audio_url = d.get('output', {}).get('audio', {}).get('url', '')
        if not audio_url:
            log_print('响应无音频 URL:', json.dumps(d, ensure_ascii=False)[:500])
            return False
        log_print('音频 URL 已获取，开始下载...')
        urllib.request.urlretrieve(audio_url, OUT_FILE)
        size = os.path.getsize(OUT_FILE)
        log_print('下载完成: %s (%d bytes)' % (OUT_FILE, size))
        return True
    except urllib.error.HTTPError as e:
        log_print('合成失败 HTTP %s: %s' % (e.code, e.read().decode('utf-8', 'ignore')))
        return False
    except Exception as e:
        log_print('下载失败: %s' % e)
        return False


if __name__ == '__main__':
    b64 = 'data:audio/wav;base64,' + base64.b64encode(pathlib.Path(AUDIO_FILE).read_bytes()).decode()
    log_print('=== 步骤1: 创建音色 ===')
    result = create_voice(b64)
    if not result or 'voice' not in result.get('output', {}):
        log_print('创建音色失败:', json.dumps(result or {}, ensure_ascii=False))
    else:
        voice_id = result['output']['voice']
        log_print('voice_id:', voice_id)
        log_print('=== 步骤2: 合成并下载 ===')
        synthesize_and_download(voice_id)

    with open(LOG_FILE, 'w', encoding='utf-8') as f:
        f.write('\n'.join(log))
