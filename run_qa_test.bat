@echo off
title LiveChord QA Battle Test

echo ==================================================
echo   LiveChord QA Battle - 3 Song Benchmark
echo   Pop: Dancing Queen
echo   Jazz: Autumn Leaves
echo   Rock: Bohemian Rhapsody
echo ==================================================
echo.

cd /d W:\backend
python -u -c "^
import sys; sys.stdout.reconfigure(encoding='utf-8')^
import json, logging, time^
logging.disable(logging.WARNING)^
^
songs = [^
    ('da794604e574', 'Pop: Dancing Queen'),^
    ('d1266e46e854', 'Jazz: Autumn Leaves'),^
    ('fb9bc8e2b4e7', 'Rock: Bohemian Rhapsody'),^
]^
^
for h, label in songs:^
    t0 = time.time()^
    chord_data = json.loads(open(f'W:/data/chords/{h}.json', encoding='utf-8').read())^
    chords = chord_data.get('chords', [])^
    mel_data = json.loads(open(f'W:/data/melodies/{h}.json', encoding='utf-8').read())^
    melody = mel_data.get('melody', [])^
    ^
    from ai.accompaniment_generator import generate_accompaniment^
    acc = generate_accompaniment(chords, melody, bpm=120, style='Arpeggio', level='L2')^
    ^
    from ai.pedal_advisor import generate_pedal_suggestions^
    acc['pedal'] = generate_pedal_suggestions(chords, melody=melody, bpm=120)^
    ^
    from ai.dynamics_engine import generate_dynamics^
    all_ev = list(acc['left_hand']) + list(acc['right_hand'])^
    generate_dynamics(all_ev, bpm=120, section_type='chorus')^
    ^
    from ai.battle_qa import run_full_qa^
    result = run_full_qa(chords, melody, acc, bpm=120, level='L2')^
    elapsed = time.time() - t0^
    ^
    v = result['verdict'].upper()^
    s = result['overall_score']^
    sc = result['scores']^
    print(f'{label:30s} {v:4s} {s:3d}/100  mel={sc[\"melody\"][\"score\"]} acc={sc[\"accompaniment\"][\"score\"]} fing={sc[\"fingering\"][\"score\"]} ped={sc[\"pedal\"][\"score\"]} dyn={sc[\"dynamics\"][\"score\"]}  ({elapsed:.2f}s)')^
    for sug in result['suggestions'][:2]:^
        print(f'  -> {sug}')^
"

echo.
echo ==================================================
echo   QA Battle completed.
echo ==================================================
pause
