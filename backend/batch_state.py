"""可重用的背景任務狀態追蹤器"""


class BatchState:
    """封裝背景批次任務的狀態，避免裸 dict + global 散落各處"""

    def __init__(self, **extra_fields):
        self._defaults = {
            "running": False,
            "started_at": "",
            "finished_at": "",
            **extra_fields,
        }
        self._state = dict(self._defaults)

    # -- 讀取 --
    def __getitem__(self, key):
        return self._state[key]

    def get(self, key, default=None):
        return self._state.get(key, default)

    @property
    def running(self) -> bool:
        return self._state["running"]

    def snapshot(self, keys: list[str] | None = None) -> dict:
        """回傳指定欄位的拷貝（預設全部）"""
        if keys:
            return {k: self._state.get(k) for k in keys}
        return dict(self._state)

    # -- 寫入 --
    def __setitem__(self, key, value):
        self._state[key] = value

    def update(self, **kw):
        self._state.update(kw)

    def reset(self):
        self._state = dict(self._defaults)
