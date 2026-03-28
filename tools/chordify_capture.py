"""
Chordify Ground Truth 擷取工具
===============================
在 Chordify 播放歌曲時，自動擷取螢幕上的時間戳和高亮和弦。

使用方式:
1. 在瀏覽器開啟 Chordify 歌曲頁面
2. 執行此腳本: python chordify_capture.py
3. 按照提示框選「時間區域」和「和弦區域」
4. 在 Chordify 按播放
5. 按 F6 開始/停止擷取
6. 按 ESC 結束並儲存
"""

import sys
import os
import json
import time
import threading
from datetime import datetime

import mss
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import easyocr
import keyboard  # pip install keyboard

# ---------------------------------------------------------------------------
# 全域狀態
# ---------------------------------------------------------------------------
STATE = {
    "capturing": False,
    "running": True,
    "records": [],        # [(time_sec, chord_str), ...]
    "last_chord": None,
    "time_region": None,  # (x, y, w, h)
    "chord_region": None, # (x, y, w, h)
    "interval": 0.3,      # 擷取間隔（秒）
}

# EasyOCR reader（延遲載入）
_reader = None

def get_reader():
    global _reader
    if _reader is None:
        print("  載入 OCR 模型（首次較慢）...")
        _reader = easyocr.Reader(['en'], gpu=False, verbose=False)
    return _reader


# ---------------------------------------------------------------------------
# 螢幕擷取
# ---------------------------------------------------------------------------

def capture_region(region):
    """擷取螢幕指定區域"""
    x, y, w, h = region
    with mss.mss() as sct:
        monitor = {"left": x, "top": y, "width": w, "height": h}
        img = sct.grab(monitor)
        return Image.frombytes("RGB", (img.width, img.height), img.rgb)


def ocr_time(img):
    """OCR 辨識時間文字（如 01:23）"""
    reader = get_reader()
    # 預處理：放大 + 高對比
    img = img.resize((img.width * 3, img.height * 3), Image.LANCZOS)
    arr = np.array(img)
    # 灰階 → 二值化
    gray = np.mean(arr, axis=2)
    # Chordify 的時間文字是深色背景上的白字或淺色背景上的黑字
    # 嘗試兩種模式
    results = reader.readtext(arr, allowlist='0123456789:', paragraph=False)

    for _, text, conf in results:
        text = text.strip()
        if ':' in text and conf > 0.3:
            # 解析 mm:ss
            parts = text.split(':')
            try:
                if len(parts) == 2:
                    return int(parts[0]) * 60 + int(parts[1])
            except ValueError:
                continue
    return None


