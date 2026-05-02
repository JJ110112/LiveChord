import html as html_mod
import sqlite3
import hashlib
import os
import secrets
import json
import time
from pathlib import Path
from collections import defaultdict
from fastapi import APIRouter, HTTPException, Depends, Header, Request

router = APIRouter(prefix="/api/auth", tags=["auth"])

DATA_DIR = Path(__file__).parent.parent / "data"
DB_PATH = DATA_DIR / "users.db"
CONFIG_PATH = DATA_DIR / "config.json"

# Token expiry: 30 days (seconds)
TOKEN_EXPIRY_SECONDS = 30 * 24 * 3600

# ---------------------------------------------------------------------------
# Simple in-memory rate limiter (per-IP)
# ---------------------------------------------------------------------------
_rate_store: dict[str, list[float]] = defaultdict(list)
_RATE_WINDOW = 300   # 5 minutes
_RATE_MAX_AUTH = 10   # max login/register attempts per window


def _real_client_ip(request) -> str:
    # Behind Cloudflare Tunnel, request.client.host is 127.0.0.1 for all public
    # traffic — the real client IP is in CF-Connecting-IP (Cloudflare) or
    # X-Forwarded-For. Same precedence used by get_current_user / get_admin_user.
    return (
        request.headers.get("cf-connecting-ip")
        or (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        or (request.client.host if request.client else "unknown")
    )


def _check_rate_limit(ip: str):
    now = time.time()
    attempts = _rate_store[ip]
    # Purge old entries
    _rate_store[ip] = [t for t in attempts if now - t < _RATE_WINDOW]
    if len(_rate_store[ip]) >= _RATE_MAX_AUTH:
        raise HTTPException(status_code=429, detail="請求過於頻繁，請稍後再試 (Rate limited)")
    _rate_store[ip].append(now)


# Initialization
def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                token TEXT NOT NULL
            )
        """)
        try:
            conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0")
            conn.execute("UPDATE users SET is_admin = 1 WHERE rowid = (SELECT MIN(rowid) FROM users)")
        except sqlite3.OperationalError:
            pass
        # Add token_created_at column for expiry
        try:
            conn.execute("ALTER TABLE users ADD COLUMN token_created_at REAL DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        # Add TOS consent column
        try:
            conn.execute("ALTER TABLE users ADD COLUMN tos_accepted INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE users ADD COLUMN tos_accepted_at TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        # Phase B (OAuth): bind external identity to a row in users. We keep
        # password_hash NOT NULL and store '' for OAuth-only accounts (sentinel
        # — login() rejects empty hash via mismatch check). New columns are all
        # nullable / default '' so existing rows survive untouched.
        for col, decl in (
            ("email", "TEXT DEFAULT ''"),
            ("display_name", "TEXT DEFAULT ''"),
            ("oauth_provider", "TEXT DEFAULT ''"),
            ("oauth_sub", "TEXT DEFAULT ''"),
        ):
            try:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col} {decl}")
            except sqlite3.OperationalError:
                pass
        conn.execute("""
            CREATE TABLE IF NOT EXISTS oauth_identities (
                provider TEXT NOT NULL,
                sub TEXT NOT NULL,
                username TEXT NOT NULL,
                email TEXT DEFAULT '',
                display_name TEXT DEFAULT '',
                created_at REAL DEFAULT 0,
                PRIMARY KEY (provider, sub)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_oauth_identities_user ON oauth_identities(username)"
        )
        conn.commit()

init_db()


# ---------------------------------------------------------------------------
# Invite code table (multi-code support)
# ---------------------------------------------------------------------------

def init_invite_db():
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS invite_codes (
                code TEXT PRIMARY KEY,
                created_by TEXT DEFAULT 'system',
                created_at TEXT NOT NULL,
                max_uses INTEGER DEFAULT 1,
                use_count INTEGER DEFAULT 0,
                expires_at TEXT DEFAULT NULL,
                revoked INTEGER DEFAULT 0
            )
        """)
        conn.commit()


init_invite_db()


def get_config():
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    return {"invite_code": "LiveChordAlpha"}

def hash_password(password: str, salt: str = "LiveChordSalt2026") -> str:
    return hashlib.sha256((password + salt).encode('utf-8')).hexdigest()


def _invite_lookup(conn, code: str) -> dict | None:
    """Return invite-info dict if valid, None otherwise. Read-only — does NOT
    increment use_count. Consumption happens atomically with the user INSERT
    in register() so failed registrations (duplicate username etc.) don't burn
    an invite use. See stress-test-2026-04-19.md bug #2."""
    now_str = time.strftime("%Y-%m-%dT%H:%M:%S")
    row = conn.execute(
        "SELECT code, max_uses, use_count, expires_at, revoked FROM invite_codes WHERE code=?",
        (code,)
    ).fetchone()
    if row:
        _, max_uses, use_count, expires_at, revoked = row
        if revoked:
            return None
        if expires_at and expires_at < now_str:
            return None
        if use_count >= max_uses:
            return None
        return {"type": "db", "max_uses": max_uses, "use_count": use_count}
    # Legacy single invite code from config.json (no counter)
    cfg = get_config()
    if code == cfg.get("invite_code"):
        return {"type": "legacy"}
    return None


