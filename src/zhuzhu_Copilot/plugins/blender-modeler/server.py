"""blender-modeler：把 Blender 变成 Agent 的真实 3D 建模后端（stdio MCP 服务器）。

架构
----
本文件是 MCP stdio 服务器（由应用用内置 Python 拉起），负责：
  1. 发现本机 blender.exe（可被插件配置 BLENDER_EXE 覆盖）；
  2. 首次建模调用时拉起**常驻** `blender --background` 进程（blender_host.py），
     通过 127.0.0.1 本地 socket 转发 JSON 行请求；
  3. 把渲染出的 PNG 以 MCP image 内容项返回给模型。

为什么常驻而不是一次性脚本：建模是几十次小步迭代（加体块 → 倒角 → 材质 → 打光 →
渲染 → 修），每次重启 Blender 要 2~5 秒，多步流程会变得不可用。常驻进程同时保留
场景、材质、相机与 Cycles 渲染缓存。

纪律
----
- stdout 只输出 JSON-RPC，日志一律走 stderr；
- 不做任何 mock：所有结果来自真实 bpy 求值；
- 插件配置（config.json → 环境变量）：BLENDER_EXE / BLENDER_OUTPUT_DIR /
  BLENDER_RENDER_ENGINE / BLENDER_START_TIMEOUT / BLENDER_IDLE_TIMEOUT /
  BLENDER_PREVIEW_PORT / BLENDER_PREVIEW_BIND；
- 局域网预览页是独立线程里的只读 HTTP 服务，轮询只读缓存，不挤占 Blender 调用锁；
- 工具在主循环线程里串行执行（与 computer-control 相同的教训：不要用线程池 +
  超时，否则一个慢工具会把后续调用全部拖进排队超时）。
"""
import atexit
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

APP_NAME = "blender-modeler"
MCP_VERSION = "2024-11-05"

PLUGIN_DIR = Path(__file__).resolve().parent
HOST_SCRIPT = PLUGIN_DIR / "blender_host.py"
PRESETS_DIR = PLUGIN_DIR / "presets"

START_TIMEOUT = float(os.environ.get("BLENDER_START_TIMEOUT") or 90)
IDLE_TIMEOUT = float(os.environ.get("BLENDER_IDLE_TIMEOUT") or 1800)
DEFAULT_ENGINE = (os.environ.get("BLENDER_RENDER_ENGINE") or "CYCLES").upper()
DEFAULT_SAMPLES = int(os.environ.get("BLENDER_RENDER_SAMPLES") or 32)


def _log(text: str) -> None:
    try:
        sys.stderr.write(f"[{APP_NAME}] {text}\n")
        sys.stderr.flush()
    except Exception:
        pass


# ============================================================================
# 输出目录 / Blender 可执行文件发现
# ============================================================================

def output_dir() -> Path:
    """默认落在桌面交付目录：<Desktop>/zhuzhu Copilot/blender；可用配置覆盖。"""
    raw = str(os.environ.get("BLENDER_OUTPUT_DIR") or "").strip()
    p = Path(os.path.expanduser(raw)) if raw else (Path.home() / "Desktop" / "zhuzhu Copilot" / "blender")
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        p = PLUGIN_DIR / "output"
        p.mkdir(parents=True, exist_ok=True)
    return p


_BLENDER_CACHE = {"path": "", "ts": 0.0}


def _version_key(path: Path):
    """按目录名里的数字排序（Blender 3.5 < 4.1 < 4.10）。"""
    import re
    nums = [int(x) for x in re.findall(r"\d+", path.parent.name)]
    return nums


