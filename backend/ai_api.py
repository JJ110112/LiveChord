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
    from config import get_music_root
    full_path = os.path.join(get_music_root(), path)
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
    from ai.hmm import build_emission_from_songs

    emission = build_emission_from_songs(str(CHORDS_DIR))
    if chord:
        return {"chord": chord, "top_notes": emission.top_notes_for_chord(chord, 8)}
    return emission.get_stats()


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
    """重新訓練所有模型"""
    from ai.markov import retrain as do_retrain
    from ai.chord2vec import get_chord2vec
    from ai.groove_dict import get_groove_dict

    markov_stats = do_retrain(str(CHORDS_DIR))

    # 重建 Chord2Vec
    import ai.chord2vec as c2v
    c2v._model = None
    c2v_model = get_chord2vec(str(CHORDS_DIR))

    # 重建 Groove Dict
    import ai.groove_dict as gd_mod
    gd_mod._dict = None
    gd = get_groove_dict(str(CHORDS_DIR))

    return {
        "ok": True,
        "markov": markov_stats,
        "chord2vec": c2v_model.get_stats(),
        "groove": gd.get_stats(),
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
