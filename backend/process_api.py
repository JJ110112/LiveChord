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
    if track_path.startswith("__upload/"):
        raise HTTPException(
            status_code=410,
            detail="此曲為上傳/YT 分析，原始音檔已清除，無法重新偵測節拍",
        )

    audio_path = resolve_path(track_path) if track_path else ""
    if not audio_path or not os.path.isfile(audio_path):
        raise HTTPException(status_code=404, detail="audio file not found")

    chords = sheet.get("chords") or []
    if not chords:
        raise HTTPException(status_code=400, detail="chord JSON has no chords")

    title = sheet.get("title") or os.path.basename(track_path) or hash
    queued_status = _enqueue(hash, audio_path, str(chord_file),
                             title=title, requested_by=username)
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
