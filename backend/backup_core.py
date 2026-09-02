"""LiveChord data backup helper.

Tiered backup strategy (ranked by recreate-cost ↓):
  tier1: unrecreatable user/feedback/auth data (~MB, seconds to copy)
  tier2: expensive-to-recompute chords/melodies/models (~GB, minutes)
  tier3: bulk derived caches (accompaniments, indexes) (~15+GB, 10-20 min)

Each run drops a timestamped snapshot under
  <BACKUP_ROOT>/<tier>/YYYYMMDD_HHMMSS/<item>
preserving the source's directory structure. Existing snapshots are never
touched — retention is handled separately (not implemented yet).
"""

import os
import shutil
import time
import json
import logging
import threading
from datetime import datetime
from pathlib import Path

log = logging.getLogger("livechord.backup")
DATA_DIR = Path(__file__).parent.parent / "data"

# Relative paths (to DATA_DIR). Missing paths are silently skipped at copy time.
TIERS = {
    "tier1": [
        # Settings & their rolling snapshots
        "settings_personal.json",
        "settings_beta.json",
        "settings_shared.json",
        "settings.json",                    # legacy archival
        "backups/settings",                 # whole snapshot dir
        # Auth / user / feedback databases (no .wal .shm — checkpoint-safe)
        "auth.db",
        "feedback.db",
        "analytics.db",
        "process_audit.db",
        "audit.db",
        # Human corrections — irreplaceable AI training signal
        "human_feedback",
        "human_sections",
        # User-facing state
        "ratings.json",
        "recent.json",
        "favorites.json",
        "jam_tracks.json",
        "custom_progressions.json",
    ],
    "tier2": [
        # Heavy to recompute
        "chords",
        "melodies",
        "hybrid_bass",
        "hybrid_melody",
        "models",
        "chord_index.json",
        "library_cache.json",
    ],
    "tier3": [
        # Large bulk caches (rebuildable from tier2 + code but slow)
        "accompaniments",
    ],
}

HISTORY_FILE = DATA_DIR / "backup_history.json"
DEFAULT_BACKUP_ROOT = Path(r"W:\LiveChord\backup")

# Live state for the currently-running backup (polled by admin UI)
_state = {
    "running": False,
    "tier": None,
    "started_at": None,
    "current_item": None,
    "progress_items": 0,
    "total_items": 0,
    "last_result": None,
}
_state_lock = threading.Lock()


def get_backup_target() -> Path:
    """Backup destination. Priority: env var > settings_shared.data_backup_root > default."""
    env = os.environ.get("LIVECHORD_BACKUP_ROOT")
    if env:
        return Path(env)
    # Lazy import to avoid a hard dep (auto_worker pulls chord_cache etc.)
    try:
        from auto_worker import load_settings
        configured = (load_settings() or {}).get("data_backup_root")
        if configured:
            return Path(configured)
    except Exception:
        pass
    return DEFAULT_BACKUP_ROOT


def _set_state(**kv):
    with _state_lock:
        _state.update(kv)


def get_state() -> dict:
    with _state_lock:
        return dict(_state)


def _copy_item(src: Path, dst: Path):
    """Copy file or directory tree. Creates parents as needed."""
    if src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    elif src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)


def _count_items(path: Path) -> tuple:
    """Return (file_count, total_bytes) for a file or recursively for a dir."""
    if path.is_file():
        try:
            return 1, path.stat().st_size
        except Exception:
            return 1, 0
    if not path.is_dir():
        return 0, 0
    n, size = 0, 0
    try:
        for f in path.rglob("*"):
            if f.is_file():
                n += 1
                try:
                    size += f.stat().st_size
                except Exception:
                    pass
    except Exception:
        pass
    return n, size


