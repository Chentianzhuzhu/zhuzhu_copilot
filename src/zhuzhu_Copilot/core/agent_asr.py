# -*- coding: utf-8 -*-
"""科大讯飞「语音听写」语音输入：Windows 麦克风采集 + WebSocket 实时识别。

用途
----
主面板输入框右侧的「语音输入」按钮：点击开始录音，边说边出字（流式中间结果），
再次点击结束并把最终文本落到输入框。录音期间输入框上覆盖一层蓝色渐变遮罩，
亮度与波形跟随说话音量（见 ``ui/agent_panel.py`` 的 ``_VoiceOverlay``）。

两套协议 + 自动回退
-------------------
讯飞「语音听写」有两条互不兼容的线，同一个 APPID 往往只开通其中一条：

- ``llm``    大模型版：``wss://iat.cn-huabei-1.xf-yun.com/v1``，报文
  ``header/parameter/payload``，结果 base64 包在 ``payload.result.text`` 里；
  未开通时首帧返回 ``11201 licc failed``。
- ``legacy`` 经典流式版：``wss://iat-api.xfyun.cn/v2/iat``，报文
  ``common/business/data``，结果直接是明文 JSON；未开通时首帧报
  ``10404 no category route found`` / ``10163``。

两者鉴权算法完全一致（HMAC-SHA256 签名 URL）。会话按「配置的协议 → 另一个」
依次尝试，**首个响应就失败（还没拿到任何成功结果）时自动换协议重试**，并把
最终可用的协议写回 ``asr.json``，之后直接命中、不再试错。

实现要点
--------
1. **麦克风采集走 Windows 原生 winmm（waveIn\\*）**，用 ctypes 直接调用，不引入
   pyaudio / sounddevice 等新依赖（本项目只支持 Windows，且打包体积敏感）。
   格式固定 16kHz / 16bit / 单声道 —— 讯飞 IAT 要求的采样格式。
2. **WebSocket 走项目已有的 websockets 库**（同步客户端 ``websockets.sync.client``），
   跑在后台线程里，不阻塞 Qt 事件循环。
3. **回调全部在后台线程触发**，UI 侧必须用 Qt 信号切回主线程后再碰控件
   （见 agent_panel 的 ``voice_text_signal`` / ``voice_level_signal``）。
4. 凭据读取顺序：环境变量 → ``~/.zhuzhu_Copilot/agent/asr.json`` → 内置默认。

公开 API
--------
- ``VoiceInputSession``  一次完整的「开始录音 → 流式识别 → 结束」会话
- ``build_auth_url``     生成带 HMAC-SHA256 鉴权的 wss 地址（可离线单测）
- ``load_credentials`` / ``save_credentials`` / ``load_protocol`` / ``save_protocol``
- ``MicError``           麦克风不可用（无设备 / 被占用 / 系统拒绝）
"""
from __future__ import annotations

import array
import base64
import ctypes
import datetime
import hashlib
import hmac
import json
import math
import os
import queue
import ssl
import threading
import time
from ctypes import wintypes
from time import mktime
from urllib.parse import urlencode
from wsgiref.handlers import format_date_time

from zhuzhu_Copilot import app_identity

# ---------------------------------------------------------------- 音频格式
SAMPLE_RATE = 16000          # 讯飞 IAT 要求 16kHz
SAMPLE_WIDTH = 2             # 16bit
CHANNELS = 1                 # 单声道
FRAME_MS = 40                # 每帧音频时长（40ms，与官方 demo 的 1280B/帧一致）
FRAME_BYTES = SAMPLE_RATE * FRAME_MS // 1000 * SAMPLE_WIDTH * CHANNELS

STATUS_FIRST_FRAME = 0
STATUS_CONTINUE_FRAME = 1
STATUS_LAST_FRAME = 2

# ---------------------------------------------------------------- 协议定义
PROTOCOL_LLM = "llm"          # 大模型版（多语种）
PROTOCOL_LEGACY = "legacy"    # 经典流式版
PROTOCOLS = (PROTOCOL_LLM, PROTOCOL_LEGACY)

#: 协议 → (host, path, 业务参数)。两条线的业务参数结构完全不同，不能混用。
PROTOCOL_SPECS = {
    PROTOCOL_LLM: {
        "host": "iat.cn-huabei-1.xf-yun.com",
        "path": "/v1",
        "params": {
            "domain": "slm",
            "language": "mul_cn",
            "accent": "mandarin",
            "result": {"encoding": "utf8", "compress": "raw", "format": "json"},
        },
    },
    PROTOCOL_LEGACY: {
        "host": "iat-api.xfyun.cn",
        "path": "/v2/iat",
        # ⚠ 经典版**不接受** business.result 子对象（实测返回
        # 10163 "param validate error:'$.business.result' unknown field"）；
        # 结果编码/压缩/格式由服务端默认（utf8/raw/json），无需声明。
        "params": {
            "domain": "iat",
            "language": "zh_cn",
            "accent": "mandarin",
            "vad_eos": 3000,      # 静音 3s 判句尾
            "dwa": "wpgs",        # 开启动态修正：句内逐字修正，流式观感更好
        },
    },
}

