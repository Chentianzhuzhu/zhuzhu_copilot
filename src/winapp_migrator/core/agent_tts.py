# -*- coding: utf-8 -*-
"""Qwen-TTS 声音复刻与语音合成模块（阿里云 DashScope 真实 API 调用）

提供能力：
- create_voice  : 上传参考音频创建自定义音色（声音复刻，qwen-voice-enrollment）
- query_voice   : 查询音色详情（action=query，需 voice 参数）
- delete_voice  : 删除音色（action=delete，不可带 target_model）
- synthesize    : 用指定音色合成语音并下载到本地（multimodal-generation）

API Key 读取顺序（避免硬编码）：
1. 环境变量 DASHSCOPE_API_KEY
2. 独立配置文件 ~/.winapp_migrator/agent/tts.json 的 api_key 字段
   （不放进 settings.json，因其会被设置面板整体覆写）

参考音频要求：10~20s 推荐（最长 60s）、≥24kHz、单声道、≤10MB。
"""
import base64
import json
import os
import pathlib
import urllib.error
import urllib.request
from pathlib import Path

CUSTOMIZATION_URL = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization"
TTS_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
ENROLL_MODEL = "qwen-voice-enrollment"
DEFAULT_TARGET_MODEL = "qwen3-tts-vc-2026-01-22"

CONFIG_FILE = Path.home() / ".winapp_migrator" / "agent" / "tts.json"

# 音色显示名（设置面板与状态提示使用友好名称，而非一长串 voice_id）
VOICE_DISPLAY_NAME = "蝶-三角洲行动"


# ---------------------------------------------------------------- 配置
def load_api_key() -> str:
    """读取 DashScope API Key：环境变量优先，其次独立配置文件 tts.json"""
    key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if key:
        return key
    try:
        if CONFIG_FILE.exists():
            return str(json.loads(CONFIG_FILE.read_text(encoding="utf-8")).get("api_key", "")).strip()
    except Exception:
        pass
    return ""


def save_config(api_key: str = "", target_model: str = DEFAULT_TARGET_MODEL,
                voice_id: str = "", preferred_name: str = "", auto_read: bool = True,
                speech_rate: float = 1.0, voice_angry_id: str = "") -> bool:
    """保存 TTS 配置到独立文件 tts.json（设置面板写入，避免被 settings.json 覆写）
    voice_id 为默认音色（正常/高兴），voice_angry_id 为生气/怀疑专用音色。"""
    try:
        data = {}
        if CONFIG_FILE.exists():
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        data["api_key"] = api_key or data.get("api_key", "")
        data["target_model"] = target_model or data.get("target_model", DEFAULT_TARGET_MODEL)
        data["voice_id"] = voice_id or data.get("voice_id", "")
        data["voice_angry_id"] = voice_angry_id or data.get("voice_angry_id", "")
        data["preferred_name"] = preferred_name or data.get("preferred_name", "")
        data["auto_read"] = bool(auto_read)
        data["speech_rate"] = float(speech_rate or 1.0)
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except OSError:
        return False


def load_config() -> dict:
    """读取 TTS 配置（不含敏感 Key 之外的全部字段）"""
    data = {"target_model": DEFAULT_TARGET_MODEL, "voice_id": "", "voice_angry_id": "",
            "preferred_name": "", "auto_read": True, "speech_rate": 1.0}
    try:
        if CONFIG_FILE.exists():
            data.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass
    return data


# 朗读意图关键词：命中即自动开启"AI 边输出边朗读 / 回复自动朗读"
READ_INTENT_KEYWORDS = (
    "朗读", "读出来", "读给我听", "读一下", "读一读", "读给", "读给我", "念出来",
    "语音回复", "语音播报", "语音输出", "语音回答", "语音", "播报",
    "边读边", "边输出边读", "边说边读", "读出来给我",
    "voice", "speak", "read aloud",
)


