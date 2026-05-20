"""AI 和弦預測 + Jazzify + Phase 11 教學引擎 API"""

import logging

from fastapi import APIRouter, Query, Body
from pydantic import BaseModel
from pathlib import Path
from typing import Optional, List

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai", tags=["ai"])

from chord_cache import chord_file_for, chord_bak_for, ensure_chord_bucket

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
    bpm: Optional[float] = None


@router.post("/jazzify")
async def jazzify(body: JazzifyRequest):
    """Jazzify: 將和弦進行重配為爵士風格"""
    from ai.reharmonizer import Reharmonizer

    rh = Reharmonizer(level=body.level)
    result = rh.jazzify(body.chords, key=body.key, mode=body.mode, bpm=body.bpm)
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
def get_melody(
    path: str = Query(default="", description="歌曲路徑"),
    hash: str = Query(default="", description="直接用 hash 查詢"),
):
    """取得旋律資料（快取或即時提取）"""
    import json as _json
    import hashlib, os

    MELODY_DIR = DATA_DIR / "melodies"
    MELODY_DIR.mkdir(parents=True, exist_ok=True)

    # Hash mode: direct lookup (for process results)
    if hash and not path:
        cache_file = MELODY_DIR / f"{hash}.json"
        if cache_file.is_file():
            return _json.loads(cache_file.read_text(encoding="utf-8"))
        # Try to find path from chord data and derive melody hash
        chords_file = chord_file_for(hash)
        if chords_file.is_file():
            try:
                cd = _json.loads(chords_file.read_text(encoding="utf-8"))
                if cd.get("path"):
                    from chord_cache import song_hash as get_song_hash
                    melody_hash = get_song_hash(cd["path"])
                    alt_file = MELODY_DIR / f"{melody_hash}.json"
                    if alt_file.is_file():
                        return _json.loads(alt_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"melody": []}

    if not path:
        return {"melody": []}

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
def detect_sections_api(
    path: str = Query(None, description="歌曲路徑 (可選，與 hash 二擇一)"),
    hash: str = Query(None, description="歌曲 hash (可選，與 path 二擇一；hash mode 專用)"),
    author: str = Query(None, description="要載入哪個使用者的標註 (可選)"),
    username: str = Depends(get_current_user)
):
    """偵測段落結構（Intro/Verse/Chorus/Bridge/Outro）"""
    import json as _json
    from ai.section_detect import detect_sections

    if hash:
        h = hash
    elif path:
        from chord_cache import song_hash as get_song_hash
        h = get_song_hash(path)
    else:
        return {"error": "missing path or hash"}
    chords_file = chord_file_for(h)
    if not chords_file.is_file():
        return {"error": "no chord data"}

    data = _json.loads(chords_file.read_text(encoding="utf-8"))
    
    # 決定要讀取的 Ground Truth (標註) 來源，優先讀取 author，否則讀自己的。
    # Fallback：如果該使用者沒有針對這首歌的 human_sections 標註，就用 root DATA_DIR
    # （讓 beta 使用者拿到跟 personal 相同的演算法偵測結果，不會因為 per-user dir
    #  沒 MIDI feature / annotations 就退化成單一 verse）。
    target_user = author if author else username
    user_data_dir = DATA_DIR / "users" / target_user
    user_human_sections = user_data_dir / "human_sections" / f"{h}.json"
    effective_data_dir = str(user_data_dir) if user_human_sections.is_file() else str(DATA_DIR)

    result = detect_sections(
        data.get("chords", []), data.get("key", "C"),
        song_hash=h, data_dir=effective_data_dir,
        fallback_data_dir=str(DATA_DIR),
        hint_bpm=data.get("bpm"),
    )
    result["path"] = path
    result["hash"] = h
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
def retrain():
    """重新訓練所有模型（含儲存快取）。

    Plain def (not async) so FastAPI dispatches to the worker thread pool —
    training is CPU-bound + file-I/O-heavy and would freeze the event loop
    otherwise (see feedback_async_def rule and commit 2b503b4).
    """
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


