import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.ai.melody_extractor import MelodyExtractor
from backend.ai.melody_extractor_v2 import MelodyExtractorV2
from backend.ai.melody_schema import (
    MELODY_EVENT_SCHEMA_VERSION,
    MELODY_VOICE_LANE,
    finalize_melody_events,
)


class TestMelodyPhase3(unittest.TestCase):
    def test_finalize_melody_events_stamps_schema_and_extends_small_gap(self):
        events = [
            {"start": 0.0, "end": 0.45, "note": "C4", "midi": 60, "confidence": 0.9},
            {"start": 0.5, "end": 1.0, "note": "D4", "midi": 62, "confidence": 0.8},
        ]

        repaired = finalize_melody_events(events, bpm=120, time_signature="4/4")

        self.assertEqual(repaired[0]["schema_version"], MELODY_EVENT_SCHEMA_VERSION)
        self.assertEqual(repaired[0]["voice_lane"], MELODY_VOICE_LANE)
        self.assertEqual(repaired[0]["pitch"], 60)
        self.assertEqual(repaired[0]["midi"], 60)
        self.assertEqual(repaired[0]["gate_ratio"], 1.0)
        self.assertAlmostEqual(repaired[0]["time"], 0.0)
        self.assertAlmostEqual(repaired[0]["duration"], 0.5)
        self.assertAlmostEqual(repaired[0]["end"], 0.5)
        self.assertAlmostEqual(repaired[0]["continuity_meta"]["extended_by"], 0.05)

    def test_finalize_melody_events_keeps_phrase_silence(self):
        events = [
            {"start": 0.0, "end": 0.5, "note": "C4", "midi": 60},
            {"start": 2.5, "end": 3.0, "note": "G4", "midi": 67},
        ]

        repaired = finalize_melody_events(events, bpm=120)

        self.assertAlmostEqual(repaired[0]["duration"], 0.5)
        self.assertAlmostEqual(repaired[0]["end"], 0.5)
        self.assertNotIn("continuity_meta", repaired[0])

    def test_v1_post_process_output_uses_schema_helper(self):
        extractor = MelodyExtractor()
        post_processed = extractor._post_process([
            {"start": 0.0, "end": 0.2, "note": "C4", "midi": 60, "confidence": 0.8},
            {"start": 0.25, "end": 0.5, "note": "C4", "midi": 60, "confidence": 0.6},
            {"start": 0.55, "end": 0.8, "note": "D4", "midi": 62, "confidence": 0.7},
        ])

        repaired = finalize_melody_events(post_processed, bpm=120)

        self.assertEqual(len(repaired), 2)
        self.assertAlmostEqual(repaired[0]["end"], 0.55)
        self.assertEqual(repaired[0]["voice_lane"], MELODY_VOICE_LANE)

    def test_v2_filter_output_uses_schema_helper(self):
        extractor = MelodyExtractorV2.__new__(MelodyExtractorV2)
        filtered = extractor._filter_to_melody([
            {"start": 0.0, "end": 0.4, "note": "C4", "midi": 60, "confidence": 0.1},
            {"start": 0.1, "end": 0.5, "note": "E4", "midi": 64, "confidence": 0.9},
            {"start": 0.55, "end": 0.8, "note": "F4", "midi": 65, "confidence": 0.9},
        ])

        repaired = finalize_melody_events(filtered, bpm=120)

        self.assertEqual(repaired[0]["midi"], 64)
        self.assertAlmostEqual(repaired[0]["time"], 0.1)
        self.assertAlmostEqual(repaired[0]["duration"], 0.45)
        self.assertAlmostEqual(repaired[0]["end"], 0.55)
        self.assertEqual(repaired[0]["schema_version"], MELODY_EVENT_SCHEMA_VERSION)

    def test_ai_api_cached_legacy_melody_lazy_upgrades(self):
        backend_dir = Path(__file__).resolve().parents[1]
        if str(backend_dir) not in sys.path:
            sys.path.insert(0, str(backend_dir))
        import ai_api

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            melodies = root / "melodies"
            melodies.mkdir()
            cache_file = melodies / "song123.json"
            cache_file.write_text(
                json.dumps({
                    "path": "song.mp3",
                    "melody": [
                        {"start": 0.0, "end": 0.45, "note": "C4", "midi": 60},
                        {"start": 0.5, "end": 1.0, "note": "D4", "midi": 62},
                    ],
                }),
                encoding="utf-8",
            )

            with patch.object(ai_api, "DATA_DIR", root):
                result = ai_api.get_melody(path="", hash="song123")

            self.assertEqual(result["schema_version"], MELODY_EVENT_SCHEMA_VERSION)
            self.assertAlmostEqual(result["melody"][0]["duration"], 0.5)
            self.assertEqual(result["melody"][0]["voice_lane"], MELODY_VOICE_LANE)

            saved = json.loads(cache_file.read_text(encoding="utf-8"))
            self.assertEqual(saved["schema_version"], MELODY_EVENT_SCHEMA_VERSION)
            self.assertAlmostEqual(saved["melody"][0]["end"], 0.5)


if __name__ == "__main__":
    unittest.main()
