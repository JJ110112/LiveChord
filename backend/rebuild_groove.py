"""One-shot: rebuild only groove.json with style stratification from existing style_map.json."""

import os
import sys
import json
import time
from pathlib import Path

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

DATA_DIR = Path(__file__).parent.parent / "data"
CHORDS_DIR = DATA_DIR / "chords"
MODELS_DIR = DATA_DIR / "models"

style_map_path = DATA_DIR / "style_map.json"
style_map = None
if style_map_path.is_file():
    sm_data = json.loads(style_map_path.read_text(encoding="utf-8"))
    style_map = sm_data.get("tracks", {})
    print(f"Loaded style_map: {len(style_map)} tracks, {len(sm_data.get('styles', {}))} styles")

from ai.groove_dict import GrooveDictionary

t0 = time.time()
gd = GrooveDictionary()
gd.build(str(CHORDS_DIR), style_map=style_map)

groove_path = MODELS_DIR / "groove.json"
gd.save(str(groove_path), min_count=2, style_min_count=3)

stats = gd.get_stats()
print(f"\n歌曲: {stats['total_songs']}")
print(f"4-chord patterns (global): {stats['unique_4_patterns']}")
print(f"8-chord patterns (global): {stats['unique_8_patterns']}")
print(f"Styles: {stats.get('style_count', 0)}")
if stats.get("by_style"):
    for s, sb in stats["by_style"].items():
        print(f"  {s:30s} songs={sb['total_songs']:6d}  4p={sb['unique_4_patterns']:7d}  8p={sb['unique_8_patterns']:7d}")

size_mb = groove_path.stat().st_size / 1024 / 1024
print(f"\nSaved: {groove_path} ({size_mb:.1f} MB)")
print(f"Elapsed: {time.time() - t0:.1f}s")
