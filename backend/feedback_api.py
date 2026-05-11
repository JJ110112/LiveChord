"""Beta Feedback API — 和弦評價、留言、Bug 回報"""

import html
import sqlite3
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

from auth_api import get_current_user, get_admin_user
from config import is_beta_mode, is_public_mode


def _require_user_facing():
    """Feedback (ratings, bug reports) is available on user-facing instances:
    beta or public. Personal-mode (LAN self-use) returns 404 — there are no
    end-users on personal mode to leave feedback."""
    if not (is_beta_mode() or is_public_mode()):
        raise HTTPException(status_code=404, detail="Not available")


# Back-compat alias
_require_beta = _require_user_facing


router = APIRouter(prefix="/api/feedback", tags=["feedback"])

DATA_DIR = Path(__file__).parent.parent / "data"
DB_PATH = DATA_DIR / "feedback.db"


def _get_conn():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_feedback_db():
    with _get_conn() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ratings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                song_hash TEXT NOT NULL,
                song_title TEXT DEFAULT '',
                username TEXT NOT NULL,
                rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
                comment TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bug_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                category TEXT NOT NULL,
                description TEXT NOT NULL,
                page_url TEXT DEFAULT '',
                browser_info TEXT DEFAULT '',
                status TEXT DEFAULT 'open',
                admin_note TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chord_corrections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                song_hash TEXT NOT NULL,
                ts REAL NOT NULL,
                username TEXT NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('name','time','insert','delete')),
                chord_time REAL,
                before_name TEXT,
                after_name TEXT,
                before_dur REAL,
                after_dur REAL,
                bpm REAL,
                key TEXT,
                source_version TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chord_corrections_hash ON chord_corrections(song_hash)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chord_corrections_ts ON chord_corrections(ts)")
        conn.commit()


init_feedback_db()


# ---------------------------------------------------------------------------
# chord_corrections — used by /api/chords POST to capture human edits as
# training signal. Available regardless of mode (personal/beta/public) so
# personal-mode edits also accumulate. The /api/feedback/* endpoints below
# are still mode-gated.
# ---------------------------------------------------------------------------

