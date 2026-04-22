"""Background worker for on-demand beat upgrades.

Decouples the slow madmom run (~30s/song) from the HTTP request so the
client doesn't sit on a blocked connection. Pattern mirrors process_queue
but smaller: single in-memory queue, daemon worker, dict-based status
lookup keyed by song hash (one in-flight upgrade per song).

Lifecycle:
- ``enqueue(hash, audio_path, sheet_path)`` → returns "queued" / "running"
  / "duplicate" (already in flight). Worker picks up and runs madmom.
- ``get_status(hash)`` → returns dict ``{status, started_at, completed_at,
  error, result}`` or ``None`` if no record exists for this hash.

Status records are kept in memory and survive until the next process
restart. We keep the most recent ~100 results so frontend polling can
fetch a "done" record minutes after the user clicked. On overflow we
drop the oldest non-running record.

This module is import-safe even when madmom is missing — the worker
just records an error result and moves on.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger(__name__)

# Status values surfaced to the frontend
QUEUED = "queued"
RUNNING = "running"
DONE = "done"
ERROR = "error"

# Cap how many completed records we retain in memory
_MAX_RECORDS = 200


@dataclass
class _Record:
    hash: str
    audio_path: str
    sheet_path: str
    title: str = ""
    status: str = QUEUED
    started_at: float = 0.0
    completed_at: float = 0.0
    error: Optional[str] = None
    result: Optional[dict] = None  # {bpm, beats_source, beat_version, n_beats, n_downbeats, tempo_range}
    submitted_at: float = field(default_factory=time.time)
    requested_by: str = ""


_jobs: dict[str, _Record] = {}
_queue: "queue.Queue[str]" = queue.Queue()
_lock = threading.Lock()
_worker_started = False


def _evict_old_records():
    """Trim _jobs back to _MAX_RECORDS, dropping oldest non-active entries."""
    if len(_jobs) <= _MAX_RECORDS:
        return
    # Sort by completion time (oldest first), keep currently-active ones
    candidates = sorted(
        ((h, r) for h, r in _jobs.items() if r.status in (DONE, ERROR)),
        key=lambda kv: kv[1].completed_at or kv[1].submitted_at,
    )
    n_to_drop = len(_jobs) - _MAX_RECORDS
    for h, _ in candidates[:n_to_drop]:
        _jobs.pop(h, None)


def enqueue(hash: str, audio_path: str, sheet_path: str,
            title: str = "", requested_by: str = "") -> str:
    """Add an upgrade job for ``hash``. Returns the new status string.

    If a job for this hash is already queued/running, returns "duplicate"
    without re-queueing. If a previous job finished (DONE/ERROR), the
    record is replaced with a fresh QUEUED record and re-queued.
    """
    with _lock:
        existing = _jobs.get(hash)
        if existing and existing.status in (QUEUED, RUNNING):
            return "duplicate"
        _jobs[hash] = _Record(
            hash=hash, audio_path=audio_path, sheet_path=sheet_path,
            title=title, requested_by=requested_by,
        )
        _evict_old_records()
    _queue.put(hash)
    _ensure_worker()
    return QUEUED


def get_status(hash: str) -> Optional[dict]:
    """Return a serializable snapshot of the job for this hash, or None."""
    with _lock:
        rec = _jobs.get(hash)
    if rec is None:
        return None
    out = asdict(rec)
    # Don't expose internal-only paths to the client unless useful
    out.pop("audio_path", None)
    out.pop("sheet_path", None)
    return out


def _process_one(hash: str):
    """Worker handler: run madmom on the queued hash and write the result."""
    from beat_snap import analyze_and_snap_dynamic, HAS_MADMOM, MADMOM_IMPORT_ERROR

    with _lock:
        rec = _jobs.get(hash)
        if rec is None:
            return
        rec.status = RUNNING
        rec.started_at = time.time()
        audio_path = rec.audio_path
        sheet_path = rec.sheet_path

    if not HAS_MADMOM:
        with _lock:
            rec.status = ERROR
            rec.completed_at = time.time()
            rec.error = f"madmom not installed ({MADMOM_IMPORT_ERROR or 'unknown'})"
        logger.warning("upgrade-beats %s: madmom missing", hash)
        return

    if not audio_path or not os.path.isfile(audio_path):
        with _lock:
            rec.status = ERROR
            rec.completed_at = time.time()
            rec.error = "audio file not found"
        return

    try:
        sheet = json.loads(open(sheet_path, encoding="utf-8").read())
    except Exception as e:
        with _lock:
            rec.status = ERROR
            rec.completed_at = time.time()
            rec.error = f"chord JSON read failed: {type(e).__name__}: {e}"
        return

    chords = sheet.get("chords") or []
    if not chords:
        with _lock:
            rec.status = ERROR
            rec.completed_at = time.time()
            rec.error = "chord JSON has no chords"
        return

    # Backup .bak.beats (first call wins)
    bak = sheet_path + ".bak.beats"
    if not os.path.exists(bak):
        try:
            with open(bak, "w", encoding="utf-8") as f:
                json.dump(sheet, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("upgrade-beats %s: backup failed: %s", hash, e)

    # Run madmom
    chords_copy = [dict(c) for c in chords]
    try:
        info = analyze_and_snap_dynamic(audio_path, chords_copy, prefer_madmom=True)
    except Exception as e:
        with _lock:
            rec.status = ERROR
            rec.completed_at = time.time()
            rec.error = f"madmom failed: {type(e).__name__}: {e}"
        logger.error("upgrade-beats %s: madmom error: %s", hash, e)
        return

    if not info.get("beats_source"):
        with _lock:
            rec.status = ERROR
            rec.completed_at = time.time()
            rec.error = "no beats detected"
        return

    sheet["chords"] = chords_copy
    if info.get("bpm"):
        sheet["bpm"] = round(info["bpm"], 1)
    sheet["beats"] = info.get("beats", [])
    sheet["downbeats"] = info.get("downbeats", [])
    sheet["tempo_curve"] = info.get("tempo_curve", [])
    sheet["beats_source"] = info["beats_source"]
    sheet["beat_version"] = int(sheet.get("beat_version", 0)) + 1

    tmp = sheet_path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(sheet, f, ensure_ascii=False, indent=2)
        os.replace(tmp, sheet_path)
    except Exception as e:
        with _lock:
            rec.status = ERROR
            rec.completed_at = time.time()
            rec.error = f"write failed: {type(e).__name__}: {e}"
        return

    tc = info.get("tempo_curve") or []
    rng = (max(p["bpm"] for p in tc) - min(p["bpm"] for p in tc)) if tc else 0
    with _lock:
        rec.status = DONE
        rec.completed_at = time.time()
        rec.result = {
            "bpm": sheet.get("bpm"),
            "beats_source": sheet["beats_source"],
            "beat_version": sheet["beat_version"],
            "n_beats": len(sheet.get("beats", [])),
            "n_downbeats": len(sheet.get("downbeats", [])),
            "tempo_range": round(rng, 1),
        }
    logger.info("upgrade-beats %s done in %.1fs (bpm=%s range=%.1f)",
                hash, rec.completed_at - rec.started_at, sheet.get("bpm"), rng)


def _worker_loop():
    while True:
        try:
            h = _queue.get()
        except Exception:
            continue
        try:
            _process_one(h)
        except Exception as e:
            logger.error("upgrade-beats worker crashed on %s: %s", h, e)
        finally:
            _queue.task_done()


def _ensure_worker():
    """Lazily start the worker thread on first enqueue."""
    global _worker_started
    if _worker_started:
        return
    with _lock:
        if _worker_started:
            return
        t = threading.Thread(target=_worker_loop, name="beat-upgrade-worker",
                             daemon=True)
        t.start()
        _worker_started = True
    logger.info("beat-upgrade worker thread started")
