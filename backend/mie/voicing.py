"""Voice leading (plan §11 Phase 2, first item).

Phase 1 chose pitches with `constraint.snap`: the legal pitch nearest the note
the algorithm proposed. Nothing looked at what that lane had just played, so a
line could jump an octave between two chords and every entry sounded like a
fresh, unrelated event. That is the main source of the mechanical feel the
player reported.

`lead()` keeps the algorithm's intent but resolves it into the pitch that moves
best from the lane's previous note:

  * smallest movement wins, stepwise motion is preferred over a leap;
  * a note that wants to resolve (the 7th, or the 4th over its chord) gets a
    bonus for stepping down onto a chord tone;
  * candidates that would make a parallel fifth or octave with another sounding
    generated voice are penalised;
  * the pitch the algorithm asked for still pulls, so Follow's interval and
    Sustain's colour choice are not thrown away.

Pure functions; the engine passes in what each lane last played.
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence

# cost weights, in semitones of equivalent movement
W_PREV = 1.0            # distance from what this lane just played
W_INTENT = 0.5          # distance from the pitch the algorithm proposed
LEAP_LIMIT = 7          # a movement wider than a fifth counts as a leap
LEAP_PENALTY = 0.6      # per semitone beyond LEAP_LIMIT
STEP_BONUS = 1.2        # for moving by a step (1 or 2 semitones)
RESOLVE_BONUS = 2.5     # for resolving a 7th or 4th downwards onto a chord tone
PARALLEL_PENALTY = 6.0  # for a parallel fifth or octave with another voice
AVOID_PENALTY = 4.0     # for landing on a pitch class we were asked to avoid
# There is deliberately no penalty for staying put: holding a common tone
# through a chord change is the textbook move, and whether a lane may repeat a
# pitch at all is the algorithm's business (Sustain already excludes the notes
# it is currently holding).


def wants_resolution(note: int, chord_root: Optional[int], chord_pcs: Iterable[int]) -> int:
    """Direction this note wants to move: -1 down, 0 nowhere.

    The 7th and the 4th above the root are the tones that carry tension; both
    resolve downwards by step in common practice.
    """
    if chord_root is None:
        return 0
    degree = (note - chord_root) % 12
    return -1 if degree in (10, 11, 5) else 0


def _is_perfect(a: int, b: int) -> bool:
    return abs(a - b) % 12 in (0, 7)


def _parallel(prev_self: int, cand: int, others: Sequence[tuple[int, int]]) -> bool:
    """True if moving prev_self -> cand makes a parallel fifth/octave.

    `others` are (previous, current) pitches of the other sounding voices.
    Two voices are parallel when they held a perfect interval, still hold one,
    and both moved in the same direction.
    """
    d_self = cand - prev_self
    if d_self == 0:
        return False
    for prev_other, cur_other in others:
        if prev_other == cur_other:
            continue
        if (cur_other - prev_other > 0) != (d_self > 0):
            continue
        if _is_perfect(prev_self, prev_other) and _is_perfect(cand, cur_other):
            return True
    return False


def candidates(pcs: Iterable[int], lo: int, hi: int) -> list[int]:
    pcs = set(pcs)
    return [n for n in range(max(0, lo), min(127, hi) + 1) if (n % 12) in pcs]


def cost(cand: int, *, prev: Optional[int], intent: Optional[int], avoid_pcs: Iterable[int] = (),
         chord_root: Optional[int] = None, chord_pcs: Iterable[int] = (),
         others: Sequence[tuple[int, int]] = ()) -> float:
    """Lower is better. Exposed so tests can explain a choice."""
    c = 0.0
    if intent is not None:
        c += W_INTENT * abs(cand - intent)
    if prev is not None:
        move = abs(cand - prev)
        c += W_PREV * move
        if move in (1, 2):
            c -= STEP_BONUS
        if move > LEAP_LIMIT:
            c += LEAP_PENALTY * (move - LEAP_LIMIT)
        want = wants_resolution(prev, chord_root, chord_pcs)
        if want < 0 and 0 < prev - cand <= 2 and (cand % 12) in set(chord_pcs):
            c -= RESOLVE_BONUS
        if _parallel(prev, cand, others):
            c += PARALLEL_PENALTY
    if (cand % 12) in set(avoid_pcs):
        c += AVOID_PENALTY
    return c


def lead(pcs: Iterable[int], *, prev: Optional[int], intent: Optional[int], lo: int, hi: int,
         avoid_pcs: Iterable[int] = (), chord_root: Optional[int] = None,
         chord_pcs: Iterable[int] = (), others: Sequence[tuple[int, int]] = ()) -> Optional[int]:
    """Best pitch in [lo, hi] whose pitch class is allowed. None if there is none."""
    cands = candidates(pcs, lo, hi)
    if not cands:
        return None
    if prev is None and intent is None:
        return cands[len(cands) // 2]
    best, best_c = None, None
    for n in cands:
        c = cost(n, prev=prev, intent=intent, avoid_pcs=avoid_pcs, chord_root=chord_root,
                 chord_pcs=chord_pcs, others=others)
        # ties go to the candidate nearer the algorithm's intent, then lower
        key = (c, abs(n - intent) if intent is not None else 0, n)
        if best_c is None or key < best_c:
            best, best_c = n, key
    return best