from pydantic import BaseModel

class RegisterRequest(BaseModel):
    username: str
    password: str
    invite_code: str

class LoginRequest(BaseModel):
    username: str
    password: str

def _block_password_in_public():
    """Public mode disables username/password sign-up + sign-in. The OAuth
    flow under /api/auth/oauth/* is the only login path on the cloud
    instance. Personal/beta keep the password flow as-is."""
    from config import is_public_mode
    if is_public_mode():
        raise HTTPException(status_code=404, detail="Not available")


@router.post("/register")
async def register(req: RegisterRequest, request: Request):
    _block_password_in_public()
    _check_rate_limit(_real_client_ip(request))

    # Cheap pre-checks before opening DB
    if len(req.password) < 8:
        raise HTTPException(status_code=400, detail="密碼至少 8 個字元 (Password must be at least 8 characters)")
    username = html_mod.escape(req.username.strip())
    if len(username) < 3:
        raise HTTPException(status_code=400, detail="使用者名稱太短 (Username too short)")

    pw_hash = hash_password(req.password)
    token = secrets.token_hex(32)
    now = time.time()

    # Atomic: invite validation → consume → user INSERT all in one transaction.
    # If INSERT fails (duplicate username), sqlite3's connection context manager
    # rolls back the entire transaction, so use_count is NOT incremented.
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            invite = _invite_lookup(conn, req.invite_code)
            if not invite:
                raise HTTPException(status_code=403, detail="邀請碼錯誤 (Invalid Invite Code)")

            if invite["type"] == "db":
                conn.execute(
                    "UPDATE invite_codes SET use_count = use_count + 1 WHERE code=?",
                    (req.invite_code,)
                )

            cursor = conn.execute("SELECT COUNT(*) FROM users")
            count = cursor.fetchone()[0]
            is_admin = 1 if count == 0 else 0

            conn.execute(
                "INSERT INTO users (username, password_hash, token, is_admin, token_created_at) VALUES (?, ?, ?, ?, ?)",
                (username, pw_hash, token, is_admin, now)
            )
            # with-block commit happens on successful exit

        # Directory creation outside the DB transaction (file I/O).
        user_dir = DATA_DIR / "users" / username / "human_sections"
        user_dir.mkdir(parents=True, exist_ok=True)

        return {"ok": True, "token": token, "username": username}
    except HTTPException:
        raise
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="帳號已被註冊 (Username already exists)")

@router.post("/login")
async def login(req: LoginRequest, request: Request):
    _block_password_in_public()
    _check_rate_limit(_real_client_ip(request))

    pw_hash = hash_password(req.password)
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        cursor = conn.execute("SELECT token FROM users WHERE username=? AND password_hash=?", (req.username.strip(), pw_hash))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="帳號或密碼錯誤 (Invalid credentials)")

        # Refresh token on login (reset expiry)
        new_token = secrets.token_hex(32)
        conn.execute(
            "UPDATE users SET token=?, token_created_at=? WHERE username=?",
            (new_token, time.time(), req.username.strip())
        )
        conn.commit()
        return {"ok": True, "token": new_token, "username": req.username}

