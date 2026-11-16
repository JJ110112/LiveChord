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


def test_mid_song_dropout_does_not_shift_later_bars():
    # Chords change every 4 s. The tracker loses one beat at 40 s; a single
    # global bar phase would put every later bar line 1 beat off the chords.
    beats = [i * 0.5 for i in range(40)] + [20 + i for i in range(20)] + [41 + i for i in range(60)]
    onsets = [i * 4.0 for i in range(10)] + [41 + i * 4.0 for i in range(15)]
    d = {"beats": beats, "downbeats": [], "bpm": 60, "chords": [{"time": t} for t in onsets]}
    assert regularize(d)["applied"]
    later = [b for b in d["downbeats"] if b > 45]
    assert later and all(any(abs(b - t) < 0.05 for t in onsets) for b in later)


def test_chord_boundary_snaps_to_beat_but_keeps_short_chords():
    from beat_density_regularizer import snap_chords_to_beats
    beats = [float(i) for i in range(20)]
    # Gm7b5 starts 0.4 beat late (BTC lag) → onto beat 6; the next boundary
    # is 0.3 off but would leave a < half-beat chord, so it stays.
    chords = [{"chord": "Fm", "time": 0.0, "end": 6.4}, {"chord": "Gm7b5", "time": 6.4, "end": 8.0},
              {"chord": "C7", "time": 8.0, "end": 8.3}, {"chord": "Db", "time": 8.3, "end": 12.0}]
    out, moved = snap_chords_to_beats(chords, beats, 1.0)
    assert out[1]["time"] == 6.0 and out[0]["end"] == 6.0
    assert out[3]["time"] == 8.3          # C7 would shrink to 0 → untouched
    assert out[0]["time"] == 0.0 and out[-1]["end"] == 12.0


def test_late_first_chord_and_mid_beat_boundary_snap():
    from beat_density_regularizer import snap_chords_to_beats
    # Countdown to Heaven intro: beats every 1.0 s from 0.86; BTC puts Dbmaj7
    # at 1.42 and Eb at 2.42 — a 1-beat Db. Both snap back a beat → 2 + 2.
    beats = [0.86 + i for i in range(10)]
    chords = [{"chord": "Dbmaj7", "time": 1.42, "end": 2.42}, {"chord": "Eb", "time": 2.42, "end": 4.86},
              {"chord": "Fm", "time": 4.86, "end": 8.86}]
    out, _ = snap_chords_to_beats(chords, beats, 0.92)
    assert [(c["time"], c["end"]) for c in out[:2]] == [(0.86, 2.86), (2.86, 4.86)]


def test_user_edited_boundaries_are_never_snapped():
    from beat_density_regularizer import snap_chords_to_beats
    beats = [float(i) for i in range(10)]
    chords = [{"chord": "F", "time": 0.0, "end": 2.3}, {"chord": "G", "time": 2.3, "end": 5.4}, {"chord": "C", "time": 5.4, "end": 9.0}]
    # 2.3 is the user's own boundary (not in the official BTC timeline); 5.4 is BTC's.
    out, _ = snap_chords_to_beats(chords, beats, 1.0, only_times=[0.0, 3.1, 5.4])
    assert out[1]["time"] == 2.3 and out[2]["time"] == 5.0


def test_mid_point_boundary_goes_to_the_half_bar():
    from beat_density_regularizer import snap_chords_to_beats
    # Db(user) 8.84 | Eb at 10.4 — 0.52 after beat 9.88, 0.48 before 10.88.
    # 10.88 is the middle of the bar 8.84–12.86, so Db gets 2 beats.
    beats = [8.84, 9.88, 10.88, 11.84, 12.86, 13.86]
    chords = [{"chord": "Db", "time": 8.84, "end": 10.4}, {"chord": "Eb", "time": 10.4, "end": 12.86},
              {"chord": "Fm", "time": 12.86, "end": 13.86}]
    out, _ = snap_chords_to_beats(chords, beats, 1.0, bars=[8.84, 12.86])
    assert out[1]["time"] == 10.88
