"""应用级网络代理回退：直连优先，连不上时经 SSH 隧道中转。

场景：更新服务器（Cloudflare 后面）/ 插件与更新包下载（GitHub 等开发者
平台）在部分网络环境直连超时或被网关拦截，而服务端 SSH 可以到达。

链路：客户端 ──SSH 加密通道──> 代理服务器 ──TCP──> 目标站

与早期 squid https_port 方案的区别：外层是标准 SSH 协议而非 TLS，不会
触发 GFW 的 TLS-in-TLS 行为检测；内层 HTTPS 目标经 MemoryBIO 驱动 TLS
握手，证书校验与直连一致。实测 2026-10-19 从国内移动网络经 SSH 隧道
访问 GitHub / Cloudflare / PyPI 全部 200 OK。

settings.json 键（设置页「网络代理」区域写入）：
  proxy_enabled  "1"/"0"        总开关（默认关）
  proxy_host     SSH 服务器 IP/域名
  proxy_port     SSH 端口（数字字符串，默认 22）
  proxy_user     SSH 用户名
  proxy_pass     SSH 密码

约定：
  * HTTPError（服务端已应答，包括 403/407）不触发回退——网络通、业务层
    拒绝不是代理能解决的问题；
  * 回退仅针对连接层失败（URLError / OSError / 超时），且只重试一次；
  * 本机地址（localhost / 127.* / ::1）永不走代理；
  * SSH Transport 全局复用，断连自动重建。
"""

import http.client
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from io import BytesIO
from urllib.parse import urljoin, urlsplit

_MAX_REDIRECTS = 5

_SSH_LOCK = threading.Lock()
_SSH_TRANSPORT = None          # paramiko.Transport 全局复用
_last_fallback = ""            # 最近一次回退成功的说明（诊断用）


def _is_direct_host(host) -> bool:
    if not host:
        return True
    return host in ("localhost", "::1") or host.startswith("127.")


def _ssh_config():
    """读取 settings 的 SSH 代理配置，返回 (host, port, user, pwd)；未启用返回 None。"""
    try:
        from zhuzhu_Copilot.core import agent_skills
        s = agent_skills.settings_view()
    except Exception:
        return None
    if str(s.get("proxy_enabled", "0")).strip().lower() not in ("1", "true", "on", "yes"):
        return None
    host = str(s.get("proxy_host", "")).strip()
    port_str = str(s.get("proxy_port", "22")).strip() or "22"
    user = str(s.get("proxy_user", "")).strip()
    pwd = str(s.get("proxy_pass", ""))
    if not host or not port_str.isdigit() or not user:
        return None
    return host, int(port_str), user, pwd


def _get_ssh_transport():
    """获取或重建 SSH Transport，失败抛异常。"""
    global _SSH_TRANSPORT
    cfg = _ssh_config()
    if not cfg:
        raise RuntimeError("proxy not configured")
    host, port, user, pwd = cfg
    with _SSH_LOCK:
        if _SSH_TRANSPORT is not None:
            try:
                if _SSH_TRANSPORT.is_active():
                    return _SSH_TRANSPORT
            except Exception:
                pass
            try:
                _SSH_TRANSPORT.close()
            except Exception:
                pass
            _SSH_TRANSPORT = None
        import paramiko
        sock = socket.create_connection((host, port), 15)
        t = paramiko.Transport(sock)
        try:
            t.connect(username=user, password=pwd)
        except Exception:
            t.close()
            raise
        _SSH_TRANSPORT = t
        return t


def _open_direct_tunnel(target_host: str, target_port: int, timeout: float):
    """开一条 SSH direct-tcpip 通道到目标 host:port。"""
    t = _get_ssh_transport()
    return t.open_channel("direct-tcpip", (target_host, target_port),
                          ("127.0.0.1", 0), timeout=timeout)


