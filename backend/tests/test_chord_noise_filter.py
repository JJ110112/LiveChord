import unittest

from backend.chord_noise_filter import (
    filter_noise_tails,
    filter_isolated_short_chords,
    maybe_filter_for_serve,
)


class TestChordNoiseFilter(unittest.TestCase):
    def test_merges_short_same_root_sus_fragment_inside_one_bar(self):
        chords = [
            {"time": 41.57, "end": 43.89, "chord": "Ab"},
            {"time": 43.89, "end": 44.54, "chord": "Bb"},
            {"time": 44.54, "end": 46.20, "chord": "Bbsus4"},
            {"time": 46.20, "end": 48.89, "chord": "Eb"},
        ]
        out = filter_noise_tails(chords, [41.52, 43.92, 46.24], 100.0)
        self.assertEqual([c["chord"] for c in out], ["Ab", "Bb", "Eb"])
        self.assertAlmostEqual(out[1]["time"], 43.89)
        self.assertAlmostEqual(out[1]["end"], 46.20)

    def test_leaves_different_root_passing_chords_alone(self):
        chords = [
            {"time": 0.0, "end": 2.4, "chord": "C"},
            {"time": 2.4, "end": 3.0, "chord": "D7"},
            {"time": 3.0, "end": 4.8, "chord": "G"},
        ]
        out = filter_noise_tails(chords, [0.0, 2.4, 4.8], 100.0)
        self.assertEqual([c["chord"] for c in out], ["C", "D7", "G"])

    def test_preserves_global_arbiter_bar_tail_splits(self):
        chords = [
            {
                "time": 10.286,
                "end": 12.806,
                "chord": "Cm7",
                "display_beats": 4,
                "global_arbiter": "stable-downbeat-quantize",
            },
            {
                "time": 12.806,
                "end": 13.700,
                "chord": "Cm7",
                "display_beats": 1,
                "global_arbiter": "stable-downbeat-quantize",
            },
            {"time": 13.700, "end": 15.325, "chord": "Abmaj7"},
        ]

        out = filter_noise_tails(chords, [10.286, 12.806, 15.325], 93.8)

        self.assertEqual(len(out), 3)
        self.assertEqual([c.get("display_beats") for c in out[:2]], [4, 1])

    def test_explicit_meter_skips_pop_noise_filter(self):
        data = {
            "time_signature": "6/8",
            "meter_correction": {"applied": True},
            "bpm": 47.62,
            "downbeats": [1.10, 3.74, 6.32],
            "chords": [
                {"time": 1.10, "end": 3.74, "chord": "Am"},
                {"time": 3.74, "end": 6.32, "chord": "C"},
                {"time": 6.32, "end": 8.90, "chord": "G"},
            ],
        }

        out = maybe_filter_for_serve(data)

        self.assertEqual([c["chord"] for c in out["chords"]], ["Am", "C", "G"])
        self.assertEqual(out["noise_filter_meta"]["reason"], "explicit-meter-card-grid")


