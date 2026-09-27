# -*- coding: utf-8 -*-
"""截取 diede.wav 前 20 秒作为复刻参考音频"""
import wave
import os

SRC = r'C:\Users\zhuzhu\Desktop\my first android app\src\diede.wav'
OUT = r'C:\Users\zhuzhu\Desktop\my first android app\src\diede_20s.wav'

src = wave.open(SRC, 'rb')
ch = src.getnchannels()
rate = src.getframerate()
sw = src.getsampwidth()
frames = src.getnframes()
log = []
log.append('原音频: 声道=%d 采样率=%d 位宽=%d 时长=%.2fs' % (ch, rate, sw, frames / rate))

cut_frames = int(20 * rate)
src.setpos(0)
data = src.readframes(cut_frames)
src.close()

out = wave.open(OUT, 'wb')
out.setnchannels(ch)
out.setsampwidth(sw)
out.setframerate(rate)
out.writeframes(data)
out.close()

log.append('已生成 diede_20s.wav, 大小: %d bytes' % os.path.getsize(OUT))

# 检测前20秒的音量，判断是否含人声（粗略：计算 RMS）
import struct
samples = struct.unpack('<%dh' % (len(data) // 2), data)
rms = (sum(s * s for s in samples) / len(samples)) ** 0.5
log.append('前20秒 RMS 音量: %.1f' % rms)

with open(r'C:\Users\zhuzhu\Desktop\my first android app\scripts\cut_audio_log.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(log))
print('\n'.join(log))
