import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


def _load_tool_module():
    repo = Path(__file__).resolve().parents[2]
    path = repo / "tools" / "continuity_phase5_rollout.py"
    spec = importlib.util.spec_from_file_location("continuity_phase5_rollout", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


rollout = _load_tool_module()


class TestPhase5RolloutTool(unittest.TestCase):
    def _write_chord(self, root: Path, song_hash: str, *, bpm=90, meter="4/4"):
        path = root / "chords" / song_hash[:2] / f"{song_hash}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({
                "path": f"song-{song_hash}.mp3",
                "bpm": bpm,
                "time_signature": meter,
                "chords": [
                    {"time": 0.0, "end": 2.0, "chord": "C"},
                    {"time": 2.0, "end": 4.0, "chord": "G"},
                ],
            }),
            encoding="utf-8",
        )

    def test_collect_candidates_reads_ratings_and_chord_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            song_hash = "aaaaaaaaaaaa"
            self._write_chord(root, song_hash, bpm=72, meter="6/8")
            (root / "ratings.json").write_text(
                json.dumps({song_hash: {"official": {"u": 5}}}),
                encoding="utf-8",
            )

            candidates = rollout.collect_candidates(
                root,
                limit=5,
                engine_version="v8",
                fallback_scan=0,
            )

            self.assertEqual(candidates[0].song_hash, song_hash)
            self.assertEqual(candidates[0].time_signature, "6/8")
            self.assertEqual(candidates[0].bpm, 72)
            self.assertIn("high_rating_json", candidates[0].sources)

    def test_prewarm_plan_dry_run_reports_existing_and_planned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            song_hash = "bbbbbbbbbbbb"
            self._write_chord(root, song_hash)
            candidate = rollout.enrich_candidate(
                rollout.Candidate(song_hash=song_hash),
                root,
                "v8",
            )
            acc_dir = root / "accompaniments"
            acc_dir.mkdir()
            existing = acc_dir / rollout.cache_name(song_hash, "Block", "L1", "default", "piano", "v8")
            existing.write_text("{}", encoding="utf-8")

            plan = rollout.prewarm_plan(
                root,
                [candidate],
                styles=["Block", "Arpeggio"],
                levels=["L1"],
                instruments=["piano"],
                section="default",
                engine_version="v8",
                execute=False,
            )

            self.assertEqual(plan["summary"]["existing"], 1)
            self.assertEqual(plan["summary"]["planned"], 1)
            statuses = {record["style"]: record["status"] for record in plan["records"]}
            self.assertEqual(statuses["Block"], "existing")
            self.assertEqual(statuses["Arpeggio"], "planned")

    def test_audit_events_flags_schema_and_small_gaps(self):
        events = [
            {"time": 0.0, "duration": 0.2, "pitch": 60, "schema_version": 2, "voice_lane": "rh_melody", "gate_ratio": 1.0},
            {"time": 0.3, "duration": 0.05, "pitch": 62, "schema_version": 2, "voice_lane": "rh_melody"},
        ]

        audit = rollout.audit_events(events)

        self.assertEqual(audit["event_count"], 2)
        self.assertEqual(audit["schema2_ratio"], 1.0)
        self.assertEqual(audit["very_short_duration_count"], 1)
        self.assertEqual(audit["small_gap_by_lane"]["rh_melody"], 1)

    def test_representative_set_keeps_distinct_slots_when_available(self):
        candidates = [
            rollout.Candidate(song_hash="a", score=10, sources={"high_rating_json": 1}, bpm=70, time_signature="6/8", has_chords=True, chord_count=2),
            rollout.Candidate(song_hash="b", score=9, sources={"favorite": 1}, bpm=130, time_signature="4/4", has_chords=True, chord_count=2),
            rollout.Candidate(song_hash="c", score=8, sources={"recent": 1}, bpm=100, time_signature="3/4", has_chords=True, chord_count=2),
        ]

        reps = rollout.choose_representative(candidates, limit=3)

        self.assertEqual(len(reps), 3)
        self.assertEqual(len({item["hash"] for item in reps}), 3)


if __name__ == "__main__":
    unittest.main()
