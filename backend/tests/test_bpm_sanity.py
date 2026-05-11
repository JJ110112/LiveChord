import unittest

from backend.bpm_sanity import (
    maybe_apply_structural_bpm_correction_for_serve,
    structural_doubletime_check,
)


class TestStructuralDoubletime(unittest.TestCase):
    def test_halves_high_bpm_with_six_tick_downbeats(self):
        sheet = {
            "bpm": 230.8,
            "beats": [i * 0.26 for i in range(32)],
            "downbeats": [i * 1.56 for i in range(8)],
            "tempo_curve": [{"t": 0.0, "bpm": 230.8}, {"t": 10.0, "bpm": 240.0}],
        }

        corrected, meta = structural_doubletime_check(sheet)

        self.assertTrue(meta["applied"], meta)
        self.assertAlmostEqual(corrected, 115.4)
        maybe_apply_structural_bpm_correction_for_serve(sheet)
        self.assertAlmostEqual(sheet["bpm"], 115.4)
        self.assertEqual(len(sheet["beats"]), 16)
        self.assertEqual(len(sheet["downbeats"]), 8)
        self.assertEqual(sheet["tempo_curve"][0]["bpm"], 115.4)
        self.assertEqual(sheet["bpm_correction"]["reason"], "structural-doubletime-bpb6")

    def test_leaves_normal_fast_song_alone(self):
        sheet = {
            "bpm": 180.0,
            "beats": [i * (60.0 / 180.0) for i in range(32)],
            "downbeats": [i * (4 * 60.0 / 180.0) for i in range(8)],
        }

        corrected, meta = structural_doubletime_check(sheet)

        self.assertFalse(meta["applied"])
        self.assertEqual(corrected, 180.0)


if __name__ == "__main__":
    unittest.main()
