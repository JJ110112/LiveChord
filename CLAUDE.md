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
- **Production Admin Account**: User: `hitea` (password user-managed; first-registered user is auto-promoted to admin by `auth_api.init_db`, no seeded account)
- **Beta (8801) QA Account** (non-admin, for Playwright QA where login is required): `qatest` / `qatest1234`
- **Admin page**: `http://localhost:8800/admin`

## Beta Testing Active (invites sent — tread carefully)

livechord.org is live, invitation codes have been distributed, real users are analyzing songs on 8801. Default to **cautious** workflow — the NUC runtime is production for these users, not a scratchpad.

**Default workflow for any change**:

1. **Edit only in the dev repo** (`c:\Users\hitea\Claude\LiveChord`). Never edit `V:\` directly — commits won't pick it up and rollbacks are harder
2. **QA on PC localhost 8802 first** via [start_beta_local.bat](start_beta_local.bat) (same `LIVECHORD_MODE=beta` semantics as 8801, but isolated `data/` folder so your experiments can't corrupt user auth/audit/ratings)
   - Local QA account: `qatest` / `qatest1234` + invite `LiveChordAlpha` (first register auto-promotes to admin only if auth.db is empty; qatest on the dev box is non-admin by default)
3. **Ship to V:\ only after local QA passes**. Copy via `cp` + verify with `diff -q`
4. **Announce restarts**: backend `.py` changes don't auto-reload on NUC (uvicorn has no `--reload` in prod) — the user must run [restart_dual.bat](restart_dual.bat) before changes take effect on 8801. Surface this in your summary so it isn't forgotten

**When stopping to ask before acting**:
- Any backend change that touches `process_audit`, `feedback.db`, `auth.db`, `settings_beta.json`, `youtube_library_map`, or user-facing state — these have live data from real users
- Batch file renames / deletions under `V:\`
- Anything that invalidates outstanding auth tokens (schema change, secret rotation)
- Destructive sqlite operations on any DB path under `V:\data\`
- "Let me clean up this old file" instincts — check `git log` first; old-looking files may be live-referenced

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
- **yt-dlp subprocess calls MUST specify `encoding="utf-8", errors="replace"`** — NUC default code page is cp950; without explicit UTF-8 decode, `_get_youtube_title` drops CJK/Hangul from titles (bug: `FIFTY FIFTY (피프티피프티)` → `FIFTY FIFTY ()`). Prefer `--dump-json` over `--get-title` so output is always UTF-8 JSON regardless of console locale
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
- **Toolbar SVG icons**: 12 toolbar triggers + Tools/AI-Teaching popup buttons use inline Lucide SVG (`.tb-icon` class, stroke="currentColor") so Android/iOS/desktop render identically; emoji entities would fall back to colored Noto on Android regardless of FE0E variant. Play/pause and mute use two SVGs with `.is-playing` / `.is-muted` class toggle instead of `innerHTML =`. Loop button has 3 states (off/single/favorites=repeat+heart-badge) baked into `_updateLoopUI`. `#tbLocalFile` removed from toolbar (replaced by homepage 本機音樂 section). Speed cycle reordered `1× → 0.75× → 0.5× → 1.25× → 1.5× → 2×` — students usually want to slow down first, not speed up
- **Chord ribbon scale**: inline `[−] [val] [+] [☰ 總覽]` row in `.ribbon-scale-bar` (was a popup, user couldn't revert the value). On mobile portrait ribbon is upper 50vh with `display: grid; grid-template-columns: repeat(auto-fit, minmax(calc(140px * var(--ribbon-scale, 1)), 1fr))` so `+/-` grows cards in both dimensions (not just height)
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

## Dual-instance isolation (Personal 8800 vs Beta 8801)

LiveChord runs two uvicorn processes on the same NUC. They share code + music files but NOT configuration. Design after the "one `settings.json` overwrite wiped out the beta groups" incident (see [doc/QA_BATTLE_STORY.md](doc/QA_BATTLE_STORY.md) 番外篇 IV):

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

## Reference

- QA protocol, test matrix, UI architecture rules: [doc/QA.md](doc/QA.md)
- **UX convention (mandatory for all UI changes)**: [doc/UX_CONVENTION.md](doc/UX_CONVENTION.md)
- Battle stories / past incidents: [doc/QA_BATTLE_STORY.md](doc/QA_BATTLE_STORY.md)
- Productization roadmap (Beta 能跑起來): [doc/PRODUCTIZATION.md](doc/PRODUCTIZATION.md)
- Scaling roadmap (Beta 成功之後 — 個人/公眾分割、GPU、雲端部署、DB 擴展、i18n): [doc/SCALING.md](doc/SCALING.md)
- NotebookLM hand-off (accompaniment knowledge doc for AI-coding): [doc/for-notebooklm/](doc/for-notebooklm/)
- Implementation plans (historical): [doc/plans/](doc/plans/)

## NotebookLM hand-off

[doc/for-notebooklm/](doc/for-notebooklm/) is the staging folder for files the user drops into Google NotebookLM (sources panel). When the user asks for "彙整 / summary / hand-off for NotebookLM", write the Markdown artifact here with a dated, self-contained filename (e.g. `2026-04-18-qa-digest.md`) — each file must stand alone with no cross-links to other repo files, because NotebookLM only sees what's uploaded. Keep prior files; don't auto-clean. No MCP bridge exists — the flow is one-way, file-based.
