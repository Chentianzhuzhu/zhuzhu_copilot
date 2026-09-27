import wave
import math
import struct
import os

# 1. 验证 diede.wav 能否被 wave 模块读取
try:
    w = wave.open(r"C:\Users\zhuzhu\Desktop\my first android app\src\diede.wav", "rb")
    print("diede.wav OK:", w.getnchannels(), "ch", w.getsampwidth()*8, "bit",
          w.getframerate(), "Hz", w.getnframes(), "frames",
          f"{(w.getnframes()/w.getframerate()):.1f}s")
    w.close()
except Exception as e:
    print("diede.wav 读取失败:", e)

# 2. 生成一个标准正弦波 wav
out = r"C:\Users\zhuzhu\Desktop\my first android app\build\sine.wav"
sr = 44100
dur = 2.0
frames = int(sr * dur)
with wave.open(out, "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(sr)
    data = bytearray()
    for i in range(frames):
        v = int(16000 * math.sin(2 * math.pi * 440 * i / sr))
        data += struct.pack("<h", v)
    w.writeframes(bytes(data))
print("生成标准 wav:", out, os.path.getsize(out), "bytes")
