"""和弦 API — 和弦資訊、和弦圖、和弦譜 CRUD、自動偵測、批次偵測"""

import json
import logging
import os
import re
import time
import asyncio
from pathlib import Path
from threading import Lock
from typing import Optional

logger = logging.getLogger(__name__)


def _normalize_name(name: str) -> str:
    """正規化名稱：轉小寫、移除標點、連字號/底線→空格、壓縮空白、保留 CJK"""
    name = name.lower()
    name = re.sub(r"[_\-]", " ", name)            # 底線/連字號→空格
    name = re.sub(r"[^a-z0-9\s\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", "", name)  # 保留 CJK/日/韓
    name = re.sub(r"\s+", " ", name).strip()       # 壓縮空白
    return name


from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Depends, Header, Request
from pydantic import BaseModel

from auth_api import get_current_user, get_admin_user, DB_PATH as AUTH_DB_PATH
import sqlite3
from chord_table import get_chord_info, get_chord_jianpu, analyze_chord_in_key
from chord_diagrams import get_chord_diagram, get_chord_voicings
from chord_cache import (
    song_hash, update_entry_from_file as cache_update_entry,
    chord_file_for, chord_bak_for, ensure_chord_bucket,
)
from chord_splitter import maybe_split_for_serve
from chord_tail_extender import maybe_extend_tail_for_serve
from chord_noise_filter import maybe_filter_for_serve as maybe_noise_filter_for_serve
from bar_phase_corrector import maybe_correct_for_serve as maybe_phase_correct_for_serve
from meter_regularizer import maybe_regularize_for_serve
from chord_quality_smoother import maybe_smooth_for_serve
from key_relative import maybe_fix_relative_key_for_serve
from bpm_sanity import maybe_apply_structural_bpm_correction_for_serve
from global_chord_arbiter import maybe_analyze_global_structure_for_serve
from instrument_registry import get_instrument, list_instruments, INSTRUMENTS

from config import resolve_path

router = APIRouter(prefix="/api", tags=["chord"])

DATA_DIR = Path(__file__).parent.parent / "data"
CHORDS_DIR = DATA_DIR / "chords"
CACHE_FILE = DATA_DIR / "library_cache.json"
RATINGS_FILE = DATA_DIR / "ratings.json"

_ratings_lock = Lock()