def find_blender(force: bool = False) -> str:
    """定位 blender.exe：配置 > 标准安装目录（多版本取最高） > PATH。"""
    configured = str(os.environ.get("BLENDER_EXE") or "").strip()
    if configured:
        p = Path(os.path.expanduser(configured))
        if p.is_file():
            return str(p)
        found = shutil.which(configured)
        if found:
            return found
        raise RuntimeError(f"配置的 BLENDER_EXE 不可用：{configured}")

    if not force and _BLENDER_CACHE["path"] and time.time() - _BLENDER_CACHE["ts"] < 300:
        if Path(_BLENDER_CACHE["path"]).is_file():
            return _BLENDER_CACHE["path"]

    roots = [Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Blender Foundation",
             Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Blender Foundation"]
    cands = []
    for root in roots:
        if not str(root) or not root.is_dir():
            continue
        for d in root.iterdir():
            exe = d / "blender.exe"
            if exe.is_file():
                cands.append(exe)
    if cands:
        cands.sort(key=_version_key, reverse=True)
        _BLENDER_CACHE.update(path=str(cands[0]), ts=time.time())
        return str(cands[0])

    found = shutil.which("blender")
    if found:
        _BLENDER_CACHE.update(path=found, ts=time.time())
        return found
    raise RuntimeError(
        "没有找到 Blender。请安装 Blender，或在插件配置里设置 BLENDER_EXE 为 blender.exe 的完整路径。")


# ============================================================================
# 常驻 Blender 进程 + 本地 socket 转发
# ============================================================================

class BlenderBridge:
    def __init__(self):
        self.proc = None
        self.conn = None
        self.rf = None
        self.wf = None
        self.listen = None
        self.expect_pid = None
        self._lock = threading.RLock()
        self._id = 0
        self._stderr = deque(maxlen=80)
        self._stdout = deque(maxlen=40)
        self.version = ""

    # ---------- 进程生命周期 ----------

    def alive(self) -> bool:
        return bool(self.proc and self.proc.poll() is None and self.conn)

    def start(self, restart: bool = False) -> dict:
        with self._lock:
            if self.alive() and not restart:
                return {"version": self.version, "pid": self.proc.pid, "restarted": False}
            self.stop()
            exe = find_blender()
            if not HOST_SCRIPT.is_file():
                raise RuntimeError(f"缺少 blender_host.py：{HOST_SCRIPT}")
            out = output_dir()
            self.listen = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.listen.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.listen.bind(("127.0.0.1", 0))
            self.listen.listen(1)
            port = self.listen.getsockname()[1]
            self.listen.settimeout(START_TIMEOUT)

            cmd = [exe, "--background", "--factory-startup",
                   "--python", str(HOST_SCRIPT), "--",
                   "--port", str(port),
                   "--output", str(out),
                   "--presets", str(PRESETS_DIR),
                   "--idle", str(IDLE_TIMEOUT)]
            flags = 0
            if os.name == "nt":
                flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            _log(f"启动 Blender：{' '.join(cmd)}")
            self.proc = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, creationflags=flags, close_fds=True)
            threading.Thread(target=self._drain, args=(self.proc.stdout, self._stdout, "out"),
                             daemon=True).start()
            threading.Thread(target=self._drain, args=(self.proc.stderr, self._stderr, "err"),
                             daemon=True).start()

            try:
                self.conn, _ = self.listen.accept()
                self.conn.settimeout(30)
                self.rf = self.conn.makefile("rb")
                self.wf = self.conn.makefile("wb")
                hello = json.loads(self.rf.readline().decode("utf-8", "replace") or "{}")
                if not hello.get("hello"):
                    raise RuntimeError("Blender 服务握手失败（未收到 hello）")
                self.version = str(hello.get("blender") or "")
                self.conn.settimeout(None)
                _log(f"Blender 就绪：{self.version}（pid={self.proc.pid}, port={port}）")
                return {"version": self.version, "pid": self.proc.pid, "restarted": True}
            except Exception as e:
                tail = "\n".join(list(self._stderr)[-12:])
                self.stop()
                raise RuntimeError(
                    f"Blender 后台进程启动失败：{e}\n"
                    f"可执行文件：{exe}\n"
                    f"--- Blender 输出（末尾）---\n{tail or '（无输出）'}")

    def stop(self) -> None:
        for f in (self.rf, self.wf):
            try:
                f.close()
            except Exception:
                pass
        try:
            self.conn.close()
        except Exception:
            pass
        try:
            self.listen.close()
        except Exception:
            pass
        self.conn = self.rf = self.wf = self.listen = None
        p = self.proc
        self.proc = None
        if p is None:
            return
        try:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    p.kill()
        except Exception:
            pass
        self.version = ""

    @staticmethod
    def _drain(pipe, sink: deque, tag: str):
        try:
            for line in iter(pipe.readline, b""):
                text = line.decode("utf-8", "replace").rstrip()
                if not text:
                    continue
                sink.append(text)
                if tag == "err":
                    _log(f"blender: {text}")
        except Exception:
            pass
        finally:
            try:
                pipe.close()
            except Exception:
                pass

    # ---------- 请求 ----------

    def call(self, method: str, params: dict, timeout: float = 600) -> dict:
        with self._lock:
            if not self.alive():
                self.start()
            self._id += 1
            rid = self._id
            payload = json.dumps({"id": rid, "method": method, "params": params},
                                 ensure_ascii=False).encode("utf-8") + b"\n"
            try:
                self.conn.settimeout(timeout)
                self.wf.write(payload)
                self.wf.flush()
                line = self.rf.readline()
            except socket.timeout:
                self.stop()
                raise RuntimeError(
                    f"Blender 在 {timeout:.0f}s 内没有返回（操作可能过重）。后台实例已停止，"
                    f"请降低渲染分辨率/采样后重试，或先 blender_start 重新拉起。")
            except (OSError, ValueError) as e:
                tail = "\n".join(list(self._stderr)[-8:])
                self.stop()
                raise RuntimeError(f"与 Blender 的连接中断：{e}\n{tail}")
            if not line:
                tail = "\n".join(list(self._stderr)[-8:])
                self.stop()
                raise RuntimeError(f"Blender 进程已退出（场景丢失）。\n{tail}")
            resp = json.loads(line.decode("utf-8", "replace"))
            if not resp.get("ok"):
                raise RuntimeError(str(resp.get("error") or "Blender 未知错误"))
            result = resp.get("result")
            return result if isinstance(result, dict) else {"result": result}


BRIDGE = BlenderBridge()
atexit.register(lambda: BRIDGE.stop())


# ============================================================================
# 局域网预览页：浏览器（手机 / 同网段电脑）实时看渲染图，只读、不改场景
# ============================================================================

PREVIEW_BIND = (os.environ.get("BLENDER_PREVIEW_BIND") or "0.0.0.0").strip() or "0.0.0.0"
PREVIEW_PORT = int(os.environ.get("BLENDER_PREVIEW_PORT") or 8787)
# 页面展示的图片数量上限：只保留最近的渲染结果，避免目录长期使用后列表过重
PREVIEW_MAX_IMAGES = 60

_STATE = {"scene": None, "scene_at": 0.0}


def _remember_scene(result) -> None:
    """缓存最近一次场景摘要，供预览页展示（页面轮询不直接占用 Blender 调用锁）。"""
    if isinstance(result, dict) and isinstance(result.get("summary"), dict):
        _STATE["scene"] = result["summary"]
        _STATE["scene_at"] = time.time()