#: 兼容旧名（历史调用点）
IAT_HOST = PROTOCOL_SPECS[PROTOCOL_LLM]["host"]
IAT_PATH = PROTOCOL_SPECS[PROTOCOL_LLM]["path"]
IAT_URL = f"wss://{IAT_HOST}{IAT_PATH}"
IAT_PARAMS = PROTOCOL_SPECS[PROTOCOL_LLM]["params"]

# ---------------------------------------------------------------- 内置默认凭据
# 讯飞开放平台应用凭据。可在设置里覆盖（写进 asr.json），或用环境变量覆盖。
#
# ⚠ APISecret 第 8 位是**小写字母 l**（不是大写 I）：写成大写 I 时服务端返回
# HTTP 401「HMAC signature does not match」（两种写法只差一个字符，但小写 l
# 解码后才是干净的 24 位十六进制串 102d9f76dc142bd1537a5896）。
DEFAULT_APP_ID = "4b598548"
DEFAULT_API_KEY = "835edefcf1bbb085ed80b2ffb469e792"
DEFAULT_API_SECRET = "MTAyZDlmNzZkYzE0MmJkMTUzN2E1ODk2"

#: 凭据 / 协议配置文件（与 tts.json 同目录；独立成文件，避免被 settings.json 整体覆写）
CONFIG_FILE = app_identity.data_root() / "agent" / "asr.json"

#: 无语音自动结束：连续静音超过该秒数则自动收尾（避免忘记点停止而一直录）
SILENCE_TIMEOUT_S = 8.0


class MicError(RuntimeError):
    """麦克风不可用（无设备 / 被占用 / 系统拒绝）。"""


# ================================================================ 配置
def _read_config() -> dict:
    """读 asr.json；任何异常都返回空字典（配置损坏绝不能影响语音功能）。"""
    try:
        if CONFIG_FILE.exists():
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _update_config(**fields) -> bool:
    """合并写入 asr.json（保留未提及的字段）。成功返回 True。"""
    try:
        data = _read_config()
        data.update({k: v for k, v in fields.items() if v is not None})
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                               encoding="utf-8")
        return True
    except Exception:
        return False


def load_credentials() -> tuple:
    """读取讯飞凭据，返回 ``(app_id, api_key, api_secret)``。

    优先级：环境变量 ``XFYUN_APP_ID`` / ``XFYUN_API_KEY`` / ``XFYUN_API_SECRET``
    → 配置文件 ``asr.json`` → 内置默认值。任一项缺失时按字段回退，不整体丢弃。
    """
    app_id = os.environ.get("XFYUN_APP_ID", "").strip()
    api_key = os.environ.get("XFYUN_API_KEY", "").strip()
    api_secret = os.environ.get("XFYUN_API_SECRET", "").strip()
    if not (app_id and api_key and api_secret):
        data = _read_config()
        app_id = app_id or str(data.get("app_id") or "").strip()
        api_key = api_key or str(data.get("api_key") or "").strip()
        api_secret = api_secret or str(data.get("api_secret") or "").strip()
    return (app_id or DEFAULT_APP_ID,
            api_key or DEFAULT_API_KEY,
            api_secret or DEFAULT_API_SECRET)


def save_credentials(app_id: str, api_key: str, api_secret: str) -> bool:
    """保存讯飞凭据到 ``asr.json``（设置面板写入）。成功返回 True。"""
    return _update_config(app_id=str(app_id or "").strip(),
                          api_key=str(api_key or "").strip(),
                          api_secret=str(api_secret or "").strip())


def load_protocol() -> str:
    """读取已记住的可用协议；未记录/非法时返回空串（表示自动探测）。"""
    p = str(_read_config().get("protocol") or "").strip().lower()
    return p if p in PROTOCOLS else ""


def save_protocol(protocol: str) -> bool:
    """记住可用的协议，下次直接命中、不再试错。

    传空串表示**清空记忆**（下次会话重新自动探测），供「重新检测接口协议」菜单用。
    """
    if not protocol:
        return _update_config(protocol="")
    if protocol not in PROTOCOLS:
        return False
    return _update_config(protocol=protocol)


def protocol_order(preferred: str = "") -> list:
    """返回本次会话要依次尝试的协议列表（首选在前，另一个兜底）。"""
    first = preferred if preferred in PROTOCOLS else load_protocol()
    if first not in PROTOCOLS:
        first = PROTOCOL_LLM          # 未探测过：先试效果更好的大模型版
    return [first] + [p for p in PROTOCOLS if p != first]


