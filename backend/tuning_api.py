"""Admin endpoints for library tuning correction. Worker: tuning_fix_queue.

All long work is enqueued (CLAUDE.md >5s rule); handlers only validate.
"""

import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth_api import get_admin_user
from chord_cache import chord_file_for, song_hash
from config import resolve_path
import tuning_fix_queue as tq

router = APIRouter(prefix="/api/admin/tuning", tags=["tuning"])


class SongReq(BaseModel):
    path: Optional[str] = None
    hash: Optional[str] = None
    force: bool = False


class ScanReq(BaseModel):
    prefix: str = ""


class BatchFixReq(BaseModel):
    paths: Optional[list[str]] = None   # None -> every fixable row in the scan report
    force: bool = False


def _path_of(req: SongReq) -> str:
    if req.path:
        return req.path
    if req.hash:
        cf = chord_file_for(req.hash)
        if cf.is_file():
            import json
            p = json.loads(cf.read_text(encoding="utf-8")).get("path")
            if p:
                return p
        raise HTTPException(status_code=404, detail="hash not found; pass the library path")
    raise HTTPException(status_code=400, detail="missing path or hash")


def _check_audio(path: str) -> str:
    full = resolve_path(path)
    if not os.path.isfile(full):
        raise HTTPException(status_code=404, detail=f"audio not found: {full}")
    return full


@router.post("/analyze")
def tuning_analyze(req: SongReq, _: str = Depends(get_admin_user)):
    """Measure one song (~3-5s, run in the worker pool, read-only)."""
    path = _path_of(req)
    full = _check_audio(path)
    a = tq._get_pool().submit(tq._sub_analyze, full).result(timeout=120)
    return {"path": path, **a,
            "corrected": os.path.exists(full + ".bak"),
            "human_data": tq.human_data_reasons(song_hash(path))}


@router.post("/fix")
def tuning_fix(req: SongReq, _: str = Depends(get_admin_user)):
    path = _path_of(req)
    full = _check_audio(path)
    if os.path.exists(full + ".bak"):
        raise HTTPException(status_code=409, detail="already corrected (.bak exists); revert first")
    return {"path": path, **tq.enqueue("fix", path, force=req.force)}


@router.post("/revert")
def tuning_revert(req: SongReq, _: str = Depends(get_admin_user)):
    path = _path_of(req)
    full = resolve_path(path)
    if not os.path.exists(full + ".bak"):
        raise HTTPException(status_code=404, detail="no .bak for this song")
    try:
        return tq.revert(path)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"revert failed: {e}")


@router.post("/scan")
def tuning_scan(req: ScanReq, _: str = Depends(get_admin_user)):
    """Batch scan every library track whose path starts with ``prefix``."""
    return tq.enqueue("scan", req.prefix)


@router.get("/scan/report")
def tuning_scan_report(only_fixable: bool = False, _: str = Depends(get_admin_user)):
    rep = tq.read_report()
    if rep is None:
        raise HTTPException(status_code=404, detail="no scan report yet")
    if only_fixable:
        rep["rows"] = [r for r in rep["rows"] if r.get("fixable") and not r.get("corrected")]
    return rep


@router.post("/fix/batch")
def tuning_fix_batch(req: BatchFixReq, _: str = Depends(get_admin_user)):
    paths = req.paths
    if paths is None:
        rep = tq.read_report()
        if rep is None:
            raise HTTPException(status_code=404, detail="no scan report; run a scan first")
        if rep.get("partial"):
            raise HTTPException(status_code=409, detail="scan still running")
        paths = [r["path"] for r in rep["rows"] if r.get("fixable") and not r.get("corrected")]
    jobs = [{"path": p, **tq.enqueue("fix", p, force=req.force)} for p in paths]
    return {"queued": len(jobs), "jobs": jobs}


@router.get("/status")
def tuning_status(id: Optional[str] = None, _: str = Depends(get_admin_user)):
    return tq.get_status(id)
