"""后台任务执行器。

界面线程只负责绘制，所有 RPC 调用都投递到这里串行执行：
一条连接上同时只能有一个未完成的请求，串行是最省心的正确做法，
也避免长任务（截图、大文件）把界面卡住。
"""

from __future__ import annotations

import queue
import threading
import traceback
from typing import Any, Callable, Dict, Optional, Tuple

from PySide6.QtCore import QObject, Signal


class TaskRunner(QObject):
    """串行任务队列。回调在界面线程触发（Qt 自动排队）。"""

    finished = Signal(int, object)
    failed = Signal(int, str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._queue: "queue.Queue[Optional[Tuple[int, Callable[[], Any]]]]" = queue.Queue()
        self._callbacks: Dict[int, Tuple[Optional[Callable[[Any], None]], Optional[Callable[[str], None]]]] = {}
        self._next = 0
        self._stopped = False
        self.finished.connect(self._dispatch_ok)
        self.failed.connect(self._dispatch_err)
        self._thread = threading.Thread(target=self._loop, name="echolink-ui-tasks", daemon=True)
        self._thread.start()

    def submit(
        self,
        fn: Callable[[], Any],
        on_done: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> int:
        self._next += 1
        token = self._next
        self._callbacks[token] = (on_done, on_error)
        self._queue.put((token, fn))
        return token

    def stop(self) -> None:
        self._stopped = True
        self._queue.put(None)

    def _loop(self) -> None:
        while not self._stopped:
            item = self._queue.get()
            if item is None:
                break
            token, fn = item
            try:
                result = fn()
            except Exception as exc:  # noqa: BLE001 - 任何异常都要回报给界面
                message = str(exc) or exc.__class__.__name__
                detail = traceback.format_exc(limit=3)
                self.failed.emit(token, f"{message}")
                _ = detail
            else:
                self.finished.emit(token, result)

    def _dispatch_ok(self, token: int, result: Any) -> None:
        done, _ = self._callbacks.pop(token, (None, None))
        if done is not None:
            done(result)

    def _dispatch_err(self, token: int, message: str) -> None:
        _, on_error = self._callbacks.pop(token, (None, None))
        if on_error is not None:
            on_error(message)