# ================================================================ 鉴权
def build_auth_url(app_id: str, api_key: str, api_secret: str,
                   host: str = IAT_HOST, path: str = IAT_PATH,
                   now: "datetime.datetime | None" = None) -> str:
    """生成带鉴权参数的 wss 地址（RFC1123 时间戳 + HMAC-SHA256 签名）。

    两套协议共用同一套鉴权（只有 host/path 不同）。``now`` 仅供测试注入固定
    时间；签名串必须与讯飞服务端完全一致，顺序为 ``host`` → ``date`` →
    ``GET {path} HTTP/1.1``，各占一行。
    """
    ts = now or datetime.datetime.now()
    date = format_date_time(mktime(ts.timetuple()))
    signature_origin = f"host: {host}\ndate: {date}\nGET {path} HTTP/1.1"
    signature = hmac.new(api_secret.encode("utf-8"),
                         signature_origin.encode("utf-8"),
                         digestmod=hashlib.sha256).digest()
    signature = base64.b64encode(signature).decode("utf-8")
    authorization_origin = (
        f'api_key="{api_key}", algorithm="hmac-sha256", '
        f'headers="host date request-line", signature="{signature}"')
    authorization = base64.b64encode(
        authorization_origin.encode("utf-8")).decode("utf-8")
    return (f"wss://{host}{path}?" + urlencode({
        "authorization": authorization, "date": date, "host": host}))


def build_protocol_url(protocol: str, app_id: str, api_key: str,
                       api_secret: str) -> str:
    """按协议生成鉴权地址（未知协议回落大模型版）。"""
    spec = PROTOCOL_SPECS.get(protocol) or PROTOCOL_SPECS[PROTOCOL_LLM]
    return build_auth_url(app_id, api_key, api_secret,
                          spec["host"], spec["path"])


# ================================================================ 音量
def pcm_level(data: bytes) -> float:
    """16bit 单声道 PCM → 0~1 音量（对数映射，贴合视觉/听觉感知）。

    线性 RMS 在安静段几乎不动、在正常说话段已经顶到上限，做遮罩强度不好看；
    换算成 dB 后按 -55dB~-5dB 映射到 0~1，人声的强弱变化才有可见的动态范围。
    """
    n = len(data) // 2
    if n <= 0:
        return 0.0
    samples = array.array("h")
    samples.frombytes(data[:n * 2])       # Windows x86/x64 为小端，与原生 'h' 一致
    acc = 0
    for v in samples:
        acc += v * v
    rms = math.sqrt(acc / n)
    if rms <= 1.0:
        return 0.0
    db = 20.0 * math.log10(rms / 32768.0)     # 约 -90 ~ 0
    return max(0.0, min(1.0, (db + 55.0) / 50.0))


# ================================================================ winmm 录音
_winmm = None
_winmm_lock = threading.Lock()

_WAVE_FORMAT_PCM = 1
_WAVE_MAPPER = ctypes.c_uint(-1).value        # 0xFFFFFFFF
_CALLBACK_FUNCTION = 0x00030000
_WIM_DATA = 0x03C0
_MMSYSERR_NOERROR = 0

#: waveIn 错误码 → 可读原因（其余按原始错误码展示）
_WAVEIN_ERRORS = {
    1: "系统未安装音频设备",
    2: "音频设备驱动未启用或被禁用",
    4: "内存不足，无法分配录音缓冲",
    5: "参数不受支持（采样率/位宽/声道）",
    8: "录音设备已被其他程序独占",
    32: "录音设备正在被使用，请关闭其他录音程序后重试",
    33: "系统不支持该录音格式",
}


class _WAVEFORMATEX(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    ]


class _WAVEHDR(ctypes.Structure):
    """WAVEHDR（64 位布局）：指针字段一律用 c_void_p，保证对齐与真实结构一致。"""
    _fields_ = [
        ("lpData", ctypes.c_void_p),
        ("dwBufferLength", wintypes.DWORD),
        ("dwBytesRecorded", wintypes.DWORD),
        ("dwUser", ctypes.c_void_p),
        ("dwFlags", wintypes.DWORD),
        ("dwLoops", wintypes.DWORD),
        ("lpNext", ctypes.c_void_p),
        ("reserved", ctypes.c_void_p),
    ]


if hasattr(ctypes, "WINFUNCTYPE"):
    _WAVEINPROC = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.UINT,
                                     ctypes.c_void_p, ctypes.c_void_p,
                                     ctypes.c_void_p)
else:                                        # 非 Windows（仅静态检查/测试用）
    _WAVEINPROC = ctypes.CFUNCTYPE(None, wintypes.HANDLE, wintypes.UINT,
                                   ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.c_void_p)


