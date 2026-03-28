"""
和弦偵測分級測試工具
用法: python run_test.py [Lv1|Lv2|...|all]
"""
import os
import sys
import json
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))
from chord_detect import detect_key, detect_chords


def scan_level(level_dir):
    """掃描指定等級資料夾中的 FLAC 檔案"""
    files = []
    for f in sorted(os.listdir(level_dir)):
        if f.lower().endswith(".flac"):
            files.append(os.path.join(level_dir, f))
    return files


def test_file(filepath):
    """偵測單首歌曲並回傳結果"""
    name = os.path.basename(filepath).replace(".flac", "")
    print(f"\n  Testing: {name}")
    print(f"  {'─' * 60}")

    t0 = time.time()
    try:
        key = detect_key(filepath)
        chords = detect_chords(filepath)
        elapsed = time.time() - t0

        print(f"  Key: {key}")
        print(f"  Chords: {len(chords)} ({elapsed:.1f}s)")

        # 顯示和弦摘要（合併重複）
        chord_names = [c["chord"] for c in chords]
        unique = []
        for c in chord_names:
            if not unique or unique[-1] != c:
                unique.append(c)

        # 只顯示前 20 個
        display = unique[:20]
        if len(unique) > 20:
            display.append(f"... (+{len(unique)-20})")
        print(f"  Progression: {' → '.join(display)}")

        return {
            "name": name, "key": key, "chord_count": len(chords),
            "unique_chords": list(set(chord_names)),
            "elapsed": round(elapsed, 1), "error": None,
        }
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  ERROR: {e} ({elapsed:.1f}s)")
        return {"name": name, "key": None, "chord_count": 0,
                "elapsed": round(elapsed, 1), "error": str(e)}


def run_level(level):
    """執行一個等級的測試"""
    base = os.path.dirname(os.path.abspath(__file__))
    level_dir = os.path.join(base, level)

    if not os.path.isdir(level_dir):
        print(f"ERROR: {level_dir} not found")
        return

    files = scan_level(level_dir)
    if not files:
        print(f"\n{'═' * 64}")
        print(f"  {level}: 無 FLAC 檔案，請依 criteria.txt 放入測試曲目")
        print(f"{'═' * 64}")
        return

    print(f"\n{'═' * 64}")
    print(f"  {level} — {len(files)} test files")
    print(f"{'═' * 64}")

    results = []
    for f in files:
        results.append(test_file(f))

    # 摘要
    ok = sum(1 for r in results if r["error"] is None)
    print(f"\n  {'─' * 60}")
    print(f"  {level} Summary: {ok}/{len(results)} succeeded")
    total_time = sum(r["elapsed"] for r in results)
    print(f"  Total time: {total_time:.1f}s")

    # 儲存結果
    result_file = os.path.join(level_dir, "results.json")
    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"  Results saved: {result_file}")


def main():
    levels = sys.argv[1:] if len(sys.argv) > 1 else ["all"]

    if "all" in levels:
        levels = ["Lv1", "Lv2", "Lv3", "Lv4", "Lv5"]

    for lv in levels:
        run_level(lv)

    print(f"\n{'═' * 64}")
    print("  Done!")
    print(f"{'═' * 64}")


if __name__ == "__main__":
    main()
