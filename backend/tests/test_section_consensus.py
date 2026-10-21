"""Cross-section consensus must unify the SAME harmony renamed under a new
arrangement, and must never overwrite a real substitution."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from section_consensus import apply_consensus, _compatible

BAR = 4.0


def _verse(start, names):
    """One chord per bar."""
    return [{"chord": n, "time": start + i * BAR, "end": start + (i + 1) * BAR} for i, n in enumerate(names)]


def _song(variants):
    chords, secs = [], []
    for k, names in enumerate(variants):
        start = k * len(names) * BAR
        chords += _verse(start, names)
        secs.append({"type": "verse", "start": start, "end": start + len(names) * BAR})
    return chords, secs


def _names(chords, start, n):
    return [c["chord"] for c in chords if start - 0.01 <= c["time"] < start + n * BAR - 0.01]


def test_enharmonic_respelling_follows_majority():
    # Bbm6 has exactly Gm7b5's notes; Edim7 is C7b9 without its root.
    base = ["Fm", "Eb", "Gm7b5", "C7"]
    chords, secs = _song([base, base, ["Fm", "Eb", "Bbm6", "Edim7"], base])
    out, meta = apply_consensus(chords, secs, BAR)
    assert _names(out, 2 * 4 * BAR, 4) == base
    assert meta["replaced"] == 2


def test_real_substitution_is_kept():
    # Cm vs Eb share two notes but are different harmonies (different bass) → untouched.
    base = ["Fm", "Eb", "Db", "C7"]
    chords, secs = _song([base, base, ["Fm", "Cm", "Db", "C7"], base])
    out, meta = apply_consensus(chords, secs, BAR)
    assert "Cm" in _names(out, 2 * 4 * BAR, 4)
    assert not meta["applied"]


def test_two_sections_have_no_majority():
    chords, secs = _song([["Fm", "Gm7b5"], ["Fm", "Bbm6"]])
    out, meta = apply_consensus(chords, secs, BAR)
    assert not meta["applied"] and [c["chord"] for c in out] == ["Fm", "Gm7b5", "Fm", "Bbm6"]


def test_compatibility_is_subset_based():
    assert _compatible("Fm", "Dbmaj7")       # Fm triad sits inside Dbmaj7
    assert _compatible("Edim7", "C7")        # rootless C7b9
    assert not _compatible("Cm", "Eb")
    assert not _compatible("Bbm7", "Eb")
