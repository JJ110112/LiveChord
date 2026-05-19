"""Cross-genre BTC chord-onset offset audit (read-only).

Samples chord JSONs from ``V:\\data\\chords`` (sharded), computes the signed
offset of each chord's ``time`` from the nearest entry in ``beats[]``, and
produces a per-song / per-genre / global summary in Markdown.

The point: figure out whether the ~0.42s LiveChord-vs-Chordify offset observed
on a single song (CoCo Lee 愛我久一點, hash ``b9ff54449865``) is a global
systematic BTC lead or a single-track acoustic glitch. The decision drives
whether Layer 2 (process_queue onset snap) is safe to ship library-wide or
must be scoped to specific genres / fragment-guard-vetoed songs.

Pure read-only — no file writes, no API calls, no chord JSON mutations.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import os
import random
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

CHORDS_DIR = Path("V:/data/chords")
ANCHOR_HASH = "b9ff54449865"
DEFAULT_SAMPLES_PER_GENRE = 8
SEED = 42


_KNOWN_TOPS = {
    "POP", "JAZZ", "CLASSICS", "RELAX", "SLEEP", "CHRISTMAS", "JAM",
    "OTHER", "ELECTRONIC DANCE MUSIC", "EDM",
}


def infer_genre(path_str: str) -> str:
    """Map chord_data['path'] to a genre bucket.

    Path field is inconsistent across the library:
      @1/POP/C-POP/Artist/Track.flac      ← path-ingested w/ root token
      POP/C-POP/Artist/Track.flac          ← path-ingested w/o root token
      Jazz/Artist/Album/Track.flac
      Backstreet Boys - I Want It.flac     ← upload (no directory)
      #recycle/old/Track.flac               ← recycle bin spill

    Returns:
      - "POP/<subgenre>" for songs under POP/<X>/...
      - "<top>" for songs under one of _KNOWN_TOPS
      - "UNKNOWN" for uploads / unfamiliar tops — caller filters these out
    """
    if not path_str:
        return "UNKNOWN"
    s = path_str.replace("\\", "/")
    if s.startswith("@1/"):
        s = s[3:]
    parts = [p for p in s.split("/") if p]
    if len(parts) < 2:
        # Just a filename, no folder → no genre we can trust
        return "UNKNOWN"
    top = parts[0]
    if top.upper() not in _KNOWN_TOPS:
        return "UNKNOWN"
    if top.upper() == "POP" and len(parts) >= 3:
        return f"POP/{parts[1]}"
    return top


def chord_offsets(beats: List[float], chords: List[dict]) -> List[Tuple[float, str]]:
    """For each non-first chord, return (signed_offset, chord_name).

    signed_offset = chord.time - nearest_beat   (positive = chord late vs beat).
    """
    out: List[Tuple[float, str]] = []
    if len(beats) < 3 or len(chords) < 2:
        return out
    for c in chords[1:]:
        t = c.get("time")
        if t is None:
            continue
        try:
            t = float(t)
        except (TypeError, ValueError):
            continue
        i = bisect.bisect_left(beats, t)
        candidates = []
        if i > 0:
            candidates.append(beats[i - 1])
        if i < len(beats):
            candidates.append(beats[i])
        if not candidates:
            continue
        nearest = min(candidates, key=lambda b: abs(b - t))
        out.append((t - nearest, str(c.get("chord", ""))))
    return out


def song_stats(json_path: Path, chord_data: dict) -> Optional[dict]:
    """Compute per-song offset summary, or None if the JSON doesn't qualify."""
    chords = chord_data.get("chords") or []
    beats = chord_data.get("beats") or []
    bpm = chord_data.get("bpm") or 0.0
    if len(chords) < 5 or len(beats) < 20 or not (bpm > 0):
        return None
    try:
        beats_f = [float(b) for b in beats]
    except (TypeError, ValueError):
        return None
    spb = 60.0 / float(bpm)
    offsets = [o for o, _ in chord_offsets(beats_f, chords)]
    if not offsets:
        return None
    abs_offsets = [abs(o) for o in offsets]
    mean_s = statistics.mean(offsets)
    median_s = statistics.median(offsets)
    rmse = math.sqrt(sum(o * o for o in offsets) / len(offsets))
    max_abs = max(abs_offsets)
    pct_50 = sum(1 for o in abs_offsets if o < 0.050) / len(abs_offsets)
    pct_q = sum(1 for o in abs_offsets if o < 0.25 * spb) / len(abs_offsets)
    return {
        "json_path": str(json_path),
        "path": chord_data.get("path", json_path.name),
        "genre": infer_genre(chord_data.get("path", "")),
        "bpm": float(bpm),
        "spb": spb,
        "beats_source": chord_data.get("beats_source"),
        "n_chords": len(chords),
        "n_beats": len(beats_f),
        "n_offsets": len(offsets),
        "mean_signed": mean_s,
        "median_signed": median_s,
        "rmse": rmse,
        "max_abs": max_abs,
        "pct_within_50ms": pct_50,
        "pct_within_qbeat": pct_q,
    }


