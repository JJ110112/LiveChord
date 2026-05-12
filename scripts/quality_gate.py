"""Stratified LiveChord quality gate over a library root.

This is a stage gate, not a ground-truth evaluator. It answers:

  "Are beat grids, chord card splitting, source arbitration, and coverage
   good enough that the current phase can stop relying on one-song bug reports?"

It samples Z:/ by first-level category, excludes non-target categories, applies
the same serve-time repair pipeline used by the player, then emits JSON/CSV/HTML
reports with pass/fail thresholds.

Example:
  python scripts/quality_gate.py --library-root Z:/ --data-root V:/data \
      --sample 1000 --out-dir reports/quality_gate
"""

from __future__ import annotations

import argparse
import copy
import csv
import html
import json
import math
import random
import statistics
import sys
import urllib.parse
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BACKEND))

from bar_phase_corrector import fragmentation_risk, maybe_correct_for_serve  # noqa: E402
from bpm_sanity import maybe_apply_structural_bpm_correction_for_serve  # noqa: E402
from chord_cache import chord_file_for, song_hash  # noqa: E402
from chord_noise_filter import maybe_filter_for_serve  # noqa: E402
from chord_splitter import maybe_split_for_serve  # noqa: E402
from chord_tail_extender import maybe_extend_tail_for_serve  # noqa: E402
from global_chord_arbiter import maybe_analyze_global_structure_for_serve  # noqa: E402


AUDIO_EXTS = {".flac", ".mp3", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".wma"}
DEFAULT_EXCLUDE = {"classics", "sleep"}
DEFAULT_WEIGHTS = {"POP": 0.50, "Jazz": 0.25, "__OTHER__": 0.25}
PLAYER_BASE = "http://192.168.50.6:8800/player?path="


THRESHOLDS = {
    "POP": {"pass_rate": 0.95, "severe_rate": 0.02, "avg_fragments": 1.0},
    "Jazz": {"pass_rate": 0.90, "severe_rate": 0.04, "avg_fragments": 1.8},
    "__OTHER__": {"pass_rate": 0.88, "severe_rate": 0.05, "avg_fragments": 1.8},
    "__ALL__": {
        "legacy_midi_rate": 0.03,
        "long_card_rate": 0.01,
        "tail_gap_rate": 0.01,
        "fragment_song_rate": 0.03,
    },
}


@dataclass
class Track:
    category: str
    rel_path: str
    abs_path: Path


def _norm_category(name: str) -> str:
    if name.lower() == "pop":
        return "POP"
    if name.lower() == "jazz":
        return "Jazz"
    return name


def _iter_audio_by_category(library_root: Path, exclude: set[str]) -> Dict[str, List[Track]]:
    by_cat: Dict[str, List[Track]] = {}
    for cat_dir in sorted(p for p in library_root.iterdir() if p.is_dir()):
        if cat_dir.name.lower() in exclude:
            continue
        category = _norm_category(cat_dir.name)
        tracks: List[Track] = []
        for f in cat_dir.rglob("*"):
            if f.is_file() and f.suffix.lower() in AUDIO_EXTS:
                rel = f.relative_to(library_root).as_posix()
                tracks.append(Track(category=category, rel_path=rel, abs_path=f))
        if tracks:
            by_cat[category] = tracks
    return by_cat


def _allocate_sample(by_cat: Dict[str, List[Track]], total: int, rng: random.Random) -> List[Track]:
    if total <= 0:
        return [t for tracks in by_cat.values() for t in tracks]

    selected: List[Track] = []
    used_ids = set()

    def add_from(cat: str, n: int) -> None:
        if n <= 0 or cat not in by_cat:
            return
        pool = by_cat[cat]
        take = min(n, len(pool))
        for t in rng.sample(pool, take):
            tid = t.rel_path
            if tid not in used_ids:
                selected.append(t)
                used_ids.add(tid)

    pop_n = round(total * DEFAULT_WEIGHTS["POP"])
    jazz_n = round(total * DEFAULT_WEIGHTS["Jazz"])
    other_budget = max(0, total - pop_n - jazz_n)
    add_from("POP", pop_n)
    add_from("Jazz", jazz_n)

    other_cats = [c for c in by_cat if c not in {"POP", "Jazz"}]
    other_total = sum(len(by_cat[c]) for c in other_cats)
    for cat in other_cats:
        quota = max(1, round(other_budget * (len(by_cat[cat]) / other_total))) if other_total else 0
        add_from(cat, quota)

    if len(selected) < total:
        remaining = [t for tracks in by_cat.values() for t in tracks if t.rel_path not in used_ids]
        selected.extend(rng.sample(remaining, min(total - len(selected), len(remaining))))

    return selected[:total]


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def _apply_serve_pipeline(data: Dict[str, Any]) -> Dict[str, Any]:
    served = copy.deepcopy(data)
    maybe_apply_structural_bpm_correction_for_serve(served)
    maybe_correct_for_serve(served)
    maybe_analyze_global_structure_for_serve(served)
    maybe_filter_for_serve(served)
    maybe_extend_tail_for_serve(served)
    maybe_split_for_serve(served)
    return served


