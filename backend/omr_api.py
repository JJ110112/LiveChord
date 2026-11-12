"""OMR API — sheet-music upload → MusicXML / MIDI / note-event JSON.

Recognition takes minutes per page, so this follows the loosely-coupled job
pattern (CLAUDE.md, canonical: /api/process/upgrade-beats):

  POST /api/omr/convert            pre-flight checks, enqueue, return job_id (<100 ms)
  GET  /api/omr/status/{job_id}    poll every 3-5 s; on ``done`` carries the score JSON
  GET  /api/omr/download/{job_id}/{kind}   kind = musicxml (?page=N) | midi | json

Personal-mode only (the OMR engines are heavy and only installed on the NUC).
"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

import omr_processor as omr
from auth_api import get_current_user
from personal_mode import require_personal_mode

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/omr", tags=["omr"],
                   dependencies=[Depends(require_personal_mode)])

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
_JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_MAGIC = {".pdf": (b"%PDF",), ".png": (b"\x89PNG",), ".jpg": (b"\xff\xd8",), ".jpeg": (b"\xff\xd8",)}


def _check_job_id(job_id: str):
    if not _JOB_ID_RE.match(job_id):
        raise HTTPException(status_code=404, detail="job not found")


def _links(job_id: str, musicxml_files: list[str]) -> dict:
    base = f"/api/omr/download/{job_id}"
    return {
        "json": f"{base}/json",
        "midi": f"{base}/midi",
        "musicxml": [f"{base}/musicxml?page={i}" for i in range(1, len(musicxml_files) + 1)],
    }


@router.post("/convert")
def convert(file: UploadFile = File(...),
            engine: str = Form("auto"),
            username: str = Depends(get_current_user)):
    try:
        engine_name = omr.resolve_engine(engine)
    except omr.OmrError as e:
        raise HTTPException(status_code=503, detail=str(e))

    name = file.filename or ""
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if ext not in _MAGIC:
        raise HTTPException(status_code=400, detail="僅支援 PDF / PNG / JPG")
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="檔案超過 50MB 上限")
    if not data.startswith(_MAGIC[ext]):
        raise HTTPException(status_code=400, detail=f"檔案內容不是有效的 {ext[1:].upper()}")

    job_id = omr.new_job_id()
    d = omr.job_dir(job_id)
    d.mkdir(parents=True, exist_ok=True)
    src = d / f"input{ext}"
    src.write_bytes(data)

    if ext == ".pdf":  # page-count limit is cheap to check now, expensive to hit later
        import fitz
        try:
            with fitz.open(str(src)) as doc:
                n = doc.page_count
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"PDF 無法開啟: {e}")
        if n == 0 or n > omr.MAX_PAGES:
            raise HTTPException(status_code=400,
                                detail=f"PDF 頁數 {n}，需介於 1–{omr.MAX_PAGES} 頁")

    omr.enqueue(job_id, src, engine_name, filename=name, requested_by=username)
    logger.info("omr convert enqueued by %s: job=%s engine=%s file=%s",
                username, job_id, engine_name, name)
    return {"ok": True, "job_id": job_id, "engine": engine_name, "status": omr.QUEUED,
            "queue_position": omr.queue_position(job_id),
            "status_url": f"/api/omr/status/{job_id}"}


@router.get("/status/{job_id}")
def status(job_id: str):
    _check_job_id(job_id)
    snap = omr.get_status(job_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="job not found")
    if snap["status"] == omr.QUEUED:
        snap["queue_position"] = omr.queue_position(job_id)
    if snap["status"] == omr.DONE:
        result = omr.load_result(job_id) or {}
        snap["score"] = result.get("score")
        snap["warnings"] = result.get("job", {}).get("warnings", snap.get("warnings", []))
        snap["downloads"] = _links(job_id, result.get("musicxml_files", []))
    return snap


@router.get("/download/{job_id}/{kind}")
def download(job_id: str, kind: str, page: int = 1):
    _check_job_id(job_id)
    result = omr.load_result(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="result not ready")
    d = omr.job_dir(job_id)
    stem = (result.get("job", {}).get("filename") or "score").rsplit(".", 1)[0] or "score"
    if kind == "json":
        return FileResponse(d / "score.json", media_type="application/json",
                            filename=f"{stem}.json")
    if kind == "midi":
        return FileResponse(d / "score.mid", media_type="audio/midi", filename=f"{stem}.mid")
    if kind == "musicxml":
        files = result.get("musicxml_files", [])
        if not 1 <= page <= len(files):
            raise HTTPException(status_code=404, detail=f"page must be 1–{len(files)}")
        suffix = f"_p{page}" if len(files) > 1 else ""
        return FileResponse(d / "musicxml" / files[page - 1],
                            media_type="application/vnd.recordare.musicxml+xml",
                            filename=f"{stem}{suffix}.musicxml")
    raise HTTPException(status_code=404, detail="kind must be json / midi / musicxml")


@router.get("/engines")
def engines():
    """Which OMR engines are installed on this instance (for the upload UI)."""
    avail = omr.available_engines()
    return {"engines": {k: bool(v) for k, v in avail.items()}}
