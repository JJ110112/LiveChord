"""
Chordify Ground Truth 擷取工具
===============================
在 Chordify 播放歌曲時，自動擷取螢幕上的時間戳和高亮和弦。

使用方式:
1. 在瀏覽器開啟 Chordify 歌曲頁面
2. 執行此腳本: python chordify_capture.py
3. 首次使用會要求框選「時間區域」和「和弦區域」（座標會記住）
4. 在 Chordify 按播放
5. 按 F6 開始擷取（偵測到 Well done 會自動停止）
6. 按 ESC 手動結束
"""

import sys
import os
import json
import time
import threading
from pathlib import Path

import mss
import numpy as np
from PIL import Image
import easyocr
import keyboard

# ---------------------------------------------------------------------------
# 路徑
# ---------------------------------------------------------------------------
TOOLS_DIR = Path(__file__).parent
PROJECT_DIR = TOOLS_DIR.parent
TEST_SONGS_DIR = PROJECT_DIR / "data" / "test_songs"
CONFIG_FILE = TOOLS_DIR / "capture_config.json"

# ---------------------------------------------------------------------------
# 全域狀態
# ---------------------------------------------------------------------------
STATE = {
    "capturing": False,
    "running": True,
    "records": [],
    "last_chord": None,
    "last_time": None,
    "time_region": None,
    "chord_region": None,
    "interval": 0.3,
}

_reader = None

def get_reader():
    global _reader
    if _reader is None:
        print("  載入 OCR 模型（首次較慢）...")
        _reader = easyocr.Reader(['en'], gpu=False, verbose=False)
    return _reader


# ---------------------------------------------------------------------------
# 設定檔（記住區域座標）
# ---------------------------------------------------------------------------

def load_config() -> dict:
    if CONFIG_FILE.is_file():
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    return {}

def save_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# 螢幕擷取 + OCR
# ---------------------------------------------------------------------------

def capture_region(region):
    x, y, w, h = region
    with mss.mss() as sct:
        monitor = {"left": x, "top": y, "width": w, "height": h}
        img = sct.grab(monitor)
        return Image.frombytes("RGB", (img.width, img.height), img.rgb)


def ocr_time(img):
    """OCR 辨識時間文字（如 01:23）"""
    reader = get_reader()
    img = img.resize((img.width * 3, img.height * 3), Image.LANCZOS)
    arr = np.array(img)
    results = reader.readtext(arr, allowlist='0123456789:', paragraph=False)

    for _, text, conf in results:
        text = text.strip()
        if ':' in text and conf > 0.3:
            parts = text.split(':')
            try:
                if len(parts) == 2:
                    return int(parts[0]) * 60 + int(parts[1])
            except ValueError:
                continue
    return None


def detect_well_done(img) -> bool:
    """偵測畫面是否出現 'Well done' 對話框（歌曲播完）"""
    reader = get_reader()
    arr = np.array(img)
    results = reader.readtext(arr, paragraph=False)
    for _, text, conf in results:
        lower = text.strip().lower()
        if 'well done' in lower or 'play again' in lower:
            return True
    return False


