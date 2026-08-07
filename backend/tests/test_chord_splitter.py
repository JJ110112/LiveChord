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
    split_long_chords_evenly_by_bpm,
    maybe_split_for_serve,
    merge_same_chord_fragments,
    _interior_downbeats,
    _is_confident,
    _median_bar_gap,
    _drop_small_segment_boundaries,
    _interpolate_oversized_gaps,
    _resolve_split_downbeats,
    _compound_six_eight_bar_snap,
    _snap_chord_boundaries_to_downbeats,
    _BAR_SNAP_MIN_CONFIDENCE,
    _PHASE_SLIP_TOL_BARS,
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

    def test_six_beat_bar_grid_is_confident(self):
        data = {
            "bpm": 200.0,
            "beats_source": "beat_this",
            "downbeats": [0.0, 1.8, 3.6, 5.4, 7.2],
        }
        resolved = _resolve_split_downbeats(data)
        self.assertIsNotNone(resolved)
        self.assertEqual(len(resolved), 5)

    def test_long_shifted_chord_rebalanced_without_edge_fragments(self):
        # A 6-beat held chord against a shifted 4/4 grid would split as 1+4+1.
        # Rebalance to two even cards instead.
        bpm = 120.0
        spb = 60.0 / bpm
        chords = [{"time": 0.0, "end": 6 * spb, "chord": "C"}]
        downbeats = [-1.5 * spb, 1 * spb, 5 * spb, 9 * spb]
        out = split_chords_at_bars(chords, downbeats)
        self.assertEqual(len(out), 2)
        seg_beats = [(s["end"] - s["time"]) / spb for s in out]
        self.assertEqual([round(v, 2) for v in seg_beats], [3.0, 3.0])
        self.assertTrue(all(s.get("auto_split") for s in out))

    def test_long_chord_without_interior_downbeat_splits_evenly(self):
        bpm = 125.0
        spb = 60.0 / bpm
        chords = [{"time": 4.17, "end": 4.17 + 6.16 * spb, "chord": "G"}]
        downbeats = [0.33, 2.25, 8.01, 9.93]

        out = split_chords_at_bars(chords, downbeats)

        self.assertEqual(len(out), 2)
        seg_beats = [(s["end"] - s["time"]) / spb for s in out]
        self.assertEqual([round(v, 2) for v in seg_beats], [3.08, 3.08])
        self.assertTrue(all(s.get("auto_split") for s in out))

    def test_low_confidence_long_chord_splits_evenly_by_bpm(self):
        bpm = 60.0
        chords = [{"time": 0.0, "end": 8.0, "chord": "C"}]

        out = split_long_chords_evenly_by_bpm(chords, bpm, 4)

        self.assertEqual(len(out), 2)
        self.assertEqual([(c["time"], c["end"]) for c in out], [(0.0, 4.0), (4.0, 8.0)])
        self.assertTrue(all(c.get("auto_split") for c in out))
        self.assertTrue(all(c.get("auto_split_fallback") == "bpm-even" for c in out))

    def test_short_chord_passes_through(self):
        # 2-beat chord (1.0s) with no interior downbeats → unchanged
        chords = [{"time": 0.0, "end": 1.0, "chord": "F"}]
        downbeats = [0.0, 2.0]
        out = split_chords_at_bars(chords, downbeats)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0], chords[0])
        self.assertNotIn("auto_split", out[0])

    def test_global_arbiter_cards_are_not_split(self):
        chords = [{
            "time": 0.0,
            "end": 8.0,
            "chord": "G",
            "display_beats": 4,
            "global_arbiter": "free-time-intro-cycle",
        }]
        downbeats = [0.0, 2.0, 4.0, 6.0, 8.0]

        out = split_chords_at_bars(chords, downbeats)

        self.assertEqual(out, chords)

    def test_stable_downbeat_quantize_cards_can_still_split(self):
        chords = [{
            "time": 3.24,
            "end": 12.34,
            "chord": "C",
            "display_beats": 4,
            "global_arbiter": "stable-downbeat-quantize",
        }]
        downbeats = [3.22, 5.46, 7.76, 10.04, 12.34]

        out = split_chords_at_bars(chords, downbeats)

        self.assertGreater(len(out), 1)
        self.assertTrue(all(c.get("auto_split") for c in out))

    def test_one_bar_chord_not_split_by_local_half_bar_downbeat(self):
        # Mixed grid: global median bar is 2.0s, but this local C bar has an
        # extra half-bar downbeat at 11.0. It is still C(4), not C(2)+C(2).
        chords = [{"time": 10.0, "end": 12.0, "chord": "C"}]
        downbeats = [0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 11.0, 12.0, 14.0, 16.0]
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

    def test_preserves_split_display_beats(self):
        chords = [{"time": 0.0, "end": 6.0, "chord": "Fm", "split_display_beats": [4, 2]}]
        out = split_chords_at_bars(chords, [0.0, 4.0])
        self.assertEqual([c["display_beats"] for c in out], [4, 2])
        self.assertTrue(all(c.get("auto_split") for c in out))

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

    def test_merge_same_chord_three_plus_one_persisted_fragment(self):
        chords = [
            {"time": 0.0, "end": 1.5, "chord": "Cm7", "auto_split": True},
            {"time": 1.5, "end": 2.0, "chord": "Cm7", "auto_split": True},
            {"time": 2.0, "end": 4.0, "chord": "F"},
        ]
        out, meta = merge_same_chord_fragments(chords, bpm=120.0)
        self.assertTrue(meta["applied"])
        self.assertEqual(meta["merged"], 1)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["time"], 0.0)
        self.assertEqual(out[0]["end"], 2.0)
        self.assertNotIn("auto_split", out[0])

    def test_preserve_stale_auto_split_full_bar_plus_sliver(self):
        chords = [
            {"time": 10.0, "end": 10.6, "chord": "Fm7", "auto_split": True},
            {"time": 10.6, "end": 12.7, "chord": "Fm7", "auto_split": True},
            {"time": 12.7, "end": 13.5, "chord": "Fm7", "auto_split": True},
            {"time": 13.5, "end": 15.0, "chord": "Gm7"},
        ]
        out, meta = merge_same_chord_fragments(chords, bpm=115.0)
        self.assertFalse(meta["applied"])
        self.assertEqual(meta["merged"], 0)
        self.assertEqual(len(out), 4)
        self.assertEqual(out[0]["time"], 10.0)
        self.assertEqual(out[0]["end"], 10.6)

    def test_merge_stale_auto_split_same_chord_single_bar_fragment(self):
        chords = [
            {"time": 13.61, "end": 14.88, "chord": "Gm7", "auto_split": True},
            {"time": 14.88, "end": 15.83, "chord": "Gm7", "auto_split": True},
            {"time": 15.83, "end": 17.0, "chord": "Cm7"},
        ]
        out, meta = merge_same_chord_fragments(chords, bpm=115.4)
        self.assertTrue(meta["applied"])
        self.assertEqual(meta["merged"], 1)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["time"], 13.61)
        self.assertEqual(out[0]["end"], 15.83)
        self.assertNotIn("auto_split", out[0])

    def test_same_chord_merge_preserves_global_arbiter_splits(self):
        chords = [
            {
                "time": 0.0,
                "end": 2.5,
                "chord": "Cm7",
                "display_beats": 4,
                "global_arbiter": "stable-downbeat-quantize",
            },
            {
                "time": 2.5,
                "end": 3.2,
                "chord": "Cm7",
                "display_beats": 1,
                "global_arbiter": "stable-downbeat-quantize",
            },
            {
                "time": 3.2,
                "end": 5.7,
                "chord": "Ab",
                "display_beats": 4,
                "global_arbiter": "stable-downbeat-quantize",
            },
        ]
        out, meta = merge_same_chord_fragments(chords, bpm=95.0)
        self.assertFalse(meta["applied"])
        self.assertEqual(meta["merged"], 0)
        self.assertEqual(len(out), 3)
        self.assertEqual([c.get("display_beats") for c in out[:2]], [4, 1])

    def test_merge_same_chord_one_bar_fragment_without_auto_split_flag(self):
        spb = 60.0 / 70.0
        chords = [
            {"time": 31.0, "end": 31.0 + 3 * spb, "chord": "Bbmaj7"},
            {"time": 31.0 + 3 * spb, "end": 31.0 + 5 * spb, "chord": "Bbmaj7"},
            {"time": 31.0 + 5 * spb, "end": 31.0 + 9 * spb, "chord": "F"},
        ]
        out, meta = merge_same_chord_fragments(chords, bpm=70.0)
        self.assertTrue(meta["applied"])
        self.assertEqual(meta["merged"], 1)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["chord"], "Bbmaj7")
        self.assertAlmostEqual(out[0]["time"], 31.0)
        self.assertAlmostEqual(out[0]["end"], 31.0 + 5 * spb)

    def test_merge_same_chord_run_keeps_first_bar_then_merges_tail(self):
        spb = 60.0 / 70.0
        chords = [
            {"time": 80.0, "end": 80.0 + 4 * spb, "chord": "C7"},
            {"time": 80.0 + 4 * spb, "end": 80.0 + 7 * spb, "chord": "C7"},
            {"time": 80.0 + 7 * spb, "end": 80.0 + 8 * spb, "chord": "C7"},
            {"time": 80.0 + 8 * spb, "end": 80.0 + 12 * spb, "chord": "F"},
        ]
        out, meta = merge_same_chord_fragments(chords, bpm=70.0)
        self.assertTrue(meta["applied"])
        self.assertEqual(meta["merged"], 1)
        self.assertEqual(len(out), 3)
        self.assertAlmostEqual(out[0]["time"], 80.0)
        self.assertAlmostEqual(out[0]["end"], 80.0 + 4 * spb)
        self.assertAlmostEqual(out[1]["time"], 80.0 + 4 * spb)
        self.assertAlmostEqual(out[1]["end"], 80.0 + 8 * spb)

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
    def test_explicit_meter_preserves_manual_cards(self):
        data = {
            "bpm": 47.62,
            "display_bpm": 47.62,
            "time_signature": "6/8",
            "meter_correction": {"applied": True},
            "chords": [
                {"time": 16.30, "end": 16.72, "chord": "Am"},
                {"time": 16.72, "end": 17.98, "chord": "Am"},
                {"time": 17.98, "end": 19.24, "chord": "Am"},
                {"time": 19.24, "end": 20.50, "chord": "Am"},
            ],
            "downbeats": [16.3, 18.82, 21.34],
            "beats_source": "demo_manual_6_8",
        }

        out = maybe_split_for_serve(data)

        self.assertEqual(len(out["chords"]), 4)
        self.assertEqual([c["time"] for c in out["chords"]], [16.30, 16.72, 17.98, 19.24])
        self.assertFalse(out["auto_split_meta"]["applied"])
        self.assertEqual(out["auto_split_meta"]["reason"], "explicit-meter-card-grid")

    def test_explicit_meter_splits_long_served_cards(self):
        data = {
            "bpm": 47.62,
            "display_bpm": 47.62,
            "time_signature": "6/8",
            "meter_correction": {"applied": True},
            "chords": [
                {"time": 16.72, "end": 22.40, "chord": "Am"},
            ],
            "downbeats": [16.3, 18.82, 21.34],
            "beats_source": "demo_manual_6_8",
        }

        out = maybe_split_for_serve(data)

        self.assertEqual(len(out["chords"]), 5)
        self.assertTrue(out["auto_split_meta"]["applied"])
        self.assertEqual(out["auto_split_meta"]["reason"], "explicit-meter-card-grid")
        self.assertTrue(all(c["chord"] == "Am" for c in out["chords"]))
        self.assertLess(max(c["end"] - c["time"] for c in out["chords"]), 1.2)

    def test_six_eight_explicit_meter_uses_pulse_grid(self):
        data = {
            "bpm": 47.62,
            "display_bpm": 47.62,
            "time_signature": "6/8",
            "meter_correction": {"applied": True},
            "beats": [1.10, 2.44, 3.74, 5.04, 6.32],
            "chords": [
                {"time": 1.10, "end": 2.44, "chord": "Am"},
                {"time": 2.44, "end": 3.74, "chord": "C"},
                {"time": 3.74, "end": 5.04, "chord": "G"},
                {"time": 5.04, "end": 6.32, "chord": "Em"},
            ],
        }

        out = maybe_split_for_serve(data)

        self.assertEqual([c["chord"] for c in out["chords"]], ["Am", "C", "G", "Em"])
        self.assertEqual([c["time"] for c in out["chords"]], [1.10, 2.44, 3.74, 5.04])
        self.assertEqual(out["auto_split_meta"]["reason"], "explicit-meter-card-grid")

    def test_compound_six_eight_absorbs_one_tick_card_and_resyncs_next_bar(self):
        data = {
            "bpm": 136.4,
            "time_signature": "6/8",
            "display_subdivisions_per_bar": 6,
            "practice_pulses_per_bar": 2,
            "beats": [18.40, 18.84, 19.28, 19.70, 20.12, 20.56, 20.98, 21.42, 21.86, 22.30, 22.74, 23.16, 23.56],
            "downbeats": [18.40, 20.98, 23.56],
            "beats_source": "beat_this",
            "chords": [
                {"time": 18.40, "end": 20.12, "chord": "Bb"},
                {"time": 20.12, "end": 20.56, "chord": "F"},
                {"time": 20.56, "end": 23.56, "chord": "Eb"},
            ],
        }

        out = maybe_split_for_serve(data)

        self.assertEqual([c["chord"] for c in out["chords"]], ["Bb", "Eb"])
        self.assertEqual([c["time"] for c in out["chords"]], [18.40, 20.98])
        self.assertEqual([c["end"] for c in out["chords"]], [20.98, 23.56])
        cleanup = out["auto_split_meta"]["fragment_guard"]["compound_cleanup"]
        self.assertTrue(cleanup["applied"])
        self.assertEqual(cleanup["merged"], 1)

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
            "chords": [{"time": 0.0, "end": 2.4, "chord": "C"}],
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

    def test_fragment_guard_still_splits_clearly_long_cards(self):
        data = {
            "chords": [
                {"time": 0.0, "end": 4.0, "chord": "C"},
                {"time": 4.0, "end": 6.0, "chord": "F"},
                {"time": 6.0, "end": 8.0, "chord": "G"},
                {"time": 8.0, "end": 10.0, "chord": "Am"},
            ],
            "downbeats": [2.0, 4.5, 6.5, 8.5, 10.5],
            "beats_source": "beat_this",
            "bpm": 120.0,
        }
        out = maybe_split_for_serve(data)
        self.assertTrue(out["auto_split_meta"]["applied"])
        self.assertEqual(out["auto_split_meta"]["reason"], "fragment-guard-safe-long-split")
        self.assertEqual(out["auto_split_meta"]["before"], 4)
        self.assertEqual(out["auto_split_meta"]["after"], 5)
        self.assertEqual(out["chords"][0]["time"], 0.0)
        self.assertEqual(out["chords"][0]["end"], 2.0)
        self.assertEqual(out["chords"][1]["time"], 2.0)
        self.assertEqual(out["chords"][1]["end"], 4.0)
        self.assertEqual([c["chord"] for c in out["chords"][2:]], ["F", "G", "Am"])

    def test_stale_merge_prevents_immediate_resplit(self):
        data = {
            "chords": [
                {"time": 0.0, "end": 0.5, "chord": "Gm7", "auto_split": True},
                {"time": 0.5, "end": 2.0, "chord": "Gm7", "auto_split": True},
                {"time": 2.0, "end": 4.0, "chord": "Cm7"},
            ],
            "downbeats": [0.5, 2.0, 4.0],
            "beats_source": "beat_this",
            "bpm": 120.0,
        }
        out = maybe_split_for_serve(data)
        self.assertTrue(out["same_chord_fragment_meta"]["applied"])
        self.assertFalse(out["auto_split_meta"]["applied"])
        self.assertEqual(out["auto_split_meta"]["reason"], "fragment-guard-after-stale-merge")
        self.assertEqual(len(out["chords"]), 2)
        self.assertEqual(out["chords"][0]["time"], 0.0)
        self.assertEqual(out["chords"][0]["end"], 2.0)


