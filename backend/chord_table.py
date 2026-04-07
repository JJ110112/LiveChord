"""
和弦組成音對照表
將和弦名稱解析為組成音，並轉換為簡譜符號
"""

# 12個半音，用於計算音程
NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']

# 7個自然音的字母與半音數
LETTERS = ['C', 'D', 'E', 'F', 'G', 'A', 'B']
LETTER_SEMITONES = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}

# 等音對照 (flat → sharp)
FLAT_TO_SHARP = {
    'Cb': 'B', 'Db': 'C#', 'Eb': 'D#', 'Fb': 'E',
    'Gb': 'F#', 'Ab': 'G#', 'Bb': 'A#',
}

# sharp → flat (用於顯示)
SHARP_TO_FLAT = {
    'C#': 'Db', 'D#': 'Eb', 'F#': 'Gb', 'G#': 'Ab', 'A#': 'Bb',
}

# 音名 → 簡譜符號 (以C為1)
NOTE_TO_JIANPU = {
    'C': '1', 'C#': '#1', 'Db': 'b2',
    'D': '2', 'D#': '#2', 'Eb': 'b3',
    'E': '3', 'Fb': '4', 'E#': '4',
    'F': '4', 'F#': '#4', 'Gb': 'b5',
    'G': '5', 'G#': '#5', 'Ab': 'b6',
    'A': '6', 'A#': '#6', 'Bb': 'b7',
    'B': '7', 'Cb': '7', 'B#': '1',
    # 重降音 (double flat) — dim7 會用到
    # Xbb = X 降兩個半音，等於前一個自然音
    'Cbb': 'b7', 'Dbb': '1', 'Ebb': '2', 'Fbb': 'b3',
    'Gbb': '4', 'Abb': '5', 'Bbb': '6',
    # 重升音 (double sharp)
    'C##': '2', 'D##': '3', 'E##': '#4',
    'F##': '5', 'G##': '6', 'A##': '7', 'B##': '#1',
}

