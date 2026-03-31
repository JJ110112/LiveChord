"""基準測試 API — 管理 ground truth、執行比對評分"""

import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import re

router = APIRouter(prefix="/api/benchmark", tags=["benchmark"])

TEST_SONGS_DIR = Path(__file__).parent.parent / "data" / "test_songs"
LEVELS = ["Lv1", "Lv2", "Lv3", "Lv4", "Lv5"]


# ---------------------------------------------------------------------------
# 列出測試曲目
# ---------------------------------------------------------------------------

@router.get("/songs")
async def list_songs():
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
async def get_ground_truth(level: str, song: str):
    """取得某首歌的 ground truth"""
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
async def save_ground_truth(data: GroundTruthData):
    """儲存 ground truth（從前端貼上參考資料）"""
    if data.level not in LEVELS:
        raise HTTPException(400, f"無效等級: {data.level}")

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
async def run_detection(level: str, song: str):
    """對測試曲目執行 BTC 偵測，結果存為 .det.lab"""
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
async def get_detection(level: str, song: str):
    """取得偵測結果"""
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
async def score_song(level: str, song: str):
    """比對某首歌的 ground truth vs 偵測結果"""
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


@router.get("/score-all")
async def score_all():
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

        if lv_scores:
            avg_root = round(sum(s["root_accuracy"] for s in lv_scores) / len(lv_scores), 1)
            avg_full = round(sum(s["full_accuracy"] for s in lv_scores) / len(lv_scores), 1)
            key_pct = round(sum(1 for s in lv_scores if s["key_match"]) / len(lv_scores) * 100, 1)
            results[lv] = {"songs": lv_scores, "avg_root_accuracy": avg_root,
                           "avg_full_accuracy": avg_full, "key_accuracy": key_pct}

    return results
