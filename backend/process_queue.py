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
# Separate queue for post-DONE melody extraction. Decouples ~40s CPU work from the
# chord worker so the next queued job starts immediately after chord save.
# Items: (job_id, audio_path, result_hash). Melody worker owns audio file cleanup.
_melody_queue: queue.Queue = queue.Queue(maxsize=40)
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

def _probe_audio_duration(audio_path: str) -> float:
    """Return the audio file's total duration in seconds, or 0 on failure.

    Used to populate the chord JSON's `duration` field so the player's
    YT-desync check has a trustworthy reference (last-chord-end is a bad
    proxy — outros with no detected chord make the song look shorter).
    """
    try:
        from mutagen import File as MutagenFile
        af = MutagenFile(audio_path)
        if af is not None and hasattr(af, "info") and hasattr(af.info, "length"):
            return float(af.info.length or 0)
    except Exception as e:
        logger.debug("Duration probe failed for %s: %s", audio_path, e)
    return 0.0


def _save_chord_json(job: ProcessJob, chords: list, key: str,
                     audio_path: str = "") -> str:
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
    # Embed true audio duration when we still have the file on disk — the
    # player's desync check reads this, and falling back to last-chord-end
    # was triggering false positives on songs with outro silence.
    if audio_path:
        d = _probe_audio_duration(audio_path)
        if d > 0:
            sheet["duration"] = round(d, 3)
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
    """Extract video title from YouTube URL using yt-dlp.

    Uses --dump-json + explicit UTF-8 decoding so CJK titles survive on
    Windows NUCs whose console code page is not UTF-8 (cp950/cp932 etc.).
    """
    try:
        result = subprocess.run(
            [YTDLP_BIN, "--dump-json", "--no-download", "--no-playlist", url],
            capture_output=True, timeout=30,
            encoding="utf-8", errors="replace",
        )
        if result.returncode == 0 and result.stdout.strip():
            first = result.stdout.strip().splitlines()[0]
            return (json.loads(first).get("title") or "").strip()
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
        # Task 2: YouTube URL → library hash map. Lets beta users reuse NAS-analyzed
        # chords when they drop the same song's YouTube URL into process, instead of
        # re-downloading and re-analyzing. Populated by front-end auto-learn after
        # strict duration match (Δ/D < 5%) or manually by admin.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS youtube_library_map (
                youtube_url TEXT PRIMARY KEY,
                library_hash TEXT NOT NULL,
                mapped_by TEXT NOT NULL,
                ts DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ylm_hash ON youtube_library_map(library_hash)")
        conn.commit()


_init_audit_db()