def get_current_user(request: Request, authorization: str = Header(None)):
    from config import is_personal_mode, is_lan_ip

    # LAN bypass is personal-mode only. In public (cloud) the LAN concept
    # doesn't exist; in beta the front-end forces login.
    if is_personal_mode():
        client_ip = (
            request.headers.get("cf-connecting-ip")
            or (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
            or (request.client.host if request.client else "")
        )
        if is_lan_ip(client_ip):
            return "admin"  # auto-bypass on LAN

    if not authorization:
        raise HTTPException(status_code=401, detail="未授權 (Unauthorized)")

    token = authorization.replace("Bearer ", "")
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        cursor = conn.execute("SELECT username, token_created_at FROM users WHERE token=?", (token,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="無效憑證 (Invalid Token)")
        # Check token expiry (skip for tokens without timestamp — legacy)
        created = row[1] if row[1] else 0
        if created > 0 and (time.time() - created) > TOKEN_EXPIRY_SECONDS:
            raise HTTPException(status_code=401, detail="憑證已過期，請重新登入 (Token expired)")
        return row[0]

import re as _re

# X-Anon-Id format: 8-32 chars of [A-Za-z0-9_-], chosen client-side.
# Backend prefixes with `anon_` so storage keys are namespaced away from real
# usernames (real usernames don't start with `anon_`; OAuth ones start with
# `oauth_<provider>_`; password ones are user-chosen).
_ANON_ID_RE = _re.compile(r"^[A-Za-z0-9_-]{8,32}$")
_ANON_PREFIX = "anon_"


def get_optional_user(
    request: Request,
    authorization: str = Header(None),
):
    """Return logged-in username if a valid token is presented, else None.
    Does NOT raise on missing/invalid auth. Use for endpoints where login is
    optional (anonymous visitors allowed) — caller decides what to do with None.
    Personal-mode LAN bypass still applies.
    """
    from config import is_personal_mode, is_lan_ip

    if is_personal_mode():
        client_ip = (
            request.headers.get("cf-connecting-ip")
            or (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
            or (request.client.host if request.client else "")
        )
        if is_lan_ip(client_ip):
            return "admin"

    if not authorization:
        return None
    token = authorization.replace("Bearer ", "")
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        cursor = conn.execute(
            "SELECT username, token_created_at FROM users WHERE token=?",
            (token,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        created = row[1] if row[1] else 0
        if created > 0 and (time.time() - created) > TOKEN_EXPIRY_SECONDS:
            return None
        return row[0]


def get_user_or_anon(
    request: Request,
    authorization: str = Header(None),
    x_anon_id: str = Header(None, alias="X-Anon-Id"),
):
    """Always returns a string identity. If the caller is logged in, returns
    their real username. Else they MUST send a well-formed X-Anon-Id header,
    which is namespaced as ``anon_<id>`` and stored in audit/quota/history.

    Anonymous quota gaming (just regenerate the UUID) is mitigated by IP-level
    rate limiting elsewhere; this layer only ensures a stable per-browser key.
    """
    user = get_optional_user(request, authorization)
    if user:
        return user
    if not x_anon_id or not _ANON_ID_RE.match(x_anon_id):
        raise HTTPException(
            status_code=400,
            detail="匿名身分缺失 (X-Anon-Id header missing or malformed)",
        )
    return f"{_ANON_PREFIX}{x_anon_id}"


def is_anon(username: str) -> bool:
    """True if the identity returned by get_user_or_anon represents an
    anonymous (not-logged-in) caller."""
    return bool(username) and username.startswith(_ANON_PREFIX)


def _admin_email_allowlist() -> set:
    """Read LIVECHORD_ADMIN_EMAILS env at call time so adding an email to the
    .env (and restarting) takes effect without DB writes. Empty/unset → no
    public-mode admins (only DB-flagged is_admin=1 accounts qualify)."""
    raw = os.environ.get("LIVECHORD_ADMIN_EMAILS", "") or ""
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def get_admin_user(request: Request, authorization: str = Header(None)):
    from config import is_personal_mode, is_public_mode, is_lan_ip

    # LAN bypass is personal-mode only (see get_current_user).
    if is_personal_mode():
        client_ip = (
            request.headers.get("cf-connecting-ip")
            or (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
            or (request.client.host if request.client else "")
        )
        if is_lan_ip(client_ip):
            return "admin"  # auto-bypass on LAN

    if not authorization:
        raise HTTPException(status_code=401, detail="未授權 (Unauthorized)")

    token = authorization.replace("Bearer ", "")
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        cursor = conn.execute(
            "SELECT username, is_admin, token_created_at, email FROM users WHERE token=?",
            (token,),
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="無效憑證 (Invalid Token)")
        username, is_admin_flag, created, email = row
        created = created or 0
        if created > 0 and (time.time() - created) > TOKEN_EXPIRY_SECONDS:
            raise HTTPException(status_code=401, detail="憑證已過期，請重新登入 (Token expired)")
        if is_admin_flag == 1:
            return username
        # Public-mode override: env-driven email allowlist promotes the caller
        # without requiring an `is_admin=1` flip in the DB. Convenient for the
        # cloud deployment where admin identity follows the OAuth email and
        # may need to change without surgery.
        if is_public_mode() and email:
            if email.lower() in _admin_email_allowlist():
                return username
        raise HTTPException(status_code=403, detail="需要管理員權限 (Admin Privileges Required)")

@router.get("/is_admin")
async def check_is_admin(request: Request, username: str = Depends(get_current_user)):
    from config import is_personal_mode, is_lan_ip
    if is_personal_mode():
        client_ip = (
            request.headers.get("cf-connecting-ip")
            or (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
            or (request.client.host if request.client else "")
        )
        if is_lan_ip(client_ip):
            return {"ok": True, "is_admin": True}
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        cursor = conn.execute("SELECT is_admin FROM users WHERE username=?", (username,))
        row = cursor.fetchone()
        return {"ok": True, "is_admin": bool(row and row[0] == 1)}


# ---------------------------------------------------------------------------
# Admin: Invite code management
# ---------------------------------------------------------------------------

class CreateInviteRequest(BaseModel):
    max_uses: int = 1
    expires_days: int = 30   # 0 = no expiry


@router.post("/admin/invite")
def create_invite(req: CreateInviteRequest, admin: str = Depends(get_admin_user)):
    code = secrets.token_urlsafe(12)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    expires = None
    if req.expires_days > 0:
        expires = time.strftime("%Y-%m-%dT%H:%M:%S",
                                time.localtime(time.time() + req.expires_days * 86400))
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.execute(
            "INSERT INTO invite_codes (code, created_by, created_at, max_uses, expires_at) VALUES (?,?,?,?,?)",
            (code, admin, now, req.max_uses, expires)
        )
        conn.commit()
    return {"ok": True, "code": code, "expires_at": expires}


@router.get("/admin/invites")
def list_invites(admin: str = Depends(get_admin_user)):
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT code, created_by, created_at, max_uses, use_count, expires_at, revoked FROM invite_codes ORDER BY created_at DESC"
        ).fetchall()
    return {"invites": [dict(r) for r in rows]}


@router.delete("/admin/invite/{code}")
def revoke_invite(code: str, admin: str = Depends(get_admin_user)):
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.execute("UPDATE invite_codes SET revoked=1 WHERE code=?", (code,))
        conn.commit()
    return {"ok": True}


@router.get("/admin/users")
def list_users(admin: str = Depends(get_admin_user)):
    """Users sorted newest-registered first (rowid DESC) with usage counters
    joined in from process_audit (analyses) and feedback.db (ratings)."""
    # Users table (auth.db): registration order = rowid, last login =
    # token_created_at (regenerated on every login).
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT rowid, username, is_admin, token_created_at "
            "FROM users ORDER BY rowid DESC"
        ).fetchall()
    users = [
        {
            "rowid": r["rowid"],
            "username": r["username"],
            "is_admin": bool(r["is_admin"]),
            "last_login": time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.localtime(r["token_created_at"])
            ) if r["token_created_at"] else None,
            "analyses_total": 0,
            "analyses_7d": 0,
            "ratings_total": 0,
        }
        for r in rows
    ]
    if not users:
        return {"users": []}

    usernames = [u["username"] for u in users]
    qmarks = ",".join("?" * len(usernames))

    # process_audit lives in audit.db — separate file, can't JOIN with
    # users.db. Query it independently then merge.
    try:
        from process_queue import AUDIT_DB_PATH
        seven_days_ago = time.time() - 7 * 86400
        # process_audit stores `created_at` as ISO string; compare as string
        # works because the format is fixed width.
        cutoff_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(seven_days_ago))
        with sqlite3.connect(AUDIT_DB_PATH, timeout=10) as conn:
            totals = dict(conn.execute(
                f"SELECT username, COUNT(*) FROM process_audit "
                f"WHERE status='done' AND username IN ({qmarks}) "
                f"GROUP BY username",
                usernames,
            ).fetchall())
            recent = dict(conn.execute(
                f"SELECT username, COUNT(*) FROM process_audit "
                f"WHERE status='done' AND created_at >= ? "
                f"AND username IN ({qmarks}) GROUP BY username",
                [cutoff_iso, *usernames],
            ).fetchall())
        for u in users:
            u["analyses_total"] = totals.get(u["username"], 0)
            u["analyses_7d"] = recent.get(u["username"], 0)
    except Exception:
        pass  # never fail the whole list over audit-count lookup

    # feedback.db ratings count (best-effort, tolerate missing schema)
    try:
        from feedback_api import DB_PATH as FEEDBACK_DB_PATH
        with sqlite3.connect(FEEDBACK_DB_PATH, timeout=10) as conn:
            rat = dict(conn.execute(
                f"SELECT username, COUNT(*) FROM ratings "
                f"WHERE username IN ({qmarks}) GROUP BY username",
                usernames,
            ).fetchall())
        for u in users:
            u["ratings_total"] = rat.get(u["username"], 0)
    except Exception:
        pass

    return {"users": users}


# ---------------------------------------------------------------------------
# TOS (Terms of Service) consent
# ---------------------------------------------------------------------------

@router.get("/tos-status")
def tos_status(username: str = Depends(get_current_user)):
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        row = conn.execute(
            "SELECT tos_accepted FROM users WHERE username=?", (username,)
        ).fetchone()
    return {"accepted": bool(row and row[0])}


@router.post("/accept-tos")
def accept_tos(username: str = Depends(get_current_user)):
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.execute(
            "UPDATE users SET tos_accepted=1, tos_accepted_at=? WHERE username=?",
            (now, username)
        )
        conn.commit()
    return {"ok": True}
