"""
LiveChord 離線伴奏與指法生成批次工廠 — Phase 11 升級版

新增:
  - 段落偵測 (section_detect) → section_type 自動注入
  - 踏板建議 (pedal_advisor) → pedal[] 附加到輸出
  - 力度表情 (dynamics_engine) → velocity/articulation 寫入 events
  - QA Battle 分數記錄
"""

import os
import sys
import json
import time
import argparse
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai.accompaniment_generator import generate_accompaniment, ACC_ENGINE_VERSION

CHORDS_DIR = Path(__file__).parent.parent / "data" / "chords"
from chord_cache import chord_file_for, iter_chord_files  # noqa: E402
MELODIES_DIR = Path(__file__).parent.parent / "data" / "melodies"
ACCOMP_DIR = Path(__file__).parent.parent / "data" / "accompaniments"

print_lock = threading.Lock()


def _detect_sections_safe(chords, key, song_hash=None):
    """安全的段落偵測（失敗時回傳空）"""
    try:
        from ai.section_detect import detect_sections
        result = detect_sections(chords, key, song_hash=song_hash)
        return result.get("sections", [])
    except Exception:
        return []


def _get_dominant_section(sections, chords):
    """找出和弦序列中最常見的段落類型"""
    if not sections:
        return "default"
    from ai.section_context import get_section_context
    total_dur = chords[-1].get("end", 0) if chords else 0
    type_counts = {}
    for c in chords:
        ctx = get_section_context(c.get("time", 0), sections, total_dur)
        t = ctx["type"]
        type_counts[t] = type_counts.get(t, 0) + 1
    return max(type_counts, key=type_counts.get) if type_counts else "default"


def process_track(song_hash: str, levels: list, styles: list,
                  add_pedal: bool = True, add_dynamics: bool = True):
    chord_file = chord_file_for(song_hash)
    melody_file = MELODIES_DIR / f"{song_hash}.json"

    if not chord_file.is_file() or not melody_file.is_file():
        return "SKIP_MISSING"

    try:
        sheet_data = json.loads(chord_file.read_text(encoding="utf-8"))
        melody_data = json.loads(melody_file.read_text(encoding="utf-8"))

        chords = sheet_data.get("chords", [])
        if not chords:
            return "SKIP_EMPTY"

        melody = melody_data.get("melody", []) if isinstance(melody_data, dict) else melody_data
        key = sheet_data.get("key", "C")
        genre = sheet_data.get("genre", "pop")
        bpm = sheet_data.get("bpm", 120)
        # Phase 2: dynamic-beat fields. Empty/None when chord JSON predates
        # beat_snap.analyze_and_snap_dynamic — generators fall back to scalar bpm.
        tempo_curve = sheet_data.get("tempo_curve") or None
        beat_version = sheet_data.get("beat_version", 0)
        time_signature = sheet_data.get("time_signature") or sheet_data.get("meter") or "4/4"

        # Phase 11: 段落偵測
        sections = _detect_sections_safe(chords, key, song_hash=song_hash)
        dominant_section = _get_dominant_section(sections, chords)

        results = []
        for level in levels:
            for style in styles:
                # v6: filename includes instrument segment. Batch worker only
                # backfills the piano cache; guitar/uke variants regenerate
                # lazily on demand (string-instrument tabs are infrequent).
                out_name = f"{song_hash}_{style}_{level}_default_piano_{ACC_ENGINE_VERSION}.json"
                out_path = ACCOMP_DIR / out_name

                if out_path.exists():
                    results.append("S")
                    continue

                # 生成伴奏 (含 section_type)
                acc = generate_accompaniment(
                    chords=chords, melody=melody,
                    bpm=bpm, style=style, level=level, genre=genre,
                    section_type=dominant_section,
                    tempo_curve=tempo_curve,
                    time_signature=time_signature,
                )
                acc["bpm"] = round(bpm, 1)
                acc["genre"] = genre
                # Phase 2: stamp source beat version so player can detect
                # stale acc when the chord JSON's beats[] has been regenerated.
                acc["source_beat_version"] = beat_version

                # Phase 11: 踏板
                if add_pedal:
                    try:
                        from ai.pedal_advisor import generate_pedal_suggestions
                        pedal_style = "rhythmic" if dominant_section == "chorus" else "legato"
                        acc["pedal"] = generate_pedal_suggestions(
                            chords, melody=melody, bpm=bpm, style=pedal_style
                        )
                    except Exception:
                        acc["pedal"] = []

                # Phase 11: 力度表情 — 分手處理以保留左右手音量平衡
                if add_dynamics:
                    try:
                        from ai.dynamics_engine import generate_dynamics
                        generate_dynamics(acc.get("left_hand", []), bpm=int(bpm),
                                          section_type=dominant_section)
                        generate_dynamics(acc.get("right_hand", []), bpm=int(bpm),
                                          section_type=dominant_section)
                    except Exception:
                        pass

                out_path.write_text(
                    json.dumps(acc, ensure_ascii=False),
                    encoding="utf-8",
                )
                results.append("G")

        return "".join(results) + f" sec={dominant_section}"
    except Exception as e:
        return f"ERR({e})"


