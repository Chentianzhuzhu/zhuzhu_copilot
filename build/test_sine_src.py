import os
import sys

os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")))

from PyQt6.QtCore import QCoreApplication, QUrl, QTimer
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput

app = QCoreApplication([])
SINE = r"C:\Users\zhuzhu\Desktop\my first android app\build\sine.wav"

events = []
p = QMediaPlayer()
ao = QAudioOutput()
p.setAudioOutput(ao)
p.errorOccurred.connect(lambda e, es: events.append(f"ERROR={e}|{es}"))
p.mediaStatusChanged.connect(lambda s: events.append(f"status={s}"))
p.playbackStateChanged.connect(lambda s: events.append(f"state={s}"))
p.positionChanged.connect(lambda pos: events.append(f"pos={pos}"))
p.setSource(QUrl.fromLocalFile(SINE))
p.play()
QTimer.singleShot(3000, app.quit)
app.exec()
print("===== sine.wav (源码环境) =====")
for e in events:
    print("  ", e)