def _gap_cv(values: Iterable[float]) -> Optional[float]:
    vals = sorted(float(v) for v in values if v is not None)
    gaps = [vals[i + 1] - vals[i] for i in range(len(vals) - 1) if vals[i + 1] - vals[i] > 0.05]
    if len(gaps) < 3:
        return None
    mean = statistics.fmean(gaps)
    if mean <= 0:
        return None
    return statistics.pstdev(gaps) / mean


def _median_gap(values: Iterable[float]) -> Optional[float]:
    vals = sorted(float(v) for v in values if v is not None)
    gaps = [vals[i + 1] - vals[i] for i in range(len(vals) - 1) if vals[i + 1] - vals[i] > 0.05]
    return statistics.median(gaps) if gaps else None


def _float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _display_dot_count(chord: Dict[str, Any], bpm: float, bpb: int = 4) -> int:
    """Approximate player.js _virtualBeats for quality scoring.

    The gate should judge what the user actually sees. The frontend snaps
    nominal one-bar and half-bar cards to musical dot counts, so an internal
    downbeat that would have been a raw 1+3 split is not necessarily a visible
    failure anymore.
    """
    forced = _float(chord.get("display_beats"))
    if 1 <= forced <= 16:
        return int(round(forced))
    if bpm <= 0 or bpb <= 0:
        return 0
    start = _float(chord.get("time"))
    end = _float(chord.get("end"), start)
    if end <= start:
        return 0
    beats = (end - start) / (60.0 / bpm)
    if chord.get("auto_split") and beats >= bpb * 0.65:
        return bpb
    half = bpb / 2
    if half >= 1 and abs(beats - half) < 0.55:
        return int(round(half))
    bars_approx = beats / bpb
    rounded_bars = round(bars_approx)
    bar_snap_tol = 0.30 if rounded_bars == 1 else 0.20
    if rounded_bars >= 1 and abs(bars_approx - rounded_bars) < bar_snap_tol:
        return min(16, int(rounded_bars * bpb))
    return max(1, min(16, int(round(beats))))


