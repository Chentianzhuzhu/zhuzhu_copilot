"""Piano Studio —— AI 钢琴演奏插件（MCP stdio + 局域网 HTTP/SSE 演奏台）

三件套：
  · MCP（stdio）  piano_play / piano_stop / piano_status / piano_voices / piano_server
  · HTTP（局域网） / 演奏台页面、/api/state、/api/voices、/api/events（SSE 实时推送音符）
  · SKILL.md     告诉 Agent 何时用、怎么记谱、怎么让用户打开收听页

设计要点：
  · 音频在浏览器端用 Web Audio 按 web/voices.json 数据化合成，服务器只推事件，
    因此多台手机/电脑能同时收听，且音色、包络、泛音全部由 JSON 驱动，改数据不改代码。
  · 服务器按「绝对时间轴 + 提前量」逐音推送：每个音在发声前 lookahead 毫秒到达浏览器，
    浏览器用 AudioContext 精确排程，网络抖动不会让节奏散掉。
  · 服务器只读：HTTP 侧没有写状态接口，演奏只能由本机 MCP 工具触发，避免局域网内
    其他设备远程操控。
"""

import json
import os
import re
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP_NAME = "piano-studio"
MCP_VERSION = "2024-11-05"
BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"

# ---------------------------------------------------------------------------
# 配置（config.json 为默认值，应用设置页保存后以大写键名注入环境变量覆盖）
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


_CFG = _read_json(BASE_DIR / "config.json")


def _cfg(key: str, default=None):
    env = os.environ.get(key.upper())
    if env is not None and str(env).strip() != "":
        return env
    return _CFG.get(key, default)


def _cfg_int(key: str, default: int, lo: int = None, hi: int = None) -> int:
    try:
        val = int(float(str(_cfg(key, default)).strip()))
    except (TypeError, ValueError):
        val = int(default)
    if lo is not None:
        val = max(lo, val)
    if hi is not None:
        val = min(hi, val)
    return val


def _cfg_float(key: str, default: float, lo: float = None, hi: float = None) -> float:
    try:
        val = float(str(_cfg(key, default)).strip())
    except (TypeError, ValueError):
        val = float(default)
    if lo is not None:
        val = max(lo, val)
    if hi is not None:
        val = min(hi, val)
    return val


def _cfg_bool(key: str, default: bool) -> bool:
    val = _cfg(key, default)
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# 音色与音区（全部来自 web/voices.json，代码不写死任何音色）
# ---------------------------------------------------------------------------

_VOICES_DOC = _read_json(WEB_DIR / "voices.json")
_VOICES = {}
for _v in _VOICES_DOC.get("voices") or []:
    if isinstance(_v, dict) and str(_v.get("id") or "").strip():
        _VOICES[str(_v["id"]).strip()] = _v

_DEFAULT_VOICE = str(_cfg("piano_default_voice", _VOICES_DOC.get("default") or "")).strip()
if _DEFAULT_VOICE not in _VOICES:
    _DEFAULT_VOICE = str(_VOICES_DOC.get("default") or "").strip()
if _DEFAULT_VOICE not in _VOICES:
    _DEFAULT_VOICE = next(iter(_VOICES), "")

_REGISTERS = {}
for _key, _item in (_VOICES_DOC.get("registers") or {}).items():
    if isinstance(_item, dict):
        try:
            _oct = int(_item.get("octave"))
        except (TypeError, ValueError):
            continue
        _REGISTERS[str(_key)] = {"octave": _oct, "label": str(_item.get("label") or _key)}

if not _REGISTERS:
    _REGISTERS = {"low": {"octave": 3, "label": "低音区"},
                  "mid": {"octave": 4, "label": "中音区"},
                  "high": {"octave": 5, "label": "高音区"}}

_LOOKAHEAD_MS = _cfg_int("piano_lookahead_ms", 1200, 200, 5000)
_DEFAULT_INTERVAL_MS = _cfg_int("piano_default_interval_ms", 320, 20, 60000)
_DEFAULT_DURATION_MS = _cfg_int("piano_default_duration_ms", 0, 0, 60000)
_DEFAULT_VELOCITY = _cfg_float("piano_default_velocity", 0.85, 0.05, 1.0)
_MAX_NOTES = _cfg_int("piano_max_notes", 2048, 1, 20000)
_MAX_SECONDS = _cfg_int("piano_max_seconds", 1800, 1, 21600)

_MIDI_LOW, _MIDI_HIGH = 21, 108          # 标准钢琴 A0 - C8