def _lan_ip() -> str:
    """取本机在局域网中的 IPv4：UDP connect 只做路由选择、不发包；失败再退回主机名解析。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                return ip
    except OSError:
        pass
    return "127.0.0.1"


def _preview_state() -> dict:
    """预览页数据：渲染实例状态 + 场景摘要（缓存）+ 输出目录里的 PNG 列表。"""
    out = output_dir()
    images = []
    try:
        files = [p for p in out.glob("*.png") if p.is_file()]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for p in files[:PREVIEW_MAX_IMAGES]:
            st = p.stat()
            images.append({"name": p.name, "url": f"img/{quote(p.name)}?v={int(st.st_mtime)}",
                           "bytes": st.st_size, "mtime": st.st_mtime})
    except OSError:
        pass
    info = PREVIEW.info()
    return {"running": BRIDGE.alive(), "blender": BRIDGE.version or "",
            "output_dir": str(out), "scene": _STATE.get("scene"),
            "images": images, "now": time.time(),
            "lan_url": info["url"] if info["running"] else "",
            "bind": info["bind"], "port": info["port"]}


_PREVIEW_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Blender 预览</title>
<style>
  :root{--bg:#05070A;--panel:#0E1116;--line:#1E242C;--fg:#D8DDE4;--dim:#7A828C;--ok:#7FB2E5}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 ui-monospace,SFMono-Regular,Consolas,"Courier New",monospace}
  header{position:sticky;top:0;z-index:2;background:rgba(5,7,10,.92);border-bottom:1px solid var(--line);padding:12px 16px;display:flex;flex-wrap:wrap;gap:10px;align-items:center}
  h1{font-size:14px;font-weight:600;margin:0;letter-spacing:.06em}
  .url{color:var(--dim);font-size:12px}
  .pill{margin-left:auto;border:1px solid var(--line);border-radius:999px;padding:2px 10px;font-size:12px;color:var(--dim)}
  .pill.on{color:var(--ok);border-color:#24486B}
  main{padding:14px 16px 40px;max-width:1400px;margin:0 auto}
  #stats{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px}
  .stat{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:6px 10px;min-width:96px}
  .stat b{display:block;font-size:11px;color:var(--dim);font-weight:400;letter-spacing:.04em}
  .stat span{font-size:15px}
  #grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:8px;overflow:hidden;display:block;color:inherit;text-decoration:none}
  .card img{display:block;width:100%;height:auto;background:#000}
  .cap{padding:8px 10px;border-top:1px solid var(--line);font-size:12px;color:var(--dim);display:flex;justify-content:space-between;gap:8px}
  .cap b{color:var(--fg);font-weight:500;word-break:break-all}
  #empty{color:var(--dim);padding:28px 0;text-align:center;border:1px dashed var(--line);border-radius:8px}
  #meta{color:var(--dim);font-size:12px;margin-top:16px}
</style></head>
<body>
<header><h1>ZHIZHU BLENDER PREVIEW</h1><span class="url" id="addr"></span>
<span class="pill" id="status">连接中</span></header>
<main><div id="stats"></div><div id="grid"></div>
<div id="empty">尚无渲染图：让 Agent 调用 render_image 后这里会自动出现。</div>
<div id="meta"></div></main>
<script>
(function(){
  var statusEl=document.getElementById('status'),statsEl=document.getElementById('stats'),
      gridEl=document.getElementById('grid'),emptyEl=document.getElementById('empty'),
      metaEl=document.getElementById('meta'),addrEl=document.getElementById('addr');
  var sig='';
  function pad(n){return n<10?'0'+n:''+n;}
  function timeText(s){var d=new Date(s*1000);return pad(d.getHours())+':'+pad(d.getMinutes())+':'+pad(d.getSeconds());}
  function sizeText(n){return n>1048576?(n/1048576).toFixed(1)+' MB':Math.max(1,Math.round(n/1024))+' KB';}
  function stat(label,value){
    var d=document.createElement('div');d.className='stat';
    var b=document.createElement('b');b.textContent=label;
    var s=document.createElement('span');
    s.textContent=(value===undefined||value===null||value==='')?'—':String(value);
    d.appendChild(b);d.appendChild(s);return d;
  }
  function applyStats(sc){
    var res=(sc&&sc.resolution)||[];
    var rows=[['对象',sc&&sc.objects],['三角面',sc&&sc.triangles],['材质',sc&&sc.materials],
      ['引擎',sc&&sc.engine],['分辨率',res.length?res[0]+' x '+res[1]:''],['相机',sc&&sc.camera]];
    statsEl.textContent='';
    rows.forEach(function(r){statsEl.appendChild(stat(r[0],r[1]));});
  }
  function applyGrid(images){
    var next=images.map(function(i){return i.name+'@'+i.bytes+'@'+i.mtime;}).join('|');
    if(next===sig){return;}
    sig=next;gridEl.textContent='';
    emptyEl.style.display=images.length?'none':'block';
    images.forEach(function(it){
      var a=document.createElement('a');a.className='card';a.href=it.url;
      a.target='_blank';a.rel='noopener';
      var img=document.createElement('img');img.src=it.url;img.alt=it.name;img.loading='lazy';
      var cap=document.createElement('div');cap.className='cap';
      var name=document.createElement('b');name.textContent=it.name;
      var info=document.createElement('span');info.textContent=timeText(it.mtime)+' · '+sizeText(it.bytes);
      cap.appendChild(name);cap.appendChild(info);
      a.appendChild(img);a.appendChild(cap);gridEl.appendChild(a);
    });
  }
  function tick(){
    fetch('api/state',{cache:'no-store'}).then(function(r){return r.json();}).then(function(s){
      statusEl.textContent=s.running?'渲染实例运行中':'渲染实例未启动';
      statusEl.className='pill'+(s.running?' on':'');
      addrEl.textContent=s.lan_url||'';
      applyStats(s.scene);applyGrid(s.images||[]);
      metaEl.textContent='最近刷新 '+timeText(s.now)+' · 输出目录 '+s.output_dir
        +' · 共 '+(s.images||[]).length+' 张（最近 60 张）';
    }).catch(function(){
      statusEl.textContent='连接中断';statusEl.className='pill';
    });
  }
  tick();setInterval(tick,2000);
})();
</script></body></html>"""


