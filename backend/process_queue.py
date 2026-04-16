"""Process Queue — 上傳/YouTube 新歌和弦偵測佇列

Single-threaded worker + queue.Queue，與 chord_batch.py 風格一致。
複用 chord_detect.detect_chords_and_key_isolated() 做 GPU BTC 偵測。
"""

import hashlib
import json
import logging
import os
import queue
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
CHORDS_DIR = DATA_DIR / "chords"
TMP_DIR = DATA_DIR / "tmp"
AUDIT_DB_PATH = DATA_DIR / "audit.db"

# Ensure directories exist
TMP_DIR.mkdir(parents=True, exist_ok=True)
CHORDS_DIR.mkdir(parents=True, exist_ok=True)

def _find_ytdlp() -> str:
    """Resolve yt-dlp executable path, falling back to known pip Scripts dir."""
    found = shutil.which("yt-dlp")
    if found:
        return found
    # pip install puts it here on Windows
    fallback = Path.home() / "AppData/Local/Python/pythoncore-3.14-64/Scripts/yt-dlp.exe"
    if fallback.is_file():
        return str(fallback)
    raise FileNotFoundError("yt-dlp not found on PATH or in known locations")

YTDLP_BIN = _find_ytdlp()


# ---------------------------------------------------------------------------
# Job data model
# ---------------------------------------------------------------------------

class JobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    DONE = "done"
    ERROR = "error"


@dataclass
class ProcessJob:
    job_id: str
    username: str
    source_type: str          # "upload" or "youtube"
    audio_path: str = ""      # temp file path (for upload)
    youtube_url: str = ""     # YouTube URL (for youtube)
    title: str = ""
    file_hash: str = ""       # SHA256 first 1MB, 16 hex chars
    created_at: float = field(default_factory=time.time)
    status: JobStatus = JobStatus.QUEUED
    progress: int = 0
    result_hash: Optional[str] = None
    error_msg: str = ""


# ---------------------------------------------------------------------------
# In-memory stores
# ---------------------------------------------------------------------------

_job_queue: queue.Queue = queue.Queue(maxsize=20)
_jobs: dict[str, ProcessJob] = {}
_jobs_lock = threading.Lock()

# Per-user daily quota
_quota_store: dict[str, list[float]] = defaultdict(list)
_QUOTA_MAX = 10
_QUOTA_WINDOW = 86400  # 24 hours


# ---------------------------------------------------------------------------
# Quota
# ---------------------------------------------------------------------------

def check_quota(username: str) -> bool:
    """Return True if user is within daily quota."""
    now = time.time()
    stamps = _quota_store[username]
    _quota_store[username] = [t for t in stamps if now - t < _QUOTA_WINDOW]
    return len(_quota_store[username]) < _QUOTA_MAX


def consume_quota(username: str):
    _quota_store[username].append(time.time())


def get_user_daily_count(username: str) -> int:
    now = time.time()
    return len([t for t in _quota_store[username] if now - t < _QUOTA_WINDOW])


# ---------------------------------------------------------------------------
# Job management
# ---------------------------------------------------------------------------

def submit_job(job: ProcessJob) -> ProcessJob:
    """Submit a job to the queue. Raises queue.Full if overloaded."""
    with _jobs_lock:
        _jobs[job.job_id] = job
    _job_queue.put(job.job_id, block=False)
    consume_quota(job.username)
    return job


def get_job(job_id: str) -> Optional[ProcessJob]:
    with _jobs_lock:
        return _jobs.get(job_id)


def generate_job_id() -> str:
    return uuid.uuid4().hex[:16]


def compute_file_hash(filepath: str) -> str:
    """SHA256 of first 1MB, truncated to 16 hex chars."""
    h = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            h.update(f.read(1024 * 1024))
    except Exception:
        return ""
    return h.hexdigest()[:16]


# ---------------------------------------------------------------------------
# Chord JSON saving (matches existing format)
# ---------------------------------------------------------------------------

