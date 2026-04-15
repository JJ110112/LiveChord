"""和弦批次偵測 + 批次 MIDI 匯入 API"""

import json
import os
import time
import threading
from pathlib import Path
from collections import Counter

from fastapi import APIRouter, HTTPException, Query

from chord_cache import (
    song_hash,
    ensure_synced as cache_ensure_synced,
    get_chord_meta,
    update_entry_from_file as cache_update_entry,
    invalidate as cache_invalidate,
)
from config import resolve_path
from batch_state import BatchState
from task_lock import get_task_lock
from library_groups import filter_tracks_by_group

router = APIRouter(prefix="/api", tags=["chord-batch"])

DATA_DIR = Path(__file__).parent.parent / "data"
CHORDS_DIR = DATA_DIR / "chords"
CACHE_FILE = DATA_DIR / "library_cache.json"

# ---------------------------------------------------------------------------
# 批次偵測狀態
# ---------------------------------------------------------------------------

_batch_state = BatchState(
    total=0, done=0, current_track="",
    succeeded=0, failed=0, skipped=0,
    errors=[],
)

# admin 列表 polling 用：chords 目錄 hash 集合的短 TTL 快取
_chord_hashes_cache = {"set": None, "ts": 0.0}
_CHORD_HASHES_TTL = 5.0


def _get_chord_hashes() -> set:
    """一次 listdir 產生 chord hash 集合，避免每檔 is_file() 打到 FS"""
    now = time.time()
    cached = _chord_hashes_cache["set"]
    if cached is not None and now - _chord_hashes_cache["ts"] < _CHORD_HASHES_TTL:
        return cached
    hashes = set()
    if CHORDS_DIR.is_dir():
        try:
            hashes = {n[:-5] for n in os.listdir(CHORDS_DIR) if n.endswith(".json")}
        except OSError:
            pass
    _chord_hashes_cache["set"] = hashes
    _chord_hashes_cache["ts"] = now
    return hashes


def get_batch_state() -> BatchState:
    """供其他模組查詢批次狀態（如 chord_api.chords_stats）"""
    return _batch_state


# ---------------------------------------------------------------------------
# 批次和弦偵測
# ---------------------------------------------------------------------------

