import unittest

from backend.ai.accompaniment_generator import (
    ACC_ENGINE_VERSION,
    generate_accompaniment,
)
from backend.ai.dynamics_engine import generate_dynamics


class TestAccompanimentPhase2(unittest.TestCase):
    def test_engine_version_outputs_schema_v2_voice_lanes_and_gate_ratios(self):
        # Pin the continuity mode: the machine's data/settings.json may say
        # "active" (NUC rollout 2026-09-06) and this test is about the schema.
        import backend.ai.accompaniment_generator as acc
        from unittest.mock import patch

        with patch.object(acc, "_CONTINUITY_MODE_CACHE", "shadow"):
            result = generate_accompaniment(
                chords=[{"time": 0.0, "end": 2.0, "chord": "C"}],
                melody=[],
                bpm=120,
                style="Arpeggio",
                humanize=0,
                time_signature="4/4",
            )

        self.assertEqual(ACC_ENGINE_VERSION, "v9")
        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(result["continuity_observation"]["mode"], "shadow")
        all_events = result["left_hand"] + result["right_hand"]
        self.assertTrue(all_events)
        self.assertTrue(all(e.get("schema_version") == 2 for e in all_events))
        self.assertTrue(all(e.get("voice_lane") for e in all_events))
        self.assertTrue(all(0.05 <= e.get("gate_ratio", 0) <= 1.0 for e in all_events))

    def test_pattern_duration_is_canonical_and_gate_preserves_old_touch(self):
        result = generate_accompaniment(
            chords=[{"time": 0.0, "end": 2.0, "chord": "C"}],
            melody=[],
            bpm=120,
            style="Arpeggio",
            humanize=0,
        )

        left = result["left_hand"]
        first_lh = min(left, key=lambda e: e["time"])

        self.assertAlmostEqual(first_lh["duration"], 0.5)
        self.assertAlmostEqual(first_lh["gate_ratio"], 0.9)
        self.assertIn(first_lh["voice_lane"], {"lh_bass", "lh_chord"})

    def test_period_pattern_drops_truncation_slivers_at_chord_end(self):
        # Phase 2.5 shadow review: pattern events cut to 20-41 ms by the chord
        # end were grouped with the NEXT chord's onset by the continuity core
        # and stretched across the boundary under the wrong harmony. They must
        # not be emitted at all; a real (>= 50 ms) truncated tail still is.
        from backend.ai.accompaniment_generator import _MIN_PATTERN_EVENT_S, _emit_period_pattern

        pattern = [(0.0, [0], 1.0), (0.5, [1], 0.8)]  # two half-period events
        emitted = []

        def emit(pi, item, t, dur):
            emitted.append((pi, round(t, 4), round(dur, 4)))

        # 120 BPM, period 4 beats = 2.0 s; chord ends 0.03 s after the 2nd event onset
        _emit_period_pattern(pattern, 0.0, 1.03, 4, 120.0, None, emit)
        self.assertEqual([e[0] for e in emitted], [0], emitted)
        self.assertLess(0.03, _MIN_PATTERN_EVENT_S)

        emitted.clear()
        _emit_period_pattern(pattern, 0.0, 1.10, 4, 120.0, None, emit)
        self.assertEqual([e[0] for e in emitted], [0, 1], emitted)
        self.assertAlmostEqual(emitted[1][2], 0.10, places=3)  # truncated, kept

    def test_block_chord_uses_full_canonical_duration(self):
        result = generate_accompaniment(
            chords=[{"time": 0.0, "end": 2.0, "chord": "C"}],
            melody=[],
            bpm=120,
            style="Block",
            humanize=0,
        )

        right = result["right_hand"]
        self.assertTrue(right)
        self.assertTrue(all(e["duration"] == 2.0 for e in right))
        self.assertTrue(all(e["gate_ratio"] == 0.9 for e in right))
        self.assertTrue(all(e["voice_lane"] == "rh_accompaniment" for e in right))

    def test_string_strum_has_string_voice_lane_and_group_gate(self):
        result = generate_accompaniment(
            chords=[{"time": 0.0, "end": 2.0, "chord": "C"}],
            melody=[],
            bpm=120,
            style="Block",
            humanize=0,
            instrument="guitar",
        )

        right = result["right_hand"]
        self.assertTrue(right)
        self.assertTrue(any(e.get("strum_id") for e in right))
        self.assertTrue(all(e["voice_lane"] == "string_strum" for e in right))
        self.assertTrue(all(0.05 <= e["gate_ratio"] <= 1.0 for e in right))

    def test_generate_dynamics_sets_gate_ratio_without_mutating_duration(self):
        events = [
            {"time": 0.0, "duration": 0.5, "pitch": 60, "velocity": 80, "gate_ratio": 0.9},
            {"time": 0.5, "duration": 0.5, "pitch": 60, "velocity": 80, "gate_ratio": 0.9},
            {"time": 1.0, "duration": 0.5, "pitch": 60, "velocity": 80, "gate_ratio": 0.9},
        ]

        generate_dynamics(events, bpm=160)

        self.assertTrue(all(e["duration"] == 0.5 for e in events))
        self.assertTrue(all(e["articulation"] == "staccato" for e in events))
        self.assertTrue(all(e["gate_ratio"] == 0.5 for e in events))


if __name__ == "__main__":
    unittest.main()
