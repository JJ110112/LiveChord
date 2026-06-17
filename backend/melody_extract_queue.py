"""Background worker for on-demand library melody extraction.

Decouples Phase 11b's expensive melody extraction (onset + pYIN dual
evidence, ~40-60s/song) from the GET /api/ai/melody request. Two problems
it solves at once:

1. The client no longer sits on a blocked connection for ~1 min.
2. More importantly, the CPU-heavy pure-Python/numpy work no longer runs
   inside a FastAPI thread-pool worker where it holds the GIL and starves
   every other request thread (the actual "前端無回應" cause after Phase
   11b made the extractor heavier).

Pattern mirrors ``beat_upgrade_queue``: single in-memory queue, daemon
worker, dict status keyed by song hash (one in-flight extraction per
song). Extraction runs in a ``ProcessPoolExecutor`` (max_workers=1,
spawned lazily) so the heavy lifting lives in a fresh interpreter with
its own GIL — same isolation the ingest melody worker
(``process_queue._melody_worker_loop``) and the BTC pipeline use.

Hash-mode (uploaded / freshly-analyzed) songs already extract melody at
ingest via ``process_queue``'s ``_melody_queue``; this module covers the
on-demand path-mode case for library songs that never went through ingest
melody extraction. The worker writes the exact same finalized cache the
old synchronous path produced (``data/melodies/<hash>.json``), so the
GET endpoint's cache-read path is unchanged.

Import-safe even when melody-extractor deps are missing — the worker just
records an error result and moves on.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Status values surfaced to the frontend
QUEUED = "queued"
RUNNING = "running"
DONE = "done"
ERROR = "error"

# Cap how many completed records we retain in memory
_MAX_RECORDS = 200

DATA_DIR = Path(__file__).parent.parent / "data"
MELODY_DIR = DATA_DIR / "melodies"


@dataclass
class _Record:
    hash: str
    audio_path: str
    path: str = ""
    status: str = QUEUED
    started_at: float = 0.0
    completed_at: float = 0.0
    error: Optional[str] = None
    note_count: int = 0
    submitted_at: float = field(default_factory=time.time)


_jobs: "dict[str, _Record]" = {}
_queue: "queue.Queue[str]" = queue.Queue()
_lock = threading.Lock()
_worker_started = False
_pool = None  # lazily-created ProcessPoolExecutor(max_workers=1)


def _evict_old_records():
    """Trim _jobs back to _MAX_RECORDS, dropping oldest finished entries."""
    if len(_jobs) <= _MAX_RECORDS:
        return
    candidates = sorted(
        ((h, r) for h, r in _jobs.items() if r.status in (DONE, ERROR)),
        key=lambda kv: kv[1].completed_at or kv[1].submitted_at,
    )
    n_to_drop = len(_jobs) - _MAX_RECORDS
    for h, _ in candidates[:n_to_drop]:
        _jobs.pop(h, None)


def enqueue(hash: str, audio_path: str, path: str = "") -> str:
    """Add an extraction job for ``hash``. Returns the new status string.

    Returns "duplicate" without re-queueing if a job for this hash is
    already queued/running. A previous DONE/ERROR record is replaced with
    a fresh QUEUED record and re-queued (lets a failed extraction retry on
    the next poll, and self-heals if the worker died). Caller is expected
    to validate ``audio_path`` existence before enqueueing.
    """
    with _lock:
        existing = _jobs.get(hash)
        if existing and existing.status in (QUEUED, RUNNING):
            return "duplicate"
        _jobs[hash] = _Record(hash=hash, audio_path=audio_path, path=path)
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
    out.pop("audio_path", None)
    return out


def _subprocess_extract(audio_path, song_hash):
    """Run inside the worker subprocess. Imports lazily so the parent (and
    spawned child) don't pay for melody-extractor deps until needed.

    Mirrors ``process_queue._melody_subprocess_extract`` — kept independent
    so the child re-imports only this lightweight module, not process_queue
    (which starts its own daemon threads at import time).
    """
    from ai.melody_extractor import MelodyExtractor
    from ai.melody_schema import melody_context_from_chord_cache
    context = melody_context_from_chord_cache(song_hash)
    return MelodyExtractor().extract_melody(
        audio_path,
        bpm=context["bpm"],
        tempo_curve=context["tempo_curve"],
        time_signature=context["time_signature"],
    )


def _process_one(hash: str):
    global _pool
    with _lock:
        rec = _jobs.get(hash)
        if rec is None:
            return
        rec.status = RUNNING
        rec.started_at = time.time()
        audio_path = rec.audio_path
        path = rec.path

    if not audio_path or not os.path.isfile(audio_path):
        with _lock:
            rec.status = ERROR
            rec.completed_at = time.time()
            rec.error = "audio file not found"
        return

    try:
        from concurrent.futures import ProcessPoolExecutor

        from ai.melody_schema import (
            atomic_write_json,
            finalize_melody_payload,
            melody_context_from_chord_cache,
        )

        if _pool is None:
            _pool = ProcessPoolExecutor(max_workers=1)
            logger.info("melody-extract ProcessPool created (max_workers=1)")

        future = _pool.submit(_subprocess_extract, audio_path, hash)
        melody = future.result(timeout=600)

        context = melody_context_from_chord_cache(hash)
        result = finalize_melody_payload(
            {"path": path, "melody": melody or []},
            path=path,
            bpm=context["bpm"],
            tempo_curve=context["tempo_curve"],
            time_signature=context["time_signature"],
        )
        MELODY_DIR.mkdir(parents=True, exist_ok=True)
        atomic_write_json(MELODY_DIR / f"{hash}.json", result)

        with _lock:
            rec.status = DONE
            rec.completed_at = time.time()
            rec.note_count = len(result.get("melody", []))
        logger.info("melody-extract %s done in %.1fs (%d notes)",
                    hash, rec.completed_at - rec.started_at, rec.note_count)
    except Exception as e:
        with _lock:
            rec.status = ERROR
            rec.completed_at = time.time()
            rec.error = f"{type(e).__name__}: {str(e)[:200]}"
        logger.error("melody-extract %s failed: %s", hash, e)


def _worker_loop():
    while True:
        try:
            h = _queue.get()
        except Exception:
            continue
        try:
            _process_one(h)
        except Exception as e:
            logger.error("melody-extract worker crashed on %s: %s", h, e)
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
        t = threading.Thread(target=_worker_loop, name="melody-extract-worker",
                             daemon=True)
        t.start()
        _worker_started = True
    logger.info("melody-extract worker thread started")
