"""Chord-progression song search — powers the Progression Library "matching
songs" feature.

Given a roman-numeral progression (degree + major/minor tokens), scans the
prebuilt progression index (data/progression_index.json, see
tools/build_progression_index.py) and returns library songs that contain the
progression in any key. Matching is transposition-invariant and key-field
independent (see progression_match.py).

Read-only. On deployments without an index file (e.g. the public VPS, which has
no NAS library) every query simply returns zero matches.
"""
import json
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from auth_api import get_current_user

import progression_match as pm

router = APIRouter()

DATA_DIR = Path(__file__).parent.parent / "data"
INDEX_FILE = DATA_DIR / "progression_index.json"

# Lazy in-memory cache of the index, invalidated by file mtime so a rebuilt
# index is picked up without a restart.
_cache = {"mtime": -1.0, "songs": None}

# Short-lived cache for /trends responses, keyed by the deduped seq. Cleared
# whenever the underlying index mtime changes (see _load_index).
_trends_cache: dict[str, dict] = {}
_trends_cache_mtime = -1.0

# Roman-numeral labels by relative degree (degree 0 = the start tonic).
# Lowercased by the caller when the chord is minor-family.
_ROMAN_BASE = {
    0: "I", 1: "♭II", 2: "II", 3: "♭III", 4: "III", 5: "IV",
    6: "♭V", 7: "V", 8: "♭VI", 9: "VI", 10: "♭VII", 11: "VII",
}


def _load_index():
    global _trends_cache_mtime
    try:
        mt = INDEX_FILE.stat().st_mtime
    except OSError:
        _cache["songs"] = []
        _cache["mtime"] = -1.0
        return _cache["songs"]
    if _cache["songs"] is not None and _cache["mtime"] == mt:
        return _cache["songs"]
    try:
        data = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        _cache["songs"] = data.get("songs", [])
        _cache["mtime"] = mt
    except (OSError, json.JSONDecodeError):
        _cache["songs"] = []
        _cache["mtime"] = -1.0
    # The index changed (or first load) — drop any stale trends responses.
    if _trends_cache_mtime != _cache["mtime"]:
        _trends_cache.clear()
        _trends_cache_mtime = _cache["mtime"]
    return _cache["songs"]


def _dedupe_consecutive(tokens):
    """Drop consecutive duplicate (deg, minor) tokens, matching the packing in
    progression_match.chords_to_packed so a query lines up with the song data."""
    out = []
    prev = None
    for deg, minor in tokens:
        cur = (deg % 12, bool(minor))
        if cur != prev:
            out.append(cur)
            prev = cur
    return out


def _parse_seq(seq: str):
    """"0M-7M-9m-5M" -> [(0, False), (7, False), (9, True), (5, False)]."""
    tokens = []
    for part in seq.split("-"):
        part = part.strip()
        if len(part) < 2:
            continue
        q = part[-1]
        try:
            deg = int(part[:-1])
        except ValueError:
            continue
        # Lowercase 'm' = minor, uppercase 'M' = major. Do NOT lowercase here.
        tokens.append((deg % 12, q == "m"))
    return tokens


@router.get("/api/progression/match")
def match_progression(
    seq: str = Query(..., description="degree+M/m tokens, e.g. 0M-7M-9m-5M"),
    limit: int = Query(24, ge=1, le=60),
    offset: int = Query(0, ge=0, description="page offset into the match list"),
):
    tokens = _parse_seq(seq)
    if len(tokens) < 2:
        raise HTTPException(status_code=400, detail="progression must have at least 2 chords")
    variants = pm.query_variants(tokens)
    songs = _load_index()
    t0 = time.time()
    matches = [s for s in songs if pm.song_matches(s[3], variants)]
    page = matches[offset:offset + limit]
    out = [
        {"hash": s[0], "title": s[1], "key": s[2], "path": s[4] if len(s) > 4 else ""}
        for s in page
    ]
    return {
        "count": len(matches),
        "offset": offset,
        "limit": limit,
        "indexed": len(songs),
        "took_ms": round((time.time() - t0) * 1000, 1),
        "songs": out,
    }


# Cap on how many next-chord candidates we return — beyond ~12 the long tail is
# noise no one drills into.
_TRENDS_TOP_N = 12


