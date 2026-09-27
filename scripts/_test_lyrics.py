"""歌词引擎单元测试（独立运行：python scripts/_test_lyrics.py）

覆盖：LRC 解析（标准/多标签/offset/增强标签/排序/坏行）、定位与填充进度、
同目录自动匹配、手动导入与绑定持久化。使用临时目录避免污染真实音乐库。
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PyQt6.QtCore import QObject, pyqtSignal

from winapp_migrator.core import lyrics_engine as le

_TMP = tempfile.mkdtemp(prefix="lyrics_test_")
le.MUSIC_DIR = _TMP
le.LYRICS_STATE_FILE = os.path.join(_TMP, "lyrics.json")

FAIL = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS {name}")
    else:
        FAIL.append(name)
        print(f"  FAIL {name}  {detail}")


class FakePlayer(QObject):
    song_changed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._cur = ""

    def current(self):
        return self._cur


def test_parse():
    print("[parse_lrc]")
    lrc = """[ti:测试]
[ar:歌手]
[offset:+500]
[00:01.00]第一句
[00:02.50][00:05.00]一句复用
[00:03.00]<00:03.20>增强<00:03.80>标签句
bad line without tag
[01:02.34]中后段
"""
    lines = le.parse_lrc(lrc)
    check("不止元信息被忽略", all(t > 0 for t, _ in lines))
    check("坏行被忽略", len(lines) == 5, f"got {len(lines)}")
    times = [t for t, _ in lines]
    check("升序", times == sorted(times), str(times))
    check("offset 全局 +500ms", lines[0][0] == 1500, str(lines[0]))
    check("一句多标签展开", (3000, "一句复用") in lines and (5500, "一句复用") in lines,
          str(lines))
    check("增强标签剥离", (3500, "增强标签句") in lines, str(lines))
    check("百分秒换算 0.34s→340ms 且含 offset", (62840, "中后段") in lines, str(lines))
    check("同时间合并为一行", all(lines[i][0] != lines[i - 1][0] for i in range(1, len(lines))))


def test_locate():
    print("[locate 普通行（行距 < 休止阈值，填充线性）]")
    eng = le.LyricsEngine()
    eng._lines = [(0, "零秒"), (900, "一秒"), (1800, "二秒")]  # 行距 0.9s < 2 拍(1s)
    idx, fill = eng.locate(0)
    check("起点命中首行", idx == 0 and fill == 0.0, f"{idx},{fill}")
    idx, fill = eng.locate(0.45)
    check("行中填充 50%", idx == 0 and abs(fill - 0.5) < 1e-6, f"{idx},{fill}")
    idx, fill = eng.locate(1.35)
    check("下一行中段", idx == 1 and abs(fill - 0.5) < 1e-6, f"{idx},{fill}")
    eng._lines = [(0, "零秒"), (900, "一秒"), (1800, "二秒")]
    idx, fill = eng.locate(3.0)
    check("末行兜底时长内插值", idx == 2 and abs(fill - (3.0 - 1.8) / 5.0) < 1e-6,
          f"{idx},{fill}")
    idx, fill = eng.locate(-1)
    check("负位置回退首行", idx == 0 and fill == 0.0, f"{idx},{fill}")
    eng._lines = []
    idx, fill = eng.locate(1.0)
    check("无歌词返回 -1", idx == -1 and fill == 0.0)
    idx, fill = eng.locate("abc")
    check("非法位置容错", idx == -1 and fill == 0.0)


def test_hold_and_speed():
    print("[休止识别 + 变速换算 + 逐字插值 + 元数据]")
    eng = le.LyricsEngine()
    eng._meta = {"bpm": 60.0}      # 拍长 1000ms，hold = 2s
    eng._lines = [(0, "第一句"), (6000, "第二句"), (9000, "第三句")]
    # 休止：6s 间隙 ≥ 2s → span=3s（前段填充，后段保持满格）
    idx, fill = eng.locate(1.5)
    check("休止间隙前半填充", idx == 0 and abs(fill - 0.5) < 1e-6, f"{idx},{fill}")
    idx, fill = eng.locate(4.0)
    check("休止后半保持满格", idx == 0 and fill == 1.0, f"{idx},{fill}")
    idx, fill = eng.locate(5.999)
    check("休止内不提前切行", idx == 0 and fill == 1.0, f"{idx},{fill}")
    idx, fill = eng.locate(6.0)
    check("休止结束正常切行", idx == 1 and fill == 0.0, f"{idx},{fill}")
    # 普通行距（1.5s < 2s）不判休止
    idx, fill = eng.locate(6.75)
    check("普通行距线性填充", idx == 1 and abs(fill - 0.5) < 1e-6, f"{idx},{fill}")

    # 变速换算：1.2x 时墙上 1.2s = 内容 1s
    eng.set_speed(1.2)
    check("变速系数生效", abs(eng.speed() - 1.2) < 1e-9)
    check("墙钟→内容换算", abs(eng.wall_to_content(1.2) - 1.0) < 1e-9)
    check("换算后定位一致", eng.locate(eng.wall_to_content(1.2 * 1.5))[0] ==
          eng.locate(1.5)[0])
    eng.set_speed(0.0)
    check("非法速度钳制下限", eng.speed() >= 0.1)

    # 逐字插值：6 字行在前段 3s 内等分
    eng._lines = [(0, "甲乙丙丁"), (6000, "戊")]
    ts = eng.word_times(0)
    check("逐字数量 = 字数", len(ts) == 4 and ts[0][0] == 0, str(ts))
    check("逐字按有效跨度等分", ts[-1][0] == 2250, f"end={ts[-1][0]}")  # span=3000, seg=750
    check("逐字文本正确", "".join(ch for _, ch in ts) == "甲乙丙丁")
    check("越界行返回空", eng.word_times(9) == [])

    # 元数据解析
    m = le.parse_meta("[bpm:70]\n[beat:6/8]\n[key:G]\n[ar:陈粒]")
    check("bpm 解析为浮点", m.get("bpm") == 70.0, str(m))
    check("拍号与调式解析", m.get("beat") == "6/8" and m.get("key") == "G", str(m))
    check("忽略非节奏标签", "ar" not in m)
    check("无元数据返回空", le.parse_meta("[ti:x]") == {})
    beat = le.LyricsEngine().beat_ms()
    check("无 bpm 用默认拍长", abs(beat - le.DEFAULT_BEAT_MS) < 1e-9)


def test_match_and_import():
    print("[同目录匹配 + 手动导入绑定]")
    song = "demo.mp3"
    with open(os.path.join(_TMP, "demo.lrc"), "w", encoding="utf-8") as f:
        f.write("[00:00.00]自动匹配歌词\n[00:05.00]第二行\n")
    eng = le.LyricsEngine()
    eng._load_bindings.__self__._bindings = {}
    eng.load(song)
    check("同目录同名 .lrc 自动匹配", eng.have_lyrics() and eng.lines()[0] == "自动匹配歌词",
          str(eng.lines()))

    # 手动导入：源文件为带时间戳的 lrc，应复制入库并绑定
    src = os.path.join(_TMP, "external", "manual.lrc")
    os.makedirs(os.path.dirname(src), exist_ok=True)
    with open(src, "w", encoding="utf-8") as f:
        f.write("[00:01.00]手动导入歌词\n[00:06.00]另一行\n")
    ok = eng.import_lyric("other.mp3", src)
    check("导入成功", ok)
    check("复制入库", os.path.isfile(os.path.join(_TMP, "other.lrc")))
    eng.load("other.mp3")
    check("绑定生效", eng.have_lyrics() and eng.lines()[0] == "手动导入歌词",
          str(eng.lines()))

    # 绑定持久化（模拟重启：重建引擎）
    eng2 = le.LyricsEngine()
    eng2.load("other.mp3")
    check("绑定跨重启持久化", eng2.have_lyrics() and eng2.lines()[0] == "手动导入歌词",
          str(eng2.lines()))

    # 删除歌曲清理绑定：绑定记录移除；同名歌词文件仍存在（供未来重传音乐时复用）
    eng2.forget("other.mp3")
    eng3 = le.LyricsEngine()
    eng3.load("other.mp3")
    check("删除后绑定清理", "other.mp3" not in eng3._bindings, str(eng3._bindings))
    check("同名歌词文件仍可匹配", eng3.have_lyrics())


def test_import_invalid():
    print("[无效导入拒绝]")
    eng = le.LyricsEngine()
    src = os.path.join(_TMP, "bad.txt")
    with open(src, "w", encoding="utf-8") as f:
        f.write("no timestamp here")
    check("非 lrc 后缀拒绝", not eng.import_lyric("x.mp3", src))
    bug = os.path.join(_TMP, "bad.lrc")
    with open(bug, "w", encoding="utf-8") as f:
        f.write("[ti:no-body]")
    check("无有效歌词行拒绝", not eng.import_lyric("x.mp3", bug))


def main():
    test_parse()
    test_locate()
    test_hold_and_speed()
    test_match_and_import()
    test_import_invalid()
    shutil.rmtree(_TMP, ignore_errors=True)
    if FAIL:
        print(f"\n{len(FAIL)} 项失败: {FAIL}")
        sys.exit(1)
    print("\n全部歌词引擎测试通过")


if __name__ == "__main__":
    main()