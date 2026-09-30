"""流式 HTTP 请求的连接复用池（stdlib http.client，零第三方依赖）。

背景（真实探针 scripts/_probe_concurrency_perf.py 实测）：Agent 长任务每轮调用一次
/chat/completions 流式接口，urllib 每次请求都重建 TCP+TLS 连接——一次握手白付一次，
N 轮任务即 N 次。本模块按 (scheme, host, port) 复用 keep-alive 连接：

  * 响应对象与 urllib 的 HTTPResponse 接口兼容（readline/read/getheader/close/fp），
    调用方（agent_llm 的读行循环、_relax_socket_timeout）无需改动；
  * 状态码 >= 400 抛 urllib.error.HTTPError（错误体可读），调用方重试/文案逻辑不变；
  * 流正常读完（EOF）→ 连接自动归还池；中途 close() / 读异常 → 连接立即丢弃；
  * 建连失败 → 丢弃连接并回退 urllib 一次（自愈，行为与优化前一致）。

回退 urllib 原路径的条件（任一）：目标主机配置了 http(s) 代理（http.client 不走代理，
避免改写用户网络路径）、settings.json 关闭能力开关 `cap_http_pool`（默认开，
与 cap_mcp/cap_skill 同一约定）、URL 非 http(s)、http.client 建连失败。
"""

import http.client
import threading
import time
import urllib.error
from io import BytesIO
from urllib.parse import urlsplit

# 每个主机的空闲连接上限：多会话并发（数路会话 + 重试余量）各占一条，多余即关
MAX_IDLE_PER_HOST = 4
# 空闲连接回收（秒）：超过即关闭重连，避免复用被上游/中间网关单方断开的死连接
IDLE_RECYCLE_SECS = 60.0
# 代理自检时视为「直连」的主机前缀（loopback 不走代理，本地自测服务器也由此稳定命中池）
_DIRECT_HOSTS = ("localhost", "::1")


def _is_direct_host(host: str) -> bool:
    return host in _DIRECT_HOSTS or host.startswith("127.")


class _Conn:
    """池中一条连接：conn + 上次归还时间（单调时钟，用于空闲回收）。"""
    __slots__ = ("conn", "last_used")

    def __init__(self, conn, last_used: float):
        self.conn = conn
        self.last_used = last_used


class _PooledStream:
    """池化连接的响应流：接口对齐 urllib 的 HTTPResponse，调用方不感知池的存在。

    连接归还规则：
      * readline()/read() 读到 EOF（b""）→ 响应完整 → 归还连接复用；
      * close() 提前关闭（stop/看门狗中断）或读异常 → 丢弃连接（socket 残留数据不可复用）；
      * 其余属性（fp/status/getheader/will_close…）透明转发给底层 HTTPResponse。
    """

    def __init__(self, key, ent: _Conn, resp):
        self._key = key
        self._ent = ent
        self._resp = resp
        self._done = False

    def readline(self, *args):
        return self._read(self._resp.readline, args)

    def read(self, *args):
        return self._read(self._resp.read, args)

    def close(self):
        if not self._done:
            self._discard()          # 未读完就关闭：不能复用
        self._resp.close()

    def __getattr__(self, name):
        if name.startswith("_"):     # 防初始化/内省阶段的递归委托
            raise AttributeError(name)
        return getattr(self._resp, name)

    def _read(self, fn, args):
        try:
            raw = fn(*args)
        except BaseException:
            self._discard()          # 读异常（超时/连接重置）→ 连接不可复用
            raise
        if not raw:
            self._release()
        return raw

    def _release(self):
        if self._done:
            return
        self._done = True
        ent = self._ent
        try:
            reusable = ent.conn.sock is not None and not self._resp.will_close
        except AttributeError:
            reusable = False
        if reusable:
            _put_back(self._key, ent)
        else:
            ent.conn.close()

    def _discard(self):
        if self._done:
            return
        self._done = True
        try:
            self._ent.conn.close()
        except OSError:
            pass


_POOL: dict = {}          # (scheme, host, port) -> [_Conn]，后进先出（用最近活跃的）
_LOCK = threading.Lock()
_SSL_CONTEXT: list = []   # 惰性创建的 SSLContext 单例（默认证书校验，与 urllib 一致）
_PROXY_CHECK: list = []   # 代理检测结果缓存（进程内稳定）


def _ssl_context():
    if not _SSL_CONTEXT:
        import ssl
        _SSL_CONTEXT.append(ssl.create_default_context())
    return _SSL_CONTEXT[0]