def run_backup(tier: str, dest_root: Path = None) -> dict:
    """Back up items in `tier` to `dest_root/<tier>/<timestamp>/<item>`.

    Blocking; publishes progress to module-level _state for polling.
    Returns the result dict (also appended to backup_history.json).
    """
    if tier not in TIERS:
        raise ValueError(f"unknown tier: {tier}")

    items = TIERS[tier]
    dest_root = Path(dest_root) if dest_root else get_backup_target()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = dest_root / tier / ts

    _set_state(
        running=True,
        tier=tier,
        started_at=ts,
        current_item=None,
        progress_items=0,
        total_items=len(items),
        last_result=None,
    )

    start = time.time()
    files_total, bytes_total = 0, 0
    errors = []

    try:
        dest.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        result = {
            "ok": False, "tier": tier, "timestamp": ts,
            "dest": str(dest), "error": f"cannot create dest: {e}",
            "files": 0, "bytes": 0, "duration_s": 0, "errors": [str(e)],
        }
        _set_state(running=False, last_result=result)
        _append_history(result)
        return result

    for i, item in enumerate(items):
        _set_state(current_item=item, progress_items=i)
        src = DATA_DIR / item
        if not src.exists():
            continue
        try:
            dst = dest / item
            _copy_item(src, dst)
            n, s = _count_items(src)
            files_total += n
            bytes_total += s
        except Exception as e:
            log.warning(f"backup failed for {item}: {e}")
            errors.append(f"{item}: {e}")

    duration = time.time() - start
    result = {
        "ok": len(errors) == 0,
        "tier": tier,
        "timestamp": ts,
        "dest": str(dest),
        "files": files_total,
        "bytes": bytes_total,
        "duration_s": round(duration, 2),
        "errors": errors,
    }
    _set_state(running=False, current_item=None, progress_items=len(items), last_result=result)
    _append_history(result)
    invalidate_tier_info_cache()  # force fresh counts on next info fetch
    return result


def run_backup_async(tier: str, dest_root: Path = None):
    """Kick off a backup on a background thread. Returns immediately.
    Caller should poll get_state() and/or get_history() for completion."""
    if _state.get("running"):
        raise RuntimeError("another backup is already running")
    t = threading.Thread(target=run_backup, args=(tier, dest_root), daemon=True)
    t.start()
    return {"ok": True, "started": True, "tier": tier}


def list_backups(dest_root: Path = None) -> dict:
    """List existing snapshot folders per tier."""
    dest_root = Path(dest_root) if dest_root else get_backup_target()
    out = {}
    for tier in TIERS:
        tier_dir = dest_root / tier
        snapshots = []
        if tier_dir.is_dir():
            try:
                for d in sorted(tier_dir.iterdir(), reverse=True):
                    if d.is_dir():
                        try:
                            st = d.stat()
                            snapshots.append({
                                "name": d.name,
                                "path": str(d),
                                "mtime": datetime.fromtimestamp(st.st_mtime).strftime(
                                    "%Y-%m-%d %H:%M:%S"
                                ),
                            })
                        except Exception:
                            pass
            except Exception:
                pass
        out[tier] = snapshots
    return out


def _load_history() -> list:
    if not HISTORY_FILE.is_file():
        return []
    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _append_history(entry: dict):
    hist = _load_history()
    hist.append(entry)
    if len(hist) > 100:
        hist = hist[-100:]
    try:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        HISTORY_FILE.write_text(
            json.dumps(hist, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        log.warning(f"history write failed: {e}")


def get_history(limit: int = 20) -> list:
    """Newest-first history entries."""
    return list(reversed(_load_history()))[:limit]


_tier_info_cache = {"data": None, "ts": 0.0}
_TIER_INFO_TTL = 60.0  # seconds


def get_tier_info(force: bool = False) -> dict:
    """Per-tier metadata: which declared items currently exist + total size.

    Heavy for tier2/tier3 (rglob over 43k/173k files on SMB — can take tens of
    seconds). Results are cached for 60s so repeated admin-page loads and the
    in-progress polling loop don't re-scan the whole library every time.
    Pass force=True to bypass cache (e.g. right after a backup run).
    """
    now = time.time()
    if not force and _tier_info_cache["data"] is not None \
            and (now - _tier_info_cache["ts"]) < _TIER_INFO_TTL:
        return _tier_info_cache["data"]

    out = {}
    for tier, items in TIERS.items():
        files, bytes_ = 0, 0
        existing = []
        missing = []
        for item in items:
            src = DATA_DIR / item
            if src.exists():
                n, s = _count_items(src)
                files += n
                bytes_ += s
                existing.append(item)
            else:
                missing.append(item)
        out[tier] = {
            "existing_items": existing,
            "missing_items": missing,
            "files": files,
            "bytes": bytes_,
        }

    _tier_info_cache["data"] = out
    _tier_info_cache["ts"] = now
    return out


def invalidate_tier_info_cache():
    """Call after a backup completes so UI sees fresh numbers immediately."""
    _tier_info_cache["data"] = None
    _tier_info_cache["ts"] = 0.0
