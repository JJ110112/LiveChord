"""How the player is playing right now (plan §11 Phase 2, item 5).

Not what they are playing - the chord and key answer that - but the manner:
holding a chord down, rolling an arpeggio, singing a single line, striking
block chords. The engine already knows density and energy, which say how MUCH
is happening; this says what SHAPE it has, so an edge can be told to speak only
over a held chord, or only under a melody.

The rules are deliberately plain and use only what `MusicalState` already keeps
(`recent_notes` with their lengths, and the gesture window that groups notes
struck together). Everything is measured over a short window, so the answer
follows the playing rather than the whole take.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .state import MusicalState

TEXTURES = ("quiet", "sustained", "chord", "arpeggio", "melody")

WINDOW_S = 3.0          # how far back a judgement looks
MIN_ONSETS = 3          # below this there is not enough to read
CHORD_SHARE = 0.45      # this share of notes arriving inside a strike = block chords
SPARSE_ONSETS = 1.2     # onsets per second below this, while ringing, is holding
CHORD_TONES = 0.75      # this share of a line landing on chord tones = spelling the chord
RUN = 2.0               # and moving this far in one direction before turning
HAND_GAP = 7            # a gap this wide splits the hands
HAND_MAX_LH = 60        # a left hand does not live above middle C


def _groups(recs: list, window: float) -> list[list]:
    """Notes struck together are one strike (the state's own gesture window)."""
    out: list[list] = []
    for r in recs:
        if out and r.t - out[-1][-1].t <= window:
            out[-1].append(r)
        else:
            out.append([r])
    return out


def hands(notes: list[int]) -> tuple[list[int], list[int]]:
    """Split what is sounding into left and right hand.

    The split is the widest gap in the voicing, not a fixed pitch: a player
    holding a bass note under a melody leaves a hole in the middle, and where
    that hole is depends on the music. It only counts as two hands if the lower
    group really is low - otherwise a wide right-hand voicing would read as two.
    """
    ns = sorted(set(notes))
    if len(ns) < 2:
        return [], ns
    gaps = [(b - a, i) for i, (a, b) in enumerate(zip(ns, ns[1:]))]
    width, at = max(gaps)
    if width < HAND_GAP:
        return [], ns
    lh, rh = ns[:at + 1], ns[at + 1:]
    if lh[-1] > HAND_MAX_LH:
        return [], ns
    return lh, rh


def _run_length(heads: list) -> float:
    """How far the line goes in one direction before it turns.

    An arpeggio runs up or down through a chord; a melody turns more often.
    Repeated notes are not a direction and are skipped rather than breaking
    the run.
    """
    steps = [1 if b.note > a.note else (-1 if b.note < a.note else 0)
             for a, b in zip(heads, heads[1:])]
    lengths, cur, prev = [], 0, 0
    for x in steps:
        if x == 0:
            continue
        if x == prev:
            cur += 1
        else:
            if cur:
                lengths.append(cur)
            cur, prev = 1, x
    if cur:
        lengths.append(cur)
    return sum(lengths) / len(lengths) if lengths else 1.0


def classify(st: "MusicalState", now: float) -> tuple[str, float]:
    """(texture, confidence 0-1) for the last `WINDOW_S` of playing."""
    recs = [r for r in st.recent_notes if now - r.t <= WINDOW_S]
    ringing = len(st.held) + len(st.sustained)
    if len(recs) < MIN_ONSETS:
        # Not enough has happened to read anything new.
        #   a chord still down  -> they are holding
        #   one or two notes    -> keep the previous reading; a single struck
        #                          note is not a texture, and calling it one
        #                          let an edge gated on `sustained` speak on
        #                          the very first note of a melody
        #   nothing at all      -> quiet, and no edge should read that as playing
        if ringing >= 3:
            return "sustained", 0.9
        return (st.texture, 0.4) if ringing else ("quiet", 1.0)

    groups = _groups(recs, st.GESTURE_WINDOW_S)
    # How much of the playing arrives as strikes rather than as a stream. The
    # real gap distribution on the 2026-09-07 takes is bimodal - a spike under
    # 20 ms and a broad band at 150-400 ms, with almost nothing between - so
    # this separates cleanly and does not depend on a fitted threshold.
    struck = 1.0 - len(groups) / len(recs)
    if struck >= CHORD_SHARE:
        return "chord", min(1.0, 0.5 + struck)

    heads = [g[0] for g in groups]
    if len(heads) < MIN_ONSETS:
        return "chord", 0.6

    # Sparse and still ringing: they are holding, not playing a line.
    if len(heads) / WINDOW_S < SPARSE_ONSETS and ringing >= 3:
        return "sustained", 0.7

    # Arpeggio or melody. Leap size cannot tell them apart - measured over the
    # 2026-09-07 takes the within-hand leap sits at 5-7 semitones either way,
    # which is why an earlier version called almost everything an arpeggio.
    # What does separate them is WHAT the line lands on and how it moves: an
    # arpeggio spells the chord and runs in one direction, a melody neither.
    # On those takes the phrase tests (deliberate arpeggios) read 0.83-1.00
    # chord tones with runs of 2.0-3.0, and the free playing 0.54 with 1.38.
    tones = st.chord.tones if st.chord else None
    share = (sum(1 for h in heads if h.note % 12 in tones) / len(heads)) if tones else 0.0
    run = _run_length(heads)
    if tones and share >= CHORD_TONES and run >= RUN:
        return "arpeggio", min(1.0, share * run / 3.0)
    return "melody", min(1.0, 0.5 + (CHORD_TONES - share))
