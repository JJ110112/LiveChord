import json
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import analytics_api  # noqa: E402


class TestAnalyticsApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        analytics_api.DATA_DIR = Path(self.tmp.name)
        analytics_api.DB_PATH = analytics_api.DATA_DIR / "analytics.db"
        analytics_api.init_analytics_db()

        app = FastAPI()
        app.include_router(analytics_api.router)
        app.dependency_overrides[analytics_api.get_admin_user] = lambda: "admin"
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def _event(self, event_type, payload, anon_id="anonTest"):
        return self.client.post(
            "/api/analytics/event",
            headers={"X-Anon-Id": anon_id, "referer": "https://livechord.org/player?hash=x"},
            json={"event_type": event_type, "payload": payload},
        )

    def test_public_event_accepts_anon_and_sanitizes_payload(self):
        res = self._event(
            "upload_success",
            {
                "song_hash": "real1",
                "title": "Real Song",
                "source": "upload",
                "is_demo": False,
                "long": "x" * 500,
            },
        )

        self.assertEqual(res.status_code, 200)
        with analytics_api._get_conn() as conn:
            row = conn.execute("SELECT username, payload FROM events").fetchone()
        payload = json.loads(row["payload"])
        self.assertEqual(row["username"], "anon_anonTest")
        self.assertEqual(len(payload["long"]), 300)
        self.assertEqual(payload["page_path"], "https://livechord.org/player?hash=x")

        bad = self._event("bad event name", {"source": "upload"})
        self.assertEqual(bad.status_code, 400)

    def test_admin_summary_excludes_demo_and_reports_player_quality(self):
        self._event("upload_success", {"song_hash": "real1", "title": "Real One", "source": "upload", "is_demo": False})
        self._event("upload_success", {"song_hash": "real2", "title": "Real Two", "source": "upload", "is_demo": False})
        self._event("song_play", {"song_hash": "real1", "song_title": "Real One", "source": "upload", "is_demo": False})
        self._event("song_play", {"song_hash": "real1", "song_title": "Real One", "source": "upload", "is_demo": False})
        self._event("song_play", {"song_hash": "demo1", "song_title": "Demo One", "source": "demo", "is_demo": True}, anon_id="demoUser")
        self._event("song_play", {"song_hash": "maint1", "song_title": "Maintenance", "source": "internal"}, anon_id="opsUser")
        self._event(
            "player_quality_view",
            {
                "song_hash": "real1",
                "title": "Real One",
                "source": "upload",
                "is_demo": False,
                "quality_status": "ok",
                "quality_issues": [],
                "rendered_cards": 24,
                "expected_chords": 24,
                "beat_dot_count": 96,
            },
        )
        self._event(
            "player_quality_view",
            {
                "song_hash": "real2",
                "title": "Real Two",
                "source": "upload",
                "is_demo": False,
                "quality_status": "bad",
                "quality_issues": ["no_beat_dots"],
                "rendered_cards": 12,
                "expected_chords": 12,
                "beat_dot_count": 0,
            },
        )
        self._event(
            "player_quality_view",
            {
                "song_hash": "demo1",
                "title": "Demo One",
                "source": "demo",
                "is_demo": True,
                "quality_status": "bad",
                "quality_issues": ["no_chord_cards"],
            },
            anon_id="demoUser",
        )
        self._event("quality_feedback", {"song_hash": "real1", "source": "upload", "is_demo": False, "action": "good"})

        res = self.client.get("/api/analytics/admin/summary?days=7")
        self.assertEqual(res.status_code, 200)
        data = res.json()

        self.assertEqual(data["successful_uploads"], 2)
        self.assertEqual(data["successful_song_count"], 2)
        self.assertEqual(data["repeat_upload_users"], 1)
        self.assertEqual(data["repeat_play_users"], 1)
        self.assertEqual(data["repeat_same_song_users"], 1)
        self.assertEqual(data["player_viewed_successful_song_count"], 2)
        self.assertEqual(data["unviewed_successful_song_count"], 0)
        self.assertEqual(data["top_songs"][0]["song_hash"], "real1")
        self.assertEqual(data["top_demo_songs"][0]["song_hash"], "demo1")
        self.assertEqual(data["player_quality_views"]["by_status"], {"ok": 1, "bad": 1})
        self.assertEqual(data["player_quality_views"]["issue_songs"][0]["song_hash"], "real2")
        self.assertEqual(data["quality_feedback"], {"good": 1})


if __name__ == "__main__":
    unittest.main()