def _voices_public() -> dict:
    return {
        "default": _DEFAULT_VOICE,
        "max_polyphony": int(_VOICES_DOC.get("max_polyphony") or 64),
        "registers": _REGISTERS,
        "voices": [
            {"id": str(v.get("id")), "label": str(v.get("label") or v.get("id")),
             "description": str(v.get("description") or "")}
            for v in _VOICES.values()
        ],
        # 合成参数给浏览器：同一份数据驱动 Web Audio，不在前端重复维护音色表
        "definitions": list(_VOICES.values()),
    }


# ---------------------------------------------------------------------------
# 记谱解析：C4 / C#4 / Db4 / 60 / C4+E4+G4 / R / @间隔 :长度 !力度
# ---------------------------------------------------------------------------

_NOTE_RE = re.compile(r"^([A-Ga-g])([#b♯♭]?)(-?\d+)?$")
_SEMITONES = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_ACCIDENTALS = {"": 0, "#": 1, "b": -1, "♯": 1, "♭": -1}
_PC_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_REST_WORDS = {"R", "REST", "-", "_"}


def _midi_name(midi: int) -> str:
    return f"{_PC_NAMES[midi % 12]}{midi // 12 - 1}"


def _split_modifiers(token: str) -> tuple:
    """按「音高 @间隔 :长度 !力度」拆出本体与三个修饰（后两个可省略）。"""
    body = str(token or "").strip()
    gap = dur = vel = None
    if "!" in body:
        body, raw = body.split("!", 1)
        try:
            vel = float(raw)
        except ValueError:
            raise ValueError(f"力度格式错误：{token}")
    # 先拆 :长度 再拆 @间隔：冒号在间隔之后，避免把 "600:500" 整体当成间隔
    if ":" in body:
        body, raw = body.split(":", 1)
        try:
            dur = int(round(float(raw)))
        except ValueError:
            raise ValueError(f"发声长度格式错误：{token}")
    if "@" in body:
        body, raw = body.split("@", 1)
        try:
            gap = int(round(float(raw)))
        except ValueError:
            raise ValueError(f"间隔格式错误：{token}")
    return body.strip(), gap, dur, vel


def _parse_register(value) -> tuple:
    """返回 (key, 八度, 显示名)。key 为 low/mid/high/custom。"""
    text = "mid" if value is None or str(value).strip() == "" else str(value).strip()
    aliases = {"低音": "low", "低音区": "low", "中音": "mid", "中音区": "mid",
               "高音": "high", "高音区": "high"}
    key = aliases.get(text.lower(), text.lower())
    if key in _REGISTERS:
        return key, int(_REGISTERS[key]["octave"]), str(_REGISTERS[key]["label"])
    try:
        octave = int(float(text))
    except ValueError:
        names = " / ".join(f"{k}={v['label']}(八度{v['octave']})" for k, v in _REGISTERS.items())
        raise ValueError(f"音区参数无效：{value}；可用 {names}，或直接给 0-8 的八度数字")
    if not 0 <= octave <= 8:
        raise ValueError(f"八度超出范围：{octave}（0-8）")
    return "custom", octave, f"{octave} 八度"


def _parse_note_token(token: str, register_octave: int,
                      default_gap: int, default_dur: int, default_vel: float) -> dict:
    body, gap, dur, vel = _split_modifiers(token)
    if not body:
        raise ValueError(f"空音符：{token}")
    gap = default_gap if gap is None else max(20, min(60000, gap))
    vel = default_vel if vel is None else max(0.05, min(1.0, vel))
    if dur is None or dur <= 0:
        dur = default_dur if default_dur > 0 else max(80, int(round(gap * 0.88)))
    dur = max(30, min(60000, int(dur)))

    if body.upper() in _REST_WORDS:
        return {"rest": True, "ms": gap, "dur": dur, "velocity": vel, "token": token}

    midis, names = [], []
    for part in re.split(r"[+＋]", body):
        part = part.strip()
        if not part:
            raise ValueError(f"和弦里有空音符：{token}")
        if part.isdigit():
            midi = int(part)
            name = _midi_name(midi) if _MIDI_LOW <= midi <= _MIDI_HIGH else part
        else:
            match = _NOTE_RE.match(part)
            if not match:
                raise ValueError(f"无法识别的音符：{part}（示例：C4、F#3、Bb4、60）")
            letter = match.group(1).upper()
            accidental = match.group(2)
            octave_text = match.group(3)
            octave = int(octave_text) if octave_text is not None else int(register_octave)
            midi = 12 * (octave + 1) + _SEMITONES[letter] + _ACCIDENTALS[accidental]
            name = _midi_name(midi) if _MIDI_LOW <= midi <= _MIDI_HIGH else part
        if not _MIDI_LOW <= midi <= _MIDI_HIGH:
            raise ValueError(f"音符超出钢琴范围：{part}（可用 A0 - C8，MIDI {_MIDI_LOW}-{_MIDI_HIGH}）")
        if midi not in midis:
            midis.append(midi)
            names.append(name if name != part else _midi_name(midi))

    return {"rest": False, "midis": midis, "names": names,
            "ms": gap, "dur": dur, "velocity": round(vel, 3), "token": token}


