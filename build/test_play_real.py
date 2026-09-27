import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")))

from PyQt6.QtWidgets import QApplication
app = QApplication([])
from PyQt6.QtCore import QUrl, QTimer
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget

MP4 = r"C:\Users\zhuzhu\Desktop\weida\Screenrecording_20260828_135003.mp4"
WAV = r"C:\Users\zhuzhu\Desktop\my first android app\src\diede.wav"


def run(label, path, use_video):
    events = []
    p = QMediaPlayer()
    ao = QAudioOutput()
    p.setAudioOutput(ao)
    if use_video:
        v = QVideoWidget()
        p.setVideoOutput(v)
    p.mediaStatusChanged.connect(lambda s: events.append(f"status={s}"))
    p.playbackStateChanged.connect(lambda s: events.append(f"state={s}"))
    p.errorOccurred.connect(lambda e, es: events.append(f"ERROR={e}|{es}"))
    p.positionChanged.connect(lambda pos: events.append(f"pos={pos}") if pos % 500 < 100 else None)
    p.setSource(QUrl.fromLocalFile(path))
    p.play()
    QTimer.singleShot(3000, app.quit)
    app.exec()
    print(f"\n===== {label} =====")
    print("events:")
    for e in events:
        print("  ", e)


run("mp4 视频(带 QVideoWidget)", MP4, True)
run("mp4 视频(无视频输出)", MP4, False)
run("wav 音频", WAV, False)
