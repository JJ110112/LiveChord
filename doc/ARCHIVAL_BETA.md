# Archival — Beta deployment + dual-instance

The 2026-04-16 → 2026-04-26 invite-only beta on `livechord.org:8801` ended on 2026-04-26. The `LIVECHORD_MODE=beta` gates, beta-only endpoints, hashed-path search, invite flow, dual-instance scripts, and beta-only frontend branches are **all still in the codebase** but not exercised by the live deployment. This file documents how those gates work so they can be re-enabled or modified safely.

> ⚠ **2026-05-04 — yt-dlp + YouTube extraction REMOVED for the open-source release.** Modules and call sites mentioning `_get_youtube_title` / `_download_youtube` / `find_existing_result` / `find_library_mapping` / `youtube_library_map` / `_searchAndEmbedYouTube` / `_initYouTubeEmbed` / `_ytPlayer` / `_addPlaylist` / `_searchTriggerUrlAnalyze` / `share_target` / Web Share Target / `/api/process/youtube*` / `frontend/share.html` / `yt_dlp_fetch.py` were deleted (commits prefixed `chore(yt-removal stage N/4)`). The `youtube_url` field on chord JSONs is read-back compatible (won't crash on legacy data) but never written or acted on. Re-enabling YT requires reverting those commits, not flipping a flag.

## Beta-mode deployment gates

- **Deployment mode**: env var `LIVECHORD_MODE` (priority) → `data/settings.json` `"deployment_mode"` (fallback) → `"personal"` (default)
- **Dual-instance** (archival): the NUC could run two uvicorn processes via [start_dual.bat](../start_dual.bat) — Personal on 8800, Beta on 8801. Currently only the Personal 8800 instance runs
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
- **Cover art**: uploaded files have covers extracted via mutagen; YouTube uses `img.youtube.com` thumbnails

## Beta homepage (non-admin) routing

- Search bar is the single entry. Typed/pasted YouTube URL is detected live (`_YT_URL_RE`) and routed by shape:
  - **Single-video URL** → "偵測到 YouTube 網址" CTA → `_searchTriggerUrlAnalyze(url)` opens the add-song modal with URL prefilled + auto-fires 分析
  - **Playlist URL** (`&list=PL...` via `_YT_PLAYLIST_RE`) → "偵測到 YouTube 播放清單" CTA → `_addPlaylist(url)` fetches `/api/process/playlist-info`, persists `{list_id, title, videos:[{video_id, title, duration, existing_hash}]}` in `localStorage.livechord_yt_playlists`, renders in homepage section `#secBetaPlaylists` as expandable cards. Each video: ▶ 播放 if `existing_hash` known (library_map hit OR prior audit), 分析 otherwise (one-at-a-time via existing YT upload flow; `_currentAnalyzingPlaylistVid` stamps `existing_hash` back on done)
  - **Plain text + Enter** → empty add-song modal opens
  - **Plain text typed** → hits `/api/search`. In **beta non-admin** the endpoint short-circuits to return **only the user's own analyzed songs** (scans `process_audit` where `username=? AND status='done'`) — NAS library results are intentionally hidden because library tracks were chord-analyzed from album masters while beta users are almost always looking for YouTube MV versions; surfacing the library match caused "same title, wrong audio length" confusion. Personal (8800) and beta admin still get the merged result: user_uploads on top, then library. `user_uploads` album label routes off `source_type`: `"upload"` → `"本機上傳"`, `"youtube"` → `"YouTube 分析"` (don't mislabel YT analyses as local uploads)
