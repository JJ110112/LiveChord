"""音樂庫 API — 瀏覽、搜尋、串流、metadata"""

import os
import json
import hashlib
import time
import threading
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import FileResponse, StreamingResponse

from mutagen.flac import FLAC

from config import get_music_root, set_music_root

router = APIRouter(prefix="/api", tags=["music"])
DATA_DIR = Path(__file__).parent.parent / "data"
CACHE_FILE = DATA_DIR / "library_cache.json"

# ---------------------------------------------------------------------------
# 掃描狀態（背景執行緒共享）
# ---------------------------------------------------------------------------
_scan_state = {
    "running": False,
    "progress": 0,        # 已掃描數
    "total_dirs": 0,      # 已遍歷目錄數
    "new_tracks": 0,      # 新增曲目數
    "updated_tracks": 0,  # 更新曲目數
    "mode": "",           # "full" or "incremental"
    "started_at": "",
    "finished_at": "",
    "error": "",
}

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _safe_path(path: str) -> str:
    """驗證路徑在 MUSIC_ROOT 內，防止路徑穿越"""
    root = get_music_root()
    # 用 normpath 而非 realpath 避免 UNC 解析問題
    resolved = os.path.normpath(path)
    if not resolved.lower().startswith(root.lower()):
        raise HTTPException(status_code=403, detail="路徑不允許")
    if ".." in resolved:
        raise HTTPException(status_code=403, detail="路徑不允許")
    return resolved


def _read_flac_meta(filepath: str) -> dict:
    """讀取 FLAC metadata"""
    try:
        audio = FLAC(filepath)
        return {
            "title": (audio.get("title") or [""])[0],
            "artist": (audio.get("artist") or [""])[0],
            "album": (audio.get("album") or [""])[0],
            "genre": (audio.get("genre") or [""])[0],
            "duration": round(audio.info.length, 2) if audio.info else 0,
            "sample_rate": audio.info.sample_rate if audio.info else 0,
            "bits_per_sample": audio.info.bits_per_sample if audio.info else 0,
            "channels": audio.info.channels if audio.info else 0,
        }
    except Exception:
        name = os.path.splitext(os.path.basename(filepath))[0]
        return {
            "title": name, "artist": "", "album": "",
            "genre": "", "duration": 0, "sample_rate": 0,
            "bits_per_sample": 0, "channels": 0,
        }


def _find_cover(filepath: str) -> Optional[str]:
    """尋找同目錄的 cover.jpg"""
    directory = os.path.dirname(filepath)
    for name in ("cover.jpg", "cover.png", "Cover.jpg", "folder.jpg"):
        cover = os.path.join(directory, name)
        if os.path.isfile(cover):
            return cover
    return None


