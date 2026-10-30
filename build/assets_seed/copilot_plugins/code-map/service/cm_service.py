"""代码地图服务（Copilot 插件 code-map 的后台部分）。

只做一件事：**预热 + 定时刷新扫描缓存**，让「代码地图」页打开就是数据，不必等扫描。

配置读插件包内的 `config.json`（直接按 `__file__` 定位，不走任何注册接口）：

  refresh_minutes  刷新间隔（分钟，0 = 只预热一次，默认 10）
  max_depth        扫描深度上限（默认 4）
  exclude          额外排除的目录名，逗号分隔

全部异常自行吞掉：性能/统计类插件绝不能把应用启动搞挂。
"""

import json
import threading
import time
from pathlib import Path

_kernel = None
_worker = None
_last = [0.0]
_started = [False]
_timer_ref = [None]

_DEFAULT_CFG = {"refresh_minutes": 10, "max_depth": 4, "exclude": ""}


def _config() -> dict:
    """读插件包内 config.json（缺失/损坏 → 默认值）。"""
    cfg = dict(_DEFAULT_CFG)
    try:
        p = Path(__file__).resolve().parent.parent / "config.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for k in cfg:
                if k in data:
                    cfg[k] = data[k]
    except (OSError, ValueError):
        pass
    return cfg


def _load_kernel():
    global _kernel
    if _kernel is not None:
        return _kernel
    try:
        from zhuzhu_Copilot.core import code_map
        _kernel = code_map
    except Exception:
        _kernel = None
    return _kernel


def _depth() -> int:
    try:
        return max(1, min(6, int(_config().get("max_depth") or 4)))
    except (TypeError, ValueError):
        return 4


def _warm(reason: str = "startup") -> None:
    """后台线程扫描一次并写缓存（同一时刻只跑一个扫描）。"""
    global _worker
    k = _load_kernel()
    if k is None:
        return
    if _worker is not None and _worker.is_alive():
        return
    try:
        root = k.load_root()
    except Exception:
        root = ""
    if not root:
        return                     # 还没选过目录：等服务被页面/工具叫醒再说
    _last[0] = time.time()
    depth = _depth()

    def _run():
        try:
            k.cache_scan(root, max_depth=depth)
        except Exception:
            pass

    _worker = threading.Thread(target=_run, daemon=True,
                               name="code-map-warm-%s" % reason)
    _worker.start()


def start():
    """预热 + 起定时刷新（幂等）。返回句柄供 stop() 使用。"""
    if _started[0]:
        return {"stop": stop}
    _started[0] = True
    _warm("startup")
    try:
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import QApplication
        minutes = _config().get("refresh_minutes")
        try:
            minutes = float(minutes)
        except (TypeError, ValueError):
            minutes = 10.0
        if minutes > 0 and QApplication.instance() is not None:
            t = QTimer(QApplication.instance())
            t.setTimerType(Qt.TimerType.VeryCoarseTimer)
            t.setInterval(max(60_000, int(minutes * 60_000)))
            t.timeout.connect(lambda: _warm("timer"))
            t.start()
            _timer_ref[0] = t
    except Exception:
        pass
    return {"stop": stop}


def stop():
    """停定时器（停用插件/退出时调用）。"""
    t = _timer_ref[0]
    if t is not None:
        try:
            t.stop()
            t.deleteLater()
        except Exception:
            pass
    _timer_ref[0] = None
    _started[0] = False
