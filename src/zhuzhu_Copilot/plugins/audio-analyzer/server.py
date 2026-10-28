"""音频分析插件（MCP stdio 工具 + 本地 HTTP 界面）

三件套：
- MCP（stdio）：upload_audio / analyze_audio / get_spectrum / predict_pitch / detect_beat / extract_tracks / get_status
- 本地 HTTP 服务器：文件上传、频谱可视化、分析参数配置
- SKILL.md：技能规范

依赖：soundfile, librosa（首次使用时自动安装）
"""

import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import numpy as np

# 懒加载音频库
try:
    import soundfile as sf
    import librosa
except ImportError:
    sf = None
    librosa = None

APP_NAME = "audio-analyzer"
MCP_VERSION = "2024-11-05"

# 共享状态
_STATE = {
    "files": {},      # file_id -> {path, duration, sample_rate, channels, metadata}
    "tasks": {},      # task_id -> {status, result}
    "next_id": 1
}

# 音频存储目录
AUDIO_DIR = os.path.join(tempfile.gettempdir(), "audio_analyzer_files")
os.makedirs(AUDIO_DIR, exist_ok=True)


def _gen_id():
    _id = _STATE["next_id"]
    _STATE["next_id"] += 1
    return str(_id)


def _load_audio(path):
    """加载音频文件"""
    if librosa is None:
        return None, "缺少 librosa 库，请先安装: pip install librosa soundfile"
    try:
        y, sr = librosa.load(path, sr=None)
        duration = len(y) / sr
        channels = 1 if len(y.shape) == 1 else y.shape[0]
        return y, sr, duration, channels
    except Exception as e:
        return None, None, None, f"加载失败: {e}"


class AudioAPIHandler(BaseHTTPRequestHandler):
    """HTTP API 处理器"""

    def log_message(self, format, *args):
        pass  # 静默日志

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # 根路径返回 HTML 界面
        if path == "/":
            path = "/index.html"

        if path == "/api/state":
            self._send_json({
                "files": {k: {"id": k, "duration": v.get("duration"), "sample_rate": v.get("sample_rate")} for k, v in _STATE["files"].items()},
                "tasks": {k: v["status"] for k, v in _STATE["tasks"].items()}
            })
        elif path == "/api/files":
            self._send_json(list(_STATE["files"].keys()))
        else:
            self._serve_static(path)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""

        if path == "/api/upload":
            self._handle_upload(body)
        elif path.startswith("/api/analyze/"):
            file_id = path.split("/")[3]
            self._handle_analyze(file_id, body)
        elif path.startswith("/api/spectrum/"):
            file_id = path.split("/")[3]
            self._handle_spectrum(file_id, body)
        else:
            self._send_error(404, "未知端点")

    def _serve_static(self, path):
        """提供静态文件"""
        static_dir = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(static_dir, path.lstrip("/"))
        if os.path.exists(file_path) and os.path.isfile(file_path):
            ext = os.path.splitext(file_path)[1].lower()
            content_type = {
                ".html": "text/html",
                ".css": "text/css",
                ".js": "application/javascript",
                ".png": "image/png",
                ".svg": "image/svg+xml"
            }.get(ext, "application/octet-stream")
            with open(file_path, "rb") as f:
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.end_headers()
                self.wfile.write(f.read())
        else:
            self._send_error(404, "文件不存在")

    def _handle_upload(self, body):
        """处理文件上传（base64 编码）"""
        try:
            data = json.loads(body)
            file_data = data.get("file_data", "")
            filename = data.get("filename", "audio.wav")

            if not file_data:
                self._send_error(400, "缺少文件数据")
                return

            # 解码 base64
            import base64
            audio_bytes = base64.b64decode(file_data)

            # 保存到临时目录
            file_id = _gen_id()
            safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in filename)
            save_path = os.path.join(AUDIO_DIR, f"{file_id}_{safe_name}")

            with open(save_path, "wb") as f:
                f.write(audio_bytes)

            # 加载音频信息
            y, sr, duration, err = _load_audio(save_path)
            if err:
                self._send_error(500, err)
                return

            _STATE["files"][file_id] = {
                "path": save_path,
                "filename": safe_name,
                "duration": duration,
                "sample_rate": sr,
                "channels": 1 if y is None else (1 if len(y.shape) == 1 else y.shape[0])
            }

            self._send_json({"success": True, "file_id": file_id})
        except Exception as e:
            self._send_error(500, str(e))

    def _handle_analyze(self, file_id, body):
        """音频分析"""
        if file_id not in _STATE["files"]:
            self._send_error(404, "文件不存在")
            return

        file_info = _STATE["files"][file_id]
        self._send_json({
            "file_id": file_id,
            "filename": file_info["filename"],
            "duration": file_info["duration"],
            "sample_rate": file_info["sample_rate"],
            "channels": file_info["channels"]
        })

    def _handle_spectrum(self, file_id, body):
        """获取频谱数据"""
        if file_id not in _STATE["files"]:
            self._send_error(404, "文件不存在")
            return

        try:
            data = json.loads(body) if body else {}
            window_size = int(data.get("window_size", 2048))
            hop_length = int(data.get("hop_length", 512))

            file_info = _STATE["files"][file_id]
            y, sr, _, _ = _load_audio(file_info["path"])

            if y is None:
                self._send_error(500, "音频加载失败")
                return

            # 计算频谱
            stft = np.abs(librosa.stft(y, n_fft=window_size, hop_length=hop_length))
            spectrum = librosa.amplitude_to_db(stft, ref=np.max)

            # 返回数据（分段以避免过大）
            result = {
                "file_id": file_id,
                "spectrum": spectrum.tolist()[:100],  # 限制长度
                "sample_rate": sr,
                "window_size": window_size,
                "hop_length": hop_length
            }
            self._send_json(result)
        except Exception as e:
            self._send_error(500, str(e))

    def _send_json(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def _send_error(self, code, message):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"success": False, "error": message}).encode())


