import unittest

from backend.global_chord_arbiter import analyze_global_structure


class TestGlobalChordArbiter(unittest.TestCase):
    def test_detects_free_time_intro_cycle_and_modulation(self):
        sheet = {
            "bpm": 76.0,
            "beats": [0.0, 1.4, 3.2, 8.0, 17.0] + [55.0 + i * 0.79 for i in range(80)],
            "downbeats": [0.0, 6.0, 18.0, 36.0] + [55.0 + i * 3.16 for i in range(20)],
            "chords": [
                {"time": 0.0, "end": 53.0, "chord": "G"},
                {"time": 53.0, "end": 56.0, "chord": "Em"},
                {"time": 56.0, "end": 59.0, "chord": "Am"},
                {"time": 59.0, "end": 62.0, "chord": "D7"},
                {"time": 62.0, "end": 65.0, "chord": "G"},
                {"time": 100.0, "end": 103.0, "chord": "Ab"},
                {"time": 103.0, "end": 106.0, "chord": "Fm"},
                {"time": 106.0, "end": 109.0, "chord": "Bbm"},
                {"time": 109.0, "end": 112.0, "chord": "Eb7"},
            ],
        }

        meta = analyze_global_structure(sheet)

        self.assertTrue(meta["applied"], meta)
        self.assertTrue(meta["intro_beat_confidence"]["low_confidence"])
        self.assertEqual(meta["hints"][0]["type"], "free_time_long_intro")
        self.assertEqual(meta["hints"][0]["suggested_cycle"], ["G", "Em", "Am", "D7"])
        self.assertEqual(meta["hints"][0]["suggested_key"], "G")
        self.assertEqual(meta["modulated_cycle_candidates"][0]["to_cycle"], ["Ab", "Fm", "Bbm", "Eb7"])
        self.assertEqual(meta["modulated_cycle_candidates"][0]["from_key"], "G")
        self.assertEqual(meta["modulated_cycle_candidates"][0]["to_key"], "Ab")
        self.assertEqual(meta["modulated_cycle_candidates"][0]["shift_semitones"], 1)

    def test_detects_two_beat_chorus_grammar(self):
        sheet = {
            "bpm": 76.0,
            "chords": [
                {"time": 75.0, "end": 76.58, "chord": "G"},
                {"time": 76.58, "end": 78.16, "chord": "B"},
                {"time": 78.16, "end": 79.74, "chord": "Em"},
                {"time": 79.74, "end": 81.32, "chord": "G"},
                {"time": 81.32, "end": 84.48, "chord": "A"},
                {"time": 84.48, "end": 86.06, "chord": "D"},
                {"time": 86.06, "end": 87.64, "chord": "D7"},
            ],
        }

        meta = analyze_global_structure(sheet)

        self.assertTrue(meta["two_beat_grammar_candidates"], meta)
        cand = meta["two_beat_grammar_candidates"][0]
        self.assertEqual(cand["suggested_card_beats"], [2, 2, 2, 2, 4, 2, 2])
        self.assertEqual(cand["chords"], ["G", "B", "Em", "G", "A", "D", "D7"])
        self.assertEqual(cand["local_key"]["key"], "G")
        self.assertEqual(cand["degrees"], ["I", "III", "VIm", "I", "II", "V", "V7"])


if __name__ == "__main__":
    unittest.main()
