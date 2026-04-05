import unittest
import os
import mido
import tempfile
from backend.ai.midi_sanitizer import MidiSanitizer

class TestMidiSanitizer(unittest.TestCase):
    def setUp(self):
        self.sanitizer = MidiSanitizer()
        
    def test_parse_chord_symbol(self):
        c_maj = self.sanitizer._parse_chord_symbol("C:maj")
        self.assertEqual(sorted(c_maj), [0, 4, 7])
        
        d_min = self.sanitizer._parse_chord_symbol("D:min")
        self.assertEqual(sorted(d_min), [2, 5, 9])
        
        db_maj = self.sanitizer._parse_chord_symbol("Db:maj")
        self.assertEqual(sorted(db_maj), [1, 5, 8]) # C#, F, G#
        
    def test_snap_pitch(self):
        # C major chord: C(0), E(4), G(7)
        c_maj_pcs = [0, 4, 7]
        
        # Test snapping C# (1) -> C (0) or D (2 -> E4)
        c_sharp = 61 # C#4
        snapped = self.sanitizer._snap_pitch(c_sharp, c_maj_pcs)
        self.assertEqual(snapped, 60) # C4
        
        # F# (6) -> snap to G (7)
        f_sharp = 66
        snapped = self.sanitizer._snap_pitch(f_sharp, c_maj_pcs)
        self.assertEqual(snapped, 67) # G4
        
        # E (4) -> No change
        e_nat = 64
        snapped = self.sanitizer._snap_pitch(e_nat, c_maj_pcs)
        self.assertEqual(snapped, 64)

    def test_get_chord_at_time(self):
        chords = [
            {"start_time": 0.0, "end_time": 2.0, "chord": "C:maj"},
            {"start_time": 2.0, "end_time": 4.0, "chord": "G:maj"}
        ]
        
        self.assertEqual(self.sanitizer._get_chord_at_time(1.0, chords), "C:maj")
        self.assertEqual(self.sanitizer._get_chord_at_time(3.0, chords), "G:maj")
        self.assertEqual(self.sanitizer._get_chord_at_time(5.0, chords), "N")

if __name__ == "__main__":
    unittest.main()