def has_read_intent(text: str) -> bool:
    """判断用户输入是否要求朗读（命中任一关键词返回 True）"""
    t = (text or "").lower()
    return any(k in t for k in READ_INTENT_KEYWORDS)


def _request(url: str, payload: dict, timeout: int = 120) -> dict:
    """发起 DashScope POST 请求，返回解析后的 JSON dict；失败抛 RuntimeError"""
    key = load_api_key()
    if not key:
        raise RuntimeError("未配置 DashScope API Key：请设置环境变量 DASHSCOPE_API_KEY "
                           "或在 TTS 设置中填写")
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        raise RuntimeError(f"DashScope HTTP {e.code}: {body[:500]}")


def _audio_data_url(path: str) -> str:
    """读取音频文件并转为 data URL（data:audio/wav;base64,...）"""
    p = pathlib.Path(path)
    if not p.exists():
        raise RuntimeError(f"参考音频不存在: {path}")
    size = p.stat().st_size
    if size > 10 * 1024 * 1024:
        raise RuntimeError(f"参考音频过大（{size} bytes > 10MB），请截取 10~20s 片段")
    b64 = base64.b64encode(p.read_bytes()).decode()
    return "data:audio/wav;base64," + b64


# ---------------------------------------------------------------- 音色管理
def create_voice(audio_path: str, target_model: str = DEFAULT_TARGET_MODEL,
                 preferred_name: str = "diede", timeout: int = 120) -> str:
    """上传参考音频创建自定义音色，返回 voice_id。

    preferred_name 仅允许字母/数字/下划线（中文等非法字符会导致 API 400），
    传入中文显示名时自动清洗为合法 ASCII 名。
    """
    import re as _re
    safe = _re.sub(r"[^0-9A-Za-z_]", "_", preferred_name or "").strip("_")
    if not safe:
        safe = "diede"
    payload = {
        "model": ENROLL_MODEL,
        "input": {
            "action": "create",
            "target_model": target_model,
            "preferred_name": safe,
            "audio": {"data": _audio_data_url(audio_path)},
        },
    }
    d = _request(CUSTOMIZATION_URL, payload, timeout=timeout)
    voice_id = (d.get("output") or {}).get("voice", "")
    if not voice_id:
        raise RuntimeError("创建音色失败：响应中无 voice 字段: " +
                           json.dumps(d, ensure_ascii=False)[:300])
    return voice_id


def query_voice(voice_id: str, target_model: str = DEFAULT_TARGET_MODEL,
                timeout: int = 60) -> dict:
    """查询音色详情（action=query 为单查，必须携带 voice 参数）"""
    payload = {
        "model": ENROLL_MODEL,
        "input": {"action": "query", "target_model": target_model, "voice": voice_id},
    }
    return _request(CUSTOMIZATION_URL, payload, timeout=timeout)


def delete_voice(voice_id: str, timeout: int = 60) -> dict:
    """删除音色（action=delete，注意：不可带 target_model，否则报 VoiceNotFound）"""
    payload = {
        "model": ENROLL_MODEL,
        "input": {"action": "delete", "voice": voice_id},
    }
    return _request(CUSTOMIZATION_URL, payload, timeout=timeout)


def list_voices(timeout: int = 60) -> list:
    """列出已创建的音色（action=list），返回 [{'voice','target_model','language','gmt_create'}, ...]"""
    payload = {"model": ENROLL_MODEL, "input": {"action": "list"}}
    d = _request(CUSTOMIZATION_URL, payload, timeout=timeout)
    return (d.get("output") or {}).get("voice_list") or []


# ---------------------------------------------------------------- 合成
def _strip_wav_header(chunk: bytes) -> bytes:
    """剥离首个音频分片中的 RIFF WAV 头，返回纯 PCM 数据"""
    if chunk[:4] != b"RIFF" or chunk[8:12] != b"WAVE":
        return chunk  # 不是 WAV 头（异常分片），原样返回
    pos = 12
    while pos + 8 <= len(chunk):
        cid = chunk[pos:pos + 4]
        csz = int.from_bytes(chunk[pos + 4:pos + 8], "little")
        if cid == b"data":
            return chunk[pos + 8:]
        pos += 8 + csz + (csz % 2)
    return chunk