def find_highlighted_chord(img):
    """偵測 Chordify 高亮和弦（深色背景的格子）"""
    reader = get_reader()
    arr = np.array(img)
    h, w, _ = arr.shape
    gray = np.mean(arr, axis=2)

    col_width = max(w // 20, 30)
    darkest_x = 0
    darkest_val = 255

    for x in range(0, w - col_width, col_width // 2):
        region = gray[:, x:x+col_width]
        mean_val = np.mean(region)
        if mean_val < darkest_val:
            darkest_val = mean_val
            darkest_x = x

    overall_mean = np.mean(gray)
    if darkest_val > overall_mean - 15:
        return None

    margin = col_width // 2
    crop_x = max(0, darkest_x - margin)
    crop_w = min(w, darkest_x + col_width + margin)
    crop = img.crop((crop_x, 0, crop_w, h))
    crop = crop.resize((crop.width * 3, crop.height * 3), Image.LANCZOS)

    results = reader.readtext(np.array(crop),
                              allowlist='ABCDEFGabcdefgm#b0123456789/dimaugsusmaj',
                              paragraph=False)

    for _, text, conf in results:
        text = text.strip()
        if text and conf > 0.3 and any(c in text for c in 'ABCDEFG'):
            return _normalize_chord(text)
    return None


def _normalize_chord(raw: str) -> str:
    raw = raw.strip()
    replacements = {"min": "m", "MIN": "m", "Maj": "maj", "MAJ": "maj",
                    "DIM": "dim", "AUG": "aug", "SUS": "sus"}
    for old, new in replacements.items():
        raw = raw.replace(old, new)
    if raw and raw[0].islower():
        raw = raw[0].upper() + raw[1:]
    return raw


# ---------------------------------------------------------------------------
# 區域選擇（tkinter）
# ---------------------------------------------------------------------------

def select_region(title="框選區域"):
    try:
        import tkinter as tk
        if os.name == 'nt':
            import ctypes
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                try:
                    ctypes.windll.user32.SetProcessDPIAware()
                except Exception:
                    pass
    except ImportError:
        return manual_region_input(title)

    result = [None]
    root = tk.Tk()
    root.attributes('-fullscreen', True)
    root.attributes('-alpha', 0.3)
    root.attributes('-topmost', True)
    root.configure(bg='black')

    canvas = tk.Canvas(root, cursor="cross", bg="black", highlightthickness=0)
    canvas.pack(fill=tk.BOTH, expand=True)

    start_x = start_y = 0
    rect = None

    label = tk.Label(root, text=f"  {title}：按住左鍵框選，放開確認  ",
                     font=("Segoe UI", 16), fg="white", bg="#e94560")
    label.place(relx=0.5, rely=0.02, anchor="n")

    def on_press(e):
        nonlocal start_x, start_y, rect
        start_x, start_y = e.x, e.y
        if rect: canvas.delete(rect)
        rect = canvas.create_rectangle(start_x, start_y, start_x, start_y,
                                       outline="red", width=3)
    def on_drag(e):
        if rect: canvas.coords(rect, start_x, start_y, e.x, e.y)

    def on_release(e):
        x1, y1 = min(start_x, e.x), min(start_y, e.y)
        x2, y2 = max(start_x, e.x), max(start_y, e.y)
        if x2 - x1 > 10 and y2 - y1 > 10:
            result[0] = (x1, y1, x2 - x1, y2 - y1)
        root.destroy()

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)
    root.bind("<Escape>", lambda e: root.destroy())
    root.mainloop()
    return result[0]


def manual_region_input(title):
    print(f"  {title}")
    try:
        x, y = int(input("    x: ")), int(input("    y: "))
        w, h = int(input("    w: ")), int(input("    h: "))
        return (x, y, w, h)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# 擷取循環
# ---------------------------------------------------------------------------

def capture_loop():
    well_done_checks = 0

    while STATE["running"]:
        if not STATE["capturing"]:
            time.sleep(0.1)
            continue

        try:
            # 擷取時間
            time_img = capture_region(STATE["time_region"])
            current_sec = ocr_time(time_img)

            # 每 10 次檢查一次是否播完（避免頻繁 OCR 整個和弦區域找 well done）
            well_done_checks += 1
            if well_done_checks >= 10:
                well_done_checks = 0
                chord_img = capture_region(STATE["chord_region"])
                if detect_well_done(chord_img):
                    print("\n  🎵 偵測到 Well done！歌曲播完，自動停止擷取")
                    STATE["capturing"] = False
                    STATE["finished"] = True
                    continue

            # 偵測時間倒退（播完後時間會重置）
            if current_sec is not None and STATE["last_time"] is not None:
                if current_sec < STATE["last_time"] - 5:
                    print("\n  🎵 偵測到時間重置，歌曲可能已播完")
                    STATE["capturing"] = False
                    STATE["finished"] = True
                    continue

            # 擷取和弦
            chord_img = capture_region(STATE["chord_region"])
            chord = find_highlighted_chord(chord_img)

            if current_sec is not None:
                STATE["last_time"] = current_sec

            if current_sec is not None and chord and chord != STATE["last_chord"]:
                STATE["records"].append((current_sec, chord))
                STATE["last_chord"] = chord
                m, s = divmod(current_sec, 60)
                print(f"    {m}:{s:02d} → {chord}  ({len(STATE['records'])})", end="\r")

        except Exception:
            pass

        time.sleep(STATE["interval"])


# ---------------------------------------------------------------------------
# 儲存
# ---------------------------------------------------------------------------

def save_results(song_name: str, level: str = ""):
    records = STATE["records"]
    if not records:
        print("  無擷取資料")
        return None

    seen = {}
    for sec, chord in records:
        if sec not in seen:
            seen[sec] = chord

    sorted_records = sorted(seen.items())
    entries = []
    for i, (sec, chord) in enumerate(sorted_records):
        end = sorted_records[i + 1][0] if i + 1 < len(sorted_records) else sec + 4.0
        entries.append({"time": sec, "end": end, "chord": chord})

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
        "song": song_name, "level": level,
        "key": key, "source": "Chordify (screen capture)",
        "entries": entries,
    }

    if level:
        save_dir = TEST_SONGS_DIR / level
    else:
        save_dir = TEST_SONGS_DIR

    save_dir.mkdir(parents=True, exist_ok=True)
    save_path = save_dir / f"{song_name}.lab"
    save_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n  ✓ 已儲存: {save_path}")
    print(f"    和弦數: {len(entries)}, Key: {key}")
    return str(save_path)


