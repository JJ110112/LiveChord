"""Beta Feedback API — 和弦評價、留言、Bug 回報"""

import sqlite3
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

from auth_api import get_current_user, get_admin_user

router = APIRouter(prefix="/api/feedback", tags=["feedback"])

DATA_DIR = Path(__file__).parent.parent / "data"
DB_PATH = DATA_DIR / "feedback.db"


def _get_conn():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_feedback_db():
    with _get_conn() as conn:
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
        conn.commit()


init_feedback_db()


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

@router.post("/rating")
def submit_rating(req: RatingRequest, username: str = Depends(get_current_user)):
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    with _get_conn() as conn:
        # Upsert: one rating per user per song
        existing = conn.execute(
            "SELECT id FROM ratings WHERE song_hash=? AND username=?",
            (req.song_hash, username)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE ratings SET rating=?, comment=?, song_title=?, created_at=? WHERE id=?",
                (req.rating, req.comment, req.song_title, now, existing["id"])
            )
        else:
            conn.execute(
                "INSERT INTO ratings (song_hash, song_title, username, rating, comment, created_at) VALUES (?,?,?,?,?,?)",
                (req.song_hash, req.song_title, username, req.rating, req.comment, now)
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

@router.post("/bug")
def submit_bug(req: BugReportRequest, username: str = Depends(get_current_user)):
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO bug_reports (username, category, description, page_url, browser_info, created_at) VALUES (?,?,?,?,?,?)",
            (username, req.category, req.description, req.page_url, req.browser_info, now)
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