def _detect_language(text: str) -> str:
    """根据文本内容判断语言类型（DashScope language_type 参数）：
    含中文按中文合成（发音/语调更接近参考音频），否则用英文。"""
    t = text or ""
    # 中日韩统一表意文字区间
    if any("\u4e00" <= ch <= "\u9fff" for ch in t):
        return "Chinese"
    return "English"


# ---------------------------------------------------------------- 语速
# 说明：qwen3-tts-vc 实测会把文本内 [happy]/[sad] 等情感标签当正文读出来
# （DashScope 官方 emtag 仅对部分模型生效），因此不做文本注入，
# 只通过 speech_rate 按句动态变速（感叹快/悲伤慢/长句慢/短句快），
# 让朗读节奏有起伏；情感语义交给模型自身理解（High Expressiveness）。
def _analyze_expression(text: str):
    """按文本内容做轻量情感/语速分析（本地规则，不额外调用 LLM）。

    返回 (情感分类, 语速倍率)：
    - 情感分类用于按情感选择音色（angry/suspicious 用生气音色，其余默认音色）
    - 倍率用于 speech_rate 参数（感叹快/悲伤慢/长句慢/短句快）
    """
    t = text or ""
    emotion = "normal"
    rate = 1.0
    # 生气/怀疑 → 新音色；悲伤/高兴 → 语速倾向
    if any(k in t for k in ("愤怒", "生气", "可恶", "过分", "凭什么", "受不了",
                            "气死", "怒斥", "严厉", "警告", "滚", "闭嘴",
                            "讨厌", "烦死了", "岂有此理", "混蛋")):
        emotion, rate = "angry", 1.15
    elif any(k in t for k in ("怀疑", "质疑", "难道", "莫非", "真的假的",
                              "不可能吧", "骗人", "说谎", "有诈", "不对劲",
                              "古怪", "蹊跷", "糊弄", "耍我", "玩我")):
        emotion, rate = "suspicious", 1.0
    elif any(k in t for k in ("难过", "伤心", "遗憾", "痛苦", "失望", "难受",
                              "悲伤", "心碎", "沮丧", "委屈", "低沉", "呜咽",
                              "唉", "恳求", "拜托", "求求", "轻声", "温柔",
                              "累了", "疲惫", "辛苦", "无奈")):
        emotion, rate = "sad", 0.9
    elif any(k in t for k in ("恭喜", "太棒", "真好", "万岁", "成功了", "赢了",
                              "完美", "惊喜", "开心", "高兴", "欢呼")):
        emotion, rate = "happy", 1.1
    if emotion in ("normal", "suspicious"):
        # 标点补充：感叹/疑问略快，省略号舒缓
        if "！" in t or "!" in t:
            rate = 1.1
        elif "？" in t or "?" in t or any(k in t for k in ("吗", "呢", "怎么", "为什么")):
            rate = 1.05
        elif "……" in t or "..." in t:
            rate = 0.95
    # 句长补充：长句放慢、短句稍快，制造节奏起伏
    if len(t) >= 40:
        rate = min(rate, 0.95)
    elif len(t) <= 8:
        rate = max(rate, 1.05)
    return emotion, rate


def _select_voice(text: str, voice_id: str = "") -> str:
    """按文本情感选择音色：显式指定的 voice_id 优先；
    生气/怀疑文本用配置的 voice_angry_id（新音色），其余用默认 voice_id。"""
    if voice_id.strip():
        return voice_id.strip()
    cfg = load_config()
    emotion = _analyze_expression(text)[0]
    if emotion in ("angry", "suspicious"):
        angry_id = str(cfg.get("voice_angry_id") or "").strip()
        if angry_id:
            return angry_id
    return str(cfg.get("voice_id") or "").strip()


