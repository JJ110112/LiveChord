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
   - 8801 / 8802 (beta-mode local) are no longer part of the routine. Only spin them up when explicitly testing `LIVECHORD_MODE=beta`-gated code (forced login, hashed paths, invite flows) before re-enabling beta — if you do, see [doc/ARCHIVAL_BETA.md](doc/ARCHIVAL_BETA.md) for the gating details
3. **Ship to V:\ only after local QA passes**. Copy via `cp` + verify with `diff -q`
4. **Restart NUC after backend `.py` changes**: uvicorn has no `--reload` in prod, so changes don't take effect until restart. AI agents can self-trigger via `ssh nuc "schtasks /run /tn LiveChordRestart"` (or run [restart_nuc.bat](restart_nuc.bat)), then poll `curl http://192.168.50.6:8800/` until 200 (~4s) before continuing tests. Requires hitea logged into NUC desktop (interactive task constraint). **Do NOT use `ssh nuc "C:\LiveChord\restart.bat"` directly** — SSH lands in Windows session 0, the `start "..." python ...` inside fails to spawn a new console, and the OLD uvicorn gets killed without a replacement (verified live, took 8800 down on 2026-05-19). The schtasks indirection runs `restart.bat` in hitea's interactive session where `start` works normally. Restart is briefly destructive to anyone playing — confirm with user first if production-impacting

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
- **No `async def` for file I/O endpoints** — use plain `def` so FastAPI dispatches to the thread pool (see `feedback_async_def`). Applies to retrain/training endpoints too: `/api/ai/retrain` was `async def` and froze the event loop during chord2vec SVD — flipped to `def`. **Lint**: [tools/lint_async_handlers.py](tools/lint_async_handlers.py) flags any `async def` route handler whose body contains no `await`. Run before pushing backend changes; baseline at [tools/async_handler_baseline.txt](tools/async_handler_baseline.txt) silences the 62 known-but-trivial pre-existing offenders so CI fails only on NEW violations. Bit us three times: `/api/ai/evaluate` (commit 2b503b4), `/api/ai/retrain` (commit 7cc3fd0), `/api/ai/sections` (commit 96aba1f)
- **Cache-busting**: bump `?v=N` on `<script>`/`<link>` tags when editing JS/CSS (QA UI rule 6). **For new i18n keys** also bump `DICT_VERSION` in [frontend/js/i18n.js](frontend/js/i18n.js) — the bundle fetch is keyed to that constant, not the script-tag `?v=`. Bit us once on the chord-export rollout: button shipped, EN/zh-TW keys shipped, but browsers held onto the stale cached `i18n/zh-TW.json?v=48` so the new key resolved to its literal name. Lockstep: any new key in `frontend/i18n/*.json` → bump `DICT_VERSION` AND every HTML's `i18n.js?v=` ref via `grep -l 'i18n.js?v=' frontend/*.html | xargs sed -i`
- **Print PDF: open a new tab, do NOT `@media print` + hide-body** — mobile WebKit (iOS Safari) and Android Chrome have unreliable `@media print` snapshot timing on the current page: `display:none !important` on body children, `position:sticky` ancestors, and `min-height:100vh` body all break the print-engine snapshot, producing blank previews. Canonical pattern: [frontend/js/chord-exporter.js](frontend/js/chord-exporter.js) `_buildPrintHtml()` → `_exportPdf()` does `window.open('','_blank')` synchronously inside the click handler chain (mobile blocks async popups), `document.write()`s a complete standalone HTML doc with all CSS inlined, then `setTimeout(window.print, 250)` from inside the new tab. The new doc has no parent stylesheet pollution and prints reliably on every platform. If you add another print-PDF feature, follow this pattern; do NOT try to fix `@media print` for in-page printing on mobile
- **`chordCache[name].notes` are pitch-class NAMES (strings like "Bb", "D", "F"), NOT MIDI ints** — the data comes from `/api/chord/info/<name>` which returns chord-tone labels for display. Any MIDI export code MUST convert via `window.noteToSemitone()` ([frontend/js/utils.js:129](frontend/js/utils.js#L129)) → 0-11 semitone, then voice into an octave (chord-exporter uses MIDI 48+, root in C3-B3, subsequent tones stacked upward). The `typeof p !== 'number'` check that "looks defensive" will silently strip every note and produce an empty MIDI file. Reference: [chord-exporter.js `_voiceChordToMidi`](frontend/js/chord-exporter.js)
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

## Player UI invariants

Anti-foot-gun reminders for the player frontend (most earned via real incidents — don't undo without checking git blame). The beta-deployment + dual-instance archival lives in [doc/ARCHIVAL_BETA.md](doc/ARCHIVAL_BETA.md).

- **Homepage gear settings menu** ([index.html `#btnHeaderSettings`](frontend/index.html) + [base.css `.header-nav` + `.hn-row`](frontend/css/base.css)): topbar collapses Help / Sponsor / Language / Install / Sign-out into a single ⚙ trigger. Theme picker lives here (global setting); player.html no longer carries `.theme-opt` buttons. Popup uses `var(--lc-popup-bg)` + `var(--text)` so it inherits the active theme. Coffee/sponsor pill (warm-amber, links to `/sponsor`) sits immediately left of the gear. Search-bar magnifying glass uses an SVG `mask` with `currentColor` so it renders monochrome on every theme/OS
- **Jianpu visibility toggle** ([player.html `#btnToggleJianpu`](frontend/player.html) + [player.js `_applyJianpuVisibility`](frontend/js/player.js)): Tools popup → 簡譜 row flips `body.no-jianpu`, hiding `.rv-jianpu` + `.chord-jianpu` + `.melody-jianpu` together. Persisted in `localStorage.livechord_show_jianpu`. Default ON
- **Light-theme canvas ink helper** ([chord-render.js `_crInk()`](frontend/js/chord-render.js) + [string-instrument.js `_rhInk()`](frontend/js/string-instrument.js)): canvas-drawn fretboard / waterfall lines flip white-on-dark → black-on-cream when `<html data-theme>` is `light/sakura/sunny/sky`. Both helpers read the attribute directly (no opts threading) and apply 1.4× alpha bump on light bg. `drawVerticalFretboard`, `drawGuitarFretboard`, `_drawRhWaterfall` all routed through these. Don't add new white-RGBA literals — call `_crInk(a)` instead
- **88-key piano height** (subtle gotcha — bit us once): `.chord-88-keys` is the WRAPPER for both waterfall AND piano canvas. Don't `flex: 0 0 auto` it or cap its height — waterfall needs to fill vertical space inside it. Layout: `.chord-88-keys { flex: 1; min-height: 0 }`, waterfall `flex: 1 1 auto; min-height: 120`, piano canvas `flex-shrink: 0; margin-top: auto` (intrinsic ~200px, pinned to bottom). Keyboard-clipping protection in [`_get88PianoMaxWidth`](frontend/js/player.js) caps with `reservedH = 178` — don't remove
- **Toolbar SVG icons**: 12 toolbar triggers + Tools/AI-Teaching popup buttons use inline Lucide SVG (`.tb-icon`, `stroke="currentColor"`) so all platforms render identically; emoji entities fall back to colored Noto on Android regardless of FE0E variant. Play/pause and mute use two SVGs with `.is-playing` / `.is-muted` class toggle instead of `innerHTML =`. Loop button has 3 states (off/single/favorites=repeat+heart-badge) baked into `_updateLoopUI`. Speed cycle order `1× → 0.75× → 0.5× → 1.25× → 1.5× → 2×` — students slow down first
- **Chord ribbon scale**: inline `[−] [val] [+] [☰ 總覽]` row in `.ribbon-scale-bar` (was a popup, user couldn't revert). Mobile portrait: ribbon is upper 50vh with `display: grid; grid-template-columns: repeat(auto-fit, minmax(calc(140px * var(--ribbon-scale, 1)), 1fr))` so `+/-` grows cards in both dimensions
- **Chord ribbon contrast tiers** ([player.css `.rv-item`](frontend/css/player.css) + [player.js `_updateActiveChord`](frontend/js/player.js)): 4 visual states. `played` 0.3 · default/far-ahead 0.72 + 1px divider · `upcoming` (i+2..i+3) 0.9 · `upcoming-next` (i+1) 0.95 + blue left-border · `active` 1.0 + 4px accent border + glow. Prior binary contrast (0.5↔1.0) lost the read-ahead cue; users on the Ribbon want to read 1–3 chords ahead
- **Waterfall pedal — left-gutter design** ([player.js `drawWaterfall` Phase 11](frontend/js/player.js#L2922)): pedal sustain = 10px saturated green stripe on left edge (`rgba(76,175,80, 0.4 + depth*0.25)`); full-width wash dropped to `depth*0.05` so note bars keep contrast. Prior 15% green wash competed with blue LH / orange RH bars
- **自動切分 panel — 3 modes** ([player.js `_showAutoSplitPanel`](frontend/js/player.js) + [chord-correction.js](frontend/js/chord-correction.js) `inferBeatsPerBar` / `nextDownbeatAfter`):
  1. **依小節切分** (default) — slice chords longer than one bar into bar-sized cards. `beatsPerBar` resolves from user TS override → `inferBeatsPerBar(downbeats[])` → fallback 4. Mid-bar starts: first cut fills current bar (anchored to next real downbeat or synthetic `phase + k·barDur` grid), remaining beats cut into full bars
  2. **對齊小節線** — *destructive* boundary relocation. Snaps every inner `chord[i].end == chord[i+1].time` to nearest bar line within ±½-bar tolerance, then runs bar-split. Preserves `chord[0].time` and `chord[last].end`; rejects snaps that would collapse a chord to < ½ beat. Shows orange warning because it changes which chord is active at snapped moments. BTC sometimes places boundaries off bar lines deliberately (syncopation) — user runs 還原 if needed
  3. **依比例切分** (legacy) — `threshold + ratio` for proportional splits independent of meter
  - Shared UI: bar+barsnap share `.as-bar-section` with TS chips; ratio hides it. `localStorage.livechord_auto_split` persists per-user
  - **Doesn't auto-populate downbeats** — librosa-fallback songs (ingest default for speed) have `downbeats=[]`; tool drops to synthetic grid. For precise alignment run 升級節拍 (`POST /api/process/upgrade-beats`) or let `bar_arbitrator` / `beat_refiner` re-derive at ingest
- **Empty chord state**: `loadChords()` renders `chord-empty-state` with hero "一鍵偵測和弦" button (`#btnDetectHero`, document delegation to `runChordDetection()`). Personal mode: detection auto-fires on chord-empty
- **Fingering display**: waterfall does NOT render finger numbers (too cluttered) — reserved for 88-key piano area. `fingeringMap` populates from `accData.left_hand/right_hand`. For hash-mode without accData, fallback synthesizes LH 5-3-1 / 5-3-2-1 / 5-4-3-2-1 from current chord voicing (sorted low→high). RH melody has no reliable fingering, shows nothing
- **Hash-mode AI accompaniment parity**: `_loadAccompaniment()` was hard-gated on `trackPath` non-empty, so hash-mode songs fell back to chord-voicing blocks. `_accPath()` helper (`trackPath || chordData.path`) unlocks the endpoint — backend `song_hash(path)` produces the same hash for `__upload/<job_id>` that process-queue used at save time. Hash-mode chord-load branch also calls `_loadAccompaniment()` once
- **RH content mode (`rhContentMode`)**: `#btnRhContent` cycles `acc → mel → both` (labels 伴/旋/全), persisted in `localStorage.livechord_rh_mode`. Gates RH waterfall AND keyboard `activeRh` — both read the same flag so highlights and falling bars never disagree. `acc` mode with empty `accData.right_hand` auto-falls-through to melody
- **Jazzify AI transformer time-stretch**: [reharmonizer.py `_apply_transformer`](backend/ai/reharmonizer.py) accumulates `current_time += dur_val` bounded by `max_len=128` + EOS, producing a chord timeline ~1/3 the original length. Post-decode, if `|orig_span - trans_span| > 1s`, rescale every chord's `time/end` by `scale = orig_span / trans_span`. Rule-based L1/L2/L3 paths preserve timing via in-place splits, untouched
- **Jazzify empty-chord resilience**: when API returns `res.chords = []`, button used to pin on AI state — next click hit `chordsEmpty` guard and stalled. Guard now requires `chordsEmpty && !originalChords` so cycle reaches state 0 (restore-from-backup); `!Array.isArray(res.chords) || !res.chords.length` throws to reset button immediately
- **Three-tier mobile toolbar**: `.bottom-toolbar` wraps `.tb-item`s into 3 `.tb-row` groups — `tb-row-t1` (Speed/Loop/Transpose/Volume), `tb-row-tx` (Prev/Play/Next + times), `tb-row-t2` (Instrument/Jazzify/A-B/Teaching/Tools/BugReport). Desktop = single inline row; `@media (max-width:640px) and (orientation:portrait)` stacks vertically. Landscape stays single-row to preserve viewport budget
- **State-cycle → absolute-value popup**: Speed / Loop / A-B / Jazzify used to cycle silently on click → users mistook for "沒反應". Now each has `.tb-popup` listing all options (`.speed-opt`, `.loop-opt`, `.ab-opt`, `.jazz-opt` data-attrs). Setters extracted from cycle handlers (`_setSpeed / _setLoopMode / _handleAB / _setJazzifyLevel`) and bound to popup buttons; cycle handlers stay wired but gated by `if (_isTouchLike) return` so desktop click cycles, mobile tap opens popup. Jazzify popup order `關閉 / ✨AI / L1 / L2 / L3`
- **5-option popups 2+3 grid**: Instrument and Jazzify popups have 5 options; touch portrait `.tb-popup-5 .tb-popup-btn:nth-child(-n+2) { flex-basis: calc(50% - 6px) }` + `:nth-child(n+3) { flex-basis: calc(33.333% - 8px) }` forces 2-on-top / 3-on-bottom. AI-Teaching + Tools popups use 3-column grid with `.tb-separator { flex-basis: 100% }` as full-row divider. Landscape overrides to natural compact width with `max-width: min(90vw, 520px)`
- **Touch hit-target rescue**: S22 Ultra showed Tools/Bug buttons "need top 1/5 to trigger" — Android gesture nav strip ate bottom-half taps, and `progress-bar-mid` at `bottom:44px` hijacked missed taps. Fixes: viewport meta `viewport-fit=cover`; `@media (pointer:coarse)` sets `.bottom-toolbar { padding-bottom: max(env(safe-area-inset-bottom), 10px); min-height: 54px }` + `.tb-trigger { min-height: 44px; padding: 8px 10px }`; progress bar floats 10px above toolbar with `height: 4px` default (6px hover)
- **Ghost-click / SVG tap routing**: `.tb-trigger { touch-action: manipulation; -webkit-tap-highlight-color: rgba(0,0,0,0) }` bypasses Android Chrome's 300ms double-tap-zoom delay; `.tb-trigger .tb-icon, .tb-trigger svg, .tb-trigger > span { pointer-events: none }` routes all taps to the button regardless of which SVG path the finger lands on. JS click delegation at `.tb-item` level forwards taps on empty padding as last-resort
- **Chord-quality LED**: `#chordSource` badge combines data-source hint with user rating summary. `_updateChordQualityBadge(cd, ratingKey)` paints source-based fallback (白話 labels — AI / 校), then hits `/api/feedback/ratings/summary?song_hash=<key>` and overlays rating-driven color when `count ≥ 3`: avg ≥4 → 綠 with "4.2★" text, ≥3 → 黃, <3 → 紅. Tooltip: `來源：AI 偵測 / 人工校對 / 人工匯入` + rating line. `ratingKey` is `trackPath` in DB-path mode or `hashMode` in hash mode
- **Melody filter (`_filterMelody`)**: two-pass cleanse on raw `/api/ai/melody` output before waterfall. Pass 1 drops `midi < 48 && conf < 0.03` (low-pitch + low-confidence noise). Pass 2 octave-folds around the median — any note >12 semitones from median is shifted by whole octaves until within ±1 octave
- **Sheet-music score layer** ([frontend/js/score-render.js](frontend/js/score-render.js) + [#scoreLayer](frontend/player.html) + [score wiring in player.js](frontend/js/player.js) `_scoreRedraw / _updateScore / _recomputeScoreEligible`): white-bg grand staff sandwiched between chord row and waterfall inside `.chord-88-keys`. VexFlow 4.2.3 self-hosted at [/js/vendor/vexflow.min.js](frontend/js/vendor/vexflow.min.js) (Bravura build, ~570 KB raw / ~140 KB gzip; served via existing `/js` static mount, no backend mount change needed). Eligibility = waterfall wrapper width ≥ 540 AND viewport height ≥ 500 AND `activeTab === "piano"` (NOT gated on overview-mode — overview at 50/50 split happily coexists with score on the right). Tools popup `#btnToggleScore` adds hard override persisted at `localStorage.livechord_show_score`. Schema-v2 rendering consumes canonical `duration`, supports compound meter denominators (6/8, 12/8), emits dotted durations where readable, and splits cross-bar notes into tied VexFlow notes.
- **VexFlow gotchas** (all bit us, all costly to rediscover):
  - `new Stave(x, y, w)` — `y` is the TOP of stave's visual box, which INCLUDES 40 px above-staff headroom; the top staff line is at y+40, bottom at y+80, and ledger notes extend further (D2 = +95, C2 = +100, A1 = +110). Accidentals (♯/♭) add ~12 px below the notehead. For grand-staff with low bass + accidentals, `bottomY = max(94, stage_h - 135)` keeps A#1/Ab1 visible. Layer height clamp(240, 26vh, 300) so even at min, stage_h ≥ 226 = safe
  - Cursor x must use `stave.getNoteStartX()` for bar 1 (after clef/key/time), NOT `stave.getX()` — otherwise the cursor sits in the clef area while notes are drawn in the music area → "score lags playback" by ~60 px
  - **`addModifier(new Accidental("b" | "#"), idx)` is REQUIRED** for `keys: ["bb/3"]` to render the flat glyph — VexFlow sets `keyProps[i].accidental` but does NOT auto-emit the modifier. Without this, "bb/3" draws at B3 position WITHOUT the flat → reads as B3 / visually A3
  - Voice duration MUST equal the meter window exactly. Off-by-1/16-beat throws `RhythmException` and crashes the layer. `_buildBarNotesV2` includes a Padding Patch: after quantization, if `sum(note durations) ≠ meter.quarterBeats`, append rests; if still off by > 0.001, replace the bar with a full-bar rest fallback
  - StaveNote `keys[]` MUST be sorted low → high — wrong order flips stem direction and stacks noteheads incorrectly. Dedupe identical pitches in the same chord-group BEFORE sorting (AI accompaniment occasionally outputs two events at same time/same pitch — visually stacks as duplicate noteheads otherwise)
  - `Formatter().format([voice], W)` — pass `W = stave.getWidth() - 44`, NOT the full stave width. The 44 px buffer absorbs notehead + accidental + flag + breathing space so dense bars don't overshoot the bar line ("最右邊音符超出五線譜")
- **Score density / layout**:
  - Bars per page = 2 when stage_h < 1000 px (e.g., 50/50 split on 1920-wide viewport gives stage ~960), else 4. Forces breathing room on narrow waterfalls where 4 dense eighth-bars pack unreadably tight
  - Pitch-range filter in `_buildBarNotesV2` drops out-of-range pitches BEFORE rendering (bass: MIDI 33–72 / A1–C5; treble: 53–100 / F3–E7). Ledger lines are KEPT for in-range notes — user wants them as a reading scaffold, NOT octave-clamped (silent shift broke pitch correspondence with keyboard, rejected)
  - Page-flip transition: 220 ms fade + slide-from-right on `#scoreStage` + `#scoreChordRow`. Cursor is NOT animated — it's positioned to `timeToX(currentTime)` immediately so it always reflects actual play time; brief 220 ms visual misalignment with sliding-in content is acceptable. First render (oldRange.end === 0) skips animation; toggle-off resets `_scoreCurrentRange = {0,0}` so re-enable is also a "first render" without slide
- **Score sync hooks**: `_updateScore(t)` is called from THREE event paths, not just `tickSync`:
  1. `tickSync` RAF (while playing) — already called next to `drawWaterfall`
  2. `audio.addEventListener("pause", ...)` — without this, the cursor freezes at the last-tick position when user pauses and scrubs
  3. `audio.addEventListener("seeked", ...)` — calls `_scoreRedraw(t)` (not just `_updateScore`) because a large seek may land in a different page → need to re-render the staff content, not just move the cursor
  4. `audio.addEventListener("timeupdate", ...)` when `audio.paused` — covers slow progress-bar drag where seeked doesn't fire between snaps
- **Cache-bust for new i18n keys**: bumping `?v=N` on `frontend/js/i18n.js` is NOT enough — also bump `const DICT_VERSION = N` inside `i18n.js` because the dictionary fetch is keyed to that constant, not the script-tag `?v=`. Bit us on every new player toolbar key
- **88-key C labels — inside white keys, not below**: previously labels lived in a 16-px strip below the bevel, which on mobile landscape touched the `progress-bar-mid` and visually overlapped. Now drawn at `y = labelSize + 3` (top of white key, above the area where finger-number ①②③④⑤ indicators appear during chord highlight), x-centered at `info.w * 0.38` (the visible white area, not the full key — C# black key covers the right ~24 % at the top). C4 uses `font-weight: 800; color: #1a1a1a`; other Cs use `500 / #aaaaaa`. The old cyan middle-C tick was removed — C4's typography alone is the anchor

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

## Note Event Schema v2 (canonical duration contract)

This contract applies to AI accompaniment, melody extraction, score rendering, playback, and MIDI export. It exists to fix the systemic "short notes + frequent rests" failure mode at the event-data layer, not just in notation.

**Core rules**:
- **`schema_version: 2` is a data-format version, independent from `ACC_ENGINE_VERSION`**. `ACC_ENGINE_VERSION` invalidates accompaniment-generation caches; `schema_version` describes the event payload shared by accompaniment and melody extraction. Do not tie the two version numbers together.
- **`duration` means canonical musical duration**. It should express the readable/quantized note value that fills musical space. Do not shorten `duration` to create staccato, portato, humanized release, or playback-only articulation.
- **Short-touch playback lives in `gate_ratio`**. `gate_ratio` defaults to `1.0` and is consumed by playback only. Optional `articulation` (`legato`, `normal`, `portato`, `staccato`) may describe intent, but it must not make score/MIDI consumers fragment the canonical duration.
- **Readable MIDI is the first-version export policy**. `frontend/js/midi-exporter.js` and `frontend/js/chord-exporter.js` must export canonical `duration` and ignore `gate_ratio` until a separate Performance MIDI feature is approved. Do not add a UI switch in the first pass.
- **`voice_lane` is generator-owned**. The generator must emit lanes such as `lh_bass`, `lh_chord`, `rh_accompaniment`, `rh_melody`, and instrument-specific lanes when it knows the source of the note. Continuity repair may infer a fallback only for legacy/cache data.
- **Melody schema v2 keeps legacy bounds**. Melody extractors must emit `schema_version=2`, `voice_lane="rh_melody"`, `pitch`, `midi`, `time`, canonical `duration`, and `gate_ratio=1.0`, while preserving `start`/`end` for current player consumers. If continuity extends `duration`, synchronize `end = time + duration`.
- **Continuity repair is lane-local**. Extend a note to the next same-lane onset when the gap is small and musically empty; do not merge across different `voice_lane`, chord boundary, intentional phrase break, or user-edited note boundary.
- **`strum_id` groups move as one unit**. Notes in the same strum/roll keep their intentional onset offsets, but their canonical end should align as a group, normally based on the last onset in the group and the next safe same-lane boundary. Never extend a strum group across the next `strum_id`.
- **Phase gates are mandatory for this overhaul**. After each implementation phase, stop and report completed items for user review. Do not begin the next phase until the user approves.

Recommended event shape:

```json
{
  "schema_version": 2,
  "time": 12.0,
  "duration": 1.5,
  "pitch": 64,
  "velocity": 0.72,
  "voice_lane": "rh_melody",
  "gate_ratio": 0.88,
  "articulation": "normal",
  "strum_id": null,
  "continuity_meta": {
    "source_duration": 0.75,
    "extended_by": 0.75,
    "reason": "small_gap_same_voice"
  }
}
```

Playback must clamp release tails inside the canonical duration window so `gate_ratio` does not create overlap/click artifacts:

```javascript
const canonicalEnd = startTime + duration;
let audioOff = startTime + duration * gateRatio;
if (audioOff + releaseTail > canonicalEnd) {
  audioOff = Math.max(startTime, canonicalEnd - releaseTail);
}
source.stop(audioOff + releaseTail);
```

## AI Accompaniment (engine v8, deployed 2026-05-20)

LH/RH accompaniment in [backend/ai/accompaniment_generator.py](backend/ai/accompaniment_generator.py) — 21 styles, 7 piano RH modes, multi-instrument (piano / guitar / ukulele / accordion / arranger). Output cached at `data/accompaniments/{hash}_{style}_{level}_{section_type}_{instrument}_{ACC_ENGINE_VERSION}.json`; engine version `v8` (bump to invalidate). Architectural detail in [doc/for-notebooklm/](doc/for-notebooklm/).

**Engineering invariants — don't break these**:
- **`encodeURIComponent` on the style query param**. The "1+3" style has a literal `+` which decodes to a space in `application/x-www-form-urlencoded`; without encoding FastAPI receives `style=" 3"`, falls through `STYLE_DICT`, silently uses Block. Bit us once
- **Cache filename includes instrument**. v6+ are `..._{instrument}_v{N}.json`. Never strip the instrument segment without bumping `ACC_ENGINE_VERSION`
- **v8 accompaniment events use schema v2**. Canonical `duration` is readable musical duration; playback shortening lives in `gate_ratio`. `player.js` consumes `gate_ratio` with release tails clamped inside the canonical duration window.
- **Phase 2.5 continuity rollout is feature-flagged**. `LIVECHORD_NOTE_CONTINUITY_MODE` or `data/settings.json.note_continuity_mode` accepts `off` / `shadow` / `active`; default is `shadow`. Shadow mode writes event-level `continuity_meta.would_*` and top-level `continuity_observation` without changing durations. Do not switch to `active` until the phase gate is approved. Mode changes require uvicorn restart; to backfill existing v8 cache after a mode flip, use `/api/admin/accompaniment/recompute` or bump `ACC_ENGINE_VERSION`.
- **Phase 5 continuity rollout tooling**. Use `python tools/continuity_phase5_rollout.py` for safe top-N validation/prewarm planning. It is dry-run by default, selects recent/favorite/high-rated/chord-index candidates, audits schema/readable-duration metrics, and writes reports to `data/logs/`. Add `--execute` only when intentionally prewarming a bounded set; do not bulk rebuild the full 15GB accompaniment cache unless explicitly requested.
- **`_assign_fingering` is piano-only**. Skip Viterbi fingering for `instrument != "piano"` — string-family events already carry p/i/m/a labels the keyboard 1-5 pass would overwrite
- **Per-string default finger for melody is by OPEN PITCH rank**, not string index. Guitar happens to match index order; ukulele reentrant doesn't. `_defaultMelodyFinger` ranks by `openMidi` ascending, then maps low→p/p/p/i/m/a (guitar) or p/i/m/a (ukulele)
- **String idiom routing is by STYLE NAME** (`STRING_IDIOM_BY_STYLE`), not piano `rh_mode`. v6 routed by `rh_mode` and the literally-named "Arpeggio" style (`rh_mode="fill_only"`) became a strum on guitar. v7 explicit table: arpeggio = {Arpeggio, Alberti, 1+3, PopBallad, RockBallad, RnBNeoSoul} · offbeat = {Reggae, BossaNova, JazzCharleston, JazzWaltz} · strum (default) = the rest
- **Single source of truth for visual = audio**. String events carry both `pitch` (audio) and `string`/`finger`/`strum_id`/`strum_dir` (visual). The legacy client-side `_generateRhEvents` is deleted; `_extractStringRhEvents` reads from `accData.right_hand` only. `arpeggio-patterns.js` survives solely for label-text lookup
- **Practice picker writes `livechord_rh_mode`** (`acc` | `mel` | `both`); RH waterfall AND keyboard `activeRh` both read this — never let them disagree
- **Legacy `<select id="guitarStyleSelect">` is permanently hidden** — was a v5 client-side strum picker that didn't drive backend; users picked "Block" expecting strum, accData stayed unchanged, visual silently diverged. Use `<select id="teachStyle">` (visible on every tab in v7+)
- Admin `/api/admin/accompaniment/recompute` clears cache for one song without `ACC_ENGINE_VERSION` bump

UX rules added during this work: UX_CONVENTION §13a (color-scheme + select optgroup styling), §13b (`.toast { pointer-events: none }` mandatory), §13c (light-theme saturated-color remap helper applied to ALL FCLR consumers).

## Demo Songs (15 PD/CC tracks shipped 2026-05-06)

Anonymous visitors at livechord.org get a "try without uploading" entry: 15 royalty-free pre-analyzed tracks in 4 categories (🎵 Easy → 🌍 Folk → 🎷 Jazz → 🎹 Classical). Backend at [backend/demo_api.py](backend/demo_api.py) (single endpoint `/api/demo/list`); build pipeline at [scripts/build_demo.py](scripts/build_demo.py); manifest + audio + covers committed under `data/demo/` (~85 MB).

**Engineering invariants — don't break these**:
- **CC-BY/SA attribution required**: every track in `data/demo/manifest.json` carries `source_url` + `license` + `license_url`; homepage `.demo-attribution-block` at the bottom of `#secDemoSongs` / `#secDemoSongsHero` is JS-populated from these and is **required** for the CC-BY/SA tracks (Canon, Greensleeves, K.265, Frère Jacques, Moonlight, Clair de Lune, Carefree, Bossa)
- **Asset cache-busting via `MANIFEST_ASSET_VERSION`** in build_demo.py — bump it to invalidate Cloudflare + browser caches; manifest then appends `?v=N` to every `audio_url` / `cover_url` and chord JSONs' `demo_audio_url`. Don't manually purge CF
- **Demo melody files git-tracked via gitignore exception**: `!data/melodies/<hash>.json` lines are reachable only because the umbrella exclusion uses `data/melodies/*` (the `*`, not `/`) — git can't re-include children of an excluded directory
- **Per-track meter overrides** (`bpm_override` / `beats_per_bar_override` in TRACKS): for compound-time pieces beat_this picks up as 4/4 (Chopin Nocturne is 12/8 at BPM 50). Override rebuilds `downbeats[]` from `beats[]` taking every Nth, anchored to first detected downbeat to preserve phase
- **`cover_title` overrides painted text** for tracks whose manifest title contains CJK the painter's Latin font (Segoe UI Bold) can't render. Card title in manifest can keep CJK; the browser font handles it
- **4-category render order enforced by DOM order** in `index.html` (Easy / Folk / Jazz / Classical) — `_loadDemoSongs` iterates `sec.querySelectorAll(".demo-category")` which preserves DOM. To reorder, edit the HTML, not JS
- **Backend reuse points**: `chord_cache.chord_file_for(hash)` falls back to `data/demo/chords/<hash>.json` when sharded path is missing → `/api/chords/by-hash` transparently serves demos. `process_api._get_demo_cover_map()` lazily reads manifest so 最近播放 cards resolve to shipped JPGs. `/static/demo` mount in `main.py` serves audio + covers

## Reference

- QA protocol, test matrix, UI architecture rules: [doc/QA.md](doc/QA.md)
- **UX convention (mandatory for all UI changes)**: [doc/UX_CONVENTION.md](doc/UX_CONVENTION.md)
- Battle stories / past incidents: [doc/QA_BATTLE_STORY.md](doc/QA_BATTLE_STORY.md)
- **VPS operations runbook** (deploy workflow, yt-dlp cookies, tunnel cutover, gotchas): [doc/OPS.md](doc/OPS.md)
- **SEO / search visibility plan** (current GSC state, Phase 1-5 ranking roadmap): [doc/SEO.md](doc/SEO.md)
- **HF Hub + PyPI release** (livechord-beat-refiner + livechord-bar-arbitrator under `livechord-music` org, Apache 2.0; live since 2026-05-09): [doc/HF_RELEASE.md](doc/HF_RELEASE.md). Held-out metrics: [doc/beat_refiner_metrics.md](doc/beat_refiner_metrics.md). Staging dirs (not in git): `c:\Users\hitea\hf-hub-staging\`
- Productization roadmap (Beta 能跑起來): [doc/PRODUCTIZATION.md](doc/PRODUCTIZATION.md)
- **Beta deployment + dual-instance archival** (LIVECHORD_MODE=beta gates, hashed paths, invite flow, dual-instance settings split): [doc/ARCHIVAL_BETA.md](doc/ARCHIVAL_BETA.md)
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
