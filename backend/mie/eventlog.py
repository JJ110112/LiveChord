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

# One definition of where the recordings live. The panel's log list reads this
# same name rather than rebuilding the path, so the list can never point
# somewhere the writer is not.
LOG_DIR = os.environ.get("MIE_LOG_DIR") or os.path.join(REPO_ROOT, "data", "logs", "mie")


def default_path() -> str:
    base = os.path.join(LOG_DIR, datetime.now().strftime("session-%Y%m%d-%H%M%S.jsonl"))
    # Two segments saved inside the same second would otherwise append to one
    # file, and "save this bit" would silently hand back two takes in one.
    if not os.path.exists(base):
        return base
    stem = base[:-len(".jsonl")]
    for i in range(2, 60):
        p = f"{stem}-{i}.jsonl"
        if not os.path.exists(p):
            return p
    return base


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
            if "__rotate__" in item:
                # Swapping the file HERE, on the writer thread, means everything
                # queued before this marker has already gone to the old file -
                # the segment boundary is exact rather than approximately where
                # the button was pressed.
                try:
                    self._fh.close()
                except Exception:
                    pass
                try:
                    self._fh = open(item["__rotate__"], "a", encoding="utf-8", buffering=1)
                    for line in item.get("__head__") or []:
                        self._fh.write(json.dumps(line, ensure_ascii=False, default=str) + chr(10))
                except Exception:
                    pass
                item["__done__"].set()
                continue
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
                    # `advice` belongs here for one reason: on the 16:43 take
                    # the density reached 4.19 generated notes per played note
                    # and the player switched five lanes off, and afterwards
                    # there was no way to tell from the log whether the advisory
                    # had said so. The one feature whose whole job is noticing
                    # that left no trace of having noticed. Only when it has
                    # something to say - it is empty almost all the time.
                    row = {"type": "snapshot", "t": s["t"], "mode": s["mode"],
                           "state": s["state"], "stats": s["stats"], "drops": s["drops"],
                           "jitter": s["jitter"]}
                    if s.get("advice"):
                        row["advice"] = [a["id"] for a in s["advice"]]
                    self.log(row)
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

    def rotate(self, summary: Optional[dict] = None, header: Optional[dict] = None) -> str:
        """Close this segment into its own file and start a new one. Returns the closed path.

        So a take can be kept without leaving the panel and pressing q at the
        console - which meant the only way to finish a recording was to stop
        the engine, in the middle of playing.
        """
        old_path = self.path
        new_path = default_path()
        if summary is not None:
            self.log({"type": "summary", **summary})
        head = []
        if header:
            head.append({"type": "session", "t": header.get("t", 0.0),
                         "started": datetime.now().isoformat(timespec="seconds"),
                         **{k: v for k, v in header.items() if k != "t"}})
        done = threading.Event()
        try:
            self._q.put({"__rotate__": new_path, "__done__": done, "__head__": head})
        except Exception:
            return old_path
        done.wait(timeout=2.0)
        self.path = new_path
        return old_path

    @property
    def stats(self) -> dict:
        return {"path": self.path, "written": self._written, "dropped": self._dropped}