def _dedupe_hand_collisions(left_hand, right_hand, melody=None, tol=0.05):
    """Remove LH pitches that collide in time+pitch with RH or the song's melody.

    LH must not double a note that is already sounding in RH or in the melody
    (which is rendered as orange in the waterfall and is in the actual audio).
    Tolerance `tol` in seconds lets near-simultaneous onsets count as a collision.
    """
    blocker_index = {}
    for e in right_hand or []:
        pitches = e.get("pitches") or ([e["pitch"]] if "pitch" in e else [])
        start = float(e.get("time", 0.0))
        end = start + float(e.get("duration", 0.5))
        for p in pitches:
            blocker_index.setdefault(p, []).append((start, end))
    for e in melody or []:
        p = e.get("midi") if "midi" in e else e.get("pitch")
        if p is None:
            continue
        start = float(e.get("start", e.get("time", 0.0)))
        end = float(e.get("end", start + float(e.get("duration", 0.5))))
        blocker_index.setdefault(p, []).append((start, end))

    cleaned = []
    for e in left_hand:
        pitches = e.get("pitches") or ([e["pitch"]] if "pitch" in e else [])
        start = float(e.get("time", 0.0))
        end = start + float(e.get("duration", 0.5))
        kept = [
            p for p in pitches
            if not any(a - tol < end and b + tol > start for (a, b) in blocker_index.get(p, ()))
        ]
        if not kept:
            continue
        ev = dict(e)
        if "pitches" in e:
            ev["pitches"] = kept
        elif len(kept) == 1:
            ev["pitch"] = kept[0]
        else:
            ev.pop("pitch", None)
            ev["pitches"] = kept
        cleaned.append(ev)
    return cleaned


