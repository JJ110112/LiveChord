"""基準測試 API — 管理 ground truth、執行比對評分"""

import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import re

from chord_cache import song_hash, chord_file_for

router = APIRouter(prefix="/api/benchmark", tags=["benchmark"])

DATA_DIR = Path(__file__).parent.parent / "data"
TEST_SONGS_DIR = DATA_DIR / "test_songs"
CHORDS_DIR = DATA_DIR / "chords"
LIBRARY_CACHE_FILE = DATA_DIR / "library_cache.json"

LIBRARY_LEVEL = "library"
LIBRARY_GT_DIR = TEST_SONGS_DIR / LIBRARY_LEVEL  # GT 寫在 data/test_songs/library/{hash}.lab

LEVELS = ["Lv1", "Lv2", "Lv3", "Lv4", "Lv5"]
ALL_LEVELS = LEVELS + [LIBRARY_LEVEL]


def _is_library(level: str) -> bool:
    return level == LIBRARY_LEVEL


def _library_gt_path(song_id: str) -> Path:
    """library mode 下，song_id 即 song_hash"""
    LIBRARY_GT_DIR.mkdir(parents=True, exist_ok=True)
    return LIBRARY_GT_DIR / f"{song_id}.lab"


def _library_cache_path(song_id: str) -> Path:
    """library mode 下的偵測來源 — 直接讀現成 chord cache"""
    return chord_file_for(song_id)