def _write_audit(job: ProcessJob, chord_count: int = 0, status: Optional[str] = None):
    """Persist the job outcome to audit DB.

    ``status`` overrides ``job.status.value`` — used by the success branch
    to record "done" before flipping the in-memory job status, avoiding a
    race where my-history queries hit before the status change.
    """
    final_status = status if status is not None else job.status.value
    try:
        with sqlite3.connect(AUDIT_DB_PATH, timeout=10) as conn:
            conn.execute(
                """INSERT INTO process_audit
                   (job_id, username, source_type, file_hash, youtube_url, title, status, chord_count, created_at, completed_at, result_hash)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (job.job_id, job.username, job.source_type, job.file_hash,
                 job.youtube_url, job.title, final_status, chord_count,
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


def find_library_mapping(youtube_url: str) -> dict | None:
    """Task 2: look up a YouTube URL → NAS-library hash mapping.

    Self-heals stale rows: if the mapped chord JSON no longer exists (admin
    deleted it), the map entry is removed and None is returned.
    """
    if not youtube_url:
        return None
    with sqlite3.connect(AUDIT_DB_PATH, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT library_hash, mapped_by, ts FROM youtube_library_map WHERE youtube_url=?",
            (youtube_url,),
        ).fetchone()
        if not row:
            return None
        lh = row["library_hash"]
        if not (CHORDS_DIR / f"{lh}.json").is_file():
            # Stale — auto-purge.
            conn.execute("DELETE FROM youtube_library_map WHERE youtube_url=?", (youtube_url,))
            conn.commit()
            return None
        return dict(row)


def upsert_library_mapping(youtube_url: str, library_hash: str, mapped_by: str) -> bool:
    """Task 2: record a YT URL → library hash mapping. Returns True if newly inserted."""
    if not youtube_url or not library_hash:
        return False
    if not (CHORDS_DIR / f"{library_hash}.json").is_file():
        return False
    try:
        with sqlite3.connect(AUDIT_DB_PATH, timeout=10) as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO youtube_library_map(youtube_url, library_hash, mapped_by) "
                "VALUES (?,?,?)",
                (youtube_url, library_hash, mapped_by or "auto"),
            )
            conn.commit()
            return cur.rowcount > 0
    except Exception as e:
        logger.error("upsert_library_mapping failed: %s", e)
        return False


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


def _purge_user_hash_refs(hashes: set[str]) -> None:
    """Remove __hash/<h> entries from every user's recent.json AND favorites.json
    when the backing audit row + chord data are being deleted. Prevents dangling
    cards on the home page / favorites list that would navigate to a 404 player."""
    if not hashes:
        return
    targets = {f"__hash/{h}" for h in hashes}
    users_root = DATA_DIR / "users"
    if not users_root.is_dir():
        return
    # (filename, top-level key) — both follow the same shape {key: [{path, ...}, ...]}
    for fname, list_key in (("recent.json", "recent"), ("favorites.json", "favorites")):
        for user_dir in users_root.iterdir():
            if not user_dir.is_dir():
                continue
            f = user_dir / fname
            if not f.is_file():
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            orig = data.get(list_key, [])
            cleaned = [r for r in orig if r.get("path") not in targets]
            if len(cleaned) != len(orig):
                data[list_key] = cleaned
                try:
                    f.write_text(
                        json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                except OSError:
                    pass


# Backward-compat shim: some older imports may still reach for the old name
_purge_recent_entries = _purge_user_hash_refs


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
        deleted_hashes: set[str] = set()
        # Delete associated files
        for r in rows:
            rh = r["result_hash"] or ""
            if rh:
                deleted_hashes.add(rh)
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
    # Cascade: prune dangling __hash/<h> entries from every user's recent.json + favorites.json
    _purge_user_hash_refs(deleted_hashes)
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
            # Step 1: If YouTube, download first. Progress must be monotonic so the
            # UI bar never goes backwards (frontend uses Math.max clamp but initial
            # value there starts at 0).
            if job.source_type == "youtube" and job.youtube_url:
                job.progress = 15
                job.stage = "讀取 YouTube 標題…"
                # Extract title before download
                title = _get_youtube_title(job.youtube_url)
                if title:
                    job.title = title
                job.progress = 20
                job.stage = "下載 YouTube 音訊…"
                out_path = str(TMP_DIR / f"{job.job_id}.wav")
                audio_path = _download_youtube(job.youtube_url, out_path)
                job.audio_path = audio_path
                job.file_hash = compute_file_hash(audio_path)
                job.progress = 35

            # Step 2: BTC chord detection
            job.progress = 40
            job.stage = "分析和弦中（BTC）…"
            from chord_detect import detect_chords_and_key_isolated
            chords, key = detect_chords_and_key_isolated(audio_path)
            job.progress = 75
            job.stage = "儲存和弦資料…"

            # Step 3: Extract cover art (before audio is deleted)
            if job.source_type == "upload" and audio_path:
                _extract_cover(audio_path, "pending")  # placeholder, real hash below

            # Step 4: Save chord JSON (+ real audio duration so YT desync check
            # has a trustworthy reference) → result_hash available for reuse lookups
            result_hash = _save_chord_json(job, chords, key, audio_path)
            chord_count = len(chords)
            job.result_hash = result_hash

            # Rename cover file to final hash if extracted
            if job.source_type == "upload":
                pending_cover = COVERS_DIR / "pending.jpg"
                if pending_cover.is_file():
                    pending_cover.rename(COVERS_DIR / f"{result_hash}.jpg")

            # Write audit + flip status to DONE NOW so the frontend can navigate to
            # the player page immediately. Melody extraction runs afterwards below
            # (takes ~40s on CPU) — user reads chords while melody renders in the
            # background. Once melody JSON lands, a page reload picks it up for the
            # waterfall view. We accept that the worker thread stays busy past DONE;
            # subsequent queued jobs wait, which is fine for beta-scale load.
            job.progress = 90
            job.stage = "寫入紀錄…"
            _write_audit(job, chord_count, status="done")

            job.status = JobStatus.DONE
            job.progress = 100
            job.stage = "完成"
            logger.info("Job %s done (chords): %s chords, key=%s", job_id, chord_count, key)

            # Step 5: Hand off melody extraction to the separate melody worker so
            # this chord worker can immediately start on the next queued job.
            # Melody worker owns the audio file lifecycle from here.
            try:
                _melody_queue.put((job_id, audio_path, result_hash), block=False)
                handed_off_to_melody = True
            except queue.Full:
                logger.warning("Melody queue full, skipping melody for %s", job_id)
                handed_off_to_melody = False

        except Exception as e:
            job.status = JobStatus.ERROR
            job.error_msg = str(e)[:500]
            logger.error("Job %s failed: %s", job_id, e)
            _write_audit(job, chord_count)
            handed_off_to_melody = False

        finally:
            # Only delete the audio file if we did NOT hand it off to melody worker.
            if not locals().get("handed_off_to_melody", False):
                if audio_path and os.path.isfile(audio_path):
                    try:
                        os.remove(audio_path)
                    except OSError:
                        pass


def _melody_worker_loop():
    """Background melody-extraction worker. Runs after chord worker has already
    flipped job.status to DONE, so failures here never surface to the user —
    they just leave the song without a waterfall melody."""
    while True:
        try:
            item = _melody_queue.get(timeout=5)
        except queue.Empty:
            continue
        if item is None:
            continue
        job_id, audio_path, result_hash = item
        try:
            if not audio_path or not os.path.isfile(audio_path):
                logger.warning("Melody worker: audio missing for %s (%s)", job_id, audio_path)
                continue
            try:
                from ai.melody_extractor import MelodyExtractor
                ext = MelodyExtractor()
                melody_data = ext.extract_melody(audio_path)
                if melody_data:
                    mel_file = MELODIES_DIR / f"{result_hash}.json"
                    mel_file.write_text(
                        json.dumps({"path": f"__upload/{job_id}", "melody": melody_data}, ensure_ascii=False),
                        encoding="utf-8"
                    )
                    logger.info("Job %s melody saved (bg worker)", job_id)
            except Exception as mel_err:
                logger.warning("Melody extraction failed for %s: %s", job_id, mel_err)
        finally:
            # Melody worker owns cleanup now.
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
_melody_thread = threading.Thread(target=_melody_worker_loop, daemon=True, name="melody-worker")
_cleanup_thread = threading.Thread(target=_cleanup_loop, daemon=True, name="tmp-cleanup")
_evict_thread = threading.Thread(target=_evict_loop, daemon=True, name="job-evict")

_worker_thread.start()
_melody_thread.start()
_cleanup_thread.start()
_evict_thread.start()
