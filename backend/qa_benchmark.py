"""QA Battle 3-Song Benchmark"""
import sys
sys.stdout.reconfigure(encoding="utf-8")

import json, logging, time, os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
logging.disable(logging.WARNING)

songs = [
    ("da794604e574", "Pop: Dancing Queen"),
    ("d1266e46e854", "Jazz: Autumn Leaves"),
    ("fb9bc8e2b4e7", "Rock: Bohemian Rhapsody"),
]

print("=" * 70)
print("  LiveChord QA Battle — 3 Song Benchmark")
print("=" * 70)

for h, label in songs:
    chord_path = f"V:/data/chords/{h}.json"
    melody_path = f"V:/data/melodies/{h}.json"
    if not os.path.isfile(chord_path) or not os.path.isfile(melody_path):
        print(f"  {label:30s} SKIP (missing data)")
        continue

    t0 = time.time()
    chord_data = json.loads(open(chord_path, encoding="utf-8").read())
    chords = chord_data.get("chords", [])
    mel_data = json.loads(open(melody_path, encoding="utf-8").read())
    melody = mel_data.get("melody", [])

    from ai.accompaniment_generator import generate_accompaniment
    acc = generate_accompaniment(chords, melody, bpm=120, style="Arpeggio", level="L2")

    from ai.pedal_advisor import generate_pedal_suggestions
    acc["pedal"] = generate_pedal_suggestions(chords, melody=melody, bpm=120)

    from ai.dynamics_engine import generate_dynamics
    all_ev = list(acc["left_hand"]) + list(acc["right_hand"])
    generate_dynamics(all_ev, bpm=120, section_type="chorus")

    from ai.battle_qa import run_full_qa
    result = run_full_qa(chords, melody, acc, bpm=120, level="L2")
    elapsed = time.time() - t0

    v = result["verdict"].upper()
    s = result["overall_score"]
    sc = result["scores"]
    print(f"\n  {label}")
    print(f"    Verdict: {v}  Score: {s}/100  ({elapsed:.2f}s)")
    print(f"    melody={sc['melody']['score']}  accomp={sc['accompaniment']['score']}  "
          f"fingering={sc['fingering']['score']}  pedal={sc['pedal']['score']}  "
          f"dynamics={sc['dynamics']['score']}  playability={sc['playability']['score']}")
    for sug in result["suggestions"][:2]:
        print(f"    -> {sug}")

print("\n" + "=" * 70)
print("  Benchmark completed.")
print("=" * 70)
