"""AI 和弦預測 + Jazzify API"""

from fastapi import APIRouter, Query, Body
from pydantic import BaseModel
from pathlib import Path
from typing import Optional

router = APIRouter(prefix="/api/ai", tags=["ai"])

DATA_DIR = Path(__file__).parent.parent / "data"
CHORDS_DIR = DATA_DIR / "chords"


@router.get("/suggest")
async def suggest(
    chords: str = Query(..., description="最近和弦，逗號分隔，例如 C,F,G"),
    key: str = Query(default="C", description="調性"),
    top_k: int = Query(default=5, description="回傳數量"),
):
    """AI 預測下一個和弦"""
    from ai.markov import get_predictor

    predictor = get_predictor(str(CHORDS_DIR))
    recent = [c.strip() for c in chords.split(",") if c.strip()]

    if not recent:
        return {"suggestions": [], "model": predictor.get_stats()}

    results = predictor.suggest(recent, key=key, top_k=top_k)
    return {
        "context": recent,
        "key": key,
        "suggestions": results,
        "model": predictor.get_stats(),
    }


@router.get("/generate")
async def generate(
    key: str = Query(default="C", description="調性"),
    length: int = Query(default=16, description="和弦數量"),
    seed: str = Query(default="", description="起始級數，例如 I"),
):
    """AI 生成和弦進行"""
    from ai.markov import get_predictor

    predictor = get_predictor(str(CHORDS_DIR))
    progression = predictor.generate(
        key=key, length=min(length, 64),
        seed=seed if seed else None,
    )
    return {"key": key, "progression": progression}


class JazzifyRequest(BaseModel):
    chords: list
    key: str = "C"
    level: int = 1


@router.post("/jazzify")
async def jazzify(body: JazzifyRequest):
    """Jazzify: 將和弦進行重配為爵士風格"""
    from ai.reharmonizer import Reharmonizer

    rh = Reharmonizer(level=body.level)
    result = rh.jazzify(body.chords, key=body.key)
    return result


@router.get("/similar")
async def similar(
    chord: str = Query(..., description="和弦級數，如 IIm7"),
    top_k: int = Query(default=5),
):
    """Chord2Vec: 找相似和弦"""
    from ai.chord2vec import get_chord2vec

    model = get_chord2vec(str(CHORDS_DIR))
    results = model.similar(chord, top_k=top_k)
    return {"chord": chord, "similar": [{"degree": d, "similarity": round(s, 3)} for d, s in results]}


@router.get("/groove")
async def groove(
    context: str = Query(default="", description="前幾個級數，逗號分隔"),
    top_k: int = Query(default=5),
):
    """Groove Dictionary: 常見循環模式"""
    from ai.groove_dict import get_groove_dict

    gd = get_groove_dict(str(CHORDS_DIR))
    if context:
        ctx = [c.strip() for c in context.split(",")]
        return {"patterns": gd.suggest_pattern(ctx, top_k)}
    else:
        return {"patterns_4": gd.top_patterns(4, top_k), "patterns_8": gd.top_patterns(8, top_k)}


@router.get("/evaluate")
async def evaluate():
    """模型評測：perplexity, accuracy"""
    from ai.evaluate import full_evaluation

    return full_evaluation(str(CHORDS_DIR))


@router.get("/melody")
async def get_melody(
    path: str = Query(..., description="歌曲路徑"),
):
    """取得旋律資料（快取或即時提取）"""
    import json as _json
    import hashlib, os

    MELODY_DIR = DATA_DIR / "melodies"
    MELODY_DIR.mkdir(parents=True, exist_ok=True)

    h = hashlib.md5(path.encode()).hexdigest()[:12]
    cache_file = MELODY_DIR / f"{h}.json"

    # 有快取直接回傳
    if cache_file.is_file():
        return _json.loads(cache_file.read_text(encoding="utf-8"))

    # 即時提取
    from config import resolve_path
    full_path = resolve_path(path)
    if not os.path.isfile(full_path):
        return {"error": "file not found", "melody": []}

    try:
        from ai.melody_extractor import MelodyExtractor
        ext = MelodyExtractor()
        melody = ext.extract_melody(full_path)

        result = {"path": path, "melody": melody}
        cache_file.write_text(_json.dumps(result, ensure_ascii=False), encoding="utf-8")
        return result
    except Exception as e:
        return {"error": str(e), "melody": []}


@router.get("/emission")
async def emission_stats(
    chord: str = Query(default="", description="和弦級數，如 I 或 V7"),
):
    """HMM 發射矩陣統計"""
    from ai.hmm import get_emission

    emission = get_emission(str(CHORDS_DIR))
    if chord:
        return {"chord": chord, "top_notes": emission.top_notes_for_chord(chord, 8)}
    return emission.get_stats()


