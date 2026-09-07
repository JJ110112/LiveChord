"""`MusicalState`: the engine's short-term memory (plan §2.2, §6, §10).

Only the Engine thread mutates this object.  All time comes in as an argument
(`now`, seconds from the engine clock) so tests can drive it with a FakeClock.
"""

from __future__ import annotations

import math
import statistics
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
    # -expm1(-x) instead of 1 - exp(-x): the latter loses most of its
    # significant digits when dt is small next to tau, which is the normal case
    # for MIDI events arriving a millisecond apart.
    a = -math.expm1(-dt / tau)
    return prev + a * (target - prev)


class MusicalState:
    # EMA time constants (plan §6)
    TAU_DENSITY = 1.5
    TAU_VEL = 2.0
    TAU_ATTACK = 0.3
    TAU_RELEASE = 2.5
    HUMAN_CH_WINDOW = 2.0
    EXTERNAL_CLOCKS = ("player", "midi")   # grids we can trust without guessing
    PC_HIST_TAU = 12.0
    GESTURE_WINDOW_S = 0.045   # notes struck this close together are one chord/gesture

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
        self.sustained: dict[int, HeldNote] = {}   # released but still ringing under the pedal
        self.sustain: dict[int, bool] = {}         # CC64 state per human channel
        self._human_ch_t: dict[int, float] = {}
        self.register = "mid"
        self.direction = 0
        self.silence_s = 0.0
        self.last_sound_end_t: Optional[float] = None   # when the last human note stopped ringing
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

    def next_grid_t(self, t: float, grid_beats: float) -> float:
        """The first point of a `grid_beats` grid at or after `t`.

        Harmonic rhythm (plan §11 Phase 2): a lane that enters on its own
        initiative should enter in time. Returns `t` unchanged when the grid is
        off or `t` already sits on it, and never moves a note into the past.
        """
        if grid_beats <= 0:
            return t
        g = grid_beats * self.beat_s
        k = math.ceil((t - self.beat_origin_t) / g - 1e-9)
        return self.beat_origin_t + k * g

    def beat_strength(self, t: float) -> float:
        """1.0 on a downbeat, 0.5 on another beat, 0.0 off the beat."""
        pos = ((t - self.beat_origin_t) / self.beat_s) % self.beats_per_bar
        if abs(pos - round(pos)) > 0.08:
            return 0.0
        return 1.0 if round(pos) % self.beats_per_bar == 0 else 0.5

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
        # Notes struck together are ONE gesture. Counting all five notes of a
        # chord as five events made slow ambient chord playing read as busy
        # playing, so restraint held the engine back (2026-09-07 play test).
        same_gesture = self.last_human_on_t is not None and (now - self.last_human_on_t) < self.GESTURE_WINDOW_S
        # Anchor the beat grid to the player. Without a player timeline or MIDI
        # clock, `beat_origin_t` is just "when the engine started", so quantising
        # a lane to it would be arbitrary. The first note after a bar of rest is
        # how a musician states the pulse, so take it as the downbeat.
        if self.clock_source not in self.EXTERNAL_CLOCKS and not same_gesture:
            gap = now - self.last_human_on_t if self.last_human_on_t is not None else 1e9
            if gap > self.beat_s * self.beats_per_bar:
                self.beat_origin_t = now
        self.held[note] = HeldNote(vel, now, ev.ch)
        if not same_gesture:
            self.density += 1.0 / self.TAU_DENSITY
        self.vel_mean = _ema(self.vel_mean, vel, 1.0, 0.0) if self.human_note_count == 0 else \
            self.vel_mean + (1.0 - math.exp(-1.0 / 4.0)) * (vel - self.vel_mean)
        self.vel_var = self.vel_var + 0.2 * ((vel - self.vel_mean) ** 2 - self.vel_var)
        if self.last_human_on_t is not None and not same_gesture:
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
            if self.sustain.get(h.ch):
                self.sustained[note] = h      # the pedal keeps it ringing
        # the chord itself is kept: harmonic context outlives the release
        self._mark_sound_end(now)
        return dur

    def set_sustain(self, ch: int, on: bool, now: float) -> None:
        """CC64 for one human channel. Lifting it stops every note it was holding."""
        was = self.sustain.get(ch, False)
        self.sustain[ch] = on
        if was and not on:
            for note, h in list(self.sustained.items()):
                if h.ch == ch:
                    del self.sustained[note]
            self._mark_sound_end(now)

    @property
    def sounding(self) -> dict[int, HeldNote]:
        """Every human note still audible: fingers down plus pedal-held.

        Both sources are copied first. The merge itself is atomic under the
        GIL, but taking explicit snapshots keeps the intent readable and keeps
        this correct if the engine ever runs on a free-threaded build.
        """
        return {**dict(self.sustained), **dict(self.held)}

    def _mark_sound_end(self, now: float) -> None:
        if not self.held and not self.sustained:
            self.last_sound_end_t = now

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

    # A gap may be any of these note values of the pulse, but not all readings
    # are equally good: a pulse two thirds of the real one explains everything
    # through dotted values and would otherwise score the same, which is what
    # made the estimate slide from 142 BPM to 97 on the second take. Simple
    # ratios are worth more.
    _RATIO_WEIGHTS = {1.0: 1.0, 0.5: 0.9, 2.0: 0.9, 0.25: 0.7, 4.0: 0.7,
                      1.5: 0.45, 0.75: 0.45, 3.0: 0.4}
    IOI_MIN_SAMPLES = 8
    IOI_ADOPT_CONF = 0.55
    IOI_TOLERANCE = 0.18       # how far off a note value may sit

    @classmethod
    def _pulse_fit(cls, iois: list[float], period: float) -> float:
        """How well this pulse explains the gaps, 0-1, weighted by simplicity."""
        if period <= 0:
            return 0.0
        score = 0.0
        for x in iois:
            r = x / period
            best = 0.0
            for k, w in cls._RATIO_WEIGHTS.items():
                if abs(r - k) <= cls.IOI_TOLERANCE * k:
                    best = max(best, w)
            score += best
        return score / len(iois)

    def _estimate_bpm(self) -> None:
        """Induce the pulse from recent gaps between notes.

        The old version demanded that 60 % of the gaps land in one 4-BPM bucket.
        Real playing mixes quarters, eighths and dotted notes, so no bucket ever
        got there and the engine sat on the scene default for a whole piece: in
        the 2026-09-07 log the player was around 140 BPM while every echo was
        spaced at 92, which is 1.5 of their beats - neither on the beat nor a
        clean subdivision. Scoring candidate pulses by how many gaps are a plain
        note value of them handles mixed rhythms, which is the normal case.
        """
        iois = [x for x in self.recent_ioi if 0.05 < x < 4.0]
        if len(iois) < self.IOI_MIN_SAMPLES:
            return
        seeds = {statistics.median(iois)}
        for x in iois:
            seeds.add(x)
        best, best_fit = None, 0.0
        for seed in seeds:
            period = seed
            while period > 0 and 60.0 / period > 180.0:
                period *= 2.0
            while period > 0 and 60.0 / period < 60.0:
                period /= 2.0
            if not (0.3 <= period <= 1.05):
                continue
            fit = self._pulse_fit(iois, period)
            if fit > best_fit + 1e-9:
                best, best_fit = period, fit
        if best is None:
            return
        self.ioi_bpm = 60.0 / best
        self.ioi_conf = best_fit
        if self.clock_source not in ("scene", "ioi") or best_fit < self.IOI_ADOPT_CONF:
            return
        if self.clock_source == "ioi":
            # already locked: only move for a reading that is clearly better,
            # so the pulse does not wander mid-phrase
            keep = self._pulse_fit(iois, 60.0 / self.bpm)
            if best_fit <= keep * 1.12:
                return
        self.bpm = self.ioi_bpm
        self.clock_source = "ioi"

    # ---- generated notes --------------------------------------------------
    def gen_on(self, ch: int, note: int, now: float, lane: str, root_id: int, hop: int,
               src_note: Optional[int] = None, max_dur: float = 8.0) -> None:
        self.active_gen[(ch, note)] = GenNote(now, lane, root_id, hop, src_note, max_dur)

    def gen_off(self, ch: int, note: int) -> Optional[GenNote]:
        return self.active_gen.pop((ch, note), None)

    def gen_notes_for_lane(self, lane: str) -> list[tuple[int, int]]:
        return [k for k, g in list(self.active_gen.items()) if g.lane == lane]

    # ---- periodic ---------------------------------------------------------
    def tick(self, now: float) -> None:
        self._decay(now)
        # Silence is "nothing of mine is ringing any more", not "no new key was
        # struck": holding a chord (or holding it on the pedal) is not 留白.
        if self.held or self.sustained:
            self.silence_s = 0.0
        elif self.last_sound_end_t is not None:
            self.silence_s = now - self.last_sound_end_t
        elif self.last_human_on_t is not None:
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
            "pulse_bpm": round(self.ioi_bpm, 1) if self.ioi_bpm else None,
            "pulse_conf": round(self.ioi_conf, 2),
            "beat": self.bar_pos(now) + 1, "beats_per_bar": self.beats_per_bar,
            "held": sorted(dict(self.held)), "sustained": sorted(dict(self.sustained)),
            "pedal": sorted(ch for ch, on in list(self.sustain.items()) if on),
            "human_chs": sorted(self.human_chs),
            "register": self.register, "direction": self.direction,
            "silence_s": round(self.silence_s, 2),
            "density": round(self.density, 2), "vel_mean": round(self.vel_mean, 1),
            "energy": round(self.human_energy, 3),
            "active_gen": [[ch, n, g.lane] for (ch, n), g in list(self.active_gen.items())],
        }