# 和弦類型 → 音程列表 (音級偏移, 半音數)
# 音級偏移: 0=根音, 2=三度, 4=五度, 6=七度, 1(+7)=九度, 3(+7)=十一度, 5(+7)=十三度
CHORD_INTERVALS = {
    '':        [(0, 0), (2, 4), (4, 7)],                                         # Major
    'm':       [(0, 0), (2, 3), (4, 7)],                                         # Minor
    'min':     [(0, 0), (2, 3), (4, 7)],                                         # Minor
    'dim':     [(0, 0), (2, 3), (4, 6)],                                         # Diminished
    'aug':     [(0, 0), (2, 4), (4, 8)],                                         # Augmented
    'sus2':    [(0, 0), (1, 2), (4, 7)],                                         # Suspended 2nd
    'sus4':    [(0, 0), (3, 5), (4, 7)],                                         # Suspended 4th
    '7':       [(0, 0), (2, 4), (4, 7), (6, 10)],                               # Dominant 7th
    'maj7':    [(0, 0), (2, 4), (4, 7), (6, 11)],                               # Major 7th
    'm7':      [(0, 0), (2, 3), (4, 7), (6, 10)],                               # Minor 7th
    'min7':    [(0, 0), (2, 3), (4, 7), (6, 10)],                               # Minor 7th
    'dim7':    [(0, 0), (2, 3), (4, 6), (6, 9)],                                # Diminished 7th
    'm7b5':    [(0, 0), (2, 3), (4, 6), (6, 10)],                               # Half-diminished
    'aug7':    [(0, 0), (2, 4), (4, 8), (6, 10)],                               # Augmented 7th
    '6':       [(0, 0), (2, 4), (4, 7), (5, 9)],                                # Major 6th
    'm6':      [(0, 0), (2, 3), (4, 7), (5, 9)],                                # Minor 6th
    '6/9':     [(0, 0), (2, 4), (4, 7), (5, 9), (1, 14)],                       # Major 6/9
    'm6/9':    [(0, 0), (2, 3), (4, 7), (5, 9), (1, 14)],                       # Minor 6/9
    '9':       [(0, 0), (2, 4), (4, 7), (6, 10), (1, 14)],                      # Dominant 9th
    'maj9':    [(0, 0), (2, 4), (4, 7), (6, 11), (1, 14)],                      # Major 9th
    'm9':      [(0, 0), (2, 3), (4, 7), (6, 10), (1, 14)],                      # Minor 9th
    '11':      [(0, 0), (2, 4), (4, 7), (6, 10), (1, 14), (3, 17)],             # Dominant 11th
    'm11':     [(0, 0), (2, 3), (4, 7), (6, 10), (1, 14), (3, 17)],             # Minor 11th
    '13':      [(0, 0), (2, 4), (4, 7), (6, 10), (1, 14), (3, 17), (5, 21)],    # Dominant 13th
    'add9':    [(0, 0), (2, 4), (4, 7), (1, 14)],                               # Add 9th
    'sus':     [(0, 0), (3, 5), (4, 7)],                                         # Suspended (= sus4)
    '7sus4':   [(0, 0), (3, 5), (4, 7), (6, 10)],                               # 7th suspended 4th
    '7sus2':   [(0, 0), (1, 2), (4, 7), (6, 10)],                               # 7th suspended 2nd
    '9sus4':   [(0, 0), (3, 5), (4, 7), (6, 10), (1, 14)],                      # 9th suspended 4th
    'mb5':     [(0, 0), (2, 3), (4, 6)],                                         # Minor flat 5 (= dim)
    'madd9':   [(0, 0), (2, 3), (4, 7), (1, 14)],                               # Minor add 9th
    'mM7':     [(0, 0), (2, 3), (4, 7), (6, 11)],                               # Minor major 7th
    'mM9':     [(0, 0), (2, 3), (4, 7), (6, 11), (1, 14)],                      # Minor major 9th
    'mM11':    [(0, 0), (2, 3), (4, 7), (6, 11), (1, 14), (3, 17)],             # Minor major 11th
    'mM7b5':   [(0, 0), (2, 3), (4, 6), (6, 11)],                               # Minor major 7th flat 5
    'm7#5':    [(0, 0), (2, 3), (4, 8), (6, 10)],                               # Minor 7th sharp 5
    'm#5':     [(0, 0), (2, 3), (4, 8)],                                         # Minor sharp 5
    'm9b5':    [(0, 0), (2, 3), (4, 6), (6, 10), (1, 14)],                      # Minor 9th flat 5
    'm11b5':   [(0, 0), (2, 3), (4, 6), (6, 10), (1, 14), (3, 17)],             # Minor 11th flat 5
    'm7b5(11)':[(0, 0), (2, 3), (4, 6), (6, 10), (3, 17)],                      # m7b5 add 11
    'm7b5(b13)':[(0, 0), (2, 3), (4, 6), (6, 10), (5, 20)],                     # m7b5 flat 13
    '7b5':     [(0, 0), (2, 4), (4, 6), (6, 10)],                               # Dominant 7th flat 5
    '7#5':     [(0, 0), (2, 4), (4, 8), (6, 10)],                               # Dominant 7th sharp 5
    '7b9':     [(0, 0), (2, 4), (4, 7), (6, 10), (1, 13)],                      # Dominant 7th flat 9
    '7#9':     [(0, 0), (2, 4), (4, 7), (6, 10), (1, 15)],                      # Dominant 7th sharp 9
    '7#11':    [(0, 0), (2, 4), (4, 7), (6, 10), (3, 18)],                      # Dominant 7th sharp 11
    '7b13':    [(0, 0), (2, 4), (4, 7), (6, 10), (5, 20)],                      # Dominant 7th flat 13
    'add11':   [(0, 0), (2, 4), (4, 7), (3, 17)],                               # Add 11th
    'm(11)':   [(0, 0), (2, 3), (4, 7), (3, 17)],                               # Minor add 11
    'm6(9)':   [(0, 0), (2, 3), (4, 7), (5, 9), (1, 14)],                       # Minor 6th add 9
    'm6(9,11)':[(0, 0), (2, 3), (4, 7), (5, 9), (1, 14), (3, 17)],              # Minor 6(9,11)
    'm7(11)':  [(0, 0), (2, 3), (4, 7), (6, 10), (3, 17)],                      # Minor 7th add 11
    '6(9)':    [(0, 0), (2, 4), (4, 7), (5, 9), (1, 14)],                       # 6th add 9
    '6(11)':   [(0, 0), (2, 4), (4, 7), (5, 9), (3, 17)],                       # 6th add 11
}


