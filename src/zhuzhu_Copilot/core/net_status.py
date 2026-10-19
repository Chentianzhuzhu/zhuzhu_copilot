# -*- coding: utf-8 -*-
"""网络连通性判定：非阻塞、只探测应用真正依赖的端点。

为什么重写（真实故障复盘）
--------------------------
旧实现（``ui/agent_panel.py::_check_network``）每 5 秒在 **UI 线程**里
``socket.create_connection(("8.8.8.8", 53), timeout=2)``，失败再试
``223.5.5.5:53``。企业网关把公共 DNS（含 Cloudflare ``1.1.1.1``）的 53 端口
直接丢包时会出现两个后果：

1. **界面卡死**：每次检测要等满 2s × 2 = 4s 才返回，占 5s 周期的 80%。
   Qt 事件循环被反复掐住，表现为「模型 API 明明能用，但每一步操作、
   连拖动窗口都卡」——这就是本次要修的那个「离谱 bug」。
2. **误报离线**：公共 DNS 的 53 被拦 ≠ 断网。拿它当在线判据，网络完全正常
   也会显示「离线」，更新检查顺带显示「获取失败」。

现在的三条原则
--------------
1. **UI 线程零 I/O**：探测只在后台线程跑，UI 只读缓存结果。
2. **只探测应用真正要用的服务**（系统代理 / 模型 API）；
   公共 DNS 不再参与判定，探测失败也不再直接等于离线。
   **Cloudflare 节点一律排除**：CF 公共 DNS（1.1.1.1 等）被企业网关整段封锁是
   常态，挂 CF 的更新服务器同样如此 —— 探测它们只会白耗预算、拖慢退避，还让
   企业网用户看到假的「受限/离线」。
3. **多信号三态**：应用自身请求成功 > 服务端点可达 > 系统链路状态，
   最终给出 ``online`` / ``limited`` / ``offline``，把「探测被网关拦截」
   如实说成「受限」，而不是谎报「离线」。

本模块不依赖 Qt（便于单测）：UI 侧只负责起线程、收结果、画图标。
"""

from __future__ import annotations

import os
import socket
import threading
import time
import urllib.parse

# ---------------------------------------------------------------- 状态常量
STATE_UNKNOWN = "unknown"    # 尚未探测出结果（首帧）
STATE_ONLINE = "online"      # 在线
STATE_LIMITED = "limited"    # 受限：系统有链路，但应用端点不可达（多为网关/防火墙拦截）
STATE_OFFLINE = "offline"    # 离线：系统明确没有可用链路

# 单个探测目标的连接超时（秒）。短超时 + 后台线程，失败也不会拖住任何人
PROBE_TIMEOUT = 0.8
# 一轮探测的总预算（秒）：最多试到预算用完，避免目标多时后台线程无限拖长
PROBE_BUDGET = 2.4
# 应用自身请求成功后，多少秒内直接判定「在线」（最可信的证据）
TRAFFIC_TRUST = 90.0
# 轮询间隔：正常 5s；连续失败按指数退避到 30s，避免在受限网络里反复打洞
BASE_INTERVAL = 5.0
MAX_INTERVAL = 30.0
# 探测目标缓存（秒）：读设置文件有成本，且端点不会频繁变化
TARGETS_TTL = 60.0

_LOCK = threading.Lock()
_last_ok = 0.0            # 最近一次「应用请求成功」的单调时钟
_fail_streak = 0          # 连续探测失败次数（退避用）
_targets_cache: tuple = (0.0, [])


# ---------------------------------------------------------------- 应用流量
def note_success() -> None:
    """记录一次应用自身请求成功（模型 API / 更新服务器）。

    这是最强的在线证据：请求都成功了，再因为某个探测节点不通而报离线就荒谬。
    """
    global _last_ok, _fail_streak
    with _LOCK:
        _last_ok = time.monotonic()
        _fail_streak = 0


def traffic_ok(window: float = TRAFFIC_TRUST) -> bool:
    """最近 window 秒内应用自身请求是否成功过。"""
    with _LOCK:
        last = _last_ok
    return last > 0 and (time.monotonic() - last) <= window


