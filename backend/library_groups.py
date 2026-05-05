"""曲目群組抽象 — 將 library_cache 依「root 第一層資料夾」分組

支援兩種音樂庫結構：
- 扁平庫（如 Y:\）：根目錄下的檔案歸為「未分類」，子資料夾名稱即群組
- 階層庫（如 Z:\，路徑前綴 @N/）：取 @N/ 之後的第一層資料夾名

群組統計從現有 library_cache.json 與 chord_cache 計算，不重掃磁碟。
"""

import json
import os
import time
from pathlib import Path

from chord_cache import song_hash
from config import get_music_roots
from data_cache import get_library_cache, get_chord_hash_set

DATA_DIR = Path(__file__).parent.parent / "data"
CACHE_FILE = DATA_DIR / "library_cache.json"
CHORDS_DIR = DATA_DIR / "chords"

# list_groups() cache — invalidates when library mtime or chord hash set changes
_groups_cache = {"data": None, "lib_mtime": 0.0, "chord_ts": 0.0}

UNCATEGORIZED = "未分類"


def _split_group(rel_path: str) -> tuple[int, str, str]:
    """從相對路徑解析出 (root_idx, label, path_prefix)

    例：
      "POP/song.flac"          -> (0, "POP", "POP/")
      "song.flac"              -> (0, "未分類", "")  # 根目錄檔案
      "@1/Jazz/Miles/x.flac"   -> (1, "Jazz", "@1/Jazz/")
      "@1/x.flac"              -> (1, "未分類", "@1/")
    """
    p = rel_path.replace("\\", "/")
    root_idx = 0
    prefix = ""
    if p.startswith("@"):
        slash = p.find("/")
        if slash > 0:
            try:
                root_idx = int(p[1:slash])
            except ValueError:
                root_idx = 0
            prefix = p[: slash + 1]
            p = p[slash + 1 :]
    parts = p.split("/", 1)
    if len(parts) < 2:
        return root_idx, UNCATEGORIZED, prefix
    label = parts[0] or UNCATEGORIZED
    return root_idx, label, prefix + label + "/"


def _root_label(root_idx: int) -> str:
    roots = get_music_roots()
    if 0 <= root_idx < len(roots):
        return roots[root_idx].rstrip("/\\") or f"@{root_idx}"
    return f"@{root_idx}"


def _chord_hash_set() -> set:
    """向下相容：保留函式，內部委派給共享快取。"""
    return get_chord_hash_set()


def list_groups() -> list[dict]:
    """從 library_cache 計算所有群組與覆蓋率。
    每個已設定的 music_root 至少會回傳一筆 placeholder（即使尚無曲目），
    讓使用者在 admin 介面可以看到所有掛載的音樂庫。
    結果依 library mtime + chord_hash_set 時戳快取，避免重複迴圈 45k 曲目。
    """
    from data_cache import _lib_cache, _chord_hash_cache
    lib_mt = _lib_cache["mtime"]
    ch_ts = _chord_hash_cache["ts"]
    if (_groups_cache["data"] is not None
            and _groups_cache["lib_mtime"] == lib_mt
            and _groups_cache["chord_ts"] == ch_ts):
        return _groups_cache["data"]

    cache = get_library_cache()
    chord_hashes = get_chord_hash_set()
    groups: dict[tuple[int, str], dict] = {}

    if cache:
        for t in cache.get("tracks", []):
            path = t.get("path", "")
            if not path:
                continue
            root_idx, label, prefix = _split_group(path)
            key = (root_idx, label)
            g = groups.get(key)
            if g is None:
                g = {
                    "group_id": f"@{root_idx}/{label}",
                    "root_idx": root_idx,
                    "root_label": _root_label(root_idx),
                    "label": label,
                    "path_prefix": prefix,
                    "track_count": 0,
                    "chords_done": 0,
                }
                groups[key] = g
            g["track_count"] += 1
            if song_hash(path) in chord_hashes:
                g["chords_done"] += 1

    # 為每一個未出現任何 group 的 music_root 補上 placeholder，避免使用者
    # 因為某個音樂庫尚未掃描或暫時離線就完全看不到該根目錄。
    roots = get_music_roots()
    for idx in range(len(roots)):
        if not any(g["root_idx"] == idx for g in groups.values()):
            key = (idx, UNCATEGORIZED)
            groups[key] = {
                "group_id": f"@{idx}/{UNCATEGORIZED}",
                "root_idx": idx,
                "root_label": _root_label(idx),
                "label": UNCATEGORIZED,
                "path_prefix": "",
                "track_count": 0,
                "chords_done": 0,
            }

    # 額外：即使 scan 尚未走到某個子資料夾，只要它實際存在於磁碟上，
    # 就在 admin UI 顯示 placeholder（track_count=0），讓使用者「邊掃邊勾選」
    # 而不用等完整 scan 結束。成本：每個 root 一次淺層 listdir（十幾個項目）。
    EXCLUDE_DIRS = {"#recycle", "@eaDir", "@tmp", "#snapshot"}
    for idx, root in enumerate(roots):
        if not os.path.isdir(root):
            continue
        try:
            entries = os.listdir(root)
        except OSError:
            continue
        for name in entries:
            if name.startswith(".") or name in EXCLUDE_DIRS:
                continue
            if not os.path.isdir(os.path.join(root, name)):
                continue
            key = (idx, name)
            if key in groups:
                continue  # cache 已有 tracks，維持計算結果
            prefix = (f"@{idx}/" if idx > 0 else "") + name + "/"
            groups[key] = {
                "group_id": f"@{idx}/{name}",
                "root_idx": idx,
                "root_label": _root_label(idx),
                "label": name,
                "path_prefix": prefix,
                "track_count": 0,
                "chords_done": 0,
            }

    result = list(groups.values())
    for g in result:
        g["chords_pending"] = g["track_count"] - g["chords_done"]
        g["coverage"] = round(g["chords_done"] / g["track_count"] * 100, 1) if g["track_count"] else 0
    result.sort(key=lambda x: (x["root_idx"], x["label"]))
    _groups_cache["data"] = result
    _groups_cache["lib_mtime"] = _lib_cache["mtime"]
    _groups_cache["chord_ts"] = _chord_hash_cache["ts"]
    return result


def invalidate_groups_cache():
    """強制下次 list_groups() 重新計算。"""
    _groups_cache["data"] = None


def filter_tracks_by_group(tracks: list, group_id: str) -> list:
    """從一個 tracks 陣列中過濾屬於指定 group 的曲目

    group_id 格式：@<root_idx>/<label>
    """
    if not group_id:
        return tracks
    if not group_id.startswith("@") or "/" not in group_id:
        return []
    try:
        root_part, label = group_id[1:].split("/", 1)
        target_root = int(root_part)
    except (ValueError, IndexError):
        return []

    matched = []
    for t in tracks:
        path = t.get("path", "") if isinstance(t, dict) else t
        if not path:
            continue
        root_idx, t_label, _ = _split_group(path)
        if root_idx == target_root and t_label == label:
            matched.append(t)
    return matched
