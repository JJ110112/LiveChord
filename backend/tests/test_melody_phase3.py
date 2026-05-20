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
    MELODY_CANDIDATE_CACHE_VERSION,
    SOLO_PIANO_POLYPHONIC,
    VOCAL_STEM_CREPE,
    build_candidate_payload,
    candidate_path,
    read_candidate_cache,
    stem_path,
    write_candidate_cache,
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
from backend.ai.stem_separation import StemCache
from backend.ai.vocal_melody_crepe import VocalStemCrepeExtractor


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


if __name__ == "__main__":
    unittest.main()
