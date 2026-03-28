"""
擷取工具診斷腳本
================
逐步測試各個模組，找出擷取中斷的原因。

用法: python capture_debug.py
"""

import os
import sys
import json
import time
import numpy as np
from pathlib import Path
from PIL import Image

# 路徑
TOOLS_DIR = Path(__file__).parent
CONFIG_FILE = TOOLS_DIR / "capture_config.json"
DEBUG_DIR = TOOLS_DIR / "debug_output"
DEBUG_DIR.mkdir(exist_ok=True)

# 載入已儲存的區域座標
def load_regions():
    if not CONFIG_FILE.is_file():
        print("  ✗ 找不到 capture_config.json，請先執行 chordify_capture.py 框選區域")
        sys.exit(1)
    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    return {
        "play_btn": tuple(cfg["play_btn_region"]),
        "time": tuple(cfg["time_region"]),
        "chord": tuple(cfg["chord_region"]),
    }

# 匯入擷取模組
sys.path.insert(0, str(TOOLS_DIR))
from chordify_capture import (
    capture_region, detect_play_button_state, ocr_time,
    find_highlighted_chord, get_reader
)


def test_1_regions(regions):
    """測試 1: 擷取各區域截圖並儲存"""
    print("\n" + "=" * 60)
    print("  測試 1: 區域擷取")
    print("=" * 60)

    for name, region in regions.items():
        img = capture_region(region)
        path = DEBUG_DIR / f"region_{name}.png"
        img.save(str(path))
        print(f"  {name:10s}: {region} → {img.size} → {path.name}")

    print("  → 請檢查 tools/debug_output/ 下的截圖是否正確框選目標")


def test_2_play_button(regions):
    """測試 2: 播放按鈕偵測（連續 10 次）"""
    print("\n" + "=" * 60)
    print("  測試 2: 播放按鈕偵測（連續 10 次，每秒 1 次）")
    print("  請在測試期間嘗試播放/暫停 Chordify")
    print("=" * 60)

    for i in range(10):
        img = capture_region(regions["play_btn"])
        state = detect_play_button_state(img)

        # 保存截圖供分析
        img.save(str(DEBUG_DIR / f"btn_{i:02d}_{state}.png"))

        # 額外分析
        arr = np.array(img)
        h, w, _ = arr.shape
        gray = np.mean(arr, axis=2)
        y_start, y_end = int(h * 0.3), int(h * 0.7)
        center_band = gray[y_start:y_end, :]
        col_brightness = np.mean(center_band, axis=0)
        cx_start, cx_end = int(w * 0.33), int(w * 0.66)
        center_cols = col_brightness[cx_start:cx_end]

        mid = len(center_cols) // 2
        mid_zone = center_cols[max(0, mid-2):mid+3]
        side_left = center_cols[:max(1, mid-3)]
        side_right = center_cols[min(len(center_cols)-1, mid+3):]
        mid_val = np.mean(mid_zone)
        side_val = (np.mean(side_left) + np.mean(side_right)) / 2
        dip = side_val - mid_val

        icon = "||" if state == "playing" else "▶"
        print(f"  [{i:2d}] {icon} {state:8s}  dip={dip:5.1f}  mid={mid_val:5.1f}  side={side_val:5.1f}  (threshold=15)")

        time.sleep(1)


def test_3_time_ocr(regions):
    """測試 3: 時間 OCR（連續 15 次）"""
    print("\n" + "=" * 60)
    print("  測試 3: 時間 OCR（連續 15 次，每 0.5 秒）")
    print("  請確保 Chordify 正在播放")
    print("=" * 60)

    success = 0
    fail = 0
    for i in range(15):
        img = capture_region(regions["time"])
        img.save(str(DEBUG_DIR / f"time_{i:02d}.png"))

        result = ocr_time(img)
        if result is not None:
            m, s = divmod(result, 60)
            print(f"  [{i:2d}] ✓ {m}:{s:02d} ({result}s)")
            success += 1
        else:
            print(f"  [{i:2d}] ✗ 無法辨識")
            fail += 1

        time.sleep(0.5)

    rate = success / (success + fail) * 100 if (success + fail) > 0 else 0
    print(f"\n  OCR 成功率: {success}/{success+fail} ({rate:.0f}%)")
    if rate < 80:
        print("  ⚠ OCR 成功率過低！可能原因：")
        print("    - 時間區域框選不夠精確（太大或太小）")
        print("    - 瀏覽器字型/大小不同")
        print("    → 請檢查 debug_output/time_*.png")


