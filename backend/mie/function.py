"""Functional harmony for the engine (plan §11 Phase 2, second item).

Phase 1 offered two ways to choose a pitch and neither is what a musician does:

  `constraint: "chord"`  - only the literal chord tones. Under a held triad that
                           is three pitch classes, so a pad lane runs out of
                           colour and turns into a drone.
  `constraint: "scale"`  - any note in the key. Every tone is "legal" and the
                           lane wanders onto notes that blur the harmony, which
                           is the "academically correct, musically ugly" result
                           the reviewer warned about.

`constraint: "function"` sits between them: the chords of the key that share the
current chord's *function* lend it their tones. Over a C in C major the tonic
group is C, Em7 and Am7, so the colour set is C D E G A B - the notes an
accompanist would actually reach for - and F, which would pull towards the
subdominant, is left out. Over G7 the dominant group is G7 and Bm7b5, giving
G A B D F.

The T/S/D classification itself is not rewritten here: it comes from
`backend/ai/jazz_rules.py`, which the offline reharmonizer already uses, so the
two subsystems cannot drift apart.
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from typing import Iterable, Optional

# jazz_rules only needs `re` and the stdlib-only `preprocess`, so importing the
# repo's existing harmony knowledge costs the engine nothing.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
try:
    from backend.ai import jazz_rules as _jr
except Exception:  # pragma: no cover - the engine must still run without it
    _jr = None

NUMERALS = ("I", "II", "III", "IV", "V", "VI", "VII")
TONIC, SUBDOMINANT, DOMINANT, AMBIGUOUS = "tonic", "subdominant", "dominant", "ambiguous"

# semitones of the seven diatonic degrees, per mode
MAJOR_STEPS = (0, 2, 4, 5, 7, 9, 11)
MINOR_STEPS = (0, 2, 3, 5, 7, 8, 10)

# fallback used when jazz_rules is unavailable; same table it defines
_FALLBACK = {"I": TONIC, "III": TONIC, "VI": TONIC,
             "II": SUBDOMINANT, "IV": SUBDOMINANT,
             "V": DOMINANT, "VII": DOMINANT}


def steps_for(mode: str) -> tuple:
    return MINOR_STEPS if str(mode).lower().startswith("min") or str(mode).lower() == "aeolian" else MAJOR_STEPS


def degree_index(root_pc: int, tonic_pc: int, mode: str) -> Optional[int]:
    """0-6 if the chord root is a degree of the key, else None (borrowed chord)."""
    steps = steps_for(mode)
    rel = (root_pc - tonic_pc) % 12
    return steps.index(rel) if rel in steps else None


def _quality_suffix(idx: int, mode: str) -> str:
    """The numeral suffix jazz_rules expects, so IIIm reads as tonic, not III."""
    steps = steps_for(mode)
    third = (steps[(idx + 2) % 7] - steps[idx]) % 12
    fifth = (steps[(idx + 4) % 7] - steps[idx]) % 12
    if fifth == 6:
        return "dim"
    return "m" if third == 3 else ""


def function_of(root_pc: int, tonic_pc: int, mode: str = "major") -> str:
    """Which harmonic function a chord root has in the key."""
    idx = degree_index(root_pc, tonic_pc, mode)
    if idx is None:
        return AMBIGUOUS
    numeral = NUMERALS[idx] + _quality_suffix(idx, mode)
    if _jr is not None:
        return _jr.classify_function(numeral)
    return _FALLBACK.get(NUMERALS[idx], AMBIGUOUS)


@lru_cache(maxsize=64)
def _groups(tonic_pc: int, mode: str) -> dict[str, frozenset[int]]:
    """Pitch classes each function can colour with, built from the key's own
    diatonic seventh chords."""
    steps = steps_for(mode)
    out: dict[str, set[int]] = {TONIC: set(), SUBDOMINANT: set(), DOMINANT: set()}
    for idx in range(7):
        fn = function_of((tonic_pc + steps[idx]) % 12, tonic_pc, mode)
        if fn not in out:
            continue
        for k in (0, 2, 4, 6):          # root, third, fifth, seventh
            out[fn].add((tonic_pc + steps[(idx + k) % 7]) % 12)
    return {k: frozenset(v) for k, v in out.items()}


def group_pcs(function: str, tonic_pc: int, mode: str = "major") -> frozenset[int]:
    """Colour set for a function; empty if the function is not one of the three."""
    return _groups(tonic_pc % 12, str(mode).lower()).get(function, frozenset())


def colour_pcs(chord_pcs: Iterable[int], chord_root: Optional[int], tonic_pc: int,
               mode: str = "major", tension: float = 0.0) -> frozenset[int]:
    """Pitch classes a lane may use over this chord.

    The chord's own tones are always allowed; its functional relatives widen
    that, and `tension` adds named extensions on top. A borrowed chord with no
    function in the key keeps its own tones plus extensions, which is the safe
    answer.
    """
    own = frozenset(chord_pcs)
    if chord_root is None:
        return own
    root = chord_root % 12
    fn = function_of(root, tonic_pc % 12, mode)
    out = own | group_pcs(fn, tonic_pc, mode)
    if tension > 0:
        key_pcs = frozenset((tonic_pc + step) % 12 for step in steps_for(mode))
        out = out | extension_pcs(root, tension, key_pcs)
    return out


# --- tension: notes outside the chord must be nameable ---------------------
#
# The rule from the review: an out-of-chord tone has to be an extension or a
# secondary-dominant tone, never a random pitch that merely happened to be
# legal. These are the intervals above the chord root that have names.
NATURAL_EXTENSIONS = {2: "9", 5: "11", 9: "13"}
ALTERED_EXTENSIONS = {1: "b9", 3: "#9", 6: "#11", 8: "b13"}

# tension thresholds (0 = plain triads, 1 = altered)
T_NATURAL = 0.35      # above this, diatonic extensions are allowed
T_ANY_NATURAL = 0.70  # above this, extensions outside the key too
T_ALTERED = 0.85      # above this, the altered tones


def extension_pcs(chord_root: int, tension: float, key_pcs: Optional[frozenset] = None) -> frozenset[int]:
    """Named extensions available over a chord root at this tension level."""
    if tension < T_NATURAL:
        return frozenset()
    out = set()
    for iv in NATURAL_EXTENSIONS:
        pc = (chord_root + iv) % 12
        if tension >= T_ANY_NATURAL or key_pcs is None or pc in key_pcs:
            out.add(pc)
    if tension >= T_ALTERED:
        out |= {(chord_root + iv) % 12 for iv in ALTERED_EXTENSIONS}
    return frozenset(out)


def name_of(pc: int, chord_root: int) -> str:
    """What a pitch class is called over this chord root, for the event stream."""
    iv = (pc - chord_root) % 12
    named = {0: "1", 3: "b3", 4: "3", 7: "5", 10: "b7", 11: "7"}
    return named.get(iv) or NATURAL_EXTENSIONS.get(iv) or ALTERED_EXTENSIONS.get(iv) or ("+%d" % iv)
