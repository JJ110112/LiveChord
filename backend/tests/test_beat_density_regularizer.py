"""Beat-density regularization must re-level a tracker that switches between
eighth and quarter pulses — and leave a steady pulse alone."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from beat_density_regularizer import regularize


def _gaps(xs):
    return [round(b - a, 3) for a, b in zip(xs, xs[1:])]


def test_eighth_note_intro_is_folded_to_quarters():
    # 12 s of eighths (0.5 s) then 60 s of quarters (1.0 s) at 60 BPM: the
    # Countdown to Heaven pattern. The intro must read 60 BPM, not 120.
    beats = [i * 0.5 for i in range(24)] + [12 + i * 1.0 for i in range(60)]
    d = {"beats": beats, "downbeats": [i * 4.0 for i in range(18)], "bpm": 70, "chords": []}
    meta = regularize(d)
    assert meta["applied"]
    assert set(_gaps(d["beats"])) == {1.0}
    assert d["bpm"] == 60.0
    assert set(_gaps(d["downbeats"])) == {4.0}
    assert all(abs(p["bpm"] - 60) < 0.01 for p in d["tempo_curve"])


def test_steady_pulse_is_untouched():
    beats = [i * 0.5 for i in range(200)]   # a genuine 120 BPM song
    d = {"beats": list(beats), "downbeats": beats[::4], "bpm": 120, "chords": []}
    meta = regularize(d)
    assert not meta["applied"] and d["beats"] == beats


def test_tempo_drift_is_kept():
    # 1.0 s → 0.9 s quarters with an eighth-note stretch: the fold must follow
    # the drift (60 → 66 BPM), not force one tempo on the whole song.
    beats = [i * 1.0 for i in range(40)]
    t = beats[-1]
    beats += [t + 0.45 * (i + 1) for i in range(40)]
    t = beats[-1]
    beats += [t + 0.9 * (i + 1) for i in range(60)]
    d = {"beats": beats, "downbeats": [], "bpm": 70, "chords": []}
    assert regularize(d)["applied"]
    g = _gaps(d["beats"])
    assert all(0.85 <= x <= 1.05 for x in g)
    assert g[0] == 1.0 and abs(g[-1] - 0.9) < 0.01


def test_compound_meter_is_left_to_meter_regularizer():
    d = {"time_signature": "6/8", "beats": [i * 0.5 for i in range(100)], "chords": []}
    assert not regularize(d)["applied"]
