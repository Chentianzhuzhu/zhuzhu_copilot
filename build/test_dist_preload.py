import os
import sys
import ctypes

DIST = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dist", "zhuzhu Copilot"))
os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")
os.environ.pop("QT_PLUGIN_PATH", None)

sys.path.insert(0, DIST)
os.add_dll_directory(DIST)
os.add_dll_directory(os.path.join(DIST, "PyQt6", "Qt6", "bin"))
ctypes.WinDLL(os.path.join(DIST, "Qt6Core.dll"))

# 模拟 _preload_media_dlls：从 Qt6/bin 预加载 ffmpeg 库
BIN = os.path.join(DIST, "PyQt6", "Qt6", "bin")
for f in ("avcodec-61.dll", "avformat-61.dll", "avutil-59.dll",
          "swresample-5.dll", "swscale-8.dll", "opengl32sw.dll"):
    p = os.path.join(BIN, f)
    if os.path.isfile(p):
        try:
            ctypes.WinDLL(p)
            print(f"预加载: {f} from {BIN}")
        except Exception as e:
            print(f"预加载失败: {f} -> {e}")

from PyQt6.QtCore import QCoreApplication, QUrl, QTimer
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput

app = QCoreApplication([])
SINE = r"C:\Users\zhuzhu\Desktop\my first android app\build\sine.wav"
WAV = r"C:\Users\zhuzhu\Desktop\my first android app\src\diede.wav"
PATH = os.environ.get("PLAY_FILE", SINE)

events = []
p = QMediaPlayer()
ao = QAudioOutput()
p.setAudioOutput(ao)
p.errorOccurred.connect(lambda e, es: events.append(f"ERROR={e}|{es}"))
p.mediaStatusChanged.connect(lambda s: events.append(f"status={s}"))
p.playbackStateChanged.connect(lambda s: events.append(f"state={s}"))
p.positionChanged.connect(lambda pos: events.append(f"pos={pos}"))
p.setSource(QUrl.fromLocalFile(PATH))
p.play()
QTimer.singleShot(3000, app.quit)
app.exec()
print(f"===== {os.path.basename(PATH)} (dist布局+Qt6/bin预加载) =====")
for e in events:
    print("  ", e)
