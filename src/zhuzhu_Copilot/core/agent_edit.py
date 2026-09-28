"""文本编辑类工具的并发写互斥：同一文件（按解析后绝对路径）的写入串行，
不同文件可并行 —— 支撑 AI 同一轮并行编辑多个文件而不破坏同文件写入原子性。

路径锁按需创建并缓存；缓存超出上限时整体清空重建（锁对象被替换后旧的仍在
退出作用域时释放，无泄漏；并发任务正在持有的锁不受影响）。
"""

import threading

_lock = threading.Lock()
_path_locks: dict = {}
_MAX_LOCKS = 1024


def file_lock(path: str) -> threading.Lock:
    """返回目标路径的独占锁；空路径返回一次性临时锁（无实际互斥需求）。"""
    if not path:
        return threading.Lock()
    lk = _path_locks.get(path)
    if lk is not None:
        return lk
    with _lock:
        lk = _path_locks.get(path)
        if lk is None:
            lk = threading.Lock()
            _path_locks[path] = lk
            if len(_path_locks) > _MAX_LOCKS:
                _path_locks.clear()
    return lk