@router.get("/accompaniment")
def get_accompaniment(
    path: str = Query(..., description="歌曲路徑"),
    style: str = Query(default="Block", description="伴奏風格: Block/Arpeggio/Rhythm/Alberti/Shell/Walking/Stride"),
    level: str = Query(default="L1", description="難度: L1/L2/L3"),
    section_type: str = Query(default="default", description="段落類型: intro/verse/chorus/bridge/outro/default"),
    instrument: str = Query(default="piano", description="樂器: piano/guitar/ukulele (v6: 弦樂器產生 string-family 事件)"),
    nocache: int = Query(default=0, description="1=強制重新生成（刪除快取）"),
):
    """生成伴奏（左右手 MIDI events + 指法 + 踏板 + 力度），含快取"""
    import json as _json
    import hashlib, os

    ACC_DIR = DATA_DIR / "accompaniments"
    ACC_DIR.mkdir(parents=True, exist_ok=True)

    # v6: only piano/guitar/ukulele are valid acc backends today; anything else
    # (e.g. accordion/arranger tabs) silently falls back to the piano cache so
    # the synth still has SOMETHING to schedule.
    if instrument not in ("piano", "guitar", "ukulele"):
        instrument = "piano"

    from chord_cache import song_hash as get_song_hash
    from ai.accompaniment_generator import ACC_ENGINE_VERSION
    h = get_song_hash(path)
    cache_file = ACC_DIR / f"{h}_{style}_{level}_{section_type}_{instrument}_{ACC_ENGINE_VERSION}.json"

    # nocache: 清除此歌所有伴奏快取
    if nocache:
        import glob
        for f in ACC_DIR.glob(f"{h}_*.json"):
            try:
                f.unlink()
            except Exception:
                pass

    # 載入旋律快取（供去重使用）
    melody = []
    melody_file = DATA_DIR / "melodies" / f"{h}.json"
    if melody_file.is_file():
        mel_data = _json.loads(melody_file.read_text(encoding="utf-8"))
        melody = mel_data.get("melody", mel_data if isinstance(mel_data, list) else [])

    # 快取命中
    if cache_file.is_file():
        cached = _json.loads(cache_file.read_text(encoding="utf-8"))
        cached["left_hand"] = _dedupe_hand_collisions(
            cached.get("left_hand", []), cached.get("right_hand", []), melody
        )
        return cached

    # 載入和弦資料
    chords_file = chord_file_for(h)
    if not chords_file.is_file():
        return {"error": "no chord data", "left_hand": [], "right_hand": []}

    chord_data = _json.loads(chords_file.read_text(encoding="utf-8"))
    chords = chord_data.get("chords", [])
    if not chords:
        return {"error": "empty chords", "left_hand": [], "right_hand": []}

    # Phase 2: dynamic-beat fields. Older chord JSONs (pre Phase 1 deploy)
    # don't have these — generators fall back to scalar bpm.
    tempo_curve = chord_data.get("tempo_curve") or None
    beat_version = chord_data.get("beat_version", 0)
    time_signature = chord_data.get("time_signature") or chord_data.get("meter") or "4/4"

    # 取得 BPM 與 genre。優先順序：chord JSON 的 bpm（經 ballad-halving 修正）
    # → library_cache → 120 預設。BPM_STYLE_MAP 閾值 80/120 決定 Slow Ballad
    # vs Dance-Pop 風格，所以這裡拿修正後值才會自動切到 Arpeggio/Shell。
    bpm_persisted = chord_data.get("bpm")
    bpm_correction = chord_data.get("bpm_correction") or {}
    bpm = float(bpm_persisted) if bpm_persisted else 120.0
    genre = ""
    cache_path = DATA_DIR / "library_cache.json"
    if cache_path.is_file():
        try:
            lib = _json.loads(cache_path.read_text(encoding="utf-8"))
            for t in lib.get("tracks", []):
                if t.get("path", "").replace("\\", "/") == path.replace("\\", "/"):
                    genre = t.get("genre", "")
                    if not bpm_persisted:
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

    if bpm_correction.get("applied"):
        logger.info(
            "[accompaniment] %s using halved BPM %.1f (was %.1f, reason=%s)",
            h, bpm, bpm_correction.get("original", 0),
            bpm_correction.get("reason", ""),
        )

    # 載入段落資料 (Auto mode 用)
    sections = []
    if style == "Auto":
        try:
            from ai.section_detect import detect_sections
            sec_result = detect_sections(
                chords, chord_data.get("key", "C"),
                song_hash=h, hint_bpm=bpm_persisted,
            )
            sections = sec_result.get("sections", [])
        except Exception:
            pass

    # 生成伴奏
    from ai.accompaniment_generator import generate_accompaniment

    result = generate_accompaniment(
        chords=chords, melody=melody,
        bpm=bpm, style=style, level=level, genre=genre,
        section_type=section_type, sections=sections,
        tempo_curve=tempo_curve,
        instrument=instrument,
        time_signature=time_signature,
    )
    result["path"] = path
    result["bpm"] = round(bpm, 1)
    result["genre"] = genre
    # Phase 2: stamp source beat version so player / downstream can detect
    # stale acc when the chord JSON's beats[] has been regenerated.
    result["source_beat_version"] = beat_version

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

    # 消除左手與右手／旋律同時落在同一音高的碰撞（人手無法彈；與原曲 melody 雙擊也會糊）
    result["left_hand"] = _dedupe_hand_collisions(
        result.get("left_hand", []), result.get("right_hand", []), melody
    )

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
    time_signature = ""
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

    # 從和弦估算 BPM + 取得 time signature（若 chord JSON 有）
    chords_file = chord_file_for(h)
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
            # Time-signature hint feeds suggest_style for 3/4 / 6/8 routing.
            time_signature = chord_data.get("time_signature", "") or ""
        except Exception:
            pass

    from ai.accompaniment_generator import suggest_style

    return {
        "path": path,
        "genre": genre,
        "bpm": round(bpm, 1),
        "time_signature": time_signature,
        "suggested_styles": suggest_style(genre, bpm, time_signature),
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
    chords_file = chord_file_for(h)
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
    chords_file = chord_file_for(h)
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
    chords_file = chord_file_for(h)
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
    chords_file = chord_file_for(h)
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
