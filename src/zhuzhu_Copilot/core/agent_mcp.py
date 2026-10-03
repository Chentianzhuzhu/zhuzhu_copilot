"""MCP（Model Context Protocol）客户端：支持 stdio 与 SSE 两种传输

- stdio：子进程 + newline-delimited JSON-RPC（tools/list、tools/call）
- SSE：HTTP GET /sse 建立事件流，POST /messages 发请求
- 多服务器聚合：tools 按 server 前缀去重命名，供 LLM function calling 使用
"""

import json
import os
import queue
import subprocess
import sys
import threading
import urllib.request
import urllib.error

MCP_VERSION = "2024-11-05"
_REQ_TIMEOUT = 30


def _safe_stdio_command(config: dict) -> str:
    """frozen 打包模式下，MCP stdio 命令若指向应用自身 exe（旧配置遗留/误写），
    subprocess 会递归拉起应用导致「无限打开程序自己」。此时若 args[0] 是 .py 脚本，
    改用真实 Python 解释器（沙盒/内置运行时）运行；其它命令原样返回。
    命中判定：命令与当前 exe 同路径，或同名（同名不同安装位置，如从 dist 直跑
    而配置指向安装目录的 exe），均视为应用自身。"""
    command = config.get("command", "")
    if not command or not getattr(sys, "frozen", False):
        return command
    args = config.get("args") or []
    if not (args and str(args[0]).lower().endswith(".py")):
        return command
    try:
        self_exe = os.path.normcase(os.path.realpath(sys.executable))
        cmd = os.path.normcase(os.path.realpath(command))
    except Exception:
        return command
    if cmd != self_exe and os.path.basename(cmd).lower() != os.path.basename(self_exe).lower():
        return command
    try:
        from zhuzhu_Copilot.core import agent_runtime
        return agent_runtime.python_interpreter()
    except Exception:
        return "python"


class McpError(Exception):
    pass


class _StdioTransport:
    def __init__(self, command: str, args: list):
        # CREATE_NO_WINDOW：禁止子进程弹出控制台窗口（否则连接时会闪一个黑色终端）
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            self._proc = subprocess.Popen(
                [command, *args], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=creationflags)
        except Exception as e:
            raise McpError(f"启动 MCP 服务器失败: {e}")
        self._pending: dict = {}
        self._lock = threading.Lock()
        self._id = 0
        threading.Thread(target=self._read_loop, daemon=True).start()

    def _read_loop(self):
        try:
            for line in self._proc.stdout:
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line.decode("utf-8", "replace"))
                except json.JSONDecodeError:
                    continue
                if isinstance(msg, dict) and "id" in msg:
                    q = self._pending.pop(msg["id"], None)
                    if q:
                        q.put(msg)
        except Exception:
            pass

    def request(self, method: str, params: dict) -> dict:
        with self._lock:
            self._id += 1
            rid = self._id
            q = queue.Queue()
            self._pending[rid] = q
        payload = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
        try:
            self._proc.stdin.write(json.dumps(payload).encode() + b"\n")
            self._proc.stdin.flush()
        except Exception as e:
            raise McpError(f"MCP 发送失败: {e}")
        try:
            resp = q.get(timeout=_REQ_TIMEOUT)
        except queue.Empty:
            raise McpError(f"MCP 请求超时: {method}")
        if "error" in resp:
            raise McpError(f"MCP 错误: {resp['error']}")
        return resp.get("result", {})

    def close(self):
        try:
            self._proc.terminate()
        except Exception:
            pass