def _decorate_for_synthesis(text: str) -> tuple:
    """合成前处理：计算有效语速（不注入任何文本，避免标签被读出）。

    返回 (原文本, 有效语速倍率)。
    有效语速 = 用户面板基准语速 × 动态倍率，限制在 [0.5, 2.0]。
    """
    _emotion, rate = _analyze_expression(text)
    cfg = load_config()
    base = float(cfg.get("speech_rate") or 1.0)
    eff = min(2.0, max(0.5, base * rate))
    return text, eff


def _pcm_to_wav(pcm: bytes, rate: int = 24000, channels: int = 1, bits: int = 16) -> bytes:
    """PCM -> 完整 WAV 内存字节（流式合成落盘用）"""
    import struct
    byte_rate = rate * channels * bits // 8
    block_align = channels * bits // 8
    return struct.pack("<4sI4s4sIHHIIHH4sI",
                       b"RIFF", 36 + len(pcm), b"WAVE", b"fmt ", 16,
                       1, channels, rate, byte_rate, block_align, bits, b"data", len(pcm)) + pcm


def _fade_edges(pcm: bytes, rate: int = 24000, fade_ms: int = 20) -> bytes:
    """句首淡入 + 句尾淡出（作用于 16bit PCM 拼接数据）。

    服务端合成的音频开头/结尾往往直接是非零语音波形（无前导/尾随静音），
    任何播放器从静音突变到非零波形都会爆一声"咚"。落盘前线性渐入渐出，
    保证文件本身开头从 0 渐起、结尾渐到 0，任何播放器播放都干净。
    """
    if not pcm:
        return pcm
    import array as _arr
    a = _arr.array("h", pcm)
    n = len(a)
    f = min(rate * fade_ms // 1000, n // 2)
    if f > 0:
        for i in range(f):
            a[i] = int(a[i] * i / f)                 # 句首渐入（首样本=0）
            a[n - 1 - i] = int(a[n - 1 - i] * i / f)  # 句尾渐出（末样本=0）
    return a.tobytes()


def _fade_wav_file(path: str, fade_ms: int = 20):
    """对已落盘的 WAV 文件做句首淡入 + 句尾淡出（16bit 才处理，其他跳过）"""
    import struct
    import wave
    try:
        with wave.open(path, "rb") as w:
            ch, sw, fr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            data = w.readframes(n)
    except Exception:
        return
    if sw != 2 or not data:
        return
    a = __import__("array").array("h", data)
    f = min(fr * fade_ms // 1000, len(a) // 2)
    if f > 0:
        for i in range(f):
            a[i] = int(a[i] * i / f)
            a[len(a) - 1 - i] = int(a[len(a) - 1 - i] * i / f)
    b = a.tobytes()
    hdr = struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + len(b), b"WAVE", b"fmt ", 16,
                      1, ch, fr, fr * ch * sw, ch * sw, sw * 8, b"data", len(b))
    try:
        with open(path, "wb") as wf:
            wf.write(hdr + b)
    except OSError:
        pass


def synthesize_stream(text: str, voice_id: str, model: str = DEFAULT_TARGET_MODEL,
                      on_chunk=None, output_path: str = "", timeout: int = 120) -> str:
    """流式合成语音（SSE 分片），边接收边回调，返回完整 wav 文件路径。

    on_chunk(pcm_bytes)：每收到一段 PCM（已剥离 WAV 头，24kHz/16bit/单声道）回调一次，
    可用于边生成边播放；on_chunk 为空时仅保存文件。
    """
    if not text.strip():
        raise RuntimeError("合成文本为空")
    if not voice_id.strip():
        voice_id = str(load_config().get("voice_id", "")).strip()
    if not voice_id.strip():
        raise RuntimeError("未指定音色 voice_id，请先在 AI 设置中选择音色")
    key = load_api_key()
    if not key:
        raise RuntimeError("未配置 DashScope API Key：请设置环境变量 DASHSCOPE_API_KEY "
                           "或在 TTS 设置中填写")
    payload = {
        "model": model,
        "input": {"text": text, "voice": voice_id},
        "parameters": {"stream": True},
    }
    # 语言类型 + 情感标签 + 动态语速：还原原生音色并让朗读有情感/节奏变化
    deco_text, eff_speed = _decorate_for_synthesis(text)
    payload["input"]["text"] = deco_text
    lang = _detect_language(text)   # 用原文判断语言（标签为英文，避免误判）
    if lang:
        payload["input"]["language_type"] = lang
    speed = eff_speed
    if 0.5 <= speed <= 2.0:
        payload["parameters"]["speech_rate"] = speed
    req = urllib.request.Request(
        TTS_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                 "X-DashScope-SSE": "enable", "Accept": "text/event-stream"})
    parts = []
    first = True
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "ignore").strip()
                if not line.startswith("data:"):
                    continue
                try:
                    d = json.loads(line[5:].strip())
                except Exception:
                    continue
                b64 = ((d.get("output") or {}).get("audio") or {}).get("data") or ""
                if not b64:
                    continue
                chunk = base64.b64decode(b64)
                if first:
                    chunk = _strip_wav_header(chunk)
                    first = False
                if chunk:
                    parts.append(chunk)
                    if on_chunk:
                        on_chunk(chunk)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"DashScope HTTP {e.code}: "
                           + e.read().decode("utf-8", "ignore")[:500])
    if not parts:
        raise RuntimeError("合成失败：未收到音频分片")
    if not output_path:
        out_dir = pathlib.Path.cwd() / "tts_output"
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(out_dir / f"tts_{int(__import__('time').time())}.wav")
    # 落盘前做句首淡入 + 句尾淡出：服务端音频开头/结尾直接是非零波形，
    # 不处理则任何播放器（含系统播放器）从静音突变到语音都会爆"咚"
    pcm = _fade_edges(b"".join(parts))
    pathlib.Path(output_path).write_bytes(_pcm_to_wav(pcm))
    return output_path


