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
