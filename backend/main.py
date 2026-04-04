"""LiveChord — 即時音樂和弦+簡譜顯示網站"""

from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from music_api import router as music_router
from chord_api import router as chord_router
from user_api import router as user_router
from benchmark_api import router as benchmark_router
from ai_api import router as ai_router
import auto_worker


# ---------------------------------------------------------------------------
# App lifecycle: 啟動時自動開始背景工作器
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app):
    settings = auto_worker.load_settings()
    if settings.get("auto_scan_enabled") or settings.get("auto_chord_enabled"):
        auto_worker.start_worker()
    yield
    auto_worker.stop_worker()


app = FastAPI(title="LiveChord", version="1.0.0", lifespan=lifespan)

# 掛載 API routers
app.include_router(music_router)
app.include_router(chord_router)
app.include_router(user_router)
app.include_router(benchmark_router)
app.include_router(ai_router)

# 前端靜態檔案
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
app.mount("/css", StaticFiles(directory=FRONTEND_DIR / "css"), name="css")
app.mount("/js", StaticFiles(directory=FRONTEND_DIR / "js"), name="js")
app.mount("/img", StaticFiles(directory=FRONTEND_DIR / "img"), name="img")


# ---------------------------------------------------------------------------
# 自動工作器 API
# ---------------------------------------------------------------------------

@app.get("/api/auto/status")
async def auto_status():
    return auto_worker.get_worker_state()


@app.get("/api/auto/log")
async def auto_log(limit: int = 50):
    return {"log": auto_worker.get_log(limit)}


@app.get("/api/auto/settings")
async def auto_settings_get():
    return auto_worker.load_settings()


@app.post("/api/auto/settings")
async def auto_settings_save(body: dict):
    settings = auto_worker.load_settings()
    settings.update(body)
    auto_worker.save_settings(settings)
    return {"ok": True}


@app.post("/api/auto/start")
async def auto_start():
    ok = auto_worker.start_worker()
    return {"ok": ok, "message": "已啟動" if ok else "已在運行中"}


@app.post("/api/auto/stop")
async def auto_stop():
    ok = auto_worker.stop_worker()
    return {"ok": ok, "message": "正在停止" if ok else "未在運行"}


@app.post("/api/auto/trigger")
async def auto_trigger():
    ok = auto_worker.trigger_now()
    return {"ok": ok, "message": "已觸發" if ok else "工作器非等待狀態"}


# ---------------------------------------------------------------------------
# 頁面路由
# ---------------------------------------------------------------------------

@app.get("/favicon.svg")
async def favicon():
    return FileResponse(FRONTEND_DIR / "favicon.svg", media_type="image/svg+xml")


@app.get("/manifest.json")
async def manifest():
    return FileResponse(FRONTEND_DIR / "manifest.json", media_type="application/manifest+json")


@app.get("/")
async def index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/player")
async def player():
    return FileResponse(FRONTEND_DIR / "player.html")


@app.get("/editor")
async def editor():
    return FileResponse(FRONTEND_DIR / "editor.html")


@app.get("/admin")
async def admin():
    return FileResponse(FRONTEND_DIR / "admin.html")


@app.get("/benchmark")
async def benchmark():
    return FileResponse(FRONTEND_DIR / "benchmark.html")


if __name__ == "__main__":
    import sys, asyncio
    # Python 3.14+ Windows: ProactorEventLoop 避免 SelectorEventLoop 的 assert 'Data should not be empty' bug
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8800, reload=True)
