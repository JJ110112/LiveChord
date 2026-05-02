"""OAuth 2.0 / OIDC sign-in for public mode (Google + Discord).

Apple is intentionally not wired (deferred per user decision; the provider
slot is left commented as a one-line flip when credentials arrive).

Personal/beta modes do NOT route here — endpoints return 404 — so the legacy
username/password flow on those instances is untouched.

Flow per provider:

  1. GET /api/auth/oauth/<provider>/start
       Builds an authorize URL with redirect_uri = OAUTH_REDIRECT_BASE +
       this provider's callback path. Authlib stores state in the Starlette
       session (signed cookie via SessionMiddleware in main.py) so the
       callback can verify it. Returns 302.

  2. <provider> redirects browser back to
     GET /api/auth/oauth/<provider>/callback?code=...&state=...
       Authlib exchanges the code for an access token, runs the OIDC ID-token
       verification path for Google, and returns the userinfo claim dict for
       Google (or fetches /users/@me for Discord). We then upsert into
       users + oauth_identities, mint our own random token, and 302 the
       browser back to the homepage with the token in the URL fragment so
       JS can stash it in localStorage and immediately strip it.

The redirect URI registered at the provider must match
``{OAUTH_REDIRECT_BASE}/api/auth/oauth/<provider>/callback`` exactly.
"""

from __future__ import annotations

import os
import sqlite3
import secrets
import time
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Depends, Header
from fastapi.responses import RedirectResponse
from authlib.integrations.starlette_client import OAuth, OAuthError
import httpx

from auth_api import (
    DB_PATH,
    TOKEN_EXPIRY_SECONDS,
    _check_rate_limit,
    _real_client_ip,
    get_current_user,
    _admin_email_allowlist,
)
from config import is_public_mode

router = APIRouter(prefix="/api/auth/oauth", tags=["oauth"])
logger = logging.getLogger("livechord.oauth")


# ---------------------------------------------------------------------------
# Provider registration
# ---------------------------------------------------------------------------
# Authlib's OAuth registry. Providers without an env-supplied client_id are
# silently omitted from the registry — `oauth.create_client(name)` returns
# None for them and we surface a 404. So .env can leave Discord blank without
# breaking Google or vice versa.

oauth = OAuth()


def _register_providers():
    g_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    g_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    if g_id and g_secret:
        oauth.register(
            name="google",
            client_id=g_id,
            client_secret=g_secret,
            server_metadata_url=(
                "https://accounts.google.com/.well-known/openid-configuration"
            ),
            client_kwargs={"scope": "openid email profile"},
        )

    d_id = os.environ.get("DISCORD_CLIENT_ID", "").strip()
    d_secret = os.environ.get("DISCORD_CLIENT_SECRET", "").strip()
    if d_id and d_secret:
        oauth.register(
            name="discord",
            client_id=d_id,
            client_secret=d_secret,
            authorize_url="https://discord.com/oauth2/authorize",
            access_token_url="https://discord.com/api/oauth2/token",
            api_base_url="https://discord.com/api/",
            client_kwargs={"scope": "identify email"},
        )

    # Apple — placeholder. Uncomment + fill APPLE_* env when the Apple
    # Developer enrollment is done (see plan §Phase B).
    # a_id = os.environ.get("APPLE_CLIENT_ID", "").strip()
    # if a_id:
    #     oauth.register(name="apple", ... )


_register_providers()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PROVIDERS = ("google", "discord")  # Apple deferred


def _require_public_or_404():
    if not is_public_mode():
        raise HTTPException(status_code=404, detail="Not available")


def _redirect_uri(request: Request, provider: str) -> str:
    """Resolve the callback URL the provider should bounce to. Prefers
    OAUTH_REDIRECT_BASE env (production: https://livechord.org) so Cloudflare
    Tunnel callbacks work even when uvicorn binds 0.0.0.0:8800 internally.
    Falls back to request.base_url for local dev."""
    base = (os.environ.get("OAUTH_REDIRECT_BASE") or "").rstrip("/")
    if not base:
        base = str(request.base_url).rstrip("/")
    return f"{base}/api/auth/oauth/{provider}/callback"


def _ensure_user(provider: str, sub: str, email: str, display_name: str) -> str:
    """Upsert a users row + oauth_identities row for this OAuth identity.
    Returns the local username (auto-derived as ``oauth_<provider>_<sub[:8]>``
    for first-time logins; stable across re-logins thanks to the (provider,sub)
    primary key on oauth_identities)."""
    sub = (sub or "").strip()
    if not sub:
        raise HTTPException(status_code=400, detail="Missing subject from provider")
    email = (email or "").strip()
    display_name = (display_name or "").strip() or email or "user"

    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        existing = conn.execute(
            "SELECT username FROM oauth_identities WHERE provider=? AND sub=?",
            (provider, sub),
        ).fetchone()
        if existing:
            username = existing[0]
            # Refresh email/display_name in case the user changed them at the provider
            conn.execute(
                "UPDATE oauth_identities SET email=?, display_name=? "
                "WHERE provider=? AND sub=?",
                (email, display_name, provider, sub),
            )
            conn.execute(
                "UPDATE users SET email=?, display_name=? WHERE username=?",
                (email, display_name, username),
            )
            conn.commit()
            return username

        # First-time OAuth login → mint a username and a users row. Username
        # format is opaque (`oauth_google_a1b2c3d4`); the user-facing label is
        # display_name.
        username = f"oauth_{provider}_{sub[:8]}"
        # Disambiguate if a previous identity collided (extremely unlikely but
        # cheap to handle).
        suffix = 0
        while conn.execute(
            "SELECT 1 FROM users WHERE username=?", (username,)
        ).fetchone():
            suffix += 1
            username = f"oauth_{provider}_{sub[:8]}_{suffix}"

        # First-registered user is admin (mirrors password flow).
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        is_admin = 1 if count == 0 else 0
        conn.execute(
            "INSERT INTO users (username, password_hash, token, is_admin, "
            "token_created_at, email, display_name, oauth_provider, oauth_sub) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                username,
                "",  # OAuth-only sentinel; login() empty-hash mismatches by design
                "",  # token assigned later in oauth_callback after _ensure_user
                is_admin,
                time.time(),
                email,
                display_name,
                provider,
                sub,
            ),
        )
        conn.execute(
            "INSERT INTO oauth_identities "
            "(provider, sub, username, email, display_name, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (provider, sub, username, email, display_name, time.time()),
        )
        conn.commit()
        return username


