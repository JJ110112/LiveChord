"""
gen_fingers.py - Auto-generate 'fingers' for chord_diagrams.py entries
and append GUITAR_VOICINGS dictionary.

Rules:
  1. fret=-1 (muted) or fret=0 (open) -> finger=0
  2. Barre chords: barre fret -> finger 1 on all strings at that fret,
     remaining pressed strings get 2,3,4 ascending by fret then string index
  3. Non-barre: pressed strings sorted by (fret, string_index),
     assign fingers 1,2,3,4 in order
"""

import re
import sys
import os

INPUT_FILE = r"V:\backend\chord_diagrams.py"
OUTPUT_FILE = r"V:\backend\chord_diagrams.py"


def compute_fingers(strings, barres):
    """Compute finger assignments for a chord."""
    fingers = [0] * len(strings)

    # Collect pressed strings: (string_index, fret)
    pressed = []
    for i, fret in enumerate(strings):
        if fret > 0:
            pressed.append((i, fret))

    if not pressed:
        return fingers

    if barres:
        barre_fret = min(barres)
        # Assign finger 1 to all strings at the barre fret
        for i, fret in pressed:
            if fret == barre_fret:
                fingers[i] = 1

        # Remaining pressed strings (not at barre fret) get 2,3,4
        remaining = [(i, fret) for i, fret in pressed if fret != barre_fret]
        remaining.sort(key=lambda x: (x[1], x[0]))
        next_finger = 2
        for i, fret in remaining:
            if next_finger <= 4:
                fingers[i] = next_finger
                next_finger += 1
            else:
                fingers[i] = 4  # cap at pinky
    else:
        # Non-barre: sort by fret ascending, then string index ascending
        pressed.sort(key=lambda x: (x[1], x[0]))
        next_finger = 1
        for i, fret in pressed:
            if next_finger <= 4:
                fingers[i] = next_finger
                next_finger += 1
            else:
                fingers[i] = 4

    return fingers


def process_file():
    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        content = f.read()

    # Pattern to match a chord entry line like:
    #   'C':       {'strings': [-1, 3, 2, 0, 1, 0], 'baseFret': 1, 'barres': []},
    # We need to add 'fingers': [...] if not already present
    chord_pattern = re.compile(
        r"(\{)"                                    # opening brace
        r"('strings':\s*\[([^\]]*)\])"             # strings key+value
        r",\s*('baseFret':\s*\d+)"                 # baseFret
        r",\s*('barres':\s*\[([^\]]*)\])"          # barres key+value
        r"(\})"                                    # closing brace
    )

    def replacer(m):
        full = m.group(0)
        # Skip if already has fingers
        if 'fingers' in full:
            return full

        strings_str = m.group(3)
        barres_str = m.group(6)

        strings = [int(x.strip()) for x in strings_str.split(',')]
        barres = [int(x.strip()) for x in barres_str.split(',') if x.strip()]

        fingers = compute_fingers(strings, barres)

        # Rebuild: {strings, baseFret, barres, fingers}
        result = (
            m.group(1) +
            m.group(2) +
            ", " + m.group(4) +
            ", " + m.group(5) +
            ", 'fingers': " + str(fingers) +
            m.group(7)
        )
        return result

    new_content = chord_pattern.sub(replacer, content)

    # Count how many entries were modified
    original_count = len(chord_pattern.findall(content))
    print(f"Found {original_count} chord entries total")

    # Now append GUITAR_VOICINGS before the functions at the end
    voicings = generate_guitar_voicings()

    # Find where to insert: before the first function def
    insert_marker = "\ndef get_chord_diagram("
    if insert_marker in new_content:
        parts = new_content.split(insert_marker, 1)
        new_content = parts[0] + voicings + "\n" + insert_marker + parts[1]
    else:
        # fallback: append before last function
        new_content += "\n" + voicings

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write(new_content)

    print(f"Written to {OUTPUT_FILE}")


