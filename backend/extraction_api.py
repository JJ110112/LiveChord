"""
LiveChord Extraction Manager API

架構: NUC server 監控 PC 端批次擷取進度
- start: 不支援（需在 PC 端手動執行批次檔）
- stop: 透過 NAS sentinel file 跨機器通訊
- status/logs: 讀取 PC 端寫入的進度檔
"""

from fastapi import APIRouter, Depends
from extraction_manager import extraction_manager
from personal_mode import require_personal_mode

# Entire extraction cluster API is personal-only (PC batch-job monitoring).
router = APIRouter(
    prefix="/api/extraction",
    tags=["extraction"],
    dependencies=[Depends(require_personal_mode)],
)


@router.post("/stop")
def stop_extraction(target: str = "all"):
    return extraction_manager.stop_job(target=target)


@router.get("/status")
def extraction_status():
    return extraction_manager.get_status()


@router.get("/logs")
def extraction_logs(limit: int = 50):
    return {"logs": extraction_manager.get_logs(limit)}


@router.get("/drives")
def extraction_drives():
    return {"drives": extraction_manager.list_drives()}
