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
MELODIES_DIR = DATA_DIR / "melodies"
TMP_DIR = DATA_DIR / "tmp"
AUDIT_DB_PATH = DATA_DIR / "audit.db"

# Ensure directories exist
TMP_DIR.mkdir(parents=True, exist_ok=True)
CHORDS_DIR.mkdir(parents=True, exist_ok=True)
MELODIES_DIR.mkdir(parents=True, exist_ok=True)

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
    stage: str = ""           # Human-readable current step (e.g. "旋律擷取中…")
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
    if job.youtube_url:
        sheet["youtube_url"] = job.youtube_url
    out_file = CHORDS_DIR / f"{hash_val}.json"
    out_file.write_text(json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8")
    return hash_val


def _extract_cover(audio_path: str, result_hash: str):
    """Extract embedded cover art from audio file and save as JPEG."""
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(audio_path)
        if audio is None:
            return
        cover_data = None
        # FLAC
        if hasattr(audio, "pictures") and audio.pictures:
            cover_data = audio.pictures[0].data
        # MP3 (ID3)
        elif hasattr(audio, "tags") and audio.tags:
            for key in audio.tags:
                if key.startswith("APIC"):
                    cover_data = audio.tags[key].data
                    break
        # MP4/M4A
        elif hasattr(audio, "tags") and audio.tags and "covr" in audio.tags:
            cover_data = bytes(audio.tags["covr"][0])
        if cover_data and len(cover_data) > 100:
            cover_path = COVERS_DIR / f"{result_hash}.jpg"
            cover_path.write_bytes(cover_data)
            logger.info("Cover extracted for %s (%d bytes)", result_hash, len(cover_data))
    except Exception as e:
        logger.debug("Cover extraction failed for %s: %s", result_hash, e)


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
         "--max-filesize", "200m", "--no-playlist",
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

COVERS_DIR = DATA_DIR / "covers"
COVERS_DIR.mkdir(parents=True, exist_ok=True)


def _init_audit_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(AUDIT_DB_PATH, timeout=10) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
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
        # Migration: add result_hash column
        try:
            conn.execute("ALTER TABLE process_audit ADD COLUMN result_hash TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        conn.commit()


_init_audit_db()


def _write_audit(job: ProcessJob, chord_count: int = 0):
    try:
        with sqlite3.connect(AUDIT_DB_PATH, timeout=10) as conn:
            conn.execute(
                """INSERT INTO process_audit
                   (job_id, username, source_type, file_hash, youtube_url, title, status, chord_count, created_at, completed_at, result_hash)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (job.job_id, job.username, job.source_type, job.file_hash,
                 job.youtube_url, job.title, job.status.value, chord_count,
                 time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(job.created_at)),
                 time.strftime("%Y-%m-%dT%H:%M:%S"),
                 job.result_hash or "")
            )
            conn.commit()
    except Exception as e:
        logger.error("Audit write failed: %s", e)


def find_existing_result(youtube_url: str) -> dict | None:
    """Check if a YouTube URL was already processed successfully."""
    with sqlite3.connect(AUDIT_DB_PATH, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT result_hash, title, chord_count FROM process_audit "
            "WHERE youtube_url=? AND status='done' AND result_hash!='' "
            "ORDER BY id DESC LIMIT 1",
            (youtube_url,)
        ).fetchone()
    if not row:
        return None
    # Verify chord file still exists
    rh = row["result_hash"]
    if not (CHORDS_DIR / f"{rh}.json").is_file():
        return None
    return dict(row)


def write_reuse_audit(username: str, youtube_url: str, title: str,
                      result_hash: str, chord_count: int):
    """Write audit entry for a reused result (no actual processing)."""
    try:
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        with sqlite3.connect(AUDIT_DB_PATH, timeout=10) as conn:
            conn.execute(
                """INSERT INTO process_audit
                   (job_id, username, source_type, file_hash, youtube_url, title, status, chord_count, created_at, completed_at, result_hash)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (f"reuse_{int(time.time())}", username, "youtube", "",
                 youtube_url, title, "done", chord_count, now, now, result_hash)
            )
            conn.commit()
    except Exception as e:
        logger.error("Reuse audit write failed: %s", e)


def delete_audit_entries(ids: list[int]) -> int:
    """Delete audit entries by ID and clean up associated chord/cover files."""
    if not ids:
        return 0
    with sqlite3.connect(AUDIT_DB_PATH, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT id, result_hash FROM process_audit WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
        # Delete associated files
        for r in rows:
            rh = r["result_hash"] or ""
            if rh:
                for path in [
                    CHORDS_DIR / f"{rh}.json",
                    COVERS_DIR / f"{rh}.jpg",
                    MELODIES_DIR / f"{rh}.json",
                ]:
                    if path.is_file():
                        path.unlink(missing_ok=True)
        conn.execute(
            f"DELETE FROM process_audit WHERE id IN ({placeholders})",
            ids,
        )
        conn.commit()
    return len(rows)


def get_audit_log(limit: int = 50, offset: int = 0) -> list[dict]:
    with sqlite3.connect(AUDIT_DB_PATH, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM process_audit ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset)
        ).fetchall()
    import html as _html_mod
    result = []
    for r in rows:
        d = dict(r)
        if d.get("title"):
            d["title"] = _html_mod.unescape(d["title"])
        result.append(d)
    return result


def get_user_audit_log(username: str, limit: int = 20) -> list[dict]:
    """Get a specific user's process history, deduplicated by title (latest only)."""
    with sqlite3.connect(AUDIT_DB_PATH, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM process_audit WHERE username=? ORDER BY id DESC",
            (username,)
        ).fetchall()
    seen_titles = set()
    seen_hashes = set()
    seen_urls = set()
    results = []
    import html as _html_mod
    for r in rows:
        d = dict(r)
        # Unescape old rows that were stored with html.escape()
        if d.get("title"):
            d["title"] = _html_mod.unescape(d["title"])
        # Reconstruct result_hash for old rows that don't have it
        if not d.get("result_hash") and d["status"] == "done":
            d["result_hash"] = hashlib.md5(
                f"__upload/{d['job_id']}".encode("utf-8")
            ).hexdigest()[:12]
        # Deduplicate by result_hash
        rh = d.get("result_hash") or ""
        if rh and rh in seen_hashes:
            continue
        if rh:
            seen_hashes.add(rh)
        # Deduplicate by youtube_url
        yt = (d.get("youtube_url") or "").strip()
        if yt and yt in seen_urls:
            continue
        if yt:
            seen_urls.add(yt)
        # Deduplicate by title (keep most recent)
        title_key = (d.get("title") or "").strip().lower()
        if title_key and title_key in seen_titles:
            continue
        if title_key:
            seen_titles.add(title_key)
        # Enrich with chord stats if available
        rh2 = d.get("result_hash") or ""
        if rh2:
            chords_file = CHORDS_DIR / f"{rh2}.json"
            if chords_file.is_file():
                try:
                    import json as _json
                    cd = _json.loads(chords_file.read_text(encoding="utf-8"))
                    chords_list = cd.get("chords") or []
                    unique = set(c.get("chord", "") for c in chords_list if c.get("chord"))
                    d["unique_chords"] = len(unique)
                    d["chord_key"] = cd.get("key", "")
                except Exception:
                    pass
        results.append(d)
        if len(results) >= limit:
            break
    return results


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
        job.stage = "準備中…"
        audio_path = job.audio_path
        chord_count = 0

        try:
            # Step 1: If YouTube, download first
            if job.source_type == "youtube" and job.youtube_url:
                job.progress = 5
                job.stage = "讀取 YouTube 標題…"
                # Extract title before download
                title = _get_youtube_title(job.youtube_url)
                if title:
                    job.title = title
                job.progress = 10
                job.stage = "下載 YouTube 音訊…"
                out_path = str(TMP_DIR / f"{job.job_id}.wav")
                audio_path = _download_youtube(job.youtube_url, out_path)
                job.audio_path = audio_path
                job.file_hash = compute_file_hash(audio_path)
                job.progress = 30

            # Step 2: BTC chord detection
            job.progress = 40
            job.stage = "分析和弦中（BTC）…"
            from chord_detect import detect_chords_and_key_isolated
            chords, key = detect_chords_and_key_isolated(audio_path)
            job.progress = 80
            job.stage = "擷取旋律中…"

            # Step 2.5: Melody extraction (while audio file still exists)
            melody_data = None
            try:
                from ai.melody_extractor import MelodyExtractor
                ext = MelodyExtractor()
                melody_data = ext.extract_melody(audio_path)
            except Exception as mel_err:
                logger.warning("Melody extraction failed for %s: %s", job_id, mel_err)
            job.progress = 90
            job.stage = "儲存和弦與旋律資料…"

            # Step 3: Extract cover art (before audio is deleted)
            if job.source_type == "upload" and audio_path:
                _extract_cover(audio_path, "pending")  # placeholder, real hash below

            # Step 4: Save chord JSON
            result_hash = _save_chord_json(job, chords, key)
            chord_count = len(chords)
            job.result_hash = result_hash

            # Rename cover file to final hash if extracted
            if job.source_type == "upload":
                pending_cover = COVERS_DIR / "pending.jpg"
                if pending_cover.is_file():
                    pending_cover.rename(COVERS_DIR / f"{result_hash}.jpg")

            # Step 4.5: Save melody JSON
            if melody_data:
                try:
                    mel_file = MELODIES_DIR / f"{result_hash}.json"
                    mel_file.write_text(
                        json.dumps({"path": f"__upload/{job.job_id}", "melody": melody_data}, ensure_ascii=False),
                        encoding="utf-8"
                    )
                except Exception as mel_save_err:
                    logger.warning("Melody save failed: %s", mel_save_err)

            # Write audit BEFORE marking done — so history exists when frontend sees "done"
            job.progress = 98
            job.stage = "寫入紀錄…"
            _write_audit(job, chord_count)

            job.status = JobStatus.DONE
            job.progress = 100
            job.stage = "完成"
            logger.info("Job %s done: %s chords, key=%s", job_id, chord_count, key)

        except Exception as e:
            job.status = JobStatus.ERROR
            job.error_msg = str(e)[:500]
            logger.error("Job %s failed: %s", job_id, e)
            _write_audit(job, chord_count)

        finally:
            # Always clean up audio file
            if audio_path and os.path.isfile(audio_path):
                try:
                    os.remove(audio_path)
                except OSError:
                    pass


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
