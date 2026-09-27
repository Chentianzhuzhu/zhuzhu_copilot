# -*- coding: utf-8 -*-
"""实验：QMediaPlayer + 自定义 QIODevice，分片写入能否边写边播（离屏观察状态机）"""
import sys, os, time, json, base64, urllib.request
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, r'C:\Users\zhuzhu\Desktop\my first android app\src')

from PyQt6.QtCore import QIODevice, QByteArray, QTimer, QUrl
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtWidgets import QApplication

app = QApplication([])

class FeedDevice(QIODevice):
    def __init__(self):
        super().__init__()
        self.buf = QByteArray()
        self.pos = 0
        self.open(QIODevice.OpenModeFlag.ReadOnly | QIODevice.OpenModeFlag.WriteOnly)
    def readData(self, maxlen):
        if self.pos >= self.buf.size():
            return b''
        n = min(maxlen, self.buf.size() - self.pos)
        data = self.buf.mid(self.pos, n).data()
        self.pos += n
        return data
    def writeData(self, data):
        self.buf.append(QByteArray(bytes(data)))
        return len(data)
    def seek(self, pos):
        if 0 <= pos <= self.buf.size():
            self.pos = pos
            return True
        return False
    def size(self):
        return self.buf.size()
    def bytesAvailable(self):
        return self.buf.size() - self.pos

player = QMediaPlayer()
audio = QAudioOutput()
player.setAudioOutput(audio)
audio.setVolume(0.0)  # 离屏无设备，静音

dev = FeedDevice()
player.setSourceDevice(dev, QUrl())

# 拉取真实 SSE 分片
sys.path.insert(0, r'C:\Users\zhuzhu\Desktop\my first android app\src')
from winapp_migrator.core import agent_tts
key = agent_tts.load_api_key()
payload = {
    "model": agent_tts.DEFAULT_TARGET_MODEL,
    "input": {"text": "你好，流式播放测试。一二三四五六七八九十。", "voice": agent_tts.load_config().get("voice_id", "")},
    "parameters": {"stream": True},
}
req = urllib.request.Request(agent_tts.TTS_URL,
    data=json.dumps(payload).encode('utf-8'),
    headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
             "X-DashScope-SSE": "enable", "Accept": "text/event-stream"})

chunks = []
with urllib.request.urlopen(req, timeout=60) as resp:
    for raw in resp:
        line = raw.decode('utf-8', 'ignore').strip()
        if line.startswith('data:'):
            try:
                d = json.loads(line[5:].strip())
                a = ((d.get('output') or {}).get('audio') or {}).get('data') or ''
                if a:
                    chunks.append(base64.b64decode(a))
            except Exception:
                pass
sys.stdout.write('chunks=%d total=%d\n' % (len(chunks), sum(len(c) for c in chunks)))

# 分片模拟真实节奏喂入
states = []
t0 = time.time()
def feed():
    for i, c in enumerate(chunks):
        dev.writeData(c)
        states.append((round(time.time()-t0, 2), i, player.mediaStatus().name))
        time.sleep(0.1)
    sys.stdout.write('FEED_DONE states=%d\n' % len(states))
    for s in states[:30]:
        sys.stdout.write('  t=%.2f chunk=%d status=%s\n' % s)

QTimer.singleShot(0, feed)
def poll():
    global n
    n = getattr(poll, 'n', 0) + 1
    if n % 10 == 0:
        sys.stdout.write('poll t=%.1f status=%s state=%s pos=%d dur=%d\n' % (
            time.time()-t0, player.mediaStatus().name, player.playbackState().name,
            player.position(), player.duration()))
    if n > 120:  # 12s
        app.quit()
QTimer.singleShot(100, poll) if False else None
timer = QTimer(); timer.timeout.connect(poll); timer.start(100)
QTimer.singleShot(14000, app.quit)
app.exec()
sys.stdout.write('EXPERIMENT_DONE\n')
sys.stdout.flush()
