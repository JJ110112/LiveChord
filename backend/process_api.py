"""Process API — 上傳音檔 / YouTube URL 和弦偵測"""

import os
import re
import queue
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

from auth_api import get_current_user, get_admin_user
from config import is_beta_mode
from process_queue import (
    ProcessJob, JobStatus, TMP_DIR, COVERS_DIR,
    submit_job, get_job, generate_job_id, compute_file_hash,
    check_quota, get_user_daily_count, get_audit_log, get_user_audit_log,
    delete_audit_entries, find_existing_result, write_reuse_audit,
    find_library_mapping, upsert_library_mapping,
    CHORDS_DIR,
)

router = APIRouter(prefix="/api/process", tags=["process"])

# Max upload size: 200 MB
MAX_UPLOAD_BYTES = 200 * 1024 * 1024

ALLOWED_MIME_TYPES = {
    "audio/mpeg", "audio/mp3", "audio/flac", "audio/x-flac",
    "audio/wav", "audio/x-wav", "audio/ogg", "audio/mp4",
    "audio/x-m4a", "audio/aac", "audio/webm",
}

_YOUTUBE_RE = re.compile(
    r"^https?://((www|m)\.)?(youtube\.com/(watch|shorts)|youtu\.be/|music\.youtube\.com/watch)"
)


def _normalize_youtube_url(raw_url: str) -> tuple[str, str]:
    """Extract 11-char video ID and build canonical URL.

    Returns (canonical_url, video_id). Empty video_id if extraction fails;
    caller decides whether to reject or pass through.
    """
    m = re.search(r"(?:v=|youtu\.be/|/shorts/)([A-Za-z0-9_-]{11})", raw_url or "")
    vid = m.group(1) if m else ""
    if vid:
        return f"https://www.youtube.com/watch?v={vid}", vid
    return raw_url, ""


def _require_beta():
    if not is_beta_mode():
        raise HTTPException(status_code=404, detail="Not available")


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

@router.post("/upload", dependencies=[Depends(_require_beta)])
def upload_audio(file: UploadFile = File(...),
                 username: str = Depends(get_current_user)):
    # Quota check
    if not check_quota(username):
        raise HTTPException(
            status_code=429,
            detail=f"每日額度已用完 ({get_user_daily_count(username)}/10)"
        )

    # MIME validation
    ct = (file.content_type or "").lower()
    if ct not in ALLOWED_MIME_TYPES:
        raise HTTPException(status_code=400, detail=f"不支援的音檔格式: {ct}")

    # Read file with size limit
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="檔案超過 200MB 上限")

    # Save to tmp
    ext = os.path.splitext(file.filename or "audio.mp3")[1] or ".mp3"
    job_id = generate_job_id()
    tmp_path = TMP_DIR / f"{job_id}{ext}"
    tmp_path.write_bytes(data)

    # Compute file hash for audit
    file_hash = compute_file_hash(str(tmp_path))

    title = os.path.splitext(file.filename or "")[0].strip() or "Uploaded"

    job = ProcessJob(
        job_id=job_id,
        username=username,
        source_type="upload",
        audio_path=str(tmp_path),
        title=title,
        file_hash=file_hash,
    )

    try:
        submit_job(job)
    except queue.Full:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=503, detail="處理佇列已滿，請稍後再試")

    return {"job_id": job_id, "status": "queued"}


# ---------------------------------------------------------------------------
# YouTube URL
# ---------------------------------------------------------------------------

class YouTubeRequest(BaseModel):
    url: str = Field(min_length=10, max_length=500)


