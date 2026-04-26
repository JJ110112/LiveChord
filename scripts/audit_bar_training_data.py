"""Audit chord JSONs to estimate bar_arbitrator training data viability.

Read-only sweep over data/chords/*.json. Classifies each song by the
regularity of its raw downbeats and reports counts per category, so we
know before training whether we have enough clean ground truth + enough
diverse failure modes.

Categories
----------
clean         — raw_cv < 0.05, n_downbeats >= 16. High-quality positive
                samples. Use as ground truth for training.
mild_drift    — 0.05 <= raw_cv < 0.15. Usable as soft positive (rubato
                or live performance).
phase_noisy   — 0.15 <= raw_cv < 0.30. Suspect positive — likely a
                tracker glitch on otherwise-OK song. Inspect manually.
broken        — raw_cv >= 0.30. Likely bar-doubled / beat-vs-bar / phase
                wandering. Use as negative samples (raw was wrong).
no_beats      — empty downbeats[] (librosa-fallback or beat_this pre-batch).
                Cannot train on these — they have no input features.
unparseable   — JSON load failed or schema unexpected. Logged for repair.

Usage
-----
    python scripts/audit_bar_training_data.py [--root V:\\data\\chords]
                                              [--skip-recent-sec 600]
                                              [--out reports/bar_audit.json]

By default reads ``data/chords/`` in the dev repo (safe). Pass --root to
point at a different chord store. ``--skip-recent-sec`` skips files
modified within the last N seconds — useful while beat_this batch is
still running so we don't read partially-written files.

This script is idempotent and read-only. Safe to run while other workers
process the same directory (we only ``json.load``, never write).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

# Make backend importable when run as a top-level script
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "backend"))

from backend.ai.bar_arbitrator import _gap_stats  # noqa: E402


def _classify(downbeats, beats, n_chords):
    """Return (category, raw_cv) for one song."""
    if not downbeats:
        return "no_beats", None
    if n_chords < 8:
        return "too_short", None
    _, cv = _gap_stats(downbeats)
    if cv is None:
        return "no_beats", None
    if cv < 0.05:
        return "clean", cv
    if cv < 0.15:
        return "mild_drift", cv
    if cv < 0.30:
        return "phase_noisy", cv
    return "broken", cv


def _walk_chord_dir(root: Path):
    """Yield (path, mtime) for every chord JSON under root.

    Handles both flat layout (``data/chords/<hash>.json``) and the
    sharded layout (``data/chords/<hash[:2]>/<hash>.json``) introduced
    in commit abe6172. Skips ``.bak.*`` backup files.
    """
    if not root.exists():
        return
    for p in root.rglob("*.json"):
        # Skip the .bak.beat_this / .bak.librosa backups — they're useful
        # for paired-comparison training (Phase 1) but not for general audit
        if ".bak." in p.name:
            continue
        try:
            yield p, p.stat().st_mtime
        except OSError:
            continue


def audit(root: Path, skip_recent_sec: float, max_files: int | None = None):
    counts = Counter()
    examples = {k: [] for k in
                ("clean", "mild_drift", "phase_noisy", "broken", "no_beats", "too_short")}
    parse_errors = []
    cv_buckets = []

    now = time.time()
    n_seen = 0
    n_skipped_recent = 0

    for path, mtime in _walk_chord_dir(root):
        if now - mtime < skip_recent_sec:
            n_skipped_recent += 1
            continue

        n_seen += 1
        if max_files and n_seen > max_files:
            break

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            counts["unparseable"] += 1
            if len(parse_errors) < 20:
                parse_errors.append(f"{path.name}: {type(e).__name__}: {e}")
            continue

        downbeats = data.get("downbeats") or []
        beats = data.get("beats") or []
        chords = data.get("chords") or []

        category, cv = _classify(downbeats, beats, len(chords))
        counts[category] += 1
        if cv is not None:
            cv_buckets.append(cv)

        if len(examples.get(category, [])) < 5:
            examples.setdefault(category, []).append({
                "hash": path.stem,
                "bpm": data.get("bpm"),
                "beats_source": data.get("beats_source"),
                "n_chords": len(chords),
                "n_downbeats": len(downbeats),
                "raw_cv": round(cv, 4) if cv is not None else None,
            })

    summary = {
        "root": str(root),
        "n_files_scanned": n_seen,
        "n_files_skipped_recent": n_skipped_recent,
        "skip_recent_sec": skip_recent_sec,
        "counts": dict(counts),
        "cv_quartiles": _quartiles(cv_buckets),
        "examples": examples,
        "parse_errors_first_20": parse_errors,
    }
    return summary


def _quartiles(values):
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    return {
        "min": round(s[0], 4),
        "q25": round(s[n // 4], 4),
        "median": round(s[n // 2], 4),
        "q75": round(s[(3 * n) // 4], 4),
        "max": round(s[-1], 4),
        "n": n,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path,
                    default=_REPO / "data" / "chords",
                    help="Chord JSON root (default: dev repo data/chords)")
    ap.add_argument("--skip-recent-sec", type=float, default=600.0,
                    help="Skip files modified in the last N seconds (avoid "
                         "reading mid-write files from concurrent batch)")
    ap.add_argument("--max-files", type=int, default=None,
                    help="Cap files scanned (for quick smoke test)")
    ap.add_argument("--out", type=Path, default=None,
                    help="Write summary JSON to this path (default: stdout)")
    args = ap.parse_args()

    summary = audit(args.root, args.skip_recent_sec, args.max_files)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(summary, indent=2, ensure_ascii=False),
                            encoding="utf-8")
        print(f"Wrote {args.out}")
    else:
        print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
