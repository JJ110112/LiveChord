"""
LiveChord Extraction Manager — 監控 PC 端批次擷取進度

架構:
  - NUC: 執行 LiveChord server (FastAPI)，提供 Web UI 監控
  - PC:  有 GPU + i9，直接執行批次擷取腳本
  - W:   NAS 共用儲存，進度檔與停止信號透過此處跨機器通訊

PC 端的 batch worker 寫入進度到 W:/data/.extraction_progress.json
NUC 端的 Web UI 讀取該檔案顯示進度
停止信號透過 W:/data/.extraction_stop sentinel file 傳遞
"""

import os
import json
from pathlib import Path


PROGRESS_FILE = Path("W:/data/.extraction_progress.json")       # PC 端
PROGRESS_FILE_NUC = Path("W:/data/.extraction_progress_nuc.json")  # NUC 端
STOP_FILE = Path("W:/data/.extraction_stop")
STOP_FILE_NUC = Path("W:/data/.extraction_stop_nuc")


class ExtractionManager:
    """跨機器監控批次擷取任務"""

    def _read_progress(self, path: Path, label: str) -> dict | None:
        """讀取一個進度檔"""
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            if data.get("status") == "running":
                import time
                mtime = path.stat().st_mtime
                if time.time() - mtime > 300:
                    data["status"] = "stale"
                    data["message"] = f"{label} 進度超過 5 分鐘未更新，可能已中斷"
            data["_source"] = label
            return data
        except (json.JSONDecodeError, OSError):
            return None

    def get_status(self) -> dict:
        """讀取 PC 端和 NUC 端的進度檔"""
        pc = self._read_progress(PROGRESS_FILE, "PC")
        nuc = self._read_progress(PROGRESS_FILE_NUC, "NUC")

        jobs = []
        if pc:
            jobs.append(pc)
        if nuc:
            jobs.append(nuc)

        if not jobs:
            return {"status": "idle", "message": "沒有任務在執行", "jobs": []}

        # 主狀態取最「活躍」的
        any_running = any(j.get("status") == "running" for j in jobs)
        any_stale = any(j.get("status") == "stale" for j in jobs)
        if any_running:
            overall = "running"
        elif any_stale:
            overall = "stale"
        else:
            overall = jobs[0].get("status", "idle")

        return {"status": overall, "jobs": jobs}

    def stop_job(self, target: str = "all") -> dict:
        """透過 NAS 寫入停止信號"""
        status = self.get_status()
        if status.get("status") not in ("running", "stale"):
            return {"ok": False, "message": "沒有任務在執行"}

        stopped = []
        try:
            if target in ("all", "pc"):
                STOP_FILE.write_text("stop", encoding='utf-8')
                stopped.append("PC")
            if target in ("all", "nuc"):
                STOP_FILE_NUC.write_text("stop", encoding='utf-8')
                stopped.append("NUC")
            return {"ok": True, "message": f"已發送停止信號: {', '.join(stopped)}"}
        except OSError as e:
            return {"ok": False, "message": f"無法寫入停止信號: {e}"}

    def get_logs(self, limit: int = 50) -> list:
        """從所有進度檔中取得最近的 log"""
        status = self.get_status()
        all_logs = []
        for job in status.get("jobs", []):
            source = job.get("_source", "")
            for log in job.get("recent_logs", []):
                log_copy = dict(log)
                log_copy["source"] = source
                all_logs.append(log_copy)
        # 依時間排序
        all_logs.sort(key=lambda x: x.get("time", ""))
        return all_logs[-limit:]

    @staticmethod
    def list_drives() -> list[dict]:
        """列出可用的音樂庫目錄"""
        drives = []
        for label, path in [
            ("Y:/ (LiveChord Music)", "Y:/"),
            ("Z:/ (Music)", "Z:/"),
        ]:
            exists = os.path.isdir(path)
            drives.append({"label": label, "path": path, "exists": exists})
        return drives


# 全域單例
extraction_manager = ExtractionManager()
