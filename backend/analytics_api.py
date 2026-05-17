"""First-party analytics API — privacy-light usage telemetry."""

import json
import html
import re
import sqlite3
import time
from contextlib import contextmanager
from typing import Any
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from auth_api import get_admin_user, get_user_or_anon


router = APIRouter(prefix="/api/analytics", tags=["analytics"])

DATA_DIR = Path(__file__).parent.parent / "data"
DB_PATH = DATA_DIR / "analytics.db"
_EVENT_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,50}$")
_MAX_PAYLOAD_KEYS = 40
_MAX_STRING_LEN = 300
_MAX_LIST_LEN = 30
_NON_USER_SOURCES = ("demo", "internal", "maintenance")


@contextmanager
def _get_conn():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_analytics_db():
    with _get_conn() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                username TEXT NOT NULL,
                payload TEXT DEFAULT '{}',
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_time ON events(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_username ON events(username)")
        conn.commit()


init_analytics_db()


class EventRequest(BaseModel):
    event_type: str = Field(max_length=50)
    payload: dict = Field(default_factory=dict)


def _clean_event_type(value: str) -> str:
    event_type = (value or "").strip()
    if not _EVENT_RE.match(event_type):
        raise HTTPException(status_code=400, detail="invalid event_type")
    return html.escape(event_type)


def _sanitize_payload(value: Any, depth: int = 0) -> Any:
    if depth > 3:
        return None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:_MAX_STRING_LEN]
    if isinstance(value, list):
        return [_sanitize_payload(v, depth + 1) for v in value[:_MAX_LIST_LEN]]
    if isinstance(value, dict):
        out = {}
        for key, val in list(value.items())[:_MAX_PAYLOAD_KEYS]:
            k = str(key)[:80]
            out[k] = _sanitize_payload(val, depth + 1)
        return out
    return str(value)[:_MAX_STRING_LEN]


def _event_source_filter(alias: str = "payload") -> str:
    return f"""
        AND COALESCE(json_extract({alias}, '$.is_demo'), 0) NOT IN (1, 'true')
        AND COALESCE(json_extract({alias}, '$.source'), '') NOT IN {_sql_in(_NON_USER_SOURCES)}
    """


def _sql_in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join("'" + v.replace("'", "''") + "'" for v in values) + ")"


def _row_int(row: sqlite3.Row | None, key: str = "cnt") -> int:
    if not row:
        return 0
    return int(row[key] or 0)


@router.post("/event")
def track_event(
    req: EventRequest,
    request: Request,
    username: str = Depends(get_user_or_anon),
):
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    event_type = _clean_event_type(req.event_type)
    payload = _sanitize_payload(req.payload or {})
    if isinstance(payload, dict):
        payload.setdefault("page_path", str(request.headers.get("referer") or "")[:_MAX_STRING_LEN])
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO events (event_type, username, payload, created_at) VALUES (?,?,?,?)",
            (event_type, username, json.dumps(payload, ensure_ascii=False), now)
        )
        conn.commit()
    return {"ok": True}


