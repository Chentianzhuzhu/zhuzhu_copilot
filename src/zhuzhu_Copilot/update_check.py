"""客户端自动更新检查：每 30s 轮询服务器更新 API，发现新版本发信号提示。

服务器地址来源（优先级）：
1. app_identity.qsettings() 的 update_server
2. 环境变量 UPDATE_SERVER_URL（如 https://example.com）
3. 内置默认服务器 DEFAULT_SERVER（保证首次启动必检）
"""
from zhuzhu_Copilot import app_identity
import json
import os
import threading
import urllib.request

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

APP_VERSION = "5.1.7"          # 当前客户端版本（发新版时同步修改）
POLL_INTERVAL_MS = 30_000      # 30s 轮询
DEFAULT_SERVER = "https://chentian.dpdns.org"   # 默认更新服务器（可被 QSettings/环境变量覆盖）


class UpdateChecker(QObject):
    """轮询更新服务器，发现新版本（未提示过）时发出 update_found 信号"""

    update_found = pyqtSignal(dict)   # latest/notes/size/force/url
    manual_result = pyqtSignal(dict)  # 手动检查结果：status=ok|none|noserver|busy|error

    def __init__(self, parent=None):
        super().__init__(parent)
        q = app_identity.qsettings()
        server = (q.value("update_server", "") or "").strip().rstrip("/")
        if not server:
            server = os.environ.get("UPDATE_SERVER_URL", "").strip().rstrip("/")
        if not server:
            server = DEFAULT_SERVER
        self.server = server
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self.check)
        self._notified = set()   # 本次运行已提示过的版本，避免重复弹窗
        self._busy = False       # 请求进行中，防止并发检查

    def start(self):
        """启动轮询：首次启动立即检查一次，之后每 30s 巡检"""
        if not self.server:
            return
        self.check()          # 首次启动必须检查（调用服务器 API）
        self._timer.start()

    def check(self, manual: bool = False):
        """检查更新。manual=True 时结果通过 manual_result 信号反馈，供手动按钮使用"""
        if not self.server:
            if manual:
                self.manual_result.emit({"status": "noserver", "info": None})
            return
        if self._busy:
            if manual:
                self.manual_result.emit({"status": "busy", "info": None})
            return
        self._busy = True
        threading.Thread(target=self._check_worker, args=(manual,), daemon=True).start()

    def _check_worker(self, manual: bool = False):
        try:
            url = (f"{self.server}/api/update/check?"
                   f"version={APP_VERSION}&platform=windows")
            # 服务器/Cloudflare 会拦截 Python-urllib 默认 UA（403），伪装桌面浏览器 UA
            req = urllib.request.Request(url, headers={
                "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/126.0 Safari/537.36 zhuzhuCopilot")})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if not data.get("hasUpdate"):
                if manual:
                    self.manual_result.emit({"status": "none", "info": None})
                return
            ver = str(data.get("latest") or "")
            if not ver:
                if manual:
                    self.manual_result.emit({"status": "none", "info": None})
                return
            info = {
                "latest": ver,
                "notes": data.get("notes") or "",
                "size": data.get("size") or 0,
                "force": bool(data.get("force")),
                "url": self.server + (data.get("url") or ""),
            }
            if manual:
                self.manual_result.emit({"status": "ok", "info": info})
                return
            if ver in self._notified:
                return
            self._notified.add(ver)
            self.update_found.emit(info)
        except Exception as e:
            # 自动轮询：静默失败，下轮重试；手动检查：反馈失败原因
            if manual:
                self.manual_result.emit({"status": "error", "info": None, "error": str(e)})
        finally:
            self._busy = False