class PreviewServer:
    """只读 HTTP 预览服务（ThreadingHTTPServer，守护线程）。"""

    def __init__(self):
        self.httpd = None
        self.thread = None
        self.port = 0
        self.bind = ""
        self.lan = ""

    def running(self) -> bool:
        return bool(self.httpd is not None and self.thread is not None and self.thread.is_alive())

    def start(self, port=None, bind=None) -> dict:
        if self.running():
            return self.info()
        bind = str(bind or PREVIEW_BIND).strip() or "0.0.0.0"
        port = PREVIEW_PORT if port in (None, "") else int(port)
        try:
            httpd = ThreadingHTTPServer((bind, port), _preview_handler())
        except OSError as e:
            raise RuntimeError(f"预览页启动失败（{bind}:{port}）：{e}")
        httpd.daemon_threads = True
        self.httpd, self.bind = httpd, bind
        self.port = int(httpd.server_address[1])
        self.lan = _lan_ip()
        self.thread = threading.Thread(target=httpd.serve_forever,
                                       kwargs={"poll_interval": 0.3}, daemon=True)
        self.thread.start()
        _log(f"预览页已启动：{self.info()['url']}")
        return self.info()

    def stop(self) -> bool:
        if self.httpd is None:
            return False
        try:
            self.httpd.shutdown()
        except Exception:
            pass
        try:
            self.httpd.server_close()
        except Exception:
            pass
        self.httpd = self.thread = None
        return True

    def info(self) -> dict:
        port = self.port or PREVIEW_PORT
        return {"running": self.running(), "bind": self.bind or PREVIEW_BIND, "port": port,
                "lan_ip": self.lan or "127.0.0.1",
                "local_url": f"http://127.0.0.1:{port}/",
                "url": f"http://{self.lan or '127.0.0.1'}:{port}/"}


PREVIEW = PreviewServer()
atexit.register(lambda: PREVIEW.stop())


def _preview_handler():
    """按请求构造 handler；`/` 页面、`/api/state` 数据、`/img/<name>` 图片，其余 404。"""

    class Handler(BaseHTTPRequestHandler):
        server_version = "zhizhu-blender-preview/1.0"

        def log_message(self, fmt, *args):   # 静默：HTTP 访问日志不淹 stderr
            pass

        def _send(self, code: int, body: bytes, ctype: str, cache: str = "no-store"):
            try:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", cache)
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            try:
                path = unquote(urlparse(self.path).path)
                if path in ("/", "/index.html"):
                    self._send(200, _PREVIEW_HTML.encode("utf-8"), "text/html; charset=utf-8")
                elif path == "/api/state":
                    body = json.dumps(_preview_state(), ensure_ascii=False).encode("utf-8")
                    self._send(200, body, "application/json; charset=utf-8")
                elif path.startswith("/img/"):
                    self._image(path[len("/img/"):])
                else:
                    self._send(404, b"not found", "text/plain; charset=utf-8")
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _image(self, name: str):
            # 只允许输出目录下的单层 .png 文件名，杜绝 ../ 穿越
            if (not name or "/" in name or "\\" in name or name.startswith(".")
                    or not name.lower().endswith(".png")):
                self._send(404, b"not found", "text/plain; charset=utf-8")
                return
            p = output_dir() / name
            try:
                if not p.is_file():
                    raise OSError("missing")
                body = p.read_bytes()
            except OSError:
                self._send(404, b"not found", "text/plain; charset=utf-8")
                return
            self._send(200, body, "image/png", cache="public, max-age=86400")

    return Handler


# ============================================================================
# 结果格式化
# ============================================================================

def _pretty(result: dict) -> str:
    if not isinstance(result, dict):
        return str(result)
    head = str(result.get("message") or "").strip()
    rest = {k: v for k, v in result.items() if k != "message"}
    body = json.dumps(rest, ensure_ascii=False, indent=2) if rest else ""
    return (head + ("\n" if head and body else "") + body).strip()


def _call(method: str, args: dict, timeout: float = 300) -> str:
    return _pretty(BRIDGE.call(method, args, timeout=timeout))


# ============================================================================
# 工具实现
# ============================================================================

def tool_status(args):
    try:
        exe = find_blender()
        found, ok = f"Blender 已找到：{exe}", True
    except Exception as e:
        found, ok = f"未找到可用的 Blender：{e}", False
    if args.get("auto_start") and ok:
        try:
            BRIDGE.start()
        except Exception as e:
            return f"{found}\n启动失败：{e}"
    if BRIDGE.alive():
        info = BRIDGE.call("status", {})
        _remember_scene(BRIDGE.call("scene_info", {"detail": "brief"}))
        state = f"后台实例运行中（版本 {BRIDGE.version or info.get('blender')}，pid={BRIDGE.proc.pid}）\n" + _pretty(info)
    else:
        state = "后台实例未启动（首次建模调用会自动拉起，也可先 blender_start）"
    preview = PREVIEW.info()
    page = (f"局域网预览页：{preview['url']}（手机同网段可看，2 秒自动刷新）"
            if preview["running"] else "局域网预览页未启动（preview_start 可开启）")
    return f"{found}\n{state}\n输出目录：{output_dir()}\n{page}"


