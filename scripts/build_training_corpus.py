"""Build the training corpus manifest for the audio-level beat_refiner.

Walks ``V:\\data\\chords`` (or whatever ``--root``), classifies each song
along TWO independent quality axes, and writes a stratified train/val/test
split to ``backend/ai/splits/``.

Two axes — why
--------------
A song's chord-label quality and beat-label quality are largely
independent:

  - ``source`` indicates how chords were derived (``midi``, ``chordify``,
    ``btc_batch``, …). MIDI/chordify chords are gold-quality.
  - ``beats_source`` + downbeats[] regularity indicates how trustworthy
    the per-frame timing is. Even a MIDI-sourced song's beats[] still
    comes from ``beat_this`` running on the audio — so it can be noisy.

If we collapse both into one tier, we lose two big buckets:

  - MIDI-sourced songs whose beat_this happened to be noisy
    (gold chords, useless beats — still great for chord_boundary head)
  - btc_batch songs whose beat_this happened to be clean
    (mediocre chords, great beats — usable for beat / downbeat heads)

So we tag each song with ``(beat_quality, chord_quality)``. The trainer
picks the right subset for each head:

  beat / downbeat heads:  beat_quality ≠ "skip"
  chord_boundary head:    chord_quality ≠ "skip"

Per-axis rules
--------------
beat_quality:
  gold  — cv < 0.05 AND chord-change alignment ≥ 0.5
  ok    — cv < 0.15 AND chord-change alignment ≥ 0.4
  skip  — otherwise (mid-noisy, broken, or missing downbeats)

chord_quality:
  gold  — source ∈ {"midi", "chordify"}
  ok    — source starts with "btc" (BTC-detected, all variants)
  skip  — unknown / missing

Hard gates (excluded entirely)
------------------------------
n_chords < 8, duration < 60s, audio cannot be resolved, OR
both axes are "skip" (no head can train on this song).

NOTE: cv ≥ 0.30 ("broken downbeats") is NOT a hard gate — it just means
beat_quality = skip. The chord labels are still usable for the
chord_boundary head, which reads CQT directly (not beats[]).

Why "chord-change alignment" matters for the beat axis
------------------------------------------------------
``cv`` measures the regularity of downbeat gaps but doesn't notice
*phase* errors — a perfectly-spaced grid offset by half a bar will look
clean (cv ≈ 0) yet be wrong. Alignment (fraction of chord changes
within 70 ms of any downbeat) catches this: in real music ≥ 50% of
chord changes land on or near a downbeat. Below that, the downbeats
are likely phase-shifted relative to the actual harmonic rhythm —
exactly the user-visible "3+1" failure mode.

Output
------
``--out`` directory (default ``backend/ai/splits/``) gets:
  - manifest.json     full per-song axes + paths + alignment metrics
  - train.json        80% — stratified by (beat_quality, chord_quality)
  - val.json          10%
  - test.json         10%
  - audit_report.json cross-tabulated counts, exclusion reasons, quartiles

Splits use the same RNG seed (default 42) so reruns are deterministic.
Stratification uses the combined axis key, so val/test contain proportional
representation of every (beat × chord) cell — gold-only metrics are
reportable without re-shuffling.

Usage
-----
Stats only (count what would land in each cell, no writes)::

    python scripts/build_training_corpus.py --stats --root V:/data/chords \\
        --music-roots Y:/ Z:/

Real build::

    python scripts/build_training_corpus.py --build \\
        --root V:/data/chords \\
        --music-roots Y:/ Z:/ \\
        --out backend/ai/splits

Dry-run (parse args, no I/O)::

    python scripts/build_training_corpus.py --dry-run

Read-only on the chord corpus. Writes only to ``--out``. Safe to run
while batch workers are still updating chords (use ``--skip-recent-sec``
to avoid mid-write files).
"""

from __future__ import annotations

import argparse
import bisect
import json
import logging
import random
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Make backend importable when run as a top-level script
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "backend"))

from backend.ai.bar_arbitrator import _gap_stats  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("build_training_corpus")


# ---------------------------------------------------------------------------
# Hard pre-flight gates (apply to both axes)
# ---------------------------------------------------------------------------

_MIN_CHORDS = 8
_MIN_DURATION_SEC = 60.0