def record_chord_corrections(records: list) -> int:
    """Batch-insert chord_corrections rows. Returns number of rows inserted.

    Each record dict shape:
      {song_hash, ts, username, kind, chord_time?, before_name?, after_name?,
       before_dur?, after_dur?, bpm?, key?, source_version?}

    Failure is non-fatal at the call-site (caller wraps in try/except) — this
    helper itself raises on schema/connect errors so the caller knows.
    """
    if not records:
        return 0
    rows = [
        (
            r.get("song_hash"),
            r.get("ts") or time.time(),
            r.get("username"),
            r.get("kind"),
            r.get("chord_time"),
            r.get("before_name"),
            r.get("after_name"),
            r.get("before_dur"),
            r.get("after_dur"),
            r.get("bpm"),
            r.get("key"),
            r.get("source_version"),
        )
        for r in records
    ]
    with _get_conn() as conn:
        conn.executemany(
            """INSERT INTO chord_corrections
               (song_hash, ts, username, kind, chord_time, before_name,
                after_name, before_dur, after_dur, bpm, key, source_version)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        conn.commit()
    return len(rows)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class RatingRequest(BaseModel):
    song_hash: str
    song_title: str = ""
    rating: int = Field(ge=1, le=5)
    comment: str = ""


class BugReportRequest(BaseModel):
    category: str = Field(pattern=r"^(accuracy|ui|performance|feature_request|other)$")
    description: str = Field(min_length=1, max_length=2000)
    page_url: str = ""
    browser_info: str = ""


class BugStatusUpdate(BaseModel):
    status: str = Field(pattern=r"^(open|in_progress|resolved|wontfix)$")
    admin_note: str = ""


# ---------------------------------------------------------------------------
# Rating endpoints
# ---------------------------------------------------------------------------

@router.post("/rating", dependencies=[Depends(_require_beta)])
def submit_rating(req: RatingRequest, username: str = Depends(get_current_user)):
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    comment = html.escape(req.comment.strip()) if req.comment else ""
    song_title = html.escape(req.song_title.strip()) if req.song_title else ""
    with _get_conn() as conn:
        # Upsert: one rating per user per song
        existing = conn.execute(
            "SELECT id FROM ratings WHERE song_hash=? AND username=?",
            (req.song_hash, username)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE ratings SET rating=?, comment=?, song_title=?, created_at=? WHERE id=?",
                (req.rating, comment, song_title, now, existing["id"])
            )
        else:
            conn.execute(
                "INSERT INTO ratings (song_hash, song_title, username, rating, comment, created_at) VALUES (?,?,?,?,?,?)",
                (req.song_hash, song_title, username, req.rating, comment, now)
            )
        conn.commit()
    return {"ok": True}


@router.get("/rating")
def get_my_rating(song_hash: str, username: str = Depends(get_current_user)):
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT rating, comment FROM ratings WHERE song_hash=? AND username=?",
            (song_hash, username)
        ).fetchone()
    if row:
        return {"rating": row["rating"], "comment": row["comment"]}
    return {"rating": None, "comment": ""}


@router.get("/ratings/summary")
def rating_summary(song_hash: str):
    """Public: average rating + count for a song"""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT AVG(rating) as avg, COUNT(*) as cnt FROM ratings WHERE song_hash=?",
            (song_hash,)
        ).fetchone()
    return {
        "average": round(row["avg"], 1) if row["avg"] else None,
        "count": row["cnt"]
    }


# ---------------------------------------------------------------------------
# Bug report endpoints
# ---------------------------------------------------------------------------

@router.post("/bug", dependencies=[Depends(_require_beta)])
def submit_bug(req: BugReportRequest, username: str = Depends(get_current_user)):
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    description = html.escape(req.description.strip())
    page_url = html.escape(req.page_url.strip()) if req.page_url else ""
    browser_info = html.escape(req.browser_info.strip()) if req.browser_info else ""
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO bug_reports (username, category, description, page_url, browser_info, created_at) VALUES (?,?,?,?,?,?)",
            (username, req.category, description, page_url, browser_info, now)
        )
        conn.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------

@router.get("/admin/ratings")
def admin_ratings(limit: int = 100, offset: int = 0, admin: str = Depends(get_admin_user)):
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM ratings ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset)
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM ratings").fetchone()[0]
    return {"total": total, "ratings": [dict(r) for r in rows]}


@router.get("/admin/ratings/stats")
def admin_ratings_stats(admin: str = Depends(get_admin_user)):
    with _get_conn() as conn:
        overall = conn.execute(
            "SELECT AVG(rating) as avg, COUNT(*) as cnt FROM ratings"
        ).fetchone()
        # Worst-rated songs (for prioritization)
        worst = conn.execute("""
            SELECT song_hash, song_title, AVG(rating) as avg, COUNT(*) as cnt
            FROM ratings GROUP BY song_hash
            HAVING cnt >= 1 ORDER BY avg ASC LIMIT 20
        """).fetchall()
        # Rating distribution
        dist = conn.execute(
            "SELECT rating, COUNT(*) as cnt FROM ratings GROUP BY rating ORDER BY rating"
        ).fetchall()
    return {
        "overall_avg": round(overall["avg"], 2) if overall["avg"] else None,
        "total_ratings": overall["cnt"],
        "worst_songs": [dict(r) for r in worst],
        "distribution": {r["rating"]: r["cnt"] for r in dist}
    }


@router.get("/admin/bugs")
def admin_bugs(status: str = "all", limit: int = 100, offset: int = 0,
               admin: str = Depends(get_admin_user)):
    with _get_conn() as conn:
        where = "" if status == "all" else "WHERE status=?"
        params = [] if status == "all" else [status]
        rows = conn.execute(
            f"SELECT * FROM bug_reports {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset]
        ).fetchall()
        total = conn.execute(f"SELECT COUNT(*) FROM bug_reports {where}", params).fetchone()[0]
    return {"total": total, "bugs": [dict(r) for r in rows]}


@router.put("/admin/bug/{bug_id}")
def admin_update_bug(bug_id: int, req: BugStatusUpdate,
                     admin: str = Depends(get_admin_user)):
    with _get_conn() as conn:
        row = conn.execute("SELECT id FROM bug_reports WHERE id=?", (bug_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Bug report not found")
        conn.execute(
            "UPDATE bug_reports SET status=?, admin_note=? WHERE id=?",
            (req.status, req.admin_note, bug_id)
        )
        conn.commit()
    return {"ok": True}