def _visible_fragment_risk(chords: List[Dict[str, Any]], bpm: float, bpb: int = 4) -> Dict[str, Any]:
    """Count visible POP card/dot failures after the serve pipeline.

    Raw downbeat fragmentation is still useful diagnostic data, but the stage
    gate should fail a song only when the player would display suspicious
    short/long beat-dot cards: one-bar cards not showing 4 dots, half-bar cards
    not showing 2 dots, or same-chord adjacent fragments that survived merging.
    """
    result = {"bad_fragments": 0, "patterns": {}, "examples": []}
    if not chords or bpm <= 0:
        return result
    spb = 60.0 / bpm

    def add(pattern: str, chord: Dict[str, Any], detail: str) -> None:
        result["bad_fragments"] += 1
        result["patterns"][pattern] = result["patterns"].get(pattern, 0) + 1
        if len(result["examples"]) < 5:
            result["examples"].append({
                "time": round(_float(chord.get("time")), 3),
                "end": round(_float(chord.get("end"), _float(chord.get("time"))), 3),
                "chord": chord.get("chord"),
                "detail": detail,
            })

    for c in chords:
        start = _float(c.get("time"))
        end = _float(c.get("end"), start)
        if end <= start:
            continue
        dur_beats = (end - start) / spb
        dots = _display_dot_count(c, bpm, bpb)
        one_bar_low = bpb - 0.5
        one_bar_high = bpb + 0.5
        half_bar = bpb / 2
        if one_bar_low <= dur_beats <= one_bar_high and dots != bpb:
            add(f"one-bar-{dots}-dots", c, f"{dur_beats:.2f} beats -> {dots} dots")
        elif (half_bar - 0.45) <= dur_beats <= (half_bar + 0.45) and dots != int(round(half_bar)):
            add(f"half-bar-{dots}-dots", c, f"{dur_beats:.2f} beats -> {dots} dots")
        elif one_bar_high < dur_beats <= (bpb + 1.5) and not c.get("auto_split") and dots > bpb:
            add(f"five-beat-{dots}-dots", c, f"{dur_beats:.2f} beats -> {dots} dots")

    for prev, cur in zip(chords, chords[1:]):
        if prev.get("chord") != cur.get("chord"):
            continue
        prev_end = _float(prev.get("end"), _float(prev.get("time")))
        cur_start = _float(cur.get("time"))
        if abs(prev_end - cur_start) > 0.08:
            continue
        start = _float(prev.get("time"))
        end = _float(cur.get("end"), cur_start)
        seg_a = (prev_end - start) / spb
        seg_b = (end - cur_start) / spb
        total = seg_a + seg_b
        # Same-chord 1+3 / 3+1 inside one notated bar is the suspicious
        # player artifact. A full bar plus a short tail (4+1 in 4/4) can be
        # an intentional long-hold split from the global arbiter or a readable
        # remix/ostinato hold, so do not count it as visible fragmentation.
        if (bpb - 0.7) <= total <= (bpb + 0.7) and min(seg_a, seg_b) <= 1.35:
            left = round(seg_a)
            right = round(seg_b)
            add(f"same-chord-{left}+{right}", prev, f"{seg_a:.2f}+{seg_b:.2f} beats")

    return result


def _issue(issues: List[Dict[str, Any]], issue_type: str, severity: str, detail: str) -> None:
    issues.append({"type": issue_type, "severity": severity, "detail": detail})


def _rate_den(total: int) -> int:
    return max(1, total)


def _meter_class(bpb: Optional[float]) -> int:
    if bpb is None or bpb <= 0:
        return 4
    if 2.5 <= bpb < 3.3:
        return 3
    if 3.3 <= bpb <= 4.7:
        return 4
    if 5.2 <= bpb <= 6.8:
        return 6
    if 7.5 <= bpb <= 8.5:
        return 8
    return 4