def note_probe(ok: bool) -> None:
    """记录一轮探测结果（只影响轮询退避，不影响判定语义）。"""
    global _fail_streak
    with _LOCK:
        _fail_streak = 0 if ok else _fail_streak + 1


def fail_streak() -> int:
    with _LOCK:
        return _fail_streak


def interval(fail_count: int | None = None) -> float:
    """本轮之后应该等多久再探测：5s → 10s → 20s → 30s（封顶）。"""
    n = fail_streak() if fail_count is None else max(0, int(fail_count))
    return min(MAX_INTERVAL, BASE_INTERVAL * (2 ** min(n, 3)))


# ---------------------------------------------------------------- 探测目标
# Cloudflare 公共 DNS 与其他一票否决的节点：企业网封锁 CF 网段是常态，探测它们
# 只会拖慢探测/退避并造成误判（用户要求：连通性检测不 ping 任何 CF 相关节点）。
# 按主机名/IP 一票否决，端口无关（53/853/443 都不给探）。
_BANNED_HOSTS = frozenset({
    "1.1.1.1", "1.0.0.1",                       # Cloudflare DNS（IPv4）
    "2606:4700:4700::1111", "2606:4700:4700::1001",   # Cloudflare DNS（IPv6）
    "one.one.one.one", "cloudflare-dns.com",     # 上述 IP 的域名形式
})


def _is_banned(hp) -> bool:
    """该 (host, port) 是否属于一票否决的探测目标（Cloudflare 公共 DNS 等）。"""
    if not hp:
        return False
    return str(hp[0]).strip().lower() in _BANNED_HOSTS


def _host_port(url: str):
    """从 URL 里取 (host, port)；本机地址返回 None（探测本机说明不了外网状态）。"""
    try:
        text = str(url or "").strip()
        if not text:
            return None
        if "://" not in text:
            text = "https://" + text
        u = urllib.parse.urlsplit(text)
        host = (u.hostname or "").strip()
        if not host or host in ("localhost", "::1") or host.startswith("127."):
            return None
        port = int(u.port or (443 if u.scheme == "https" else 80))
        return host, port
    except (ValueError, TypeError):
        return None


def _proxy_endpoint():
    """系统代理端点：企业网里它才是真正的出口链路。"""
    try:
        import urllib.request
        proxies = urllib.request.getproxies() or {}
    except Exception:
        return None
    for key in ("https", "http"):
        value = proxies.get(key)
        if value:
            hp = _host_port(str(value))
            if hp:
                return hp
    return None


def _update_server_endpoint():
    """更新服务器端点：与 update_check.py 同源（QSettings → 环境变量 → 内置默认）。"""
    server = os.environ.get("UPDATE_SERVER_URL", "").strip()
    if not server:
        try:
            from zhuzhu_Copilot import app_identity
            server = str(app_identity.qsettings().value("update_server", "") or "").strip()
        except Exception:
            server = ""
    if not server:
        try:
            from zhuzhu_Copilot.update_check import DEFAULT_SERVER
            server = DEFAULT_SERVER
        except Exception:
            return None
    return _host_port(server)


def _model_endpoints():
    """当前配置的模型 API 端点（多服务商全部纳入，任一可达即说明外网通）。"""
    out: list = []
    try:
        from zhuzhu_Copilot.core import agent_skills
        model = (agent_skills.load_settings() or {}).get("model") or {}
        if not isinstance(model, dict):
            return out
        providers = [p for p in (model.get("providers") or []) if isinstance(p, dict)]
        if not providers:
            providers = [{"base_url": model.get("base_url")}]
        for p in providers:
            hp = _host_port(str(p.get("base_url") or ""))
            if hp and hp not in out:
                out.append(hp)
    except Exception:
        pass
    return out


