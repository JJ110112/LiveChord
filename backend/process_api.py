"""Process API — 上傳音檔 / YouTube URL 和弦偵測"""

import html
import os
import re
import queue
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel, Field

from auth_api import get_current_user, get_admin_user
from config import is_beta_mode
from process_queue import (
    ProcessJob, JobStatus, TMP_DIR,
    submit_job, get_job, generate_job_id, compute_file_hash,
    check_quota, get_user_daily_count, get_audit_log, CHORDS_DIR,
)

router = APIRouter(prefix="/api/process", tags=["process"])

# Max upload size: 50 MB
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

ALLOWED_MIME_TYPES = {
    "audio/mpeg", "audio/mp3", "audio/flac", "audio/x-flac",
    "audio/wav", "audio/x-wav", "audio/ogg", "audio/mp4",
    "audio/x-m4a", "audio/aac", "audio/webm",
}

_YOUTUBE_RE = re.compile(
    r"^https?://(www\.)?(youtube\.com/(watch|shorts)|youtu\.be/|music\.youtube\.com/watch)"
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
        raise HTTPException(status_code=400, detail="檔案超過 50MB 上限")

    # Save to tmp
    ext = os.path.splitext(file.filename or "audio.mp3")[1] or ".mp3"
    job_id = generate_job_id()
    tmp_path = TMP_DIR / f"{job_id}{ext}"
    tmp_path.write_bytes(data)

    # Compute file hash for audit
    file_hash = compute_file_hash(str(tmp_path))

    title = html.escape(os.path.splitext(file.filename or "")[0].strip()) or "Uploaded"

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

    job_id = generate_job_id()
    job = ProcessJob(
        job_id=job_id,
        username=username,
        source_type="youtube",
        youtube_url=url,
        title=f"YouTube: {url[-11:]}",
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
# Admin: Audit log
# ---------------------------------------------------------------------------

@router.get("/admin/audit")
def admin_audit(limit: int = 50, offset: int = 0,
                admin: str = Depends(get_admin_user)):
    return {"audit": get_audit_log(limit, offset)}