def test_4_chord_detection(regions):
    """測試 4: 和弦偵測（連續 20 次）"""
    print("\n" + "=" * 60)
    print("  測試 4: 和弦偵測（連續 20 次，每 0.3 秒）")
    print("  請確保 Chordify 正在播放，觀察高亮和弦變化")
    print("=" * 60)

    last_center = None
    for i in range(20):
        img = capture_region(regions["chord"])
        img.save(str(DEBUG_DIR / f"chord_{i:02d}.png"))

        result = find_highlighted_chord(img)

        # find_highlighted_chord 可能回傳 (chord, center) 或 (None, center) 或 None
        if isinstance(result, tuple):
            chord, center = result
        else:
            chord, center = result, None

        # 檢查方塊移動
        moved = ""
        if center and last_center:
            dx = abs(center[0] - last_center[0])
            dy = abs(center[1] - last_center[1])
            if dx > 15 or dy > 15:
                moved = f" MOVED(dx={dx},dy={dy})"
        if center:
            last_center = center

        center_str = f"({center[0]:4d},{center[1]:4d})" if center else "(None)"
        chord_str = chord if chord else "None"
        print(f"  [{i:2d}] chord={chord_str:12s}  center={center_str}{moved}")

        time.sleep(0.3)


def test_5_full_capture_sim(regions):
    """測試 5: 模擬完整擷取流程（30 秒）"""
    print("\n" + "=" * 60)
    print("  測試 5: 模擬完整擷取 30 秒")
    print("  請確保 Chordify 正在播放")
    print("=" * 60)

    records = []
    last_chord = None
    last_center = None
    last_time = None
    btn_check = 0
    start = time.perf_counter()

    for i in range(100):  # 100 iterations * 0.3s = ~30 seconds
        elapsed = time.perf_counter() - start

        # 時間 OCR
        time_img = capture_region(regions["time"])
        current_sec = ocr_time(time_img)

        # 播放按鈕（每 8 次）
        btn_check += 1
        btn_stopped = False
        if btn_check >= 8:
            btn_check = 0
            btn_img = capture_region(regions["play_btn"])
            state = detect_play_button_state(btn_img)
            if state == "stopped":
                btn_stopped = True

        # 時間倒退
        time_reset = False
        if current_sec is not None and last_time is not None:
            if current_sec < last_time - 5:
                time_reset = True
        if current_sec is not None:
            last_time = current_sec

        # 和弦
        chord_img = capture_region(regions["chord"])
        result = find_highlighted_chord(chord_img)
        if isinstance(result, tuple):
            chord, center = result
        else:
            chord, center = result, None

        box_moved = False
        if center:
            if last_center is None:
                box_moved = True
            elif abs(center[0] - last_center[0]) > 15 or abs(center[1] - last_center[1]) > 15:
                box_moved = True
            last_center = center

        new_chord = box_moved and chord and chord != last_chord
        if new_chord:
            records.append((elapsed, chord))
            last_chord = chord

        # 輸出
        time_str = f"{current_sec//60}:{current_sec%60:02d}" if current_sec else "?:??"
        chord_str = chord or "---"
        flags = []
        if btn_stopped: flags.append("BTN_STOP")
        if time_reset: flags.append("TIME_RESET")
        if box_moved: flags.append("BOX_MOVED")
        if new_chord: flags.append("NEW_CHORD")
        flag_str = " ".join(flags)

        print(f"  [{elapsed:5.1f}s] OCR={time_str:5s} chord={chord_str:10s} {flag_str}")

        if btn_stopped:
            print("\n  ⚠ BTN_STOP 觸發！這就是擷取中斷的原因")
            print(f"    → 檢查 debug_output/btn_stop_at_{elapsed:.0f}s.png")
            btn_img = capture_region(regions["play_btn"])
            btn_img.save(str(DEBUG_DIR / f"btn_stop_at_{elapsed:.0f}s.png"))
            break

        if time_reset:
            print("\n  ⚠ TIME_RESET 觸發！")
            break

        time.sleep(0.3)

    print(f"\n  結果: {len(records)} 個和弦，{elapsed:.1f} 秒")
    for t, c in records:
        print(f"    {t:6.1f}s  {c}")

    if len(records) < 5:
        print("\n  ⚠ 和弦數過少，可能原因：")
        print("    1. box_moved 偵測閾值(15px)不適合你的螢幕解析度")
        print("    2. 和弦區域框選太大/太小")
        print("    3. 高亮方塊的對比度不夠")


def main():
    print("=" * 60)
    print("  Chordify 擷取工具 — 診斷模式")
    print("=" * 60)

    regions = load_regions()
    print(f"  已載入區域座標:")
    for k, v in regions.items():
        print(f"    {k}: {v}")

    print(f"\n  診斷截圖儲存於: {DEBUG_DIR}")

    # 預載 OCR
    get_reader()

    tests = [
        ("1", "區域擷取截圖", test_1_regions),
        ("2", "播放按鈕偵測 (10s)", test_2_play_button),
        ("3", "時間 OCR (7.5s)", test_3_time_ocr),
        ("4", "和弦偵測 (6s)", test_4_chord_detection),
        ("5", "完整擷取模擬 (30s)", test_5_full_capture_sim),
    ]

    print("\n  可選測試：")
    for num, desc, _ in tests:
        print(f"    {num}. {desc}")
    print(f"    a. 全部執行")

    choice = input("\n  選擇 (1-5/a): ").strip().lower()

    if choice == 'a':
        for _, _, fn in tests:
            fn(regions)
    elif choice in [t[0] for t in tests]:
        for num, _, fn in tests:
            if num == choice:
                fn(regions)
                break
    else:
        print("  無效選擇")


if __name__ == "__main__":
    main()
