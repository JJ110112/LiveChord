"""中央任務鎖 — scan / batch-detect / midi-import / jam-build 互斥

避免兩個磁碟密集任務同時搶 I/O 拖垮前台 player 服務。
所有 admin 觸發的長任務應 try_acquire 後才執行；auto_worker 內部排程
也透過此鎖協調，避免它在使用者手動操作時搶資源。
"""

import threading
import time
from typing import Optional


class TaskLock:
    def __init__(self):
        self._lock = threading.Lock()
        self._current: Optional[dict] = None

    def try_acquire(self, kind: str, scope: str = "ALL") -> bool:
        with self._lock:
            if self._current is not None:
                return False
            self._current = {
                "kind": kind,
                "scope": scope,
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "current_item": "",
                "done": 0,
                "total": 0,
            }
            return True

    def release(self):
        with self._lock:
            self._current = None

    def update_progress(self, current_item: str = "", done: int = 0, total: int = 0):
        with self._lock:
            if self._current is None:
                return
            if current_item:
                self._current["current_item"] = current_item
            if done:
                self._current["done"] = done
            if total:
                self._current["total"] = total

    def status(self) -> Optional[dict]:
        with self._lock:
            return dict(self._current) if self._current else None

    def is_busy(self) -> bool:
        with self._lock:
            return self._current is not None

    def current_kind(self) -> Optional[str]:
        with self._lock:
            return self._current["kind"] if self._current else None


_TASK_LOCK = TaskLock()


def get_task_lock() -> TaskLock:
    return _TASK_LOCK