def tool_start(args):
    info = BRIDGE.start(restart=bool(args.get("restart")))
    summary = BRIDGE.call("status", {})
    return f"Blender 后台实例已就绪（版本 {info.get('version') or '?'}，pid={info['pid']}）\n{_pretty(summary)}"


def tool_stop(args):
    was = BRIDGE.alive()
    BRIDGE.stop()
    return "已停止 Blender 后台实例" if was else "后台实例本来就没有运行"


def tool_scene_info(args):
    result = BRIDGE.call("scene_info", args)
    _remember_scene(result)
    return _pretty(result)


def tool_preview_start(args):
    info = PREVIEW.start(args.get("port"), args.get("bind"))
    return ("局域网预览页已启动（只读：不占用、不改动 Blender 场景）。\n"
            f"本机打开：{info['local_url']}\n"
            f"局域网打开：{info['url']}\n"
            f"监听地址：{info['bind']}:{info['port']}\n"
            f"页面每 2 秒拉取一次 /api/state，显示输出目录（{output_dir()}）里最新的渲染图与场景摘要；"
            "手机连同一 Wi-Fi 打开局域网地址即可。\n"
            "若 Windows 首次弹出防火墙提示，请勾选“专用网络”并允许。")


def tool_preview_stop(args):
    return "预览页已关闭" if PREVIEW.stop() else "预览页本来就没有运行"


def tool_render(args):
    result = BRIDGE.call("render_image", args, timeout=1800)
    # 渲染成功后顺带刷新场景摘要：预览页轮询时只读缓存，不再打扰 Blender
    try:
        _remember_scene(BRIDGE.call("scene_info", {"detail": "brief"}))
    except Exception:
        pass
    text = _pretty(result)
    content = [{"type": "text", "text": text}]
    p = str(result.get("path") or "")
    if p and os.path.isfile(p):
        try:
            with open(p, "rb") as f:
                content.append({"type": "image",
                                "data": base64.b64encode(f.read()).decode(),
                                "mimeType": "image/png"})
        except OSError as e:
            text += f"\n（读取渲染图失败：{e}）"
            content[0]["text"] = text
    return content


def _forward(method, timeout=300):
    def run(args):
        return _call(method, args, timeout=timeout)
    return run


_HANDLERS = {
    "blender_status": tool_status,
    "blender_start": tool_start,
    "blender_stop": tool_stop,
    "preview_start": tool_preview_start,
    "preview_stop": tool_preview_stop,
    "scene_new": _forward("scene_new"),
    "scene_info": tool_scene_info,
    "scene_save": _forward("scene_save"),
    "scene_open": _forward("scene_open", 600),
    "object_add": _forward("object_add"),
    "object_transform": _forward("object_transform"),
    "object_duplicate": _forward("object_duplicate"),
    "object_delete": _forward("object_delete"),
    "object_join": _forward("object_join"),
    "object_shade": _forward("object_shade"),
    "object_modifier": _forward("object_modifier"),
    "object_boolean": _forward("object_boolean", 600),
    "material_create": _forward("material_create"),
    "material_assign": _forward("material_assign"),
    "text_add": _forward("text_add"),
    "camera_add": _forward("camera_add"),
    "camera_frame": _forward("camera_frame"),
    "light_add": _forward("light_add"),
    "light_rig": _forward("light_rig"),
    "world_set": _forward("world_set"),
    "render_image": tool_render,
    "export_model": _forward("export_model", 900),
    "template_list": _forward("template_list"),
    "template_apply": _forward("template_apply", 600),
}


# ============================================================================
# MCP 工具表
# ============================================================================

def _schema(props: dict, required=None):
    return {"type": "object", "properties": props, "required": list(required or [])}


_VEC = {"type": "array", "items": {"type": "number"}, "description": "[x, y, z]"}
_NAMES = {"type": "array", "items": {"type": "string"}, "description": "对象名列表"}
_COLOR = {"type": "string", "description": "#RRGGBB 十六进制颜色"}