def _load_winmm():
    """惰性加载 winmm.dll 并声明函数签名（只做一次）。"""
    global _winmm
    with _winmm_lock:
        if _winmm is None:
            try:
                mm = ctypes.WinDLL("winmm")
            except Exception as e:                       # pragma: no cover
                raise MicError(f"无法加载系统录音库 winmm.dll：{e}") from e
            mm.waveInOpen.argtypes = [ctypes.POINTER(wintypes.HANDLE),
                                      wintypes.UINT,
                                      ctypes.POINTER(_WAVEFORMATEX),
                                      ctypes.c_void_p, ctypes.c_void_p,
                                      wintypes.DWORD]
            mm.waveInOpen.restype = wintypes.UINT
            # 缓冲三兄弟：(HWAVEIN, LPWAVEHDR, UINT)
            for _fn in ("waveInPrepareHeader", "waveInUnprepareHeader",
                        "waveInAddBuffer"):
                getattr(mm, _fn).argtypes = [wintypes.HANDLE,
                                             ctypes.c_void_p, wintypes.UINT]
                getattr(mm, _fn).restype = wintypes.UINT
            # 控制三兄弟只吃 HWAVEIN（曾按缓冲三兄弟声明 → TypeError）
            for _fn in ("waveInStart", "waveInStop", "waveInReset",
                        "waveInClose"):
                getattr(mm, _fn).argtypes = [wintypes.HANDLE]
                getattr(mm, _fn).restype = wintypes.UINT
            _winmm = mm
        return _winmm


class WindowsMicRecorder:
    """winmm ``waveIn*`` 录音：固定 16kHz/16bit/单声道，按帧回调 PCM 数据。

    **回调线程约束（MSDN）**：waveIn 的回调里不得调用其它 wave 函数，否则死锁。
    因此回调只做「拷数据 + 塞队列」，把「缓冲重新挂回设备」交给独立的 feeder 线程。
    """

    BUFFERS = 8      # 8 × 40ms = 320ms 缓冲，给 WebSocket 发送留出抖动余量

    def __init__(self, on_audio, frame_bytes: int = FRAME_BYTES,
                 sample_rate: int = SAMPLE_RATE):
        self._on_audio = on_audio
        self._frame = int(frame_bytes)
        self._rate = int(sample_rate)
        self._hwi = wintypes.HANDLE()
        self._bufs = []              # [(ctypes buffer, _WAVEHDR)]
        self._by_addr = {}           # WAVEHDR 地址 → 索引
        self._q = queue.Queue()
        self._proc = None            # 必须保活：被 GC 会直接崩溃
        self._feeder = None
        self._running = False
        self._opened = False

    # ---------------------------------------------------------- 生命周期
    def start(self):
        """打开设备、挂载缓冲并开始录音。失败抛 ``MicError``。"""
        if self._opened:
            return
        mm = _load_winmm()

        wf = _WAVEFORMATEX()
        wf.wFormatTag = _WAVE_FORMAT_PCM
        wf.nChannels = CHANNELS
        wf.nSamplesPerSec = self._rate
        wf.wBitsPerSample = 16
        wf.nBlockAlign = CHANNELS * 16 // 8
        wf.nAvgBytesPerSec = self._rate * wf.nBlockAlign
        wf.cbSize = 0

        self._proc = _WAVEINPROC(self._on_wavein)     # 保活：见字段注释
        rc = mm.waveInOpen(ctypes.byref(self._hwi), _WAVE_MAPPER,
                           ctypes.byref(wf),
                           ctypes.cast(self._proc, ctypes.c_void_p),
                           None, _CALLBACK_FUNCTION)
        if rc != _MMSYSERR_NOERROR:
            self._proc = None
            raise MicError(self._describe(rc, "无法打开麦克风"))

        try:
            for _ in range(self.BUFFERS):
                buf = ctypes.create_string_buffer(self._frame)
                hdr = _WAVEHDR()
                hdr.lpData = ctypes.cast(buf, ctypes.c_void_p)
                hdr.dwBufferLength = self._frame
                hdr.dwFlags = 0
                self._bufs.append((buf, hdr))
                self._by_addr[ctypes.addressof(hdr)] = len(self._bufs) - 1
                self._prepare(mm, hdr)
        except Exception:
            self._teardown()
            raise

        self._running = True
        self._feeder = threading.Thread(target=self._feeder_loop,
                                        name="mic-feeder", daemon=True)
        self._feeder.start()
        rc = mm.waveInStart(self._hwi)
        if rc != _MMSYSERR_NOERROR:
            self._teardown()
            raise MicError(self._describe(rc, "无法启动录音"))
        self._opened = True

    def stop(self):
        """停止录音并释放设备（幂等，可重复调用）。"""
        self._teardown()

    def _prepare(self, mm, hdr):
        mm.waveInUnprepareHeader(self._hwi, ctypes.byref(hdr),
                                 ctypes.sizeof(hdr))
        hdr.dwFlags = 0
        hdr.dwBytesRecorded = 0
        mm.waveInPrepareHeader(self._hwi, ctypes.byref(hdr),
                               ctypes.sizeof(hdr))
        mm.waveInAddBuffer(self._hwi, ctypes.byref(hdr), ctypes.sizeof(hdr))

    def _teardown(self):
        """收尾：停设备 → 停 feeder → 卸缓冲 → 关句柄（任一步失败都继续往下走）。"""
        self._running = False
        if not self._bufs and not self._opened:
            self._proc = None
            return
        try:
            mm = _load_winmm()
        except Exception:
            self._bufs = []
            self._by_addr = {}
            self._proc = None
            self._opened = False
            return
        try:
            mm.waveInStop(self._hwi)
        except Exception:
            pass
        try:
            mm.waveInReset(self._hwi)     # 归还全部在途缓冲，否则 unprepare 会失败
        except Exception:
            pass
        if self._feeder is not None:
            self._feeder.join(timeout=1.5)
            self._feeder = None
        for _buf, hdr in self._bufs:
            try:
                mm.waveInUnprepareHeader(self._hwi, ctypes.byref(hdr),
                                         ctypes.sizeof(hdr))
            except Exception:
                pass
        if self._opened:
            try:
                mm.waveInClose(self._hwi)
            except Exception:
                pass
        self._bufs = []
        self._by_addr = {}
        self._proc = None
        self._opened = False

    # ---------------------------------------------------------- 内部
    def _on_wavein(self, hwi, msg, instance, param1, param2):
        """waveIn 回调（winmm 线程）：仅拷贝数据入队，不调用任何 wave 函数。"""
        if msg != _WIM_DATA:
            return
        try:
            idx = self._by_addr.get(int(param1 or 0))
            if idx is None:
                return
            hdr = self._bufs[idx][1]
            n = int(hdr.dwBytesRecorded)
            if n > 0 and hdr.lpData:
                self._q.put((idx, ctypes.string_at(hdr.lpData, n)))
        except Exception:
            pass          # 回调里绝不能抛：ctypes 会吞掉但会污染输出并丢帧

    def _feeder_loop(self):
        """消费录音帧 → 回调使用方 → 把缓冲重新挂回设备。"""
        mm = _load_winmm()
        while self._running:
            try:
                idx, data = self._q.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self._on_audio(data)
            except Exception:
                pass
            try:
                _buf, hdr = self._bufs[idx]
                self._prepare(mm, hdr)
            except Exception:
                pass

    @staticmethod
    def _describe(rc: int, prefix: str) -> str:
        reason = _WAVEIN_ERRORS.get(int(rc), f"系统错误码 {rc}")
        return f"{prefix}：{reason}"


