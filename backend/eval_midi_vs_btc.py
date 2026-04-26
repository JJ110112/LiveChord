"""
MIDI Ground Truth vs BTC AI 和弦偵測 評估腳本
=============================================
X:\ = MIDI 檔案 (ground truth)
Y:\ = 對應的音檔 (.flac)
data/chords/ = BTC 偵測結果

用法: python eval_midi_vs_btc.py [--midi-dir X:/ --music-dir Y:/]
"""

import os
import sys
import json
from pathlib import Path
from collections import Counter

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
sys.stdout.reconfigure(encoding="utf-8")

CHORDS_DIR = Path(__file__).parent.parent / "data" / "chords"

# ---- 評分核心 (from benchmark_api.py) ----

_ROOT_MAP = {
    "C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4,
    "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9,
    "A#": 10, "Bb": 10, "B": 11,
}


def _parse_root(chord: str) -> int:
    if not chord or chord in ("N", "X", "NC"):
        return -1
    if len(chord) >= 2 and chord[1] in ("#", "b"):
        return _ROOT_MAP.get(chord[:2], -1)
    return _ROOT_MAP.get(chord[:1], -1)


def _parse_quality(chord: str) -> str:
    if not chord or chord in ("N", "X", "NC"):
        return ""
    # strip slash bass
    chord = chord.split("/")[0]
    if len(chord) >= 2 and chord[1] in ("#", "b"):
        return chord[2:]
    return chord[1:]


def _normalize_quality(q: str) -> str:
    """將品質正規化為基礎分類，提高比對公平性"""
    # maj7/maj9 → maj7 family
    if q in ("maj7", "maj9", "maj11", "maj13", "6", "add9"):
        return "maj7"
    if q in ("m7", "m9", "m11", "m6", "madd9"):
        return "m7"
    if q in ("7", "9", "11", "13", "7b9", "7#9", "7#11", "7b13"):
        return "7"
    if q in ("m7b5",):
        return "m7b5"
    if q in ("dim", "dim7"):
        return "dim"
    if q in ("aug",):
        return "aug"
    if q in ("sus4", "sus2"):
        return "sus"
    if q in ("m", "min"):
        return "m"
    if q == "":
        return ""
    return q


def score_song(gt_entries: list, det_entries: list):
    """
    逐段比對 ground truth 與偵測結果 (time-weighted)。
    回傳 root_accuracy, quality_accuracy, full_accuracy。
    """
    if not gt_entries or not det_entries:
        return {"root_accuracy": 0, "full_accuracy": 0, "quality_accuracy": 0,
                "segments": 0, "duration": 0}

    total_dur = 0
    root_dur = 0
    qual_dur = 0
    full_dur = 0

    for gt in gt_entries:
        gt_start = gt["time"]
        gt_end = gt.get("end", gt_start + 2.0)
        gt_chord = gt["chord"]
        gt_dur = gt_end - gt_start
        if gt_dur <= 0:
            continue
        total_dur += gt_dur

        if gt_chord in ("N", "X", "NC", ""):
            continue

        gt_root = _parse_root(gt_chord)
        gt_qual = _normalize_quality(_parse_quality(gt_chord))

        # 找時間重疊最多的偵測片段
        best_det = None
        best_overlap = 0
        for det in det_entries:
            d_start = det["time"]
            d_end = det.get("end", d_start + 2.0)
            overlap = max(0, min(gt_end, d_end) - max(gt_start, d_start))
            if overlap > best_overlap:
                best_overlap = overlap
                best_det = det

        if best_det is None:
            continue

        det_root = _parse_root(best_det["chord"])
        det_qual = _normalize_quality(_parse_quality(best_det["chord"]))

        root_match = (gt_root == det_root) and (gt_root >= 0)
        qual_match = (gt_qual == det_qual)
        full_match = root_match and qual_match

        if root_match:
            root_dur += gt_dur
        if qual_match and root_match:
            qual_dur += gt_dur
        if full_match:
            full_dur += gt_dur

    if total_dur == 0:
        return {"root_accuracy": 0, "full_accuracy": 0, "quality_accuracy": 0,
                "segments": len(gt_entries), "duration": 0}

    return {
        "root_accuracy": round(root_dur / total_dur * 100, 1),
        "quality_accuracy": round(qual_dur / total_dur * 100, 1),
        "full_accuracy": round(full_dur / total_dur * 100, 1),
        "segments": len(gt_entries),
        "duration": round(total_dur, 1),
    }


from chord_cache import song_hash, chord_file_for