def _new_conn(scheme: str, host: str, port: int, timeout: float):
    if scheme == "https":
        return http.client.HTTPSConnection(host, port, timeout=timeout,
                                           context=_ssl_context())
    return http.client.HTTPConnection(host, port, timeout=timeout)


def _proxies_configured() -> bool:
    """http/https 代理自检：有代理时连接池让路（池不走代理，避免改写用户网络路径）。"""
    if not _PROXY_CHECK:
        try:
            import urllib.request as _ur
            proxies = _ur.getproxies() or {}
            _PROXY_CHECK.append(any(k in ("http", "https") for k in proxies))
        except (ImportError, OSError):
            _PROXY_CHECK.append(False)   # 检测失败按「无代理」：默认开启池
    return _PROXY_CHECK[0]


def pool_enabled(host: str) -> bool:
    """目标主机是否启用连接池：settings.json 能力开关 cap_http_pool（默认开）+ 代理自检。"""
    try:
        from zhuzhu_Copilot.core import agent_skills
        if not agent_skills.cap_enabled("http_pool"):
            return False             # 开关关闭对全部主机生效（含本机）
    except (ImportError, OSError, ValueError, KeyError):
        pass                         # 读设置失败不因噎废食：按默认开启
    if _is_direct_host(host):
        return True                  # 本机直连：系统代理不影响
    return not _proxies_configured()


def _acquire(key, timeout: float) -> _Conn:
    """取一条可用连接：优先池中最近归还的；过旧/已断的顺手关掉重建。"""
    now = time.monotonic()
    conn = None
    with _LOCK:
        bucket = _POOL.get(key)
        while bucket:
            ent = bucket.pop()
            if ent.conn.sock is not None and now - ent.last_used <= IDLE_RECYCLE_SECS:
                conn = ent.conn
                break
            ent.conn.close()
    if conn is None:
        return _Conn(_new_conn(*key, timeout=timeout), now)
    try:
        conn.sock.settimeout(timeout)   # 流式曾把读超时放宽过，复用前恢复连接/首包超时
    except OSError:
        pass
    return _Conn(conn, now)


def _put_back(key, ent: _Conn) -> None:
    with _LOCK:
        bucket = _POOL.setdefault(key, [])
        if len(bucket) >= MAX_IDLE_PER_HOST:
            ent.conn.close()
            return
        ent.last_used = time.monotonic()
        bucket.append(ent)


def close_idle() -> None:
    """关闭池中全部空闲连接（测试隔离用）；在途连接不受影响。"""
    with _LOCK:
        buckets = list(_POOL.values())
        _POOL.clear()
    for bucket in buckets:
        for ent in bucket:
            ent.conn.close()


def _urlopen(req, timeout: float):
    import urllib.request as _ur
    return _ur.urlopen(req, timeout=timeout)   # 必须关键字：urlopen 第二位置参数是 data


def open_stream(req, timeout: float):
    """打开流式响应（urlopen 语义，调用方按 urllib 的 HTTPResponse 使用）。

    - 返回与 HTTPResponse 接口兼容的流（池化或 urllib 原路径）；
    - 状态码 >= 400 抛 urllib.error.HTTPError（错误体已读入，e.read() 可用）；
    - 池不可用/建连失败/重定向 → 回退 urllib.urlopen（行为与优化前一致）。
    """
    parts = urlsplit(req.full_url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return _urlopen(req, timeout)
    host = parts.hostname
    if not pool_enabled(host):
        return _urlopen(req, timeout)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    key = (parts.scheme, host, port)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    ent = _acquire(key, timeout)
    try:
        ent.conn.request(req.get_method(), path, body=req.data,
                         headers=dict(req.header_items()))
        resp = ent.conn.getresponse()
    except (OSError, http.client.HTTPException):
        ent.conn.close()             # 建连/首包失败（含复用死连接被 RST）→ 回退一次
        return _urlopen(req, timeout)
    if 300 <= resp.status < 400:
        resp.close()                 # 重定向：urllib 会自动跟随，http.client 不会
        ent.conn.close()
        return _urlopen(req, timeout)
    if resp.status >= 400:
        body = b""
        try:
            body = resp.read()
        except (OSError, http.client.HTTPException):
            pass
        finally:
            resp.close()
            ent.conn.close()
        raise urllib.error.HTTPError(req.full_url, resp.status, resp.reason,
                                     resp.headers, BytesIO(body))
    return _PooledStream(key, ent, resp)