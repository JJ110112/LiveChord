"""
Chordify 截圖解析工具（批次模式）
==================================
解析 Chordify 的截圖，提取和弦網格。
不需要即時擷取，只要截圖即可。

使用方式:
  python chordify_screenshot.py screenshot.png "Dancing Queen" Lv1

或互動模式:
  python chordify_screenshot.py
"""

import sys
import os
import json
import numpy as np
from PIL import Image
import easyocr

# ---------------------------------------------------------------------------
# Chordify 截圖解析
# ---------------------------------------------------------------------------

def analyze_chordify_screenshot(image_path: str) -> dict:
    """
    分析 Chordify 截圖，提取：
    1. 當前播放時間（play 按鈕旁）
    2. 和弦網格（所有和弦 + 位置）
    3. 高亮的當前和弦
    """
    img = Image.open(image_path).convert("RGB")
    arr = np.array(img)
    h, w, _ = arr.shape

    reader = easyocr.Reader(['en'], gpu=False, verbose=False)

    # OCR 整張圖
    print("  OCR 分析中...")
    results = reader.readtext(arr, paragraph=False)

    # 提取所有看起來像和弦的文字
    chords_found = []
    time_found = None

    for bbox, text, conf in results:
        text = text.strip()
        if not text:
            continue

        # 找時間 (mm:ss 格式)
        if ':' in text and all(c in '0123456789:' for c in text):
            try:
                parts = text.split(':')
                if len(parts) == 2 and len(parts[1]) == 2:
                    sec = int(parts[0]) * 60 + int(parts[1])
                    # 取位置最靠左上的時間（播放時間）
                    cx = (bbox[0][0] + bbox[2][0]) / 2
                    cy = (bbox[0][1] + bbox[2][1]) / 2
                    if time_found is None or cy < time_found[1]:
                        time_found = (sec, cy, text)
            except (ValueError, IndexError):
                pass
            continue

        # 判斷是否為和弦
        if _is_chord_text(text) and conf > 0.2:
            cx = (bbox[0][0] + bbox[2][0]) / 2
            cy = (bbox[0][1] + bbox[2][1]) / 2

            # 檢查該區域的背景亮度（判斷是否高亮）
            bx1 = max(0, int(bbox[0][0]) - 5)
            by1 = max(0, int(bbox[0][1]) - 5)
            bx2 = min(w, int(bbox[2][0]) + 5)
            by2 = min(h, int(bbox[2][1]) + 5)
            bg_region = arr[by1:by2, bx1:bx2]
            bg_brightness = np.mean(bg_region) if bg_region.size > 0 else 200

            chord_name = _normalize_chord_text(text)
            if chord_name:
                chords_found.append({
                    "text": chord_name,
                    "x": cx, "y": cy,
                    "brightness": float(bg_brightness),
                    "conf": float(conf),
                    "highlighted": bg_brightness < 100,  # 深色背景 = 高亮
                    "bbox": [[float(p[0]), float(p[1])] for p in bbox],
                })

    return {
        "time": time_found,
        "chords": chords_found,
        "image_size": (w, h),
    }


def _is_chord_text(text: str) -> bool:
    """判斷文字是否為和弦名稱"""
    if not text:
        return False
    # 首字必須是音符名
    first = text[0].upper()
    if first not in "ABCDEFG":
        return False
    # 過濾純文字（如 "INSTRUMENT", "VOLUME"）
    if len(text) > 8:
        return False
    # 必須包含有效和弦字元
    valid = set("ABCDEFGabcdefgm#b0123456789/dimaugsusMaj")
    return all(c in valid for c in text)


def _normalize_chord_text(text: str) -> str:
    """正規化和弦文字"""
    text = text.strip()
    if not text:
        return ""

    # OCR 常見錯誤
    text = text.replace("Cff", "C#")
    text = text.replace("FH", "F#")
    text = text.replace("Ff", "F#")
    text = text.replace("Gf", "G#")
    text = text.replace("Bf", "B")

    # 確保首字母大寫
    if text[0].islower():
        text = text[0].upper() + text[1:]

    return text


