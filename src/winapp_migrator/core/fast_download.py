"""多线程分段高速下载（纯标准库 urllib，无第三方依赖）

原理：HTTP Range 将文件切成若干段，用固定数量的 worker 并发下载，完成后按序合并。
- worker 池领取式：段任务放进队列，空闲连接自动领取下一段——快的连接多干、
  慢的少干，整体速度平滑稳定、贴近带宽上限（避免固定分段的"最慢段拖累"）
- 段按 1MB 下限自动切分（大文件切更多段，小文件不浪费并发）
- 服务器不支持 Range / 文件过小时自动回退单线程
- 支持暂停/恢复（分片断点续传）与取消；分片写 .part 临时文件，完成后合并删除
"""

import glob
import os
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Optional

DEFAULT_SEGMENTS = 32          # 默认并发连接数（UI 档位：32/16/8）
MIN_SEGMENT_BYTES = 1024 * 1024  # 每段下限 1MB：小于 1MB 不分段 / 按此切分
MAX_SEGMENTS = 512             # 段数上限，避免碎片过多
SEGMENT_RETRY = 2              # 每段失败重试次数
READ_CHUNK = 1024 * 1024       # 1MB 读缓冲，减少锁竞争与 IO 次数
_TIMEOUT = 60
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def _sanitize_name(name: str) -> str:
    """清理 Windows 非法文件名符号，保留扩展名"""
    name = re.sub(r'[\\/:*?"<>|]', "_", name).strip().strip(".")
    return name or f"download_{int(time.time())}.bin"


def _filename_from(url: str, content_disp: str = "") -> str:
    """解析文件名：Content-Disposition > URL 最后一段 > 时间戳"""
    if content_disp:
        for part in content_disp.split(";"):
            part = part.strip()
            if part.lower().startswith("filename="):
                name = part.split("=", 1)[1].strip('"').strip("'")
                if name:
                    return _sanitize_name(name)
    name = url.split("?")[0].rstrip("/").split("/")[-1]
    return _sanitize_name(name)


