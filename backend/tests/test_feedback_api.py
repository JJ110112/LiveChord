import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import feedback_api  # noqa: E402


class TestFeedbackApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        feedback_api.DATA_DIR = Path(self.tmp.name)
        feedback_api.DB_PATH = feedback_api.DATA_DIR / "feedback.db"
        feedback_api._report_rate_store.clear()
        self._orig_public = feedback_api.is_public_mode
        self._orig_beta = feedback_api.is_beta_mode
        feedback_api.is_public_mode = lambda: True
        feedback_api.is_beta_mode = lambda: False
        feedback_api.init_feedback_db()

        app = FastAPI()
        app.include_router(feedback_api.router)
        app.dependency_overrides[feedback_api.get_admin_user] = lambda: "admin"
        self.client = TestClient(app)

    def tearDown(self):
        feedback_api.is_public_mode = self._orig_public
        feedback_api.is_beta_mode = self._orig_beta
        feedback_api._report_rate_store.clear()
        self.client.close()
        self.tmp.cleanup()

    def _bug(self, description="Something broke", anon_id="anonReport01", **extra):
        payload = {
            "category": extra.pop("category", "ui"),
            "description": description,
            "page_url": "https://livechord.org/player?hash=abc123",
            "browser_info": "UnitTest Browser",
            "song_hash": "abc123",
            "song_title": "Unit Test Song",
            **extra,
        }
        return self.client.post(
            "/api/feedback/bug",
            headers={"X-Anon-Id": anon_id, "CF-Connecting-IP": "203.0.113.7"},
            json=payload,
        )

    def _rows(self):
        conn = sqlite3.connect(feedback_api.DB_PATH, timeout=10)
        try:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute("SELECT * FROM bug_reports ORDER BY id")]
        finally:
            conn.close()

    def test_anonymous_report_is_stored_without_plain_ip(self):
        res = self._bug(contact="user@example.com")

        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["ok"])
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["username"], "anon_anonReport01")
        self.assertEqual(row["category"], "ui")
        self.assertEqual(row["song_hash"], "abc123")
        self.assertEqual(row["contact"], "user@example.com")
        self.assertNotIn("203.0.113.7", str(row))
        self.assertEqual(row["duplicate_count"], 1)

    def test_honeypot_accepts_without_persisting(self):
        res = self._bug(website="https://spam.example")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"ok": True, "ignored": True})
        self.assertEqual(self._rows(), [])

    def test_duplicate_report_updates_existing_row(self):
        first = self._bug(description="The same visible issue")
        second = self._bug(description="The same visible issue")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["duplicate"])
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["duplicate_count"], 2)

    def test_admin_can_list_and_update_reports_without_reply_flow(self):
        created = self._bug(description="Admin triage target").json()["id"]

        listed = self.client.get("/api/feedback/admin/bugs?status=open&limit=10")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["total"], 1)
        bug = listed.json()["bugs"][0]
        self.assertEqual(bug["id"], created)
        self.assertIn("contact", bug)

        updated = self.client.put(
            f"/api/feedback/admin/bug/{created}",
            json={"status": "in_progress", "admin_note": "triaged"},
        )
        self.assertEqual(updated.status_code, 200)
        rows = self._rows()
        self.assertEqual(rows[0]["status"], "in_progress")
        self.assertEqual(rows[0]["admin_note"], "triaged")

    def test_rate_limit_blocks_report_burst(self):
        statuses = [
            self._bug(description=f"Different report {i}").status_code
            for i in range(7)
        ]

        self.assertEqual(statuses[:5], [200, 200, 200, 200, 200])
        self.assertEqual(statuses[5], 429)
        self.assertEqual(statuses[6], 429)


if __name__ == "__main__":
    unittest.main()
