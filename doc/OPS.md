# LiveChord Public VPS — Operations Runbook

Operational procedures for the public deployment on Hetzner CPX21 Hillsboro OR (us-west, IPv4 `5.78.135.8`). Personal/NUC operations stay in [CLAUDE.md](../CLAUDE.md). VPS provider rationale: [vps-survey-for-livechord-jolly-pancake.md](../../.claude/plans/vps-survey-for-livechord-jolly-pancake.md). Deployed 2026-05-03; tunnel `livechord` (UUID `d182dd0a-3655-42db-86e3-b78294aee428`) routes via local config at [/etc/cloudflared/config.yml](../deploy/cloudflared.yml).

## YouTube ingest — REMOVED (2026-05-04)

LiveChord no longer accepts YouTube URLs as an analysis input. The `yt-dlp`
binary, `yt-dlp-ejs` JS solver, the deno runtime install, and the entire
cookies-refresh workflow are gone. The repo went open-source under AGPL
on the same day; chasing YouTube's rotation defense with
disposable-account cookies wasn't a maintenance burden the project would
take on. Personal NUC use moved to CD-track ingest only.

If YouTube ingest needs to come back, it would require reverting the
`chore(yt-removal stage N/4)` commit series, not flipping a flag — the
modules and call sites have been deleted.