def main():
    parser = argparse.ArgumentParser(description="LiveChord 離線伴奏工廠 (Phase 11)")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--levels", type=str, default="L1,L2",
                        help="Comma separated e.g. L1,L2,L3")
    parser.add_argument("--styles", type=str, default="Arpeggio,Block",
                        help="Comma separated")
    parser.add_argument("--no-pedal", action="store_true",
                        help="Skip pedal generation")
    parser.add_argument("--no-dynamics", action="store_true",
                        help="Skip dynamics generation")

    args = parser.parse_args()

    ACCOMP_DIR.mkdir(parents=True, exist_ok=True)

    levels = [x.strip() for x in args.levels.split(",") if x.strip()]
    styles = [x.strip() for x in args.styles.split(",") if x.strip()]

    print("==================================================")
    print("LiveChord 離線伴奏工廠 (Phase 11)")
    print(f"  Levels: {levels}")
    print(f"  Styles: {styles}")
    print(f"  Workers: {args.workers}")
    print(f"  Pedal: {'ON' if not args.no_pedal else 'OFF'}")
    print(f"  Dynamics: {'ON' if not args.no_dynamics else 'OFF'}")
    print("==================================================")

    # 找出同時有 chord + melody 的 hash
    chord_hashes = {f.stem for f in iter_chord_files()}
    melody_hashes = {f.stem for f in MELODIES_DIR.glob("*.json")}
    hashes = sorted(chord_hashes & melody_hashes)

    total = len(hashes)
    print(f"Found {total} songs with chord+melody data.")

    if total == 0:
        return

    start_time = time.time()
    done = 0
    generated = 0
    err_count = 0

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                process_track, h, levels, styles,
                not args.no_pedal, not args.no_dynamics
            ): h
            for h in hashes
        }

        for future in as_completed(futures):
            h = futures[future]
            done += 1
            try:
                res = future.result()
                if "ERR" in res:
                    err_count += 1
                if "G" in res:
                    generated += res.count("G")
                if done % 100 == 0 or "ERR" in res:
                    elapsed = time.time() - start_time
                    with print_lock:
                        print(f"[{done}/{total}] {h} -> {res}  "
                              f"({elapsed:.0f}s, {done/elapsed:.1f}/s)")
            except Exception as e:
                err_count += 1
                with print_lock:
                    print(f"[{done}/{total}] {h} -> FATAL: {e}")

    elapsed = time.time() - start_time
    print(f"\nDone! {elapsed:.0f}s ({elapsed/60:.1f}min)")
    print(f"  Songs: {total}, Generated: {generated}, Errors: {err_count}")


if __name__ == "__main__":
    main()
