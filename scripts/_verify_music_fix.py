"""验证音乐播放器三个问题修复：
A. 暂停后不重置进度、不重播（_poll 误判曲目结束）
B. 重启后首次 toggle 未 load 也能播放（_loaded 标记）
C. autoplay_on_panel_open 重启后走 play_name 从保存位置续播
D. _music_refresh setChecked blockSignals 不覆盖 autoplay
"""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
sys.path.insert(0, os.path.abspath("src"))

import types
pyg = types.ModuleType("pygame")
pyg.version = types.SimpleNamespace(ver="9.9.9")
mix = types.ModuleType("pygame.mixer")
music = types.ModuleType("pygame.mixer.music")
_state = {"busy": False, "pos": 0, "loaded": False, "paused": False,
          "load_calls": 0, "play_calls": 0}


def _stop():
    _state["busy"] = False; _state["paused"] = False


def _load(f):
    _state["loaded"] = True; _state["busy"] = False
    _state["load_calls"] += 1


def _play(start=0.0):
    if not _state["loaded"]:
        raise RuntimeError("music not loaded")
    _state["busy"] = True; _state["paused"] = False
    _state["pos"] = float(start) * 1000
    _state["play_calls"] += 1


def _pause():
    _state["busy"] = False; _state["paused"] = True


def _unpause():
    _state["busy"] = True; _state["paused"] = False


music.stop = _stop
music.load = _load
music.play = _play
music.pause = _pause
music.unpause = _unpause
music.get_busy = lambda: _state["busy"]
music.get_pos = lambda: _state["pos"]
music.set_volume = lambda v: None
mix.music = music
mix.init = lambda *a, **k: None
mix.get_init = lambda: (44100, -16, 2)
mix.Sound = type("Sound", (), {"__init__": lambda self, f: None,
                               "get_length": lambda self: 180})
pyg.mixer = mix
pyg.error = RuntimeError
sys.modules["pygame"] = pyg
sys.modules["pygame.mixer"] = mix
sys.modules["pygame.mixer.music"] = music

from PyQt6.QtWidgets import QApplication, QCheckBox
app = QApplication([])

import tempfile
import winapp_migrator.core.music_player as mp_mod
_tmp = tempfile.mkdtemp()
mp_mod.MUSIC_DIR = _tmp
mp_mod.STATE_FILE = os.path.join(_tmp, "player.json")
open(os.path.join(_tmp, "a.mp3"), "wb").write(b"x")
open(os.path.join(_tmp, "b.mp3"), "wb").write(b"x")

p = mp_mod.get_player()
p.refresh_library()

# ---- A. 暂停不重置进度、不重播 ----
p.play_name("a.mp3")
_state["pos"] = 30000            # 模拟播到 30 秒
p.toggle()                        # 暂停
assert p.is_paused() and p.is_playing()
pos_paused = p.position()
print("A debug: current=", p.current(), "paused=", p._paused,
      "playing=", p._playing, "base=", p._base, "positions=", p._positions)
p._poll()                         # 暂停期间定时器轮询
print("A debug after poll: current=", p.current(), "positions=", p._positions)
assert p.current() == "a.mp3", "暂停误触结束导致切曲/重播"
assert p.position() == pos_paused, "暂停后进度被重置"
print("A. 暂停不重置进度/不重播: OK, pos=", p.position())

# ---- B. 重启后首次 toggle 未 load 也能播放 ----
p.stop()
p._loaded = False                 # 模拟重启后未 load
p._current = "a.mp3"
p._positions["a.mp3"] = 15        # 保存的续播位置
_state["load_calls"] = _state["play_calls"] = 0
p.toggle()                        # 未 load → 应走 play_name
assert p.is_playing(), "重启后首次播放无响应"
assert _state["loaded"] and _state["load_calls"] >= 1, "未走 play_name 加载"
assert _state["pos"] == 15000, f"应从保存位置15s续播, got {_state['pos']}"
print("B. 重启后首次 toggle 正常播放: OK")

# ---- C. autoplay 重启后从保存位置续播 ----
p.stop()
p._autoplay = True
p._loaded = False
p._current = "a.mp3"
p._positions["a.mp3"] = 20
p._base = 20.0
_state["load_calls"] = _state["play_calls"] = 0
p.autoplay_on_panel_open()
assert p.is_playing(), "自动播放无响应"
assert _state["pos"] == 20000, f"自动播放应从20s续播, got {_state['pos']}"
print("C. 自动播放从保存位置续播: OK")

# ---- D. setChecked blockSignals 不覆盖 autoplay ----
p._autoplay = True
cb = QCheckBox()
cb.toggled.connect(lambda on: p.set_autoplay(on))
cb.blockSignals(True)
cb.setChecked(False)              # 模拟刷新（目标值不同）但被 blockSignals 屏蔽
cb.blockSignals(False)
assert p.autoplay() is True, "刷新覆盖了 autoplay 设置"
print("D. setChecked 刷新不覆盖 autoplay: OK")

print("SMOKE OK")