class ViterbiRequest(BaseModel):
    melody_midi: list
    key: str = "C"
    top_k: int = 10


@router.post("/viterbi")
async def viterbi_decode(body: ViterbiRequest):
    """Viterbi 解碼：給定旋律 MIDI 序列，找最優和弦路徑"""
    from ai.hmm import get_viterbi_decoder
    from ai.preprocess import SEMI_TO_NOTE

    decoder = get_viterbi_decoder(str(CHORDS_DIR))
    path, log_prob = decoder.decode(body.melody_midi, top_k=body.top_k)

    # 將級數轉回絕對和弦
    from ai.markov import get_predictor
    predictor = get_predictor(str(CHORDS_DIR))
    chords = [predictor.degree_to_chord(d, body.key) for d in path]

    return {
        "key": body.key,
        "melody_notes": [SEMI_TO_NOTE[m % 12] for m in body.melody_midi],
        "path_degrees": path,
        "path_chords": chords,
        "log_probability": round(log_prob, 2),
    }


@router.get("/sections")
async def detect_sections_api(
    path: str = Query(..., description="歌曲路徑"),
):
    """偵測段落結構（Intro/Verse/Chorus/Bridge/Outro）"""
    import json as _json
    from ai.section_detect import detect_sections

    chords_file = CHORDS_DIR / f"{__import__('hashlib').md5(path.encode()).hexdigest()[:12]}.json"
    if not chords_file.is_file():
        return {"error": "no chord data"}

    data = _json.loads(chords_file.read_text(encoding="utf-8"))
    result = detect_sections(data.get("chords", []), data.get("key", "C"))
    result["path"] = path
    return result


@router.get("/patterns")
async def detect_patterns(
    chords: str = Query(..., description="和弦序列，逗號分隔"),
    key: str = Query(default="C"),
):
    """偵測和弦序列中的樂理 Pattern"""
    from ai.pattern_extractor import PatternExtractor

    extractor = PatternExtractor()
    chord_list = [c.strip() for c in chords.split(",")]
    results = extractor.extract_patterns(chord_list, key)
    return {"key": key, "chords": chord_list, "patterns": results}


@router.post("/retrain")
async def retrain():
    """重新訓練所有模型（含儲存快取）"""
    from ai.markov import retrain as do_retrain
    from ai.chord2vec import get_chord2vec
    from ai.groove_dict import get_groove_dict

    markov_stats = do_retrain(str(CHORDS_DIR))

    # 重建轉移矩陣（Viterbi 用）
    from ai.markov import get_predictor
    import json as _json
    predictor = get_predictor()
    trans = {}
    for state, counter in predictor.bigram.items():
        total = sum(counter.values())
        trans[state] = {s: round(c / total, 6) for s, c in counter.items()}
    trans_path = DATA_DIR / "models" / "transition.json"
    trans_path.write_text(_json.dumps({"states": list(predictor.bigram.keys()), "transitions": trans}, ensure_ascii=False), encoding="utf-8")

    # 重建 Chord2Vec
    import ai.chord2vec as c2v
    c2v._model = None
    c2v_model = get_chord2vec(str(CHORDS_DIR))

    # 重建 Groove Dict + 儲存快取（刪除舊快取強制重建）
    import ai.groove_dict as gd_mod
    gd_mod._dict = None
    if gd_mod._CACHE_FILE.is_file():
        gd_mod._CACHE_FILE.unlink()
    gd = get_groove_dict(str(CHORDS_DIR))
    gd_mod._MODELS_DIR.mkdir(parents=True, exist_ok=True)
    gd.save(str(gd_mod._CACHE_FILE))

    # 重建 Emission + 儲存快取（刪除舊快取強制重建）
    import ai.hmm as hmm_mod
    hmm_mod._emission = None
    if hmm_mod._EMISSION_CACHE.is_file():
        hmm_mod._EMISSION_CACHE.unlink()
    em = hmm_mod.get_emission(str(CHORDS_DIR))
    em.save(str(hmm_mod._EMISSION_CACHE))

    return {
        "ok": True,
        "markov": markov_stats,
        "chord2vec": c2v_model.get_stats(),
        "groove": gd.get_stats(),
    }


