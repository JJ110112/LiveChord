"""Beta Analytics API — 匿名使用統計"""

import html
import sqlite3
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Optional

from auth_api import get_current_user, get_admin_user
from config import is_beta_mode


def _require_beta():
    if not is_beta_mode():
        raise HTTPException(status_code=404, detail="Not available")


router = APIRouter(prefix="/api/analytics", tags=["analytics"])

DATA_DIR = Path(__file__).parent.parent / "data"
DB_PATH = DATA_DIR / "analytics.db"


def _get_conn():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_analytics_db():
    with _get_conn() as conn:
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
        conn.commit()


init_analytics_db()


class EventRequest(BaseModel):
    event_type: str = Field(max_length=50)
    payload: dict = {}


@router.post("/event", dependencies=[Depends(_require_beta)])
def track_event(req: EventRequest, username: str = Depends(get_current_user)):
    import json
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    event_type = html.escape(req.event_type.strip())
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO events (event_type, username, payload, created_at) VALUES (?,?,?,?)",
            (event_type, username, json.dumps(req.payload, ensure_ascii=False), now)
        )
        conn.commit()
    return {"ok": True}


@router.get("/admin/summary")
def admin_summary(days: int = 7, admin: str = Depends(get_admin_user)):
    """Recent analytics summary for admin dashboard"""
    import json
    cutoff = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - days * 86400))
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
        # Top songs played
        top_songs = conn.execute("""
            SELECT json_extract(payload, '$.song_hash') as song,
                   json_extract(payload, '$.song_title') as title,
                   COUNT(*) as cnt
            FROM events WHERE event_type='song_play' AND created_at >= ?
            GROUP BY song ORDER BY cnt DESC LIMIT 10
        """, (cutoff,)).fetchall()
    return {
        "period_days": days,
        "active_users": users,
        "events_by_type": {r["event_type"]: r["cnt"] for r in by_type},
        "daily_active_users": [{"day": r["day"], "count": r["cnt"]} for r in dau],
        "top_songs": [{"song_hash": r["song"], "title": r["title"], "plays": r["cnt"]} for r in top_songs],
    }
