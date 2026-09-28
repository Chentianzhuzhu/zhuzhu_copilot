# -*- coding: utf-8 -*-
"""直接修改 agent_panel.py 中 _build_tts_page 的提示文案（自动播放）"""
import sys

P = r'C:\Users\zhuzhu\Desktop\my first android app\src\zhuzhu_Copilot\ui\agent_panel.py'
src = open(P, encoding='utf-8').read()

old = ('tip = QLabel("音色选择：AI 使用 tts_speak 直接调用 DashScope API 合成语音，"\n'
       '                     "结果保存到工作目录 tts_output/。点击刷新从云端同步已创建的音色。")\n')
new = ('tip = QLabel("音色选择：AI 使用 tts_speak 调用 DashScope API 流式合成语音，"\n'
       '                     "边生成边自动播放，结果保存到工作目录 tts_output/。点击刷新从云端同步已创建的音色。")\n')
if old in src:
    src = src.replace(old, new)
    open(P, 'w', encoding='utf-8', newline='').write(src)
    sys.stdout.write('TIP_UPDATED\n')
else:
    sys.stdout.write('TIP_NOT_FOUND（可能文案不同，尝试宽匹配）\n')
    idx = src.find('tts_speak')
    sys.stdout.write('tts_speak 位置: %d\n' % idx)
    if idx >= 0:
        sys.stdout.write(src[idx-200:idx+300].replace('\n', '\\n') + '\n')
sys.stdout.flush()