# beat-axis only: a song needs at least this many beats[] to be a
# candidate for beat_quality classification. Below this we tag beat=skip
# but still allow chord-axis training.
_MIN_BEATS = 32

# ---------------------------------------------------------------------------
# beat_quality bands
# ---------------------------------------------------------------------------

_BEAT_GOLD_CV = 0.05
_BEAT_GOLD_ALIGN = 0.50

_BEAT_OK_CV = 0.15
_BEAT_OK_ALIGN = 0.40

# Chord-change alignment tolerance — 70 ms matches mir_eval beat F-measure.
_ALIGN_TOL_SEC = 0.07

# ---------------------------------------------------------------------------
# chord_quality buckets
# ---------------------------------------------------------------------------

# High-trust chord sources: human-mapped (chordify) or quantized from MIDI.
_CHORD_GOLD_SOURCES = {"midi", "chordify"}

# Anything that starts with "btc" — covers "btc", "btc_batch",
# "btc_youtube", "btc_upload", "btc_eval". All run the same model.
_CHORD_OK_PREFIX = "btc"

# ---------------------------------------------------------------------------
# Audio path resolution
# ---------------------------------------------------------------------------

# chord JSONs prefix paths with "@N/" pointing at the Nth music root,
# e.g. "@1/Relax/Foo.flac". We strip the marker and try each --music-root.
_AT_PREFIX_RE = re.compile(r"^@\d+[/\\]")


def resolve_audio_path(rel: str, music_roots: List[Path]) -> Optional[Path]:
    """Resolve a chord JSON ``path`` field to an actual audio file.

    Returns the first matching ``Path``, or None if no root has it.
    Silently skips roots that are unmounted / permission-denied.
    """
    if not rel:
        return None
    stripped = _AT_PREFIX_RE.sub("", rel.replace("\\", "/"))
    for root in music_roots:
        candidate = root / stripped
        try:
            if candidate.exists():
                return candidate
        except OSError:
            continue
    return None


# ---------------------------------------------------------------------------
# Chord-change alignment metric
# ---------------------------------------------------------------------------

def _chord_change_times(chords: List[Dict]) -> List[float]:
    """Return strictly-monotonic chord-onset times (collapses repeats).

    Mirrors backend.ai.bar_arbitrator._chord_change_times so the alignment
    score uses exactly the same anchors the arbitrator consumes downstream.
    """
    if not chords:
        return []
    out = [float(chords[0].get("time", 0.0))]
    last = chords[0].get("chord")
    for c in chords[1:]:
        cur = c.get("chord")
        if cur != last:
            out.append(float(c.get("time", 0.0)))
            last = cur
    return out


def chord_change_downbeat_alignment(chords: List[Dict],
                                    downbeats: List[float],
                                    tol: float = _ALIGN_TOL_SEC) -> float:
    """Fraction of chord changes within ``tol`` seconds of a downbeat.

    1.0 = every chord change lands on a downbeat. 0.0 = none do. Returns
    0.0 (not None) on degenerate inputs so callers can sort it as a score.
    """
    cc = _chord_change_times(chords)
    if not cc or not downbeats:
        return 0.0
    db_sorted = sorted(downbeats)
    hits = 0
    for t in cc:
        i = bisect.bisect_left(db_sorted, t)
        nearest = float("inf")
        if i < len(db_sorted):
            nearest = min(nearest, abs(db_sorted[i] - t))
        if i > 0:
            nearest = min(nearest, abs(db_sorted[i - 1] - t))
        if nearest <= tol:
            hits += 1
    return hits / len(cc)


# ---------------------------------------------------------------------------
# Per-axis classification
# ---------------------------------------------------------------------------

def _duration_of(cd: Dict) -> float:
    """Best-effort song duration from the chord JSON."""
    beats = cd.get("beats") or []
    chords = cd.get("chords") or []
    return max(
        beats[-1] if beats else 0.0,
        chords[-1].get("end", 0.0) if chords else 0.0,
        float(cd.get("duration") or 0.0),
    )