def _score_track(track: Track, data_root: Path) -> Dict[str, Any]:
    h = song_hash(track.rel_path)
    chord_path = chord_file_for(h, root=data_root / "chords")
    issues: List[Dict[str, Any]] = []
    row: Dict[str, Any] = {
        "category": track.category,
        "path": track.rel_path,
        "hash": h,
        "chord_file": str(chord_path),
        "exists": chord_path.is_file(),
        "url": PLAYER_BASE + urllib.parse.quote(track.rel_path),
    }

    if not chord_path.is_file():
        _issue(issues, "missing_chords", "severe", "no official chord json")
        row.update(_finalize(row, issues, {}))
        return row

    raw = _load_json(chord_path)
    if not raw:
        _issue(issues, "invalid_json", "severe", "failed to parse chord json")
        row.update(_finalize(row, issues, {}))
        return row

    served = _apply_serve_pipeline(raw)
    chords = served.get("chords") or []
    beats = served.get("beats") or []
    downbeats = served.get("downbeats") or []
    raw_bpm = _float(served.get("bpm"))
    display_bpm = _float(served.get("display_bpm"))
    bpm = display_bpm or raw_bpm
    source = served.get("source") or ""
    beats_source = served.get("beats_source") or ""

    row.update({
        "source": source,
        "key": served.get("key") or "",
        "bpm": round(raw_bpm, 2) if raw_bpm else "",
        "display_bpm": round(display_bpm, 2) if display_bpm else "",
        "beats_source": beats_source,
        "n_chords": len(chords),
        "n_beats": len(beats),
        "n_downbeats": len(downbeats),
    })

    if source in {"midi", "chordify"}:
        _issue(issues, "legacy_source", "warn", f"source={source}")
    if not chords:
        _issue(issues, "no_chords", "severe", "no served chords")
    if len(beats) < 8:
        _issue(issues, "missing_beats", "severe", f"beats={len(beats)}")
    if len(downbeats) < 4:
        _issue(issues, "missing_downbeats", "severe", f"downbeats={len(downbeats)}")
    if beats_source and beats_source != "beat_this":
        _issue(issues, "weak_beat_source", "warn", f"beats_source={beats_source}")
    if raw_bpm and not (45 <= raw_bpm <= 220):
        _issue(issues, "bpm_outlier", "warn", f"bpm={raw_bpm:.1f}")

    metrics: Dict[str, Any] = {}
    beat_gap = _median_gap(beats)
    bar_gap = _median_gap(downbeats)
    beat_cv = _gap_cv(beats)
    db_cv = _gap_cv(downbeats)
    metrics["beat_cv"] = beat_cv
    metrics["db_cv"] = db_cv
    metrics["bpb"] = (bar_gap / beat_gap) if beat_gap and bar_gap else None
    row["beat_cv"] = round(beat_cv, 4) if beat_cv is not None else ""
    row["db_cv"] = round(db_cv, 4) if db_cv is not None else ""
    row["bpb"] = round(metrics["bpb"], 3) if metrics["bpb"] else ""

    if db_cv is not None and db_cv > 0.18:
        _issue(issues, "irregular_downbeats", "warn", f"db_cv={db_cv:.2f}")
    bpb = metrics["bpb"]
    # POP target for this phase is 4/4 card stability. Slow ballads often
    # report bpb≈3 from sparse/half-density beats, but the player expectation
    # is still a 4-dot bar. Keep POP at 4 unless the grid clearly indicates
    # a six-beat compound bar.
    if track.category == "POP":
        meter_bpb = 6 if bpb and 5.2 <= bpb <= 6.8 else 4
    else:
        meter_bpb = _meter_class(bpb)
    if bpb and not (2.5 <= bpb <= 4.5 or 5.5 <= bpb <= 6.5 or 7.5 <= bpb <= 8.5):
        _issue(issues, "irregular_meter", "warn", f"bpb={bpb:.2f}")
    weak_pop_grid = (
        track.category == "POP"
        and (
            (db_cv is not None and db_cv > 0.30)
            or (bpb is not None and bpb < 2.6)
        )
    )
    if weak_pop_grid:
        _issue(issues, "weak_grid_context", "warn", "POP weak/no-drum beat grid; visual anomalies need manual review")

    long_cards = 0
    tiny_cards = 0
    max_beats = 0.0
    if bpm > 0:
        spb = 60.0 / bpm
        for c in chords:
            start = _float(c.get("time"))
            end = _float(c.get("end"))
            dur_beats = (end - start) / spb if end > start else 0
            max_beats = max(max_beats, dur_beats)
            # One bar can measure slightly above 4 nominal beats when BTC
            # boundaries/BPM are jittery. The player-risk case is a multi-bar
            # card that survived splitting.
            dots = _display_dot_count(c, bpm, meter_bpb)
            tolerated_weak_grid_hold = weak_pop_grid and dots <= (meter_bpb + 2) and dur_beats <= (meter_bpb + 3)
            if (
                dur_beats > (meter_bpb + 1.5)
                and dots > meter_bpb
                and not c.get("auto_split")
                and not tolerated_weak_grid_hold
            ):
                long_cards += 1
            if 0 < dur_beats < 0.45:
                tiny_cards += 1
    row["long_cards"] = long_cards
    row["tiny_cards"] = tiny_cards
    row["max_card_beats"] = round(max_beats, 2) if max_beats else ""
    if long_cards:
        _issue(issues, "long_cards", "severe", f"{long_cards} cards > 4.75 beats")
    if tiny_cards >= 3:
        _issue(issues, "tiny_cards", "warn", f"{tiny_cards} cards < 0.45 beats")

    tail_gap = None
    if chords and beats:
        last_chord_end = _float(chords[-1].get("end"), _float(chords[-1].get("time")))
        last_beat = _float(beats[-1])
        tail_gap = max(0.0, last_beat - last_chord_end)
        row["tail_gap_sec"] = round(tail_gap, 2)
        if tail_gap > max(8.0, last_beat * 0.05):
            _issue(issues, "tail_gap", "severe", f"tail gap {tail_gap:.1f}s")

    raw_frag = fragmentation_risk(chords, downbeats, bpm, meter_bpb)
    frag = _visible_fragment_risk(chords, bpm, meter_bpb)
    bad_frag = int(frag.get("bad_fragments") or 0)
    row["bad_fragments"] = bad_frag
    row["fragment_penalty"] = raw_frag.get("penalty", 0)
    row["fragment_patterns"] = json.dumps(frag.get("patterns") or {}, ensure_ascii=False)
    row["raw_bad_fragments"] = int(raw_frag.get("bad_fragments") or 0)
    row["raw_fragment_patterns"] = json.dumps(raw_frag.get("patterns") or {}, ensure_ascii=False)
    if bad_frag:
        sev = "severe" if bad_frag >= 8 else "warn"
        _issue(issues, "fragments", sev, f"{bad_frag} visible card/dot fragments")

    row.update(_finalize(row, issues, metrics))
    return row


