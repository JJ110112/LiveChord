# LiveChord Health Monitor

Periodic health check for both LiveChord servers (NUC personal at `192.168.50.6:8800` and VPS public at `livechord.org`). Runs on PC every 10 min. Two-tier design — cheap rule-based checks first, Hermes summarization only when an anomaly is suspected, Telegram push only when severity warrants it.

## Quick start

1. **Install dependencies** (just stdlib + optional `concurrent-log-handler` already used elsewhere — no new pip needed). Python 3.11+ for `zoneinfo`.
2. **Telegram token + chat_id — auto-resolved.** No manual paste needed: `check.py` auto-reads `TELEGRAM_BOT_TOKEN` from `~/.claude/channels/telegram/.env` and the chat_id from `~/.claude/channels/telegram/access.json` `allowFrom[0]` (both are canonical stores written by Claude Code's `telegram:configure` / `telegram:access` skills). If you ever rotate the token or change allowlist entries via those skills, the monitor picks up the new values on next run. To override (e.g. use a different bot or send to a different chat), set `tg_token` / `tg_chat_id` directly in [config.json](config.json) and those win.
3. **Verify Ollama is reachable**:
   ```
   curl http://localhost:11434/api/tags
   ```
   Should list the model named in config (`hermes3:latest` by default — change to whichever Hermes / Llama / Mistral variant you have loaded).
4. **Verify SSH alias works** for VPS log access:
   ```
   ssh livechord-vps "journalctl -u livechord -n 5 --no-pager"
   ```
   If this prompts for a password, set up an SSH key + agent first — the scheduled task runs non-interactively.
5. **Dry-run** to confirm probes work end-to-end:
   ```
   python check.py --target both --dry-run
   ```
   Should print `probe.ok=True … reason=none` for both targets in normal state.
6. **Force a real Telegram alert** to verify the push path:
   ```
   python check.py --target nuc --inject-error
   ```
   You should get a TG message within ~30s. The fingerprint is cached so re-running won't double-ping.
7. **Schedule it**:
   - Open Windows Task Scheduler → Create Basic Task
   - Trigger: Daily, recur every 1 day, repeat every 10 minutes for 1 day, indefinitely
   - Action: Start a program — `cmd.exe /c "C:\Users\hitea\Claude\LiveChord\tools\health_monitor\run-once.bat"`
   - Settings: ✓ Run task as soon as possible after a scheduled start is missed, ✓ Stop task if runs longer than 5 minutes
   - Run whether user is logged in or not? Yes (so it runs when screen is locked)

## How it works

### Tier 1 — Probes (always)

Each tick:
- HTTP GET `target.url` with 10s timeout, records `{ok, status, latency_ms}`
- Reads last 200 lines of the target's log:
  - **NUC**: direct file read of `V:/data/server.log` (V:\ is mounted from the NUC; reads happen on PC, no network)
  - **VPS**: `ssh livechord-vps "journalctl -u livechord -n 200 --no-pager"`
- Counts `[ERROR]`, `[WARN]`, `Traceback (most recent call last):`, and `HTTP/1.1" 5xx` matches
- Computes `last_error_age_min` from the timestamp of the most recent `[ERROR]` line

Fires Tier 2 if any of:
- liveness probe failed
- `traceback_count >= 1`
- `error_count >= 3`
- `last_error_age_min < 5`
- `5xx_count >= 1`

### Tier 2 — Hermes summarization (only when triggered)

POSTs the log slice + indicators to `http://localhost:11434/api/generate` with `format=json`. Hermes returns:

```json
{
  "severity": "critical|warning|info",
  "summary": "<= 80 字繁中",
  "root_cause_guess": "繁中",
  "suggested_action": "繁中"
}
```

If Ollama is down or returns garbage, falls back to `severity=warning` + raw indicators in the message. **Hermes outage doesn't silence the monitor** — that's a deliberate property.

### Tier 3 — Telegram

Sends a markdown message to your TG chat. Suppresses by **fingerprint** (sha256 of the dominant error line) for `cooldown_minutes` (default 30). Re-alerts with the same fingerprint return `suppressed=True` and write nothing to TG.

### Quiet hours

Default ON, 23:00–08:00 Asia/Taipei. `severity=warning` and `info` are suppressed during the window; `severity=critical` always breaks through. Adjust in `config.json`:

```json
"quiet_hours": { "enabled": true, "start": "23:00", "end": "08:00" }
```

Or disable: `"enabled": false`.

## Files

- [check.py](check.py) — main script (~330 LOC)
- [config.json](config.json) — secrets + targets + tunables
- [run-once.bat](run-once.bat) — Task Scheduler entry point
- `state.json` — auto-created; suppression fingerprints + last_run_ts
- `run.log` — rolling local log (1 MB × 2 backups); rotation handled by stdlib RotatingFileHandler
- `run.scheduler.log` — captures stdout/stderr from each scheduled run (for debugging missed schedules)

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| "config.json not found, writing template" | First run; edit the template and re-run |
| `liveness probe failed` for VPS but site loads in browser | Cloudflare may rate-limit the User-Agent we use. Try changing the URL in config to `/sitemap.xml` or `/favicon.ico` |
| `ssh exit 255: ...` for VPS | SSH key not loaded in the scheduled-task user's agent. Test: `ssh livechord-vps echo ok` from a fresh cmd window — if that fails, SSH agent isn't autoloading. Use `pageant` autostart or a passwordless key |
| `hermes call failed: TimeoutError` | Ollama slow first-load (cold model). Increase the timeout in `call_hermes` or pre-warm via OpenWebUI |
| TG message contains `Hermes 摘要不可用` | Ollama unreachable; check it's running on F:\AI |
| Scheduler runs but no run.log update | Permissions issue — the scheduled task user can't write to `tools/health_monitor/`. Run the bat file once manually as that user to surface the error |

## Manual ops

- **Force re-alert** for the same issue: delete the corresponding entry from `state.json` `per_target` and re-run.
- **Pause monitoring**: disable the Task Scheduler entry; nothing on disk needs cleanup.
- **Change targets**: edit `config.json` `targets[]` — add a new entry with `kind: "local-log"` (file read) or `kind: "ssh-journal"` (SSH).
- **Disable Hermes** (Tier 2): set `ollama_url` to an unreachable address. Tier 1 will still run, falling back to raw-indicator alerts on every trigger.

## Future extensions (not implemented)

- Per-target TG chat (multi-tenant fan-out) — config schema already supports it; add a `tg_chat_id` field to each target entry.
- Auto-remediation (e.g. auto-restart on liveness fail) — deliberately out of scope; too risky without per-action approval.
- Web dashboard — TG is the user-facing channel. Adding a dashboard would be more code for less benefit.
