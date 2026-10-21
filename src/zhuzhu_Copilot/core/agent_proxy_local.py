"""本地全局代理转发器：SOCKS5 + HTTP CONNECT，经 SSH 隧道转发任意目标。

系统代理指向本机监听端口后，浏览器/Electron 等 WinINET 应用的全部流量进入
此处，再按 agent_proxy.route_host() 的决策走 SSH 隧道或直连：
  * 全局模式：除本机地址外一律经 SSH 隧道；
  * 回退模式：仅 proxy_domains 命中的目标走隧道，其余直连。

只监听 127.0.0.1（不暴露公网），无认证；每个连接一个线程 + 一条 SSH 通道。
SOCKS5 支持 CONNECT（TCP），不支持 UDP ASSOCIATE（SSH 无法承载 UDP）。
"""

import socket
import struct
import threading

_HEAD_LIMIT = 65536
_CONNECT_TIMEOUT = 15
_BUF = 65536


class LocalProxyServer:
    def __init__(self, port: int = 0):
        self._port = int(port or 0)
        self._sock = None
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.total = 0
        self.via_ssh = 0
        self.direct = 0
        self.last_error = ""

    @property
    def port(self) -> int:
        return self._port

    @property
    def address(self) -> str:
        return f"127.0.0.1:{self._port}"

    def running(self) -> bool:
        return self._sock is not None

    def start(self) -> None:
        with self._lock:
            if self._sock is not None:
                return
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", self._port))
            s.listen(128)
            s.settimeout(0.5)
            self._port = s.getsockname()[1]
            self._sock = s
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._accept_loop, daemon=True, name="local-proxy")
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._stop.set()
            s, self._sock = self._sock, None
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            s = self._sock
            if s is None:
                return
            try:
                client, _ = s.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self._handle, args=(client,),
                             daemon=True, name="proxy-conn").start()

    # ---------------- 协议分发 ----------------
    def _handle(self, client: socket.socket) -> None:
        self.total += 1
        try:
            client.settimeout(30)
            first = client.recv(1, socket.MSG_PEEK)
            if not first:
                return
            if first == b"\x05":
                self._socks5(client)
            else:
                self._http(client)
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            _close(client)

    # ---------------- SOCKS5 ----------------
    def _socks5(self, client: socket.socket) -> None:
        _recv_exact(client, 1)                     # version
        nmethods = _recv_exact(client, 1)[0]
        _recv_exact(client, nmethods)              # methods
        client.sendall(b"\x05\x00")                # no auth
        ver, cmd, _rsv, atyp = _recv_exact(client, 4)
        if ver != 5 or cmd != 1:
            client.sendall(b"\x05\x07\x00\x01" + socket.inet_aton("0.0.0.0") + b"\x00\x00")
            _close(client)
            return
        if atyp == 1:
            host = socket.inet_ntoa(_recv_exact(client, 4))
        elif atyp == 3:
            n = _recv_exact(client, 1)[0]
            host = _recv_exact(client, n).decode("ascii", "replace")
        elif atyp == 4:
            host = socket.inet_ntop(socket.AF_INET6, _recv_exact(client, 16))
        else:
            client.sendall(b"\x05\x08\x00\x01" + socket.inet_aton("0.0.0.0") + b"\x00\x00")
            _close(client)
            return
        port = struct.unpack("!H", _recv_exact(client, 2))[0]
        try:
            upstream = self._open_upstream(host, port)
        except Exception as e:
            self.last_error = f"{host}:{port} {type(e).__name__}"
            code = 0x05 if isinstance(e, ConnectionRefusedError) else 0x04
            client.sendall(bytes([5, code, 0, 1]) + socket.inet_aton("0.0.0.0") + b"\x00\x00")
            _close(client)
            return
        client.sendall(b"\x05\x00\x00\x01" + socket.inet_aton("0.0.0.0") + b"\x00\x00")
        _relay(client, upstream)

    # ---------------- HTTP 代理 ----------------
    def _http(self, client: socket.socket) -> None:
        head, rest = _read_head(client)
        if not head:
            _close(client)
            return
        lines = head.split(b"\r\n")
        try:
            method, target, version = lines[0].decode("iso-8859-1").split(" ", 2)
        except ValueError:
            _bad_gateway(client, "400 Bad Request")
            return
        headers = [ln.decode("iso-8859-1") for ln in lines[1:] if ln]

        if method.upper() == "CONNECT":
            host, _, port_s = target.partition(":")
            port = int(port_s) if port_s.isdigit() else 443
            try:
                upstream = self._open_upstream(host, port)
            except Exception as e:
                self.last_error = f"{host}:{port} {type(e).__name__}"
                _bad_gateway(client, "502 Bad Gateway")
                return
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            if rest:
                upstream.sendall(rest)
            _relay(client, upstream)
            return

        # 明文 HTTP：绝对 URI → 改写为 origin-form 后转发
        if not target.lower().startswith("http://"):
            _bad_gateway(client, "501 Not Implemented")
            return
        without_scheme = target[7:]
        hostport, _, path = without_scheme.partition("/")
        host, _, port_s = hostport.partition(":")
        port = int(port_s) if port_s.isdigit() else 80
        path = "/" + path
        try:
            upstream = self._open_upstream(host, port)
        except Exception as e:
            self.last_error = f"{host}:{port} {type(e).__name__}"
            _bad_gateway(client, "502 Bad Gateway")
            return
        keep = [h for h in headers
                if h.split(":", 1)[0].strip().lower()
                not in ("proxy-connection", "proxy-authorization", "host")]
        out = [f"{method} {path} {version}", f"Host: {hostport}"] + keep
        upstream.sendall(("\r\n".join(out) + "\r\n\r\n").encode("iso-8859-1"))
        if rest:
            upstream.sendall(rest)
        _relay(client, upstream)

    # ---------------- 上游连接 ----------------
    def _open_upstream(self, host: str, port: int):
        from zhuzhu_Copilot.core import agent_proxy
        if agent_proxy.route_host(host) == "ssh":
            self.via_ssh += 1
            return agent_proxy.open_channel(host, port, timeout=_CONNECT_TIMEOUT)
        self.direct += 1
        return socket.create_connection((host, port), _CONNECT_TIMEOUT)


