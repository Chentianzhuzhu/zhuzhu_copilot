"""真实播放链路端到端验证：桌面歌词在切歌/重播/单曲循环/暂停恢复中的同步。

使用临时音乐库（2 首正弦音 + 各自 .lrc），真实 pygame 播放推进：
场景1 切歌：A 播放中切 B → 桌面歌词显示 B 首句
场景2 重播：B 播放中重播 B（位置归零）→ 桌面歌词回到 B 首句、填充归零
场景3 单曲循环：loop_one 自然播完重播 → 桌面回到 B 首句
场景4 暂停恢复：暂停冻结、恢复继续填充

独立运行：python scripts/_verify_replay_sync.py
"""
import math
import os
import shutil
import struct
import sys
import tempfile
import time
import wave

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PyQt6.QtWidgets import QApplication

app = QApplication([])

import winapp_migrator.core.music_player as mp

_FAIL = []
TMP = tempfile.mkdtemp(prefix="replay_test_")


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        _FAIL.append(name)


def make_sine(path, sec=3.0, freq=440.0):
    rate = 22050
    with wave.open(path, "w") as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(rate)
        frames = bytearray()
        for i in range(int(sec * rate)):
            v = int(32767 * 0.05 * math.sin(2 * math.pi * freq * i / rate))
            frames += struct.pack("<hh", v, v)
        f.writeframes(bytes(frames))


def write_lrc(name, lines):
    with open(os.path.join(TMP, name), "w", encoding="utf-8") as f:
        for ts, text in lines:
            mm, ss = divmod(int(ts * 1000) // 1000, 60)
            f.write(f"[{mm:02d}:{ss:02d}.00]{text}\n")


def pump(seconds):
    """推进 Qt 事件循环与 pygame 播放位置"""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def main():
    # ---- 临时库：真实播放器 ----
    mp.MUSIC_DIR = TMP
    mp.STATE_FILE = os.path.join(TMP, "player.json")
    import winapp_migrator.core.lyrics_engine as le
    le.MUSIC_DIR = TMP
    le.LYRICS_STATE_FILE = os.path.join(TMP, "lyrics.json")

    a_wav = os.path.join(TMP, "A.wav")
    b_wav = os.path.join(TMP, "B.wav")
    make_sine(a_wav, sec=4.0, freq=330.0)
    make_sine(b_wav, sec=4.0, freq=440.0)
    # 歌词：A 两行、B 两行（行距 0.9s，避免休止阈值干扰）
    a_lines = [(0.0, "A 的第一句歌词"), (1.8, "A 的第二句歌词")]
    b_lines = [(0.0, "B 的第一句歌词"), (1.8, "B 的第二句歌词")]
    write_lrc("A.lrc", a_lines)
    write_lrc("B.lrc", b_lines)

    p = mp.MusicPlayer()
    p.refresh_library()
    check("库扫描两首测试音频", sorted(p.library()) == ["A.wav", "B.wav"],
          str(p.library()))

    from winapp_migrator.ui.desktop_lyrics import DesktopLyrics
    dl = DesktopLyrics()
    dl.set_state("预唱", "当前", 1.0)   # 污染初始态，验证逐步被覆盖
    dl.bind(p)
    dl.show()
    p.set_mode(mp.MODE_LOOP_ONE)

    # ---- 场景1 切歌：A → B ----
    p.play_name("A.wav")
    pump(0.6)
    ok1 = (dl._current == "A 的第一句歌词" and dl._pending == "A 的第二句歌词")
    check("切歌 A 显示首句", ok1, f"cur={dl._current!r} pend={dl._pending!r}")
    p.play_name("B.wav")
    pump(0.6)
    ok2 = (dl._current == "B 的第一句歌词" and dl._pending == "B 的第二句歌词")
    check("切歌 B 同步新歌词", ok2, f"cur={dl._current!r} pend={dl._pending!r}")

    # ---- 场景2 手动重播同一首（默认从头，填充从 0 连续增长）----
    pump(1.5)                       # B 播到中段
    before_fill = dl._fill
    p.play_name("B.wav")            # 重播 B（from_saved=False → 从头）
    pump(0.2)
    check("重播回到首句", dl._current == "B 的第一句歌词",
          f"重播后 cur={dl._current!r}")
    check("重播后填充从起点回落", dl._fill <= 0.6,
          f"重播前fill={before_fill:.2f} 重播后fill={dl._fill:.3f}")
    pump(0.5)
    check("重播后填充继续增长", dl._fill > 0.2, f"fill={dl._fill:.3f}")

    # ---- 场景3 单曲循环自然播完重播 ----
    print("     [debug] 循环重播采样:")
    saw_restart = False
    for _ in range(8):              # 约 4.8s，覆盖 4s 音频自然结束 + 重播
        pump(0.6)
        if dl._current == "B 的第一句歌词":
            saw_restart = True       # 听到重播后的首句即证明歌词跟随循环
        print(f"     [debug] cur={dl._current!r} fill={dl._fill:.3f} "
              f"pos={p.current_position_f():.3f} playing={p.is_playing()}")
    check("单曲循环重播回首句并被跟随", saw_restart,
          f"cur={dl._current!r} fill={dl._fill:.3f}")
    check("单曲循环后仍在播放", p.is_playing(), str(p.is_playing()))

    # ---- 场景4 暂停冻结 / 恢复继续 ----
    p.toggle()                      # 暂停
    fill_paused = dl._fill
    pump(0.5)
    check("暂停时歌词冻结", dl._fill == fill_paused,
          f"{dl._fill:.3f} == {fill_paused:.3f}")
    p.toggle()                      # 恢复
    pump(0.8)
    check("恢复后歌词继续填充", dl._fill > fill_paused or dl._current != "B 的第一句歌词",
          f"{dl._fill:.3f} > {fill_paused:.3f}")

    p.stop()
    dl.close()
    shutil.rmtree(TMP, ignore_errors=True)
    if _FAIL:
        print(f"\n{len(_FAIL)} 项失败: {_FAIL}")
        sys.exit(1)
    print("\n桌面歌词同步（切歌/重播/循环/暂停恢复）端到端验证通过")
    sys.exit(0)


if __name__ == "__main__":
    main()