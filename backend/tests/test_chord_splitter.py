"""Tests for backend.chord_splitter — serve-time chord bar-splitting.

Covers the two UI bugs the splitter exists to fix:
  - long chords (8/12/16 beats) overflow the chord card
  - dot animation speed varies because dot count != beat count

The splitter only runs when downbeats are trustworthy (madmom/beat_this or
arbitrator-applied) — these tests pin both the happy path and the gates.
"""

import unittest

from backend.chord_splitter import (
    split_chords_at_bars,
    maybe_split_for_serve,
    _interior_downbeats,
    _is_confident,
    _median_bar_gap,
    _drop_small_segment_boundaries,
    _interpolate_oversized_gaps,
)


class TestInteriorDownbeats(unittest.TestCase):
    def test_strict_interior(self):
        # downbeats at 0, 2, 4, 6, 8; chord spans 2..6 → only 4 is interior
        result = _interior_downbeats(2.0, 6.0, [0.0, 2.0, 4.0, 6.0, 8.0])
        self.assertEqual(result, [4.0])

    def test_boundary_within_epsilon_excluded(self):
        # 2.001 is "at" 2.0 within epsilon → not interior
        result = _interior_downbeats(2.001, 6.0, [2.0, 4.0])
        self.assertEqual(result, [4.0])

    def test_no_interior(self):
        result = _interior_downbeats(2.0, 4.0, [0.0, 2.0, 4.0])
        self.assertEqual(result, [])