# ================================================================ 结果解析
def _assemble_ws(node: dict) -> str:
    """从识别片段里拼出文本：``ws[].cw[].w`` 顺序拼接（两套协议结构一致）。"""
    out = []
    for item in (node.get("ws") or []):
        for cw in (item.get("cw") or []):
            w = cw.get("w")
            if w:
                out.append(str(w))
    return "".join(out)


def _decode_result(protocol: str, message: dict) -> tuple:
    """把一帧服务端报文解成 ``(code, status, result_node)``。

    - ``llm``    ：``header.code`` / ``header.status`` / ``payload.result.text``（base64 JSON）
    - ``legacy`` ：``code`` / ``data.status`` / ``data.result``（明文 JSON）
    """
    if protocol == PROTOCOL_LEGACY:
        code = int(message.get("code") or 0)
        data = message.get("data") or {}
        status = data.get("status", 0)
        result = data.get("result") or {}
        return code, status, (result if isinstance(result, dict) else {})
    header = message.get("header") or {}
    code = int(header.get("code") or 0)
    status = header.get("status", 0)
    payload = message.get("payload") or {}
    result = payload.get("result") or {}
    node = {}
    b64 = result.get("text")
    if b64:
        try:
            decoded = json.loads(base64.b64decode(b64).decode("utf-8"))
            if isinstance(decoded, dict):
                node = decoded
        except Exception:
            node = {}
    return code, status, node


