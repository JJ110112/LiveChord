"""Session event log: what the engine did while someone was playing.

Python logging records faults. It says nothing about the music, so after a
performance there was no way to look at what the engine actually did - which
edges fired, what it dropped, how the energy moved. This writes one JSON object
per line to `data/logs/mie/`, which is git-ignored, plus a state snapshot every
second so the harmonic context can be read back alongside the events.

Writing happens on its own thread: `_ui()` runs on the engine and scheduler
threads and must never touch the disk.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
from datetime import datetime
from typing import Optional

from .graph import REPO_ROOT

LOG_DIR = os.path.join(REPO_ROOT, "data", "logs", "mie")


def default_path() -> str:
    return os.path.join(LOG_DIR, datetime.now().strftime("session-%Y%m%d-%H%M%S.jsonl"))


class EventLog:
    """Append-only JSONL sink. `log(ev)` is safe to call from any thread."""

    def __init__(self, path: Optional[str] = None, *, snapshot_every_s: float = 1.0):
        self.path = path or default_path()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.snapshot_every_s = snapshot_every_s
        self._q: "queue.Queue[Optional[dict]]" = queue.Queue(maxsize=20000)
        self._fh = open(self.path, "a", encoding="utf-8", buffering=1)
        self._stop = threading.Event()
        self._dropped = 0
        self._written = 0
        self._th = threading.Thread(target=self._run, name="mie-eventlog", daemon=True)
        self._th.start()

    def log(self, ev: dict) -> None:
        try:
            self._q.put_nowait(ev)
        except queue.Full:
            self._dropped += 1      # never block a MIDI thread on the disk

    def header(self, **kw) -> None:
        self.log({"type": "session", "t": 0.0, "started": datetime.now().isoformat(timespec="seconds"), **kw})

    def _run(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                break
            try:
                self._fh.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")
                self._written += 1
            except Exception:
                pass                # a broken log must never take the engine down

    def snapshots_from(self, engine, stop: threading.Event) -> threading.Thread:
        """Periodic state lines, so the events can be read in context."""
        def loop():
            while not stop.is_set() and not self._stop.is_set():
                time.sleep(self.snapshot_every_s)
                try:
                    s = engine.snapshot()
                    self.log({"type": "snapshot", "t": s["t"], "mode": s["mode"],
                              "state": s["state"], "stats": s["stats"], "drops": s["drops"],
                              "jitter": s["jitter"]})
                except Exception:
                    pass
        th = threading.Thread(target=loop, name="mie-eventlog-snap", daemon=True)
        th.start()
        return th

    def close(self, summary: Optional[dict] = None) -> None:
        if summary is not None:
            self.log({"type": "summary", **summary})
        self._stop.set()
        self._q.put(None)
        self._th.join(timeout=2.0)
        try:
            self._fh.close()
        except Exception:
            pass

    @property
    def stats(self) -> dict:
        return {"path": self.path, "written": self._written, "dropped": self._dropped}