def walk_library(chords_dir: Path) -> List[Path]:
    """List every chord JSON, skipping refiner backups."""
    out: List[Path] = []
    if not chords_dir.exists():
        return out
    for shard in sorted(chords_dir.iterdir()):
        if not shard.is_dir():
            continue
        for f in shard.iterdir():
            if not f.is_file():
                continue
            name = f.name
            if not name.endswith(".json"):
                continue
            if name.endswith(".bak.beat_refiner.json"):
                continue
            if ".bak." in name:
                continue
            out.append(f)
    return out


def safe_load(path: Path) -> Optional[dict]:
    try:
        with path.open("r", encoding="utf-8") as fp:
            return json.load(fp)
    except Exception:
        return None


_PATH_RE = re.compile(r'"path"\s*:\s*"((?:[^"\\]|\\.)*)"')


def peek_path(json_path: Path, head_bytes: int = 2048) -> Optional[str]:
    """Extract ``path`` from JSON without parsing the whole file.

    81k chord JSONs × full json.load is ~slow because chords[] / beats[] /
    tempo_curve[] dominate file size. ``path`` is always near the top of
    each JSON (first key per our writer), so a regex on the first ~2KB is
    enough to bucket the song by genre. Sampled songs get full json.load
    later for the offset computation.
    """
    try:
        with json_path.open("rb") as fp:
            head = fp.read(head_bytes)
        text = head.decode("utf-8", errors="replace")
        m = _PATH_RE.search(text)
        if not m:
            return None
        # JSON-decode the captured string so escapes (\\, \", \uXXXX) resolve
        try:
            return json.loads(f'"{m.group(1)}"')
        except json.JSONDecodeError:
            return m.group(1)
    except Exception:
        return None


def sample_by_genre(
    files: List[Path],
    samples_per_genre: int,
    seed: int,
    progress_every: int = 5000,
) -> Tuple[Dict[str, List[Path]], int]:
    """Group all JSONs by genre via lightweight path-peek, then sample.

    Returns (sampled_by_genre, total_with_path).
    """
    rng = random.Random(seed)
    by_genre: Dict[str, List[Path]] = defaultdict(list)
    have_path = 0
    for i, f in enumerate(files, 1):
        if i % progress_every == 0:
            print(f"# bucketed {i}/{len(files)}...", file=sys.stderr)
        p = peek_path(f)
        if not p:
            continue
        have_path += 1
        genre = infer_genre(p)
        if genre == "UNKNOWN":
            continue
        by_genre[genre].append(f)

    sampled: Dict[str, List[Path]] = {}
    for g, items in by_genre.items():
        if not items:
            continue
        n = min(samples_per_genre, len(items))
        sampled[g] = rng.sample(items, n)
    return sampled, have_path


def find_anchor(files: List[Path], anchor_hash: str) -> Optional[Tuple[Path, dict]]:
    """Return (path, data) for the explicit CoCo Lee anchor song."""
    for f in files:
        if f.stem == anchor_hash:
            d = safe_load(f)
            if d:
                return f, d
    return None


