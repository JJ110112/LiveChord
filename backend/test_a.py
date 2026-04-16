import sys, json, os

import file_catalog
from chord_db import get_chords_by_hash
from ai.section_detect import detect_sections

path_clean = "y:/christopher cross - arthur's theme (best that you can do) (official music video) [remastered hd].flac".replace('\\\\', '/')
song_hash = None

lib = file_catalog.scan_library()
for e in lib.get('entries', []):
    if e['path'].lower().replace('\\\\', '/') == path_clean.lower():
        song_hash = e.get('hash')
        break

if song_hash:
    res = get_chords_by_hash(song_hash)
    if res:
        secs = detect_sections(res['chords'], res['key'], song_hash=song_hash)
        for s in secs['sections']:
            print(f"{s['label']} ({s['type']}): {s['start']}s - {s['end']}s")
    else:
        print('No chords found')
else:
    print('No hash')