class _SSETransport:
    def __init__(self, url: str):
        self._url = url
        self._messages_url = None
        self._pending: dict = {}
        self._lock = threading.Lock()
        self._id = 0
        self._ready = threading.Event()
        threading.Thread(target=self._read_loop, daemon=True).start()
        if not self._ready.wait(timeout=_REQ_TIMEOUT):
            raise McpError(f"MCP SSE 初始化超时: {url}")

    def _read_loop(self):
        try:
            resp = urllib.request.urlopen(self._url, timeout=_REQ_TIMEOUT)
            buf = ""
            for raw in resp:
                buf += raw.decode("utf-8", "replace")
                while "\n\n" in buf:
                    chunk, buf = buf.split("\n\n", 1)
                    for line in chunk.splitlines():
                        if not line.startswith("data:"):
                            continue
                        data = line[len("data:"):].strip()
                        if not data:
                            continue
                        try:
                            obj = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(obj, dict) and obj.get("endpoint"):
                            self._messages_url = obj["endpoint"]
                            self._ready.set()
                            continue
                        if isinstance(obj, dict) and "id" in obj:
                            q = self._pending.pop(obj["id"], None)
                            if q:
                                q.put(obj)
        except Exception:
            pass
        finally:
            self._ready.set()

    def request(self, method: str, params: dict) -> dict:
        if not self._messages_url:
            raise McpError("MCP SSE 未获得 messages 端点")
        with self._lock:
            self._id += 1
            rid = self._id
            q = queue.Queue()
            self._pending[rid] = q
        body = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method,
                           "params": params}).encode()
        req = urllib.request.Request(
            self._messages_url, data=body,
            headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=_REQ_TIMEOUT)
        except urllib.error.HTTPError as e:
            raise McpError(f"MCP POST 失败: {e.code}")
        except urllib.error.URLError as e:
            raise McpError(f"MCP POST 网络错误: {e.reason}")
        try:
            resp = q.get(timeout=_REQ_TIMEOUT)
        except queue.Empty:
            raise McpError(f"MCP 请求超时: {method}")
        if "error" in resp:
            raise McpError(f"MCP 错误: {resp['error']}")
        return resp.get("result", {})

    def close(self):
        pass


class McpClient:
    """单个 MCP 服务器连接：stdio 或 sse"""

    def __init__(self, name: str, config: dict):
        self.name = name
        self._transport = None
        self._used: set = set()      # 已用的原始工具名（避免跨服务器重名）
        self._call_map: dict = {}    # schema 工具名 -> MCP 原始工具名
        typ = config.get("type", "stdio")
        if typ == "stdio":
            self._transport = _StdioTransport(_safe_stdio_command(config),
                                              config.get("args", []))
        elif typ == "sse":
            self._transport = _SSETransport(config["url"])
        else:
            raise McpError(f"不支持的 MCP 传输类型: {typ}")
        self._init()

    def _init(self):
        self._transport.request("initialize", {
            "protocolVersion": MCP_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "winapp-migrator", "version": "1.0"},
        })
        # notifications/initialized（无 id 的通知，直接发送即可）
        try:
            self._transport.request("notifications/initialized", {})
        except McpError:
            pass

    def list_tools(self) -> list:
        """返回 OpenAI function schema 列表"""
        result = self._transport.request("tools/list", {})
        out = []
        for t in result.get("tools", []):
            raw = t.get("name", "")
            fname = raw if raw not in self._used else f"{self.name}_{raw}"
            self._used.add(raw)
            self._call_map[fname] = raw
            out.append({
                "type": "function",
                "function": {
                    "name": fname,
                    "description": t.get("description", "") or "",
                    "parameters": t.get("inputSchema") or {"type": "object",
                                                           "properties": {}},
                },
            })
        return out

    def call_tool(self, name: str, arguments: dict) -> str:
        raw = self._call_map.get(name, name)
        result = self._transport.request("tools/call", {"name": raw, "arguments": arguments})
        texts = []
        for item in result.get("content", []):
            if item.get("type") == "text":
                texts.append(item.get("text", ""))
        text = "\n".join(texts) or "(空结果)"
        if result.get("isError"):
            text = f"[MCP 工具错误] {text}"
        return text

    def close(self):
        if self._transport:
            self._transport.close()