class VoiceInputSession:
    """一次完整的语音输入会话：录音 → 流式识别 → 结束。

    所有回调都在**后台线程**触发，使用方（UI）必须自行切回主线程：

    - ``on_text(str)``   中间/最终识别文本（全量，非增量，直接覆盖显示即可）
    - ``on_level(float)``当前音量 0~1（用于遮罩波动）
    - ``on_state(str)``  会话状态：``connecting`` / ``recording`` / ``finished``
    - ``on_error(str)``  失败原因（触发后会话即结束）

    ``protocol`` 显式指定协议；留空则按「记住的协议 → 大模型版 → 经典版」自动尝试。
    """

    def __init__(self, on_text=None, on_level=None, on_error=None, on_state=None,
                 app_id: str = "", api_key: str = "", api_secret: str = "",
                 protocol: str = ""):
        self._on_text = on_text or (lambda _t: None)
        self._on_level = on_level or (lambda _v: None)
        self._on_error = on_error or (lambda _m: None)
        self._on_state = on_state or (lambda _s: None)

        creds = load_credentials()
        self._app_id = (app_id or creds[0]).strip()
        self._api_key = (api_key or creds[1]).strip()
        self._api_secret = (api_secret or creds[2]).strip()
        self._protocol = protocol if protocol in PROTOCOLS else ""

        self._stop = threading.Event()
        self._thread = None
        # 有界队列：WS 握手期间麦克风帧先排队（实测握手 ~1.2s ≈ 30 帧），
        # 上限 10s 是防「发送侧彻底卡死」时内存无限增长，正常绝不会触顶。
        self._pcm_q = queue.Queue(maxsize=int(10 * 1000 / FRAME_MS))

        self._lock = threading.Lock()
        self._segs = []         # [(sn, 文本)]，按出现顺序排列（动态修正就地替换）
        self._last_voice_at = time.monotonic()
        self._used_protocol = ""

    # ---------------------------------------------------------- 控制
    @property
    def active(self) -> bool:
        t = self._thread
        return t is not None and t.is_alive()

    @property
    def used_protocol(self) -> str:
        """本次会话实际生效的协议（未成功则为空串）。"""
        return self._used_protocol

    def start(self):
        """启动后台识别线程（已在运行则忽略）。"""
        if self.active:
            return
        self._stop.clear()
        self._pcm_q = queue.Queue(maxsize=int(10 * 1000 / FRAME_MS))
        with self._lock:
            self._segs = []
        self._last_voice_at = time.monotonic()
        self._used_protocol = ""
        self._thread = threading.Thread(target=self._run, name="voice-input",
                                        daemon=True)
        self._thread.start()

    def stop(self):
        """请求结束会话（后台线程发送收尾帧、取回最终结果后自然退出）。"""
        self._stop.set()

    # ---------------------------------------------------------- 文本拼装
    def current_text(self) -> str:
        """当前已识别的全文（测试与状态展示用）。"""
        with self._lock:
            return "".join(t for _sn, t in self._segs)

    def _push_segment(self, sn, text: str, pgs=None, rg=None):
        """把一段识别结果并入全文。三种语义都要覆盖：

        - **新 sn 追加**（经典版未开 wpgs：每句一个新 sn，各带该句全文）
        - **同 sn 覆盖**（动态修正：同一句反复刷新）
        - **``pgs == "rpl"``**（经典版开 ``dwa`` 的修正指令）：用本段替换
          ``rg=[起, 止]`` 区间的历史段。**替换要就地插入**——若直接删掉再追加，
          被修正的内容会跑到句尾，语序就乱了。
        """
        if not text:
            return
        key = sn if isinstance(sn, int) else (
            self._segs[-1][0] + 1 if self._segs else 1)
        with self._lock:
            lo = hi = None
            if str(pgs or "").lower() == "rpl" and isinstance(rg, (list, tuple)) \
                    and len(rg) >= 2:
                try:
                    lo, hi = int(rg[0]), int(rg[1])
                except Exception:
                    lo = hi = None
            if lo is not None:
                kept, at = [], None
                for s, t in self._segs:
                    if lo <= s <= hi:
                        if at is None:
                            at = len(kept)      # 记下第一个被替换段的位置
                        continue
                    kept.append((s, t))
                kept.insert(at if at is not None else len(kept), (key, text))
                self._segs = kept
            else:
                for i, (s, _t) in enumerate(self._segs):
                    if s == key:
                        self._segs[i] = (key, text)
                        break
                else:
                    self._segs.append((key, text))
            out = "".join(t for _s, t in self._segs)
        self._on_text(out)

    # ---------------------------------------------------------- 音频
    def _on_pcm(self, data: bytes):
        """麦克风帧回调（feeder 线程）：转发给发送队列 + 上报音量。"""
        try:
            self._pcm_q.put_nowait(data)
        except queue.Full:
            pass          # 队列积压（发送侧卡住）时丢最旧的帧，绝不阻塞录音线程
        try:
            lv = pcm_level(data)
        except Exception:
            lv = 0.0
        if lv > 0.08:
            self._last_voice_at = time.monotonic()
        try:
            self._on_level(lv)
        except Exception:
            pass

    # ---------------------------------------------------------- 主流程
    def _run(self):
        """按协议顺序尝试；首个响应即失败时自动换另一套协议重试。"""
        order = protocol_order(self._protocol)
        err = ""
        for i, proto in enumerate(order):
            ok, retry, msg, validated = self._run_once(proto)
            if ok:
                if validated:
                    self._used_protocol = proto
                    if load_protocol() != proto:
                        save_protocol(proto)  # 记住可用协议：下次直接命中
                break
            err = msg
            if not retry:
                break
            if i + 1 < len(order):
                self._on_state("connecting")  # 换协议重连：UI 保持录音态
        if err and not self._used_protocol:
            self._on_error(err)
        try:
            self._on_state("finished")
        except Exception:
            pass

    def _run_once(self, protocol: str) -> tuple:
        """跑一次指定协议，返回 ``(是否跑完, 是否值得换协议重试, 错误信息, 是否已验证可用)``。

        「验证可用」= 至少收到过一帧 ``code == 0`` 的正常响应；只有验证过才值得把
        协议写回配置（否则用户一声没出就点停止，也会把未验证的协议记成可用）。
        """
        ws = None
        mic = None
        got_ok = False          # 是否已拿到过 code==0 的正常响应
        final_seen = False
        try:
            if not (self._app_id and self._api_key and self._api_secret):
                raise RuntimeError("未配置语音识别凭据（APPID / APIKey / APISecret）")
            self._on_state("connecting")

            # **先开麦、后握手**：WS 握手实测要 1s 出头，若等握手完再开麦，
            # 用户按下按钮立刻说的头一两个字会被整段丢掉。麦克风帧先进队列，
            # 握手成功后一次性补发（40ms/帧，积压量很小，补发几乎瞬时）。
            mic = WindowsMicRecorder(self._on_pcm)
            mic.start()

            from websockets.sync.client import connect as ws_connect
            ctx = ssl.create_default_context()
            # 与官方 demo 一致：不校验证书链，避免部分 Windows 环境根证书缺失导致握手失败
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            url = build_protocol_url(protocol, self._app_id, self._api_key,
                                     self._api_secret)
            try:
                ws = ws_connect(url, ssl=ctx, open_timeout=8, close_timeout=2,
                                ping_interval=None, max_size=None)
            except TypeError:      # 老版本 websockets 不接受某些关键字
                ws = ws_connect(url, ssl=ctx, open_timeout=8, close_timeout=2)
            self._on_state("recording")

            # 首帧**必须携带音频**（官方 demo 如此）：先攒到第一块 PCM 再开帧，
            # 用空音频开帧会被服务端判为参数/数据异常。
            started = False

            while not self._stop.is_set():
                sent = False
                while True:
                    try:
                        data = self._pcm_q.get_nowait()
                    except queue.Empty:
                        break
                    if not data:
                        continue
                    sent = True
                    self._send_frame(ws, protocol, data,
                                     STATUS_FIRST_FRAME if not started
                                     else STATUS_CONTINUE_FRAME)
                    started = True
                if not started:
                    # 用户还没开口：不发帧，静候首块音频（停止请求也能立刻响应）
                    if self._stop.wait(0.02):
                        break
                    continue
                # 长时间无语音自动收尾（用户忘点停止时不至于一直录）
                if (time.monotonic() - self._last_voice_at) > SILENCE_TIMEOUT_S:
                    break
                try:
                    msg = ws.recv(timeout=0.02 if sent else 0.05)
                except TimeoutError:
                    continue
                if msg is None:
                    break
                done, ok, code = self._handle_message(protocol, msg)
                if code:
                    # 首个响应就报错 → 很可能该协议未开通，值得换另一套重试
                    return False, (not got_ok), _describe_code(code), got_ok
                got_ok = got_ok or ok
                if done:
                    final_seen = True
                    break

            # ---- 收尾：把残余音频发完，再发结束帧，等最终结果
            if not final_seen:
                while True:
                    try:
                        data = self._pcm_q.get_nowait()
                    except queue.Empty:
                        break
                    if not data:
                        continue
                    self._send_frame(ws, protocol, data,
                                     STATUS_FIRST_FRAME if not started
                                     else STATUS_CONTINUE_FRAME)
                    started = True
                if not started:
                    # 全程没开口（点开立刻点停 / 无输入设备）：仍须一次开帧 + 结束帧。
                    # 这里**不能发零长音频**——服务端会按参数/数据异常回错误码，而
                    # 此时 got_ok 仍为 False，会被判成「该协议未开通」去换另一套重试，
                    # 用户什么都没说却收到「语音识别失败」。发一帧静音 PCM 即可。
                    self._send_frame(ws, protocol, b"\x00" * FRAME_BYTES,
                                     STATUS_FIRST_FRAME)
                self._send_frame(ws, protocol, b"", STATUS_LAST_FRAME)
                deadline = time.monotonic() + 4.0
                while time.monotonic() < deadline:
                    try:
                        msg = ws.recv(timeout=0.3)
                    except TimeoutError:
                        continue
                    except Exception:
                        break
                    if msg is None:
                        break
                    done, ok, code = self._handle_message(protocol, msg)
                    if code:
                        return False, (not got_ok), _describe_code(code), got_ok
                    got_ok = got_ok or ok
                    if done:
                        break

            return True, False, "", got_ok
        except MicError as e:
            return False, False, str(e), got_ok
        except Exception as e:
            # 握手阶段被拒（401/403）属于凭据问题，换协议也救不回来 → 不重试
            retry = not got_ok and not _is_auth_error(e)
            return False, retry, _friendly_error(e), got_ok
        finally:
            if mic is not None:
                try:
                    mic.stop()
                except Exception:
                    pass
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass

    def _send_frame(self, ws, protocol: str, audio: bytes, status: int):
        """按协议打包并发送一帧音频（status=0 首帧带参数，1 中间帧，2 末帧）。"""
        payload = base64.b64encode(audio).decode("utf-8")
        spec = PROTOCOL_SPECS[protocol]
        if protocol == PROTOCOL_LEGACY:
            frame = {
                "common": {"app_id": self._app_id},
                "business": spec["params"],
                "data": {"status": status,
                         "format": f"audio/L16;rate={SAMPLE_RATE}",
                         "encoding": "raw",
                         "audio": payload},
            }
        else:
            frame = {
                "header": {"status": status, "app_id": self._app_id},
                "payload": {"audio": {"audio": payload,
                                      "sample_rate": SAMPLE_RATE,
                                      "encoding": "raw"}},
            }
            if status == STATUS_FIRST_FRAME:
                frame["parameter"] = {"iat": spec["params"]}
        ws.send(json.dumps(frame))

    def _handle_message(self, protocol: str, raw) -> tuple:
        """处理一帧服务端结果，返回 ``(是否结束, 本帧是否成功, 错误码)``。

        错误码不在这里上报：由 ``_run_once`` 决定是「换协议重试」还是「最终报错」，
        否则会出现「换协议前先弹一次错」的噪声。
        """
        try:
            message = json.loads(raw)
        except Exception:
            return False, False, 0
        code, status, node = _decode_result(protocol, message)
        if code != 0:
            return True, False, code
        if node:
            self._push_segment(node.get("sn"), _assemble_ws(node),
                               node.get("pgs"), node.get("rg"))
        return status == STATUS_LAST_FRAME, True, 0


