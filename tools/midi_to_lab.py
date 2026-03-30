"""
MIDI → .lab 轉換工具
====================
從 MIDI 檔案自動產出和弦 + 精確時間軸。

用法:
  python midi_to_lab.py <midi_file> [--output <output_dir>]

或由 GUI 呼叫:
  from midi_to_lab import midi_to_lab
  entries = midi_to_lab("song.mid")
"""

import sys
import json
import hashlib
from pathlib import Path
import pretty_midi

NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']

# 和弦模板：intervals → quality suffix
CHORD_TYPES = {
    (0, 4, 7): '',
    (0, 3, 7): 'm',
    (0, 4, 7, 10): '7',
    (0, 3, 7, 10): 'm7',
    (0, 4, 7, 11): 'maj7',
    (0, 3, 6): 'dim',
    (0, 4, 8): 'aug',
    (0, 5, 7): 'sus4',
    (0, 2, 7): 'sus2',
    (0, 4, 7, 9): '6',
    (0, 3, 7, 9): 'm6',
    (0, 3, 6, 9): 'dim7',
    (0, 3, 7, 11): 'm(maj7)',
    (0, 4, 7, 10, 14): '9',
    (0, 4, 7, 10, 2): '9',  # 9th wrapped
    (0, 3, 7, 10, 14): 'm9',
    (0, 3, 7, 10, 2): 'm9',
    (0, 4, 7, 11, 14): 'maj9',
    (0, 4, 7, 11, 2): 'maj9',
}


def identify_chord(note_pitches: list, bass_pitch: int = None) -> str:
    """從 MIDI 音符識別和弦名稱"""
    notes = sorted(set(p % 12 for p in note_pitches))
    if not notes:
        return 'N'

    bass = bass_pitch % 12 if bass_pitch is not None else notes[0]

    # 嘗試每個音作為 root
    best_match = None
    for root in notes:
        intervals = tuple(sorted((n - root) % 12 for n in notes))
        if intervals in CHORD_TYPES:
            quality = CHORD_TYPES[intervals]
            chord_name = NOTE_NAMES[root] + quality
            if bass != root:
                chord_name += '/' + NOTE_NAMES[bass]
            # 優先選 root == bass
            if root == bass:
                return chord_name
            if best_match is None:
                best_match = chord_name

    # 沒有完全匹配 → 用最接近的
    if best_match:
        return best_match

    # fallback: 用 bass 當根音
    return NOTE_NAMES[bass]


def midi_to_lab(midi_path: str, verbose: bool = True) -> list:
    """
    解析 MIDI 檔案，回傳 .lab 格式的和弦列表。

    Returns:
        [{"time": 0.859, "end": 3.278, "chord": "A"}, ...]
    """
    midi = pretty_midi.PrettyMIDI(midi_path)

    if verbose:
        print(f"  MIDI: {Path(midi_path).name}")
        print(f"  Duration: {midi.get_end_time():.1f}s")
        print(f"  Tracks: {len(midi.instruments)}")

    # 策略：找有最多音符的 track 作為和弦 track，
    # 找音符最少且音域最低的作為 bass track
    tracks = [(i, inst, len(inst.notes)) for i, inst in enumerate(midi.instruments)]
    tracks.sort(key=lambda x: -x[2])

    if len(tracks) >= 2:
        # 兩個 track：較多音符 = 和弦，較少 = bass
        chord_track = tracks[0][1]
        bass_track = tracks[1][1]
        if verbose:
            print(f"  Chord track: {tracks[0][2]} notes")
            print(f"  Bass track: {tracks[1][2]} notes")
    elif len(tracks) == 1:
        chord_track = tracks[0][1]
        bass_track = None
        if verbose:
            print(f"  Single track: {tracks[0][2]} notes")
    else:
        return []

    # 以 bass track 的音符時間為基準分組
    if bass_track and len(bass_track.notes) > 0:
        entries = []
        for bass_note in bass_track.notes:
            t_start = bass_note.start
            t_end = bass_note.end

            # 找同時開始的和弦音符
            chord_pitches = [n.pitch for n in chord_track.notes
                             if abs(n.start - t_start) < 0.1]

            chord_name = identify_chord(chord_pitches, bass_note.pitch)
            entries.append({
                "time": round(t_start, 3),
                "end": round(t_end, 3),
                "chord": chord_name,
            })
    else:
        # 單 track：按同時發聲音符分組
        # 找所有不同的 onset 時間
        onsets = sorted(set(round(n.start, 3) for n in chord_track.notes))
        entries = []
        for i, onset in enumerate(onsets):
            notes_at = [n for n in chord_track.notes if abs(n.start - onset) < 0.05]
            if not notes_at:
                continue
            pitches = [n.pitch for n in notes_at]
            end = onsets[i + 1] if i + 1 < len(onsets) else max(n.end for n in notes_at)
            chord_name = identify_chord(pitches)
            entries.append({
                "time": round(onset, 3),
                "end": round(end, 3),
                "chord": chord_name,
            })

    if verbose:
        chords_unique = set(e["chord"] for e in entries)
        print(f"  Chords: {len(entries)} ({len(chords_unique)} unique)")

    return entries


def save_lab(entries: list, song_name: str, output_dir: str, source: str = "midi"):
    """儲存為 .lab JSON"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 推導 key
    from collections import Counter
    roots = []
    for e in entries:
        c = e["chord"]
        if c and c[0] in "ABCDEFG":
            root = c[0]
            if len(c) > 1 and c[1] in '#b':
                root += c[1]
            roots.append(root)
    key = Counter(roots).most_common(1)[0][0] if roots else ""

    lab = {
        "song": song_name,
        "key": key,
        "source": f"MIDI ({source})",
        "entries": entries,
    }

    lab_path = output_dir / f"{song_name}.lab"
    lab_path.write_text(json.dumps(lab, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ Saved: {lab_path} ({len(entries)} chords, Key: {key})")
    return str(lab_path)


def import_to_livechord(entries: list, nas_path: str, key: str):
    """匯入到 LiveChord 播放系統"""
    PROJECT_DIR = Path(__file__).parent.parent
    CHORDS_DIR = PROJECT_DIR / "data" / "chords"
    CHORDS_DIR.mkdir(parents=True, exist_ok=True)

    song_hash = hashlib.md5(nas_path.encode("utf-8")).hexdigest()[:12]
    chord_data = {
        "path": nas_path,
        "key": key,
        "capo": 0,
        "source": "midi",
        "chords": entries,
    }
    chord_file = CHORDS_DIR / f"{song_hash}.json"
    chord_file.write_text(json.dumps(chord_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ Imported to LiveChord: {chord_file.name}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python midi_to_lab.py <midi_file> [--output <dir>]")
        sys.exit(1)

    midi_path = sys.argv[1]
    output_dir = sys.argv[3] if len(sys.argv) > 3 and sys.argv[2] == "--output" else str(Path(midi_path).parent)
    song_name = Path(midi_path).stem

    print(f"=== MIDI → .lab: {song_name} ===")
    entries = midi_to_lab(midi_path)
    if entries:
        save_lab(entries, song_name, output_dir)
