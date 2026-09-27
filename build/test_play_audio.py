import os
import sys

os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")))

from PyQt6.QtCore import QCoreApplication, QUrl, QTimer
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput

app = QCoreApplication([])

WAV = r"C:\Users\zhuzhu\Desktop\my first android app\src\diede.wav"
MP4 = r"C:\Users\zhuzhu\Desktop\weida\Screenrecording_20260828_135003.mp4"


def run(label, path):
    events = []
    p = QMediaPlayer()
    ao = QAudioOutput()
    p.setAudioOutput(ao)
    p.mediaStatusChanged.connect(lambda s: events.append(f"status={s}"))
    p.playbackStateChanged.connect(lambda s: events.append(f"state={s}"))
    p.errorOccurred.connect(lambda e, es: events.append(f"ERROR={e}|{es}"))
    p.setSource(QUrl.fromLocalFile(path))
    p.play()
    QTimer.singleShot(3000, app.quit)
    app.exec()
    print(f"===== {label} =====")
    for e in events:
        print("  ", e)
    print()


run("wav 音频 (QCoreApp)", WAV)
run("mp4 视频(无视频输出) (QCoreApp)", MP4)