def _finalize(row: Dict[str, Any], issues: List[Dict[str, Any]], metrics: Dict[str, Any]) -> Dict[str, Any]:
    severe = sum(1 for i in issues if i["severity"] == "severe")
    warn = sum(1 for i in issues if i["severity"] == "warn")
    return {
        "status": "fail" if severe else "pass",
        "severe_count": severe,
        "warn_count": warn,
        "issue_types": ";".join(i["type"] for i in issues),
        "issue_details": " | ".join(f"{i['type']}:{i['detail']}" for i in issues),
    }


def _summarize(rows: List[Dict[str, Any]], by_cat_counts: Dict[str, int], sample_size: int) -> Dict[str, Any]:
    categories = sorted({r["category"] for r in rows})
    cat_summary: Dict[str, Any] = {}
    all_fail = False
    for cat in categories:
        subset = [r for r in rows if r["category"] == cat]
        total = len(subset)
        pass_rate = sum(1 for r in subset if r["status"] == "pass") / _rate_den(total)
        severe_rate = sum(1 for r in subset if r["severe_count"] > 0) / _rate_den(total)
        avg_frag = sum(int(r.get("bad_fragments") or 0) for r in subset) / _rate_den(total)
        gate_key = cat if cat in THRESHOLDS else "__OTHER__"
        th = THRESHOLDS[gate_key]
        passed = (
            pass_rate >= th["pass_rate"]
            and severe_rate <= th["severe_rate"]
            and avg_frag <= th["avg_fragments"]
        )
        all_fail = all_fail or not passed
        cat_summary[cat] = {
            "sampled": total,
            "library_tracks": by_cat_counts.get(cat, 0),
            "pass_rate": round(pass_rate, 4),
            "severe_rate": round(severe_rate, 4),
            "avg_fragments": round(avg_frag, 3),
            "passed": passed,
            "threshold": th,
        }

    total = len(rows)
    all_th = THRESHOLDS["__ALL__"]
    legacy_rate = sum(1 for r in rows if "legacy_source" in r.get("issue_types", "")) / _rate_den(total)
    long_rate = sum(1 for r in rows if int(r.get("long_cards") or 0) > 0) / _rate_den(total)
    tail_rate = sum(1 for r in rows if "tail_gap" in r.get("issue_types", "")) / _rate_den(total)
    frag_song_rate = sum(1 for r in rows if int(r.get("bad_fragments") or 0) >= 8) / _rate_den(total)
    global_pass = (
        legacy_rate <= all_th["legacy_midi_rate"]
        and long_rate <= all_th["long_card_rate"]
        and tail_rate <= all_th["tail_gap_rate"]
        and frag_song_rate <= all_th["fragment_song_rate"]
    )

    issue_counts = Counter()
    for r in rows:
        for issue in (r.get("issue_types") or "").split(";"):
            if issue:
                issue_counts[issue] += 1

    return {
        "sample_size_requested": sample_size,
        "sampled": total,
        "overall_passed": global_pass and not all_fail,
        "global": {
            "pass_rate": round(sum(1 for r in rows if r["status"] == "pass") / _rate_den(total), 4),
            "severe_rate": round(sum(1 for r in rows if r["severe_count"] > 0) / _rate_den(total), 4),
            "legacy_source_rate": round(legacy_rate, 4),
            "long_card_song_rate": round(long_rate, 4),
            "tail_gap_rate": round(tail_rate, 4),
            "fragment_song_rate": round(frag_song_rate, 4),
            "threshold": all_th,
        },
        "categories": cat_summary,
        "issue_counts": dict(issue_counts.most_common()),
    }


