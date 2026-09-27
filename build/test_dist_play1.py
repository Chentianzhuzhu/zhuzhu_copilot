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

from PyQt6.QtCore import QCoreApplication, QUrl, QTimer
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput

app = QCoreApplication([])
WAV = r"C:\Users\zhuzhu\Desktop\my first android app\src\diede.wav"
MP4 = r"C:\Users\zhuzhu\Desktop\weida\Screenrecording_20260828_135003.mp4"
PATH = os.environ.get("PLAY_FILE", WAV)

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
print(f"===== {os.path.basename(PATH)} (dist布局) =====")
for e in events:
    print("  ", e)