class TestSplitChordsAtBars(unittest.TestCase):
    def test_4_4_eight_beat_chord_splits_in_two(self):
        # 4/4 @ 120bpm: bar = 2.0s. 8-beat chord = 4.0s spans one interior downbeat
        chords = [{"time": 0.0, "end": 4.0, "chord": "C"}]
        downbeats = [0.0, 2.0, 4.0]
        out = split_chords_at_bars(chords, downbeats)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["time"], 0.0)
        self.assertEqual(out[0]["end"], 2.0)
        self.assertEqual(out[1]["time"], 2.0)
        self.assertEqual(out[1]["end"], 4.0)
        self.assertTrue(all(seg["chord"] == "C" for seg in out))
        self.assertTrue(all(seg.get("auto_split") for seg in out))

    def test_4_4_twelve_beat_chord_splits_in_three(self):
        # 12-beat chord = 6.0s spans two interior downbeats
        chords = [{"time": 0.0, "end": 6.0, "chord": "Am"}]
        downbeats = [0.0, 2.0, 4.0, 6.0]
        out = split_chords_at_bars(chords, downbeats)
        self.assertEqual(len(out), 3)
        self.assertEqual([s["end"] - s["time"] for s in out], [2.0, 2.0, 2.0])

    def test_4_4_sixteen_beat_chord_splits_in_four(self):
        chords = [{"time": 0.0, "end": 8.0, "chord": "G"}]
        downbeats = [0.0, 2.0, 4.0, 6.0, 8.0]
        out = split_chords_at_bars(chords, downbeats)
        self.assertEqual(len(out), 4)

    def test_short_chord_passes_through(self):
        # 2-beat chord (1.0s) with no interior downbeats → unchanged
        chords = [{"time": 0.0, "end": 1.0, "chord": "F"}]
        downbeats = [0.0, 2.0]
        out = split_chords_at_bars(chords, downbeats)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0], chords[0])
        self.assertNotIn("auto_split", out[0])

    def test_no_downbeats_passes_through(self):
        chords = [{"time": 0.0, "end": 8.0, "chord": "C"}]
        out = split_chords_at_bars(chords, [])
        self.assertEqual(out, chords)

    def test_chord_without_end_passes_through(self):
        chords = [{"time": 0.0, "chord": "C"}]
        out = split_chords_at_bars(chords, [0.0, 2.0, 4.0])
        self.assertEqual(out, chords)

    def test_preserves_extra_fields(self):
        chords = [{"time": 0.0, "end": 4.0, "chord": "C", "bass": "E", "custom": "x"}]
        out = split_chords_at_bars(chords, [0.0, 2.0, 4.0])
        self.assertEqual(len(out), 2)
        for seg in out:
            self.assertEqual(seg["bass"], "E")
            self.assertEqual(seg["custom"], "x")

    def test_does_not_mutate_input(self):
        chords = [{"time": 0.0, "end": 4.0, "chord": "C"}]
        original = dict(chords[0])
        split_chords_at_bars(chords, [0.0, 2.0, 4.0])
        self.assertEqual(chords[0], original)

    def test_missed_downbeats_interpolated(self):
        # Real case from "Right Here Waiting": F at [8.39, 17.00] but beat_this
        # only emitted downbeats at 8.40 and 15.00 inside this range — a 6.6s
        # "bar" (median bar = 2.7s). Expect interpolation to fill the gap so
        # F splits into ~3 bars + a short final bar (or merged via min-seg).
        chords = [{"time": 8.39, "end": 17.00, "chord": "F"}]
        # song-wide downbeats including the gap
        downbeats = [3.0, 5.7, 8.4, 15.0, 17.02, 19.7, 22.4, 25.1]
        out = split_chords_at_bars(chords, downbeats)
        # Should produce at least 3 segments, none shorter than ~1.0s
        self.assertGreaterEqual(len(out), 3)
        for seg in out:
            self.assertGreater(seg["end"] - seg["time"], 1.0)

    def test_song_end_chord_no_interior_downbeats_split_via_interpolation(self):
        # Real case from "Right Here Waiting": last C at [254.41, 262.42] (8s).
        # beat_this stops emitting downbeats at 253.56 — so zero interior
        # downbeats inside the C. Median bar 2.7s. Should split into 3 bars
        # via interpolation, not pass through as one 16-beat overflow card.
        chords = [{"time": 254.41, "end": 262.42, "chord": "C"}]
        downbeats = [243.38, 246.08, 248.78, 251.5, 253.56]  # all before C
        out = split_chords_at_bars(chords, downbeats)
        self.assertEqual(len(out), 3)
        for seg in out:
            dur = seg["end"] - seg["time"]
            self.assertAlmostEqual(dur, 8.01 / 3, places=2)
            self.assertTrue(seg["auto_split"])

    def test_real_world_tail_sliver_dropped(self):
        # Real case: D chord at [93.553, 100.055] with downbeats at 96.34, 99.82.
        # Bar length ~3.48s (median of song). 99.82 → 100.055 = 0.235s sliver.
        # Should split at 96.34 only (2 segments), drop 99.82 split point.
        chords = [{"time": 93.553, "end": 100.055, "chord": "D"}]
        downbeats = [89.36, 91.12, 92.86, 96.34, 99.82, 103.22, 106.76]
        out = split_chords_at_bars(chords, downbeats)
        self.assertEqual(len(out), 2)
        self.assertAlmostEqual(out[0]["time"], 93.553, places=3)
        self.assertAlmostEqual(out[0]["end"], 96.34, places=3)
        self.assertAlmostEqual(out[1]["time"], 96.34, places=3)
        self.assertAlmostEqual(out[1]["end"], 100.055, places=3)

    def test_four_plus_one_fragment_boundary_dropped(self):
        chords = [{"time": 0.0, "end": 2.5, "chord": "C"}]
        downbeats = [0.0, 2.0, 4.0]
        out = split_chords_at_bars(chords, downbeats)
        self.assertEqual(len(out), 1)
        self.assertNotIn("auto_split", out[0])

    def test_duplicate_downbeats_collapse(self):
        # Duplicate downbeat at 103.22 and 103.30 (0.08s apart) — splitter
        # should not produce a 0.08s segment.
        chords = [{"time": 100.0, "end": 107.0, "chord": "G"}]
        downbeats = [96.5, 100.0, 103.22, 103.30, 106.7, 110.0]
        out = split_chords_at_bars(chords, downbeats)
        # interior raw = [103.22, 103.30, 106.7]; sliver between dups removed
        self.assertTrue(all((s["end"] - s["time"]) > 0.5 for s in out))

    def test_mixed_chords_some_split_some_not(self):
        chords = [
            {"time": 0.0, "end": 1.0, "chord": "C"},   # short, no split
            {"time": 1.0, "end": 5.0, "chord": "Am"},  # spans db at 2,4 → 3 segs
            {"time": 5.0, "end": 6.0, "chord": "F"},   # short, no split
        ]
        downbeats = [0.0, 2.0, 4.0, 6.0]
        out = split_chords_at_bars(chords, downbeats)
        self.assertEqual(len(out), 5)
        self.assertEqual(out[0]["chord"], "C")
        self.assertEqual([s["chord"] for s in out[1:4]], ["Am", "Am", "Am"])
        self.assertEqual(out[4]["chord"], "F")


class TestConfidenceGate(unittest.TestCase):
    def test_madmom_passes(self):
        self.assertTrue(_is_confident({
            "downbeats": [0.0, 2.0, 4.0],
            "beats_source": "madmom",
        }))

    def test_beat_this_passes(self):
        self.assertTrue(_is_confident({
            "downbeats": [0.0, 2.0, 4.0],
            "beats_source": "beat_this",
        }))

    def test_librosa_fallback_blocked(self):
        self.assertFalse(_is_confident({
            "downbeats": [0.0, 2.0, 4.0],
            "beats_source": "librosa-fallback",
        }))

    def test_arbitrator_applied_passes(self):
        self.assertTrue(_is_confident({
            "downbeats": [0.0, 2.0, 4.0],
            "beats_source": "librosa-fallback",
            "bar_correction": {"applied": True},
        }))

    def test_too_few_downbeats_blocked(self):
        self.assertFalse(_is_confident({
            "downbeats": [0.0],
            "beats_source": "madmom",
        }))


