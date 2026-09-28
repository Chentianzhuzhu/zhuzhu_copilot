"""播放进度持久化端到端验证：播放途中节流落盘 / 正常退出 flush / 强杀兜底 / 重启恢复续播。

独立运行：python scripts/_verify_progress_persist.py
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

import zhuzhu_Copilot.core.music_player as mp

_FAIL = []
TMP = tempfile.mkdtemp(prefix="progress_test_")


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        _FAIL.append(name)


def make_sine(path, sec=20.0, freq=440.0):
    rate = 22050
    with wave.open(path, "w") as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(rate)
        frames = bytearray()
        for i in range(int(sec * rate)):
            v = int(32767 * 0.03 * math.sin(2 * math.pi * freq * i / rate))
            frames += struct.pack("<hh", v, v)
        f.writeframes(bytes(frames))


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def main():
    mp.MUSIC_DIR = TMP
    mp.STATE_FILE = os.path.join(TMP, "player.json")

    make_sine(os.path.join(TMP, "A.wav"), sec=20.0, freq=330.0)
    p = mp.MusicPlayer()
    p.refresh_library()
    p.play_name("A.wav")
    pump(7.0)                       # 播放 7s，> 节流窗口(5s) → 途中至少落盘一次

    saved_during = p.position_of("A.wav")
    now_pos = p.current_position()
    check("途中节流已落盘", saved_during >= 5.0 and saved_during <= 7.0,
          f"落盘={saved_during} 实时={now_pos}")
    check("强杀丢失 ≤ 节流窗口", now_pos - saved_during <= mp.STATE_FLUSH_S,
          f"丢失 {now_pos - saved_during}s")

    # ---- 模拟正常退出：flush_now() 精确落盘 ----
    p.flush_now()
    saved_exit = p.position_of("A.wav")
    check("正常退出 flush 精确落盘", abs(saved_exit - now_pos) <= 1,
          f"保存={saved_exit} 实时={now_pos}")

    # ---- 重启恢复：新实例读盘 → autoplay 从保存点续播 ----
    p.stop(emit=False)
    p2 = mp.MusicPlayer()           # 模拟重启（新实例读取 player.json）
    check("重启保留进度", p2.position_of("A.wav") == saved_exit,
          f"{p2.position_of('A.wav')} vs {saved_exit}")
    p2.set_autoplay(True)
    p2.autoplay_on_panel_open()
    pump(1.0)
    resumed = p2.current_position_f()
    check("自动续播从保存点开始", abs(resumed - saved_exit) <= 2.5,
          f"续播位置={resumed:.2f} 保存点={saved_exit}")

    # 暂停点立即持久化：暂停后不 flush 也应保留暂停时刻进度
    p2.toggle()                     # 暂停（内部立即落盘）
    pause_pos = p2.position_of("A.wav")
    p3 = mp.MusicPlayer()           # 再次重启验证暂停点
    check("暂停点已持久化", p3.position_of("A.wav") == pause_pos,
          f"{p3.position_of('A.wav')} vs {pause_pos}")

    p2.stop()
    shutil.rmtree(TMP, ignore_errors=True)
    if _FAIL:
        print(f"\n{len(_FAIL)} 项失败: {_FAIL}")
        sys.exit(1)
    print("\n播放进度持久化（途中/退出/强杀兜底/重启续播）验证通过")
    sys.exit(0)


if __name__ == "__main__":
    main()