def _normalize_items(notes) -> list:
    """把字符串 / 列表统一成 token 列表（列表元素可为字符串或对象）。"""
    if isinstance(notes, str):
        return [x for x in re.split(r"[\s,，、;；]+", notes.strip()) if x]
    if isinstance(notes, (list, tuple)):
        items = []
        for item in notes:
            if isinstance(item, (str, int)):
                items.append(str(item))
                continue
            if not isinstance(item, dict):
                raise ValueError("notes 列表元素必须是字符串或对象")
            rest_value = item.get("rest")
            base = str(item.get("note") or item.get("notes") or "").strip()
            if not base and rest_value is not None:
                base = "R"
            if not base:
                raise ValueError("notes 对象缺少 note / rest 字段")
            gap_value = item.get("ms")
            if gap_value is None and base == "R" and isinstance(rest_value, (int, float)) \
                    and not isinstance(rest_value, bool):
                gap_value = rest_value
            tail = ""
            if gap_value is not None:
                tail += f"@{gap_value}"
            if item.get("dur") is not None:
                tail += f":{item.get('dur')}"
            if item.get("velocity") is not None:
                tail += f"!{item.get('velocity')}"
            items.append(base + tail)
        return [x for x in items if x]
    raise ValueError("notes 必须是字符串（空格分隔）或数组")


def _parse_notes(notes, register_octave: int, default_gap: int,
                 default_dur: int, default_vel: float) -> tuple:
    """返回 (事件列表, 总时长 ms)。事件带绝对开始时间 at。"""
    tokens = _normalize_items(notes)
    if not tokens:
        raise ValueError("notes 为空")
    events, cursor = [], 0
    for index, token in enumerate(tokens):
        try:
            item = _parse_note_token(token, register_octave, default_gap, default_dur, default_vel)
        except ValueError as exc:
            raise ValueError(f"第 {index + 1} 个音符「{token}」：{exc}")
        if item["rest"]:
            cursor += item["ms"]
            continue
        events.append({"at": cursor, "ms": item["ms"], "dur": item["dur"],
                       "velocity": item["velocity"], "midis": item["midis"],
                       "names": item["names"]})
        cursor += item["ms"]
    if not events:
        raise ValueError("notes 里没有可演奏的音符（全是休止符）")
    if len(events) > _MAX_NOTES:
        raise ValueError(f"音符数量 {len(events)} 超过上限 {_MAX_NOTES}，请分成多段调用")
    total = max(cursor, events[-1]["at"] + events[-1]["dur"])
    if total > _MAX_SECONDS * 1000:
        raise ValueError(f"总时长 {total / 1000:.1f}s 超过上限 {_MAX_SECONDS}s，请分成多段调用")
    return events, total


# ---------------------------------------------------------------------------
# 运行状态、SSE 客户端、演奏调度
# ---------------------------------------------------------------------------

_LOCK = threading.RLock()
_CLIENTS = {}


class _Client:
    __slots__ = ("queue", "audio", "alive")

    def __init__(self, audio: bool):
        import queue as _queue
        self.queue = _queue.Queue(maxsize=4096)
        self.audio = bool(audio)
        self.alive = True


def _client_counts() -> tuple:
    with _LOCK:
        total = sum(1 for c in _CLIENTS.values() if c.alive)
        audio = sum(1 for c in _CLIENTS.values() if c.alive and c.audio)
    return total, audio


def _broadcast(event: str, payload: dict) -> None:
    frame = _sse_frame(event, payload)
    with _LOCK:
        clients = [c for c in _CLIENTS.values() if c.alive]
    for client in clients:
        try:
            client.queue.put_nowait(frame)
        except Exception:
            # 慢客户端：丢掉最旧的一帧，保证后续音符仍然及时到达
            try:
                client.queue.get_nowait()
                client.queue.put_nowait(frame)
            except Exception:
                pass


def _sse_frame(event: str, payload: dict) -> bytes:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {data}\n\n".encode("utf-8")


def _broadcast_listeners() -> None:
    total, audio = _client_counts()
    _broadcast("listeners", {"type": "listeners", "listeners": total, "audio_ready": audio})


