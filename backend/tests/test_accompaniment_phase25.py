import os
import unittest
from unittest.mock import patch

from backend.ai import accompaniment_generator as acc


class TestAccompanimentPhase25(unittest.TestCase):
    def setUp(self):
        acc._CONTINUITY_MODE_CACHE = None

    def tearDown(self):
        acc._CONTINUITY_MODE_CACHE = None

    def test_default_continuity_mode_is_shadow(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(acc._load_continuity_mode(), "shadow")

    def test_shadow_mode_writes_would_metadata_without_extending_duration(self):
        events = [
            {
                "time": 0.0,
                "duration": 0.45,
                "pitch": 60,
                "voice_lane": "lh_bass",
                "schema_version": 2,
                "gate_ratio": 0.9,
            },
            {
                "time": 0.5,
                "duration": 0.5,
                "pitch": 62,
                "voice_lane": "lh_bass",
                "schema_version": 2,
                "gate_ratio": 0.9,
            },
        ]

        observed, summary = acc._repair_for_continuity_observation(
            events,
            bpm=120,
            tempo_curve=None,
            time_signature="4/4",
            hand="left",
            chord_boundaries=[],
            mode="shadow",
        )

        self.assertAlmostEqual(observed[0]["duration"], 0.45)
        self.assertAlmostEqual(observed[0]["continuity_meta"]["would_duration"], 0.5)
        self.assertEqual(summary["mode"], "shadow")
        self.assertEqual(summary["candidate_events"], 1)
        self.assertAlmostEqual(summary["total_extend_by"], 0.05)
        self.assertEqual(summary["lane_counts"], {"lh_bass": 1})

    def test_active_mode_extends_duration_and_reports_extended_metadata(self):
        events = [
            {"time": 0.0, "duration": 0.45, "pitch": 60, "voice_lane": "lh_bass"},
            {"time": 0.5, "duration": 0.5, "pitch": 62, "voice_lane": "lh_bass"},
        ]

        observed, summary = acc._repair_for_continuity_observation(
            events,
            bpm=120,
            tempo_curve=None,
            time_signature="4/4",
            hand="left",
            chord_boundaries=[],
            mode="active",
        )

        self.assertAlmostEqual(observed[0]["duration"], 0.5)
        self.assertAlmostEqual(observed[0]["continuity_meta"]["extended_by"], 0.05)
        self.assertEqual(summary["mode"], "active")
        self.assertEqual(summary["candidate_events"], 1)

    def test_off_mode_does_not_add_continuity_metadata(self):
        events = [
            {"time": 0.0, "duration": 0.45, "pitch": 60, "voice_lane": "lh_bass"},
            {"time": 0.5, "duration": 0.5, "pitch": 62, "voice_lane": "lh_bass"},
        ]

        observed, summary = acc._repair_for_continuity_observation(
            events,
            bpm=120,
            tempo_curve=None,
            time_signature="4/4",
            hand="left",
            chord_boundaries=[],
            mode="off",
        )

        self.assertIs(observed, events)
        self.assertNotIn("continuity_meta", observed[0])
        self.assertEqual(summary["mode"], "off")
        self.assertEqual(summary["candidate_events"], 0)

    def test_generate_accompaniment_includes_shadow_observation(self):
        with patch.dict(os.environ, {"LIVECHORD_NOTE_CONTINUITY_MODE": "shadow"}):
            acc._CONTINUITY_MODE_CACHE = None
            result = acc.generate_accompaniment(
                chords=[{"time": 0.0, "end": 2.0, "chord": "C"}],
                melody=[],
                bpm=120,
                style="Arpeggio",
                humanize=0,
            )

        observation = result["continuity_observation"]
        self.assertEqual(observation["mode"], "shadow")
        self.assertIn("left_hand", observation)
        self.assertIn("right_hand", observation)
        self.assertIn("total", observation)
        self.assertEqual(
            observation["total"]["event_count"],
            len(result["left_hand"]) + len(result["right_hand"]),
        )

    def test_comp_stab_gate_uses_factor_without_legacy_divisor(self):
        with patch.dict(os.environ, {"LIVECHORD_NOTE_CONTINUITY_MODE": "off"}):
            acc._CONTINUITY_MODE_CACHE = None
            result = acc.generate_accompaniment(
                chords=[{"time": 0.0, "end": 2.0, "chord": "C7"}],
                melody=[],
                bpm=120,
                style="BossaNova",
                humanize=0,
            )

        right = result["right_hand"]
        self.assertTrue(right)
        self.assertAlmostEqual(right[0]["duration"], 1.0)
        self.assertAlmostEqual(right[0]["gate_ratio"], 0.18)


if __name__ == "__main__":
    unittest.main()
