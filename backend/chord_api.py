"""和弦 API — 和弦資訊、和弦圖、和弦譜 CRUD、自動偵測、批次偵測"""

import json
import os
import re
import time
import asyncio
from pathlib import Path


def _normalize_name(name: str) -> str:
    """正規化名稱：轉小寫、移除標點、連字號/底線→空格、壓縮空白、保留 CJK"""
    name = name.lower()
    name = re.sub(r"[_\-]", " ", name)            # 底線/連字號→空格
    name = re.sub(r"[^a-z0-9\s\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", "", name)  # 保留 CJK/日/韓
    name = re.sub(r"\s+", " ", name).strip()       # 壓縮空白
    return name


def _extract_keywords(name: str) -> set:
    """提取名稱中有意義的關鍵字（去掉短詞和常見噪音詞）"""
    stop = {"the", "a", "an", "of", "in", "at", "on", "and", "or", "for",
            "live", "official", "video", "music", "lyric", "remaster", "remastered",
            "version", "studio", "mtv", "time", "aligned", "bpm", "chordify"}
    words = set(_normalize_name(name).split())
    return {w for w in words if len(w) > 1 and w not in stop and not w.isdigit()}


def _midi_matches(song_name: str, midi_fname: str) -> bool:
    """比對歌曲名與 MIDI 檔名是否匹配"""
    sn = _normalize_name(song_name)
    mn = _normalize_name(midi_fname.replace(".mid", "").replace(".midi", ""))
    # 雙向子字串包含（兩邊都必須非空才比對）
    if sn and mn and (sn in mn or mn in sn):
        return True
    # 關鍵字交集 >= 60% 的較短方
    sk = _extract_keywords(song_name)
    mk = _extract_keywords(midi_fname)
    if not sk or not mk:
        return False
    overlap = len(sk & mk)
    min_len = min(len(sk), len(mk))
    return overlap >= max(2, min_len * 0.6)

from fastapi import APIRouter, HTTPException, Query, UploadFile, File
from pydantic import BaseModel

from chord_table import get_chord_info, get_chord_jianpu, analyze_chord_in_key
from chord_diagrams import get_chord_diagram, get_chord_voicings
from chord_cache import song_hash
from instrument_registry import get_instrument, list_instruments, INSTRUMENTS

from config import resolve_path

router = APIRouter(prefix="/api", tags=["chord"])

DATA_DIR = Path(__file__).parent.parent / "data"
CHORDS_DIR = DATA_DIR / "chords"
CACHE_FILE = DATA_DIR / "library_cache.json"




# ---------------------------------------------------------------------------
# instrument registry
# ---------------------------------------------------------------------------

@router.get("/instruments")
def instruments_api():
    """回傳所有已註冊樂器及其 metadata"""
    return {name: meta for name, meta in INSTRUMENTS.items()}


# ---------------------------------------------------------------------------
# chord info / diagram
# ---------------------------------------------------------------------------

@router.get("/chord/info/{name:path}")
async def chord_info(name: str):
    """取得和弦資訊（組成音、簡譜）"""
    info = get_chord_info(name)
    if not info or not info.get("notes"):
        raise HTTPException(status_code=404, detail=f"未知和弦: {name}")
    return info


@router.get("/chord/diagram/{instrument}/{name:path}")
async def chord_diagram(instrument: str, name: str):
    """取得和弦圖（吉他/烏克麗麗/...）"""
    if instrument not in list_instruments():
        raise HTTPException(status_code=400, detail=f"instrument 須為 {', '.join(list_instruments())}")
    diagram = get_chord_diagram(name, instrument=instrument)
    if not diagram:
        raise HTTPException(status_code=404, detail=f"無 {instrument} 和弦圖: {name}")
    return diagram


@router.get("/chord/voicings/{instrument}/{name:path}")
def chord_voicings_api(instrument: str, name: str):
    """取得和弦所有把位指法"""
    if instrument not in list_instruments():
        raise HTTPException(status_code=400, detail=f"instrument 須為 {', '.join(list_instruments())}")
    voicings = get_chord_voicings(name, instrument=instrument)
    inst_meta = get_instrument(instrument)
    num_strings = inst_meta["num_strings"] if inst_meta else 6
    return {"name": name, "numStrings": num_strings, "voicings": voicings}


@router.get("/chord/analysis/{key}/{name:path}")
def chord_analysis_api(key: str, name: str):
    """回傳和弦在調性中的級數分析"""
    result = analyze_chord_in_key(key, name)
    return result


# ---------------------------------------------------------------------------
# chord sheet CRUD
# ---------------------------------------------------------------------------

class ChordSheet(BaseModel):
    path: str
    key: str = ""
    capo: int = 0
    chords: list = []