def _batch_detect_worker(tracks: list, skip_existing: bool):
    """背景批次偵測所有曲目的和弦"""
    from chord_detect import detect_chords_and_key_isolated

    lock = get_task_lock()
    _batch_state.update(
        running=True, total=len(tracks), done=0,
        current_track="", succeeded=0, failed=0,
        skipped=0, errors=[],
        started_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        finished_at="",
    )
    lock.update_progress(total=len(tracks))

    CHORDS_DIR.mkdir(parents=True, exist_ok=True)

    for i, track_path in enumerate(tracks):
        _batch_state["done"] = i + 1
        _batch_state["current_track"] = track_path
        lock.update_progress(current_item=track_path, done=i + 1)

        chords_file = CHORDS_DIR / f"{song_hash(track_path)}.json"
        if skip_existing and chords_file.is_file():
            _batch_state["skipped"] += 1
            continue

        full = resolve_path(track_path)
        if not os.path.isfile(full):
            _batch_state["skipped"] += 1
            continue

        try:
            chords, key = detect_chords_and_key_isolated(full)
            sheet = {"path": track_path, "key": key, "capo": 0, "source": "btc", "chords": chords}
            chords_file.write_text(
                json.dumps(sheet, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            cache_update_entry(track_path)
            _batch_state["succeeded"] += 1
        except Exception as e:
            _batch_state["failed"] += 1
            _batch_state["errors"].append({"path": track_path, "error": str(e)})
            if len(_batch_state["errors"]) > 20:
                _batch_state["errors"] = _batch_state["errors"][-20:]

    _batch_state["done"] = len(tracks)
    _batch_state["current_track"] = ""
    _batch_state["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

    # 觸發一次同步，把所有 update_entry 寫入磁碟（單次 IO）
    if _batch_state["succeeded"] > 0:
        cache_ensure_synced(force=True)

    if _batch_state["succeeded"] > 0 and CACHE_FILE.is_file():
        try:
            # 一次 listdir 取代每檔一次 is_file()（同時捎帶修正其他歷史遺留的 stale 條目）
            chord_hashes = {n[:-5] for n in os.listdir(CHORDS_DIR) if n.endswith(".json")} if CHORDS_DIR.is_dir() else set()
            cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            modified = False
            for t in cache.get("tracks", []):
                if not t.get("has_chords") and song_hash(t.get("path", "")) in chord_hashes:
                    t["has_chords"] = True
                    modified = True
            if modified:
                tmp = CACHE_FILE.with_suffix(".tmp")
                tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
                tmp.replace(CACHE_FILE)
            _chord_hashes_cache["set"] = None  # 使 admin 列表快取失效
        except Exception:
            pass

    _batch_state["running"] = False


def _batch_thread_entry(tracks: list, skip_existing: bool):
    try:
        _batch_detect_worker(tracks, skip_existing)
    finally:
        get_task_lock().release()


@router.post("/chords/batch-detect")
async def batch_detect(
    skip_existing: bool = Query(default=True),
    genre: str = Query(default=""),
    group_id: str = Query(default=""),
):
    """啟動批次和弦偵測（背景非同步）

    可選擇範圍：
    - group_id=@<root_idx>/<label>：只處理該群組
    - genre=<prefix>：依路徑前綴過濾（向後相容）
    - 兩者皆無：處理整個音樂庫
    """
    if not CACHE_FILE.is_file():
        raise HTTPException(status_code=400, detail="請先執行音樂庫掃描")

    cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    track_dicts = cache.get("tracks", [])

    if group_id:
        track_dicts = filter_tracks_by_group(track_dicts, group_id)

    tracks = [t["path"] for t in track_dicts]

    if genre and not group_id:
        tracks = [p for p in tracks if p.lower().startswith(genre.lower())]

    if not tracks:
        return {"ok": False, "message": "找不到曲目"}

    lock = get_task_lock()
    scope = group_id or genre or "ALL"
    if not lock.try_acquire("CHORD_BATCH", scope):
        cur = lock.status() or {}
        return {"ok": False,
                "message": f"後台正在處理 {cur.get('kind', '')} ({cur.get('scope', '')})，請稍候",
                "done": _batch_state["done"], "total": _batch_state["total"]}

    t = threading.Thread(target=_batch_thread_entry, args=(tracks, skip_existing), daemon=True)
    t.start()

    return {"ok": True, "message": f"批次偵測已啟動（{len(tracks)} 首）", "total": len(tracks), "scope": scope}


@router.get("/chords/batch-detect/status")
async def batch_detect_status():
    """查詢批次偵測進度"""
    return {
        "running": _batch_state["running"],
        "total": _batch_state["total"],
        "done": _batch_state["done"],
        "current_track": _batch_state["current_track"],
        "succeeded": _batch_state["succeeded"],
        "failed": _batch_state["failed"],
        "skipped": _batch_state["skipped"],
        "errors": _batch_state["errors"][-5:],
        "started_at": _batch_state["started_at"],
        "finished_at": _batch_state["finished_at"],
    }


# ---------------------------------------------------------------------------
# 批次 MIDI 匯入
# ---------------------------------------------------------------------------

def _midi_matches(song_name: str, midi_fname: str) -> bool:
    """比對歌曲名與 MIDI 檔名是否匹配"""
    import re

    def _normalize_name(name: str) -> str:
        name = name.lower()
        name = re.sub(r"[_\-]", " ", name)
        name = re.sub(r"[^a-z0-9\s\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", "", name)
        name = re.sub(r"\s+", " ", name).strip()
        return name

    def _extract_keywords(name: str) -> set:
        stop = {"the", "a", "an", "of", "in", "at", "on", "and", "or", "for",
                "live", "official", "video", "music", "lyric", "remaster", "remastered",
                "version", "studio", "mtv", "time", "aligned", "bpm", "chordify"}
        words = set(_normalize_name(name).split())
        return {w for w in words if len(w) > 1 and w not in stop and not w.isdigit()}

    sn = _normalize_name(song_name)
    mn = _normalize_name(midi_fname.replace(".mid", "").replace(".midi", ""))
    if sn and mn and (sn in mn or mn in sn):
        return True
    sk = _extract_keywords(song_name)
    mk = _extract_keywords(midi_fname)
    if not sk or not mk:
        return False
    overlap = len(sk & mk)
    min_len = min(len(sk), len(mk))
    return overlap >= max(2, min_len * 0.6)


@router.post("/chords/batch-midi-import")
def batch_midi_import():
    """批次 MIDI 匯入：對所有尚無和弦的曲目搜尋 MIDI 並匯入"""
    from config import get_midi_root
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))
    from midi_to_lab import midi_to_lab

    midi_root = get_midi_root()
    if not os.path.isdir(midi_root):
        return {"ok": False, "message": f"MIDI 目錄不存在: {midi_root}", "imported": 0}

    if not CACHE_FILE.is_file():
        return {"ok": False, "message": "請先掃描音樂庫", "imported": 0}

    cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    CHORDS_DIR.mkdir(parents=True, exist_ok=True)

    # Build MIDI index
    midi_files = []
    for dirpath, _, filenames in os.walk(midi_root):
        for fname in filenames:
            if fname.lower().endswith(('.mid', '.midi')):
                midi_files.append((fname, os.path.join(dirpath, fname)))

    imported = 0
    skipped = 0
    errors = []

    for t in cache.get("tracks", []):
        p = t.get("path", "")
        chord_file = CHORDS_DIR / f"{song_hash(p)}.json"
        if chord_file.is_file():
            try:
                existing = json.loads(chord_file.read_text(encoding="utf-8"))
                src = existing.get("source", "") or ""
                if src in ("chordify", "midi"):
                    skipped += 1
                    continue
            except Exception:
                pass

        song_name = os.path.splitext(os.path.basename(p))[0]
        matched = None
        for mname, mfull in midi_files:
            if _midi_matches(song_name, mname):
                matched = mfull
                break

        if not matched:
            continue

        try:
            entries = midi_to_lab(matched, verbose=False)
            if not entries:
                continue
            roots = []
            for e in entries:
                c = e["chord"]
                if c and c[0] in "ABCDEFG":
                    root = c[0]
                    if len(c) > 1 and c[1] in '#b':
                        root += c[1]
                    roots.append(root)
            key = Counter(roots).most_common(1)[0][0] if roots else ""
            sheet = {"path": p, "key": key, "capo": 0, "source": "midi", "chords": entries}
            chord_file.write_text(json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8")
            cache_update_entry(p)
            imported += 1
            t["has_chords"] = True
        except Exception as e:
            errors.append({"path": p, "error": str(e)})

    if imported > 0:
        cache_ensure_synced(force=True)
        try:
            tmp = CACHE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            tmp.replace(CACHE_FILE)
        except Exception:
            pass

    return {"ok": True, "imported": imported, "skipped": skipped, "errors": errors[:10]}


# ---------------------------------------------------------------------------
# 和弦曲目管理 + 統計
# ---------------------------------------------------------------------------

@router.get("/chords/tracks")
def chords_tracks(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=100, ge=1, le=1000),
    query: str = Query(default=""),
    status: str = Query(default="all") # "all", "has_chords", "no_chords"
):
    """列出所有曲目及其和弦狀態（供 admin 管理用，支援分頁與搜尋）

    所有 has_chords / source / chord_count / key 都從 chord_cache 取得，
    避免每首曲子打開 chord JSON。
    """
    if not CACHE_FILE.is_file():
        return {"tracks": [], "total": 0, "page": 1, "limit": limit, "total_pages": 0}

    # 同步 chord_cache 與磁碟（5 秒 TTL，幾乎零成本）
    cache_ensure_synced()

    cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    all_tracks = cache.get("tracks", [])

    # 篩選：has_chords 透過 cache lookup
    if query or status != "all":
        filtered_tracks = []
        q_lower = query.lower()
        for t in all_tracks:
            p = t.get("path", "")
            if query and q_lower not in p.lower() and q_lower not in t.get("title", "").lower() and q_lower not in t.get("artist", "").lower():
                continue

            if status != "all":
                has_chords = get_chord_meta(p)["has_chords"]
                if status == "has_chords" and not has_chords:
                    continue
                if status == "no_chords" and has_chords:
                    continue
            filtered_tracks.append(t)
        all_tracks = filtered_tracks

    all_tracks.sort(key=lambda x: x.get("mtime", 0), reverse=True)

    total = len(all_tracks)
    total_pages = max(1, (total + limit - 1) // limit)
    page = min(page, total_pages)
    start_idx = (page - 1) * limit
    sliced_tracks = all_tracks[start_idx : start_idx + limit]

    result = []
    for t in sliced_tracks:
        p = t.get("path", "")
        meta = get_chord_meta(p)
        result.append({
            "path": p,
            "title": t.get("title", ""),
            "artist": t.get("artist", ""),
            "has_chords": meta["has_chords"],
            "source": meta["source"],
            "chord_count": meta["chord_count"],
            "key": meta["chord_key"],
            "mtime": t.get("mtime", 0),
        })

    return {
        "tracks": result,
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": total_pages,
    }


_stats_cache = {"data": None, "ts": 0}
_STATS_CACHE_TTL = 10


@router.get("/chords/stats")
def chords_stats():
    """和弦譜統計（只計算 library 中的曲目）— 使用快取避免阻塞"""
    now = time.time()
    if _stats_cache["data"] and now - _stats_cache["ts"] < _STATS_CACHE_TTL:
        cached = _stats_cache["data"]
        cached["batch_running"] = _batch_state.running
        return cached

    total_tracks = 0
    tracks_with_chords = 0

    if CACHE_FILE.is_file():
        cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        total_tracks = len(cache.get("tracks", []))
    if CHORDS_DIR.is_dir():
        tracks_with_chords = sum(1 for f in os.listdir(CHORDS_DIR) if f.endswith(".json"))

    result = {
        "total_tracks": total_tracks,
        "tracks_with_chords": tracks_with_chords,
        "coverage": round(tracks_with_chords / total_tracks * 100, 1) if total_tracks else 0,
        "batch_running": _batch_state.running,
    }
    _stats_cache["data"] = result
    _stats_cache["ts"] = now
    return result
