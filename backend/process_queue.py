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
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@contextmanager
def _audit_conn():
    """SQLite connection with guaranteed close + auto-commit/rollback.

    ``with sqlite3.connect(...) as conn:`` only manages the transaction —
    the connection itself stays open until GC reclaims it. Under burst
    load (e.g. 10-song stress test on 2026-04-20) one of those connections
    went stale while holding a WAL write-lock, freezing every subsequent
    reader for 35+ minutes until uvicorn was restarted. This helper closes
    the connection deterministically on block exit.
    """
    conn = sqlite3.connect(AUDIT_DB_PATH, timeout=10)
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()

DATA_DIR = Path(__file__).parent.parent / "data"
CHORDS_DIR = DATA_DIR / "chords"
MELODIES_DIR = DATA_DIR / "melodies"
TMP_DIR = DATA_DIR / "tmp"
AUDIT_DB_PATH = DATA_DIR / "audit.db"

# Sharded chord layout helpers — see chord_cache for design
from chord_cache import chord_file_for, chord_bak_for, ensure_chord_bucket  # noqa: E402

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

def _ingest_beats_modal_or_local(audio_path: str, chords: list, job_id: str) -> dict:
    """Beat-tracking dispatcher used at YouTube/upload ingest time.

    Returns the same dict shape as ``beat_snap.analyze_and_snap_dynamic``
    (keys: bpm, n_snapped, beats, downbeats, tempo_curve, beats_source,
    beat_version) plus an optional ``bpm_correction`` key when Modal
    beat_this already ran ballad_halving_check inside its container — the
    caller surfaces that record into the chord JSON without re-running
    the local librosa-backed sanity check.

    Tier 1: Modal beat_this when ``LIVECHORD_USE_MODAL_BEAT_THIS=1``
    (public/VPS). Tier 2/3: local madmom or librosa via beat_snap.

    Falls through to local on any Modal failure so ingest never blocks
    on Modal availability or transient errors.
    """
    try:
        from modal_beat_this import modal_beat_this_enabled
        if modal_beat_this_enabled():
            try:
                from modal_beat_this import detect_beats_via_modal
                from beat_snap import _snap_to_grid, BEAT_VERSION
                import numpy as np
                t0 = time.time()
                r = detect_beats_via_modal(audio_path)
                elapsed = time.time() - t0
                if r and r.get("beats") and r.get("bpm"):
                    beat_arr = np.sort(np.asarray(r["beats"], dtype=np.float64))
                    n_snap = _snap_to_grid(chords, beat_arr)
                    logger.info(
                        "[beat_this-modal] %s ok in %.1fs (%d beats, %d downbeats, bpm=%.1f)",
                        job_id, elapsed,
                        len(r["beats"]), len(r["downbeats"]), r["bpm"],
                    )
                    return {
                        "bpm": r["bpm"],
                        "n_snapped": n_snap,
                        "beats": r["beats"],
                        "downbeats": r["downbeats"],
                        "tempo_curve": r["tempo_curve"],
                        "beats_source": "beat_this-modal",
                        "beat_version": BEAT_VERSION,
                        "bpm_correction": r.get("bpm_correction"),
                    }
                logger.warning(
                    "[beat_this-modal] %s empty result in %.1fs; falling back to local",
                    job_id, elapsed,
                )
            except Exception as e:
                logger.warning(
                    "[beat_this-modal] %s dispatch failed (%s: %s); falling back to local",
                    job_id, type(e).__name__, str(e)[:200],
                )
    except Exception:
        pass

    from beat_snap import analyze_and_snap_dynamic
    return analyze_and_snap_dynamic(audio_path, chords)


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

    # Dynamic beat tracking — extracts rubato-aware beats[]/downbeats[]/
    # tempo_curve so the player can sync waterfall to actual tempo drift on
    # live recordings. Three-tier dispatch:
    #   1. Modal beat_this (LIVECHORD_USE_MODAL_BEAT_THIS=1, public/VPS) —
    #      ~5-30s incl. cold start, returns full beats[]+downbeats[] grid
    #   2. Local madmom (NUC personal w/ madmom installed) — ~30s, rubato-aware
    #   3. librosa static (any host) — ~1-2s, returns BPM only, no beats[]
    # Without (1) or (2) the chord JSON has empty beats[]/downbeats[] and every
    # downstream bar-aware step (beat_refiner, bar_arbitrator, auto-split,
    # bar_phase, noise_filter) skips with reason=too-few-beats. Failure of any
    # tier falls through to the next; the chord JSON is still useful without
    # beat metadata.
    if audio_path and os.path.isfile(audio_path):
        try:
            beat_info = _ingest_beats_modal_or_local(audio_path, chords, job.job_id)
            raw_bpm = beat_info.get("bpm")
            modal_bpm_correction = beat_info.pop("bpm_correction", None)
            if raw_bpm:
                if modal_bpm_correction is not None:
                    # Modal beat_this already ran ballad_halving_check inside
                    # the GPU container — surface its record without a second
                    # local pass (would be a no-op but wastes a librosa.load).
                    if modal_bpm_correction.get("applied"):
                        sheet["bpm_correction"] = modal_bpm_correction
                    sheet["bpm"] = round(raw_bpm, 1)
                else:
                    try:
                        from bpm_sanity import ballad_halving_check, apply_halving_to_beat_info
                        corrected_bpm, bpm_meta = ballad_halving_check(float(raw_bpm), audio_path)
                        if bpm_meta.get("applied"):
                            apply_halving_to_beat_info(beat_info)
                            sheet["bpm_correction"] = bpm_meta
                            logger.info(
                                "[bpm_sanity] %s halved %.1f -> %.1f (onset=%.2f, rms_cov=%.4f)",
                                job.job_id, bpm_meta["original"], corrected_bpm,
                                bpm_meta["onset_density"] or 0, bpm_meta["rms_cov"] or 0,
                            )
                        sheet["bpm"] = round(corrected_bpm, 1)
                    except Exception as e:
                        logger.warning("bpm_sanity failed for %s: %s", job.job_id, e)
                        sheet["bpm"] = round(raw_bpm, 1)
            if beat_info.get("beats_source"):
                sheet["beats"] = beat_info.get("beats", [])
                sheet["downbeats"] = beat_info.get("downbeats", [])
                sheet["tempo_curve"] = beat_info.get("tempo_curve", [])
                sheet["beats_source"] = beat_info["beats_source"]
                sheet["beat_version"] = beat_info.get("beat_version", 0)
        except Exception as e:
            logger.warning("beat_snap failed for %s: %s", job.job_id, e)

    # Beat refiner — Phase 1 audio-level Compact Transformer that re-emits
    # beats[] and downbeats[] from CQT/chroma/onset/RMS + the initial grid
    # as a hint. Runs BEFORE bar_arbitrator so the bar arbitrator's
    # downbeat-phase recovery sees the refined grid. Gated by
    # ``beat_refiner_enabled`` (default off). The phase1_refine internal
    # gate (align_min_gain=+0.02 + count_ratio band) ensures the refined
    # output only replaces input when it's meaningfully better; otherwise
    # this is a silent no-op. Failure non-fatal.
    if audio_path and os.path.isfile(audio_path):
        try:
            from config import is_personal_mode, is_public_mode
            if is_personal_mode() or is_public_mode():
                from auto_worker import load_settings
                if load_settings().get("beat_refiner_enabled", False):
                    _run_beat_refiner_into_sheet(sheet, audio_path, job.job_id)
        except Exception as e:
            logger.warning("beat_refiner hook failed for %s: %s", job.job_id, e)

    # Bar arbitrator — AI bar/downbeat correction. Runs in personal AND public
    # modes (default flag-off in personal, flag-on in public so cloud users
    # get auto-corrected downbeats and the player auto-bar-split). Beta is
    # excluded (archival). Runs AFTER bpm_sanity AND beat_refiner so it sees
    # the corrected bpm/downbeats; mutates sheet["downbeats"] + writes
    # sheet["bar_correction"]. Failure non-fatal.
    try:
        from config import is_personal_mode, is_public_mode
        if is_personal_mode() or is_public_mode():
            from auto_worker import load_settings
            if load_settings().get("bar_arbitrator_enabled", False):
                _run_bar_arbitrator_into_sheet(sheet, job.job_id)
    except Exception as e:
        logger.warning("bar_arbitrator hook failed for %s: %s", job.job_id, e)

    ensure_chord_bucket(hash_val)
    out_file = chord_file_for(hash_val)
    out_file.write_text(json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8")
    return hash_val


# Resolve once per process — model file is small (~768 KB) but we don't
# want to pay the disk hit on every job. None until first call.
_BAR_MODEL_PATH: Optional[str] = None


def _resolve_bar_model_path() -> Optional[str]:
    """Locate bar_arbitrator_v1.onnx under data/models/. Returns the absolute
    path string when present, None otherwise (caller falls back to rule
    baseline)."""
    global _BAR_MODEL_PATH
    if _BAR_MODEL_PATH is not None:
        return _BAR_MODEL_PATH or None  # may have been set to "" if missing
    candidate = DATA_DIR / "models" / "bar_arbitrator_v1.onnx"
    if candidate.is_file():
        _BAR_MODEL_PATH = str(candidate)
    else:
        _BAR_MODEL_PATH = ""  # cache the negative so we stop probing
        logger.info("bar_arbitrator model not found at %s — falling back to rule baseline", candidate)
    return _BAR_MODEL_PATH or None


def _run_beat_refiner_into_sheet(sheet: dict, audio_path: str, job_id: str):
    """Apply the audio-level beat_refiner to the freshly-built sheet.

    Always writes ``sheet["beat_refiner"]`` metadata (even when applied=False
    so we have an audit trail of which songs were considered). When applied,
    REPLACES ``sheet["beats"]`` and ``sheet["downbeats"]`` with the refined
    arrays — downstream bar_arbitrator + chord_splitter then see the
    refined grid.

    The refiner's internal gate (phase1_refine in bar_arbitrator.py)
    decides whether refinement is worth applying:
      - input already clean (cv<0.10, align>0.7) -> bypass
      - refined output not measurably better (align gain <+0.02, or
        count out of band) -> reject
      - everything else -> adopt

    Caller wraps in try/except — never propagates.
    """
    from ai.bar_arbitrator import phase1_refine
    res = phase1_refine(sheet, audio_path)
    sheet["beat_refiner"] = {
        "applied": res["applied"],
        "reason": res["reason"],
        "model_version": res.get("model_version", "phase1_v1"),
        "score_before": res.get("score_before", 0.0),
        "score_after": res.get("score_after", 0.0),
        "n_beats_before": len(res.get("beats_before") or []),
        "n_beats_after": len(res.get("beats_after") or []),
        "n_downbeats_before": len(res.get("downbeats_before") or []),
        "n_downbeats_after": len(res.get("downbeats_after") or []),
    }
    if res["applied"]:
        sheet["beats"] = res["beats_after"]
        sheet["downbeats"] = res["downbeats_after"]
        logger.info(
            "[beat_refiner] %s replaced beats: align %.2f->%.2f, "
            "n_beats %d->%d, n_downbeats %d->%d",
            job_id, res.get("score_before", 0.0), res.get("score_after", 0.0),
            len(res["beats_before"]), len(res["beats_after"]),
            len(res["downbeats_before"]), len(res["downbeats_after"]),
        )
    else:
        logger.info("[beat_refiner] %s skipped: %s", job_id, res["reason"])


def _run_bar_arbitrator_into_sheet(sheet: dict, job_id: str):
    """Apply bar_arbitrator to the freshly-built sheet dict.

    Always writes ``sheet["bar_correction"]`` (even when applied=False, so
    we have an audit trail). Replaces ``sheet["downbeats"]`` only when the
    arbitrator decided to. Caller wraps in try/except — never propagates.
    """
    from ai.bar_arbitrator import arbitrate, apply_to_chord_json
    model_path = _resolve_bar_model_path()
    res = arbitrate(sheet, model_path=model_path)
    apply_to_chord_json(sheet, res)
    if res["applied"]:
        logger.info(
            "[bar_arbitrator] %s replaced downbeats: bpb=%d conf=%.2f model=%s n_db %d->%d",
            job_id, res["beats_per_bar"], res["score_after"], res["model_version"],
            len(res["downbeats_before"]), len(res["downbeats_after"]),
        )
    else:
        logger.info("[bar_arbitrator] %s skipped: %s", job_id, res["reason"])


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
            # Mode-aware: public deploys can route cover storage to Cloudflare
            # R2 by setting LIVECHORD_USE_R2=1 + the R2_* vars. The local-disk
            # path stays the default for personal/beta and acts as the
            # fallback when R2 upload fails so we never lose a cover.
            from r2_storage import is_r2_enabled, upload_cover
            uploaded_to_r2 = False
            if is_r2_enabled():
                try:
                    upload_cover(result_hash, cover_data)
                    uploaded_to_r2 = True
                    logger.info("Cover → R2 for %s (%d bytes)", result_hash, len(cover_data))
                except Exception as e:
                    logger.warning("R2 upload failed for %s, falling back to local disk: %s", result_hash, e)
            if not uploaded_to_r2:
                cover_path = COVERS_DIR / f"{result_hash}.jpg"
                cover_path.write_bytes(cover_data)
                logger.info("Cover → local for %s (%d bytes)", result_hash, len(cover_data))
    except Exception as e:
        logger.debug("Cover extraction failed for %s: %s", result_hash, e)


# ---------------------------------------------------------------------------
# YouTube download
# ---------------------------------------------------------------------------

def _get_youtube_title(url: str) -> str:
    """Extract video title from YouTube URL using yt-dlp.

    Routes through yt_dlp_fetch.run_yt_dlp so VPS deployments transparently
    fall back to cookies (tier 2) / Modal (tier 3) when the host IP is
    flagged. UTF-8 decoding is the default in the wrapper.
    """
    try:
        from yt_dlp_fetch import run_yt_dlp
        result, _tier = run_yt_dlp(
            ["--dump-json", "--no-download", "--no-playlist", url],
            timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            first = result.stdout.strip().splitlines()[0]
            return (json.loads(first).get("title") or "").strip()
    except Exception:
        pass
    return ""


def _download_youtube(url: str, output_path: str) -> str:
    """Download audio from YouTube URL. Returns path to wav file.

    Three-tier fallback via yt_dlp_fetch (see backend/yt_dlp_fetch.py and
    doc/OPS.md). On NUC personal mode tier 1 almost always wins; on VPS
    public mode tier 2/3 carry it.
    """
    from yt_dlp_fetch import run_yt_dlp
    result, tier = run_yt_dlp(
        ["-x", "--audio-format", "wav", "--audio-quality", "0",
         "--max-filesize", "200m", "--no-playlist",
         "-o", output_path, url],
        timeout=180,
    )
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed (tier={tier}): {(result.stderr or '')[:300]}")
    if tier > 1:
        logger.info("yt-dlp download served from tier %d for %s", tier, url)
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
    with _audit_conn() as conn:
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
    # Retry loop: under burst load a single attempt can race with other writers.
    # Was previously swallowing all exceptions silently with just logger.error —
    # during the 2026-04-20 stress test 6/10 audit rows were silently lost
    # (chord/melody JSONs saved OK, but the user had no history entry).
    last_exc: Optional[BaseException] = None
    for attempt in range(3):
        try:
            with _audit_conn() as conn:
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
            return  # success
        except sqlite3.OperationalError as e:
            last_exc = e
            logger.warning(
                "Audit write attempt %d/3 failed for job %s: %s",
                attempt + 1, job.job_id, e,
            )
            time.sleep(0.5 * (attempt + 1))
        except Exception as e:
            last_exc = e
            break
    logger.error(
        "Audit write PERMANENTLY FAILED for job %s (user=%s, title=%r, hash=%s): %s. "
        "Chord/melody files may exist on disk but will not appear in user history.",
        job.job_id, job.username, job.title, job.result_hash, last_exc,
    )


def find_existing_result(youtube_url: str) -> dict | None:
    """Check if a YouTube URL was already processed successfully."""
    with _audit_conn() as conn:
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
    if not chord_file_for(rh).is_file():
        return None
    return dict(row)


def find_library_mapping(youtube_url: str) -> dict | None:
    """Task 2: look up a YouTube URL → NAS-library hash mapping.

    Self-heals stale rows: if the mapped chord JSON no longer exists (admin
    deleted it), the map entry is removed and None is returned.
    """
    if not youtube_url:
        return None
    with _audit_conn() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT library_hash, mapped_by, ts FROM youtube_library_map WHERE youtube_url=?",
            (youtube_url,),
        ).fetchone()
        if not row:
            return None
        lh = row["library_hash"]
        if not chord_file_for(lh).is_file():
            # Stale — auto-purge.
            conn.execute("DELETE FROM youtube_library_map WHERE youtube_url=?", (youtube_url,))
            conn.commit()
            return None
        return dict(row)


def upsert_library_mapping(youtube_url: str, library_hash: str, mapped_by: str) -> bool:
    """Task 2: record a YT URL → library hash mapping. Returns True if newly inserted."""
    if not youtube_url or not library_hash:
        return False
    if not chord_file_for(library_hash).is_file():
        return False
    try:
        with _audit_conn() as conn:
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
        with _audit_conn() as conn:
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
    with _audit_conn() as conn:
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
                    chord_file_for(rh),
                    COVERS_DIR / f"{rh}.jpg",
                    MELODIES_DIR / f"{rh}.json",
                ]:
                    if path.is_file():
                        path.unlink(missing_ok=True)
                # Also drop any sidecar .bak.<src> snapshots for this hash
                for bak in chord_file_for(rh).parent.glob(f"{rh}.json.bak.*"):
                    bak.unlink(missing_ok=True)
        conn.execute(
            f"DELETE FROM process_audit WHERE id IN ({placeholders})",
            ids,
        )
        conn.commit()
    # Cascade: prune dangling __hash/<h> entries from every user's recent.json + favorites.json
    _purge_user_hash_refs(deleted_hashes)
    return len(rows)