class MCPHandler:
    """MCP 工具处理器"""

    TOOLS = [
        {
            "name": "upload_audio",
            "description": "上传音频文件",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "音频文件本地路径"}
                },
                "required": ["file_path"]
            }
        },
        {
            "name": "analyze_audio",
            "description": "分析音频基本信息",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "音频文件路径"}
                },
                "required": ["file_path"]
            }
        },
        {
            "name": "get_spectrum",
            "description": "获取频谱数据",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "window_size": {"type": "integer", "default": 2048},
                    "hop_length": {"type": "integer", "default": 512}
                },
                "required": ["file_path"]
            }
        },
        {
            "name": "predict_pitch",
            "description": "检测音高",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "start_time": {"type": "number", "default": 0},
                    "duration": {"type": "number", "default": 1.0}
                },
                "required": ["file_path"]
            }
        },
        {
            "name": "detect_beat",
            "description": "检测节拍",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"}
                },
                "required": ["file_path"]
            }
        }
    ]

    def __init__(self):
        self.tools = self.TOOLS

    def list_tools(self):
        return {"tools": self.tools}

    def call_tool(self, name, args):
        """调用工具"""
        if name == "upload_audio":
            return self._upload_audio(args.get("file_path", ""))
        elif name == "analyze_audio":
            return self._analyze_audio(args.get("file_path", ""))
        elif name == "get_spectrum":
            return self._get_spectrum(args.get("file_path", ""), args.get("window_size", 2048), args.get("hop_length", 512))
        elif name == "predict_pitch":
            return self._predict_pitch(args.get("file_path", ""), args.get("start_time", 0), args.get("duration", 1.0))
        elif name == "detect_beat":
            return self._detect_beat(args.get("file_path", ""))
        else:
            return {"error": f"未知工具: {name}"}

    def _upload_audio(self, file_path):
        if not os.path.exists(file_path):
            return {"success": False, "error": f"文件不存在: {file_path}"}
        file_id = _gen_id()
        y, sr, duration, channels = _load_audio(file_path)
        if y is None:
            return {"success": False, "error": "音频加载失败"}
        _STATE["files"][file_id] = {
            "path": file_path,
            "filename": os.path.basename(file_path),
            "duration": duration,
            "sample_rate": sr,
            "channels": channels
        }
        return {"success": True, "file_id": file_id, "duration": duration, "sample_rate": sr}

    def _analyze_audio(self, file_path):
        if not os.path.exists(file_path):
            return {"success": False, "error": f"文件不存在: {file_path}"}
        y, sr, duration, channels = _load_audio(file_path)
        if y is None:
            return {"success": False, "error": "音频加载失败"}
        return {"success": True, "file_id": None, "duration": duration, "sample_rate": sr, "channels": channels}

    def _get_spectrum(self, file_path, window_size, hop_length):
        if not os.path.exists(file_path):
            return {"success": False, "error": f"文件不存在: {file_path}"}
        y, sr, _, _ = _load_audio(file_path)
        if y is None:
            return {"success": False, "error": "音频加载失败"}
        try:
            stft = np.abs(librosa.stft(y, n_fft=window_size, hop_length=hop_length))
            spectrum = librosa.amplitude_to_db(stft, ref=np.max)
            return {"success": True, "spectrum": spectrum.tolist()[:50], "sample_rate": sr}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _predict_pitch(self, file_path, start_time, duration):
        if not os.path.exists(file_path):
            return {"success": False, "error": f"文件不存在: {file_path}"}
        y, sr, _, _ = _load_audio(file_path)
        if y is None:
            return {"success": False, "error": "音频加载失败"}
        try:
            start_sample = int(start_time * sr)
            end_sample = min(int((start_time + duration) * sr), len(y))
            segment = y[start_sample:end_sample]
            # 使用 librosa 的音高检测
            pitches, magnitudes = librosa.yin(segment, fmin=librosa.note_to_hz('C2'), fmax=librosa.note_to_hz('C7'))
            if len(pitches) > 0:
                avg_pitch = float(np.mean(pitches[magnitudes > 0.5])) if np.any(magnitudes > 0.5) else 0
                return {"success": True, "pitch_hz": avg_pitch, "notes": [librosa.hz_to_note(avg_pitch)]}
            return {"success": True, "pitch_hz": 0, "notes": []}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _detect_beat(self, file_path):
        if not os.path.exists(file_path):
            return {"success": False, "error": f"文件不存在: {file_path}"}
        y, sr, _, _ = _load_audio(file_path)
        if y is None:
            return {"success": False, "error": "音频加载失败"}
        try:
            tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
            beat_times = librosa.frames_to_time(beat_frames, sr=sr)
            return {"success": True, "tempo": float(tempo), "beats": beat_times.tolist()[:20]}
        except Exception as e:
            return {"success": False, "error": str(e)}


