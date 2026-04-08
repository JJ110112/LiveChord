"""樂器註冊表 — 新增樂器只需在 INSTRUMENTS 加一筆"""

INSTRUMENTS = {
    "guitar": {
        "num_strings": 6,
        "tuning_midi": [40, 45, 50, 55, 59, 64],
        "string_labels": ["E", "A", "D", "G", "B", "e"],
        "frets": 22,
    },
    "ukulele": {
        "num_strings": 4,
        "tuning_midi": [55, 48, 52, 57],
        "string_labels": ["G", "C", "E", "A"],
        "frets": 15,
    },
    "accordion": {
        "type": "accordion",
        "right_hand": "piano",
        "left_hand": "stradella",
        "bass_rows": 3,
        "bass_cols": 7,
        "bass_columns": ["Bb", "F", "C", "G", "D", "A", "E"],
        "bass_row_labels": ["Bass", "Major", "Minor"],
        "dimple_col": 2,
    },
}


def get_instrument(name):
    """取得樂器 metadata，不存在回 None"""
    return INSTRUMENTS.get(name)


def list_instruments():
    """回傳所有已註冊的樂器名稱"""
    return list(INSTRUMENTS.keys())
