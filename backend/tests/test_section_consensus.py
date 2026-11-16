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


def test_section_of_different_length_is_left_out():
    # A mis-placed boundary (one verse half as long, the next 1.5x) must not
    # be scaled onto the others' bars — that compares the wrong chords.
    base = ["Fm", "Eb", "Gm7b5", "C7"]
    chords, secs = _song([base, base, ["Fm", "Eb", "Bbm6", "Edim7"], base])
    secs[2]["end"] = secs[2]["start"] + 2 * BAR
    secs[3]["start"] = secs[2]["end"]
    out, meta = apply_consensus(chords, secs, BAR)
    assert not meta["applied"]
    assert meta["groups"]["verse"]["skipped"]


def test_human_section_boundaries_follow_the_rebuilt_bar_grid():
    from section_consensus import snap_sections_to_bars
    bars = [0.86 + 4.0 * i for i in range(12)]
    secs = [{"type": "verse", "start": 0.0, "end": 32.4}, {"type": "verse", "start": 32.4, "end": 41.2},
            {"type": "outro", "start": 41.2, "end": 46.0}]
    out, meta = snap_sections_to_bars(secs, bars)
    assert out[0]["end"] == out[1]["start"] == 32.86      # 0.46 s off → bar line
    assert out[1]["end"] == out[2]["start"] == 40.86
    assert out[0]["start"] == 0.0 and out[-1]["end"] == 46.0   # outer edges stay
    assert secs[0]["end"] == 32.4                                 # caller's data untouched


def test_section_boundary_far_from_a_bar_is_kept():
    from section_consensus import snap_sections_to_bars
    bars = [4.0 * i for i in range(10)]
    secs = [{"type": "a", "start": 0, "end": 18.0}, {"type": "b", "start": 18.0, "end": 36.0}]
    out, meta = snap_sections_to_bars(secs, bars)
    assert out[1]["start"] == 18.0 and not meta["applied"]   # exactly mid-bar: half a bar off, not "within"


def test_boundary_on_a_chord_change_is_anchored_by_harmony():
    from section_consensus import snap_sections_to_bars
    # Bridge drawn on the Bbm at 66.84; the grid has a bar line at 68.62.
    bars = [60.88, 64.84, 68.62, 72.2]
    secs = [{"type": "verse", "start": 50.0, "end": 66.84}, {"type": "bridge", "start": 66.84, "end": 80.0}]
    out, meta = snap_sections_to_bars(secs, bars, [{"time": 64.84}, {"time": 66.84}, {"time": 68.62}])
    assert out[1]["start"] == 66.84 and meta["chord_anchored"] == 1