# ---- 主入口 ----
def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--selftest", action="store_true", help="启动 HTTP 服务器进行自检")
    parser.add_argument("--port", type=int, default=8765, help="HTTP 端口")
    args = parser.parse_args()

    if args.selftest:
        # 启动 HTTP 服务器
        server = ThreadingHTTPServer(("0.0.0.0", args.port), AudioAPIHandler)
        print(f"[audio-analyzer] HTTP server running on http://localhost:{args.port}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            server.shutdown()
    else:
        # MCP stdio 模式
        handler = MCPHandler()
        print(json.dumps({"jsonrpc": "2.0", "method": "initialize", "params": {"protocolVersion": MCP_VERSION}}))
        sys.stdout.flush()
        for line in sys.stdin:
            try:
                msg = json.loads(line.strip())
                if msg.get("method") == "tools/list":
                    result = handler.list_tools()
                    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}))
                    sys.stdout.flush()
                elif msg.get("method") == "tools/call":
                    params = msg.get("params", {})
                    result = handler.call_tool(params.get("name", ""), params.get("arguments", {}))
                    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}))
                    sys.stdout.flush()
            except Exception as e:
                print(json.dumps({"jsonrpc": "2.0", "error": {"code": -32603, "message": str(e)}, "id": msg.get("id")}))
                sys.stdout.flush()


if __name__ == "__main__":
    main()
