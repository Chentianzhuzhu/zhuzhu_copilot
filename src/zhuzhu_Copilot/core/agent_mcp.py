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
import time
import urllib.request
import urllib.error

MCP_VERSION = "2024-11-05"
# 建连/握手阶段的最长等待：只覆盖 initialize、tools/list 这类「本该瞬时」的请求。
_INIT_TIMEOUT = 8
# 工具调用的等待上限：**默认不设上限**。
# 为什么：工具的真实耗时由用户机器与界面状态决定（截图、图像对比、点击校验都可能
# 秒级），拿一个拍脑袋的固定秒数去卡，只会把「正在正常干活」的工具判成超时；
# 客户端一旦提前放弃，服务端仍在执行，下一次调用再排队，就演变成雪崩式「MCP 频繁
# 超时」。真正需要兜底的是「连接死了」——那由读循环死亡检测**立即**失败唤醒，
# 不需要靠超时。需要时可用环境变量显式给一个上限（秒），便于排障。
def _tool_timeout():
    raw = os.environ.get("ZHZHU_MCP_TOOL_TIMEOUT", "").strip()
    if not raw:
        return None
    try:
        v = float(raw)
        return v if v > 0 else None
    except ValueError:
        return None



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
    def __init__(self, command: str, args: list, env: dict = None):
        # env：插件配置注入的环境变量（键名已由上层大写化）。在继承当前进程
        # 环境的基础上覆盖，保证 PATH 等基础变量不丢；保留键上层已拦截。
        env_full = None
        if env:
            env_full = dict(os.environ)
            env_full.update({str(k): str(v) for k, v in env.items()})
        # CREATE_NO_WINDOW：禁止子进程弹出控制台窗口（否则连接时会闪一个黑色终端）
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            self._proc = subprocess.Popen(
                [command, *args], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env=env_full, creationflags=creationflags)
        except Exception as e:
            raise McpError(f"启动 MCP 服务器失败: {e}")
        self._pending: dict = {}
        self._lock = threading.Lock()
        self._id = 0
        self._dead = ""           # 读循环死亡原因（空=活着）。死了就必须立刻失败，
                                  # 绝不能白等超时 —— 那正是「MCP 请求超时」的来源。
        self._write_lock = threading.Lock()  # 只锁「写一行 JSON-RPC」这一步：
                                            # 多个线程可以同时有在途请求（响应靠 id
                                            # 分流），但绝不能交错写 stdin。
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
                    with self._lock:
                        q = self._pending.pop(msg["id"], None)
                    if q:
                        q.put(msg)
        except Exception as e:
            self._dead = f"{type(e).__name__}: {e}"
        finally:
            # 读循环一死，主程序再也收不到任何响应。这里做两件事：
            # ① 记下死因，让 request() 立刻抛错而不是干等 8 秒；
            # ② 把还在等待的请求全部唤醒（否则它们只能等超时）。
            self._dead = self._dead or "MCP 读循环退出（子进程可能已退出）"
            while True:
                try:
                    with self._lock:
                        _rid, q = self._pending.popitem()
                except KeyError:
                    break
                q.put({"error": {"code": -32000, "message": self._dead}})

    def request(self, method: str, params: dict, timeout=None) -> dict:
        """单次请求。`timeout=None` 表示一直等到有响应（默认）。

        并发策略：只锁「写 stdin」这一小段，**不再把整个请求串行化**。JSON-RPC 的
        响应靠 id 分流，两个线程同时有在途请求完全合法；早先把整段「写→等→收」都
        锁住，导致一个慢工具把同服务器上其它请求（以及工具表刷新）一起堵死，
        随后成片超时。
        """
        if self._dead:
            raise McpError(f"MCP 连接已断开：{self._dead}")
        with self._lock:
            self._id += 1
            rid = self._id
            q = queue.Queue()
            self._pending[rid] = q
        payload = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
        try:
            with self._write_lock:
                if self._dead:
                    raise McpError(f"MCP 连接已断开：{self._dead}")
                self._proc.stdin.write(json.dumps(payload).encode() + b"\n")
                self._proc.stdin.flush()
        except McpError:
            with self._lock:
                self._pending.pop(rid, None)
            raise
        except Exception as e:
            with self._lock:
                self._pending.pop(rid, None)
            raise McpError(f"MCP 发送失败: {e}")
        try:
            resp = q.get(timeout=timeout)
        except queue.Empty:
            # 放弃等待时必须把在途表清掉，否则晚到的响应会一直挂在内存里
            with self._lock:
                self._pending.pop(rid, None)
            raise McpError(f"MCP 请求超时: {method}")
        if "error" in resp:
            raise McpError(f"MCP 错误: {resp['error']}")
        # 契约：返回 **result 段**（不是整个信封）。调用方 list_tools 取
        # result["tools"]、call_tool 取 result["content"]，都按这个约定写的。
        # 曾经这里误写成 `return resp`，于是 tools 永远取不到 → 工具表为空 →
        # 模型一调就「未知 MCP 工具」。
        return resp.get("result", {})

    def notify(self, method: str, params: dict) -> None:
        """JSON-RPC 通知：不发 id、**不等任何响应**。

        通知本来就不该有响应；等它只会白烧一个超时，还会把请求序号表搅乱
        （后面的 tools/list 会拿到错位的响应 → 工具表为空 → 「未知 MCP 工具」）。
        """
        with self._write_lock:
            try:
                self._proc.stdin.write(json.dumps(
                    {"jsonrpc": "2.0", "method": method,
                     "params": params}).encode() + b"\n")
                self._proc.stdin.flush()
            except Exception as e:
                raise McpError(f"MCP 发送失败: {e}")

    def call(self, method: str, params: dict, timeout=None) -> dict:
        """带一次重试的请求：管道抖动 / 子进程刚就绪时不该直接失败。

        连接已断开不重试（重试没意义）—— 直接抛给上层，让 McpManager 自愈重连。
        工具调用默认不设超时（见 `_tool_timeout`）。
        """
        last = None
        for attempt in (0, 1):
            try:
                return self.request(method, params, timeout=timeout)
            except McpError as e:
                last = e
                # 超时/已断开都不重试：重试会再排一次队，把界面状态搅得更乱
                if "已断开" in str(e) or "超时" in str(e) or attempt:
                    raise
                time.sleep(0.12)
        raise last

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
        self._dead = ""            # 事件流断开的死因（空=活着）
        self._ready = threading.Event()
        threading.Thread(target=self._read_loop, daemon=True).start()
        if not self._ready.wait(timeout=_INIT_TIMEOUT):
            raise McpError(f"MCP SSE 初始化超时: {url}")
        if self._dead or not self._messages_url:
            raise McpError(f"MCP SSE 连接失败: {self._dead or '未获得 messages 端点'}")

    def _read_loop(self):
        try:
            # timeout=None：SSE 事件流本来就是「可能长时间没有数据」的长连接，
            # 设读超时会让健康连接在空闲 8 秒后被判死（表现为随机「MCP 请求超时」）。
            resp = urllib.request.urlopen(self._url, timeout=None)
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
                            with self._lock:
                                q = self._pending.pop(obj["id"], None)
                            if q:
                                q.put(obj)
        except Exception as e:
            self._dead = f"{type(e).__name__}: {e}"
        finally:
            # 事件流一断，所有在途请求永远不会再有响应 → 立刻唤醒它们，
            # 让上层自愈重连，而不是让调用方无限等待。
            self._dead = self._dead or "MCP SSE 事件流已断开"
            self._ready.set()
            while True:
                try:
                    with self._lock:
                        _rid, q = self._pending.popitem()
                except KeyError:
                    break
                q.put({"error": {"code": -32000, "message": self._dead}})

    def request(self, method: str, params: dict, timeout=None) -> dict:
        if not self._messages_url:
            raise McpError("MCP SSE 未获得 messages 端点")
        if self._dead:
            raise McpError(f"MCP 连接已断开：{self._dead}")
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
            urllib.request.urlopen(req, timeout=_INIT_TIMEOUT)
        except urllib.error.HTTPError as e:
            with self._lock:
                self._pending.pop(rid, None)
            raise McpError(f"MCP POST 失败: {e.code}")
        except urllib.error.URLError as e:
            with self._lock:
                self._pending.pop(rid, None)
            raise McpError(f"MCP POST 网络错误: {e.reason}")
        except Exception as e:
            with self._lock:
                self._pending.pop(rid, None)
            raise McpError(f"MCP POST 失败: {e}")
        try:
            resp = q.get(timeout=timeout)
        except queue.Empty:
            with self._lock:
                self._pending.pop(rid, None)
            raise McpError(f"MCP 请求超时: {method}")
        if "error" in resp:
            raise McpError(f"MCP 错误: {resp['error']}")
        return resp.get("result", {})

    def call(self, method: str, params: dict, timeout=None) -> dict:
        return self.request(method, params, timeout=timeout)

    @property
    def dead(self) -> str:
        return self._dead

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
            # config["env"]：插件配置注入（见 agent_plugins.set_plugin_config）
            self._transport = _StdioTransport(_safe_stdio_command(config),
                                              config.get("args", []),
                                              config.get("env"))
        elif typ == "sse":
            self._transport = _SSETransport(config["url"])
        else:
            raise McpError(f"不支持的 MCP 传输类型: {typ}")
        self._init()

    def _init(self):
        self._transport.call("initialize", {
            "protocolVersion": MCP_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "winapp-migrator", "version": "1.0"},
        }, timeout=_INIT_TIMEOUT)
        # notifications/initialized 是**通知**（无 id，协议规定不产生响应），必须走
        # notify 直接写出去。早先这里走的是普通 request，于是为一个永远不该有响应的
        # 通知白等满超时（原 30s，后来 8s 还要重试一次 = 16s）：每次建连都白烧
        # 16 秒，还把请求序号表搅乱（紧接着的 tools/list 会拿到错位的响应 →
        # 工具表为空 → 「未知 MCP 工具」）。用户侧的「5 分钟没完成」很大一块在这。
        self._transport.notify("notifications/initialized", {})

    def list_tools(self) -> list:
        """返回 OpenAI function schema 列表"""
        result = self._transport.call("tools/list", {}, timeout=_INIT_TIMEOUT)
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

    def call_tool(self, name: str, arguments: dict) -> tuple:
        """调用工具，返回 (文本结果, 图片 data URL 列表)。

        MCP 结果的 content 支持 text 与 image 两类内容项（image 为 base64 + mimeType）。
        图片项转成 data URL 交给引擎喂视觉模型（如电脑操控插件的屏幕截图）——
        丢弃图片会让「需要看屏幕」的 MCP 工具退化成盲操作。
        """
        raw = self._call_map.get(name, name)
        result = self._transport.call("tools/call", {"name": raw, "arguments": arguments},
                                      timeout=_tool_timeout())
        texts, images = [], []
        for item in result.get("content", []):
            if item.get("type") == "text":
                texts.append(item.get("text", ""))
            elif item.get("type") == "image" and item.get("data"):
                mime = str(item.get("mimeType") or "image/png")
                images.append(f"data:{mime};base64,{item['data']}")
        text = "\n".join(texts) or ("(图片结果)" if images else "(空结果)")
        if result.get("isError"):
            text = f"[MCP 工具错误] {text}"
        return text, images

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
        self._last_servers: list = []   # 最近一次成功/尝试的服务器配置（自愈用）
        self._healing = False           # 自愈进行中：避免并发重复重连
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
        self._last_servers = [dict(c) for c in servers]
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
        self._last_servers = [want[k] for k in want]
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

    def _self_heal(self) -> bool:
        """工具表为空时按上次的配置重连一次（有界：不并发、只此一次）。

        为什么需要：主程序可能比插件子进程早几秒起来，第一次握手失败 → `_clients`
        空 → 聚合工具表空 → 之后再也不补。用户侧看到的就是「未知 MCP 工具: xxx」，
        而插件进程明明好着（手动连立刻成功）。只在整个连接表为空时才自愈，
        不会把「模型拼错工具名」误触发成重连。
        """
        if self._healing or not self._last_servers:
            return False
        self._healing = True
        try:
            self.connect_all(self._last_servers)
            return bool(self._clients)
        except Exception:
            return False
        finally:
            self._healing = False

    def call_tool(self, name: str, arguments: dict) -> tuple:
        """调用工具，返回 (文本结果, 图片 data URL 列表)（与 McpClient.call_tool 一致）"""
        server = self._name_to_server.get(name)
        if not server and not self._clients:
            if self._self_heal():
                server = self._name_to_server.get(name)
        # 连接还在、但底层读循环已经死了（子进程退出 / 管道被关）→ 也自愈一次。
        # 否则工具表看着有工具，一调用就「连接已断开」，用户侧仍是不可用。
        if server:
            cli = self._clients.get(server)
            if cli is not None and getattr(getattr(cli, "_transport", None), "_dead", ""):
                if self._self_heal():
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
