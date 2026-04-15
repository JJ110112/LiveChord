"""AI 和弦預測 + Jazzify + Phase 11 教學引擎 API"""

from fastapi import APIRouter, Query, Body
from pydantic import BaseModel
from pathlib import Path
from typing import Optional, List

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
    mode: str = "rule-based"


@router.post("/jazzify")
async def jazzify(body: JazzifyRequest):
    """Jazzify: 將和弦進行重配為爵士風格"""
    from ai.reharmonizer import Reharmonizer

    rh = Reharmonizer(level=body.level)
    result = rh.jazzify(body.chords, key=body.key, mode=body.mode)
    return result


@router.get("/similar")
def similar(
    chord: str = Query(..., description="和弦級數，如 IIm7"),
    top_k: int = Query(default=5),
):
    """Chord2Vec: 找相似和弦"""
    try:
        from ai.chord2vec import get_similar_chords
    except ImportError as e:
        return {"chord": chord, "similar": [], "error": f"chord2vec unavailable: {e}"}
    results = get_similar_chords(chord, top_n=top_k)
    return {"chord": chord, "similar": [{"degree": d, "similarity": round(float(s), 3)} for d, s in results]}


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
def evaluate():
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

    from chord_cache import song_hash as get_song_hash
    h = get_song_hash(path)
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

class EvaluateFeedbackRequest(BaseModel):
    path: str
    action: str  # "good" or "bad"
    context: dict = {}

class SectionsFeedbackRequest(BaseModel):
    path: str
    sections: list
    


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


from auth_api import get_current_user
from fastapi import Depends

@router.get("/sections")
async def detect_sections_api(
    path: str = Query(..., description="歌曲路徑"),
    author: str = Query(None, description="要載入哪個使用者的標註 (可選)"),
    username: str = Depends(get_current_user)
):
    """偵測段落結構（Intro/Verse/Chorus/Bridge/Outro）"""
    import json as _json
    from ai.section_detect import detect_sections

    from chord_cache import song_hash as get_song_hash
    h = get_song_hash(path)
    chords_file = CHORDS_DIR / f"{h}.json"
    if not chords_file.is_file():
        return {"error": "no chord data"}

    data = _json.loads(chords_file.read_text(encoding="utf-8"))
    
    # 決定要讀取的 Ground Truth (標註) 來源，優先讀取 author，否則讀自己的
    target_user = author if author else username
    user_data_dir = DATA_DIR / "users" / target_user
    
    result = detect_sections(data.get("chords", []), data.get("key", "C"), song_hash=h, data_dir=str(user_data_dir), fallback_data_dir=str(DATA_DIR))
    result["path"] = path
    result["author"] = target_user
    return result

@router.post("/evaluate-feedback")
async def evaluate_feedback_api(body: EvaluateFeedbackRequest, username: str = Depends(get_current_user)):
    """(RLHF) 接收和弦星星評分並附加至紀錄檔"""
    import json as _json
    from datetime import datetime
    
    user_dir = DATA_DIR / "users" / username / "human_feedback"
    user_dir.mkdir(parents=True, exist_ok=True)
    file_path = user_dir / "chord_eval.jsonl"
    
    import hashlib
    from chord_cache import song_hash as get_song_hash
    song_hash = get_song_hash(body.path)
    
    record = {
        "timestamp": datetime.now().isoformat(),
        "path": body.path,
        "song_hash": song_hash,
        "action": body.action,
        "context": body.context
    }
    with open(file_path, "a", encoding="utf-8") as f:
        f.write(_json.dumps(record, ensure_ascii=False) + "\n")
    return {"status": "success", "message": "Feedback recorded"}

@router.post("/sections/feedback")
async def sections_feedback_api(body: SectionsFeedbackRequest, username: str = Depends(get_current_user)):
    """(RLHF) 接收使用者人工修正的樂句並作為 Ground Truth 保存"""
    import json as _json
    import hashlib
    from chord_cache import song_hash as get_song_hash
    song_hash = get_song_hash(body.path)
    
    user_dir = DATA_DIR / "users" / username / "human_sections"
    user_dir.mkdir(parents=True, exist_ok=True)
    file_path = user_dir / f"{song_hash}.json"
    
    data = {
        "path": body.path,
        "song_hash": song_hash,
        "human_labeled": True,
        "sections": body.sections
    }
    with open(file_path, "w", encoding="utf-8") as f:
        _json.dump(data, f, ensure_ascii=False, indent=2)
    return {"status": "success", "message": f"Sections for {song_hash} saved"}

