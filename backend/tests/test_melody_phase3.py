import json
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

import tools.sample_melody_phase0_survey as survey_script
import tools.sample_rh_song_type_labels as song_type_label_script
import tools.report_rh_song_type_labels as song_type_report_script
import tools.train_rh_song_type_classifier as song_type_train_script
import tools.extract_rh_song_type_audio_features as song_type_audio_script
import tools.diagnose_rh_song_type_classifier as song_type_diagnostic_script
import tools.precompute_rh_song_type_stems as song_type_stems_script
import tools.evaluate_rh_vocal_gate as vocal_gate_script
import tools.sample_rh_vocal_gate_validation as vocal_gate_sample_script
import tools.run_basic_pitch_polyphonic_batch as polyphonic_script
from backend.ai.song_type_audio_features import (
    AUDIO_FEATURE_SOURCE,
    cached_stem_energy_features,
    summarize_audio_array,
)
from backend.ai.song_type_classifier import (
    AUDIO_MODEL_VERSION,
    evaluate_leave_one_out,
    merge_audio_feature_rows,
    predict_metadata_nb,
    train_metadata_nb,
)
from backend.ai.song_type_vocal_gate import evaluate_vocal_gate, threshold_sweep
from backend.ai.melody_extractor import MelodyExtractor
from backend.ai.melody_extractor_v2 import MelodyExtractorV2
from backend.ai.melody_review import (
    collect_library_cache_candidates,
    collect_survey_candidates,
    load_library_cache_paths,
    read_latest_review_tags,
    read_survey_queue,
    resolve_review_data_dir,
    sample_survey_candidates,
    write_survey_queue,
)
from backend.ai.melody_candidate import (
    FULL_MIX_PYIN,
    INSTRUMENT_LEAD,
    MELODY_CANDIDATE_CACHE_VERSION,
    SOLO_PIANO_POLYPHONIC,
    VOCAL_STEM_CREPE,
    build_candidate_payload,
    candidate_path,
    read_candidate_cache,
    selected_path,
    stem_path,
    write_candidate_cache,
)
from backend.ai.melody_resolver import MelodyResolver, RESOLVER_VERSION, RETREAT_LOW_COVERAGE_FLAG
from backend.ai.melody_ab_review_report import (
    build_review_rows,
    render_review_markdown,
    write_review_markdown,
)
from backend.ai.melody_residual_report import (
    build_vocal_residual_report,
    coverage_gap_metrics,
    phrase_tail_jump_metrics,
    write_residual_report,
)
from backend.ai.song_type_label_queue import (
    build_label_report,
    build_label_candidates,
    infer_song_type_hint,
    load_excluded_hashes,
    parse_quotas,
    render_label_report_markdown,
    sample_label_queue,
    write_label_queue,
)
from backend.ai.melody_shadow_generator import ShadowCandidateResult, ShadowGenerationResult, generate_shadow_candidates
from backend.ai.melody_shadow_smoke import (
    SMOKE_SURVEY_ID,
    SmokeQueueItem,
    read_smoke_queue,
    run_smoke_queue,
    write_smoke_report,
)
from backend.ai.piano_rh_selector import select_right_hand_melody
from backend.ai.melody_schema import (
    MELODY_EVENT_SCHEMA_VERSION,
    MELODY_VOICE_LANE,
    atomic_write_json,
    finalize_melody_events,
    finalize_melody_payload,
    melody_review_taxonomy,
    melody_context_from_chord_cache,
)
from backend.ai.stem_separation import StemCache, StemCacheResult
from backend.ai.vocal_melody_crepe import VocalCrepeResult, VocalStemCrepeExtractor


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
            self.assertEqual(result["melody_source"]["id"], "full_mix_pyin")
            self.assertIn("fallback_full_mix", result["quality_flags"])
            self.assertEqual(result["melody_stats"]["note_count"], 2)
            self.assertAlmostEqual(result["melody"][0]["duration"], 0.5)
            self.assertEqual(result["melody"][0]["voice_lane"], MELODY_VOICE_LANE)

            saved = json.loads(cache_file.read_text(encoding="utf-8"))
            self.assertEqual(saved["schema_version"], MELODY_EVENT_SCHEMA_VERSION)
            self.assertEqual(saved["melody_source"]["algorithm"], "librosa.pyin")
            self.assertEqual(saved["melody_stats"]["note_count"], 2)
            self.assertAlmostEqual(saved["melody"][0]["end"], 0.5)

    def test_ai_api_melody_debug_reports_metadata_without_extracting_or_writing(self):
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
                    "melody": [{"start": 0.0, "end": 0.5, "note": "C4", "midi": 60}],
                }),
                encoding="utf-8",
            )
            before = cache_file.read_text(encoding="utf-8")

            with patch.object(ai_api, "DATA_DIR", root):
                result = ai_api.get_melody_debug(path="", hash="song123", _="admin")

            self.assertTrue(result["cache"]["exists"])
            self.assertEqual(result["melody_source"]["id"], "full_mix_pyin")
            self.assertIn("fallback_full_mix", result["quality_flags"])
            self.assertEqual(result["melody_stats"]["note_count"], 1)
            self.assertIn("audio_quality", result["taxonomy"]["primary_tags"])
            self.assertEqual(cache_file.read_text(encoding="utf-8"), before)

    def test_ai_api_melody_debug_missing_cache_keeps_shape(self):
        backend_dir = Path(__file__).resolve().parents[1]
        if str(backend_dir) not in sys.path:
            sys.path.insert(0, str(backend_dir))
        import ai_api

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "melodies").mkdir()

            with patch.object(ai_api, "DATA_DIR", root):
                result = ai_api.get_melody_debug(path="", hash="missing123", _="admin")

            self.assertFalse(result["cache"]["exists"])
            self.assertEqual(result["melody_source"]["id"], "no_cache")
            self.assertEqual(result["melody_source"]["selected_by"], "no_cache")
            self.assertEqual(result["melody_source"]["cache_version"], "")
            self.assertEqual(result["melody_source"]["phase"], "phase0")
            self.assertIn("no_cache", result["quality_flags"])
            self.assertEqual(result["melody_stats"]["density_when_active_per_s"], 0.0)

    def test_ai_api_melody_debug_empty_query_keeps_shape(self):
        backend_dir = Path(__file__).resolve().parents[1]
        if str(backend_dir) not in sys.path:
            sys.path.insert(0, str(backend_dir))
        import ai_api

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "melodies").mkdir()

            with patch.object(ai_api, "DATA_DIR", root):
                result = ai_api.get_melody_debug(path="", hash="", _="admin")

            self.assertEqual(result["lookup"], "none")
            self.assertEqual(result["melody_source"]["id"], "no_cache")
            self.assertIn("no_cache", result["quality_flags"])

    def test_ai_api_melody_debug_rehashed_chord_path_updates_hash(self):
        backend_dir = Path(__file__).resolve().parents[1]
        if str(backend_dir) not in sys.path:
            sys.path.insert(0, str(backend_dir))
        import ai_api
        import backend.chord_cache as chord_cache

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            melodies = root / "melodies"
            melodies.mkdir()
            chord_root = root / "chords"
            old_hash = "abc123456789"
            path = "renamed/song.mp3"
            new_hash = chord_cache.song_hash(path)
            (chord_root / old_hash[:2]).mkdir(parents=True)
            (chord_root / old_hash[:2] / f"{old_hash}.json").write_text(
                json.dumps({"path": path}),
                encoding="utf-8",
            )
            (melodies / f"{new_hash}.json").write_text(
                json.dumps({
                    "path": path,
                    "melody": [{"start": 0.0, "end": 0.5, "note": "C4", "midi": 60}],
                }),
                encoding="utf-8",
            )

            patch_targets = [chord_cache]
            try:
                import chord_cache as top_level_chord_cache
                if top_level_chord_cache is not chord_cache:
                    patch_targets.append(top_level_chord_cache)
            except ImportError:
                pass

            with ExitStack() as stack:
                stack.enter_context(patch.object(ai_api, "DATA_DIR", root))
                for target in patch_targets:
                    stack.enter_context(patch.object(target, "CHORDS_DIR", chord_root))
                    stack.enter_context(patch.object(target, "DEMO_CHORDS_DIR", root / "demo" / "chords"))
                result = ai_api.get_melody_debug(path="", hash=old_hash, _="admin")

            self.assertEqual(result["lookup"], "hash_via_chord_path_rehash")
            self.assertEqual(result["query_hash"], old_hash)
            self.assertEqual(result["song_hash"], new_hash)
            self.assertTrue(result["hash_recomputed"])

    def test_ai_api_melody_debug_tag_appends_phase0_jsonl(self):
        backend_dir = Path(__file__).resolve().parents[1]
        if str(backend_dir) not in sys.path:
            sys.path.insert(0, str(backend_dir))
        import ai_api

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            melodies = root / "melodies"
            melodies.mkdir()
            (root / "melody_reviews").mkdir()
            (melodies / "song123.json").write_text(
                json.dumps({
                    "path": "song.mp3",
                    "melody": [{"start": 0.0, "end": 0.5, "note": "C4", "midi": 60}],
                }),
                encoding="utf-8",
            )
            body = ai_api.MelodyDebugTagRequest(
                hash="song123",
                failure_tag="wrong_octave",
                secondary_flags=["needs_ab_replay", "needs_ab_replay"],
                audio_quality_note="reverb_high",
                review_note="octave is high in chorus",
                survey_id="phase0_random_200_seed_20260520",
            )

            with patch.object(ai_api, "DATA_DIR", root):
                result = ai_api.post_melody_debug_tag(body=body, reviewer="teacher")

            self.assertTrue(result["ok"])
            self.assertEqual(result["entry"]["reviewer"], "teacher")
            self.assertEqual(result["entry"]["failure_tag"], "wrong_octave")
            self.assertTrue(result["entry"]["post_filter_fixable"])
            self.assertEqual(result["entry"]["secondary_flags"], ["needs_ab_replay"])
            self.assertEqual(result["entry"]["melody_stats"]["note_count"], 1)
            log_file = root / "melody_reviews" / "phase0_tags.jsonl"
            rows = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["audio_quality_note"], "reverb_high")

    def test_ai_api_melody_debug_survey_reports_queue_and_latest_tags(self):
        backend_dir = Path(__file__).resolve().parents[1]
        if str(backend_dir) not in sys.path:
            sys.path.insert(0, str(backend_dir))
        import ai_api

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            review_dir = root / "melody_reviews"
            review_dir.mkdir()
            queue = review_dir / "phase0_survey_queue.jsonl"
            queue.write_text(
                "\n".join([
                    json.dumps({
                        "survey_id": "phase0",
                        "hash": "song123",
                        "path": "song.mp3",
                        "sample_order": 1,
                    }),
                    json.dumps({
                        "survey_id": "other",
                        "hash": "song999",
                        "path": "other.mp3",
                        "sample_order": 2,
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            (review_dir / "phase0_survey_queue.summary.json").write_text(
                json.dumps({"survey_id": "phase0", "sample_size": 1}),
                encoding="utf-8",
            )
            (review_dir / "phase0_tags.jsonl").write_text(
                json.dumps({
                    "survey_id": "phase0",
                    "song_hash": "song123",
                    "path": "song.mp3",
                    "failure_tag": "pyin_fine",
                    "created_at": "2026-05-20T00:00:00+00:00",
                }) + "\n",
                encoding="utf-8",
            )

            with patch.object(ai_api, "DATA_DIR", root):
                result = ai_api.get_melody_debug_survey(_="admin")

            self.assertEqual(result["survey_id"], "phase0")
            self.assertEqual(result["total"], 1)
            self.assertEqual(result["completed"], 1)
            self.assertEqual(result["items"][0]["review_tag"]["failure_tag"], "pyin_fine")
            self.assertIn("pyin_fine", result["taxonomy"]["primary_tags"])

    def test_ai_api_melody_debug_candidates_reports_shadow_cache(self):
        backend_dir = Path(__file__).resolve().parents[1]
        if str(backend_dir) not in sys.path:
            sys.path.insert(0, str(backend_dir))
        import ai_api

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            melodies = root / "melodies"
            melodies.mkdir()
            song_hash = "song123"
            (melodies / f"{song_hash}.json").write_text(
                json.dumps({
                    "path": "song.mp3",
                    "melody": [{"start": 0.0, "end": 0.5, "note": "C4", "midi": 60}],
                }),
                encoding="utf-8",
            )
            candidate_payload = build_candidate_payload(
                song_hash=song_hash,
                path="song.mp3",
                candidate_id=VOCAL_STEM_CREPE,
                melody=[
                    {"start": 0.0, "end": 0.4, "note": "D4", "midi": 62},
                    {"start": 0.4, "end": 0.8, "note": "E4", "midi": 64},
                ],
                stem="vocals",
                algorithm="htdemucs+crepe",
                quality_flags=["shadow_candidate"],
            )
            write_candidate_cache(root, song_hash, VOCAL_STEM_CREPE, candidate_payload)

            with patch.object(ai_api, "DATA_DIR", root):
                result = ai_api.get_melody_debug_candidates(path="", hash=song_hash, _="admin")

            self.assertTrue(result["ok"])
            self.assertEqual(result["song_hash"], song_hash)
            self.assertEqual(result["current"]["melody_stats"]["note_count"], 1)
            by_id = {item["id"]: item for item in result["candidates"]}
            self.assertTrue(by_id[VOCAL_STEM_CREPE]["exists"])
            self.assertEqual(by_id[VOCAL_STEM_CREPE]["melody_stats"]["note_count"], 2)
            self.assertEqual(by_id[VOCAL_STEM_CREPE]["melody_source"]["algorithm"], "htdemucs+crepe")
            self.assertFalse(by_id[SOLO_PIANO_POLYPHONIC]["exists"])
            self.assertIn("candidate_missing", by_id[SOLO_PIANO_POLYPHONIC]["quality_flags"])

    def test_ai_api_melody_debug_candidates_reports_invalid_json(self):
        backend_dir = Path(__file__).resolve().parents[1]
        if str(backend_dir) not in sys.path:
            sys.path.insert(0, str(backend_dir))
        import ai_api

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            song_hash = "song123"
            bad_path = candidate_path(root, song_hash, VOCAL_STEM_CREPE)
            bad_path.parent.mkdir(parents=True, exist_ok=True)
            bad_path.write_text("{bad json", encoding="utf-8")

            with patch.object(ai_api, "DATA_DIR", root):
                result = ai_api.get_melody_debug_candidates(path="", hash=song_hash, _="admin")

            by_id = {item["id"]: item for item in result["candidates"]}
            self.assertTrue(by_id[VOCAL_STEM_CREPE]["exists"])
            self.assertIn("invalid_json", by_id[VOCAL_STEM_CREPE]["error"])
            self.assertIn("candidate_invalid_json", by_id[VOCAL_STEM_CREPE]["quality_flags"])

    def test_ai_api_melody_ab_review_lists_candidates_and_latest_feedback(self):
        backend_dir = Path(__file__).resolve().parents[1]
        if str(backend_dir) not in sys.path:
            sys.path.insert(0, str(backend_dir))
        import ai_api

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            review_dir = root / "melody_reviews"
            review_dir.mkdir()
            song_hash = "song123"
            smoke_row = {
                "sample_order": 1,
                "group": "vocal",
                "requested": {"path": "song.mp3", "title": "Song", "artist": "Singer"},
                "resolved_candidates": [FULL_MIX_PYIN, VOCAL_STEM_CREPE],
                "result": {
                    "song_hash": song_hash,
                    "path": "song.mp3",
                    "results": [
                        {"candidate_id": FULL_MIX_PYIN, "ok": True, "status": "cached"},
                        {"candidate_id": VOCAL_STEM_CREPE, "ok": True, "status": "generated"},
                    ],
                },
            }
            (review_dir / "phase0_5_ab_smoke_results.jsonl").write_text(
                json.dumps(smoke_row) + "\n",
                encoding="utf-8",
            )
            (review_dir / "phase0_5_ab_feedback.jsonl").write_text(
                json.dumps({
                    "song_hash": song_hash,
                    "group": "vocal",
                    "candidate_a": FULL_MIX_PYIN,
                    "candidate_b": VOCAL_STEM_CREPE,
                    "applicable": True,
                    "preferred": "b",
                    "review_note": "stem is cleaner",
                    "created_at": "2026-05-21T00:00:00+00:00",
                }) + "\n",
                encoding="utf-8",
            )
            write_candidate_cache(
                root,
                song_hash,
                FULL_MIX_PYIN,
                build_candidate_payload(
                    song_hash=song_hash,
                    path="song.mp3",
                    candidate_id=FULL_MIX_PYIN,
                    melody=[{"start": 0.0, "end": 0.5, "note": "C4", "midi": 60}],
                    stem="full_mix",
                    algorithm="pyin",
                ),
            )
            write_candidate_cache(
                root,
                song_hash,
                VOCAL_STEM_CREPE,
                build_candidate_payload(
                    song_hash=song_hash,
                    path="song.mp3",
                    candidate_id=VOCAL_STEM_CREPE,
                    melody=[{"start": 0.0, "end": 0.4, "note": "D4", "midi": 62}],
                    stem="vocals",
                    algorithm="htdemucs+crepe",
                ),
            )

            with patch.object(ai_api, "DATA_DIR", root):
                result = ai_api.get_melody_ab_review(group="vocal", _="admin")

            self.assertTrue(result["ok"])
            self.assertEqual(result["total"], 1)
            item = result["items"][0]
            self.assertEqual(item["title"], "Song")
            self.assertIn("/api/track/stream?path=song.mp3", item["audio_url"])
            self.assertEqual(item["candidate_a"]["id"], FULL_MIX_PYIN)
            self.assertEqual(item["candidate_b"]["id"], VOCAL_STEM_CREPE)
            self.assertEqual(item["candidate_a"]["melody"][0]["midi"], 60)
            self.assertEqual(item["candidate_b"]["smoke_status"], "generated")
            self.assertEqual(item["feedback"]["preferred"], "b")
            self.assertIs(item["feedback"]["applicable"], True)

    def test_ai_api_melody_ab_feedback_appends_jsonl(self):
        backend_dir = Path(__file__).resolve().parents[1]
        if str(backend_dir) not in sys.path:
            sys.path.insert(0, str(backend_dir))
        import ai_api

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "melody_reviews").mkdir()
            body = ai_api.MelodyAbFeedbackRequest(
                song_hash="song123",
                path="song.mp3",
                group="vocal",
                candidate_a=FULL_MIX_PYIN,
                candidate_b=VOCAL_STEM_CREPE,
                applicable=False,
                preferred="b",
                octave="b",
                sustain="tie",
                boundary="b",
                review_note="better phrase endings",
            )

            with patch.object(ai_api, "DATA_DIR", root):
                result = ai_api.post_melody_ab_feedback(body=body, reviewer="teacher")

            self.assertTrue(result["ok"])
            self.assertEqual(result["entry"]["reviewer"], "teacher")
            self.assertEqual(result["entry"]["preferred"], "b")
            log_file = root / "melody_reviews" / "phase0_5_ab_feedback.jsonl"
            rows = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["review_note"], "better phrase endings")
            self.assertIs(rows[0]["applicable"], False)

    def test_ai_api_song_type_label_queue_round_trips_latest_label(self):
        backend_dir = Path(__file__).resolve().parents[1]
        if str(backend_dir) not in sys.path:
            sys.path.insert(0, str(backend_dir))
        import ai_api

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            review_dir = root / "melody_reviews"
            review_dir.mkdir()
            song_hash = "songtype123"
            (review_dir / "phase0_5_song_type_label_queue.jsonl").write_text(
                json.dumps({
                    "schema_version": 1,
                    "phase": "phase0_5_song_type",
                    "survey_id": "heldout",
                    "sample_order": 1,
                    "hash": song_hash,
                    "path": "song.mp3",
                    "title": "Song",
                    "candidate_hint": "vocal_led",
                    "human_label": "pending",
                }) + "\n",
                encoding="utf-8",
            )
            body = ai_api.SongTypeLabelRequest(
                song_hash=song_hash,
                path="song.mp3",
                human_label="vocal_led",
                candidate_hint="vocal_led",
                review_note="clear vocal",
                survey_id="heldout",
            )

            with patch.object(ai_api, "DATA_DIR", root):
                saved = ai_api.post_song_type_label(body=body, reviewer="teacher")
                result = ai_api.get_song_type_label_queue(_="admin")

            self.assertTrue(saved["ok"])
            self.assertEqual(saved["entry"]["human_label"], "vocal_led")
            self.assertEqual(result["total"], 1)
            self.assertEqual(result["items"][0]["song_hash"], song_hash)
            self.assertEqual(result["items"][0]["label"]["review_note"], "clear vocal")
            self.assertIn("/api/track/stream?path=song.mp3", result["items"][0]["audio_url"])

    def test_ai_api_vocal_gate_label_queue_uses_binary_labels(self):
        backend_dir = Path(__file__).resolve().parents[1]
        if str(backend_dir) not in sys.path:
            sys.path.insert(0, str(backend_dir))
        import ai_api

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            review_dir = root / "melody_reviews"
            review_dir.mkdir()
            song_hash = "vocalgate123"
            (review_dir / "phase0_5_vocal_gate_validation_queue.jsonl").write_text(
                json.dumps({
                    "schema_version": 1,
                    "phase": "phase0_5_vocal_gate_validation",
                    "survey_id": "validation",
                    "sample_order": 1,
                    "hash": song_hash,
                    "path": "song.mp3",
                    "title": "Song",
                    "candidate_hint": "vocal_led",
                    "human_label": "pending",
                }) + "\n",
                encoding="utf-8",
            )
            body = ai_api.SongTypeLabelRequest(
                song_hash=song_hash,
                path="song.mp3",
                human_label="not_vocal",
                candidate_hint="vocal_led",
                review_note="instrumental track",
                survey_id="validation",
            )

            with patch.object(ai_api, "DATA_DIR", root):
                saved = ai_api.post_song_type_label(
                    body=body,
                    queue="vocal_gate_validation",
                    reviewer="teacher",
                )
                result = ai_api.get_song_type_label_queue(queue="vocal_gate_validation", _="admin")

            self.assertTrue(saved["ok"])
            self.assertEqual(saved["entry"]["phase"], "phase0_5_vocal_gate_validation")
            self.assertEqual(saved["entry"]["human_label"], "not_vocal")
            self.assertIn("not_vocal", result["label_options"])
            self.assertEqual(result["queue"], "vocal_gate_validation")
            self.assertEqual(result["items"][0]["label"]["human_label"], "not_vocal")
            log_file = review_dir / "phase0_5_vocal_gate_validation_labels.jsonl"
            rows = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(rows[0]["review_note"], "instrumental track")

    def test_ai_api_song_type_label_queue_still_rejects_not_vocal(self):
        backend_dir = Path(__file__).resolve().parents[1]
        if str(backend_dir) not in sys.path:
            sys.path.insert(0, str(backend_dir))
        import ai_api

        body = ai_api.SongTypeLabelRequest(song_hash="song123", human_label="not_vocal")
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(ai_api, "DATA_DIR", Path(tmp)):
                with self.assertRaises(HTTPException):
                    ai_api.post_song_type_label(body=body, queue="song_type", reviewer="teacher")

    def test_ai_api_song_type_label_rejects_unknown_label(self):
        backend_dir = Path(__file__).resolve().parents[1]
        if str(backend_dir) not in sys.path:
            sys.path.insert(0, str(backend_dir))
        import ai_api

        body = ai_api.SongTypeLabelRequest(song_hash="song123", human_label="bad_label")
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(ai_api, "DATA_DIR", Path(tmp)):
                with self.assertRaises(HTTPException):
                    ai_api.post_song_type_label(body=body, reviewer="teacher")

    def test_ai_api_melody_debug_tag_rejects_unknown_labels(self):
        backend_dir = Path(__file__).resolve().parents[1]
        if str(backend_dir) not in sys.path:
            sys.path.insert(0, str(backend_dir))
        import ai_api

        body = ai_api.MelodyDebugTagRequest(
            hash="song123",
            failure_tag="not_a_real_tag",
        )

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(ai_api, "DATA_DIR", Path(tmp)):
                with self.assertRaises(HTTPException) as ctx:
                    ai_api.post_melody_debug_tag(body=body, reviewer="admin")

        self.assertEqual(ctx.exception.status_code, 400)

    def test_ai_api_melody_debug_tag_requires_target(self):
        backend_dir = Path(__file__).resolve().parents[1]
        if str(backend_dir) not in sys.path:
            sys.path.insert(0, str(backend_dir))
        import ai_api

        body = ai_api.MelodyDebugTagRequest(failure_tag="pyin_fine")

        with self.assertRaises(HTTPException) as ctx:
            ai_api.post_melody_debug_tag(body=body, reviewer="admin")

        self.assertEqual(ctx.exception.status_code, 400)

    def test_melody_phase0_survey_sampling_filters_and_writes_queue(self):
        backend_dir = Path(__file__).resolve().parents[1]
        if str(backend_dir) not in sys.path:
            sys.path.insert(0, str(backend_dir))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chord_root = root / "chords"
            audio_root = root / "audio"
            audio_root.mkdir()
            (audio_root / "song-a.mp3").write_text("audio", encoding="utf-8")
            for h, payload in {
                "aa1111111111": {"path": "song-a.mp3", "source": "btc", "chords": [{"chord": "C"}]},
                "bb2222222222": {"path": "song-b.mp3", "source": "midi", "chords": [{"chord": "G"}]},
                "cc3333333333": {"source": "btc", "chords": [{"chord": "F"}]},
            }.items():
                bucket = chord_root / h[:2]
                bucket.mkdir(parents=True, exist_ok=True)
                (bucket / f"{h}.json").write_text(json.dumps(payload), encoding="utf-8")

            def resolver(path):
                return str(audio_root / path)

            candidates, stats = collect_survey_candidates(
                chord_root,
                require_audio=True,
                resolve_audio_path=resolver,
            )
            sample = sample_survey_candidates(candidates, sample_size=200, seed=7)
            out = root / "melody_reviews" / "queue.jsonl"
            summary = write_survey_queue(
                out,
                sample,
                survey_id="phase0_random_200_seed_7",
                seed=7,
                candidate_stats=stats,
            )

            self.assertEqual(stats["checked"], 3)
            self.assertEqual(stats["missing_audio"], 1)
            self.assertEqual(stats["missing_path"], 1)
            self.assertEqual(len(sample), 1)
            self.assertEqual(sample[0]["hash"], "aa1111111111")
            self.assertEqual(summary["sample_size"], 1)
            row = json.loads(out.read_text(encoding="utf-8").strip())
            self.assertEqual(row["status"], "pending")
            self.assertEqual(row["survey_id"], "phase0_random_200_seed_7")

    def test_melody_phase0_survey_include_missing_audio_and_preserves_blank_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chord_root = root / "chords"
            h = "aa1111111111"
            bucket = chord_root / h[:2]
            bucket.mkdir(parents=True, exist_ok=True)
            (bucket / f"{h}.json").write_text(
                json.dumps({"path": "missing.mp3", "chords": [{"chord": "C"}]}),
                encoding="utf-8",
            )

            candidates, stats = collect_survey_candidates(
                chord_root,
                require_audio=False,
                resolve_audio_path=lambda path: str(root / path),
            )

            self.assertEqual(stats["missing_audio"], 0)
            self.assertEqual(len(candidates), 1)
            self.assertFalse(candidates[0]["audio_exists"])
            self.assertEqual(candidates[0]["source"], "")

    def test_melody_phase0_survey_filters_with_library_cache_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chord_root = root / "chords"
            for h, path in {
                "aa1111111111": "@1/POP/song-a.mp3",
                "bb2222222222": "missing/song-b.mp3",
            }.items():
                bucket = chord_root / h[:2]
                bucket.mkdir(parents=True, exist_ok=True)
                (bucket / f"{h}.json").write_text(
                    json.dumps({"path": path, "chords": [{"chord": "C"}]}),
                    encoding="utf-8",
                )
            library_cache = root / "library_cache.json"
            library_cache.write_text(
                json.dumps({"tracks": [{"path": "POP/song-a.mp3"}]}),
                encoding="utf-8",
            )

            existing = load_library_cache_paths(library_cache)
            candidates, stats = collect_survey_candidates(
                chord_root,
                require_audio=True,
                existing_audio_paths=existing,
            )

            self.assertEqual(existing, {"pop/song-a.mp3"})
            self.assertEqual(stats["missing_audio"], 1)
            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0]["hash"], "aa1111111111")

    def test_melody_phase0_survey_collects_library_cache_candidates(self):
        import backend.chord_cache as chord_cache

        with tempfile.TemporaryDirectory() as tmp:
            library_cache = Path(tmp) / "library_cache.json"
            library_cache.write_text(
                json.dumps({
                    "tracks": [
                        {
                            "path": "@1/POP/song-a.mp3",
                            "title": "Song A",
                            "artist": "Artist",
                            "duration": 123.4,
                        },
                        {"path": ""},
                    ]
                }),
                encoding="utf-8",
            )

            candidates, stats = collect_library_cache_candidates(library_cache)

            self.assertEqual(stats["checked"], 2)
            self.assertEqual(stats["missing_path"], 1)
            self.assertEqual(stats["candidates"], 1)
            self.assertEqual(candidates[0]["hash"], chord_cache.song_hash("@1/POP/song-a.mp3"))
            self.assertEqual(candidates[0]["selection_source"], "library_cache")
            self.assertTrue(candidates[0]["audio_exists"])
            self.assertEqual(candidates[0]["duration_s"], 123.4)

    def test_song_type_label_queue_infers_metadata_hints(self):
        self.assertEqual(
            infer_song_type_hint({"title": "Chopin Nocturne solo piano"})[0],
            "solo_piano",
        )
        self.assertEqual(
            infer_song_type_hint({"path": "Jam/Blues backing track.flac"})[0],
            "no_clear_lead",
        )
        self.assertEqual(
            infer_song_type_hint({"title": "Sam Smith - How Do You Sleep? Official Music Video"})[0],
            "vocal_led",
        )
        self.assertEqual(
            infer_song_type_hint({"artist": "Dave Brubeck", "title": "Take Five"})[0],
            "instrumental_lead",
        )
        self.assertEqual(
            infer_song_type_hint({"title": "ABBA Official Music Video"})[0],
            "vocal_led",
        )

    def test_song_type_label_queue_excludes_smoke_hashes_and_writes_summary(self):
        import backend.chord_cache as chord_cache

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            excluded_path = root / "smoke.jsonl"
            excluded_hash = chord_cache.song_hash("ABBA - Song.flac")
            excluded_path.write_text(
                json.dumps({"hash": excluded_hash}) + "\n",
                encoding="utf-8",
            )
            tracks = [
                {"path": "ABBA - Song.flac", "title": "ABBA Official Music Video", "duration": 100},
                {"path": "Chopin - Nocturne.flac", "title": "Chopin Nocturne solo piano", "duration": 200},
                {"path": "Jazz/Take Five.flac", "artist": "Dave Brubeck", "duration": 300},
                {"path": "Jam/Backing Track.flac", "title": "Blues backing track", "duration": 150},
            ]

            excluded = load_excluded_hashes([excluded_path])
            candidates, stats = build_label_candidates(
                tracks,
                exclude_hashes=excluded,
                base_url="http://example.test",
            )
            sample = sample_label_queue(
                candidates,
                quotas={"solo_piano": 1, "instrumental_lead": 1, "no_clear_lead": 1},
                seed=1,
            )
            out = root / "labels.jsonl"
            summary = write_label_queue(
                out,
                sample,
                survey_id="heldout",
                seed=1,
                candidate_stats=stats,
                quotas={"solo_piano": 1, "instrumental_lead": 1, "no_clear_lead": 1},
            )

            self.assertEqual(stats["excluded"], 1)
            self.assertEqual(len(sample), 3)
            rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(rows[0]["phase"], "phase0_5_song_type")
            self.assertEqual(rows[0]["human_label"], "pending")
            self.assertIn("solo_piano", rows[0]["label_options"])
            self.assertTrue(rows[0]["player_url"].startswith("http://example.test/player?path="))
            self.assertEqual(summary["by_hint"], {
                "instrumental_lead": 1,
                "no_clear_lead": 1,
                "solo_piano": 1,
            })

    def test_sample_rh_song_type_labels_cli_writes_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            library_cache = root / "library_cache.json"
            library_cache.write_text(
                json.dumps({
                    "tracks": [
                        {"path": "ABBA - Song.flac", "title": "Official Music Video"},
                        {"path": "Chopin - Nocturne.flac", "title": "Chopin Nocturne solo piano"},
                    ]
                }),
                encoding="utf-8",
            )
            out = root / "queue.jsonl"

            with patch.object(sys, "argv", [
                "sample_rh_song_type_labels.py",
                "--library-cache", str(library_cache),
                "--out", str(out),
                "--quotas", "vocal_led=1,solo_piano=1",
                "--force",
            ]):
                code = song_type_label_script.main()

            self.assertEqual(code, 0)
            rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 2)
            self.assertTrue(out.with_suffix(".summary.json").is_file())

    def test_song_type_label_queue_parse_quotas_rejects_unknown_label(self):
        with self.assertRaises(ValueError):
            parse_quotas("vocal=10")

    def test_song_type_label_report_computes_precision_and_confusion(self):
        queue_rows = [
            {"survey_id": "heldout", "hash": "a", "candidate_hint": "vocal_led"},
            {"survey_id": "heldout", "hash": "b", "candidate_hint": "vocal_led"},
            {"survey_id": "heldout", "hash": "c", "candidate_hint": "solo_piano"},
            {"survey_id": "heldout", "hash": "d", "candidate_hint": "no_clear_lead"},
        ]
        label_rows = [
            {"survey_id": "heldout", "song_hash": "a", "human_label": "vocal_led"},
            {"survey_id": "heldout", "song_hash": "b", "human_label": "instrumental_lead"},
            {"survey_id": "heldout", "song_hash": "c", "human_label": "solo_piano"},
        ]

        report = build_label_report(queue_rows, label_rows)
        markdown = render_label_report_markdown(report)

        self.assertEqual(report["total"], 4)
        self.assertEqual(report["labeled"], 3)
        self.assertEqual(report["pending"], 1)
        self.assertEqual(report["confusion"]["vocal_led"]["vocal_led"], 1)
        self.assertEqual(report["confusion"]["vocal_led"]["instrumental_lead"], 1)
        self.assertAlmostEqual(report["precision_by_label"]["vocal_led"], 0.5)
        self.assertIn("RH Song-Type Label Report", markdown)

    def test_report_rh_song_type_labels_cli_writes_json_and_returns_nonzero_without_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue = root / "queue.jsonl"
            labels = root / "labels.jsonl"
            out = root / "report.json"
            queue.write_text(
                json.dumps({"survey_id": "heldout", "hash": "a", "candidate_hint": "vocal_led"}) + "\n",
                encoding="utf-8",
            )

            with patch.object(sys, "argv", [
                "report_rh_song_type_labels.py",
                "--queue", str(queue),
                "--labels", str(labels),
                "--out", str(out),
                "--force-output",
            ]):
                code = song_type_report_script.main()

            self.assertEqual(code, 1)
            report = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(report["labeled"], 0)
            self.assertEqual(report["pending"], 1)

    def test_song_type_metadata_nb_predicts_from_text(self):
        rows = [
            {"title": "ABBA Official Music Video", "genre": "POP", "resolved_label": "vocal_led"},
            {"title": "Whitney Houston Live", "genre": "POP", "resolved_label": "vocal_led"},
            {"title": "Chopin Nocturne", "path": "Classics/Piano", "resolved_label": "solo_piano"},
            {"title": "Moonlight Sonata", "path": "Classics/Piano", "resolved_label": "solo_piano"},
            {"title": "Take Five", "artist": "Dave Brubeck", "genre": "Jazz", "resolved_label": "instrumental_lead"},
            {"title": "Backing Track in C", "path": "Jam/backing track.flac", "resolved_label": "no_clear_lead"},
        ]

        model = train_metadata_nb(rows)
        vocal = predict_metadata_nb({"title": "ABBA live video", "genre": "POP"}, model)
        piano = predict_metadata_nb({"title": "Chopin Piano Sonata", "path": "Classics/Piano"}, model)
        report = evaluate_leave_one_out(rows)

        self.assertEqual(model["documents"], 6)
        self.assertEqual(vocal["song_type"], "vocal_led")
        self.assertEqual(piano["song_type"], "solo_piano")
        self.assertEqual(report["total"], 6)
        self.assertIn("vocal_led", report["precision_by_label"])

    def test_song_type_metadata_nb_ignores_unseen_tokens_instead_of_favoring_minority_class(self):
        rows = [
            {"title": "ABBA Official Music Video", "genre": "POP", "resolved_label": "vocal_led"},
            {"title": "Whitney Houston Live Vocal", "genre": "POP", "resolved_label": "vocal_led"},
            {"title": "Mariah Carey Ballad", "genre": "POP", "resolved_label": "vocal_led"},
            {"title": "Beatles Lyrics", "genre": "POP", "resolved_label": "vocal_led"},
            {"title": "Obscure Ambiguous Intro", "genre": "Other", "resolved_label": "unknown"},
        ]

        model = train_metadata_nb(rows)
        prediction = predict_metadata_nb({"title": "zzzxq novel unseen tokens"}, model)

        self.assertEqual(prediction["song_type"], "vocal_led")

    def test_song_type_metadata_nb_ignores_common_stop_tokens(self):
        rows = [
            {"title": "Song Of The Night", "resolved_label": "vocal_led"},
            {"title": "Jazz Of The Night", "resolved_label": "instrumental_lead"},
            {"title": "Piano Of The Night", "resolved_label": "solo_piano"},
        ]

        model = train_metadata_nb(rows)

        self.assertNotIn("of", model["vocab"])
        self.assertNotIn("the", model["vocab"])
        self.assertIn("night", model["vocab"])

    def test_song_type_metadata_audio_nb_uses_audio_feature_tokens(self):
        rows = [
            {
                "survey_id": "heldout",
                "hash": "v1",
                "title": "Ambiguous Song",
                "resolved_label": "vocal_led",
            },
            {
                "survey_id": "heldout",
                "hash": "v2",
                "title": "Another Track",
                "resolved_label": "vocal_led",
            },
            {
                "survey_id": "heldout",
                "hash": "p1",
                "title": "Ambiguous Song",
                "resolved_label": "solo_piano",
            },
            {
                "survey_id": "heldout",
                "hash": "p2",
                "title": "Another Track",
                "resolved_label": "solo_piano",
            },
        ]
        feature_rows = [
            {
                "survey_id": "heldout",
                "song_hash": "v1",
                "status": "ok",
                "mix": {"spectral_centroid_mean": 1900, "zero_crossing_rate_mean": 0.13, "hpss_harmonic_ratio": 0.75},
                "stems": {"stem_status": "missing_cached_stems"},
            },
            {
                "survey_id": "heldout",
                "song_hash": "v2",
                "status": "ok",
                "mix": {"spectral_centroid_mean": 1850, "zero_crossing_rate_mean": 0.14, "hpss_harmonic_ratio": 0.76},
                "stems": {"stem_status": "missing_cached_stems"},
            },
            {
                "survey_id": "heldout",
                "song_hash": "p1",
                "status": "ok",
                "mix": {"spectral_centroid_mean": 700, "zero_crossing_rate_mean": 0.04, "hpss_harmonic_ratio": 0.96},
                "stems": {"stem_status": "missing_cached_stems"},
            },
            {
                "survey_id": "heldout",
                "song_hash": "p2",
                "status": "ok",
                "mix": {"spectral_centroid_mean": 750, "zero_crossing_rate_mean": 0.05, "hpss_harmonic_ratio": 0.95},
                "stems": {"stem_status": "missing_cached_stems"},
            },
        ]

        enriched = merge_audio_feature_rows(rows, feature_rows)
        model = train_metadata_nb(enriched)
        prediction = predict_metadata_nb({
            "title": "Ambiguous Track",
            "_audio_features": {
                "mix": {"spectral_centroid_mean": 1950, "zero_crossing_rate_mean": 0.13, "hpss_harmonic_ratio": 0.74},
                "stems": {"stem_status": "missing_cached_stems"},
            },
        }, model)

        self.assertEqual(model["model_version"], AUDIO_MODEL_VERSION)
        self.assertTrue(model["audio_features"])
        self.assertEqual(prediction["song_type"], "vocal_led")

    def test_song_type_audio_features_summarize_array(self):
        import numpy as np

        sr = 16000
        t = np.linspace(0, 1.0, sr, endpoint=False)
        audio = (0.4 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)

        features = summarize_audio_array(audio, sr)

        self.assertEqual(features["feature_source"], AUDIO_FEATURE_SOURCE)
        self.assertAlmostEqual(features["analyzed_duration_s"], 1.0)
        self.assertGreater(features["rms_mean"], 0.0)
        self.assertGreaterEqual(features["onset_density_per_s"], 0.0)
        self.assertIsNotNone(features["hpss_harmonic_ratio"])
        self.assertGreaterEqual(features["hpss_harmonic_ratio"], 0.0)
        self.assertLessEqual(features["hpss_harmonic_ratio"], 1.0)

    def test_song_type_audio_features_reads_cached_stem_energy_ratio(self):
        import numpy as np

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            song_hash = "aa123456"
            for stem in ("vocals", "bass", "drums", "other"):
                path = stem_path(root, song_hash, stem)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("stub", encoding="utf-8")

            def fake_load(path, *, sample_rate, max_seconds):
                value = 2.0 if str(path).endswith("vocals.wav") else 1.0
                return np.ones(8, dtype=np.float32) * value, sample_rate

            with patch("backend.ai.song_type_audio_features.load_audio_mono", side_effect=fake_load):
                features = cached_stem_energy_features(root, song_hash)

        self.assertEqual(features["stem_status"], "cached_stems")
        self.assertAlmostEqual(features["stem_analyzed_duration_s"], 8.0 / 16000.0)
        self.assertAlmostEqual(features["vocal_stem_energy_ratio"], 4.0 / 7.0)
        self.assertAlmostEqual(features["vocal_vs_other_energy_ratio"], 4.0 / 5.0)

    def test_extract_rh_song_type_audio_features_cli_writes_rows_and_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue = root / "queue.jsonl"
            labels = root / "labels.jsonl"
            out = root / "features.jsonl"
            queue.write_text(
                "\n".join([
                    json.dumps({"survey_id": "heldout", "hash": "a", "path": "song-a.flac", "candidate_hint": "vocal_led", "duration_s": 123.4}),
                    json.dumps({"survey_id": "heldout", "hash": "b", "path": "song-b.flac", "candidate_hint": "solo_piano"}),
                ]) + "\n",
                encoding="utf-8",
            )
            labels.write_text(
                "\n".join([
                    json.dumps({"survey_id": "heldout", "song_hash": "a", "human_label": "vocal_led"}),
                    json.dumps({"survey_id": "heldout", "song_hash": "b", "human_label": "solo_piano"}),
                ]) + "\n",
                encoding="utf-8",
            )

            def fake_build(row, *, audio_path, data_dir, sample_rate, max_seconds, include_cached_stems):
                return {
                    "schema_version": 1,
                    "feature_source": AUDIO_FEATURE_SOURCE,
                    "song_hash": row["hash"],
                    "duration_s": row.get("duration_s"),
                    "resolved_label": row["resolved_label"],
                    "status": "ok",
                    "mix": {"onset_density_per_s": 1.5},
                    "stems": {"stem_status": "not_requested"},
                }

            with ExitStack() as stack:
                stack.enter_context(patch.object(sys, "argv", [
                    "extract_rh_song_type_audio_features.py",
                    "--queue", str(queue),
                    "--labels", str(labels),
                    "--out", str(out),
                    "--data-dir", str(root),
                    "--no-cached-stems",
                    "--force-output",
                ]))
                stack.enter_context(patch.object(song_type_audio_script, "_resolve_audio_path", lambda path: str(root / path)))
                stack.enter_context(patch.object(song_type_audio_script, "build_audio_feature_row", side_effect=fake_build))
                code = song_type_audio_script.main()

            self.assertEqual(code, 0)
            rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
            summary = json.loads(out.with_suffix(".summary.json").read_text(encoding="utf-8"))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["resolved_label"], "vocal_led")
            self.assertEqual(rows[0]["duration_s"], 123.4)
            self.assertEqual(summary["ok"], 2)
            self.assertEqual(summary["stem_status"]["not_requested"], 2)

    def test_extract_rh_song_type_audio_features_records_missing_audio_failure(self):
        rows = [{
            "survey_id": "heldout",
            "hash": "missing",
            "path": "missing.flac",
            "resolved_label": "vocal_led",
        }]

        result = song_type_audio_script.extract_feature_rows(
            rows,
            data_dir=Path("V:/data"),
            sample_rate=16000,
            max_seconds=1.0,
            include_cached_stems=False,
        )

        self.assertEqual(result[0]["status"], "failed")
        self.assertEqual(result[0]["song_hash"], "missing")
        self.assertIn("FileNotFoundError", result[0]["error"])

    def test_train_rh_song_type_classifier_cli_writes_model_and_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue = root / "queue.jsonl"
            labels = root / "labels.jsonl"
            features = root / "features.jsonl"
            model_out = root / "model.json"
            report_out = root / "report.json"
            queue.write_text(
                "\n".join([
                    json.dumps({"survey_id": "heldout", "hash": "a", "title": "ABBA Official", "candidate_hint": "vocal_led"}),
                    json.dumps({"survey_id": "heldout", "hash": "b", "title": "Chopin Piano", "candidate_hint": "solo_piano"}),
                    json.dumps({"survey_id": "heldout", "hash": "c", "title": "Backing Track", "candidate_hint": "no_clear_lead"}),
                ]) + "\n",
                encoding="utf-8",
            )
            labels.write_text(
                "\n".join([
                    json.dumps({"survey_id": "heldout", "song_hash": "a", "human_label": "vocal_led"}),
                    json.dumps({"survey_id": "heldout", "song_hash": "b", "human_label": "solo_piano"}),
                    json.dumps({"survey_id": "heldout", "song_hash": "c", "human_label": "no_clear_lead"}),
                ]) + "\n",
                encoding="utf-8",
            )
            features.write_text(
                "\n".join([
                    json.dumps({"survey_id": "heldout", "song_hash": "a", "status": "ok", "mix": {"spectral_centroid_mean": 1900}}),
                    json.dumps({"survey_id": "heldout", "song_hash": "b", "status": "ok", "mix": {"spectral_centroid_mean": 700}}),
                    json.dumps({"survey_id": "heldout", "song_hash": "c", "status": "ok", "mix": {"spectral_centroid_mean": 1100}}),
                ]) + "\n",
                encoding="utf-8",
            )

            with patch.object(sys, "argv", [
                "train_rh_song_type_classifier.py",
                "--queue", str(queue),
                "--labels", str(labels),
                "--audio-features", str(features),
                "--model-out", str(model_out),
                "--report-out", str(report_out),
                "--force-output",
            ]):
                code = song_type_train_script.main()

            self.assertEqual(code, 0)
            self.assertTrue(model_out.is_file())
            model = json.loads(model_out.read_text(encoding="utf-8"))
            report = json.loads(report_out.read_text(encoding="utf-8"))
            self.assertEqual(model["model_version"], AUDIO_MODEL_VERSION)
            self.assertEqual(report["total"], 3)
            self.assertEqual(report["audio_feature_rows"], 3)

    def test_diagnose_rh_song_type_classifier_writes_focus_rows(self):
        rows = [
            {
                "survey_id": "heldout",
                "hash": "v1",
                "title": "Ambiguous",
                "resolved_label": "vocal_led",
                "_audio_features": {"mix": {"spectral_centroid_mean": 800}, "stems": {"stem_status": "missing_cached_stems"}},
                "latest_label": {"review_note": "singer enters late"},
            },
            {
                "survey_id": "heldout",
                "hash": "i1",
                "title": "Ambiguous",
                "resolved_label": "instrumental_lead",
                "_audio_features": {"mix": {"spectral_centroid_mean": 850}, "stems": {"stem_status": "missing_cached_stems"}},
            },
            {
                "survey_id": "heldout",
                "hash": "v2",
                "title": "Bright Vocal",
                "resolved_label": "vocal_led",
                "_audio_features": {"mix": {"spectral_centroid_mean": 1900}, "stems": {"stem_status": "missing_cached_stems"}},
            },
        ]

        report = song_type_diagnostic_script.build_diagnostics(
            rows,
            focus_gold="vocal_led",
            focus_prediction="instrumental_lead",
            audio_features=True,
        )

        self.assertEqual(report["total"], 3)
        self.assertIn("vocal_led->instrumental_lead", report["errors_by_pair"])
        self.assertEqual(len(report["focus_rows"]), 1)
        self.assertEqual(report["focus_rows"][0]["song_hash"], "v1")
        self.assertEqual(report["focus_rows"][0]["review_note"], "singer enters late")

    def test_precompute_rh_song_type_stems_dry_run_plans_selected_rows(self):
        rows = [
            {"hash": "a1", "resolved_label": "vocal_led", "path": "a.flac"},
            {"hash": "b2", "resolved_label": "solo_piano", "path": "b.flac"},
        ]

        with patch.object(song_type_stems_script, "_resolve_audio_path", lambda path: str(Path("C:/missing") / path)):
            report_rows, summary = song_type_stems_script.run_precompute(
                song_type_stems_script.select_rows(rows, labels=["vocal_led"]),
                data_dir=Path("V:/data"),
                execute=False,
            )

        self.assertEqual(len(report_rows), 1)
        self.assertEqual(report_rows[0]["song_hash"], "a1")
        self.assertEqual(report_rows[0]["status"], "audio_not_found")
        self.assertEqual(summary["by_status"]["audio_not_found"], 1)

    def test_precompute_rh_song_type_stems_execute_uses_injected_cache_and_writes_report(self):
        class FakeStemCache:
            def ensure_stems(self, *, song_hash, audio_path, force=False):
                return StemCacheResult(
                    ok=True,
                    stems={"vocals": f"{song_hash}/vocals.wav", "other": f"{song_hash}/other.wav"},
                    cache_dir=f"cache/{song_hash}",
                    reused=False,
                )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "a.flac"
            audio.write_text("audio", encoding="utf-8")
            out = root / "stems.jsonl"
            rows = [{"hash": "a1", "resolved_label": "vocal_led", "path": "a.flac"}]

            with ExitStack() as stack:
                stack.enter_context(patch.object(song_type_stems_script, "_resolve_audio_path", lambda path: str(audio)))
                stack.enter_context(patch.object(song_type_stems_script, "_module_available", lambda name: True))
                report_rows, summary = song_type_stems_script.run_precompute(
                    rows,
                    data_dir=root,
                    execute=True,
                    stem_cache=FakeStemCache(),
                )
                written_summary = song_type_stems_script.write_report(report_rows, summary, out, force=True)

            saved = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(saved[0]["status"], "generated")
            self.assertEqual(saved[0]["stems"]["vocals"], "a1/vocals.wav")
            self.assertEqual(written_summary["ok"], 1)
            self.assertTrue(out.with_suffix(".summary.json").is_file())

    def test_vocal_gate_prefers_high_stem_ratio_and_blocks_short_audio(self):
        rows = [
            {
                "song_hash": "vocal",
                "resolved_label": "vocal_led",
                "duration_s": 120,
                "stems": {"vocal_stem_energy_ratio": 0.6},
            },
            {
                "song_hash": "inst",
                "resolved_label": "instrumental_lead",
                "duration_s": 180,
                "stems": {"vocal_stem_energy_ratio": 0.2},
            },
            {
                "song_hash": "short",
                "resolved_label": "unknown",
                "duration_s": 6,
                "stems": {"vocal_stem_energy_ratio": 0.99},
            },
        ]

        report = evaluate_vocal_gate(rows, vocal_ratio_threshold=0.3, min_duration_s=30)

        self.assertEqual(report["true_positive"], 1)
        self.assertEqual(report["false_positive"], 0)
        self.assertEqual(report["false_negative"], 0)
        self.assertEqual(report["precision"], 1.0)
        self.assertEqual(report["strict_precision"], 1.0)
        self.assertEqual(report["lenient_precision"], 1.0)
        self.assertEqual(report["unknown_total"], 1)
        self.assertEqual(report["rows"][2]["reason"], "duration_below_min")

        sweep = threshold_sweep(rows, thresholds=[0.25, 0.30, 0.35], min_duration_s=30)
        self.assertIn("0.300", sweep)
        self.assertEqual(sweep["0.300"]["precision"], 1.0)

    def test_vocal_gate_reports_strict_and_lenient_unknown_policy(self):
        rows = [
            {
                "song_hash": "vocal",
                "resolved_label": "vocal_led",
                "duration_s": 120,
                "stems": {"vocal_stem_energy_ratio": 0.6},
            },
            {
                "song_hash": "unknown",
                "resolved_label": "unknown",
                "duration_s": 120,
                "stems": {"vocal_stem_energy_ratio": 0.9},
            },
        ]

        report = evaluate_vocal_gate(rows, vocal_ratio_threshold=0.3, min_duration_s=30)

        self.assertEqual(report["unknown_total"], 1)
        self.assertEqual(report["unknown_predicted_vocal"], 1)
        self.assertEqual(report["strict_precision"], 0.5)
        self.assertEqual(report["lenient_precision"], 1.0)
        self.assertEqual(report["strict_recall"], 1.0)
        self.assertEqual(report["lenient_recall"], 1.0)

    def test_evaluate_rh_vocal_gate_cli_writes_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            features = root / "features.jsonl"
            out = root / "gate.json"
            features.write_text(
                "\n".join([
                    json.dumps({"song_hash": "v", "resolved_label": "vocal_led", "duration_s": 100, "stems": {"vocal_stem_energy_ratio": 0.7}}),
                    json.dumps({"song_hash": "i", "resolved_label": "instrumental_lead", "duration_s": 100, "stems": {"vocal_stem_energy_ratio": 0.0}}),
                ]) + "\n",
                encoding="utf-8",
            )

            with patch.object(sys, "argv", [
                "evaluate_rh_vocal_gate.py",
                "--features", str(features),
                "--out", str(out),
                "--force-output",
            ]):
                code = vocal_gate_script.main()

            report = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(code, 0)
            self.assertEqual(report["precision"], 1.0)
            self.assertEqual(report["recall"], 1.0)
            self.assertEqual(report["vocal_ratio_threshold"], 0.06)
            self.assertIn("0.060", report["threshold_sweep"])

    def test_melody_resolver_selects_vocal_candidate_when_gate_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            song_hash = "abcdef123456"
            baseline = finalize_melody_payload(
                {"path": "song.flac", "melody": [{"start": 0, "end": 2.0, "midi": 60}]},
                path="song.flac",
            )
            candidate = build_candidate_payload(
                song_hash=song_hash,
                path="song.flac",
                candidate_id=VOCAL_STEM_CREPE,
                melody=[{"start": 0, "end": 1.0, "midi": 64}, {"start": 1.0, "end": 2.0, "midi": 65}],
                stem="vocals",
                algorithm="htdemucs.vocals+torchcrepe.full",
                quality_flags=["shadow_candidate"],
            )
            write_candidate_cache(root, song_hash, VOCAL_STEM_CREPE, candidate)

            with patch(
                "backend.ai.melody_resolver.cached_stem_energy_features",
                return_value={
                    "stem_status": "cached_stems",
                    "missing_stems": [],
                    "stem_analyzed_duration_s": 120.0,
                    "vocal_stem_energy_ratio": 0.08,
                },
            ):
                resolved = MelodyResolver(root).resolve(baseline, song_hash=song_hash, path="song.flac")

            self.assertEqual(resolved["melody_source"]["id"], VOCAL_STEM_CREPE)
            self.assertEqual(resolved["melody_source"]["selected_by"], RESOLVER_VERSION)
            self.assertEqual(resolved["melody_source"]["song_type"], "vocal_led")
            self.assertEqual(resolved["melody_source"]["resolver_gate"]["vocal_stem_energy_ratio"], 0.08)
            self.assertTrue(selected_path(root, song_hash).is_file())

    def test_melody_resolver_falls_back_when_gate_fails_or_coverage_low(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            song_hash = "abcdef123456"
            baseline = finalize_melody_payload(
                {"path": "song.flac", "melody": [{"start": 0, "end": 10.0, "midi": 60}]},
                path="song.flac",
            )
            candidate = build_candidate_payload(
                song_hash=song_hash,
                path="song.flac",
                candidate_id=VOCAL_STEM_CREPE,
                melody=[{"start": 0, "end": 1.0, "midi": 64}],
                stem="vocals",
                algorithm="htdemucs.vocals+torchcrepe.full",
            )
            write_candidate_cache(root, song_hash, VOCAL_STEM_CREPE, candidate)

            with patch(
                "backend.ai.melody_resolver.cached_stem_energy_features",
                return_value={
                    "stem_status": "cached_stems",
                    "missing_stems": [],
                    "stem_analyzed_duration_s": 120.0,
                    "vocal_stem_energy_ratio": 0.05,
                },
            ):
                gate_failed = MelodyResolver(root).resolve(baseline, song_hash=song_hash, path="song.flac")

            self.assertEqual(gate_failed["melody_source"]["id"], FULL_MIX_PYIN)
            self.assertEqual(gate_failed["melody_source"]["selected_by"], "fallback")
            self.assertEqual(gate_failed["melody_source"]["fallback_reason"], "vocal_ratio_below_threshold")

            with patch(
                "backend.ai.melody_resolver.cached_stem_energy_features",
                return_value={
                    "stem_status": "cached_stems",
                    "missing_stems": [],
                    "stem_analyzed_duration_s": 120.0,
                    "vocal_stem_energy_ratio": 0.08,
                },
            ):
                low_coverage = MelodyResolver(root).resolve(baseline, song_hash=song_hash, path="song.flac")

            self.assertEqual(low_coverage["melody_source"]["id"], FULL_MIX_PYIN)
            self.assertEqual(low_coverage["melody_source"]["fallback_reason"], RETREAT_LOW_COVERAGE_FLAG)
            self.assertIn(RETREAT_LOW_COVERAGE_FLAG, low_coverage["quality_flags"])

    def test_melody_resolver_missing_candidate_does_not_read_stems(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = finalize_melody_payload(
                {"path": "song.flac", "melody": [{"start": 0, "end": 2.0, "midi": 60}]},
                path="song.flac",
            )

            with patch("backend.ai.melody_resolver.cached_stem_energy_features") as stem_features:
                resolved = MelodyResolver(root).resolve(baseline, song_hash="abcdef123456", path="song.flac")

            stem_features.assert_not_called()
            self.assertEqual(resolved["melody_source"]["id"], FULL_MIX_PYIN)
            self.assertEqual(resolved["melody_source"]["fallback_reason"], "vocal_candidate_missing")
            self.assertFalse(selected_path(root, "abcdef123456").is_file())

    def test_ai_api_melody_hash_rehash_uses_recomputed_hash_for_resolver(self):
        backend_dir = Path(__file__).resolve().parents[1]
        if str(backend_dir) not in sys.path:
            sys.path.insert(0, str(backend_dir))
        import ai_api

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_hash = "oldhash123456"
            melody_hash = "newhash123456"
            path = "renamed-song.flac"
            melodies = root / "melodies"
            chords = root / "chords" / old_hash[:2]
            melodies.mkdir(parents=True)
            chords.mkdir(parents=True)
            (chords / f"{old_hash}.json").write_text(json.dumps({"path": path, "bpm": 60}), encoding="utf-8")
            (melodies / f"{melody_hash}.json").write_text(
                json.dumps({"path": path, "melody": [{"start": 0, "end": 0.5, "midi": 60}, {"start": 0.9, "end": 1.4, "midi": 62}]}),
                encoding="utf-8",
            )

            with ExitStack() as stack:
                stack.enter_context(patch.object(ai_api, "DATA_DIR", root))
                stack.enter_context(patch.object(ai_api, "chord_file_for", lambda h: chords / f"{h}.json"))
                stack.enter_context(patch("chord_cache.song_hash", lambda p: melody_hash))
                context = stack.enter_context(patch(
                    "ai.melody_schema.melody_context_from_chord_cache",
                    side_effect=lambda h: {
                        "bpm": 60 if h == old_hash else 120,
                        "tempo_curve": None,
                        "time_signature": "4/4",
                    },
                ))
                resolver = stack.enter_context(patch.object(ai_api, "_maybe_resolve_rh_melody", side_effect=lambda payload, **kwargs: {**payload, "resolver_kwargs": kwargs}))
                result = ai_api.get_melody(path="", hash=old_hash)

            self.assertEqual(result["resolver_kwargs"]["song_hash"], melody_hash)
            self.assertAlmostEqual(result["melody"][0]["end"], 0.9)
            context.assert_any_call(old_hash)
            resolver.assert_called_once()

    def test_sample_rh_vocal_gate_validation_excludes_hashes_and_writes_queue(self):
        import backend.chord_cache as chord_cache

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            excluded_hash = chord_cache.song_hash("ABBA - Song.flac")
            exclude = root / "exclude.jsonl"
            exclude.write_text(json.dumps({"hash": excluded_hash}) + "\n", encoding="utf-8")
            tracks = [
                {"path": "ABBA - Song.flac", "title": "ABBA Official Music Video"},
                {"path": "Chopin - Nocturne.flac", "title": "Chopin Nocturne"},
                {"path": "Jazz/Take Five.flac", "artist": "Dave Brubeck"},
            ]
            out = root / "vocal_gate_queue.jsonl"

            excluded = vocal_gate_sample_script.load_excluded_hashes([exclude])
            candidates, stats = vocal_gate_sample_script.build_vocal_gate_candidates(
                tracks,
                exclude_hashes=excluded,
                base_url="http://example.test",
            )
            sample = vocal_gate_sample_script.sample_vocal_gate_queue(candidates, sample_size=10, seed=7)
            summary = vocal_gate_sample_script.write_vocal_gate_queue(
                out,
                sample,
                survey_id="validation",
                seed=7,
                candidate_stats=stats,
            )

            rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(stats["excluded"], 1)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["phase"], "phase0_5_vocal_gate_validation")
            self.assertEqual(rows[0]["label_options"], ["vocal_led", "not_vocal", "unknown"])
            self.assertTrue(rows[0]["player_url"].startswith("http://example.test/player?path="))
            self.assertEqual(summary["sample_size"], 2)

    def test_melody_phase0_review_data_dir_prefers_existing_local_then_production(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "melody_reviews").mkdir()

            self.assertEqual(resolve_review_data_dir(root), root)

    def test_melody_phase0_review_queue_helpers_read_latest_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            review_dir = root / "melody_reviews"
            review_dir.mkdir()
            (review_dir / "phase0_survey_queue.jsonl").write_text(
                json.dumps({"survey_id": "phase0", "hash": "abc", "path": "song.mp3"}) + "\n",
                encoding="utf-8",
            )
            (review_dir / "phase0_survey_queue.summary.json").write_text(
                json.dumps({"survey_id": "phase0"}),
                encoding="utf-8",
            )
            (review_dir / "phase0_tags.jsonl").write_text(
                "\n".join([
                    json.dumps({"survey_id": "phase0", "song_hash": "abc", "failure_tag": "wrong_octave", "created_at": "1"}),
                    json.dumps({"survey_id": "phase0", "song_hash": "abc", "failure_tag": "pyin_fine", "created_at": "2"}),
                ]) + "\n",
                encoding="utf-8",
            )

            rows, summary, queue_file = read_survey_queue(root)
            latest, tag_file = read_latest_review_tags(root, "phase0")

            self.assertEqual(queue_file.name, "phase0_survey_queue.jsonl")
            self.assertEqual(tag_file.name, "phase0_tags.jsonl")
            self.assertEqual(summary["survey_id"], "phase0")
            self.assertEqual(rows[0]["hash"], "abc")
            self.assertEqual(latest["phase0|abc"]["failure_tag"], "pyin_fine")

    def test_melody_phase0_survey_write_queue_refuses_overwrite_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "queue.jsonl"
            out.write_text("existing\n", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                write_survey_queue(
                    out,
                    [],
                    survey_id="phase0",
                    seed=1,
                    candidate_stats={},
                    force=False,
                )

    def test_sample_melody_phase0_default_chords_root_prefers_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"LIVECHORD_CHORDS_DIR": tmp}):
                self.assertEqual(survey_script._default_chords_root(), tmp)

    def test_sample_melody_phase0_default_chords_root_warns_before_dev_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            fallback = Path(tmp) / "repo" / "data" / "chords"
            fallback.mkdir(parents=True)
            with ExitStack() as stack:
                stack.enter_context(patch.dict(os.environ, {}, clear=True))
                stack.enter_context(patch.object(survey_script, "REPO_ROOT", Path(tmp) / "repo"))
                stack.enter_context(patch.object(survey_script, "DEFAULT_PRODUCTION_CHORDS_ROOT", Path(tmp) / "missing"))
                with patch("sys.stderr") as stderr:
                    result = survey_script._default_chords_root()

            self.assertEqual(result, str(fallback))
            self.assertTrue(stderr.write.called)

    def test_finalize_melody_payload_accepts_bare_list(self):
        result = finalize_melody_payload(
            [{"start": 0.0, "end": 0.25, "note": "C4", "midi": 60}],
            path="song.mp3",
            bpm=120,
        )

        self.assertEqual(result["schema_version"], MELODY_EVENT_SCHEMA_VERSION)
        self.assertEqual(result["path"], "song.mp3")
        self.assertEqual(result["melody_source"]["selected_by"], "legacy_primary")
        self.assertEqual(result["melody_source"]["cache_version"], "rhmelody-v2")
        self.assertEqual(result["melody_source"]["phase"], "phase0")
        self.assertIn("fallback_full_mix", result["quality_flags"])
        self.assertEqual(result["melody_stats"]["midi_median"], 60.0)
        self.assertEqual(result["melody_stats"]["density_when_active_per_s"], 4.0)
        self.assertEqual(result["melody"][0]["pitch"], 60)
        self.assertEqual(result["melody"][0]["voice_lane"], MELODY_VOICE_LANE)

    def test_finalize_melody_payload_preserves_non_fallback_source(self):
        result = finalize_melody_payload(
            {
                "melody_source": {
                    "id": "midi_aligned",
                    "algorithm": "dtw_midi_align",
                    "selected_by": "resolver",
                },
                "quality_flags": ["alignment_high"],
                "melody": [{"start": 0.0, "end": 0.5, "note": "E4", "midi": 64}],
            },
            bpm=120,
        )

        self.assertEqual(result["melody_source"]["id"], "midi_aligned")
        self.assertIn("alignment_high", result["quality_flags"])
        self.assertNotIn("fallback_full_mix", result["quality_flags"])

    def test_melody_review_taxonomy_includes_audio_quality(self):
        taxonomy = melody_review_taxonomy()

        self.assertIn("audio_quality", taxonomy["primary_tags"])
        self.assertIn("duet_alternating", taxonomy["primary_tags"])
        self.assertIn("solo_piano_polyphonic_collapse", taxonomy["primary_tags"])
        self.assertIn("audio_quality_secondary", taxonomy["secondary_flags"])
        self.assertNotIn("audio_quality", taxonomy["secondary_flags"])
        self.assertIn("quantization_jitter", taxonomy["secondary_flags"])
        self.assertIn("all reviewed primary tags in the denominator", taxonomy["review_rule"])

    def test_melody_context_from_chord_cache_reads_bpm_curve_and_meter(self):
        import backend.chord_cache as chord_cache

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chord_root = root / "chords"
            song_hash = "ab1234567890"
            song_dir = chord_root / song_hash[:2]
            song_dir.mkdir(parents=True)
            (song_dir / f"{song_hash}.json").write_text(
                json.dumps({
                    "bpm": 85,
                    "tempo_curve": [{"t": 0, "bpm": 85}, {"t": 10, "bpm": 78}],
                    "time_signature": "6/8",
                }),
                encoding="utf-8",
            )

            patch_targets = [chord_cache]
            try:
                import chord_cache as top_level_chord_cache
                if top_level_chord_cache is not chord_cache:
                    patch_targets.append(top_level_chord_cache)
            except ImportError:
                pass

            with ExitStack() as stack:
                for target in patch_targets:
                    stack.enter_context(patch.object(target, "CHORDS_DIR", chord_root))
                    stack.enter_context(patch.object(target, "DEMO_CHORDS_DIR", root / "demo" / "chords"))
                context = melody_context_from_chord_cache(song_hash)

        self.assertEqual(context["bpm"], 85.0)
        self.assertEqual(context["time_signature"], "6/8")
        self.assertEqual(context["tempo_curve"][1]["bpm"], 78)

    def test_atomic_write_json_replaces_cache_without_tmp_leftover(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "song.json"
            path.write_text('{"old": true}', encoding="utf-8")

            atomic_write_json(path, {"schema_version": MELODY_EVENT_SCHEMA_VERSION})

            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                {"schema_version": MELODY_EVENT_SCHEMA_VERSION},
            )
            self.assertFalse(path.with_suffix(path.suffix + ".tmp").exists())

    def test_melody_candidate_cache_uses_sharded_layout_and_payload_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = build_candidate_payload(
                song_hash="abcdef123456",
                path="POP/song.mp3",
                candidate_id=VOCAL_STEM_CREPE,
                melody=[{"start": 0.0, "end": 0.5, "midi": 64, "confidence": 0.8}],
                stem="vocals",
                algorithm="htdemucs.vocals+torchcrepe.full",
                quality_flags=["shadow_candidate"],
                bpm=120,
            )

            out = write_candidate_cache(root, "abcdef123456", VOCAL_STEM_CREPE, payload)
            loaded = read_candidate_cache(root, "abcdef123456", VOCAL_STEM_CREPE)

            self.assertEqual(out, root / "melody_candidates" / "ab" / "abcdef123456" / "vocal_stem_crepe.json")
            self.assertEqual(candidate_path(root, "abcdef123456", VOCAL_STEM_CREPE), out)
            self.assertEqual(loaded["melody_source"]["id"], VOCAL_STEM_CREPE)
            self.assertEqual(loaded["melody_source"]["selected_by"], "shadow_candidate")
            self.assertEqual(loaded["melody_source"]["cache_version"], MELODY_CANDIDATE_CACHE_VERSION)
            self.assertEqual(loaded["melody"][0]["voice_lane"], MELODY_VOICE_LANE)

    def test_stem_cache_reuses_existing_stems_and_copies_new_outputs(self):
        class FakeSeparator:
            def __init__(self, source_dir):
                self.source_dir = source_dir
                self.calls = 0

            def separate(self, _audio_path):
                self.calls += 1
                return {
                    "vocals": str(self.source_dir / "vocals.wav"),
                    "other": str(self.source_dir / "other.wav"),
                }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "src"
            src.mkdir()
            (src / "vocals.wav").write_bytes(b"vocals")
            (src / "other.wav").write_bytes(b"other")
            audio = root / "song.wav"
            audio.write_bytes(b"audio")
            fake = FakeSeparator(src)
            cache = StemCache(root, separator_factory=lambda: fake)

            first = cache.ensure_stems(song_hash="abcdef123456", audio_path=str(audio))
            second = cache.ensure_stems(song_hash="abcdef123456", audio_path=str(audio))

            self.assertTrue(first.ok)
            self.assertFalse(first.reused)
            self.assertEqual(Path(first.stems["vocals"]), stem_path(root, "abcdef123456", "vocals"))
            self.assertTrue(second.ok)
            self.assertTrue(second.reused)
            self.assertEqual(fake.calls, 1)

    def test_stem_cache_partial_cache_does_not_report_reused_ok(self):
        class FakeSeparator:
            def __init__(self, source_dir):
                self.source_dir = source_dir
                self.calls = 0

            def separate(self, _audio_path):
                self.calls += 1
                return {
                    "vocals": str(self.source_dir / "fresh_vocals.wav"),
                    "other": str(self.source_dir / "fresh_other.wav"),
                }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stem_path(root, "abcdef123456", "vocals").parent.mkdir(parents=True)
            stem_path(root, "abcdef123456", "vocals").write_bytes(b"stale-vocals")
            src = root / "src"
            src.mkdir()
            (src / "fresh_vocals.wav").write_bytes(b"fresh-vocals")
            (src / "fresh_other.wav").write_bytes(b"fresh-other")
            audio = root / "song.wav"
            audio.write_bytes(b"audio")
            fake = FakeSeparator(src)

            result = StemCache(root, separator_factory=lambda: fake).ensure_stems(
                song_hash="abcdef123456",
                audio_path=str(audio),
            )

            self.assertTrue(result.ok)
            self.assertFalse(result.reused)
            self.assertEqual(fake.calls, 1)
            self.assertIn("other", result.stems)

    def test_piano_rh_selector_prefers_smooth_upper_voice_over_bass(self):
        notes = [
            {"start": 0.0, "end": 0.5, "midi": 40, "velocity": 96},
            {"start": 0.0, "end": 0.5, "midi": 64, "velocity": 70},
            {"start": 0.5, "end": 1.0, "midi": 43, "velocity": 96},
            {"start": 0.5, "end": 1.0, "midi": 65, "velocity": 72},
            {"start": 1.0, "end": 1.5, "midi": 47, "velocity": 96},
            {"start": 1.0, "end": 1.5, "midi": 67, "velocity": 74},
        ]

        selected = select_right_hand_melody(notes, key="C", bpm=120)

        self.assertEqual([event["midi"] for event in selected], [64, 65, 67])
        self.assertTrue(all(event["voice_lane"] == MELODY_VOICE_LANE for event in selected))

    def test_piano_rh_selector_ignores_confidence_as_velocity_fallback(self):
        notes = [
            {"start": 0.0, "end": 0.5, "midi": 64, "confidence": 0.01},
            {"start": 0.5, "end": 1.0, "midi": 65, "confidence": 0.01},
        ]

        selected = select_right_hand_melody(notes, key="A minor", bpm=120)

        self.assertEqual([event["midi"] for event in selected], [64, 65])
        self.assertTrue(all(event["confidence"] > 1.55 for event in selected))

    def test_piano_rh_selector_handles_flat_named_minor_keys(self):
        # Bb minor (= Db major). Db major scale: Db Eb F Gb Ab Bb C → pcs {1,3,5,6,8,10,0}.
        # Pick midi values inside the scale (Bb=70 in C5, Db=73, F=77) so the key prior
        # should fire positive. If Bb-minor falls back to C major like the pre-fix code,
        # all three notes would be out-of-key (pcs 10,1,5 vs C major {0,2,4,5,7,9,11}
        # → only F=5 in-key), and the key_score deltas would cut the total score.
        notes = [
            {"start": 0.0, "end": 0.5, "midi": 70, "velocity": 64},  # Bb4
            {"start": 0.5, "end": 1.0, "midi": 73, "velocity": 64},  # Db5
            {"start": 1.0, "end": 1.5, "midi": 77, "velocity": 64},  # F5
        ]

        in_key = select_right_hand_melody(notes, key="Bb minor", bpm=120)
        fallback = select_right_hand_melody(notes, key="C", bpm=120)

        self.assertEqual([event["midi"] for event in in_key], [70, 73, 77])
        # Bb minor → Db major scale: Bb (pc 10), Db (pc 1), F (pc 5) all in-key.
        # C major fallback: only F is in scale; Bb and Db each take a -0.08 penalty
        # instead of +0.16, so 2 notes × 0.24 delta = 0.48 higher total for in-key.
        in_key_total = sum(event["confidence"] for event in in_key)
        fallback_total = sum(event["confidence"] for event in fallback)
        self.assertGreater(in_key_total - fallback_total, 0.4)

    def test_piano_rh_selector_handles_eb_minor_via_enharmonic(self):
        # Eb minor (= Gb major). Reference smoke: F (pc 5) is the 7th of Gb major,
        # in-key; if the lookup fell through to C major, it would still be in-key
        # (C major has F=5), masking the bug. So use Cb (pc 11 → midi 71/83) which
        # is the 4th of Gb major BUT out-of-key for C major (C major has B=11 wait
        # actually 11 IS in C major). Pick Gb itself: midi 78 = Gb5, pc 6. In Gb
        # major scale ✓, NOT in C major (C major has F=5 and G=7, not F#/Gb=6).
        notes = [
            {"start": 0.0, "end": 0.5, "midi": 78, "velocity": 64},  # Gb5
            {"start": 0.5, "end": 1.0, "midi": 75, "velocity": 64},  # Eb5
        ]

        in_key = select_right_hand_melody(notes, key="Eb minor", bpm=120)
        fallback = select_right_hand_melody(notes, key="C", bpm=120)

        self.assertEqual([event["midi"] for event in in_key], [78, 75])
        in_key_total = sum(event["confidence"] for event in in_key)
        fallback_total = sum(event["confidence"] for event in fallback)
        self.assertGreater(in_key_total - fallback_total, 0.2)

    def test_piano_rh_selector_preserves_flat_named_major_keys(self):
        # Regression guard: a previous draft normalized flats to sharps globally,
        # which broke Bb major / Eb major lookups in KEY_MAJOR_PCS (it indexes by
        # flat spelling for these). This test pins Bb major behavior.
        notes = [
            {"start": 0.0, "end": 0.5, "midi": 70, "velocity": 64},  # Bb4
            {"start": 0.5, "end": 1.0, "midi": 74, "velocity": 64},  # D5
        ]

        in_key = select_right_hand_melody(notes, key="Bb major", bpm=120)
        out_of_key = select_right_hand_melody(notes, key="E major", bpm=120)

        self.assertEqual([event["midi"] for event in in_key], [70, 74])
        in_key_total = sum(event["confidence"] for event in in_key)
        out_total = sum(event["confidence"] for event in out_of_key)
        self.assertGreater(in_key_total, out_total)

    def test_piano_candidate_payload_accepts_selector_output(self):
        selected = select_right_hand_melody(
            [
                {"start": 0.0, "end": 0.5, "midi": 52, "velocity": 90},
                {"start": 0.0, "end": 0.5, "midi": 72, "velocity": 65},
            ],
            key="C",
            bpm=120,
        )

        payload = build_candidate_payload(
            song_hash="fedcba654321",
            path="Classics/piano.flac",
            candidate_id=SOLO_PIANO_POLYPHONIC,
            melody=selected,
            stem="polyphonic_midi",
            algorithm="magenta.onsets_frames+skyline_temperley",
            bpm=120,
            song_type="solo_piano",
        )

        self.assertEqual(payload["melody_source"]["id"], SOLO_PIANO_POLYPHONIC)
        self.assertEqual(payload["melody_source"]["song_type"], "solo_piano")
        self.assertEqual(payload["melody"][0]["midi"], 72)

    def test_vocal_crepe_reports_missing_dependency_without_writing_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stem = root / "vocals.wav"
            stem.write_bytes(b"not really wav")
            extractor = VocalStemCrepeExtractor(data_dir=root)
            with patch.object(extractor, "_extract_events", side_effect=ImportError(name="torchcrepe")):
                result = extractor.extract_to_cache(
                    song_hash="abcdef123456",
                    path="song.wav",
                    vocal_stem_path=str(stem),
                )

            self.assertFalse(result.ok)
            self.assertEqual(result.error, "missing_dependency:torchcrepe")
            self.assertFalse(candidate_path(root, "abcdef123456", VOCAL_STEM_CREPE).exists())

    def test_vocal_crepe_general_extraction_failure_label_is_not_crepe_specific(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stem = root / "vocals.wav"
            stem.write_bytes(b"not really wav")
            extractor = VocalStemCrepeExtractor(data_dir=root)
            with patch.object(extractor, "_extract_events", side_effect=RuntimeError("bad audio")):
                result = extractor.extract_to_cache(
                    song_hash="abcdef123456",
                    path="song.wav",
                    vocal_stem_path=str(stem),
                )

            self.assertFalse(result.ok)
            self.assertEqual(result.error, "extraction_failed:RuntimeError:bad audio")
            self.assertFalse(candidate_path(root, "abcdef123456", VOCAL_STEM_CREPE).exists())

    def test_shadow_generator_wraps_legacy_full_mix_pyin_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "melodies").mkdir()
            (root / "melodies" / "abcdef123456.json").write_text(
                json.dumps({
                    "melody": [{"start": 0.0, "end": 0.5, "midi": 64, "confidence": 0.7}],
                }),
                encoding="utf-8",
            )

            result = generate_shadow_candidates(
                data_dir=root,
                song_hash="abcdef123456",
                path="POP/song.mp3",
                candidates=[FULL_MIX_PYIN],
            )
            payload = read_candidate_cache(root, "abcdef123456", FULL_MIX_PYIN)

            self.assertTrue(result.ok)
            self.assertEqual(result.results[0].status, "generated")
            self.assertEqual(payload["melody_source"]["id"], FULL_MIX_PYIN)
            self.assertEqual(payload["candidate"]["build_info"]["source"], "legacy_cache")
            self.assertEqual(payload["melody"][0]["voice_lane"], MELODY_VOICE_LANE)

            second = generate_shadow_candidates(
                data_dir=root,
                song_hash="abcdef123456",
                path="POP/song.mp3",
                candidates=[FULL_MIX_PYIN],
            )

            self.assertTrue(second.ok)
            self.assertEqual(second.results[0].status, "cached")

    def test_shadow_generator_vocal_candidate_uses_stem_cache_and_extractor(self):
        class FakeStemCache:
            def ensure_stems(self, **_kwargs):
                return StemCacheResult(
                    ok=True,
                    stems={"vocals": str(root / "vocals.wav"), "other": str(root / "other.wav")},
                    cache_dir=str(root / "stems"),
                    reused=True,
                )

        class FakeVocalExtractor:
            def extract_to_cache(self, **kwargs):
                payload = build_candidate_payload(
                    song_hash=kwargs["song_hash"],
                    path=kwargs["path"],
                    candidate_id=VOCAL_STEM_CREPE,
                    melody=[{"start": 0.0, "end": 0.5, "midi": 67}],
                    stem="vocals",
                    algorithm="fake-crepe",
                    bpm=120,
                )
                out = write_candidate_cache(root, kwargs["song_hash"], VOCAL_STEM_CREPE, payload)
                return VocalCrepeResult(ok=True, payload=payload, cache_file=str(out))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "vocals.wav").write_bytes(b"v")
            (root / "other.wav").write_bytes(b"o")

            result = generate_shadow_candidates(
                data_dir=root,
                song_hash="abcdef123456",
                path="POP/song.mp3",
                audio_path=str(root / "song.wav"),
                candidates=[VOCAL_STEM_CREPE],
                stem_cache=FakeStemCache(),
                vocal_extractor=FakeVocalExtractor(),
            )

            self.assertTrue(result.ok)
            self.assertEqual(result.results[0].candidate_id, VOCAL_STEM_CREPE)
            self.assertEqual(result.results[0].details["reused"], True)
            self.assertTrue(candidate_path(root, "abcdef123456", VOCAL_STEM_CREPE).exists())

    def test_shadow_generator_instrument_lead_uses_other_stem_and_extractor(self):
        class FakeStemCache:
            def ensure_stems(self, **_kwargs):
                return StemCacheResult(
                    ok=True,
                    stems={"vocals": str(root / "vocals.wav"), "other": str(root / "other.wav")},
                    cache_dir=str(root / "stems"),
                    reused=True,
                )

        class FakeCrepeExtractor:
            def extract_stem_to_cache(self, **kwargs):
                payload = build_candidate_payload(
                    song_hash=kwargs["song_hash"],
                    path=kwargs["path"],
                    candidate_id=kwargs["candidate_id"],
                    melody=[{"start": 0.0, "end": 0.5, "midi": 72}],
                    stem=kwargs["stem_label"],
                    algorithm=kwargs["algorithm"],
                    song_type=kwargs["song_type"],
                    bpm=120,
                )
                out = write_candidate_cache(root, kwargs["song_hash"], kwargs["candidate_id"], payload)
                return VocalCrepeResult(ok=True, payload=payload, cache_file=str(out))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "vocals.wav").write_bytes(b"v")
            (root / "other.wav").write_bytes(b"o")

            result = generate_shadow_candidates(
                data_dir=root,
                song_hash="abcdef123456",
                path="Jazz/lead.flac",
                audio_path=str(root / "song.wav"),
                candidates=[INSTRUMENT_LEAD],
                stem_cache=FakeStemCache(),
                vocal_extractor=FakeCrepeExtractor(),
            )
            payload = read_candidate_cache(root, "abcdef123456", INSTRUMENT_LEAD)

            self.assertTrue(result.ok)
            self.assertEqual(result.results[0].candidate_id, INSTRUMENT_LEAD)
            self.assertEqual(payload["melody_source"]["stem"], "other")
            self.assertEqual(payload["melody_source"]["song_type"], "instrumental")
            self.assertEqual(payload["melody"][0]["midi"], 72)

    def test_shadow_generator_solo_piano_from_polyphonic_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes_file = root / "poly.json"
            notes_file.write_text(
                json.dumps([
                    {"start": 0.0, "end": 0.5, "midi": 40, "velocity": 100},
                    {"start": 0.0, "end": 0.5, "midi": 72, "velocity": 64},
                    {"start": 0.5, "end": 1.0, "midi": 43, "velocity": 100},
                    {"start": 0.5, "end": 1.0, "midi": 74, "velocity": 64},
                ]),
                encoding="utf-8",
            )

            result = generate_shadow_candidates(
                data_dir=root,
                song_hash="abcdef123456",
                path="Classics/piano.flac",
                candidates=[SOLO_PIANO_POLYPHONIC],
                polyphonic_json=str(notes_file),
                key="C",
            )
            payload = read_candidate_cache(root, "abcdef123456", SOLO_PIANO_POLYPHONIC)

            self.assertTrue(result.ok)
            self.assertEqual(result.results[0].details["input_notes"], 4)
            self.assertEqual([event["midi"] for event in payload["melody"]], [72, 74])
            self.assertEqual(payload["melody_source"]["id"], SOLO_PIANO_POLYPHONIC)

    def test_shadow_generator_solo_piano_uses_chord_cache_key_when_cli_key_omitted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            song_hash = "abcdef123456"
            chord_dir = root / "chords" / song_hash[:2]
            chord_dir.mkdir(parents=True)
            (chord_dir / f"{song_hash}.json").write_text(
                json.dumps({"path": "Classics/piano.flac", "key": "Bb minor"}),
                encoding="utf-8",
            )
            notes_file = root / "poly.json"
            notes_file.write_text(
                json.dumps([{"start": 0.0, "end": 0.5, "midi": 70, "velocity": 80}]),
                encoding="utf-8",
            )

            result = generate_shadow_candidates(
                data_dir=root,
                song_hash=song_hash,
                candidates=[SOLO_PIANO_POLYPHONIC],
                polyphonic_json=str(notes_file),
            )
            payload = read_candidate_cache(root, song_hash, SOLO_PIANO_POLYPHONIC)

            self.assertTrue(result.ok)
            self.assertEqual(payload["candidate"]["build_info"]["key"], "Bb minor")

    def test_shadow_generator_solo_piano_explicit_key_overrides_chord_cache_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            song_hash = "abcdef123456"
            chord_dir = root / "chords" / song_hash[:2]
            chord_dir.mkdir(parents=True)
            (chord_dir / f"{song_hash}.json").write_text(
                json.dumps({"path": "Classics/piano.flac", "key": "Bb minor"}),
                encoding="utf-8",
            )
            notes_file = root / "poly.json"
            notes_file.write_text(
                json.dumps([{"start": 0.0, "end": 0.5, "midi": 70, "velocity": 80}]),
                encoding="utf-8",
            )

            result = generate_shadow_candidates(
                data_dir=root,
                song_hash=song_hash,
                candidates=[SOLO_PIANO_POLYPHONIC],
                polyphonic_json=str(notes_file),
                key="C",
            )
            payload = read_candidate_cache(root, song_hash, SOLO_PIANO_POLYPHONIC)

            self.assertTrue(result.ok)
            self.assertEqual(payload["candidate"]["build_info"]["key"], "C")

    def test_shadow_generator_solo_piano_requires_polyphonic_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            result = generate_shadow_candidates(
                data_dir=root,
                song_hash="abcdef123456",
                path="Classics/piano.flac",
                candidates=[SOLO_PIANO_POLYPHONIC],
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.results[0].candidate_id, SOLO_PIANO_POLYPHONIC)
            self.assertEqual(result.results[0].status, "polyphonic_load_failed")
            self.assertTrue(result.results[0].error.startswith("polyphonic_load_failed:"))
            self.assertFalse(candidate_path(root, "abcdef123456", SOLO_PIANO_POLYPHONIC).exists())

    def test_shadow_smoke_queue_defaults_candidates_by_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue = root / "queue.jsonl"
            queue.write_text(
                "\n".join([
                    json.dumps({"hash": "vocal123", "group": "vocal"}),
                    json.dumps({"hash": "piano123", "group": "solo_piano", "polyphonic_json": str(root / "notes.json")}),
                    json.dumps({"hash": "inst123", "group": "instrumental"}),
                ]) + "\n",
                encoding="utf-8",
            )

            items = read_smoke_queue(queue)

            self.assertEqual(items[0].resolved_candidates(), [FULL_MIX_PYIN, VOCAL_STEM_CREPE])
            self.assertEqual(items[1].resolved_candidates(), [FULL_MIX_PYIN, SOLO_PIANO_POLYPHONIC])
            self.assertEqual(items[2].resolved_candidates(), [FULL_MIX_PYIN, INSTRUMENT_LEAD])

    def test_shadow_smoke_queue_accepts_utf8_bom(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue = root / "queue.jsonl"
            queue.write_bytes(b"\xef\xbb\xbf" + json.dumps({"hash": "vocal123", "group": "vocal"}).encode("utf-8") + b"\n")

            items = read_smoke_queue(queue)

            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].hash, "vocal123")

    def test_shadow_smoke_runner_invokes_generator_and_writes_report(self):
        calls = []

        def fake_generator(**kwargs):
            calls.append(kwargs)
            candidate_id = kwargs["candidates"][0]
            return ShadowGenerationResult(
                ok=True,
                song_hash=kwargs.get("song_hash") or "hash-from-path",
                path=kwargs.get("path") or "",
                audio_path=kwargs.get("audio_path") or "",
                results=[
                    ShadowCandidateResult(
                        candidate_id=candidate_id,
                        ok=True,
                        status="generated",
                        cache_file=f"/tmp/{candidate_id}.json",
                    )
                ],
            )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            items = [
                SmokeQueueItem(hash="abc123", group="vocal", candidates=[VOCAL_STEM_CREPE]),
                SmokeQueueItem(path="Classics/piano.flac", group="solo_piano", candidates=[SOLO_PIANO_POLYPHONIC], polyphonic_json="notes.json"),
            ]

            rows, summary = run_smoke_queue(items, data_dir=root, generator=fake_generator)
            out = root / "reviews" / "smoke.jsonl"
            written = write_smoke_report(rows, summary, out)

            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0]["candidates"], [VOCAL_STEM_CREPE])
            self.assertEqual(calls[1]["polyphonic_json"], "notes.json")
            self.assertEqual(summary["survey_id"], SMOKE_SURVEY_ID)
            self.assertEqual(summary["ok"], 2)
            self.assertEqual(summary["by_candidate"][VOCAL_STEM_CREPE]["by_status"]["generated"], 1)
            self.assertTrue(out.is_file())
            self.assertTrue(Path(written["summary_output"]).is_file())
            loaded_rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(loaded_rows[0]["survey_id"], SMOKE_SURVEY_ID)

    def test_shadow_smoke_runner_keeps_going_after_row_exception(self):
        def fake_generator(**kwargs):
            if kwargs.get("song_hash") == "boom":
                raise RuntimeError("audio_missing")
            return ShadowGenerationResult(
                ok=True,
                song_hash=kwargs.get("song_hash") or "",
                path=kwargs.get("path") or "",
                audio_path="",
                results=[
                    ShadowCandidateResult(
                        candidate_id=kwargs["candidates"][0],
                        ok=True,
                        status="generated",
                    )
                ],
            )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows, summary = run_smoke_queue(
                [
                    SmokeQueueItem(hash="ok1", group="vocal", candidates=[FULL_MIX_PYIN]),
                    SmokeQueueItem(hash="boom", group="vocal", candidates=[FULL_MIX_PYIN]),
                    SmokeQueueItem(hash="ok2", group="vocal", candidates=[FULL_MIX_PYIN]),
                ],
                data_dir=root,
                generator=fake_generator,
            )

            self.assertEqual(len(rows), 3)
            self.assertTrue(rows[0]["result"]["ok"])
            self.assertFalse(rows[1]["result"]["ok"])
            self.assertTrue(rows[2]["result"]["ok"])
            self.assertEqual(rows[1]["resolved_candidates"], [])
            self.assertIn("RuntimeError:audio_missing", rows[1]["result"]["error"])
            self.assertEqual(summary["ok"], 2)
            self.assertEqual(summary["failed"], 1)

    def test_shadow_smoke_dry_run_surfaces_polyphonic_path_errors_and_unknown_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows, summary = run_smoke_queue(
                [
                    SmokeQueueItem(
                        hash="piano1",
                        group="solo_piano",
                        candidates=[SOLO_PIANO_POLYPHONIC],
                        polyphonic_json=str(root / "missing-notes.json"),
                    ),
                    SmokeQueueItem(hash="typo1", group="balad"),
                ],
                data_dir=root,
                dry_run=True,
            )

            self.assertFalse(rows[0]["result"]["ok"])
            self.assertEqual(rows[0]["result"]["results"][0]["status"], "polyphonic_json_missing")
            self.assertEqual(rows[1]["resolved_candidates"], [FULL_MIX_PYIN])
            self.assertIn("unknown_group:balad;defaulting_to_unknown", rows[1]["warnings"])
            self.assertEqual(summary["failed"], 1)
            self.assertEqual(summary["warnings"]["unknown_group:balad;defaulting_to_unknown"], 1)
            self.assertEqual(summary["by_candidate"][SOLO_PIANO_POLYPHONIC]["by_status"]["polyphonic_json_missing"], 1)

    def test_shadow_smoke_dry_run_surfaces_vocal_dependency_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("backend.ai.melody_shadow_smoke._module_available", side_effect=lambda name: False):
                rows, summary = run_smoke_queue(
                    [SmokeQueueItem(hash="vocal1", group="vocal", candidates=[VOCAL_STEM_CREPE])],
                    data_dir=root,
                    dry_run=True,
                )

            self.assertFalse(rows[0]["result"]["ok"])
            self.assertEqual(rows[0]["result"]["results"][0]["status"], "dependency_missing")
            self.assertEqual(rows[0]["result"]["results"][0]["details"]["path"], "demucs,torchcrepe")
            self.assertEqual(summary["by_candidate"][VOCAL_STEM_CREPE]["by_status"]["dependency_missing"], 1)

            with patch("backend.ai.melody_shadow_smoke._module_available", side_effect=lambda name: False):
                rows, summary = run_smoke_queue(
                    [SmokeQueueItem(hash="inst1", group="instrumental", candidates=[INSTRUMENT_LEAD])],
                    data_dir=root,
                    dry_run=True,
                )
            self.assertFalse(rows[0]["result"]["ok"])
            self.assertEqual(rows[0]["result"]["results"][0]["status"], "dependency_missing")
            self.assertEqual(summary["by_candidate"][INSTRUMENT_LEAD]["by_status"]["dependency_missing"], 1)

            with patch("backend.ai.melody_shadow_smoke._module_available", side_effect=lambda name: name == "torchcrepe"):
                rows, _summary = run_smoke_queue(
                    [SmokeQueueItem(hash="vocal2", group="vocal", candidates=[VOCAL_STEM_CREPE])],
                    data_dir=root,
                    dry_run=True,
                )
            self.assertEqual(rows[0]["result"]["results"][0]["details"]["path"], "demucs")

    def test_shadow_smoke_report_refuses_overwrite_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "smoke.jsonl"
            out.write_text("existing\n", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                write_smoke_report([], {"survey_id": SMOKE_SURVEY_ID}, out, force=False)

    def test_basic_pitch_polyphonic_events_to_notes(self):
        notes = polyphonic_script.basic_pitch_events_to_notes([
            (0.1, 0.5, 60.2, 0.5, []),
            (0.1, 0.4, 72.0, 1.2, []),
            ("bad", 1.0, 64, 0.1, []),
        ])

        self.assertEqual([note["midi"] for note in notes], [72, 60])
        self.assertEqual(notes[0]["velocity"], 127)
        self.assertEqual(notes[1]["velocity"], 64)
        self.assertEqual(notes[1]["confidence"], 0.5)

    def test_basic_pitch_polyphonic_batch_writes_queue_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "song.flac"
            audio.write_text("audio", encoding="utf-8")
            out = root / "polyphonic" / "aa" / "aa1111111111.json"
            rows = [{
                "sample_order": 13,
                "group": "solo_piano",
                "hash": "aa1111111111",
                "path": "song.flac",
                "polyphonic_json": str(out),
            }]

            summary = polyphonic_script.run_batch(
                rows,
                resolve_path=lambda path: str(audio),
                transcribe=lambda path: [{"start": 0.0, "end": 0.5, "midi": 72, "velocity": 90}],
            )

            self.assertEqual(summary["ok"], 1)
            self.assertEqual(summary["failed"], 0)
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(payload["source"], "basic_pitch_polyphonic")
            self.assertEqual(payload["notes"][0]["midi"], 72)

    def test_melody_ab_review_report_builds_clickable_vocal_rows(self):
        rows = [
            {
                "sample_order": 2,
                "group": "vocal",
                "note": "soft lead",
                "requested": {
                    "hash": "abc123",
                    "path": "A/B Song.flac",
                    "title": "A|B Song",
                    "artist": "Singer",
                },
                "result": {
                    "song_hash": "abc123",
                    "path": "A/B Song.flac",
                    "results": [
                        {"candidate_id": FULL_MIX_PYIN, "ok": True, "status": "cached"},
                        {"candidate_id": VOCAL_STEM_CREPE, "ok": True, "status": "generated"},
                    ],
                },
            },
            {
                "sample_order": 1,
                "group": "solo_piano",
                "requested": {"hash": "piano1", "path": "piano.flac"},
                "result": {"results": []},
            },
        ]

        review_rows = build_review_rows(rows, base_url="http://example.test")
        markdown = render_review_markdown(review_rows)

        self.assertEqual(len(review_rows), 1)
        self.assertEqual(review_rows[0]["admin_url"], "http://example.test/admin?melody=abc123&melodyCandidates=1")
        self.assertIn("A%2FB%20Song.flac", review_rows[0]["player_url"])
        self.assertIn("Octave displacement", markdown)
        self.assertIn("Singer - A\\|B Song", markdown)

    def test_melody_ab_review_report_refuses_overwrite_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "review.md"
            out.write_text("existing\n", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                write_review_markdown("new\n", out, force=False)

    def test_vocal_residual_metrics_flag_coverage_and_tail_jump(self):
        baseline = [
            {"start": 0.0, "end": 1.0, "midi": 60},
            {"start": 10.0, "end": 11.0, "midi": 62},
        ]
        candidate = [
            {"start": "bad", "end": 0.1, "midi": 55},
            {"start": 0.0, "end": 0.2, "midi": 60},
            {"start": 2.0, "end": 2.02, "midi": 72},
            {"start": 2.15, "end": 2.17, "midi": 60},
        ]

        coverage = coverage_gap_metrics(
            baseline,
            candidate,
            duration_s=20.0,
            window_s=10.0,
            min_baseline_active_s=0.5,
            min_candidate_coverage_ratio=0.3,
        )
        tail = phrase_tail_jump_metrics(
            candidate,
            phrase_gap_s=0.5,
            tail_window_s=0.5,
            tail_jump_semitones=7,
        )

        self.assertEqual(coverage["baseline_active_windows"], 2)
        self.assertEqual(coverage["candidate_missing_windows"], 2)
        self.assertEqual(coverage["missing_window_fraction"], 1.0)
        self.assertEqual(tail["phrase_tail_count"], 2)
        self.assertEqual(tail["jump_tail_count"], 1)
        self.assertEqual(tail["jump_tail_fraction"], 0.5)

    def test_vocal_residual_report_reads_candidate_caches_and_writes_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            song_hash = "aa1111111111"
            smoke_rows = [{
                "sample_order": 1,
                "group": "vocal",
                "requested": {"hash": song_hash, "path": "song.flac", "duration_s": 20},
                "result": {"song_hash": song_hash, "path": "song.flac"},
            }]
            write_candidate_cache(
                root,
                song_hash,
                FULL_MIX_PYIN,
                {"melody": [{"start": 0.0, "end": 1.0, "midi": 60}]},
            )
            write_candidate_cache(
                root,
                song_hash,
                VOCAL_STEM_CREPE,
                {"melody": [{"start": 0.0, "end": 1.0, "midi": 60}]},
            )

            report = build_vocal_residual_report(smoke_rows, data_dir=root, window_s=10.0)
            out = root / "residual.json"
            write_residual_report(report, out)

            saved = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(saved["summary"]["total"], 1)
            self.assertTrue(saved["summary"]["passes_stage_b_residual_gate"])
            self.assertEqual(saved["rows"][0]["song_hash"], song_hash)

            with self.assertRaises(FileExistsError):
                write_residual_report(report, out)


if __name__ == "__main__":
    unittest.main()
