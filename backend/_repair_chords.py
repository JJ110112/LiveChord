"""One-off repair: sort a chord JSON by time, drop near-duplicates, fix ends.

Usage:
    python _repair_chords.py <path/to/file.json> [--dry-run]

Preserves: every top-level field except ``chords`` (path, key, capo, bpm,
    source, beats, downbeats, tempo_curve, beats_source, beat_version, …).
    New fields are picked up automatically — no allowlist to maintain.
Rewrites: chord array order (sorted by time) and end values (= next.time).
Drops: any chord whose time is within 0.3s of the previous surviving chord
       (tie-break: KEEP the one that appears LATER in the original array,
       on the assumption that later-indexed entries are from more recent edits).
"""

import json
import sys
from pathlib import Path

if len(sys.argv) < 2:
    print("usage: python _repair_chords.py <file.json> [--dry-run]")
    sys.exit(1)

path = Path(sys.argv[1])
dry_run = "--dry-run" in sys.argv

sys.stdout.reconfigure(encoding="utf-8")

data = json.loads(path.read_text(encoding="utf-8"))
orig = data["chords"]
print(f"Before: {len(orig)} chords")

# Pair each chord with its original index so ties can be broken by index
indexed = list(enumerate(orig))

# Sort by time; on tie, prefer LATER original index (reverse stable sort trick)
indexed.sort(key=lambda p: (p[1]["time"], -p[0]))

# Dedupe consecutive (in time-sorted order) entries within 0.3s
MIN_GAP = 0.3
kept = []
for idx, c in indexed:
    if kept and c["time"] - kept[-1][1]["time"] < MIN_GAP:
        # Within tolerance — keep the one with higher original index
        if idx > kept[-1][0]:
            kept[-1] = (idx, c)
        # else: drop the new one
    else:
        kept.append((idx, c))

repaired = [c for _, c in kept]

# Rewrite each end to match the next chord's time (keep last chord's end as-is)
for i in range(len(repaired) - 1):
    repaired[i]["end"] = round(repaired[i + 1]["time"], 3)

# Diagnostics
dropped = len(orig) - len(repaired)
print(f"After:  {len(repaired)} chords ({dropped} dropped as duplicates)")
print(f"Time range: {repaired[0]['time']:.2f}s – {repaired[-1].get('end', repaired[-1]['time']):.2f}s")

# Show first few to sanity-check ordering
print("\nFirst 5:")
for c in repaired[:5]:
    print(f"  time={c['time']:.3f}  end={c.get('end'):.3f}  chord={c['chord']}")
print("Last 5:")
for c in repaired[-5:]:
    print(f"  time={c['time']:.3f}  end={c.get('end'):.3f}  chord={c['chord']}")

if dry_run:
    print("\n[dry-run] no file written")
else:
    data["chords"] = repaired
    backup = path.with_suffix(path.suffix + ".bak")
    if not backup.exists():
        backup.write_text(json.dumps({"chords": orig, **{k: v for k, v in data.items() if k != "chords"}},
                                     ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Backup written: {backup}")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Repaired: {path}")