TOOLS = [
    {"name": "blender_status", "description":
        "查看 Blender 可用性、后台实例状态、当前场景摘要（对象/三角面/材质/相机/分辨率）与输出目录。"
        "只读；auto_start=true 时顺带拉起后台实例。开始建模前先调用它确认环境。",
     "inputSchema": _schema({"auto_start": {"type": "boolean", "description": "是否顺带启动后台 Blender（默认 false）"}})},
    {"name": "blender_start", "description":
        "拉起（或重启）常驻 Blender 后台实例。首次建模调用会自动启动，通常不需要手动调用；"
        "restart=true 会丢弃当前场景重新拉起。",
     "inputSchema": _schema({"restart": {"type": "boolean", "description": "是否强制重启（会丢失未保存场景）"}})},
    {"name": "blender_stop", "description": "停止常驻 Blender 后台实例并释放内存。",
     "inputSchema": _schema({})},
    {"name": "preview_start", "description":
        "开启局域网预览页（只读 HTTP 服务）：手机/同网段电脑用浏览器打开返回的地址，"
        "即可每 2 秒看到输出目录里最新的渲染图和场景摘要（对象/三角面/材质/引擎/分辨率）。"
        "不占用 Blender、不改场景；默认监听 0.0.0.0:8787。",
     "inputSchema": _schema({
         "port": {"type": "integer", "description": "端口，默认 8787（0 表示系统自动分配）"},
         "bind": {"type": "string", "description": "监听地址，默认 0.0.0.0（局域网可访问）"}})},
    {"name": "preview_stop", "description": "关闭局域网预览页（不影响 Blender 与已渲染的图）。",
     "inputSchema": _schema({})},
    {"name": "scene_new", "description":
        "新建空场景并设置世界背景（默认纯黑）与单位比例。建模任务的第一步。",
     "inputSchema": _schema({
         "background_color": _COLOR, "strength": {"type": "number", "description": "世界背景强度，默认 1.0"},
         "unit_scale": {"type": "number", "description": "单位比例，默认 1.0（1 单位=1 米）"}})},
    {"name": "scene_info", "description":
        "读取当前场景：对象列表（位置/旋转/缩放/顶点/面/材质）、包围盒、相机、灯光、渲染设置。"
        "建模过程中用它核对尺寸与命名，不要靠记忆。",
     "inputSchema": _schema({"detail": {"type": "string", "enum": ["brief", "full"],
                                        "description": "brief=概要（默认）；full=含尺寸/修改器/集合"}})},
    {"name": "scene_save", "description":
        "保存当前场景为 .blend（相对路径落在输出目录，绝对路径原样使用）。",
     "inputSchema": _schema({
         "name": {"type": "string", "description": "文件名（不含扩展名），默认用场景名"},
         "path": {"type": "string", "description": "自定义保存路径（可选）"},
         "compress": {"type": "boolean", "description": "是否压缩，默认 true"}})},
    {"name": "scene_open", "description":
        "打开已有 .blend 场景（相对路径按输出目录解析）。",
     "inputSchema": _schema({"path": {"type": "string", "description": ".blend 路径"}}, ["path"])},
    {"name": "object_add", "description":
        "创建低模基础体：cube / uv_sphere / ico_sphere / cylinder / cone / torus / plane / capsule / text / empty。"
        "尺寸单位是米，Z 轴朝上；size 是整体最大尺寸（球=直径，立方体=边长，圆柱=高度/直径）。"
        "低模风格建议：球体 segments 12~16、圆柱 vertices 12~16，shade=flat，边角加 bevel。",
     "inputSchema": _schema({
         "kind": {"type": "string", "enum": ["cube", "uv_sphere", "ico_sphere", "cylinder", "cone",
                                             "torus", "plane", "capsule", "text", "empty"]},
         "name": {"type": "string", "description": "对象名（后续工具用它引用）"},
         "location": _VEC, "rotation": {"type": "array", "items": {"type": "number"},
                                        "description": "欧拉角（度）[rx, ry, rz]"},
         "scale": _VEC, "size": {"type": "number", "description": "整体尺寸，默认 1.0"},
         "segments": {"type": "integer", "description": "球/圆柱/圆环分段，低模用 12~16"},
         "vertices": {"type": "integer", "description": "圆柱/圆锥边数（可选）"},
         "shade": {"type": "string", "enum": ["flat", "smooth", "auto"], "description": "着色方式，低模默认 flat"},
         "bevel": {"type": "number", "description": "倒角宽度（米），0 表示不倒角"},
         "material": {"type": "string", "description": "已有材质名（可选）"},
         "color": _COLOR, "collection": {"type": "string", "description": "集合名（不存在会自动创建）"},
         "body": {"type": "string", "description": "kind=text 时的文字内容"},
         "align": {"type": "string", "enum": ["LEFT", "CENTER", "RIGHT"], "description": "文字对齐，默认 LEFT"}})},
    {"name": "object_transform", "description":
        "批量移动/旋转/缩放对象。relative=true 时按增量叠加（location/delta 相加、scale 相乘、rotation 相加）。",
     "inputSchema": _schema({
         "names": _NAMES, "location": _VEC,
         "rotation": {"type": "array", "items": {"type": "number"}, "description": "欧拉角（度）[rx, ry, rz]"},
         "scale": _VEC, "delta": _VEC,
         "relative": {"type": "boolean", "description": "是否增量叠加，默认 false"}}, ["names"])},
    {"name": "object_duplicate", "description":
        "复制对象（含网格与材质），可沿 offset 复制多份，或用 mirror=X/Y/Z 生成镜像副本（桌宠左右肢体的标准做法）。",
     "inputSchema": _schema({
         "name": {"type": "string", "description": "源对象名"},
         "new_name": {"type": "string", "description": "新对象基础名"},
         "offset": _VEC, "count": {"type": "integer", "description": "复制份数，默认 1"},
         "mirror": {"type": "string", "enum": ["X", "Y", "Z"], "description": "镜像轴（可选）"}}, ["name"])},
    {"name": "object_delete", "description": "删除一个或多个对象。",
     "inputSchema": _schema({"names": _NAMES}, ["names"])},
    {"name": "object_join", "description":
        "把多个对象合并成一个网格（保留首个对象的材质槽）。低模交付前常用：把桌宠各部件合成单体，或把芯片引脚合成一块。",
     "inputSchema": _schema({"names": _NAMES,
                             "name": {"type": "string", "description": "合并后的对象名（可选）"}}, ["names"])},
    {"name": "object_shade", "description":
        "设置着色：flat=平面低模；smooth=平滑；auto=按角度自动平滑（angle 默认 30 度）。",
     "inputSchema": _schema({
         "names": _NAMES, "mode": {"type": "string", "enum": ["flat", "smooth", "auto"]},
         "angle": {"type": "number", "description": "auto 模式的角度阈值（度），默认 30"}}, ["names"])},
    {"name": "object_modifier", "description":
        "添加/应用修改器：bevel（倒角，低模硬表面必用）/ solidify（加厚）/ array（阵列）/ "
        "mirror（镜像）/ weighted_normal（硬表面法线）/ triangulate / subsurf。apply=true 立即烘焙。",
     "inputSchema": _schema({
         "name": {"type": "string", "description": "对象名"},
         "modifier": {"type": "string", "enum": ["bevel", "solidify", "array", "mirror",
                                                 "weighted_normal", "triangulate", "subsurf"]},
         "width": {"type": "number", "description": "bevel 宽度（米）"},
         "segments": {"type": "integer", "description": "bevel 分段，默认 2"},
         "angle": {"type": "number", "description": "bevel 角度阈值（度），默认 30"},
         "thickness": {"type": "number", "description": "solidify 厚度"},
         "offset": _VEC, "count": {"type": "integer", "description": "array 数量"},
         "axis": {"type": "string", "enum": ["X", "Y", "Z"], "description": "mirror 轴"},
         "levels": {"type": "integer", "description": "subsurf 级别"},
         "apply": {"type": "boolean", "description": "是否立即应用，默认 false"}}, ["name", "modifier"])},
    {"name": "object_boolean", "description":
        "布尔运算：target 与 operand 做 DIFFERENCE / UNION / INTERSECT（默认相减并删除 operand），"
        "用于电路板开孔、零件挖槽。apply 默认 true。",
     "inputSchema": _schema({
         "target": {"type": "string", "description": "被修改的对象"},
         "operand": {"type": "string", "description": "参与运算的对象"},
         "op": {"type": "string", "enum": ["DIFFERENCE", "UNION", "INTERSECT"]},
         "apply": {"type": "boolean", "description": "是否立即应用，默认 true"},
         "delete_operand": {"type": "boolean", "description": "应用后是否删除 operand，默认 true"}},
         ["target", "operand"])},
    {"name": "material_create", "description":
        "创建/更新 PBR 材质：base_color（#RRGGBB）、metallic、roughness、自发光、透明度。"
        "低模桌宠用 roughness 0.55~0.7 的哑光；电路板金手指用 metallic 1.0 / roughness 0.25~0.35。",
     "inputSchema": _schema({
         "name": {"type": "string", "description": "材质名"},
         "base_color": _COLOR,
         "metallic": {"type": "number", "description": "金属度 0~1，默认 0"},
         "roughness": {"type": "number", "description": "粗糙度 0~1，默认 0.5"},
         "emission_color": _COLOR, "emission_strength": {"type": "number", "description": "自发光强度"},
         "alpha": {"type": "number", "description": "透明度 0~1，默认 1"}}, ["name"])},
    {"name": "material_assign", "description": "把已有材质分配给一个或多个对象。",
     "inputSchema": _schema({"targets": _NAMES, "material": {"type": "string", "description": "材质名"}},
                             ["targets", "material"])},
    {"name": "text_add", "description":
        "添加文字（自动转网格，导出 glTF/FBX 不会丢）：用于电路板丝印 U1/R1/C1、产品铭牌。"
        "文字默认躺在 XY 平面上（正面朝 +Z）。",
     "inputSchema": _schema({
         "body": {"type": "string", "description": "文字内容"},
         "name": {"type": "string", "description": "对象名（可选）"},
         "location": _VEC, "rotation": {"type": "array", "items": {"type": "number"},
                                        "description": "欧拉角（度）"},
         "size": {"type": "number", "description": "字高（米），默认 0.2"},
         "extrude": {"type": "number", "description": "加厚厚度（米），默认 0"},
         "material": {"type": "string", "description": "已有材质名"}, "color": _COLOR,
         "align": {"type": "string", "enum": ["LEFT", "CENTER", "RIGHT"]}}, ["body"])},
    {"name": "camera_add", "description": "创建相机并可选看向 look_at 点；set_active 默认设为当前渲染相机。",
     "inputSchema": _schema({
         "name": {"type": "string", "description": "相机名，默认 Camera"},
         "location": _VEC, "look_at": _VEC,
         "lens": {"type": "number", "description": "焦距 mm，默认 50"},
         "ortho": {"type": "boolean", "description": "是否正交"},
         "ortho_scale": {"type": "number", "description": "正交视野尺寸"}})},
    {"name": "camera_frame", "description":
        "自动取景：按当前所有网格的包围盒把相机摆到指定视角并看向中心。"
        "view=hero/iso/front/back/left/right/top/bottom。渲染前一步调用最省事。",
     "inputSchema": _schema({
         "view": {"type": "string", "enum": ["hero", "iso", "front", "back", "left", "right", "top", "bottom"]},
         "lens": {"type": "number", "description": "焦距 mm，默认 50"},
         "margin": {"type": "number", "description": "留白系数，默认 1.25（越大越远）"},
         "ortho": {"type": "boolean", "description": "是否正交"},
         "camera": {"type": "string", "description": "指定相机名（可选）"}})},
    {"name": "light_add", "description":
        "添加灯光：AREA（柔光箱，产品渲染首选）/ SUN / POINT / SPOT。energy 单位瓦，area 用 size 控制柔硬。",
     "inputSchema": _schema({
         "kind": {"type": "string", "enum": ["AREA", "SUN", "POINT", "SPOT"]},
         "name": {"type": "string", "description": "灯光名（可选）"},
         "location": _VEC, "look_at": _VEC,
         "energy": {"type": "number", "description": "功率（瓦），默认 400"},
         "color": _COLOR, "size": {"type": "number", "description": "AREA 尺寸（米），默认 2"},
         "angle": {"type": "number", "description": "SPOT 张角（度）"}})},
    {"name": "light_rig", "description":
        "一键三点布光（含深色世界背景与哑光地面）：preset=product（柔和产品图）/ dramatic（硬边高对比）。"
        "按当前场景包围盒自动缩放灯位与功率。",
     "inputSchema": _schema({
         "preset": {"type": "string", "description": "灯光预设名（内置 product / dramatic），默认 product"},
         "scale": {"type": "number", "description": "灯位整体缩放，默认 1.0"},
         "center": _VEC})},
    {"name": "world_set", "description": "设置世界背景颜色与强度（默认纯黑，符合简约交付风格）。",
     "inputSchema": _schema({"color": _COLOR, "strength": {"type": "number", "description": "强度，默认 1.0"}})},
    {"name": "render_image", "description":
        "渲染当前场景为 PNG 并返回图片给你查看。默认 CYCLES / 960px / 32 采样（CPU，约十几秒）。"
        "view=hero 会自动取景；引擎可选 CYCLES / BLENDER_EEVEE / BLENDER_WORKBENCH。"
        "改完模型/材质后必须重新渲染确认，不要凭想象交付。",
     "inputSchema": _schema({
         "name": {"type": "string", "description": "输出文件名（不含扩展名）"},
         "path": {"type": "string", "description": "自定义输出路径（可选）"},
         "view": {"type": "string", "enum": ["hero", "iso", "front", "back", "left", "right",
                                             "top", "bottom", "current"], "description": "取景视角，默认 hero"},
         "camera": {"type": "string", "description": "指定相机（可选）"},
         "lens": {"type": "number", "description": "焦距 mm，默认 50"},
         "margin": {"type": "number", "description": "取景留白，默认 1.25"},
         "resolution": {"type": "integer", "description": "正方形边长，默认 960（也可传 [w,h]）"},
         "samples": {"type": "integer", "description": f"采样数，默认 {DEFAULT_SAMPLES}"},
         "engine": {"type": "string", "enum": ["CYCLES", "BLENDER_EEVEE", "BLENDER_WORKBENCH"],
                    "description": f"渲染引擎，默认 {DEFAULT_ENGINE}"},
         "transparent": {"type": "boolean", "description": "透明背景（PNG RGBA）"},
         "denoise": {"type": "boolean", "description": "Cycles 降噪，默认 true"}})},
    {"name": "export_model", "description":
        "导出模型：glb（推荐，含材质）/ fbx / obj / stl。selected_only=true 只导出选中对象。",
     "inputSchema": _schema({
         "name": {"type": "string", "description": "输出文件名（不含扩展名）"},
         "format": {"type": "string", "enum": ["glb", "fbx", "obj", "stl"], "description": "默认 glb"},
         "path": {"type": "string", "description": "自定义输出路径"},
         "selected_only": {"type": "boolean", "description": "只导出选中对象，默认 false"}})},
    {"name": "template_list", "description":
        "列出内置模板（桌宠 pet / 电路板 pcb）及其可调参数与风格。新建模板只需在 presets/ 增加 JSON。",
     "inputSchema": _schema({})},
    {"name": "template_apply", "description":
        "套用内置模板：pet（Q 版桌宠）/ pcb（电路板实物）。"
        "模板数据驱动（presets/<name>.json），支持 size 缩放与 colors 覆盖调色板；"
        "style 取该模板 styles 里的键（内置 pet: cat/bear/rabbit）。生成后可用 object_* / 材质工具继续改。",
     "inputSchema": _schema({
         "template": {"type": "string", "description": "模板名（内置 pet / pcb；presets/ 下的 JSON 文件名即模板名）"},
         "style": {"type": "string", "description": "风格（pet: cat/bear/rabbit）"},
         "size": {"type": "number", "description": "整体缩放，默认 1.0"},
         "colors": {"type": "object", "description": "覆盖调色板，例如 {\"body\":\"#8899AA\"}"},
         "prefix": {"type": "string", "description": "对象名前缀（多只桌宠用）"}}, ["template"])},
]