What this means for the VPS:
- No `yt-dlp` / `deno` / `yt-dlp-ejs` install required
- `/home/livechord/.config/yt-dlp/` directory unused (deleted)
- `LIVECHORD_USE_MODAL_YTDL` env var has no effect (the module it gated is gone)
- The `/api/process/youtube*`, `/api/process/playlist-info`,
  `/api/process/yt-library-learn` endpoints return 404 unconditionally
  (well, technically: they don't exist on the router)
- Users analyse songs by uploading audio files (200 MB cap, MP3 / FLAC /
  WAV / M4A / OGG)

## VPS health checks

### Memory pressure

```bash
free -h          # used vs cached
ps aux --sort=-%mem | head     # top RAM consumers
```

p95 used > 2.5 GB or swap engaging → `hcloud server change-type <name> cpx31` (one-click vertical resize, ~30 s downtime).

### CPU steal time

```bash
vmstat 5 5       # st column = stolen by hypervisor (noisy neighbour)
mpstat -P ALL 5 3
```

p95 `st > 10%` over a week → CPX21 shared cores are contended. Switch to **CCX13** (dedicated AMD, €14.86/mo).

### Modal usage

- Modal dashboard → `livechord-btc` and (post-Phase-H) `livechord-ytdl` apps
- Watch GPU seconds/month vs the 350–2100 sec/month projection. Consistently over → audit retry logic in chord_detect.py

## Cloudflare Tunnel cutover

### Pointing livechord.org at the VPS

1. Cloudflare Dashboard → Networks → Tunnels
2. Pick the existing tunnel (currently NUC-bound)
3. Public Hostname tab → edit `livechord.org` → service URL: `http://127.0.0.1:8800` (tunnel runs on the VPS)
4. Or: provision a new tunnel on the VPS, install the credentials JSON to `/etc/cloudflared/`, copy [deploy/cloudflared.yml](../deploy/cloudflared.yml), enable `cloudflared` systemd unit, then update DNS to the new tunnel UUID

### Rollback (NUC fallback)

CF dashboard → re-point hostname back to the NUC tunnel. Takes ~5 min for global DNS to propagate. The VPS keeps running and is reachable by IP for debug.

## Backup snapshots

Hetzner provider snapshot is sufficient for the public VPS — the heavy data (chord JSONs, models, audit DB) reflects user analyses that can be re-derived from the underlying YouTube/upload sources if catastrophically lost. Personal-side training corpus stays on NUC + W:\ tiered backup, untouched by VPS state.

Schedule:
- Hetzner Cloud → Server → Snapshots → weekly automatic, 4 retained
- Manual snapshot before any `apt upgrade` or major systemd change

## When the VPS goes down

1. Cloudflare dashboard → repoint `livechord.org` to NUC tunnel (rollback above)
2. Investigate VPS via Hetzner console (KVM access if SSH dead)
3. If unrecoverable, restore from latest weekly snapshot
4. systemd journals: `journalctl -u livechord.service -n 200 --no-pager`

## Deploying code changes to the VPS

The VPS pulls from GitHub via deploy key `livechord-vps-prod` (SSH `git@github.com:JJ110112/LiveChord.git`). The repo lives at `/srv/livechord` owned by user `livechord`. Always use `git pull` — never `scp` or `rsync` files in (see "Things that will bite you" below).

**SSH access** — `~/.ssh/config` on PC has a `livechord-vps` host alias:
```
Host livechord-vps
HostName 5.78.135.8
User root
IdentityFile ~/.ssh/livechord_ed25519
```

So `ssh livechord-vps` lands as root. Git operations must run as user `livechord` (the repo owner), via `sudo -u livechord -H bash -c '...'`.

### Standard deploy (PC dev → VPS)

```bash
# 1. PC: commit + push to feature/beta-productization
cd c:/Users/hitea/Claude/LiveChord
git add <files>
git commit -m "..."
git push

# 2. VPS: pull + restart in one SSH call
ssh livechord-vps "sudo -u livechord -H bash -c 'cd /srv/livechord && git pull --ff-only' && sudo systemctl restart livechord && sleep 3 && sudo systemctl is-active livechord"

# 3. Verify via public URL
curl -sI https://livechord.org/   # 200 OK expected
```

If `git pull --ff-only` rejects with "non-fast-forward", someone made commits on the VPS. Check `git log --oneline origin/feature/beta-productization..HEAD` before deciding whether to merge or reset — never blanket-discard.

### Things that will bite you

- **Hot-copying files (scp / rsync) to VPS leaves git out of sync.** This already happened once — VPS had ~20 files showing as "modified" in `git status` because someone scp'd from PC after a PC commit, but never `git pull`-ed on the VPS. The file content matched origin (so functionally OK), but `git pull` then refused with "local changes would be overwritten." Recovery is `git checkout -- .` then `git pull`, but only after **inspecting every diff** to confirm no real VPS-only edit gets lost. Always pull, never scp.

- **VPS-only edit on `backend/requirements.txt`**: as of 2026-05-03 the VPS working tree intentionally removes the `yt-dlp-ejs>=0.8` block (because deno+ejs install was deferred — see "yt-dlp YouTube extraction prerequisites" above). Origin has the line. Every `git pull` that touches `requirements.txt` needs the removal re-applied:
  ```bash
  cp backend/requirements.txt /tmp/vps-req.txt
  git checkout -- backend/requirements.txt
  git pull --ff-only
  cp /tmp/vps-req.txt backend/requirements.txt
  ```
  The "M backend/requirements.txt" in `git status` is **expected and load-bearing** — don't try to clean it up. Once deno + yt-dlp-ejs are actually installed and proven working, commit a removal of the block to origin so the working tree goes clean.

- **CRLF vs LF line endings**: PC commits files with CRLF (Git for Windows default). On Linux VPS, big files (`music_api.py`, `chord-render.js`, `app.js`, `string-instrument.js`) get logged with thousand-line "diffs" that are just line-ending normalization — no real content change. `git checkout -- .` resets them to clean LF.

- **The systemd service does NOT auto-install requirements.** After `git pull` of a commit that adds Python deps, you must `sudo -u livechord -H /srv/livechord/.venv/bin/pip install -r backend/requirements.txt` and only then restart. Otherwise uvicorn crashes on import.

- **Cloudflare caches `robots.txt` and other static files** with `Cache-Control: max-age=14400` (4 hours). After a `robots.txt` edit deploys, public URL still serves old content for up to 4 hours. Manually purge in Cloudflare dashboard → Caching → "Purge by URL" if it matters (e.g., before re-submitting to Search Console).

- **Pre-flight check both ports**: a healthy deploy responds 200 on both:
  - `curl -sI http://127.0.0.1:8800/` (origin, behind tunnel)
  - `curl -sI https://livechord.org/` (Cloudflare → tunnel → uvicorn)

  If origin is 200 but public is 5xx, the tunnel (cloudflared) is the problem, not uvicorn.

### Restart vs reload

The systemd unit has `Restart=always` and 3 worker processes. `systemctl restart livechord` is ~3 seconds of downtime. There is no graceful reload — uvicorn workers die and respawn. For a 3-worker setup that's tolerable; if traffic ever justifies zero-downtime, switch to gunicorn with `--reload-graceful` or front the service with a reverse proxy that handles 502 retries.
