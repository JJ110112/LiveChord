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
    with sqlite3.connect(DB_PATH) as conn:
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
        conn.commit()

init_db()


# ---------------------------------------------------------------------------
# Invite code table (multi-code support)
# ---------------------------------------------------------------------------

def init_invite_db():
    with sqlite3.connect(DB_PATH) as conn:
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


def _validate_invite_code(code: str) -> bool:
    """Check invite code against multi-code table, then legacy config fallback."""
    now_str = time.strftime("%Y-%m-%dT%H:%M:%S")
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT code, max_uses, use_count, expires_at, revoked FROM invite_codes WHERE code=?",
            (code,)
        ).fetchone()
        if row:
            _, max_uses, use_count, expires_at, revoked = row
            if revoked:
                return False
            if expires_at and expires_at < now_str:
                return False
            if use_count >= max_uses:
                return False
            # Valid — increment usage
            conn.execute("UPDATE invite_codes SET use_count = use_count + 1 WHERE code=?", (code,))
            conn.commit()
            return True
    # Fallback: legacy single invite code from config.json
    cfg = get_config()
    return code == cfg.get("invite_code")


from pydantic import BaseModel

class RegisterRequest(BaseModel):
    username: str
    password: str
    invite_code: str

class LoginRequest(BaseModel):
    username: str
    password: str

@router.post("/register")
async def register(req: RegisterRequest, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(client_ip)

    # Validate invite code: check multi-code table first, fallback to legacy config
    if not _validate_invite_code(req.invite_code):
        raise HTTPException(status_code=403, detail="邀請碼錯誤 (Invalid Invite Code)")

    if len(req.password) < 8:
        raise HTTPException(status_code=400, detail="密碼至少 8 個字元 (Password must be at least 8 characters)")

    username = html_mod.escape(req.username.strip())
    if len(username) < 3:
        raise HTTPException(status_code=400, detail="使用者名稱太短 (Username too short)")
    pw_hash = hash_password(req.password)
    token = secrets.token_hex(32)
    now = time.time()

    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM users")
            count = cursor.fetchone()[0]
            is_admin = 1 if count == 0 else 0

            conn.execute(
                "INSERT INTO users (username, password_hash, token, is_admin, token_created_at) VALUES (?, ?, ?, ?, ?)",
                (username, pw_hash, token, is_admin, now)
            )
            conn.commit()

            # Create user data directory
            user_dir = DATA_DIR / "users" / username / "human_sections"
            user_dir.mkdir(parents=True, exist_ok=True)

            return {"ok": True, "token": token, "username": username}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="帳號已被註冊 (Username already exists)")

@router.post("/login")
async def login(req: LoginRequest, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(client_ip)

    pw_hash = hash_password(req.password)
    with sqlite3.connect(DB_PATH) as conn:
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

def get_current_user(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="未授權 (Unauthorized)")

    token = authorization.replace("Bearer ", "")
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute("SELECT username, token_created_at FROM users WHERE token=?", (token,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="無效憑證 (Invalid Token)")
        # Check token expiry (skip for tokens without timestamp — legacy)
        created = row[1] if row[1] else 0
        if created > 0 and (time.time() - created) > TOKEN_EXPIRY_SECONDS:
            raise HTTPException(status_code=401, detail="憑證已過期，請重新登入 (Token expired)")
        return row[0]

def get_admin_user(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="未授權 (Unauthorized)")

    token = authorization.replace("Bearer ", "")
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute("SELECT username, is_admin, token_created_at FROM users WHERE token=?", (token,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="無效憑證 (Invalid Token)")
        created = row[2] if row[2] else 0
        if created > 0 and (time.time() - created) > TOKEN_EXPIRY_SECONDS:
            raise HTTPException(status_code=401, detail="憑證已過期，請重新登入 (Token expired)")
        if row[1] != 1:
            raise HTTPException(status_code=403, detail="需要管理員權限 (Admin Privileges Required)")
        return row[0]

@router.get("/is_admin")
async def check_is_admin(username: str = Depends(get_current_user)):
    with sqlite3.connect(DB_PATH) as conn:
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
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO invite_codes (code, created_by, created_at, max_uses, expires_at) VALUES (?,?,?,?,?)",
            (code, admin, now, req.max_uses, expires)
        )
        conn.commit()
    return {"ok": True, "code": code, "expires_at": expires}


@router.get("/admin/invites")
def list_invites(admin: str = Depends(get_admin_user)):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT code, created_by, created_at, max_uses, use_count, expires_at, revoked FROM invite_codes ORDER BY created_at DESC"
        ).fetchall()
    return {"invites": [dict(r) for r in rows]}


@router.delete("/admin/invite/{code}")
def revoke_invite(code: str, admin: str = Depends(get_admin_user)):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE invite_codes SET revoked=1 WHERE code=?", (code,))
        conn.commit()
    return {"ok": True}


@router.get("/admin/users")
def list_users(admin: str = Depends(get_admin_user)):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT username, is_admin, token_created_at FROM users").fetchall()
    return {"users": [{"username": r["username"], "is_admin": bool(r["is_admin"]),
                        "last_login": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(r["token_created_at"]))
                        if r["token_created_at"] else None}
                       for r in rows]}


# ---------------------------------------------------------------------------
# TOS (Terms of Service) consent
# ---------------------------------------------------------------------------

@router.get("/tos-status")
def tos_status(username: str = Depends(get_current_user)):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT tos_accepted FROM users WHERE username=?", (username,)
        ).fetchone()
    return {"accepted": bool(row and row[0])}


@router.post("/accept-tos")
def accept_tos(username: str = Depends(get_current_user)):
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE users SET tos_accepted=1, tos_accepted_at=? WHERE username=?",
            (now, username)
        )
        conn.commit()
    return {"ok": True}