@router.get("/accompaniment")
def get_accompaniment(
    path: str = Query(..., description="歌曲路徑"),
    style: str = Query(default="Block", description="伴奏風格: Block/Arpeggio/Rhythm/Alberti/Shell/Walking/Stride"),
    level: str = Query(default="L1", description="難度: L1/L2/L3"),
):
    """生成伴奏（左右手 MIDI events + 指法），含快取"""
    import json as _json
    import hashlib, os

    ACC_DIR = DATA_DIR / "accompaniments"
    ACC_DIR.mkdir(parents=True, exist_ok=True)

    h = hashlib.md5(path.encode()).hexdigest()[:12]
    cache_file = ACC_DIR / f"{h}_{style}_{level}.json"

    # 快取命中
    if cache_file.is_file():
        return _json.loads(cache_file.read_text(encoding="utf-8"))

    # 載入和弦資料
    chords_file = CHORDS_DIR / f"{h}.json"
    if not chords_file.is_file():
        return {"error": "no chord data", "left_hand": [], "right_hand": []}

    chord_data = _json.loads(chords_file.read_text(encoding="utf-8"))
    chords = chord_data.get("chords", [])
    if not chords:
        return {"error": "empty chords", "left_hand": [], "right_hand": []}

    # 載入旋律快取
    melody = []
    melody_file = DATA_DIR / "melodies" / f"{h}.json"
    if melody_file.is_file():
        mel_data = _json.loads(melody_file.read_text(encoding="utf-8"))
        melody = mel_data.get("melody", mel_data if isinstance(mel_data, list) else [])

    # 取得 BPM 與 genre (從 library_cache)
    bpm = 120.0
    genre = ""
    cache_path = DATA_DIR / "library_cache.json"
    if cache_path.is_file():
        try:
            lib = _json.loads(cache_path.read_text(encoding="utf-8"))
            for t in lib.get("tracks", []):
                if t.get("path", "").replace("\\", "/") == path.replace("\\", "/"):
                    genre = t.get("genre", "")
                    dur = t.get("duration", 0)
                    if dur > 0 and chords:
                        # 估算 BPM: 中位和弦長度
                        durations = [c.get("end", 0) - c.get("time", 0)
                                     for c in chords if c.get("end", 0) > c.get("time", 0)]
                        if durations:
                            median_dur = sorted(durations)[len(durations) // 2]
                            if median_dur > 0:
                                bpm = 60.0 / median_dur
                    break
        except Exception:
            pass

    # 生成伴奏
    from ai.accompaniment_generator import generate_accompaniment

    result = generate_accompaniment(
        chords=chords, melody=melody,
        bpm=bpm, style=style, level=level, genre=genre,
    )
    result["path"] = path
    result["bpm"] = round(bpm, 1)
    result["genre"] = genre

    # 寫入快取
    try:
        cache_file.write_text(
            _json.dumps(result, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass

    return result


@router.get("/suggest-style")
def suggest_style_api(
    path: str = Query(..., description="歌曲路徑"),
):
    """根據曲風+BPM 建議伴奏風格"""
    import json as _json
    import hashlib

    h = hashlib.md5(path.encode()).hexdigest()[:12]

    bpm = 120.0
    genre = ""
    cache_path = DATA_DIR / "library_cache.json"
    if cache_path.is_file():
        try:
            lib = _json.loads(cache_path.read_text(encoding="utf-8"))
            for t in lib.get("tracks", []):
                if t.get("path", "").replace("\\", "/") == path.replace("\\", "/"):
                    genre = t.get("genre", "")
                    break
        except Exception:
            pass

    # 從和弦估算 BPM
    chords_file = CHORDS_DIR / f"{h}.json"
    if chords_file.is_file():
        try:
            chord_data = _json.loads(chords_file.read_text(encoding="utf-8"))
            chords = chord_data.get("chords", [])
            durations = [c.get("end", 0) - c.get("time", 0)
                         for c in chords if c.get("end", 0) > c.get("time", 0)]
            if durations:
                median_dur = sorted(durations)[len(durations) // 2]
                if median_dur > 0:
                    bpm = 60.0 / median_dur
        except Exception:
            pass

    from ai.accompaniment_generator import suggest_style

    return {
        "path": path,
        "genre": genre,
        "bpm": round(bpm, 1),
        "suggested_styles": suggest_style(genre, bpm),
    }


@router.get("/stats")
async def stats():
    """所有模型統計"""
    from ai.markov import get_predictor
    from ai.chord2vec import get_chord2vec
    from ai.groove_dict import get_groove_dict

    return {
        "markov": get_predictor(str(CHORDS_DIR)).get_stats(),
        "chord2vec": get_chord2vec(str(CHORDS_DIR)).get_stats(),
        "groove": get_groove_dict(str(CHORDS_DIR)).get_stats(),
    }
