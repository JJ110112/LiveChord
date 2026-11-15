"""Background worker for player-triggered chord detection (POST /api/chords/detect).

The old endpoint awaited the whole job inside the request: BTC (already in a
subprocess), then — when BTC found nothing — a melody-to-chord fallback and
beat tracking on ``asyncio.to_thread``. Those last two are pure
Python/numpy/librosa and held the uvicorn GIL for ~20-30 s, so every other
request on the NUC (the next song's page, its stream, other users) stalled
too. Measured 2026-11-15: a trivial /api/config/public took 20 s while one
EDM track was re-detected.

Pattern mirrors ``melody_extract_queue``: in-memory records keyed by song
hash, one daemon worker, all CPU-heavy work in a lazily spawned
``ProcessPoolExecutor`` so the FastAPI threads keep their GIL. The worker
writes the same chord JSON the synchronous endpoint wrote (atomically).
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

QUEUED, RUNNING, DONE, ERROR = "queued", "running", "done", "error"
_MAX_RECORDS = 200


@dataclass
class _Record:
    hash: str
    path: str
    audio_path: str
    status: str = QUEUED
    submitted_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    completed_at: float = 0.0
    error: Optional[str] = None
    result: Optional[dict] = None


_jobs: "dict[str, _Record]" = {}
_queue: "queue.Queue[str]" = queue.Queue()
_lock = threading.Lock()
_worker_started = False
_pool = None


def _evict_old_records():
    if len(_jobs) <= _MAX_RECORDS:
        return
    done = sorted(
        ((h, r) for h, r in _jobs.items() if r.status in (DONE, ERROR)),
        key=lambda kv: kv[1].completed_at or kv[1].submitted_at,
    )
    for h, _ in done[: len(_jobs) - _MAX_RECORDS]:
        _jobs.pop(h, None)


def enqueue(hash: str, path: str, audio_path: str) -> str:
    """Queue detection for ``hash``. Returns ``queued`` or ``duplicate`` when
    a job for the same song is already queued / running. Caller validates
    that ``audio_path`` exists."""
    with _lock:
        existing = _jobs.get(hash)
        if existing and existing.status in (QUEUED, RUNNING):
            return "duplicate"
        _jobs[hash] = _Record(hash=hash, path=path, audio_path=audio_path)
        _evict_old_records()
    _queue.put(hash)
    _ensure_worker()
    return QUEUED


def get_status(hash: str) -> Optional[dict]:
    with _lock:
        rec = _jobs.get(hash)
        if rec is None:
            return None
        out = asdict(rec)
    out.pop("audio_path", None)
    return out


def _sub_fallback_and_beats(audio_path: str, chords: list, key: str, chords_dir: str):
    """Runs in the worker subprocess: melody-to-chord fallback when BTC found
    nothing (e.g. 8-bit chiptune), then beat tracking."""
    chords = list(chords or [])
    if not chords:
        from ai.melody_extractor import MelodyExtractor
        from ai.hmm import get_viterbi_decoder
        from ai.markov import get_predictor

        melody_events = MelodyExtractor().extract_melody(audio_path)
        if melody_events:
            midi_sequence = [evt["midi"] for evt in melody_events]
            path_degrees, _ = get_viterbi_decoder(chords_dir).decode(midi_sequence, top_k=20)
            predictor = get_predictor(chords_dir)
            current = None
            for i, evt in enumerate(melody_events):
                name = predictor.degree_to_chord(path_degrees[i], key)
                if name != current:
                    chords.append({"time": evt["start"], "end": evt["end"], "chord": name})
                    current = name
                else:
                    chords[-1]["end"] = evt["end"]
    try:
        from beat_snap import analyze_and_snap_dynamic
        beat_info = analyze_and_snap_dynamic(audio_path, chords) or {}
    except Exception:
        beat_info = {}
    return chords, beat_info


def _get_pool():
    global _pool
    if _pool is None:
        from concurrent.futures import ProcessPoolExecutor
        _pool = ProcessPoolExecutor(max_workers=1)
        logger.info("chord-detect ProcessPool created (max_workers=1)")
    return _pool


def _run(rec: _Record) -> dict:
    from chord_cache import (
        CHORDS_DIR, chord_file_for, ensure_chord_bucket, update_entry_from_file,
    )
    from chord_detect import detect_chords_and_key_isolated

    chords, key = detect_chords_and_key_isolated(rec.audio_path)
    chords, beat_info = _get_pool().submit(
        _sub_fallback_and_beats, rec.audio_path, chords, key, str(CHORDS_DIR),
    ).result(timeout=900)

    sheet = {"path": rec.path, "key": key, "capo": 0, "source": "btc", "chords": chords}
    if beat_info.get("bpm"):
        sheet["bpm"] = round(float(beat_info["bpm"]), 1)
    if beat_info.get("beats_source"):
        for k in ("beats", "downbeats", "tempo_curve"):
            sheet[k] = beat_info.get(k, [])
        sheet["beats_source"] = beat_info["beats_source"]
        sheet["beat_version"] = beat_info.get("beat_version", 0)

    ensure_chord_bucket(rec.hash)
    cf = chord_file_for(rec.hash)
    tmp = str(cf) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(sheet, f, ensure_ascii=False, indent=2)
    os.replace(tmp, str(cf))
    update_entry_from_file(rec.path)
    return {
        "ok": True,
        "path": rec.path,
        "key": key,
        "chord_count": len(chords),
        "source": "btc",
        "beats_source": sheet.get("beats_source"),
        "bpm": sheet.get("bpm"),
    }


def _process_one(hash: str):
    with _lock:
        rec = _jobs.get(hash)
        if rec is None:
            return
        rec.status = RUNNING
        rec.started_at = time.time()
    try:
        result = _run(rec)
        with _lock:
            rec.result = result
            rec.status = DONE  # only after the atomic write landed
            rec.completed_at = time.time()
        logger.info("chord-detect %s done in %.1fs (%d chords)",
                    rec.path, rec.completed_at - rec.started_at, result["chord_count"])
    except Exception as e:
        with _lock:
            rec.status = ERROR
            rec.error = f"{type(e).__name__}: {str(e)[:200]}"
            rec.completed_at = time.time()
        logger.error("chord-detect %s failed: %s", rec.path, e)


def _worker_loop():
    while True:
        h = _queue.get()
        try:
            _process_one(h)
        except Exception as e:
            logger.error("chord-detect worker crashed on %s: %s", h, e)
        finally:
            _queue.task_done()


def _ensure_worker():
    global _worker_started
    if _worker_started:
        return
    with _lock:
        if _worker_started:
            return
        threading.Thread(target=_worker_loop, name="chord-detect-worker", daemon=True).start()
        _worker_started = True
