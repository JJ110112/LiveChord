"""Background worker for library tuning correction (scan + fix + revert).

Pattern mirrors beat_upgrade_queue: in-memory records, one daemon worker,
CPU-heavy steps (librosa analysis, soxr resample, beat tracking) inside a
lazily spawned ProcessPoolExecutor so the FastAPI threads keep their GIL.

Fix job for one library track:
  1. analyze (ai.tuning_detect.analyze_tuning) — must be ``fixable``
  2. skip when the song carries human data (user chord edits, human
     sections / feedback, chord_corrections rows) unless ``force``
  3. rewrite the source audio in place, original kept as ``<file>.bak``
     (the NAS file is shared by other players, so the source is fixed)
  4. move the chord JSON + every per-hash derived cache (melody, stems,
     accompaniment, ...) into data/tuning_pretune/<hash>/ — they were
     computed on the off-pitch audio. Revert moves them back.
  5. re-run BTC + beat tracking, write a fresh chord JSON stamped with
     ``tuning_correction``

song_hash is path-only, so ratings / recents / favourites keep working.

Scan job: analyze every library track under a path prefix, write
data/tuning_scan.json (rows sorted by |offset|). Batch fix enqueues one
fix job per fixable row.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import queue
import shutil
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
PRETUNE_DIR = DATA_DIR / "tuning_pretune"
SCAN_REPORT = DATA_DIR / "tuning_scan.json"

# Per-hash derived data computed from the audio signal. Human data dirs
# (human_sections, human_feedback, melody_reviews) are NOT here.
_DERIVED_DIRS = (
    "accompaniments", "melodies", "melodies_v2", "melodies_rh_v2",
    "hybrid_melody", "hybrid_bass", "polyphonic_notes",
    "melody_candidates", "stems", "separated",
)

QUEUED, RUNNING, DONE, ERROR, SKIPPED = "queued", "running", "done", "error", "skipped"
_MAX_RECORDS = 500


@dataclass
class _Record:
    id: str
    kind: str                      # "fix" | "scan"
    path: str = ""                 # library path (fix) or prefix (scan)
    force: bool = False
    status: str = QUEUED
    step: str = ""
    progress: int = 0
    total: int = 0
    error: Optional[str] = None
    result: Optional[dict] = None
    submitted_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    completed_at: float = 0.0


_jobs: dict[str, _Record] = {}
_queue: "queue.Queue[str]" = queue.Queue()
_lock = threading.Lock()
_worker_started = False
_pool: Optional[ProcessPoolExecutor] = None


def _get_pool() -> ProcessPoolExecutor:
    global _pool
    if _pool is None:
        _pool = ProcessPoolExecutor(max_workers=2)
    return _pool


# ---------------------------------------------------------------- subprocess

def _sub_analyze(full: str) -> dict:
    from ai.tuning_detect import analyze_tuning
    return analyze_tuning(full)


def _sub_correct(full: str, cents: float) -> dict:
    from ai.tuning_detect import correct_file, verify_corrected
    res = correct_file(full, cents)
    res["residual_cents"] = verify_corrected(full)
    return res


def _sub_beats(full: str, chords: list) -> dict:
    from beat_snap import analyze_and_snap_dynamic
    return analyze_and_snap_dynamic(full, chords)


# ---------------------------------------------------------------- helpers

def human_data_reasons(h: str) -> list[str]:
    """Why a song must not be auto-reprocessed (empty list = safe)."""
    reasons = []
    if (DATA_DIR / "human_sections" / f"{h}.json").is_file():
        reasons.append("human_sections")
    if glob.glob(str(DATA_DIR / "human_feedback" / f"{h}*")):
        reasons.append("human_feedback")
    users = DATA_DIR / "users"
    if users.is_dir():
        for u in users.iterdir():
            for sub, tag in (("chords", "user_chords"),
                             ("human_sections", "human_sections"),
                             ("human_feedback", "human_feedback")):
                if glob.glob(str(u / sub / f"{h}*")):
                    reasons.append(f"{tag}:{u.name}")
    db = DATA_DIR / "feedback.db"
    if db.is_file():
        try:
            with sqlite3.connect(str(db), timeout=10) as conn:
                n = conn.execute("SELECT COUNT(*) FROM chord_corrections WHERE song_hash=?",
                                 (h,)).fetchone()[0]
            if n:
                reasons.append(f"chord_corrections:{n}")
        except sqlite3.Error:
            pass
    return sorted(set(reasons))


def _derived_files(h: str) -> list[Path]:
    found = []
    for d in _DERIVED_DIRS:
        root = DATA_DIR / d
        if not root.is_dir():
            continue
        for pat in (f"{h}*", f"{h[:2]}/{h}*", f"*/{h}*"):
            found += [Path(p) for p in glob.glob(str(root / pat))]
    return sorted(set(found))


def _stash_derived(h: str) -> int:
    """Move chord JSON + derived caches into PRETUNE_DIR/<h>/ (revert restores)."""
    from chord_cache import chord_file_for
    dest = PRETUNE_DIR / h
    if dest.exists():
        shutil.rmtree(dest)  # leftover from an earlier fix that was reverted
    n = 0
    chords = chord_file_for(h)
    if chords.is_file():
        (dest / "chords").mkdir(parents=True, exist_ok=True)
        shutil.move(str(chords), str(dest / "chords" / chords.name))
        n += 1
    for f in _derived_files(h):
        rel = f.relative_to(DATA_DIR)
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(f), str(dest / rel))
        n += 1
    return n


def _unstash_derived(h: str) -> int:
    from chord_cache import chord_file_for
    src = PRETUNE_DIR / h
    if not src.is_dir():
        return 0
    # the post-fix data was computed on corrected audio — drop it first
    cf = chord_file_for(h)
    if cf.is_file():
        cf.unlink()
    for f in _derived_files(h):
        shutil.rmtree(f) if f.is_dir() else f.unlink()
    n = 0
    for f in src.rglob("*"):
        if f.is_dir():
            continue
        rel = f.relative_to(src)
        target = cf if rel.parts[0] == "chords" else DATA_DIR / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(f), str(target))
        n += 1
    shutil.rmtree(src, ignore_errors=True)
    return n


def _redetect(path: str, full: str, tuning_meta: dict) -> dict:
    """BTC + beat tracking, same fields as /api/chords/detect."""
    from chord_cache import chord_file_for, ensure_chord_bucket, song_hash, update_entry_from_file
    from chord_detect import detect_chords_and_key_isolated
    chords, key = detect_chords_and_key_isolated(full)
    try:
        beat_info = _get_pool().submit(_sub_beats, full, chords).result()
    except Exception as e:
        logger.warning("tuning-fix %s: beat tracking failed: %s", path, e)
        beat_info = {}
    sheet = {"path": path, "key": key, "capo": 0, "source": "btc", "chords": chords,
             "tuning_correction": tuning_meta}
    if beat_info.get("bpm"):
        sheet["bpm"] = round(float(beat_info["bpm"]), 1)
    if beat_info.get("beats_source"):
        for k in ("beats", "downbeats", "tempo_curve"):
            sheet[k] = beat_info.get(k, [])
        sheet["beats_source"] = beat_info["beats_source"]
        sheet["beat_version"] = beat_info.get("beat_version", 0)
    h = song_hash(path)
    ensure_chord_bucket(h)
    cf = chord_file_for(h)
    tmp = str(cf) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(sheet, f, ensure_ascii=False, indent=2)
    os.replace(tmp, str(cf))
    update_entry_from_file(path)
    return {"key": key, "chord_count": len(chords), "bpm": sheet.get("bpm")}


# ---------------------------------------------------------------- jobs

def _run_fix(rec: _Record):
    from chord_cache import song_hash
    from config import resolve_path
    full = resolve_path(rec.path)
    h = song_hash(rec.path)
    if not os.path.isfile(full):
        raise FileNotFoundError(f"audio not found: {full}")
    if os.path.exists(full + ".bak"):
        rec.status, rec.result = SKIPPED, {"reason": "already_corrected"}
        return
    rec.step = "analyzing"
    a = _get_pool().submit(_sub_analyze, full).result()
    if not a["fixable"]:
        rec.status, rec.result = SKIPPED, {"reason": a["reason"], "analysis": a}
        return
    human = human_data_reasons(h)
    if human and not rec.force:
        rec.status, rec.result = SKIPPED, {"reason": "human_data", "human": human, "analysis": a}
        return
    rec.step = "correcting"
    c = _get_pool().submit(_sub_correct, full, a["offset_cents"]).result()
    rec.step = "stashing"
    stashed = _stash_derived(h)
    meta = {"applied": True, "cents": a["offset_cents"], "spread": a["spread_cents"],
            "residual_cents": c.get("residual_cents"), "method": "resample_pitch_and_speed",
            "factor": round(c["factor"], 6), "backup": os.path.basename(c["backup"]),
            "duration_before": c["duration_before"], "duration_after": c["duration_after"],
            "human_data_overridden": human, "at": int(time.time())}
    rec.step = "redetecting"
    det = _redetect(rec.path, full, meta)
    rec.result = {"analysis": a, "correction": meta, "stashed_files": stashed, "chords": det}
    logger.info("tuning-fix %s: %+.1f cents, residual %s, %d caches stashed",
                rec.path, a["offset_cents"], c.get("residual_cents"), stashed)


def _run_scan(rec: _Record):
    from config import resolve_path
    from data_cache import get_library_tracks
    tracks = [t for t in get_library_tracks()
              if str(t.get("path", "")).startswith(rec.path)]
    rec.total = len(tracks)
    rows = []
    pool = _get_pool()
    futs = []
    for t in tracks:
        full = resolve_path(t["path"])
        futs.append((t, full, pool.submit(_sub_analyze, full)))
    for t, full, fut in futs:
        row = {"path": t["path"], "title": t.get("title", ""), "artist": t.get("artist", ""),
               "corrected": os.path.exists(full + ".bak")}
        try:
            row.update(fut.result())
        except Exception as e:
            row.update({"fixable": False, "reason": f"error: {type(e).__name__}: {e}"})
        rows.append(row)
        rec.progress += 1
        if rec.progress % 100 == 0:
            _write_report(rec, rows, partial=True)
    _write_report(rec, rows, partial=False)
    rec.result = {"total": len(rows), "fixable": sum(1 for r in rows if r.get("fixable")),
                  "report": str(SCAN_REPORT)}


def _write_report(rec: _Record, rows: list, partial: bool):
    rows = sorted(rows, key=lambda r: -abs(r.get("offset_cents") or 0))
    data = {"prefix": rec.path, "partial": partial, "scanned": len(rows),
            "total": rec.total, "at": int(time.time()), "rows": rows}
    tmp = str(SCAN_REPORT) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, str(SCAN_REPORT))


def read_report() -> Optional[dict]:
    if not SCAN_REPORT.is_file():
        return None
    return json.loads(SCAN_REPORT.read_text(encoding="utf-8"))


def revert(path: str) -> dict:
    """Restore the original audio and the pre-fix chord JSON / caches (fast)."""
    from ai.tuning_detect import revert_file
    from chord_cache import song_hash, update_entry_from_file
    from config import resolve_path
    full = resolve_path(path)
    revert_file(full)
    n = _unstash_derived(song_hash(path))
    try:
        update_entry_from_file(path)
    except Exception:
        pass
    return {"ok": True, "path": path, "restored_files": n}


# ---------------------------------------------------------------- queue API

def _evict():
    if len(_jobs) <= _MAX_RECORDS:
        return
    old = sorted((r for r in _jobs.values() if r.status not in (QUEUED, RUNNING)),
                 key=lambda r: r.completed_at or r.submitted_at)
    for r in old[:len(_jobs) - _MAX_RECORDS]:
        _jobs.pop(r.id, None)


def enqueue(kind: str, path: str, force: bool = False) -> dict:
    with _lock:
        for r in _jobs.values():
            if r.kind == kind and r.path == path and r.status in (QUEUED, RUNNING):
                return {"id": r.id, "status": "duplicate"}
        rec = _Record(id=uuid.uuid4().hex[:10], kind=kind, path=path, force=force)
        _jobs[rec.id] = rec
        _evict()
    _queue.put(rec.id)
    _ensure_worker()
    return {"id": rec.id, "status": QUEUED}


def get_status(job_id: Optional[str] = None) -> dict:
    with _lock:
        if job_id:
            r = _jobs.get(job_id)
            return asdict(r) if r else {}
        recs = sorted(_jobs.values(), key=lambda r: -r.submitted_at)
        return {"jobs": [asdict(r) for r in recs[:100]],
                "pending": sum(1 for r in recs if r.status in (QUEUED, RUNNING))}


def _worker():
    while True:
        jid = _queue.get()
        rec = _jobs.get(jid)
        if rec is None:
            continue
        rec.status, rec.started_at = RUNNING, time.time()
        try:
            (_run_scan if rec.kind == "scan" else _run_fix)(rec)
            if rec.status == RUNNING:
                rec.status = DONE
        except Exception as e:
            logger.exception("tuning job %s (%s %s) failed", jid, rec.kind, rec.path)
            rec.status, rec.error = ERROR, f"{type(e).__name__}: {e}"
        rec.step = ""
        rec.completed_at = time.time()


def _ensure_worker():
    global _worker_started
    with _lock:
        if _worker_started:
            return
        threading.Thread(target=_worker, name="tuning-fix", daemon=True).start()
        _worker_started = True
