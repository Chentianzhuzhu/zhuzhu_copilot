"""音乐播放核心模块：基于 pygame.mixer，支持上传/播放/进度/音量/模式/持久化。

- 歌曲存放在 ~/.zhuzhu_Copilot/music/（上传时复制入库）
- 播放状态（当前曲目/各曲进度/音量/模式/自动播放开关）写入同目录 player.json
- pygame 延迟初始化：首次真实播放才 init，避免无音频设备/测试环境崩溃
- 纯 Qt 信号驱动（QObject 单例），UI 轮询 position 更新进度条
"""
from zhuzhu_Copilot import app_identity
import json
import os
import random
import tempfile
import threading
import time

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

MUSIC_DIR = os.path.join(str(app_identity.data_root()), "music")
STATE_FILE = os.path.join(MUSIC_DIR, "player.json")
AUDIO_EXTS = (".mp3", ".wav", ".ogg", ".flac", ".m4a")

MODE_SEQUENCE = "sequence"      # 顺序播放
MODE_RANDOM = "random"          # 随机播放
MODE_LOOP_ONE = "loop_one"      # 单曲循环

_MODE_LABELS = {MODE_SEQUENCE: "顺序", MODE_RANDOM: "随机", MODE_LOOP_ONE: "单曲循环"}

# 播放进度落盘节流间隔（秒）：播放途中周期写入，关闭/强杀时最多丢失该窗口内的进度
STATE_FLUSH_S = 5

_pygame_lock = threading.Lock()


