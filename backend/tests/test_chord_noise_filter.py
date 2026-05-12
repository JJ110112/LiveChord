import unittest

from backend.chord_noise_filter import filter_noise_tails


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


if __name__ == "__main__":
    unittest.main()
