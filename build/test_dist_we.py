import os
import sys
import ctypes

DIST = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dist", "zhuzhu Copilot"))
os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")
os.environ.pop("QT_PLUGIN_PATH", None)
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS",
                      "--disable-gpu --no-sandbox --disable-gpu-compositing --disable-software-rasterizer --single-process")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

sys.path.insert(0, DIST)
os.add_dll_directory(DIST)
os.add_dll_directory(os.path.join(DIST, "PyQt6", "Qt6", "bin"))
ctypes.WinDLL(os.path.join(DIST, "Qt6Core.dll"))

print("模拟 main.py 预导入 QtWebEngineWidgets ...")
try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa
    print("WebEngine 预导入 OK")
except Exception as e:
    print("WebEngine 预导入失败:", e)

from PyQt6.QtCore import QCoreApplication, QUrl, QTimer
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput

app = QCoreApplication([])
WAV = r"C:\Users\zhuzhu\Desktop\my first android app\src\diede.wav"

events = []
p = QMediaPlayer()
ao = QAudioOutput()
p.setAudioOutput(ao)
p.errorOccurred.connect(lambda e, es: events.append(f"ERROR={e}|{es}"))
p.mediaStatusChanged.connect(lambda s: events.append(f"status={s}"))
p.playbackStateChanged.connect(lambda s: events.append(f"state={s}"))
p.positionChanged.connect(lambda pos: events.append(f"pos={pos}"))
p.setSource(QUrl.fromLocalFile(WAV))
p.play()
QTimer.singleShot(3000, app.quit)
app.exec()
print("===== wav (dist布局 + WebEngine 预导入) =====")
for e in events:
    print("  ", e)
