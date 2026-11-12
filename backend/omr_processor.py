"""OMR (optical music recognition): sheet-music image/PDF → MusicXML → events.

Pipeline per job (``data/omr/<job_id>/``):

1. **Render** — PDF pages → PNG at ``RENDER_DPI`` via PyMuPDF (already a
   backend dependency; pdf2image would need a poppler install on every box).
   PNG/JPG inputs are normalised (EXIF rotation, RGB) and re-saved as PNG.
2. **Recognise** — each page goes through the OMR engine CLI (``oemer`` or
   ``homr``) in a **subprocess**: the engines are minutes-per-page ONNX/torch
   workloads, and a separate process keeps their GIL and memory away from the
   FastAPI request threads (CLAUDE.md long-running-ops rule).
3. **Parse** — page MusicXMLs are joined into one timeline by
   ``score_parser.parse_musicxml`` and written as ``score.json`` + ``score.mid``.

Jobs run on one daemon worker (OMR is too heavy to run two at a time on the
NUC). Status lives in memory; finished results live on disk, so downloads keep
working after a restart. ``result.json`` is written with ``.tmp`` +
``os.replace`` before the status flips to ``done``.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from config import DATA_DIR

logger = logging.getLogger(__name__)

OMR_DIR = DATA_DIR / "omr"
RENDER_DPI = 300
MAX_PAGES = 20
PAGE_TIMEOUT_S = int(os.environ.get("LIVECHORD_OMR_PAGE_TIMEOUT", "900"))
ENGINES = ("homr", "oemer")

PDF_EXTS = {".pdf"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg"}

QUEUED = "queued"
RUNNING_RENDER = "running:rendering"
RUNNING_OMR = "running:recognizing"
RUNNING_PARSE = "running:parsing"
DONE = "done"
ERROR = "error"
_ACTIVE = (QUEUED, RUNNING_RENDER, RUNNING_OMR, RUNNING_PARSE)
_MAX_RECORDS = 200


class OmrError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Engine discovery
# ---------------------------------------------------------------------------

def _which(name: str) -> Optional[str]:
    found = shutil.which(name)
    if found:
        return found
    # pip installs console scripts next to the interpreter (Windows: Scripts/)
    scripts = Path(sys.executable).parent / ("Scripts" if os.name == "nt" else "")
    for cand in (scripts / f"{name}.exe", scripts / name):
        if cand.is_file():
            return str(cand)
    return None


def available_engines() -> dict[str, Optional[str]]:
    return {e: _which(e) for e in ENGINES}


def resolve_engine(requested: Optional[str] = None) -> str:
    """Pick the engine: explicit request > LIVECHORD_OMR_ENGINE > first installed.

    Raises OmrError when the chosen (or any) engine is not installed, so the
    API can answer 503 before accepting the upload.
    """
    want = (requested or os.environ.get("LIVECHORD_OMR_ENGINE") or "auto").lower()
    avail = available_engines()
    if want == "auto":
        for e in ENGINES:
            if avail[e]:
                return e
        raise OmrError("no OMR engine installed (pip install homr  或  pip install oemer)")
    if want not in ENGINES:
        raise OmrError(f"unknown OMR engine: {want}")
    if not avail[want]:
        raise OmrError(f"OMR engine '{want}' not installed")
    return want


# ---------------------------------------------------------------------------
# Step 1 — render inputs to PNG pages
# ---------------------------------------------------------------------------

def render_pages(src: Path, pages_dir: Path, dpi: int = RENDER_DPI,
                 max_pages: int = MAX_PAGES) -> list[Path]:
    pages_dir.mkdir(parents=True, exist_ok=True)
    ext = src.suffix.lower()
    out: list[Path] = []
    if ext in PDF_EXTS:
        import fitz  # PyMuPDF
        with fitz.open(str(src)) as doc:
            if doc.page_count == 0:
                raise OmrError("PDF has no pages")
            if doc.page_count > max_pages:
                raise OmrError(f"PDF has {doc.page_count} pages (limit {max_pages})")
            for i, page in enumerate(doc):
                pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csRGB, alpha=False)
                p = pages_dir / f"page_{i + 1:03d}.png"
                pix.save(str(p))
                out.append(p)
    elif ext in IMAGE_EXTS:
        from PIL import Image, ImageOps
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            p = pages_dir / "page_001.png"
            im.save(p, "PNG")
            out.append(p)
    else:
        raise OmrError(f"unsupported file type: {ext}")
    return out


# ---------------------------------------------------------------------------
# Step 2 — run the OMR engine on one page (subprocess)
# ---------------------------------------------------------------------------

def run_engine(engine: str, image: Path, out_dir: Path,
               timeout: int = PAGE_TIMEOUT_S) -> Path:
    """Recognise one page image; returns the produced MusicXML path."""
    exe = _which(engine)
    if not exe:
        raise OmrError(f"OMR engine '{engine}' not installed")
    out_dir.mkdir(parents=True, exist_ok=True)
    if engine == "oemer":
        cmd = [exe, str(image), "-o", str(out_dir)]
        expected = [out_dir / f"{image.stem}.musicxml"]
    else:  # homr writes <stem>.musicxml next to the input image
        cmd = [exe, str(image)]
        expected = [image.with_suffix(".musicxml")]
    env = dict(os.environ)
    # onnxruntime-gpu on the RTX 5080 box routes homr's fp16 attention through
    # cuDNN flash attention, which fails (missing nvrtc64_130_0.dll) and kills
    # the page. The plain CUDA attention kernel works (≈9 s/page). No effect on
    # CPU-only installs (NUC). Caller's environment wins if it sets this.
    env.setdefault("ORT_ENABLE_CUDNN_FLASH_ATTENTION", "0")
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env,
                              encoding="utf-8", errors="replace", cwd=str(out_dir))
    except subprocess.TimeoutExpired as e:
        raise OmrError(f"{engine} timed out after {timeout}s on {image.name}") from e
    produced = next((p for p in expected if p.is_file()), None)
    if produced is None:
        produced = next(iter(sorted(out_dir.glob(f"{image.stem}*.musicxml"))), None)
    if proc.returncode != 0 or produced is None:
        # onnxruntime logs come through with NULs and multi-KB graph dumps —
        # keep the status record readable.
        lines = [ln.replace("\x00", "").strip()
                 for ln in (proc.stderr or proc.stdout or "").splitlines()]
        tail = " | ".join(ln[:200] for ln in lines if ln)[-600:]
        raise OmrError(f"{engine} failed on {image.name} (exit {proc.returncode}): {tail}")
    final = out_dir / f"{image.stem}.musicxml"
    if produced != final:
        os.replace(produced, final)
    return final


# ---------------------------------------------------------------------------
# Job queue
# ---------------------------------------------------------------------------

@dataclass
class OmrJob:
    job_id: str
    input_path: str
    engine: str
    filename: str = ""
    requested_by: str = ""
    status: str = QUEUED
    page: int = 0
    pages: int = 0
    error: Optional[str] = None
    warnings: list = field(default_factory=list)
    submitted_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    completed_at: float = 0.0


_jobs: dict[str, OmrJob] = {}
_queue: "queue.Queue[str]" = queue.Queue()
_lock = threading.Lock()
_worker_started = False


def job_dir(job_id: str) -> Path:
    return OMR_DIR / job_id


def new_job_id() -> str:
    return uuid.uuid4().hex


def enqueue(job_id: str, input_path: Path, engine: str,
            filename: str = "", requested_by: str = "") -> OmrJob:
    job = OmrJob(job_id=job_id, input_path=str(input_path), engine=engine,
                 filename=filename, requested_by=requested_by)
    with _lock:
        _jobs[job_id] = job
        _evict()
    _queue.put(job_id)
    _ensure_worker()
    return job


def queue_position(job_id: str) -> int:
    with _lock:
        waiting = sorted((j for j in _jobs.values() if j.status == QUEUED),
                         key=lambda j: j.submitted_at)
    return next((i for i, j in enumerate(waiting) if j.job_id == job_id), -1)


def get_status(job_id: str) -> Optional[dict]:
    with _lock:
        job = _jobs.get(job_id)
        snap = asdict(job) if job else None
    if snap is None:
        # Survives restarts: a finished job is whatever is on disk.
        res = job_dir(job_id) / "result.json"
        if res.is_file():
            meta = json.loads(res.read_text(encoding="utf-8")).get("job", {})
            return {**meta, "job_id": job_id, "status": DONE}
        return None
    snap.pop("input_path", None)
    return snap


def load_result(job_id: str) -> Optional[dict]:
    res = job_dir(job_id) / "result.json"
    return json.loads(res.read_text(encoding="utf-8")) if res.is_file() else None


def _evict():
    if len(_jobs) <= _MAX_RECORDS:
        return
    done = sorted((j for j in _jobs.values() if j.status not in _ACTIVE),
                  key=lambda j: j.completed_at or j.submitted_at)
    for j in done[: len(_jobs) - _MAX_RECORDS]:
        _jobs.pop(j.job_id, None)


def _set(job: OmrJob, **kw):
    with _lock:
        for k, v in kw.items():
            setattr(job, k, v)


def _write_json_atomic(path: Path, obj: dict):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def process_job(job: OmrJob):
    """Run render → OMR → parse for one job. Raises on fatal failure."""
    from score_parser import parse_musicxml, public_view, to_midi

    d = job_dir(job.job_id)
    _set(job, status=RUNNING_RENDER, started_at=time.time())
    pages = render_pages(Path(job.input_path), d / "pages")
    _set(job, status=RUNNING_OMR, pages=len(pages))

    xmls: list[Path] = []
    for i, img in enumerate(pages, 1):
        _set(job, page=i)
        try:
            xmls.append(run_engine(job.engine, img, d / "musicxml"))
        except OmrError as e:
            # Keep going: one unreadable page shouldn't sink a 10-page score,
            # but the gap is reported — never silently dropped.
            logger.warning("omr %s page %d failed: %s", job.job_id, i, e)
            job.warnings.append(f"page {i}: {e}")
    if not xmls:
        raise OmrError("OMR produced no MusicXML for any page; " + "; ".join(job.warnings))

    _set(job, status=RUNNING_PARSE)
    score = parse_musicxml(xmls)
    to_midi(score, d / "score.mid")
    score = public_view(score)
    _write_json_atomic(d / "score.json", score)
    _write_json_atomic(d / "result.json", {
        "job": {"job_id": job.job_id, "engine": job.engine, "filename": job.filename,
                "pages": len(pages), "pages_recognized": len(xmls),
                "warnings": list(job.warnings), "completed_at": time.time()},
        "musicxml_files": [p.name for p in xmls],
        "score": score,
    })


def _worker_loop():
    while True:
        job_id = _queue.get()
        with _lock:
            job = _jobs.get(job_id)
        if job is None:
            continue
        try:
            process_job(job)
            _set(job, status=DONE, completed_at=time.time())
            logger.info("omr %s done (%s, %d pages)", job_id, job.engine, job.pages)
        except Exception as e:  # noqa: BLE001 — every failure must reach the status record
            logger.exception("omr %s failed", job_id)
            _set(job, status=ERROR, completed_at=time.time(),
                 error=f"{type(e).__name__}: {e}")


def _ensure_worker():
    global _worker_started
    with _lock:
        if _worker_started:
            return
        _worker_started = True
    threading.Thread(target=_worker_loop, name="omr-worker", daemon=True).start()