def render_md(
    rows: List[dict],
    anchor_row: Optional[dict],
    samples_per_genre: int,
    total_scanned: int,
) -> str:
    """Build the Markdown report."""
    lines: List[str] = []
    lines.append("# BTC Chord-Onset Offset Audit (cross-genre, blind sample)")
    lines.append("")
    lines.append(f"- Generated by `tools/audit_library_offsets.py` (seed=42, samples/genre={samples_per_genre})")
    lines.append(f"- Library: `V:\\data\\chords\\*\\*.json` — scanned **{total_scanned}** qualifying songs")
    lines.append(f"- Sampled **{len(rows)}** songs across **{len({r['genre'] for r in rows})}** genres")
    lines.append("- offset = `chord.time - nearest beat` (signed; negative = chord onset *earlier* than nearest beat = BTC leading)")
    lines.append("- Anchor reference song (CoCo Lee 愛我久一點, hash `b9ff54449865`) appended below for cross-check")
    lines.append("")

    # ----- Per-song table (sorted by median_signed for readability) -----
    lines.append("## Per-song")
    lines.append("")
    lines.append("| genre | bpm | beats_source | n_ch | n_off | median_signed (s) | mean_signed (s) | RMSE (s) | max_abs (s) | %<50ms | %<¼·spb | path |")
    lines.append("|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for r in sorted(rows, key=lambda x: (x["genre"], x["median_signed"])):
        lines.append(
            f"| {r['genre']} | {r['bpm']:.1f} | {r['beats_source'] or '?'} | "
            f"{r['n_chords']} | {r['n_offsets']} | "
            f"{r['median_signed']:+.3f} | {r['mean_signed']:+.3f} | "
            f"{r['rmse']:.3f} | {r['max_abs']:.3f} | "
            f"{r['pct_within_50ms']*100:.0f}% | {r['pct_within_qbeat']*100:.0f}% | "
            f"`{r['path']}` |"
        )
    lines.append("")

    if anchor_row is not None:
        lines.append("### Anchor — CoCo Lee `Ai Wo Jiu Yi Dian` (single-song reference; not in random sample)")
        lines.append("")
        lines.append("| genre | bpm | beats_source | n_ch | n_off | median_signed | mean_signed | RMSE | max_abs | %<50ms | %<¼·spb | path |")
        lines.append("|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---|")
        r = anchor_row
        lines.append(
            f"| **{r['genre']}** | {r['bpm']:.1f} | {r['beats_source'] or '?'} | "
            f"{r['n_chords']} | {r['n_offsets']} | "
            f"**{r['median_signed']:+.3f}** | {r['mean_signed']:+.3f} | "
            f"{r['rmse']:.3f} | {r['max_abs']:.3f} | "
            f"{r['pct_within_50ms']*100:.0f}% | {r['pct_within_qbeat']*100:.0f}% | "
            f"`{r['path']}` |"
        )
        lines.append("")
        lines.append(f"_User-provided baseline: median_signed ≈ **-0.420 s** from Chordify diff. Audit measured: **{r['median_signed']:+.3f} s** — these should agree within a few ms if the script's geometry is correct (Chordify diff is chord-vs-chord; audit is chord-vs-beat, but both reflect the same BTC onset)._")
        lines.append("")

    # ----- Per-genre aggregate -----
    lines.append("## Per-genre aggregate (over per-song median_signed values)")
    lines.append("")
    lines.append("| genre | n | median(med_signed) | IQR (25–75%) | min | max | songs with med<-200ms | songs with \\|med\\|<50ms |")
    lines.append("|---|---:|---:|---|---:|---:|---:|---:|")
    by_genre: Dict[str, List[dict]] = defaultdict(list)
    for r in rows:
        by_genre[r["genre"]].append(r)
    for g in sorted(by_genre):
        gr = by_genre[g]
        meds = sorted(x["median_signed"] for x in gr)
        n = len(meds)
        med = statistics.median(meds)
        # quartiles via index — small n
        q1 = meds[max(0, (n - 1) // 4)]
        q3 = meds[min(n - 1, (3 * (n - 1)) // 4)]
        leading = sum(1 for x in meds if x < -0.200)
        tight = sum(1 for x in meds if abs(x) < 0.050)
        lines.append(
            f"| {g} | {n} | {med:+.3f} | {q1:+.3f} – {q3:+.3f} | {min(meds):+.3f} | {max(meds):+.3f} | {leading} | {tight} |"
        )
    lines.append("")

    # ----- Global summary -----
    lines.append("## Global summary (across all sampled songs)")
    lines.append("")
    all_meds = sorted(r["median_signed"] for r in rows)
    n = len(all_meds)
    if n:
        gmed = statistics.median(all_meds)
        q1 = all_meds[max(0, (n - 1) // 4)]
        q3 = all_meds[min(n - 1, (3 * (n - 1)) // 4)]
        lines.append(f"- Median of per-song `median_signed`: **{gmed:+.3f} s**")
        lines.append(f"- IQR (25% – 75%): {q1:+.3f} s – {q3:+.3f} s")
        lines.append(f"- Range: {min(all_meds):+.3f} s … {max(all_meds):+.3f} s")
        lines.append("")

        # Histogram bins
        bins = [
            ("(-∞, -0.500)", lambda x: x < -0.500),
            ("[-0.500, -0.300)", lambda x: -0.500 <= x < -0.300),
            ("[-0.300, -0.100)", lambda x: -0.300 <= x < -0.100),
            ("[-0.100, -0.050)", lambda x: -0.100 <= x < -0.050),
            ("[-0.050, +0.050]", lambda x: -0.050 <= x <= 0.050),
            ("( +0.050, +0.100]", lambda x: 0.050 < x <= 0.100),
            ("( +0.100, +0.300]", lambda x: 0.100 < x <= 0.300),
            ("( +0.300, +0.500]", lambda x: 0.300 < x <= 0.500),
            ("( +0.500, +∞)", lambda x: x > 0.500),
        ]
        lines.append("**Distribution of per-song median_signed offset:**")
        lines.append("")
        lines.append("| bin (seconds) | count | bar |")
        lines.append("|---|---:|---|")
        max_count = max(sum(1 for x in all_meds if pred(x)) for _, pred in bins) or 1
        for label, pred in bins:
            c = sum(1 for x in all_meds if pred(x))
            bar = "█" * int(round(40 * c / max_count))
            lines.append(f"| `{label}` | {c} | {bar} |")
        lines.append("")

    # ----- Interpretation guide -----
    lines.append("## Interpretation guide (paste into next session)")
    lines.append("")
    lines.append("Cross-reference with the user's three-case decision matrix:")
    lines.append("")
    lines.append("- **Case A (Global systematic BTC lead)** — Global median ≈ -0.4 s AND most genres show consistent negative median (|x| > 200ms). → Layer 2 onset-snap in `backend/process_queue.py` with **double gate** `|chord[i].time - beat_nearest| < min(0.25 * spb, 150ms)` is the right fix.")
    lines.append("- **Case B (Single-track acoustic glitch)** — Global median ≈ 0 AND most songs have |median_signed| < 50 ms, but anchor song stays at -0.42 s. → DO NOT touch process_queue. Add Layer 3 escalation in `chord_splitter.py` when `fragment_guard.skipped > 10`.")
    lines.append("- **Case C (Genre-correlated bias)** — Some genres consistently offset, others not. → Onset-snap gated on inferred genre / acoustic features, NOT global.")
    lines.append("")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--chords-dir", type=Path, default=CHORDS_DIR, help="Sharded chord JSON root (default: V:/data/chords)")
    ap.add_argument("--samples-per-genre", type=int, default=DEFAULT_SAMPLES_PER_GENRE)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--dry-run", action="store_true", help="List sampled paths only, do not compute offsets")
    ap.add_argument("--out", type=Path, default=None, help="Write report to file (default: stdout)")
    args = ap.parse_args(argv)

    if not args.chords_dir.exists():
        print(f"ERROR: chord dir not found: {args.chords_dir}", file=sys.stderr)
        return 2

    files = walk_library(args.chords_dir)
    print(f"# walked {len(files)} chord JSONs from {args.chords_dir}", file=sys.stderr)

    sampled, have_path = sample_by_genre(files, args.samples_per_genre, args.seed)
    print(f"# JSONs with extractable path: {have_path}", file=sys.stderr)
    print(f"# sampled {sum(len(v) for v in sampled.values())} songs across {len(sampled)} genres", file=sys.stderr)

    if args.dry_run:
        print("# dry-run: listing sampled paths only")
        for g in sorted(sampled):
            print(f"\n## {g} ({len(sampled[g])} sampled)")
            for f in sampled[g]:
                p = peek_path(f) or "?"
                print(f"  {f.name}  {p}")
        return 0

    rows: List[dict] = []
    for g in sorted(sampled):
        for f in sampled[g]:
            d = safe_load(f)
            if d is None:
                continue
            s = song_stats(f, d)
            if s:
                rows.append(s)
        print(f"# processed genre {g}: {len(sampled[g])} songs", file=sys.stderr)

    # Anchor — always include CoCo Lee for cross-check, even if randomly sampled
    anchor_path = files[0].parent.parent / ANCHOR_HASH[:2] / f"{ANCHOR_HASH}.json"
    anchor_row = None
    if anchor_path.exists():
        ad = safe_load(anchor_path)
        if ad is not None:
            anchor_row = song_stats(anchor_path, ad)
        # If anchor was also in random sample, dedupe (compare json_path)
        if anchor_row:
            rows = [r for r in rows if r.get("json_path") != anchor_row.get("json_path")]
        print(f"# anchor song included (hash={ANCHOR_HASH})", file=sys.stderr)
    else:
        print(f"# WARNING: anchor song hash {ANCHOR_HASH} not found in library", file=sys.stderr)

    md = render_md(rows, anchor_row, args.samples_per_genre, have_path)

    if args.out:
        args.out.write_text(md, encoding="utf-8")
        print(f"# wrote report to {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