def default_targets(force_refresh: bool = False) -> list:
    """本轮要探测的 (host, port) 列表：代理 → 模型 API。

    刻意**不含公共 DNS**（8.8.8.8 / 223.5.5.5 / 1.1.1.1 等）：它们被企业网关
    拦截是常态，而且与应用能不能工作没有必然关系——正是旧实现误报离线的根源。

    刻意**不含更新服务器**：它挂在 Cloudflare 后面（见 update_check.py），企业网
    封锁 CF 网段时探测它只会白耗预算、拖慢退避节奏（用户要求：连通性检测不碰
    任何 Cloudflare 相关节点）。更新可达性由 update_check 自己的轮询与退避负责。

    `_update_server_endpoint` 保留不删：update_check / 排障日志仍可能用到它的
    解析逻辑，只是连通性判定不再依赖。
    """
    global _targets_cache
    now = time.monotonic()
    with _LOCK:
        ts, cached = _targets_cache
        if cached and not force_refresh and (now - ts) < TARGETS_TTL:
            return list(cached)
    targets: list = []
    for hp in (_proxy_endpoint(), *_model_endpoints()):
        if hp and hp not in targets and not _is_banned(hp):
            targets.append(hp)
    with _LOCK:
        _targets_cache = (now, list(targets))
    return targets


# ---------------------------------------------------------------- 探测与判定
def tcp_reachable(host: str, port: int, timeout: float = PROBE_TIMEOUT) -> bool:
    """TCP 建连探测（不握手 TLS、不发请求：零流量、零业务副作用）。"""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _probe_targets(targets, timeout: float, budget: float) -> bool:
    deadline = time.monotonic() + max(0.1, budget)
    for host, port in targets:
        left = deadline - time.monotonic()
        if left <= 0.05:
            break
        if tcp_reachable(host, port, min(timeout, left)):
            return True
    return False


def probe(targets=None, timeout: float = PROBE_TIMEOUT,
          budget: float = PROBE_BUDGET):
    """探测应用依赖的端点（**硬预算**：到点必返回，绝不拖住调用方）。

    返回 True（有端点可达）/ False（不可达或超预算）/ None（没有可探测目标，
    例如只配置了本机代理——此时不能拿「探测失败」当离线证据）。

    为什么要再套一层线程：``socket.create_connection`` 会先做域名解析，而
    ``getaddrinfo`` **不受 socket 超时约束**。企业 DNS 卡住时，单个目标就能
    拖几秒，把「每个目标 0.8s」的预算彻底击穿（实测 3 个目标跑了 3.8s）。
    这里到点即放弃，探测线程是 daemon，自己跑完即回收。
    """
    if targets is None:
        targets = default_targets()
    # 一票否决兜底：即使调用方直接传目标，也绝不允许探测 Cloudflare 公共 DNS
    targets = [hp for hp in list(targets) if not _is_banned(hp)]
    if not targets:
        return None
    box: dict = {}

    def _run():
        try:
            box["r"] = _probe_targets(targets, timeout, budget)
        except Exception:
            box["r"] = False

    th = threading.Thread(target=_run, daemon=True)
    th.start()
    th.join(max(0.2, budget) + 0.2)
    return bool(box.get("r", False))


def decide(probed, nlm: str | None = None) -> str:
    """把多路信号合成最终状态。

    :param probed: ``True`` 端点可达 / ``False`` 全不可达 / ``None`` 未探测
    :param nlm: 系统网络可达性（Windows NLM）：``"online"`` / ``"offline"`` / ``None``
    """
    if traffic_ok():
        return STATE_ONLINE          # 应用自己的请求刚刚成功：无需再猜
    if probed is True:
        return STATE_ONLINE
    if nlm == "offline":
        return STATE_OFFLINE         # 系统明确无链路（断网/网卡关闭）
    if probed is None and nlm == "online":
        return STATE_ONLINE
    return STATE_LIMITED             # 有链路但探测被拦 / 无法判定：如实说「受限」


def reset() -> None:
    """清空进程内状态（单测隔离用）。"""
    global _last_ok, _fail_streak, _targets_cache
    with _LOCK:
        _last_ok = 0.0
        _fail_streak = 0
        _targets_cache = (0.0, [])
