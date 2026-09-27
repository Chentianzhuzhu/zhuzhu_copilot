"""验证：拖动音量条触发刷新时进度条不再跳回开头。
场景：播放中（fake pos=30s，_base=0）→ set_volume → state_changed
→ _music_refresh 调 _music_tick(mp.current_position(), mp.duration())
→ 进度条应显示 30 而非 0。
"""
import os, sys, types
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
sys.path.insert(0, os.path.abspath("src"))

import types as _t
pyg = _t.ModuleType("pygame")
pyg.version = _t.SimpleNamespace(ver="9.9.9")
mix = _t.ModuleType("pygame.mixer")
music = _t.ModuleType("pygame.mixer.music")
_state = {"busy": False, "pos": 0, "loaded": False, "paused": False}
music.stop = lambda: (_state.__setitem__("busy", False), _state.__setitem__("paused", False))
def _load(f): _state["loaded"] = True; _state["busy"] = False
def _play(start=0.0):
    if not _state["loaded"]:
        raise RuntimeError("music not loaded")
    _state["busy"] = True; _state["paused"] = False
    _state["pos"] = float(start) * 1000
def _pause(): _state["busy"] = False; _state["paused"] = True
def _unpause(): _state["busy"] = True; _state["paused"] = False
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

from PyQt6.QtWidgets import QApplication, QSlider, QLabel
from PyQt6.QtCore import Qt
app = QApplication([])

import tempfile
import winapp_migrator.core.music_player as mp_mod
_tmp = tempfile.mkdtemp()
mp_mod.MUSIC_DIR = _tmp
mp_mod.STATE_FILE = os.path.join(_tmp, "player.json")
open(os.path.join(_tmp, "a.mp3"), "wb").write(b"x")

p = mp_mod.get_player()
p.refresh_library()
p.play_name("a.mp3")

# 播放中播到 30 秒
_state["pos"] = 30000
assert p.position() == 0, "旧 position() 只返回 _base"
cur = p.current_position()
assert cur == 30, f"current_position 应返回实时位置30, got {cur}"
print("current_position 实时位置:", cur)

# 绑定 _AgentSettingsDialog._music_tick 到 stub，验证刷新不会跳 0
from winapp_migrator.ui import agent_panel as ap
stub = types.SimpleNamespace()
stub.music_slider = QSlider(Qt.Orientation.Horizontal)
stub.music_slider.setRange(0, 0)
stub._music = p
stub.music_pos = QLabel()
stub.music_total = QLabel()
stub._music_tick = types.MethodType(ap._AgentSettingsDialog._music_tick, stub)

# 模拟拖动音量条触发的 _music_refresh 末尾刷新
stub._music_tick(p.current_position(), p.duration())
slider_val = stub.music_slider.value()
assert slider_val == 30, f"刷新后进度条应为30, got {slider_val}"
print("刷新后进度条位置:", slider_val, "-> 不跳回开头 OK")

# 修复前行为对照：无参 _music_tick() 会把进度条跳回 0
stub._music_tick()
old_val = stub.music_slider.value()
print("对照：无参 _music_tick() 进度条:", old_val, "(旧行为跳回 0)")

print("SMOKE OK")
