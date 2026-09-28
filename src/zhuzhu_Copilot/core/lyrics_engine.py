"""歌词引擎：LRC 解析 / 节奏元数据 / 变速换算 / 休止识别 / 逐字插值 / 定位。

- 歌词文件约定：与歌曲同名同目录的 .lrc（如 歌曲.mp3 → 歌曲.lrc）
- 支持的节奏元数据：[bpm:70] [beat:6/8] [key:G]，供变速播放与休止识别
- 变速：播放器报告的位置为内容时间时歌词天然跟随任意变速；对报告墙钟时间的
  播放器，用 set_speed() + wall_to_content() 换算后再定位
- 停顿：行间间隙 ≥ HOLD_GAP_BEATS 拍时视为休止，填充在前 FILL_HOLD_RATIO 段内
  完成、剩余段保持满格（不拖慢字迹）
- 逐字：word_times() 按 BPM 等分插值生成逐字时间轴（不固化进文件）
- 纯逻辑层（仅依赖 PyQt 信号，不依赖 Widget），便于单元测试
"""
import json
import os
import re
import threading

from PyQt6.QtCore import QObject, pyqtSignal

from zhuzhu_Copilot.core.music_player import MUSIC_DIR

LYRICS_STATE_FILE = os.path.join(MUSIC_DIR, "lyrics.json")

# 末行没有下一行时间戳作上限时，单行默认时长（毫秒，模块常量可扩展调整）
DEFAULT_LINE_SPAN_MS = 5000
# 无 [bpm] 元数据时的默认拍长（毫秒）
DEFAULT_BEAT_MS = 500
# 行间间隙 ≥ N 拍判定为休止（停顿）
HOLD_GAP_BEATS = 2
# 休止间隙中演唱段占比：填充在该段内完成，其余保持满格
FILL_HOLD_RATIO = 0.5

