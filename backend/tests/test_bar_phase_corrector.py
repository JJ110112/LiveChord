import unittest

from backend.bar_phase_corrector import (
    correct_phase,
    fragmentation_risk,
    search_best_phase,
)


class TestFragmentationRisk(unittest.TestCase):
    def test_shifted_4_4_grid_flags_one_three_fragments(self):
        chords = [
            {"time": 0.0, "end": 2.0, "chord": "C"},
            {"time": 2.0, "end": 4.0, "chord": "F"},
            {"time": 4.0, "end": 6.0, "chord": "G"},
        ]
        shifted_downbeats = [0.5, 2.5, 4.5]
        risk = fragmentation_risk(chords, shifted_downbeats, bpm=120.0, bpb=4)
        self.assertGreaterEqual(risk["bad_fragments"], 3)
        self.assertIn("1+3", risk["patterns"])
        self.assertGreaterEqual(risk["penalty"], 0.18)

    def test_clean_4_4_grid_has_no_fragment_penalty(self):
        chords = [
            {"time": 0.0, "end": 2.0, "chord": "C"},
            {"time": 2.0, "end": 4.0, "chord": "F"},
            {"time": 4.0, "end": 6.0, "chord": "G"},
        ]
        clean_downbeats = [0.0, 2.0, 4.0, 6.0]
        risk = fragmentation_risk(chords, clean_downbeats, bpm=120.0, bpb=4)
        self.assertEqual(risk["bad_fragments"], 0)
        self.assertEqual(risk["penalty"], 0.0)


class TestPhaseCorrectorFragmentGuard(unittest.TestCase):
    def test_search_prefers_clean_phase_over_fragmented_phase(self):
        bpm = 120.0
        beats = [i * 0.5 for i in range(17)]
        chords = [
            {"time": 0.0, "end": 2.0, "chord": "C"},
            {"time": 2.0, "end": 4.0, "chord": "F"},
            {"time": 4.0, "end": 6.0, "chord": "G"},
            {"time": 6.0, "end": 8.0, "chord": "C"},
        ]
        chord_changes = [c["time"] for c in chords[1:]]
        bpb, phase, align, frag = search_best_phase(chord_changes, beats, bpm, chords)
        self.assertEqual(bpb, 4)
        self.assertEqual(phase, 0)
        self.assertGreater(align, 0.5)
        self.assertEqual(frag["bad_fragments"], 0)

    def test_correct_phase_replaces_clean_but_shifted_downbeats(self):
        bpm = 120.0
        beats = [i * 0.5 for i in range(49)]
        chords = [
            {"time": i * 2.0, "end": (i + 1) * 2.0, "chord": "C" if i % 2 == 0 else "F"}
            for i in range(12)
        ]
        data = {
            "chords": chords,
            "beats": beats,
            "downbeats": [0.5 + i * 2.0 for i in range(12)],
            "bpm": bpm,
        }
        res = correct_phase(data)
        self.assertTrue(res["applied"], res["reason"])
        self.assertEqual(res["bpb_after"], 4)
        self.assertEqual(res["phase_after"], 0)
        self.assertEqual(res["bad_fragments"], 0)


if __name__ == "__main__":
    unittest.main()