def _classify_beat_axis(cd: Dict) -> Tuple[str, Optional[float], Optional[float]]:
    """Return (beat_quality, raw_cv, alignment) for one song.

    beat_quality ∈ {"gold", "ok", "skip"}. cv / alignment are returned as
    None when not computable (used for audit-report quartiles).
    """
    beats = cd.get("beats") or []
    downbeats = cd.get("downbeats") or []
    chords = cd.get("chords") or []
    if len(beats) < _MIN_BEATS:
        return "skip", None, None
    if not downbeats:
        return "skip", None, None

    _, cv = _gap_stats(downbeats)
    if cv is None:
        return "skip", None, None

    align = chord_change_downbeat_alignment(chords, downbeats)

    if cv < _BEAT_GOLD_CV and align >= _BEAT_GOLD_ALIGN:
        return "gold", cv, align
    if cv < _BEAT_OK_CV and align >= _BEAT_OK_ALIGN:
        return "ok", cv, align
    return "skip", cv, align


def _classify_chord_axis(cd: Dict) -> str:
    """Return chord_quality ∈ {"gold", "ok", "skip"} for one song.

    Pre-flight n_chords gate is applied at the corpus-walk level (we want
    "too-few-chords" to be a single hard exclusion reason, not split
    across both axes).
    """
    source = (cd.get("source") or "").lower()
    if not source:
        return "skip"
    if source in _CHORD_GOLD_SOURCES:
        return "gold"
    if source.startswith(_CHORD_OK_PREFIX):
        return "ok"
    return "skip"


def classify_song(cd: Dict) -> Tuple[str, str, str, Dict]:
    """Return (beat_quality, chord_quality, status, metrics).

    status ∈ {"included", "excluded:<reason>"} — drives the manifest cut.
    Pre-flight gates (n_chords, duration) produce "excluded" with a
    specific reason. Otherwise we compute both axes; if both are "skip",
    the song is also excluded ("no-trainable-axis").
    """
    chords = cd.get("chords") or []
    beats = cd.get("beats") or []
    downbeats = cd.get("downbeats") or []
    source = (cd.get("source") or "").lower()
    beats_source = (cd.get("beats_source") or "").lower()
    duration = _duration_of(cd)

    metrics = {
        "source": source or None,
        "beats_source": beats_source or None,
        "n_chords": len(chords),
        "n_beats": len(beats),
        "n_downbeats": len(downbeats),
        "duration": round(duration, 2),
        "raw_cv": None,
        "alignment": None,
    }

    # Hard pre-flight (apply to both axes)
    if len(chords) < _MIN_CHORDS:
        return "skip", "skip", "excluded:too-few-chords", metrics
    if duration < _MIN_DURATION_SEC:
        return "skip", "skip", "excluded:too-short", metrics

    beat_q, cv, align = _classify_beat_axis(cd)
    metrics["raw_cv"] = round(cv, 4) if cv is not None else None
    metrics["alignment"] = round(align, 4) if align is not None else None

    chord_q = _classify_chord_axis(cd)

    if beat_q == "skip" and chord_q == "skip":
        return beat_q, chord_q, "excluded:no-trainable-axis", metrics

    return beat_q, chord_q, "included", metrics


# ---------------------------------------------------------------------------
# Corpus walk
# ---------------------------------------------------------------------------

def _walk_chord_dir(root: Path):
    """Yield (path, mtime) for every chord JSON under ``root``.

    Mirrors scripts/audit_bar_training_data.py — handles flat AND sharded
    layout, skips ``.bak.*`` backups.
    """
    if not root.exists():
        return
    for p in root.rglob("*.json"):
        if ".bak." in p.name:
            continue
        try:
            yield p, p.stat().st_mtime
        except OSError:
            continue


