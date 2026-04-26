"""LiveChord — 即時音樂和弦+簡譜顯示網站"""

import ipaddress
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

# ---------------------------------------------------------------------------
# Logging: rotate to data/server.log (10 MB × 5 backups) + keep stdout so
# the uvicorn cmd windows still show live output. Before this, closing the
# stdout window meant no post-hoc debugging trail — see stress-test report.
# ---------------------------------------------------------------------------
_LOG_DIR = Path(__file__).parent.parent / "data"
_LOG_DIR.mkdir(exist_ok=True)
_log_formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
_file_h = RotatingFileHandler(
    _LOG_DIR / "server.log", maxBytes=10_000_000, backupCount=5, encoding="utf-8"
)
_file_h.setFormatter(_log_formatter)
_stream_h = logging.StreamHandler()
_stream_h.setFormatter(_log_formatter)

logging.basicConfig(level=logging.INFO, handlers=[_file_h, _stream_h], force=True)

# Route uvicorn's own loggers through these handlers too
for _name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
    _lg = logging.getLogger(_name)
    _lg.handlers = [_file_h, _stream_h]
    _lg.propagate = False
    _lg.setLevel(logging.INFO)

from music_api import router as music_router
from chord_api import router as chord_router
from chord_batch import router as chord_batch_router
from user_api import router as user_router
from benchmark_api import router as benchmark_router
from ai_api import router as ai_router
from extraction_api import router as extraction_router
from jam_tracks_api import router as jam_tracks_router
from auth_api import router as auth_router
from feedback_api import router as feedback_router
from analytics_api import router as analytics_router
from process_api import router as process_router
import auth_api
from fastapi import Depends
import auto_worker
from task_lock import get_task_lock
import library_groups
from personal_mode import require_personal_mode



# ---------------------------------------------------------------------------
# App lifecycle: 啟動時依設定決定是否開啟 Core
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app):
    settings = auto_worker.load_settings()
    # 只有明確開啟 auto_start_on_boot 才自動啟動 Core
    # 預設關閉，使用者需手動點 ▶ 啟動 Core，避免在還沒設定群組時就開始擷取
    if settings.get("auto_start_on_boot", False):
        auto_worker.start_worker()
    # Personal-only: tiered data backup scheduler (beta doesn't manage infra).
    # Dev opt-out: set LIVECHORD_NO_SCHEDULER=1 in start_personal_local.bat to
    # suppress on the dev box (8803) so it doesn't compete with NUC prod (8800)
    # for backup writes against the same data root.
    try:
        import os as _os
        from config import is_beta_mode
        if is_beta_mode():
            pass
        elif _os.environ.get("LIVECHORD_NO_SCHEDULER", "").strip() in ("1", "true", "yes"):
            import logging
            logging.getLogger("livechord").info(
                "backup_scheduler skipped (LIVECHORD_NO_SCHEDULER=%s)",
                _os.environ.get("LIVECHORD_NO_SCHEDULER"))
        else:
            import backup_scheduler
            backup_scheduler.start()
    except Exception as e:
        import logging
        logging.getLogger("livechord").warning(f"backup_scheduler start failed: {e}")
    yield
    auto_worker.stop_worker()
    try:
        import backup_scheduler
        backup_scheduler.stop()
    except Exception:
        pass


app = FastAPI(title="LiveChord", version="1.0.0", lifespan=lifespan)

# 掛載 API routers
app.include_router(music_router)
app.include_router(chord_router)
app.include_router(chord_batch_router)
app.include_router(user_router)
app.include_router(benchmark_router)
app.include_router(ai_router)
app.include_router(extraction_router)
app.include_router(jam_tracks_router)
app.include_router(auth_router)
app.include_router(feedback_router)
app.include_router(analytics_router)
app.include_router(process_router)

# ---------------------------------------------------------------------------
# Admin IP 限制 (beta mode 時只允許 LAN 存取 /admin 相關路徑)
# ---------------------------------------------------------------------------



