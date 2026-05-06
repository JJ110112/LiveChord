"""
和弦摘要快取模組

維護一份 chords/*.json 的記憶體索引，避免 admin pagination / chord 列表
每次都對 70k+ 個 JSON 做 stat + open。

每個條目（key 為 song_hash）包含：
  - unique_chords: 不重複的和弦數量
  - chord_key:     主調
  - chord_list:    不重複的和弦清單
  - source:        "btc" / "midi" / "chordify" / ""
  - chord_count:   原始事件數（不去重）
  - mtime:         產生此條目時的 chord JSON mtime（用於失效判定）
"""

import atexit
import json
import os
import re
import time
import hashlib
from pathlib import Path

# Paths
DATA_DIR = Path(__file__).parent.parent / "data"
CHORDS_DIR = DATA_DIR / "chords"
INDEX_FILE = DATA_DIR / "chord_index.json"

# Demo songs ship in the repo with their chord JSONs at data/demo/chords/<hash>.json
# (flat, not sharded — only ~5 files). chord_file_for() falls back to this dir when
# the regular sharded path doesn't exist, so /api/chords/by-hash transparently
# serves demos without a separate code path.
DEMO_CHORDS_DIR = DATA_DIR / "demo" / "chords"

_chord_index_cache: dict | None = None

# ensure_synced 的節流：30 秒內重複呼叫直接跳過。對 NAS 上 50k+ 檔做 scandir
# 本身就要好幾秒，每次 admin 列表請求都掃描會把回應時間拉到 7 秒以上。
_sync_state = {"last_sync_ts": 0.0}
_SYNC_TTL = 30.0

# Index-save 節流：chord_index.json 可能達 10+ MB，每次 cache-miss 都寫整份
# 會把 /api/recent / /api/favorites 的延遲拉到秒級（壓測時平均 5 秒）。
# 改成 30 秒內最多寫一次；行程結束前 atexit 強制 flush 保證不遺失。
_save_state = {"last_save_ts": 0.0, "dirty": False}
_SAVE_TTL = 30.0


def song_hash(path: str) -> str:
    """產生穩定的 song hash（統一將反斜線轉為正斜線，同時移除 @N/ 前綴以相容舊版單一路徑的 hash）"""
    path = path.replace("\\", "/")
    path = re.sub(r"^@\d+/", "", path)
    return hashlib.md5(path.encode("utf-8")).hexdigest()[:12]


# ----------------------------------------------------------------------
# Sharded chord layout: <root>/<hash[:2]>/<hash>.json
# ----------------------------------------------------------------------
# Hash is 12-char hex → first 2 chars give 256 uniform buckets, ~300 files
# per bucket at 76k songs. NTFS / SMB enumeration scales much better than
# a single 200k-file flat directory once .bak.<src> sidecars accumulate.
#
# All chord JSON path construction across the backend MUST go through these
# helpers (do not rebuild ``CHORDS_DIR / f"{h}.json"`` directly). Likewise
# all bulk iteration MUST go through ``iter_chord_files`` so adding/changing
# the bucket scheme later only touches this module.

def chord_file_for(hash_str: str, root: Path | None = None) -> Path:
    """Return the sharded chord JSON path for ``hash_str``.

    Falls back to the demo dir (``data/demo/chords/<hash>.json``) when the
    sharded path doesn't exist — lets shipped demo songs be served via the
    same hash-mode endpoints without writes ever leaking into the demo dir
    (writes go through ensure_chord_bucket + the sharded path returned here,
    which is only the demo path on cache *miss* — i.e. when no real song
    has claimed that hash, which is statistically guaranteed for the
    handful of __demo/<id> hashes shipped in the repo).
    """
    base = root or CHORDS_DIR
    sharded = base / hash_str[:2] / f"{hash_str}.json"
    if not sharded.is_file():
        demo = DEMO_CHORDS_DIR / f"{hash_str}.json"
        if demo.is_file():
            return demo
    return sharded


def chord_bak_for(hash_str: str, src: str, root: Path | None = None) -> Path:
    """Return the sharded ``.bak.<src>`` snapshot path for ``hash_str``.

    ``src`` is the tracker name (``librosa`` / ``madmom`` / ``beat_this``)
    or any other suffix used by the bidirectional switch / upgrade flows.
    """
    base = root or CHORDS_DIR
    return base / hash_str[:2] / f"{hash_str}.json.bak.{src}"