def collect_corpus(root: Path, music_roots: List[Path],
                   skip_recent_sec: float, max_files: Optional[int]):
    """Single-pass walk + classify. Returns (entries, report)."""
    # 3x3 cross-tab of (beat_quality, chord_quality) for included songs.
    cross_tab: Counter = Counter()
    # 1D counters for quick at-a-glance totals.
    beat_counts: Counter = Counter()
    chord_counts: Counter = Counter()
    exclusion_reasons: Counter = Counter()
    cv_samples: List[float] = []
    align_samples: List[float] = []
    entries: List[Dict] = []
    examples: Dict[str, List[Dict]] = defaultdict(list)
    n_unparseable = 0

    now = time.time()
    n_seen = 0
    n_skipped_recent = 0
    last_log = time.time()

    for path, mtime in _walk_chord_dir(root):
        if now - mtime < skip_recent_sec:
            n_skipped_recent += 1
            continue
        n_seen += 1
        if max_files and n_seen > max_files:
            break

        try:
            cd = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            n_unparseable += 1
            exclusion_reasons[f"unparseable-{type(e).__name__}"] += 1
            continue

        beat_q, chord_q, status, metrics = classify_song(cd)

        # Audio existence is a hard gate, applied AFTER axis classification
        # so the audit report can show "would-be-trainable but missing audio"
        # separately from other exclusion reasons.
        rel = cd.get("path") or ""
        audio_path = resolve_audio_path(rel, music_roots) if rel else None
        metrics["rel_path"] = rel
        metrics["audio_exists"] = audio_path is not None

        if status == "included" and audio_path is None:
            status = "excluded:audio-missing"

        if metrics.get("raw_cv") is not None:
            cv_samples.append(metrics["raw_cv"])
        if metrics.get("alignment") is not None:
            align_samples.append(metrics["alignment"])

        if status.startswith("excluded:"):
            reason = status.split(":", 1)[1]
            exclusion_reasons[reason] += 1
            ex_key = f"excluded:{reason}"
            if len(examples[ex_key]) < 5:
                examples[ex_key].append({"hash": path.stem, **metrics})
            continue

        # Included: record both axes
        cross_tab[(beat_q, chord_q)] += 1
        beat_counts[beat_q] += 1
        chord_counts[chord_q] += 1

        entries.append({
            "hash": path.stem,
            "beat_quality": beat_q,
            "chord_quality": chord_q,
            "label_path": str(path),
            "audio_path": str(audio_path) if audio_path else None,
            "metrics": metrics,
        })

        cell_key = f"{beat_q}|{chord_q}"
        if len(examples[cell_key]) < 5:
            examples[cell_key].append({"hash": path.stem, **metrics})

        if time.time() - last_log > 10:
            logger.info(
                "  scanned %d files | included=%d (b_gold=%d b_ok=%d / c_gold=%d c_ok=%d)",
                n_seen, len(entries),
                beat_counts["gold"], beat_counts["ok"],
                chord_counts["gold"], chord_counts["ok"],
            )
            last_log = time.time()

    # Stringify cross-tab for JSON serialization.
    cross_tab_dict = {f"{b}|{c}": n for (b, c), n in cross_tab.items()}

    report = {
        "n_files_scanned": n_seen,
        "n_files_skipped_recent": n_skipped_recent,
        "n_unparseable": n_unparseable,
        "n_included": len(entries),
        "n_excluded": sum(exclusion_reasons.values()),
        "beat_counts": dict(beat_counts),
        "chord_counts": dict(chord_counts),
        "cross_tab": cross_tab_dict,
        "exclusion_reasons": dict(exclusion_reasons),
        "cv_quartiles": _quartiles(cv_samples),
        "alignment_quartiles": _quartiles(align_samples),
        "examples": dict(examples),
    }
    return entries, report


def _quartiles(values: List[float]):
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


# ---------------------------------------------------------------------------
# Stratified splitting
# ---------------------------------------------------------------------------