def get_audit_log(limit: int = 50, offset: int = 0) -> list[dict]:
    with _audit_conn() as conn:
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
    with _audit_conn() as conn:
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
            chords_file = chord_file_for(rh2)
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


# ---------------------------------------------------------------------------
# Melody extraction subprocess pool — keeps librosa.pyin's GIL pressure off
# the FastAPI event loop. Top-level def for picklability under spawn (Windows).
# Mirrors the chord_detect ProcessPoolExecutor pattern documented in CLAUDE.md
# under "Pure-Python loops on daemon threads still hold the GIL".
# ---------------------------------------------------------------------------

_melody_pool = None


def _melody_subprocess_extract(audio_path):
    """Run inside the worker subprocess. Imports lazily so the parent process
    doesn't pay for melody-extractor deps until we actually need them."""
    from ai.melody_extractor import MelodyExtractor
    return MelodyExtractor().extract_melody(audio_path)


def _melody_worker_loop():
    """Background melody-extraction worker. Runs after chord worker has already
    flipped job.status to DONE, so failures here never surface to the user —
    they just leave the song without a waterfall melody.

    librosa.pyin is partly numpy-vectorized but holds the GIL on its inner
    Python segments long enough to starve unrelated FastAPI requests if we
    run it in-process on a daemon thread. We dispatch each job to a long-
    lived ProcessPoolExecutor so the heavy work runs in a fresh interpreter
    with its own GIL — same fix the BTC pipeline uses.
    """
    global _melody_pool
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
            v1_time_s = None
            v1_notes_count = None
            try:
                if _melody_pool is None:
                    from concurrent.futures import ProcessPoolExecutor
                    _melody_pool = ProcessPoolExecutor(max_workers=1)
                    logger.info("Melody ProcessPool created (max_workers=1)")
                _v1_t0 = time.time()
                future = _melody_pool.submit(_melody_subprocess_extract, audio_path)
                melody_data = future.result(timeout=600)
                v1_time_s = round(time.time() - _v1_t0, 2)
                if melody_data:
                    v1_notes_count = len(melody_data)
                    mel_file = MELODIES_DIR / f"{result_hash}.json"
                    mel_file.write_text(
                        json.dumps({"path": f"__upload/{job_id}", "melody": melody_data}, ensure_ascii=False),
                        encoding="utf-8"
                    )
                    logger.info("Job %s melody saved (bg worker)", job_id)
            except Exception as mel_err:
                logger.warning("Melody extraction failed for %s: %s", job_id, mel_err)

            # V2 Shadow Mode — fire-and-forget, never blocks user flow.
            # Copies audio first so the finally-block delete below is safe.
            try:
                from ai.melody_shadow import run_shadow_async
                run_shadow_async(
                    audio_path, result_hash,
                    v1_time_s=v1_time_s, v1_notes=v1_notes_count,
                )
            except Exception as shadow_err:
                logger.debug("Shadow V2 dispatch failed for %s: %s", job_id, shadow_err)
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
