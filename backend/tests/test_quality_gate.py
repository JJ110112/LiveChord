import unittest

from scripts.quality_gate import _visible_fragment_risk, _display_dot_count


class TestQualityGate(unittest.TestCase):
    def test_same_chord_full_bar_plus_tail_is_not_fragment(self):
        bpm = 120.0
        spb = 60.0 / bpm
        chords = [
            {"time": 0.0, "end": 4 * spb, "chord": "Bm", "display_beats": 4},
            {"time": 4 * spb, "end": 5 * spb, "chord": "Bm", "display_beats": 1},
        ]

        risk = _visible_fragment_risk(chords, bpm, 4)

        self.assertEqual(risk["bad_fragments"], 0)

    def test_same_chord_one_plus_three_remains_fragment(self):
        bpm = 120.0
        spb = 60.0 / bpm
        chords = [
            {"time": 0.0, "end": 1 * spb, "chord": "Bbmaj7", "display_beats": 1},
            {"time": 1 * spb, "end": 4 * spb, "chord": "Bbmaj7", "display_beats": 3},
        ]

        risk = _visible_fragment_risk(chords, bpm, 4)

        self.assertEqual(risk["bad_fragments"], 1)
        self.assertEqual(risk["patterns"], {"same-chord-1+3": 1})

    def test_long_duration_with_four_display_dots_is_readable(self):
        bpm = 120.0
        chord = {"time": 0.0, "end": 12.0, "chord": "Bm", "display_beats": 4}

        self.assertEqual(_display_dot_count(chord, bpm, 4), 4)


if __name__ == "__main__":
    unittest.main()