_ENHARMONIC_SHARP = {'E#': 'F', 'B#': 'C', 'Fb': 'E', 'Cb': 'B'}

def root_to_semitone(root: str) -> int:
    """將根音轉換為半音數 (C=0)"""
    # Handle double sharps (e.g. C## -> D)
    if '##' in root:
        base = root.replace('##', '')
        return (NOTE_NAMES.index(base) + 2) % 12
    # Handle double flats (e.g. Dbb -> C)
    if 'bb' in root:
        base = root.replace('bb', '')
        return (NOTE_NAMES.index(base) - 2) % 12
    if root in FLAT_TO_SHARP:
        root = FLAT_TO_SHARP[root]
    elif root in _ENHARMONIC_SHARP:
        root = _ENHARMONIC_SHARP[root]
    return NOTE_NAMES.index(root)


def root_to_letter_index(root: str) -> int:
    """將根音轉換為字母索引 (C=0, D=1, ..., B=6)"""
    return LETTERS.index(root[0])


def semitone_to_note(semitone: int, prefer_flat: bool = True) -> str:
    """將半音數轉換為音名"""
    note = NOTE_NAMES[semitone % 12]
    if prefer_flat and note in SHARP_TO_FLAT:
        return SHARP_TO_FLAT[note]
    return note


def spell_note(root_letter_idx: int, degree_offset: int, target_semitone: int) -> str:
    """
    根據音程正確拼寫音名
    root_letter_idx: 根音字母索引 (C=0)
    degree_offset: 音級偏移 (0=根音, 2=三度, 4=五度, ...)
    target_semitone: 目標半音數
    """
    # 計算目標字母
    target_letter_idx = (root_letter_idx + degree_offset) % 7
    target_letter = LETTERS[target_letter_idx]
    natural_semitone = LETTER_SEMITONES[target_letter]

    # 計算需要的變化記號
    diff = (target_semitone % 12) - natural_semitone
    if diff > 6:
        diff -= 12
    elif diff < -6:
        diff += 12

    if diff == 0:
        return target_letter
    elif diff == 1:
        return target_letter + '#'
    elif diff == -1:
        return target_letter + 'b'
    elif diff == 2:
        return target_letter + '##'
    elif diff == -2:
        return target_letter + 'bb'
    else:
        # 無法正常拼寫，回退到簡單方式
        return semitone_to_note(target_semitone % 12)


def note_to_jianpu(note: str) -> str:
    """將音名轉換為簡譜符號"""
    if note in NOTE_TO_JIANPU:
        return NOTE_TO_JIANPU[note]
    # 嘗試等音轉換
    if note in FLAT_TO_SHARP:
        sharp = FLAT_TO_SHARP[note]
        if sharp in NOTE_TO_JIANPU:
            return NOTE_TO_JIANPU[sharp]
    if note in SHARP_TO_FLAT:
        flat = SHARP_TO_FLAT[note]
        if flat in NOTE_TO_JIANPU:
            return NOTE_TO_JIANPU[flat]
    return note