class McpManager:
    """管理多个 MCP 服务器，聚合工具供 LLM 使用

    跨服务器同名工具按「服务器名_工具名」前缀去重（与模块文档一致），
    调用时按原始工具名路由到对应服务器。"""

    def __init__(self):
        self._clients: dict = {}
        self._configs: dict = {}      # 服务器名 -> 建连时所用配置（增量热加载据此判断「新增/变更」）
        self._schemas: dict = {}      # 服务器名 -> 建连时抓到的工具 schema
        self._name_to_server: dict = {}
        self._tool_raw: dict = {}      # 聚合工具名 -> 服务器原始工具名
        self._tools: list = []
        self.errors: list = []   # 各服务器连接失败信息
        # 最近一次 reload 的动作摘要（热加载后回填进工具结果文案，让模型知道新增了什么）
        self.last_reload: dict = {"added": [], "updated": [], "removed": []}

    def _connect(self, cfg: dict) -> bool:
        """建单个服务器连接并抓一次工具清单；失败只记 errors，不影响其他服务器。"""
        name = str(cfg.get("name") or "mcp")
        client = None
        try:
            client = McpClient(name, cfg)
            schemas = client.list_tools()
        except Exception as e:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
            self.errors.append(f"[{name}] {e}")
            return False
        self._clients[name] = client
        self._configs[name] = dict(cfg)
        # 工具清单必须在这里抓一次并缓存：McpClient.list_tools 每次调用都会往内部去重表
        # 写名字，再调一次会把同一工具返回成「带前缀」的另一种形态（名字漂移 + 重复）。
        self._schemas[name] = schemas
        return True

    def _drop(self, name: str) -> None:
        """断开并移除单个服务器的连接与缓存"""
        client = self._clients.pop(name, None)
        self._configs.pop(name, None)
        self._schemas.pop(name, None)
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    def _rebuild_aggregate(self) -> list:
        """按当前连接重建聚合工具表（规则与旧实现一致：先到者用原名，跨服务器重名者加服务器名前缀）。"""
        self._tools = []
        self._name_to_server = {}
        self._tool_raw = {}
        used: set = set()
        for name, _client in self._clients.items():
            for schema in self._schemas.get(name) or []:
                cname = schema["function"]["name"]     # 客户端层名字
                if cname in used:
                    agg = f"{name}_{cname}"
                    schema = json.loads(json.dumps(schema))
                    schema["function"]["name"] = agg
                else:
                    agg = cname
                used.add(agg)
                self._name_to_server[agg] = name
                self._tool_raw[agg] = cname
                self._tools.append(schema)
        return self._tools

    def connect_all(self, servers: list) -> list:
        """servers: [{"name","type","command","args"|"url"}]，返回聚合后的工具 schema（全量重建）"""
        self.close_all()
        self.errors = []
        self.last_reload = {"added": [str(c.get("name") or "mcp") for c in servers],
                            "updated": [], "removed": []}
        for cfg in servers:
            self._connect(cfg)
        return self._rebuild_aggregate()

    def reload(self, servers: list) -> list:
        """**增量**热加载：只重建「新增或配置变更」的服务器，断开已移除的，未变的连接原样保留。

        为什么不一律 close_all + connect_all：每个服务器都是一次真实建连（stdio 要起子进程、
        sse 要握手），全量重连会把与本次变更无关的服务器一起打断 —— 正在用的服务器会莫名断线
        （丢掉进程内状态），耗时也随服务器数量线性增长。增量只付「真正变了的那几个」的成本。
        """
        want: dict = {}
        for cfg in servers:
            want[str(cfg.get("name") or "mcp")] = cfg
        added: list = []
        updated: list = []
        removed: list = []
        for name in list(self._clients):
            if name not in want:
                self._drop(name)
                removed.append(name)
            elif self._configs.get(name) != want[name]:
                self._drop(name)
                updated.append(name)
        self.errors = []
        for cfg in servers:
            name = str(cfg.get("name") or "mcp")
            if name in self._clients:
                continue                      # 未变：连接与工具清单都原样保留
            # 建连失败只进 errors（会重试），不得混进 added 让摘要谎报「新增成功」
            if self._connect(cfg) and name not in updated:
                added.append(name)
        self.last_reload = {"added": added, "updated": updated, "removed": removed}
        return self._rebuild_aggregate()

    def tool_schemas(self) -> list:
        return self._tools

    def tool_schemas_for(self, allowed_servers) -> list:
        """按允许的服务器名过滤聚合工具（工作流 MCP 隔离：未绑定服务器的工具不暴露）"""
        if not allowed_servers:
            return []
        return [t for t in self._tools
                if self._name_to_server.get(t["function"]["name"]) in allowed_servers]

    def server_for_tool(self, name: str) -> str:
        """工具所属服务器名（空 = 未知/非 MCP 工具）"""
        return self._name_to_server.get(name, "")

    def call_tool(self, name: str, arguments: dict) -> str:
        server = self._name_to_server.get(name)
        if not server:
            raise McpError(f"未知 MCP 工具: {name}")
        # 用服务器原始工具名调用（去重后的前缀名在客户端 _call_map 中还原）
        return self._clients[server].call_tool(self._tool_raw.get(name, name), arguments)

    def close_all(self):
        for c in self._clients.values():
            try:
                c.close()
            except Exception:
                pass
        self._clients.clear()
        self._configs.clear()
        self._schemas.clear()
        self._name_to_server.clear()
        self._tool_raw.clear()
        self._tools = []