# ================================================================ 错误文案
#: 常见讯飞错误码 → 用户可读说明（重点覆盖「授权/服务未开通」这一类）
_ERROR_HINTS = {
    10105: "该应用未开通语音听写服务，请在讯飞开放平台控制台添加服务",
    10106: "无效参数，请检查语音听写服务参数",
    10163: "参数校验失败，请检查语音听写服务参数",
    10404: "该应用未开通此语音听写接口（路由不存在）",
    10700: "音频数据格式错误",
    11200: "该应用的语音听写授权数量已用尽",
    11201: "该应用未开通此语音听写服务（授权校验失败），请在讯飞控制台开通对应服务",
}


def _describe_code(code: int) -> str:
    hint = _ERROR_HINTS.get(int(code))
    return (f"语音识别服务返回错误码 {code}：{hint}" if hint
            else f"语音识别服务返回错误码 {code}")


def _is_auth_error(exc: Exception) -> bool:
    """是否为握手阶段的鉴权/权限拒绝（换协议重试没有意义）。"""
    name = type(exc).__name__
    if name == "InvalidStatus":
        resp = getattr(exc, "response", None)
        code = getattr(resp, "status_code", 0)
        return code in (401, 403)
    return False


def _friendly_error(exc: Exception) -> str:
    """把底层异常翻成用户看得懂的一句话。"""
    name = type(exc).__name__
    text = str(exc) or name
    if isinstance(exc, ImportError):
        return "缺少 websockets 依赖，无法连接语音识别服务"
    if isinstance(exc, ssl.SSLError):
        return f"语音识别连接 SSL 失败：{text}"
    if isinstance(exc, TimeoutError):
        return "连接语音识别服务超时，请检查网络后重试"
    if _is_auth_error(exc):
        return "语音识别鉴权失败，请检查 APPID / APIKey / APISecret"
    if name in ("InvalidURI", "InvalidHandshake"):
        return "语音识别鉴权失败，请检查 APPID / APIKey / APISecret"
    if name in ("ConnectionClosed", "ConnectionClosedError", "ConnectionClosedOK"):
        return "语音识别连接已断开"
    if isinstance(exc, OSError):
        return f"网络连接失败：{text}"
    return text
