"""Offline preview for the isolated-short-chord filter.

Read-only — never writes back to the chord JSON. Loads a chord JSON, applies
``chord_noise_filter.filter_isolated_short_chords`` against its existing
``chords``/``downbeats``/``bpm``, and prints every removed event with the
prev/next context. Used to verify the filter against golden songs before
shipping to ingest.

Usage:
    python tools/preview_isolated_filter.py [chord_json_path]

Default path: V:/data/chords/b9/b9ff54449865.json (愛我久一點 golden fixture).
"""

import json
import sys
from pathlib import Path

# Make backend importable without modifying sys.path elsewhere.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from chord_noise_filter import filter_isolated_short_chords  # noqa: E402


DEFAULT_PATH = Path(r"V:\data\chords\b9\b9ff54449865.json")


def main(argv):
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT_PATH
    if not path.is_file():
        print(f"❌ chord JSON not found: {path}")
        return 1

    data = json.loads(path.read_text(encoding="utf-8"))
    title = data.get("title") or Path(data.get("path", "")).stem or path.stem
    bpm = float(data.get("bpm") or 0)
    downbeats = data.get("downbeats") or []
    chords = data.get("chords") or []

    print(f"[Isolated Filter Preview] Loading {path.name} ({title})...")
    if not bpm or not downbeats or len(chords) < 3:
        print(f"❌ insufficient beat data: bpm={bpm}, downbeats={len(downbeats)}, chords={len(chords)}")
        return 1
    spb = 60.0 / bpm
    print(f"✓ Detected {bpm:.1f} BPM, SPB {spb:.3f}s, {len(downbeats)} downbeats, {len(chords)} chords")

    out, meta = filter_isolated_short_chords(chords, downbeats, bpm)

    for r in meta.get("removed", []):
        path_tag = r["path"]
        ratio_str = f" (Ratio: {r['rel_short_ratio']:.3f})" if r.get("rel_short_ratio") is not None else ""
        print(
            f"🔥 MATCH FOUND: {r['time']:.2f}s - {r['end']:.2f}s [{r['chord']}] "
            f"({r['dur_sec']:.2f}s) sandwiched by {r['prev_chord']} -> {r['next_chord']}. "
            f"Path: {path_tag}{ratio_str}"
        )

    removed_n = meta.get("removed_count", 0)
    before_n = len(chords)
    after_n = len(out)
    print(
        f"🚀 Filter completed. Total {removed_n} noise elements suppressed. "
        f"Chord count: {before_n} -> {after_n}"
    )

    # Golden-song specific check — only meaningful for b9ff54449865
    if path.name == "b9ff54449865.json":
        wanted = [
            (24.23, "D", "P1"),
            (45.93, "D", "P2"),
            (48.43, "A", "P1"),
        ]
        removed_lookup = {(round(r["time"], 2), r["chord"]): r["path"] for r in meta.get("removed", [])}
        all_hit = True
        for t, ch, expected_path in wanted:
            actual_path = removed_lookup.get((t, ch))
            if actual_path != expected_path:
                print(f"⚠️  golden case MISSED: {t}s {ch} (expected {expected_path}, got {actual_path!r})")
                all_hit = False
        if all_hit:
            print("✅ All 3 golden cases matched.")
        else:
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
