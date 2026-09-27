# -*- coding: utf-8 -*-
"""检查项目音频播放能力：QtMultimedia / pygame / winsound / ffplay 等"""
import sys, importlib, shutil

# 1) PyQt6.QtMultimedia 是否可用
try:
    from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
    sys.stdout.write('QtMultimedia: AVAILABLE\n')
except Exception as e:
    sys.stdout.write('QtMultimedia: UNAVAILABLE -> %s\n' % e)

# 2) 音频库
for mod in ('pygame', 'playsound', 'winsound', 'pyaudio', 'sounddevice'):
    try:
        importlib.import_module(mod)
        sys.stdout.write('%s: AVAILABLE\n' % mod)
    except Exception as e:
        sys.stdout.write('%s: missing (%s)\n' % (mod, type(e).__name__))

# 3) ffplay / ffmpeg
for exe in ('ffplay', 'ffmpeg'):
    sys.stdout.write('%s: %s\n' % (exe, shutil.which(exe) or 'NOT FOUND'))

sys.stdout.flush()
