"""Phrase echo (plan §11 Phase 2): answer the whole gesture, not each note.

The note echo rolls the dice once per note, so a phrase came back with holes -
the player's own image: shout 你好嗎 into a canyon and it should return 你好嗎,
not 你＿嗎. Measured on the 2026-09-07 take, only 29 % of notes were echoed, and
the phrase that came back was missing nine of its twenty-one pitches.

This one waits for the gesture to finish, rolls once for the whole phrase, and
replays it with its internal rhythm intact, each pass quieter and shorter
than the last (`decay`, per pass) -
a short looper rather than a sprinkle of single notes.

`lane_state` keys: `answered` (the onset time of the phrase already dealt with).
"""

from __future__ import annotations

from random import Random
from typing import Optional

from ..events import Proposal
from ..graph import Edge
from ..state import MusicalState
from . import how_many, tail_floor


def phrase_gap(st: MusicalState, edge: Edge) -> float:
    """How long a silence has to be before the gesture counts as finished.

    A fixed number of beats is not enough. On the 20:28 take the engine had
    locked onto 179.9 BPM, so one beat was 0.33 s - exactly the spacing of the
    notes the player was playing. Every note therefore "ended a phrase", each
    phrase collected only one or two notes, `min_notes` refused them, and the
    engine answered ONCE in 39 seconds while the player played eight clear
    phrases separated by 2.7-7.8 s of silence.

    So the threshold also has to be relative to how fast this player is
    actually playing: a real phrase break is several times their own note
    spacing. Whichever is longer wins.
    """
    beats = float(edge.params.get("phrase_gap_beats", 1.0)) * st.beat_s
    iois = sorted(x for x in st.recent_ioi if 0.05 < x < 4.0)
    if len(iois) >= 4:
        mid = iois[len(iois) // 2]
        beats = max(beats, float(edge.params.get("phrase_gap_iois", 2.2)) * mid)
    return beats


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
    gap = phrase_gap(st, edge)
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
    repeats = how_many(edge, st, "repeats", 3)
    min_vel = tail_floor(edge, st, 12)
    dur_min = float(p.get("dur_min_beats", 0.25)) * st.beat_s
    semis = edge.transpose + 12 * edge.octave

    # A pass starts a `delay` after the phrase ended. Detecting the end costs
    # `phrase_gap_beats`, so by now we are already late; the whole phrase is
    # shifted to absorb that rather than losing its opening note - an echo that
    # drops the first word is the very thing this algorithm exists to avoid.
    dur_decay = float(p.get("dur_decay", 0.8))
    # Two different jobs, so two numbers. `vel_scale` is the edge's own gain -
    # how loud this instrument answers at all - and applies once. `decay` is how
    # fast the tail dies and applies per pass AFTER the first. Folding them into
    # one (vel_scale ** k, as the note echo does) put the very first return
    # already 40 % down, so the whole tail sat in a narrow quiet band and the
    # player heard "有重複但是聲音感覺一樣大" on the 19:18 take: v32 -> v19 -> stop.
    fade = float(p.get("decay", 0.6))
    late = max(0.0, now - notes[-1].t)
    head = max(0.0, delay - late)               # when the first pass opens
    # The chord the phrase was sung over. Carried on every note so the repeat
    # can be moved bodily to whatever chord is in force when it comes back -
    # see `Engine._phrase_transpose`. None when the edge does not ask for it,
    # and the note then returns at its original pitch.
    root, quality = None, ""
    if p.get("follow_chord") and st.chord is not None:
        root, quality = st.chord.root_pc, st.chord.quality
    fire_id = round(st.last_human_on_t or now, 4)
    out: list[Proposal] = []
    for k in range(1, repeats + 1):
        decay = edge.vel_scale * fade ** (k - 1)
        # all or nothing: judge the pass by its QUIETEST note, so a fading tail
        # stops between phrases instead of returning half of one. The loudest
        # note was the wrong test - it let a pass through that then dropped its
        # own soft notes one by one, which is the hole this algorithm removes.
        if int(round(min(r.vel for r in notes) * decay)) < min_vel:
            break                                   # the tail has died away
        # `late` is pushed back ONCE, for the whole answer. Clamping each pass
        # separately meant that when detection cost more than `delay` - which
        # it does whenever the player is slow, since the gap threshold now
        # follows their own note spacing - every pass clamped to zero and they
        # all landed on the same instant. Seen on the 21:30 take: two passes
        # scheduled at identical offsets [0, .006, .014, .022, .031].
        start = head + (k - 1) * period
        for rec in notes:
            vel = max(min_vel, int(round(rec.vel * decay + edge.vel_offset)))
            offset = rec.t - t0
            # A pass must be finished before the next one opens. Without this a
            # held note rings across two or three passes, the channel runs out
            # of voices and the voice budget eats the head of the next pass -
            # the same "missing word" this algorithm exists to prevent, arriving
            # by another door. Measured on the 19:10 take: MODX lost n55 and n60
            # of pass 2 to `drop reason=voices`.
            # a note still under the finger has no recorded length yet; use how
            # long it has been down, or the whole phrase becomes 160 ms blips
            heard = rec.dur if rec.dur else max(dur_min, now - rec.t)
            dur = max(dur_min, heard * edge.dur_scale * dur_decay ** (k - 1))
            dur = min(dur, max(dur_min, period - offset))
            out.append(Proposal(ch=edge.dst, note=rec.note + semis, vel=min(127, vel),
                                dur=dur, lane=edge.lane,
                                t_offset=start + offset,
                                capture_root=root, capture_quality=quality,
                                pass_id=(fire_id, k)))
    return out


def on_skip(st: MusicalState, edge: Edge, now: float, lane_state: dict) -> None:
    """A refused roll should not swallow the phrase silently; let the next one try."""
    lane_state["answered"] = None