@router.post("/youtube", dependencies=[Depends(_require_beta)])
def process_youtube(req: YouTubeRequest, username: str = Depends(get_current_user)):
    if not check_quota(username):
        raise HTTPException(
            status_code=429,
            detail=f"每日額度已用完 ({get_user_daily_count(username)}/10)"
        )

    raw_url = req.url.strip()
    if not _YOUTUBE_RE.match(raw_url):
        raise HTTPException(status_code=400, detail="請提供有效的 YouTube URL")

    url, vid = _normalize_youtube_url(raw_url)
    logger.info("process_youtube: user=%s raw=%r normalized=%r vid=%r",
                username, raw_url, url, vid)

    # Task 2: cross-mode reuse — check YT URL → NAS library hash map first.
    libmap = find_library_mapping(url)
    if libmap:
        lh = libmap["library_hash"]
        logger.info("process_youtube: LIBRARY MAP hit url=%r → library_hash=%r", url, lh)
        # Count chords for audit accuracy
        try:
            import json as _json
            _cd = _json.loads((CHORDS_DIR / f"{lh}.json").read_text(encoding="utf-8"))
            _cc = len(_cd.get("chords", []))
            _title = _cd.get("title", "")
        except Exception:
            _cc, _title = 0, ""
        write_reuse_audit(username, url, _title, lh, _cc)
        return {"job_id": None, "status": "done",
                "result_hash": lh, "title": _title, "source": "library"}

    # Reuse existing result if same URL was already processed via upload/YT
    existing = find_existing_result(url)
    if existing:
        logger.info("process_youtube: REUSE hit url=%r → result_hash=%r title=%r",
                    url, existing.get("result_hash"), existing.get("title"))
        write_reuse_audit(username, url, existing["title"],
                          existing["result_hash"], existing["chord_count"])
        return {"job_id": None, "status": "done",
                "result_hash": existing["result_hash"],
                "title": existing["title"]}
    logger.info("process_youtube: no reuse, queueing new job for url=%r", url)

    job_id = generate_job_id()
    job = ProcessJob(
        job_id=job_id,
        username=username,
        source_type="youtube",
        youtube_url=url,
        title=f"YouTube: {vid or url[-11:]}",
    )

    try:
        submit_job(job)
    except queue.Full:
        raise HTTPException(status_code=503, detail="處理佇列已滿，請稍後再試")

    return {"job_id": job_id, "status": "queued"}


# ---------------------------------------------------------------------------
# Status & Result
# ---------------------------------------------------------------------------

@router.get("/status/{job_id}", dependencies=[Depends(_require_beta)])
def job_status(job_id: str, username: str = Depends(get_current_user)):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job_id": job.job_id,
        "status": job.status.value,
        "progress": job.progress,
        "stage": job.stage,
        "title": job.title,
        "error": job.error_msg if job.status == JobStatus.ERROR else "",
        "result_hash": job.result_hash if job.status == JobStatus.DONE else None,
        "source_type": job.source_type,
        "youtube_url": job.youtube_url if job.source_type == "youtube" else "",
    }


