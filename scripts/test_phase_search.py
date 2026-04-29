"""For each problem song, brute-force try all 4 possible phases (downbeat
on beat[0], beat[1], beat[2], beat[3]) and report alignment to chord
changes for each. The phase with best alignment is the ground truth —
shows whether the user's notes ("down beat at 2nd") match the math.

If max-align phase > current phase by clear margin, the model SHOULD have
picked that. The fact it didn't tells us training-data is missing
patterns like this.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "backend")
from ai.bar_arbitrator import _alignment

SONGS = [
    ("T-Square - Kasuba No Shonen", "V:/data/chords/fd/fdfec02c13e3.json", "user said: down beat almost at 2nd~3rd"),
    ("Slow Jazzy Blues Jam G Min", "V:/data/chords/80/803fdc6e5288.json", "user said: bass over drum beat"),
    ("Crusaders - Wayne's Pop", "V:/data/chords/71/717b1e5ac11f.json", "user said: down beat at 2nd"),
    ("Snarky Puppy - Thorn", "V:/data/chords/42/42121bd7d777.json", "user said: down beat at 2nd, 4th"),
]


def grid_from_phase(beats, phase, beats_per_bar):
    """Generate downbeats by selecting every Nth beat starting at phase."""
    return [beats[i] for i in range(phase, len(beats), beats_per_bar)]


def main():
    for label, fp, note in SONGS:
        d = json.loads(Path(fp).read_text(encoding="utf-8"))
        chords = d.get("chords") or []
        beats = d.get("beats") or []
        chord_changes = [c["time"] for c in chords[1:] if c.get("time") is not None]

        print(f"\n=== {label} ===")
        print(f"  {note}")
        print(f"  bpm={d.get('bpm', 0):.1f}, n_beats={len(beats)}, n_chord_changes={len(chord_changes)}")

        # Try phases 0..3 with bpb=4
        print(f"  bpb=4 phase search:")
        best_phase = 0
        best_align = -1
        for ph in range(4):
            grid = grid_from_phase(beats, ph, 4)
            align = _alignment(chord_changes, grid)
            mark = "  "
            if align > best_align:
                best_align = align
                best_phase = ph
            print(f"    phase={ph} (downbeat on beat[{ph},{ph+4},{ph+8}...]): align={align:.4f}")
        print(f"  best phase: {best_phase} (align {best_align:.4f})")

        # Also try bpb=3 (waltz check)
        print(f"  bpb=3 phase search:")
        for ph in range(3):
            grid = grid_from_phase(beats, ph, 3)
            align = _alignment(chord_changes, grid)
            print(f"    phase={ph}: align={align:.4f}")


if __name__ == "__main__":
    main()