class TestMaybeSplitForServe(unittest.TestCase):
    def test_applied_path(self):
        data = {
            "chords": [{"time": 0.0, "end": 4.0, "chord": "C"}],
            "downbeats": [0.0, 2.0, 4.0],
            "beats_source": "madmom",
        }
        out = maybe_split_for_serve(data)
        self.assertEqual(len(out["chords"]), 2)
        self.assertTrue(out["auto_split_meta"]["applied"])
        self.assertEqual(out["auto_split_meta"]["before"], 1)
        self.assertEqual(out["auto_split_meta"]["after"], 2)

    def test_low_confidence_skips(self):
        data = {
            "chords": [{"time": 0.0, "end": 8.0, "chord": "C"}],
            "downbeats": [0.0, 2.0, 4.0, 6.0, 8.0],
            "beats_source": "librosa-fallback",
        }
        out = maybe_split_for_serve(data)
        self.assertEqual(len(out["chords"]), 1)
        self.assertFalse(out["auto_split_meta"]["applied"])
        self.assertEqual(out["auto_split_meta"]["reason"], "low-confidence-downbeats")

    def test_no_chords(self):
        data = {"chords": [], "downbeats": [0.0, 2.0], "beats_source": "madmom"}
        out = maybe_split_for_serve(data)
        self.assertFalse(out["auto_split_meta"]["applied"])
        self.assertEqual(out["auto_split_meta"]["reason"], "no-chords")

    def test_three_four_with_fuzz_passes(self):
        # 'A Thousand Miles' scenario: 93.8 BPM, song genuinely in 3/4.
        # 125 downbeats over 237s → median gap ~1.90s → bpb ≈ 2.97. The
        # strict <3.0 gate would reject by 0.03 due to a couple of
        # dropped/extra downbeats; widened bound to 2.7 lets it pass.
        # bar_arbitrator on the same chord data also reports
        # beats_per_bar: 3, so 3/4 is the right call here.
        bar_gap = (60.0 / 93.8) * 2.97  # ~1.90s
        raw_db = [round(i * bar_gap, 2) for i in range(20)]
        data = {
            "chords": [{"time": 0.0, "end": raw_db[-1], "chord": "B"}],
            "downbeats": raw_db,
            "beats_source": "beat_this",
            "bpm": 93.8,
        }
        out = maybe_split_for_serve(data)
        self.assertTrue(out["auto_split_meta"]["applied"])
        self.assertEqual(out["auto_split_meta"]["reason"], "ok")

    def test_two_seven_lower_bound_still_rejects(self):
        # bpb=2.5 is too far from genuine 3/4 — likely sparse half-bar
        # emissions. Reject, since halved fallback only triggers in
        # [1.5, 2.3].
        bar_gap = (60.0 / 100.0) * 2.5  # 1.5s
        raw_db = [round(i * bar_gap, 2) for i in range(10)]
        data = {
            "chords": [{"time": 0.0, "end": raw_db[-1], "chord": "C"}],
            "downbeats": raw_db,
            "beats_source": "beat_this",
            "bpm": 100.0,
        }
        out = maybe_split_for_serve(data)
        self.assertFalse(out["auto_split_meta"]["applied"])
        self.assertTrue(out["auto_split_meta"]["reason"].startswith("implausible-bpb="))

    def test_halved_downbeats_fallback(self):
        # Slow-ballad scenario: beat_this emits a downbeat every 2 beats
        # (half-bar density). With BPM 71.4 and gap 0.84s the raw bpb is
        # ~2.12 — under 3.0 so the strict gate would reject. The halved
        # grid (every other downbeat, gap 1.68s) gives bpb ≈ 4.24, in
        # range. Reproduces the Air Supply 'Out Of Nothing At All' case
        # (LiveChord-11w).
        bar_gap = (60.0 / 71.4) * 2.12  # ~1.78s
        raw_db = [round(i * bar_gap, 2) for i in range(9)]  # 9 half-bars
        data = {
            "chords": [{"time": 0.0, "end": raw_db[-1], "chord": "C"}],
            "downbeats": raw_db,
            "beats_source": "beat_this",
            "bpm": 71.4,
        }
        out = maybe_split_for_serve(data)
        self.assertTrue(out["auto_split_meta"]["applied"])
        self.assertEqual(out["auto_split_meta"]["reason"], "ok-halved-downbeats")
        # 9 half-bar downbeats → 5 full-bar downbeats (idx 0,2,4,6,8) →
        # 3 interior split points → original chord becomes 4 segments.
        self.assertEqual(out["auto_split_meta"]["after"], 4)

    def test_fragment_guard_blocks_shifted_4_4_one_three_splits(self):
        data = {
            "chords": [
                {"time": 0.0, "end": 2.0, "chord": "C"},
                {"time": 2.0, "end": 4.0, "chord": "F"},
                {"time": 4.0, "end": 6.0, "chord": "G"},
            ],
            "downbeats": [0.5, 2.5, 4.5, 6.5],
            "beats_source": "beat_this",
            "bpm": 120.0,
        }
        out = maybe_split_for_serve(data)
        self.assertFalse(out["auto_split_meta"]["applied"])
        self.assertEqual(out["auto_split_meta"]["reason"], "fragment-guard")
        guard = out["auto_split_meta"]["fragment_guard"]
        self.assertGreaterEqual(guard["skipped"], 3)
        self.assertIn("1+3", guard["patterns"])


if __name__ == "__main__":
    unittest.main()
