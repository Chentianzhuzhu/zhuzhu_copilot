"""后台任务管理器（BackgroundTaskManager）。

为 AgentEngine / 子 Agent / 命令执行提供统一的后台并发执行基础设施：
- BackgroundTask：单个后台任务的数据与状态（运行中/完成/失败/已取消）
- BackgroundTaskManager：全局单例，维护任务注册表 + 线程池 + 回调分发
- 后台命令 / 后台子 Agent / 跨工作流主 Agent 均通过本管理器登记与调度

线程安全：所有共享状态（_tasks、_seq）均用 _lock 保护；任务输出通过
on_output 回调在任务线程内触发，UI 侧应自行回主线程（PyQt signal 自动排队）。

不依赖 PyQt：回调为普通 callable，UI 层用 signal 包装即可；无 UI 环境下
回调直接在工作线程执行（调用方须保证线程安全）。
"""
from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


# ---------------------------------------------------------------------------
# 任务状态常量
# ---------------------------------------------------------------------------
STATUS_PENDING = "pending"        # 已登记，尚未开始执行
STATUS_RUNNING = "running"        # 正在执行
STATUS_DONE = "done"              # 正常完成
STATUS_FAILED = "failed"          # 执行异常
STATUS_CANCELLED = "cancelled"    # 被用户/系统取消

ACTIVE_STATUSES = frozenset({STATUS_PENDING, STATUS_RUNNING})
TERMINAL_STATUSES = frozenset({STATUS_DONE, STATUS_FAILED, STATUS_CANCELLED})

# 任务类型
TYPE_COMMAND = "command"           # 后台命令（run_command run_in_background=true）
TYPE_SUBAGENT = "subagent"         # 后台子 Agent（注册式 / 临时）
TYPE_CROSS_WORKFLOW = "cross_workflow"  # 跨工作流主 Agent
TYPE_BATCH = "batch"               # 批量后台任务（多个子 Agent 并发）


# ---------------------------------------------------------------------------
# BackgroundTask
# ---------------------------------------------------------------------------
@dataclass
class BackgroundTask:
    """单个后台任务的完整状态与输出缓冲。

    输出采用增量追加模式：on_output 回调每次收到增量时同时写入 output 缓冲，
    方便后续查询 / 完成后汇总。output 为完整累积文本，delta 仅在回调瞬间有效。
    """
    task_id: str
    task_type: str = TYPE_COMMAND
    title: str = ""
    description: str = ""
    status: str = STATUS_PENDING
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    output: str = ""                 # 累积输出（线程安全追加）
    error: str = ""                  # 失败时的错误信息
    result: Any = None               # 任务完成后的结构化结果（如子 Agent 汇总）
    progress: float = 0.0            # 0.0 ~ 1.0，可选进度
    metadata: Dict[str, Any] = field(default_factory=dict)
    # 回调（均在工作线程触发；UI 侧用 signal 包装回主线程）
    on_start: Optional[Callable[["BackgroundTask"], None]] = None
    on_output: Optional[Callable[["BackgroundTask", str], None]] = None
    on_done: Optional[Callable[["BackgroundTask"], None]] = None
    on_failed: Optional[Callable[["BackgroundTask", str], None]] = None
    on_cancelled: Optional[Callable[["BackgroundTask"], None]] = None
    # 内部
    _cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    _output_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _future: Any = field(default=None, repr=False)
    _stop_fn: Optional[Callable[[], None]] = field(default=None, repr=False)

    # ---- 状态查询 ----
    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def duration(self) -> float:
        """已运行时长（秒）；未开始返回 0，已结束返回总耗时。"""
        if self.started_at is None:
            return 0.0
        end = self.finished_at or time.time()
        return max(0.0, end - self.started_at)

    # ---- 输出追加（线程安全）----
    def append_output(self, delta: str) -> None:
        if not delta:
            return
        with self._output_lock:
            self.output += delta
        if self.on_output:
            try:
                self.on_output(self, delta)
            except Exception:
                pass  # 回调异常不影响任务执行

    def set_output(self, text: str) -> None:
        with self._output_lock:
            self.output = text or ""

    # ---- 取消 ----
    def cancel(self) -> bool:
        """请求取消任务。返回 True 表示取消请求已发出（任务可能仍在运行，
        需等待工作线程检查 cancel_event 后退出）。"""
        if self.is_terminal:
            return False
        self._cancel_event.set()
        if self._stop_fn:
            try:
                self._stop_fn()
            except Exception:
                pass
        return True

    @property
    def cancelled(self) -> bool:
        return self._cancel_event.is_set()

    # ---- 状态迁移（内部用，由管理器调用）----
    def _mark_running(self) -> None:
        self.status = STATUS_RUNNING
        self.started_at = time.time()
        if self.on_start:
            try:
                self.on_start(self)
            except Exception:
                pass

    def _mark_done(self, result: Any = None) -> None:
        self.status = STATUS_DONE
        self.finished_at = time.time()
        self.result = result
        if self.on_done:
            try:
                self.on_done(self)
            except Exception:
                pass

    def _mark_failed(self, error: str) -> None:
        self.status = STATUS_FAILED
        self.finished_at = time.time()
        self.error = error or ""
        if self.on_failed:
            try:
                self.on_failed(self, error)
            except Exception:
                pass

    def _mark_cancelled(self) -> None:
        self.status = STATUS_CANCELLED
        self.finished_at = time.time()
        if self.on_cancelled:
            try:
                self.on_cancelled(self)
            except Exception:
                pass

    def to_dict(self) -> Dict[str, Any]:
        """序列化摘要（供 UI / 查询工具使用）。"""
        return {
            "task_id": self.task_id,
            "type": self.task_type,
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "progress": round(self.progress, 3),
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration": round(self.duration, 2),
            "output_preview": (self.output[-500:] if len(self.output) > 500
                                else self.output),
            "output_length": len(self.output),
            "error": self.error,
            "metadata": {k: v for k, v in self.metadata.items()
                         if isinstance(v, (str, int, float, bool, type(None)))},
        }


