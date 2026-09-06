r"""Serve-time drift report for the RH vocal melody resolver (LiveChord-fu6g).

The batch report (rh_candidates_batch_report.py) looks at what the candidate
builder produced.  This one looks at what the resolver actually SERVED, so a
regression shows up before someone hears it in the player:

  1. Resolver selections   every record under <data-dir>/melodies_rh_v2/ is one
                           resolve() call: vocal_stem_crepe selected (with /
                           without a pYIN baseline), or a full_mix_pyin fallback
                           with its reason (gate refused, low-coverage retreat,
                           candidate missing).  Counted overall and for the
                           --since-days window.
  2. Gate boundary         vocal_stem_energy_ratio of every served record; the
                           ones within --band of the threshold are listed on
                           both sides (precision risk above, recall risk below).
  3. Manual overrides      <data-dir>/vocal_gate_overrides.json entries, each
                           checked for sidecar + CREPE candidate on disk.  An
                           override whose candidate is missing serves an EMPTY
                           melody (bit us 2026-09-06 with Lux Aeterna: the song
                           was batched with --skip-crepe-below-gate before the
                           override existed) -> WARN with the --force rerun
                           command.
  4. Worst-window coverage per served vocal selection, 10 s windows.  With a
                           pYIN baseline (melodies/<hash>.json) this is the
                           residual-report metric: windows where the baseline is
                           active but the candidate covers < 30 % of it.  Without
                           a baseline it degrades to candidate-silent windows
                           (informational: real vocals rest during intros).
  5. Library reference     ratio distribution from the batch logs, so the
                           served population can be compared with the corpus.

Writes <data-dir>/logs/rh_resolver_drift_<ts>.md unless --out is given; --json
prints the machine-readable report instead.

Examples (PC, against the NUC data dir):
  python tools/rh_resolver_drift_report.py
  python tools/rh_resolver_drift_report.py --since-days 7 --worst 10
  python tools/rh_resolver_drift_report.py --json > drift.json
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from ai.melody_candidate import (  # noqa: E402
    MELODY_SELECTED_DIR_NAME,
    VOCAL_STEM_CREPE,
    candidate_path,
)
from ai.melody_resolver import RETREAT_LOW_COVERAGE_FLAG  # noqa: E402
from ai.melody_residual_report import (  # noqa: E402
    DEFAULT_WINDOW_S,
    _active_overlap,
    coverage_gap_metrics,
)
from ai.song_type_audio_features import read_stem_energy_sidecar  # noqa: E402
from ai.song_type_vocal_gate import (  # noqa: E402
    DEFAULT_VOCAL_RATIO_THRESHOLD,
    OVERRIDES_FILENAME,
    load_vocal_gate_overrides,
)

LOG_GLOB = "rh_melody_candidates_*.jsonl"
SILENT_WINDOW_ACTIVE_S = 0.5  # candidate-only: a 10 s window with < 0.5 s of notes is "silent"


# --------------------------------------------------------------------------
# inputs
# --------------------------------------------------------------------------
def _read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _float(v: Any) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def read_selected_records(data_dir: Path) -> List[Dict[str, Any]]:
    """One row per resolver selection file, newest first."""
    rows: List[Dict[str, Any]] = []
    for f in sorted((data_dir / MELODY_SELECTED_DIR_NAME).glob("*/*.json")):
        d = _read_json(f)
        if not d:
            continue
        ms = d.get("melody_source") if isinstance(d.get("melody_source"), dict) else {}
        gate = ms.get("resolver_gate") if isinstance(ms.get("resolver_gate"), dict) else {}
        rows.append({
            "song_hash": str(d.get("song_hash") or f.stem),
            "path": str(d.get("path") or ""),
            "written_at": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc),
            "source_id": str(ms.get("id") or ""),
            "selected_by": str(ms.get("selected_by") or ""),
            "fallback_reason": ms.get("fallback_reason"),
            "gate_reason": gate.get("reason"),
            "gate_version": gate.get("version"),
            "ratio": _float(gate.get("vocal_stem_energy_ratio")),
            "flags": [str(x) for x in d.get("quality_flags") or []],
            "note_count": len(d.get("melody") or []),
            "melody": d.get("melody") or [],
            "melody_stats": d.get("melody_stats") if isinstance(d.get("melody_stats"), dict) else {},
        })
    rows.sort(key=lambda r: r["written_at"], reverse=True)
    return rows


def read_log_ratios(data_dir: Path) -> Dict[str, float]:
    """song_hash -> latest vocal_stem_energy_ratio from the candidate batch logs."""
    out: Dict[str, float] = {}
    for f in sorted(glob.glob(str(data_dir / "logs" / LOG_GLOB))):
        try:
            with open(f, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    ratio = _float(r.get("vocal_stem_energy_ratio"))
                    if ratio is not None and r.get("song_hash"):
                        out[str(r["song_hash"])] = ratio
        except OSError:
            continue
    return out


# --------------------------------------------------------------------------
# analysis
# --------------------------------------------------------------------------
def _outcome(r: Dict[str, Any]) -> str:
    if r["source_id"] == VOCAL_STEM_CREPE:
        if r["gate_reason"] == "manual_override":
            return "selected_override"
        if "resolver_selected_without_baseline" in r["flags"]:
            return "selected_no_baseline"
        return "selected"
    return f"fallback:{r['fallback_reason'] or 'unknown'}"


def _count(items) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for x in items:
        out[x] = out.get(x, 0) + 1
    return dict(sorted(out.items()))


def _quantile(xs: List[float], q: float) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(q * (len(s) - 1)))], 3)


def analyze_selections(rows: List[Dict[str, Any]], *, since_days: int, threshold: float, band: float) -> Dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    recent = [r for r in rows if r["written_at"] >= cutoff]
    ratios = [r["ratio"] for r in rows if r["ratio"] is not None]
    just_above = [r for r in rows if r["ratio"] is not None and threshold <= r["ratio"] < threshold + band]
    just_below = [r for r in rows if r["ratio"] is not None and threshold - band <= r["ratio"] < threshold]

    def _brief(r: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "song_hash": r["song_hash"],
            "ratio": round(r["ratio"], 3) if r["ratio"] is not None else None,
            "outcome": _outcome(r),
            "note_count": r["note_count"],
            "path": r["path"],
        }

    return {
        "total": len(rows),
        "since_days": since_days,
        "recent": len(recent),
        "by_outcome": _count(_outcome(r) for r in rows),
        "recent_by_outcome": _count(_outcome(r) for r in recent),
        "by_gate_version": _count(str(r["gate_version"]) for r in rows),
        "by_day": _count(r["written_at"].strftime("%Y-%m-%d") for r in rows),
        "ratio_p10": _quantile(ratios, 0.1),
        "ratio_median": _quantile(ratios, 0.5),
        "ratio_p90": _quantile(ratios, 0.9),
        "low_coverage_retreats": sum(1 for r in rows if RETREAT_LOW_COVERAGE_FLAG in r["flags"]),
        "empty_selected": [_brief(r) for r in rows if r["source_id"] == VOCAL_STEM_CREPE and r["note_count"] == 0],
        "just_above": [_brief(r) for r in sorted(just_above, key=lambda r: r["ratio"])],
        "just_below": [_brief(r) for r in sorted(just_below, key=lambda r: -r["ratio"])],
    }


def analyze_overrides(data_dir: Path, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    overrides = load_vocal_gate_overrides(data_dir)
    served = {r["song_hash"]: r for r in rows}
    entries = []
    for h, entry in sorted(overrides.items()):
        sidecar = read_stem_energy_sidecar(data_dir, h)
        cand = candidate_path(data_dir, h, VOCAL_STEM_CREPE)
        cand_ok = cand.is_file()
        cand_notes = len((_read_json(cand) if cand_ok else {}).get("melody") or [])
        rec = served.get(h)
        problems = []
        if sidecar is None:
            problems.append("sidecar_missing")
        if not cand_ok:
            problems.append("candidate_missing")
        elif cand_notes == 0:
            problems.append("candidate_empty")
        if rec is not None and rec["source_id"] != VOCAL_STEM_CREPE:
            problems.append(f"served_{rec['source_id'] or 'other'}")
        entries.append({
            "song_hash": h,
            "predict_vocal": bool(entry.get("predict_vocal")),
            "note": str(entry.get("note") or ""),
            "ratio": round(float(sidecar["vocal_stem_energy_ratio"]), 3)
            if sidecar and _float(sidecar.get("vocal_stem_energy_ratio")) is not None else None,
            "path": (sidecar or {}).get("path") or (rec or {}).get("path") or "",
            "candidate_notes": cand_notes if cand_ok else None,
            "served_outcome": _outcome(rec) if rec else None,
            "problems": problems,
        })
    return {
        "file": str(data_dir / OVERRIDES_FILENAME),
        "total": len(entries),
        "with_problems": sum(1 for e in entries if e["problems"]),
        "entries": entries,
    }


def window_coverage(data_dir: Path, r: Dict[str, Any], *, window_s: float) -> Dict[str, Any]:
    """Per served vocal selection: residual-style coverage vs pYIN baseline if it
    exists, else candidate-only silent windows."""
    events = r["melody"]
    baseline = _read_json(data_dir / "melodies" / f"{r['song_hash']}.json").get("melody") or []
    duration = max([_float(e.get("end")) or 0.0 for e in events] + [_float(e.get("end")) or 0.0 for e in baseline] + [0.0])
    row: Dict[str, Any] = {
        "song_hash": r["song_hash"],
        "path": r["path"],
        "ratio": round(r["ratio"], 3) if r["ratio"] is not None else None,
        "note_count": r["note_count"],
        "duration_s": round(duration, 1),
        "mode": "vs_baseline" if baseline else "candidate_only",
    }
    if baseline:
        cov = coverage_gap_metrics(baseline, events, duration_s=duration, window_s=window_s)
        row.update({
            "windows": cov["baseline_active_windows"],
            "missing_windows": cov["candidate_missing_windows"],
            "missing_fraction": cov["missing_window_fraction"],
            "worst_window_ratio": cov["worst_window_ratio"],
            "missing_at": [f"{w['start']:.0f}-{w['end']:.0f}s" for w in cov["windows"] if w["missing"]][:12],
        })
        return row
    silent: List[str] = []
    n = 0
    start = 0.0
    while start < duration:
        end = min(duration, start + window_s)
        n += 1
        if _active_overlap(events, start, end) < SILENT_WINDOW_ACTIVE_S:
            silent.append(f"{start:.0f}-{end:.0f}s")
        start += window_s
    row.update({
        "windows": n,
        "missing_windows": len(silent),
        "missing_fraction": round(len(silent) / n, 4) if n else 0.0,
        "worst_window_ratio": None,
        "missing_at": silent[:12],
    })
    return row


def analyze_coverage(data_dir: Path, rows: List[Dict[str, Any]], *, window_s: float, worst_n: int) -> Dict[str, Any]:
    vocal = [r for r in rows if r["source_id"] == VOCAL_STEM_CREPE]
    per_song = [window_coverage(data_dir, r, window_s=window_s) for r in vocal]
    with_baseline = [c for c in per_song if c["mode"] == "vs_baseline"]
    cand_only = [c for c in per_song if c["mode"] == "candidate_only"]

    def _worst(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return sorted(items, key=lambda c: (-(c["missing_fraction"] or 0), c["worst_window_ratio"] if c["worst_window_ratio"] is not None else 9))[:worst_n]

    return {
        "window_s": window_s,
        "vocal_selections": len(vocal),
        "with_baseline": len(with_baseline),
        "candidate_only": len(cand_only),
        "with_missing_windows": sum(1 for c in with_baseline if c["missing_windows"]),
        "worst_ratio_le_005": sum(1 for c in with_baseline if c["worst_window_ratio"] is not None and c["worst_window_ratio"] <= 0.05),
        "worst_vs_baseline": _worst(with_baseline),
        "worst_candidate_only": _worst(cand_only),
    }


def analyze_library(log_ratios: Dict[str, float], *, threshold: float, band: float) -> Dict[str, Any]:
    xs = list(log_ratios.values())
    return {
        "songs": len(xs),
        "pass": sum(1 for x in xs if x >= threshold),
        "pass_pct": round(100 * sum(1 for x in xs if x >= threshold) / len(xs), 1) if xs else None,
        "just_below": sum(1 for x in xs if threshold - band <= x < threshold),
        "just_above": sum(1 for x in xs if threshold <= x < threshold + band),
        "p10": _quantile(xs, 0.1),
        "median": _quantile(xs, 0.5),
        "p90": _quantile(xs, 0.9),
    }


def verdict(sel: Dict[str, Any], ov: Dict[str, Any], cov: Dict[str, Any]) -> List[str]:
    v: List[str] = []
    bad = [e for e in ov["entries"] if e["problems"]]
    if bad:
        v.append(f"WARN {len(bad)} override(s) cannot serve a vocal melody: "
                 + ", ".join(f"{e['song_hash']} ({'/'.join(e['problems'])})" for e in bad)
                 + ". Fix: python tools/build_rh_melody_candidates.py --force --execute --hash <hash>")
    else:
        v.append(f"OK {ov['total']} override(s) all have sidecar + CREPE candidate")
    if sel["empty_selected"]:
        v.append(f"WARN {len(sel['empty_selected'])} vocal selection(s) served with 0 notes")
    if sel["just_above"]:
        v.append(f"CHECK {len(sel['just_above'])} served vocal/fallback record(s) within the band just above the gate (precision risk) - listen list in section 2")
    if sel["just_below"]:
        v.append(f"CHECK {len(sel['just_below'])} fallback(s) just below the gate (recall risk) - override candidates if they are sung")
    if sel["low_coverage_retreats"]:
        v.append(f"INFO {sel['low_coverage_retreats']} low-coverage retreat(s)")
    if cov["with_baseline"]:
        v.append(f"INFO worst-window: {cov['with_missing_windows']}/{cov['with_baseline']} baseline-compared selections have >=1 missing 10 s window, {cov['worst_ratio_le_005']} with worst ratio <= 0.05")
    if not sel["total"]:
        v.append("WARN no resolver selection records found - resolver disabled or wrong --data-dir?")
    return v


def build_report(data_dir: Path, *, since_days: int, threshold: float, band: float, window_s: float, worst_n: int) -> Dict[str, Any]:
    rows = read_selected_records(data_dir)
    sel = analyze_selections(rows, since_days=since_days, threshold=threshold, band=band)
    ov = analyze_overrides(data_dir, rows)
    cov = analyze_coverage(data_dir, rows, window_s=window_s, worst_n=worst_n)
    lib = analyze_library(read_log_ratios(data_dir), threshold=threshold, band=band)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data_dir": str(data_dir),
        "threshold": threshold,
        "band": band,
        "verdict": verdict(sel, ov, cov),
        "selections": sel,
        "overrides": ov,
        "coverage": cov,
        "library": lib,
    }


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------
def _md(s: Any) -> str:
    return str(s).replace("|", "\\|")


def _kv(d: Dict[str, int]) -> str:
    return ", ".join(f"{k}={v}" for k, v in d.items()) or "-"


def render_markdown(rep: Dict[str, Any]) -> str:
    sel, ov, cov, lib = rep["selections"], rep["overrides"], rep["coverage"], rep["library"]
    L: List[str] = []
    L.append("# RH resolver drift report\n")
    L.append(f"data: `{rep['data_dir']}`  \ngenerated: {rep['generated_at']}  \ngate threshold {rep['threshold']}, band +/-{rep['band']}\n")
    L.append("## Verdict\n")
    L += [f"- {v}" for v in rep["verdict"]]

    L.append("\n## 1. Resolver selections (served records)\n")
    L.append("| metric | value |\n|---|---|")
    L.append(f"| records | {sel['total']} |")
    L.append(f"| last {sel['since_days']} days | {sel['recent']} ({_kv(sel['recent_by_outcome'])}) |")
    L.append(f"| by outcome | {_kv(sel['by_outcome'])} |")
    L.append(f"| by gate version | {_kv(sel['by_gate_version'])} |")
    L.append(f"| by day | {_kv(sel['by_day'])} |")
    L.append(f"| ratio p10 / median / p90 | {sel['ratio_p10']} / {sel['ratio_median']} / {sel['ratio_p90']} |")
    L.append(f"| low-coverage retreats | {sel['low_coverage_retreats']} |")
    if sel["empty_selected"]:
        L.append("\n### Vocal selections served with 0 notes\n")
        L.append("| hash | ratio | outcome | path |\n|---|---|---|---|")
        for e in sel["empty_selected"]:
            L.append(f"| {e['song_hash']} | {e['ratio']} | {e['outcome']} | {_md(e['path'])} |")

    L.append("\n## 2. Gate boundary among served records\n")
    for key, title in (("just_above", "Just above threshold (instrumental leaking through?)"), ("just_below", "Just below threshold (sung but refused?)")):
        L.append(f"\n### {title}: {len(sel[key])}\n")
        if sel[key]:
            L.append("| hash | ratio | outcome | notes | path |\n|---|---|---|---|---|")
            for e in sel[key]:
                L.append(f"| {e['song_hash']} | {e['ratio']} | {e['outcome']} | {e['note_count']} | {_md(e['path'])} |")

    L.append(f"\n## 3. Manual overrides ({ov['total']}, {ov['with_problems']} with problems)\n")
    L.append(f"file: `{ov['file']}`\n")
    if ov["entries"]:
        L.append("| hash | ratio | candidate notes | served | problems | path |\n|---|---|---|---|---|---|")
        for e in ov["entries"]:
            L.append(f"| {e['song_hash']} | {e['ratio']} | {e['candidate_notes']} | {e['served_outcome'] or '-'} | {'/'.join(e['problems']) or 'ok'} | {_md(e['path'])} |")

    L.append(f"\n## 4. Worst-window coverage ({cov['window_s']:.0f} s windows)\n")
    L.append("| metric | value |\n|---|---|")
    L.append(f"| vocal selections | {cov['vocal_selections']} (vs baseline {cov['with_baseline']}, candidate-only {cov['candidate_only']}) |")
    L.append(f"| with >=1 missing window (vs baseline) | {cov['with_missing_windows']} |")
    L.append(f"| worst window ratio <= 0.05 | {cov['worst_ratio_le_005']} |")
    for key, title in (("worst_vs_baseline", "Worst vs pYIN baseline (missing = candidate < 30 % of baseline activity)"),
                       ("worst_candidate_only", "Candidate-only silent windows (no baseline; rests during intros are normal)")):
        if cov[key]:
            L.append(f"\n### {title}\n")
            L.append("| hash | ratio | notes | dur s | windows | missing | frac | worst | where | path |\n|---|---|---|---|---|---|---|---|---|---|")
            for c in cov[key]:
                L.append(f"| {c['song_hash']} | {c['ratio']} | {c['note_count']} | {c['duration_s']} | {c['windows']} | {c['missing_windows']} | {c['missing_fraction']} | {c['worst_window_ratio'] if c['worst_window_ratio'] is not None else '-'} | {' '.join(c['missing_at']) or '-'} | {_md(c['path'])} |")

    L.append("\n## 5. Library reference (candidate batch logs)\n")
    L.append("| metric | value |\n|---|---|")
    L.append(f"| songs with ratio | {lib['songs']} |")
    L.append(f"| pass at threshold | {lib['pass']} ({lib['pass_pct']}%) |")
    L.append(f"| just below / just above | {lib['just_below']} / {lib['just_above']} |")
    L.append(f"| ratio p10 / median / p90 | {lib['p10']} / {lib['median']} / {lib['p90']} |")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=r"V:\data")
    ap.add_argument("--since-days", type=int, default=14)
    ap.add_argument("--threshold", type=float, default=DEFAULT_VOCAL_RATIO_THRESHOLD)
    ap.add_argument("--band", type=float, default=0.05, help="Half-width of the boundary band around the threshold.")
    ap.add_argument("--window-s", type=float, default=DEFAULT_WINDOW_S)
    ap.add_argument("--worst", type=int, default=8, help="Rows in each worst-window table.")
    ap.add_argument("--out", default="", help="Markdown path (default: <data-dir>/logs/rh_resolver_drift_<ts>.md).")
    ap.add_argument("--json", action="store_true", help="Print JSON report to stdout instead of markdown.")
    args = ap.parse_args()

    data_dir = Path(os.path.expandvars(args.data_dir))
    rep = build_report(
        data_dir,
        since_days=args.since_days,
        threshold=args.threshold,
        band=args.band,
        window_s=args.window_s,
        worst_n=args.worst,
    )
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
        return 0
    md = render_markdown(rep)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(os.path.expandvars(args.out)) if args.out else data_dir / "logs" / f"rh_resolver_drift_{ts}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"written: {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