def parse_chord(chord_str: str) -> tuple:
    """
    解析和弦名稱，回傳 (根音, 和弦類型, 低音)
    例如: "Ebmaj7" → ("Eb", "maj7", None)
          "Bb/D"   → ("Bb", "", "D")
    """
    import re

    chord_str = chord_str.strip()

    # 根音大小寫正規化 (c → C, eb → Eb)
    if chord_str and chord_str[0].islower() and chord_str[0] in 'abcdefg':
        chord_str = chord_str[0].upper() + chord_str[1:]

    # 處理斜線和弦 (slash chord)
    # 注意: 6/9, m6/9 不是斜線和弦，是和弦類型
    bass = None
    if '/' in chord_str:
        slash_pos = chord_str.index('/')
        after_slash = chord_str[slash_pos + 1:]
        before_slash = chord_str[:slash_pos]
        # 6/9 和 m6/9 是和弦類型，不是斜線低音
        if after_slash == '9' and before_slash.rstrip('m').endswith('6'):
            pass  # 保持原樣，不拆分
        else:
            chord_str = before_slash
            bass = after_slash

    # 解析根音
    match = re.match(r'^([A-G][b#]?)(.*)', chord_str)
    if not match:
        return None, None, None

    root = match.group(1)
    quality = match.group(2)

    # 標準化和弦類型
    # 處理 "m7", "min7" 等
    if quality not in CHORD_INTERVALS:
        # 嘗試常見的變體
        quality_map = {
            'minor': 'm', 'major': 'maj', 'M7': 'maj7', 'M': '',
            'mi': 'm', 'ma': 'maj', 'Maj7': 'maj7',
        }
        if quality in quality_map:
            quality = quality_map[quality]

    return root, quality, bass


def get_chord_notes(chord_str: str) -> list:
    """
    取得和弦的組成音名列表（正確拼寫）
    例如: "Ebmaj7" → ["Eb", "G", "Bb", "D"]
          "Gm"     → ["G", "Bb", "D"]
    """
    root, quality, bass = parse_chord(chord_str)
    if root is None:
        return []

    if quality not in CHORD_INTERVALS:
        quality = ''

    intervals = CHORD_INTERVALS[quality]
    root_semitone = root_to_semitone(root)
    root_letter_idx = root_to_letter_index(root)

    notes = []
    for degree_offset, semitones in intervals:
        target_semitone = (root_semitone + semitones) % 12
        note = spell_note(root_letter_idx, degree_offset, target_semitone)
        notes.append(note)

    return notes


def get_chord_jianpu(chord_str: str) -> str:
    """
    取得和弦的簡譜組成音字串
    例如: "Ebmaj7" → "b3 5 b7 2"
    """
    notes = get_chord_notes(chord_str)
    if not notes:
        return ""
    jianpu = [note_to_jianpu(n) for n in notes]
    return ' '.join(jianpu)


QUALITY_NAMES = {
    '': 'Major', 'm': 'Minor', 'min': 'Minor',
    'dim': 'Diminished', 'aug': 'Augmented',
    'sus2': 'Suspended 2nd', 'sus4': 'Suspended 4th', 'sus': 'Suspended 4th',
    '7': 'Dominant 7th', 'maj7': 'Major 7th', 'm7': 'Minor 7th', 'min7': 'Minor 7th',
    'dim7': 'Diminished 7th', 'm7b5': 'Half-Diminished',
    'aug7': 'Augmented 7th', '7b5': 'Dominant 7th ♭5', '7#5': 'Dominant 7th ♯5',
    '6': 'Major 6th', 'm6': 'Minor 6th', '6/9': 'Major 6/9',
    '9': 'Dominant 9th', 'maj9': 'Major 9th', 'm9': 'Minor 9th',
    '11': 'Dominant 11th', 'm11': 'Minor 11th',
    '13': 'Dominant 13th',
    'add9': 'Add 9th', 'madd9': 'Minor Add 9th',
    'mM7': 'Minor Major 7th',
    '7sus4': '7th Suspended 4th', '7sus2': '7th Suspended 2nd',
    '7b9': 'Dominant 7th ♭9', '7#9': 'Dominant 7th ♯9',
    '7#11': 'Dominant 7th ♯11',
}

# 自然大調各級和弦的半音數 → 羅馬數字
_MAJOR_SCALE_SEMITONES = [0, 2, 4, 5, 7, 9, 11]
_ROMAN_NUMERALS = ['I', 'II', 'III', 'IV', 'V', 'VI', 'VII']