def _save_chord_json(job: ProcessJob, chords: list, key: str) -> str:
    """Save chord JSON in the same format as chord_batch.py. Returns song hash."""
    # For uploaded songs, use a virtual path so they don't collide with library
    virtual_path = f"__upload/{job.job_id}"
    hash_val = hashlib.md5(virtual_path.encode("utf-8")).hexdigest()[:12]

    sheet = {
        "path": virtual_path,
        "key": key,
        "capo": 0,
        "source": f"btc_{job.source_type}",
        "title": job.title,
        "chords": chords,
    }
    out_file = CHORDS_DIR / f"{hash_val}.json"
    out_file.write_text(json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8")
    return hash_val


# ---------------------------------------------------------------------------
# YouTube download
# ---------------------------------------------------------------------------

def _get_youtube_title(url: str) -> str:
    """Extract video title from YouTube URL using yt-dlp."""
    try:
        result = subprocess.run(
            [YTDLP_BIN, "--get-title", "--no-download", url],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return ""


def _download_youtube(url: str, output_path: str) -> str:
    """Download audio from YouTube URL using yt-dlp. Returns path to wav file."""
    result = subprocess.run(
        [YTDLP_BIN, "-x", "--audio-format", "wav", "--audio-quality", "0",
         "--max-filesize", "50m", "--no-playlist",
         "-o", output_path, url],
        capture_output=True, text=True, timeout=180
    )
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed: {result.stderr[:300]}")
    # yt-dlp may append .wav if output_path doesn't have it
    if os.path.isfile(output_path):
        return output_path
    wav_path = output_path.rsplit(".", 1)[0] + ".wav"
    if os.path.isfile(wav_path):
        return wav_path
    # Search for any file with the job uuid prefix in tmp
    base = os.path.basename(output_path).rsplit(".", 1)[0]
    for f in TMP_DIR.iterdir():
        if f.name.startswith(base):
            return str(f)
    raise RuntimeError(f"Downloaded file not found for {output_path}")


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

def _init_audit_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(AUDIT_DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS process_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                username TEXT NOT NULL,
                source_type TEXT NOT NULL,
                file_hash TEXT DEFAULT '',
                youtube_url TEXT DEFAULT '',
                title TEXT DEFAULT '',
                status TEXT NOT NULL,
                chord_count INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                completed_at TEXT DEFAULT ''
            )
        """)
        conn.commit()


_init_audit_db()


def _write_audit(job: ProcessJob, chord_count: int = 0):
    try:
        with sqlite3.connect(AUDIT_DB_PATH) as conn:
            conn.execute(
                """INSERT INTO process_audit
                   (job_id, username, source_type, file_hash, youtube_url, title, status, chord_count, created_at, completed_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (job.job_id, job.username, job.source_type, job.file_hash,
                 job.youtube_url, job.title, job.status.value, chord_count,
                 time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(job.created_at)),
                 time.strftime("%Y-%m-%dT%H:%M:%S"))
            )
            conn.commit()
    except Exception as e:
        logger.error("Audit write failed: %s", e)


def get_audit_log(limit: int = 50, offset: int = 0) -> list[dict]:
    with sqlite3.connect(AUDIT_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM process_audit ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset)
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Worker thread
# ---------------------------------------------------------------------------

def _worker_loop():
    """Single-threaded worker: process one job at a time."""
    while True:
        try:
            job_id = _job_queue.get(timeout=5)
        except queue.Empty:
            continue

        job = get_job(job_id)
        if not job:
            continue

        job.status = JobStatus.PROCESSING
        job.progress = 10
        audio_path = job.audio_path
        chord_count = 0

        try:
            # Step 1: If YouTube, download first
            if job.source_type == "youtube" and job.youtube_url:
                job.progress = 5
                # Extract title before download
                title = _get_youtube_title(job.youtube_url)
                if title:
                    import html as html_mod
                    job.title = html_mod.escape(title)
                job.progress = 10
                out_path = str(TMP_DIR / f"{job.job_id}.wav")
                audio_path = _download_youtube(job.youtube_url, out_path)
                job.audio_path = audio_path
                job.file_hash = compute_file_hash(audio_path)
                job.progress = 30

            # Step 2: BTC chord detection
            job.progress = 40
            from chord_detect import detect_chords_and_key_isolated
            chords, key = detect_chords_and_key_isolated(audio_path)
            job.progress = 90

            # Step 3: Save chord JSON
            result_hash = _save_chord_json(job, chords, key)
            chord_count = len(chords)
            job.result_hash = result_hash
            job.status = JobStatus.DONE
            job.progress = 100
            logger.info("Job %s done: %s chords, key=%s", job_id, chord_count, key)

        except Exception as e:
            job.status = JobStatus.ERROR
            job.error_msg = str(e)[:500]
            logger.error("Job %s failed: %s", job_id, e)

        finally:
            # Always clean up audio file
            if audio_path and os.path.isfile(audio_path):
                try:
                    os.remove(audio_path)
                except OSError:
                    pass
            # Write audit log
            _write_audit(job, chord_count)


# ---------------------------------------------------------------------------
# Cleanup thread — sweep orphaned tmp files every 5 minutes
# ---------------------------------------------------------------------------

_CLEANUP_INTERVAL = 300   # 5 minutes
_MAX_TMP_AGE = 600        # 10 minutes


def _cleanup_loop():
    while True:
        time.sleep(_CLEANUP_INTERVAL)
        try:
            now = time.time()
            for f in TMP_DIR.iterdir():
                if f.is_file() and (now - f.stat().st_mtime) > _MAX_TMP_AGE:
                    try:
                        f.unlink()
                        logger.info("Cleanup: removed %s", f.name)
                    except OSError:
                        pass
        except Exception as e:
            logger.error("Cleanup error: %s", e)


# ---------------------------------------------------------------------------
# Stale job eviction (1-hour TTL)
# ---------------------------------------------------------------------------

_JOB_TTL = 3600  # 1 hour


def _evict_loop():
    while True:
        time.sleep(600)
        try:
            now = time.time()
            with _jobs_lock:
                stale = [jid for jid, j in _jobs.items()
                         if j.status in (JobStatus.DONE, JobStatus.ERROR)
                         and (now - j.created_at) > _JOB_TTL]
                for jid in stale:
                    del _jobs[jid]
        except Exception as e:
            logger.error("Evict error: %s", e)


# ---------------------------------------------------------------------------
# Start daemon threads
# ---------------------------------------------------------------------------

_worker_thread = threading.Thread(target=_worker_loop, daemon=True, name="process-worker")
_cleanup_thread = threading.Thread(target=_cleanup_loop, daemon=True, name="tmp-cleanup")
_evict_thread = threading.Thread(target=_evict_loop, daemon=True, name="job-evict")

_worker_thread.start()
_cleanup_thread.start()
_evict_thread.start()