def _float(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _audio_duration_for_path(path: str) -> float:
    if not path or path.startswith("__"):
        return 0.0
    try:
        full = resolve_path(path)
        if not full or not os.path.isfile(full):
            return 0.0
        from mutagen import File as MutagenFile
        audio = MutagenFile(full)
        if audio is not None and hasattr(audio, "info") and hasattr(audio.info, "length"):
            return float(audio.info.length or 0.0)
    except Exception:
        return 0.0
    return 0.0


def _suppress_stale_legacy_timeline_for_serve(data: dict, request_path: str = "") -> bool:
    """Hide legacy chord charts whose timeline plainly belongs to another file."""
    source = str(data.get("source") or "").lower()
    if source not in {"midi", "chordify"}:
        return False
    chords = data.get("chords") or []
    if not chords:
        return False
    max_end = max(
        _float(c.get("end"), _float(c.get("time")))
        for c in chords
        if isinstance(c, dict)
    )
    path = request_path or str(data.get("path") or "")
    duration = _float(data.get("duration")) or _audio_duration_for_path(path)
    if duration <= 0 or max_end <= max(duration + 8.0, duration * 1.35):
        return False
    data["stale_timeline_meta"] = {
        "applied": True,
        "reason": "legacy-source-exceeds-audio-duration",
        "source": source,
        "audio_duration": round(duration, 3),
        "max_chord_end": round(max_end, 3),
        "original_chord_count": len(chords),
    }
    data["chords"] = []
    data["key_changes"] = None
    data["keys"] = None
    return True


def _load_ratings() -> dict:
    if not RATINGS_FILE.is_file():
        return {}
    try:
        return json.loads(RATINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_ratings(data: dict) -> None:
    RATINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = RATINGS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(RATINGS_FILE)


def _version_rating(votes: dict) -> tuple:
    """Return (avg, count) from a {username: score} dict."""
    if not votes:
        return (0.0, 0)
    vals = [v for v in votes.values() if isinstance(v, (int, float))]
    if not vals:
        return (0.0, 0)
    return (round(sum(vals) / len(vals), 1), len(vals))


def _optional_user(request: Request, authorization: str = Header(None)) -> Optional[str]:
    """Return username if caller is identifiable, else None.

    Delegates to ``get_current_user`` so LAN bypass (personal-mode auto-admin)
    applies even when the browser has no Authorization header — without this,
    GET /chords falls through to the official version while POST /chords saves
    to data/users/admin/, making the admin's edits invisible on the next load.
    """
    try:
        return get_current_user(request, authorization)
    except Exception:
        return None


def _is_admin(username: str) -> bool:
    try:
        with sqlite3.connect(AUTH_DB_PATH, timeout=10) as conn:
            row = conn.execute("SELECT is_admin FROM users WHERE username=?", (username,)).fetchone()
            return bool(row and row[0] == 1)
    except Exception:
        return False




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
async def chord_diagram(instrument: str, name: str, split: int = Query(None)):
    """取得和弦圖（吉他/烏克麗麗/...）"""
    if instrument not in list_instruments():
        raise HTTPException(status_code=400, detail=f"instrument 須為 {', '.join(list_instruments())}")
    if instrument == "arranger" and split is not None:
        from chord_diagrams import _arranger_resolve
        diagram = _arranger_resolve(name, split_point=split)
        if diagram:
            diagram["name"] = name
    else:
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
    if instrument == "accordion":
        return {"name": name, "type": "accordion", "voicings": voicings}
    if instrument == "arranger":
        return {"name": name, "type": "arranger", "voicings": voicings}
    num_strings = inst_meta.get("num_strings", 6) if inst_meta else 6
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
    bpm: float = 0
    chords: list = []


_BEAT_FIELDS = ("bpm", "beats", "downbeats", "tempo_curve",
                "beats_source", "beat_version", "bpm_correction")
_STRUCTURAL_METER_FIELDS = (
    "time_signature",
    "display_subdivisions_per_bar",
    "practice_pulses_per_bar",
    "beats_per_bar",
)
_BEAT_SOURCE_MODES = {"librosa", "madmom", "beat_this"}


def _apply_requested_beat_source(data: dict, base_file: Path, beat_source: Optional[str]) -> None:
    """Overlay cached beat fields for a requested player beat source.

    This is response-only: the player can default to beat_this when the PC/VPS
    batch has produced .bak.beat_this, while canonical chord JSON stays intact.
    """
    mode = (beat_source or "").strip().lower()
    if not mode:
        return
    if mode not in _BEAT_SOURCE_MODES:
        raise HTTPException(status_code=400, detail="beat_source must be librosa, madmom or beat_this")

    base_path = str(base_file)
    if mode == "librosa":
        legacy = base_path + ".bak.beats"
        lib = base_path + ".bak.librosa"
        if os.path.exists(legacy) and not os.path.exists(lib):
            try:
                os.replace(legacy, lib)
            except Exception:
                pass

    bak_path = base_path + f".bak.{mode}"
    if not os.path.isfile(bak_path):
        return
    try:
        snapshot = json.loads(Path(bak_path).read_text(encoding="utf-8"))
    except Exception:
        return
    # Refuse to overlay an empty/degenerate sidecar — some songs have a
    # `.bak.librosa` left over from an `analyze_and_snap_dynamic` librosa-
    # fallback that produced bpm but zero beats/downbeats. Overlaying it would
    # wipe the active source's working beat data, blank out time_signature,
    # and break downbeat rendering. Bail silently — caller keeps the data it
    # had before the overlay request, and the meter-fallback helper below
    # will still try to stamp time_signature from a usable sidecar.
    if not snapshot.get("beats") and not snapshot.get("downbeats"):
        return
    for key in _BEAT_FIELDS:
        if key in snapshot:
            data[key] = snapshot[key]
        else:
            data.pop(key, None)
    # Phase 1: the canonical chord JSON may carry beat_probs / downbeat_probs /
    # bar_probs aligned with its OWN beats/downbeats. Sidecars have neither
    # field nor index alignment with the canonical grid, so any retained probs
    # would silently mis-gate the splitter. Strip them — non-canonical beat
    # sources fall back to Phase 0 unconditional snap (= today's behavior).
    data.pop("beat_probs", None)
    data.pop("downbeat_probs", None)
    data.pop("bar_probs", None)


# Sidecar load order for compound 6/8 fallback. librosa is densest/most
# reliable for downbeat detection; madmom second; beat_this last (it is
# the most likely source to have produced the sparse downbeats[] that
# caused the original detection failure).
_METER_FALLBACK_SIDECAR_ORDER = ("librosa", "madmom", "beat_this")
_METER_FIELDS_FROM_COMPOUND = (
    "time_signature",
    "display_subdivisions_per_bar",
    "practice_pulses_per_bar",
)


def _maybe_meter_fallback_from_sidecars(data: dict, official_file: Path) -> None:
    """If serve payload lacks time_signature, try to detect 6/8 from sidecars.

    Read-only: never writes to any chord JSON. Mutates the in-memory `data`
    dict only. Cheap for the common case (skips entirely when time_signature
    is already present).
    """
    if data.get("time_signature"):
        return
    if not data.get("beats") and not data.get("downbeats"):
        # Nothing to seed bpm from either; classifier won't work even on sidecars
        # for songs with completely degenerate beat data. Beats-only path also
        # requires beats[]. Bail quietly.
        pass

    # Lazy import to avoid circular import at module load
    try:
        from global_chord_arbiter import (
            _compound_6_8_meter_info_from_payload,
            _compound_6_8_meter_info_beats_only,
            _simple_3_4_meter_info_from_payload,
        )
    except ImportError:
        from backend.global_chord_arbiter import (
            _compound_6_8_meter_info_from_payload,
            _compound_6_8_meter_info_beats_only,
            _simple_3_4_meter_info_from_payload,
        )

    base_path = str(official_file)
    info = None
    for mode in _METER_FALLBACK_SIDECAR_ORDER:
        sidecar_path = base_path + f".bak.{mode}"
        if not os.path.isfile(sidecar_path):
            continue
        try:
            snapshot = json.loads(Path(sidecar_path).read_text(encoding="utf-8"))
        except Exception:
            continue
        # Try 6/8 first, then 3/4 (disjoint by beat_gap so order doesn't
        # matter for correctness, only marginally for performance).
        info = _compound_6_8_meter_info_from_payload(snapshot)
        if not info:
            info = _simple_3_4_meter_info_from_payload(snapshot)
        if info:
            info["_fallback_source"] = f"sidecar:{mode}"
            break

    if not info:
        # Beats-only fallback is intentionally a LAST resort. Only fire when
        # downbeats[] is truly absent or too sparse for the standard classifier
        # to have produced a verdict. If downbeats[] are present (≥4 entries)
        # but the sidecar classifier rejected, trust that rejection — beats-only
        # has no way to verify bar structure and would false-positive on 4/4
        # songs with very regular eighth-note beats (verified on "Breakaway"
        # by Kelly Clarkson — 4/4 with downbeat-to-beat ratio 4.05, not 6/8).
        downbeats_present = len([v for v in (data.get("downbeats") or []) if v is not None])
        if downbeats_present < 4:
            info = _compound_6_8_meter_info_beats_only(
                [float(v) for v in (data.get("beats") or [])],
                float(data.get("bpm") or 0.0),
            )
            if info:
                info["_fallback_source"] = "beats-only"

    if not info:
        return

    for key in _METER_FIELDS_FROM_COMPOUND:
        if info.get(key) is not None:
            data[key] = info[key]
    if info.get("bars"):
        data.setdefault("bars", info["bars"])
    # Breadcrumb at TOP-LEVEL so apply_global_structure_corrections (which
    # later replaces global_arbiter_meta wholesale) can't clobber it.
    data["serve_time_meter_fallback"] = {
        "source": info.get("_fallback_source"),
        "type": info.get("type"),
    }


def _merge_official_timing_fields_for_serve(data: dict, official_file: Path,
                                            beat_source: Optional[str]) -> None:
    """Fill timing-grid fields stripped from personal chord saves.

    User chord versions own the chord names/times, but ChordSheet historically
    did not persist beat arrays or meter metadata. Without these fields a
    personal edit can shadow the official 6/8 grid and make the player render
    1/2/4/8-dot phase drift again.
    """
    if not official_file.is_file():
        return
    try:
        official = json.loads(official_file.read_text(encoding="utf-8"))
    except Exception:
        return
    _apply_requested_beat_source(official, official_file, beat_source)

    data_has_grid = bool(data.get("beats")) or bool(data.get("downbeats"))
    official_has_grid = bool(official.get("beats")) or bool(official.get("downbeats"))
    if official_has_grid and not data_has_grid:
        for key in _BEAT_FIELDS:
            if key in official:
                data[key] = official[key]
            else:
                data.pop(key, None)

    for key in _STRUCTURAL_METER_FIELDS:
        if data.get(key) in (None, "", 0) and official.get(key) not in (None, "", 0):
            data[key] = official[key]



def apply_serve_pipeline(data: dict, official_file: Path, path: str = "") -> dict:
    """Run the same serve-time corrections GET /api/chords applies, so other
    consumers (progression summary) analyse exactly what the player shows."""
    if path and _suppress_stale_legacy_timeline_for_serve(data, path):
        return data
    maybe_fix_relative_key_for_serve(data)   # Ab major vs Fm: pick the tonic by cadences/endings
    maybe_apply_structural_bpm_correction_for_serve(data)
    maybe_phase_correct_for_serve(data)
    _maybe_meter_fallback_from_sidecars(data, official_file)
    maybe_analyze_global_structure_for_serve(data)
    maybe_regularize_for_serve(data)
    maybe_noise_filter_for_serve(data)
    maybe_smooth_for_serve(data)
    maybe_extend_tail_for_serve(data)
    maybe_split_for_serve(data)
    return data


@router.get("/chords")
async def get_chords(path: str = Query(...), version: str = Query(None),
                     beat_source: str = Query(None),
                     username: Optional[str] = Depends(_optional_user)):
    """取得某首歌的和弦譜"""
    is_fallback = False
    official_file = chord_file_for(song_hash(path))
    if not version and username:
        user_file = DATA_DIR / "users" / username / "chords" / f"{song_hash(path)}.json"
        if user_file.is_file():
            version = username
    if version and version != "official":
        chords_file = DATA_DIR / "users" / version / "chords" / f"{song_hash(path)}.json"
        if not chords_file.is_file():
            chords_file = official_file
            is_fallback = True
    else:
        chords_file = official_file

    if not chords_file.is_file():
        return {"path": path, "key": "", "capo": 0, "chords": [], "exists": False}

    data = json.loads(chords_file.read_text(encoding="utf-8"))
    _apply_requested_beat_source(data, official_file, beat_source)
    if chords_file != official_file:
        _merge_official_timing_fields_for_serve(data, official_file, beat_source)
    data["exists"] = True
    data["current_version"] = "official" if is_fallback or not version else version
    if _suppress_stale_legacy_timeline_for_serve(data, path):
        return data
    maybe_fix_relative_key_for_serve(data)   # Ab major vs Fm: pick the tonic by cadences/endings
    maybe_apply_structural_bpm_correction_for_serve(data)
    maybe_phase_correct_for_serve(data) # rewrite irregular downbeats[] to regular grid
    _maybe_meter_fallback_from_sidecars(data, official_file)  # stamp 6/8 from sidecars when active source can't detect
    maybe_analyze_global_structure_for_serve(data) # section-level diagnostic hints
    maybe_regularize_for_serve(data)    # 6/8 & 3/4: clean bars, fix phase, even beats, snap chord edges
    maybe_noise_filter_for_serve(data)  # absorb 1-beat noise tails
    maybe_smooth_for_serve(data)        # Fm → Fm6 → Fm extension flicker → Fm
    maybe_extend_tail_for_serve(data)   # fill missing outro cards from beat tail
    maybe_split_for_serve(data)         # split long chords at corrected downbeats
    return data


@router.get("/chords/by-hash")
async def get_chords_by_hash(hash: str = Query(..., min_length=8, max_length=16),
                             beat_source: str = Query(None)):
    """直接用 hash 取得和弦譜（給 process 結果用）"""
    chords_file = chord_file_for(hash)
    if not chords_file.is_file():
        return {"hash": hash, "key": "", "capo": 0, "chords": [], "exists": False}
    data = json.loads(chords_file.read_text(encoding="utf-8"))
    _apply_requested_beat_source(data, chords_file, beat_source)
    data["exists"] = True
    if _suppress_stale_legacy_timeline_for_serve(data):
        return data
    maybe_fix_relative_key_for_serve(data)   # Ab major vs Fm: pick the tonic by cadences/endings
    maybe_apply_structural_bpm_correction_for_serve(data)
    maybe_phase_correct_for_serve(data)
    _maybe_meter_fallback_from_sidecars(data, chords_file)
    maybe_analyze_global_structure_for_serve(data)
    maybe_regularize_for_serve(data)
    maybe_noise_filter_for_serve(data)
    maybe_smooth_for_serve(data)
    maybe_extend_tail_for_serve(data)
    maybe_split_for_serve(data)
    return data


def _resolve_correction_baseline(path: str, username: str):
    """Return ``(chords_list, source_version_label)`` to diff a fresh save against.

    Priority: the user's prior personal version > the official BTC version.
    Returns ``(None, None)`` when neither exists (first save by anyone — no
    diff worth computing, all chords would look like inserts).
    """
    h = song_hash(path)
    user_file = DATA_DIR / "users" / username / "chords" / f"{h}.json"
    if user_file.is_file():
        try:
            data = json.loads(user_file.read_text(encoding="utf-8"))
            return data.get("chords") or [], username
        except Exception:
            pass
    official = chord_file_for(h)
    if official.is_file():
        try:
            data = json.loads(official.read_text(encoding="utf-8"))
            return data.get("chords") or [], "official"
        except Exception:
            pass
    return None, None


def _diff_chord_arrays(before: list, after: list, *, tol_sec: float = 0.1) -> list:
    """Compute the minimal correction diff between two chord arrays.

    Pairing heuristic: scan both arrays in time order; treat two entries as
    "same chord" when their ``time`` differs by ≤ ``tol_sec``. From there:
      - same time, different name  → ``kind='name'``
      - same time, name same, end differs >tol → ``kind='time'``
      - in before, no match in after → ``kind='delete'``
      - in after,  no match in before → ``kind='insert'``

    O(n+m) walk. Returns a list of dicts ready for record_chord_corrections().
    """
    if not isinstance(before, list) or not isinstance(after, list):
        return []
    diffs = []
    i = j = 0
    while i < len(before) and j < len(after):
        b = before[i]
        a = after[j]
        bt = float(b.get("time", 0.0))
        at = float(a.get("time", 0.0))
        if abs(bt - at) <= tol_sec:
            bn = b.get("chord")
            an = a.get("chord")
            be = float(b.get("end", bt))
            ae = float(a.get("end", at))
            if bn != an:
                diffs.append({
                    "kind": "name",
                    "chord_time": round(at, 3),
                    "before_name": bn,
                    "after_name": an,
                })
            elif abs((be - bt) - (ae - at)) > tol_sec:
                diffs.append({
                    "kind": "time",
                    "chord_time": round(at, 3),
                    "before_name": bn,
                    "after_name": an,
                    "before_dur": round(be - bt, 3),
                    "after_dur": round(ae - at, 3),
                })
            i += 1
            j += 1
        elif bt < at:
            diffs.append({
                "kind": "delete",
                "chord_time": round(bt, 3),
                "before_name": b.get("chord"),
                "after_name": None,
            })
            i += 1
        else:
            diffs.append({
                "kind": "insert",
                "chord_time": round(at, 3),
                "before_name": None,
                "after_name": a.get("chord"),
            })
            j += 1
    while i < len(before):
        b = before[i]
        diffs.append({
            "kind": "delete",
            "chord_time": round(float(b.get("time", 0.0)), 3),
            "before_name": b.get("chord"),
            "after_name": None,
        })
        i += 1
    while j < len(after):
        a = after[j]
        diffs.append({
            "kind": "insert",
            "chord_time": round(float(a.get("time", 0.0)), 3),
            "before_name": None,
            "after_name": a.get("chord"),
        })
        j += 1
    return diffs


@router.post("/chords")
async def save_chords(sheet: ChordSheet, username: str = Depends(get_current_user)):
    """儲存和弦譜至個人專屬空間。

    Belt-and-suspenders: sort the chord array by time and drop near-duplicate
    consecutive entries (within 0.2s) before writing. The frontend already
    does this in _cleanChordsForSave, but we repeat it server-side because a
    corrupted-but-unsorted payload here was how the previous overlap bug
    ("C Bb 重疊" at 2:39) got persisted.
    """
    user_chords_dir = DATA_DIR / "users" / username / "chords"
    user_chords_dir.mkdir(parents=True, exist_ok=True)
    chords_file = user_chords_dir / f"{song_hash(sheet.path)}.json"

    payload = sheet.model_dump()
    chords = sorted(payload.get("chords") or [],
                    key=lambda c: c.get("time", 0.0))
    MIN_GAP = 0.2
    cleaned = []
    for c in chords:
        if cleaned and (c.get("time", 0.0) - cleaned[-1].get("time", 0.0)) < MIN_GAP:
            cleaned[-1] = c  # later-indexed wins (matches frontend policy)
        else:
            cleaned.append(c)
    # Ensure end == next.time so ribbon doesn't show gaps
    for i in range(len(cleaned) - 1):
        cleaned[i]["end"] = cleaned[i + 1].get("time", cleaned[i].get("end"))
    payload["chords"] = cleaned

    # Merge-on-save: preserve fields the ChordSheet model doesn't know about
    # (beats, downbeats, tempo_curve, beats_source, beat_version, bpm_correction,
    # source, etc.) — otherwise upgrade-beats data gets wiped on the next chord
    # correction save and the ribbon can no longer derive correct dot counts.
    if chords_file.is_file():
        try:
            existing = json.loads(chords_file.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                merged = {**existing, **payload}
                payload = merged
        except Exception:
            pass

    tmp = chords_file.with_suffix(chords_file.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp, chords_file)

    # Capture user corrections as training signal — diff against the
    # baseline (the user's prior version if it existed, else the official BTC
    # output). Failure non-fatal so a feedback-db blip never blocks save.
    try:
        baseline_chords, baseline_version = _resolve_correction_baseline(
            sheet.path, username,
        )
        if baseline_chords is not None:
            diffs = _diff_chord_arrays(baseline_chords, payload.get("chords") or [])
            if diffs:
                from feedback_api import record_chord_corrections
                ts = time.time()
                records = []
                for d in diffs:
                    d_rec = dict(d)
                    d_rec.update({
                        "song_hash": song_hash(sheet.path),
                        "ts": ts,
                        "username": username,
                        "bpm": payload.get("bpm"),
                        "key": payload.get("key"),
                        "source_version": baseline_version,
                    })
                    records.append(d_rec)
                record_chord_corrections(records)
    except Exception as e:
        logger.warning("chord_corrections persist failed for %s: %s",
                       sheet.path, e)

    return {"ok": True, "path": sheet.path, "version": username}


@router.get("/chords/versions")
def get_chord_versions(path: str = Query(...), username: Optional[str] = Depends(_optional_user)):
    """取得這首歌的所有和弦版本清單，含真實平均評分與目前使用者自己的評分"""
    target_hash = song_hash(path)
    versions = []

    all_ratings = _load_ratings().get(target_hash, {})
    is_admin = _is_admin(username) if username else False

    def _build(version_id: str, name: str):
        votes = all_ratings.get(version_id, {})
        avg, count = _version_rating(votes)
        is_self = bool(username) and version_id == username
        return {
            "id": version_id,
            "name": name,
            "rating": avg,
            "count": count,
            "my_rating": votes.get(username, 0) if username else 0,
            "can_rate": bool(username) and (not is_self or is_admin),
            "is_self": is_self,
        }

    # 1. 官方版
    official_file = chord_file_for(target_hash)
    if official_file.is_file():
        versions.append(_build("official", "官方 AI 版"))

    # 2. 社群版 (掃描所有使用者的 chords 目錄)
    users_dir = DATA_DIR / "users"
    if users_dir.is_dir():
        for user_folder in users_dir.iterdir():
            if user_folder.is_dir():
                user_file = user_folder / "chords" / f"{target_hash}.json"
                if user_file.is_file():
                    versions.append(_build(user_folder.name, user_folder.name))

    # Sort versions: official first, then highest rating, then most votes
    versions.sort(key=lambda x: (x["id"] != "official", -x["rating"], -x["count"]))

    return {"ok": True, "versions": versions, "current_user": username}


class RatingRequest(BaseModel):
    path: str
    version: str
    score: int  # 1..5, or 0 to remove existing vote


@router.post("/chords/rate")
def rate_chord_version(req: RatingRequest, username: str = Depends(get_current_user)):
    """對某個和弦版本評分。score=0 代表收回先前的評分。
    一般使用者不能評自己上傳的版本（防刷分）；管理員不在此限。"""
    if not (0 <= req.score <= 5):
        raise HTTPException(status_code=400, detail="score must be between 0 and 5")

    if req.version == username and not _is_admin(username):
        raise HTTPException(status_code=403, detail="不能對自己的版本評分 (Cannot rate your own version)")

    target_hash = song_hash(req.path)

    with _ratings_lock:
        data = _load_ratings()
        song_votes = data.setdefault(target_hash, {})
        version_votes = song_votes.setdefault(req.version, {})

        if req.score == 0:
            version_votes.pop(username, None)
            if not version_votes:
                song_votes.pop(req.version, None)
            if not song_votes:
                data.pop(target_hash, None)
        else:
            version_votes[username] = int(req.score)

        _save_ratings(data)

    avg, count = _version_rating(data.get(target_hash, {}).get(req.version, {}))
    return {
        "ok": True,
        "rating": avg,
        "count": count,
        "my_rating": int(req.score),
    }


# ---------------------------------------------------------------------------
# admin: BPM recompute (ballad halving heuristic, per-song manual)
# ---------------------------------------------------------------------------

class BpmRecomputeRequest(BaseModel):
    path: Optional[str] = None
    hash: Optional[str] = None
    mode: str = "auto"  # "auto" | "halve" | "double"


@router.post("/admin/bpm/recompute")
def admin_bpm_recompute(req: BpmRecomputeRequest, username: str = Depends(get_admin_user)):
    """Manually re-decide BPM for a single song.

    mode=auto  → re-run bpm_sanity.ballad_halving_check against the current
                 stored BPM (no re-running madmom — that's expensive and a
                 separate concern via /api/process/upgrade-beats).
    mode=halve → force bpm /= 2, halve tempo_curve, thin downbeats.
    mode=double → inverse of halve (for undo).

    Admin-only via get_admin_user (handles LAN bypass + token auth uniformly).
    """
    if req.hash:
        h = req.hash
    elif req.path:
        h = song_hash(req.path)
    else:
        raise HTTPException(status_code=400, detail="missing path or hash")

    chords_file = chord_file_for(h)
    if not chords_file.is_file():
        raise HTTPException(status_code=404, detail="chord JSON not found")

    data = json.loads(chords_file.read_text(encoding="utf-8"))
    original_bpm = float(data.get("bpm") or 0)
    if original_bpm <= 0:
        raise HTTPException(status_code=400, detail="stored chord JSON has no bpm")

    corrected_bpm = original_bpm
    meta = {"applied": False, "reason": "", "original": original_bpm}

    if req.mode == "halve":
        corrected_bpm = original_bpm / 2.0
        meta = {"applied": True, "reason": "admin-manual-halve", "original": original_bpm}
    elif req.mode == "double":
        corrected_bpm = original_bpm * 2.0
        meta = {"applied": True, "reason": "admin-manual-double", "original": original_bpm}
    else:  # auto
        audio_path = data.get("audio_path") or data.get("path") or ""
        try:
            resolved = resolve_path(audio_path) if audio_path else ""
        except Exception:
            resolved = ""
        if not resolved or not os.path.isfile(resolved):
            raise HTTPException(status_code=400, detail="audio file not available; use halve/double")
        from bpm_sanity import ballad_halving_check
        corrected_bpm, meta = ballad_halving_check(original_bpm, resolved)

    if meta.get("applied"):
        # scale tempo_curve + thin downbeats to match new tempo
        scale = corrected_bpm / original_bpm if original_bpm else 1.0
        tempo_curve = data.get("tempo_curve") or []
        data["tempo_curve"] = [
            {"t": p.get("t", 0.0), "bpm": round(float(p.get("bpm", 0.0)) * scale, 2)}
            for p in tempo_curve
        ]
        downbeats = data.get("downbeats") or []
        if req.mode == "halve" or (req.mode == "auto" and scale < 1):
            if len(downbeats) >= 2:
                data["downbeats"] = downbeats[::2]
        elif req.mode == "double":
            # Doubling downbeats correctly would need beat times; we conservatively
            # leave them — player derives from bpm+beats anyway
            pass
        data["bpm"] = round(corrected_bpm, 1)
        data["bpm_correction"] = meta

    chords_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "ok": True,
        "hash": h,
        "original_bpm": original_bpm,
        "corrected_bpm": round(corrected_bpm, 1),
        "applied": meta.get("applied", False),
        "reason": meta.get("reason", ""),
        "onset_density": meta.get("onset_density"),
        "rms_cov": meta.get("rms_cov"),
    }


# ---------------------------------------------------------------------------
# admin: bar/downbeat arbitrator recompute (Phase 2 — bar_arbitrator)
# ---------------------------------------------------------------------------

class BarRecomputeRequest(BaseModel):
    path: Optional[str] = None
    hash: Optional[str] = None
    force: bool = False  # bypass already-regular gate + low-confidence gate


@router.post("/admin/bar/recompute")
def admin_bar_recompute(req: BarRecomputeRequest, username: str = Depends(get_admin_user)):
    """Manually re-run bar_arbitrator on a single song.

    Reads the chord JSON, runs ``ai.bar_arbitrator.arbitrate`` (model path
    if available else rule baseline), writes the result back. Personal-mode
    only — admin token already restricts to LAN in beta mode.

    ``force=True`` bypasses both gates (raw-already-regular and low model
    confidence) so admins can override on songs where the auto-judge would
    keep the (broken) raw downbeats.

    Refuses to act when process_queue has in-flight jobs — avoids two
    workers writing the same chord JSON race.
    """
    # Queue-empty guard — don't race the ingest worker
    try:
        from process_queue import process_queue, queue
        if not isinstance(process_queue, queue.Queue):
            pass  # imported wrong thing, skip guard
        elif process_queue.qsize() > 0:
            raise HTTPException(
                status_code=409,
                detail=f"process_queue has {process_queue.qsize()} jobs in flight; retry when idle",
            )
    except HTTPException:
        raise
    except Exception:
        # If we can't introspect the queue, proceed (single-song manual op
        # is low-risk vs. blocking admin entirely)
        pass

    if req.hash:
        h = req.hash
    elif req.path:
        h = song_hash(req.path)
    else:
        raise HTTPException(status_code=400, detail="missing path or hash")

    chords_file = chord_file_for(h)
    if not chords_file.is_file():
        raise HTTPException(status_code=404, detail="chord JSON not found")

    data = json.loads(chords_file.read_text(encoding="utf-8"))

    from ai.bar_arbitrator import arbitrate, apply_to_chord_json
    from process_queue import _resolve_bar_model_path
    model_path = _resolve_bar_model_path()
    res = arbitrate(data, model_path=model_path, force=req.force)
    apply_to_chord_json(data, res)

    # Atomic write — same .tmp + os.replace pattern bpm/recompute uses
    tmp = chords_file.with_suffix(chords_file.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, chords_file)

    return {
        "ok": True,
        "hash": h,
        "applied": res["applied"],
        "reason": res["reason"],
        "model_version": res["model_version"],
        "beats_per_bar": res["beats_per_bar"],
        "raw_regularity_cv": res["raw_regularity_cv"],
        "score_after": res["score_after"],
        "n_downbeats_before": len(res["downbeats_before"]),
        "n_downbeats_after": len(res["downbeats_after"]),
    }


class BarRestoreRequest(BaseModel):
    path: Optional[str] = None
    hash: Optional[str] = None


@router.post("/admin/bar/restore")
def admin_bar_restore(req: BarRestoreRequest, username: str = Depends(get_admin_user)):
    """Revert downbeats to ``bar_correction.downbeats_original`` snapshot.

    Pairs with /admin/bar/recompute. Snapshot is created on first apply and
    preserved across apply→restore→apply cycles, so this always returns to
    the truly-original beat-tracker output.

    Returns 400 with ``no-snapshot`` if the chord JSON has no
    ``bar_correction.downbeats_original`` (i.e. arbitrator was never run on
    this song, or it ran via an old version that didn't snapshot).
    """
    if req.hash:
        h = req.hash
    elif req.path:
        h = song_hash(req.path)
    else:
        raise HTTPException(status_code=400, detail="missing path or hash")

    chords_file = chord_file_for(h)
    if not chords_file.is_file():
        raise HTTPException(status_code=404, detail="chord JSON not found")

    data = json.loads(chords_file.read_text(encoding="utf-8"))
    from ai.bar_arbitrator import restore_from_snapshot
    ok, reason = restore_from_snapshot(data)
    if not ok:
        raise HTTPException(status_code=400, detail=reason)

    tmp = chords_file.with_suffix(chords_file.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, chords_file)

    return {
        "ok": True,
        "hash": h,
        "n_downbeats": len(data.get("downbeats") or []),
    }


# ---------------------------------------------------------------------------
# admin: read-only chord_corrections inspection. The diffs are persisted by
# /chords POST as users edit; these GETs let admins audit accumulation and
# (later) export to a fine-tuning corpus.
# ---------------------------------------------------------------------------

@router.get("/admin/chord/corrections")
def admin_chord_corrections_list(
    song_hash: Optional[str] = Query(None),
    username: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    _: str = Depends(get_admin_user),
):
    """Recent chord_corrections rows. Optionally filter by song_hash or username."""
    from feedback_api import _get_conn
    conds = []
    args = []
    if song_hash:
        conds.append("song_hash = ?")
        args.append(song_hash)
    if username:
        conds.append("username = ?")
        args.append(username)
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    sql = (
        "SELECT id, song_hash, ts, username, kind, chord_time, before_name, "
        "after_name, before_dur, after_dur, bpm, key, source_version "
        "FROM chord_corrections" + where + " ORDER BY id DESC LIMIT ?"
    )
    args.append(limit)
    with _get_conn() as conn:
        rows = conn.execute(sql, args).fetchall()
    return {"ok": True, "count": len(rows), "rows": [dict(r) for r in rows]}


@router.get("/admin/chord/corrections/stats")
def admin_chord_corrections_stats(_: str = Depends(get_admin_user)):
    """Aggregate stats across all chord_corrections."""
    from feedback_api import _get_conn
    with _get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) AS c FROM chord_corrections").fetchone()["c"]
        by_kind = {
            r["kind"]: r["c"]
            for r in conn.execute(
                "SELECT kind, COUNT(*) AS c FROM chord_corrections GROUP BY kind"
            ).fetchall()
        }
        top_songs = [
            {"song_hash": r["song_hash"], "edits": r["c"]}
            for r in conn.execute(
                "SELECT song_hash, COUNT(*) AS c FROM chord_corrections "
                "GROUP BY song_hash ORDER BY c DESC LIMIT 20"
            ).fetchall()
        ]
        top_users = [
            {"username": r["username"], "edits": r["c"]}
            for r in conn.execute(
                "SELECT username, COUNT(*) AS c FROM chord_corrections "
                "GROUP BY username ORDER BY c DESC LIMIT 10"
            ).fetchall()
        ]
        n_distinct_songs = conn.execute(
            "SELECT COUNT(DISTINCT song_hash) AS c FROM chord_corrections"
        ).fetchone()["c"]
    return {
        "ok": True,
        "total": total,
        "n_distinct_songs": n_distinct_songs,
        "by_kind": by_kind,
        "top_edited_songs": top_songs,
        "top_users": top_users,
    }


# ---------------------------------------------------------------------------
# admin: re-run post-BTC processing (bar_arbitrator + ghost filter) without
# audio. Lets us backfill old chord JSONs that were ingested before the new
# pipeline shipped (bar_correction:False, ghost_chord_filter:None) when the
# original audio is no longer on disk.
# ---------------------------------------------------------------------------

class ChordPostprocessRequest(BaseModel):
    path: Optional[str] = None
    hash: Optional[str] = None
    apply_bar_arbitrator: bool = True
    apply_ghost_filter: bool = True
    force_arbitrator: bool = False  # passes through to arbitrate(force=...)
    ghost_mode: str = "strict"  # "strict" (default) or "loose" — see chord_detect.filter_ghost_boundary_chords
    apply_markov_rescore: bool = False  # post-BTC Markov quality swap (see ai/chord_markov_rescorer.py)
    apply_isolated_filter: bool = True  # drop isolated short chord noise (chord_noise_filter.filter_isolated_short_chords)


@router.post("/admin/chord/postprocess")
def admin_chord_postprocess(
    req: ChordPostprocessRequest, username: str = Depends(get_admin_user),
):
    """Re-run audio-free post-processing on an existing chord JSON.

    Use case: songs ingested before bar_arbitrator + ghost filter shipped
    have ``bar_correction: False / None`` and ``ghost_chord_filter: None``
    in their JSON. Re-uploading is the cleanest fix but requires the audio
    (uploads clear tmp post-analysis). This endpoint runs the same passes
    against the existing chord+downbeat+bpm data so legacy songs benefit
    without re-upload.

    Does NOT re-run BTC, beat tracker, or beat_refiner (all of which need
    audio). Boundary errors larger than the snap tolerance still need
    either re-upload or the frontend「對齊小節線」tool.
    """
    # Queue-empty guard — same as /admin/bar/recompute
    try:
        from process_queue import process_queue, queue
        if isinstance(process_queue, queue.Queue) and process_queue.qsize() > 0:
            raise HTTPException(
                status_code=409,
                detail=f"process_queue has {process_queue.qsize()} jobs in flight; retry when idle",
            )
    except HTTPException:
        raise
    except Exception:
        pass

    if req.hash:
        h = req.hash
    elif req.path:
        h = song_hash(req.path)
    else:
        raise HTTPException(status_code=400, detail="missing path or hash")

    chords_file = chord_file_for(h)
    if not chords_file.is_file():
        raise HTTPException(status_code=404, detail="chord JSON not found")

    data = json.loads(chords_file.read_text(encoding="utf-8"))

    summary = {
        "ok": True,
        "hash": h,
        "bar_arbitrator": None,
        "ghost_chord_filter": None,
        "isolated_chord_filter": None,
        "n_chords_before": len(data.get("chords") or []),
        "n_chords_after": None,
        "n_downbeats_before": len(data.get("downbeats") or []),
        "n_downbeats_after": None,
    }

    if req.apply_bar_arbitrator:
        try:
            from ai.bar_arbitrator import arbitrate, apply_to_chord_json
            from process_queue import _resolve_bar_model_path
            model_path = _resolve_bar_model_path()
            res = arbitrate(data, model_path=model_path, force=req.force_arbitrator)
            apply_to_chord_json(data, res)
            summary["bar_arbitrator"] = {
                "applied": res["applied"],
                "reason": res["reason"],
                "model_version": res.get("model_version"),
                "score_after": res.get("score_after"),
                "n_downbeats_after": len(res.get("downbeats_after") or []),
            }
        except Exception as e:
            summary["bar_arbitrator"] = {"applied": False, "error": f"{type(e).__name__}: {e}"}

    if req.apply_ghost_filter:
        try:
            from chord_detect import filter_ghost_boundary_chords
            filtered, ghost_meta = filter_ghost_boundary_chords(
                data.get("chords", []),
                data.get("downbeats", []),
                data.get("bpm"),
                mode=req.ghost_mode,
            )
            if ghost_meta.get("applied"):
                data["chords"] = filtered
                data["ghost_chord_filter"] = ghost_meta
            summary["ghost_chord_filter"] = ghost_meta
        except Exception as e:
            summary["ghost_chord_filter"] = {"applied": False, "error": f"{type(e).__name__}: {e}"}

    if req.apply_isolated_filter:
        try:
            from chord_noise_filter import filter_isolated_short_chords
            iso_filtered, iso_meta = filter_isolated_short_chords(
                data.get("chords", []),
                data.get("downbeats", []),
                data.get("bpm"),
            )
            if iso_meta.get("applied"):
                data["chords"] = iso_filtered
                data["isolated_chord_filter"] = iso_meta
            summary["isolated_chord_filter"] = iso_meta
        except Exception as e:
            summary["isolated_chord_filter"] = {"applied": False, "error": f"{type(e).__name__}: {e}"}

    if req.apply_markov_rescore:
        try:
            from ai.chord_markov_rescorer import rescore_chords
            from ai.markov import get_predictor
            rescored, mk_meta = rescore_chords(
                data.get("chords", []),
                data.get("key", "C"),
                get_predictor(),
            )
            if mk_meta.get("applied"):
                data["chords"] = rescored
                data["markov_rescoring"] = mk_meta
            summary["markov_rescoring"] = mk_meta
        except Exception as e:
            summary["markov_rescoring"] = {"applied": False, "error": f"{type(e).__name__}: {e}"}

    summary["n_chords_after"] = len(data.get("chords") or [])
    summary["n_downbeats_after"] = len(data.get("downbeats") or [])

    tmp = chords_file.with_suffix(chords_file.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, chords_file)

    return summary


# ---------------------------------------------------------------------------
# admin: AI accompaniment cache recompute (per-song manual)
# ---------------------------------------------------------------------------

class AccompanimentRecomputeRequest(BaseModel):
    path: Optional[str] = None
    hash: Optional[str] = None


@router.post("/admin/accompaniment/recompute")
def admin_accompaniment_recompute(
    req: AccompanimentRecomputeRequest,
    username: str = Depends(get_admin_user),
):
    """Invalidate AI accompaniment cache for one song.

    Deletes every ``data/accompaniments/{hash}_*.json`` file for the target
    song. Subsequent ``GET /api/ai/accompaniment`` requests will regenerate
    fresh under the current ``ACC_ENGINE_VERSION`` + STYLE_DICT, so admins
    can immediately hear the effect of pattern / engine tweaks without
    bumping the version (which would invalidate every song's cache).

    Mainly a dev-cycle helper. End-user cache invalidation already happens
    automatically when ``ACC_ENGINE_VERSION`` is bumped.

    Personal-only via ``get_admin_user`` (handles LAN bypass + token auth).
    """
    if req.hash:
        h = req.hash
    elif req.path:
        h = song_hash(req.path)
    else:
        raise HTTPException(status_code=400, detail="missing path or hash")

    acc_dir = DATA_DIR / "accompaniments"
    if not acc_dir.is_dir():
        return {"ok": True, "hash": h, "deleted": 0, "note": "no accompaniments dir"}

    deleted = 0
    errors = []
    for f in acc_dir.glob(f"{h}_*.json"):
        try:
            f.unlink()
            deleted += 1
        except Exception as e:
            errors.append({"file": f.name, "error": str(e)})

    return {
        "ok": True,
        "hash": h,
        "deleted": deleted,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# admin: chord-detect quarantine list
# ---------------------------------------------------------------------------

@router.get("/admin/chord/quarantine")
def admin_chord_quarantine_list(username: str = Depends(get_admin_user)):
    """List tracks currently quarantined from auto chord detection.

    Tracks land here when chord_detect raises "audio too short / corrupted"
    (zero-frame audio, decoder lost sync on tiny buffer, etc). The auto
    worker filters them out at queue-build time so they don't keep retrying
    every scan cycle and spamming server.log with BTC tracebacks.
    """
    from auto_worker import get_quarantine_summary
    return get_quarantine_summary()


@router.post("/admin/chord/quarantine/clear")
def admin_chord_quarantine_clear(username: str = Depends(get_admin_user)):
    """Drop the entire quarantine list. Quarantined tracks will re-enter the
    auto-detect queue on the next scan cycle. Use after fixing the underlying
    source files (re-rip, re-download, repair container)."""
    from auto_worker import clear_quarantine
    n = clear_quarantine()
    return {"ok": True, "cleared": n}


# ---------------------------------------------------------------------------
# admin: bulk degenerate-beat-data scan + upgrade (LiveChord-g1k)
#
# Phase 1b (LiveChord-cyz) revealed that ~98.5% of chord JSONs in the corpus
# (>53,000 songs) are pre-beat-tracker btc_batch ingests with chords[] but
# beats[]=downbeats=[]. Without beats[]/downbeats[] no meter-aware feature
# (Phase 1b sample-at-grid, bar_arbitrator, compound 6/8 splitter,
# beat_refiner) can do anything. g1k is the gating prerequisite.
#
# Design: thin wrapper around the existing CLI tool
# (tools/backfill_degenerate_beats.py) — the scan logic is reused
# directly, the bulk upgrade is a detached subprocess that writes a JSON
# progress file the status endpoint polls. Subprocess approach keeps the
# multi-day batch isolated from the FastAPI event loop and lets the CLI
# tool's --workers parallelism work unchanged.
# ---------------------------------------------------------------------------

import subprocess as _g1k_subprocess
import sys as _g1k_sys
from datetime import datetime as _g1k_datetime

_REPO_ROOT_PATH = Path(__file__).parent.parent
_G1K_PROGRESS_FILE = DATA_DIR / "g1k_progress.json"
_G1K_LOG_DIR = _REPO_ROOT_PATH / "tools" / "logs"
_G1K_CLI = _REPO_ROOT_PATH / "tools" / "backfill_degenerate_beats.py"


def _g1k_pid_alive(pid: int) -> bool:
    """Cross-platform PID liveness check. Returns False on any failure
    so a stale progress file with a dead PID doesn't block new runs."""
    if not pid or pid <= 0:
        return False
    try:
        if os.name == "nt":
            res = _g1k_subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5,
                encoding="utf-8", errors="replace",
            )
            return f'"{pid}"' in (res.stdout or "")
        else:
            os.kill(pid, 0)
            return True
    except Exception:
        return False


def _g1k_read_progress() -> Optional[dict]:
    if not _G1K_PROGRESS_FILE.is_file():
        return None
    try:
        return json.loads(_G1K_PROGRESS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


@router.get("/admin/beats/degenerate")
def admin_beats_degenerate_scan(
    limit: int = Query(20, ge=0, le=500),
    include_old_version: bool = Query(False),
    include_version_2: bool = Query(False),
    _: str = Depends(get_admin_user),
):
    """Walk the chord JSON corpus and return count + sample of songs
    with degenerate beat data.

    Degenerate = beats[] empty OR downbeats[] empty OR beats_source
    contains 'librosa'. Optional flags expand the set to include older
    schema versions.

    Sync def so FastAPI dispatches to thread pool. Walk takes ~30-60s
    for ~50k JSONs on local SSD via the two-pass prefilter; admin UI
    should show a loading indicator and lazy-load on card expand.
    """
    tools_dir = _REPO_ROOT_PATH / "tools"
    if str(tools_dir) not in _g1k_sys.path:
        _g1k_sys.path.insert(0, str(tools_dir))
    from backfill_degenerate_beats import scan_degenerate

    t0 = time.time()
    files = scan_degenerate(
        CHORDS_DIR,
        include_old_version=include_old_version,
        include_version_2=include_version_2,
    )
    elapsed_sec = round(time.time() - t0, 2)
    count = len(files)

    # Build sample by reading metadata from the first N matched files.
    # The scan already touched each file so OS cache makes this cheap.
    sample = []
    sample_target = min(limit, count)
    for f in files[:sample_target]:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            sample.append({
                "hash": f.stem,
                "path": d.get("path", ""),
                "beats_source": d.get("beats_source", ""),
                "beat_version": d.get("beat_version", 0),
                "n_chords": len(d.get("chords") or []),
                "n_beats": len(d.get("beats") or []),
                "n_downbeats": len(d.get("downbeats") or []),
            })
        except Exception as e:
            sample.append({"hash": f.stem, "error": f"{type(e).__name__}: {e}"})

    return {
        "ok": True,
        "count": count,
        "sample": sample,
        "elapsed_sec": elapsed_sec,
        "filter": {
            "include_old_version": include_old_version,
            "include_version_2": include_version_2,
        },
    }


class G1kUpgradeRequest(BaseModel):
    workers: int = 4
    tracker: str = "madmom"  # "madmom" | "beat_this"
    limit: int = 0  # 0 = all degenerate songs
    include_old_version: bool = False
    no_backup: bool = False


@router.post("/admin/beats/degenerate/upgrade")
def admin_beats_degenerate_upgrade(
    req: G1kUpgradeRequest,
    _: str = Depends(get_admin_user),
):
    """Spawn the backfill_degenerate_beats CLI as a detached subprocess
    to bulk-upgrade chord JSONs with degenerate beat data.

    Pre-flight (sync):
    - Refuse if a previous upgrade is still running (progress file + PID
      liveness check)
    - Validate tracker; if madmom, verify HAS_MADMOM on this server
    - Validate workers in [1, 8]

    Returns immediately with {ok, pid, log_path, started_at, cmd}.
    Subprocess writes progress to data/g1k_progress.json after each file;
    poll GET /admin/beats/degenerate/status for state. Multi-day batches
    are expected — the subprocess survives uvicorn restarts on Windows
    via DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP creation flags.
    """
    prev = _g1k_read_progress()
    if prev and not prev.get("completed_at"):
        if _g1k_pid_alive(int(prev.get("pid") or 0)):
            raise HTTPException(
                status_code=409,
                detail=(f"upgrade already running (pid={prev.get('pid')}, "
                        f"processed={prev.get('processed', 0)}/"
                        f"{prev.get('total', 0)}); poll /status or wait for "
                        f"completion"),
            )
        # Stale progress file with dead PID — leave it; new run will
        # overwrite it after spawning.

    if req.tracker not in ("madmom", "beat_this"):
        raise HTTPException(status_code=400,
                            detail=f"invalid tracker: {req.tracker!r}")
    if req.tracker == "madmom":
        try:
            from beat_snap import HAS_MADMOM as _has_madmom
        except Exception:
            _has_madmom = False
        if not _has_madmom:
            raise HTTPException(
                status_code=503,
                detail=("madmom not installed on this server; install per "
                        "CLAUDE.md 'madmom install on Windows' or use "
                        "tracker='beat_this' (requires Modal)"),
            )
    if req.workers < 1 or req.workers > 8:
        raise HTTPException(status_code=400,
                            detail="workers must be in [1, 8]")

    _G1K_LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    log_path = _G1K_LOG_DIR / f"g1k_{ts}.log"
    cmd = [
        _g1k_sys.executable,
        str(_G1K_CLI),
        "--workers", str(req.workers),
        "--tracker", req.tracker,
        "--progress-file", str(_G1K_PROGRESS_FILE),
    ]
    if req.limit > 0:
        cmd.extend(["--limit", str(req.limit)])
    if req.include_old_version:
        cmd.append("--include-old-version")
    if req.no_backup:
        cmd.append("--no-backup")

    # PYTHONIOENCODING forces the subprocess's stdout to UTF-8 so CJK
    # song paths in the log don't get cp950-mangled (CLAUDE.md
    # subprocess-encoding gotcha).
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"

    log_fp = open(log_path, "wb")  # bytes-mode so subprocess UTF-8 bytes pass through
    if os.name == "nt":
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        popen_kwargs = {
            "creationflags": DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        }
    else:
        popen_kwargs = {"start_new_session": True}

    try:
        proc = _g1k_subprocess.Popen(
            cmd,
            stdout=log_fp,
            stderr=_g1k_subprocess.STDOUT,
            stdin=_g1k_subprocess.DEVNULL,
            cwd=str(_REPO_ROOT_PATH),
            env=env,
            **popen_kwargs,
        )
    except Exception as e:
        log_fp.close()
        raise HTTPException(status_code=500,
                            detail=f"failed to spawn: {type(e).__name__}: {e}")

    return {
        "ok": True,
        "pid": proc.pid,
        "log_path": str(log_path),
        "started_at": _g1k_datetime.now().isoformat(),
        "cmd": cmd,
    }


@router.get("/admin/beats/degenerate/status")
def admin_beats_degenerate_status(_: str = Depends(get_admin_user)):
    """Return current bulk-upgrade progress. Reads g1k_progress.json
    and checks PID liveness; tails the last 20 lines of the most recent
    g1k_*.log."""
    prev = _g1k_read_progress()
    if not prev:
        return {"running": False, "progress": None, "log_tail": []}

    pid = int(prev.get("pid") or 0)
    pid_alive = _g1k_pid_alive(pid)
    completed = bool(prev.get("completed_at"))
    running = pid_alive and not completed

    log_tail = []
    log_name = ""
    if _G1K_LOG_DIR.is_dir():
        try:
            logs = sorted(
                _G1K_LOG_DIR.glob("g1k_*.log"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if logs:
                log_name = logs[0].name
                # Read last ~8KB; decode with replace so cp950-mangled bytes
                # (in case PYTHONIOENCODING wasn't honored) don't crash.
                with open(logs[0], "rb") as f:
                    f.seek(0, 2)
                    size = f.tell()
                    f.seek(max(0, size - 8192))
                    tail = f.read().decode("utf-8", errors="replace")
                log_tail = tail.splitlines()[-20:]
        except Exception:
            pass

    return {
        "running": running,
        "pid_alive": pid_alive,
        "completed": completed,
        "progress": prev,
        "log_name": log_name,
        "log_tail": log_tail,
    }


# ---------------------------------------------------------------------------
# auto-detect (Phase 4)
# ---------------------------------------------------------------------------

@router.post("/chords/detect")
async def detect_chords_api(path: str = Query(...)):
    """自動偵測音訊中的和弦，偵測完成後自動儲存"""
    full = resolve_path(path)
    if not os.path.isfile(full):
        raise HTTPException(status_code=404, detail="檔案不存在")

    # Player Tools -> Detect is an explicit user re-analysis request. Older
    # chordify/MIDI charts were early reference baselines and may be stale or
    # mismatched, so do not preserve them here; run BTC and overwrite.
    chords_file = chord_file_for(song_hash(path))

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
    beat_info = {}
    try:
        from beat_snap import analyze_and_snap_dynamic
        beat_info = await asyncio.to_thread(analyze_and_snap_dynamic, full, chords)
    except Exception:
        beat_info = {}

    sheet = {
        "path": path,
        "key": key,
        "capo": 0,
        "source": "btc",
        "chords": chords,
    }
    if beat_info.get("bpm"):
        sheet["bpm"] = round(float(beat_info["bpm"]), 1)
    if beat_info.get("beats_source"):
        sheet["beats"] = beat_info.get("beats", [])
        sheet["downbeats"] = beat_info.get("downbeats", [])
        sheet["tempo_curve"] = beat_info.get("tempo_curve", [])
        sheet["beats_source"] = beat_info["beats_source"]
        sheet["beat_version"] = beat_info.get("beat_version", 0)
    CHORDS_DIR.mkdir(parents=True, exist_ok=True)
    chords_file = chord_file_for(song_hash(path))
    chords_file.write_text(
        json.dumps(sheet, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    cache_update_entry(path)

    return {
        "ok": True,
        "path": path,
        "key": key,
        "chord_count": len(chords),
        "chords": chords,
        "source": "btc",
        "beats_source": sheet.get("beats_source"),
        "bpm": sheet.get("bpm"),
    }


# MIDI matching, MIDI search, MIDI import, and MIDI upload were all
# removed 2026-05-14 after `_midi_matches` substring false-positives
# tagged 10k chord JSONs with source=midi pointing at the wrong song
# (LiveChord-a7c / LiveChord-ebx). Auto chord detection is BTC-only;
# manual MIDI workflows are dropped entirely per user request.
# To re-introduce them, restore commit b45c7e8/7dd2e6e from git.


# 批次偵測、批次 MIDI 匯入、和弦曲目管理、和弦統計 → chord_batch.py
