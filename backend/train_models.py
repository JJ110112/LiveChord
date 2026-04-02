"""
LiveChord AI 模型離線訓練腳本
用 43K 和弦資料建立並儲存所有矩陣至 data/models/

用法: python train_models.py [--chords-dir PATH]
"""

import os
import sys
import time
import argparse
from pathlib import Path

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

DATA_DIR = Path(__file__).parent.parent / "data"
CHORDS_DIR = DATA_DIR / "chords"
MODELS_DIR = DATA_DIR / "models"


def train_all(chords_dir: str):
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    total_start = time.time()

    # ---- 1. Markov (Bigram + Trigram) ----
    print("=" * 60)
    print("🎯 [1/4] 訓練 Markov Chain (Bigram + Trigram)...")
    t0 = time.time()

    from ai.markov import ChordPredictor
    predictor = ChordPredictor()
    stats = predictor.train(chords_dir)

    markov_path = MODELS_DIR / "markov.json"
    predictor.save(str(markov_path))

    print(f"  歌曲: {stats['total_songs']}")
    print(f"  和弦變化: {stats['total_chord_changes']}")
    print(f"  Bigram 狀態: {predictor.get_stats()['bigram_states']}")
    print(f"  Trigram 狀態: {predictor.get_stats()['trigram_states']}")
    print(f"  儲存: {markov_path} ({markov_path.stat().st_size / 1024:.0f} KB)")
    print(f"  耗時: {time.time() - t0:.1f}s")

    # ---- 2. Emission Matrix ----
    print("=" * 60)
    print("🎯 [2/4] 建立 HMM 發射矩陣...")
    t0 = time.time()

    from ai.hmm import build_emission_from_songs
    emission = build_emission_from_songs(chords_dir)

    emission_path = MODELS_DIR / "emission.json"
    emission.save(str(emission_path))

    em_stats = emission.get_stats()
    print(f"  和弦狀態: {em_stats['chord_states']}")
    print(f"  觀測總數: {em_stats['total_observations']}")
    print(f"  儲存: {emission_path} ({emission_path.stat().st_size / 1024:.0f} KB)")
    print(f"  耗時: {time.time() - t0:.1f}s")

    # ---- 3. Transition Probs (for Viterbi) ----
    print("=" * 60)
    print("🎯 [3/4] 建立轉移機率矩陣...")
    t0 = time.time()

    import json
    trans = {}
    for state, counter in predictor.bigram.items():
        total = sum(counter.values())
        trans[state] = {s: round(c / total, 6) for s, c in counter.items()}
    states = list(predictor.bigram.keys())

    trans_path = MODELS_DIR / "transition.json"
    trans_data = {"states": states, "transitions": trans}
    trans_path.write_text(json.dumps(trans_data, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"  狀態數: {len(states)}")
    print(f"  轉移對: {sum(len(v) for v in trans.values())}")
    print(f"  儲存: {trans_path} ({trans_path.stat().st_size / 1024:.0f} KB)")
    print(f"  耗時: {time.time() - t0:.1f}s")

    # ---- 4. Groove Dictionary ----
    print("=" * 60)
    print("🎯 [4/4] 建立 Groove Dictionary...")
    t0 = time.time()

    from ai.groove_dict import GrooveDictionary
    gd = GrooveDictionary()
    gd.build(chords_dir)

    groove_path = MODELS_DIR / "groove.json"
    gd.save(str(groove_path))

    gd_stats = gd.get_stats()
    print(f"  歌曲: {gd_stats['total_songs']}")
    print(f"  4-chord patterns: {gd_stats['unique_4_patterns']}")
    print(f"  8-chord patterns: {gd_stats['unique_8_patterns']}")
    print(f"  儲存: {groove_path} ({groove_path.stat().st_size / 1024 / 1024:.1f} MB)")
    print(f"  耗時: {time.time() - t0:.1f}s")

    # ---- 完成 ----
    total_time = time.time() - total_start
    print("=" * 60)
    print(f"✅ 全部完成！總耗時: {total_time:.1f}s")
    print(f"📂 模型目錄: {MODELS_DIR}")
    for f in sorted(MODELS_DIR.glob("*.json")):
        size = f.stat().st_size
        unit = "KB" if size < 1024 * 1024 else "MB"
        val = size / 1024 if unit == "KB" else size / 1024 / 1024
        print(f"   {f.name:25s} {val:.1f} {unit}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LiveChord AI 模型離線訓練")
    parser.add_argument("--chords-dir", type=str, default=str(CHORDS_DIR))
    args = parser.parse_args()
    train_all(args.chords_dir)
