# LiveChord Public VPS — Operations Runbook

Operational procedures for the public deployment on Hetzner CPX21 Hillsboro OR (us-west, IPv4 `5.78.135.8`). Personal/NUC operations stay in [CLAUDE.md](../CLAUDE.md). VPS provider rationale: [vps-survey-for-livechord-jolly-pancake.md](../../.claude/plans/vps-survey-for-livechord-jolly-pancake.md). Deployed 2026-05-03; tunnel `livechord` (UUID `d182dd0a-3655-42db-86e3-b78294aee428`) routes via local config at [/etc/cloudflared/config.yml](../deploy/cloudflared.yml).

## yt-dlp YouTube extraction prerequisites

yt-dlp 2026.x deprecated extraction without a JavaScript runtime, and YouTube added an "n parameter" JS challenge that requires a community-maintained solver script. Both are mandatory for any audio download:

1. **Deno** (JS runtime) — `apt` doesn't ship it; install via the official script with target `/usr/local/bin` so it's on the systemd PATH:
   ```bash
   apt-get install -y unzip
   curl -fsSL https://deno.land/install.sh | sh -s -- -y
   mv /root/.deno/bin/deno /usr/local/bin/deno
   chmod 755 /usr/local/bin/deno
   ```
2. **yt-dlp-ejs** (solver script distribution) — pulled into the venv via [backend/requirements.txt](../backend/requirements.txt). After upgrade just `uv pip install yt-dlp-ejs` to refresh.

Without both, yt-dlp errors with "Only images are available for download" / "n challenge solving failed" even with valid cookies.

## yt-dlp cookies refresh

Hetzner IP ranges are flagged by YouTube. Tier 1 (direct) hits 403/SABR walls; tier 2 carries an authenticated cookie jar from a low-value Google account.

### Initial setup (once)

1. Create a throwaway Google account. Don't reuse a real one — when it gets terminated, no collateral damage
2. Sign into youtube.com from a clean browser profile, watch a few videos so the account looks lived-in
3. Install the [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) extension (or any maintained equivalent — the LOCALLY suffix matters; some forks exfiltrate)
4. Export cookies for `youtube.com` to `cookies.txt`
5. Copy to VPS:
   ```bash
   scp cookies.txt livechord@<vps-ip>:/home/livechord/.config/yt-dlp/cookies.txt
   ssh livechord@<vps-ip> 'chmod 600 /home/livechord/.config/yt-dlp/cookies.txt'
   ```
6. systemd unit's `ReadWritePaths=/home/livechord/.config/yt-dlp` already permits writes from the service

### Weekly refresh (scheduled)

Cookies expire on ~14–28 day cycles even without account intervention. Set a calendar reminder for Sunday evening:

1. From the same browser profile, re-export `cookies.txt`
2. `scp` overwrite as above
3. No service restart needed — `yt_dlp_fetch.py` reads the file at each invocation

### Emergency refresh (`ip_block` ratio spikes)

Trigger: weekly `process_audit` query (see survey §Verification step 5) shows `ip_block` ratio crossing 5% on tier 1 calls.

1. Refresh cookies as above
2. If still failing within 24 h → the throwaway account is burned; create a new one and re-export
3. If burning new accounts within days → escalate to residential proxy or Phase H Modal tier 3 (see [LiveChord-5gg](https://github.com/...))

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