@router.get("/result/{job_id}", dependencies=[Depends(_require_beta)])
def job_result(job_id: str, username: str = Depends(get_current_user)):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != JobStatus.DONE:
        raise HTTPException(status_code=400, detail=f"Job not done (status: {job.status.value})")
    if not job.result_hash:
        raise HTTPException(status_code=500, detail="No result hash")

    chord_file = CHORDS_DIR / f"{job.result_hash}.json"
    if not chord_file.is_file():
        raise HTTPException(status_code=404, detail="Chord data not found")

    import json
    return json.loads(chord_file.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Cover art
# ---------------------------------------------------------------------------

@router.get("/cover/{hash}")
def get_cover(hash: str):
    """Serve cover art for a processed song."""
    cover_path = COVERS_DIR / f"{hash}.jpg"
    if not cover_path.is_file():
        raise HTTPException(status_code=404, detail="No cover")
    return FileResponse(cover_path, media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=86400"})


# ---------------------------------------------------------------------------
# On-demand beat upgrade — runs the slow madmom path against an existing
# chord JSON. Default ingest uses librosa (fast); users opt into rubato
# tracking from the player Tools popup. See doc/QA_BATTLE_STORY.md 番外篇 VII.
# ---------------------------------------------------------------------------

def upgrade_beats(hash: str, username: str = Depends(get_current_user)):
    """Enqueue an on-demand madmom beat-detection job.

    Returns immediately with ``{status: queued|duplicate, hash, ...}``.
    The actual madmom run (~30s) happens in beat_upgrade_queue's daemon
    worker thread; client polls ``GET /api/process/upgrade-beats/status``
    for completion. Pre-flight rejects (early validation):
      - madmom missing → 503 with diagnostic
      - upload-mode path (audio cleaned up) → 410
      - chord JSON / audio missing → 404
      - chord array empty → 400
    """
    import json as _json
    from beat_snap import HAS_MADMOM, MADMOM_IMPORT_ERROR
    from beat_upgrade_queue import enqueue as _enqueue, get_status as _get_status
    from config import resolve_path

    if not HAS_MADMOM:
        raise HTTPException(
            status_code=503,
            detail=f"madmom not installed: {MADMOM_IMPORT_ERROR or 'unknown'}",
        )

    chord_file = CHORDS_DIR / f"{hash}.json"
    if not chord_file.is_file():
        raise HTTPException(status_code=404, detail="chord JSON not found")

    sheet = _json.loads(chord_file.read_text(encoding="utf-8"))
    track_path = sheet.get("path", "")
    youtube_url = sheet.get("youtube_url", "")

    # Upload-mode (process_queue cleaned up the audio after melody extraction).
    # YT-mode upload (chord JSON has youtube_url): worker can re-download.
    # Pure file upload (no youtube_url): no recovery path → 410 with prompt.
    if track_path.startswith("__upload/") and not youtube_url:
        raise HTTPException(
            status_code=410,
            detail="此曲為直接上傳音檔，原始檔已清除；請從首頁重新上傳此曲以重新偵測節拍",
        )

    # For library-mode (NAS path) songs, audio must exist on disk now.
    # For YT upload-mode, skip the existence check — worker will re-download.
    audio_path = ""
    if track_path and not track_path.startswith("__upload/"):
        audio_path = resolve_path(track_path)
        if not audio_path or not os.path.isfile(audio_path):
            raise HTTPException(status_code=404, detail="audio file not found")

    chords = sheet.get("chords") or []
    if not chords:
        raise HTTPException(status_code=400, detail="chord JSON has no chords")

    title = sheet.get("title") or os.path.basename(track_path) or hash
    queued_status = _enqueue(hash, audio_path, str(chord_file),
                             title=title, requested_by=username,
                             youtube_url=youtube_url)
    snapshot = _get_status(hash) or {}

    logger.info("upgrade_beats enqueued by %s: hash=%s status=%s",
                username, hash, queued_status)

    return {
        "ok": True,
        "hash": hash,
        "title": title,
        "status": snapshot.get("status", queued_status),
        "queued": queued_status == "queued",
        "duplicate": queued_status == "duplicate",
    }


def upgrade_beats_status(hash: str, username: str = Depends(get_current_user)):
    """Return current status of a beat-upgrade job for ``hash``.

    Status values: queued / running / done / error / not_found.
    Frontend polls this to decide when to fire the completion toast.
    """
    from beat_upgrade_queue import get_status as _get_status
    snap = _get_status(hash)
    if snap is None:
        return {"status": "not_found", "hash": hash}
    return {"hash": hash, **snap}


router.add_api_route("/upgrade-beats", upgrade_beats, methods=["POST"])
router.add_api_route("/upgrade-beats/status", upgrade_beats_status,
                     methods=["GET"])


# ---------------------------------------------------------------------------
# Beat source toggle (personal-only, 8800)
# ---------------------------------------------------------------------------
# Lets the user flip a song between librosa (fast, ingest default) and madmom
# (rubato tracking) beat sources, for A/B comparison. Each source's result is
# cached to disk so subsequent switches are instant (no 30s madmom re-run).
#
# Caches:
#   <hash>.json.bak.librosa  — full sheet snapshot when librosa was source
#   <hash>.json.bak.madmom   — full sheet snapshot when madmom was source
# Legacy .bak.beats (written by old upgrade_beats flow) migrates to .bak.librosa
# on first read — semantically equivalent (pre-madmom == librosa).
#
# Swap preserves user edits to chords/sections/etc; only beat-related fields
# are replaced (see BEAT_FIELDS).
# ---------------------------------------------------------------------------
from personal_mode import require_personal_mode

_BEAT_FIELDS = ("bpm", "beats", "downbeats", "tempo_curve",
                "beats_source", "beat_version", "bpm_correction")


def _beat_source_category(src) -> str:
    if not src:
        return "librosa"
    s = str(src).lower()
    return "madmom" if "madmom" in s else "librosa"


def _migrate_legacy_bak(sheet_path: str):
    legacy = sheet_path + ".bak.beats"
    lib = sheet_path + ".bak.librosa"
    if os.path.exists(legacy) and not os.path.exists(lib):
        try:
            os.replace(legacy, lib)
            logger.info("migrated legacy .bak.beats -> .bak.librosa: %s", sheet_path)
        except Exception as e:
            logger.warning("bak migration failed %s: %s", sheet_path, e)


def _apply_beat_fields_from_snapshot(current: dict, snapshot: dict):
    """Copy beat fields from snapshot onto current (in-place).

    Keys missing in snapshot get removed from current, so a librosa snapshot
    predating bpm_correction doesn't leave a stale correction behind.
    """
    for k in _BEAT_FIELDS:
        if k in snapshot:
            current[k] = snapshot[k]
        else:
            current.pop(k, None)


def _overlay_beat_fields_to_user_version(hash: str, username: str, sheet: dict):
    """If the caller has a user-specific chord version saved (which is what
    GET /api/chords serves them on LAN personal mode), overlay the beat fields
    from the canonical sheet onto that file so the UI sees the new beats_source.

    The user version was saved via ChordSheet (Pydantic) which only preserves
    path/key/capo/bpm/chords — beat fields get stripped. Without this overlay,
    the user continues to see stale/missing beat data after a switch.
    """
    import json as _json
    from pathlib import Path
    if not username or username == "admin" and False:
        return  # (no special-case; kept explicit for reviewers)
    if not username:
        return
    user_path = (Path(__file__).parent.parent / "data" / "users"
                 / username / "chords" / f"{hash}.json")
    if not user_path.is_file():
        return
    try:
        user_sheet = _json.loads(user_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("overlay %s -> user %s failed to read: %s",
                       hash, username, e)
        return
    # Apply the same beat fields (bpm is in ChordSheet so already tracked;
    # beats/downbeats/tempo_curve/beats_source/beat_version/bpm_correction
    # are the ones that normally get stripped).
    _apply_beat_fields_from_snapshot(user_sheet, sheet)
    tmp = str(user_path) + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(user_sheet, f, ensure_ascii=False, indent=2)
        os.replace(tmp, str(user_path))
        logger.info("overlay beat fields to user version: %s / %s", username, hash)
    except Exception as e:
        logger.warning("overlay %s -> user %s write failed: %s",
                       hash, username, e)


@router.post("/beats/switch", dependencies=[Depends(require_personal_mode)])
def switch_beats(
    mode: str,
    hash: str = "",
    path: str = "",
    username: str = Depends(get_current_user),
):
    """Swap a song's beat source between librosa and madmom.

    - If target cache exists: atomic in-place swap, <1s.
    - If target == librosa and cache missing: run librosa synchronously (~1-2s).
    - If target == madmom and cache missing: enqueue madmom job (~30s,
      background) — client polls /upgrade-beats/status.
    Accepts ``hash`` OR ``path`` (8800 path-mode sends path; hash-mode sends hash).
    Personal-mode only; beta instance returns 404.
    """
    import json as _json
    from chord_cache import song_hash
    from config import resolve_path
    from beat_snap import (HAS_MADMOM, MADMOM_IMPORT_ERROR,
                           analyze_and_snap_dynamic)

    if mode not in ("librosa", "madmom"):
        raise HTTPException(status_code=400,
                            detail="mode must be 'librosa' or 'madmom'")

    if not hash and path:
        hash = song_hash(path)
    if not hash:
        raise HTTPException(status_code=400, detail="hash or path required")

    chord_file = CHORDS_DIR / f"{hash}.json"
    if not chord_file.is_file():
        raise HTTPException(status_code=404, detail="chord JSON not found")

    sheet_path = str(chord_file)
    sheet = _json.loads(chord_file.read_text(encoding="utf-8"))

    _migrate_legacy_bak(sheet_path)

    current_cat = _beat_source_category(sheet.get("beats_source"))
    if current_cat == mode:
        return {
            "ok": True, "already": True, "hash": hash, "mode": mode,
            "bpm": sheet.get("bpm"),
            "beats_source": sheet.get("beats_source"),
        }

    target_bak = sheet_path + f".bak.{mode}"
    other_bak = sheet_path + f".bak.{current_cat}"

    # Fast path: target cache hit
    if os.path.isfile(target_bak):
        try:
            target_snapshot = _json.loads(
                open(target_bak, encoding="utf-8").read())
        except Exception as e:
            raise HTTPException(status_code=500,
                                detail=f"target cache read failed: {e}")
        # Save current state as the other-mode cache if not already stored
        if not os.path.exists(other_bak):
            try:
                with open(other_bak, "w", encoding="utf-8") as f:
                    _json.dump(sheet, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.warning("switch %s: %s cache write failed: %s",
                               hash, current_cat, e)
        _apply_beat_fields_from_snapshot(sheet, target_snapshot)
        tmp = sheet_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(sheet, f, ensure_ascii=False, indent=2)
        os.replace(tmp, sheet_path)
        _overlay_beat_fields_to_user_version(hash, username, sheet)
        logger.info("switch_beats %s -> %s (cached, by %s)",
                    hash, mode, username)
        return {
            "ok": True, "switched": True, "cached": True,
            "hash": hash, "mode": mode,
            "bpm": sheet.get("bpm"),
            "beats_source": sheet.get("beats_source"),
        }

    # Slow path: target cache missing → compute
    track_path = sheet.get("path", "")
    youtube_url = sheet.get("youtube_url", "")
    audio_path = ""
    if track_path and not track_path.startswith("__upload/"):
        audio_path = resolve_path(track_path) or ""

    chords = sheet.get("chords") or []
    if not chords:
        raise HTTPException(status_code=400, detail="chord JSON has no chords")

    if mode == "librosa":
        if not audio_path or not os.path.isfile(audio_path):
            raise HTTPException(status_code=404, detail="audio file not found")
        # Snapshot current (madmom) as its cache before overwriting
        if not os.path.exists(other_bak):
            try:
                with open(other_bak, "w", encoding="utf-8") as f:
                    _json.dump(sheet, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.warning("switch %s: %s cache write failed: %s",
                               hash, current_cat, e)
        chords_copy = [dict(c) for c in chords]
        info = analyze_and_snap_dynamic(audio_path, chords_copy,
                                        prefer_madmom=False)
        if not info.get("beats_source"):
            raise HTTPException(status_code=500,
                                detail="librosa returned no beats")
        sheet["chords"] = chords_copy
        if info.get("bpm"):
            sheet["bpm"] = round(info["bpm"], 1)
        sheet["beats"] = info.get("beats", [])
        sheet["downbeats"] = info.get("downbeats", [])
        sheet["tempo_curve"] = info.get("tempo_curve", [])
        sheet["beats_source"] = info["beats_source"]
        sheet["beat_version"] = int(sheet.get("beat_version", 0)) + 1
        # Write librosa cache
        try:
            with open(sheet_path + ".bak.librosa", "w", encoding="utf-8") as f:
                _json.dump(sheet, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("switch %s: librosa cache write failed: %s", hash, e)
        tmp = sheet_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(sheet, f, ensure_ascii=False, indent=2)
        os.replace(tmp, sheet_path)
        _overlay_beat_fields_to_user_version(hash, username, sheet)
        logger.info("switch_beats %s -> librosa (fresh, by %s)", hash, username)
        return {
            "ok": True, "switched": True, "cached": False,
            "hash": hash, "mode": "librosa",
            "bpm": sheet.get("bpm"),
            "beats_source": sheet.get("beats_source"),
        }

    # mode == "madmom" — delegate to background queue
    if not HAS_MADMOM:
        raise HTTPException(
            status_code=503,
            detail=f"madmom not installed: {MADMOM_IMPORT_ERROR or 'unknown'}",
        )
    if track_path.startswith("__upload/") and not youtube_url:
        raise HTTPException(
            status_code=410,
            detail="此曲為直接上傳音檔，原始檔已清除；請從首頁重新上傳此曲以重新偵測節拍",
        )
    if track_path and not track_path.startswith("__upload/"):
        if not audio_path or not os.path.isfile(audio_path):
            raise HTTPException(status_code=404, detail="audio file not found")

    # Snapshot current (librosa) as its cache before queuing madmom run
    if not os.path.exists(other_bak):
        try:
            with open(other_bak, "w", encoding="utf-8") as f:
                _json.dump(sheet, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("switch %s: librosa cache write failed: %s", hash, e)

    from beat_upgrade_queue import enqueue as _enqueue, get_status as _get_status
    title = sheet.get("title") or os.path.basename(track_path) or hash
    qs = _enqueue(hash, audio_path, str(chord_file),
                  title=title, requested_by=username, youtube_url=youtube_url)
    snap = _get_status(hash) or {}
    logger.info("switch_beats %s -> madmom queued by %s: %s",
                hash, username, qs)
    return {
        "ok": True, "queued": True, "hash": hash, "mode": "madmom",
        "title": title,
        "status": snap.get("status", qs),
        "duplicate": qs == "duplicate",
    }


# ---------------------------------------------------------------------------
# YouTube search (find matching video for a song title)
# ---------------------------------------------------------------------------

import subprocess
from process_queue import YTDLP_BIN

# Simple in-memory cache for YouTube search results
_yt_search_cache: dict[str, str] = {}


class LibraryLearnRequest(BaseModel):
    youtube_url: str = Field(min_length=10, max_length=500)
    library_hash: str = Field(min_length=8, max_length=32, pattern=r"^[a-f0-9]+$")


@router.post("/yt-library-learn", dependencies=[Depends(_require_beta)])
def yt_library_learn(req: LibraryLearnRequest,
                     username: str = Depends(get_current_user)):
    """Task 2: auto-learn YT URL → library hash mapping.

    Called by the player front-end after it confirms (a) the NAS chord JSON
    exists at this hash and (b) the embedded YT video length matches the
    chord length within 5%. Strict gate on the front-end keeps wrong
    mappings out.
    """
    if not _YOUTUBE_RE.match(req.youtube_url.strip()):
        raise HTTPException(status_code=400, detail="Invalid YouTube URL")
    url, vid = _normalize_youtube_url(req.youtube_url.strip())
    if not vid:
        raise HTTPException(status_code=400, detail="Cannot extract video ID")
    inserted = upsert_library_mapping(url, req.library_hash, mapped_by=f"auto:{username}")
    logger.info("yt_library_learn: user=%s url=%r lib=%r new=%s",
                username, url, req.library_hash, inserted)
    return {"ok": True, "new": inserted}


@router.get("/playlist-info", dependencies=[Depends(_require_beta)])
def playlist_info(url: str, username: str = Depends(get_current_user)):
    """Extract video list from a YouTube playlist URL via yt-dlp --flat-playlist.

    Returns {playlist_title, videos:[{video_id, title, duration, existing_hash}]}
    where existing_hash is set if this video was already analyzed (library map
    hit or prior process_audit 'done' row). UI renders 分析 or ▶ 播放 accordingly.
    """
    url = (url or "").strip()
    if "list=" not in url:
        raise HTTPException(status_code=400, detail="URL 不含 playlist (list=)")
    try:
        result = subprocess.run(
            [YTDLP_BIN, "--flat-playlist", "--dump-json", "--no-warnings",
             "--playlist-end", "50",   # cap to avoid huge playlists flooding
             url],
            capture_output=True, text=True, timeout=45
        )
        if result.returncode != 0:
            raise HTTPException(status_code=500,
                                detail=f"yt-dlp failed: {(result.stderr or '')[:200]}")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="yt-dlp 逾時（清單太大？）")

    import json as _json
    videos = []
    playlist_title = ""
    for line in (result.stdout or "").splitlines():
        if not line.strip():
            continue
        try:
            entry = _json.loads(line)
        except Exception:
            continue
        vid = entry.get("id") or ""
        if len(vid) != 11:
            continue
        if not playlist_title:
            playlist_title = entry.get("playlist_title") or entry.get("playlist") or ""
        canonical = f"https://www.youtube.com/watch?v={vid}"
        # Was this video already analyzed? Check library map → process_audit.
        existing_hash = None
        try:
            m = find_library_mapping(canonical)
            if m:
                existing_hash = m["library_hash"]
            else:
                e = find_existing_result(canonical)
                if e:
                    existing_hash = e["result_hash"]
        except Exception:
            pass
        videos.append({
            "video_id": vid,
            "title": entry.get("title") or vid,
            "duration": entry.get("duration") or 0,
            "existing_hash": existing_hash,
        })
    logger.info("playlist_info: user=%s url=%r videos=%d title=%r",
                username, url, len(videos), playlist_title)
    return {"playlist_title": playlist_title, "videos": videos}


@router.get("/youtube-search")
def youtube_search(q: str, username: str = Depends(get_current_user)):
    """Search YouTube for a song and return the best match video ID."""
    q = q.strip()[:120]
    if not q:
        raise HTTPException(status_code=400, detail="Empty query")

    # Check cache
    cache_key = q.lower()
    if cache_key in _yt_search_cache:
        vid = _yt_search_cache[cache_key]
        return {"video_id": vid, "url": f"https://www.youtube.com/watch?v={vid}"}

    try:
        result = subprocess.run(
            [YTDLP_BIN, "--get-id", "--no-download", "--no-playlist",
             f"ytsearch1:{q}"],
            capture_output=True, text=True, timeout=15
        )
        vid = result.stdout.strip()
        if result.returncode != 0 or not vid or len(vid) != 11:
            return {"video_id": None, "url": None}
        _yt_search_cache[cache_key] = vid
        return {"video_id": vid, "url": f"https://www.youtube.com/watch?v={vid}"}
    except Exception as e:
        return {"video_id": None, "url": None, "error": str(e)[:100]}


# ---------------------------------------------------------------------------
# User history (non-admin)
# ---------------------------------------------------------------------------

@router.get("/my-history", dependencies=[Depends(_require_beta)])
def my_history(limit: int = 20, username: str = Depends(get_current_user)):
    """Get current user's own process history from audit log."""
    return {"history": get_user_audit_log(username, limit)}


# ---------------------------------------------------------------------------
# Admin: Audit log
# ---------------------------------------------------------------------------

@router.get("/admin/audit")
def admin_audit(limit: int = 50, offset: int = 0,
                admin: str = Depends(get_admin_user)):
    return {"audit": get_audit_log(limit, offset)}


class DeleteAuditRequest(BaseModel):
    ids: list[int] = Field(..., min_length=1, max_length=100)


@router.post("/admin/audit/delete")
def admin_audit_delete(req: DeleteAuditRequest,
                       admin: str = Depends(get_admin_user)):
    """Delete audit entries and associated chord/cover/melody files."""
    deleted = delete_audit_entries(req.ids)
    return {"deleted": deleted}
