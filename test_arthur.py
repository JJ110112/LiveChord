import json
import glob
from pathlib import Path
import sys
sys.path.insert(0, str(Path("backend/ai").resolve()))
from section_detect import detect_sections

# Need to find Arthur's theme Hash since I don't know it exactly. I will find it.
def find_arthur():
    for file in glob.glob("W:/data/chords/*.json"):
        try:
            with open(file, 'r', encoding='utf-8') as f:
                d = json.load(f)
                if "Arthur" in d.get("title", ""):
                    return file
        except Exception:
            pass
    return None

file = find_arthur()
if file:
    print(f"Found {file}")
    with open(file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    res = detect_sections(data["chords"], key=data["key"], song_hash=Path(file).stem, mode="dl")
    print("----- SECTIONS -----")
    for s in res["sections"]:
        print(f"{s['start']}s -> {s['end']}s : {s['label']} ({s['type']})")
else:
    print("Not found")