# ---------------------------------------------------------------------------
# 主程式
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("  Chordify Ground Truth 擷取工具")
    print("=" * 60)

    # 輸入歌曲資訊
    song_name = input("\n  歌曲名稱: ").strip()
    if not song_name:
        print("  取消")
        return

    level = input("  等級 (Lv1~Lv5, 可留空): ").strip()

    # 檢查是否已有 ground truth
    if level:
        existing = TEST_SONGS_DIR / level / f"{song_name}.lab"
    else:
        existing = TEST_SONGS_DIR / f"{song_name}.lab"

    if existing.is_file():
        data = json.loads(existing.read_text(encoding="utf-8"))
        n = len(data.get("entries", []))
        print(f"\n  ⚠ 已存在 ground truth ({n} 個和弦, 來源: {data.get('source', '?')})")
        redo = input("  要重新擷取嗎？(y/n): ").strip().lower()
        if redo != 'y':
            print("  保留現有檔案，結束")
            return

    # 載入設定（記住上次的區域座標）
    cfg = load_config()
    saved_time_region = cfg.get("time_region")
    saved_chord_region = cfg.get("chord_region")

    if saved_time_region and saved_chord_region:
        print(f"\n  已記住上次的擷取區域：")
        print(f"    時間: {tuple(saved_time_region)}")
        print(f"    和弦: {tuple(saved_chord_region)}")
        reuse = input("  沿用上次的區域？(y/n, 預設 y): ").strip().lower()
        if reuse != 'n':
            STATE["time_region"] = tuple(saved_time_region)
            STATE["chord_region"] = tuple(saved_chord_region)
        else:
            saved_time_region = None
            saved_chord_region = None

    # 框選時間區域
    if not STATE["time_region"]:
        print("\n  步驟 1: 請框選 Chordify 的「時間顯示」區域")
        print("         （播放按鈕旁邊的 00:00 ~ 03:53 數字）")
        input("  按 Enter 開始框選...")
        STATE["time_region"] = select_region("框選時間區域 (00:00)")
        if not STATE["time_region"]:
            print("  未選擇，取消")
            return

    # 測試 OCR
    print("  測試時間 OCR...")
    test_img = capture_region(STATE["time_region"])
    test_time = ocr_time(test_img)
    if test_time is not None:
        m, s = divmod(test_time, 60)
        print(f"  ✓ 當前時間: {m}:{s:02d}")
    else:
        print("  ⚠ 無法辨識時間，播放後再試")

    # 框選和弦區域
    if not STATE["chord_region"]:
        print("\n  步驟 2: 請框選 Chordify 的「和弦格子」區域")
        print("         （只要框住目前高亮和弦會移動的那一行即可）")
        input("  按 Enter 開始框選...")
        STATE["chord_region"] = select_region("框選和弦區域")
        if not STATE["chord_region"]:
            print("  未選擇，取消")
            return

    # 儲存區域座標
    cfg["time_region"] = list(STATE["time_region"])
    cfg["chord_region"] = list(STATE["chord_region"])
    save_config(cfg)
    print("  ✓ 區域座標已記住（下次可直接使用）")

    # 開始擷取
    STATE["finished"] = False
    print("\n" + "=" * 60)
    print("  準備就緒！")
    print("    F6  = 開始/暫停擷取")
    print("    ESC = 手動結束並儲存")
    print("    歌曲播完（Well done）會自動停止")
    print()
    print("  請在 Chordify 按播放，然後按 F6 開始")
    print("=" * 60)

    capture_thread = threading.Thread(target=capture_loop, daemon=True)
    capture_thread.start()

    def on_f6():
        if STATE.get("finished"):
            print("\n  已完成，按 ESC 儲存")
            return
        STATE["capturing"] = not STATE["capturing"]
        status = "擷取中..." if STATE["capturing"] else "暫停"
        print(f"\n  [{status}] 已擷取 {len(STATE['records'])} 個和弦")

    keyboard.add_hotkey("F6", on_f6)
    keyboard.wait("esc")

    STATE["running"] = False
    STATE["capturing"] = False

    print(f"\n  擷取結束，共 {len(STATE['records'])} 個和弦")

    if STATE["records"]:
        save_results(song_name, level)
    else:
        print("  無資料，未儲存")


if __name__ == "__main__":
    main()
