"""Compare legacy vs current bar-phase/split behavior for audio files.

The script generates (or reuses) a temporary chord JSON per song, then runs:
  - legacy serve-time pipeline: old phase scoring + splitter without guard
  - current serve-time pipeline: fragment-aware phase scoring + splitter guard

It prints a compact before/after table focused on 1+3 / 3+1 / 4+1 fragments.
Temporary analysis cache lives outside the repo by default:
  C:/tmp/livechord_bar_compare/<hash>.json
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ROOT))

from bar_phase_corrector import (  # noqa: E402
    _ALIGN_DROP_TOL,
    _ALIGN_TOL_SEC,
    _ALREADY_CLEAN_ALIGN,
    _CV_MESSY,
    _MIN_BEST_ALIGN,
    _MIN_CHORD_CHANGES,
    _MIN_GAIN,
    _alignment,
    _gap_cv,
    _grid_from_phase,
)
from chord_noise_filter import maybe_filter_for_serve  # noqa: E402
from chord_splitter import (  # noqa: E402
    _drop_small_segment_boundaries,
    _interior_downbeats,
    _interpolate_oversized_gaps,
    _median_bar_gap,
    _resolve_split_downbeats,
    split_chords_at_bars,
)


CACHE_DIR = Path(os.environ.get("LIVECHORD_COMPARE_CACHE", "C:/tmp/livechord_bar_compare"))
DEFAULT_SONGS = [
    ROOT / "songs" / "過完冬季.flac",
    ROOT / "songs" / "Night Birds.flac",
]


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        h.update(f.read(1024 * 1024))
    return h.hexdigest()[:16]


def _load_or_analyze(audio: Path) -> Dict:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f"{_hash_file(audio)}.json"
    if cache.is_file():
        sheet = json.loads(cache.read_text(encoding="utf-8"))
        if sheet.get("beats") and sheet.get("downbeats"):
            return sheet
        sheet = _add_beat_this(audio, sheet)
        cache.write_text(json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8")
        return sheet

    from chord_detect import detect_chords_and_key_isolated
    from process_queue import _ingest_beats_modal_or_local, _probe_audio_duration

    print(f"analyzing {audio.name} ...", flush=True)
    chords, key = detect_chords_and_key_isolated(str(audio))
    sheet = {
        "path": str(audio),
        "title": audio.stem,
        "key": key,
        "capo": 0,
        "source": "compare-script",
        "chords": chords,
    }
    dur = _probe_audio_duration(str(audio))
    if dur > 0:
        sheet["duration"] = round(dur, 3)

    beat_info = _ingest_beats_modal_or_local(str(audio), chords, f"compare-{audio.stem}")
    _merge_beat_info(sheet, beat_info)
    if not sheet.get("beats") or not sheet.get("downbeats"):
        sheet = _add_beat_this(audio, sheet)

    cache.write_text(json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8")
    return sheet


def _merge_beat_info(sheet: Dict, beat_info: Dict) -> None:
    if beat_info.get("bpm"):
        sheet["bpm"] = round(float(beat_info["bpm"]), 1)
    if beat_info.get("beats_source"):
        sheet["beats"] = beat_info.get("beats", [])
        sheet["downbeats"] = beat_info.get("downbeats", [])
        sheet["tempo_curve"] = beat_info.get("tempo_curve", [])
        sheet["beats_source"] = beat_info.get("beats_source")
        sheet["beat_version"] = beat_info.get("beat_version", 0)
    if beat_info.get("bpm_correction"):
        sheet["bpm_correction"] = beat_info["bpm_correction"]


def _add_beat_this(audio: Path, sheet: Dict) -> Dict:
    """Run local beat_this for comparison when ingest fallback had no grid."""
    try:
        import numpy as np
        import torch
        from beat_snap import _snap_to_grid, BEAT_VERSION
        from migrate_add_beats_via_beat_this import (
            _clean_beats,
            _compute_bpm,
            _compute_tempo_curve,
            _get_predictor,
        )
    except Exception as e:
        print(f"beat_this unavailable for {audio.name}: {type(e).__name__}: {e}", flush=True)
        return sheet

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"running beat_this for {audio.name} on {device} ...", flush=True)
    pred = _get_predictor(device)
    beats_arr, downbeats_arr = pred(str(audio))
    beats = _clean_beats([float(x) for x in beats_arr])
    downbeats = [float(x) for x in downbeats_arr]
    if not beats:
        return sheet
    bpm = _compute_bpm(beats)
    tempo_curve = _compute_tempo_curve(beats)
    chords = sheet.get("chords") or []
    if chords:
        beat_arr = np.sort(np.asarray(beats, dtype=np.float64))
        _snap_to_grid(chords, beat_arr)
    sheet["bpm"] = round(bpm, 1)
    sheet["beats"] = beats
    sheet["downbeats"] = downbeats
    sheet["tempo_curve"] = tempo_curve
    sheet["beats_source"] = "beat_this"
    sheet["beat_version"] = BEAT_VERSION
    return sheet


def _legacy_search_best_phase(chord_changes: List[float], beats: List[float], bpm: float):
    best_bpb, best_phase, best_align = 4, 0, -1.0
    spb = 60.0 / bpm if bpm > 0 else 0.0
    for bpb in (3, 4):
        for phase in range(bpb):
            grid = _grid_from_phase(beats, phase, bpb)
            if len(grid) < 4:
                continue
            gaps = sorted([grid[i + 1] - grid[i] for i in range(len(grid) - 1)])
            if len(gaps) < 2:
                continue
            bar_gap = gaps[len(gaps) // 2]
            if spb > 0:
                computed_bpb = bar_gap / spb
                if not (2.5 <= computed_bpb <= 4.5 or 5.5 <= computed_bpb <= 6.5):
                    continue
            align = _alignment(chord_changes, grid, _ALIGN_TOL_SEC)
            if align > best_align:
                best_bpb, best_phase, best_align = bpb, phase, align
    return best_bpb, best_phase, best_align


def _legacy_phase_correct(data: Dict) -> Dict:
    chords = data.get("chords") or []
    beats = data.get("beats") or []
    current = data.get("downbeats") or []
    meta = {
        "applied": False,
        "reason": "",
        "align_before": 0.0,
        "align_after": 0.0,
        "bpb_after": 4,
        "phase_after": 0,
    }
    if len(chords) < _MIN_CHORD_CHANGES + 1 or len(beats) < 32:
        meta["reason"] = "insufficient-data"
        data["bar_phase_meta"] = meta
        return data
    chord_changes = [c["time"] for c in chords[1:] if c.get("time") is not None]
    if len(chord_changes) < _MIN_CHORD_CHANGES:
        meta["reason"] = "too-few-chord-changes"
        data["bar_phase_meta"] = meta
        return data
    current_align = _alignment(chord_changes, current, _ALIGN_TOL_SEC)
    current_cv = _gap_cv(current)
    if current_align >= _ALREADY_CLEAN_ALIGN and (current_cv is None or current_cv < _CV_MESSY):
        meta["reason"] = "already-clean"
        data["bar_phase_meta"] = meta
        return data
    bpm = float(data.get("bpm") or 0)
    bpb, phase, best_align = _legacy_search_best_phase(chord_changes, beats, bpm)
    meta.update({
        "align_before": round(current_align, 4),
        "align_after": round(best_align, 4),
        "bpb_after": bpb,
        "phase_after": phase,
    })
    gain = best_align - current_align
    if gain >= _MIN_GAIN or (
        current_cv is not None and current_cv >= _CV_MESSY
        and gain >= -_ALIGN_DROP_TOL and best_align >= _MIN_BEST_ALIGN
    ):
        data["downbeats"] = _grid_from_phase(beats, phase, bpb)
        meta["applied"] = True
        meta["reason"] = "legacy-phase-fix"
    else:
        meta["reason"] = "legacy-no-fix"
    data["bar_phase_meta"] = meta
    return data


def _legacy_split(data: Dict) -> Dict:
    resolved = _resolve_split_downbeats(data)
    chords = data.get("chords") or []
    if resolved is None:
        data["auto_split_meta"] = {
            "applied": False,
            "reason": "low-confidence-downbeats",
            "before": len(chords),
            "after": len(chords),
        }
        return data
    before = len(chords)
    data["chords"] = _legacy_split_chords_at_bars(chords, resolved)
    data["auto_split_meta"] = {
        "applied": True,
        "reason": "legacy-ok",
        "before": before,
        "after": len(data["chords"]),
    }
    return data


def _legacy_split_chords_at_bars(chords: List[Dict], downbeats: List[float]) -> List[Dict]:
    db_sorted = sorted(float(d) for d in downbeats if d is not None)
    bar_gap = _median_bar_gap(db_sorted)
    min_seg = (bar_gap * 0.20) if bar_gap else 0.0
    out: List[Dict] = []
    for chord in chords:
        start = chord.get("time")
        end = chord.get("end")
        if start is None or end is None or end <= start:
            out.append(chord)
            continue
        interior = _interior_downbeats(float(start), float(end), db_sorted)
        boundaries = [float(start)] + interior + [float(end)]
        if not interior and not bar_gap:
            out.append(chord)
            continue
        if bar_gap:
            boundaries = _interpolate_oversized_gaps(boundaries, bar_gap)
        if min_seg > 0:
            boundaries = _drop_small_segment_boundaries(boundaries, min_seg)
        if len(boundaries) <= 2:
            out.append(chord)
            continue
        for i in range(len(boundaries) - 1):
            seg = dict(chord)
            seg["time"] = boundaries[i]
            seg["end"] = boundaries[i + 1]
            seg["auto_split"] = True
            out.append(seg)
    return out


def _current_pipeline(data: Dict) -> Dict:
    from bar_phase_corrector import maybe_correct_for_serve
    from chord_splitter import maybe_split_for_serve

    maybe_correct_for_serve(data)
    maybe_filter_for_serve(data)
    maybe_split_for_serve(data)
    return data


def _legacy_pipeline(data: Dict) -> Dict:
    _legacy_phase_correct(data)
    maybe_filter_for_serve(data)
    _legacy_split(data)
    return data


def _fragment_patterns(chords: List[Dict], bpm: float) -> Dict[str, int]:
    if bpm <= 0:
        return {}
    spb = 60.0 / bpm
    out: Dict[str, int] = {}
    i = 0
    while i < len(chords):
        j = i + 1
        group = [chords[i]]
        while j < len(chords) and chords[j].get("chord") == chords[i].get("chord"):
            group.append(chords[j])
            j += 1
        if len(group) > 1 and any(g.get("auto_split") for g in group):
            total = (float(group[-1].get("end", group[-1].get("time", 0)))
                     - float(group[0].get("time", 0))) / spb
            segs = [
                (float(g.get("end", g.get("time", 0))) - float(g.get("time", 0))) / spb
                for g in group
            ]
            if 3.5 <= total <= 4.5 and min(segs) <= 1.25:
                name = "1+3" if round(segs[0]) <= 1 else "3+1"
                out[name] = out.get(name, 0) + 1
            elif 4.5 <= total <= 5.5 and min(segs) <= 1.25:
                name = "1+4" if round(segs[0]) <= 1 else "4+1"
                out[name] = out.get(name, 0) + 1
        i = j
    return out


def _summarize(label: str, data: Dict) -> Dict:
    bpm = float(data.get("bpm") or 0)
    patterns = _fragment_patterns(data.get("chords") or [], bpm)
    return {
        "label": label,
        "chords": len(data.get("chords") or []),
        "auto_added": (data.get("auto_split_meta", {}).get("after", 0)
                       - data.get("auto_split_meta", {}).get("before", 0)),
        "patterns": patterns,
        "bar_reason": data.get("bar_phase_meta", {}).get("reason", ""),
        "split_reason": data.get("auto_split_meta", {}).get("reason", ""),
        "bad_fragments": data.get("bar_phase_meta", {}).get("bad_fragments", 0),
        "fragment_penalty": data.get("bar_phase_meta", {}).get("fragment_penalty", 0.0),
    }


def main(argv: List[str]) -> int:
    songs = [Path(p) for p in argv] if argv else DEFAULT_SONGS
    print("song\tmode\tchords\tauto_added\tpatterns\tbar_reason\tsplit_reason\tphase_bad\tphase_penalty")
    for audio in songs:
        if not audio.is_file():
            raise SystemExit(f"missing audio: {audio}")
        base = _load_or_analyze(audio)
        legacy = _legacy_pipeline(copy.deepcopy(base))
        current = _current_pipeline(copy.deepcopy(base))
        for row in (_summarize("before", legacy), _summarize("after", current)):
            print(
                f"{audio.name}\t{row['label']}\t{row['chords']}\t{row['auto_added']}\t"
                f"{json.dumps(row['patterns'], ensure_ascii=False)}\t{row['bar_reason']}\t"
                f"{row['split_reason']}\t{row['bad_fragments']}\t{row['fragment_penalty']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
