# -*- coding: utf-8 -*-
"""继续实测 query/delete 参数组合（修复元组）"""
import json
import urllib.request

API_KEY = 'sk-ws-H.EEERIHM.mujM.MEUCIQC8bWtlWQDDg8bo9Rraa8Ggkf5yG7cvd2pRcUff0yqxJQIgBBwcZiene8Yc2CPCZ-ohXN11Pe2CSpXVQPsmRcXttuo'
CUSTOMIZATION_URL = 'https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization'
TARGET_MODEL = 'qwen3-tts-vc-2026-01-22'
VOICE_ID = 'qwen-tts-vc-diede-voice-20260814101259206-1475'


def call(payload, desc):
    req = urllib.request.Request(
        CUSTOMIZATION_URL,
        data=json.dumps(payload).encode('utf-8'),
        headers={'Authorization': 'Bearer ' + API_KEY, 'Content-Type': 'application/json'})
    try:
        r = urllib.request.urlopen(req, timeout=60)
        d = json.loads(r.read())
        print('=== %s 成功 ===' % desc)
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return d
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', 'ignore')
        print('=== %s 失败 HTTP %s ===' % (desc, e.code))
        print(body[:800])
        return None


if __name__ == '__main__':
    combos = [
        ({'model': 'qwen-voice-enrollment',
          'input': {'action': 'query', 'target_model': TARGET_MODEL, 'voice': VOICE_ID}},
         'query+voice'),
        ({'model': 'qwen-voice-enrollment',
          'input': {'action': 'query', 'target_model': TARGET_MODEL, 'voice': ''}},
         'query+voice空'),
        ({'model': 'qwen-voice-enrollment',
          'input': {'action': 'delete', 'voice': VOICE_ID}},
         'delete'),
        ({'model': 'qwen-voice-enrollment',
          'input': {'action': 'delete', 'voice': VOICE_ID, 'target_model': TARGET_MODEL}},
         'delete+target_model'),
    ]
    for p, d in combos:
        call(p, d)
        print()
