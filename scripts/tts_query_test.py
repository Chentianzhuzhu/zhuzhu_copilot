# -*- coding: utf-8 -*-
"""实测声音复刻 API 的 query / delete 操作参数"""
import json
import urllib.request

API_KEY = 'sk-ws-H.EEERIHM.mujM.MEUCIQC8bWtlWQDDg8bo9Rraa8Ggkf5yG7cvd2pRcUff0yqxJQIgBBwcZiene8Yc2CPCZ-ohXN11Pe2CSpXVQPsmRcXttuo'
CUSTOMIZATION_URL = 'https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization'
TARGET_MODEL = 'qwen3-tts-vc-2026-01-22'


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
    # 1. query 查询音色列表（不同参数组合依次试）
    for extra in [
        {'action': 'query', 'target_model': TARGET_MODEL},
        {'action': 'query'},
    ]:
        call({'model': 'qwen-voice-enrollment', 'input': extra}, 'query %s' % extra)
        print()