@router.get("/human_sections/authors")
async def get_human_section_authors(path: str = Query(..., description="歌曲路徑")):
    """List all users who have created a ground truth entry for this song."""
    import hashlib
    from chord_cache import song_hash as get_song_hash
    song_hash = get_song_hash(path)
    
    users_dir = DATA_DIR / "users"
    if not users_dir.exists():
        return {"authors": []}
        
    authors = []
    for user_folder in users_dir.iterdir():
        if user_folder.is_dir():
            target_file = user_folder / "human_sections" / f"{song_hash}.json"
            if target_file.exists():
                authors.append(user_folder.name)
                
    return {"authors": authors}


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
    section_type: str = Query(default="default", description="段落類型: intro/verse/chorus/bridge/outro/default"),
    nocache: int = Query(default=0, description="1=強制重新生成（刪除快取）"),
):
    """生成伴奏（左右手 MIDI events + 指法 + 踏板 + 力度），含快取"""
    import json as _json
    import hashlib, os

    ACC_DIR = DATA_DIR / "accompaniments"
    ACC_DIR.mkdir(parents=True, exist_ok=True)

    from chord_cache import song_hash as get_song_hash
    h = get_song_hash(path)
    cache_file = ACC_DIR / f"{h}_{style}_{level}_{section_type}.json"

    # nocache: 清除此歌所有伴奏快取
    if nocache:
        import glob
        for f in ACC_DIR.glob(f"{h}_*.json"):
            try:
                f.unlink()
            except Exception:
                pass

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

    # 載入段落資料 (Auto mode 用)
    sections = []
    if style == "Auto":
        try:
            from ai.section_detect import detect_sections
            sec_result = detect_sections(chords, chord_data.get("key", "C"), song_hash=h)
            sections = sec_result.get("sections", [])
        except Exception:
            pass

    # 生成伴奏
    from ai.accompaniment_generator import generate_accompaniment

    result = generate_accompaniment(
        chords=chords, melody=melody,
        bpm=bpm, style=style, level=level, genre=genre,
        section_type=section_type, sections=sections,
    )
    result["path"] = path
    result["bpm"] = round(bpm, 1)
    result["genre"] = genre

    # Phase 11: 踏板建議
    try:
        from ai.pedal_advisor import generate_pedal_suggestions
        result["pedal"] = generate_pedal_suggestions(
            chords, melody=melody, bpm=bpm,
            style="rhythmic" if section_type == "chorus" else "legato",
        )
    except Exception:
        result["pedal"] = []

    # Phase 11: 力度表情 — 分手處理以保留左右手音量平衡
    try:
        from ai.dynamics_engine import generate_dynamics
        generate_dynamics(result["left_hand"], bpm=int(bpm), section_type=section_type)
        generate_dynamics(result["right_hand"], bpm=int(bpm), section_type=section_type)
    except Exception:
        pass

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

    from chord_cache import song_hash as get_song_hash
    h = get_song_hash(path)

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
def stats():
    """所有模型統計"""
    from ai.markov import get_predictor
    from ai.groove_dict import get_groove_dict

    result = {
        "markov": get_predictor(str(CHORDS_DIR)).get_stats(),
        "groove": get_groove_dict(str(CHORDS_DIR)).get_stats(),
    }
    try:
        from pathlib import Path as _P
        models_dir = _P(CHORDS_DIR).parent / "models"
        emb = models_dir / "chord_embeddings.npy"
        vocab = models_dir / "vocab.json"
        if emb.exists() and vocab.exists():
            import json as _json, numpy as _np
            v = _json.loads(vocab.read_text(encoding="utf-8"))
            arr = _np.load(emb, mmap_mode="r")
            result["chord2vec"] = {"vocab_size": len(v), "embedding_dim": int(arr.shape[1])}
        else:
            result["chord2vec"] = {"status": "not_trained"}
    except Exception as e:
        result["chord2vec"] = {"error": str(e)}
    return result


