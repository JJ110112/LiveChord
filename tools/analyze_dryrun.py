"""
Analyze a midi_catalog.jsonl produced by identify_midi.py --dry-run.

Outputs:
  - ASCII histograms of normalized_cost and warp_r2 (always)
  - A PNG with matplotlib if available
  - A ranked "suspected true positives" list: entries that FAILED the
    default --cost-threshold gate but LOOK like real matches based on
    the secondary signals (warp R², key-mode match, tempo if cached).
    Use this list to decide whether the cost threshold needs loosening
    or the synth backend needs upgrading.

Usage:
  python analyze_dryrun.py --catalog /tmp/dryrun_catalog.jsonl \
      [--cost-threshold 0.08] [--warp-r2-threshold 0.95] \
      [--png /tmp/dryrun_hist.png]
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
from collections import Counter, defaultdict

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass


def _hist(values: list[float], buckets: int = 20, width: int = 60) -> str:
    if not values:
        return "(no data)"
    lo = min(values); hi = max(values)
    if hi - lo < 1e-6:
        hi = lo + 1e-6
    edges = [lo + (hi - lo) * i / buckets for i in range(buckets + 1)]
    counts = [0] * buckets
    for v in values:
        idx = min(int((v - lo) / (hi - lo) * buckets), buckets - 1)
        counts[idx] += 1
    peak = max(counts) or 1
    lines: list[str] = []
    for i in range(buckets):
        left = edges[i]; right = edges[i + 1]
        bar = "█" * int(counts[i] / peak * width)
        lines.append(f"  {left:6.3f}–{right:6.3f}  {counts[i]:5d}  {bar}")
    return "\n".join(lines)


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((len(s) - 1) * p))))
    return s[k]


def _try_matplotlib(entries: list[dict], png_path: str) -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:
        print(f"  [PNG skipped] matplotlib unavailable: {e}")
        return False
    costs = [e["normalized_cost"] for e in entries if "normalized_cost" in e]
    r2s = [e["warp_r2"] for e in entries if "warp_r2" in e]
    if not costs:
        return False
    fig, axes = plt.subplots(1, 3, figsize=(15, 4))
    axes[0].hist(costs, bins=30, color="#4c8bf5", edgecolor="white")
    axes[0].axvline(0.08, color="red", linestyle="--", label="cost=0.08")
    axes[0].set_title("normalized_cost distribution")
    axes[0].set_xlabel("cost"); axes[0].legend()
    axes[1].hist(r2s, bins=30, color="#55b66f", edgecolor="white")
    axes[1].axvline(0.95, color="red", linestyle="--", label="R²=0.95")
    axes[1].set_title("warp_r2 distribution")
    axes[1].set_xlabel("R²"); axes[1].legend()
    axes[2].scatter(costs, r2s, s=8, alpha=0.5)
    axes[2].axvline(0.08, color="red", linestyle="--")
    axes[2].axhline(0.95, color="red", linestyle="--")
    axes[2].set_xlabel("normalized_cost"); axes[2].set_ylabel("warp_r2")
    axes[2].set_title("cost vs R²  (lower-right quadrant = matched)")
    plt.tight_layout()
    plt.savefig(png_path, dpi=120)
    plt.close(fig)
    print(f"  [PNG] wrote {png_path}")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--cost-threshold", type=float, default=0.08)
    ap.add_argument("--warp-r2-threshold", type=float, default=0.95)
    ap.add_argument("--png", default=None)
    ap.add_argument("--top-suspected", type=int, default=30)
    args = ap.parse_args()

    entries: list[dict] = []
    statuses: Counter[str] = Counter()
    with open(args.catalog, "r", encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
                entries.append(r)
                statuses[r.get("status", "?")] += 1
            except Exception:
                pass

    print(f"Catalog: {args.catalog}")
    print(f"Total entries: {len(entries)}")
    print("Status breakdown:")
    for s, c in statuses.most_common():
        print(f"  {s:30s} {c:5d}  ({100*c/max(len(entries),1):.1f}%)")

    scored = [e for e in entries if "normalized_cost" in e and "warp_r2" in e]
    print(f"\nScored (synth+DTW ran): {len(scored)}")
    if not scored:
        return

    costs = [e["normalized_cost"] for e in scored]
    r2s = [e["warp_r2"] for e in scored]

    print(f"\nnormalized_cost distribution  (threshold={args.cost_threshold:.3f})")
    print(f"  min={min(costs):.4f}  p10={_pct(costs,0.10):.4f}  p25={_pct(costs,0.25):.4f}  "
          f"p50={_pct(costs,0.50):.4f}  p75={_pct(costs,0.75):.4f}  max={max(costs):.4f}")
    print(_hist(costs, buckets=20))

    print(f"\nwarp_r2 distribution  (threshold>={args.warp_r2_threshold:.2f})")
    print(f"  min={min(r2s):.3f}  p10={_pct(r2s,0.10):.3f}  p50={_pct(r2s,0.50):.3f}  "
          f"p90={_pct(r2s,0.90):.3f}  max={max(r2s):.3f}")
    print(_hist(r2s, buckets=20))

    # Quadrant counts
    low_cost = [e for e in scored if e["normalized_cost"] < args.cost_threshold]
    high_r2 = [e for e in scored if e["warp_r2"] >= args.warp_r2_threshold]
    both = [e for e in scored
            if e["normalized_cost"] < args.cost_threshold and e["warp_r2"] >= args.warp_r2_threshold]
    print(f"\nQuadrant counts:")
    print(f"  cost<{args.cost_threshold:.3f}           : {len(low_cost)}")
    print(f"  warp_r2>={args.warp_r2_threshold:.2f}            : {len(high_r2)}")
    print(f"  BOTH (= 'matched' candidate): {len(both)}")

    # Mode-match rate
    mode_matches = sum(1 for e in scored if e.get("mode_match"))
    print(f"  mode_match=True        : {mode_matches} ({100*mode_matches/len(scored):.1f}%)")

    # Suspected true positives — rejected by cost but secondary signals look good
    susp = []
    for e in scored:
        if e["normalized_cost"] < args.cost_threshold:
            continue  # already accepted
        # score: how "real-looking" is this near-miss?
        score = 0
        if e.get("warp_r2", 0) >= 0.97: score += 2
        elif e.get("warp_r2", 0) >= 0.93: score += 1
        if e.get("mode_match"): score += 1
        if e.get("root_match"): score += 1
        if e.get("tempo_match") is True: score += 2
        if e.get("coarse_score", 0) >= 0.98: score += 1
        if e.get("artist_hint"):
            # If the filename had a clear artist hint AND the match's artist
            # looks similar, score extra
            hint = e["artist_hint"].lower()
            mart = (e.get("artist") or "").lower()
            if hint and mart and (hint in mart or mart in hint):
                score += 3
        if score >= 3:
            susp.append((score, e))
    susp.sort(key=lambda t: (-t[0], t[1]["normalized_cost"]))

    print(f"\nSuspected true positives filtered out by cost threshold: {len(susp)}")
    print(f"  (ranked by secondary-evidence score; pick threshold by eyeballing these)")
    print()
    print(f"  {'score':>5}  {'cost':>7}  {'r2':>5}  {'key_q':>8}  {'key_r':>8}  "
          f"{'mode':>5}  {'tempo':>5}  {'hint':<20}  match")
    for score, e in susp[: args.top_suspected]:
        mn = os.path.basename(e.get("midi_path", ""))[:40]
        an = e.get("artist", "")[:16]
        tn = e.get("title", "")[:22]
        match_label = f"{an} – {tn}"
        hint = (e.get("artist_hint") or "")[:20]
        tempo = "yes" if e.get("tempo_match") is True else ("no" if e.get("tempo_match") is False else "?")
        print(f"  {score:>5}  {e['normalized_cost']:7.4f}  {e['warp_r2']:5.3f}  "
              f"{e.get('key_query','?'):>8}  {e.get('key_ref','?'):>8}  "
              f"{'✓' if e.get('mode_match') else '✗':>5}  {tempo:>5}  "
              f"{hint:<20}  {match_label}")
        print(f"         midi: {mn}")

    # Cost distribution broken down by mode_match (true positives should cluster
    # in the mode_match=True group)
    print("\nCost percentiles conditioned on mode_match:")
    for mm in (True, False):
        vals = [e["normalized_cost"] for e in scored if e.get("mode_match") == mm]
        if vals:
            print(f"  mode_match={mm}: N={len(vals):4d}  "
                  f"p10={_pct(vals,0.10):.4f}  p25={_pct(vals,0.25):.4f}  "
                  f"p50={_pct(vals,0.50):.4f}  p75={_pct(vals,0.75):.4f}")

    if args.png:
        _try_matplotlib(scored, args.png)


if __name__ == "__main__":
    main()