_playback = None
_playback_seq = 0
_playback_gen = 0


def _playback_state() -> dict:
    with _LOCK:
        cur = _playback
        if not cur:
            return {"playing": False}
        elapsed = int((time.monotonic() - cur["timeline_start"]) * 1000)
        elapsed = max(0, min(elapsed, cur["total_ms"]))
        return {
            "playing": True,
            "playback_id": cur["id"],
            "title": cur["title"],
            "voice": cur["voice"],
            "voice_label": cur["voice_label"],
            "register": cur["register"],
            "register_label": cur["register_label"],
            "note_count": cur["note_count"],
            "total_ms": cur["total_ms"],
            "elapsed_ms": elapsed,
            "remaining_ms": max(0, cur["total_ms"] - elapsed),
            "range": cur["range"],
            "lead_ms": cur["lead_ms"],
        }


def _state_payload() -> dict:
    total, audio = _client_counts()
    voices_doc = _voices_public()
    return {
        "server": _server_info(),
        "playback": _playback_state(),
        "listeners": total,
        "audio_ready": audio,
        "voices": voices_doc["voices"],
        "registers": voices_doc["registers"],
        "max_polyphony": voices_doc["max_polyphony"],
        "defaults": {
            "voice": _DEFAULT_VOICE,
            "interval_ms": _DEFAULT_INTERVAL_MS,
            "duration_ms": _DEFAULT_DURATION_MS,
            "velocity": _DEFAULT_VELOCITY,
            "lookahead_ms": _LOOKAHEAD_MS,
        },
    }


def _stop_playback(reason: str = "stop") -> None:
    global _playback, _playback_gen
    with _LOCK:
        cur = _playback
        if not cur:
            return
        _playback = None
        _playback_gen += 1
    _broadcast("stop", {"type": "stop", "playback_id": cur["id"], "reason": reason})


def _start_playback(events: list, meta: dict) -> dict:
    global _playback, _playback_seq, _playback_gen
    _stop_playback("interrupted")
    with _LOCK:
        _playback_seq += 1
        _playback_gen += 1
        gen = _playback_gen
        playback = {
            "id": f"p{_playback_seq}",
            "title": meta["title"],
            "voice": meta["voice"],
            "voice_label": meta["voice_label"],
            "register": meta["register"],
            "register_label": meta["register_label"],
            "note_count": len(events),
            "total_ms": meta["total_ms"],
            "range": meta["range"],
            "lead_ms": _LOOKAHEAD_MS,
            # 时间轴整体后移一个提前量：第一个音也有完整的提前量做视觉/音频排程
            "timeline_start": time.monotonic() + _LOOKAHEAD_MS / 1000.0,
        }
        _playback = playback
    _broadcast("start", {
        "type": "start",
        "playback_id": playback["id"],
        "title": playback["title"],
        "voice": playback["voice"],
        "voice_label": playback["voice_label"],
        "register": playback["register"],
        "register_label": playback["register_label"],
        "note_count": playback["note_count"],
        "total_ms": playback["total_ms"],
        "range": playback["range"],
        "lead_ms": playback["lead_ms"],
    })
    threading.Thread(target=_run_playback, args=(gen, events, playback),
                     daemon=True, name="piano-playback").start()
    return playback


def _run_playback(gen: int, events: list, playback: dict) -> None:
    timeline_start = playback["timeline_start"]
    lookahead = playback["lead_ms"] / 1000.0
    try:
        for event in events:
            deadline = timeline_start + event["at"] / 1000.0 - lookahead
            while True:
                if gen != _playback_gen:
                    return
                remain = deadline - time.monotonic()
                if remain <= 0:
                    break
                time.sleep(min(0.05, remain))
            in_ms = int(round((timeline_start + event["at"] / 1000.0 - time.monotonic()) * 1000))
            in_ms = max(0, min(in_ms, int(lookahead * 1000)))
            _broadcast("note", {
                "type": "note",
                "playback_id": playback["id"],
                "voice": playback["voice"],
                "in_ms": in_ms,
                "dur_ms": event["dur"],
                "velocity": event["velocity"],
                "notes": [{"midi": m, "name": n} for m, n in zip(event["midis"], event["names"])],
            })
        end_at = timeline_start + playback["total_ms"] / 1000.0
        while True:
            if gen != _playback_gen:
                return
            remain = end_at - time.monotonic()
            if remain <= 0:
                break
            time.sleep(min(0.05, remain))
        _broadcast("end", {"type": "end", "playback_id": playback["id"]})
    finally:
        global _playback
        with _LOCK:
            if gen == _playback_gen and _playback is playback:
                _playback = None