def iter_chord_files(root: Path | None = None):
    """Yield every sharded chord JSON Path under ``root`` (excludes .bak)."""
    base = root or CHORDS_DIR
    if not base.is_dir():
        return
    for sub in base.iterdir():
        if not sub.is_dir() or len(sub.name) != 2:
            continue
        for f in sub.glob("*.json"):
            # Defensive: glob("*.json") matches "<hash>.json" but also any
            # ".json.bak.X" doesn't end with .json, so we're safe. But pin
            # down by explicit suffix check in case future bak naming drifts.
            if f.suffix == ".json":
                yield f


def ensure_chord_bucket(hash_str: str, root: Path | None = None) -> Path:
    """Make sure the bucket dir exists; return the bucket Path.

    Idempotent. Use before atomic writes to a sharded chord JSON.
    """
    base = root or CHORDS_DIR
    bucket = base / hash_str[:2]
    bucket.mkdir(parents=True, exist_ok=True)
    return bucket


def _load_chord_index():
    """載入或初始化全域快取"""
    global _chord_index_cache
    if _chord_index_cache is None:
        if INDEX_FILE.is_file():
            try:
                _chord_index_cache = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
            except Exception:
                _chord_index_cache = {}
        else:
            _chord_index_cache = {}


def _save_chord_index(force: bool = False):
    """將快取寫回硬碟。預設 30 秒最多寫一次；force=True 無條件寫（行程退出時用）。"""
    if _chord_index_cache is None:
        return
    now = time.time()
    if not force and (now - _save_state["last_save_ts"] < _SAVE_TTL):
        _save_state["dirty"] = True
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = INDEX_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(_chord_index_cache, ensure_ascii=False), encoding="utf-8")
    tmp.replace(INDEX_FILE)
    _save_state["last_save_ts"] = now
    _save_state["dirty"] = False


# Flush pending updates on process exit so we don't lose the last <30s of changes.
atexit.register(lambda: _save_chord_index(force=True) if _save_state.get("dirty") else None)


def _build_entry_from_file(h: str, mtime: float) -> dict | None:
    """從硬碟讀取單一 chord JSON 並建構索引條目"""
    chords_file = chord_file_for(h)
    try:
        cdata = json.loads(chords_file.read_text(encoding="utf-8"))
    except Exception:
        return None
    chords = cdata.get("chords", [])
    unique = sorted({c["chord"] for c in chords if c.get("chord") and c["chord"] != "N"})
    return {
        "unique_chords": len(unique),
        "chord_key": cdata.get("key", ""),
        "chord_list": unique,
        "source": (cdata.get("source") or "btc"),
        "chord_count": len(chords),
        "mtime": mtime,
    }


def _entry_is_complete(entry: dict) -> bool:
    """判斷條目是否包含新欄位（用於 schema 升級偵測）"""
    return "source" in entry and "chord_count" in entry


def get_chord_summary(path: str) -> dict:
    """取得和弦摘要：unique count, key, chord list。內建 mtime 驗證的快取機制"""
    _load_chord_index()
    h = song_hash(path)
    chords_file = chord_file_for(h)

    if not chords_file.is_file():
        return {"unique_chords": 0, "chord_key": "", "chord_list": []}

    try:
        mtime = os.path.getmtime(chords_file)
    except OSError:
        mtime = 0

    cached = _chord_index_cache.get(h)
    if cached and cached.get("mtime") == mtime and _entry_is_complete(cached):
        return {
            "unique_chords": cached.get("unique_chords", 0),
            "chord_key": cached.get("chord_key", ""),
            "chord_list": cached.get("chord_list", []),
        }

    entry = _build_entry_from_file(h, mtime)
    if entry is None:
        return {"unique_chords": 0, "chord_key": "", "chord_list": []}
    _chord_index_cache[h] = entry
    _save_chord_index()
    return {
        "unique_chords": entry["unique_chords"],
        "chord_key": entry["chord_key"],
        "chord_list": entry["chord_list"],
    }


def get_chord_meta(path: str) -> dict:
    """快速取得和弦 metadata（不含 chord_list）

    回傳 has_chords / source / chord_count / chord_key — admin 列表用。
    呼叫者應在進入時呼叫 ensure_synced() 以確保快取與磁碟一致。
    """
    _load_chord_index()
    h = song_hash(path)
    entry = _chord_index_cache.get(h)
    if not entry:
        return {"has_chords": False, "source": "", "chord_count": 0, "chord_key": ""}
    return {
        "has_chords": True,
        "source": entry.get("source") or "btc",
        "chord_count": entry.get("chord_count", 0),
        "chord_key": entry.get("chord_key", ""),
    }