# ---------------- 工具函数 ----------------
def _recv_exact(sock, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("peer closed")
        buf += chunk
    return buf


def _read_head(sock) -> tuple:
    """读到 \\r\\n\\r\\n 为止，返回 (head, 已多读的 body 字节)。"""
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(_BUF)
        if not chunk:
            return b"", b""
        buf += chunk
        if len(buf) > _HEAD_LIMIT:
            raise ConnectionError("header too large")
    head, _, rest = buf.partition(b"\r\n\r\n")
    return head, rest


def _shutdown_write(x) -> None:
    try:
        if hasattr(x, "shutdown_write"):
            x.shutdown_write()          # paramiko.Channel
        else:
            x.shutdown(socket.SHUT_WR)
    except Exception:
        pass


def _close(x) -> None:
    try:
        x.close()
    except Exception:
        pass


def _bad_gateway(client, status: str) -> None:
    try:
        client.sendall(f"HTTP/1.1 {status}\r\nConnection: close\r\n\r\n".encode())
    except Exception:
        pass
    _close(client)


def _relay(a, b) -> None:
    """双向转发：任一方向 EOF 后关闭两侧。"""
    def copy(src, dst):
        try:
            while True:
                data = src.recv(_BUF)
                if not data:
                    break
                dst.sendall(data)
        except Exception:
            pass
        finally:
            _shutdown_write(dst)

    t = threading.Thread(target=copy, args=(a, b), daemon=True, name="proxy-pump")
    t.start()
    copy(b, a)
    t.join(timeout=30)
    _close(a)
    _close(b)