def main():
    import argparse
    parser = argparse.ArgumentParser(description="MIDI vs BTC 和弦偵測評估")
    parser.add_argument("--midi-dir", type=str, default="X:/")
    parser.add_argument("--music-dir", type=str, default="Y:/")
    parser.add_argument("--detect", action="store_true", help="對缺少偵測結果的歌曲即時執行 BTC")
    args = parser.parse_args()

    midi_dir = Path(args.midi_dir)
    music_dir = Path(args.music_dir)

    # 掃描 MIDI 檔案
    midi_files = sorted(midi_dir.glob("*.mid"))
    print(f"MIDI ground truth: {len(midi_files)} files in {midi_dir}")
    print(f"Music directory: {music_dir}")
    print(f"BTC chords: {CHORDS_DIR}")
    if args.detect:
        print("🔥 --detect 模式：缺少偵測結果的歌曲將即時執行 BTC")
    print()

    from midi_to_lab import midi_to_lab

    results = []
    skipped = []
    errors = []

    for midi_file in midi_files:
        name = midi_file.stem
        # 找對應的音檔
        audio_file = None
        for ext in (".flac", ".mp3", ".wav"):
            candidate = music_dir / f"{name}{ext}"
            if candidate.is_file():
                audio_file = candidate
                break

        if audio_file is None:
            skipped.append((name, "no_audio"))
            continue

        # 找 BTC 偵測結果
        rel_path = audio_file.name
        h = song_hash(rel_path)
        chord_file = chord_file_for(h)

        if not chord_file.is_file():
            if not args.detect:
                skipped.append((name, "no_btc"))
                continue
            # 即時偵測
            try:
                print(f"  🔍 偵測中: {name}...", end="", flush=True)
                from chord_detect import detect_chords, _key_from_chords
                chords = detect_chords(str(audio_file))
                key = _key_from_chords(chords) if chords else "C"
                sheet = {"path": rel_path, "key": key, "capo": 0,
                         "source": "btc_eval", "chords": chords}
                CHORDS_DIR.mkdir(parents=True, exist_ok=True)
                chord_file.write_text(json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f" OK ({len(chords)} chords)")
            except Exception as e:
                print(f" FAIL: {e}")
                errors.append((name, f"detect: {e}"))
                continue

        # 讀取 BTC 偵測結果
        try:
            btc_data = json.loads(chord_file.read_text(encoding="utf-8"))
            btc_chords = btc_data.get("chords", [])
            btc_key = btc_data.get("key", "?")
        except Exception as e:
            errors.append((name, f"btc_read: {e}"))
            continue

        # 解析 MIDI ground truth
        try:
            gt_chords = midi_to_lab(str(midi_file), verbose=False)
        except Exception as e:
            errors.append((name, f"midi_parse: {e}"))
            continue

        if not gt_chords:
            skipped.append((name, "empty_midi"))
            continue

        # 評分
        s = score_song(gt_chords, btc_chords)
        s["name"] = name
        s["btc_key"] = btc_key
        s["gt_chords"] = len(gt_chords)
        s["btc_chords"] = len(btc_chords)
        results.append(s)

    # ---- 輸出結果 ----
    print(f"{'Song':<55s} {'Root%':>6s} {'Full%':>6s} {'GT':>4s} {'BTC':>4s} {'Key':>4s}")
    print("-" * 85)

    for r in sorted(results, key=lambda x: -x["root_accuracy"]):
        print(f"{r['name'][:54]:<55s} {r['root_accuracy']:>5.1f}% {r['full_accuracy']:>5.1f}% "
              f"{r['gt_chords']:>4d} {r['btc_chords']:>4d} {r['btc_key']:>4s}")

    # 統計
    if results:
        avg_root = sum(r["root_accuracy"] for r in results) / len(results)
        avg_full = sum(r["full_accuracy"] for r in results) / len(results)
        avg_qual = sum(r["quality_accuracy"] for r in results) / len(results)

        # Root accuracy 分布
        root_buckets = Counter()
        for r in results:
            if r["root_accuracy"] >= 80:
                root_buckets["80-100% (excellent)"] += 1
            elif r["root_accuracy"] >= 60:
                root_buckets["60-80% (good)"] += 1
            elif r["root_accuracy"] >= 40:
                root_buckets["40-60% (fair)"] += 1
            else:
                root_buckets["0-40% (poor)"] += 1

        print()
        print("=" * 85)
        print(f"📊 總評 ({len(results)} songs matched)")
        print(f"   平均 Root Accuracy:    {avg_root:.1f}%")
        print(f"   平均 Quality Accuracy: {avg_qual:.1f}%")
        print(f"   平均 Full Accuracy:    {avg_full:.1f}%")
        print()
        print("   Root Accuracy 分布:")
        for bucket in ["80-100% (excellent)", "60-80% (good)", "40-60% (fair)", "0-40% (poor)"]:
            cnt = root_buckets.get(bucket, 0)
            bar = "█" * cnt
            print(f"     {bucket:25s} {cnt:3d} {bar}")

    if skipped:
        print(f"\n⏭️ 跳過 {len(skipped)} 首:")
        for name, reason in skipped[:10]:
            print(f"   [{reason}] {name}")
        if len(skipped) > 10:
            print(f"   ... 還有 {len(skipped) - 10} 首")

    if errors:
        print(f"\n❌ 錯誤 {len(errors)} 首:")
        for name, reason in errors:
            print(f"   [{reason}] {name}")

    # 儲存詳細結果
    out_path = Path(__file__).parent / "eval_results.json"
    out_data = {
        "total_matched": len(results),
        "avg_root_accuracy": round(avg_root, 1) if results else 0,
        "avg_full_accuracy": round(avg_full, 1) if results else 0,
        "songs": results,
        "skipped": skipped,
        "errors": errors,
    }
    out_path.write_text(json.dumps(out_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n💾 詳細結果已儲存: {out_path}")


if __name__ == "__main__":
    main()