def analyze_chord_in_key(key: str, chord_str: str) -> dict:
    """
    分析和弦在調性中的級數 (Roman numeral analysis)
    Returns: {roman, degree, type_name, explanation}
    """
    root, quality, bass = parse_chord(chord_str)
    if not root:
        return {'roman': '?', 'degree': 0, 'type_name': '', 'explanation': ''}

    # Strip quality suffix from key (e.g. "Dm" -> "D", "Bbm" -> "Bb")
    key_root = key
    if len(key_root) >= 2 and key_root[-1] == 'm' and key_root[-1] != '#':
        key_root = key_root[:-1]
    if len(key_root) >= 4 and key_root.endswith("min"):
        key_root = key_root[:-3]
    key_semi = root_to_semitone(key_root)
    chord_semi = root_to_semitone(root)
    interval = (chord_semi - key_semi) % 12

    # Find degree in major scale
    if interval in _MAJOR_SCALE_SEMITONES:
        degree_idx = _MAJOR_SCALE_SEMITONES.index(interval)
        roman = _ROMAN_NUMERALS[degree_idx]
        prefix = ''
    else:
        # Chromatic alteration — prefer ♭ (more common in music theory)
        found = False
        for i, s in enumerate(_MAJOR_SCALE_SEMITONES):
            if (s - 1) % 12 == interval:
                roman = _ROMAN_NUMERALS[i]
                prefix = '♭'
                degree_idx = i
                found = True
                break
        if not found:
            for i, s in enumerate(_MAJOR_SCALE_SEMITONES):
                if (s + 1) % 12 == interval:
                    roman = _ROMAN_NUMERALS[i]
                    prefix = '♯'
                    degree_idx = i
                    found = True
                    break
        if not found:
            roman = '?'
            prefix = ''
            degree_idx = 0

    # Minor chords use lowercase Roman
    is_minor = quality in ('m', 'min', 'm7', 'min7', 'm9', 'm11', 'm6', 'dim', 'dim7', 'm7b5',
                           'mM7', 'madd9', 'mb5', 'm6/9')
    if is_minor:
        roman = roman.lower()

    # Append quality suffix to Roman
    quality_suffix = ''
    if quality in ('7', 'maj7', 'm7', 'min7', 'dim7', 'm7b5', 'aug7', '9', 'maj9', 'm9',
                    '11', 'm11', '13', 'mM7', '6', 'm6', 'add9', 'sus2', 'sus4', '7sus4'):
        quality_suffix = quality.replace('min7', 'm7')
        # For minor qualities, strip the leading 'm' since Roman is already lowercase
        if quality_suffix.startswith('m') and is_minor and quality_suffix != 'maj7' and quality_suffix != 'maj9':
            quality_suffix = quality_suffix[1:] if len(quality_suffix) > 1 else ''
    elif quality == 'dim':
        quality_suffix = '°'
    elif quality == 'aug':
        quality_suffix = '+'

    full_roman = f'{prefix}{roman}{quality_suffix}'
    type_name = QUALITY_NAMES.get(quality, quality or 'Major')
    degree = degree_idx + 1

    # Generate explanation
    if prefix:
        explanation = f'{chord_str} is the {prefix}{roman}{quality_suffix} in key {key} — borrowed chord'
    else:
        explanation = f'{chord_str} is the {full_roman} in key {key}'

    return {
        'roman': full_roman,
        'degree': degree,
        'type_name': type_name,
        'explanation': explanation,
    }


def get_chord_info(chord_str: str) -> dict:
    """
    取得和弦的完整資訊
    """
    root, quality, bass = parse_chord(chord_str)
    notes = get_chord_notes(chord_str)
    jianpu = get_chord_jianpu(chord_str)

    return {
        'chord': chord_str,
        'root': root,
        'quality': quality,
        'bass': bass,
        'notes': notes,
        'jianpu': jianpu,
        'type_name': QUALITY_NAMES.get(quality, quality or 'Major'),
    }


if __name__ == '__main__':
    # 測試
    test_chords = [
        'Eb', 'Gm', 'Ab', 'Fm7', 'Bb11', 'Bb7',
        'Ebmaj7', 'Eb7', 'Abmaj7', 'Dbmaj7', 'Gbmaj7',
        'Cb', 'Cm7', 'F11', 'Bb', 'Bbmaj7',
        'Bb/D', 'D7', 'A7', 'F',
    ]
    for chord in test_chords:
        info = get_chord_info(chord)
        print(f"{chord:12s} → 組成音: {', '.join(info['notes']):20s} 簡譜: {info['jianpu']}")