def _write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    fields = [
        "status", "category", "path", "source", "key", "bpm", "beats_source",
        "display_bpm", "n_chords", "n_beats", "n_downbeats", "bpb", "beat_cv", "db_cv",
        "long_cards", "tiny_cards", "max_card_beats", "tail_gap_sec",
        "bad_fragments", "fragment_penalty", "fragment_patterns",
        "raw_bad_fragments", "raw_fragment_patterns",
        "issue_types", "issue_details", "url", "hash", "chord_file",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _write_html(path: Path, summary: Dict[str, Any], rows: List[Dict[str, Any]]) -> None:
    bad = [r for r in rows if r["status"] != "pass"]
    bad.sort(key=lambda r: (r["severe_count"], r["warn_count"], int(r.get("bad_fragments") or 0)), reverse=True)
    trs = []
    for r in bad[:200]:
        trs.append(
            "<tr>"
            f"<td>{html.escape(r['status'])}</td>"
            f"<td>{html.escape(r['category'])}</td>"
            f"<td><a href=\"{html.escape(r['url'])}\">{html.escape(r['path'])}</a></td>"
            f"<td>{html.escape(str(r.get('source','')))}</td>"
            f"<td>{html.escape(str(r.get('key','')))}</td>"
            f"<td>{html.escape(str(r.get('beats_source','')))}</td>"
            f"<td>{html.escape(str(r.get('issue_details','')))}</td>"
            "</tr>"
        )
    doc = f"""<!doctype html>
<meta charset="utf-8">
<title>LiveChord Quality Gate</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 24px; color: #172018; }}
pre {{ background: #f4f6f4; padding: 12px; overflow: auto; }}
table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
th,td {{ border-bottom: 1px solid #d7ded7; padding: 6px 8px; text-align: left; }}
th {{ background: #eef4ee; position: sticky; top: 0; }}
a {{ color: #126c4f; }}
</style>
<h1>LiveChord Quality Gate</h1>
<pre>{html.escape(json.dumps(summary, ensure_ascii=False, indent=2))}</pre>
<h2>Top Failures</h2>
<table>
<thead><tr><th>Status</th><th>Category</th><th>Path</th><th>Source</th><th>Key</th><th>Beats</th><th>Issues</th></tr></thead>
<tbody>{''.join(trs)}</tbody>
</table>
"""
    path.write_text(doc, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--library-root", default="Z:/", help="Music library root")
    ap.add_argument("--data-root", default="V:/data", help="LiveChord data root containing chords/")
    ap.add_argument("--out-dir", default="reports/quality_gate", help="Output directory")
    ap.add_argument("--sample", type=int, default=1000, help="Total sample size; <=0 audits all")
    ap.add_argument("--seed", type=int, default=20260511)
    ap.add_argument("--exclude", nargs="*", default=sorted(DEFAULT_EXCLUDE))
    args = ap.parse_args()

    library_root = Path(args.library_root)
    data_root = Path(args.data_root)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rng = random.Random(args.seed)
    by_cat = _iter_audio_by_category(library_root, {x.lower() for x in args.exclude})
    counts = {cat: len(tracks) for cat, tracks in by_cat.items()}
    sample = _allocate_sample(by_cat, args.sample, rng)

    rows = [_score_track(t, data_root) for t in sample]
    rows.sort(key=lambda r: (r["status"] == "pass", r["category"], r["path"]))
    summary = _summarize(rows, counts, args.sample)
    summary["library_root"] = str(library_root)
    summary["data_root"] = str(data_root)
    summary["excluded_categories"] = args.exclude
    summary["category_counts"] = counts

    (out_dir / "quality_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_csv(out_dir / "quality_failures.csv", [r for r in rows if r["status"] != "pass"])
    _write_csv(out_dir / "quality_sample.csv", rows)
    _write_html(out_dir / "quality_report.html", summary, rows)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nWrote {out_dir.resolve()}")
    return 0 if summary["overall_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