def _mint_token(username: str) -> str:
    new_token = secrets.token_urlsafe(32)
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.execute(
            "UPDATE users SET token=?, token_created_at=? WHERE username=?",
            (new_token, time.time(), username),
        )
        conn.commit()
    return new_token


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/{provider}/start")
async def oauth_start(provider: str, request: Request):
    _require_public_or_404()
    _check_rate_limit(_real_client_ip(request))
    if provider not in PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown provider")
    client = oauth.create_client(provider)
    if client is None:
        raise HTTPException(
            status_code=503,
            detail=f"{provider} OAuth not configured on this server",
        )
    redirect_uri = _redirect_uri(request, provider)
    return await client.authorize_redirect(request, redirect_uri)


@router.get("/{provider}/callback")
async def oauth_callback(provider: str, request: Request):
    _require_public_or_404()
    if provider not in PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown provider")
    client = oauth.create_client(provider)
    if client is None:
        raise HTTPException(
            status_code=503,
            detail=f"{provider} OAuth not configured on this server",
        )
    try:
        token = await client.authorize_access_token(request)
    except OAuthError as e:
        logger.warning("oauth_callback %s OAuthError: %s", provider, e)
        # Bounce to /login with an error param so the UI can render a friendly message
        return RedirectResponse(url=f"/login?oauth_error=1&p={provider}", status_code=302)
    except Exception as e:
        logger.exception("oauth_callback %s unexpected error", provider)
        return RedirectResponse(url=f"/login?oauth_error=1&p={provider}", status_code=302)

    # Provider-specific userinfo extraction
    sub = email = display_name = ""
    if provider == "google":
        userinfo = token.get("userinfo") or {}
        sub = str(userinfo.get("sub") or "")
        email = userinfo.get("email") or ""
        display_name = userinfo.get("name") or email or "user"
    elif provider == "discord":
        # Discord returns OAuth2 token; userinfo via /users/@me
        try:
            resp = await client.get("users/@me", token=token)
            data = resp.json() if resp.status_code == 200 else {}
        except Exception as e:
            logger.warning("discord users/@me fetch failed: %s", e)
            data = {}
        sub = str(data.get("id") or "")
        email = data.get("email") or ""
        display_name = (
            data.get("global_name") or data.get("username") or email or "user"
        )

    if not sub:
        return RedirectResponse(url=f"/login?oauth_error=1&p={provider}", status_code=302)

    username = _ensure_user(provider, sub, email, display_name)
    new_token = _mint_token(username)

    # Ship token + username back via URL fragment. The fragment is browser-only
    # (never sent to the server) so it doesn't appear in access logs / proxies.
    # Frontend captures it in DOMContentLoaded, persists to localStorage, then
    # strips the fragment via history.replaceState.
    from urllib.parse import quote
    return RedirectResponse(
        url=(
            f"/?oauth_done=1#token={quote(new_token)}"
            f"&username={quote(username)}"
        ),
        status_code=302,
    )


@router.get("/me")
def oauth_me(request: Request, username: str = Depends(get_current_user)):
    """Return the logged-in user's profile + admin flag (with public-mode
    email-allowlist override applied). Used by the frontend to render the
    header greeting and conditionally show the admin button."""
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        row = conn.execute(
            "SELECT email, display_name, is_admin, oauth_provider FROM users WHERE username=?",
            (username,),
        ).fetchone()
    if not row:
        # LAN-bypass admin in personal mode hits this code path with username='admin'
        # but no row in users. Synthesize a profile.
        return {
            "username": username,
            "email": "",
            "display_name": username,
            "is_admin": True,
            "provider": "lan",
        }
    email, display_name, is_admin_flag, provider = (
        row[0] or "",
        row[1] or username,
        row[2],
        row[3] or "",
    )
    is_admin = is_admin_flag == 1
    if not is_admin and is_public_mode() and email:
        if email.lower() in _admin_email_allowlist():
            is_admin = True
    return {
        "username": username,
        "email": email,
        "display_name": display_name,
        "is_admin": is_admin,
        "provider": provider,
    }


@router.post("/logout")
def oauth_logout(authorization: str = Header(None)):
    """Invalidate the caller's token by overwriting it. Idempotent — missing
    or already-invalid tokens return 200 so the client UI can always proceed."""
    if not authorization:
        return {"ok": True}
    token = authorization.replace("Bearer ", "")
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.execute(
            "UPDATE users SET token='', token_created_at=0 WHERE token=?",
            (token,),
        )
        conn.commit()
    return {"ok": True}


@router.get("/providers")
def list_providers():
    """Tell the frontend which OAuth buttons to render. Returns the subset
    that have credentials configured at this instance."""
    available = []
    for name in PROVIDERS:
        if oauth.create_client(name) is not None:
            available.append(name)
    return {"providers": available}
