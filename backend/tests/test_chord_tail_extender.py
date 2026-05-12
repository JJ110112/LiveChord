import unittest

from backend.chord_tail_extender import maybe_extend_tail_for_serve


class TestChordTailExtender(unittest.TestCase):
    def test_repeats_clear_suffix_progression_to_last_beat(self):
        sheet = {
            "beats": [i * 0.5 for i in range(41)],
            "chords": [
                {"time": 0.0, "end": 2.0, "chord": "G"},
                {"time": 2.0, "end": 4.0, "chord": "D"},
                {"time": 4.0, "end": 6.0, "chord": "G"},
                {"time": 6.0, "end": 8.0, "chord": "D"},
            ],
        }

        maybe_extend_tail_for_serve(sheet)

        self.assertTrue(sheet["tail_fill_meta"]["applied"])
        self.assertEqual(sheet["tail_fill_meta"]["mode"], "repeat-2")
        self.assertEqual([c["chord"] for c in sheet["chords"][-2:]], ["G", "D"])
        self.assertAlmostEqual(sheet["chords"][-1]["end"], 20.0)
        self.assertTrue(all(c.get("tail_fill") for c in sheet["chords"][4:]))

    def test_extends_final_chord_when_no_suffix_pattern(self):
        sheet = {
            "beats": [0.0, 2.0, 4.0, 18.0],
            "chords": [
                {"time": 0.0, "end": 2.0, "chord": "C"},
                {"time": 2.0, "end": 4.0, "chord": "F"},
            ],
        }

        maybe_extend_tail_for_serve(sheet)

        self.assertEqual(sheet["tail_fill_meta"]["mode"], "extend-last")
        self.assertEqual(len(sheet["chords"]), 2)
        self.assertAlmostEqual(sheet["chords"][-1]["end"], 18.0)
        self.assertTrue(sheet["chords"][-1]["tail_fill"])

    def test_ignores_small_tail_gap(self):
        sheet = {
            "beats": [0.0, 2.0, 6.0],
            "chords": [{"time": 0.0, "end": 2.5, "chord": "C"}],
        }

        maybe_extend_tail_for_serve(sheet)

        self.assertNotIn("tail_fill_meta", sheet)
        self.assertAlmostEqual(sheet["chords"][-1]["end"], 2.5)


if __name__ == "__main__":
    unittest.main()
