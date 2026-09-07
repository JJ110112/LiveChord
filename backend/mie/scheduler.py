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
    muted: bool = False        # master volume silenced it as it was sent
    sent_vel: int = 0          # what actually left the process, after the master volume
    on_sent: bool = False
    off_sent: bool = False
    dropped: bool = False
    ttl_wall: float = float("inf")
    collision: str = "octave"
    voice_lead: str = "off"
    tension: float = 0.0
    note_range: Optional[tuple] = None
    capture_root: Optional[int] = None
    pass_id: Optional[tuple] = None


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
        # Live pairs indexed by channel. Releasing used to scan the whole heap
        # and re-heapify it while holding the lock; on a big heap that took
        # longer than the scheduler's own spin threshold and turned straight
        # into jitter, and a call that matched nothing paid the same price.
        # The channel is the one key that never changes - `note` is re-snapped
        # at send time, so an index keyed on it would go stale.
        self._live: dict[int, list[NotePair]] = {}
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
        with self.cv:
            self._live.setdefault(pair.ch, []).append(pair)
        self._push(pair.t_on, "on", pair)
        self._push(pair.t_off, "off", pair)

    def _pairs(self, ch: int) -> list[NotePair]:
        """Live pairs on one channel; finished ones are dropped as we go.
        Caller must hold the lock."""
        lst = self._live.get(ch)
        if not lst:
            return []
        if any(p.dropped or p.off_sent for p in lst):
            lst = [p for p in lst if not (p.dropped or p.off_sent)]
            self._live[ch] = lst
        return lst

    def _bring_off_forward(self, p: NotePair, at_t: float) -> bool:
        """Make a sounding note stop sooner, or drop it if it has not started.

        A fresh "off" entry is pushed rather than the old one being moved, so
        nothing has to be re-heapified; the stale entry is skipped on pop
        because the pair is already marked off_sent. Caller holds the lock.
        """
        if p.dropped:
            return False
        if not p.on_sent:
            p.dropped = True          # never started: pump skips it
            return False
        if p.off_sent or at_t >= p.t_off:
            return False
        p.t_off = at_t
        self.seq += 1
        heapq.heappush(self.heap, Due(at_t, self.seq, "off", p))
        return True

    def schedule_raw(self, t: float, payload) -> None:
        self._push(t, "raw", None, payload)

    def release(self, ch: int, note: int, at_t: float, *, lane: Optional[str] = None,
                src_note: Optional[int] = None, mark_sent: bool = False) -> int:
        """Stop matching notes at `at_t`; ones that have not started are dropped.

        `mark_sent` is for a caller that sends the note_off itself: the pair is
        recorded as already released so the scheduler does not send a second
        one. A stray note_off is not silent - it can cut short another lane's
        note of the same pitch on the same channel.
        """
        n = 0
        with self.cv:
            for p in list(self._pairs(ch)):
                if p.note != note:
                    continue
                if lane is not None and p.lane != lane:
                    continue
                if src_note is not None and p.src_note != src_note:
                    continue
                if self._bring_off_forward(p, at_t):
                    n += 1
                if mark_sent and p.on_sent:
                    p.off_sent = True
            self.cv.notify()
        return n

    def release_by_src(self, ch: int, lane: str, src_note: int, at_t: float) -> int:
        """Release every pair bound to the human note `src_note` (Shadow).

        Matching on `src_note` instead of the generated note is what makes this
        safe against the engine seeing the human note_off before the scheduler's
        "note sent" notice: the pair is live either way.
        """
        n = 0
        with self.cv:
            for p in list(self._pairs(ch)):
                if p.lane != lane or p.src_note != src_note:
                    continue
                if self._bring_off_forward(p, at_t):
                    n += 1
            self.cv.notify()
        return n

    def sounding_at(self, ch: int, t: float) -> int:
        """Notes on `ch` that will be sounding at time `t`, sent or still scheduled.

        The voice budget asks "how many at once", so counting every scheduled
        note_on charged a two second echo tail as if all of it sounded together
        and the budget threw most of the tail away.
        """
        with self.lock:
            return sum(1 for p in self._pairs(ch) if p.t_on <= t < p.t_off)

    def sounding_notes(self, t: float, ch: Optional[int] = None) -> list:
        """Pitches live at `t`, sent or still scheduled, on `ch` or everywhere.

        `active_gen` is the wrong source for this: it is filled from the "note
        sent" notice on the engine thread, so two notes scheduled in the same
        instant cannot see each other there. The scheduler knows about a pair
        the moment it is queued.
        """
        with self.lock:
            chans = [ch] if ch is not None else list(self._live)
            out = []
            for c in chans:
                for p in self._pairs(c):
                    if not p.dropped and p.t_on <= t < p.t_off:
                        out.append(p.note)
            return out

    def cancel_all(self) -> list[NotePair]:
        """Drop everything not yet sent. Returns pairs whose note_on went out but
        whose note_off had not (the caller must release them explicitly)."""
        with self.cv:
            sounding = [p for lst in self._live.values() for p in lst
                        if p.on_sent and not p.off_sent and not p.dropped]
            for lst in self._live.values():
                for p in lst:
                    p.dropped = True
            for d in self.heap:
                if d.pair is not None:
                    d.pair.dropped = True
            self.heap.clear()
            self._live.clear()
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
                if not p.on_sent or p.off_sent:
                    continue          # never started, or a superseded off entry
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
