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

    def test_correct_phase_accepts_large_relative_fragment_improvement(self):
        bpm = 120.0
        beats = [i * 0.5 for i in range(65)]
        chords = []
        for i in range(12):
            start = i * 2.0
            # A handful of real one-beat pickups remain even on the best grid.
            # The corrector should still accept the phase when it removes the
            # much larger wrong-grid fragment pattern.
            end = start + (2.5 if i in {1, 3, 5, 7, 9} else 2.0)
            chords.append({"time": start, "end": end, "chord": "C" if i % 2 == 0 else "F"})
        data = {
            "chords": chords,
            "beats": beats,
            "downbeats": [0.5 + i * 2.0 for i in range(12)],
            "bpm": bpm,
        }

        res = correct_phase(data)

        self.assertTrue(res["applied"], res["reason"])
        self.assertIn("relative-fragment-fix", res["reason"])
        self.assertEqual(res["phase_after"], 0)
        self.assertGreater(res["bad_fragments_before"], res["bad_fragments"])

    def test_preserves_stable_current_four_four_against_three_beat_candidate(self):
        bpm = 130.4
        beats = [round(0.24 + i * 0.46, 3) for i in range(192)]
        downbeats = [round(0.24 + i * 1.84, 3) for i in range(48)]
        chords = [
            {"time": 1.207, "end": 3.042, "chord": "G"},
            {"time": 3.042, "end": 4.876, "chord": "C"},
            {"time": 4.876, "end": 7.639, "chord": "Am"},
            {"time": 7.639, "end": 9.520, "chord": "G"},
            {"time": 9.520, "end": 10.400, "chord": "C"},
            {"time": 10.400, "end": 11.817, "chord": "D"},
            {"time": 11.817, "end": 13.189, "chord": "Gm"},
            {"time": 13.189, "end": 15.511, "chord": "Cm"},
            {"time": 15.511, "end": 17.345, "chord": "G"},
            {"time": 17.345, "end": 20.571, "chord": "Cm"},
            {"time": 20.571, "end": 21.965, "chord": "Gm"},
            {"time": 21.965, "end": 22.894, "chord": "Dm"},
            {"time": 22.894, "end": 32.109, "chord": "Gm"},
        ]
        data = {"chords": chords, "beats": beats, "downbeats": downbeats, "bpm": bpm}

        res = correct_phase(data)

        self.assertFalse(res["applied"])
        self.assertIn("preserve-stable-4/4", res["reason"])
        self.assertEqual(res["bpb_after"], 3)


if __name__ == "__main__":
    unittest.main()