@router.get("/api/progression/trends")
def progression_trends(
    seq: str = Query(..., description="relative degrees from the start tonic, e.g. 0M-7M-9m"),
):
    """Next-chord probability distribution for a progression path.

    The path is a sequence of degrees RELATIVE to the chord you started from
    (degree 0 = the tonic 'I'). For every library song we slide all 12
    transpositions of the path over the song's key-free packed sequence; each
    physical occurrence is counted once (it matches exactly one transposition),
    and the chord that immediately follows it is tallied as a relative degree.
    The result is a transposition-invariant Markov step pooled over the corpus —
    same key-free philosophy as /api/progression/match.
    """
    tokens = _dedupe_consecutive(_parse_seq(seq))
    if not tokens:
        raise HTTPException(status_code=400, detail="progression must have at least 1 chord")

    songs = _load_index()
    cache_key = "-".join(f"{d}{'m' if m else 'M'}" for d, m in tokens)
    cached = _trends_cache.get(cache_key)
    if cached is not None:
        return cached

    variants = pm.query_variants(tokens)  # 12 transposed packed strings of the full path
    plen = len(variants[0]) if variants else 0
    counts: dict[tuple, int] = {}
    total = 0
    t0 = time.time()

    if plen:
        for s in songs:
            packed = s[3]
            slen = len(packed)
            for t, v in enumerate(variants):
                start = 0
                find = packed.find
                while True:
                    i = find(v, start)
                    if i < 0:
                        break
                    j = i + plen  # index of the chord following the matched run
                    if j < slen:
                        nv = ord(packed[j]) - 48
                        key = ((nv >> 1) - t) % 12, nv & 1
                        counts[key] = counts.get(key, 0) + 1
                        total += 1
                    # Advance by 1 so overlapping occurrences of a repeating path
                    # (e.g. I-V-I-V) are each counted as a distinct transition.
                    start = i + 1

    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:_TRENDS_TOP_N]
    nxt = [
        {
            "deg": deg,
            "minor": bool(minor),
            "roman": _ROMAN_BASE[deg].lower() if minor else _ROMAN_BASE[deg],
            "count": c,
            "prob": round(c / total, 4) if total else 0.0,
        }
        for (deg, minor), c in ranked
    ]
    result = {
        "indexed": len(songs),
        "path_len": len(tokens),
        "total": total,
        "next": nxt,
        "took_ms": round((time.time() - t0) * 1000, 1),
    }
    _trends_cache[cache_key] = result
    return result


# ---------------------------------------------------------------------------
# Custom progressions — user-collected progressions (personal use). Stored in
# data/custom_progressions.json (tier1 backup). Chord specs mirror the frontend
# LIBRARY shape: [degree 0-11, quality, optional bass degree].
# ---------------------------------------------------------------------------
import os
import re
import threading
import uuid

from pydantic import BaseModel, Field

CUSTOM_FILE = DATA_DIR / "custom_progressions.json"
_custom_lock = threading.Lock()

_CUSTOM_QUALITIES = {
    "maj", "m", "dim", "aug", "maj7", "m7", "7", "m7b5", "dim7", "maj9", "m9",
    "9", "7sus4", "6", "m6", "add9", "6/9", "11", "m11", "13", "sus2", "sus4",
    "7b9", "7#9", "7b5", "7#5", "7b13", "7#11", "maj7#11", "m7b9", "mmaj7", "9sus4", "m6/9",
}
_CUSTOM_MAX = 200


class CustomProgressionIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    chords: list = Field(..., min_length=2, max_length=32)
    desc: str = Field("", max_length=500)
    source_url: str = Field("", max_length=500)
    input_text: str = Field("", max_length=300)
    input_key: str = Field("", max_length=4)


def _read_custom() -> list:
    try:
        data = json.loads(CUSTOM_FILE.read_text(encoding="utf-8"))
        items = data.get("items", [])
        return items if isinstance(items, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _write_custom(items: list) -> None:
    CUSTOM_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CUSTOM_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"version": 1, "items": items}, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, CUSTOM_FILE)


def _normalize_chords(chords) -> list:
    out = []
    for spec in chords:
        if not isinstance(spec, (list, tuple)) or len(spec) < 2:
            raise HTTPException(status_code=400, detail="each chord must be [degree, quality]")
        try:
            deg = int(spec[0]) % 12
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="chord degree must be an integer")
        q = str(spec[1])
        if q not in _CUSTOM_QUALITIES:
            raise HTTPException(status_code=400, detail=f"unsupported chord quality: {q}")
        item = [deg, q]
        if len(spec) > 2 and spec[2] is not None:
            try:
                item.append(int(spec[2]) % 12)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="bass degree must be an integer")
        out.append(item)
    return out


def _validate_custom(body: CustomProgressionIn) -> dict:
    url = body.source_url.strip()
    if url and not re.match(r"^https?://", url, re.I):
        raise HTTPException(status_code=400, detail="source_url must start with http:// or https://")
    return {
        "name": body.name.strip(),
        "chords": _normalize_chords(body.chords),
        "desc": body.desc.strip(),
        "source_url": url,
        "input_text": body.input_text.strip(),
        "input_key": body.input_key.strip(),
    }


@router.get("/api/progression/custom")
def list_custom_progressions():
    return {"items": _read_custom()}


