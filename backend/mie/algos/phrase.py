"""Phrase echo (plan §11 Phase 2): answer the whole gesture, not each note.

The note echo rolls the dice once per note, so a phrase came back with holes -
the player's own image: shout 你好嗎 into a canyon and it should return 你好嗎,
not 你＿嗎. Measured on the 2026-09-07 take, only 29 % of notes were echoed, and
the phrase that came back was missing nine of its twenty-one pitches.

This one waits for the gesture to finish, rolls once for the whole phrase, and
replays it with its internal rhythm intact, each pass quieter than the last -
a short looper rather than a sprinkle of single notes.

`lane_state` keys: `answered` (the onset time of the phrase already dealt with).
"""

from __future__ import annotations

from random import Random
from typing import Optional

from ..events import Proposal
from ..graph import Edge
from ..state import MusicalState


def collect_phrase(st: MusicalState, gap_s: float, max_notes: int) -> list:
    """The run of notes that ends the buffer, bounded by a gap of `gap_s`.

    Walks back from the last note while the gaps stay inside the phrase, then
    keeps the tail: a canyon answers what you just shouted, not the whole piece.
    """
    recs = [r for r in st.recent_notes]
    if not recs:
        return []
    out = [recs[-1]]
    for a, b in zip(reversed(recs[:-1]), reversed(recs[1:])):
        if b.t - a.t > gap_s:
            break
        out.append(a)
    out.reverse()
    return out[-max_notes:]


def tick(st: MusicalState, edge: Edge, rng: Random, now: float, lane_state: dict,
         tension: float = 0.0) -> list[Proposal]:
    p = edge.params
    gap = float(p.get("phrase_gap_beats", 1.0)) * st.beat_s
    if st.quiet_s < gap or st.last_human_on_t is None:
        return []                                   # the phrase is still running
    if lane_state.get("answered") == st.last_human_on_t:
        return []                                   # this one has been dealt with
    lane_state["answered"] = st.last_human_on_t

    notes = collect_phrase(st, gap, int(p.get("max_notes", 8)))
    if len(notes) < int(p.get("min_notes", 3)):
        return []
    t0 = notes[0].t
    length = max(notes[-1].t - t0, 0.05)
    delay = float(p.get("delay_beats", 1.0)) * st.beat_s
    # a repeat starts a phrase-length plus the gap after the previous one, so the
    # returns are separated the way a real echo is rather than piling up
    period = length + delay
    repeats = max(1, int(p.get("repeats", 3)))
    min_vel = int(p.get("min_vel", 12))
    dur_min = float(p.get("dur_min_beats", 0.25)) * st.beat_s
    semis = edge.transpose + 12 * edge.octave

    # A pass starts a `delay` after the phrase ended. Detecting the end costs
    # `phrase_gap_beats`, so by now we are already late; the whole phrase is
    # shifted to absorb that rather than losing its opening note - an echo that
    # drops the first word is the very thing this algorithm exists to avoid.
    late = max(0.0, now - notes[-1].t)
    out: list[Proposal] = []
    for k in range(1, repeats + 1):
        decay = edge.vel_scale ** k
        if int(round(max(r.vel for r in notes) * decay)) < min_vel:
            break                                   # the tail has died away
        start = delay + (k - 1) * period - late
        shift = max(0.0, -start)                    # never schedule into the past
        for rec in notes:
            vel = int(round(rec.vel * decay + edge.vel_offset))
            if vel < min_vel:
                continue
            dur = max(dur_min, (rec.dur or dur_min) * edge.dur_scale)
            out.append(Proposal(ch=edge.dst, note=rec.note + semis, vel=min(127, vel),
                                dur=dur, lane=edge.lane,
                                t_offset=start + shift + (rec.t - t0)))
    return out


def on_skip(st: MusicalState, edge: Edge, now: float, lane_state: dict) -> None:
    """A refused roll should not swallow the phrase silently; let the next one try."""
    lane_state["answered"] = None
