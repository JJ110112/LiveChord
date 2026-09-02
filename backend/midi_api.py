"""MIDI upload API — ``POST /api/midi/upload`` → background ingest → player.

Loose-coupled per CLAUDE.md "Long-running operations": the POST validates and
enqueues, a daemon worker runs ``midi_ingest.py`` in a *subprocess* (its
note loops + FluidSynth render never touch the server GIL), and the frontend
polls ``GET /api/midi/status/{job_id}`` until ``done`` then opens
``/player?hash=<result_hash>``.

Available in every deployment mode (MIDI carries no audio-licensing
concern); personal-mode LAN is the primary target.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from auth_api import get_user_or_anon
from process_queue import (
    ProcessJob, TMP_DIR, generate_job_id, compute_file_hash, _write_audit,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/midi", tags=["midi"])

BACKEND_DIR = Path(__file__).resolve().parent
DATA_DIR = BACKEND_DIR.parent / "data"
MIDI_AUDIO_DIR = DATA_DIR / "midi_audio"
INGEST_SCRIPT = BACKEND_DIR / "midi_ingest.py"

MAX_MIDI_BYTES = 20 * 1024 * 1024
MIDI_EXTENSIONS = {".mid", ".midi"}
_MAX_RECORDS = 200
_SUBPROCESS_TIMEOUT_S = 600


@dataclass
class _MidiJob:
    job_id: str
    username: str
    title: str
    midi_path: str
    file_hash: str = ""
    created_at: float = field(default_factory=time.time)
    status: str = "queued"          # queued / processing / done / error
    stage: str = "queued"           # i18n suffix: queued / parsing / render / save / done
    progress: int = 0
    result_hash: Optional[str] = None
    error_msg: str = ""
    audio_url: str = ""
    warnings: list = field(default_factory=list)


_jobs: dict[str, _MidiJob] = {}
_queue: "queue.Queue[str]" = queue.Queue()
_lock = threading.Lock()
_worker_started = False


def _evict_old():
    if len(_jobs) <= _MAX_RECORDS:
        return
    done = sorted(
        (j for j in _jobs.values() if j.status in ("done", "error")),
        key=lambda j: j.created_at,
    )
    for j in done[: len(_jobs) - _MAX_RECORDS]:
        _jobs.pop(j.job_id, None)


def _ensure_worker():
    global _worker_started
    with _lock:
        if _worker_started:
            return
        _worker_started = True
    threading.Thread(target=_worker_loop, daemon=True, name="midi-ingest-worker").start()


def _run_ingest(job: _MidiJob) -> dict:
    out_path = TMP_DIR / f"{job.job_id}.midi_result.json"
    progress_path = TMP_DIR / f"{job.job_id}.midi_progress.json"
    cmd = [
        sys.executable, str(INGEST_SCRIPT),
        "--midi", job.midi_path,
        "--job-id", job.job_id,
        "--title", job.title,
        "--out", str(out_path),
        "--progress", str(progress_path),
    ]
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    proc = subprocess.Popen(
        cmd, cwd=str(BACKEND_DIR), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        encoding="utf-8", errors="replace",
    )
    deadline = time.time() + _SUBPROCESS_TIMEOUT_S
    while proc.poll() is None:
        if time.time() > deadline:
            proc.kill()
            raise TimeoutError("MIDI ingest timed out")
        try:
            if progress_path.is_file():
                p = json.loads(progress_path.read_text(encoding="utf-8"))
                with _lock:
                    job.stage = str(p.get("stage") or job.stage)
                    job.progress = max(job.progress, int(p.get("progress") or 0))
        except (OSError, ValueError):
            pass
        time.sleep(0.5)
    _, stderr = proc.communicate()
    progress_path.unlink(missing_ok=True)
    if not out_path.is_file():
        raise RuntimeError((stderr or "").strip()[-400:] or f"ingest exited {proc.returncode}")
    try:
        result = json.loads(out_path.read_text(encoding="utf-8"))
    finally:
        out_path.unlink(missing_ok=True)
    if not result.get("ok"):
        raise RuntimeError(result.get("error") or (stderr or "").strip()[-400:] or "ingest failed")
    return result


def _worker_loop():
    while True:
        job_id = _queue.get()
        job = _jobs.get(job_id)
        if job is None:
            continue
        with _lock:
            job.status = "processing"
            job.stage = "parsing"
            job.progress = 5
        try:
            result = _run_ingest(job)
            with _lock:
                job.result_hash = result["hash"]
                job.audio_url = result.get("audio_url", "")
                job.warnings = list(result.get("warnings") or [])
                job.progress = 100
                job.stage = "done"
            # Audit row so the song shows up in history like an upload.
            try:
                audit = ProcessJob(
                    job_id=job.job_id, username=job.username, source_type="midi",
                    audio_path=job.midi_path, title=job.title, file_hash=job.file_hash,
                    created_at=job.created_at, result_hash=job.result_hash,
                )
                _write_audit(audit, chord_count=int(result.get("chord_count") or 0), status="done")
            except Exception as e:  # never fail the job on audit
                logger.warning("midi audit write failed for %s: %s", job.job_id, e)
            with _lock:
                job.status = "done"
            if job.warnings:
                logger.info("midi ingest %s warnings: %s", job.job_id, job.warnings)
        except Exception as e:
            logger.error("midi ingest %s failed: %s", job.job_id, e)
            with _lock:
                job.status = "error"
                job.stage = "error"
                job.error_msg = str(e)[:400]
        finally:
            try:
                Path(job.midi_path).unlink(missing_ok=True)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/upload")
def upload_midi(file: UploadFile = File(...), username: str = Depends(get_user_or_anon)):
    name = file.filename or ""
    ext = os.path.splitext(name)[1].lower()
    if ext not in MIDI_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"不支援的檔案格式: {ext or '(無副檔名)'}，請上傳 .mid / .midi")

    data = file.file.read(MAX_MIDI_BYTES + 1)
    if len(data) > MAX_MIDI_BYTES:
        raise HTTPException(status_code=400, detail="MIDI 檔案超過 20MB 上限")
    if len(data) < 14 or data[:4] not in (b"MThd", b"RIFF"):
        raise HTTPException(status_code=400, detail="不是有效的 MIDI 檔案 (缺少 MThd 標頭)")

    job_id = generate_job_id()
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = TMP_DIR / f"{job_id}{ext}"
    tmp_path.write_bytes(data)

    title = os.path.splitext(os.path.basename(name))[0].strip() or "MIDI"
    job = _MidiJob(
        job_id=job_id, username=username, title=title,
        midi_path=str(tmp_path), file_hash=compute_file_hash(str(tmp_path)),
    )
    with _lock:
        _jobs[job_id] = job
        _evict_old()
    _queue.put(job_id)
    _ensure_worker()
    return {"job_id": job_id, "status": "queued"}


@router.get("/status/{job_id}")
def midi_status(job_id: str):
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    with _lock:
        return {
            "job_id": job.job_id,
            "status": job.status,
            "stage": job.stage,
            "progress": job.progress,
            "title": job.title,
            "error": job.error_msg if job.status == "error" else "",
            "result_hash": job.result_hash if job.status == "done" else None,
            "audio_url": job.audio_url if job.status == "done" else "",
            "warnings": job.warnings if job.status == "done" else [],
            "source_type": "midi",
        }


@router.get("/audio/{song_hash}")
def midi_audio(song_hash: str):
    """Serve the FluidSynth render for a MIDI-derived song (flac or wav)."""
    if not song_hash.isalnum() or len(song_hash) > 32:
        raise HTTPException(status_code=400, detail="bad hash")
    for ext, mime in ((".flac", "audio/flac"), (".wav", "audio/wav")):
        p = MIDI_AUDIO_DIR / f"{song_hash}{ext}"
        if p.is_file():
            return FileResponse(str(p), media_type=mime,
                                headers={"Cache-Control": "public, max-age=86400"})
    raise HTTPException(status_code=404, detail="no rendered audio for this MIDI")
