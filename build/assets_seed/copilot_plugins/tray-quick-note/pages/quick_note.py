"""托盘动作 + 后台服务示例（Copilot 插件 tray-quick-note）。

本文件**随插件包分发**（不走源码树）：copilot.json 用 module 指向它，
主窗口启动时按文件路径动态导入并调用 entry/action 函数。

服务约定：start_service() 返回的句柄若带 stop()/close() 会被退出时调用；
模块级 stop_service() 也会被调用（先模块级、再句柄级，双保险）。
"""
import threading

_stop_evt = threading.Event()
_thread = None


def quick_note(*_args):
    """托盘动作：弹一个快速记事框（示例用消息框，真实插件可弹自定义窗口）。"""
    from PyQt6.QtWidgets import QMessageBox
    mb = QMessageBox()
    mb.setWindowTitle("快速记一句")
    mb.setText("在这里快速记录一句话（示例）。")
    mb.exec()


def start_service():
    """后台服务入口：起一个每 60s 空转的心跳线程（示例）。"""
    global _thread
    _stop_evt.clear()

    def _loop():
        while not _stop_evt.wait(60):
            pass          # 真实插件在这里做定时任务

    _thread = threading.Thread(target=_loop, daemon=True,
                               name="copilot_ext_quick_note")
    _thread.start()
    return _thread


def stop_service():
    """退出时停止服务。"""
    _stop_evt.set()
