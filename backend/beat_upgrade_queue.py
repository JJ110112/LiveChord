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
RUNNING_ANALYZING = "running:analyzing"      # madmom phase
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
    # "madmom" (default) or "beat_this". When "beat_this", _process_one
    # routes to Modal dispatch (modal_beat_this.detect_beats_via_modal) and
    # writes the result into .bak.beat_this instead of .bak.madmom.
    tracker: str = "madmom"


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
            title: str = "", requested_by: str = "",
            tracker: str = "madmom") -> str:
    """Add an upgrade job for ``hash``. Returns the new status string.

    If a job for this hash is already queued/running, returns "duplicate"
    without re-queueing. If a previous job finished (DONE/ERROR), the
    record is replaced with a fresh QUEUED record and re-queued.

    ``audio_path`` must point at an existing file — re-running an upgrade
    on an upload whose source audio was cleaned up returns ERROR. Caller
    is expected to validate file existence before enqueueing.
    """
    with _lock:
        existing = _jobs.get(hash)
        if existing and existing.status in (QUEUED, RUNNING,
                                            RUNNING_ANALYZING):
            return "duplicate"
        _jobs[hash] = _Record(
            hash=hash, audio_path=audio_path, sheet_path=sheet_path,
            title=title, requested_by=requested_by,
            tracker=tracker,
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
    """Worker handler: run the requested tracker on the queued hash and
    write the result. ``audio_path`` must point at an existing file —
    upload-mode jobs whose source audio was cleaned up should be rejected
    by the API layer before enqueueing.
    """
    from beat_snap import analyze_and_snap_dynamic, HAS_MADMOM, MADMOM_IMPORT_ERROR

    with _lock:
        rec = _jobs.get(hash)
        if rec is None:
            return
        rec.status = RUNNING
        rec.started_at = time.time()
        audio_path = rec.audio_path
        sheet_path = rec.sheet_path
        tracker = rec.tracker

    # madmom track requires a local madmom install. beat_this track only
    # requires the LIVECHORD_USE_MODAL_BEAT_THIS Modal dispatcher to be
    # configured (caller validated the env flag before enqueueing).
    if tracker == "madmom" and not HAS_MADMOM:
        with _lock:
            rec.status = ERROR
            rec.completed_at = time.time()
            rec.error = f"madmom not installed ({MADMOM_IMPORT_ERROR or 'unknown'})"
        logger.warning("upgrade-beats %s: madmom missing", hash)
        return

    temp_audio_to_cleanup = None
    if not audio_path or not os.path.isfile(audio_path):
        with _lock:
            rec.status = ERROR
            rec.completed_at = time.time()
            rec.error = "audio file not found"
        return

    try:
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

        # Backup pre-madmom state as .bak.librosa (first call wins).
        # One-time migration: legacy .bak.beats → .bak.librosa (same semantic —
        # the pre-madmom snapshot was always the librosa/ingest-default version).
        # This cache powers bidirectional /api/process/beats/switch on 8800.
        legacy_bak = sheet_path + ".bak.beats"
        librosa_bak = sheet_path + ".bak.librosa"
        if os.path.exists(legacy_bak) and not os.path.exists(librosa_bak):
            try:
                os.replace(legacy_bak, librosa_bak)
            except Exception as e:
                logger.warning("upgrade-beats %s: legacy bak migration failed: %s",
                               hash, e)
        if not os.path.exists(librosa_bak):
            try:
                with open(librosa_bak, "w", encoding="utf-8") as f:
                    json.dump(sheet, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.warning("upgrade-beats %s: librosa backup failed: %s",
                               hash, e)

        # Flip to analyzing phase so the polling client can update its toast
        with _lock:
            rec.status = RUNNING_ANALYZING

        # ---- Analysis step (tracker-specific) ----
        chords_copy = [dict(c) for c in chords]
        if tracker == "beat_this":
            try:
                from modal_beat_this import detect_beats_via_modal
                info = detect_beats_via_modal(audio_path)
            except Exception as e:
                with _lock:
                    rec.status = ERROR
                    rec.completed_at = time.time()
                    rec.error = f"beat_this Modal failed: {type(e).__name__}: {str(e)[:200]}"
                logger.error("upgrade-beats %s: beat_this Modal error: %s", hash, e)
                return
            # Modal function returns a flat dict with bpm_correction key.
            # Map to the sheet directly (chord boundaries unchanged — the
            # PC migration script doesn't snap chords for beat_this either).
            if not info.get("beats_source"):
                with _lock:
                    rec.status = ERROR
                    rec.completed_at = time.time()
                    rec.error = info.get("error") or "no beats detected"
                return
            if info.get("bpm"):
                sheet["bpm"] = round(info["bpm"], 1)
            sheet["beats"] = info.get("beats", [])
            sheet["downbeats"] = info.get("downbeats", [])
            sheet["tempo_curve"] = info.get("tempo_curve", [])
            sheet["beats_source"] = info["beats_source"]
            sheet["beat_version"] = int(sheet.get("beat_version", 0)) + 1
            if info.get("bpm_correction") is not None:
                sheet["bpm_correction"] = info["bpm_correction"]
            elif "bpm_correction" in sheet:
                sheet.pop("bpm_correction", None)
        else:
            # Run madmom (default)
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

        # Cache the new state under .bak.<tracker> for fast bidirectional
        # toggle via /beats/switch. Same convention as the PC migration script.
        bak_path = sheet_path + f".bak.{tracker}"
        try:
            with open(bak_path, "w", encoding="utf-8") as f:
                json.dump(sheet, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("upgrade-beats %s: %s cache write failed: %s",
                           hash, tracker, e)

        # Overlay beat fields onto the requesting user's personal chord version
        # if one exists. GET /api/chords serves the user version first on
        # personal-mode LAN, and ChordSheet (Pydantic) strips beat fields on
        # save — without this overlay, the UI would keep showing the pre-switch
        # beats_source even though the canonical sheet was updated.
        if rec.requested_by:
            try:
                from pathlib import Path
                user_path = (Path(__file__).parent.parent / "data" / "users"
                             / rec.requested_by / "chords" / f"{hash}.json")
                if user_path.is_file():
                    user_sheet = json.loads(user_path.read_text(encoding="utf-8"))
                    for k in ("bpm", "beats", "downbeats", "tempo_curve",
                              "beats_source", "beat_version", "bpm_correction"):
                        if k in sheet:
                            user_sheet[k] = sheet[k]
                        else:
                            user_sheet.pop(k, None)
                    tmp = str(user_path) + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(user_sheet, f, ensure_ascii=False, indent=2)
                    os.replace(tmp, str(user_path))
                    logger.info("upgrade-beats %s overlaid to user %s",
                                hash, rec.requested_by)
            except Exception as e:
                logger.warning("upgrade-beats %s: user overlay failed: %s",
                               hash, e)

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
    finally:
        # Cleanup temp YT-downloaded audio regardless of success/failure
        if temp_audio_to_cleanup and os.path.isfile(temp_audio_to_cleanup):
            try:
                os.remove(temp_audio_to_cleanup)
            except Exception as e:
                logger.warning("upgrade-beats %s: cleanup failed for %s: %s",
                               hash, temp_audio_to_cleanup, e)


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
