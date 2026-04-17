# CLAUDE.md — LiveChord

Project-specific guidance for Claude Code working in this repo. Also see [doc/QA.md](doc/QA.md) for the full QA protocol.

## Environment

- **Dev repo**: `c:\Users\hitea\Claude\LiveChord` (git, source of truth)
- **Local testing**: IDE Live Server is installed, Playwright MCP is registered for AI-driven local QA.
- **Prod runtime** (NUC, mounted as `V:\` from PC): backend runs from `V:\backend`, frontend from `V:\frontend`
- **Backend server**: FastAPI/uvicorn, `main:app` — dual instance on NUC (start: [start_dual.bat](start_dual.bat) · restart: [restart_dual.bat](restart_dual.bat))
  - **Personal (Port 8800)**: `LIVECHORD_MODE=personal` — LAN bypass, full NAS access, no login required on LAN
  - **Beta (Port 8801)**: `LIVECHORD_MODE=beta` — Cloudflare Tunnel, forced login, NAS paths hashed
- **Production QA Server**: `http://192.168.50.6:8800/` (LAN Personal) / `https://livechord.org` (public Beta, Port 8801)
- **Production Admin Account**: User: `hitea`
- **Dev Admin Account**: User: `admin` / Password: `admin`
- **Admin page**: `http://localhost:8800/admin`

## Deploy Sync (QA §590 防線5)

After any code change:
- Backend `.py` → copy to matching path under `V:\backend\`
- Frontend `.html`/`.js`/`.css` → copy to matching path under `V:\frontend\`
- Verify with `diff -q` after copy
- `W:\` is **not** the runtime — edits there are silent no-ops for the server

## UI QA (feedback_playwright_qa)

Any change to frontend files requires Playwright verification before claiming done:
- Uses Playwright MCP registered in IDE. AI should trigger playwright testing locally via Live Server or target URLs.
- Target `http://192.168.50.6:8800/` (v:\) if the feature is already deployed, as it has been human tested.
- Test the changed buttons / appearance / behaviour
- Cover edge cases (hard reload, no cache)
- Watch for regressions in unrelated features
- If the server isn't running and live QA is impossible, **say so explicitly** instead of claiming success

## Coding Rules

- **No `async def` for file I/O endpoints** — use plain `def` so FastAPI dispatches to the thread pool (see `feedback_async_def`)
- **Cache-busting**: bump `?v=N` on `<script>`/`<link>` tags when editing JS/CSS (QA UI rule 6)
- **BTC chord detection** runs in a `ProcessPoolExecutor` to isolate from the event loop GIL
- **SQLite concurrency**: all `sqlite3.connect()` calls use `timeout=10`; all DBs init with `PRAGMA journal_mode=WAL` (dual-instance safe)

## Beta Productization

- **Deployment mode**: env var `LIVECHORD_MODE` (priority) → `data/settings.json` `"deployment_mode"` (fallback) → `"personal"` (default)
- **Dual-instance**: NUC runs two uvicorn processes via [start_dual.bat](start_dual.bat) — Personal on 8800, Beta on 8801
- **LAN bypass** (personal mode): LAN IPs (`192.168.x.x`, `10.x.x.x`, `127.x.x.x`) auto-authenticated as admin, no login needed
- In beta mode: feedback UI, bug report FAB, analytics tracking, local audio playback, process page are enabled; admin paths restricted to LAN
- In personal mode: all beta features hidden, full NAS path visibility, zero login friction on LAN
- **Phase 1 modules**: `feedback_api.py` (ratings + bugs), `analytics_api.py` (usage tracking)
- **Phase 2 modules**: `process_queue.py` (job queue + worker + audit + melody extraction + result reuse via `find_existing_result`/`write_reuse_audit`), `process_api.py` (upload/YouTube endpoints, URL normalization)
- **Invite code system**: multi-code with expiry, managed via Admin page → "Beta 回饋" card
- **Security**: rate limiting on auth endpoints, password ≥8 chars, token 30-day expiry, XSS sanitization on all user inputs, admin IP restriction (LAN-only in beta mode)
- **NAS privacy**: non-admin beta users cannot browse NAS; search results use hashed paths; `/api/browse` returns 403
- **TOS**: `/tos` page, consent required before using process features
- **Process flow**: upload audio (200MB max) or YouTube URL → queue → BTC GPU detection → chord JSON → player `?hash=`
- **Upload-to-play**: IndexedDB passes audio blob from process/homepage to player for instant auto-play
- **YouTube embed sync**: player embeds YouTube IFrame, uses `getCurrentTime()` for real-time chord sync
- **YouTube auto-search**: for database songs, `/api/process/youtube-search` finds matching YouTube video via yt-dlp
- **Cover art**: uploaded files have covers extracted via mutagen; YouTube uses `img.youtube.com` thumbnails
- **Beta homepage**: non-admin sees floating action button (FAB) for upload/YouTube + wrapping grid library with vertical scroll + analysis history (replaces music library)
- **Prerequisites for YouTube**: `yt-dlp` + `ffmpeg` must be on NUC system PATH
- Productization roadmap: [doc/PRODUCTIZATION.md](doc/PRODUCTIZATION.md)

## Reference

- QA protocol, test matrix, UI architecture rules: [doc/QA.md](doc/QA.md)
- Battle stories / past incidents: [doc/QA_BATTLE_STORY.md](doc/QA_BATTLE_STORY.md)
- Productization roadmap: [doc/PRODUCTIZATION.md](doc/PRODUCTIZATION.md)