# ============================================================================
# MCP 分发
# ============================================================================

def _handle(msg: dict):
    method = msg.get("method", "")
    params = msg.get("params", {}) or {}
    rid = msg.get("id")
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
        name = params.get("name", "")
        fn = _HANDLERS.get(name)
        if fn is None:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32601, "message": f"未知工具: {name}"}}
        try:
            out = fn(params.get("arguments", {}) or {})
            content = out if isinstance(out, list) else [{"type": "text", "text": str(out)}]
            result = {"content": content, "isError": False}
        except Exception as e:
            result = {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}],
                      "isError": True}
        return {"jsonrpc": "2.0", "id": rid, "result": result}
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": f"未知方法: {method}"}}


def main():
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    for line in stdin:
        if not line.strip():
            continue
        try:
            msg = json.loads(line.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            continue
        resp = _handle(msg)
        if resp is not None:
            stdout.write(json.dumps(resp, ensure_ascii=False).encode("utf-8") + b"\n")
            stdout.flush()


# ---------- 离线自检：真实拉起 Blender 并完成一次建模 + 渲染 ----------

def _selftest() -> int:
    out = {"ok": False, "tools": [t["name"] for t in TOOLS], "steps": {}}
    try:
        info = BRIDGE.start()
        out["steps"]["start"] = {"version": info.get("version"), "pid": info.get("pid")}
        BRIDGE.call("scene_new", {"background_color": "#05070A"})
        BRIDGE.call("object_add", {"kind": "cube", "name": "SelfTestCube", "size": 1.0,
                                   "location": [0, 0, 0.5], "bevel": 0.03, "color": "#8FA3B8"})
        BRIDGE.call("light_rig", {"preset": "product"})
        BRIDGE.call("camera_frame", {"view": "hero"})
        r = BRIDGE.call("render_image", {"name": "selftest", "resolution": 320, "samples": 8},
                        timeout=600)
        out["steps"]["render"] = {"path": r.get("path"), "bytes": r.get("bytes"),
                                  "seconds": r.get("seconds")}
        out["steps"]["templates"] = [t["template"] for t in BRIDGE.call("template_list", {})["templates"]]
        out["ok"] = os.path.isfile(str(r.get("path") or ""))
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    finally:
        BRIDGE.stop()
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    main()