@router.get("/chords")
async def get_chords(path: str = Query(...)):
    """取得某首歌的和弦譜"""
    chords_file = CHORDS_DIR / f"{song_hash(path)}.json"
    if not chords_file.is_file():
        return {"path": path, "key": "", "capo": 0, "chords": [], "exists": False}

    data = json.loads(chords_file.read_text(encoding="utf-8"))
    data["exists"] = True
    return data


@router.post("/chords")
async def save_chords(sheet: ChordSheet):
    """儲存和弦譜"""
    CHORDS_DIR.mkdir(parents=True, exist_ok=True)
    chords_file = CHORDS_DIR / f"{song_hash(sheet.path)}.json"
    chords_file.write_text(
        json.dumps(sheet.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {"ok": True, "path": sheet.path}


# ---------------------------------------------------------------------------
# auto-detect (Phase 4)
# ---------------------------------------------------------------------------

@router.post("/chords/detect")
async def detect_chords_api(path: str = Query(...)):
    """自動偵測音訊中的和弦，偵測完成後自動儲存"""
    full = resolve_path(path)
    if not os.path.isfile(full):
        raise HTTPException(status_code=404, detail="檔案不存在")

    # 如果已有 chordify 來源的和弦，不要用 BTC 覆蓋
    chords_file = CHORDS_DIR / f"{song_hash(path)}.json"
    if chords_file.is_file():
        existing = json.loads(chords_file.read_text(encoding="utf-8"))
        if existing.get("source") == "chordify":
            return {
                "ok": True, "path": path,
                "key": existing.get("key", ""),
                "chord_count": len(existing.get("chords", [])),
                "chords": existing.get("chords", []),
                "source": "chordify (已有高品質和弦，跳過 BTC 偵測)",
            }

    try:
        from chord_detect import detect_chords_and_key_isolated
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"缺少依賴: {e}")

    def _sync_detect(audio_path):
        """同步偵測（在子程序中執行，不阻塞 event loop）"""
        chords, key = detect_chords_and_key_isolated(audio_path)

        # [Fallback] 如果 BTC 無法辨識（例如 8-bit chiptune 等非常規軌道），降級使用 Melody-to-Chord Viterbi 解析
        if not chords:
            print(f"[Fallback] BTC 失敗，啟動 Viterbi Melody-to-Chord 管道...")
            from ai.melody_extractor import MelodyExtractor
            from ai.hmm import get_viterbi_decoder
            from ai.markov import get_predictor

            extractor = MelodyExtractor()
            melody_events = extractor.extract_melody(audio_path)
            if melody_events:
                midi_sequence = [evt["midi"] for evt in melody_events]
                decoder = get_viterbi_decoder(str(CHORDS_DIR))
                path_degrees, _ = decoder.decode(midi_sequence, top_k=20)

                predictor = get_predictor(str(CHORDS_DIR))
                current_chord = None
                for i, evt in enumerate(melody_events):
                    chord_name = predictor.degree_to_chord(path_degrees[i], key)
                    if chord_name != current_chord:
                        chords.append({
                            "time": evt["start"],
                            "end": evt["end"],
                            "chord": chord_name
                        })
                        current_chord = chord_name
                    else:
                        chords[-1]["end"] = evt["end"]

        return chords, key

    try:
        chords, key = await asyncio.to_thread(_sync_detect, full)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"偵測失敗: {e}")

    # 自動儲存
    sheet = {
        "path": path,
        "key": key,
        "capo": 0,
        "source": "btc",
        "chords": chords,
    }
    CHORDS_DIR.mkdir(parents=True, exist_ok=True)
    chords_file = CHORDS_DIR / f"{song_hash(path)}.json"
    chords_file.write_text(
        json.dumps(sheet, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "ok": True,
        "path": path,
        "key": key,
        "chord_count": len(chords),
        "chords": chords,
        "source": "btc",
    }


# ---------------------------------------------------------------------------
# MIDI 匯入
# ---------------------------------------------------------------------------

@router.get("/chords/midi-search")
def midi_search(path: str = Query(...)):
    """搜尋 X:\\ 中與此曲目名稱相符的 MIDI 檔案"""
    from config import get_midi_root
    midi_root = get_midi_root()

    song_name = os.path.splitext(os.path.basename(path))[0]

    results = []
    if os.path.isdir(midi_root):
        for dirpath, dirnames, filenames in os.walk(midi_root):
            # 跳過回收站等隱藏資料夾
            dirnames[:] = [d for d in dirnames if not d.startswith(('#', '.', '@'))]
            for fname in filenames:
                if not fname.lower().endswith(('.mid', '.midi')):
                    continue
                if _midi_matches(song_name, fname):
                    rel = os.path.relpath(os.path.join(dirpath, fname), midi_root).replace("\\", "/")
                    results.append({"name": fname, "path": rel})

    return {"song": song_name, "midi_root": midi_root, "results": results}


@router.post("/chords/midi-import")
def midi_import(path: str = Query(...), midi_path: str = Query(...)):
    """從 MIDI 檔案匯入和弦，儲存到 chords/{hash}.json"""
    from config import get_midi_root
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))
    from midi_to_lab import midi_to_lab

    midi_root = get_midi_root()
    full_midi = os.path.normpath(os.path.join(midi_root, midi_path))

    if not os.path.isfile(full_midi):
        raise HTTPException(status_code=404, detail=f"MIDI 檔案不存在: {midi_path}")

    try:
        entries = midi_to_lab(full_midi, verbose=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"MIDI 解析失敗: {e}")

    if not entries:
        raise HTTPException(status_code=400, detail="MIDI 無法解析出和弦")

    # 推導 key
    from collections import Counter
    roots = []
    for e in entries:
        c = e["chord"]
        if c and c[0] in "ABCDEFG":
            root = c[0]
            if len(c) > 1 and c[1] in '#b':
                root += c[1]
            roots.append(root)
    key = Counter(roots).most_common(1)[0][0] if roots else ""

    # 驗證：比對 MIDI key 與音檔 key 是否一致
    key_mismatch = False
    audio_key = ""
    try:
        full_audio = resolve_path(path)
        if os.path.isfile(full_audio):
            from chord_detect import detect_chords_and_key_isolated
            btc_chords, audio_key = detect_chords_and_key_isolated(full_audio)
            if audio_key and key:
                from .preprocess import NOTE_TO_SEMI
                midi_semi = NOTE_TO_SEMI.get(key.rstrip("m"), -1)
                audio_semi = NOTE_TO_SEMI.get(audio_key.rstrip("m"), -1)
                if midi_semi >= 0 and audio_semi >= 0 and midi_semi != audio_semi:
                    key_mismatch = True
    except Exception:
        btc_chords = []

    # key 不一致 → fallback BTC（已在上面一併偵測，不需再跑一次）
    if key_mismatch and btc_chords:
        sheet = {"path": path, "key": audio_key, "capo": 0,
                 "source": "btc", "chords": btc_chords}
        CHORDS_DIR.mkdir(parents=True, exist_ok=True)
        chords_file = CHORDS_DIR / f"{song_hash(path)}.json"
        chords_file.write_text(json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "ok": True, "path": path, "key": audio_key,
            "chord_count": len(btc_chords), "source": "btc",
            "warning": f"MIDI 調性不符（MIDI={key}, 音檔={audio_key}），已改用 BTC 偵測",
        }

    # 儲存 MIDI 結果
    sheet = {
        "path": path, "key": key, "capo": 0,
        "source": "midi", "chords": entries,
    }
    CHORDS_DIR.mkdir(parents=True, exist_ok=True)
    chords_file = CHORDS_DIR / f"{song_hash(path)}.json"
    chords_file.write_text(json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "ok": True, "path": path, "key": key,
        "chord_count": len(entries), "source": "midi",
        "midi_file": midi_path,
    }