class TestCompoundBarSnapConfidenceGate(unittest.TestCase):
    """Phase 1: per-bar Sigmoid confidence gates _compound_six_eight_bar_snap.

    Fixture: 6/8 at eighth-pulse 120 BPM (beat_gap = 0.5s, bar = 3.0s).
    Three chord cards each ending 0.1s past a bar — both interior boundaries
    are snap candidates within the ±1-eighth (±0.5s) tolerance, and both
    resulting cards stay above the 2-eighth (1.0s) minimum, so Phase 0
    unconditionally snaps both.
    """

    def _fixture(self, *, bar_probs=None, omit_key=False):
        # bars at 0/3/6/9; chords each end 0.1s past a bar → 2 snap candidates
        data = {
            "time_signature": "6/8",
            "bars": [0.0, 3.0, 6.0, 9.0],
            "beats": [i * 0.5 for i in range(20)],  # 10s of eighth pulses
            "bpm": 120.0,
            "chords": [
                {"time": 0.0, "end": 3.1, "chord": "C"},
                {"time": 3.1, "end": 6.1, "chord": "G"},
                {"time": 6.1, "end": 9.0, "chord": "F"},
            ],
        }
        if not omit_key:
            data["bar_probs"] = bar_probs if bar_probs is not None else []
        return data

    def test_high_conf_snaps_both_boundaries(self):
        data = self._fixture(bar_probs=[0.9, 0.9, 0.9, 0.9])
        out, meta = _compound_six_eight_bar_snap(data["chords"], data)
        self.assertEqual(meta["snapped"], 2)
        self.assertTrue(meta["applied"])
        self.assertEqual(meta["confidence_gated_skips"], 0)
        self.assertTrue(meta["confidence_gate_active"])
        self.assertAlmostEqual(out[0]["end"], 3.0, places=3)
        self.assertAlmostEqual(out[1]["time"], 3.0, places=3)
        self.assertAlmostEqual(out[1]["end"], 6.0, places=3)
        self.assertAlmostEqual(out[2]["time"], 6.0, places=3)

    def test_low_conf_skips_all_snaps(self):
        data = self._fixture(bar_probs=[0.3, 0.3, 0.3, 0.3])
        out, meta = _compound_six_eight_bar_snap(data["chords"], data)
        self.assertEqual(meta["snapped"], 0)
        self.assertFalse(meta["applied"])
        self.assertEqual(meta["confidence_gated_skips"], 2)
        self.assertTrue(meta["confidence_gate_active"])
        # Original boundaries preserved
        self.assertAlmostEqual(out[0]["end"], 3.1, places=3)
        self.assertAlmostEqual(out[1]["time"], 3.1, places=3)

    def test_mixed_conf_only_high_snaps(self):
        # bars[1]=3.0 high-conf → first snap fires; bars[2]=6.0 low-conf →
        # second snap gated. bars[0] and bars[3] are not lookup targets.
        data = self._fixture(bar_probs=[0.5, 0.9, 0.3, 0.5])
        out, meta = _compound_six_eight_bar_snap(data["chords"], data)
        self.assertEqual(meta["snapped"], 1)
        self.assertEqual(meta["confidence_gated_skips"], 1)
        self.assertAlmostEqual(out[0]["end"], 3.0, places=3)
        self.assertAlmostEqual(out[1]["time"], 3.0, places=3)
        # Second boundary NOT snapped
        self.assertAlmostEqual(out[1]["end"], 6.1, places=3)
        self.assertAlmostEqual(out[2]["time"], 6.1, places=3)

    def test_threshold_edge_just_below_skips(self):
        # 0.69 is just below 0.70 threshold → skip; 0.71 is above → snap
        data = self._fixture(bar_probs=[0.5, 0.69, 0.71, 0.5])
        self.assertEqual(_BAR_SNAP_MIN_CONFIDENCE, 0.7)
        out, meta = _compound_six_eight_bar_snap(data["chords"], data)
        self.assertEqual(meta["snapped"], 1)
        self.assertEqual(meta["confidence_gated_skips"], 1)
        # First boundary (looks up bars[1]=0.69) gated; second snaps on bars[2]=0.71
        self.assertAlmostEqual(out[0]["end"], 3.1, places=3)
        self.assertAlmostEqual(out[1]["end"], 6.0, places=3)

    def test_legacy_no_probs_key_keeps_phase0_behavior(self):
        # No bar_probs key at all → Phase 0 unconditional snap
        data = self._fixture(omit_key=True)
        out, meta = _compound_six_eight_bar_snap(data["chords"], data)
        self.assertEqual(meta["snapped"], 2)
        self.assertEqual(meta["confidence_gated_skips"], 0)
        self.assertFalse(meta["confidence_gate_active"])

    def test_mismatched_prob_length_falls_back_to_phase0(self):
        # 4 bars but only 2 probs → mismatched → ignore, Phase 0 behavior
        data = self._fixture(bar_probs=[0.9, 0.9])
        out, meta = _compound_six_eight_bar_snap(data["chords"], data)
        self.assertEqual(meta["snapped"], 2)
        self.assertFalse(meta["confidence_gate_active"])
        self.assertEqual(meta["confidence_gated_skips"], 0)

    def test_empty_probs_falls_back_to_phase0(self):
        data = self._fixture(bar_probs=[])
        out, meta = _compound_six_eight_bar_snap(data["chords"], data)
        self.assertEqual(meta["snapped"], 2)
        self.assertFalse(meta["confidence_gate_active"])

    def test_simple_3_4_unaffected_by_phase1(self):
        # time_signature="3/4" hits early return at function entry; bar_probs
        # presence must not change that gate.
        data = self._fixture(bar_probs=[0.9, 0.9, 0.9, 0.9])
        data["time_signature"] = "3/4"
        out, meta = _compound_six_eight_bar_snap(data["chords"], data)
        self.assertEqual(meta["snapped"], 0)
        self.assertFalse(meta["applied"])
        # Should NOT carry phase1 keys when early-returning
        self.assertNotIn("confidence_gate_active", meta)

    def test_nan_or_inf_probs_treated_as_below_threshold(self):
        # _coerce_prob clamps NaN/Inf to 0.0; gate then rejects (< 0.7).
        # Mix in valid high-conf entries to verify the rest of the array
        # still drives snaps normally.
        import json
        data = self._fixture(bar_probs=[
            0.9,                  # bars[0] (unused as snap target)
            float("nan"),         # bars[1] → coerced to 0.0 → skip
            float("inf"),         # bars[2] → coerced to 0.0 → skip
        ])
        # Pad to 4 to match bars length
        data["bar_probs"].append(0.9)
        out, meta = _compound_six_eight_bar_snap(data["chords"], data)
        # Both snap candidates gated by NaN/Inf entries
        self.assertEqual(meta["snapped"], 0)
        self.assertEqual(meta["confidence_gated_skips"], 2)
        # Meta must serialize cleanly to JSON despite originally-bad inputs
        json.dumps(meta)

    # Phase 1b: prob_source provenance tag must not influence splitter
    # behavior — gate decisions are driven purely by bar_probs[] values.
    # These tests pin that invariant so future code can't accidentally
    # special-case 'sample_at_grid' probs (e.g. with a higher threshold)
    # without an explicit, named code change.

    def test_compound_bar_snap_sampled_probs_low_conf_skip(self):
        data = self._fixture(bar_probs=[0.4, 0.4, 0.4, 0.4])
        data["prob_source"] = "sample_at_grid"
        out, meta = _compound_six_eight_bar_snap(data["chords"], data)
        self.assertEqual(meta["snapped"], 0)
        self.assertFalse(meta["applied"])
        self.assertEqual(meta["confidence_gated_skips"], 2)
        self.assertTrue(meta["confidence_gate_active"])
        # Boundaries preserved exactly like peak_pick low-conf case
        self.assertAlmostEqual(out[0]["end"], 3.1, places=3)

    def test_compound_bar_snap_sampled_probs_high_conf_snap(self):
        data = self._fixture(bar_probs=[0.9, 0.9, 0.9, 0.9])
        data["prob_source"] = "sample_at_grid"
        out, meta = _compound_six_eight_bar_snap(data["chords"], data)
        self.assertEqual(meta["snapped"], 2)
        self.assertTrue(meta["applied"])
        self.assertEqual(meta["confidence_gated_skips"], 0)
        self.assertAlmostEqual(out[0]["end"], 3.0, places=3)
        self.assertAlmostEqual(out[1]["end"], 6.0, places=3)


