"""LiveChord health monitor — Tier 1 probes + Tier 2 Hermes summarization + Tier 3 Telegram.

Designed to run on the PC every 10 min via Windows Task Scheduler. Two targets
out of the box: NUC personal at 192.168.50.6:8800 (log read from V:\\data\\server.log,
which is mounted on PC) and VPS public at livechord.org (log fetched via
`ssh livechord-vps "journalctl -u livechord -n 200"`).

Tier 1 (always runs): cheap deterministic checks — HTTP liveness + log slice
indicators (error count, traceback count, 5xx count, last-error age).

Tier 2 (only if Tier 1 trips): Ollama call to summarize the suspicious slice
into one Chinese sentence + severity verdict. Falls back to raw indicators if
Ollama is unreachable.

Tier 3: Telegram bot push. Cooldown by fingerprint to avoid spam.

CLI:
    python check.py --target both                  # normal scheduled run
    python check.py --target nuc --dry-run         # probe + print, no TG
    python check.py --target vps --inject-error    # force a trigger for testing

Config: tools/health_monitor/config.json (see README.md for schema).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import logging
import logging.handlers
import os
import re
import shlex
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
CONFIG_FILE = HERE / "config.json"
STATE_FILE = HERE / "state.json"
RUN_LOG = HERE / "run.log"

TZ = ZoneInfo("Asia/Taipei")

# Indicator regexes — log lines look like:
#   2026-05-10 00:00:04,858 [INFO] uvicorn.access: 1.2.3.4 - "GET /x HTTP/1.1" 200
#   2026-05-10 00:00:04,858 [ERROR] livechord.auto: 偵測失敗: ...
RE_LEVEL = re.compile(r"\[(ERROR|WARN|WARNING|CRITICAL)\]")
RE_TRACEBACK = re.compile(r"^Traceback \(most recent call last\):", re.MULTILINE)
RE_5XX = re.compile(r'HTTP/1\.1" 5\d\d')
RE_TS_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


# ---------------------------------------------------------------------------
# Logging — own rolling log so we don't pollute server.log
# ---------------------------------------------------------------------------

logger = logging.getLogger("health_monitor")
logger.setLevel(logging.INFO)
_h = logging.handlers.RotatingFileHandler(
    RUN_LOG, maxBytes=1_000_000, backupCount=2, encoding="utf-8"
)
_h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(_h)
# Also stderr so --dry-run shows live output
_se = logging.StreamHandler(sys.stderr)
_se.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
logger.addHandler(_se)


# ---------------------------------------------------------------------------
# Config + state
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "tg_token": "",
    "tg_chat_id": 0,  # 0 → auto-read from access.json's allowFrom[0]
    "ollama_url": "http://localhost:11434",
    "ollama_model": "hermes3:8b",
    "cooldown_minutes": 30,
    "quiet_hours": {"enabled": True, "start": "23:00", "end": "08:00"},
    "tier2_severity_threshold": "warning",
    "targets": [
        {
            "name": "NUC 8800",
            "kind": "local-log",
            "url": "http://192.168.50.6:8800/api/recent",
            "log_path": "V:/data/server.log",
            "log_tail_lines": 200,
        },
        {
            "name": "VPS livechord.org",
            "kind": "ssh-journal",
            "url": "https://livechord.org/api/recent",
            "ssh_host": "livechord-vps",
            "journal_unit": "livechord",
            "feedback_db_path": "/srv/livechord/data/feedback.db",
            "log_tail_lines": 200,
        },
    ],
}


TELEGRAM_DIR = Path.home() / ".claude" / "channels" / "telegram"
TELEGRAM_ENV_FILE = TELEGRAM_DIR / ".env"
TELEGRAM_ACCESS_FILE = TELEGRAM_DIR / "access.json"


def _read_telegram_token_from_env() -> str:
    """Lazy-read TELEGRAM_BOT_TOKEN from the Claude Code telegram channel's .env.
    Returns empty string if file missing or token absent. Avoids storing the
    token a second time in config.json — single source of truth at the plugin's
    own .env. User can still override by setting tg_token directly in config.json."""
    if not TELEGRAM_ENV_FILE.is_file():
        return ""
    try:
        for line in TELEGRAM_ENV_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception as e:
        logger.warning(f"reading {TELEGRAM_ENV_FILE} failed: {e}")
    return ""


def _read_telegram_chat_id_from_access() -> int:
    """Pick the user's TG chat_id from access.json (first numeric entry in
    allowFrom[]). Lets the committed config.json stay free of personal IDs."""
    if not TELEGRAM_ACCESS_FILE.is_file():
        return 0
    try:
        data = json.loads(TELEGRAM_ACCESS_FILE.read_text(encoding="utf-8"))
        allow = data.get("allowFrom") or []
        for entry in allow:
            try:
                cid = int(entry)
                if cid > 0:
                    return cid
            except (TypeError, ValueError):
                continue
    except Exception as e:
        logger.warning(f"reading {TELEGRAM_ACCESS_FILE} failed: {e}")
    return 0


def load_config() -> dict:
    if not CONFIG_FILE.is_file():
        logger.warning(f"config.json not found at {CONFIG_FILE}; writing template")
        CONFIG_FILE.write_text(
            json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        cfg = dict(DEFAULT_CONFIG)
    else:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        # Shallow-merge defaults so missing keys don't crash later
        for k, v in DEFAULT_CONFIG.items():
            cfg.setdefault(k, v)
    # Token resolution: explicit non-placeholder value in config wins; otherwise
    # auto-read from the telegram plugin's .env so a single token rotation
    # (via the /telegram:configure skill) propagates here without manual copy.
    token = (cfg.get("tg_token") or "").strip()
    if not token or token == "PASTE_BOT_TOKEN_HERE":
        env_token = _read_telegram_token_from_env()
        if env_token:
            cfg["tg_token"] = env_token
            cfg["_token_source"] = str(TELEGRAM_ENV_FILE)
    # chat_id resolution: same pattern. Committed config.json keeps tg_chat_id=0
    # so personal TG user IDs don't end up in the public repo.
    if not cfg.get("tg_chat_id"):
        cid = _read_telegram_chat_id_from_access()
        if cid:
            cfg["tg_chat_id"] = cid
            cfg["_chat_id_source"] = str(TELEGRAM_ACCESS_FILE)
    return cfg


def load_state() -> dict:
    if not STATE_FILE.is_file():
        return {"per_target": {}, "last_run_ts": ""}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"state.json unreadable ({e}); resetting")
        return {"per_target": {}, "last_run_ts": ""}


def save_state(state: dict) -> None:
    state["last_run_ts"] = dt.datetime.now(TZ).isoformat(timespec="seconds")
    STATE_FILE.write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Probing
# ---------------------------------------------------------------------------

def probe_http(url: str, timeout: float = 10.0) -> dict:
    """Returns {ok, status, latency_ms, error}."""
    t0 = dt.datetime.now()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "livechord-health-monitor"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            ok = 200 <= status < 400
        latency_ms = int((dt.datetime.now() - t0).total_seconds() * 1000)
        return {"ok": ok, "status": status, "latency_ms": latency_ms, "error": ""}
    except urllib.error.HTTPError as e:
        latency_ms = int((dt.datetime.now() - t0).total_seconds() * 1000)
        # 4xx is technically "alive" — we count it as ok=true, alert path triggers on 5xx via log
        ok = 400 <= e.code < 500
        return {"ok": ok, "status": e.code, "latency_ms": latency_ms, "error": str(e)}
    except Exception as e:
        latency_ms = int((dt.datetime.now() - t0).total_seconds() * 1000)
        return {"ok": False, "status": 0, "latency_ms": latency_ms, "error": f"{type(e).__name__}: {e}"}


def fetch_log_tail(target: dict) -> tuple[str, str]:
    """Returns (text, error). text is the last N lines of the target's log."""
    n = int(target.get("log_tail_lines", 200))
    if target["kind"] == "local-log":
        p = Path(target["log_path"])
        if not p.is_file():
            return "", f"log file missing: {p}"
        try:
            # Read last ~64 KB to keep memory low; should cover 200 lines comfortably
            with open(p, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                back = min(size, 256_000)
                f.seek(size - back)
                raw = f.read()
            text = raw.decode("utf-8", errors="replace")
            lines = text.splitlines()
            return "\n".join(lines[-n:]), ""
        except Exception as e:
            return "", f"{type(e).__name__}: {e}"
    elif target["kind"] == "ssh-journal":
        try:
            cmd = [
                "ssh", target["ssh_host"],
                f"journalctl -u {target['journal_unit']} -n {n} --no-pager"
            ]
            out = subprocess.run(
                cmd, capture_output=True, text=True, timeout=20, encoding="utf-8", errors="replace"
            )
            if out.returncode != 0:
                return out.stdout, f"ssh exit {out.returncode}: {out.stderr.strip()[:200]}"
            return out.stdout, ""
        except subprocess.TimeoutExpired:
            return "", "ssh timeout (>20s)"
        except Exception as e:
            return "", f"{type(e).__name__}: {e}"
    else:
        return "", f"unknown target kind: {target['kind']}"


def fetch_feedback_snapshot(target: dict) -> tuple[dict, str]:
    """Return recent feedback report state for targets that opt in.

    The VPS path is read over SSH so Telegram credentials stay on the PC health
    monitor instead of being copied to the public server.
    """
    db_path = target.get("feedback_db_path")
    if not db_path:
        return {}, ""
    script = r'''
import json, sqlite3, sys, time
from pathlib import Path

path = Path(sys.argv[1])
empty = {"max_id": 0, "open_count": 0, "recent_1h": 0, "latest": []}
if not path.is_file():
    print(json.dumps(empty))
    raise SystemExit(0)

cutoff = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 3600))
with sqlite3.connect(path, timeout=10) as conn:
    conn.row_factory = sqlite3.Row
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(bug_reports)").fetchall()}
    summary = conn.execute("""
        SELECT COALESCE(MAX(id), 0) AS max_id,
               SUM(CASE WHEN status IN ('open','in_progress') THEN 1 ELSE 0 END) AS open_count,
               SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END) AS recent_1h
        FROM bug_reports
    """, (cutoff,)).fetchone()
    optional = {
        "song_hash": "song_hash" if "song_hash" in cols else "'' AS song_hash",
        "song_title": "song_title" if "song_title" in cols else "'' AS song_title",
        "duplicate_count": "duplicate_count" if "duplicate_count" in cols else "1 AS duplicate_count",
    }
    latest = conn.execute(f"""
        SELECT id, category, username, description, {optional['song_hash']}, {optional['song_title']},
               {optional['duplicate_count']}, status, created_at
        FROM bug_reports
        ORDER BY id DESC LIMIT 8
    """).fetchall()
out = {
    "max_id": int(summary["max_id"] or 0),
    "open_count": int(summary["open_count"] or 0),
    "recent_1h": int(summary["recent_1h"] or 0),
    "latest": [
        {
            "id": int(r["id"]),
            "category": r["category"] or "",
            "username": r["username"] or "",
            "description": (r["description"] or "")[:180],
            "song_hash": r["song_hash"] or "",
            "song_title": r["song_title"] or "",
            "duplicate_count": int(r["duplicate_count"] or 1),
            "status": r["status"] or "",
            "created_at": r["created_at"] or "",
        }
        for r in latest
    ],
}
print(json.dumps(out, ensure_ascii=False))
'''
    if target.get("kind") == "ssh-journal":
        remote = (
            "sudo -u livechord -H /srv/livechord/.venv/bin/python -c "
            + shlex.quote(script)
            + " "
            + shlex.quote(str(db_path))
        )
        try:
            out = subprocess.run(
                ["ssh", target["ssh_host"], remote],
                capture_output=True,
                text=True,
                timeout=20,
                encoding="utf-8",
                errors="replace",
            )
            if out.returncode != 0:
                return {}, f"feedback ssh exit {out.returncode}: {out.stderr.strip()[:200]}"
            return json.loads(out.stdout.strip() or "{}"), ""
        except Exception as e:
            return {}, f"{type(e).__name__}: {e}"
    return {}, "feedback_db_path is only implemented for ssh-journal targets"


def compute_indicators(log_text: str) -> dict:
    """Hard rule-based indicators from a log slice."""
    if not log_text:
        return {
            "error_count": 0, "warn_count": 0, "traceback_count": 0,
            "_5xx_count": 0, "last_error_age_min": None, "last_error_line": "",
        }
    error_count = 0
    warn_count = 0
    last_error_ts: dt.datetime | None = None
    last_error_line = ""
    for line in log_text.splitlines():
        m = RE_LEVEL.search(line)
        if m:
            lvl = m.group(1)
            if lvl in ("ERROR", "CRITICAL"):
                error_count += 1
                ts_match = RE_TS_PREFIX.search(line)
                if ts_match:
                    try:
                        last_error_ts = dt.datetime.strptime(
                            ts_match.group(1), "%Y-%m-%d %H:%M:%S"
                        ).replace(tzinfo=TZ)
                        last_error_line = line.strip()
                    except Exception:
                        pass
            elif lvl in ("WARN", "WARNING"):
                warn_count += 1
    traceback_count = len(RE_TRACEBACK.findall(log_text))
    _5xx_count = len(RE_5XX.findall(log_text))
    last_error_age_min: float | None = None
    if last_error_ts is not None:
        age = (dt.datetime.now(TZ) - last_error_ts).total_seconds() / 60.0
        last_error_age_min = round(age, 1)
    return {
        "error_count": error_count,
        "warn_count": warn_count,
        "traceback_count": traceback_count,
        "_5xx_count": _5xx_count,
        "last_error_age_min": last_error_age_min,
        "last_error_line": last_error_line[:300],
    }


# ---------------------------------------------------------------------------
# Anomaly trigger + fingerprint + cooldown
# ---------------------------------------------------------------------------

def trigger_reason(probe: dict, ind: dict) -> str | None:
    """Return a short reason string if anomaly should fire, else None."""
    if not probe.get("ok"):
        return f"liveness probe failed (status={probe.get('status')}, error={probe.get('error', '')[:80]})"
    if ind["traceback_count"] >= 1:
        return f"traceback x{ind['traceback_count']}"
    if ind["error_count"] >= 3:
        return f"error_count={ind['error_count']}"
    if ind["last_error_age_min"] is not None and ind["last_error_age_min"] < 5:
        return f"fresh error ({ind['last_error_age_min']:.1f} min old)"
    if ind["_5xx_count"] >= 1:
        return f"5xx x{ind['_5xx_count']}"
    return None


def fingerprint(probe: dict, ind: dict) -> str:
    """Stable hash of the dominant error so a flapping issue doesn't spam."""
    # Use the last error line if any, else status code, else "live-down"
    key = ind.get("last_error_line") or f"http-{probe.get('status', 0)}"
    return hashlib.sha256(key.encode("utf-8", errors="replace")).hexdigest()[:16]


def is_suppressed(state: dict, target_name: str, fp: str) -> bool:
    rec = state["per_target"].get(target_name)
    if not rec or rec.get("fingerprint") != fp:
        return False
    until = rec.get("suppressed_until", "")
    if not until:
        return False
    try:
        until_dt = dt.datetime.fromisoformat(until)
        return dt.datetime.now(TZ) < until_dt
    except Exception:
        return False


def mark_suppressed(state: dict, target_name: str, fp: str, cooldown_minutes: int) -> None:
    rec = state["per_target"].setdefault(target_name, {})
    rec.update({
        "fingerprint": fp,
        "suppressed_until": (dt.datetime.now(TZ) + dt.timedelta(minutes=cooldown_minutes))
            .isoformat(timespec="seconds"),
    })


def mark_feedback_seen(state: dict, target_name: str, max_id: int) -> None:
    rec = state["per_target"].setdefault(target_name, {})
    rec["feedback_last_id"] = int(max_id or 0)


# ---------------------------------------------------------------------------
# Quiet hours
# ---------------------------------------------------------------------------

def in_quiet_hours(cfg: dict, severity: str) -> bool:
    qh = cfg.get("quiet_hours") or {}
    if not qh.get("enabled"):
        return False
    if severity == "critical":
        return False  # critical always breaks through
    try:
        start = dt.datetime.strptime(qh["start"], "%H:%M").time()
        end = dt.datetime.strptime(qh["end"], "%H:%M").time()
    except Exception:
        return False
    now = dt.datetime.now(TZ).time()
    if start <= end:
        return start <= now < end
    # Window crosses midnight (e.g. 23:00 → 08:00)
    return now >= start or now < end


# ---------------------------------------------------------------------------
# Hermes (Tier 2)
# ---------------------------------------------------------------------------

HERMES_PROMPT = """You are a server health analyst for LiveChord, a music chord-detection web app
(FastAPI backend, Python 3.14 on Windows for NUC, Linux for VPS). Below is the
last log slice and liveness probe for one server. Identify the most important
anomaly and respond as STRICT JSON:
{{
  "severity": "critical" | "warning" | "info",
  "summary": "one sentence in 繁中, max 80 chars",
  "root_cause_guess": "one sentence in 繁中, may be 'unknown'",
  "suggested_action": "one sentence in 繁中, e.g. restart, check disk, ignore"
}}
No markdown, no extra prose. JSON only.

Target: {target_name}
Liveness: ok={probe_ok} status={probe_status} latency_ms={latency} error={probe_err}
Indicators: error_count={error_count} traceback={traceback_count} 5xx={_5xx_count} last_error_age_min={age}

Log slice (last {n} lines):
---
{log}
---
"""


def call_hermes(cfg: dict, target_name: str, probe: dict, ind: dict, log_text: str) -> dict:
    """Tier 2 — returns {severity, summary, root_cause_guess, suggested_action}.
    Falls back to raw-indicator severity=warning if Ollama is unreachable."""
    fallback = {
        "severity": "warning",
        "summary": "Hermes 摘要不可用，請查 server.log",
        "root_cause_guess": ind.get("last_error_line", "")[:80] or "unknown",
        "suggested_action": "登入伺服器查看完整 traceback",
    }
    prompt = HERMES_PROMPT.format(
        target_name=target_name,
        probe_ok=probe.get("ok"),
        probe_status=probe.get("status"),
        latency=probe.get("latency_ms"),
        probe_err=(probe.get("error") or "")[:120],
        error_count=ind["error_count"],
        traceback_count=ind["traceback_count"],
        _5xx_count=ind["_5xx_count"],
        age=ind["last_error_age_min"],
        n=len(log_text.splitlines()),
        # Truncate log to keep prompt under ~8 KB
        log=log_text[-6000:],
    )
    payload = {
        "model": cfg["ollama_model"],
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.2, "num_ctx": 4096},
    }
    try:
        req = urllib.request.Request(
            cfg["ollama_url"].rstrip("/") + "/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        raw = data.get("response", "").strip()
        # Ollama with format=json should return clean JSON, but defend anyway
        try:
            verdict = json.loads(raw)
        except Exception:
            # Strip markdown fences if present
            cleaned = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
            verdict = json.loads(cleaned)
        sev = verdict.get("severity", "warning")
        if sev not in ("critical", "warning", "info"):
            sev = "warning"
        return {
            "severity": sev,
            "summary": str(verdict.get("summary", ""))[:200],
            "root_cause_guess": str(verdict.get("root_cause_guess", ""))[:200],
            "suggested_action": str(verdict.get("suggested_action", ""))[:200],
        }
    except Exception as e:
        logger.warning(f"hermes call failed: {type(e).__name__}: {e}")
        return fallback


# ---------------------------------------------------------------------------
# Telegram (Tier 3)
# ---------------------------------------------------------------------------

SEVERITY_ICON = {"critical": "🚨", "warning": "⚠️", "info": "ℹ️"}


def send_telegram_text(cfg: dict, text: str) -> bool:
    token = cfg.get("tg_token", "").strip()
    chat_id = cfg.get("tg_chat_id")
    if not token or not chat_id:
        logger.warning("telegram token/chat_id not configured; skipping send")
        return False
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            res = json.loads(resp.read().decode("utf-8"))
        if not res.get("ok"):
            logger.warning(f"telegram error: {res}")
            return False
        return True
    except Exception as e:
        logger.warning(f"telegram send failed: {type(e).__name__}: {e}")
        return False


def send_telegram(cfg: dict, target_name: str, verdict: dict, probe: dict, ind: dict) -> bool:
    icon = SEVERITY_ICON.get(verdict["severity"], "•")
    age = ind.get("last_error_age_min")
    age_txt = f"{age:.1f} 分鐘前" if isinstance(age, (int, float)) else "—"
    # HTML parse_mode (Telegram Bot API): more tolerant than Markdown, requires
    # only <, >, & escaping in dynamic content. Avoids the parsing failures
    # we hit when Hermes-generated text or log lines contain * / _ / [ chars.
    e = html.escape  # short alias
    text = (
        f"{icon} <b>LiveChord {e(target_name)}</b> — {e(verdict['severity'])}\n"
        f"{e(verdict['summary'])}\n\n"
        f"<b>推測原因</b>: {e(verdict['root_cause_guess'])}\n"
        f"<b>建議</b>: {e(verdict['suggested_action'])}\n\n"
        f"<i>probe: status={e(str(probe.get('status')))} {e(str(probe.get('latency_ms')))}ms · "
        f"err={ind['error_count']} tb={ind['traceback_count']} 5xx={ind['_5xx_count']} · "
        f"last_err={e(age_txt)}</i>"
    )
    return send_telegram_text(cfg, text)


def send_feedback_telegram(cfg: dict, target_name: str, feedback: dict, new_reports: list[dict]) -> bool:
    severity = "critical" if int(feedback.get("recent_1h") or 0) >= 10 else "warning"
    icon = SEVERITY_ICON.get(severity, "•")
    e = html.escape
    lines = [
        f"{icon} <b>LiveChord {e(target_name)}</b> — {e(severity)}",
        f"New in-site reports: {len(new_reports)} · open={e(str(feedback.get('open_count', 0)))} · 1h={e(str(feedback.get('recent_1h', 0)))}",
        "",
    ]
    for r in new_reports[:5]:
        dup = int(r.get("duplicate_count") or 1)
        title = r.get("song_title") or r.get("song_hash") or ""
        suffix = f" · {title}" if title else ""
        if dup > 1:
            suffix += f" · x{dup}"
        lines.append(
            f"#{e(str(r.get('id')))} [{e(r.get('category', ''))}] "
            f"{e((r.get('description') or '')[:120])}{e(suffix)}"
        )
    lines.extend(["", "Open /admin to triage; no automatic email reply is sent."])
    return send_telegram_text(cfg, "\n".join(lines))


# ---------------------------------------------------------------------------
# Per-target orchestration
# ---------------------------------------------------------------------------

def check_target(cfg: dict, state: dict, target: dict, *, dry_run: bool, force_trigger: bool) -> dict:
    name = target["name"]
    logger.info(f"--- {name} ---")
    probe = probe_http(target["url"])
    log_text, log_err = fetch_log_tail(target)
    ind = compute_indicators(log_text)
    if log_err:
        logger.warning(f"{name}: log fetch error: {log_err}")
    feedback, feedback_err = fetch_feedback_snapshot(target)
    if feedback_err:
        logger.warning(f"{name}: feedback fetch error: {feedback_err}")

    reason = trigger_reason(probe, ind)
    if force_trigger and not reason:
        reason = "forced via --inject-error"

    summary_line = (
        f"{name}: probe.ok={probe['ok']} status={probe['status']} {probe['latency_ms']}ms · "
        f"err={ind['error_count']} tb={ind['traceback_count']} 5xx={ind['_5xx_count']} · "
        f"reason={reason or 'none'}"
    )
    logger.info(summary_line)

    result: dict[str, Any] = {
        "name": name, "probe": probe, "indicators": ind,
        "reason": reason, "alerted": False, "verdict": None,
        "feedback": feedback or None,
    }
    if feedback:
        rec = state["per_target"].get(name) or {}
        last_feedback_id = int(rec.get("feedback_last_id") or 0)
        max_feedback_id = int(feedback.get("max_id") or 0)
        if max_feedback_id > 0 and last_feedback_id == 0:
            logger.info(f"{name}: initializing feedback_last_id={max_feedback_id}")
            if not dry_run:
                mark_feedback_seen(state, name, max_feedback_id)
        elif max_feedback_id > last_feedback_id:
            new_reports = [
                r for r in (feedback.get("latest") or [])
                if int(r.get("id") or 0) > last_feedback_id
            ]
            new_reports.sort(key=lambda r: int(r.get("id") or 0))
            logger.info(f"{name}: new feedback reports x{len(new_reports)} max_id={max_feedback_id}")
            result["feedback_new_reports"] = len(new_reports)
            severity = "critical" if int(feedback.get("recent_1h") or 0) >= 10 else "warning"
            if dry_run:
                logger.info(f"{name}: --dry-run set, skipping feedback TG send")
            elif in_quiet_hours(cfg, severity):
                logger.info(f"{name}: in quiet hours, skipping feedback TG (severity={severity})")
                mark_feedback_seen(state, name, max_feedback_id)
                result["feedback_quiet_hours_skip"] = True
            else:
                ok = send_feedback_telegram(cfg, name, feedback, new_reports)
                result["feedback_alerted"] = ok
                if ok:
                    mark_feedback_seen(state, name, max_feedback_id)

    if not reason:
        return result

    fp = fingerprint(probe, ind)
    if is_suppressed(state, name, fp):
        logger.info(f"{name}: suppressed by cooldown (fp={fp})")
        result["suppressed"] = True
        return result

    # Tier 2 — Hermes summarization
    verdict = call_hermes(cfg, name, probe, ind, log_text)
    logger.info(f"{name}: hermes severity={verdict['severity']} — {verdict['summary']}")
    result["verdict"] = verdict

    # Quiet hours gate
    if in_quiet_hours(cfg, verdict["severity"]):
        logger.info(f"{name}: in quiet hours, skipping TG (severity={verdict['severity']})")
        result["quiet_hours_skip"] = True
        # Still mark suppressed so we don't re-spam at end of quiet hours
        mark_suppressed(state, name, fp, cfg["cooldown_minutes"])
        return result

    # Tier 3 — Telegram
    if dry_run:
        logger.info(f"{name}: --dry-run set, skipping TG send")
    else:
        ok = send_telegram(cfg, name, verdict, probe, ind)
        result["alerted"] = ok
        if ok:
            mark_suppressed(state, name, fp, cfg["cooldown_minutes"])
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description="LiveChord health monitor")
    p.add_argument("--target", choices=["both", "nuc", "vps"], default="both",
                   help="Which target(s) to probe")
    p.add_argument("--dry-run", action="store_true",
                   help="Run probes + Hermes but don't send Telegram")
    p.add_argument("--inject-error", action="store_true",
                   help="Force a trigger even when no anomaly detected (testing)")
    args = p.parse_args()

    cfg = load_config()
    state = load_state()

    targets = cfg["targets"]
    if args.target == "nuc":
        targets = [t for t in targets if "NUC" in t["name"] or t["kind"] == "local-log"]
    elif args.target == "vps":
        targets = [t for t in targets if t["kind"] == "ssh-journal"]

    results = []
    for t in targets:
        try:
            r = check_target(cfg, state, t, dry_run=args.dry_run, force_trigger=args.inject_error)
            results.append(r)
        except Exception as e:
            logger.exception(f"{t['name']}: check failed: {e}")

    save_state(state)
    # Exit 0 always — scheduler shouldn't see failures from anomalies, only from script bugs
    return 0


if __name__ == "__main__":
    sys.exit(main())