def find_highlighted_chord(img):
    """
    偵測 Chordify 高亮和弦。
    Chordify 用深色背景標示當前和弦，其他是淺灰色。
    """
    reader = get_reader()
    arr = np.array(img)

    # 策略：找到最暗的區塊（高亮和弦的背景色）
    # Chordify 高亮是黑色/深灰背景
    h, w, _ = arr.shape

    # 將圖片分成格子，找最暗的格子
    gray = np.mean(arr, axis=2)

    # 掃描列（和弦通常在特定行）
    # 先找整張圖的暗區域
    col_width = max(w // 20, 30)  # 估計每個和弦格子寬度

    darkest_x = 0
    darkest_val = 255

    for x in range(0, w - col_width, col_width // 2):
        region = gray[:, x:x+col_width]
        mean_val = np.mean(region)
        if mean_val < darkest_val:
            darkest_val = mean_val
            darkest_x = x

    # 如果最暗區域確實比平均暗很多（是高亮）
    overall_mean = np.mean(gray)
    if darkest_val > overall_mean - 15:
        # 沒有明顯高亮，可能沒在播放
        return None

    # 裁切高亮區域，OCR 辨識和弦名稱
    margin = col_width // 2
    crop_x = max(0, darkest_x - margin)
    crop_w = min(w, darkest_x + col_width + margin)
    crop = img.crop((crop_x, 0, crop_w, h))

    # 放大 + OCR
    crop = crop.resize((crop.width * 3, crop.height * 3), Image.LANCZOS)
    results = reader.readtext(np.array(crop),
                              allowlist='ABCDEFGabcdefgm#b0123456789/dim augsusmaj',
                              paragraph=False)

    for _, text, conf in results:
        text = text.strip()
        if text and conf > 0.3 and any(c in text for c in 'ABCDEFG'):
            return _normalize_chord(text)

    return None


def _normalize_chord(raw: str) -> str:
    """正規化 OCR 辨識到的和弦名稱"""
    raw = raw.strip()
    # 常見 OCR 錯誤修正
    replacements = {
        "Cm": "Cm", "cm": "Cm",
        "#": "#", "b": "b",
        "min": "m", "MIN": "m",
        "Maj": "maj", "MAJ": "maj",
        "DIM": "dim", "AUG": "aug",
        "SUS": "sus",
    }
    for old, new in replacements.items():
        raw = raw.replace(old, new)

    # 確保首字母大寫
    if raw and raw[0].islower():
        raw = raw[0].upper() + raw[1:]

    return raw


# ---------------------------------------------------------------------------
# 區域選擇工具（用 tkinter）
# ---------------------------------------------------------------------------

def select_region(title="框選區域"):
    """讓使用者在螢幕上框選一個矩形區域"""
    try:
        import tkinter as tk
    except ImportError:
        print("  需要 tkinter，請手動輸入座標")
        return manual_region_input(title)

    result = [None]

    root = tk.Tk()
    root.attributes('-fullscreen', True)
    root.attributes('-alpha', 0.3)
    root.attributes('-topmost', True)
    root.configure(bg='black')
    root.title(title)

    canvas = tk.Canvas(root, cursor="cross", bg="black", highlightthickness=0)
    canvas.pack(fill=tk.BOTH, expand=True)

    start_x = start_y = 0
    rect = None

    label = tk.Label(root, text=f"  {title}：按住滑鼠左鍵框選區域，放開確認  ",
                     font=("Segoe UI", 16), fg="white", bg="#e94560")
    label.place(relx=0.5, rely=0.02, anchor="n")

    def on_press(e):
        nonlocal start_x, start_y, rect
        start_x, start_y = e.x, e.y
        if rect: canvas.delete(rect)
        rect = canvas.create_rectangle(start_x, start_y, start_x, start_y,
                                       outline="red", width=3)

    def on_drag(e):
        nonlocal rect
        if rect:
            canvas.coords(rect, start_x, start_y, e.x, e.y)

    def on_release(e):
        x1, y1 = min(start_x, e.x), min(start_y, e.y)
        x2, y2 = max(start_x, e.x), max(start_y, e.y)
        w, h = x2 - x1, y2 - y1
        if w > 10 and h > 10:
            result[0] = (x1, y1, w, h)
        root.destroy()

    def on_escape(e):
        root.destroy()

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)
    root.bind("<Escape>", on_escape)

    root.mainloop()
    return result[0]


def manual_region_input(title):
    """手動輸入座標"""
    print(f"  {title}")
    try:
        x = int(input("    x: "))
        y = int(input("    y: "))
        w = int(input("    w: "))
        h = int(input("    h: "))
        return (x, y, w, h)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# 擷取循環
# ---------------------------------------------------------------------------

def capture_loop():
    """背景擷取循環"""
    while STATE["running"]:
        if not STATE["capturing"]:
            time.sleep(0.1)
            continue

        try:
            # 擷取時間
            time_img = capture_region(STATE["time_region"])
            current_sec = ocr_time(time_img)

            # 擷取和弦
            chord_img = capture_region(STATE["chord_region"])
            chord = find_highlighted_chord(chord_img)

            if current_sec is not None and chord and chord != STATE["last_chord"]:
                STATE["records"].append((current_sec, chord))
                STATE["last_chord"] = chord
                print(f"    {current_sec // 60}:{current_sec % 60:02d} → {chord}")

        except Exception as e:
            pass  # 忽略偶發擷取錯誤

        time.sleep(STATE["interval"])


# ---------------------------------------------------------------------------
# 儲存結果
# ---------------------------------------------------------------------------

def save_results(song_name: str, level: str = ""):
    """將擷取結果儲存為 .lab 檔案"""
    records = STATE["records"]
    if not records:
        print("  無擷取資料")
        return None

    # 去重 + 排序
    seen = {}
    for sec, chord in records:
        if sec not in seen:
            seen[sec] = chord

    sorted_records = sorted(seen.items())

    # 建立 entries
    entries = []
    for i, (sec, chord) in enumerate(sorted_records):
        end = sorted_records[i + 1][0] if i + 1 < len(sorted_records) else sec + 4.0
        entries.append({"time": sec, "end": end, "chord": chord})

    # 推導 key（最常出現的和弦根音）
    from collections import Counter
    roots = []
    for _, chord in sorted_records:
        if chord and chord[0] in "ABCDEFG":
            root = chord[0]
            if len(chord) > 1 and chord[1] in "#b":
                root += chord[1]
            roots.append(root)
    key = Counter(roots).most_common(1)[0][0] if roots else ""

    result = {
        "song": song_name,
        "level": level,
        "key": key,
        "source": "Chordify (screen capture)",
        "entries": entries,
    }

    # 儲存路徑
    if level:
        save_dir = os.path.join(os.path.dirname(__file__), "..", "data", "test_songs", level)
    else:
        save_dir = os.path.join(os.path.dirname(__file__), "..", "data", "test_songs")

    os.makedirs(save_dir, exist_ok=True)
    save_path = os.path.join(save_dir, f"{song_name}.lab")

    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n  ✓ 已儲存: {save_path}")
    print(f"    和弦數: {len(entries)}, Key: {key}")

    return save_path


# ---------------------------------------------------------------------------
# 主程式
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("  Chordify Ground Truth 擷取工具")
    print("=" * 60)
    print()

    # 輸入歌曲資訊
    song_name = input("  歌曲名稱: ").strip()
    if not song_name:
        print("  取消")
        return

    level = input("  等級 (Lv1~Lv5, 可留空): ").strip()

    # 步驟 1: 框選時間區域
    print("\n  步驟 1: 請框選 Chordify 的「時間顯示」區域")
    print("         （播放按鈕旁邊的 00:00 數字）")
    input("  按 Enter 開始框選...")

    STATE["time_region"] = select_region("框選時間區域 (00:00)")
    if not STATE["time_region"]:
        print("  未選擇區域，取消")
        return
    print(f"  ✓ 時間區域: {STATE['time_region']}")

    # 測試 OCR
    print("  測試 OCR...")
    test_img = capture_region(STATE["time_region"])
    test_time = ocr_time(test_img)
    print(f"  當前時間: {test_time}s" if test_time is not None else "  ⚠ 無法辨識時間，請調整區域")

    # 步驟 2: 框選和弦區域
    print("\n  步驟 2: 請框選 Chordify 的「和弦格子」區域")
    print("         （包含所有和弦格子的範圍，高亮和弦會在此區域內移動）")
    input("  按 Enter 開始框選...")

    STATE["chord_region"] = select_region("框選和弦區域（整個和弦網格）")
    if not STATE["chord_region"]:
        print("  未選擇區域，取消")
        return
    print(f"  ✓ 和弦區域: {STATE['chord_region']}")

    # 步驟 3: 開始擷取
    print("\n" + "=" * 60)
    print("  準備就緒！操作說明：")
    print("    F6  = 開始/暫停 擷取")
    print("    ESC = 結束並儲存")
    print()
    print("  請在 Chordify 按播放，然後按 F6 開始擷取")
    print("=" * 60)

    # 啟動背景擷取線程
    capture_thread = threading.Thread(target=capture_loop, daemon=True)
    capture_thread.start()

    # 鍵盤監聽
    def on_f6():
        STATE["capturing"] = not STATE["capturing"]
        status = "擷取中..." if STATE["capturing"] else "暫停"
        print(f"\n  [{status}] 已擷取 {len(STATE['records'])} 個和弦")

    keyboard.add_hotkey("F6", on_f6)

    # 等待 ESC
    print("  等待中...")
    keyboard.wait("esc")

    STATE["running"] = False
    STATE["capturing"] = False

    print(f"\n  擷取結束，共 {len(STATE['records'])} 個和弦")

    # 儲存
    if STATE["records"]:
        save_results(song_name, level)
    else:
        print("  無資料，未儲存")


if __name__ == "__main__":
    main()