class MusicPlayer(QObject):
    """全局单例音乐播放器（设置页与 AI 面板共享）。"""

    library_changed = pyqtSignal()        # 歌单增删
    song_changed = pyqtSignal(str)        # 当前曲目变化（文件名，空=无）
    state_changed = pyqtSignal()          # 播放/暂停/模式/音量任一变化
    tick = pyqtSignal(int, int)           # (当前秒, 总秒) 供 UI 更新进度条

    # ---- 状态 ----
    _library = []           # 歌曲文件名列表（扫描结果）
    _current = ""           # 当前曲目文件名
    _playing = False        # True=播放中（含暂停状态由 pygame 自管）
    _paused = False
    _volume = 1.0
    _mode = MODE_SEQUENCE
    _autoplay = False
    _positions = {}         # 文件名 -> 播放秒数
    _base = 0.0             # seek 基准：pygame get_pos() 之上叠加
    _duration = 0           # 当前曲目总秒数
    _ended_handled = False  # 结束事件防抖
    _last_flush = 0.0       # 上次进度落盘时间（节流）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_pygame = False
        self._loaded = False        # 当前曲目是否已 load 到 pygame.mixer.music
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._poll)
        self._load_state()
        self.refresh_library()

    # ---------------- 库管理 ----------------

    def music_dir(self) -> str:
        try:
            os.makedirs(MUSIC_DIR, exist_ok=True)
        except OSError:
            pass
        return MUSIC_DIR

    def refresh_library(self) -> list:
        """扫描音乐目录，返回文件名列表（按名称排序）；幂等。"""
        try:
            files = sorted(
                f for f in os.listdir(self.music_dir())
                if os.path.splitext(f)[1].lower() in AUDIO_EXTS
                and os.path.isfile(os.path.join(MUSIC_DIR, f)))
        except OSError:
            files = []
        self._library = files
        if self._current and self._current not in files:
            self._current = ""
            self._playing = False
        return list(self._library)

    def file_path(self, name: str) -> str:
        return os.path.join(MUSIC_DIR, name or "")

    def import_files(self, paths: list) -> list:
        """把用户选择的音频复制入库；返回成功导入的文件名列。"""
        ok_names = []
        for p in paths or []:
            try:
                ext = os.path.splitext(p)[1].lower()
                if ext not in AUDIO_EXTS:
                    continue
                name = os.path.basename(p)
                dest = os.path.join(MUSIC_DIR, name)
                # 同名去重：追加 (n) 后缀
                i = 1
                stem, e = os.path.splitext(name)
                while os.path.exists(dest) and os.path.abspath(dest) != os.path.abspath(p):
                    dest = os.path.join(MUSIC_DIR, f"{stem} ({i}){e}")
                    name = os.path.basename(dest)
                    i += 1
                if os.path.abspath(dest) == os.path.abspath(p):
                    continue   # 源就是库内文件
                import shutil
                shutil.copy2(p, dest)
                ok_names.append(name)
            except Exception:
                continue
        if ok_names:
            self.refresh_library()
            self._save_state()
            self.library_changed.emit()
        return ok_names

    def delete_song(self, name: str):
        """从库中删除歌曲（含正在播放时先停止）"""
        if name in self._positions:
            self._positions.pop(name, None)
        if name == self._current:
            self.stop(emit=False)
        try:
            os.remove(self.file_path(name))
        except OSError:
            pass
        self.refresh_library()
        self._save_state()
        self.library_changed.emit()

    # ---------------- 播放控制 ----------------

    def _ensure(self) -> bool:
        """惰性初始化 pygame.mixer；失败（无音频设备）返回 False"""
        if self._init_pygame:
            return True
        with _pygame_lock:
            if self._init_pygame:
                return True
            try:
                import pygame
                pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=1024)
                self._init_pygame = True
                return True
            except Exception:
                return False

    def _poll(self):
        """轮询：更新进度 tick + 检测曲目结束自动切换"""
        import pygame
        if not self._init_pygame or not self._playing:
            return
        if self._paused:
            # 暂停中 pygame.mixer.music.pause() 会让 get_busy() 返回 False，
            # 若继续判定会误把暂停当“曲目结束”→ 清进度并重播/切曲
            return
        try:
            busy = pygame.mixer.music.get_busy()
        except Exception:
            busy = False
        if busy:
            pos = int(self._base + max(0, pygame.mixer.music.get_pos()) / 1000.0)
            self.tick.emit(pos, self._duration)
            self._ended_handled = False
            # 播放途中按节流周期持久化各曲进度：关闭/强杀时仅丢最近窗口内的进度
            if self._current:
                self._positions[self._current] = pos
                now = time.monotonic()
                if now - self._last_flush >= STATE_FLUSH_S:
                    self._last_flush = now
                    self._save_state()
        else:
            if not self._ended_handled:
                self._ended_handled = True
                self._on_end()

    def play_name(self, name: str, from_saved: bool = False):
        """播放指定歌曲。

        from_saved=True 时从该曲保存的进度续播（重启自动恢复/autoplay 场景）；
        默认 False 从头播放（手动点歌/切歌/循环重播均从曲首开始）。
        """
        if name not in self._library:
            self.refresh_library()
            if name not in self._library:
                return
        if not self._ensure():
            return
        import pygame
        try:
            pygame.mixer.music.stop()
        except Exception:
            pass
        self._current = name
        self._duration = self._probe_duration(name)
        start = float(self._positions.get(name, 0) or 0) if from_saved else 0.0
        self._base = start
        try:
            pygame.mixer.music.load(self.file_path(name))
            pygame.mixer.music.set_volume(max(0.0, min(1.0, self._volume)))
            if start > 0:
                pygame.mixer.music.play(start=start)
            else:
                pygame.mixer.music.play()
            self._loaded = True
            self._playing = True
            self._paused = False
            self._ended_handled = False
            self._timer.start()
        except Exception:
            self._playing = False
            return
        self._save_state()
        self.song_changed.emit(name)
        self.state_changed.emit()
        self.tick.emit(int(self._base), self._duration)

    def toggle(self):
        """播放/暂停切换（无当前曲目时播第一首）"""
        if not self._ensure():
            return
        if not self._current:
            if not self._library:
                return
            self.play_name(self._library[0])
            return
        import pygame
        if self._playing and not self._paused:
            # 暂停：记录当前位置基准
            try:
                self._base += max(0, pygame.mixer.music.get_pos()) / 1000.0
                pygame.mixer.music.pause()
            except Exception:
                pass
            self._paused = True
            # 暂停点立即持久化：暂停后关闭 App 也能从该点恢复
            if self._current:
                self._positions[self._current] = int(self._base)
            self._save_state()
            self.state_changed.emit()
        else:
            if not self._loaded:
                # 重启后首次播放：音乐尚未 load（pygame 未 load 时 play 抛异常被
                # 静默吞掉 → 无响应）。走 play_name 加载并从保存位置续播
                self.play_name(self._current)
                return
            try:
                if self._paused:
                    pygame.mixer.music.unpause()
                else:
                    pygame.mixer.music.play(start=self._base)
                self._timer.start()
            except Exception:
                return
            self._playing = True
            self._paused = False
            self._ended_handled = False
            self.state_changed.emit()

    def stop(self, emit: bool = True):
        import pygame
        # 置空播放状态前先捕获实际当前位置（基准 + 实时偏移），供进度持久化
        pos = int(self.current_position_f()) if self._current else 0
        self._playing = False
        self._paused = False
        self._loaded = False
        try:
            if self._init_pygame:
                pygame.mixer.music.stop()
        except Exception:
            pass
        self._timer.stop()
        if self._current:
            self._positions[self._current] = pos
        self._save_state()
        if emit:
            self.state_changed.emit()

    def play_next(self):
        if not self._library:
            return
        nxt = self._pick(1)
        if nxt:
            self.play_name(nxt)

    def play_prev(self):
        if not self._library:
            return
        # 单曲循环模式：上一首 = 播放刚开始则重播，否则回退同逻辑
        nxt = self._pick(-1, prev=True)
        if nxt:
            self.play_name(nxt)

    def _pick(self, step: int, prev: bool = False) -> str:
        """按当前模式选取下一/上一首；随机模式无视 step。"""
        lib = list(self._library)
        if not lib:
            return ""
        if self._mode == MODE_RANDOM and len(lib) > 1:
            cur = lib.copy()
            if self._current in cur:
                cur.remove(self._current)
            return random.choice(cur)
        try:
            idx = lib.index(self._current)
        except ValueError:
            idx = 0
        if prev and idx == 0:
            idx = len(lib) - 1
        else:
            idx = (idx + 1) % len(lib)
        return lib[idx]

    def _on_end(self):
        """曲目自然结束：按模式跳下一首 / 重播 / 停止"""
        if self._current:
            self._positions[self._current] = 0   # 播放完毕，进度清零
        if self._mode == MODE_LOOP_ONE and self._current:
            self._positions.pop(self._current, None)
            self.play_name(self._current)
            return
        if self._mode == MODE_RANDOM:
            nxt = self._pick(1)
            if nxt:
                self.play_name(nxt)
            return
        nxt = self._pick(1)
        if nxt and nxt != self._current:
            self.play_name(nxt)
        else:
            self.stop(emit=False)
            self.state_changed.emit()

    # ---------------- 进度 / 音量 ----------------

    def seek(self, seconds: float):
        import pygame
        if not self._current or not self._ensure():
            return
        seconds = max(0, int(seconds))
        self._base = float(seconds)
        self._positions[self._current] = int(seconds)
        try:
            if self._init_pygame and self._playing:
                pygame.mixer.music.play(start=seconds)
            self._ended_handled = False
            self._timer.start()
        except Exception:
            pass
        self.tick.emit(int(self._base), self._duration)
        self._save_state()

    def set_volume(self, v: float):
        self._volume = max(0.0, min(1.0, v))
        try:
            import pygame
            if self._init_pygame:
                pygame.mixer.music.set_volume(self._volume)
        except Exception:
            pass
        self._save_state()
        self.state_changed.emit()

    def set_mode(self, mode: str):
        if mode in (MODE_SEQUENCE, MODE_RANDOM, MODE_LOOP_ONE):
            self._mode = mode
            self._save_state()
            self.state_changed.emit()

    def set_autoplay(self, on: bool):
        self._autoplay = bool(on)
        self._save_state()
        self.state_changed.emit()

    def autoplay_on_panel_open(self):
        """打开 AI 面板时自动播放：开关开启且已有歌单 → 从上次进度续播。
        直接走 play_name(from_saved=True)：确保 load 音乐（重启后首次播放），
        并自动从保存位置续播。"""
        if not self._autoplay:
            return
        if self._playing:
            return
        if not self._library:
            return
        if not self._current or self._current not in self._library:
            self.play_name(self._library[0], from_saved=True)
        else:
            self.play_name(self._current, from_saved=True)

    # ---------------- 状态查询 ----------------

    def library(self) -> list:
        return list(self._library)

    def current(self) -> str:
        return self._current

    def is_playing(self) -> bool:
        return self._playing

    def is_paused(self) -> bool:
        return self._paused

    def volume(self) -> float:
        return self._volume

    def mode(self) -> str:
        return self._mode

    def mode_label(self, mode: str = "") -> str:
        return _MODE_LABELS.get(mode or self._mode, "顺序")

    def autoplay(self) -> bool:
        return self._autoplay

    def duration(self) -> int:
        return self._duration

    def position(self) -> int:
        return int(self._base)

    def current_position(self) -> int:
        """当前实际播放位置（秒，整数）：基准 _base + pygame 实时偏移（暂停/未播时取基准）"""
        return int(self.current_position_f())

    def current_position_f(self) -> float:
        """当前实际播放位置（浮点秒）：供高精度进度/歌词填充连续驱动使用"""
        off = 0.0
        if self._init_pygame and self._playing and not self._paused:
            try:
                import pygame
                off = max(0, pygame.mixer.music.get_pos()) / 1000.0
            except Exception:
                off = 0.0
        return self._base + off

    def position_of(self, name: str) -> int:
        return int(self._positions.get(name, 0) or 0)

    def flush_now(self):
        """立即把当前实时进度写入状态（正常退出前调用；强杀依赖途中节流落盘）"""
        try:
            if self._current and self._playing and not self._paused:
                self._positions[self._current] = int(self.current_position_f())
            self._save_state()
        except Exception:
            pass

    # ---------------- 持久化 ----------------

    def _probe_duration(self, name: str) -> int:
        """探测音频总时长（pygame 无直接接口，尝试常见方式，失败返回 0）"""
        try:
            import pygame
            snd = pygame.mixer.Sound(self.file_path(name))
            return int(snd.get_length())
        except Exception:
            return 0

    def _load_state(self):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                st = json.load(f)
            self._current = str(st.get("current") or "")
            self._volume = float(st.get("volume", 1.0))
            self._mode = st.get("mode", MODE_SEQUENCE) \
                if st.get("mode") in (MODE_SEQUENCE, MODE_RANDOM, MODE_LOOP_ONE) else MODE_SEQUENCE
            self._autoplay = bool(st.get("autoplay", False))
            pos = st.get("positions")
            if isinstance(pos, dict):
                self._positions = {str(k): int(v or 0) for k, v in pos.items()}
            self._base = float(self._positions.get(self._current, 0) or 0)
        except Exception:
            self._current = ""
            self._volume = 1.0
            self._mode = MODE_SEQUENCE
            self._autoplay = False
            self._positions = {}

    def _save_state(self):
        try:
            os.makedirs(MUSIC_DIR, exist_ok=True)
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "current": self._current,
                    "volume": self._volume,
                    "mode": self._mode,
                    "autoplay": self._autoplay,
                    "positions": self._positions,
                }, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


# 全局单例（模块级共享：设置页 / AI 面板 / 自动播放共用）
_player = None
_player_lock = threading.Lock()


def get_player() -> MusicPlayer:
    """获取全局单例播放器（线程安全），首次创建时挂退出兜底保存"""
    global _player
    if _player is None:
        with _player_lock:
            if _player is None:
                _player = MusicPlayer()
                # 正常退出（关闭窗口）前把当前实时进度落盘，重启后从该点续播
                try:
                    from PyQt6.QtWidgets import QApplication
                    app = QApplication.instance()
                    if app is not None:
                        app.aboutToQuit.connect(_player.flush_now)
                except Exception:
                    pass
    return _player


def _tmp_path() -> str:
    return tempfile.gettempdir()