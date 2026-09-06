r"""Post-run verification report for a build_rh_melody_candidates.py batch log.

Answers the drift questions LiveChord-fu6g asks after a large candidate batch:

  1. Did the batch itself run clean?  status counts, failure list with reasons,
     throughput (songs/hour), wall-clock span.
  2. Where do songs sit relative to the vocal gate?  vocal_stem_energy_ratio
     histogram, counts in the bands just below / above the threshold, and the
     pass rate the library WOULD have at each threshold of the sweep (so the
     0.06 -> 0.15 recall cost is measured on the real library, not the
     100-song validation set).
  3. Is the gate-fail share plausible?  pass rate per genre folder
     (POP/<sub>, JAZZ, CLASSICS, ...).  Vocal-heavy folders with a low pass
     rate are flagged as likely false negatives; instrumental folders with a
     high pass rate as likely false positives.
  4. What to review next?  --sample N writes a hashes file with N random songs
     just below and N just above the threshold for a gate-band A/B review.

Reads the per-song JSONL the batch wrote (V:\data\logs\rh_melody_candidates_<ts>.jsonl
after it finished, or %TEMP% while running).  Writes <log>.report.md next to the
log unless --out is given; --json prints the machine-readable report instead.

Examples (PC, against the finished 30k batch):
  python tools/rh_candidates_batch_report.py --log V:\data\logs\rh_melody_candidates_20260902_205347.jsonl
  python tools/rh_candidates_batch_report.py --log ... --sample 20     # + band A/B hash list
  python tools/rh_candidates_batch_report.py --log ... --json > report.json
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import random
import statistics
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

LOG_GLOB = "rh_melody_candidates_*.jsonl"
DEFAULT_THRESHOLD = 0.15  # song_type_vocal_gate.DEFAULT_VOCAL_RATIO_THRESHOLD (gate v2)
THRESHOLD_SWEEP = (0.04, 0.06, 0.08, 0.10, 0.12, 0.15, 0.18, 0.20, 0.25, 0.30)
HIST_STEP = 0.02
HIST_MAX = 0.60
BAD_STATUSES = ("error", "crepe_failed", "demucs_failed", "audio_not_found")
PROCESSED = ("ok", "sidecar_only_gate_failed")

# Genre folders that are mostly sung vs. mostly instrumental.  Used only to
# decide which direction a surprising pass rate points; the report still shows
# every folder's numbers.
VOCAL_TOPS = {"POP", "CHRISTMAS"}
INSTRUMENTAL_TOPS = {"JAZZ", "CLASSICS", "RELAX", "SLEEP", "JAM", "ELECTRONIC DANCE MUSIC", "EDM"}
_KNOWN_TOPS = VOCAL_TOPS | INSTRUMENTAL_TOPS | {"OTHER"}
VOCAL_LOW_PASS = 0.60  # POP/* folder below this pass rate -> suspect false negatives
INSTR_HIGH_PASS = 0.50  # instrumental folder above this -> suspect false positives
MIN_GENRE_N = 30


def infer_genre(path_str: str) -> str:
    """Same bucketing as tools/audit_library_offsets.infer_genre (kept inline: that
    module pulls audio deps).  '@1/POP/C-POP/x.flac' -> 'POP/C-POP'; 'Jazz/..' -> 'JAZZ'."""
    if not path_str:
        return "UNKNOWN"
    s = str(path_str).replace("\\", "/")
    if s.startswith("@1/"):
        s = s[3:]
    parts = [p for p in s.split("/") if p]
    if len(parts) < 2:
        return "UNKNOWN"
    top = parts[0].upper()
    if top not in _KNOWN_TOPS:
        return "UNKNOWN"
    if top == "POP" and len(parts) >= 3:
        return f"POP/{parts[1]}"
    return top


def find_latest_log(explicit: str, data_dir: str) -> Optional[Path]:
    if explicit:
        p = Path(os.path.expandvars(explicit))
        return p if p.is_file() else None
    cands: List[Path] = []
    for d in (Path(data_dir) / "logs", Path(tempfile.gettempdir())):
        try:
            cands += [Path(x) for x in glob.glob(str(d / LOG_GLOB))]
        except OSError:
            continue
    return max(cands, key=lambda p: p.stat().st_mtime) if cands else None


def read_rows(log: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with log.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _ts(s: Any) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _row_error(r: Dict[str, Any]) -> str:
    if r.get("error"):
        return str(r["error"])
    st = r.get("status")
    if st == "audio_not_found":
        return f"missing: {r.get('audio_path')}"
    if st == "demucs_failed":
        stems = r.get("stems") or {}
        have = [k for k, v in stems.items() if v]
        size = r.get("audio_bytes")
        size_s = f", audio {size / 1e6:.1f} MB" if isinstance(size, (int, float)) else ""
        return f"stems returned: {have or 'none'} (demucs {r.get('demucs_s')}s{size_s})"
    return ""


def _ratio(r: Dict[str, Any]) -> Optional[float]:
    v = r.get("vocal_stem_energy_ratio")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _pct(n: int, d: int) -> Optional[float]:
    return round(100.0 * n / d, 1) if d else None


# --------------------------------------------------------------------------
def analyze(rows: List[Dict[str, Any]], *, threshold: float, sample_n: int, seed: int) -> Dict[str, Any]:
    by_status: Dict[str, int] = {}
    for r in rows:
        st = str(r.get("status") or "?")
        by_status[st] = by_status.get(st, 0) + 1

    processed = [r for r in rows if r.get("status") in PROCESSED]
    failed = [r for r in rows if r.get("status") in BAD_STATUSES]
    scored = [(r, _ratio(r)) for r in processed]
    scored = [(r, x) for r, x in scored if x is not None]
    ratios = [x for _, x in scored]

    # 1. run health ------------------------------------------------------
    starts = [t for t in (_ts(r.get("started_at")) for r in rows) if t]
    ends = [t for t in (_ts(r.get("finished_at")) for r in rows) if t]
    span_s = (max(ends) - min(starts)).total_seconds() if starts and ends else None
    times = [float(r["total_s"]) for r in processed if r.get("total_s")]
    run = {
        "rows": len(rows),
        "by_status": by_status,
        "processed": len(processed),
        "failed": len(failed),
        "failed_pct": _pct(len(failed), len(rows)),
        "first_started_at": min(starts).isoformat(timespec="seconds") if starts else None,
        "last_finished_at": max(ends).isoformat(timespec="seconds") if ends else None,
        "wall_clock_h": round(span_s / 3600, 1) if span_s else None,
        "songs_per_hour": round(len(rows) / (span_s / 3600), 0) if span_s else None,
        "avg_total_s": round(sum(times) / len(times), 2) if times else None,
        "avg_demucs_s": _avg(processed, "demucs_s"),
        "avg_crepe_s": _avg([r for r in processed if r.get("status") == "ok"], "crepe_s"),
        "persisted_mb": round(sum(int(r.get(k) or 0) for r in processed for k in ("sidecar_bytes", "candidate_bytes")) / 1e6, 1),
        "failures": [
            {
                "status": r.get("status"),
                "song_hash": r.get("song_hash"),
                "reason": _row_error(r),
                "path": r.get("path"),
            }
            for r in failed
        ],
        "gate_reasons": _count(str((r.get("gate") or {}).get("reason") or "?") for r in processed),
    }

    # 2. gate distribution ----------------------------------------------
    n = len(ratios)
    passed = sum(1 for x in ratios if x >= threshold)
    hist: List[Dict[str, Any]] = []
    lo = 0.0
    while lo < HIST_MAX - 1e-9:
        hi = round(lo + HIST_STEP, 4)
        c = sum(1 for x in ratios if lo <= x < hi)
        hist.append({"lo": round(lo, 2), "hi": hi, "n": c, "pct": _pct(c, n)})
        lo = hi
    tail = sum(1 for x in ratios if x >= HIST_MAX)
    hist.append({"lo": HIST_MAX, "hi": None, "n": tail, "pct": _pct(tail, n)})

    def band(a: float, b: float) -> Dict[str, Any]:
        c = sum(1 for x in ratios if a <= x < b)
        return {"lo": a, "hi": b, "n": c, "pct": _pct(c, n)}

    sweep = []
    for t in sorted(set(THRESHOLD_SWEEP) | {threshold}):
        p = sum(1 for x in ratios if x >= t)
        sweep.append({"threshold": t, "pass": p, "pass_pct": _pct(p, n), "current": abs(t - threshold) < 1e-9})

    gate = {
        "threshold": threshold,
        "scored": n,
        "pass": passed,
        "pass_pct": _pct(passed, n),
        "fail": n - passed,
        "median_ratio": round(statistics.median(ratios), 4) if ratios else None,
        "p10_ratio": round(_quantile(ratios, 0.10), 4) if ratios else None,
        "p90_ratio": round(_quantile(ratios, 0.90), 4) if ratios else None,
        "histogram": hist,
        "band_just_below": band(round(threshold - 0.05, 4), threshold),
        "band_just_above": band(threshold, round(threshold + 0.05, 4)),
        "band_old_gate_v1": band(0.06, threshold),
        "sweep": sweep,
    }

    # 3. per-genre plausibility -----------------------------------------
    per: Dict[str, List[float]] = {}
    for r, x in scored:
        per.setdefault(infer_genre(r.get("path") or ""), []).append(x)
    genres = []
    flags = []
    for g, xs in sorted(per.items(), key=lambda kv: -len(kv[1])):
        p = sum(1 for x in xs if x >= threshold)
        rate = p / len(xs)
        top = g.split("/")[0]
        kind = "vocal" if top in VOCAL_TOPS else "instrumental" if top in INSTRUMENTAL_TOPS else "mixed"
        flag = ""
        if len(xs) >= MIN_GENRE_N:
            if kind == "vocal" and rate < VOCAL_LOW_PASS:
                flag = "suspect_false_negatives"
            elif kind == "instrumental" and rate > INSTR_HIGH_PASS:
                flag = "suspect_false_positives"
        row = {
            "genre": g,
            "kind": kind,
            "n": len(xs),
            "pass": p,
            "pass_pct": round(100 * rate, 1),
            "median_ratio": round(statistics.median(xs), 4),
            "in_band_below": sum(1 for x in xs if threshold - 0.05 <= x < threshold),
            "flag": flag,
        }
        genres.append(row)
        if flag:
            flags.append(row)
    vocal_xs = [x for g, xs in per.items() if g.split("/")[0] in VOCAL_TOPS for x in xs]
    instr_xs = [x for g, xs in per.items() if g.split("/")[0] in INSTRUMENTAL_TOPS for x in xs]
    plaus = {
        "vocal_folders_n": len(vocal_xs),
        "vocal_folders_pass_pct": _pct(sum(1 for x in vocal_xs if x >= threshold), len(vocal_xs)),
        "instrumental_folders_n": len(instr_xs),
        "instrumental_folders_pass_pct": _pct(sum(1 for x in instr_xs if x >= threshold), len(instr_xs)),
        "unknown_n": len(per.get("UNKNOWN", [])),
        "genres": genres,
        "flags": flags,
    }

    # 4. review sample --------------------------------------------------
    sample: Dict[str, Any] = {"n_per_band": sample_n, "below": [], "above": []}
    if sample_n > 0:
        rng = random.Random(seed)
        below = [(r, x) for r, x in scored if threshold - 0.05 <= x < threshold]
        above = [(r, x) for r, x in scored if threshold <= x < threshold + 0.05]
        for key, pool in (("below", below), ("above", above)):
            pick = rng.sample(pool, min(sample_n, len(pool)))
            sample[key] = [
                {"song_hash": r.get("song_hash"), "ratio": round(x, 4), "genre": infer_genre(r.get("path") or ""), "path": r.get("path")}
                for r, x in sorted(pick, key=lambda t: t[1])
            ]

    verdict = _verdict(run, gate, plaus)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "verdict": verdict,
        "run": run,
        "gate": gate,
        "plausibility": plaus,
        "sample": sample,
    }


def _verdict(run: Dict[str, Any], gate: Dict[str, Any], plaus: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    fp = run["failed_pct"] or 0.0
    out.append(
        f"Run health: {run['failed']} failures / {run['rows']} rows ({fp}%)"
        + (" - OK" if fp < 1.0 else " - HIGH, inspect the failure list")
    )
    if run["by_status"].get("crepe_failed") or run["by_status"].get("error"):
        out.append("Pipeline errors present (crepe_failed / error): fix before trusting candidates.")
    jb = gate["band_just_below"]
    out.append(
        f"Gate: {gate['pass_pct']}% of scored songs pass at {gate['threshold']}; "
        f"{jb['n']} songs ({jb['pct']}%) sit within 0.05 below the threshold and are the recall at risk."
    )
    vp, ip = plaus["vocal_folders_pass_pct"], plaus["instrumental_folders_pass_pct"]
    if vp is not None and ip is not None:
        if vp >= 70 and ip <= 40:
            out.append(f"Plausibility: vocal folders pass {vp}%, instrumental folders pass {ip}% - the split tracks the library's genre mix, gate-fail share looks genuine.")
        elif vp < 70:
            out.append(f"Plausibility: vocal folders pass only {vp}% - the threshold is likely rejecting sung songs; review the just-below band.")
        else:
            out.append(f"Plausibility: instrumental folders pass {ip}% - instrumentals are leaking through; review the just-above band.")
    for f in plaus["flags"][:6]:
        out.append(f"  flag {f['genre']}: pass {f['pass_pct']}% of {f['n']} ({f['flag']})")
    return out


def _avg(rows: List[Dict[str, Any]], key: str) -> Optional[float]:
    vals = [float(r[key]) for r in rows if r.get(key) is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def _count(items) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for it in items:
        out[it] = out.get(it, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def _quantile(xs: List[float], q: float) -> float:
    s = sorted(xs)
    k = (len(s) - 1) * q
    f, c = int(k), min(int(k) + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


# --------------------------------------------------------------------------
def render_markdown(rep: Dict[str, Any], log: Path) -> str:
    run, gate, pl, smp = rep["run"], rep["gate"], rep["plausibility"], rep["sample"]
    L: List[str] = []
    L.append("# RH melody candidate batch report\n")
    L.append(f"log: `{log}`  \ngenerated: {rep['generated_at']}\n")
    L.append("## Verdict\n")
    L += [f"- {v}" for v in rep["verdict"]]
    L.append("\n## 1. Run health\n")
    L.append("| metric | value |\n|---|---|")
    L.append(f"| rows | {run['rows']} |")
    L.append("| by status | " + ", ".join(f"{k}={v}" for k, v in sorted(run["by_status"].items())) + " |")
    L.append(f"| failures | {run['failed']} ({run['failed_pct']}%) |")
    L.append(f"| wall clock | {run['wall_clock_h']} h ({run['first_started_at']} to {run['last_finished_at']}) |")
    L.append(f"| throughput | {run['songs_per_hour']} songs/h, avg {run['avg_total_s']} s/song (demucs {run['avg_demucs_s']} s, crepe {run['avg_crepe_s']} s) |")
    L.append(f"| persisted | {run['persisted_mb']} MB |")
    L.append("| gate reasons | " + ", ".join(f"{k}={v}" for k, v in run["gate_reasons"].items()) + " |")
    if run["failures"]:
        L.append(f"\n### Failures ({len(run['failures'])})\n")
        L.append("| status | hash | reason | path |\n|---|---|---|---|")
        for f in run["failures"]:
            L.append(f"| {f['status']} | {f['song_hash']} | {_md(f['reason'])} | {_md(f['path'])} |")

    L.append(f"\n## 2. Vocal gate distribution (threshold {gate['threshold']})\n")
    L.append("| metric | value |\n|---|---|")
    L.append(f"| scored songs | {gate['scored']} |")
    L.append(f"| pass | {gate['pass']} ({gate['pass_pct']}%) |")
    L.append(f"| fail | {gate['fail']} |")
    L.append(f"| ratio p10 / median / p90 | {gate['p10_ratio']} / {gate['median_ratio']} / {gate['p90_ratio']} |")
    for name, key in (("just below (recall at risk)", "band_just_below"), ("just above (precision at risk)", "band_just_above"), ("old v1 band 0.06 to threshold", "band_old_gate_v1")):
        b = gate[key]
        L.append(f"| {name} [{b['lo']}, {b['hi']}) | {b['n']} ({b['pct']}%) |")
    L.append("\n### Histogram (vocal_stem_energy_ratio)\n")
    L.append("| bin | n | % | |\n|---|---|---|---|")
    mx = max((h["n"] for h in gate["histogram"]), default=1) or 1
    for h in gate["histogram"]:
        label = f"{h['lo']:.2f}-{h['hi']:.2f}" if h["hi"] is not None else f">= {h['lo']:.2f}"
        bar = "#" * int(round(40 * h["n"] / mx))
        mark = " <- threshold" if h["hi"] is not None and h["lo"] <= gate["threshold"] < h["hi"] else ""
        L.append(f"| {label} | {h['n']} | {h['pct']} | `{bar}`{mark} |")
    L.append("\n### Pass rate if the threshold were...\n")
    L.append("| threshold | pass | % | |\n|---|---|---|---|")
    for s in gate["sweep"]:
        L.append(f"| {s['threshold']} | {s['pass']} | {s['pass_pct']} | {'current' if s['current'] else ''} |")

    L.append("\n## 3. Plausibility by genre folder\n")
    L.append(f"vocal folders (POP/*, CHRISTMAS): {pl['vocal_folders_n']} songs, pass {pl['vocal_folders_pass_pct']}%  ")
    L.append(f"instrumental folders (JAZZ, CLASSICS, RELAX, SLEEP, JAM, EDM): {pl['instrumental_folders_n']} songs, pass {pl['instrumental_folders_pass_pct']}%  ")
    L.append(f"unknown folder (uploads / unfamiliar tops): {pl['unknown_n']} songs\n")
    L.append("| genre | kind | n | pass | % | median ratio | in band below | flag |\n|---|---|---|---|---|---|---|---|")
    for g in pl["genres"]:
        L.append(f"| {g['genre']} | {g['kind']} | {g['n']} | {g['pass']} | {g['pass_pct']} | {g['median_ratio']} | {g['in_band_below']} | {g['flag']} |")

    if smp["n_per_band"]:
        L.append(f"\n## 4. Gate-band A/B review sample ({smp['n_per_band']} per band)\n")
        for key, title in (("below", "Just below threshold (check for missed vocals)"), ("above", "Just above threshold (check for instrumentals leaking through)")):
            L.append(f"\n### {title}\n")
            L.append("| hash | ratio | genre | path |\n|---|---|---|---|")
            for s in smp[key]:
                L.append(f"| {s['song_hash']} | {s['ratio']} | {s['genre']} | {_md(s['path'])} |")
    return "\n".join(L) + "\n"


def _md(s: Any) -> str:
    return str(s if s is not None else "").replace("|", "\\|")


def write_sample_hashes(rep: Dict[str, Any], out: Path) -> Optional[Path]:
    smp = rep["sample"]
    if not smp["n_per_band"]:
        return None
    lines = ["# gate-band A/B review sample; feed to build_rh_melody_candidates.py --hashes-file ... --execute --force"]
    for key in ("below", "above"):
        lines.append(f"# {key} threshold")
        lines += [f"{s['song_hash']}  # ratio={s['ratio']} {s['genre']}" for s in smp[key]]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", default="", help="Batch JSONL (default: newest in <data-dir>/logs, then %%TEMP%%).")
    ap.add_argument("--data-dir", default=r"V:\data")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--sample", type=int, default=0, help="Write N random hashes per gate band for A/B review.")
    ap.add_argument("--seed", type=int, default=20260905)
    ap.add_argument("--out", default="", help="Markdown report path (default: <log>.report.md).")
    ap.add_argument("--json", action="store_true", help="Print JSON report to stdout instead of markdown.")
    args = ap.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    log = find_latest_log(args.log, args.data_dir)
    if log is None:
        print("no rh_melody_candidates_*.jsonl log found", file=sys.stderr)
        return 2
    rows = read_rows(log)
    if not rows:
        print(f"{log}: no rows", file=sys.stderr)
        return 2

    rep = analyze(rows, threshold=args.threshold, sample_n=args.sample, seed=args.seed)
    md = render_markdown(rep, log)
    out = Path(os.path.expandvars(args.out)) if args.out else log.with_suffix(".report.md")
    try:
        out.write_text(md, encoding="utf-8")
        written = str(out)
    except OSError as exc:
        written = f"(not written: {exc})"
    if args.sample:
        hp = write_sample_hashes(rep, log.with_suffix(".band_sample.txt"))
        rep["sample"]["hashes_file"] = str(hp) if hp else None

    if args.json:
        rep["report_md"] = written
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(md)
        print(f"report written: {written}")
        if args.sample:
            print(f"sample hashes: {rep['sample'].get('hashes_file')}")
    return 1 if rep["run"]["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
