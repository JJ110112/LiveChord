import unittest

from backend.global_chord_arbiter import analyze_global_structure, apply_global_structure_corrections


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

        apply_global_structure_corrections(sheet, meta)
        self.assertTrue(sheet["global_arbiter_meta"]["rewritten"])
        generated = [c for c in sheet["chords"] if c.get("global_arbiter") == "free-time-intro-cycle"]
        self.assertGreaterEqual(len(generated), 4)
        self.assertEqual([c["chord"] for c in generated[:4]], ["G", "Em", "Am", "D7"])
        self.assertTrue(all(c["display_beats"] == 4 for c in generated[:4]))
        self.assertGreater(generated[0]["end"] - generated[0]["time"], 2.5)
        modulated = [c for c in sheet["chords"] if c.get("global_arbiter") == "modulated-verse-cycle"]
        self.assertEqual([c["chord"] for c in modulated[:4]], ["Ab", "Fm", "Bbm", "Eb7"])
        self.assertTrue(all(c["display_beats"] == 4 for c in modulated[:4]))

        continuation = [c for c in sheet["chords"] if c.get("global_arbiter") == "free-time-cycle-continuation"]
        self.assertTrue(all(c["display_beats"] == 4 for c in continuation))

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

        apply_global_structure_corrections(sheet, meta)
        corrected = [c for c in sheet["chords"] if c.get("global_arbiter") == "two-beat-chorus-grammar"]
        self.assertEqual([c["display_beats"] for c in corrected], [2, 2, 2, 2, 4, 2, 2])
        self.assertEqual(corrected[4]["chord"], "A")
        self.assertGreater(sheet["display_bpm"], 75)
        self.assertLess(sheet["display_bpm"], 77)
        self.assertEqual(sheet["global_arbiter_meta"]["display_bpm"]["source"], "global-arbiter-display-beats")

    def test_protects_modulation_transition_and_repeated_ab_cycle(self):
        sheet = {
            "bpm": 76.0,
            "chords": [
                {"time": 0.0, "end": 53.5, "chord": "G"},
                {"time": 53.5, "end": 56.6, "chord": "Em"},
                {"time": 56.6, "end": 59.7, "chord": "Am"},
                {"time": 59.7, "end": 62.8, "chord": "D7"},
                {"time": 62.8, "end": 65.9, "chord": "G"},
                {"time": 75.5, "end": 77.1, "chord": "G"},
                {"time": 77.1, "end": 78.6, "chord": "B"},
                {"time": 78.6, "end": 80.2, "chord": "Em"},
                {"time": 80.2, "end": 81.8, "chord": "G"},
                {"time": 81.8, "end": 84.9, "chord": "A"},
                {"time": 84.9, "end": 86.4, "chord": "D"},
                {"time": 86.4, "end": 88.0, "chord": "D7"},
                {"time": 88.0, "end": 89.6, "chord": "G"},
                {"time": 89.6, "end": 91.1, "chord": "Bm"},
                {"time": 91.1, "end": 94.2, "chord": "Em"},
                {"time": 94.2, "end": 95.8, "chord": "D"},
                {"time": 95.8, "end": 97.3, "chord": "D7"},
                {"time": 97.3, "end": 98.9, "chord": "G"},
                {"time": 98.9, "end": 100.5, "chord": "Eb"},
                {"time": 100.5, "end": 103.6, "chord": "Ab"},
                {"time": 103.6, "end": 106.7, "chord": "Fm"},
                {"time": 106.7, "end": 109.8, "chord": "Bbm"},
                {"time": 109.8, "end": 112.9, "chord": "Eb7"},
                {"time": 112.9, "end": 116.0, "chord": "Ab"},
                {"time": 116.0, "end": 117.6, "chord": "Fm"},
                {"time": 117.6, "end": 119.1, "chord": "Fm7"},
                {"time": 119.1, "end": 120.7, "chord": "Bbm7"},
                {"time": 120.7, "end": 122.3, "chord": "Bb7"},
                {"time": 122.3, "end": 123.8, "chord": "Eb7"},
                {"time": 123.8, "end": 125.3, "chord": "Eb"},
                {"time": 125.3, "end": 126.9, "chord": "Ab"},
                {"time": 126.9, "end": 128.5, "chord": "C7"},
                {"time": 128.5, "end": 130.1, "chord": "Fm"},
                {"time": 130.1, "end": 131.5, "chord": "Ab"},
                {"time": 131.5, "end": 134.7, "chord": "Bb"},
                {"time": 134.7, "end": 137.8, "chord": "Eb"},
                {"time": 137.8, "end": 139.4, "chord": "Ab"},
                {"time": 139.4, "end": 140.9, "chord": "Cm"},
                {"time": 140.9, "end": 144.0, "chord": "Fm"},
                {"time": 144.0, "end": 145.6, "chord": "Eb"},
                {"time": 145.6, "end": 146.4, "chord": "Eb7"},
                {"time": 146.4, "end": 147.2, "chord": "Eb"},
                {"time": 147.2, "end": 148.7, "chord": "Ab"},
                {"time": 148.7, "end": 150.2, "chord": "Fm"},
            ],
        }

        meta = analyze_global_structure(sheet)
        apply_global_structure_corrections(sheet, meta)

        transition = [c for c in sheet["chords"] if c.get("global_arbiter") == "modulation-transition-grammar"]
        self.assertEqual([c["chord"] for c in transition], ["G", "Bm", "Em", "D", "D7", "G", "Eb"])
        self.assertEqual([c["display_beats"] for c in transition], [2, 2, 4, 2, 2, 2, 2])

        repeats = [c for c in sheet["chords"] if c.get("global_arbiter") == "modulated-verse-cycle-repeat"]
        self.assertEqual([c["chord"] for c in repeats], ["Ab", "Fm", "Bbm", "Eb7"])
        self.assertEqual([c["display_beats"] for c in repeats], [4, 4, 4, 4])
        self.assertAlmostEqual(repeats[1]["end"], 119.1)
        self.assertAlmostEqual(repeats[2]["end"], 122.3)
        self.assertAlmostEqual(repeats[3]["end"], 125.3)

        grid_cycle = [c for c in sheet["chords"] if c.get("global_arbiter") == "modulated-grid-cycle"]
        self.assertEqual([c["chord"] for c in grid_cycle], ["Ab", "Fm", "Bbm", "Eb7"])
        self.assertEqual([c["display_beats"] for c in grid_cycle], [4, 4, 4, 4])

        ending = [c for c in sheet["chords"] if c.get("global_arbiter") == "modulated-grid-ending"]
        self.assertEqual([c["chord"] for c in ending], ["Ab", "Fm", "Eb7", "Ab"])
        self.assertEqual([c["display_beats"] for c in ending], [4, 4, 4, 4])

    def test_repairs_minor_pop_loop_quality_and_passing_chords(self):
        sheet = {
            "bpm": 88.2,
            "chords": [
                {"time": 3.158, "end": 4.551, "chord": "Fm"},
                {"time": 4.551, "end": 5.224, "chord": "Eb"},
                {"time": 5.224, "end": 5.921, "chord": "Cm"},
                {"time": 5.921, "end": 7.291, "chord": "Db"},
                {"time": 7.291, "end": 8.661, "chord": "Ab"},
                {"time": 8.661, "end": 10.460, "chord": "Bbm7"},
                {"time": 10.460, "end": 11.424, "chord": "C7"},
                {"time": 11.424, "end": 15.581, "chord": "Fm"},
                {"time": 15.581, "end": 16.951, "chord": "Eb"},
                {"time": 16.951, "end": 18.344, "chord": "Db"},
                {"time": 18.344, "end": 19.017, "chord": "Cm"},
                {"time": 19.017, "end": 20.000, "chord": "Ab"},
                {"time": 20.000, "end": 21.084, "chord": "Bbm7"},
                {"time": 21.084, "end": 22.477, "chord": "C"},
                {"time": 22.477, "end": 26.610, "chord": "Fm"},
                {"time": 26.610, "end": 27.980, "chord": "Eb"},
                {"time": 27.980, "end": 29.720, "chord": "Db"},
                {"time": 29.720, "end": 30.743, "chord": "Ab"},
                {"time": 30.743, "end": 32.113, "chord": "Bbm"},
                {"time": 32.113, "end": 33.506, "chord": "C"},
                {"time": 33.506, "end": 37.640, "chord": "Fm"},
            ],
        }

        meta = analyze_global_structure(sheet)
        self.assertTrue(meta["minor_pop_loop_candidates"], meta)
        apply_global_structure_corrections(sheet, meta)

        names = [c["chord"] for c in sheet["chords"]]
        self.assertNotIn("Cm", names[:14])
        self.assertEqual(names[2], "Db")
        self.assertEqual(names[9], "Ab")
        self.assertEqual(names[10], "Bbm7")
        self.assertEqual(names[11], "C7")
        corrected = [c for c in sheet["chords"] if c.get("global_arbiter") == "minor-pop-loop-grammar"]
        self.assertTrue(corrected)
        self.assertTrue(all(c["display_beats"] == 2 for c in corrected if c["chord"] != "Fm"))
        long_tonics = [c for c in sheet["chords"] if c["chord"] == "Fm" and c.get("end", 0) - c.get("time", 0) > 2.4]
        self.assertTrue(long_tonics)
        self.assertTrue(any(c.get("split_display_beats") == [4, 2] for c in long_tonics))

    def test_quantizes_display_beats_from_stable_downbeats(self):
        sheet = {
            "bpm": 93.8,
            "downbeats": [0.28, 2.82, 5.34, 7.88, 10.4, 12.96, 15.48, 18.0, 20.5, 23.02],
            "chords": [
                {"time": 10.0, "end": 12.87, "chord": "Eb"},
                {"time": 12.87, "end": 15.28, "chord": "Bb"},
                {"time": 15.28, "end": 18.61, "chord": "Ab"},
                {"time": 18.61, "end": 19.72, "chord": "Bb"},
                {"time": 19.72, "end": 23.06, "chord": "Eb"},
            ],
        }

        apply_global_structure_corrections(sheet)

        self.assertEqual([c.get("display_beats") for c in sheet["chords"]], [4, 4, 4, 1, 2, 4, 1])
        self.assertEqual(
            sheet["global_arbiter_meta"]["corrections"][-1]["type"],
            "stable_downbeat_display_quantize",
        )

    def test_preserves_explicit_meter_display_bpm(self):
        sheet = {
            "bpm": 47.62,
            "display_bpm": 47.62,
            "time_signature": "6/8",
            "beats_per_bar": 2,
            "meter_correction": {"applied": True, "reason": "manual-6-8"},
            "downbeats": [1.1, 3.74, 6.32, 8.9, 11.58, 14.16, 16.74, 19.34, 21.94, 24.46],
            "chords": [
                {"time": 1.1, "end": 3.66, "chord": "Am"},
                {"time": 3.66, "end": 6.2, "chord": "G"},
                {"time": 6.2, "end": 8.72, "chord": "Am"},
                {"time": 8.72, "end": 11.5, "chord": "E"},
                {"time": 11.5, "end": 13.0, "chord": "Am"},
                {"time": 13.0, "end": 16.62, "chord": "G"},
                {"time": 16.62, "end": 19.4, "chord": "Am"},
                {"time": 19.4, "end": 22.0, "chord": "Am"},
            ],
        }

        apply_global_structure_corrections(sheet)

        self.assertEqual(sheet["display_bpm"], 47.62)
        self.assertEqual(
            sheet["global_arbiter_meta"]["display_quantization_skipped"]["reason"],
            "explicit-meter-card-grid",
        )
        self.assertEqual([c["time"] for c in sheet["chords"][-2:]], [16.62, 19.4])

    def test_high_bpm_acoustic_half_bar_downbeats_are_joined(self):
        sheet = {
            "bpm": 93.75,
            "downbeats": [1.98, 3.90, 5.78, 7.68, 9.58, 11.48, 13.38, 15.28, 17.18, 19.08],
            "chords": [
                {"time": 0.37, "end": 7.36, "chord": "Ab"},
                {"time": 7.36, "end": 9.89, "chord": "Fm7"},
                {"time": 9.89, "end": 11.19, "chord": "Fm"},
                {"time": 11.19, "end": 14.98, "chord": "Db"},
                {"time": 64.20, "end": 66.15, "chord": "Fm"},
                {"time": 66.15, "end": 68.03, "chord": "Db"},
                {"time": 68.03, "end": 69.92, "chord": "Ab"},
            ],
        }

        apply_global_structure_corrections(sheet)

        self.assertEqual([c["chord"] for c in sheet["chords"][:4]], ["Ab", "Ab", "Fm7", "Db"])
        self.assertEqual([c.get("display_beats") for c in sheet["chords"][:4]], [4, 4, 4, 4])
        self.assertEqual([c.get("display_beats") for c in sheet["chords"][4:]], [2, 2, 2])
        corr = sheet["global_arbiter_meta"]["corrections"][-1]
        self.assertEqual(corr["gap_factor"], 2)
        self.assertEqual(corr["merged_cards"], 1)
        self.assertEqual(corr["split_long_holds"], 1)

    def test_compound_12_8_ballad_uses_dotted_quarter_display_bpm(self):
        downbeats = [
            0.24, 2.92, 5.58, 8.24, 10.90, 12.24, 13.56, 14.90,
            16.22, 18.90, 20.24, 21.56, 22.90, 24.24, 26.24,
            28.24, 30.54, 32.24, 32.56, 34.24, 36.22, 38.22,
            40.24, 42.24, 44.22, 46.20, 48.24, 50.22, 50.56,
            52.24, 54.26, 56.26, 58.24, 60.24, 62.24, 64.26,
            66.24, 68.22, 70.22, 72.22,
        ]
        sheet = {
            "bpm": 176.5,
            "downbeats": downbeats,
            "chords": [
                {"time": 0.28, "end": 8.27, "chord": "D"},
                {"time": 8.27, "end": 10.00, "chord": "Bm"},
                {"time": 10.00, "end": 12.14, "chord": "Bm7"},
                {"time": 12.14, "end": 15.93, "chord": "G"},
                {"time": 15.93, "end": 18.06, "chord": "D"},
                {"time": 18.06, "end": 20.25, "chord": "A"},
                {"time": 20.25, "end": 24.22, "chord": "D"},
                {"time": 24.22, "end": 28.28, "chord": "Bm7"},
                {"time": 28.28, "end": 30.00, "chord": "Gsus2"},
                {"time": 30.00, "end": 33.98, "chord": "D"},
                {"time": 33.98, "end": 36.29, "chord": "A"},
                {"time": 36.29, "end": 39.59, "chord": "D"},
            ],
        }

        apply_global_structure_corrections(sheet)

        corr = sheet["global_arbiter_meta"]["corrections"][-1]
        self.assertEqual(corr["type"], "compound_12_8_display_quantize")
        self.assertAlmostEqual(corr["bar_gap"], 3.96, places=1)
        self.assertEqual([c["chord"] for c in sheet["chords"][:4]], ["D", "D", "Bm", "G"])
        self.assertEqual([c.get("display_beats") for c in sheet["chords"][:4]], [4, 4, 4, 4])
        self.assertGreater(sheet["display_bpm"], 58)
        self.assertLess(sheet["display_bpm"], 62)

    def test_stable_one_and_half_bar_hold_splits_as_four_plus_two(self):
        sheet = {
            "bpm": 130.4,
            "downbeats": [0.24, 2.08, 3.92, 5.76, 7.60, 9.44, 11.28, 13.12, 14.96, 16.80],
            "chords": [
                {"time": 0.24, "end": 2.08, "chord": "G"},
                {"time": 2.08, "end": 3.92, "chord": "C"},
                {"time": 3.92, "end": 6.68, "chord": "Am"},
                {"time": 6.68, "end": 8.52, "chord": "G"},
            ],
        }

        apply_global_structure_corrections(sheet)

        self.assertEqual([c["chord"] for c in sheet["chords"]], ["G", "C", "Am", "Am", "G"])
        self.assertEqual([c.get("display_beats") for c in sheet["chords"]], [4, 4, 4, 2, 4])
        self.assertEqual(
            sheet["chords"][2].get("display_arbiter"),
            "stable-downbeat-bar-plus-half",
        )

    def test_stable_five_beat_hold_splits_as_four_plus_tail(self):
        sheet = {
            "bpm": 95.0,
            "downbeats": [0.0, 2.53, 5.06, 7.59, 10.12, 12.65, 15.18, 17.71, 20.24],
            "chords": [
                {"time": 0.0, "end": 2.53, "chord": "Bb"},
                {"time": 2.53, "end": 5.72, "chord": "Cm7"},
                {"time": 5.72, "end": 8.25, "chord": "Ab"},
            ],
        }

        apply_global_structure_corrections(sheet)

        self.assertEqual([c["chord"] for c in sheet["chords"]], ["Bb", "Cm7", "Cm7", "Ab"])
        self.assertEqual([c.get("display_beats") for c in sheet["chords"]], [4, 4, 1, 4])
        self.assertEqual(sheet["chords"][1].get("display_arbiter"), "stable-downbeat-bar-plus-half")

    def test_fast_rock_near_bar_hold_stays_single_card(self):
        sheet = {
            "bpm": 187.5,
            "downbeats": [0.0, 1.32, 2.64, 3.96, 5.28, 6.60, 7.92, 9.24, 10.56, 11.88],
            "chords": [
                {"time": 0.0, "end": 1.32, "chord": "Bb"},
                {"time": 1.32, "end": 2.64, "chord": "F"},
                {"time": 2.64, "end": 4.26, "chord": "C"},
                {"time": 4.25, "end": 5.57, "chord": "Bb"},
            ],
        }

        apply_global_structure_corrections(sheet)

        self.assertEqual([c["chord"] for c in sheet["chords"]], ["Bb", "F", "C", "Bb"])
        self.assertEqual([c.get("display_beats") for c in sheet["chords"]], [4, 4, 4, 4])
        self.assertEqual(sheet["chords"][2].get("display_arbiter"), "stable-downbeat-near-bar-hold")

    def test_repeated_slow_near_bar_holds_are_quantized_back_to_four(self):
        sheet = {
            "bpm": 107.7,
            "downbeats": [0.0, 2.22, 4.44, 6.66, 8.88, 11.10, 13.32, 15.54, 17.76, 19.98, 22.20],
            "chords": [
                {"time": 0.00, "end": 2.22, "chord": "D"},
                {"time": 2.22, "end": 5.00, "chord": "A"},
                {"time": 5.00, "end": 7.78, "chord": "D"},
                {"time": 7.78, "end": 10.56, "chord": "Dbm7"},
                {"time": 10.56, "end": 13.34, "chord": "A"},
                {"time": 13.34, "end": 15.56, "chord": "E"},
            ],
        }

        apply_global_structure_corrections(sheet)

        self.assertEqual(
            [c["chord"] for c in sheet["chords"]],
            ["D", "A", "D", "Dbm7", "A", "E"],
        )
        self.assertEqual([c.get("display_beats") for c in sheet["chords"]], [4, 4, 4, 4, 4, 4])
        self.assertTrue(
            all(
                c.get("display_arbiter") == "stable-downbeat-near-bar-hold"
                for c in sheet["chords"][1:5]
            )
        )

    def test_fast_rock_display_bpm_can_stay_above_180(self):
        sheet = {
            "bpm": 187.5,
            "downbeats": [0.0, 1.32, 2.64, 3.96, 5.28, 6.60, 7.92, 9.24, 10.56, 11.88],
            "chords": [
                {"time": 0.0, "end": 1.385, "chord": "Bbm"},
                {"time": 1.385, "end": 2.554, "chord": "F"},
                {"time": 2.554, "end": 3.889, "chord": "Bbm"},
                {"time": 3.889, "end": 5.224, "chord": "F"},
                {"time": 5.224, "end": 6.536, "chord": "Bbm"},
                {"time": 6.536, "end": 7.848, "chord": "Bbm"},
                {"time": 7.848, "end": 9.183, "chord": "F"},
                {"time": 9.183, "end": 10.518, "chord": "F"},
            ],
        }

        apply_global_structure_corrections(sheet)

        self.assertGreater(sheet["display_bpm"], 179.5)
        self.assertLess(sheet["display_bpm"], 190)

    def test_stable_quantize_does_not_force_three_beat_intro_cards_to_four(self):
        sheet = {
            "bpm": 130.4,
            "downbeats": [0.70, 2.56, 4.40, 6.24, 8.08, 9.94, 11.78, 13.62, 15.48, 17.32],
            "chords": [
                {"time": 1.207, "end": 3.042, "chord": "G"},
                {"time": 3.042, "end": 4.876, "chord": "C"},
                {"time": 9.520, "end": 10.403, "chord": "C"},
                {"time": 10.403, "end": 11.819, "chord": "D"},
                {"time": 11.819, "end": 13.189, "chord": "Gm"},
            ],
        }

        apply_global_structure_corrections(sheet)

        by_chord = {c["chord"]: c.get("display_beats") for c in sheet["chords"]}
        self.assertEqual(by_chord["G"], 4)
        self.assertEqual(by_chord["C"], 4)
        self.assertIsNone(by_chord["D"])
        self.assertIsNone(by_chord["Gm"])


if __name__ == "__main__":
    unittest.main()