class _SSLSocketWrapper:
    """把 paramiko Channel + MemoryBIO 内层 TLS 包装成 http.client 可用的类 socket。

    Channel 不是真 socket，ssl.wrap_socket 不可用；用 MemoryBIO + wrap_bio 手动
    驱动 TLS 握手与读写，外层 SSH 已加密，内层 TLS 正常校验目标站证书。
    """

    def __init__(self, ch, host: str, timeout: float):
        self._ch = ch
        try:
            self._ch.settimeout(timeout)
        except Exception:
            pass
        ctx = ssl.create_default_context()
        self._in_bio = ssl.MemoryBIO()
        self._out_bio = ssl.MemoryBIO()
        self._ssl = ctx.wrap_bio(self._in_bio, self._out_bio, server_hostname=host)
        self._do_handshake()

    def _flush(self):
        data = self._out_bio.read()
        if data:
            self._ch.sendall(data)

    def _do_handshake(self):
        while True:
            try:
                self._ssl.do_handshake()
                self._flush()
                return
            except ssl.SSLWantReadError:
                self._flush()
                chunk = self._ch.recv(65536)
                if not chunk:
                    raise ConnectionError("SSH tunnel closed during TLS handshake")
                self._in_bio.write(chunk)

    def recv(self, n: int) -> bytes:
        try:
            data = self._ssl.read(n)
            self._flush()
            return data
        except ssl.SSLWantReadError:
            self._flush()
            chunk = self._ch.recv(65536)
            if not chunk:
                return b""
            self._in_bio.write(chunk)
            return self.recv(n)

    def sendall(self, data: bytes) -> None:
        self._ssl.write(data)
        self._flush()

    def makefile(self, mode="rb"):
        return _ChannelFile(self)

    def close(self):
        try:
            self._ch.close()
        except Exception:
            pass


class _ChannelFile:
    """供 http.client.HTTPResponse 读取的文件对象（基于类 socket 的 recv）。"""

    def __init__(self, sock):
        self._sock = sock
        self._buf = b""

    def _recv_more(self) -> bool:
        chunk = self._sock.recv(65536)
        if chunk:
            self._buf += chunk
            return True
        return False

    def read(self, n=-1):
        if n is None or n < 0:
            while self._recv_more():
                pass
            data, self._buf = self._buf, b""
            return data
        while len(self._buf) < n and self._recv_more():
            pass
        data, self._buf = self._buf[:n], self._buf[n:]
        return data

    def readline(self, limit=-1):
        while b"\n" not in self._buf:
            if limit >= 0 and len(self._buf) >= limit:
                data = self._buf[:limit]
                self._buf = self._buf[limit:]
                return data
            if not self._recv_more():
                break
        nl = self._buf.find(b"\n")
        if nl >= 0:
            end = nl + 1
            if limit >= 0:
                end = min(end, limit)
            data = self._buf[:end]
            self._buf = self._buf[end:]
            return data
        if limit >= 0:
            data = self._buf[:limit]
            self._buf = self._buf[limit:]
            return data
        data, self._buf = self._buf, b""
        return data

    def flush(self):
        pass

    def close(self):
        pass