- **Search placeholder marquee — dynamic text** ([frontend/js/utils.js](../frontend/js/utils.js) `updateSearchMarqueeText`): runs on DOMContentLoaded for both homepage + player topbar. Personal/admin gets the long copy immediately. Beta user defaults to short copy ("請輸入 YouTube URL...") and only upgrades to long after `/api/process/my-history?limit=1` returns non-empty
- **Search-result routing — beta hash vs personal path**: on 8801 `/api/search` rewrites `path` to `__hash/<hash>` and emits an extra `hash` field (NAS privacy); on 8800 `path` is the real NAS path and `hash` is absent. Frontend click handler must stamp `data-hash` and route `?hash=<h>` when present, else `?path=<p>`. Naively passing `__hash/<hash>` as `?path=` lands in NAS-stream mode with a pseudo-path that 404s
- Homepage sections shown to non-admin: 最近播放 (filled by `_loadBetaHistory`, merges `/api/process/my-history` + `/api/recent` deduped by title) · **本機音樂** (`#secBetaLocalTracks`, `<input type=file multiple>` persistent registry) · **YouTube 播放清單** (`#secBetaPlaylists`) · 我的最愛
- **Add-song modal file-only mode**: `.beta-fab-panel.file-only` class hides `#betaDropZone` + `.add-song-yt-row` so opening the modal from a local-track analyze button doesn't show two "分析" buttons
- **Melody-pending banner**: after `_betaPollJob` or `_showAnalysisBannerDone` navigates to a fresh hash, they write `sessionStorage.livechord_fresh_hash`. Player hash-mode load, if `/api/ai/melody` returns empty, `_maybeStartMelodyPolling()` polls every 5s for up to 5min; bottom-left blue banner shows `旋律擷取中`, on success hot-swaps `melodyData`

## Dual-instance isolation (Personal 8800 vs Beta 8801)

Design after the "one `settings.json` overwrite wiped out the beta groups" incident (see [QA_BATTLE_STORY.md](QA_BATTLE_STORY.md) 番外篇 IV):

- **Split settings files**: `data/settings_personal.json` + `data/settings_beta.json` + `data/settings_shared.json`
  - `SHARED_KEYS` (in [backend/auto_worker.py](../backend/auto_worker.py)) marks keys shared across instances: `accompaniment_v2_enabled`, `settings_backup_targets`, `data_backup_root`, `backup_schedule`
  - Legacy `data/settings.json` is archival-only after the one-shot migration in `_migrate_legacy_settings_if_needed`
  - `load_settings()` returns `DEFAULT ∪ shared ∪ own-mode` (own-mode wins)
  - `save_settings()` splits incoming dict by key → shared vs mode-specific files
  - **Never overwrite `V:\data\settings_*.json` wholesale** — only increment
- **Per-instance mode detection**: `_current_mode()` reads `LIVECHORD_MODE` env var (`personal` | `beta`)
- **Backend hard gates** ([backend/personal_mode.py](../backend/personal_mode.py)): `require_personal_mode` FastAPI dependency on 13 endpoints (`/api/auto/*` except settings/backups, `/api/extraction/*`, `/api/chords/stats`, `/api/tasks/status`, `/api/library/*`, `/api/settings`). Beta instance returns 404 — even if front-end cache is stale or an attacker curls the endpoint
- **Core worker hard gate**: `auto_worker.start_worker()` refuses to start when `_current_mode() == "beta"` regardless of settings
- **Settings snapshot auto-backup**: every `save_settings()` call first copies the previous mode-file to `data/backups/settings/settings_<mode>_<ts>.json` (rolling 30 per mode per target). Cross-lane restore is forbidden (`_mode_of_filename` check)
- **Scan filter placement (critical)**: `active_groups` filter lives in `auto_worker._get_unanalyzed_tracks` (detection), NOT in `music_api._scan_dir` (scan). Filter belongs at the expensive stage (BTC detection), not the cheap index stage
- **`list_groups` listdir placeholder**: `library_groups.list_groups` follows up `library_cache.tracks` with a shallow `os.listdir` so admin UI shows every folder even before scan reaches it
- **Frontend beta detection**: `window._lcIsBeta` is set synchronously from `window.location.port === "8801" || hostname.endsWith("livechord.org")` at top of `admin.html`. Sync check avoids the first-paint race that async `/api/config/public` can't

### What's SHARED across the two instances

Only settings are split. **Everything else is one shared pool** because both uvicorn processes point at the same `V:\data\` filesystem:

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
- If future chord2vec retrain pulls from `data/chords/`, beta-user-analyzed YT MVs will become training signal unless explicitly filtered. See [SCALING.md](SCALING.md) §2 for the "when to isolate" decision tree
- `process_audit` grows unbounded with beta users — no TTL yet. Watch disk when row count passes ~1M