# ---------------------------------------------------------------------------
# BackgroundTaskManager（全局单例）
# ---------------------------------------------------------------------------
class BackgroundTaskManager:
    """后台任务管理器：统一登记、调度、查询、取消所有后台任务。

    用法：
        mgr = BackgroundTaskManager.instance()
        task = mgr.submit(
            task_type=TYPE_COMMAND,
            title="构建项目",
            fn=lambda task: _run_long_command(task),
            on_output=lambda task, delta: print(delta, end=""),
        )
        # 立即返回，不阻塞
        mgr.list_tasks()       # 查询全部
        mgr.get(task.task_id)  # 查询单个
        mgr.cancel(task.task_id)  # 取消
    """

    _instance: Optional["BackgroundTaskManager"] = None
    _instance_lock = threading.Lock()

    def __init__(self, max_workers: int = 8):
        self._lock = threading.Lock()
        self._tasks: Dict[str, BackgroundTask] = {}
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, int(max_workers)),
            thread_name_prefix="bg-task",
        )
        # 全局事件回调（所有任务都会触发；UI 侧可统一监听）
        self._global_listeners: List[Callable[[BackgroundTask, str], None]] = []

    @classmethod
    def instance(cls) -> "BackgroundTaskManager":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls, max_workers: int = 8) -> "BackgroundTaskManager":
        """测试用：重置单例。正常运行不应调用。"""
        with cls._instance_lock:
            if cls._instance is not None:
                try:
                    cls._instance._executor.shutdown(wait=False, cancel_futures=True)
                except Exception:
                    pass
            cls._instance = cls(max_workers=max_workers)
            return cls._instance

    # ---- 全局监听 ----
    def add_listener(self, fn: Callable[[BackgroundTask, str], None]) -> None:
        """添加全局监听器：回调签名 (task, event_kind)，event_kind 为
        start/output/done/failed/cancelled。UI 侧统一入口。"""
        with self._lock:
            self._global_listeners.append(fn)

    def remove_listener(self, fn: Callable[[BackgroundTask, str], None]) -> None:
        with self._lock:
            try:
                self._global_listeners.remove(fn)
            except ValueError:
                pass

    def _emit_global(self, task: BackgroundTask, kind: str) -> None:
        for fn in list(self._global_listeners):
            try:
                fn(task, kind)
            except Exception:
                pass

    # ---- 提交任务 ----
    def submit(self, task_type: str = TYPE_COMMAND, title: str = "",
               description: str = "",
               fn: Optional[Callable[[BackgroundTask], Any]] = None,
               on_start: Optional[Callable[[BackgroundTask], None]] = None,
               on_output: Optional[Callable[[BackgroundTask, str], None]] = None,
               on_done: Optional[Callable[[BackgroundTask], None]] = None,
               on_failed: Optional[Callable[[BackgroundTask, str], None]] = None,
               on_cancelled: Optional[Callable[[BackgroundTask], None]] = None,
               stop_fn: Optional[Callable[[], None]] = None,
               metadata: Optional[Dict[str, Any]] = None) -> BackgroundTask:
        """提交一个后台任务，立即返回 BackgroundTask（不等待执行）。

        fn 签名：fn(task) -> Any，在工作线程内执行；可通过 task.append_output()
        流式输出，通过 task.cancelled 检查取消请求，通过 task.progress 更新进度。
        fn 返回值存入 task.result；抛出异常则任务标记为 failed。

        stop_fn：可选的强制停止函数（如终止子进程），cancel 时调用。
        """
        task_id = "bg_" + uuid.uuid4().hex[:12]
        task = BackgroundTask(
            task_id=task_id,
            task_type=task_type,
            title=title or f"后台任务 {task_id[-6:]}",
            description=description or "",
            on_start=on_start,
            on_output=on_output,
            on_done=on_done,
            on_failed=on_failed,
            on_cancelled=on_cancelled,
            metadata=dict(metadata or {}),
        )
        task._stop_fn = stop_fn
        with self._lock:
            self._tasks[task_id] = task
        # 提交新任务时自动清理超过1小时的已结束任务，防止任务列表无限增长
        try:
            self.cleanup_finished(max_age_seconds=3600.0)
        except Exception:
            pass

        def _runner():
            task._mark_running()
            self._emit_global(task, "start")
            try:
                if task.cancelled:
                    task._mark_cancelled()
                    self._emit_global(task, "cancelled")
                    return
                result = fn(task) if fn else None
                if task.cancelled:
                    task._mark_cancelled()
                    self._emit_global(task, "cancelled")
                else:
                    task._mark_done(result)
                    self._emit_global(task, "done")
            except Exception as e:
                if task.cancelled:
                    task._mark_cancelled()
                    self._emit_global(task, "cancelled")
                else:
                    task._mark_failed(str(e))
                    self._emit_global(task, "failed")

        task._future = self._executor.submit(_runner)
        return task

    # ---- 查询 ----
    def get(self, task_id: str) -> Optional[BackgroundTask]:
        with self._lock:
            return self._tasks.get(task_id)

    def list_tasks(self, task_type: Optional[str] = None,
                   active_only: bool = False) -> List[BackgroundTask]:
        with self._lock:
            tasks = list(self._tasks.values())
        if task_type:
            tasks = [t for t in tasks if t.task_type == task_type]
        if active_only:
            tasks = [t for t in tasks if t.is_active]
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return tasks

    def list_active(self) -> List[BackgroundTask]:
        return self.list_tasks(active_only=True)

    # ---- 取消 / 停止 ----
    def cancel(self, task_id: str) -> bool:
        task = self.get(task_id)
        if task is None:
            return False
        return task.cancel()

    def cancel_all(self, task_type: Optional[str] = None) -> int:
        """取消所有（或指定类型）的活跃后台任务。返回取消请求数量。"""
        count = 0
        for t in self.list_tasks(task_type=task_type, active_only=True):
            if t.cancel():
                count += 1
        return count

    # ---- 清理 ----
    def cleanup_finished(self, max_age_seconds: float = 3600.0) -> int:
        """清理已结束且超过 max_age 的任务，释放内存。返回清理数量。"""
        now = time.time()
        removed = []
        with self._lock:
            for tid, t in list(self._tasks.items()):
                if t.is_terminal and t.finished_at and \
                        (now - t.finished_at) > max_age_seconds:
                    del self._tasks[tid]
                    removed.append(tid)
        return len(removed)

    def shutdown(self, wait: bool = True) -> None:
        with self._lock:
            self._executor.shutdown(wait=wait, cancel_futures=not wait)


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------
def bg_manager() -> BackgroundTaskManager:
    """获取全局后台任务管理器单例。"""
    return BackgroundTaskManager.instance()


