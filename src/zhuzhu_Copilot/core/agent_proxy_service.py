"""代理服务生命周期：按 settings 启停本地转发器与 Windows 系统代理。

  * proxy_enabled=1 且 proxy_mode=global → 启动本地 SOCKS5/HTTP 转发器，
    并把系统代理指向它（应用内请求走 agent_proxy 直连隧道，不经过本机转发器）；
  * 其他情况 → 停止转发器并还原系统代理；
  * 启动时先还原上次异常退出遗留的系统代理快照，再按当前设置应用。
"""

import threading

from zhuzhu_Copilot.core import agent_proxy, agent_sysproxy
from zhuzhu_Copilot.core.agent_proxy_local import LocalProxyServer

_lock = threading.RLock()
_server = None


def _stop_locked() -> None:
    global _server
    if _server is not None:
        _server.stop()
        _server = None
    agent_sysproxy.restore()


def apply() -> dict:
    """按当前设置应用代理状态（幂等，可在设置保存后随时调用）。"""
    global _server
    with _lock:
        cfg = agent_proxy.config()
        if cfg is None or cfg["mode"] != "global":
            _stop_locked()
            return status_locked()
        if _server is None:
            srv = LocalProxyServer(cfg["local_port"])
            try:
                srv.start()
            except OSError:
                srv = LocalProxyServer(0)      # 端口被占用：改用临时端口
                srv.start()
            _server = srv
        agent_sysproxy.enable(_server.port)
        return status_locked()


def start_if_enabled() -> dict:
    """应用启动时调用：清理上次遗留的代理设置，再按当前配置生效。"""
    agent_sysproxy.restore()                   # 过期快照会被丢弃，不覆盖用户设置
    return apply()


def shutdown() -> None:
    """应用退出：停止转发器并还原系统代理。"""
    with _lock:
        _stop_locked()


def status() -> dict:
    with _lock:
        return status_locked()


def status_locked() -> dict:
    cfg = agent_proxy.config()
    srv = _server
    return {
        "enabled": cfg is not None,
        "mode": (cfg or {}).get("mode", "off"),
        "active": srv is not None,
        "port": srv.port if srv else 0,
        "system_proxy": agent_sysproxy.current_server(),
        "total": srv.total if srv else 0,
        "via_ssh": srv.via_ssh if srv else 0,
        "direct": srv.direct if srv else 0,
        "last_error": srv.last_error if srv else "",
    }