def _song_hash(path: str) -> str:
    """產生穩定的 song hash（用於和弦譜檔名）"""
    return hashlib.md5(path.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# browse
# ---------------------------------------------------------------------------

@router.get("/browse")
async def browse(path: str = Query(default="")):
    """瀏覽目錄"""
    root = get_music_root()
    target = os.path.join(root, path) if path else root
    target = _safe_path(target)

    if not os.path.isdir(target):
        raise HTTPException(status_code=404, detail="目錄不存在")

    entries = []
    try:
        items = sorted(os.listdir(target))
    except PermissionError:
        raise HTTPException(status_code=403, detail="無法存取目錄")

    for name in items:
        full = os.path.join(target, name)
        if name.startswith(".") or name.startswith("_") or name in (
                "#recycle", "@eaDir", "@tmp", "#snapshot"):
            continue
        rel = os.path.relpath(full, root).replace("\\", "/")
        if os.path.isdir(full):
            # 檢查是否有 cover.jpg
            cover = any(
                os.path.isfile(os.path.join(full, c))
                for c in ("cover.jpg", "cover.png")
            )
            entries.append({
                "name": name, "path": rel, "is_dir": True,
                "has_cover": cover,
            })
        elif name.lower().endswith(".flac"):
            entries.append({
                "name": name, "path": rel, "is_dir": False,
                "has_cover": _find_cover(full) is not None,
            })
    return {"current": os.path.relpath(target, root).replace("\\", "/"),
            "entries": entries}


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

@router.get("/search")
async def search(q: str = Query(default="")):
    """搜尋音樂庫（從快取索引）"""
    if not q or len(q.strip()) < 1:
        return {"results": []}

    q_lower = q.strip().lower()

    if not CACHE_FILE.is_file():
        if _scan_state["running"]:
            return {"results": [], "error": f"掃描進行中（{_scan_state['progress']} 首），請稍候…"}
        return {"results": [], "error": "索引尚未建立，請點右上角「掃描」或到管理頁面執行"}

    cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    tracks = cache.get("tracks", [])

    results = []
    for t in tracks:
        searchable = f"{t.get('title','')} {t.get('artist','')} {t.get('album','')}".lower()
        if q_lower in searchable:
            results.append(t)
            if len(results) >= 50:
                break

    return {"results": results}


# ---------------------------------------------------------------------------
# track info / stream / cover
# ---------------------------------------------------------------------------

@router.get("/track/info")
async def track_info(path: str = Query(...)):
    """取得單曲 metadata"""
    full = _safe_path(os.path.join(get_music_root(), path))
    if not os.path.isfile(full):
        raise HTTPException(status_code=404, detail="檔案不存在")
    if not full.lower().endswith(".flac"):
        raise HTTPException(status_code=400, detail="僅支援 FLAC 檔案")

    meta = _read_flac_meta(full)
    meta["path"] = path
    meta["has_cover"] = _find_cover(full) is not None

    # 檢查是否有和弦譜
    chords_file = DATA_DIR / "chords" / f"{_song_hash(path)}.json"
    meta["has_chords"] = chords_file.is_file()

    return meta


@router.get("/track/stream")
async def track_stream(request: Request, path: str = Query(...)):
    """串流 FLAC 音訊（支援 HTTP Range — 相容平板瀏覽器）"""
    full = _safe_path(os.path.join(get_music_root(), path))
    if not os.path.isfile(full):
        raise HTTPException(status_code=404, detail="檔案不存在")
    if not full.lower().endswith(".flac"):
        raise HTTPException(status_code=400, detail="僅支援 FLAC 檔案")

    file_size = os.path.getsize(full)
    range_header = request.headers.get("range")

    if range_header:
        # 解析 Range: bytes=start-end
        import re
        m = re.match(r"bytes=(\d*)-(\d*)", range_header)
        if not m:
            raise HTTPException(status_code=416, detail="Invalid Range")
        start = int(m.group(1)) if m.group(1) else 0
        end = int(m.group(2)) if m.group(2) else file_size - 1
        end = min(end, file_size - 1)
        if start > end or start >= file_size:
            raise HTTPException(status_code=416, detail="Range Not Satisfiable")
        content_length = end - start + 1

        def iter_range():
            with open(full, "rb") as f:
                f.seek(start)
                remaining = content_length
                while remaining > 0:
                    chunk = min(262144, remaining)  # 256KB chunks
                    data = f.read(chunk)
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        return StreamingResponse(
            iter_range(),
            status_code=206,
            media_type="audio/flac",
            headers={
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Content-Length": str(content_length),
                "Accept-Ranges": "bytes",
                "Cache-Control": "no-cache",
            },
        )

    # 無 Range — 回傳完整檔案（加上 Accept-Ranges 讓平板知道可以用 Range）
    def iter_file():
        with open(full, "rb") as f:
            while True:
                data = f.read(262144)  # 256KB chunks
                if not data:
                    break
                yield data

    return StreamingResponse(
        iter_file(),
        status_code=200,
        media_type="audio/flac",
        headers={
            "Content-Length": str(file_size),
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-cache",
        },
    )


@router.get("/track/cover")
async def track_cover(path: str = Query(...)):
    """取得專輯封面"""
    full = _safe_path(os.path.join(get_music_root(), path))

    # path 可能是檔案或目錄
    if os.path.isfile(full):
        cover = _find_cover(full)
    elif os.path.isdir(full):
        for name in ("cover.jpg", "cover.png", "Cover.jpg", "folder.jpg"):
            c = os.path.join(full, name)
            if os.path.isfile(c):
                cover = c
                break
        else:
            cover = None
    else:
        raise HTTPException(status_code=404, detail="路徑不存在")

    if not cover:
        raise HTTPException(status_code=404, detail="無封面圖片")

    return FileResponse(cover, media_type="image/jpeg")


# ---------------------------------------------------------------------------
# library scan — 非同步背景掃描 + 增量模式
# ---------------------------------------------------------------------------

def _save_cache(tracks: list):
    """將掃描結果存檔（掃描過程中定期呼叫 + 結束時呼叫）"""
    cache = {
        "scan_time": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "total_tracks": len(tracks),
        "tracks": tracks,
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # 先寫暫存檔再 rename，避免寫入中途被讀到不完整的 JSON
    tmp = CACHE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CACHE_FILE)


def _scan_worker(mode: str = "incremental"):
    """背景掃描執行緒"""
    global _scan_state
    _scan_state.update({
        "running": True, "progress": 0, "total_dirs": 0,
        "new_tracks": 0, "updated_tracks": 0, "mode": mode,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "finished_at": "", "error": "",
    })

    try:
        root = get_music_root()
        DATA_DIR.mkdir(parents=True, exist_ok=True)

        # 載入現有快取（增量模式用）
        existing = {}
        if mode == "incremental" and CACHE_FILE.is_file():
            old_cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            for t in old_cache.get("tracks", []):
                existing[t["path"]] = t
        # 保存現有路徑集合，用於偵測刪除
        old_paths = set(existing.keys())

        tracks = []
        seen_paths = set()

        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames
                           if not d.startswith(".") and d not in
                           ("#recycle", "@eaDir", "@tmp", "#snapshot")]
            _scan_state["total_dirs"] += 1

            for fname in filenames:
                if not fname.lower().endswith(".flac"):
                    continue

                full = os.path.join(dirpath, fname)
                rel = os.path.relpath(full, root).replace("\\", "/")
                seen_paths.add(rel)
                _scan_state["progress"] += 1

                if mode == "incremental" and rel in existing:
                    # 比對檔案修改時間，決定是否需要重新讀取 metadata
                    try:
                        mtime = os.path.getmtime(full)
                        old_mtime = existing[rel].get("mtime", 0)
                        if mtime == old_mtime:
                            # 未變動，沿用舊資料
                            tracks.append(existing[rel])
                            continue
                    except OSError:
                        pass
                    _scan_state["updated_tracks"] += 1
                else:
                    _scan_state["new_tracks"] += 1

                # 讀取 metadata
                meta = _read_flac_meta(full)
                meta["path"] = rel

                # mtime 用於下次增量比對
                try:
                    meta["mtime"] = os.path.getmtime(full)
                except OSError:
                    meta["mtime"] = 0

                # 從目錄結構推斷 genre
                parts = rel.split("/")
                meta["genre"] = meta["genre"] or (parts[0] if len(parts) > 1 else "")

                # 檢查和弦譜
                chords_file = DATA_DIR / "chords" / f"{_song_hash(rel)}.json"
                meta["has_chords"] = chords_file.is_file()

                tracks.append(meta)

                # 每 3000 首存檔一次，防止中途斷電/重啟遺失
                if len(tracks) % 3000 == 0:
                    _save_cache(tracks)

        # 計算刪除的曲目數
        deleted = old_paths - seen_paths

        # 最終存檔
        _save_cache(tracks)

        _scan_state["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        _scan_state["deleted_tracks"] = len(deleted)

    except Exception as e:
        _scan_state["error"] = str(e)
    finally:
        _scan_state["running"] = False


@router.post("/library/scan")
async def library_scan(mode: str = Query(default="incremental")):
    """
    啟動背景掃描。
    mode=full: 全部重新掃描（忽略快取）
    mode=incremental: 增量掃描（只讀取新增/修改的檔案）
    """
    if _scan_state["running"]:
        return {
            "ok": False,
            "message": "掃描進行中，請稍候",
            "progress": _scan_state["progress"],
        }

    if mode not in ("full", "incremental"):
        mode = "incremental"

    t = threading.Thread(target=_scan_worker, args=(mode,), daemon=True)
    t.start()

    return {"ok": True, "message": f"背景掃描已啟動（{mode}模式）"}


@router.get("/library/scan")
async def library_scan_get(mode: str = Query(default="incremental")):
    """GET 相容：同 POST（方便瀏覽器/批次檔呼叫）"""
    if _scan_state["running"]:
        return {
            "ok": False,
            "message": "掃描進行中，請稍候",
            "progress": _scan_state["progress"],
        }

    if mode not in ("full", "incremental"):
        mode = "incremental"

    t = threading.Thread(target=_scan_worker, args=(mode,), daemon=True)
    t.start()

    return {"ok": True, "message": f"背景掃描已啟動（{mode}模式）"}


@router.get("/library/scan/status")
async def library_scan_status():
    """查詢掃描進度"""
    return {
        "running": _scan_state["running"],
        "mode": _scan_state["mode"],
        "progress": _scan_state["progress"],
        "total_dirs": _scan_state["total_dirs"],
        "new_tracks": _scan_state["new_tracks"],
        "updated_tracks": _scan_state["updated_tracks"],
        "deleted_tracks": _scan_state.get("deleted_tracks", 0),
        "started_at": _scan_state["started_at"],
        "finished_at": _scan_state["finished_at"],
        "error": _scan_state["error"],
    }


@router.get("/settings")
def get_settings():
    return {"music_root": get_music_root()}

@router.post("/settings")
async def update_settings(request: Request):
    payload = await request.json()
    new_root = payload.get("music_root")
    if new_root:
        try:
            set_music_root(new_root)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "music_root": get_music_root()}


@router.get("/library/stats")
async def library_stats():
    """取得庫統計"""
    result = {"indexed": False, "total_tracks": 0, "scan_time": ""}

    if CACHE_FILE.is_file():
        cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        result.update({
            "indexed": True,
            "total_tracks": cache.get("total_tracks", 0),
            "scan_time": cache.get("scan_time", ""),
        })

    # 附加掃描狀態
    result["scan_running"] = _scan_state["running"]
    if _scan_state["running"]:
        result["scan_progress"] = _scan_state["progress"]

    return result
