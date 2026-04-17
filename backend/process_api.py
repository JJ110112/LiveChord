"""Process API — 上傳音檔 / YouTube URL 和弦偵測"""

import os
import re
import queue
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from auth_api import get_current_user, get_admin_user
from config import is_beta_mode
from process_queue import (
    ProcessJob, JobStatus, TMP_DIR, COVERS_DIR,
    submit_job, get_job, generate_job_id, compute_file_hash,
    check_quota, get_user_daily_count, get_audit_log, get_user_audit_log,
    delete_audit_entries, find_existing_result, write_reuse_audit,
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

    url = req.url.strip()
    if not _YOUTUBE_RE.match(url):
        raise HTTPException(status_code=400, detail="請提供有效的 YouTube URL")

    # Extract video ID and normalize URL (strip playlist/radio params)
    vid_m = re.search(r"(?:v=|youtu\.be/|/shorts/)([A-Za-z0-9_-]{11})", url)
    vid = vid_m.group(1) if vid_m else ""
    if vid:
        url = f"https://www.youtube.com/watch?v={vid}"

    # Reuse existing result if same URL was already processed
    existing = find_existing_result(url)
    if existing:
        write_reuse_audit(username, url, existing["title"],
                          existing["result_hash"], existing["chord_count"])
        return {"job_id": None, "status": "done",
                "result_hash": existing["result_hash"],
                "title": existing["title"]}

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
# YouTube search (find matching video for a song title)
# ---------------------------------------------------------------------------

import subprocess
from process_queue import YTDLP_BIN

# Simple in-memory cache for YouTube search results
_yt_search_cache: dict[str, str] = {}


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