@router.post("/chords/midi-upload")
async def midi_upload(path: str = Query(...), file: UploadFile = File(...)):
    """使用者上傳 MIDI 檔案 → 儲存到 X:\\ → 匯入和弦"""
    from config import get_midi_root
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))
    from midi_to_lab import midi_to_lab
    from collections import Counter

    midi_root = get_midi_root()
    os.makedirs(midi_root, exist_ok=True)

    # 儲存上傳的 MIDI 到 X:\
    fname = file.filename or "uploaded.mid"
    dest = os.path.join(midi_root, fname)
    content = await file.read()
    with open(dest, "wb") as f:
        f.write(content)

    # 解析和弦
    try:
        entries = midi_to_lab(dest, verbose=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"MIDI 解析失敗: {e}")

    if not entries:
        raise HTTPException(status_code=400, detail="MIDI 無法解析出和弦")

    roots = []
    for e in entries:
        c = e["chord"]
        if c and c[0] in "ABCDEFG":
            root = c[0]
            if len(c) > 1 and c[1] in '#b':
                root += c[1]
            roots.append(root)
    key = Counter(roots).most_common(1)[0][0] if roots else ""

    sheet = {"path": path, "key": key, "capo": 0, "source": "midi", "chords": entries}
    CHORDS_DIR.mkdir(parents=True, exist_ok=True)
    chords_file = CHORDS_DIR / f"{song_hash(path)}.json"
    chords_file.write_text(json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "ok": True, "path": path, "key": key,
        "chord_count": len(entries), "source": "midi",
        "midi_file": fname,
    }


# 批次偵測、批次 MIDI 匯入、和弦曲目管理、和弦統計 → chord_batch.py
