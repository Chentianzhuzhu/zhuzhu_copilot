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
  BLENDER_RENDER_ENGINE / BLENDER_START_TIMEOUT / BLENDER_IDLE_TIMEOUT；
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
from pathlib import Path

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
        state = f"后台实例运行中（版本 {BRIDGE.version or info.get('blender')}，pid={BRIDGE.proc.pid}）\n" + _pretty(info)
    else:
        state = "后台实例未启动（首次建模调用会自动拉起，也可先 blender_start）"
    return f"{found}\n{state}\n输出目录：{output_dir()}"


def tool_start(args):
    info = BRIDGE.start(restart=bool(args.get("restart")))
    summary = BRIDGE.call("status", {})
    return f"Blender 后台实例已就绪（版本 {info.get('version') or '?'}，pid={info['pid']}）\n{_pretty(summary)}"


def tool_stop(args):
    was = BRIDGE.alive()
    BRIDGE.stop()
    return "已停止 Blender 后台实例" if was else "后台实例本来就没有运行"


def tool_render(args):
    result = BRIDGE.call("render_image", args, timeout=1800)
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
    "scene_new": _forward("scene_new"),
    "scene_info": _forward("scene_info"),
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
