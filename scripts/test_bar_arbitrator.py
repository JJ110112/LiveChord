"""Test bar_arbitrator ONNX phase corrector on user-labeled downbeat-phase
problem songs. Reports before/after phase and whether the new downbeats
align better with chord changes.

Reads-only — does NOT mutate chord JSONs. Use admin endpoint or backfill
script to actually apply changes.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "backend")
from ai.bar_arbitrator import arbitrate_model_based, _alignment

MODEL = "data/models/bar_arbitrator_v1.onnx"

SONGS = [
    ("T-Square - Kasuba No Shonen", "V:/data/chords/fd/fdfec02c13e3.json"),
    ("Slow Jazzy Blues Jam G Min", "V:/data/chords/80/803fdc6e5288.json"),
    ("Crusaders - Wayne's Pop", "V:/data/chords/71/717b1e5ac11f.json"),
    ("Snarky Puppy - Thorn", "V:/data/chords/42/42121bd7d777.json"),
]


def show_phase_relative(downbeats, beats, n=8):
    """Show first N downbeats and where they sit in the beat[] index."""
    if not beats:
        return "no beats"
    out = []
    for db in downbeats[:n]:
        # Find nearest beat index
        nearest_i = min(range(len(beats)), key=lambda i: abs(beats[i] - db))
        out.append(f"db@{db:.2f}=beat[{nearest_i}]")
    return " ".join(out)


def main():
    for label, fp in SONGS:
        path = Path(fp)
        if not path.exists():
            print(f"\n=== {label} ===\n  MISSING: {fp}")
            continue
        d = json.loads(path.read_text(encoding="utf-8"))
        chords = d.get("chords") or []
        beats = d.get("beats") or []
        original_dbs = list(d.get("downbeats") or [])
        chord_changes = [c["time"] for c in chords[1:] if c.get("time") is not None]

        print(f"\n=== {label} ===")
        print(f"  bpm={d.get('bpm', 0):.1f}")
        print(f"  n_beats={len(beats)} n_downbeats={len(original_dbs)} n_chords={len(chords)}")

        # Run model with force=True (bypass already-regular gate so we always see what model would do)
        try:
            result = arbitrate_model_based(d, MODEL, force=True)
        except Exception as e:
            print(f"  ERROR: {type(e).__name__}: {e}")
            continue

        if not result.get("applied"):
            print(f"  NOT APPLIED — reason: {result.get('reason')}")
            continue

        new_dbs = result.get("downbeats_after") or []
        print(f"  applied=True bpb={result.get('beats_per_bar')} reason={result.get('reason', '')[:80]}")
        print(f"  before: phase_off={original_dbs[0] if original_dbs else 0:.3f}s, n_db={len(original_dbs)}, align={_alignment(chord_changes, original_dbs):.3f}")
        print(f"  after : phase_off={new_dbs[0] if new_dbs else 0:.3f}s, n_db={len(new_dbs)}, align={_alignment(chord_changes, new_dbs):.3f}")

        # Show beat-index of first 8 downbeats before/after
        print(f"  before db beats[]: {show_phase_relative(original_dbs, beats, 8)}")
        print(f"  after  db beats[]: {show_phase_relative(new_dbs, beats, 8)}")

        # Phase shift detection: did the model move beat-1 by some integer beats?
        if original_dbs and new_dbs:
            shift = new_dbs[0] - original_dbs[0]
            spb = (beats[1] - beats[0]) if len(beats) >= 2 else 0.5
            beat_shift = round(shift / spb) if spb > 0 else 0
            print(f"  phase shift: {shift:+.3f}s = {beat_shift:+d} beats (negative = downbeat moved earlier)")


if __name__ == "__main__":
    main()