# ==============================================================================
# Phase 11: AI 鋼琴教師 V2 端點
# ==============================================================================

@router.get("/evaluate-melody")
def evaluate_melody_api(
    path: str = Query(..., description="歌曲路徑"),
    voice: str = Query(default="default", description="聲部: soprano/alto/tenor/bass/piano_rh/piano_lh/default"),
):
    """旋律品質評測（音域/跳進比/樂句弧/節奏/置信度）"""
    import json as _json, hashlib

    from chord_cache import song_hash as get_song_hash
    h = get_song_hash(path)
    melody_file = DATA_DIR / "melodies" / f"{h}.json"
    if not melody_file.is_file():
        return {"error": "no melody data", "overall_score": 0}

    mel_data = _json.loads(melody_file.read_text(encoding="utf-8"))
    melody = mel_data.get("melody", mel_data if isinstance(mel_data, list) else [])
    if not melody:
        return {"error": "empty melody", "overall_score": 0}

    from ai.melody_evaluator import evaluate_melody
    result = evaluate_melody(melody, voice=voice)
    result["path"] = path
    return result


@router.get("/evaluate-accompaniment")
def evaluate_accompaniment_api(
    path: str = Query(..., description="歌曲路徑"),
    style: str = Query(default="Block"),
    level: str = Query(default="L1"),
):
    """伴奏品質評測（碰撞/voice leading/音域/密度/和聲）"""
    import json as _json, hashlib

    from chord_cache import song_hash as get_song_hash
    h = get_song_hash(path)

    # 載入伴奏快取
    acc_file = DATA_DIR / "accompaniments" / f"{h}_{style}_{level}_default.json"
    if not acc_file.is_file():
        acc_file = DATA_DIR / "accompaniments" / f"{h}_{style}_{level}.json"
    if not acc_file.is_file():
        return {"error": "no accompaniment data, generate first", "overall_score": 0}

    acc_data = _json.loads(acc_file.read_text(encoding="utf-8"))
    left_hand = acc_data.get("left_hand", [])
    right_hand = acc_data.get("right_hand", [])

    # 載入旋律
    melody = []
    melody_file = DATA_DIR / "melodies" / f"{h}.json"
    if melody_file.is_file():
        mel_data = _json.loads(melody_file.read_text(encoding="utf-8"))
        melody = mel_data.get("melody", [])

    # 載入和弦
    chords = []
    chords_file = CHORDS_DIR / f"{h}.json"
    if chords_file.is_file():
        chord_data = _json.loads(chords_file.read_text(encoding="utf-8"))
        chords = chord_data.get("chords", [])

    bpm = acc_data.get("bpm", 120)

    from ai.accompaniment_evaluator import evaluate_accompaniment
    result = evaluate_accompaniment(left_hand, right_hand, melody, bpm=int(bpm), chords=chords)
    result["path"] = path
    result["style"] = style
    result["level"] = level
    return result


@router.get("/pedal")
def pedal_api(
    path: str = Query(..., description="歌曲路徑"),
    style: str = Query(default="legato", description="踏板風格: legato/rhythmic/half"),
):
    """AI 踏板建議"""
    import json as _json, hashlib

    from chord_cache import song_hash as get_song_hash
    h = get_song_hash(path)
    chords_file = CHORDS_DIR / f"{h}.json"
    if not chords_file.is_file():
        return {"error": "no chord data", "pedal": []}

    chord_data = _json.loads(chords_file.read_text(encoding="utf-8"))
    chords = chord_data.get("chords", [])

    melody = []
    melody_file = DATA_DIR / "melodies" / f"{h}.json"
    if melody_file.is_file():
        mel_data = _json.loads(melody_file.read_text(encoding="utf-8"))
        melody = mel_data.get("melody", [])

    from ai.pedal_advisor import generate_pedal_suggestions, evaluate_pedal
    pedals = generate_pedal_suggestions(chords, melody=melody, bpm=120, style=style)
    score = evaluate_pedal(pedals, chords)

    return {"path": path, "style": style, "pedal": pedals, "evaluation": score}