def synthesize(text: str, voice_id: str, model: str = DEFAULT_TARGET_MODEL,
               output_path: str = "", timeout: int = 120) -> str:
    """用指定音色合成语音，下载到本地，返回音频文件路径。

    output_path 为空时自动生成：工作目录/tts_output_<时间戳>.wav
    """
    if not text.strip():
        raise RuntimeError("合成文本为空")
    if not voice_id.strip():
        # 未显式指定音色时，按文本情感选择（生气/怀疑用专用音色，其余默认）
        voice_id = _select_voice(text)
    if not voice_id.strip():
        raise RuntimeError("未指定音色 voice_id，请先在 AI 设置中选择音色")
    payload = {
        "model": model,
        "input": {"text": text, "voice": voice_id},
    }
    # 语言类型 + 动态语速（按句情感/标点/长度变速，节奏有起伏）
    _text, eff_speed = _decorate_for_synthesis(text)
    lang = _detect_language(text)
    if lang:
        payload["input"]["language_type"] = lang
    speed = eff_speed
    if 0.5 <= speed <= 2.0:
        payload["parameters"] = {"speech_rate": speed}
    d = _request(TTS_URL, payload, timeout=timeout)
    audio_url = ((d.get("output") or {}).get("audio") or {}).get("url", "")
    if not audio_url:
        raise RuntimeError("合成失败：响应无音频 URL: " +
                           json.dumps(d, ensure_ascii=False)[:300])
    if not output_path:
        out_dir = pathlib.Path.cwd() / "tts_output"
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(out_dir / f"tts_{int(__import__('time').time())}.wav")
    urllib.request.urlretrieve(audio_url, output_path)
    _fade_wav_file(output_path)   # 句首淡入 + 句尾淡出，消除文件开头/结尾爆音
    return output_path
