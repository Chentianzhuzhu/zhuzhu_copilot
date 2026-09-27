import os
import sys
import ctypes

DIST = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dist", "zhuzhu Copilot"))
os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")
os.environ.pop("QT_PLUGIN_PATH", None)

sys.path.insert(0, DIST)
# 模拟打包 exe：find_qt 第一分支（exe 目录有 Qt6Core）+ rthook（Qt6/bin）
os.add_dll_directory(DIST)
os.add_dll_directory(os.path.join(DIST, "PyQt6", "Qt6", "bin"))
ctypes.WinDLL(os.path.join(DIST, "Qt6Core.dll"))

from PyQt6.QtCore import QCoreApplication, QUrl, QTimer, QLibraryInfo
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput

print("PyQt6:", __import__("PyQt6").__file__)
print("Prefix:", QLibraryInfo.path(QLibraryInfo.LibraryPath.PrefixPath))
print("Plugins:", QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath))

app = QCoreApplication([])

WAV = r"C:\Users\zhuzhu\Desktop\my first android app\src\diede.wav"
MP4 = r"C:\Users\zhuzhu\Desktop\weida\Screenrecording_20260828_135003.mp4"


def run(label, path):
    events = []
    p = QMediaPlayer()
    ao = QAudioOutput()
    p.setAudioOutput(ao)
    p.errorOccurred.connect(lambda e, es: events.append(f"ERROR={e}|{es}"))
    p.mediaStatusChanged.connect(lambda s: events.append(f"status={s}"))
    p.playbackStateChanged.connect(lambda s: events.append(f"state={s}"))
    p.setSource(QUrl.fromLocalFile(path))
    p.play()
    QTimer.singleShot(3000, app.quit)
    app.exec()
    print(f"\n===== {label} =====")
    for e in events:
        print("  ", e)


run("wav 音频 (dist布局)", WAV)
run("mp4 视频音频 (dist布局)", MP4)