# ---------------------------------------------------------------------------
# 局域网 HTTP 服务（只读：页面 / 状态 / 音色 / SSE 事件流）
# ---------------------------------------------------------------------------

_HTTP_SERVER = None
_HTTP_PORT = 0
_HTTP_LOCK = threading.RLock()
_LAN_CACHE = {"at": 0.0, "ips": []}

_MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": ("default-src 'self'; img-src 'self' data:; "
                                "style-src 'self' 'unsafe-inline'; script-src 'self'; "
                                "connect-src 'self'; base-uri 'none'; form-action 'none'"),
}


def _lan_ips() -> list:
    now = time.monotonic()
    if now - _LAN_CACHE["at"] < 60 and _LAN_CACHE["ips"]:
        return list(_LAN_CACHE["ips"])
    ips = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = str(info[4][0])
            if ip.startswith("127.") or ip.startswith("169.254.") or ip == "0.0.0.0":
                continue
            if ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    if not ips:
        try:
            for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
                if not ip.startswith("127.") and ip not in ips:
                    ips.append(ip)
        except OSError:
            pass
    _LAN_CACHE["at"] = now
    _LAN_CACHE["ips"] = list(ips)
    return ips


def _server_info() -> dict:
    with _HTTP_LOCK:
        running = _HTTP_SERVER is not None
        port = _HTTP_PORT
    local = f"http://127.0.0.1:{port}/" if running else ""
    lan = [f"http://{ip}:{port}/" for ip in _lan_ips()] if running else []
    return {"running": running, "port": port, "local_url": local, "lan_urls": lan,
            "primary_url": (lan[0] if lan else local)}


def _primary_url() -> str:
    return _server_info()["primary_url"]


def _start_http(port: int = None) -> int:
    global _HTTP_SERVER, _HTTP_PORT
    with _HTTP_LOCK:
        if _HTTP_SERVER is not None:
            return _HTTP_PORT
        host = str(_cfg("piano_host", "0.0.0.0")).strip() or "0.0.0.0"
        requested = _cfg_int("piano_port", 8848, 0, 65535) if port is None else int(port)
        requested = max(0, min(65535, requested))
        try:
            server = ThreadingHTTPServer((host, requested), _HttpHandler)
        except OSError as exc:
            _log(f"端口 {requested} 绑定失败（{exc}），改用系统分配端口")
            try:
                server = ThreadingHTTPServer((host, 0), _HttpHandler)
            except OSError as exc2:
                _log(f"HTTP 服务启动失败：{exc2}")
                return 0
        server.daemon_threads = True
        _HTTP_SERVER = server
        _HTTP_PORT = int(server.server_address[1])
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.3},
                         daemon=True, name="piano-http").start()
        info = _server_info()
        _log(f"演奏台已启动：{info['local_url']}"
             + (f"（局域网 {', '.join(info['lan_urls'])}）" if info["lan_urls"] else ""))
        return _HTTP_PORT


def _stop_http() -> None:
    global _HTTP_SERVER, _HTTP_PORT
    with _HTTP_LOCK:
        server = _HTTP_SERVER
        _HTTP_SERVER = None
        _HTTP_PORT = 0
    with _LOCK:
        clients = list(_CLIENTS.values())
    for client in clients:
        try:
            client.queue.put_nowait(None)
        except Exception:
            pass
    if server is not None:
        try:
            server.shutdown()
        except Exception:
            pass
        try:
            server.server_close()
        except Exception:
            pass


