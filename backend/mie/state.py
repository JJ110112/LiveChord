"""`MusicalState`: the engine's short-term memory (plan §2.2, §6, §10).

Only the Engine thread mutates this object.  All time comes in as an argument
(`now`, seconds from the engine clock) so tests can drive it with a FakeClock.
"""

from __future__ import annotations

import math
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Optional

from .events import ContextSnapshot, MieEvent
from .harmony import ChordInfo, KeyInfo, estimate_key, recognize
from .scales import NOTE_NAMES, scale_for_mode


@dataclass(slots=True)
class HeldNote:
    vel: int
    t_on: float
    ch: int


@dataclass(slots=True)
class NoteRec:
    t: float
    note: int
    vel: int
    ch: int
    dur: Optional[float] = None


@dataclass(slots=True)
class GenNote:
    t_on: float
    lane: str
    root_id: int
    hop: int
    src_note: Optional[int] = None   # human note it shadows (follow_off)
    max_dur: float = 8.0


def _ema(prev: float, target: float, dt: float, tau: float) -> float:
    if tau <= 0:
        return target
    a = 1.0 - math.exp(-dt / tau)
    return prev + a * (target - prev)


class MusicalState:
    # EMA time constants (plan §6)
    TAU_DENSITY = 1.5
    TAU_VEL = 2.0
    TAU_ATTACK = 0.3
    TAU_RELEASE = 2.5
    HUMAN_CH_WINDOW = 2.0
    PC_HIST_TAU = 12.0

    def __init__(self, *, bpm: float = 92.0, beats_per_bar: int = 4,
                 key: Optional[KeyInfo] = None, now: float = 0.0):
        self.chord: Optional[ChordInfo] = None
        self.key: KeyInfo = key or KeyInfo(0, "major", 0.0, "scene")
        self.scale_id: str = scale_for_mode(self.key.mode)
        self.bpm = float(bpm)
        self.beats_per_bar = beats_per_bar
        self.clock_source = "scene"
        self.beat_origin_t = now          # wall time of some downbeat
        self.held: dict[int, HeldNote] = {}
        self._human_ch_t: dict[int, float] = {}
        self.register = "mid"
        self.direction = 0
        self.silence_s = 0.0
        self.last_human_on_t: Optional[float] = None
        self.last_human_dur: Optional[float] = None
        self.density = 0.0
        self.vel_mean = 0.0
        self.vel_var = 0.0
        self.human_energy = 0.0
        self.recent_notes: deque[NoteRec] = deque(maxlen=256)
        self.recent_intervals: deque[int] = deque(maxlen=64)
        self.recent_ioi: deque[float] = deque(maxlen=32)
        self.active_gen: dict[tuple[int, int], GenNote] = {}
        self.pc_hist = [0.0] * 12
        self._last_t = now
        self._last_note: Optional[int] = None
        self.human_note_count = 0
        self.ioi_bpm: Optional[float] = None
        self.ioi_conf = 0.0

    # ---- clocks -----------------------------------------------------------
    @property
    def beat_s(self) -> float:
        return 60.0 / max(20.0, self.bpm)

    def beat_phase(self, now: float) -> float:
        return ((now - self.beat_origin_t) / self.beat_s) % 1.0

    def beat_index(self, now: float) -> int:
        return int((now - self.beat_origin_t) / self.beat_s)

    def bar_pos(self, now: float) -> int:
        return self.beat_index(now) % self.beats_per_bar

    def next_beat_t(self, now: float) -> float:
        return self.beat_origin_t + (self.beat_index(now) + 1) * self.beat_s

    def next_downbeat_t(self, now: float) -> float:
        bar_len = self.beat_s * self.beats_per_bar
        k = int((now - self.beat_origin_t) / bar_len) + 1
        return self.beat_origin_t + k * bar_len

    @property
    def human_chs(self) -> set[int]:
        return set(self._human_ch_t)

    @property
    def per_ch_voices(self) -> Counter:
        return Counter(ch for ch, _ in self.active_gen)

    # ---- external context (player playhead / manual) ----------------------
    def set_key(self, tonic_pc: int, mode: str, source: str, confidence: float = 1.0) -> None:
        self.key = KeyInfo(tonic_pc % 12, mode, confidence, source)
        self.scale_id = scale_for_mode(mode)

    def set_tempo(self, bpm: float, now: float, source: str, downbeat_t: Optional[float] = None,
                  beats_per_bar: Optional[int] = None) -> None:
        self.bpm = float(bpm)
        self.clock_source = source
        if beats_per_bar:
            self.beats_per_bar = beats_per_bar
        if downbeat_t is not None:
            self.beat_origin_t = downbeat_t

    def set_chord(self, chord: Optional[ChordInfo]) -> None:
        self.chord = chord

    # ---- human input ------------------------------------------------------
    def _decay(self, now: float) -> None:
        dt = max(0.0, now - self._last_t)
        if dt > 0:
            self.density *= math.exp(-dt / self.TAU_DENSITY)
            f = math.exp(-dt / self.PC_HIST_TAU)
            self.pc_hist = [v * f for v in self.pc_hist]
            self._last_t = now

    def note_on_human(self, ev: MieEvent, now: float, *, duplicate: bool = False) -> None:
        """Update memory with a human note_on. `duplicate` = same key already
        counted on another channel (Fantom layered zones): only the channel
        bookkeeping is updated, no statistics."""
        self._human_ch_t[ev.ch] = now
        if duplicate:
            return
        self._decay(now)
        note, vel = int(ev.note), int(ev.vel or 0)
        self.held[note] = HeldNote(vel, now, ev.ch)
        self.density += 1.0 / self.TAU_DENSITY
        self.vel_mean = _ema(self.vel_mean, vel, 1.0, 0.0) if self.human_note_count == 0 else \
            self.vel_mean + (1.0 - math.exp(-1.0 / 4.0)) * (vel - self.vel_mean)
        self.vel_var = self.vel_var + 0.2 * ((vel - self.vel_mean) ** 2 - self.vel_var)
        if self.last_human_on_t is not None:
            ioi = now - self.last_human_on_t
            if 0.02 < ioi < 4.0:
                self.recent_ioi.append(ioi)
        if self._last_note is not None:
            self.recent_intervals.append(note - self._last_note)
        self._last_note = note
        self.last_human_on_t = now
        self.silence_s = 0.0
        self.human_note_count += 1
        self.recent_notes.append(NoteRec(now, note, vel, ev.ch))
        self.pc_hist[note % 12] += vel / 127.0
        self._update_register_direction()
        self._update_chord(now)
        self._update_energy(now)
        self._estimate_bpm()

    def note_off_human(self, ev: MieEvent, now: float) -> Optional[float]:
        note = int(ev.note)
        h = self.held.pop(note, None)
        dur = None
        if h is not None:
            dur = now - h.t_on
            self.last_human_dur = dur
            for rec in reversed(self.recent_notes):
                if rec.note == note and rec.dur is None:
                    rec.dur = dur
                    break
        if not self.held:
            pass  # keep last chord: harmonic context outlives the release
        return dur

    def _update_register_direction(self) -> None:
        recent = list(self.recent_notes)[-8:]
        if recent:
            w = sum(r.vel for r in recent) or 1
            med = sum(r.note * r.vel for r in recent) / w
            self.register = "low" if med < 48 else ("high" if med > 72 else "mid")
        last4 = [r.note for r in list(self.recent_notes)[-4:]]
        if len(last4) >= 2:
            slope = last4[-1] - last4[0]
            self.direction = 1 if slope > 1 else (-1 if slope < -1 else 0)

    def _update_chord(self, now: float) -> None:
        if len(self.held) >= 2:
            c = recognize(self.held.keys(), now, self.chord)
            if c is not None:
                self.chord = c
        if self.key.source in ("scene", "inferred") and self.human_note_count >= 8:
            k = estimate_key(self.pc_hist)
            if k and k.confidence >= 0.35 and (self.key.source == "scene" or k.confidence >= self.key.confidence * 0.8):
                self.key = k
                self.scale_id = scale_for_mode(k.mode)

    def _update_energy(self, now: float) -> None:
        density_n = max(0.0, min(1.0, self.density / 8.0))
        vel_n = max(0.0, min(1.0, (self.vel_mean - 40.0) / 80.0))
        # the velocity term only counts while notes are actually flowing
        target = 0.6 * density_n + 0.4 * vel_n * min(1.0, self.density / 1.0)
        dt = max(0.0, now - getattr(self, "_energy_t", now))
        tau = self.TAU_ATTACK if target > self.human_energy else self.TAU_RELEASE
        self.human_energy = _ema(self.human_energy, target, dt if dt > 0 else 0.05, tau)
        self._energy_t = now

    def _estimate_bpm(self) -> None:
        """Cluster recent inter-onset intervals into a 60-180 BPM tempo."""
        if len(self.recent_ioi) < 6:
            return
        cands: Counter = Counter()
        for ioi in self.recent_ioi:
            for mult in (1, 2, 4, 0.5):
                bpm = 60.0 / (ioi * mult)
                if 60 <= bpm <= 180:
                    cands[round(bpm / 4) * 4] += 1
        if not cands:
            return
        bpm, n = cands.most_common(1)[0]
        self.ioi_bpm = float(bpm)
        self.ioi_conf = n / (len(self.recent_ioi) * 1.0)
        if self.clock_source in ("scene", "ioi") and self.ioi_conf >= 0.6:
            self.bpm = self.ioi_bpm
            self.clock_source = "ioi"

    # ---- generated notes --------------------------------------------------
    def gen_on(self, ch: int, note: int, now: float, lane: str, root_id: int, hop: int,
               src_note: Optional[int] = None, max_dur: float = 8.0) -> None:
        self.active_gen[(ch, note)] = GenNote(now, lane, root_id, hop, src_note, max_dur)

    def gen_off(self, ch: int, note: int) -> Optional[GenNote]:
        return self.active_gen.pop((ch, note), None)

    def gen_notes_for_lane(self, lane: str) -> list[tuple[int, int]]:
        return [k for k, g in self.active_gen.items() if g.lane == lane]

    # ---- periodic ---------------------------------------------------------
    def tick(self, now: float) -> None:
        self._decay(now)
        if self.last_human_on_t is not None:
            self.silence_s = now - self.last_human_on_t
        for ch, t in list(self._human_ch_t.items()):
            if now - t > self.HUMAN_CH_WINDOW:
                del self._human_ch_t[ch]
        self._update_energy(now)

    def snapshot(self, now: float) -> ContextSnapshot:
        return ContextSnapshot(
            chord=self.chord.name if self.chord else None,
            key_tonic=self.key.tonic_pc, key_mode=self.key.mode, bpm=self.bpm,
            beat_phase=self.beat_phase(now), bar_pos=self.bar_pos(now),
            register=self.register, human_energy=self.human_energy,
        )

    def to_dict(self, now: float) -> dict:
        return {
            "chord": self.chord.name if self.chord else None,
            "chord_tones": sorted(self.chord.tones) if self.chord else [],
            "key": f"{NOTE_NAMES[self.key.tonic_pc]} {self.key.mode}",
            "key_source": self.key.source, "key_conf": round(self.key.confidence, 2),
            "scale": self.scale_id,
            "bpm": round(self.bpm, 1), "clock": self.clock_source,
            "beat": self.bar_pos(now) + 1, "beats_per_bar": self.beats_per_bar,
            "held": sorted(self.held), "human_chs": sorted(self.human_chs),
            "register": self.register, "direction": self.direction,
            "silence_s": round(self.silence_s, 2),
            "density": round(self.density, 2), "vel_mean": round(self.vel_mean, 1),
            "energy": round(self.human_energy, 3),
            "active_gen": [[ch, n, g.lane] for (ch, n), g in self.active_gen.items()],
        }
