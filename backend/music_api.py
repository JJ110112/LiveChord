"""音樂庫 API — 瀏覽、搜尋、串流、metadata"""

import os
import json
import time
import threading
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import FileResponse, StreamingResponse, Response

from mutagen.flac import FLAC

from config import get_music_root, get_music_roots, set_music_roots, resolve_path
from batch_state import BatchState
from task_lock import get_task_lock

router = APIRouter(prefix="/api", tags=["music"])
DATA_DIR = Path(__file__).parent.parent / "data"
CACHE_FILE = DATA_DIR / "library_cache.json"

# ---------------------------------------------------------------------------
# 掃描狀態（背景執行緒共享）
# ---------------------------------------------------------------------------
_scan_state = BatchState(
    progress=0, total_dirs=0,
    new_tracks=0, updated_tracks=0, deleted_tracks=0,
    mode="", error="",
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _safe_path(path: str) -> str:
    """驗證路徑在任一 MUSIC_ROOT 內，防止路徑穿越

    用 commonpath 做 segment-level 檢查，避免子字串誤判（例如檔名含 `...` 省略號）。
    realpath 已展開符號連結與相對段，所以 `..` 攻擊會被 commonpath 自動排除。
    """
    resolved = os.path.realpath(path)
    for root in get_music_roots():
        root_real = os.path.realpath(root)
        try:
            common = os.path.commonpath([resolved, root_real])
        except ValueError:
            continue  # 不同 drive
        if common.lower() == root_real.lower():
            return resolved
    raise HTTPException(status_code=403, detail="路徑不允許")


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


def _has_cover(filepath: str) -> bool:
    """檢查是否有封面（檔案或內嵌）"""
    if _find_cover(filepath):
        return True
    if filepath.lower().endswith(".flac"):
        try:
            audio = FLAC(filepath)
            return bool(audio.pictures)
        except Exception:
            pass
    return False


from chord_cache import get_chord_summary as _get_chord_summary, song_hash

# ---------------------------------------------------------------------------
# browse
# ---------------------------------------------------------------------------

@router.get("/browse")
def browse(path: str = Query(default="")):
    """瀏覽目錄（支援多音樂庫）"""
    roots = get_music_roots()

    # 空路徑且有多根目錄：顯示虛擬根列表
    if not path and len(roots) > 1:
        entries = []
        for i, r in enumerate(roots):
            # 磁碟機根目錄 (Y:\) → 取碟符 "Y:"，一般目錄 → 取資料夾名
            drive, _ = os.path.splitdrive(r)
            label = drive if drive else (os.path.basename(r.rstrip("\\/")) or r)
            vpath = f"@{i}"
            has_cover = any(
                os.path.isfile(os.path.join(r, c))
                for c in ("cover.jpg", "cover.png")
            )
            entries.append({
                "name": label, "path": vpath, "is_dir": True,
                "has_cover": has_cover,
            })
        return {"current": "", "entries": entries}

    # 決定實際根目錄與相對路徑前綴
    root_idx = 0
    browse_rel = path
    if path.startswith("@"):
        # @N 或 @N/sub/path
        parts = path.split("/", 1)
        try:
            root_idx = int(parts[0][1:])
        except ValueError:
            root_idx = 0
        browse_rel = parts[1] if len(parts) > 1 else ""
    rel_prefix = f"@{root_idx}/" if root_idx > 0 else ""

    if root_idx >= len(roots):
        raise HTTPException(status_code=404, detail="音樂庫索引不存在")

    root = roots[root_idx]
    target = os.path.join(root, browse_rel) if browse_rel else root
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
        inner_rel = os.path.relpath(full, root).replace("\\", "/")
        rel = rel_prefix + inner_rel
        if os.path.isdir(full):
            cover = any(
                os.path.isfile(os.path.join(full, c))
                for c in ("cover.jpg", "cover.png")
            )
            entries.append({
                "name": name, "path": rel, "is_dir": True,
                "has_cover": cover,
            })
        elif name.lower().endswith(".flac"):
            summary = _get_chord_summary(rel)
            entries.append({
                "name": name, "path": rel, "is_dir": False,
                "has_cover": True,
                **summary,
            })
    current_rel = os.path.relpath(target, root).replace("\\", "/")
    if current_rel == ".":
        # 根目錄層級
        if len(roots) > 1:
            drive, _ = os.path.splitdrive(root)
            current_display = f"@{root_idx}"
        else:
            current_display = ""
    else:
        current_display = rel_prefix + current_rel
    return {"current": current_display, "entries": entries}


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

@router.get("/search")
def search(q: str = Query(default="")):
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
        fname = os.path.splitext(os.path.basename(t.get('path','')))[0]
        searchable = f"{t.get('title','')} {t.get('artist','')} {t.get('album','')} {fname}".lower()
        if q_lower in searchable:
            if "unique_chords" not in t:
                summary = _get_chord_summary(t.get("path", ""))
                t.update(summary)
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
    full = _safe_path(resolve_path(path))
    if not os.path.isfile(full):
        raise HTTPException(status_code=404, detail="檔案不存在")
    if not full.lower().endswith(".flac"):
        raise HTTPException(status_code=400, detail="僅支援 FLAC 檔案")

    meta = _read_flac_meta(full)
    meta["path"] = path
    meta["has_cover"] = _find_cover(full) is not None

    # 檢查是否有和弦譜
    summary = _get_chord_summary(path)
    meta.update(summary)
    meta["has_chords"] = summary["unique_chords"] > 0

    return meta


@router.get("/track/stream")
async def track_stream(request: Request, path: str = Query(...)):
    """串流 FLAC 音訊（支援 HTTP Range — 相容平板瀏覽器）"""
    full = _safe_path(resolve_path(path))
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
def track_cover(path: str = Query(...)):
    """取得專輯封面（優先同目錄 cover.jpg，fallback FLAC 內嵌圖片）"""
    full = _safe_path(resolve_path(path))

    # 1. 找同目錄的 cover.jpg
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

    if cover:
        return FileResponse(cover, media_type="image/jpeg")

    # 2. 從 FLAC 內嵌圖片提取
    if os.path.isfile(full) and full.lower().endswith(".flac"):
        try:
            audio = FLAC(full)
            if audio.pictures:
                pic = audio.pictures[0]
                return Response(
                    content=pic.data,
                    media_type=pic.mime or "image/jpeg",
                    headers={"Cache-Control": "public, max-age=86400"},
                )
        except Exception:
            pass

    raise HTTPException(status_code=404, detail="無封面圖片")


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


_scan_cancel = False          # 設為 True 可中斷掃描

def cancel_scan():
    global _scan_cancel
    _scan_cancel = True

def _scan_worker(mode: str = "incremental"):
    """背景掃描執行緒（節流 I/O，可中斷）"""
    global _scan_cancel
    _scan_cancel = False
    lock = get_task_lock()
    _scan_state.update(
        running=True, progress=0, total_dirs=0,
        new_tracks=0, updated_tracks=0, deleted_tracks=0, mode=mode,
        started_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        finished_at="", error="",
    )

    try:
        roots = get_music_roots()
        DATA_DIR.mkdir(parents=True, exist_ok=True)

        # 讀取啟用群組（active_groups）— 若有設定，掃描只會走訪這些頂層資料夾
        # 已快取但屬於未啟用群組的條目會被「保留」(不會誤刪也不會重掃)
        try:
            from auto_worker import load_settings
            _settings = load_settings()
            active_groups = set(_settings.get("auto_chord_active_groups", []) or [])
        except Exception:
            active_groups = set()

        # 載入現有快取（增量模式用）
        existing = {}
        if mode == "incremental" and CACHE_FILE.is_file():
            old_cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            for t in old_cache.get("tracks", []):
                existing[t["path"]] = t
        # 保存現有路徑集合，用於偵測刪除
        old_paths = set(existing.keys())

        # 一次 listdir 取代每檔一次的 is_file()（43k+ 檔時省下整輪 stat syscall）
        chords_dir = DATA_DIR / "chords"
        chord_hashes = set()
        if chords_dir.is_dir():
            try:
                chord_hashes = {n[:-5] for n in os.listdir(chords_dir) if n.endswith(".json")}
            except OSError:
                pass

        tracks = []
        seen_paths = set()
        # scandir() 失敗（SMB 斷線、權限錯誤等）的相對路徑前綴集合。
        # 最終 save 時，此前綴之下的舊條目會被保留，避免被當成「已刪除」誤清。
        errored_prefixes: set = set()
        _new_meta_count = 0   # 本輪讀取 metadata 的次數（用於節流）

        def _scan_dir(current_dir: str, current_rel_prefix: str, root_prefix: str):
            nonlocal _new_meta_count
            if _scan_cancel:
                return
            _scan_state["total_dirs"] += 1

            try:
                entries = list(os.scandir(current_dir))
            except OSError:
                errored_prefixes.add(current_rel_prefix)
                return

            # 是否在 music_root 第一層（用來套 active_groups 過濾）
            is_at_root = (current_rel_prefix == root_prefix)
            try:
                root_idx = int(root_prefix[1:-1]) if root_prefix.startswith("@") else 0
            except ValueError:
                root_idx = 0

            dirs = []
            for entry in entries:
                if _scan_cancel:
                    return
                if entry.name.startswith("."):
                    continue

                if entry.is_dir():
                    # 只排除系統 / 同步 / 回收筒等技術目錄。
                    exclude_dirs = {"#recycle", "@eaDir", "@tmp", "#snapshot"}
                    if entry.name in exclude_dirs:
                        continue
                    # 在 music_root 第一層套 active_groups 過濾：
                    # 只有勾選的子資料夾會被走訪，其他直接跳過（節省 SMB I/O）
                    if is_at_root and active_groups:
                        gid = f"@{root_idx}/{entry.name}"
                        if gid not in active_groups:
                            continue
                    dirs.append(entry)
                elif entry.is_file() and entry.name.lower().endswith(".flac"):
                    # 在 music_root 第一層的 .flac 檔，若 active_groups 有設定且未涵蓋
                    # 「未分類」群組，則跳過（這些檔案歸屬 @{idx}/未分類）
                    if is_at_root and active_groups:
                        gid_uncategorized = f"@{root_idx}/未分類"
                        if gid_uncategorized not in active_groups:
                            continue
                    full = entry.path
                    rel = current_rel_prefix + entry.name
                    seen_paths.add(rel)
                    _scan_state["progress"] += 1
                    
                    # 節流：即使是快速的增量掃描，也定期釋放 GIL 以免卡住前端請求 (FastAPI)
                    if _scan_state["progress"] % 250 == 0:
                        time.sleep(0.01)

                    if mode == "incremental" and rel in existing:
                        try:
                            mtime = entry.stat().st_mtime
                            old_mtime = existing[rel].get("mtime", 0)
                            if mtime == old_mtime:
                                tracks.append(existing[rel])
                                continue
                        except OSError:
                            pass
                        _scan_state["updated_tracks"] += 1
                    else:
                        _scan_state["new_tracks"] += 1

                    meta = _read_flac_meta(full)
                    meta["path"] = rel

                    try:
                        meta["mtime"] = entry.stat().st_mtime
                    except OSError:
                        meta["mtime"] = 0

                    # 從目錄結構推斷 genre（去掉 @N/ 前綴）
                    inner_rel = rel[len(root_prefix):] if root_prefix and rel.startswith(root_prefix) else rel
                    genre_parts = inner_rel.split("/")
                    meta["genre"] = meta["genre"] or (genre_parts[0] if len(genre_parts) > 1 else "")

                    meta["has_chords"] = song_hash(rel) in chord_hashes

                    tracks.append(meta)

                    # 節流：每讀 50 個新 metadata 讓出 CPU/IO，避免阻塞 API
                    _new_meta_count += 1
                    if _new_meta_count % 50 == 0:
                        time.sleep(0.05)
                        lock.update_progress(current_item=rel, done=_scan_state["progress"])

                    # 週期存檔：incremental 模式下若還沒遇到新/更新，跳過（純拷貝 7MB 無意義）
                    if len(tracks) % 3000 == 0 and (
                        mode != "incremental"
                        or _scan_state["new_tracks"] + _scan_state["updated_tracks"] > 0
                    ):
                        merged = list(tracks)
                        for old_rel, old_t in existing.items():
                            if old_rel not in seen_paths:
                                merged.append(old_t)
                        _save_cache(merged)

            for d in dirs:
                _scan_dir(d.path, current_rel_prefix + d.name + "/", root_prefix)

        for root_idx, root in enumerate(roots):
            if _scan_cancel:
                break
            prefix = f"@{root_idx}/" if root_idx > 0 else ""
            if not os.path.isdir(root):
                continue

            _scan_dir(root, prefix, prefix)

        # 保留：
        # (1) 未啟用群組的既有條目 — 本輪不會走訪，完整保留避免誤刪
        # (2) 啟用群組但所屬目錄 scandir 失敗 — SMB 斷線/權限錯誤時，
        #     不能把整個目錄視為「已刪除」而清掉；保留舊條目等下次掃描
        if active_groups or errored_prefixes:
            try:
                from library_groups import _split_group
                preserved = 0
                preserved_errored = 0
                for old_rel, old_t in existing.items():
                    if old_rel in seen_paths:
                        continue
                    r_idx, label, _ = _split_group(old_rel)
                    gid = f"@{r_idx}/{label}"
                    keep = False
                    if active_groups and gid not in active_groups:
                        keep = True
                    elif errored_prefixes and any(
                        old_rel.startswith(p) for p in errored_prefixes
                    ):
                        keep = True
                        preserved_errored += 1
                    if keep:
                        tracks.append(old_t)
                        seen_paths.add(old_rel)
                        preserved += 1
                if preserved:
                    _scan_state["preserved_tracks"] = preserved
                if preserved_errored:
                    _scan_state["preserved_errored"] = preserved_errored
            except Exception:
                pass

        # 計算刪除的曲目數
        deleted = old_paths - seen_paths

        # 只在實際有變動時才重寫 cache 檔。沒變動就保持 mtime 不變，
        # 讓 data_cache.get_library_cache 的 mtime 比對命中，避免下次 admin
        # 載入時白白重 parse 50MB JSON。
        changed = (_scan_state["new_tracks"] > 0
                   or _scan_state["updated_tracks"] > 0
                   or len(deleted) > 0)
        if changed or not CACHE_FILE.is_file():
            _save_cache(tracks)
            try:
                from data_cache import invalidate_library_cache
                invalidate_library_cache()
            except Exception:
                pass

        _scan_state["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        _scan_state["deleted_tracks"] = len(deleted)

    except Exception as e:
        _scan_state["error"] = str(e)
    finally:
        _scan_state["running"] = False


def _scan_thread_entry(mode: str):
    """執行緒進入點：跑 worker 完成後釋放 task_lock。"""
    try:
        _scan_worker(mode)
    finally:
        get_task_lock().release()


def _start_scan(mode: str) -> dict:
    if mode not in ("full", "incremental"):
        mode = "incremental"

    lock = get_task_lock()
    if not lock.try_acquire("SCAN", "ALL"):
        cur = lock.status() or {}
        return {
            "ok": False,
            "message": f"後台正在處理 {cur.get('kind', '')} ({cur.get('scope', '')})，請稍候",
            "progress": _scan_state["progress"],
        }

    t = threading.Thread(target=_scan_thread_entry, args=(mode,), daemon=True)
    t.start()
    return {"ok": True, "message": f"背景掃描已啟動（{mode}模式）"}


@router.post("/library/scan")
async def library_scan(mode: str = Query(default="incremental")):
    """
    啟動背景掃描。
    mode=full: 全部重新掃描（忽略快取）
    mode=incremental: 增量掃描（只讀取新增/修改的檔案）
    """
    return _start_scan(mode)


@router.get("/library/scan")
async def library_scan_get(mode: str = Query(default="incremental")):
    """GET 相容：同 POST（方便瀏覽器/批次檔呼叫）"""
    return _start_scan(mode)


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
    return {"music_roots": get_music_roots()}

@router.post("/settings")
async def update_settings(request: Request):
    payload = await request.json()
    # 新格式：music_roots 列表
    new_roots = payload.get("music_roots")
    if new_roots and isinstance(new_roots, list):
        try:
            set_music_roots(new_roots)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    # 舊格式相容：music_root 字串
    elif payload.get("music_root"):
        try:
            set_music_roots([payload["music_root"]])
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "music_roots": get_music_roots()}


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