class _HttpHandler(BaseHTTPRequestHandler):
    server_version = "PianoStudio/1.0"

    def log_message(self, *args):
        pass

    # ---- 响应工具 ----

    def _send(self, code: int, body: bytes, ctype: str, cache: str = "no-store"):
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            for key, value in _SECURITY_HEADERS.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _send_json(self, code: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def _not_found(self):
        self._send_json(404, {"ok": False, "error": "not found"})

    # ---- 路由 ----

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path or "/"
        if path == "/api/events":
            self._serve_events(parsed)
            return
        if path == "/api/state":
            self._send_json(200, {"ok": True, **_state_payload()})
            return
        if path == "/api/voices":
            self._send_json(200, {"ok": True, **_voices_public()})
            return
        if path == "/api/health":
            self._send_json(200, {"ok": True, "name": APP_NAME, "port": _HTTP_PORT,
                                  "lan_urls": _server_info()["lan_urls"]})
            return
        if path.startswith("/api/"):
            self._not_found()
            return
        self._serve_static(path)

    def do_POST(self):
        self._send_json(405, {"ok": False, "error": "只读服务：演奏请由本机 MCP 工具触发"})

    def _serve_static(self, path: str):
        rel = path.lstrip("/") or "index.html"
        if ".." in Path(rel).parts:
            self._not_found()
            return
        target = (WEB_DIR / rel).resolve()
        try:
            target.relative_to(WEB_DIR.resolve())
        except ValueError:
            self._not_found()
            return
        if not target.is_file():
            self._not_found()
            return
        ctype = _MIME.get(target.suffix.lower())
        if not ctype:
            self._not_found()
            return
        try:
            body = target.read_bytes()
        except OSError:
            self._not_found()
            return
        self._send(200, body, ctype, cache="no-cache")

    def _serve_events(self, parsed):
        query = urllib.parse.parse_qs(parsed.query)
        audio = str((query.get("audio") or ["0"])[0]).lower() in ("1", "true", "yes")
        client = _Client(audio)
        with _LOCK:
            _CLIENTS[id(client)] = client
        _broadcast_listeners()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            for key, value in _SECURITY_HEADERS.items():
                self.send_header(key, value)
            self.end_headers()
            hello = {"type": "hello", "state": _state_payload(),
                     "definitions": _voices_public()["definitions"]}
            self.wfile.write(b": connected\n\n")
            self.wfile.write(_sse_frame("hello", hello))
            self.wfile.flush()
            while client.alive:
                try:
                    frame = client.queue.get(timeout=15)
                except Exception:
                    frame = None
                    if not client.alive:
                        break
                    try:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        break
                    continue
                if frame is None:
                    break
                try:
                    self.wfile.write(frame)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    break
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            client.alive = False
            with _LOCK:
                _CLIENTS.pop(id(client), None)
            _broadcast_listeners()
            try:
                self.close_connection = True
            except Exception:
                pass


def _log(message: str) -> None:
    """日志只写 stderr：stdout 是 MCP JSON-RPC 通道，绝不能污染。"""
    try:
        sys.stderr.write(f"[piano-studio] {message}\n")
        sys.stderr.flush()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# MCP 工具实现
# ---------------------------------------------------------------------------

def _tool_result(text: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": bool(is_error)}


def _ensure_http() -> int:
    port = _start_http()
    return port


def _url_hint() -> str:
    info = _server_info()
    if not info["running"]:
        return "局域网演奏台未启动（可调用 piano_server action=start 重试）"
    urls = info["lan_urls"] or [info["local_url"]]
    return "；".join(urls)


def _tool_play(args: dict) -> dict:
    notes = args.get("notes")
    if notes is None or (isinstance(notes, str) and not notes.strip()) or \
            (isinstance(notes, (list, tuple)) and len(notes) == 0):
        return _tool_result("缺少 notes：请给出音符序列，如 \"C4 D4 E4 G4\"", True)

    voice_id = str(args.get("voice") or _DEFAULT_VOICE).strip()
    if voice_id not in _VOICES:
        available = "、".join(f"{k}({v.get('label')})" for k, v in _VOICES.items())
        return _tool_result(f"未知音色「{voice_id}」。可用音色：{available}", True)

    try:
        register_key, register_octave, register_label = _parse_register(args.get("octave"))
    except ValueError as exc:
        return _tool_result(str(exc), True)

    try:
        gap = int(float(args.get("interval_ms") or _DEFAULT_INTERVAL_MS))
    except (TypeError, ValueError):
        gap = _DEFAULT_INTERVAL_MS
    gap = max(20, min(60000, gap))
    try:
        duration = int(float(args.get("duration_ms") or _DEFAULT_DURATION_MS))
    except (TypeError, ValueError):
        duration = _DEFAULT_DURATION_MS
    duration = max(0, min(60000, duration))
    try:
        velocity = float(args.get("velocity") or _DEFAULT_VELOCITY)
    except (TypeError, ValueError):
        velocity = _DEFAULT_VELOCITY
    velocity = max(0.05, min(1.0, velocity))

    try:
        events, total_ms = _parse_notes(notes, register_octave, gap, duration, velocity)
    except ValueError as exc:
        return _tool_result(str(exc), True)

    title = re.sub(r"\s+", " ", str(args.get("title") or "")).strip()[:80] or "AI 演奏"
    if not _ensure_http():
        return _tool_result("局域网演奏台启动失败（端口不可用），无法播放。", True)

    low = min(min(e["midis"]) for e in events)
    high = max(max(e["midis"]) for e in events)
    playback = _start_playback(events, {
        "title": title,
        "voice": voice_id,
        "voice_label": str(_VOICES[voice_id].get("label") or voice_id),
        "register": register_key,
        "register_label": register_label,
        "total_ms": total_ms,
        "range": [low, high],
    })
    total, audio = _client_counts()
    lines = [
        f"已开始演奏《{title}》：{len(events)} 个音，"
        f"音色「{playback['voice_label']}」，{register_label}，"
        f"总时长 {total_ms / 1000:.1f} 秒。",
        f"收听地址：{_url_hint()}",
        f"当前连接：{total} 个页面，其中 {audio} 个已开启声音。",
    ]
    if audio == 0:
        lines.append("注意：还没有页面开启声音，用户打开上面的地址并点击「开始聆听」后才能听到；"
                     "若已错过开头，可再次调用 piano_play 重播。")
    return _tool_result("\n".join(lines))


def _tool_stop(args: dict) -> dict:
    if not _playback_state().get("playing"):
        return _tool_result("当前没有正在演奏的曲目。")
    _stop_playback("stopped_by_ai")
    return _tool_result("已停止演奏。")


def _tool_status(args: dict) -> dict:
    info = _server_info()
    total, audio = _client_counts()
    playback = _playback_state()
    lines = [
        "Piano Studio 状态",
        f"HTTP 演奏台：{'运行中' if info['running'] else '未启动'}",
        f"监听端口：{info['port'] or '—'}",
        f"本机地址：{info['local_url'] or '—'}",
        f"局域网地址：{('、'.join(info['lan_urls']) if info['lan_urls'] else '未获取到，可用 ipconfig 查看本机 IP')}",
        f"浏览器连接：{total} 个页面（{audio} 个已开启声音）",
    ]
    if playback.get("playing"):
        lines.extend([
            f"正在演奏：《{playback['title']}》",
            f"音色/音区：{playback['voice_label']} / {playback['register_label']}",
            f"进度：{playback['elapsed_ms'] / 1000:.1f}s / {playback['total_ms'] / 1000:.1f}s",
        ])
    else:
        lines.append("当前没有演奏。")
    lines.append("可用工具：piano_play / piano_stop / piano_status / piano_voices / piano_server")
    return _tool_result("\n".join(lines))


def _tool_voices(args: dict) -> dict:
    lines = [f"可用音色（{len(_VOICES)} 种，voice 参数传 id）："]
    for voice_id, voice in _VOICES.items():
        mark = "（默认）" if voice_id == _DEFAULT_VOICE else ""
        lines.append(f"- {voice_id}｜{voice.get('label') or voice_id}{mark}："
                     f"{voice.get('description') or ''}")
    lines.append("音区参数 octave：low=低音区、mid=中音区、high=高音区，也可直接给 0-8 的八度数字。")
    return _tool_result("\n".join(lines))


def _tool_server(args: dict) -> dict:
    action = str(args.get("action") or "status").strip().lower()
    if action == "start":
        try:
            port = int(float(args.get("port") or 0))
        except (TypeError, ValueError):
            port = 0
        port = _start_http(port if port else None)
        if not port:
            return _tool_result("启动失败：端口不可用。", True)
        return _tool_result(f"局域网演奏台已启动：{_url_hint()}")
    if action == "stop":
        _stop_playback("server_stopped")
        _stop_http()
        return _tool_result("已停止局域网演奏台。")
    if action == "restart":
        try:
            port = int(float(args.get("port") or 0))
        except (TypeError, ValueError):
            port = 0
        _stop_http()
        time.sleep(0.1)
        port = _start_http(port if port else None)
        if not port:
            return _tool_result("重启失败：端口不可用。", True)
        return _tool_result(f"已重启局域网演奏台：{_url_hint()}")
    if action == "status":
        info = _server_info()
        total, audio = _client_counts()
        return _tool_result(
            f"HTTP：{'运行中' if info['running'] else '未运行'}（端口 {info['port'] or '—'}）\n"
            f"地址：{info['local_url'] or '—'}\n"
            f"局域网：{('、'.join(info['lan_urls']) if info['lan_urls'] else '—')}\n"
            f"连接：{total} 个页面（{audio} 个已开启声音）")
    return _tool_result(f"未知 action：{action}（可用 start / stop / restart / status）", True)


TOOLS = [
    {
        "name": "piano_play",
        "description": "让钢琴演奏一段旋律（真实音频在局域网网页里实时合成）。支持低/中/高音区、"
                       "多种音色、逐音间隔、逐音发声长度、和弦与休止符。音符写法：C4 为绝对音高；"
                       "不写八度则用 octave 指定音区；C4+E4+G4 为和弦；@毫秒 指定该音到下一个音的间隔；"
                       ":毫秒 指定发声长度；!0.8 指定力度；R 为休止符。示例："
                       "\"C4@400 D4@400 E4@400 G4@800\" 或 \"C4+E4+G4@1200\"。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "notes": {"type": "string",
                          "description": "音符序列（空格/逗号分隔）。支持绝对音高、和弦 +、间隔 @ms、"
                                         "长度 :ms、力度 !v、休止符 R。也接受字符串数组或对象数组。"},
                "octave": {"type": "string",
                           "description": "音区：low=低音区 / mid=中音区 / high=高音区，也可传 0-8 "
                                          "的八度数字；只影响没写八度的音符。默认 mid。"},
                "voice": {"type": "string",
                          "description": "音色 id（见 piano_voices），默认取插件配置。"},
                "interval_ms": {"type": "integer",
                                "description": "默认每音间隔（20-60000 毫秒），可被音符上的 @ms 覆盖。"},
                "duration_ms": {"type": "integer",
                                "description": "默认发声长度（毫秒），0=按间隔自动（约 88%）。"},
                "velocity": {"type": "number",
                             "description": "默认力度 0.05-1.0，可被音符上的 !v 覆盖。"},
                "title": {"type": "string", "description": "曲目名，显示在收听页上（可选）。"},
            },
            "required": ["notes"],
        },
    },
    {
        "name": "piano_stop",
        "description": "立即停止当前正在演奏的曲目。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "piano_status",
        "description": "查看演奏台状态：监听端口、本机/局域网收听地址、浏览器连接数、当前演奏进度。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "piano_voices",
        "description": "列出全部可用音色（id、名称、说明）与音区参数说明。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "piano_server",
        "description": "管理局域网演奏台 HTTP 服务：start / stop / restart / status，"
                       "start/restart 可传 port 指定端口（被占用时自动改用系统端口）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "start / stop / restart / status"},
                "port": {"type": "integer", "description": "可选端口（1-65535）"},
            },
            "required": ["action"],
        },
    },
]

