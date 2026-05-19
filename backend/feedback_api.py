"""Feedback API — chord ratings, user reports, and human correction signal."""

import html
import hashlib
import os
import re
import sqlite3
import time
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Optional

from auth_api import get_current_user, get_admin_user, get_user_or_anon
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
_REPORT_CATEGORY_RE = r"^(accuracy|ui|performance|upload|login|feature_request|other)$"
_REPORT_WINDOW_SECONDS = 10 * 60
_REPORT_WINDOW_MAX_PER_IDENTITY = 5
_REPORT_WINDOW_MAX_PER_IP = 10
_REPORT_DAY_SECONDS = 24 * 60 * 60
_REPORT_DAY_MAX_PER_IP = 60
_REPORT_GLOBAL_WINDOW_MAX = 120
_REPORT_DUP_WINDOW_SECONDS = 24 * 60 * 60
_REPORT_TEXT_MAX = 2000
_REPORT_CONTACT_MAX = 254
_REPORT_BROWSER_MAX = 500
_report_rate_store: dict[str, list[float]] = defaultdict(list)


@contextmanager
def _get_conn():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


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
                song_hash TEXT DEFAULT '',
                song_title TEXT DEFAULT '',
                contact TEXT DEFAULT '',
                ip_hash TEXT DEFAULT '',
                fingerprint TEXT DEFAULT '',
                duplicate_count INTEGER DEFAULT 1,
                last_seen_at TEXT DEFAULT '',
                status TEXT DEFAULT 'open',
                admin_note TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)
        for col, decl in (
            ("song_hash", "TEXT DEFAULT ''"),
            ("song_title", "TEXT DEFAULT ''"),
            ("contact", "TEXT DEFAULT ''"),
            ("ip_hash", "TEXT DEFAULT ''"),
            ("fingerprint", "TEXT DEFAULT ''"),
            ("duplicate_count", "INTEGER DEFAULT 1"),
            ("last_seen_at", "TEXT DEFAULT ''"),
        ):
            try:
                conn.execute(f"ALTER TABLE bug_reports ADD COLUMN {col} {decl}")
            except sqlite3.OperationalError:
                pass
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bug_reports_status ON bug_reports(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bug_reports_created ON bug_reports(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bug_reports_ip_hash ON bug_reports(ip_hash)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bug_reports_fingerprint ON bug_reports(fingerprint)")
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
    category: str = Field(pattern=_REPORT_CATEGORY_RE)
    description: str = Field(min_length=1, max_length=_REPORT_TEXT_MAX)
    page_url: str = ""
    browser_info: str = ""
    song_hash: str = ""
    song_title: str = ""
    contact: str = ""
    website: str = ""  # honeypot; real users never see/fill this


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

def _real_client_ip(request: Request) -> str:
    return (
        request.headers.get("cf-connecting-ip")
        or (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        or (request.client.host if request.client else "unknown")
    )


def _hash_ip(ip: str) -> str:
    salt = os.environ.get("LIVECHORD_IP_HASH_SALT", "livechord-feedback-v1")
    return hashlib.sha256(f"{salt}:{ip}".encode("utf-8", errors="replace")).hexdigest()[:24]


def _clean_text(value: str, *, max_len: int) -> str:
    value = (value or "").strip()
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", value)
    return value[:max_len]


def _clean_contact(value: str) -> str:
    value = _clean_text(value, max_len=_REPORT_CONTACT_MAX)
    if not value:
        return ""
    # Keep this intentionally permissive: it can be an email address or a short
    # "Discord @name" style handle. The admin handles follow-up manually.
    if any(ch in value for ch in "\r\n<>"):
        raise HTTPException(status_code=400, detail="invalid contact")
    return value


def _report_fingerprint(username: str, ip_hash: str, category: str, description: str, page_url: str, song_hash: str) -> str:
    normalized = re.sub(r"\s+", " ", description.lower()).strip()[:500]
    basis = "|".join([
        username or "",
        ip_hash or "",
        category or "",
        song_hash or "",
        page_url.split("#", 1)[0][:240],
        normalized,
    ])
    return hashlib.sha256(basis.encode("utf-8", errors="replace")).hexdigest()[:32]


def _check_memory_rate_limit(key: str, max_count: int) -> None:
    now = time.time()
    window = [t for t in _report_rate_store[key] if now - t < _REPORT_WINDOW_SECONDS]
    if len(window) >= max_count:
        _report_rate_store[key] = window
        raise HTTPException(status_code=429, detail="Too many reports. Please try again later.")
    window.append(now)
    _report_rate_store[key] = window


def _check_report_rate_limit(conn: sqlite3.Connection, username: str, ip_hash: str) -> None:
    _check_memory_rate_limit(f"user:{username}", _REPORT_WINDOW_MAX_PER_IDENTITY)
    _check_memory_rate_limit(f"ip:{ip_hash}", _REPORT_WINDOW_MAX_PER_IP)

    cutoff_window = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - _REPORT_WINDOW_SECONDS))
    cutoff_day = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - _REPORT_DAY_SECONDS))
    recent_user = conn.execute(
        "SELECT COUNT(*) FROM bug_reports WHERE username=? AND created_at >= ?",
        (username, cutoff_window),
    ).fetchone()[0]
    if recent_user >= _REPORT_WINDOW_MAX_PER_IDENTITY:
        raise HTTPException(status_code=429, detail="Too many reports. Please try again later.")

    recent_ip = conn.execute(
        "SELECT COUNT(*) FROM bug_reports WHERE ip_hash=? AND created_at >= ?",
        (ip_hash, cutoff_window),
    ).fetchone()[0]
    if recent_ip >= _REPORT_WINDOW_MAX_PER_IP:
        raise HTTPException(status_code=429, detail="Too many reports from this network. Please try again later.")

    day_ip = conn.execute(
        "SELECT COUNT(*) FROM bug_reports WHERE ip_hash=? AND created_at >= ?",
        (ip_hash, cutoff_day),
    ).fetchone()[0]
    if day_ip >= _REPORT_DAY_MAX_PER_IP:
        raise HTTPException(status_code=429, detail="Daily report limit reached. Please try again tomorrow.")

    global_recent = conn.execute(
        "SELECT COUNT(*) FROM bug_reports WHERE created_at >= ?",
        (cutoff_window,),
    ).fetchone()[0]
    if global_recent >= _REPORT_GLOBAL_WINDOW_MAX:
        raise HTTPException(status_code=429, detail="Report volume is temporarily high. Please try again later.")


