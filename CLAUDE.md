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
- **QA artifacts are git-ignored**: screenshots named `mobile-*.png`, `qa-*.png`, `editor-*.png`, `page-*.png`, `screenshot-*.png`, and the `.playwright-mcp/` directory are ignored — save QA captures with one of those prefixes so they don't leak into commits

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
- **Phase 2 modules**: `process_queue.py` (dual-worker: `process-worker` for download+BTC+chord save+DONE, separate `melody-worker` for post-DONE melody extraction; audit; URL→result reuse via `find_existing_result`/`write_reuse_audit`; YT URL→NAS library hash map via `find_library_mapping`/`upsert_library_mapping` table `youtube_library_map`; `_purge_user_hash_refs` cascade), `process_api.py` (upload/YouTube endpoints, URL normalization via `_normalize_youtube_url`, library-map reuse lookup before `find_existing_result`, `POST /api/process/yt-library-learn` endpoint for front-end auto-learn)
- **Invite code system**: multi-code with expiry, managed via Admin page → "Beta 回饋" card
- **Security**: rate limiting on auth endpoints, password ≥8 chars, token 30-day expiry (login regenerates token — old tabs invalidated), XSS sanitization on all user inputs, admin IP restriction (LAN-only in beta mode). Frontend fetch wrapper auto-redirects to `/login` on 401 from `/api/*`
- **Audit cascade**: `delete_audit_entries` collects `result_hash`es, deletes chord/cover/melody JSONs, then calls `_purge_user_hash_refs` to strip matching `__hash/<h>` entries from every user's `recent.json` + `favorites.json`. `get_recent` / `get_favorites` also self-heal orphans at serve time
- **NAS privacy**: non-admin beta users cannot browse NAS; search results use hashed paths; `/api/browse` returns 403
- **TOS**: `/tos` page, consent required before using process features
- **Process flow**: upload audio (200MB max) or YouTube URL → queue → BTC GPU detection → chord JSON → player `?hash=`
- **Upload-to-play**: IndexedDB passes audio blob from process/homepage to player for instant auto-play
- **YouTube embed sync**: player embeds YouTube IFrame, uses `getCurrentTime()` for real-time chord sync. Controls (seek, back-to-start, A-B) route through `_playerSeek/_playerCurrentTime/_playerDuration` which prefer `_ytPlayer` over `audio` when the iframe is live. Active-chord scroll gate uses `_ytActive() && getPlayerState()===1` (YT mode) OR `!audio.paused` (NAS mode) so centering works in both 8800 and 8801
- **YT duration desync detection**: on `onReady` player compares `getDuration()` vs chord JSON `duration` field (populated at chord save via `_probe_audio_duration` → mutagen `audio.info.length`). `_computeChordDuration` trusts only the explicit field (last-chord-end fallback was a false-positive trap for songs with outro silence) — old chord JSONs without duration return 0, desync check skips via its `< 30s` gate. Mismatch thresholds: `|Δ|/D > 10%` → yellow banner, gates chord/piano/key updates (time+progress still update); `|Δ|/D ≤ 5%` → fires `POST /api/process/yt-library-learn` to auto-populate `youtube_library_map` (sessionStorage guard, single-shot per hash+vid)
- **YT embed PiP widget**: `.yt-embed-container` is a floating picture-in-picture with position:fixed + draggable header bar (`.yt-pip-header` with `≡` grip) + resize handle at bottom-right (`.yt-pip-resize` maintains 16:9 body ratio via `setSize` nudge to the YT API so the internal player UI actually scales, not just the iframe box). Close `×` → widget hides, `#ytFloatBtn` (red circular FAB, white play triangle, bottom-right) reappears to re-open. Position/size persisted in `localStorage.livechord_yt_pip`; `hidden` flag cleared on every page load so new navigation always shows the PiP by default. `@media (pointer: coarse)` drives the popup-as-bottom-sheet fallback for the whole toolbar so landscape phones aren't broken by `overflow-x:auto` clipping
- **YouTube auto-search**: for database songs, `/api/process/youtube-search` finds matching YouTube video via yt-dlp. Triggered in both hash mode AND DB-path mode when running in beta. If search fails OR chordData lacks title/URL, `_showYtFallbackPanel()` appears so user can paste correct URL or load local audio file; URL submit also fires `_startAnalysisForUrl()` which posts to `/api/process/youtube` and polls status in a floating progress banner → "看和弦" CTA on done
- **Web Share Target (Android PWA)**: `manifest.json` declares `share_target` → `/share` → `share.html` parses YouTube URL from share intent → redirects to `/?youtube=<url>`; homepage auto-opens YouTube FAB + prefills + submits. iOS Safari does not support Web Share Target (known limitation)
- **Cover art**: uploaded files have covers extracted via mutagen; YouTube uses `img.youtube.com` thumbnails
- **Beta homepage (non-admin)**: search bar is the single entry. Results click → play. Empty-results branch renders "+ 新增此曲" CTA; pressing Enter opens the add-song modal (drop-zone + YT URL input + monotonic progress bar) via `#betaFabPanel` (centered, with `#betaFabBackdrop` click-to-dismiss). Typing/pasting a YouTube URL is detected live (`_YT_URL_RE`, input event) → search dropdown shows "偵測到 YouTube 網址 / 按 Enter 或點此分析" CTA instead of hitting `/api/search` (avoids a misleading "找不到結果"). `_searchTriggerUrlAnalyze(url)` is shared by the input-CTA, Enter key, and the modal URL field. Homepage also shows wrapping grid library + analysis history (no standalone FAB button anymore)
- **Melody-pending banner**: after `_betaPollJob` or the in-player `_showAnalysisBannerDone` navigates to a fresh hash, they write `sessionStorage.livechord_fresh_hash = "<hash>|<ts>"`. Player hash-mode load, if `/api/ai/melody` returns empty, `_maybeStartMelodyPolling()` checks the fresh flag (≤10min match) and polls every 5s for up to 5min; small bottom-left blue banner shows `旋律擷取中，稍後將有伴奏旋律`, and on success hot-swaps `melodyData` + shows a short done-toast. Old/static hashes → no polling. In path mode (8800 personal), `_loadMelody` uses the same bottom-left banner but delayed 600ms so instant cache hits don't flash UI (replaced the prior blocking centered modal that stalled the page for ~1min on uncached extraction)
- **Toolbar SVG icons**: 12 toolbar triggers + Tools/AI-Teaching popup buttons use inline Lucide SVG (`.tb-icon` class, stroke="currentColor") so Android/iOS/desktop render identically; emoji entities would fall back to colored Noto on Android regardless of FE0E variant. Play/pause and mute use two SVGs with `.is-playing` / `.is-muted` class toggle instead of `innerHTML =`. Loop button has 3 states (off/single/favorites=repeat+heart-badge) baked into `_updateLoopUI`
- **Empty chord state**: `loadChords()` renders `chord-empty-state` block with hero "一鍵偵測和弦" button (`#btnDetectHero`, wired via document click delegation to shared `runChordDetection()`). In personal mode, detection auto-fires on chord-empty; in beta admin, hero button waits for click
- **Melody filter (`_filterMelody`)**: two-pass cleanse applied to raw `/api/ai/melody` output before waterfall render. Pass 1 drops low-pitch + low-confidence pitch-tracker noise (`midi < 48 && conf < 0.03`). Pass 2 octave-folds around the median — any note >12 semitones from median is shifted by whole octaves until within ±1 octave. Fixes (a) unplayable wide-range RH and (b) octave-dropped vocal notes colliding with LH accompaniment
- **Prerequisites for YouTube**: `yt-dlp` + `ffmpeg` must be on NUC system PATH
- **YT debug hook**: `window.__lcYtDebug()` in player page DevTools returns live `{hasPlayer, state, currentTime, duration, fillWidth, timeText, timerAlive, lastError, chordDuration, syncDisabled, verifiedOk}` snapshot; `window.__lcYtError` holds the most recent sync-tick exception. Playwright headless cannot play YT IFrame — human-verify with desktop Chrome/Edge; see [doc/QA.md §1.1](doc/QA.md)
- Productization roadmap: [doc/PRODUCTIZATION.md](doc/PRODUCTIZATION.md)

## Reference

- QA protocol, test matrix, UI architecture rules: [doc/QA.md](doc/QA.md)
- Battle stories / past incidents: [doc/QA_BATTLE_STORY.md](doc/QA_BATTLE_STORY.md)
- Productization roadmap: [doc/PRODUCTIZATION.md](doc/PRODUCTIZATION.md)