_HANDLERS = {
    "piano_play": _tool_play,
    "piano_stop": _tool_stop,
    "piano_status": _tool_status,
    "piano_voices": _tool_voices,
    "piano_server": _tool_server,
}


# ---------------------------------------------------------------------------
# MCP stdio 主循环
# ---------------------------------------------------------------------------

def _handle(message: dict):
    method = message.get("method", "")
    params = message.get("params") or {}
    rid = message.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": MCP_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": APP_NAME, "version": "1.0"},
        }}
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name = str(params.get("name") or "")
        handler = _HANDLERS.get(name)
        if handler is None:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32601, "message": f"未知工具: {name}"}}
        try:
            result = handler(params.get("arguments") or {})
        except Exception as exc:
            result = _tool_result(f"{type(exc).__name__}: {exc}", True)
        return {"jsonrpc": "2.0", "id": rid, "result": result}
    if rid is None:      # 未知通知：按 MCP 规范不回复
        return None
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": f"未知方法: {method}"}}


def main() -> None:
    if _cfg_bool("piano_auto_start", True):
        _ensure_http()
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    for line in stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            continue
        response = _handle(message)
        if response is not None:
            stdout.write(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")
            stdout.flush()


def _selftest() -> int:
    """离线自检：起 HTTP → 拉页面/状态/音色 → 解析记谱 → 关服务。不依赖音频设备。"""
    result = {"ok": True, "page_bytes": 0, "state": False, "voices": 0,
              "parsed_notes": 0, "total_ms": 0, "error": ""}
    port = _start_http(0)
    if not port:
        result.update(ok=False, error="HTTP 启动失败")
        print(json.dumps(result, ensure_ascii=False))
        return 1
    base = f"http://127.0.0.1:{port}"
    try:
        with urllib.request.urlopen(base + "/", timeout=10) as response:
            result["page_bytes"] = len(response.read())
        with urllib.request.urlopen(base + "/api/state", timeout=10) as response:
            result["state"] = bool(json.loads(response.read().decode("utf-8")).get("ok"))
        with urllib.request.urlopen(base + "/api/voices", timeout=10) as response:
            result["voices"] = len(json.loads(response.read().decode("utf-8")).get("voices") or [])
        events, total = _parse_notes("C4@400 D4@400 E4@800", 4, 320, 0, 0.85)
        result["parsed_notes"] = len(events)
        result["total_ms"] = total
        if result["page_bytes"] < 200 or not result["state"] or not result["voices"] \
                or len(events) != 3 or total <= 0:
            result["ok"] = False
            result["error"] = "自检断言未通过"
    except Exception as exc:
        result.update(ok=False, error=f"{type(exc).__name__}: {exc}")
    finally:
        _stop_http()
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    main()