def ensure_synced(force: bool = False, progress_cb=None) -> dict:
    """以一次 scandir 同步索引到當前磁碟狀態

    - 5 秒 TTL：頻繁呼叫不會重複 IO
    - 第一次 / schema 升級：可能需要讀取數萬個 JSON，可透過 progress_cb 回報
    - 移除：磁碟上不存在的 hash 從快取剔除
    - 新增 / 更新：mtime 不同或欄位缺失 → 重讀
    """
    _load_chord_index()
    now = time.time()
    if not force and now - _sync_state["last_sync_ts"] < _SYNC_TTL:
        return {"checked": 0, "added": 0, "updated": 0, "removed": 0, "skipped": True}

    if not CHORDS_DIR.is_dir():
        _sync_state["last_sync_ts"] = now
        return {"checked": 0, "added": 0, "updated": 0, "removed": 0, "skipped": False}

    # Walk sharded layout: <CHORDS_DIR>/<bucket>/<hash>.json
    disk: dict[str, float] = {}
    try:
        with os.scandir(CHORDS_DIR) as buckets:
            for b in buckets:
                if not b.is_dir() or len(b.name) != 2:
                    continue
                try:
                    with os.scandir(b.path) as it:
                        for entry in it:
                            name = entry.name
                            if not name.endswith(".json") or ".json.bak." in name:
                                continue
                            try:
                                disk[name[:-5]] = entry.stat().st_mtime
                            except OSError:
                                pass
                except OSError:
                    continue
    except OSError:
        _sync_state["last_sync_ts"] = now
        return {"checked": 0, "added": 0, "updated": 0, "removed": 0, "skipped": False}

    cache_hashes = set(_chord_index_cache.keys())
    disk_hashes = set(disk.keys())

    removed = 0
    for h in cache_hashes - disk_hashes:
        _chord_index_cache.pop(h, None)
        removed += 1

    # 找出真正需要重讀的條目
    pending: list[tuple[str, float]] = []
    for h, mtime in disk.items():
        cached = _chord_index_cache.get(h)
        if cached and cached.get("mtime") == mtime and _entry_is_complete(cached):
            continue
        pending.append((h, mtime))

    added = updated = 0
    total_pending = len(pending)
    if progress_cb and total_pending:
        progress_cb(0, total_pending)
    # 大型 migration 需要週期性落盤，避免中途中斷時整批進度遺失
    CHECKPOINT_EVERY = 2000
    for i, (h, mtime) in enumerate(pending, 1):
        existed = h in _chord_index_cache
        entry = _build_entry_from_file(h, mtime)
        if entry is None:
            continue
        _chord_index_cache[h] = entry
        if existed:
            updated += 1
        else:
            added += 1
        if progress_cb and i % 500 == 0:
            progress_cb(i, total_pending)
        if i % CHECKPOINT_EVERY == 0:
            _save_chord_index()
    if progress_cb and total_pending:
        progress_cb(total_pending, total_pending)

    _sync_state["last_sync_ts"] = now
    if added or updated or removed:
        _save_chord_index()

    return {
        "checked": len(disk),
        "added": added,
        "updated": updated,
        "removed": removed,
        "skipped": False,
    }


def invalidate(path: str):
    """從快取中刪除單一條目（後續 ensure_synced 或 update_entry_from_file 會補回）"""
    _load_chord_index()
    h = song_hash(path)
    if _chord_index_cache.pop(h, None) is not None:
        _save_chord_index()


def update_entry_from_file(path: str):
    """重讀指定路徑的 chord JSON，直接更新快取條目（給 batch worker 寫完後使用）"""
    _load_chord_index()
    h = song_hash(path)
    chords_file = chord_file_for(h)
    if not chords_file.is_file():
        if _chord_index_cache.pop(h, None) is not None:
            _save_chord_index()
        return
    try:
        mtime = os.path.getmtime(chords_file)
    except OSError:
        return
    entry = _build_entry_from_file(h, mtime)
    if entry is None:
        return
    _chord_index_cache[h] = entry
    # 不在每首寫入後都 _save_chord_index — batch worker 會在最後做一次 ensure_synced 觸發落盤
