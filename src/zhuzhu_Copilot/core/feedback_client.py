"""用户反馈提交客户端：将反馈内容发送到服务器，受服务器端 IP 限流（3次/天）。

服务器地址来源（优先级，与 update_check.py 一致）：
1. app_identity.qsettings() 的 update_server
2. 环境变量 UPDATE_SERVER_URL
3. 内置默认服务器 DEFAULT_SERVER
"""
from zhuzhu_Copilot import app_identity
from zhuzhu_Copilot.update_check import DEFAULT_SERVER
from zhuzhu_Copilot.core.i18n import ui as _ui, uif as _uif
import json
import os
import threading
import urllib.error
import urllib.request

from PyQt6.QtCore import QObject, pyqtSignal

MIN_LEN = 5          # 反馈内容最少字数（客户端先校验，与服务器契约一致）
MAX_LEN = 2000       # 反馈内容最多字数（客户端先校验，与服务器契约一致）
HTTP_TIMEOUT = 15    # 请求超时（秒），反馈请求不急于秒回，但也不能无限挂住


class FeedbackClient(QObject):
    """后台提交用户反馈到服务器，结果通过 feedback_result 信号异步返回"""

    # {"status": "ok"|"rate_limited"|"error"|"network_error",
    #  "info": dict|None, "error": str|None}
    feedback_result = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        q = app_identity.qsettings()
        server = (q.value("update_server", "") or "").strip().rstrip("/")
        if not server:
            server = os.environ.get("UPDATE_SERVER_URL", "").strip().rstrip("/")
        if not server:
            server = DEFAULT_SERVER
        self.server = server

    def submit(self, content: str, contact: str = ""):
        """后台线程提交反馈（不阻塞 UI）。客户端先校验字数，减少无效请求。"""
        content = (content or "").strip()
        n = len(content)
        if n < MIN_LEN or n > MAX_LEN:
            self.feedback_result.emit({
                "status": "error", "info": None,
                "error": _uif("反馈内容需 {a0}-{a1} 字（当前 {a2} 字）", a0=MIN_LEN, a1=MAX_LEN, a2=n),
            })
            return
        threading.Thread(
            target=self._submit_worker,
            args=(content, (contact or "").strip()),
            daemon=True,
        ).start()

    def _submit_worker(self, content: str, contact: str):
        """实际发送 POST /api/feedback；按 HTTP 状态码 + 响应体判定结果。

        信号从后台线程发出，Qt 自动队列到对话框所在线程，与 update_check.py 同一模式。
        """
        try:
            body = json.dumps({"content": content, "contact": contact}).encode("utf-8")
            # 与 update_check.py 相同的浏览器 UA：服务器/Cloudflare 会拦 Python-urllib 默认 UA（403）
            req = urllib.request.Request(
                f"{self.server}/api/feedback",
                data=body,
                method="POST",
                headers={
                    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                                   "Chrome/126.0 Safari/537.36 zhuzhuCopilot"),
                    "Content-Type": "application/json",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                # 429 限流 / 400 参数错误：响应体带结构化错误描述
                try:
                    data = json.loads(e.read().decode("utf-8"))
                except Exception:
                    data = {}
                if not isinstance(data, dict):
                    data = {}
                if e.code == 429:
                    self.feedback_result.emit({
                        "status": "rate_limited", "info": data,
                        "error": data.get("error"),
                    })
                else:
                    self.feedback_result.emit({
                        "status": "error", "info": None,
                        "error": data.get("error") or _uif("服务器错误（HTTP {a0}）", a0=e.code),
                    })
                return
            if isinstance(data, dict) and data.get("ok"):
                self.feedback_result.emit({"status": "ok", "info": data, "error": None})
            else:
                self.feedback_result.emit({
                    "status": "error", "info": None,
                    "error": (data.get("error") if isinstance(data, dict) else None)
                             or _ui("服务器返回异常"),
                })
        except Exception as e:
            # 断网 / DNS / 超时等：统一按网络错误反馈
            self.feedback_result.emit({
                "status": "network_error", "info": None, "error": str(e),
            })
