"""评分窗服务（Copilot 插件 agent-scorecard 的应用扩展）。

由插件包 `copilot.json` 的 `services` 声明，主窗口启动时在 **GUI 线程**调用
`start()`；退出时调用 `stop()`。

职责：包装 `AgentEngine.run`——任务结束时（含异常/取消）采集指标，回到 GUI
线程弹出评分窗；用户提交后把「评分 + 评语 + 指标」发回对话，提示 Agent 反省改进。
"""
import threading

_bridge = None
_orig_run = None
_patched = False


def _make_bridge():
    """GUI 线程创建的信号桥：worker 线程 emit → GUI 线程弹窗。"""
    from PyQt6.QtCore import QObject, pyqtSignal

    class _Bridge(QObject):
        scored = pyqtSignal(object)     # dict 快照

        def __init__(self):
            super().__init__()
            self.scored.connect(self._show)

        def _show(self, snap):
            try:
                from zhuzhu_Copilot.core import agent_scorecard
                from PyQt6.QtWidgets import QApplication
                panel = getattr(QApplication.instance(), "_agent_panel", None)
                dlg = agent_scorecard.build_dialog(snap, parent=panel)
                if dlg.exec() != dlg.DialogCode.Accepted:
                    return
                text = agent_scorecard.feedback_text(snap, dlg.rating, dlg.comment)
                _send_to_agent(panel, text)
            except Exception:
                pass

    return _Bridge()


def _send_to_agent(panel, text: str):
    """把评分反馈作为用户消息发回当前会话（走面板既有发送链路）。"""
    if panel is None:
        return
    try:
        panel.input.setPlainText(text)
        panel._send()
    except Exception:
        pass


def start():
    """安装钩子（幂等）。返回句柄供 stop() 使用。"""
    global _bridge, _orig_run, _patched
    if _patched:
        return None
    from zhuzhu_Copilot.core import agent_engine, agent_scorecard
    _bridge = _make_bridge()
    _orig_run = agent_engine.AgentEngine.run

    def _run(self, *args, **kwargs):
        agent_scorecard.begin(self)
        try:
            return _orig_run(self, *args, **kwargs)
        finally:
            try:
                snap = agent_scorecard.snapshot(self)
                if _bridge is not None and int(snap.get("calls") or 0) > 0:
                    _bridge.scored.emit(snap)     # 排队到 GUI 线程弹窗
            except Exception:
                pass

    agent_engine.AgentEngine.run = _run
    _patched = True
    return {"stop": stop}


def stop():
    """卸载钩子（退出/停用插件时调用）。"""
    global _patched, _bridge
    try:
        from zhuzhu_Copilot.core import agent_engine
        if _patched and _orig_run is not None:
            agent_engine.AgentEngine.run = _orig_run
    except Exception:
        pass
    _patched = False
    _bridge = None