# 标准 LRC 时间标签：[mm:ss] / [mm:ss.xx] / [mm:ss:xx]（支持多标签一行的行歌词）
_TIME_TAG = re.compile(r"\[(\d{1,2}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
# 全局时间偏移标签：[offset:±毫秒]
_OFFSET_TAG = re.compile(r"^\[offset\s*:\s*([+-]?\d+)\s*\]")
# 增强型 LRC 逐字时间戳 <mm:ss.xx>：解析时剥离，保留文本（预留逐字扩展）
_WORD_TAG = re.compile(r"<\d{1,2}:\d{1,2}(?:[.:]\d{1,3})?>")
# 节奏元数据标签：[bpm:num] [beat:6/8] [key:G]（MULTILINE 支持文件中部行首）
_META_PATTERNS = {
    "bpm": re.compile(r"^\[bpm\s*:\s*([0-9.]+)\]", re.IGNORECASE | re.MULTILINE),
    "beat": re.compile(r"^\[beat\s*:\s*(\S+)\]", re.IGNORECASE | re.MULTILINE),
    "key": re.compile(r"^\[key\s*:\s*(\S+)\]", re.IGNORECASE | re.MULTILINE),
}


def parse_meta(text: str) -> dict:
    """提取 LRC 节奏元数据：bpm(浮点) / beat / key；无则返回空字典"""
    meta = {}
    for name, rx in _META_PATTERNS.items():
        m = rx.search(text or "")
        if not m:
            continue
        v = m.group(1)
        if name == "bpm":
            try:
                v = float(v)
            except ValueError:
                continue
            if v <= 0:
                continue
        meta[name] = v
    return meta


def parse_lrc(text: str) -> list:
    """解析 LRC 文本 → [(起始毫秒, 歌词文本)]，按时间升序。

    忽略无时间标签的行与空行；支持 [offset:±ms] 全局偏移与一句多标签。
    """
    offset = 0
    raw = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _OFFSET_TAG.match(line)
        if m:
            offset = int(m.group(1))
            continue
        tags = list(_TIME_TAG.finditer(line))
        if not tags:
            continue
        body = _WORD_TAG.sub("", _TIME_TAG.sub("", line)).strip()
        if not body:
            continue
        for t in tags:
            mm, ss, frac = int(t.group(1)), int(t.group(2)), t.group(3)
            sec = mm * 60 + ss
            if frac is None:
                ms = 0
            elif len(frac) == 3:
                ms = int(frac)          # [mm:ss.xxx] 已是毫秒
            elif len(frac) == 2:
                ms = int(frac) * 10     # 百分秒 → 毫秒
            else:
                ms = int(frac) * 100    # 十分秒 → 毫秒
            raw.append((sec * 1000 + ms, body))
    raw.sort(key=lambda it: it[0])
    # 同一时间的多行歌词合并为一行（join 保留信息，便于整行填充）
    merged = []
    for ts, body in raw:
        if merged and merged[-1][0] == ts:
            merged[-1] = (ts, f"{merged[-1][1]} {body}")
        else:
            merged.append([ts, body])
    result = []
    for ts, body in merged:
        t = ts + offset
        if t >= 0:
            result.append((t, body))
    return result


class LyricsEngine(QObject):
    """全局单例歌词引擎：监听歌曲切换自动加载，提供行定位与填充进度。

    通过信号 lyrics_changed 通知 UI 刷新（空歌词时 lines() 为空即无歌词）。
    """

    lyrics_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lines = []          # [(起始毫秒, 文本)]，升序
        self._song = ""           # 已加载歌词对应的歌曲文件名
        self._bindings = {}       # 歌曲文件名 -> 已绑定的歌词文件名
        self._meta = {}           # 节奏元数据：bpm / beat / key
        self._speed = 1.0         # 变速系数（>0）
        self._lock = threading.Lock()
        self._load_bindings()
        self._player = None

    # ---------------- 与播放器联动 ----------------

    def bind(self, player) -> "LyricsEngine":
        """绑定播放器：切歌时自动重载歌词；返回自身便于链式调用。"""
        if player is None or player is self._player:
            return self
        self._player = player
        player.song_changed.connect(self.load)
        self.load(player.current())
        return self

    def load(self, song_name: str):
        """加载指定歌曲的歌词：绑定文件优先，其次同目录同名 .lrc。"""
        changed = False
        with self._lock:
            name = song_name or ""
            if name == self._song and (self._lines or not self._lyric_path(name)):
                return
            self._song = name
            self._lines = []
            self._meta = {}
            changed = True
            if name:
                path = self._lyric_path(name)
                if path and os.path.isfile(path):
                    try:
                        with open(path, "r", encoding="utf-8", errors="replace") as f:
                            content = f.read()
                        self._meta = parse_meta(content)
                        self._lines = parse_lrc(content)
                    except OSError:
                        self._lines = []
                    if not self._lines:
                        self._forget_binding(name)
        if changed:
            self.lyrics_changed.emit()

    # ---------------- 歌词文件定位 ----------------

    def _lyric_path(self, song_name: str) -> str:
        """返回歌曲的歌词文件路径：绑定优先，其次同目录同名 .lrc。"""
        bound = self._bindings.get(song_name)
        if bound:
            p = os.path.join(MUSIC_DIR, bound) if not os.path.isabs(bound) else bound
            if os.path.isfile(p):
                return p
            self._forget_binding(song_name)
        stem, _ = os.path.splitext(song_name)
        p = os.path.join(MUSIC_DIR, f"{stem}.lrc")
        return p if os.path.isfile(p) else ""

    def import_lyric(self, song_name: str, src_path: str) -> bool:
        """导入歌词文件：复制进音乐库并绑定到歌曲；成功返回 True。"""
        if not song_name or not src_path:
            return False
        if os.path.splitext(src_path)[1].lower() != ".lrc":
            return False
        try:
            with open(src_path, "r", encoding="utf-8", errors="replace") as f:
                parsed = parse_lrc(f.read())
            if not parsed:
                return False
        except OSError:
            return False
        stem, _ = os.path.splitext(os.path.basename(song_name))
        dest = os.path.join(MUSIC_DIR, f"{stem}.lrc")
        i = 1
        while os.path.exists(dest) and os.path.abspath(dest) != os.path.abspath(src_path):
            dest = os.path.join(MUSIC_DIR, f"{stem} ({i}).lrc")
            i += 1
        try:
            if os.path.abspath(dest) != os.path.abspath(src_path):
                import shutil
                shutil.copy2(src_path, dest)
        except OSError:
            return False
        with self._lock:
            self._bindings[song_name] = os.path.basename(dest)
            self._save_bindings()
        self.load(song_name)
        return True

    def forget(self, song_name: str):
        """删除歌曲时清理其歌词绑定；保留磁盘歌词文件。"""
        with self._lock:
            if self._bindings.pop(song_name, None):
                self._save_bindings()

    def _forget_binding(self, song_name: str):
        """移除无效绑定并持久化（调用方需持 _lock）"""
        if self._bindings.pop(song_name, None):
            self._save_bindings()

    # ---------------- 变速 / 定位 / 查询 ----------------

    def set_speed(self, speed: float):
        """设置变速系数（>0，默认 1.0）。用于把播放器墙钟时间换算为内容时间"""
        self._speed = max(0.1, float(speed))

    def speed(self) -> float:
        return self._speed

    def wall_to_content(self, wall_sec) -> float:
        """墙钟时间（秒）→ 内容时间（秒）：变速 1.2x 时墙上 1.2s ≈ 内容 1s"""
        return wall_sec / self._speed

    def meta(self) -> dict:
        return dict(self._meta)

    def beat_ms(self) -> float:
        """当前歌曲单拍时长（毫秒）；无 [bpm] 时用默认拍长"""
        bpm = self._meta.get("bpm")
        return (60000.0 / bpm) if bpm else float(DEFAULT_BEAT_MS)

    def _fill_span_ms(self, idx: int) -> float:
        """第 idx 行的有效填充时长（毫秒）：行间间隙达休止阈值时只在前段完成填充"""
        lines = self._lines
        if idx >= len(lines) - 1:
            return float(DEFAULT_LINE_SPAN_MS)
        gap = lines[idx + 1][0] - lines[idx][0]
        hold = HOLD_GAP_BEATS * self.beat_ms()
        if gap >= hold:
            return max(hold / HOLD_GAP_BEATS, gap * FILL_HOLD_RATIO)
        return gap

    def locate(self, pos_sec) -> tuple:
        """当前播放位置（内容时间秒）→ (当前行序号, 行内填充进度 0..1)；无歌词返回 (-1, 0.0)。

        行间休止（间隙 ≥ HOLD_GAP_BEATS 拍）时填充提前完成并保持满格，下一句才切行。
        """
        lines = self._lines
        if not lines:
            return (-1, 0.0)
        try:
            pos_ms = int(float(pos_sec) * 1000)
        except (TypeError, ValueError):
            return (-1, 0.0)
        idx = 0
        for i, (ts, _) in enumerate(lines):
            if pos_ms < ts:
                break
            idx = i
        span = max(1, self._fill_span_ms(idx))
        fill = min(1.0, max(0.0, (pos_ms - lines[idx][0]) / float(span)))
        return (idx, fill)

    def word_times(self, idx: int) -> list:
        """第 idx 行逐字时间轴：按有效填充时长对每个字符等分插值（供逐字 KTV）。

        返回 [(起始毫秒, 字符)]。变速/休止与 locate 同源，避免固化不精确的逐字时间。
        """
        if not (0 <= idx < len(self._lines)):
            return []
        ts, text = self._lines[idx]
        if not text:
            return []
        span = max(1, self._fill_span_ms(idx))
        seg = span / len(text)
        return [(ts + int(k * seg), ch) for k, ch in enumerate(text)]

    def lines(self) -> list:
        return [text for _, text in self._lines]

    def current_song(self) -> str:
        return self._song

    def have_lyrics(self) -> bool:
        return bool(self._lines)

    # ---------------- 持久化（绑定关系） ----------------

    def _load_bindings(self):
        try:
            with open(LYRICS_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data.get("bindings"), dict):
                self._bindings = {str(k): str(v) for k, v in data["bindings"].items()}
        except (OSError, ValueError):
            self._bindings = {}

    def _save_bindings(self):
        try:
            os.makedirs(MUSIC_DIR, exist_ok=True)
            with open(LYRICS_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump({"bindings": self._bindings}, f,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass


# 全局单例（歌词属于 MusicPlayer 的伴随状态，同样全局共享）
_engine = None
_engine_lock = threading.Lock()


def get_lyrics_engine() -> LyricsEngine:
    """获取全局单例歌词引擎（线程安全）"""
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = LyricsEngine()
    return _engine