class DownloadTask:
    """一个下载任务。状态字段线程安全，UI 侧轮询 snapshot() 刷新"""

    def __init__(self, url: str, dest_dir: str, segments: int = DEFAULT_SEGMENTS,
                 display_name: str = ""):
        self.url = url
        self.dest_dir = dest_dir
        self.filename = ""            # 探测后确定
        self.path = ""                # 最终文件完整路径
        self.total = 0
        self.done = 0                 # 已下载字节
        self.status = "pending"       # pending/downloading/done/error/canceled/paused
        self.error = ""
        self.mode = "single"          # multi=分段并行 / single=单线程（探测后更新）
        self._display_name = display_name or ""
        self._segments = segments
        self._cancel = threading.Event()
        self._pause = threading.Event()
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._range_ok = False

    # ---------- 线程安全访问 ----------
    def _update_done(self, n: int):
        with self._lock:
            self.done += n

    def _set_status(self, s: str, err: str = ""):
        with self._lock:
            self.status = s
            self.error = err

    def cancel(self):
        """取消下载：清理分片并更新状态。

        下载中：设置取消事件，线程在下一次读取循环时退出并清理；
        已暂停：无活跃线程，直接清理分片并置为已取消。
        """
        self._cancel.set()
        self._pause.clear()
        if self.snapshot()["status"] == "paused":
            if self.path:
                self._cleanup(glob.glob(self.path + ".part*"))
            self._set_status("canceled")

    def pause(self):
        """暂停下载：保留已下载分片（断点续传的前提）"""
        self._pause.set()

    def resume(self):
        """恢复下载：从分片已写位置继续 Range 续传"""
        if self.snapshot()["status"] != "paused":
            return
        self._pause.clear()
        self._thread = threading.Thread(target=self._resume_run, daemon=True)
        self._thread.start()

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def join(self, timeout: Optional[float] = None):
        if self._thread:
            self._thread.join(timeout)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "status": self.status, "total": self.total, "done": self.done,
                "filename": self.filename, "path": self.path, "error": self.error,
                "mode": self.mode, "segments": self._segments,
                "display_name": self._display_name,
            }

    @property
    def _cancelled(self) -> bool:
        return self._cancel.is_set()

    # ---------- 下载 ----------
    def _open(self, method: str = "GET", headers: Optional[dict] = None):
        h = {"User-Agent": _UA}
        if headers:
            h.update(headers)
        req = urllib.request.Request(self.url, method=method, headers=h)
        return urllib.request.urlopen(req, timeout=_TIMEOUT)

    def _probe_head(self) -> bool:
        """HEAD 探测大小与 Range 支持"""
        try:
            with self._open("HEAD") as resp:
                headers = dict(resp.headers)
        except (urllib.error.HTTPError, urllib.error.URLError):
            return False
        length = headers.get("Content-Length")
        self.total = int(length) if length and length.isdigit() else 0
        self.filename = _filename_from(self.url, headers.get("Content-Disposition", ""))
        return headers.get("Accept-Ranges", "").lower() == "bytes" and self.total > 0

    def _probe_range(self) -> bool:
        """GET Range: bytes=0-0 探测（HEAD 不可用时的回退）"""
        try:
            with self._open("GET", {"Range": "bytes=0-0"}) as resp:
                if resp.status != 206:
                    return False
                cr = resp.headers.get("Content-Range", "")
                total = cr.rsplit("/", 1)[-1] if "/" in cr else ""
                self.total = int(total) if total.isdigit() else 0
                self.filename = _filename_from(self.url, resp.headers.get("Content-Disposition", ""))
                return self.total > 0
        except (urllib.error.HTTPError, urllib.error.URLError):
            return False

    def _run(self):
        try:
            self._set_status("downloading")
            self._range_ok = self._probe_head() or self._probe_range()
            if not self._range_ok or self.total <= MIN_SEGMENT_BYTES:
                self.mode = "single"
                self._download_single(self._range_ok)
            else:
                self.mode = "multi"
                self._download_multi()
        except urllib.error.HTTPError as e:
            self._set_status("error", f"HTTP {e.code} {e.reason}")
        except urllib.error.URLError as e:
            self._set_status("error", f"网络错误 {e.reason}")
        except Exception as e:
            self._set_status("error", str(e))

    def _resume_run(self):
        """暂停后恢复：按既有模式从分片位置继续"""
        try:
            self._set_status("downloading")
            if self.mode == "multi":
                self._download_multi()
            else:
                self._download_single(self._range_ok)
        except urllib.error.HTTPError as e:
            self._set_status("error", f"HTTP {e.code} {e.reason}")
        except urllib.error.URLError as e:
            self._set_status("error", f"网络错误 {e.reason}")
        except Exception as e:
            self._set_status("error", str(e))

    def _cleanup(self, paths):
        items = paths if isinstance(paths, (list, tuple)) else [paths]
        for p in items:
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass

    def _download_single(self, can_resume: bool):
        """单线程下载。can_resume=True 且存在分片时从断点续传（需服务器支持 Range）"""
        self.path = os.path.join(self.dest_dir, self.filename)
        tmp = self.path + ".part"
        written = os.path.getsize(tmp) if os.path.exists(tmp) else 0
        if not can_resume:
            self._cleanup(tmp)  # 服务器不支持 Range：无法续传，从头重下
            written = 0
        try:
            headers = {"Range": f"bytes={written}-"} if can_resume and written else {}
            with self._open("GET", headers) as resp, open(tmp, "ab" if written else "wb") as f:
                while True:
                    if self._cancelled:
                        raise InterruptedError("已取消")
                    if self._pause.is_set():
                        break  # 暂停：保留分片，等待恢复
                    chunk = resp.read(READ_CHUNK)
                    if not chunk:
                        break
                    f.write(chunk)
                    self._update_done(len(chunk))
            if self._pause.is_set():
                self._set_status("paused")
            else:
                os.replace(tmp, self.path)
                self._set_status("done")
        except InterruptedError:
            self._cleanup(tmp)
            self._set_status("canceled")

    def _download_multi(self):
        """动态 worker 池分段下载。

        段按 1MB 下限自动切分（多段任务），固定数量的 worker 从队列领取任务：
        快的连接自动多干、慢的少干，整体速度平滑且不受单段拖累。
        失败段重试后由串行补下兜底，最终严格校验每段完整性再合并。
        """
        self.path = os.path.join(self.dest_dir, self.filename)
        n = max(self._segments, self.total // MIN_SEGMENT_BYTES)
        n = min(n, MAX_SEGMENTS)
        seg_size = (self.total + n - 1) // n
        ranges = [(i * seg_size, min((i + 1) * seg_size - 1, self.total - 1)) for i in range(n)]
        part_paths = [f"{self.path}.part{i}" for i in range(n)]

        queue = list(range(n))
        failed = []
        q_lock = threading.Lock()
        workers = min(self._segments, n)

        def _worker():
            while True:
                if self._cancelled or self._pause.is_set():
                    return
                with q_lock:
                    if not queue:
                        return
                    idx = queue.pop(0)
                if not self._download_segment(ranges[idx][0], ranges[idx][1], part_paths[idx]):
                    with q_lock:
                        failed.append(idx)

        threads = [threading.Thread(target=_worker, daemon=True) for _ in range(workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        if self._cancelled:
            self._cleanup(part_paths)
            self._set_status("canceled")
            return
        if self._pause.is_set():
            self._set_status("paused")  # 暂停：保留全部已下载分片
            return

        # 失败段串行补下：服务器限制单 IP 并发数时部分段会失败，
        # 降级为串行续传保证不整体失败（比限并发下的多段重试更稳更快）
        for idx in list(failed):
            if self._cancelled:
                break
            start, end = ranges[idx]
            self._repair_segment(start, end, part_paths[idx])
        if self._cancelled:
            self._cleanup(part_paths)
            self._set_status("canceled")
            return
        if self._pause.is_set():
            self._set_status("paused")
            return
        # 严格校验每段完整，避免合并出损坏文件
        for i, (start, end) in enumerate(ranges):
            if not os.path.exists(part_paths[i]) or \
                    os.path.getsize(part_paths[i]) < end - start + 1:
                self._cleanup(part_paths)
                self._set_status("error", "分片下载失败（服务器限制连接数或中断连接）")
                return
        try:
            with open(self.path, "wb") as out:
                for pp in part_paths:
                    with open(pp, "rb") as f:
                        while True:
                            chunk = f.read(READ_CHUNK)
                            if not chunk:
                                break
                            out.write(chunk)
                    os.remove(pp)
            self._set_status("done")
        except InterruptedError:
            self._cleanup(part_paths)
            self._set_status("canceled")
        except OSError as e:
            self._cleanup(part_paths)
            self._set_status("error", f"合并失败 {e}")

    def _download_segment(self, start: int, end: int, part_path: str) -> bool:
        """下载一个分片（断点续传：已存在的分片从当前大小位置继续）。
        完整下载成功返回 True，失败/被中断返回 False。"""
        written = os.path.getsize(part_path) if os.path.exists(part_path) else 0
        if written >= end - start + 1:
            return True  # 该段已完整（暂停恢复场景直接跳过）
        headers = {"Range": f"bytes={start + written}-{end}"}
        for _ in range(SEGMENT_RETRY + 1):
            if self._cancelled or self._pause.is_set():
                return False
            try:
                with self._open("GET", headers) as resp, open(part_path, "ab") as f:
                    while True:
                        if self._cancelled or self._pause.is_set():
                            return False
                        chunk = resp.read(READ_CHUNK)
                        if not chunk:
                            break
                        f.write(chunk)
                        self._update_done(len(chunk))
                written = os.path.getsize(part_path)
                if written >= end - start + 1:
                    return True  # 该段完整下载完成
                headers = {"Range": f"bytes={start + written}-{end}"}  # 部分成功，续传剩余
            except Exception:
                time.sleep(0.5)
        return False  # 重试耗尽：段不完整，由补下阶段处理

    def _repair_segment(self, start: int, end: int, part_path: str):
        """单线程续传补下失败段：从已写位置继续 Range 下载"""
        for _ in range(SEGMENT_RETRY + 1):
            if self._cancelled or self._pause.is_set():
                return
            written = os.path.getsize(part_path) if os.path.exists(part_path) else 0
            if written >= end - start + 1:
                return
            try:
                headers = {"Range": f"bytes={start + written}-{end}"}
                with self._open("GET", headers) as resp, open(part_path, "ab") as f:
                    while True:
                        if self._cancelled or self._pause.is_set():
                            return
                        chunk = resp.read(READ_CHUNK)
                        if not chunk:
                            break
                        f.write(chunk)
                        self._update_done(len(chunk))
            except Exception:
                time.sleep(0.5)