def is_background_capable_tool(tool_schema: dict) -> bool:
    """检查工具 schema 是否标记为支持后台执行。

    约定：工具定义的 function 字典中可含 "background_capable": true 字段；
    或 parameters.properties 中含 "run_in_background" 参数。
    """
    if not isinstance(tool_schema, dict):
        return False
    fn = tool_schema.get("function") or {}
    if fn.get("background_capable"):
        return True
    params = (fn.get("parameters") or {}).get("properties") or {}
    return "run_in_background" in params


def assess_task_complexity(user_input: str, estimated_tool_calls: int = 0,
                           has_long_running: bool = False) -> dict:
    """评估任务复杂度，决定是否应后台执行。

    返回 {level, should_background, reasons}：
    - level: simple / medium / complex
    - should_background: bool，是否建议后台执行
    - reasons: list[str]，判定依据

    判定规则（满足任一即 complex → 后台）：
    1. 预计工具调用数 >= 5
    2. 涉及长时间运行命令（构建/编译/训练/下载/安装等关键词）
    3. 用户输入含"后台""异步""不阻塞""继续"等明确意图
    4. 输入长度 > 200 字符且含多步骤描述（数字编号/分号/顿号分隔）
    5. 涉及子 Agent 派发（dispatch/子agent/并发/并行 关键词）
    """
    reasons = []
    score = 0
    text = (user_input or "").strip()
    text_lower = text.lower()

    # 规则 1：预计工具调用数
    if estimated_tool_calls >= 5:
        score += 2
        reasons.append(f"预计需要 {estimated_tool_calls} 次以上工具调用")
    elif estimated_tool_calls >= 3:
        score += 1

    # 规则 2：长时间运行命令关键词
    long_running_keywords = [
        "构建", "编译", "build", "compile", "make", "npm install", "pip install",
        "训练", "train", "下载", "download", "安装", "install", "打包", "打包",
        "部署", "deploy", "测试", "test", "运行", "run", "启动", "start",
        "爬虫", "crawl", "scrape", "批量", "batch", "迁移", "migrate",
    ]
    hit_long = [kw for kw in long_running_keywords if kw in text_lower]
    if hit_long or has_long_running:
        score += 2
        if hit_long:
            reasons.append(f"涉及长时间运行操作：{', '.join(hit_long[:3])}")
        if has_long_running:
            reasons.append("包含已知长时间运行的工具调用")

    # 规则 3：用户明确要求后台
    bg_keywords = ["后台", "异步", "不阻塞", "不影响", "继续", "边...边",
                    "background", "async", "同时", "并行执行", "不耽误"]
    hit_bg = [kw for kw in bg_keywords if kw in text_lower]
    if hit_bg:
        score += 3
        reasons.append(f"用户明确要求后台/异步执行：{', '.join(hit_bg[:3])}")

    # 规则 4：多步骤复杂输入
    if len(text) > 200:
        step_indicators = ["1.", "2.", "3.", "①", "②", "③", "首先", "其次",
                           "然后", "最后", "；", "、", "和", "以及"]
        if any(ind in text for ind in step_indicators):
            score += 1
            reasons.append("输入包含多步骤描述")

    # 规则 5：子 Agent / 并发关键词
    subagent_keywords = ["子agent", "子 agent", "派发", "dispatch", "并发",
                          "并行", "多agent", "组队", "工作流", "跨工作流"]
    hit_sub = [kw for kw in subagent_keywords if kw in text_lower]
    if hit_sub:
        score += 2
        reasons.append(f"涉及子 Agent / 并发派发：{', '.join(hit_sub[:3])}")

    # 定级
    if score >= 4:
        level = "complex"
        should_background = True
    elif score >= 2:
        level = "medium"
        should_background = False  # 中等任务默认前台，用户可手动指定
    else:
        level = "simple"
        should_background = False

    if not reasons:
        reasons.append("任务简单，适合前台同步执行")

    return {
        "level": level,
        "should_background": should_background,
        "score": score,
        "reasons": reasons,
    }