@router.get("/dynamics")
def dynamics_api(
    path: str = Query(..., description="歌曲路徑"),
    style: str = Query(default="Block"),
    level: str = Query(default="L1"),
    section_type: str = Query(default="verse"),
):
    """AI 力度表情（velocity + articulation）"""
    import json as _json, hashlib

    from chord_cache import song_hash as get_song_hash
    h = get_song_hash(path)

    # 載入伴奏快取
    for suffix in [f"{style}_{level}_{section_type}", f"{style}_{level}_default", f"{style}_{level}"]:
        acc_file = DATA_DIR / "accompaniments" / f"{h}_{suffix}.json"
        if acc_file.is_file():
            break
    else:
        return {"error": "no accompaniment data, generate first"}

    acc_data = _json.loads(acc_file.read_text(encoding="utf-8"))
    events = acc_data.get("left_hand", []) + acc_data.get("right_hand", [])

    from ai.dynamics_engine import generate_dynamics, evaluate_dynamics
    generate_dynamics(events, bpm=int(acc_data.get("bpm", 120)), section_type=section_type)
    score = evaluate_dynamics(events)

    return {
        "path": path,
        "section_type": section_type,
        "total_events": len(events),
        "evaluation": score,
        "sample_events": events[:10],
    }


@router.get("/qa-battle")
def qa_battle_api(
    path: str = Query(..., description="歌曲路徑"),
    style: str = Query(default="Block"),
    level: str = Query(default="L1"),
):
    """QA Battle: 綜合品質評測 (所有 evaluator 對抗)"""
    import json as _json, hashlib

    from chord_cache import song_hash as get_song_hash
    h = get_song_hash(path)

    # 載入和弦
    chords_file = CHORDS_DIR / f"{h}.json"
    if not chords_file.is_file():
        return {"error": "no chord data", "verdict": "fail"}
    chord_data = _json.loads(chords_file.read_text(encoding="utf-8"))
    chords = chord_data.get("chords", [])

    # 載入旋律
    melody = []
    melody_file = DATA_DIR / "melodies" / f"{h}.json"
    if melody_file.is_file():
        mel_data = _json.loads(melody_file.read_text(encoding="utf-8"))
        melody = mel_data.get("melody", [])

    # 載入或生成伴奏
    acc_file = DATA_DIR / "accompaniments" / f"{h}_{style}_{level}_default.json"
    if not acc_file.is_file():
        acc_file = DATA_DIR / "accompaniments" / f"{h}_{style}_{level}.json"

    if acc_file.is_file():
        acc = _json.loads(acc_file.read_text(encoding="utf-8"))
    else:
        from ai.accompaniment_generator import generate_accompaniment
        acc = generate_accompaniment(chords, melody, bpm=120, style=style, level=level)

    # 補踏板
    if not acc.get("pedal"):
        from ai.pedal_advisor import generate_pedal_suggestions
        acc["pedal"] = generate_pedal_suggestions(chords, melody=melody, bpm=120)

    from ai.battle_qa import run_full_qa
    result = run_full_qa(chords, melody, acc, bpm=int(acc.get("bpm", 120)), level=level)
    result["path"] = path
    return result


@router.get("/section-context")
def section_context_api(
    path: str = Query(..., description="歌曲路徑"),
):
    """取得歌曲的段落結構 + 各段落的 AI 建議參數"""
    import json as _json, hashlib

    from chord_cache import song_hash as get_song_hash
    h = get_song_hash(path)
    chords_file = CHORDS_DIR / f"{h}.json"
    if not chords_file.is_file():
        return {"error": "no chord data"}

    chord_data = _json.loads(chords_file.read_text(encoding="utf-8"))
    chords = chord_data.get("chords", [])

    from ai.section_detect import detect_sections
    sections_result = detect_sections(chords, chord_data.get("key", "C"), song_hash=h)
    sections = sections_result.get("sections", [])

    from ai.section_context import build_section_timeline
    total_dur = chords[-1].get("end", 0) if chords else 0
    timeline = build_section_timeline(sections, chords, total_dur)

    return {
        "path": path,
        "sections": sections,
        "timeline_sample": timeline[:20],
        "total_sections": len(sections),
    }
