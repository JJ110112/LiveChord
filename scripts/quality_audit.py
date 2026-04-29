"""Quality audit: rank chord JSONs by "suspicion score" so manual labeling
can focus on the songs most likely to feed beat_refiner v2 training.

Suspicion factors (each 0-3 points, summed):
  - bpm extremity: stored bpm outside [60, 180]
  - cv high: downbeat regularity coefficient of variation > 0.10
  - alignment low: chord-change vs downbeat alignment < 0.50
  - bpb non-integer: median(downbeat_gap)/median(beat_gap) far from 3/4/6/8
  - tempo volatile: std of tempo_curve > 5 BPM
  - sub-bar chord ratio high: > 50% of chord changes are NOT on downbeats
  - short chord ratio high: > 30% of chords are < 0.5 beat
  - beats_source weak: librosa-fallback (vs madmom/beat_this)

Output:
  - CSV with one row per applied/relevant song, sortable by total score
  - headline reasons string per song so labeling can see WHY it's suspect
  - player URL for direct listen+verify

Usage:
  python scripts/quality_audit.py --root V:/data/chords --top 200 \
      --out G:/livechord/quality_audit.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# Tunables. Pop-friendly defaults; jazz/classical may legitimately fail
# some of these without being "broken".
_BPM_LOW = 60.0
_BPM_HIGH = 180.0
_CV_THRESH = 0.10
_ALIGN_THRESH = 0.50
_BPB_OK = (3.0, 5.0)  # 3/4 and 4/4; 6/8 and 8/8 are caught separately
_TEMPO_STD_THRESH = 5.0
_SUB_BAR_RATIO_THRESH = 0.50
_SHORT_CHORD_RATIO_THRESH = 0.30
_DB_ALIGN_TOL_SEC = 0.10  # chord change within this distance of any downbeat = "on db"
_PLAYER_BASE = "http://192.168.50.6:8800/player?path="


def _gap_stats(times: List[float]) -> Tuple[Optional[float], Optional[float]]:
    """Return (median_gap, cv) where cv = std/mean. None when too few points."""
    if len(times) < 3:
        return None, None
    gaps = sorted([times[i + 1] - times[i] for i in range(len(times) - 1) if times[i + 1] - times[i] > 0.05])
    if len(gaps) < 2:
        return None, None
    med = gaps[len(gaps) // 2]
    mean = sum(gaps) / len(gaps)
    if mean <= 0:
        return None, None
    var = sum((g - mean) ** 2 for g in gaps) / len(gaps)
    std = var ** 0.5
    cv = std / mean
    return med, cv


def _alignment(chord_changes: List[float], downbeats: List[float],
               tol: float = _DB_ALIGN_TOL_SEC) -> float:
    if not chord_changes or not downbeats:
        return 0.0
    db_sorted = sorted(downbeats)
    hits = 0
    for t in chord_changes:
        # Inline bisect for speed
        lo, hi = 0, len(db_sorted)
        while lo < hi:
            mid = (lo + hi) // 2
            if db_sorted[mid] < t:
                lo = mid + 1
            else:
                hi = mid
        nearest = float("inf")
        if lo < len(db_sorted):
            nearest = min(nearest, abs(db_sorted[lo] - t))
        if lo > 0:
            nearest = min(nearest, abs(db_sorted[lo - 1] - t))
        if nearest <= tol:
            hits += 1
    return hits / len(chord_changes)


def _score_one(p: Path) -> Optional[Dict]:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    chords = d.get("chords") or []
    beats = d.get("beats") or []
    dbs = d.get("downbeats") or []
    bpm = float(d.get("bpm") or 0)
    if len(chords) < 4 or len(beats) < 8 or len(dbs) < 4 or bpm <= 0:
        return None  # not enough data to score

    score = 0
    reasons: List[str] = []
    metrics: Dict[str, float] = {}

    # 1. BPM extremity
    if bpm < _BPM_LOW or bpm > _BPM_HIGH:
        score += 2
        reasons.append(f"bpm={bpm:.0f}")
    metrics["bpm"] = bpm

    # 2. Downbeat regularity (cv)
    bar_gap, db_cv = _gap_stats(dbs)
    if db_cv is not None and db_cv > _CV_THRESH:
        # 1 point at 0.10-0.20, 2 at 0.20-0.30, 3 above
        cv_score = 1 + min(2, int((db_cv - _CV_THRESH) / 0.10))
        score += cv_score
        reasons.append(f"cv={db_cv:.2f}")
    metrics["db_cv"] = db_cv if db_cv is not None else -1.0

    # 3. Chord/downbeat alignment
    chord_changes = [c["time"] for c in chords[1:] if c.get("time") is not None]
    align = _alignment(chord_changes, dbs)
    if align < _ALIGN_THRESH:
        # 3 at <0.20, 2 at 0.20-0.35, 1 at 0.35-0.50
        if align < 0.20:
            score += 3
        elif align < 0.35:
            score += 2
        else:
            score += 1
        reasons.append(f"align={align:.2f}")
    metrics["chord_db_align"] = align

    # 4. Beats per bar (4/4 ideal; some songs are legitimately 3/4 or 6/8)
    beat_gap, _ = _gap_stats(beats)
    bpb = (bar_gap / beat_gap) if (bar_gap and beat_gap) else None
    if bpb is not None:
        # Allow 3/4 (2.5-3.5) and 4/4 (3.5-4.5)
        if not (2.5 <= bpb <= 4.5 or 5.5 <= bpb <= 6.5 or 7.5 <= bpb <= 8.5):
            score += 3
            reasons.append(f"bpb={bpb:.2f}")
        elif abs(bpb - round(bpb)) > 0.20:
            score += 1
            reasons.append(f"bpb~{bpb:.2f}")
    metrics["bpb"] = bpb if bpb is not None else -1.0

    # 5. Tempo curve volatility — entries are {"t": s, "bpm": float}
    tempo_curve = d.get("tempo_curve") or []
    if len(tempo_curve) > 5:
        tcs: List[float] = []
        for entry in tempo_curve:
            if isinstance(entry, dict):
                v = entry.get("bpm")
                if v is not None:
                    try:
                        tcs.append(float(v))
                    except (TypeError, ValueError):
                        pass
            elif isinstance(entry, (int, float)):
                tcs.append(float(entry))
        if len(tcs) > 5:
            mean_t = sum(tcs) / len(tcs)
            std_t = (sum((t - mean_t) ** 2 for t in tcs) / len(tcs)) ** 0.5
            if std_t > _TEMPO_STD_THRESH:
                score += min(2, int(std_t / 5))
                reasons.append(f"tempo_std={std_t:.1f}")
            metrics["tempo_std"] = std_t

    # 6. Sub-bar chord change ratio
    if chord_changes:
        on_db = sum(
            1 for t in chord_changes
            if any(abs(d - t) <= _DB_ALIGN_TOL_SEC for d in dbs)
        )
        sub_ratio = 1.0 - (on_db / len(chord_changes))
        if sub_ratio > _SUB_BAR_RATIO_THRESH:
            score += 1
            reasons.append(f"sub_bar={sub_ratio:.0%}")
        metrics["sub_bar_ratio"] = sub_ratio

    # 7. Short chord ratio
    if beat_gap and beat_gap > 0:
        n_short = sum(
            1 for c in chords
            if c.get("end") is not None and c.get("time") is not None
            and (c["end"] - c["time"]) < 0.5 * beat_gap
        )
        short_ratio = n_short / len(chords)
        if short_ratio > _SHORT_CHORD_RATIO_THRESH:
            score += 1
            reasons.append(f"short_chords={short_ratio:.0%}")
        metrics["short_chord_ratio"] = short_ratio

    # 8. Beats source quality (low-confidence trackers)
    source = (d.get("beats_source") or "").lower()
    if "librosa" in source or "fallback" in source:
        score += 2
        reasons.append(f"src={source}")
    metrics["source"] = source  # type: ignore

    # Status flags from existing post-processing (don't score, just record)
    br = d.get("beat_refiner") or {}
    bc = d.get("bar_correction") or {}
    bpm_corr = d.get("bpm_correction") or {}
    flags = []
    if br.get("applied"):
        flags.append("br_applied")
    if bc.get("applied"):
        flags.append("bar_arb")
    if bpm_corr.get("applied"):
        flags.append("bpm_halved")

    return {
        "score": score,
        "hash": p.stem,
        "path": d.get("path", ""),
        "reasons": "|".join(reasons),
        "flags": "|".join(flags),
        **{f"m_{k}": v for k, v in metrics.items()},
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=Path("V:/data/chords"))
    ap.add_argument("--top", type=int, default=200,
                    help="Output top-N most suspicious songs.")
    ap.add_argument("--out", type=Path, default=Path("G:/livechord/quality_audit.csv"),
                    help="CSV output path.")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    paths = list(args.root.glob("*/*.json"))
    print(f"scanning {len(paths):,} JSONs...", file=sys.stderr)

    results: List[Dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for i, f in enumerate(as_completed([ex.submit(_score_one, p) for p in paths]), 1):
            r = f.result()
            if r:
                results.append(r)
            if i % 5000 == 0:
                print(f"  {i:,}/{len(paths):,}  scored so far: {len(results):,}", file=sys.stderr)

    # Filter out "non-music" garbage before ranking. These are songs where the
    # detector had nothing to detect (ambient pads, spoken commentary, 8Hz
    # therapy tones, soundscape). They top the suspicion ranking trivially but
    # aren't useful for beat_refiner training — there's no pulse to learn.
    #
    # Heuristic: a real-song candidate must have at least minimal evidence of
    # rhythmic + harmonic content.
    def _is_real_music(r: Dict) -> bool:
        bpm = r.get("m_bpm", 0) or 0
        align = r.get("m_chord_db_align", 0) or 0
        short_ratio = r.get("m_short_chord_ratio", 0) or 0
        if bpm < 50 or bpm > 220:
            return False  # outside any pop/rock/jazz range
        if align < 0.10:
            return False  # chord changes have no relation to detected beats
        if short_ratio > 0.60:
            return False  # most "chords" are sub-beat fragments — not music
        return True

    real = [r for r in results if _is_real_music(r)]
    garbage = len(results) - len(real)
    print(f"\nfiltered {garbage:,} non-music tracks (no pulse / no harmony)", file=sys.stderr)

    # Categorize each suspect by dominant failure mode for diverse labeling.
    def _category(r: Dict) -> str:
        bpm = r.get("m_bpm", 0) or 0
        align = r.get("m_chord_db_align", 0) or 0
        bpb = r.get("m_bpb", 0) or 0
        tempo_std = r.get("m_tempo_std", 0) or 0
        if bpm < 70:
            return "halftime?"  # likely real BPM is doubled
        if bpm > 160:
            return "doubletime?"  # likely real BPM is halved
        if 0 < bpb < 3.0 or bpb > 5.0:
            return "wrong-meter"  # 4/4 misread, etc
        if tempo_std > 15:
            return "rubato"  # tempo varies a lot
        if align < 0.30:
            return "off-grid"  # chords don't relate to detected beats
        return "other"

    for r in real:
        r["category"] = _category(r)

    # Sort by score desc, ties broken by alignment asc (worse first)
    real.sort(key=lambda r: (-r["score"], r.get("m_chord_db_align", 1.0)))

    # Stratified output: rather than just the top-N most extreme, take samples
    # across the suspicion spectrum so user sees different failure modes.
    # 60% from high (top of ranked list), 30% from medium (middle band), 10%
    # from low-ish (just-suspicious). The middle and low buckets surface the
    # cases where a small training intervention has the most impact.
    n_high = int(args.top * 0.6)
    n_mid = int(args.top * 0.3)
    n_low = args.top - n_high - n_mid
    high = real[:n_high]
    mid_start = max(n_high, len(real) // 4)
    mid = real[mid_start: mid_start + n_mid]
    low_start = max(mid_start + n_mid, len(real) // 2)
    low = real[low_start: low_start + n_low]
    top = high + mid + low

    # Write CSV
    args.out.parent.mkdir(parents=True, exist_ok=True)
    keys = ["score", "category", "reasons", "flags", "hash", "path", "player_url",
            "m_bpm", "m_db_cv", "m_chord_db_align", "m_bpb",
            "m_tempo_std", "m_sub_bar_ratio", "m_short_chord_ratio", "m_source"]
    with args.out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(keys)
        for r in top:
            row = []
            for k in keys:
                if k == "player_url":
                    row.append(_PLAYER_BASE + urllib.parse.quote(r.get("path", ""), safe=""))
                else:
                    row.append(r.get(k, ""))
            w.writerow(row)

    # Summary stats
    print(f"\nscored {len(results):,} songs (skipped {len(paths) - len(results):,} for insufficient data)", file=sys.stderr)
    print(f"\nscore distribution:", file=sys.stderr)
    score_buckets = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0, 5: 0, 6: 0, 7: 0, 8: 0, 9: 0, 10: 0}
    for r in results:
        s = min(10, r["score"])
        score_buckets[s] = score_buckets.get(s, 0) + 1
    for s, n in sorted(score_buckets.items()):
        bar = "#" * min(60, n // 100)
        print(f"  score {s:>2}: {n:>5}  {bar}", file=sys.stderr)
    print(f"\ntop {args.top} written to {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