@app.middleware("http")
async def admin_lan_restriction(request: Request, call_next):
    from config import is_beta_mode, is_lan_ip
    path = request.url.path
    
    # We still keep _ADMIN_PREFIXES definition here for local use
    _ADMIN_PREFIXES = ("/admin", "/api/auto/", "/api/tasks/", "/api/library/")
    
    if is_beta_mode() and any(path.startswith(p) for p in _ADMIN_PREFIXES):
        # Check real client IP: CF-Connecting-IP (Cloudflare), X-Forwarded-For, or direct
        client_ip = (
            request.headers.get("cf-connecting-ip")
            or (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
            or (request.client.host if request.client else "")
        )
        if not is_lan_ip(client_ip):
            return JSONResponse(status_code=403, content={"detail": "Admin access restricted to LAN"})
    return await call_next(request)


# 前端靜態檔案
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
app.mount("/css", StaticFiles(directory=FRONTEND_DIR / "css"), name="css")
app.mount("/js", StaticFiles(directory=FRONTEND_DIR / "js"), name="js")
app.mount("/img", StaticFiles(directory=FRONTEND_DIR / "img"), name="img")


# ---------------------------------------------------------------------------
# 自動工作器 API
# ---------------------------------------------------------------------------

@app.get("/api/auto/status", dependencies=[Depends(require_personal_mode)])
async def auto_status(admin: str = Depends(auth_api.get_admin_user)):
    return auto_worker.get_worker_state()


@app.get("/api/auto/log", dependencies=[Depends(require_personal_mode)])
async def auto_log(limit: int = 50, admin: str = Depends(auth_api.get_admin_user)):
    return {"log": auto_worker.get_log(limit)}


@app.get("/api/auto/settings", dependencies=[Depends(require_personal_mode)])
async def auto_settings_get(admin: str = Depends(auth_api.get_admin_user)):
    return auto_worker.load_settings()


@app.post("/api/auto/settings", dependencies=[Depends(require_personal_mode)])
async def auto_settings_save(body: dict, admin: str = Depends(auth_api.get_admin_user)):
    settings = auto_worker.load_settings()
    settings.update(body)
    auto_worker.save_settings(settings)
    return {"ok": True}


@app.get("/api/auto/settings/backups")
async def auto_settings_backups(admin: str = Depends(auth_api.get_admin_user)):
    return {
        "backups": auto_worker.list_settings_backups(),
        "targets": auto_worker.list_backup_targets(),
    }


@app.post("/api/auto/settings/backups/restore")
async def auto_settings_backup_restore(body: dict, admin: str = Depends(auth_api.get_admin_user)):
    filename = (body or {}).get("filename", "")
    target = (body or {}).get("target") or "local"
    try:
        restored = auto_worker.restore_settings_backup(filename, target)
        return {"ok": True, "restored": restored, "target": target}
    except PermissionError as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail=str(e))
    except (ValueError, FileNotFoundError) as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/auto/settings/backups/download")