class TestIsolatedShortChordFilter(unittest.TestCase):
    """Filter for mid-bar isolated short chords. See plan calm-juggling-octopus
    for the rule derivation (Path 1 same-root sandwich, Path 2 different-root
    extreme-short)."""

    # Golden-song fixtures from V:\data\chords\b9\b9ff54449865.json
    # (愛我久一點, BPM 97.8, spb ≈ 0.613). Downbeats inferred at 2-beat grid.
    BPM = 97.8
    # Synthetic downbeats far from the noise times so off-downbeat gate passes.
    GOLDEN_DOWNBEATS = [
        0.0, 2.45, 4.91, 7.36, 9.82, 12.27, 14.73, 17.18, 19.64, 22.10,
        # the noise events live near 24.23 / 45.93 / 48.43 — keep gaps
        25.40, 27.85, 30.31, 32.77, 35.22, 37.68, 40.13, 42.59, 44.40, 46.85,
        # 45.93 D ends 46.26: 46.85 is > 0.10 from both → safe
        49.30, 51.76, 54.21, 56.67,
    ]

    def test_path1_same_root_sandwich_removed(self):
        """Gmaj7 — D(0.6s) — Gmaj7 → collapses to single Gmaj7."""
        chords = [
            {"time": 22.38, "end": 24.23, "chord": "Gmaj7"},
            {"time": 24.23, "end": 24.83, "chord": "D"},      # noise
            {"time": 24.83, "end": 27.28, "chord": "Gmaj7"},
        ]
        out, meta = filter_isolated_short_chords(chords, self.GOLDEN_DOWNBEATS, self.BPM)
        self.assertTrue(meta["applied"])
        self.assertEqual(meta["removed_count"], 1)
        self.assertEqual(meta["removed"][0]["path"], "P1")
        self.assertEqual(meta["removed"][0]["chord"], "D")
        # Same-name collapse must fuse the two Gmaj7s
        self.assertEqual([c["chord"] for c in out], ["Gmaj7"])
        self.assertAlmostEqual(out[0]["time"], 22.38)
        self.assertAlmostEqual(out[0]["end"], 27.28)

    def test_path2_different_root_extreme_short_removed(self):
        """Gmaj7(3.96s) — D(0.33s) — Gbm7(2.17s) → D absorbed into Gmaj7."""
        chords = [
            {"time": 41.97, "end": 45.93, "chord": "Gmaj7"},
            {"time": 45.93, "end": 46.26, "chord": "D"},      # noise, ratio 0.152
            {"time": 46.26, "end": 48.43, "chord": "Gbm7"},
        ]
        out, meta = filter_isolated_short_chords(chords, self.GOLDEN_DOWNBEATS, self.BPM)
        self.assertTrue(meta["applied"])
        self.assertEqual(meta["removed_count"], 1)
        self.assertEqual(meta["removed"][0]["path"], "P2")
        self.assertAlmostEqual(meta["removed"][0]["rel_short_ratio"], 0.152, places=2)
        self.assertEqual([c["chord"] for c in out], ["Gmaj7", "Gbm7"])
        # prev was extended to swallow the noise gap
        self.assertAlmostEqual(out[0]["end"], 46.26)
        # next is untouched
        self.assertAlmostEqual(out[1]["time"], 46.26)
        self.assertAlmostEqual(out[1]["end"], 48.43)

    def test_adjacent_noise_events_both_removed_in_one_sweep(self):
        """Real golden-song slice 41.97-50.53s: two adjacent noise events
        (45.93s D Path-2, 48.43s A Path-1) get cleaned in a single pass."""
        chords = [
            {"time": 41.97, "end": 45.93, "chord": "Gmaj7"},
            {"time": 45.93, "end": 46.26, "chord": "D"},      # P2 noise
            {"time": 46.26, "end": 48.43, "chord": "Gbm7"},
            {"time": 48.43, "end": 48.71, "chord": "A"},      # P1 noise
            {"time": 48.71, "end": 50.53, "chord": "Gbm7"},
        ]
        out, meta = filter_isolated_short_chords(chords, self.GOLDEN_DOWNBEATS, self.BPM)
        self.assertEqual(meta["removed_count"], 2)
        paths = [r["path"] for r in meta["removed"]]
        self.assertEqual(paths, ["P2", "P1"])
        # After collapse: Gmaj7 — Gbm7 (the two Gbm7s fuse)
        self.assertEqual([c["chord"] for c in out], ["Gmaj7", "Gbm7"])
        self.assertAlmostEqual(out[0]["end"], 46.26)
        self.assertAlmostEqual(out[1]["time"], 46.26)
        self.assertAlmostEqual(out[1]["end"], 50.53)

    def test_uniform_short_trio_preserved(self):
        """Bm7(1 beat) — Em7(1 beat) — A7(1 beat) — D(1 beat) at 97.8 BPM:
        spb=0.613. Each chord ~0.61s. None are 'short relative to neighbors'
        so Path 2 ratio gate fails (all denominators ~ 0.61, ratio ~ 1.0)."""
        chords = [
            {"time": 0.31, "end": 0.92, "chord": "Bm7"},     # off-db (db at 0.0)
            {"time": 0.92, "end": 1.53, "chord": "Em7"},     # off-db
            {"time": 1.53, "end": 2.15, "chord": "A7"},      # off-db
            {"time": 2.15, "end": 2.76, "chord": "D"},
        ]
        # Path 1 fails (different roots), Path 2 ratio gate fails (1.0 not < 0.20)
        # Middle two (Em7, A7) have curr_dur 0.61 < 0.491? No, 0.61 > 0.491 → Gate A short fails too.
        # Tighter test: bump duration to under Gate A threshold
        chords = [
            {"time": 0.31, "end": 0.72, "chord": "Bm7"},     # 0.41s
            {"time": 0.72, "end": 1.13, "chord": "Em7"},     # 0.41s — candidate
            {"time": 1.13, "end": 1.54, "chord": "A7"},      # 0.41s — candidate
            {"time": 1.54, "end": 2.15, "chord": "D"},
        ]
        downbeats = [0.0, 2.45, 4.91]  # 0.31, 0.72, 1.13, 1.54 all off-db
        out, meta = filter_isolated_short_chords(chords, downbeats, self.BPM)
        # Em7 candidate: prev=Bm7(0.41), next=A7(0.41), ratio=0.41/0.41=1.0 > 0.20 → kept
        # A7 candidate: prev=Em7(0.41 — unchanged since not removed), next=D(0.61), ratio=0.41/0.41=1.0 → kept
        self.assertEqual(meta["removed_count"], 0)
        self.assertEqual([c["chord"] for c in out], ["Bm7", "Em7", "A7", "D"])

    def test_on_downbeat_short_preserved(self):
        """Real syncopated bar change starting ON a downbeat — must not eat."""
        chords = [
            {"time": 0.0, "end": 2.45, "chord": "Gmaj7"},
            {"time": 2.45, "end": 2.85, "chord": "D"},        # starts ON downbeat
            {"time": 2.85, "end": 4.91, "chord": "Gmaj7"},
        ]
        downbeats = [0.0, 2.45, 4.91, 7.36]
        out, meta = filter_isolated_short_chords(chords, downbeats, self.BPM)
        # Path 1 would fire (same root G) but Gate A start_off_db fails
        self.assertEqual(meta["removed_count"], 0)
        self.assertEqual([c["chord"] for c in out], ["Gmaj7", "D", "Gmaj7"])

    def test_no_beat_data_passes_through(self):
        chords = [
            {"time": 0.0, "end": 1.0, "chord": "C"},
            {"time": 1.0, "end": 1.3, "chord": "D"},
            {"time": 1.3, "end": 2.5, "chord": "C"},
        ]
        out, meta = filter_isolated_short_chords(chords, [], self.BPM)
        self.assertFalse(meta["applied"])
        self.assertEqual(meta["reason"], "no_beat_data")
        self.assertEqual(len(out), 3)

        out, meta = filter_isolated_short_chords(chords, [0.0, 2.0], 0)
        self.assertFalse(meta["applied"])
        self.assertEqual(meta["reason"], "no_beat_data")

    def test_empty_input_safe(self):
        out, meta = filter_isolated_short_chords([], [0.0, 2.0], 100.0)
        self.assertEqual(out, [])
        self.assertFalse(meta["applied"])

        out, meta = filter_isolated_short_chords([{"time": 0, "end": 1, "chord": "C"}], [0.0], 100.0)
        # < 3 chords → no_beat_data
        self.assertFalse(meta["applied"])

    def test_global_arbiter_preserved(self):
        """global_arbiter splits are intentional downbeat-quantize artifacts."""
        chords = [
            {"time": 22.38, "end": 24.23, "chord": "Gmaj7"},
            {"time": 24.23, "end": 24.83, "chord": "D",
             "global_arbiter": "stable-downbeat-quantize"},
            {"time": 24.83, "end": 27.28, "chord": "Gmaj7"},
        ]
        out, meta = filter_isolated_short_chords(chords, self.GOLDEN_DOWNBEATS, self.BPM)
        self.assertEqual(meta["removed_count"], 0)
        self.assertEqual(len(out), 3)


if __name__ == "__main__":
    unittest.main()
