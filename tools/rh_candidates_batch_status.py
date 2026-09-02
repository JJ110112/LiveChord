r"""Progress / error report for a running build_rh_melody_candidates.py batch.

The batch writes its per-song log to %TEMP%\rh_melody_candidates_<ts>.jsonl
while it runs (copied to <data-dir>\logs only at the end), so this reads the
newest such file and prints:

  * progress (rows done / --total, ETA from the mean per-song time)
  * status counts
  * every non-ok row (error / crepe_failed / demucs_failed / audio_not_found)
  * a stall warning when the last row is older than --stall-min minutes

Examples (run on the PC that is executing the batch):
  python tools/rh_candidates_batch_status.py --total 2000
  python tools/rh_candidates_batch_status.py --log %TEMP%\rh_melody_candidates_20260902_150000.jsonl
  python tools/rh_candidates_batch_status.py --total 2000 --json   # machine-readable

Exit code: 0 healthy, 1 errors present, 2 stalled / no log found.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

LOG_GLOB = "rh_melody_candidates_*.jsonl"
BAD_STATUSES = ("error", "crepe_failed", "demucs_failed", "audio_not_found")
OK_STATUSES = ("ok", "sidecar_only_gate_failed", "skipped_existing")


def find_latest_log(explicit: str, data_dir: str) -> Optional[Path]:
    if explicit:
        p = Path(os.path.expandvars(explicit))
        return p if p.is_file() else None
    cands: List[Path] = []
    for d in (Path(tempfile.gettempdir()), Path(data_dir) / "logs"):
        try:
            cands += [Path(x) for x in glob.glob(str(d / LOG_GLOB))]
        except OSError:
            continue
    if not cands:
        return None
    return max(cands, key=lambda p: p.stat().st_mtime)


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
                # A row still being written by the batch; ignore.
                continue
    return rows


def _parse_ts(s: Any) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _fmt_dur(seconds: float) -> str:
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"


def build_report(rows: List[Dict[str, Any]], *, log: Path, total: int, stall_min: float) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    by_status: Dict[str, int] = {}
    for r in rows:
        st = str(r.get("status") or "?")
        by_status[st] = by_status.get(st, 0) + 1

    processed = [r for r in rows if r.get("status") in ("ok", "sidecar_only_gate_failed")]
    times = [float(r["total_s"]) for r in processed if r.get("total_s")]
    avg_s = round(sum(times) / len(times), 1) if times else None
    remaining = max(0, total - len(rows)) if total else None
    eta_s = remaining * avg_s if (remaining is not None and avg_s) else None

    gate_pass = sum(1 for r in processed if (r.get("gate") or {}).get("predict_vocal"))
    ratios = [float(r["vocal_stem_energy_ratio"]) for r in processed if r.get("vocal_stem_energy_ratio") is not None]
    band = sum(1 for x in ratios if 0.06 <= x < 0.15)

    bad = [r for r in rows if r.get("status") in BAD_STATUSES]
    bad_rows = [
        {
            "i": i + 1,
            "song_hash": r.get("song_hash"),
            "status": r.get("status"),
            "error": r.get("error") or r.get("audio_path") or "",
            "path": r.get("path"),
        }
        for i, r in enumerate(rows)
        if r.get("status") in BAD_STATUSES
    ]

    last_ts = None
    for r in reversed(rows):
        last_ts = _parse_ts(r.get("finished_at")) or _parse_ts(r.get("started_at"))
        if last_ts:
            break
    log_age_s = now.timestamp() - log.stat().st_mtime
    idle_s = min(log_age_s, (now - last_ts).total_seconds()) if last_ts else log_age_s
    done = bool(total) and len(rows) >= total
    stalled = (not done) and idle_s > stall_min * 60

    first_ts = _parse_ts(rows[0].get("started_at")) if rows else None
    elapsed_s = (now - first_ts).total_seconds() if first_ts else None

    return {
        "log": str(log),
        "checked_at": now.isoformat(timespec="seconds"),
        "rows": len(rows),
        "total": total or None,
        "done": done,
        "elapsed_s": round(elapsed_s) if elapsed_s is not None else None,
        "avg_song_s": avg_s,
        "remaining": remaining,
        "eta_s": round(eta_s) if eta_s is not None else None,
        "by_status": by_status,
        "vocal_gate_pass": gate_pass,
        "vocal_gate_fail": len(processed) - gate_pass,
        "ratio_in_old_band_0.06_0.15": band,
        "errors": len(bad),
        "error_rows": bad_rows,
        "idle_s": round(idle_s),
        "stalled": stalled,
    }


def print_report(rep: Dict[str, Any], *, max_errors: int) -> None:
    total = rep["total"]
    prog = f"{rep['rows']}/{total}" if total else str(rep["rows"])
    pct = f" ({100.0 * rep['rows'] / total:.1f}%)" if total else ""
    print(f"log: {rep['log']}")
    print(f"progress: {prog}{pct}   elapsed: {_fmt_dur(rep['elapsed_s'] or 0)}")
    if rep["avg_song_s"]:
        eta = _fmt_dur(rep["eta_s"]) if rep["eta_s"] is not None else "?"
        print(f"avg/song: {rep['avg_song_s']}s   remaining: {rep['remaining']}   ETA: {eta}")
    print("status: " + ", ".join(f"{k}={v}" for k, v in sorted(rep["by_status"].items())))
    print(
        f"vocal gate: pass={rep['vocal_gate_pass']} fail={rep['vocal_gate_fail']}   "
        f"ratio in 0.06-0.15 band: {rep['ratio_in_old_band_0.06_0.15']}"
    )
    if rep["done"]:
        print("DONE: batch reached --total rows")
    elif rep["stalled"]:
        print(f"WARNING: stalled, no new row for {_fmt_dur(rep['idle_s'])}")
    else:
        print(f"last row: {_fmt_dur(rep['idle_s'])} ago")
    if rep["errors"]:
        print(f"\nERRORS ({rep['errors']}):")
        for e in rep["error_rows"][:max_errors]:
            print(f"  #{e['i']} {e['song_hash']} {e['status']}: {e['error']}")
            print(f"      {e['path']}")
        if rep["errors"] > max_errors:
            print(f"  ... {rep['errors'] - max_errors} more (use --max-errors)")
    else:
        print("\nno errors")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", default="", help="Explicit log path (default: newest in %%TEMP%% then <data-dir>/logs).")
    ap.add_argument("--data-dir", default=r"V:\data")
    ap.add_argument("--total", type=int, default=0, help="Planned song count, enables %% and ETA.")
    ap.add_argument("--stall-min", type=float, default=15.0, help="Minutes without a new row before WARNING.")
    ap.add_argument("--max-errors", type=int, default=30)
    ap.add_argument("--json", action="store_true", help="Print the report as JSON.")
    args = ap.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    log = find_latest_log(args.log, args.data_dir)
    if log is None:
        print("no rh_melody_candidates_*.jsonl log found", file=sys.stderr)
        return 2
    rows = read_rows(log)
    rep = build_report(rows, log=log, total=args.total, stall_min=args.stall_min)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print_report(rep, max_errors=args.max_errors)
    if rep["stalled"]:
        return 2
    return 1 if rep["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
