import unittest

from backend.ai.note_continuity import repair_note_continuity


class TestNoteContinuity(unittest.TestCase):
    def test_extends_small_same_lane_gap_without_mutating_input(self):
        events = [
            {"time": 0.0, "duration": 0.45, "pitch": 64, "voice_lane": "rh_melody"},
            {"time": 0.5, "duration": 0.5, "pitch": 65, "voice_lane": "rh_melody"},
        ]

        repaired = repair_note_continuity(events, bpm=120)

        self.assertAlmostEqual(events[0]["duration"], 0.45)
        self.assertAlmostEqual(repaired[0]["duration"], 0.5)
        self.assertEqual(repaired[0]["continuity_meta"]["reason"], "small_gap_same_voice")
        self.assertAlmostEqual(repaired[0]["continuity_meta"]["extended_by"], 0.05)

    def test_keeps_large_gap_as_rest(self):
        events = [
            {"time": 0.0, "duration": 0.2, "pitch": 60, "voice_lane": "rh_melody"},
            {"time": 1.0, "duration": 0.5, "pitch": 62, "voice_lane": "rh_melody"},
        ]

        repaired = repair_note_continuity(events, bpm=120)

        self.assertAlmostEqual(repaired[0]["duration"], 0.2)
        self.assertNotIn("continuity_meta", repaired[0])

    def test_uses_tempo_curve_for_gap_threshold(self):
        events = [
            {"time": 0.0, "duration": 0.2, "pitch": 60, "voice_lane": "lh_bass"},
            {"time": 0.6, "duration": 0.5, "pitch": 55, "voice_lane": "lh_bass"},
        ]

        without_curve = repair_note_continuity(events, bpm=120)
        with_curve = repair_note_continuity(
            events,
            bpm=120,
            tempo_curve=[{"t": 0.0, "bpm": 60.0}, {"t": 4.0, "bpm": 60.0}],
        )

        self.assertAlmostEqual(without_curve[0]["duration"], 0.2)
        self.assertAlmostEqual(with_curve[0]["duration"], 0.6)

    def test_uses_event_onset_for_rubato_tempo_curve(self):
        events = [
            {"time": 0.0, "duration": 0.2, "pitch": 60, "voice_lane": "lh_bass"},
            {"time": 0.6, "duration": 0.5, "pitch": 55, "voice_lane": "lh_bass"},
            {"time": 10.0, "duration": 0.2, "pitch": 57, "voice_lane": "lh_bass"},
            {"time": 10.6, "duration": 0.5, "pitch": 53, "voice_lane": "lh_bass"},
        ]

        repaired = repair_note_continuity(
            events,
            bpm=120,
            tempo_curve=[{"t": 0.0, "bpm": 120.0}, {"t": 10.0, "bpm": 60.0}],
        )

        self.assertAlmostEqual(repaired[0]["duration"], 0.2)
        self.assertAlmostEqual(repaired[2]["duration"], 0.6)

    def test_zero_bpm_uses_safe_fallback(self):
        events = [
            {"time": 0.0, "duration": 0.45, "pitch": 60, "voice_lane": "lh_bass"},
            {"time": 0.5, "duration": 0.5, "pitch": 55, "voice_lane": "lh_bass"},
        ]

        repaired = repair_note_continuity(events, bpm=0)

        self.assertAlmostEqual(repaired[0]["duration"], 0.5)

    def test_isolates_voice_lanes(self):
        events = [
            {"time": 0.0, "duration": 0.4, "pitch": 48, "voice_lane": "lh_bass"},
            {"time": 0.45, "duration": 0.2, "pitch": 72, "voice_lane": "rh_melody"},
            {"time": 0.5, "duration": 0.4, "pitch": 50, "voice_lane": "lh_bass"},
        ]

        repaired = repair_note_continuity(events, bpm=120)

        self.assertAlmostEqual(repaired[0]["duration"], 0.5)
        self.assertAlmostEqual(repaired[1]["duration"], 0.2)
        self.assertNotIn("continuity_meta", repaired[1])

    def test_block_chord_group_extends_as_one_unit(self):
        events = [
            {"time": 0.0, "duration": 0.3, "pitch": 60, "voice_lane": "rh_accompaniment"},
            {"time": 0.0, "duration": 0.3, "pitch": 64, "voice_lane": "rh_accompaniment"},
            {"time": 0.5, "duration": 0.5, "pitch": 67, "voice_lane": "rh_accompaniment"},
        ]

        repaired = repair_note_continuity(events, bpm=120)

        self.assertAlmostEqual(repaired[0]["duration"], 0.5)
        self.assertAlmostEqual(repaired[1]["duration"], 0.5)
        self.assertEqual(repaired[0]["continuity_meta"]["reason"], "small_gap_chord_group")

    def test_humanized_onset_jitter_still_groups_chord_notes(self):
        events = [
            {"time": 0.0, "duration": 0.28, "pitch": 60, "voice_lane": "rh_accompaniment"},
            {"time": 0.01, "duration": 0.27, "pitch": 64, "voice_lane": "rh_accompaniment"},
            {"time": 0.5, "duration": 0.5, "pitch": 67, "voice_lane": "rh_accompaniment"},
        ]

        repaired = repair_note_continuity(events, bpm=120)

        self.assertAlmostEqual(repaired[0]["time"] + repaired[0]["duration"], 0.5)
        self.assertAlmostEqual(repaired[1]["time"] + repaired[1]["duration"], 0.5)

    def test_accompaniment_stops_at_chord_boundary(self):
        events = [
            {"time": 0.0, "duration": 0.9, "pitch": 48, "voice_lane": "lh_bass"},
            {"time": 1.2, "duration": 0.5, "pitch": 50, "voice_lane": "lh_bass"},
        ]

        repaired = repair_note_continuity(
            events,
            bpm=60,
            role="accompaniment",
            chord_boundaries=[1.0],
        )

        self.assertAlmostEqual(repaired[0]["duration"], 1.0)
        self.assertAlmostEqual(repaired[0]["continuity_meta"]["extended_to"], 1.0)

    def test_melody_may_cross_chord_boundary(self):
        events = [
            {"time": 0.0, "duration": 0.9, "pitch": 64, "voice_lane": "rh_melody"},
            {"time": 1.2, "duration": 0.5, "pitch": 65, "voice_lane": "rh_melody"},
        ]

        repaired = repair_note_continuity(
            events,
            bpm=60,
            role="melody",
            chord_boundaries=[1.0],
        )

        self.assertAlmostEqual(repaired[0]["duration"], 1.2)

    def test_melody_large_phrase_silence_is_not_filled(self):
        events = [
            {"time": 0.0, "duration": 0.5, "pitch": 64, "voice_lane": "rh_melody"},
            {"time": 2.5, "duration": 0.5, "pitch": 67, "voice_lane": "rh_melody"},
        ]

        repaired = repair_note_continuity(events, bpm=120, role="melody")

        self.assertAlmostEqual(repaired[0]["duration"], 0.5)
        self.assertNotIn("continuity_meta", repaired[0])

    def test_melody_note_name_with_midi_field_does_not_crash(self):
        events = [
            {"time": 0.0, "duration": 0.45, "note": "C4", "midi": 60, "voice_lane": "rh_melody"},
            {"time": 0.5, "duration": 0.5, "note": "D4", "midi": 62, "voice_lane": "rh_melody"},
        ]

        repaired = repair_note_continuity(events, bpm=120, role="melody")

        self.assertAlmostEqual(repaired[0]["duration"], 0.5)

    def test_dry_run_records_would_extend_without_changing_duration(self):
        events = [
            {"time": 0.0, "duration": 0.45, "pitch": 64, "voice_lane": "rh_melody"},
            {"time": 0.5, "duration": 0.5, "pitch": 65, "voice_lane": "rh_melody"},
        ]

        repaired = repair_note_continuity(events, bpm=120, dry_run=True)

        self.assertAlmostEqual(repaired[0]["duration"], 0.45)
        self.assertAlmostEqual(repaired[0]["continuity_meta"]["would_duration"], 0.5)
        self.assertTrue(repaired[0]["continuity_meta"]["dry_run"])

    def test_preserves_gate_ratio_while_extending_canonical_duration(self):
        events = [
            {
                "time": 0.0,
                "duration": 0.45,
                "pitch": 64,
                "voice_lane": "rh_melody",
                "gate_ratio": 0.45,
                "articulation": "staccato",
            },
            {"time": 0.5, "duration": 0.5, "pitch": 65, "voice_lane": "rh_melody"},
        ]

        repaired = repair_note_continuity(events, bpm=120)

        self.assertAlmostEqual(repaired[0]["duration"], 0.5)
        self.assertAlmostEqual(repaired[0]["gate_ratio"], 0.45)
        self.assertEqual(repaired[0]["articulation"], "staccato")

    def test_meter_thresholds_cover_waltz_compound_and_unknown_meter(self):
        def first_duration(time_signature):
            repaired = repair_note_continuity(
                [
                    {"time": 0.0, "duration": 0.6, "pitch": 60, "voice_lane": "rh_accompaniment"},
                    {"time": 1.0, "duration": 0.5, "pitch": 64, "voice_lane": "rh_accompaniment"},
                ],
                bpm=60,
                time_signature=time_signature,
            )
            return repaired[0]["duration"]

        self.assertAlmostEqual(first_duration("3/4"), 1.0)
        self.assertAlmostEqual(first_duration("6/8"), 1.0)
        self.assertAlmostEqual(first_duration("12/8"), 1.0)
        self.assertAlmostEqual(first_duration("unknown"), 0.6)

    def test_strum_group_aligns_end_and_stops_at_next_strum(self):
        events = [
            {"time": 0.0, "duration": 0.15, "pitch": 52, "voice_lane": "string_strum", "strum_id": "a"},
            {"time": 0.03, "duration": 0.15, "pitch": 55, "voice_lane": "string_strum", "strum_id": "a"},
            {"time": 0.06, "duration": 0.15, "pitch": 59, "voice_lane": "string_strum", "strum_id": "a"},
            {"time": 0.4, "duration": 0.2, "pitch": 54, "voice_lane": "string_strum", "strum_id": "b"},
            {"time": 0.43, "duration": 0.2, "pitch": 57, "voice_lane": "string_strum", "strum_id": "b"},
        ]

        repaired = repair_note_continuity(events, bpm=120)
        first_group_ends = [
            round(repaired[idx]["time"] + repaired[idx]["duration"], 6)
            for idx in range(3)
        ]

        self.assertEqual(first_group_ends, [0.4, 0.4, 0.4])
        self.assertEqual(repaired[0]["continuity_meta"]["reason"], "small_gap_strum_group")
        self.assertAlmostEqual(repaired[3]["duration"], 0.2)


if __name__ == "__main__":
    unittest.main()
