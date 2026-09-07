"""MIDI safety layers 1-9 (plan §7).  Layer 10 (PANIC) lives in the engine.

Every layer is independent: a proposal has to clear all of them.  `admit()`
returns None when the note may be scheduled, otherwise a short drop reason
that the UI shows in the event stream.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Optional

from .events import Proposal
from .graph import Instrument
from .state import MusicalState


class SelfEchoFilter:
    """Layer 1: remember what we sent; flag it if it comes straight back in."""

    def __init__(self, window_s: float = 0.008):
        self.window_s = window_s
        self._sent: deque[tuple[float, tuple[int, int, bool]]] = deque()

    def note_sent(self, ch: int, note: int, is_on: bool, t: float) -> None:
        self._sent.append((t, (ch, note, is_on)))
        self._prune(t)

    def is_echo(self, ch: int, note: int, is_on: bool, t: float) -> bool:
        self._prune(t)
        key = (ch, note, is_on)
        return any(k == key for _, k in self._sent)

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_s
        while self._sent and self._sent[0][0] < cutoff:
            self._sent.popleft()


class TokenBucket:
    """Layer 5 rate limiter."""

    def __init__(self, rate: float, burst: float):
        self.rate, self.burst = float(rate), float(burst)
        self.tokens = float(burst)
        self.t = None

    def take(self, now: float, n: float = 1.0) -> bool:
        if self.t is None:
            self.t = now
        now = max(now, self.t)      # callers may pass a future send time out of order
        self.tokens = min(self.burst, self.tokens + (now - self.t) * self.rate)
        self.t = now
        if self.tokens >= n:
            self.tokens -= n
            return True
        return False


@dataclass(slots=True)
class Admission:
    ok: bool
    reason: Optional[str] = None
    steal: Optional[tuple[int, int]] = None   # (ch, note) of the generated note to release first


class Safety:
    # The burst has to cover one musical gesture: a five-note chord echoed twice
    # is ten notes inside a beat, and a burst of 8 silently trimmed the tail off
    # chord echoes (2026-09-07 play test). The sustained rate is the real guard.
    NOTE_RATE, NOTE_BURST = 20.0, 12.0
    CC_RATE, CC_BURST = 30.0, 10.0
    FANTOM_GROUP_MAX = 8
    CC_FLOOR = {7: 40}   # layer 9: volume never below this

    def __init__(self, instruments: dict[int, Instrument], globals_: dict):
        self.instruments = instruments
        self.g = globals_
        self._note_buckets: dict[int, TokenBucket] = {}
        self._cc_buckets: dict[int, TokenBucket] = {}
        self._global = TokenBucket(float(globals_.get("max_gen_notes_per_s", 12)), float(globals_.get("max_gen_notes_per_s", 12)))
        self.chain_counts: dict[int, int] = {}
        self._chain_t: dict[int, float] = {}
        self.drops: dict[str, int] = {}
        self._cc_last: dict[tuple[int, int], tuple[float, int]] = {}

    # ---- housekeeping ----
    def set_globals(self, globals_: dict) -> None:
        self.g = globals_
        r = float(globals_.get("max_gen_notes_per_s", 12))
        self._global = TokenBucket(r, r)

    def _bucket(self, ch: int) -> TokenBucket:
        b = self._note_buckets.get(ch)
        if b is None:
            b = self._note_buckets[ch] = TokenBucket(self.NOTE_RATE, self.NOTE_BURST)
        return b

    def _count(self, reason: str) -> str:
        self.drops[reason] = self.drops.get(reason, 0) + 1
        return reason

    def prune_chains(self, now: float, max_age_s: float = 30.0) -> None:
        for rid, t in list(self._chain_t.items()):
            if now - t > max_age_s:
                self._chain_t.pop(rid, None)
                self.chain_counts.pop(rid, None)

    # ---- layers 2-4 (lineage) ----
    def check_lineage(self, *, hop: int, max_hop: int, ttl_wall: float, root_id: int, now: float,
                      origin: str, fanout: int) -> Optional[str]:
        if hop > max_hop:
            return self._count("hop")
        if now > ttl_wall:
            return self._count("ttl")
        limit = int(self.g.get("max_chain_events", 24))
        if self.chain_counts.get(root_id, 0) >= limit:
            return self._count("chain")
        if origin == "GENERATIVE" and fanout > 1:
            return self._count("fanout")
        return None

    def count_chain(self, root_id: int, now: float, n: int = 1) -> None:
        self.chain_counts[root_id] = self.chain_counts.get(root_id, 0) + n
        self._chain_t[root_id] = now

    # ---- layers 5-6 + human channel ----
    def admit(self, p: Proposal, st: MusicalState, now: float, pending_on_ch: int = 0,
              t_send: Optional[float] = None) -> Admission:
        """`t_send` is when the note will actually sound. The rate limiter has to
        judge it there, not at scheduling time: a chord's worth of echoes is
        scheduled in one instant but sounds spread over the delay, and charging
        it all to `now` cut the tail off every chord (2026-09-07 play test)."""
        if p.kind != "on":
            return Admission(True)
        t_send = now if t_send is None else max(now, t_send)
        inst = self.instruments.get(p.ch)
        if inst is None or not inst.enabled:
            return Admission(False, self._count("disabled"))
        if p.ch in st.human_chs:
            return Admission(False, self._count("human_ch"))
        if not self._bucket(p.ch).take(t_send):
            return Admission(False, self._count("rate_ch"))
        if not self._global.take(t_send):
            return Admission(False, self._count("rate_global"))
        # voice budget (layer 6)
        max_v = inst.max_voices
        group_max = self.FANTOM_GROUP_MAX
        if st.human_energy > 0.7:
            max_v, group_max = 1, 3
        ch_voices = [(k, g) for k, g in list(st.active_gen.items()) if k[0] == p.ch]
        steal = None
        if len(ch_voices) + pending_on_ch >= max_v:
            if not ch_voices:
                return Admission(False, self._count("voices"))
            steal = min(ch_voices, key=lambda kv: kv[1].t_on)[0]
        if inst.group == "fantom":
            grp = [(k, g) for k, g in list(st.active_gen.items())
                   if self.instruments.get(k[0]) and self.instruments[k[0]].group == "fantom"]
            if len(grp) >= group_max and steal is None:
                if not grp:
                    return Admission(False, self._count("group_voices"))
                steal = min(grp, key=lambda kv: kv[1].t_on)[0]
        return Admission(True, None, steal)

    def clamp_dur(self, p: Proposal, inst: Optional[Instrument]) -> float:
        cap = float(self.g.get("sustain_dur_s", 30.0)) if (inst and inst.sustain_ok) else float(self.g.get("max_dur_s", 8.0))
        return max(0.02, min(p.dur, cap))

    # ---- layer 7 ----
    def watchdog(self, st: MusicalState, now: float) -> list[tuple[int, int]]:
        out = []
        for (ch, note), g in list(st.active_gen.items()):
            if now - g.t_on > g.max_dur + 0.5:
                out.append((ch, note))
        return out

    # ---- layer 9 ----
    def guard_cc(self, ch: int, cc: int, val: int, now: float, lo: int = 0, hi: int = 127) -> Optional[int]:
        """Clamp, floor and slew-limit a CC; None = drop (rate limited)."""
        b = self._cc_buckets.get(ch)
        if b is None:
            b = self._cc_buckets[ch] = TokenBucket(self.CC_RATE, self.CC_BURST)
        if not b.take(now):
            self._count("rate_cc")
            return None
        lo = max(lo, self.CC_FLOOR.get(cc, 0))
        val = max(lo, min(hi, int(val)))
        last = self._cc_last.get((ch, cc))
        if last is not None:
            t0, v0 = last
            steps = max(1, int((now - t0) / 0.02))
            val = max(v0 - 4 * steps, min(v0 + 4 * steps, val))
        self._cc_last[(ch, cc)] = (now, val)
        return val
