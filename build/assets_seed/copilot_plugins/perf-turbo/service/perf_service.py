"""性能服务（Copilot 插件 perf-turbo 的后台部分）。

职责只有两件事，都在 GUI 线程里做，且**任何异常都不上抛**（不能因为性能插件
把应用启动搞挂）：

  1. 安装性能内核的运行时挂钩（LLM 首字延迟 / 工具耗时 / 整轮耗时）；
  2. 起一个 33ms 的界面心跳，量「主线程响应间隔」——定时器实际触发比预期晚了
     多少，就是主线程被什么占住的直接证据（P95 与卡顿占比由此得出）。

由插件包 `copilot.json` 的 services 声明，主窗口在 GUI 线程调用 `start()`。
"""

import time

_kernel = None          # zhuzhu_Copilot.core.perf_turbo（源码级变更落进转译层后可用）
_timer = None
_last = [0.0]
_heartbeat_ms = 33


def _load_kernel():
    """取性能内核模块；未写入转译层（插件停用/未生效）时返回 None。"""
    global _kernel
    if _kernel is not None:
        return _kernel
    try:
        from zhuzhu_Copilot.core import perf_turbo
        _kernel = perf_turbo
    except Exception:
        _kernel = None
    return _kernel


def _tick():
    """心跳：记录本次与上次触发之间的真实间隔（基线 33ms）。"""
    now = time.perf_counter()
    prev = _last[0]
    _last[0] = now
    if prev <= 0:
        return
    k = _load_kernel()
    if k is not None:
        try:
            k.note_frame((now - prev) * 1000.0)
        except Exception:
            pass


def _start_heartbeat():
    """在 QApplication 上挂一个粗粒度定时器（无 GUI 环境则跳过）。"""
    global _timer
    try:
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            return False
        t = QTimer(app)
        t.setTimerType(Qt.TimerType.CoarseTimer)
        t.setInterval(_heartbeat_ms)
        t.timeout.connect(_tick)
        _last[0] = time.perf_counter()
        t.start()
        _timer = t
        return True
    except Exception:
        return False


def start():
    """安装挂钩 + 启动心跳（幂等）。返回句柄供 stop() 使用。"""
    k = _load_kernel()
    if k is not None:
        try:
            k.load_profile()
            k.install()
        except Exception:
            pass
    try:
        _start_heartbeat()
    except Exception:
        pass
    return {"stop": stop}


def stop():
    """停心跳 + 还原挂钩（停用插件/退出时调用）。"""
    global _timer
    try:
        if _timer is not None:
            _timer.stop()
            _timer.deleteLater()
    except Exception:
        pass
    _timer = None
    _last[0] = 0.0
    k = _load_kernel()
    if k is not None:
        try:
            k.uninstall()
        except Exception:
            pass