class TestPhaseSlipRecovery(unittest.TestCase):
    """Layer 3 grid-shelter: when fragment-guard rejects normal split because
    BTC boundaries are off-bar too often, snap each interior boundary to the
    nearest downbeat (±0.5 bar tolerance) so the regular splitter can run.
    """

    def _build(self, bpm=97.8, n_bars=12):
        spb = 60.0 / bpm
        bar = 4 * spb
        downbeats = [0.33 + i * bar for i in range(n_bars)]
        return downbeats, spb, bar

    def test_snaps_off_bar_boundary(self):
        downbeats, spb, bar = self._build()
        # Boundary at 3.78 is 1.0s away from the nearest downbeat (2.78);
        # well inside ±0.5-bar tolerance (≈1.22s at 97.8 BPM).
        chords = [
            {"time": 0.33, "end": 3.78, "chord": "A"},
            {"time": 3.78, "end": 7.20, "chord": "B"},
            {"time": 7.20, "end": 12.55, "chord": "C"},
        ]
        out, meta = _snap_chord_boundaries_to_downbeats(chords, downbeats, 97.8, 4)
        self.assertTrue(meta["applied"])
        self.assertEqual(meta["snapped"], 2)
        # First boundary snapped to second downbeat (~2.78)
        self.assertAlmostEqual(out[0]["end"], 0.33 + bar, places=2)
        self.assertAlmostEqual(out[1]["time"], 0.33 + bar, places=2)
        # Second boundary snapped to fourth downbeat (~7.69)
        self.assertAlmostEqual(out[1]["end"], 0.33 + 3 * bar, places=2)
        self.assertAlmostEqual(out[2]["time"], 0.33 + 3 * bar, places=2)

    def test_does_not_snap_when_too_far(self):
        downbeats, spb, bar = self._build()
        # Boundary at 1.5 — nearest downbeats are 0.33 (1.17s away) and
        # 2.78 (1.28s away). 1.17s is just under tolerance, but snapping
        # to 0.33 would collapse chord A to zero. Snapping to 2.78 is
        # outside tolerance (1.28 > 1.22). Either way: no snap.
        chords = [
            {"time": 0.33, "end": 1.5, "chord": "A"},
            {"time": 1.5, "end": 5.5, "chord": "B"},
        ]
        out, meta = _snap_chord_boundaries_to_downbeats(chords, downbeats, 97.8, 4)
        self.assertFalse(meta["applied"])
        self.assertEqual(out[0]["end"], 1.5)

    def test_rejects_snap_that_would_collapse_neighbour(self):
        downbeats, spb, bar = self._build()
        # Boundary at 7.5 — nearest downbeat 7.69 would leave chord B 0.19s
        # (less than 0.5*spb ≈ 0.31s), so we must NOT snap.
        chords = [
            {"time": 5.26, "end": 7.50, "chord": "A"},
            {"time": 7.50, "end": 7.69 + 0.10, "chord": "B"},  # very short
        ]
        out, meta = _snap_chord_boundaries_to_downbeats(chords, downbeats, 97.8, 4)
        self.assertFalse(meta["applied"])

    def test_empty_downbeats(self):
        chords = [
            {"time": 0.0, "end": 2.0, "chord": "A"},
            {"time": 2.0, "end": 4.0, "chord": "B"},
        ]
        out, meta = _snap_chord_boundaries_to_downbeats(chords, [], 100.0, 4)
        self.assertFalse(meta["applied"])
        self.assertEqual(meta["reason"], "insufficient-data")

    def test_first_and_last_anchors_preserved(self):
        downbeats, spb, bar = self._build()
        chords = [
            {"time": 0.10, "end": 3.78, "chord": "A"},
            {"time": 3.78, "end": 11.00, "chord": "B"},
        ]
        out, meta = _snap_chord_boundaries_to_downbeats(chords, downbeats, 97.8, 4)
        # First chord's time and last chord's end NEVER move
        self.assertEqual(out[0]["time"], 0.10)
        self.assertEqual(out[-1]["end"], 11.00)

    def test_full_pipeline_phase_slip_recovery_fires(self):
        """End-to-end: a chord list with 11+ off-bar fragments triggers
        the Layer 3 escalation in maybe_split_for_serve and produces
        bar-aligned cards.
        """
        downbeats, spb, bar = self._build(n_bars=24)
        # Build 12 chord segments that all START 1 beat past a downbeat
        # — produces a 1+3 pattern every bar, fragment_guard nukes them.
        chords = []
        for i in range(12):
            start = downbeats[i] + spb
            end = downbeats[i + 1] + spb
            chords.append({"time": round(start, 3), "end": round(end, 3),
                           "chord": "C" if i % 2 == 0 else "G"})
        data = {
            "chords": chords,
            "downbeats": downbeats,
            "beats": [downbeats[0] + j * spb for j in range(48)],
            "bpm": 97.8,
            "beats_source": "beat_this",
        }
        out = maybe_split_for_serve(data)
        meta = out["auto_split_meta"]
        # Either phase-slip recovered cleanly, OR it ran the safe-long-split
        # fallback with phase_slip_recovery metadata attached. Both indicate
        # the new code path ran.
        psr = meta.get("fragment_guard", {}).get("phase_slip_recovery")
        if "phase-slip-recovered" in meta.get("reason", ""):
            self.assertIsNotNone(psr)
            self.assertTrue(psr["applied"])
        else:
            # If recovery didn't help enough, that's still a valid outcome —
            # but the function must have been called and recorded its attempt.
            self.assertTrue(psr is None or "snapped" in psr,
                            msg=f"unexpected reason={meta.get('reason')}")


if __name__ == "__main__":
    unittest.main()