def stratified_split(entries: List[Dict], val_frac: float, test_frac: float,
                     seed: int) -> Tuple[List[Dict], List[Dict], List[Dict]]:
    """Per-cell shuffle + slice on the (beat_quality × chord_quality) key.

    Stratifying on the combined axis means val/test contain proportional
    representation of every populated cell — gold-only metrics are
    reportable without re-sampling. Cells with fewer than 3 entries skip
    the val/test slice entirely (everything goes to train).
    """
    rng = random.Random(seed)
    by_cell: Dict[Tuple[str, str], List[Dict]] = defaultdict(list)
    for e in entries:
        by_cell[(e["beat_quality"], e["chord_quality"])].append(e)

    train, val, test = [], [], []
    for (b, c), items in sorted(by_cell.items()):
        rng.shuffle(items)
        n = len(items)
        if n < 3:
            train.extend(items)
            logger.info("  split cell=%s|%s: train=%d val=0 test=0 (too small)", b, c, n)
            continue
        n_test = max(1, int(round(n * test_frac)))
        n_val = max(1, int(round(n * val_frac)))
        # Ensure train gets at least half
        n_val = min(n_val, max(0, n - n_test - n // 2))
        test_part = items[:n_test]
        val_part = items[n_test:n_test + n_val]
        train_part = items[n_test + n_val:]
        train.extend(train_part)
        val.extend(val_part)
        test.extend(test_part)
        logger.info("  split cell=%s|%s: train=%d val=%d test=%d",
                    b, c, len(train_part), len(val_part), len(test_part))
    return train, val, test


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--stats", action="store_true",
                      help="Walk + classify, print summary, no writes")
    mode.add_argument("--build", action="store_true",
                      help="Walk + classify + write splits to --out")
    mode.add_argument("--dry-run", action="store_true",
                      help="Parse args + print config, no I/O")

    ap.add_argument("--root", type=Path,
                    default=Path("V:/data/chords"),
                    help="Chord JSON root (default: V:/data/chords)")
    ap.add_argument("--music-roots", type=Path, nargs="+",
                    default=[Path("Y:/"), Path("Z:/")],
                    help="Music drive roots to resolve `path` against "
                         "(default: Y:/ Z:/)")
    ap.add_argument("--out", type=Path,
                    default=_REPO / "backend" / "ai" / "splits",
                    help="Output dir for manifest + splits "
                         "(default: backend/ai/splits/)")
    ap.add_argument("--skip-recent-sec", type=float, default=600.0,
                    help="Skip files modified in the last N seconds")
    ap.add_argument("--max-files", type=int, default=None,
                    help="Cap files scanned (smoke test)")
    ap.add_argument("--val-frac", type=float, default=0.10)
    ap.add_argument("--test-frac", type=float, default=0.10)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    cfg = {
        "mode": "stats" if args.stats else ("build" if args.build else "dry-run"),
        "root": str(args.root),
        "music_roots": [str(p) for p in args.music_roots],
        "out": str(args.out),
        "skip_recent_sec": args.skip_recent_sec,
        "max_files": args.max_files,
        "val_frac": args.val_frac,
        "test_frac": args.test_frac,
        "seed": args.seed,
    }
    logger.info("config: %s", json.dumps(cfg, ensure_ascii=False))

    if args.dry_run:
        return

    if not args.root.exists():
        logger.error("chord root does not exist: %s", args.root)
        sys.exit(2)

    t0 = time.time()
    entries, report = collect_corpus(
        args.root, args.music_roots, args.skip_recent_sec, args.max_files
    )
    elapsed = time.time() - t0

    summary = {
        "version": 2,  # bumped: dual-axis schema
        "schema": "dual-axis: beat_quality × chord_quality",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "config": cfg,
        "elapsed_sec": round(elapsed, 1),
        **{k: report[k] for k in (
            "n_files_scanned", "n_files_skipped_recent", "n_unparseable",
            "n_included", "n_excluded",
            "beat_counts", "chord_counts", "cross_tab",
            "exclusion_reasons",
        )},
    }

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nCV quartiles: {report['cv_quartiles']}")
    print(f"Alignment quartiles: {report['alignment_quartiles']}")

    if args.stats:
        return

    # --build: write manifest + splits + audit
    args.out.mkdir(parents=True, exist_ok=True)

    train, val, test = stratified_split(
        entries, args.val_frac, args.test_frac, args.seed
    )

    (args.out / "manifest.json").write_text(
        json.dumps({"version": 2, "schema": "dual-axis", "entries": entries},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    for name, part in (("train", train), ("val", val), ("test", test)):
        (args.out / f"{name}.json").write_text(
            json.dumps({"version": 2, "schema": "dual-axis", "entries": part},
                       ensure_ascii=False),
            encoding="utf-8",
        )
    full_report = {**summary,
                   "cv_quartiles": report["cv_quartiles"],
                   "alignment_quartiles": report["alignment_quartiles"],
                   "examples": report["examples"]}
    (args.out / "audit_report.json").write_text(
        json.dumps(full_report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    logger.info("wrote %d manifest entries to %s", len(entries), args.out)
    logger.info("splits: train=%d val=%d test=%d",
                len(train), len(val), len(test))


if __name__ == "__main__":
    main()
