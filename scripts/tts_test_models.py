# -*- coding: utf-8 -*-
"""测试各模型对蝶-三角洲音色的支持"""
import urllib.request
import json

URL = 'https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation'
KEY = 'sk-ws-H.EEERIHM.mujM.MEUCIQC8bWtlWQDDg8bo9Rraa8Ggkf5yG7cvd2pRcUff0yqxJQIgBBwcZiene8Yc2CPCZ-ohXN11Pe2CSpXVQPsmRcXttuo'


def call(voice, model):
    body = json.dumps({
        'model': model,
        'input': {'text': '你好，这是音色测试。', 'voice': voice}
    }).encode('utf-8')
    req = urllib.request.Request(
        URL, data=body,
        headers={'Authorization': 'Bearer ' + KEY, 'Content-Type': 'application/json'})
    try:
        r = urllib.request.urlopen(req, timeout=40)
        d = json.loads(r.read())
        out = d.get('output', d)
        return 'OK: ' + json.dumps(out, ensure_ascii=False)[:300]
    except urllib.error.HTTPError as e:
        return 'ERR %s: %s' % (e.code, e.read().decode('utf-8', 'ignore')[:400])
    except Exception as e:
        return 'EXC: %s' % e


if __name__ == '__main__':
    models = [
        'qwen3-tts-flash',
        'qwen3-tts-instruct-flash',
        'qwen3-tts-flash-realtime',
        'qwen3-tts-instruct-flash-realtime',
        'qwen-tts-realtime',
        'qwen-tts',
    ]
    for m in models:
        print(m, '=>', call('蝶-三角洲', m))