async def auto_settings_backup_download(
    filename: str,
    target: str = "local",
    admin: str = Depends(auth_api.get_admin_user),
):
    try:
        data = auto_worker.read_settings_backup(filename, target)
    except PermissionError as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail=str(e))
    except (ValueError, FileNotFoundError) as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=str(e))
    from fastapi.responses import Response
    return Response(
        content=data,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/auto/start", dependencies=[Depends(require_personal_mode)])
async def auto_start(admin: str = Depends(auth_api.get_admin_user)):
    ok = auto_worker.start_worker()
    return {"ok": ok, "message": "已啟動" if ok else "已在運行中"}


@app.post("/api/auto/stop", dependencies=[Depends(require_personal_mode)])
async def auto_stop(admin: str = Depends(auth_api.get_admin_user)):
    ok = auto_worker.stop_worker()
    return {"ok": ok, "message": "正在停止" if ok else "未在運行"}


@app.post("/api/auto/trigger", dependencies=[Depends(require_personal_mode)])
async def auto_trigger(admin: str = Depends(auth_api.get_admin_user)):
    ok = auto_worker.trigger_now()
    return {"ok": ok, "message": "已觸發" if ok else "工作器非等待狀態"}


# ---------------------------------------------------------------------------
# Data backup (tiered): Tier1 small irreplaceable data, Tier2 heavy-to-recompute,
# Tier3 bulk derived. Runs in a background thread; admin UI polls /status.
# ---------------------------------------------------------------------------

@app.get("/api/admin/backup/info", dependencies=[Depends(require_personal_mode)])
async def backup_info(admin: str = Depends(auth_api.get_admin_user)):
    import backup_core
    settings = auto_worker.load_settings() or {}
    return {
        "target": str(backup_core.get_backup_target()),
        "tiers": backup_core.get_tier_info(),
        "snapshots": backup_core.list_backups(),
        "history": backup_core.get_history(limit=20),
        "state": backup_core.get_state(),
        "schedule": settings.get("backup_schedule") or {},
    }


@app.get("/api/admin/backup/status", dependencies=[Depends(require_personal_mode)])
async def backup_status(admin: str = Depends(auth_api.get_admin_user)):
    import backup_core
    return backup_core.get_state()


@app.post("/api/admin/backup/run", dependencies=[Depends(require_personal_mode)])
async def backup_run(body: dict, admin: str = Depends(auth_api.get_admin_user)):
    import backup_core
    from fastapi import HTTPException
    tier = (body or {}).get("tier", "")
    if tier not in backup_core.TIERS:
        raise HTTPException(status_code=400, detail=f"invalid tier: {tier}")
    try:
        return backup_core.run_backup_async(tier)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


# ---------------------------------------------------------------------------
# 中央任務鎖 + 群組 API
# ---------------------------------------------------------------------------

@app.get("/api/tasks/status", dependencies=[Depends(require_personal_mode)])
async def tasks_status(admin: str = Depends(auth_api.get_admin_user)):
    """目前佔用 task_lock 的任務（scan / chord_batch / ...）"""
    return {"current": get_task_lock().status()}


@app.get("/api/library/groups", dependencies=[Depends(require_personal_mode)])
async def library_groups_list(admin: str = Depends(auth_api.get_admin_user)):
    """依「root 第一層資料夾」回傳曲目群組與和弦覆蓋率"""
    return {"groups": library_groups.list_groups()}


@app.get("/api/diag/paths")
def diag_paths():
    """從 service 進程的視角檢查每個 music_root 是否真的可以讀到。
    用來診斷 NUC 上的網路磁碟掛載狀態。免認證以便瀏覽器直開。"""
    import os
    from config import get_music_roots, get_midi_root
    result = {"music_roots": [], "midi_root": {}}
    for i, r in enumerate(get_music_roots()):
        info = {"index": i, "path": r, "exists": False, "is_dir": False, "flac_count": 0, "samples": [], "error": ""}
        try:
            info["exists"] = os.path.exists(r)
            info["is_dir"] = os.path.isdir(r)
            if info["is_dir"]:
                names = os.listdir(r)
                flacs = [n for n in names if n.lower().endswith(".flac")]
                info["flac_count"] = len(flacs)
                info["samples"] = flacs[:5]
        except Exception as e:
            info["error"] = f"{type(e).__name__}: {e}"
        result["music_roots"].append(info)
    midi_root = get_midi_root()
    midi_info = {"path": midi_root, "exists": False, "is_dir": False, "mid_count": 0, "error": ""}
    try:
        midi_info["exists"] = os.path.exists(midi_root) if midi_root else False
        midi_info["is_dir"] = os.path.isdir(midi_root) if midi_root else False
        if midi_info["is_dir"]:
            midi_info["mid_count"] = sum(1 for _, _, fs in os.walk(midi_root) for f in fs if f.lower().endswith((".mid", ".midi")))
    except Exception as e:
        midi_info["error"] = f"{type(e).__name__}: {e}"
    result["midi_root"] = midi_info
    return result


@app.get("/api/library/genres", dependencies=[Depends(require_personal_mode)])
def library_genres_list(admin: str = Depends(auth_api.get_admin_user)):
    """從 library_cache 計算所有 distinct genre（取第一層）與計數，依數量遞減排序。"""
    from collections import Counter
    from data_cache import get_library_tracks
    counter = Counter()
    for t in get_library_tracks():
        g = (t.get("genre") or "").split("/")[0].strip()
        if g:
            counter[g] += 1
    return {"genres": [{"name": k, "count": v} for k, v in counter.most_common()]}


# ---------------------------------------------------------------------------
# 公開設定 (前端需要的 deployment_mode 等)
# ---------------------------------------------------------------------------

@app.get("/api/config/public")
async def public_config():
    """回傳前端需要的非機密設定"""
    from config import get_deployment_mode
    return {"deployment_mode": get_deployment_mode()}


# ---------------------------------------------------------------------------
# 頁面路由
# ---------------------------------------------------------------------------

@app.get("/favicon.svg")
async def favicon():
    return FileResponse(FRONTEND_DIR / "favicon.svg", media_type="image/svg+xml")


@app.get("/manifest.json")
async def manifest():
    return FileResponse(FRONTEND_DIR / "manifest.json", media_type="application/manifest+json")


@app.get("/sw.js")
async def service_worker():
    # PWA service worker served from root scope. Service-Worker-Allowed header
    # + no-cache keeps the browser from ever pinning an old SW revision.
    return FileResponse(
        FRONTEND_DIR / "sw.js",
        media_type="application/javascript",
        headers={
            "Service-Worker-Allowed": "/",
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )


@app.get("/robots.txt")
async def robots():
    return FileResponse(FRONTEND_DIR / "robots.txt", media_type="text/plain")


NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}

@app.get("/")
async def root():
    return FileResponse(FRONTEND_DIR / "index.html", headers=NO_CACHE_HEADERS)


@app.get("/login")
@app.get("/login.html")
async def login_page():
    return FileResponse(FRONTEND_DIR / "login.html", headers=NO_CACHE_HEADERS)


@app.get("/player")
@app.get("/player.html")
async def player():
    return FileResponse(FRONTEND_DIR / "player.html", headers=NO_CACHE_HEADERS)


@app.get("/editor")
@app.get("/editor.html")
async def editor():
    return FileResponse(FRONTEND_DIR / "editor.html", headers=NO_CACHE_HEADERS)


@app.get("/admin")
@app.get("/admin.html")
async def admin():
    return FileResponse(FRONTEND_DIR / "admin.html", headers=NO_CACHE_HEADERS)


@app.get("/benchmark")
async def benchmark():
    return FileResponse(FRONTEND_DIR / "benchmark.html", headers=NO_CACHE_HEADERS)


@app.get("/extraction")
async def extraction():
    return FileResponse(FRONTEND_DIR / "extraction.html", headers=NO_CACHE_HEADERS)


@app.get("/process")
@app.get("/process.html")
async def process_page():
    return FileResponse(FRONTEND_DIR / "process.html", headers=NO_CACHE_HEADERS)


@app.get("/tos")
@app.get("/tos.html")
async def tos_page():
    return FileResponse(FRONTEND_DIR / "tos.html", headers=NO_CACHE_HEADERS)


@app.get("/share")
async def share_target():
    """Web Share Target 著陸頁：接住手機分享來的 YouTube 連結"""
    return FileResponse(FRONTEND_DIR / "share.html", headers=NO_CACHE_HEADERS)


if __name__ == "__main__":
    import sys, asyncio
    # Python 3.14+ Windows: ProactorEventLoop 避免 SelectorEventLoop 的 assert 'Data should not be empty' bug
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8800, reload=True)
