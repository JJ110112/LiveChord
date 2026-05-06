# CLAUDE.md — LiveChord

Project-specific guidance for Claude Code working in this repo. Also see [doc/QA.md](doc/QA.md) for the full QA protocol.

## Environment

- **Dev repo**: `c:\Users\hitea\Claude\LiveChord` (git, source of truth)
- **Local testing**: IDE Live Server is installed, Playwright MCP is registered for AI-driven local QA.
- **Prod runtime** (NUC, mounted as `V:\` from PC): backend runs from `V:\backend`, frontend from `V:\frontend`
- **Backend server**: FastAPI/uvicorn, `main:app` — single instance on NUC, port 8800 (start: [start.bat](start.bat) · restart: [restart.bat](restart.bat)). `LIVECHORD_MODE` defaults to `personal`. The dual-instance scripts ([start_dual.bat](start_dual.bat) / [restart_dual.bat](restart_dual.bat)) and beta-only port 8801/8802/8803 are archival — kept on disk for reference but not in the live workflow
- **Production QA Server**: `http://192.168.50.6:8800/` (LAN, NUC personal). `https://livechord.org` is the **public VPS** deployment (Hetzner CPX21 Hillsboro OR, `5.78.135.8`) — separate codebase clone at `/srv/livechord`, deployed via `git pull` (see [doc/OPS.md](doc/OPS.md) "Deploying code changes to the VPS"). NUC's cloudflared is disabled, so livechord.org is VPS-only
- **Production Admin Account**: User: `hitea` (password user-managed; first-registered user is auto-promoted to admin by `auth_api.init_db`, no seeded account)
- **Admin page**: `http://localhost:8800/admin`

## Post-Beta Status (split deployment — NUC personal + VPS public)

The 2026-04-16 → 2026-04-26 invite-only beta on `livechord.org:8801` ended on 2026-04-26. Going forward LiveChord runs split:

- **NUC** — single uvicorn on `192.168.50.6:8800`, `LIVECHORD_MODE=personal`, LAN bypass. Personal/admin use only. Started via [start.bat](start.bat) / [restart.bat](restart.bat).
- **VPS (Hetzner CPX21 Hillsboro OR, IPv4 `5.78.135.8`, deployed 2026-05-03)** — `LIVECHORD_MODE=public`, OAuth (Google + Discord), Modal-dispatched BTC + beat_this, R2 cover storage. Serves `livechord.org` via Cloudflare Tunnel `d182dd0a-3655-42db-86e3-b78294aee428` (locally-managed config at `/etc/cloudflared/config.yml`, systemd unit at [deploy/livechord.service](deploy/livechord.service)). Operations runbook: [doc/OPS.md](doc/OPS.md). **Public mode is upload-only as of 2026-05-04 (Plan B)** — every `/api/process/youtube*` endpoint returns 404, frontend hides every YT entry point, and the cookies refresh apparatus is no longer in use. Don't propose re-enabling YT for public via proxies/automation — explicitly rejected by user on hobby/legality grounds.

NUC's cloudflared service is **disabled** (2026-05-03) so VPS is the sole tunnel replica going forward. The beta-mode code paths, dual-instance scripts, and `LIVECHORD_MODE=beta` gates are kept inert in the codebase so the deployment can be re-enabled later without re-implementing — but they are *not* exercised in the current workflow and you should not assume they're running.

The user-facing data left over from the beta (`feedback.db`, `auth.db`, `audit.db` `process_audit` rows, `youtube_library_map`, `data/human_feedback/`, `data/human_sections/`) is **kept** — it's training signal for the AI quality pipeline below, and any historical analyses still resolve. Treat these tables as append-only history.

**Public-mode homepage flow** (anti-foot-gun reminder — bit us once on the "guest can't log in" bug):

- Two visual states only: **logged-in dashboard** (recent / favorites / local-music, header visible) vs. **logged-out marketing landing** (intro + hero + how-it-works + open-source, header hidden — clean sign-up funnel)
- Hero "Get started for free" CTA: **always** routes to `/login` when no token. Do not special-case on `livechord_guest_acked` — that flag is sticky in localStorage and any CTA branch keyed off it makes `/login` unreachable for returning users
- Guest upload entry: `/login` → "Continue as guest" link redirects to **`/?upload=1`** → [frontend/js/app.js](frontend/js/app.js) reads the query param on load and auto-opens the upload modal, then strips the param via `history.replaceState` so a hard-reload doesn't re-trigger
- `livechord_guest_acked` is vestigial: set on guest-ack (login.html), cleared on logout (index.html `logout()`), but **not read by layout or CTA logic**. Logout also redirects to `/` for all modes so logged-out users always see the marketing landing

**Default workflow for any change**:

1. **Edit only in the dev repo** (`c:\Users\hitea\Claude\LiveChord`). Never edit `V:\` directly — commits won't pick it up and rollbacks are harder
2. **QA on PC localhost first**. Two options:
   - PC personal local on 8803 via [start_personal_local.bat](start_personal_local.bat) — same `LIVECHORD_MODE=personal` semantics as NUC, isolated `data/` so experiments don't touch the NUC corpus
   - Direct QA against NUC 8800 over LAN once the change is shipped, since it's the only live target
   - 8801 / 8802 (beta-mode local) are no longer part of the routine. Only spin them up when explicitly testing `LIVECHORD_MODE=beta`-gated code (forced login, hashed paths, invite flows) before re-enabling beta — if you do, see the "Beta Productization (archival)" section below for the gating details
3. **Ship to V:\ only after local QA passes**. Copy via `cp` + verify with `diff -q`
4. **Announce restarts**: backend `.py` changes don't auto-reload on NUC (uvicorn has no `--reload` in prod) — the user must run [restart.bat](restart.bat) before changes take effect on 8800. Surface this in your summary so it isn't forgotten

**When stopping to ask before acting**:
- Any backend change that touches `process_audit`, `feedback.db`, `auth.db`, `settings_personal.json`, `settings_shared.json`, `youtube_library_map`, or training-corpus state under `data/` — beta-era user data is now AI training input, regressions are silent
- Batch file renames / deletions under `V:\`
- Anything that invalidates outstanding auth tokens (schema change, secret rotation)
- Destructive sqlite operations on any DB path under `V:\data\`
- "Let me clean up this old file" instincts — check `git log` first; old-looking files (especially under `backend/` related to `LIVECHORD_MODE=beta`, invites, hashed paths, share-target) may be inert but kept on purpose for re-enabling beta

**Safe to just do** (after local QA):
- Frontend CSS/JS/HTML edits with proper `?v=N` bump (users hard-reload to pick up)
- New read-only endpoints
- Log-only or telemetry-only changes
- New columns with default values (backward-compatible migrations)

**Cache-bust discipline — easy to forget**: if you change `.js`/`.css`, bump the corresponding `?v=N` in the referencing HTML. Miss the bump → users see stale JS from browser cache → your "fix" looks broken to them even though V:\ is correct. Rule of thumb: edit a `.js` → bump its `?v=` in the same commit.

**Reverts**: if you need to roll back, `git restore <file>` in the dev repo then re-sync to V:\. Don't try to reverse-edit V:\ in place — you'll diverge.

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

- **Long-running operations (>5s) MUST be loosely coupled from the client** — never make the user's browser sit on a synchronous request waiting for a backend job that takes tens of seconds. The pattern (canonical example: [backend/beat_upgrade_queue.py](backend/beat_upgrade_queue.py) + `POST /api/process/upgrade-beats` + `GET /api/process/upgrade-beats/status`):
  1. **Pre-flight validation synchronously** in the POST/PUT endpoint (auth, file existence, missing prereqs) so 4xx/5xx surface immediately
  2. **Enqueue + return** with a job identifier (or song hash) — request closes in <100ms
  3. **Daemon worker thread** processes the job (fresh in-memory queue per concern; reuse `process_queue` only when the work IS audio-ingest)
  4. **Status endpoint** returns the in-memory record (`queued / running / done / error` + result/error details)
  5. **Frontend polls every 3-5s** in a non-blocking interval; on completion fires a toast that works whether the user stayed on the page or navigated within the SPA
  6. **No partial commits**: status flips from `running` → `done` only after atomic file write completes (use `.tmp` + `os.replace`)
  - Counter-examples to avoid: melody extraction was once a 40s blocking call → fixed by handing off to a separate worker queue with status polling. Beat upgrade was first written as synchronous → user feedback "client 綁住無回應" → refactored to this pattern. Treat **any** new feature that takes >5s as a candidate for this discipline before writing the endpoint.
- **No `async def` for file I/O endpoints** — use plain `def` so FastAPI dispatches to the thread pool (see `feedback_async_def`). Applies to retrain/training endpoints too: `/api/ai/retrain` was `async def` and froze the event loop during chord2vec SVD — flipped to `def`
- **Cache-busting**: bump `?v=N` on `<script>`/`<link>` tags when editing JS/CSS (QA UI rule 6)
- **BTC chord detection** runs in a `ProcessPoolExecutor` to isolate from the event loop GIL
- **Pure-Python loops on daemon threads still hold the GIL** — auto_worker runs on a daemon thread but its `build_co_occurrence_matrix` in [backend/ai/chord2vec.py](backend/ai/chord2vec.py) is a nested Python loop over ~50k sequences that starved every request thread. Fix: `_train_via_subprocess` spawns `sys.executable chord2vec.py` so training lives in a fresh interpreter with its own GIL. Apply the same pattern to any future long Python-loop workload even when it's already off the event loop
- **Subprocess calls that read user-facing text MUST specify `encoding="utf-8", errors="replace"`** — NUC default code page is cp950; without explicit UTF-8 decode, CJK/Hangul characters in subprocess stdout get dropped (e.g. external metadata tools). Prefer JSON output formats so the byte stream is always UTF-8 regardless of console locale.
- **SQLite concurrency**: all `sqlite3.connect()` calls use `timeout=10`; all DBs init with `PRAGMA journal_mode=WAL` (dual-instance safe)
- **madmom install on Windows** (rubato beat tracking, see [QA_BATTLE_STORY.md 番外篇 VII](doc/QA_BATTLE_STORY.md)) — three-step gauntlet, all required:
  1. **MSVC Build Tools** — runtime-only Windows machines (NUC, GH Actions Win runner) lack the C++ toolchain. Get [vs_BuildTools.exe](https://aka.ms/vs/17/release/vs_BuildTools.exe) (4.25 MB), run `--quiet --wait --norestart --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended` (~2-3 GB). PC dev boxes typically already have it via Visual Studio
  2. **Cython + numpy first** — `pip install Cython numpy` because madmom 0.16.1's `pyproject.toml` doesn't declare them as build deps, so PEP 517 isolation fails
  3. **Git master + `--no-build-isolation`** — `pip install --no-build-isolation git+https://github.com/CPJKU/madmom.git`. The released wheel (0.16.1, 2018) imports `collections.MutableSequence` which Python 3.10+ moved to `collections.abc`; only the unreleased master is patched
  - Fallback when madmom missing: [beat_snap.analyze_and_snap_dynamic](backend/beat_snap.py) auto-degrades to librosa with `beats_source: "librosa-fallback"` — chord JSONs without rubato fields, but everything still runs
- **BPM ballad halving** ([backend/bpm_sanity.py](backend/bpm_sanity.py)): beat trackers sometimes lock onto doubletime for slow pop ballads (detected 138 BPM for a 69 BPM song), cascading into dense beat dots + over-segmented phrases + dance-pop accompaniment + misplaced Jazzify insertions. `ballad_halving_check(bpm, audio_path)` runs at ingest inside [process_queue.py](backend/process_queue.py) after `analyze_and_snap_dynamic`, three-gate AND: `bpm∈[130,150]` AND `onset_density<3.0/s` AND `rms_cov<0.015`. All three must agree — intentionally conservative to never halve a genuine 140 BPM pop song. On hit: halves `bpm` + `tempo_curve` BPMs, thins `downbeats[::2]`, writes `bpm_correction={applied, reason, onset_density, rms_cov, original}` into chord JSON. Downstream consumers read the corrected `bpm` field:
  - Beat dots in player auto-respace via [player.js `durSec / (60/BPM)`](frontend/js/player.js#L1492)
  - [section_detect.py `_estimate_bpm_and_window(hint_bpm=…)`](backend/ai/section_detect.py) preferred over chord-duration reverse-estimate — halved BPM 70 gives ~6.8s window (was ~4.0s), phrases no longer shatter
  - [accompaniment_generator.py `suggest_style(bpm)`](backend/ai/accompaniment_generator.py) auto-routes to `Arpeggio/Shell` slow-ballad templates because corrected BPM falls below the 80 threshold
  - [reharmonizer.py `_min_insert_duration(bpm)`](backend/ai/reharmonizer.py) scales the 1.2s Jazzify ii-V / secondary-dominant insert threshold to ~1.71s at 70 BPM so syncopations aren't crammed into half-measures
  - **No backfill**: existing library + beta-user chord JSONs untouched — admin-only per-song recompute via `POST /api/admin/bpm/recompute` (auto/halve/double), wired into Admin page 和弦管理 card. Rationale: beta users have stored ratings and saved style preferences keyed to current `bpm`; bulk re-halving would silently regress them
  - **Auto mode needs the audio file**: `mode=auto` re-runs `ballad_halving_check` which calls `librosa.load(audio_path)`. Uploaded songs have their tmp audio cleaned right after analysis (see [process_queue.py `TMP_DIR.iterdir()` cleanup](backend/process_queue.py)), so auto mode returns `400 audio file not available; use halve/double` for them. Library songs on NAS still work. For uploads past cleanup, `halve/double` skip the audio check and operate directly on the persisted `bpm` field
  - **Player tooltip**: `#chordBpm` shows ⓘ suffix + `已自動修正 (原 N BPM，判為慢歌倍拍)` when `bpm_correction.applied=True`, so users can see the correction happened and still override via the existing `bpmMult` click-cycle
- **Admin endpoint auth**: use `Depends(get_admin_user)` from [auth_api.py:252](backend/auth_api.py#L252) — NOT `Depends(get_current_user)` + manual `_is_admin(username)` check. Reason: LAN bypass in `get_current_user` returns the literal string `"admin"`, but the seeded admin row has the user's actual username (e.g. `hitea`), so `_is_admin("admin")` queries a non-existent row → False → 403 on personal-mode LAN. `get_admin_user` bakes the LAN bypass → always-admin truth-table in one place; every other `/admin/*` endpoint already uses it (`POST /api/admin/invite`, `GET /api/admin/ratings`, etc). Bit us once on `POST /api/admin/bpm/recompute`
- **Admin UI error surfacing**: `API.post` in [api.js](frontend/js/api.js) throws with only `${res.status} ${res.statusText}` — it does NOT read the response body's `detail` field. Admin/error UIs that need to show the backend's `HTTPException(..., detail="why")` must use plain `fetch` + `await res.json().catch(() => ({}))` then render `data.detail`. Otherwise users see `失敗: 400 Bad Request` with no hint why. Pattern: see [admin.html `btnBpmRecompute` handler](frontend/admin.html)

## Beta Productization (archival — code paths kept inert)

The beta phase wound down 2026-04-26 (see Post-Beta Status above). The `LIVECHORD_MODE=beta` gates, beta-only endpoints, hashed-path search, invite flow, dual-instance scripts, and beta-only frontend branches are **all still in the codebase** but not exercised by the live deployment. The bullet list below documents how those gates work so you can re-enable them or test changes that touch them.

> ⚠ **2026-05-04 — yt-dlp + YouTube extraction REMOVED for the open-source release.** Bullets below mentioning `_get_youtube_title` / `_download_youtube` / `find_existing_result` / `find_library_mapping` / `youtube_library_map` / `_searchAndEmbedYouTube` / `_initYouTubeEmbed` / `_ytPlayer` / `_addPlaylist` / `_searchTriggerUrlAnalyze` / `share_target` / Web Share Target / `/api/process/youtube*` endpoints / `frontend/share.html` / the player YT iframe PiP widget / `yt_dlp_fetch.py` are **historical only** — those modules and call sites have been deleted (commits prefixed `chore(yt-removal stage N/4)`). Personal NUC use moved to CD-track ingest only. The `youtube_url` field on chord JSONs is read-back compatible (won't crash on legacy data) but never written or acted on. Re-enabling YT requires reverting those commits, not flipping a flag.

- **Deployment mode**: env var `LIVECHORD_MODE` (priority) → `data/settings.json` `"deployment_mode"` (fallback) → `"personal"` (default)
- **Dual-instance** (archival): the NUC could run two uvicorn processes via [start_dual.bat](start_dual.bat) — Personal on 8800, Beta on 8801. Currently only the Personal 8800 instance runs
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
- **YT embed PiP widget**: `.yt-embed-container` is a floating picture-in-picture with position:fixed + **transparent dragzone overlay** (`.yt-pip-dragzone`, z-index:3) catching all pointer events: tap (<6px movement) re-routes to `_ytPlayer.playVideo()/.pauseVideo()`, drag (≥6px) moves the widget (top clamped ≥44 to avoid `.player-topbar`). Lock toggle `🔒`/`🔓` (`.yt-pip-lock-float`, positioned next to `×` at `bottom:2px; left:22px` so it doesn't cover YT's bottom-center CC) flips `.unlocked` class → dragzone goes `pointer-events:none` so user can reach YT native captions/settings/fullscreen. Resize handle (`.yt-pip-resize` z-index:5) at bottom-right maintains **pure 16:9** (container IS the body, no header reservation), driving `setSize` nudge to YT API with the actual container dims so internal player UI scales. Close `×` → widget hides, `#ytFloatBtn` (red circular FAB, white play triangle, bottom-right) reappears to re-open. **CSS-selector gotcha**: `new YT.Player("ytEmbed", …)` REPLACES the `<div id="ytEmbed" class="yt-pip-body">` with a fresh iframe that **loses all custom classes** — so `.yt-pip-body iframe` stops matching after init. Fill rules must target `#ytEmbed` directly (the id survives replacement). Position/size persisted **per orientation** in `localStorage.livechord_yt_pip` (shape `{hidden, p:{x,y,w,h}, l:{x,y,w,h}}`); `resize` event re-applies on rotate. `_applyYtPipState` clamps saved x/y/w/h to current viewport so coords from a larger window can't park the PiP off-screen (earlier bug: `onReady` fired, toast showed, but user saw nothing). `hidden` flag cleared on every page load. `@media (pointer: coarse)` drives popup-as-bottom-sheet fallback + `.tb-popup-wide` wrap so touch devices aren't broken by `overflow-x:auto` clipping; JS touch gate uses `'ontouchstart' in window || matchMedia('(pointer:coarse)').matches` so Chromium DevTools emulation + real mobile both bind popup handlers
- **YouTube auto-search**: for database songs, `/api/process/youtube-search` finds matching YouTube video via yt-dlp. Triggered in both hash mode AND DB-path mode when running in beta. If search fails OR chordData lacks title/URL, `_showYtFallbackPanel()` appears so user can paste correct URL or load local audio file; URL submit also fires `_startAnalysisForUrl()` which posts to `/api/process/youtube` and polls status in a floating progress banner → "看和弦" CTA on done
- **Web Share Target (Android PWA)**: `manifest.json` declares `share_target` → `/share` → `share.html` parses YouTube URL from share intent → redirects to `/?youtube=<url>`; homepage auto-opens YouTube FAB + prefills + submits. iOS Safari does not support Web Share Target (known limitation)
- **Cover art**: uploaded files have covers extracted via mutagen; YouTube uses `img.youtube.com` thumbnails
- **Beta homepage (non-admin)**: search bar is the single entry. Typed/pasted YouTube URL is detected live (`_YT_URL_RE`) and routed by shape:
  - **Single-video URL** → "偵測到 YouTube 網址" CTA → `_searchTriggerUrlAnalyze(url)` opens the add-song modal with URL prefilled + auto-fires 分析
  - **Playlist URL** (`&list=PL...` via `_YT_PLAYLIST_RE`) → "偵測到 YouTube 播放清單" CTA → `_addPlaylist(url)` fetches `/api/process/playlist-info`, persists `{list_id, title, videos:[{video_id, title, duration, existing_hash}]}` in `localStorage.livechord_yt_playlists`, renders in homepage section `#secBetaPlaylists` as expandable cards. Each video: ▶ 播放 if `existing_hash` known (library_map hit OR prior audit), 分析 otherwise (one-at-a-time via existing YT upload flow; `_currentAnalyzingPlaylistVid` stamps `existing_hash` back on done)
  - **Plain text + Enter** → empty add-song modal opens
  - **Plain text typed** → hits `/api/search`. In **beta non-admin** the endpoint short-circuits to return **only the user's own analyzed songs** (scans `process_audit` where `username=? AND status='done'`) — NAS library results are intentionally hidden because library tracks were chord-analyzed from album masters while beta users are almost always looking for YouTube MV versions; surfacing the library match caused "same title, wrong audio length" confusion. Personal (8800) and beta admin still get the merged result: user_uploads on top, then library. `user_uploads` album label routes off `source_type`: `"upload"` → `"本機上傳"`, `"youtube"` → `"YouTube 分析"` (don't mislabel YT analyses as local uploads)
- **Search placeholder marquee — dynamic text** ([frontend/js/utils.js](frontend/js/utils.js) `updateSearchMarqueeText`): runs on DOMContentLoaded for both homepage + player topbar. Personal/admin gets the long copy immediately ("請輸入歌曲、專輯、藝人或YouTube URL...", synchronous — no flash). Beta user defaults to short copy ("請輸入 YouTube URL...", matches HTML default) and only upgrades to long after `/api/process/my-history?limit=1` returns non-empty. Rationale: fresh beta users have nothing to search *within* until they've analyzed at least one song, so prompting them with "歌曲、專輯、藝人" when the backend has zero results for those tokens is misleading
- **Search-result routing — beta hash vs personal path**: on 8801 `/api/search` rewrites `path` to `__hash/<hash>` and emits an extra `hash` field (NAS privacy); on 8800 `path` is the real NAS path and `hash` is absent. Frontend click handler (`.result-item` in [frontend/js/player.js](frontend/js/player.js)) must stamp `data-hash` and route `?hash=<h>` when present, else `?path=<p>`. Naively passing `__hash/<hash>` as `?path=` lands in NAS-stream mode with a pseudo-path that 404s → "請選擇本地音檔" prompt appears, even though the song has a known YT URL
- Homepage sections shown to non-admin: 最近播放 (filled by `_loadBetaHistory`, merges `/api/process/my-history` + `/api/recent` deduped by title) · **本機音樂** (`#secBetaLocalTracks`, `<input type=file multiple>` persistent registry: metadata in `localStorage.livechord_local_tracks`, blobs in IndexedDB keyed by local_id; click unanalyzed → upload modal, click ▶ analyzed → `/player?hash=...`; dedup by name+size+lastModified) · **YouTube 播放清單** (`#secBetaPlaylists`) · 我的最愛. `#secHistory` "音樂庫" grid removed (redundant vs the three above)
- **Add-song modal file-only mode**: `.beta-fab-panel.file-only` class hides `#betaDropZone` + `.add-song-yt-row` so opening the modal from a local-track analyze button doesn't show two "分析" buttons (file + URL row). Class added only by `_onLocalTrackAction`; every other modal open explicitly removes it
- **Melody-pending banner**: after `_betaPollJob` or the in-player `_showAnalysisBannerDone` navigates to a fresh hash, they write `sessionStorage.livechord_fresh_hash = "<hash>|<ts>"`. Player hash-mode load, if `/api/ai/melody` returns empty, `_maybeStartMelodyPolling()` checks the fresh flag (≤10min match) and polls every 5s for up to 5min; small bottom-left blue banner shows `旋律擷取中，稍後將有伴奏旋律`, and on success hot-swaps `melodyData` + shows a short done-toast. Old/static hashes → no polling. In path mode (8800 personal), `_loadMelody` uses the same bottom-left banner but delayed 600ms so instant cache hits don't flash UI (replaced the prior blocking centered modal that stalled the page for ~1min on uncached extraction)
- **Homepage gear settings menu** ([index.html `#btnHeaderSettings`](frontend/index.html) + [base.css `.header-nav` + `.hn-row`](frontend/css/base.css)): the topbar collapses Help / Sponsor / Language / Install / Sign-out into a single ⚙ trigger at the far right (replaces the prior ⋮ that only collapsed on mobile). Theme picker **moved here from the player Tools popup** — it's a global setting, so the homepage gear is the canonical entry point; player.html no longer carries `.theme-opt` buttons. The popup is a uniform list-row layout that uses `var(--lc-popup-bg)` + `var(--text)` so it inherits whichever of the 6 themes is active. Coffee/sponsor pill (`.header-coffee-btn`, warm-amber linking to `/sponsor`) sits immediately left of the gear so support stays one tap away. Search-bar magnifying glass uses an SVG `mask` painted with `currentColor` instead of the colored 🔍 emoji so it renders monochrome on every theme/OS.
- **Jianpu visibility toggle** ([player.html `#btnToggleJianpu`](frontend/player.html) + [player.js `_applyJianpuVisibility`](frontend/js/player.js)): player Tools popup → 簡譜 row flips `body.no-jianpu`, hiding `.rv-jianpu` (chord ribbon) + `.chord-jianpu` + `.melody-jianpu` (88-keys ribbon). Persisted in `localStorage.livechord_show_jianpu`. Default ON. Lets players who don't read jianpu silence the numerals across both ribbon variants together.
- **Light-theme canvas ink helper** ([chord-render.js `_crInk()`](frontend/js/chord-render.js) + [string-instrument.js `_rhInk()`](frontend/js/string-instrument.js)): canvas-drawn fretboard / waterfall string lines & labels need to flip white-on-dark → black-on-cream when `<html data-theme>` is one of `light/sakura/sunny/sky`. Both helpers read the attribute directly (no opts threading) and apply a 1.4× alpha bump on light bg so faint strokes stay visible. `drawVerticalFretboard` (left-pane fretboard), `drawGuitarFretboard` (mini chord diagram), and `_drawRhWaterfall` (RH PIMA strum waterfall) all routed through these. Don't add new white-RGBA literals — call `_crInk(a)` or pass through the same pattern.
- **88-key piano height** (subtle gotcha — bit us once): `.chord-88-keys` is the WRAPPER for both the waterfall canvas AND the piano canvas. Don't `flex: 0 0 auto` it or cap its height — the waterfall needs to fill vertical space inside it. Current layout: `.chord-88-keys { flex: 1; min-height: 0 }`, waterfall `flex: 1 1 auto; min-height: 120`, piano canvas `flex-shrink: 0; margin-top: auto` (intrinsic ~200px from JS, pinned to bottom). The original keyboard-clipping fix lives in [`_get88PianoMaxWidth`](frontend/js/player.js) capping with `reservedH = 178` so the canvas height never exceeds ~200px regardless of container size — that's where the protection should stay.
- **Toolbar SVG icons**: 12 toolbar triggers + Tools/AI-Teaching popup buttons use inline Lucide SVG (`.tb-icon` class, stroke="currentColor") so Android/iOS/desktop render identically; emoji entities would fall back to colored Noto on Android regardless of FE0E variant. Play/pause and mute use two SVGs with `.is-playing` / `.is-muted` class toggle instead of `innerHTML =`. Loop button has 3 states (off/single/favorites=repeat+heart-badge) baked into `_updateLoopUI`. `#tbLocalFile` removed from toolbar (replaced by homepage 本機音樂 section). Speed cycle reordered `1× → 0.75× → 0.5× → 1.25× → 1.5× → 2×` — students usually want to slow down first, not speed up
- **Chord ribbon scale**: inline `[−] [val] [+] [☰ 總覽]` row in `.ribbon-scale-bar` (was a popup, user couldn't revert the value). On mobile portrait ribbon is upper 50vh with `display: grid; grid-template-columns: repeat(auto-fit, minmax(calc(140px * var(--ribbon-scale, 1)), 1fr))` so `+/-` grows cards in both dimensions (not just height)
- **Chord ribbon contrast tiers** ([player.css `.rv-item`](frontend/css/player.css) + [player.js `_updateActiveChord`](frontend/js/player.js)): four visual states instead of binary active/inactive. `played` 0.3 · default/far-ahead 0.72 + subtle `rgba(255,255,255,0.03)` bg + 1px divider · `upcoming` (next 2nd–3rd) 0.9 · `upcoming-next` (immediate next) 0.95 + `rgba(33,150,243,0.07)` tint + blue left-border · `active` 1.0 + `rgba(33,150,243,0.18)` + 4px accent border + gradient chord name + glow. Prior design leaked 100% of the contrast budget onto `active` (inactive was opacity 0.5 on transparent bg → cards blurred together, no read-ahead cue). JS tags `i+1..i+3` with `.upcoming` (+`.upcoming-next` on `i+1`) on each active switch, clears prior window both on advance and on song-end. Why: real users on the Ribbon wanted to read ahead 1–3 chords while playing, and browse the progression while paused; the old contrast jump 0.5→1.0 with no middle tier made both hard
- **Waterfall pedal — left-gutter design** ([player.js `drawWaterfall` Phase 11 pedal block](frontend/js/player.js#L2922)): pedal sustain indicator is a 10px saturated green stripe on the left edge of the canvas (`rgba(76,175,80, 0.4 + depth*0.25)`); full-width wash dropped from `depth*0.15` to `depth*0.05` (nearly invisible) so note bars keep their contrast. Prior design filled the entire pedal region with 15% green across full canvas width — on slow ballads with long sustain regions, the accompaniment note bars (blue LH / orange RH) competed with the green wash and finger numbers became hard to read. Horizontal boundary line at pedal start kept (solid for depth≥1, dashed 3×3 otherwise). The gutter gives peripheral awareness without touching the note area
- **自動切分 panel — 3 modes** ([player.js `_showAutoSplitPanel`](frontend/js/player.js) + [chord-correction.js `inferBeatsPerBar` / `nextDownbeatAfter`](frontend/js/chord-correction.js)):
  1. **依小節切分** (default) — any chord longer than one bar is sliced into bar-sized cards. `beatsPerBar` resolves from user's TS override → `inferBeatsPerBar(chordData.downbeats[])` → fallback `4`. If a chord starts mid-bar, first cut fills the current bar (anchored to the next real downbeat or a synthetic `phase + k·barDur` grid), remaining beats cut into full bars, tail ≤ barlen stays.
  2. **對齊小節線** — *destructive* boundary relocation. Walks every inner `chord[i].end == chord[i+1].time` and snaps it to the nearest bar line within ±½-bar tolerance, then runs the bar-split sweep for any chord still > 1 bar. Preserves `chord[0].time` and `chord[last].end` (song endpoints don't move); rejects snaps that would collapse a chord to < ½ beat or overlap a neighbour. **Changes which chord is "active" at the snapped moment** — show an orange warning in the panel. Why lossy: BTC sometimes places boundaries off bar lines deliberately (syncopation, anacrusis); user should run 還原 if the snap over-squares a legitimately syncopated part.
  3. **依比例切分** (legacy fallback) — original `threshold + ratio` behaviour, untouched, for users who want proportional splits (1:1, 1:3, 1:1:1 etc) independent of meter.
  - Shared UI: bar+barsnap share the `.as-bar-section` with TS chips (`自動 (N) / 3/4 / 4/4 / 6/8 → 6`); ratio hides it. `localStorage.livechord_auto_split = {mode, tsOverride, threshold, ratio}` persists per-user.
  - **Doesn't auto-populate downbeats** — librosa-fallback songs (the ingest default, because [beat_snap.analyze_and_snap_dynamic(prefer_madmom=False)](backend/beat_snap.py) keeps ingest fast) have `downbeats=[]` and the tool drops to the synthetic grid. Precise bar alignment requires either running 升級節拍 (`POST /api/process/upgrade-beats`) so madmom populates `downbeats[]`, or letting the new `bar_arbitrator` / `beat_refiner` pipeline (see "AI Quality Pipeline" below) re-derive them at ingest. Intentional design trade-off — ingest stays ~2s fast, user opts into the slower run per song when BPM/alignment looks wrong.
- **Empty chord state**: `loadChords()` renders `chord-empty-state` block with hero "一鍵偵測和弦" button (`#btnDetectHero`, wired via document click delegation to shared `runChordDetection()`). In personal mode, detection auto-fires on chord-empty; in beta admin, hero button waits for click
- **Fingering display**: waterfall intentionally does NOT render finger numbers (too cluttered) — reserved for the 88-key piano area. `fingeringMap` populates from `accData.left_hand/right_hand` (has `.finger`); for hash-mode songs without accData, the fallback branch synthesizes LH fingering 5-3-1 / 5-3-2-1 / 5-4-3-2-1 from the current chord voicing (sorted low→high) so the keyboard still shows numbers when `showFingering` toggle is ON. RH melody has no reliable fingering (API doesn't return it, no phrase context), so it shows no number
- **Hash-mode AI accompaniment parity**: `_loadAccompaniment()` used to hard-gate on `trackPath` being non-empty, so 8801 beta hash-mode songs always fell back to chord-voicing blocks (flat LH, no RH beyond melody). `_accPath()` helper (`trackPath || chordData.path`) unlocks the endpoint for hash mode — backend `song_hash(path)` produces the same hash for `__upload/<job_id>` that process-queue used at save time, so the same chord JSON is found. The hash-mode chord-load branch also calls `_loadAccompaniment()` once (DB-path mode already triggers on piano tab activation). Result: 8801 hash-mode now renders per-onset LH/RH bars + velocity + finger + pedal, matching 8800 path-mode
- **RH content mode (`rhContentMode`)**: `#btnRhContent` in AI-teaching popup cycles `acc → mel → both` (labels 伴/旋/全), persisted in `localStorage.livechord_rh_mode`. Gates what goes into the RH waterfall AND the keyboard `activeRh` — both read the same flag so keyboard highlights and falling bars never disagree. `acc` mode with empty `accData.right_hand` auto-falls-through to melody so user isn't left with an empty RH
- **Jazzify AI transformer time-stretch**: [reharmonizer.py `_apply_transformer`](backend/ai/reharmonizer.py) was accumulating `current_time += dur_val` freely bounded by `max_len=128` + model-learned EOS, producing a chord timeline ~1/3 the length of the original song (user saw chords ending at 0:49 on a 3-minute track). Post-decode, if `|orig_span - trans_span| > 1s`, rescale every chord's `time/end` by `scale = orig_span / trans_span` so the jazz progression spreads across the full song. Rule-based (L1/L2/L3) paths already preserve timing via in-place splits, untouched
- **Jazzify empty-chord resilience**: on the rare path where the API returns `res.chords = []` (AI glitch), the button used to pin on AI state — next click hit the `chordsEmpty` guard and returned without advancing `jazzifyLevel`. Guard now requires `chordsEmpty && !originalChords` so the cycle can still reach state 0 (restore-from-backup); additionally `!Array.isArray(res.chords) || !res.chords.length` throws so the catch branch resets the button immediately
- **Three-tier mobile toolbar**: `.bottom-toolbar` wraps its `.tb-item`s into three `.tb-row` groups — `tb-row-t1` (Speed/Loop/Transpose/Volume, 最常用練習設定), `tb-row-tx` (Prev/Play/Next + times, transport), `tb-row-t2` (Instrument/Jazzify/A-B/Teaching/Tools/BugReport, 次常用). Desktop keeps rows flowing inline as a single logical row; `@media (max-width:640px) and (orientation:portrait)` stacks them vertically with `flex-direction:column`. Landscape stays single-row to preserve vertical viewport budget. Inspired by Chordify/Chord ai's two-tier layout after user feedback "手機一排擠 12 個 icon 看不到"
- **State-cycle → absolute-value popup**: Speed / Loop / A-B / Jazzify used to cycle silently on click (`1× → 0.75× → 0.5× → …`), which users mistook for "沒反應". Now each has a `.tb-popup` listing all options (`.speed-opt`, `.loop-opt`, `.ab-opt`, `.jazz-opt` data-attrs). Setters are extracted from cycle handlers (`_setSpeed / _setLoopMode / _handleAB / _setJazzifyLevel`) and bound to popup buttons; cycle handlers stay wired on the trigger but gated by `if (_isTouchLike) return` so desktop click still cycles (power-user shortcut), mobile tap only opens popup. `_syncSpeedUI / _updateLoopUI / _updateABPopup / _syncJazzifyPopup` keep `.active` class and pill label in sync with state changes. Jazzify popup order is `關閉 / ✨AI / L1 / L2 / L3` (most-used on top)
- **5-option popups 2+3 grid**: Instrument and Jazzify popups both have 5 options; on touch portrait `.tb-popup-5 .tb-popup-btn:nth-child(-n+2) { flex-basis: calc(50% - 6px) }` + `:nth-child(n+3) { flex-basis: calc(33.333% - 8px) }` forces 2-on-top / 3-on-bottom layout instead of uneven wrap-by-label-width. AI-Teaching + Tools popups use `flex: 0 0 calc(33.333% - 8px)` 3-column grid with `.tb-separator` getting `flex-basis: 100%` as full-row divider. Landscape overrides everything to `flex: 0 1 auto; min-width: 64px` (natural compact width) + centers popup with `max-width: min(90vw, 520px)` so buttons don't sprawl across 1000+px viewports
- **Touch hit-target rescue**: S22 Ultra field test showed 工具/錯誤回報 buttons "need to tap top 1/5 to trigger" — root causes: (a) Android gesture nav strip (~16-24px) at viewport bottom consumes taps on button lower half, (b) `progress-bar-mid` at `bottom:44px` sat flush against toolbar top, so missed taps hit seek bar. Fixes: viewport meta adds `viewport-fit=cover` (Android Chrome then honors `env(safe-area-inset-bottom)`); `@media (pointer:coarse)` sets `.bottom-toolbar { padding-bottom: max(env(safe-area-inset-bottom), 10px); min-height: 54px }` + `.tb-trigger { min-height: 44px; padding: 8px 10px }` (Apple HIG compliant); progress bar `bottom: calc(54 + inset + 10)` floats 10px above toolbar with `height: 4px` default (6px hover). Portrait 3-row variant overrides `min-height: 148px` and lifts bar+popup accordingly
- **Ghost-click / SVG tap routing**: `.tb-trigger { touch-action: manipulation; -webkit-tap-highlight-color: rgba(0,0,0,0) }` bypasses Android Chrome's 300ms double-tap-zoom delay (which caused "center tap doesn't fire" on Teaching/Tools/Bug); `.tb-trigger .tb-icon, .tb-trigger svg, .tb-trigger > span { pointer-events: none }` routes all taps to the button itself regardless of which SVG `<path>` the finger lands on. JS click delegation at `.tb-item` level forwards taps on empty padding area to the trigger as last-resort safety net
- **YT PiP bottom controls**: `×` close and `🔒/🔓` lock buttons moved to the PiP's bottom strip (close left, lock center-bottom, resize handle right) and shrunk to 16×16 with 0.7 opacity so they don't cover YouTube subtitles. Hover/tap brings them to full opacity with a darker background
- **Chord-quality LED**: `#chordSource` badge now combines data-source hint with user rating summary. `_updateChordQualityBadge(cd, ratingKey)` first paints source-based fallback (白話 labels — AI / 校 — no more "BTC" / "MIDI" jargon shown to users), then hits `/api/feedback/ratings/summary?song_hash=<key>` and overlays rating-driven color when `count ≥ 3`: avg ≥4 → 綠 `src-good` with "4.2★" text, ≥3 → 黃 `src-mid`, <3 → 紅 `src-bad`. Tooltip always resolves to plain Chinese (`來源：AI 偵測 / 人工校對 / 人工匯入` + rating line when applicable). `ratingKey` is `trackPath` in DB-path mode or `hashMode` in hash mode — matches whatever the rating UI submitted
- **Melody filter (`_filterMelody`)**: two-pass cleanse applied to raw `/api/ai/melody` output before waterfall render. Pass 1 drops low-pitch + low-confidence pitch-tracker noise (`midi < 48 && conf < 0.03`). Pass 2 octave-folds around the median — any note >12 semitones from median is shifted by whole octaves until within ±1 octave. Fixes (a) unplayable wide-range RH and (b) octave-dropped vocal notes colliding with LH accompaniment
- **Prerequisites for YouTube**: `yt-dlp` + `ffmpeg` must be on NUC system PATH
- **YT debug hook**: `window.__lcYtDebug()` in player page DevTools returns live `{hasPlayer, state, currentTime, duration, fillWidth, timeText, timerAlive, lastError, chordDuration, syncDisabled, verifiedOk}` snapshot; `window.__lcYtError` holds the most recent sync-tick exception. Playwright headless cannot play YT IFrame — human-verify with desktop Chrome/Edge; see [doc/QA.md §1.1](doc/QA.md)
- Productization roadmap: [doc/PRODUCTIZATION.md](doc/PRODUCTIZATION.md)

## Dual-instance isolation (archival — Personal 8800 vs Beta 8801)

Documented for completeness; the dual-instance setup is currently *not* running (only Personal 8800 is live post-beta). The split-settings + per-instance hard gates remain in the code so re-enabling beta is a one-batch-file flip. Design after the "one `settings.json` overwrite wiped out the beta groups" incident (see [doc/QA_BATTLE_STORY.md](doc/QA_BATTLE_STORY.md) 番外篇 IV):

- **Split settings files**: `data/settings_personal.json` + `data/settings_beta.json` + `data/settings_shared.json`
  - `SHARED_KEYS` (in [backend/auto_worker.py](backend/auto_worker.py)) marks keys shared across instances: `accompaniment_v2_enabled`, `settings_backup_targets`, `data_backup_root`, `backup_schedule`
  - Legacy `data/settings.json` is archival-only after the one-shot migration in `_migrate_legacy_settings_if_needed`
  - `load_settings()` returns `DEFAULT ∪ shared ∪ own-mode` (own-mode wins)
  - `save_settings()` splits incoming dict by key → shared vs mode-specific files
  - **Never overwrite `V:\data\settings_*.json` wholesale** — only increment
- **Per-instance mode detection**: `_current_mode()` reads `LIVECHORD_MODE` env var (`personal` | `beta`)
- **Backend hard gates** ([backend/personal_mode.py](backend/personal_mode.py)): `require_personal_mode` FastAPI dependency on 13 endpoints (`/api/auto/*` except settings/backups, `/api/extraction/*`, `/api/chords/stats`, `/api/tasks/status`, `/api/library/*`, `/api/settings`). Beta instance returns 404 — even if front-end cache is stale or an attacker curls the endpoint
- **Core worker hard gate**: `auto_worker.start_worker()` refuses to start when `_current_mode() == "beta"` regardless of settings
- **Settings snapshot auto-backup**: every `save_settings()` call first copies the previous mode-file to `data/backups/settings/settings_<mode>_<ts>.json` (rolling 30 per mode per target). Multi-target: `settings_backup_targets` in shared settings lets users write snapshots to local + NAS + USB simultaneously. Cross-lane restore is forbidden (`_mode_of_filename` check in [backend/auto_worker.py](backend/auto_worker.py))
- **Scan filter placement (critical)**: `active_groups` filter lives in `auto_worker._get_unanalyzed_tracks` (detection), NOT in `music_api._scan_dir` (scan). Previously scan also filtered → `library_cache.json` never saw unselected groups → admin UI couldn't show them. Filter belongs at the expensive stage (BTC detection), not the cheap index stage (listdir + metadata)
- **`list_groups` listdir placeholder**: `library_groups.list_groups` follows up `library_cache.tracks` with a shallow `os.listdir` of each music_root so admin UI shows every folder even before scan reaches it. Users can prep their group selection while scan runs
- **Frontend beta detection**: `window._lcIsBeta` is set synchronously from `window.location.port === "8801" || hostname.endsWith("livechord.org")` at top of `admin.html`. Used to hide personal-only cards (`coreCard`, `activityCard`, `chordMgmtCard`, `extractionCard`, `backupCard`) and skip `pollStatus` / `loadSettings` / `loadGroups` etc. Sync check avoids the first-paint race that async `/api/config/public` can't

### What's SHARED across the two instances (important for beta contributions)

Only settings are split. **Everything else is one shared pool** because both uvicorn processes point at the same `V:\data\` filesystem. Important to know when deciding whether a beta user's action should influence personal's models — today, it will:

| Kind | Path / DB | Written by | Side effect |
|---|---|---|---|
| Chord JSONs | `data/chords/*.json` | Both (BTC detect, 人工校對) | chord2vec retrain co-occurrence corpus, jazzify rule stats |
| Human corrections | `data/human_feedback/*`, `feedback.db` | Both | Chord rating summary drives `#chordSource` LED in player |
| Section labels | `data/human_sections/*` | Mostly personal admin (beta UI minimal) | `section_detect` DL retrain input |
| Process audit | `audit.db` `process_audit` | Beta-dominant (personal rarely uploads) | `/api/search` user_uploads priority, reuse lookup |
| YT ↔ library map | `youtube_library_map` | Both (auto-learn on ≤5% duration match) | Cross-instance analysis reuse, player desync check |
| AI models | `data/models/*` (chord2vec, section_detect, reharmonizer, …) | `auto_worker` retrain — **personal only** (hard-gated) | Beta 8801 ingests the new weights on next request |
| Audio blobs | `data/uploads/` (job tmp), IndexedDB (client) | Beta users | Storage growth; currently no TTL cleanup |
| Cover art | `data/covers/*` | Both | Used by both homepages |

Operational implications:
- A beta user giving a song ★1 immediately drops that song's LED on personal's player too (same `feedback.db`)
- If future chord2vec retrain pulls from `data/chords/`, beta-user-analyzed YT MVs will become training signal unless explicitly filtered. See [doc/SCALING.md](doc/SCALING.md) §2 for the "when to isolate" decision tree
- `process_audit` grows unbounded with beta users — no TTL yet. Watch disk when row count passes ~1M

## Data backup (tiered)

[backend/backup_core.py](backend/backup_core.py) + [backend/backup_scheduler.py](backend/backup_scheduler.py). Admin UI: single「📦 備份」card with two collapsible subsections (⚙️ 設定快照 + 🗄️ 資料備份).

- **Three tiers by recreate-cost**:
  - **tier1** — unrecreatable user/feedback/auth/human-correction data (~MB, seconds)
  - **tier2** — chord/melody/hybrid/models (~3 GB, minutes)
  - **tier3** — accompaniment cache (~15 GB, 10-20 min)
- **Target**: env `LIVECHORD_BACKUP_ROOT` > `settings_shared.data_backup_root` > default `W:\LiveChord\backup`. Each run drops timestamped `<root>/<tier>/YYYYMMDD_HHMMSS/<item>`. **NUC requires `W:\` mounted** (or UNC path in settings)
- **tier_info 60s cache**: `get_tier_info()` recursively walks ~260k files on first call (~15s on local SSD, longer on SMB). Cached — subsequent calls < 1ms. Invalidated after each backup completes. Admin UI uses **lazy-load on card expand** so first admin paint isn't blocked by the initial walk
- **Scheduler thread** (`backup_scheduler.py`): daemon, personal-only, wakes every 5 min. Reads `settings_shared.backup_schedule` = `{tierN: {interval: "daily"|"weekly"|"monthly"|"off", hour, day_of_week?, day_of_month?}}`. Triggers `run_backup_async` when `_should_run` matches now AND `_MIN_GAP_S[interval]` has elapsed since last success. No race with manual trigger (RuntimeError handled)
- **History**: `data/backup_history.json` rolling 100. UI shows last 20 + explicit per-row error (not just count)

## PWA install

[frontend/sw.js](frontend/sw.js) + [backend/main.py](backend/main.py) `/sw.js` route + [frontend/index.html](frontend/index.html) install flow:

- Minimal service worker (fetch pass-through) exists **only to satisfy Chrome/Edge installability** — without it the browser never fires `beforeinstallprompt`
- Header `📱 安裝` button: hidden by default, `beforeinstallprompt` listener stores event to `_lcInstallPrompt` + shows button. Click → `prompt()`. `appinstalled` hides button. Standalone mode also hides button
- iOS Safari fallback: no `beforeinstallprompt` support, click shows `alert("分享 → 加到主畫面")`
- Without this: browser only auto-prompts once per origin (ever). User who uninstalled had no path back to install — manual button + SW fixes that

## A-B phrase picker

[frontend/js/player.js](frontend/js/player.js) + [frontend/css/player.css](frontend/css/player.css):

- Horizontal scrollable **pill strip** of phrase labels + right-side column with「手動設定」pill + trash/A/B buttons
- **Toggle multi-select**: tap pill adds/removes from `_abSelectedSet`. Effective loop range = `[min(set), max(set)]` — intermediate pills display `.in-range` to show they'll play (multi-segment jump not supported yet)
- **Phrase boundaries use chord grid**: `_phraseStartOf` / `_phraseEndOf` snap to the first chord whose time ≥ next-section.start (section-detector precision is coarse, chord data is the ground truth)
- **Persist by LABEL not index** (in localStorage `livechord_ab_phrase:<path>`): `{"selected": ["Verse 1", "Chorus 1"]}` — section splits/inserts/renames don't break user's saved selection. Fallback to "manual" if all labels vanish
- **Hash mode parity**: `_loadSections` supports `?hash=` param (backend [ai_api.py](backend/ai_api.py) `/api/ai/sections` accepts both path/hash). Previously hash-mode player never fetched sections → beta strip was always empty
- **Section detection fallback for zero-MIDI songs**: `ai/section_detect.py` `_classify_dl` returns False when `sum(melody_density + bass_density) == 0` → rule-based path runs instead. Many library songs (~82%) never had hybrid extraction; previously they received all-zero features and got labeled single "verse"

## AI Quality Pipeline (current focus)

Post-beta, the active engineering track is **improving the upstream signal that everything else feeds on** — beat stability, chord accuracy, and phrase (section/bar) detection. The modules below are the moving pieces; treat any change to them as a quality-track change and verify against the existing chord-JSON corpus before shipping.

- **`beat_refiner`** ([backend/ai/beat_refiner_model.py](backend/ai/beat_refiner_model.py) · [beat_refiner_features.py](backend/ai/beat_refiner_features.py) · [beat_refiner_infer.py](backend/ai/beat_refiner_infer.py)) — Compact Transformer (≈3M params, 6 encoder layers, d_model 256, sinusoidal pos enc, three sigmoid heads: beat / downbeat / chord-boundary). Runs offline / on-demand on top of beat_this/madmom/librosa output to re-pick beats with full bidirectional context. Trained on the 13,017-song corpus built by [scripts/build_training_corpus.py](scripts/build_training_corpus.py); 3-stage train pipeline ([train_refiner_1_extract.bat](train_refiner_1_extract.bat) → [train_refiner_2_train.bat](train_refiner_2_train.bat) → [train_refiner_3_deploy.bat](train_refiner_3_deploy.bat)). Backfill v2 over the existing chord-JSON library is complete (`LiveChord-sjq` closed). Inference is process-pool isolated (same pattern as BTC) — never call from the FastAPI event loop.
- **`bar_arbitrator`** ([backend/ai/bar_arbitrator.py](backend/ai/bar_arbitrator.py)) — post-processor that fixes bar/downbeat phase drift, beats-per-bar confusion, and bar-doubling without re-running expensive audio models. Phase 0 is rule-based using existing `chords[] / beats[] / downbeats[] / bpm / tempo_curve`; Phase 1 trained Transformer model is now live with admin & player tools (`bar_arbitrator_v1.pt` + ONNX export at `data/models/bar_arbitrator_v1.onnx`). Conservative by default — only acts when candidate grid scores above `_MIN_CANDIDATE_F1`. Personal-mode-only call site in [process_queue.py](backend/process_queue.py).
- **Chord-splitter / serve-time bar alignment** — [chord-correction.js](frontend/js/chord-correction.js) `inferBeatsPerBar` / `nextDownbeatAfter` + [player.js](frontend/js/player.js) `_showAutoSplitPanel`. The 自動切分 panel's three modes (依小節切分 / 對齊小節線 / 依比例切分) lean on the upgraded `downbeats[]` from above to slice and snap chord cards to bar lines. Beat-aware dot rendering on the player canvas reads the same upgraded grid.
- **Chord JSON sharding** — `data/chords/` was rewritten into `<hash[:2]>/` buckets (commit `abe6172`). Filesystem perf — admin coverage queries and migration scripts now scan ~256× fewer files per `listdir`. Tier2 backup auto-fires after sharding pushes (commit `d47747c`); 3-way beat tracker switch (librosa / madmom / beat_this) lives behind the same admin UI.
- **`neural_arranger` (Phase 2 MIDI arranger, in-progress)** — [backend/ai/neural_arranger.py](backend/ai/neural_arranger.py) + [backend/ai/remi_tokenizer.py](backend/ai/remi_tokenizer.py). Decoder-only Transformer (d_model 768, 12 heads, 12 layers, ≈110M params) trained on `F:\MIDI-Library` to generate per-role MIDI (BASS / MELODY / DRUM / etc) conditioned on chord + role prefix tokens. Training: [run_train.bat](run_train.bat) → [scripts/run_training.py](scripts/run_training.py) (50 epochs, batch_size 4, GradScaler AMP). Recent uncommitted changes add **checkpoint resume** + per-epoch save so long runs can be interrupted; output `data/models/neural_arranger_phase2.pt`. E2E generation: [run_generate.bat](run_generate.bat) → [scripts/generate_and_render.py](scripts/generate_and_render.py) emits `eval_output/*.mid` and pipes through [scripts/render_midi_to_wav.py](scripts/render_midi_to_wav.py) for FluidSynth WAV rendering. Plan/spec: [doc/midi_arranger_implementation_plan.md](doc/midi_arranger_implementation_plan.md), [doc/PHASE2_ARRANGEMENT_PLAN.md](doc/PHASE2_ARRANGEMENT_PLAN.md).

**Forward goals (in priority order)**:

1. **Beat stability** — drive down `bar_arbitrator` false-positives + push `beat_refiner` accuracy on edge genres (slow ballads, rubato, intro silence). Anchor metric: percentage of chord-JSONs where the human correction queue confirms `downbeats[]` matches musical bars.
2. **Chord accuracy** — both BTC raw output and the post-correction layer. Beta-era ★ ratings in `feedback.db` are the labeled signal; the chord-quality LED already surfaces them in the player. Next step is feeding 人工校對 corrections back into a fine-tune corpus for BTC.
3. **Phrase / section detection** — `section_detect` DL path improves once melody/bass densities are non-zero across more of the library (currently rule-based fallback covers ~82% per the comment in [backend/ai/section_detect.py](backend/ai/section_detect.py)). Hybrid extraction expansion + the Phase 4 MIDI catalog (see [doc/PHASE_4_HYBRID_MELODY.md](doc/PHASE_4_HYBRID_MELODY.md)) feeds this directly.

Open trade-offs and known issues for the active tracks live in [doc/TODOS.md](doc/TODOS.md) "Quality Tracks (active focus)".

## AI Accompaniment (engine v7, post-2026-05-06 — deployed)

The AI LH/RH accompaniment shipped on `feature/ai-accompaniment-styles` and merged into master 2026-05-06. Live on https://livechord.org (which now deploys from master directly — see [doc/OPS.md "Standard deploy"](doc/OPS.md#standard-deploy-pc-dev--vps)). Architecture summary:

- **Backend**: [backend/ai/accompaniment_generator.py](backend/ai/accompaniment_generator.py) — 21 styles in `STYLE_DICT`, 7 piano RH modes (`arpeggio` / `fill_only` / `fill_harmony` / `fill_block` / `comp_offbeat` / `comp_quarter_shell` / `muted_stab`). `STYLE_CONFIG` carries `pattern_period_beats` (4 default, 3 for JazzWaltz). Patterns tile at fixed beat-period from `chord.time`, NOT chord-relative — fixes "2-beat chord plays double-time" bug. `RH_ARPEGGIO_PATTERN` is 8-eighth resolution. Output cached at `data/accompaniments/{hash}_{style}_{level}_{section_type}_{instrument}_{ACC_ENGINE_VERSION}.json`. Engine version `v7`; bump to invalidate.
- **String-family events (v6)**: when the caller passes `instrument="guitar"`/`"ukulele"`, `_build_string_rh` emits per-string events carrying BOTH `pitch` (the audio synth consumes this unchanged) AND `string` / `finger` (`p`/`i`/`m`/`a`) / `strum_id` / `strum_dir` (the visual reads these). Visual = audio = single source of truth. `STRING_OPEN_MIDI` declares the tunings: guitar `[40,45,50,55,59,64]` (low E to high e), ukulele reentrant `[55,48,52,57]` (G C E A — index 0 is HIGHER than index 1). Diagrams resolved via `chord_diagrams.get_chord_diagram(name, instrument)`. Bass-thumb selection picks the lowest-PITCH active string (NOT lowest-index) so reentrant ukulele works.
- **String idiom routing (v7)**: `STRING_IDIOM_BY_STYLE` maps STYLE NAME → idiom (NOT piano `rh_mode`). Piano `rh_mode` names piano gap-fill behavior, not guitar idiom — naive `rh_mode` routing in v6 mapped the literally-named "Arpeggio" style (`rh_mode="fill_only"`) to strum on guitar. v7 explicit table:
  - `arpeggio`: Arpeggio, Alberti, 1+3, PopBallad, RockBallad, RnBNeoSoul
  - `offbeat`: Reggae, BossaNova, JazzCharleston, JazzWaltz
  - `strum` (default): Block, Rhythm, Rock, Blues, Stride, Swing, Samba, Funk, Shell, Walking
- **Frontend audio**: [frontend/js/player.js](frontend/js/player.js) `SampleSynth` class — generic sample/oscillator synth, switches between 9 timbres via `SAMPLE_MANIFEST` + `DEFAULT_TAB_SOUND` + `livechord_sound_<tab>` localStorage. Local samples under [frontend/audio/samples/](frontend/audio/samples/) (~25 MB binary): `grand-piano` / `nylon-guitar` / `steel-guitar` / `organ` / `accordion` from CC-BY-3.0 sources; `upright-piano` / `rhodes` / `wurlitzer` / `synth-pad` are oscillator-based. Audio scheduler reads `accData.right_hand` and pipes through `getActiveSynth().playNote()`.
- **Frontend visual (piano)**: 88-key waterfall in player.js consumes `accData.right_hand` — visual + audio agree exactly.
- **Frontend visual (guitar / ukulele)** — v6+: [frontend/js/string-instrument.js](frontend/js/string-instrument.js) `_drawRhWaterfall` reads `accData.right_hand` via `_extractStringRhEvents` (groups events by `strum_id` for sweeps, by exact-time for multi-finger plucks, otherwise solo pick). The legacy client-side `_generateRhEvents` is **deleted** (was the v5 divergence source). `arpeggio-patterns.js` survives only for the `ARPEGGIO_PATTERNS` label-text lookup (decorative).
- **Frontend visual (accordion / arranger)**: keyboard waterfall consumes `accData.right_hand` directly — same source as audio.

UI surfaces:
- AI-teaching popup `<select id="teachStyle">` (the AI Acc style — Auto/Block/Arpeggio/Rhythm/...) is **visible on every tab in v7+**, governs guitar/ukulele idiom too. The legacy `<select id="guitarStyleSelect">` (3-option arpeggio/pattern/block) is **permanently hidden** — it was a v5 client-side strum picker that didn't drive the backend; users picked "Block" there expecting strum, accData stayed unchanged, visual silently diverged.
- AI-teaching popup `<select id="instrumentSound">` — per-tab sound picker, `livechord_sound_<tab>` persistence.
- Practice picker (6 presets: L / R-acc / R-mel / R-both / LR-acc / LR-mel / LR-both) and the cycle button `#btnRhContent` write `livechord_rh_mode` (`acc` | `mel` | `both`). On guitar/uke tab `_extractStringRhEvents` honors the mode: `mel` returns melody pitches mapped to strings via `_pitchToString` (lowest-fret-playable, with classical fingerstyle finger via `_defaultMelodyFinger`); `both` overlays acc + melody. Both togglers force a `_drawRhWaterfall` + `refreshLabels` redraw so changes show immediately when audio is paused.
- RH hint label (`#gtRhHint` / `#ukuRhHint`) reads `rhContentMode` first: `mel` → "RH melody", `both` → "RH acc + melody", `acc` → idiom inferred from event shape via `_inferAccIdiom` (strum_id → strum/offbeat distinguished by all-up vs mixed direction; finger ∈ p/i/m/a → arpeggio).
- Admin `/api/admin/accompaniment/recompute` — clears cache for one song without `ACC_ENGINE_VERSION` bump.

Engineering invariants (don't break these):
- **`encodeURIComponent` on the style query param**. The "1+3" style has a literal `+` which decodes to a space in `application/x-www-form-urlencoded`. Without encoding, FastAPI receives `style=" 3"`, falls through `STYLE_DICT`, silently uses Block. Already bit us once — see `_loadAccompaniment` URL build.
- **Cache filename includes instrument**. Old `_v5.json` files (no instrument segment) stay orphaned; v6/v7 cache filenames are `..._{instrument}_v{N}.json`. Never strip the instrument segment without bumping the engine version.
- **`_assign_fingering` is piano-only**. Skip the Viterbi fingering pass for `instrument != "piano"` — string-family events already carry p/i/m/a labels that the keyboard 1-5 finger pass would overwrite.
- **Per-string default finger for melody is by OPEN PITCH rank**, not string index. Guitar standard tuning happens to match index order; ukulele reentrant does not. `_defaultMelodyFinger` ranks by `openMidi` ascending, then maps low→p/p/p/i/m/a (guitar) or p/i/m/a (ukulele).

UX rules added during this work: UX_CONVENTION §13a (color-scheme + select optgroup styling), §13b (`.toast { pointer-events: none }` mandatory), §13c (light-theme saturated-color remap helper applied to ALL FCLR consumers, not just headers).

## Reference

- QA protocol, test matrix, UI architecture rules: [doc/QA.md](doc/QA.md)
- **UX convention (mandatory for all UI changes)**: [doc/UX_CONVENTION.md](doc/UX_CONVENTION.md)
- Battle stories / past incidents: [doc/QA_BATTLE_STORY.md](doc/QA_BATTLE_STORY.md)
- **VPS operations runbook** (deploy workflow, yt-dlp cookies, tunnel cutover, gotchas): [doc/OPS.md](doc/OPS.md)
- **SEO / search visibility plan** (current GSC state, Phase 1-5 ranking roadmap): [doc/SEO.md](doc/SEO.md)
- Productization roadmap (Beta 能跑起來): [doc/PRODUCTIZATION.md](doc/PRODUCTIZATION.md)
- Scaling roadmap (Beta 成功之後 — 個人/公眾分割、GPU、雲端部署、DB 擴展、i18n): [doc/SCALING.md](doc/SCALING.md)
- NotebookLM hand-off (accompaniment knowledge doc for AI-coding): [doc/for-notebooklm/](doc/for-notebooklm/)
- Implementation plans (historical): [doc/plans/](doc/plans/)

## NotebookLM hand-off

[doc/for-notebooklm/](doc/for-notebooklm/) is the staging folder for files the user drops into Google NotebookLM (sources panel). When the user asks for "彙整 / summary / hand-off for NotebookLM", write the Markdown artifact here with a dated, self-contained filename (e.g. `2026-04-18-qa-digest.md`) — each file must stand alone with no cross-links to other repo files, because NotebookLM only sees what's uploaded. Keep prior files; don't auto-clean. No MCP bridge exists — the flow is one-way, file-based.


<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:ca08a54f -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd dolt push
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->
