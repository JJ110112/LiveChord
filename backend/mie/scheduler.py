"""Scheduler (plan §1 thread model, §7-8): min-heap of due MIDI messages.

Notes only enter as (note_on, note_off) pairs.  The heap holds `Due` items
that reference a shared `NotePair`, so a late re-snap of the note_on (plan
§10 late-binding constraint) automatically moves the note_off with it.

`pump(now)` is the whole algorithm; `run()` just calls it from a thread with
the same wait-then-spin loop the Phase 0 probe validated (p95 0.55 ms).
"""

from __future__ import annotations

import heapq
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass(slots=True)
class NotePair:
    ch: int
    note: int
    vel: int
    t_on: float
    t_off: float
    lane: str = "gen"
    origin: str = "GENERATIVE"
    root_id: int = 0
    parent_id: Optional[int] = None
    hop: int = 1
    edge_id: str = ""
    constraint: str = "free"
    follow_off: bool = False
    src_note: Optional[int] = None
    max_dur: float = 8.0
    on_sent: bool = False
    off_sent: bool = False
    dropped: bool = False
    ttl_wall: float = float("inf")
    collision: str = "octave"
    voice_lead: str = "off"


@dataclass(order=True)
class Due:
    t: float
    seq: int
    kind: str = field(compare=False)          # on | off | cc | raw
    pair: Optional[NotePair] = field(compare=False, default=None)
    payload: Optional[object] = field(compare=False, default=None)


class Scheduler:
    SPIN_S = 0.0015

    def __init__(self, clock: Callable[[], float], emit: Callable[[Due, float], None],
                 before_on: Optional[Callable[[NotePair, float], bool]] = None):
        """`emit(due, now)` performs the send; `before_on(pair, now)` may re-snap
        pair.note and returns False to drop the whole pair."""
        self.clock = clock
        self.emit = emit
        self.before_on = before_on
        self.heap: list[Due] = []
        self.seq = 0
        self.lock = threading.Lock()
        self.cv = threading.Condition(self.lock)
        self.stop = threading.Event()
        self.late_ms: list[float] = []

    # ---- enqueue ----
    def _push(self, t: float, kind: str, pair: Optional[NotePair] = None, payload=None) -> None:
        with self.cv:
            self.seq += 1
            heapq.heappush(self.heap, Due(t, self.seq, kind, pair, payload))
            self.cv.notify()

    def schedule_pair(self, pair: NotePair) -> None:
        if pair.t_off <= pair.t_on:
            pair.t_off = pair.t_on + 0.02
        self._push(pair.t_on, "on", pair)
        self._push(pair.t_off, "off", pair)

    def schedule_raw(self, t: float, payload) -> None:
        self._push(t, "raw", None, payload)

    def release(self, ch: int, note: int, at_t: float, *, lane: Optional[str] = None,
                src_note: Optional[int] = None) -> int:
        """Bring the note_off of matching pairs forward to `at_t`; pairs whose
        note_on has not been sent yet are dropped entirely. Returns count."""
        n = 0
        with self.cv:
            for d in self.heap:
                p = d.pair
                if p is None or p.ch != ch or p.note != note or p.dropped:
                    continue
                if lane is not None and p.lane != lane:
                    continue
                if src_note is not None and p.src_note != src_note:
                    continue
                if d.kind == "off" and not p.off_sent:
                    d.t = min(d.t, at_t)
                    n += 1
                elif d.kind == "on" and not p.on_sent:
                    p.dropped = True
            heapq.heapify(self.heap)
            self.cv.notify()
        return n

    def release_by_src(self, ch: int, lane: str, src_note: int, at_t: float) -> int:
        """Release every pair bound to the human note `src_note` (Shadow).

        Matching on `src_note` instead of the generated note is what makes this
        safe against the engine seeing the human note_off before the scheduler's
        "note sent" notice: the pair is in the heap either way.
        """
        n = 0
        with self.cv:
            for d in self.heap:
                p = d.pair
                if p is None or p.dropped or p.ch != ch or p.lane != lane or p.src_note != src_note:
                    continue
                if d.kind == "off" and not p.off_sent:
                    d.t = min(d.t, at_t)
                    n += 1
                elif d.kind == "on" and not p.on_sent:
                    p.dropped = True
            heapq.heapify(self.heap)
            self.cv.notify()
        return n

    def release_lane(self, ch: int, lane: str, at_t: float) -> int:
        n = 0
        with self.cv:
            for d in self.heap:
                p = d.pair
                if p is None or p.ch != ch or p.lane != lane or p.dropped:
                    continue
                if d.kind == "off" and not p.off_sent:
                    d.t = min(d.t, at_t)
                    n += 1
                elif d.kind == "on" and not p.on_sent:
                    p.dropped = True
            heapq.heapify(self.heap)
            self.cv.notify()
        return n

    def pending_on(self, ch: int) -> int:
        with self.lock:
            return sum(1 for d in self.heap if d.kind == "on" and d.pair and not d.pair.dropped and d.pair.ch == ch)

    def cancel_all(self) -> list[NotePair]:
        """Drop everything not yet sent. Returns pairs whose note_on went out but
        whose note_off had not (the caller must release them explicitly)."""
        with self.cv:
            sounding = []
            for d in self.heap:
                p = d.pair
                if p is not None and d.kind == "off" and p.on_sent and not p.off_sent:
                    sounding.append(p)
                if p is not None:
                    p.dropped = True
            self.heap.clear()
            self.cv.notify()
        return sounding

    def __len__(self) -> int:
        return len(self.heap)

    # ---- dispatch ----
    def pump(self, now: Optional[float] = None) -> int:
        """Send everything due at `now`. Safe to call from any thread."""
        now = self.clock() if now is None else now
        sent = 0
        while True:
            with self.cv:
                if not self.heap or self.heap[0].t > now:
                    return sent
                item = heapq.heappop(self.heap)
            p = item.pair
            if p is not None and p.dropped:
                continue
            if item.kind == "on":
                if now > p.ttl_wall:
                    p.dropped = True
                    continue
                if self.before_on is not None and not self.before_on(p, now):
                    p.dropped = True
                    continue
                p.on_sent = True
            elif item.kind == "off":
                if not p.on_sent:
                    continue
                p.off_sent = True
            self.late_ms.append((now - item.t) * 1000.0)
            if len(self.late_ms) > 2000:
                del self.late_ms[:1000]
            self.emit(item, now)
            sent += 1

    def run(self) -> None:
        """Thread body: wait until the next item is due, spin the last ~1 ms."""
        while not self.stop.is_set():
            with self.cv:
                while not self.heap and not self.stop.is_set():
                    self.cv.wait(0.05)
                if self.stop.is_set():
                    return
                wait = self.heap[0].t - self.clock()
                if wait > self.SPIN_S:
                    self.cv.wait(min(wait - 0.001, 0.05))
                    continue
                t_due = self.heap[0].t
            while self.clock() < t_due:
                pass
            self.pump()

    def start(self) -> threading.Thread:
        th = threading.Thread(target=self.run, name="mie-sched", daemon=True)
        th.start()
        return th

    def jitter_summary(self) -> dict:
        s = sorted(self.late_ms)
        if not s:
            return {"n": 0}
        return {"n": len(s), "p50": round(s[len(s) // 2], 2),
                "p95": round(s[min(len(s) - 1, int(round(0.95 * (len(s) - 1))))], 2), "max": round(s[-1], 2)}