@router.post("/api/progression/custom")
def create_custom_progression(body: CustomProgressionIn):
    fields = _validate_custom(body)
    with _custom_lock:
        items = _read_custom()
        if len(items) >= _CUSTOM_MAX:
            raise HTTPException(status_code=400, detail=f"limit of {_CUSTOM_MAX} custom progressions reached")
        item = {"id": f"c_{uuid.uuid4().hex[:10]}", "created_at": int(time.time()), **fields}
        items.append(item)
        _write_custom(items)
    return item


@router.put("/api/progression/custom/{item_id}")
def update_custom_progression(item_id: str, body: CustomProgressionIn):
    fields = _validate_custom(body)
    with _custom_lock:
        items = _read_custom()
        for i, it in enumerate(items):
            if it.get("id") == item_id:
                items[i] = {**it, **fields, "updated_at": int(time.time())}
                _write_custom(items)
                return items[i]
    raise HTTPException(status_code=404, detail="custom progression not found")


@router.delete("/api/progression/custom/{item_id}")
def delete_custom_progression(item_id: str):
    with _custom_lock:
        items = _read_custom()
        kept = [it for it in items if it.get("id") != item_id]
        if len(kept) == len(items):
            raise HTTPException(status_code=404, detail="custom progression not found")
        _write_custom(kept)
    return {"ok": True, "count": len(kept)}


# ---------------------------------------------------------------------------
# AI accompaniment for a bare progression (Progression Library playback).
# Builds a chord timeline from [degree, quality, bass?] specs + key and runs
# the same generator the player uses. Generation is a few ms for ≤ 32 chords,
# so it runs synchronously (plain def → thread pool).
# ---------------------------------------------------------------------------
_SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_FLAT_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
_SHARP_KEYS = {"G", "D", "A", "E", "B", "F#", "C#"}
# Quality id → suffix the accompaniment generator's chord parser understands.
# Entries whose exact spelling the parser degrades (maj7#11 → triad, m7b9 →
# major) are mapped to the nearest well-parsed quality.
_GEN_SUFFIX = {
    "maj": "", "m": "m", "dim": "dim", "aug": "aug", "maj7": "maj7", "m7": "m7", "7": "7",
    "m7b5": "m7b5", "dim7": "dim7", "maj9": "maj9", "m9": "m9", "9": "9", "7sus4": "7sus4",
    "6": "6", "m6": "m6", "add9": "add9", "6/9": "6/9", "11": "11", "m11": "m11", "13": "13",
    "sus2": "sus2", "sus4": "sus4", "7b9": "7b9", "7#9": "7#9", "7b5": "7b5", "7#5": "7#5",
    "7b13": "7b13", "7#11": "7#11", "maj7#11": "maj7", "m7b9": "m7", "mmaj7": "mM7",
    "9sus4": "9sus4", "m6/9": "m6/9",
}


class ProgressionAccIn(BaseModel):
    chords: list = Field(..., min_length=1, max_length=32)
    key: str = Field("C", max_length=4)
    bpm: float = Field(100.0, ge=40, le=200)
    beats_per_chord: int = Field(4, ge=1, le=16)
    style: str = Field("Auto", max_length=32)
    level: str = Field("L2", max_length=4)
    instrument: str = Field("piano", max_length=16)


def _key_pc(key: str) -> int:
    m = re.match(r"^([A-G])([b#])?", key or "")
    if not m:
        return 0
    base = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}[m.group(1)]
    return (base + (1 if m.group(2) == "#" else -1 if m.group(2) == "b" else 0)) % 12


@router.post("/api/progression/accompaniment")
def progression_accompaniment(body: ProgressionAccIn):
    if body.level not in ("L1", "L2", "L3"):
        raise HTTPException(status_code=400, detail="level must be L1/L2/L3")
    if body.instrument not in ("piano", "guitar", "ukulele"):
        raise HTTPException(status_code=400, detail="instrument must be piano/guitar/ukulele")
    specs = _normalize_chords(body.chords)
    key_pc = _key_pc(body.key)
    names = _SHARP_NAMES if body.key in _SHARP_KEYS else _FLAT_NAMES
    sec = 60.0 / body.bpm * body.beats_per_chord
    timeline = []
    for i, spec in enumerate(specs):
        deg, q = spec[0], spec[1]
        name = names[(key_pc + deg) % 12] + _GEN_SUFFIX.get(q, "")
        if len(spec) > 2:
            name += "/" + names[(key_pc + spec[2]) % 12]
        timeline.append({"time": round(i * sec, 4), "end": round((i + 1) * sec, 4), "chord": name})
    try:
        from ai.accompaniment_generator import generate_accompaniment
        result = generate_accompaniment(
            chords=timeline, bpm=body.bpm, style=body.style, level=body.level,
            instrument=body.instrument,
        )
    except Exception as exc:  # generator is strict about spellings; surface it
        raise HTTPException(status_code=400, detail=f"accompaniment failed: {exc}")
    keep = ("time", "duration", "pitch", "velocity", "gate_ratio", "voice_lane", "hand", "string", "finger", "strum_id")
    slim = lambda evs: [{k: e[k] for k in keep if k in e} for e in evs if isinstance(e, dict)]
    return {
        "chords": timeline,
        "loop_seconds": round(len(specs) * sec, 4),
        "style": result.get("style", body.style),
        "level": body.level,
        "instrument": body.instrument,
        "left_hand": slim(result.get("left_hand", [])),
        "right_hand": slim(result.get("right_hand", [])),
    }