@router.get("/admin/summary")
def admin_summary(days: int = 7, admin: str = Depends(get_admin_user)):
    """Recent analytics summary for admin dashboard"""
    cutoff = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - days * 86400))
    non_demo_clause = _event_source_filter()
    with _get_conn() as conn:
        # Active users
        users = conn.execute(
            "SELECT COUNT(DISTINCT username) as cnt FROM events WHERE created_at >= ?",
            (cutoff,)
        ).fetchone()["cnt"]
        # Event counts by type
        by_type = conn.execute(
            "SELECT event_type, COUNT(*) as cnt FROM events WHERE created_at >= ? GROUP BY event_type ORDER BY cnt DESC",
            (cutoff,)
        ).fetchall()
        # Daily active users
        dau = conn.execute(
            "SELECT SUBSTR(created_at, 1, 10) as day, COUNT(DISTINCT username) as cnt FROM events WHERE created_at >= ? GROUP BY day ORDER BY day",
            (cutoff,)
        ).fetchall()
        # Top real user-upload/library songs played. Demo songs are useful, but
        # reported separately so maintenance/demo-build activity never inflates
        # user-song engagement.
        top_songs = conn.execute("""
            SELECT json_extract(payload, '$.song_hash') as song,
                   json_extract(payload, '$.song_title') as title,
                   COUNT(*) as cnt
            FROM events WHERE event_type='song_play' AND created_at >= ?
        """ + non_demo_clause + """
              AND COALESCE(json_extract(payload, '$.song_hash'), '') != ''
            GROUP BY song ORDER BY cnt DESC LIMIT 10
        """, (cutoff,)).fetchall()
        top_demo_songs = conn.execute("""
            SELECT json_extract(payload, '$.song_hash') as song,
                   json_extract(payload, '$.song_title') as title,
                   COUNT(*) as cnt
            FROM events
            WHERE event_type='song_play' AND created_at >= ?
              AND (
                COALESCE(json_extract(payload, '$.is_demo'), 0) IN (1, 'true')
                OR COALESCE(json_extract(payload, '$.source'), '') = 'demo'
              )
            GROUP BY song ORDER BY cnt DESC LIMIT 10
        """, (cutoff,)).fetchall()
        repeat_upload_users = conn.execute("""
            SELECT COUNT(*) as cnt FROM (
                SELECT username FROM events
                WHERE event_type='upload_success' AND created_at >= ?
        """ + non_demo_clause + """
                GROUP BY username HAVING COUNT(*) >= 2
            )
        """, (cutoff,)).fetchone()["cnt"]
        repeat_play_users = conn.execute("""
            SELECT COUNT(*) as cnt FROM (
                SELECT username FROM events
                WHERE event_type='song_play' AND created_at >= ?
        """ + non_demo_clause + """
                GROUP BY username HAVING COUNT(*) >= 2
            )
        """, (cutoff,)).fetchone()["cnt"]
        repeat_same_song_users = conn.execute("""
            SELECT COUNT(*) as cnt FROM (
                SELECT username, json_extract(payload, '$.song_hash') as song
                FROM events
                WHERE event_type='song_play' AND created_at >= ?
        """ + non_demo_clause + """
                GROUP BY username, song HAVING COUNT(*) >= 2
            )
        """, (cutoff,)).fetchone()["cnt"]
        successful_uploads = _row_int(conn.execute("""
            SELECT COUNT(*) as cnt
            FROM events
            WHERE event_type='upload_success' AND created_at >= ?
        """ + non_demo_clause, (cutoff,)).fetchone())
        successful_song_count = _row_int(conn.execute("""
            SELECT COUNT(DISTINCT json_extract(payload, '$.song_hash')) as cnt
            FROM events
            WHERE event_type='upload_success' AND created_at >= ?
        """ + non_demo_clause + """
              AND COALESCE(json_extract(payload, '$.song_hash'), '') != ''
        """, (cutoff,)).fetchone())
        successful_songs = conn.execute("""
            SELECT json_extract(payload, '$.song_hash') as song,
                   COALESCE(NULLIF(json_extract(payload, '$.title'), ''),
                            NULLIF(json_extract(payload, '$.song_title'), ''),
                            '') as title,
                   username,
                   COUNT(*) as upload_count,
                   MAX(created_at) as last_upload_at
            FROM events
            WHERE event_type='upload_success' AND created_at >= ?
        """ + non_demo_clause + """
              AND COALESCE(json_extract(payload, '$.song_hash'), '') != ''
            GROUP BY song, username
            ORDER BY last_upload_at DESC
            LIMIT 50
        """, (cutoff,)).fetchall()
        uploaded_hash_rows = conn.execute("""
            SELECT DISTINCT json_extract(payload, '$.song_hash') as song
            FROM events
            WHERE event_type='upload_success' AND created_at >= ?
        """ + non_demo_clause + """
              AND COALESCE(json_extract(payload, '$.song_hash'), '') != ''
        """, (cutoff,)).fetchall()
        uploaded_hashes = {
            r["song"] for r in uploaded_hash_rows
            if r["song"]
        }
        player_seen_rows = conn.execute("""
            SELECT DISTINCT COALESCE(NULLIF(json_extract(payload, '$.song_hash'), ''),
                                    NULLIF(json_extract(payload, '$.path'), '')) as song
            FROM events
            WHERE event_type IN ('player_loaded', 'player_quality_view') AND created_at >= ?
        """ + non_demo_clause + """
              AND COALESCE(NULLIF(json_extract(payload, '$.song_hash'), ''),
                           NULLIF(json_extract(payload, '$.path'), '')) != ''
        """, (cutoff,)).fetchall()
        player_seen_hashes = {r["song"] for r in player_seen_rows if r["song"]}
        quality_rows = conn.execute("""
            SELECT json_extract(payload, '$.song_hash') as song,
                   COALESCE(NULLIF(json_extract(payload, '$.title'), ''),
                            NULLIF(json_extract(payload, '$.song_title'), ''),
                            '') as title,
                   COALESCE(json_extract(payload, '$.quality_status'), 'unknown') as status,
                   json_extract(payload, '$.quality_issues') as issues,
                   json_extract(payload, '$.rendered_cards') as rendered_cards,
                   json_extract(payload, '$.expected_chords') as expected_chords,
                   json_extract(payload, '$.beat_dot_count') as beat_dot_count,
                   created_at
            FROM events
            WHERE event_type='player_quality_view' AND created_at >= ?
        """ + non_demo_clause + """
            ORDER BY created_at DESC
        """, (cutoff,)).fetchall()
        quality_by_status: dict[str, int] = {}
        latest_quality_by_song: dict[str, sqlite3.Row] = {}
        for row in quality_rows:
            status = row["status"] or "unknown"
            quality_by_status[status] = quality_by_status.get(status, 0) + 1
            song = row["song"] or ""
            if song and song not in latest_quality_by_song:
                latest_quality_by_song[song] = row
        quality_issue_songs = []
        for row in latest_quality_by_song.values():
            if (row["status"] or "unknown") == "ok":
                continue
            issues_raw = row["issues"] or "[]"
            try:
                issues = json.loads(issues_raw) if isinstance(issues_raw, str) else issues_raw
            except json.JSONDecodeError:
                issues = [str(issues_raw)]
            quality_issue_songs.append({
                "song_hash": row["song"],
                "title": row["title"],
                "status": row["status"],
                "issues": issues if isinstance(issues, list) else [str(issues)],
                "rendered_cards": row["rendered_cards"],
                "expected_chords": row["expected_chords"],
                "beat_dot_count": row["beat_dot_count"],
                "last_seen_at": row["created_at"],
            })
        quality_issue_songs.sort(key=lambda r: (r["status"] != "bad", r["last_seen_at"]), reverse=False)
        quality_feedback = conn.execute("""
            SELECT COALESCE(json_extract(payload, '$.action'), 'unknown') as action,
                   COUNT(*) as cnt
            FROM events
            WHERE event_type='quality_feedback' AND created_at >= ?
        """ + non_demo_clause + """
            GROUP BY action
        """, (cutoff,)).fetchall()
    return {
        "period_days": days,
        "active_users": users,
        "events_by_type": {r["event_type"]: r["cnt"] for r in by_type},
        "daily_active_users": [{"day": r["day"], "count": r["cnt"]} for r in dau],
        "successful_uploads": successful_uploads,
        "successful_song_count": successful_song_count,
        "successful_songs": [
            {
                "song_hash": r["song"],
                "title": r["title"],
                "username": r["username"],
                "upload_count": r["upload_count"],
                "last_upload_at": r["last_upload_at"],
            }
            for r in successful_songs
        ],
        "player_viewed_successful_song_count": len(uploaded_hashes & player_seen_hashes),
        "unviewed_successful_song_count": max(0, len(uploaded_hashes - player_seen_hashes)),
        "player_quality_views": {
            "by_status": quality_by_status,
            "issue_songs": quality_issue_songs[:20],
        },
        "quality_feedback": {r["action"]: r["cnt"] for r in quality_feedback},
        "top_songs": [{"song_hash": r["song"], "title": r["title"], "plays": r["cnt"]} for r in top_songs],
        "top_demo_songs": [{"song_hash": r["song"], "title": r["title"], "plays": r["cnt"]} for r in top_demo_songs],
        "repeat_upload_users": repeat_upload_users,
        "repeat_play_users": repeat_play_users,
        "repeat_same_song_users": repeat_same_song_users,
        "exclusions": {
            "user_song_metrics": "exclude payload source=demo/internal/maintenance and is_demo=true",
            "quality_metrics": "player_quality_view is collected from rendered player DOM after chord cards are built",
        },
    }
