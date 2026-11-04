---
name: piano-studio
description: 用钢琴为用户的请求演奏旋律，支持采样级真实钢琴音色、按 BPM/拍精确排程的完整曲谱、多轨音色、踏板、和弦与休止符。用户说「弹钢琴 / 弹一首 / 演奏 / 来点音乐 / 背景音乐 / 用钢琴弹…」时使用。
---

# Piano Studio：让 AI 真正弹出声音

本插件把演奏事件推送到局域网网页，由浏览器用 Web Audio 实时合成/采样播放。**你自己不会发声，也不会生成音频文件**；你负责写曲谱、调用 MCP，用户负责在网页里点一次「开始聆听」。

## 两类演奏工具

| 工具 | 适用 |
| --- | --- |
| `piano_play` | 短句、即兴旋律、试音：用 `C4@400 D4@400` 这种紧凑记谱 |
| `piano_play_score` | **完整歌曲**：用 `tempo` + `beat/dur` 精确控制每个音的起止，支持多轨音色与踏板；先 `dry_run=true` 校验再演奏 |

完整歌曲一律优先用 `piano_play_score`：它把整份曲谱一次性交给浏览器，由音频时钟排程，网络抖动不会改变节奏。

## 标准流程

1. 调 `piano_status` 取收听地址；HTTP 未启动时先 `piano_server(action="start")`。
2. 把地址（优先局域网地址）告诉用户，请用户打开并点「开始聆听」。
   - 页面会先渲染钢琴采样（约几秒），按钮变亮后才能点；这是正常流程。
   - 浏览器策略要求一次用户点击才能出声，不是插件故障。
3. 完整歌曲：按下面的曲谱格式写 `score`，先调
   `piano_play_score(score=..., dry_run=true)` 校验；根据返回的摘要/报错修正，再正式演奏。
4. 演奏中要停：`piano_stop`；看进度/连接数：`piano_status`。

如果 `piano_play` / `piano_play_score` 返回「还没有页面开启声音」，把地址再发给用户，等用户开启后重新调用即可。

## 曲谱格式（piano_play_score）

```json
{
  "title": "曲名",
  "tempo": 72,
  "time_signature": [4, 4],
  "pedal": true,
  "octave": "mid",
  "tracks": [
    {
      "voice": "grand_piano",
      "notes": [
        {"beat": 0,   "dur": 1,   "note": "C4",        "velocity": 0.75},
        {"beat": 1,   "dur": 0.5, "note": "E4+G4",     "velocity": 0.7},
        {"beat": 1.5, "dur": 0.5, "note": "D4"},
        {"beat": 4,   "dur": 2,   "note": "C5"}
      ]
    },
    {
      "voice": "pad",
      "notes": [
        {"beat": 0, "dur": 8, "note": "C3", "velocity": 0.35}
      ]
    }
  ]
}
```

字段：

- `tempo`：BPM（20-240）。`beat` 从曲首开始计拍，可小数；`dur` 是持续拍数。
- `note`：绝对音高，支持 `C4`、`F#3`、`Bb4`、和弦 `C4+E4+G4`、休止 `R`；不写八度时用 `octave` 音区。
- 也支持绝对毫秒：用 `at_ms` 代替 `beat`、`dur_ms` 代替 `dur`。中途变速的曲子用毫秒更直接。
- `velocity`：0.05-1.0；不写用 score 级 `velocity` 或插件默认。
- `pedal`：true 时音符按钢琴踏板持续到自然衰减，不按 `dur` 截止；false 时按 `dur` 收尾。
- `tracks`：1-4 轨，每轨可指定不同 `voice`（如钢琴旋律 + `pad` 铺底 + `strings` 弦乐）。
- **曲谱音符里不要再写 `@ms` / `:ms` / `!v`**，间隔和力度必须用 `beat`、`dur`、`velocity` 字段，否则会被拒绝。
- 单次最多 2048 个音、30 分钟；超过就拆成多段，等上一段结束再调下一段（再次调用会打断当前演奏）。

## 音区与音色

- `octave`：`low`（低音区）、`mid`（中音区，默认）、`high`（高音区），也可传 0-8 的八度数字；只影响没写八度的音符。
- 采样钢琴（`grand_piano`）由 6 个锚点 × 3 层力度离线渲染，带非谐性泛音、三弦拍音、锤击噪声、混响与踏板；其他音色仍是数据化合成。
- 调 `piano_voices` 拿真实 id，**不要编造音色 id**。常用：`grand_piano`、`electric_piano`、`music_box`、`organ`、`strings`、`nylon_guitar`、`marimba`、`synth_lead`、`pad`、`celesta`。

## 紧凑记谱（piano_play）

| 写法 | 含义 |
| --- | --- |
| `C4` | 绝对音高（中央 C） |
| `C` | 不写八度时用 `octave` 指定音区 |
| `F#3` / `Bb4` / `Db5` | 升降号；也支持 `♯` `♭` |
| `60` | 直接写 MIDI 音高（A0=21，C8=108） |
| `C4+E4+G4` | 和弦（`+` 连接，同时发声） |
| `C4@500` | 这个音到下一个音的间隔 500ms |
| `C4:420` | 发声长度 420ms（不写则约为间隔的 88%） |
| `C4!0.7` | 力度 0.05-1.0 |
| `R@400` 或 `-@400` | 休止 400ms |

示例：`"C4@400 E4@400 G4@400 C5@800"`、`"C4+E4+G4@1200:1100!0.7"`。

## 版权边界

- 不要凭记忆编造或复述受版权保护歌曲的完整旋律/歌词。用户要求某首商业歌曲（如《甲乙丙丁》）但**没有提供曲谱**时，先请用户提供简谱/五线谱/MusicXML/他们自己的转写 JSON，再据此写 `score`。
- 用户把曲谱文件放进工作区时，用 `read_file` 读取并转成上面的 JSON；不要凭记忆补全缺失的旋律。
- 可以主动帮用户把已提供的曲谱转成上面的 JSON，并先用 `dry_run=true` 校验音区、拍号、总时长与轨道。
- 不要声称插件内置了某首商业歌曲；插件提供的是演奏引擎与曲谱格式。

## 工具

| 工具 | 用途 |
| --- | --- |
| `piano_play` | 紧凑记谱演奏：`notes` 必填；`octave` `voice` `interval_ms` `duration_ms` `velocity` `title` |
| `piano_play_score` | 结构化曲谱演奏；`dry_run=true` 只校验 |
| `piano_stop` | 立即停止 |
| `piano_status` | 地址、连接数、当前曲目/进度/BPM/踏板 |
| `piano_voices` | 音色 id、采样/合成标记、音区说明 |
| `piano_server` | `start` / `stop` / `restart` / `status` 局域网 HTTP 服务，可传 `port` |

## 边界

- 演奏只由本机 MCP 触发；网页只读，不要用 POST 或网页按钮控制演奏。
- 用户没打开页面、没点「开始聆听」时不会出声；如实说明，不要假装已播放。
- 音符超出 A0-C8 会报错；按报错调整八度。
- 局域网收听需要同一 Wi-Fi；首次启动若防火墙弹窗，请允许访问。