# ---------------------------------------------------------------------------
# Progression summary for the player — which loop(s) a song is built on.
# ---------------------------------------------------------------------------
@router.get("/api/progression/summary")
def progression_summary(
    path: str = Query(None, description="song path (or use hash)"),
    hash: str = Query(None, description="song hash"),
    username: str = Depends(get_current_user),
):
    from chord_cache import chord_file_for, song_hash as get_song_hash
    from ai.progression_pattern import analyze_progression
    h = hash or (get_song_hash(path) if path else None)
    if not h:
        raise HTTPException(status_code=400, detail="missing path or hash")
    f = chord_file_for(h)
    if not f.is_file():
        raise HTTPException(status_code=404, detail="no chord data")
    data = json.loads(f.read_text(encoding="utf-8"))
    raw_chords = list(data.get("chords") or [])   # section detector runs on raw chords, like /api/ai/sections
    try:  # analyse the served view (meter regularizer, quality smoothing, split)
        from chord_api import apply_serve_pipeline
        apply_serve_pipeline(data, f, path or data.get("path") or "")
    except Exception:
        pass
    chords = data.get("chords") or []
    result = analyze_progression(
        chords, key=data.get("key") or "C",
        downbeats=data.get("downbeats") or None, bpm=data.get("bpm"),
        bars=data.get("bars") or None,
    )
    result["hash"] = h
    result["path"] = path or data.get("path") or ""
    result["title"] = _title_from_path(result["path"])

    # Genre: library tag + folder category (path like "@1/POP/K-POP/…").
    from ai.progression_pattern import describe_style, map_sections, _key_semi
    result["genre"] = _genre_for_path(result["path"])
    result["style"] = describe_style(chords, result["key"], data.get("bpm"), result["genre"], result["patterns"])

    # Sections (honours human annotations like /api/ai/sections does).
    # Same source-of-truth rules as /api/ai/sections: the user's own human
    # annotation wins, otherwise the shared algorithmic detection.
    sections = []
    try:
        from ai.section_detect import detect_sections
        user_dir = DATA_DIR / "users" / str(username)
        effective = str(user_dir) if (user_dir / "human_sections" / f"{h}.json").is_file() else str(DATA_DIR)
        sec = detect_sections(raw_chords, data.get("key") or "C", song_hash=h, data_dir=effective,
                              fallback_data_dir=str(DATA_DIR), hint_bpm=data.get("bpm"))
        sections = sec.get("sections", [])
        if (sec.get("analysis") or {}).get("mode") != "human-loop":
            from ai.section_refine import refine_sections
            from ai_api import load_melody_notes
            sections, result["section_refine"] = refine_sections(sections, result, data.get("bars") or data.get("downbeats"), chords,
                                                                 melody=load_melody_notes(h))
    except Exception:
        sections = []
    result["sections"] = map_sections(sections, result["patterns"], chords, _key_semi(result["key"]))
    return result


def _title_from_path(path: str) -> str:
    base = (path or "").replace("\\", "/").rsplit("/", 1)[-1]
    return re.sub(r"\.[a-z0-9]{2,5}$", "", base, flags=re.I)


_genre_cache = {"mtime": -1.0, "map": {}}


def _genre_for_path(path: str) -> str:
    if not path or path.startswith("__"):
        return ""
    norm = path.replace("\\", "/")
    tag = ""
    cache_path = DATA_DIR / "library_cache.json"
    try:
        mt = cache_path.stat().st_mtime
        if _genre_cache["mtime"] != mt:
            lib = json.loads(cache_path.read_text(encoding="utf-8"))
            _genre_cache["map"] = {t.get("path", "").replace("\\", "/"): t.get("genre", "") for t in lib.get("tracks", [])}
            _genre_cache["mtime"] = mt
        tag = _genre_cache["map"].get(norm, "") or ""
    except (OSError, json.JSONDecodeError):
        pass
    parts = [p for p in norm.split("/")[:-1] if p and not p.startswith("@")]
    folder = " / ".join(parts[:2])
    if tag.lower() in ("", "music", "other", "unknown"):
        return folder
    if folder and tag.lower() not in folder.lower():
        return f"{folder} · {tag}"
    return tag or folder