@router.post("/bug", dependencies=[Depends(_require_beta)])
def submit_bug(
    req: BugReportRequest,
    request: Request,
    username: str = Depends(get_user_or_anon),
):
    if req.website:
        return {"ok": True, "ignored": True}
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    description = _clean_text(req.description, max_len=_REPORT_TEXT_MAX)
    if not description:
        raise HTTPException(status_code=400, detail="description required")
    page_url = _clean_text(req.page_url, max_len=500)
    browser_info = _clean_text(req.browser_info, max_len=_REPORT_BROWSER_MAX)
    song_hash = _clean_text(req.song_hash, max_len=80)
    song_title = _clean_text(req.song_title, max_len=300)
    contact = _clean_contact(req.contact)
    ip_hash = _hash_ip(_real_client_ip(request))
    fp = _report_fingerprint(username, ip_hash, req.category, description, page_url, song_hash)
    dup_cutoff = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - _REPORT_DUP_WINDOW_SECONDS))
    with _get_conn() as conn:
        _check_report_rate_limit(conn, username, ip_hash)
        existing = conn.execute(
            """SELECT id, duplicate_count FROM bug_reports
               WHERE fingerprint=? AND created_at >= ?
                 AND status IN ('open','in_progress')
               ORDER BY id DESC LIMIT 1""",
            (fp, dup_cutoff),
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE bug_reports
                   SET duplicate_count=?, last_seen_at=?, browser_info=?, contact=COALESCE(NULLIF(contact, ''), ?)
                   WHERE id=?""",
                ((existing["duplicate_count"] or 1) + 1, now, browser_info, contact, existing["id"]),
            )
            conn.commit()
            return {"ok": True, "duplicate": True, "id": existing["id"]}
        conn.execute(
            """INSERT INTO bug_reports
               (username, category, description, page_url, browser_info, song_hash, song_title,
                contact, ip_hash, fingerprint, duplicate_count, last_seen_at, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                username, req.category, description, page_url, browser_info, song_hash, song_title,
                contact, ip_hash, fp, 1, now, now,
            ),
        )
        bug_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
    return {"ok": True, "id": bug_id}


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