class _TunnelResponse:
    """包装 http.client.HTTPResponse，关闭时同时关 SSH 通道。"""

    __slots__ = ("_resp", "_ch")

    def __init__(self, resp, ch):
        self._resp = resp
        self._ch = ch

    def read(self, *a, **k):
        return self._resp.read(*a, **k)

    def readline(self, *a, **k):
        return self._resp.readline(*a, **k)

    def readlines(self, *a, **k):
        return self._resp.readlines(*a, **k)

    @property
    def status(self):
        return self._resp.status

    @property
    def reason(self):
        return self._resp.reason

    @property
    def headers(self):
        return self._resp.headers

    def getheader(self, name, default=None):
        return self._resp.getheader(name, default)

    def getheaders(self):
        return self._resp.getheaders()

    def geturl(self):
        return self._resp.geturl()

    def __getattr__(self, name):
        # 兜底转发：未显式声明的属性（如 will_close/msg/version 等）透传底层响应
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._resp, name)

    def close(self):
        try:
            self._resp.close()
        finally:
            try:
                self._ch.close()
            except Exception:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _via_ssh_proxy(req, target, timeout: float, hops: int = 0):
    """经 SSH 隧道完成一次请求，返回与 urllib 兼容的响应（含 HTTPError 契约）。"""
    method = req.get_method()
    body = req.data
    hdrs = dict(req.header_items())
    host = target.hostname
    port = target.port or (443 if target.scheme == "https" else 80)
    path = target.path or "/"
    if target.query:
        path += "?" + target.query
    if not any(k.lower() == "host" for k in hdrs):
        hdrs["Host"] = host + (f":{port}" if port not in (80, 443) else "")
    if body and not any(k.lower() == "content-length" for k in hdrs):
        hdrs["Content-Length"] = str(len(body))
    hdrs.setdefault("Connection", "close")

    ch = _open_direct_tunnel(host, port, timeout)
    try:
        if target.scheme == "https":
            sock = _SSLSocketWrapper(ch, host, timeout)
        else:
            try:
                ch.settimeout(timeout)
            except Exception:
                pass
            sock = ch
        lines = [f"{method} {path} HTTP/1.1"]
        for k, v in hdrs.items():
            lines.append(f"{k}: {v}")
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("iso-8859-1"))
        if body:
            sock.sendall(body)
        resp = http.client.HTTPResponse(sock)
        resp.begin()
        _note_traffic()
        if 300 <= resp.status < 400:
            loc = resp.getheader("Location") or ""
            try:
                resp.read()
            except Exception:
                pass
            resp.close()
            ch.close()
            if not loc or hops >= _MAX_REDIRECTS:
                raise urllib.error.HTTPError(req.full_url, resp.status, resp.reason,
                                             resp.headers, BytesIO(b""))
            newurl = urljoin(req.full_url, loc)
            newreq = urllib.request.Request(newurl, data=body, method=method)
            for k, v in hdrs.items():
                if k.lower() not in ("host",):
                    newreq.add_header(k, v)
            return _via_ssh_proxy(newreq, urlsplit(newurl), timeout, hops + 1)
        if resp.status >= 400:
            data = b""
            try:
                data = resp.read()
            except Exception:
                pass
            resp.close()
            ch.close()
            raise urllib.error.HTTPError(req.full_url, resp.status, resp.reason,
                                         resp.headers, BytesIO(data))
        global _last_fallback
        _last_fallback = f"{time.strftime('%H:%M:%S')} 经 SSH 隧道中转成功: {host}"
        return _TunnelResponse(resp, ch)
    except Exception:
        try:
            ch.close()
        except Exception:
            pass
        raise


def _note_traffic() -> None:
    """记录「应用请求已到达服务端」：顶栏网络状态据此判定在线。"""
    try:
        from zhuzhu_Copilot.core import net_status
        net_status.note_success()
    except Exception:
        pass


def urlopen(req, timeout, _fallback_done: bool = False):
    """urllib.request.urlopen 的透明替代：直连失败时按配置经 SSH 隧道重试一次。"""
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError:
        raise                              # 服务端已应答：网络通，不回退
    except (urllib.error.URLError, OSError, TimeoutError) as direct_exc:
        if _fallback_done or not _ssh_config():
            raise
        target = urlsplit(getattr(req, "full_url", "") or "")
        if _is_direct_host(target.hostname):
            raise                          # 本机地址不走代理
        try:
            return _via_ssh_proxy(req, target, timeout)
        except urllib.error.HTTPError:
            raise                          # 经代理已到服务端：以代理路径的应答为准
        except Exception:
            raise direct_exc               # 代理也不通：以直连的原始错误为准


def last_fallback() -> str:
    """最近一次代理回退成功的说明（供诊断/状态展示，无则返回空串）。"""
    return _last_fallback