def extract_chord_grid(analysis: dict) -> list:
    """
    從分析結果提取和弦網格（按行列排序）。
    回傳按出現順序排列的和弦列表。
    """
    chords = analysis["chords"]
    if not chords:
        return []

    # 按 Y 分群（同一行的和弦 Y 座標接近）
    chords_sorted = sorted(chords, key=lambda c: c["y"])

    rows = []
    current_row = [chords_sorted[0]]
    for i in range(1, len(chords_sorted)):
        if abs(chords_sorted[i]["y"] - current_row[0]["y"]) < 30:
            current_row.append(chords_sorted[i])
        else:
            rows.append(sorted(current_row, key=lambda c: c["x"]))
            current_row = [chords_sorted[i]]
    rows.append(sorted(current_row, key=lambda c: c["x"]))

    # 展平為有序列表
    ordered = []
    for row in rows:
        for c in row:
            ordered.append(c["text"])

    return ordered


# ---------------------------------------------------------------------------
# 主程式
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("  Chordify 截圖解析工具")
    print("=" * 60)

    if len(sys.argv) >= 2:
        image_path = sys.argv[1]
        song_name = sys.argv[2] if len(sys.argv) > 2 else os.path.splitext(os.path.basename(image_path))[0]
        level = sys.argv[3] if len(sys.argv) > 3 else ""
    else:
        image_path = input("  截圖路徑: ").strip().strip('"')
        song_name = input("  歌曲名稱: ").strip()
        level = input("  等級 (Lv1~Lv5, 可留空): ").strip()

    if not os.path.isfile(image_path):
        print(f"  找不到: {image_path}")
        return

    # 分析截圖
    print(f"\n  分析: {image_path}")
    analysis = analyze_chordify_screenshot(image_path)

    # 顯示結果
    if analysis["time"]:
        sec, _, text = analysis["time"]
        print(f"  播放時間: {text} ({sec}s)")

    chords = analysis["chords"]
    highlighted = [c for c in chords if c["highlighted"]]
    print(f"  偵測到 {len(chords)} 個和弦文字")
    if highlighted:
        print(f"  高亮和弦: {', '.join(c['text'] for c in highlighted)}")

    # 提取和弦網格
    grid = extract_chord_grid(analysis)
    print(f"\n  和弦序列 ({len(grid)} 個):")
    print(f"  {', '.join(grid)}")

    # 是否儲存
    print(f"\n  注意：截圖模式只能取得「和弦順序」，無法取得精確時間。")
    print(f"  你可以將上方和弦序列貼到 /benchmark 頁面，手動加上時間。")

    # 也可以用估算時間
    if analysis["time"]:
        total_sec = analysis["time"][0]
        save = input("\n  是否用估算時間儲存? (y/n): ").strip().lower()
        if save == 'y':
            # 嘗試從 Chordify 截圖推估歌曲長度
            duration = float(input("  歌曲總長（秒）: ") or "240")
            interval = duration / max(len(grid), 1)
            entries = []
            for i, chord in enumerate(grid):
                t = round(i * interval, 2)
                entries.append({
                    "time": t,
                    "end": round(t + interval, 2),
                    "chord": chord
                })

            result = {
                "song": song_name, "level": level,
                "key": grid[0] if grid else "",
                "source": "Chordify (screenshot, estimated timing)",
                "entries": entries,
            }

            if level:
                save_dir = os.path.join(os.path.dirname(__file__), "..", "data", "test_songs", level)
            else:
                save_dir = os.path.join(os.path.dirname(__file__), "..", "data", "test_songs")

            os.makedirs(save_dir, exist_ok=True)
            save_path = os.path.join(save_dir, f"{song_name}.lab")
            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            print(f"\n  ✓ 已儲存: {save_path}")
    else:
        print("\n  複製上方和弦序列，到 http://localhost:8800/benchmark 貼上即可")


if __name__ == "__main__":
    main()