def generate_guitar_voicings():
    """Generate GUITAR_VOICINGS dict with 2-4 voicing variants for 30 common chords."""
    voicings = {}

    # C variants
    voicings['C'] = [
        {'label': 'Open', 'strings': [-1, 3, 2, 0, 1, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 3, 2, 0, 1, 0]},
        {'label': 'Barre 3rd', 'strings': [-1, 3, 5, 5, 5, 3], 'baseFret': 1, 'barres': [3], 'fingers': [0, 1, 3, 3, 3, 1]},
        {'label': 'Barre 8th', 'strings': [1, 1, 3, 3, 3, 1], 'baseFret': 8, 'barres': [1], 'fingers': [1, 1, 2, 3, 4, 1]},
    ]
    voicings['Cm'] = [
        {'label': 'Barre 3rd', 'strings': [-1, 3, 5, 5, 4, 3], 'baseFret': 1, 'barres': [3], 'fingers': [0, 1, 3, 4, 2, 1]},
        {'label': 'Barre 8th', 'strings': [1, 1, 3, 3, 1, 1], 'baseFret': 8, 'barres': [1], 'fingers': [1, 1, 3, 4, 1, 1]},
        {'label': 'High', 'strings': [-1, -1, 1, 3, 2, 1], 'baseFret': 8, 'barres': [1], 'fingers': [0, 0, 1, 4, 3, 1]},
    ]
    voicings['C7'] = [
        {'label': 'Open', 'strings': [-1, 3, 2, 3, 1, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 3, 2, 4, 1, 0]},
        {'label': 'Barre 3rd', 'strings': [-1, 3, 5, 3, 5, 3], 'baseFret': 1, 'barres': [3], 'fingers': [0, 1, 3, 1, 4, 1]},
        {'label': 'Barre 8th', 'strings': [1, 1, 3, 1, 3, 1], 'baseFret': 8, 'barres': [1], 'fingers': [1, 1, 3, 1, 4, 1]},
    ]
    voicings['Cmaj7'] = [
        {'label': 'Open', 'strings': [-1, 3, 2, 0, 0, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 3, 2, 0, 0, 0]},
        {'label': 'Barre 3rd', 'strings': [-1, 3, 5, 4, 5, 3], 'baseFret': 1, 'barres': [3], 'fingers': [0, 1, 3, 2, 4, 1]},
    ]

    # D variants
    voicings['D'] = [
        {'label': 'Open', 'strings': [-1, -1, 0, 2, 3, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 0, 1, 3, 2]},
        {'label': 'Barre 5th', 'strings': [-1, 5, 7, 7, 7, 5], 'baseFret': 1, 'barres': [5], 'fingers': [0, 1, 3, 3, 3, 1]},
        {'label': 'Barre 10th', 'strings': [1, 1, 3, 3, 3, 1], 'baseFret': 10, 'barres': [1], 'fingers': [1, 1, 2, 3, 4, 1]},
    ]
    voicings['Dm'] = [
        {'label': 'Open', 'strings': [-1, -1, 0, 2, 3, 1], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 0, 2, 3, 1]},
        {'label': 'Barre 5th', 'strings': [-1, 5, 7, 7, 6, 5], 'baseFret': 1, 'barres': [5], 'fingers': [0, 1, 3, 4, 2, 1]},
        {'label': 'Barre 10th', 'strings': [1, 1, 3, 3, 1, 1], 'baseFret': 10, 'barres': [1], 'fingers': [1, 1, 3, 4, 1, 1]},
    ]
    voicings['D7'] = [
        {'label': 'Open', 'strings': [-1, -1, 0, 2, 1, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 0, 2, 1, 3]},
        {'label': 'Barre 5th', 'strings': [-1, 5, 7, 5, 7, 5], 'baseFret': 1, 'barres': [5], 'fingers': [0, 1, 3, 1, 4, 1]},
    ]
    voicings['Dmaj7'] = [
        {'label': 'Open', 'strings': [-1, -1, 0, 2, 2, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 0, 1, 2, 3]},
        {'label': 'Barre 5th', 'strings': [-1, 5, 7, 6, 7, 5], 'baseFret': 1, 'barres': [5], 'fingers': [0, 1, 3, 2, 4, 1]},
    ]

    # E variants
    voicings['E'] = [
        {'label': 'Open', 'strings': [0, 2, 2, 1, 0, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 2, 3, 1, 0, 0]},
        {'label': 'Barre 7th', 'strings': [-1, 7, 9, 9, 9, 7], 'baseFret': 1, 'barres': [7], 'fingers': [0, 1, 3, 3, 3, 1]},
    ]
    voicings['Em'] = [
        {'label': 'Open', 'strings': [0, 2, 2, 0, 0, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 2, 3, 0, 0, 0]},
        {'label': 'Barre 7th', 'strings': [-1, 7, 9, 9, 8, 7], 'baseFret': 1, 'barres': [7], 'fingers': [0, 1, 3, 4, 2, 1]},
        {'label': 'High', 'strings': [-1, -1, 2, 0, 0, 0], 'baseFret': 7, 'barres': [], 'fingers': [0, 0, 2, 0, 0, 0]},
    ]
    voicings['E7'] = [
        {'label': 'Open', 'strings': [0, 2, 0, 1, 0, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 2, 0, 1, 0, 0]},
        {'label': 'Barre 7th', 'strings': [-1, 7, 9, 7, 9, 7], 'baseFret': 1, 'barres': [7], 'fingers': [0, 1, 3, 1, 4, 1]},
    ]
    voicings['Emaj7'] = [
        {'label': 'Open', 'strings': [0, 2, 1, 1, 0, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 3, 1, 2, 0, 0]},
        {'label': 'Barre 7th', 'strings': [-1, 7, 9, 8, 9, 7], 'baseFret': 1, 'barres': [7], 'fingers': [0, 1, 3, 2, 4, 1]},
    ]

    # F variants
    voicings['F'] = [
        {'label': 'Barre', 'strings': [1, 1, 2, 3, 3, 1], 'baseFret': 1, 'barres': [1], 'fingers': [1, 1, 2, 3, 4, 1]},
        {'label': 'Easy', 'strings': [-1, -1, 3, 2, 1, 1], 'baseFret': 1, 'barres': [1], 'fingers': [0, 0, 3, 2, 1, 1]},
        {'label': 'Barre 8th', 'strings': [-1, 8, 10, 10, 10, 8], 'baseFret': 1, 'barres': [8], 'fingers': [0, 1, 3, 3, 3, 1]},
    ]
    voicings['Fm'] = [
        {'label': 'Barre', 'strings': [1, 1, 3, 3, 1, 1], 'baseFret': 1, 'barres': [1], 'fingers': [1, 1, 3, 4, 1, 1]},
        {'label': 'Barre 8th', 'strings': [-1, 8, 10, 10, 9, 8], 'baseFret': 1, 'barres': [8], 'fingers': [0, 1, 3, 4, 2, 1]},
    ]
    voicings['F7'] = [
        {'label': 'Barre', 'strings': [1, 1, 2, 1, 3, 1], 'baseFret': 1, 'barres': [1], 'fingers': [1, 1, 2, 1, 3, 1]},
        {'label': 'Easy', 'strings': [-1, -1, 3, 2, 1, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 4, 3, 2, 0]},
    ]
    voicings['Fmaj7'] = [
        {'label': 'Open', 'strings': [-1, -1, 3, 2, 1, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 3, 2, 1, 0]},
        {'label': 'Barre', 'strings': [1, 1, 2, 2, 3, 1], 'baseFret': 1, 'barres': [1], 'fingers': [1, 1, 2, 3, 4, 1]},
    ]

    # G variants
    voicings['G'] = [
        {'label': 'Open', 'strings': [3, 2, 0, 0, 0, 3], 'baseFret': 1, 'barres': [], 'fingers': [2, 1, 0, 0, 0, 3]},
        {'label': 'Barre 3rd', 'strings': [3, 3, 5, 5, 5, 3], 'baseFret': 1, 'barres': [3], 'fingers': [1, 1, 2, 3, 4, 1]},
        {'label': 'High', 'strings': [-1, -1, 5, 4, 3, 3], 'baseFret': 1, 'barres': [3], 'fingers': [0, 0, 4, 3, 1, 1]},
    ]
    voicings['Gm'] = [
        {'label': 'Barre 3rd', 'strings': [3, 5, 5, 3, 3, 3], 'baseFret': 1, 'barres': [3], 'fingers': [1, 3, 4, 1, 1, 1]},
        {'label': 'Barre 10th', 'strings': [-1, 10, 12, 12, 11, 10], 'baseFret': 1, 'barres': [10], 'fingers': [0, 1, 3, 4, 2, 1]},
        {'label': 'Easy', 'strings': [-1, -1, 5, 3, 3, 3], 'baseFret': 1, 'barres': [3], 'fingers': [0, 0, 4, 1, 1, 1]},
    ]
    voicings['G7'] = [
        {'label': 'Open', 'strings': [3, 2, 0, 0, 0, 1], 'baseFret': 1, 'barres': [], 'fingers': [3, 2, 0, 0, 0, 1]},
        {'label': 'Barre 3rd', 'strings': [3, 3, 5, 3, 5, 3], 'baseFret': 1, 'barres': [3], 'fingers': [1, 1, 3, 1, 4, 1]},
    ]
    voicings['Gmaj7'] = [
        {'label': 'Open', 'strings': [3, 2, 0, 0, 0, 2], 'baseFret': 1, 'barres': [], 'fingers': [3, 2, 0, 0, 0, 1]},
        {'label': 'Barre 3rd', 'strings': [3, 3, 5, 4, 5, 3], 'baseFret': 1, 'barres': [3], 'fingers': [1, 1, 3, 2, 4, 1]},
    ]

    # A variants
    voicings['A'] = [
        {'label': 'Open', 'strings': [-1, 0, 2, 2, 2, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 1, 2, 3, 0]},
        {'label': 'Barre 5th', 'strings': [5, 5, 7, 7, 7, 5], 'baseFret': 1, 'barres': [5], 'fingers': [1, 1, 2, 3, 4, 1]},
        {'label': 'High', 'strings': [-1, -1, 7, 6, 5, 5], 'baseFret': 1, 'barres': [5], 'fingers': [0, 0, 4, 3, 1, 1]},
    ]
    voicings['Am'] = [
        {'label': 'Open', 'strings': [-1, 0, 2, 2, 1, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 2, 3, 1, 0]},
        {'label': 'Barre 5th', 'strings': [5, 5, 7, 7, 5, 5], 'baseFret': 1, 'barres': [5], 'fingers': [1, 1, 3, 4, 1, 1]},
        {'label': 'High', 'strings': [-1, -1, 7, 5, 5, 5], 'baseFret': 1, 'barres': [5], 'fingers': [0, 0, 4, 1, 1, 1]},
    ]
    voicings['A7'] = [
        {'label': 'Open', 'strings': [-1, 0, 2, 0, 2, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 1, 0, 2, 0]},
        {'label': 'Barre 5th', 'strings': [5, 5, 7, 5, 7, 5], 'baseFret': 1, 'barres': [5], 'fingers': [1, 1, 3, 1, 4, 1]},
    ]
    voicings['Amaj7'] = [
        {'label': 'Open', 'strings': [-1, 0, 2, 1, 2, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 2, 1, 3, 0]},
        {'label': 'Barre 5th', 'strings': [5, 5, 7, 6, 7, 5], 'baseFret': 1, 'barres': [5], 'fingers': [1, 1, 3, 2, 4, 1]},
    ]

    # B variants
    voicings['B'] = [
        {'label': 'Barre 2nd', 'strings': [-1, 2, 4, 4, 4, 2], 'baseFret': 1, 'barres': [2], 'fingers': [0, 1, 2, 3, 4, 1]},
        {'label': 'Barre 7th', 'strings': [7, 7, 9, 9, 9, 7], 'baseFret': 1, 'barres': [7], 'fingers': [1, 1, 2, 3, 4, 1]},
        {'label': 'Easy', 'strings': [-1, 2, 4, 4, 4, -1], 'baseFret': 1, 'barres': [], 'fingers': [0, 1, 2, 3, 4, 0]},
    ]
    voicings['Bm'] = [
        {'label': 'Barre 2nd', 'strings': [-1, 2, 4, 4, 3, 2], 'baseFret': 1, 'barres': [2], 'fingers': [0, 1, 3, 4, 2, 1]},
        {'label': 'Barre 7th', 'strings': [7, 7, 9, 9, 7, 7], 'baseFret': 1, 'barres': [7], 'fingers': [1, 1, 3, 4, 1, 1]},
        {'label': 'Easy', 'strings': [-1, -1, 4, 4, 3, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 3, 4, 2, 1]},
    ]
    voicings['B7'] = [
        {'label': 'Open', 'strings': [-1, 2, 1, 2, 0, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 2, 1, 3, 0, 4]},
        {'label': 'Barre 7th', 'strings': [7, 7, 9, 7, 9, 7], 'baseFret': 1, 'barres': [7], 'fingers': [1, 1, 3, 1, 4, 1]},
    ]
    voicings['Bmaj7'] = [
        {'label': 'Barre 2nd', 'strings': [-1, 2, 4, 3, 4, 2], 'baseFret': 1, 'barres': [2], 'fingers': [0, 1, 3, 2, 4, 1]},
        {'label': 'Barre 7th', 'strings': [7, 7, 9, 8, 9, 7], 'baseFret': 1, 'barres': [7], 'fingers': [1, 1, 3, 2, 4, 1]},
    ]

    # Bb variants
    voicings['Bb'] = [
        {'label': 'Barre 1st', 'strings': [-1, 1, 3, 3, 3, 1], 'baseFret': 1, 'barres': [1], 'fingers': [0, 1, 2, 3, 4, 1]},
        {'label': 'Barre 6th', 'strings': [6, 6, 8, 8, 8, 6], 'baseFret': 1, 'barres': [6], 'fingers': [1, 1, 2, 3, 4, 1]},
        {'label': 'Easy', 'strings': [-1, 1, 3, 3, 3, -1], 'baseFret': 1, 'barres': [], 'fingers': [0, 1, 2, 3, 4, 0]},
    ]
    voicings['Bbm'] = [
        {'label': 'Barre 1st', 'strings': [-1, 1, 3, 3, 2, 1], 'baseFret': 1, 'barres': [1], 'fingers': [0, 1, 3, 4, 2, 1]},
        {'label': 'Barre 6th', 'strings': [6, 6, 8, 8, 6, 6], 'baseFret': 1, 'barres': [6], 'fingers': [1, 1, 3, 4, 1, 1]},
    ]

    # Build the output string
    lines = [
        "",
        "# 吉他和弦替代把位 (每個和弦 2~4 種按法)",
        "GUITAR_VOICINGS = {",
    ]

    chord_order = [
        'C', 'Cm', 'C7', 'Cmaj7',
        'D', 'Dm', 'D7', 'Dmaj7',
        'E', 'Em', 'E7', 'Emaj7',
        'F', 'Fm', 'F7', 'Fmaj7',
        'G', 'Gm', 'G7', 'Gmaj7',
        'A', 'Am', 'A7', 'Amaj7',
        'B', 'Bm', 'B7', 'Bmaj7',
        'Bb', 'Bbm',
    ]

    for chord_name in chord_order:
        variants = voicings[chord_name]
        lines.append(f"    '{chord_name}': [")
        for v in variants:
            lines.append(
                f"        {{'label': '{v['label']}', "
                f"'strings': {v['strings']}, "
                f"'baseFret': {v['baseFret']}, "
                f"'barres': {v['barres']}, "
                f"'fingers': {v['fingers']}}},")
        lines.append("    ],")

    lines.append("}")
    lines.append("")

    return "\n".join(lines)


if __name__ == '__main__':
    process_file()
    print("Done!")