def _list_library_songs() -> list:
    """從 library_cache.json 拉所有掃描完成的曲目，以 song_hash 當 id"""
    if not LIBRARY_CACHE_FILE.is_file():
        return []
    try:
        cache = json.loads(LIBRARY_CACHE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    LIBRARY_GT_DIR.mkdir(parents=True, exist_ok=True)
    songs = []
    for tr in cache.get("tracks", []):
        p = tr.get("path", "")
        if not p:
            continue
        h = song_hash(p)
        songs.append({
            "name": tr.get("title") or Path(p).stem,
            "file": p,
            "level": LIBRARY_LEVEL,
            "hash": h,
            "has_ground_truth": (LIBRARY_GT_DIR / f"{h}.lab").is_file(),
            "has_detection": chord_file_for(h).is_file(),
        })
    songs.sort(key=lambda s: s["name"].lower())
    return songs


# ---------------------------------------------------------------------------
# 列出測試曲目
# ---------------------------------------------------------------------------

@router.get("/songs")
def list_songs():
    """列出所有測試曲目（按等級分組）"""
    result = {}
    for lv in LEVELS:
        lv_dir = TEST_SONGS_DIR / lv
        if not lv_dir.is_dir():
            result[lv] = []
            continue

        songs = []
        for f in sorted(lv_dir.iterdir()):
            if f.suffix.lower() == ".flac":
                name = f.stem
                # 檢查是否有 ground truth
                gt_file = lv_dir / f"{name}.lab"
                # 檢查是否有偵測結果
                det_file = lv_dir / f"{name}.det.lab"
                songs.append({
                    "name": name,
                    "file": f.name,
                    "level": lv,
                    "has_ground_truth": gt_file.is_file(),
                    "has_detection": det_file.is_file(),
                })
        result[lv] = songs

    # 主音樂庫 — 把 library_cache 內已掃描的曲目接出來，QA 可直接點用
    result[LIBRARY_LEVEL] = _list_library_songs()

    return result


# ---------------------------------------------------------------------------
# Ground Truth CRUD（.lab 格式）
# ---------------------------------------------------------------------------

class GroundTruthEntry(BaseModel):
    time: float
    end: float
    chord: str

class GroundTruthData(BaseModel):
    level: str
    song: str
    entries: list[GroundTruthEntry]  # [{"time": 0.0, "end": 4.5, "chord": "Cm7"}, ...]
    source: str = ""  # 參考來源（Chordify, Real Book 等）
    key: str = ""


@router.get("/ground-truth/{level}/{song}")
def get_ground_truth(level: str, song: str):
    """取得某首歌的 ground truth（library mode 下 song 即 song_hash）"""
    if _is_library(level):
        gt_file = _library_gt_path(song)
    else:
        gt_file = TEST_SONGS_DIR / level / f"{song}.lab"

    if not gt_file.is_file():
        return {"exists": False, "entries": [], "source": "", "key": ""}

    try:
        data = json.loads(gt_file.read_text(encoding="utf-8"))
        data["exists"] = True
        return data
    except (json.JSONDecodeError, IOError) as e:
        raise HTTPException(500, f"讀取 ground truth 檔案失敗: {str(e)}")


@router.post("/ground-truth")
def save_ground_truth(data: GroundTruthData):
    """儲存 ground truth（從前端貼上參考資料）"""
    if data.level not in ALL_LEVELS:
        raise HTTPException(400, f"無效等級: {data.level}")

    if _is_library(data.level):
        LIBRARY_GT_DIR.mkdir(parents=True, exist_ok=True)
        lv_dir = LIBRARY_GT_DIR
    else:
        lv_dir = TEST_SONGS_DIR / data.level
        if not lv_dir.is_dir():
            raise HTTPException(400, f"目錄不存在: {data.level}")

    # 驗證時間戳記的合理性和順序
    for i, entry in enumerate(data.entries):
        if entry.time < 0 or entry.end < 0:
            raise HTTPException(400, f"時間戳記不能為負數 (entry {i})")
        if entry.time >= entry.end:
            raise HTTPException(400, f"開始時間必須小於結束時間 (entry {i})")
        if i > 0 and entry.time < data.entries[i-1].end:
            raise HTTPException(400, f"時間區間重疊 (entry {i})")
        # 驗證和弦格式
        if not entry.chord or not entry.chord.strip():
            raise HTTPException(400, f"和弦不能為空 (entry {i})")
            
        chord_pattern = r'^[A-G][#b]?(m|maj|min|dim|aug|sus[24]?|add[0-9]|[0-9]+|M)?[0-9]*(\/[A-G][#b]?)?$|^N$'
        if not re.match(chord_pattern, entry.chord.strip()):
            valid_chords = set(['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B', 'N', 'X'])
            if ':' in entry.chord:
                root, quality = entry.chord.split(':', 1)
                if root not in valid_chords or quality not in ['maj', 'min', '7', 'maj7', 'min7', 'dim', 'aug', 'sus2', 'sus4']:
                    raise HTTPException(400, f"無效和弦格式: {entry.chord} (entry {i})")
            else:
                if entry.chord not in valid_chords:
                    raise HTTPException(400, f"無效的和弦格式: {entry.chord} (entry {i})")

    gt_file = lv_dir / f"{data.song}.lab"
    gt_data = {
        "song": data.song,
        "level": data.level,
        "key": data.key,
        "source": data.source,
        "entries": [entry.dict() for entry in data.entries],
    }
    
    try:
        gt_file.write_text(json.dumps(gt_data, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "path": str(gt_file)}
    except IOError as e:
        raise HTTPException(500, f"檔案寫入失敗: {str(e)}")


# ---------------------------------------------------------------------------
# 偵測並存為 .det.lab
# ---------------------------------------------------------------------------

@router.post("/detect/{level}/{song}")
def run_detection(level: str, song: str):
    """對測試曲目執行偵測。

    library mode：song 是 song_hash，直接讀現成 chord cache，不重跑 BTC。
    Lv1~Lv5：對 .flac 跑 chord_detect 並寫 .det.lab。
    """
    if _is_library(level):
        chord_json = _library_cache_path(song)
        if not chord_json.is_file():
            raise HTTPException(404, f"chord cache 不存在: {song}")
        try:
            data = json.loads(chord_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            raise HTTPException(500, f"讀取 chord cache 失敗: {str(e)}")
        chords = data.get("chords", [])
        key = data.get("key", "")
        return {
            "ok": True, "key": key, "chord_count": len(chords),
            "entries": chords, "from_cache": True,
        }

    flac = TEST_SONGS_DIR / level / f"{song}.flac"
    if not flac.is_file():
        raise HTTPException(404, f"找不到: {flac.name}")

    from chord_detect import detect_chords, detect_key
    key = detect_key(str(flac))
    chords = detect_chords(str(flac))

    det_data = {"song": song, "level": level, "key": key, "entries": chords}
    det_file = TEST_SONGS_DIR / level / f"{song}.det.lab"
    det_file.write_text(json.dumps(det_data, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"ok": True, "key": key, "chord_count": len(chords), "entries": chords}


@router.get("/detection/{level}/{song}")
def get_detection(level: str, song: str):
    """取得偵測結果（library mode 從 chord cache 即時組裝）"""
    if _is_library(level):
        chord_json = _library_cache_path(song)
        if not chord_json.is_file():
            return {"exists": False, "entries": [], "key": ""}
        try:
            data = json.loads(chord_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"exists": False, "entries": [], "key": ""}
        return {
            "exists": True,
            "entries": data.get("chords", []),
            "key": data.get("key", ""),
            "from_cache": True,
        }

    det_file = TEST_SONGS_DIR / level / f"{song}.det.lab"
    if not det_file.is_file():
        return {"exists": False, "entries": [], "key": ""}

    data = json.loads(det_file.read_text(encoding="utf-8"))
    data["exists"] = True
    return data


# ---------------------------------------------------------------------------
# 評分比對
# ---------------------------------------------------------------------------

_ROOT_MAP = {
    "C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4,
    "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9,
    "A#": 10, "Bb": 10, "B": 11,
}


def _parse_root(chord: str) -> int:
    if not chord or chord == "N":
        return -1
    if len(chord) >= 2 and chord[1] in ("#", "b"):
        return _ROOT_MAP.get(chord[:2], -1)
    return _ROOT_MAP.get(chord[:1], -1)


def _parse_quality(chord: str) -> str:
    if not chord or chord == "N":
        return ""
    if len(chord) >= 2 and chord[1] in ("#", "b"):
        return chord[2:]
    return chord[1:]


def _score_segment(gt_entries: list, det_entries: list, tolerance: float = 1.0):
    """
    逐段比對 ground truth 與偵測結果。

    對 ground truth 的每個片段，找出偵測結果中時間重疊最多的片段，
    計算：
    - root_correct: 根音相同（考慮 enharmonic: C#=Db）
    - quality_correct: 品質相同
    - full_correct: 完全正確
    - overlap_ratio: 時間重疊比例

    回傳每段的詳細分數 + 總分。
    """
    details = []
    total_dur = 0
    root_correct_dur = 0
    full_correct_dur = 0

    for gt in gt_entries:
        gt_start = gt["time"]
        gt_end = gt.get("end", gt_start + 2.0)
        gt_chord = gt["chord"]
        gt_dur = gt_end - gt_start
        total_dur += gt_dur

        gt_root = _parse_root(gt_chord)
        gt_qual = _parse_quality(gt_chord)

        # 找所有重疊的偵測片段
        best_det = None
        best_overlap = 0
        for det in det_entries:
            d_start = det["time"]
            d_end = det.get("end", d_start + 2.0)
            overlap_start = max(gt_start, d_start)
            overlap_end = min(gt_end, d_end)
            overlap = max(0, overlap_end - overlap_start)
            if overlap > best_overlap:
                best_overlap = overlap
                best_det = det

        if best_det is None:
            details.append({
                "time": gt_start, "end": gt_end,
                "gt_chord": gt_chord, "det_chord": "N",
                "root_match": False, "quality_match": False, "full_match": False,
                "overlap": 0,
            })
            continue

        det_chord = best_det["chord"]
        det_root = _parse_root(det_chord)
        det_qual = _parse_quality(det_chord)

        root_match = (gt_root == det_root) and (gt_root >= 0)
        quality_match = (gt_qual == det_qual)
        full_match = root_match and quality_match

        if root_match:
            root_correct_dur += gt_dur
        if full_match:
            full_correct_dur += gt_dur

        details.append({
            "time": gt_start, "end": gt_end,
            "gt_chord": gt_chord, "det_chord": det_chord,
            "root_match": root_match, "quality_match": quality_match,
            "full_match": full_match,
            "overlap": round(best_overlap, 2),
        })

    root_acc = round(root_correct_dur / total_dur * 100, 1) if total_dur > 0 else 0
    full_acc = round(full_correct_dur / total_dur * 100, 1) if total_dur > 0 else 0

    return {
        "total_segments": len(gt_entries),
        "total_duration": round(total_dur, 1),
        "root_accuracy": root_acc,
        "full_accuracy": full_acc,
        "details": details,
    }


@router.get("/score/{level}/{song}")
def score_song(level: str, song: str):
    """比對某首歌的 ground truth vs 偵測結果（library mode 偵測來源 = chord cache）"""
    if _is_library(level):
        gt_file = _library_gt_path(song)
        if not gt_file.is_file():
            raise HTTPException(400, "尚無 ground truth，請先貼上參考資料")
        cache_file = _library_cache_path(song)
        if not cache_file.is_file():
            raise HTTPException(400, "尚無偵測結果（chord cache 不存在）")
        gt = json.loads(gt_file.read_text(encoding="utf-8"))
        cache = json.loads(cache_file.read_text(encoding="utf-8"))
        det = {"key": cache.get("key", ""), "entries": cache.get("chords", [])}
    else:
        gt_file = TEST_SONGS_DIR / level / f"{song}.lab"
        det_file = TEST_SONGS_DIR / level / f"{song}.det.lab"

        if not gt_file.is_file():
            raise HTTPException(400, "尚無 ground truth，請先貼上參考資料")
        if not det_file.is_file():
            raise HTTPException(400, "尚無偵測結果，請先執行偵測")

        gt = json.loads(gt_file.read_text(encoding="utf-8"))
        det = json.loads(det_file.read_text(encoding="utf-8"))

    # Key 比對
    gt_key_root = _parse_root(gt.get("key", ""))
    det_key_root = _parse_root(det.get("key", ""))
    key_match = (gt_key_root == det_key_root) and (gt_key_root >= 0)

    # 和弦比對
    result = _score_segment(gt.get("entries", []), det.get("entries", []))
    result["key_gt"] = gt.get("key", "")
    result["key_det"] = det.get("key", "")
    result["key_match"] = key_match

    return result


def _summarise_scores(lv_scores: list) -> dict:
    if not lv_scores:
        return {}
    avg_root = round(sum(s["root_accuracy"] for s in lv_scores) / len(lv_scores), 1)
    avg_full = round(sum(s["full_accuracy"] for s in lv_scores) / len(lv_scores), 1)
    key_pct = round(sum(1 for s in lv_scores if s["key_match"]) / len(lv_scores) * 100, 1)
    return {
        "songs": lv_scores,
        "avg_root_accuracy": avg_root,
        "avg_full_accuracy": avg_full,
        "key_accuracy": key_pct,
    }


@router.get("/score-all")
def score_all():
    """計算所有等級的總分"""
    results = {}
    for lv in LEVELS:
        lv_dir = TEST_SONGS_DIR / lv
        if not lv_dir.is_dir():
            continue

        lv_scores = []
        for f in sorted(lv_dir.glob("*.lab")):
            if f.name.endswith(".det.lab"):
                continue
            song = f.stem
            det_file = lv_dir / f"{song}.det.lab"
            if not det_file.is_file():
                continue

            gt = json.loads(f.read_text(encoding="utf-8"))
            det = json.loads(det_file.read_text(encoding="utf-8"))
            score = _score_segment(gt.get("entries", []), det.get("entries", []))

            gt_key_root = _parse_root(gt.get("key", ""))
            det_key_root = _parse_root(det.get("key", ""))

            lv_scores.append({
                "song": song,
                "key_match": (gt_key_root == det_key_root) and (gt_key_root >= 0),
                "root_accuracy": score["root_accuracy"],
                "full_accuracy": score["full_accuracy"],
            })

        summary = _summarise_scores(lv_scores)
        if summary:
            results[lv] = summary

    # library 等級 — 以 song_hash 為 key，用 chord cache 當偵測來源
    if LIBRARY_GT_DIR.is_dir():
        lib_scores = []
        for gt_file in sorted(LIBRARY_GT_DIR.glob("*.lab")):
            if gt_file.name.endswith(".det.lab"):
                continue
            song_id = gt_file.stem
            cache_file = _library_cache_path(song_id)
            if not cache_file.is_file():
                continue
            try:
                gt = json.loads(gt_file.read_text(encoding="utf-8"))
                cache = json.loads(cache_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            det = {"key": cache.get("key", ""), "entries": cache.get("chords", [])}
            score = _score_segment(gt.get("entries", []), det.get("entries", []))
            gt_key_root = _parse_root(gt.get("key", ""))
            det_key_root = _parse_root(det.get("key", ""))
            lib_scores.append({
                "song": song_id,
                "key_match": (gt_key_root == det_key_root) and (gt_key_root >= 0),
                "root_accuracy": score["root_accuracy"],
                "full_accuracy": score["full_accuracy"],
            })
        summary = _summarise_scores(lib_scores)
        if summary:
            results[LIBRARY_LEVEL] = summary

    return results
