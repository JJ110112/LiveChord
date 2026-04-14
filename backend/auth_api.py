import sqlite3
import hashlib
import os
import secrets
import json
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends, Header, Request

router = APIRouter(prefix="/api/auth", tags=["auth"])

DATA_DIR = Path(__file__).parent.parent / "data"
DB_PATH = DATA_DIR / "users.db"
CONFIG_PATH = DATA_DIR / "config.json"

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
            # Upgrade the first user to admin automatically if retrofitting schema
            conn.execute("UPDATE users SET is_admin = 1 WHERE rowid = (SELECT MIN(rowid) FROM users)")
        except sqlite3.OperationalError:
            pass
        conn.commit()

init_db()

def get_config():
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    return {"invite_code": "LiveChordAlpha"}

def hash_password(password: str, salt: str = "LiveChordSalt2026") -> str:
    return hashlib.sha256((password + salt).encode('utf-8')).hexdigest()

from pydantic import BaseModel

class RegisterRequest(BaseModel):
    username: str
    password: str
    invite_code: str

class LoginRequest(BaseModel):
    username: str
    password: str

@router.post("/register")
async def register(req: RegisterRequest):
    cfg = get_config()
    if req.invite_code != cfg.get("invite_code"):
        raise HTTPException(status_code=403, detail="邀請碼錯誤 (Invalid Invite Code)")
    
    if len(req.username) < 3:
        raise HTTPException(status_code=400, detail="使用者名稱太短 (Username too short)")
        
    pw_hash = hash_password(req.password)
    token = secrets.token_hex(32)
    
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM users")
            count = cursor.fetchone()[0]
            is_admin = 1 if count == 0 else 0
            
            conn.execute("INSERT INTO users (username, password_hash, token, is_admin) VALUES (?, ?, ?, ?)", 
                         (req.username.strip(), pw_hash, token, is_admin))
            conn.commit()
            
            # Create user data directory
            user_dir = DATA_DIR / "users" / req.username.strip() / "human_sections"
            user_dir.mkdir(parents=True, exist_ok=True)
            
            return {"ok": True, "token": token, "username": req.username}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="帳號已被註冊 (Username already exists)")

@router.post("/login")
async def login(req: LoginRequest):
    pw_hash = hash_password(req.password)
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute("SELECT token FROM users WHERE username=? AND password_hash=?", (req.username.strip(), pw_hash))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="帳號或密碼錯誤 (Invalid credentials)")
        
        return {"ok": True, "token": row[0], "username": req.username}

def get_current_user(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="未授權 (Unauthorized)")
        
    token = authorization.replace("Bearer ", "")
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute("SELECT username FROM users WHERE token=?", (token,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="無效憑證 (Invalid Token)")
            
        return row[0]

def get_admin_user(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="未授權 (Unauthorized)")
        
    token = authorization.replace("Bearer ", "")
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute("SELECT username, is_admin FROM users WHERE token=?", (token,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="無效憑證 (Invalid Token)")
        if row[1] != 1:
            raise HTTPException(status_code=403, detail="需要管理員權限 (Admin Privileges Required)")
            
        return row[0]

@router.get("/is_admin")
async def check_is_admin(username: str = Depends(get_current_user)):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute("SELECT is_admin FROM users WHERE username=?", (username,))
        row = cursor.fetchone()
        return {"ok": True, "is_admin": bool(row and row[0] == 1)}
