"""Background scheduler that auto-runs tiered data backups.

Reads the user's schedule from settings_shared.backup_schedule, shape:

    "backup_schedule": {
        "tier1": {"interval": "daily",   "hour": 3},
        "tier2": {"interval": "weekly",  "hour": 3, "day_of_week": 0},
        "tier3": {"interval": "monthly", "hour": 3, "day_of_month": 1},
    }

interval: "off" | "daily" | "weekly" | "monthly"
hour:     0-23  (local time)
day_of_week:  0=Monday ... 6=Sunday  (matches datetime.weekday())
day_of_month: 1-28  (capped so it always exists in every month)

The scheduler wakes every 5 minutes and triggers `backup_core.run_backup_async`
when a tier's due window matches the current wall-clock time and the tier
hasn't been successfully backed up recently (based on history timestamps).
Personal-instance-only — beta never runs backups (lifespan gate in main.py).
"""

import threading
import time
import logging
from datetime import datetime

log = logging.getLogger("livechord.backup_scheduler")

_thread = None
_stop_event = threading.Event()
_CHECK_INTERVAL_S = 300  # 5 min

# Minimum gap since last successful run before we're allowed to fire again
# (prevents multiple triggers in the same hour window).
_MIN_GAP_S = {
    "daily":   20 * 3600,      # 20h — enough to skip today's own fire
    "weekly":  6 * 86400,      # 6 days
    "monthly": 25 * 86400,     # 25 days
}


def _last_success(tier: str):
    """Most recent successful backup timestamp for `tier`, or None."""
    try:
        from backup_core import _load_history
    except ImportError:
        return None
    for entry in reversed(_load_history() or []):
        if entry.get("tier") == tier and entry.get("ok"):
            ts = entry.get("timestamp", "")
            try:
                return datetime.strptime(ts, "%Y%m%d_%H%M%S")
            except Exception:
                pass
    return None


def _should_run(tier: str, cfg: dict, now: datetime) -> bool:
    interval = (cfg or {}).get("interval", "off")
    if interval not in ("daily", "weekly", "monthly"):
        return False
    try:
        hour = int(cfg.get("hour", 3))
    except Exception:
        hour = 3
    # Hour window: trigger when current hour matches OR current hour is within
    # _CHECK_INTERVAL_S of the configured hour (safety net if scheduler wakes
    # slightly late). Keep it strict: same-hour match only.
    if now.hour != hour:
        return False

    if interval == "weekly":
        try:
            wd = int(cfg.get("day_of_week", 0))
        except Exception:
            wd = 0
        if now.weekday() != wd:
            return False
    elif interval == "monthly":
        try:
            dom = int(cfg.get("day_of_month", 1))
        except Exception:
            dom = 1
        dom = max(1, min(28, dom))
        if now.day != dom:
            return False

    last = _last_success(tier)
    min_gap = _MIN_GAP_S.get(interval, 20 * 3600)
    if last is None:
        return True
    return (now - last).total_seconds() > min_gap


def _loop():
    import backup_core
    try:
        from auto_worker import load_settings
    except ImportError:
        log.warning("backup_scheduler: can't import load_settings, thread exiting")
        return
    log.info("backup scheduler started")
    while not _stop_event.is_set():
        try:
            settings = load_settings() or {}
            sched = settings.get("backup_schedule") or {}
            now = datetime.now()
            for tier in backup_core.TIERS:
                cfg = sched.get(tier)
                if not cfg:
                    continue
                if _should_run(tier, cfg, now):
                    if backup_core.get_state().get("running"):
                        break  # another backup already running
                    try:
                        log.info(f"auto-triggering backup: {tier} (interval={cfg.get('interval')})")
                        backup_core.run_backup_async(tier)
                    except RuntimeError:
                        pass  # race with manual trigger, try next cycle
                    break  # only one tier per wake-up
        except Exception as e:
            log.warning(f"scheduler tick error: {e}")
        _stop_event.wait(_CHECK_INTERVAL_S)
    log.info("backup scheduler stopped")


def start():
    """Start the scheduler thread. Idempotent."""
    global _thread
    if _thread and _thread.is_alive():
        return False
    _stop_event.clear()
    _thread = threading.Thread(target=_loop, daemon=True, name="backup-scheduler")
    _thread.start()
    return True


def stop():
    _stop_event.set()
    return True


def is_running() -> bool:
    return bool(_thread and _thread.is_alive())
