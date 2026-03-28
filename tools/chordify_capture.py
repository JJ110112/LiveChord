"""
Chordify Ground Truth 擷取工具
===============================
全自動：框選區域 → 自動點擊播放 → 自動擷取 → 播完自動停止儲存

使用方式:
1. 在瀏覽器開啟 Chordify 歌曲頁面（Chord overview 模式）
2. 執行此腳本: python chordify_capture.py
3. 首次使用會要求框選 3 個區域（之後自動記住）
4. 程式自動點擊播放、擷取、播完停止並儲存
5. ESC 可隨時手動中止
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
import pyautogui

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
    "finished": False,
    "records": [],
    "last_chord": None,
    "last_time": None,
    "start_perf_time": None,  # 內部高精度計時器起點
    "start_ocr_sec": None,    # OCR 讀到的起始時間(秒)
    "time_region": None,      # 時間顯示區域
    "chord_region": None,     # 和弦網格區域
    "play_btn_region": None,  # 播放按鈕區域
    "play_btn_ref": None,     # 播放中(||)的參考影像
    "interval": 0.1,          # 擷取間隔（縮短以提高精確度）
    "ref_chords": [],         # 預取參考和弦序列
    "ref_idx": 0,             # 參考序列的指標
    "last_box_center": None,  # (cx, cy) 高亮方塊的位置
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
# 螢幕擷取
# ---------------------------------------------------------------------------

def capture_region(region):
    x, y, w, h = region
    with mss.mss() as sct:
        monitor = {"left": x, "top": y, "width": w, "height": h}
        img = sct.grab(monitor)
        return Image.frombytes("RGB", (img.width, img.height), img.rgb)


# ---------------------------------------------------------------------------
# 播放按鈕狀態偵測（|| vs ▶）
# ---------------------------------------------------------------------------

def detect_play_button_state(img) -> str:
    """
    偵測播放按鈕目前是 || (playing) 還是 ▶ (stopped)。

    方法：分析按鈕中央區域的白色像素分佈。
    - || (暫停圖示)：中央有一條暗縫（兩條白色豎條之間的間隙）
    - ▶ (播放圖示)：中央是三角形的一部分（白色連續）

    回傳 "playing" 或 "stopped"
    """
    arr = np.array(img)
    h, w, _ = arr.shape

    # 轉灰階，只看白色部分（按鈕上的圖示是白色）
    gray = np.mean(arr, axis=2)

    # 取中央水平帶（按鈕中間 40% 的高度）
    y_start = int(h * 0.3)
    y_end = int(h * 0.7)
    center_band = gray[y_start:y_end, :]

    # 計算每一列的平均亮度
    col_brightness = np.mean(center_band, axis=0)

    # 找中央 1/3 區域
    cx_start = int(w * 0.33)
    cx_end = int(w * 0.66)
    center_cols = col_brightness[cx_start:cx_end]

    if len(center_cols) == 0:
        return "unknown"

    # || 的特徵：中央區域有明顯的亮度下降（兩條白色條之間的間隙）
    # ▶ 的特徵：中央區域亮度較均勻或遞減
    center_min = np.min(center_cols)
    center_max = np.max(center_cols)
    center_mean = np.mean(center_cols)

    # 計算中央的「凹陷度」— || 在正中間有暗縫
    mid = len(center_cols) // 2
    mid_zone = center_cols[max(0, mid-2):mid+3]
    side_left = center_cols[:max(1, mid-3)]
    side_right = center_cols[min(len(center_cols)-1, mid+3):]

    mid_val = np.mean(mid_zone) if len(mid_zone) > 0 else center_mean
    side_val = (np.mean(side_left) + np.mean(side_right)) / 2 if len(side_left) > 0 and len(side_right) > 0 else center_mean

    # || : 兩邊亮(白條)，中間暗(間隙) → side_val > mid_val
    # ▶ : 左邊亮，右邊暗 → 無明顯中間凹陷
    dip = side_val - mid_val

    if dip > 15:
        return "playing"   # || 圖示（有中間凹陷）
    else:
        return "stopped"   # ▶ 圖示（無凹陷）


def is_still_playing() -> bool:
    """檢查播放按鈕是否仍在播放狀態"""
    if not STATE["play_btn_region"]:
        return True
    img = capture_region(STATE["play_btn_region"])
    return detect_play_button_state(img) == "playing"


def click_play_button():
    """點擊播放按鈕"""
    x, y, w, h = STATE["play_btn_region"]
    cx = x + w // 2
    cy = y + h // 2
    pyautogui.click(cx, cy)
    time.sleep(0.5)


# ---------------------------------------------------------------------------
# 時間 OCR
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# 和弦偵測
# ---------------------------------------------------------------------------

def find_highlighted_chord(img):
    """偵測 Chordify 高亮和弦（深色背景的格子）"""
    reader = get_reader()
    arr = np.array(img)
    h, w, _ = arr.shape
    if h == 0 or w == 0:
        return None

    gray = np.mean(arr, axis=2)

    # 1. 縮小圖片以進行大面積模糊，濾掉細小的白色文字
    scale = 8
    small_w, small_h = max(1, w // scale), max(1, h // scale)
    img_small = img.resize((small_w, small_h), Image.BILINEAR)
    gray_small = np.mean(np.array(img_small), axis=2)

    # 找尋最暗的中心點
    min_val = np.min(gray_small)
    if min_val > 120:  # 如果圖中沒有明顯的大塊深色，則視為無高亮
        return None, None

    min_y_s, min_x_s = np.unravel_index(np.argmin(gray_small), gray_small.shape)
    cx = int(min_x_s * scale + scale / 2)
    cy = int(min_y_s * scale + scale / 2)
    
    # 防呆邊界
    cx = max(0, min(w - 1, cx))
    cy = max(0, min(h - 1, cy))

    # 2. 從中心點往外擴張，精準找到該高亮方塊的上下左右邊界
    top = cy
    while top > 0 and np.min(gray[top, max(0, cx-20):min(w, cx+20)]) < 100:
        top -= 1

    bottom = cy
    while bottom < h - 1 and np.min(gray[bottom, max(0, cx-20):min(w, cx+20)]) < 100:
        bottom += 1

    left = cx
    while left > 0 and np.min(gray[max(0, cy-10):min(h, cy+10), left]) < 100:
        left -= 1

    right = cx
    while right < w - 1 and np.min(gray[max(0, cy-10):min(h, cy+10), right]) < 100:
        right += 1

    margin = 5
    crop = img.crop((max(0, left - margin), max(0, top - margin), 
                     min(w, right + margin), min(h, bottom + margin)))

    crop = crop.resize((crop.width * 3, crop.height * 3), Image.LANCZOS)

    results = reader.readtext(np.array(crop),
                              allowlist='ABCDEFGabcdefgm#b0123456789/dimaugsusmaj',
                              paragraph=False)

    for _, text, conf in results:
        text = text.strip()
        if text and conf > 0.3 and any(c in text for c in 'ABCDEFG'):
            return _normalize_chord(text), (cx, cy)
    return None, (cx, cy)


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
        if x2 - x1 > 5 and y2 - y1 > 5:
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
    btn_check_counter = 0

    while STATE["running"]:
        if not STATE["capturing"]:
            time.sleep(0.1)
            continue

        try:
            # 擷取時間
            time_img = capture_region(STATE["time_region"])
            current_sec = ocr_time(time_img)

            # 每 8 次檢查一次播放按鈕（約每 2.4 秒）
            btn_check_counter += 1
            if btn_check_counter >= 8:
                btn_check_counter = 0
                if not is_still_playing():
                    print("\n\n  ⏹ 偵測到播放結束（▶ 圖示），自動停止擷取")
                    STATE["capturing"] = False
                    STATE["finished"] = True
                    continue

            # 偵測時間倒退（播完後時間可能重置）
            if current_sec is not None and STATE["last_time"] is not None:
                if current_sec < STATE["last_time"] - 5:
                    print("\n\n  ⏹ 偵測到時間重置，自動停止擷取")
                    STATE["capturing"] = False
                    STATE["finished"] = True
                    continue

            # 擷取和弦
            chord_img = capture_region(STATE["chord_region"])
            chord, box_center = find_highlighted_chord(chord_img)

            if current_sec is not None:
                STATE["last_time"] = current_sec
                # 記錄第一次辨識到的 OCR 時間作為基準點
                if STATE["start_ocr_sec"] is None:
                    STATE["start_ocr_sec"] = current_sec

            box_moved = False
            if box_center:
                if STATE["last_box_center"] is None:
                    box_moved = True
                else:
                    last_cx, last_cy = STATE["last_box_center"]
                    bcx, bcy = box_center
                    # 方格大幅移動代表進入下一個和弦區塊
                    if abs(bcx - last_cx) > 15 or abs(bcy - last_cy) > 15:
                        box_moved = True

            if box_moved:
                STATE["last_box_center"] = box_center

                # 如果有提供截圖當作字典庫，則完全採用字典庫的序列確保完美準確率
                if STATE["ref_chords"] and STATE["ref_idx"] < len(STATE["ref_chords"]):
                    chord = STATE["ref_chords"][STATE["ref_idx"]]
                    STATE["ref_idx"] += 1
                    is_new_record = True
                else:
                    is_new_record = (chord and chord != STATE["last_chord"])

                if chord and is_new_record:
                    # 使用精確時間戳：取得自從按下播放至今經過的秒數 (含小數點)
                    elapsed = time.perf_counter() - STATE["start_perf_time"]
                    
                    # 若尚未讀到 OCR 時間，暫時當作從 0 起算
                    base_time = STATE["start_ocr_sec"] if STATE["start_ocr_sec"] is not None else (current_sec or 0)
                    precise_time = base_time + elapsed
                    
                    STATE["records"].append((round(precise_time, 3), chord))
                    STATE["last_chord"] = chord
                    
                    m, s = divmod(precise_time, 60)
                    s_int = int(s)
                    ms = int((s - s_int) * 1000)
                    count = len(STATE['records'])
                    source_tag = "(PNG ref)" if STATE["ref_chords"] else ""
                    sys.stdout.write(f"\r    {int(m)}:{s_int:02d}.{ms:03d} → {chord:10s}  ({count} chords) {source_tag}   ")
                    sys.stdout.flush()

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


def load_reference_png(song_name: str, level: str):
    STATE["ref_chords"] = []
    STATE["ref_idx"] = 0
    png_path = TEST_SONGS_DIR / level / f"{song_name}.png" if level else TEST_SONGS_DIR / f"{song_name}.png"
    
    if png_path.exists():
        print(f"\n  🔍 發現參考圖檔: {png_path.name}")
        print("  正在預先解析全曲和弦以提高擷取成功率...")
        try:
            # 調用 screenshot 工具中的解析邏輯
            import chordify_screenshot
            analysis = chordify_screenshot.analyze_chordify_screenshot(str(png_path))
            ref_grid = chordify_screenshot.extract_chord_grid(analysis)
            if ref_grid:
                STATE["ref_chords"] = ref_grid
                print(f"  ✓ 成功提取 {len(ref_grid)} 個和弦！之後擷取將完全採用此 PNG 的序列進行時間綁定。")
            else:
                print("  ⚠ 參考圖檔未辨識出有效和弦")
        except Exception as e:
            print(f"  ⚠ 載入失敗: {e}")

# ---------------------------------------------------------------------------
# 主程式
# ---------------------------------------------------------------------------

def setup_regions():
    """設定或載入擷取區域"""
    cfg = load_config()
    saved = {
        "time": cfg.get("time_region"),
        "chord": cfg.get("chord_region"),
        "play_btn": cfg.get("play_btn_region"),
    }

    all_saved = all(saved.values())
    if all_saved:
        print(f"\n  已記住的擷取區域：")
        print(f"    播放按鈕: {tuple(saved['play_btn'])}")
        print(f"    時間顯示: {tuple(saved['time'])}")
        print(f"    和弦網格: {tuple(saved['chord'])}")
        reuse = input("  沿用？(y/n, 預設 y): ").strip().lower()
        if reuse != 'n':
            STATE["play_btn_region"] = tuple(saved["play_btn"])
            STATE["time_region"] = tuple(saved["time"])
            STATE["chord_region"] = tuple(saved["chord"])
            return

    # 步驟 1: 框選播放按鈕
    print("\n  步驟 1/3: 框選「播放按鈕」（圓形的 ▶/|| 按鈕）")
    input("  按 Enter 開始框選...")
    STATE["play_btn_region"] = select_region("框選播放按鈕 (▶/||)")
    if not STATE["play_btn_region"]:
        print("  取消"); sys.exit(1)
    print(f"  ✓ 播放按鈕: {STATE['play_btn_region']}")

    # 測試按鈕偵測
    btn_img = capture_region(STATE["play_btn_region"])
    btn_state = detect_play_button_state(btn_img)
    print(f"  目前按鈕狀態: {'|| 播放中' if btn_state == 'playing' else '▶ 已停止'}")

    # 步驟 2: 框選時間
    print("\n  步驟 2/3: 框選「時間顯示」（播放按鈕右邊的 00:00 數字）")
    input("  按 Enter 開始框選...")
    STATE["time_region"] = select_region("框選時間 (00:00)")
    if not STATE["time_region"]:
        print("  取消"); sys.exit(1)

    # 測試 OCR
    print("  測試 OCR...")
    test_time = ocr_time(capture_region(STATE["time_region"]))
    if test_time is not None:
        m, s = divmod(test_time, 60)
        print(f"  ✓ 時間: {m}:{s:02d}")
    else:
        print("  ⚠ 無法辨識，播放後再試")

    # 步驟 3: 框選和弦
    print("\n  步驟 3/3: 框選「和弦網格」（高亮和弦移動的那一行）")
    input("  按 Enter 開始框選...")
    STATE["chord_region"] = select_region("框選和弦區域")
    if not STATE["chord_region"]:
        print("  取消"); sys.exit(1)

    # 儲存
    cfg["play_btn_region"] = list(STATE["play_btn_region"])
    cfg["time_region"] = list(STATE["time_region"])
    cfg["chord_region"] = list(STATE["chord_region"])
    save_config(cfg)
    print("\n  ✓ 區域座標已記住")


def main():
    print("=" * 60)
    print("  Chordify Ground Truth 擷取工具（全自動）")
    print("=" * 60)

    song_name = input("\n  歌曲名稱: ").strip()
    if not song_name:
        print("  取消"); return

    level = input("  等級 (Lv1~Lv5, 可留空): ").strip()

    # 檢查已有 ground truth
    if level:
        existing = TEST_SONGS_DIR / level / f"{song_name}.lab"
    else:
        existing = TEST_SONGS_DIR / f"{song_name}.lab"

    if existing.is_file():
        data = json.loads(existing.read_text(encoding="utf-8"))
        n = len(data.get("entries", []))
        print(f"\n  ⚠ 已存在 ground truth ({n} 個和弦, 來源: {data.get('source', '?')})")
        if input("  重新擷取？(y/n): ").strip().lower() != 'y':
            print("  保留現有，結束"); return

    # 檢查是否提供參考圖檔
    load_reference_png(song_name, level)

    # 設定區域
    setup_regions()

    # 全自動流程
    print("\n" + "=" * 60)
    print("  全自動模式：點擊播放 → 擷取 → 播完自動儲存")
    print("  ESC 可隨時手動中止")
    print("=" * 60)

    # 啟動背景擷取線程
    capture_thread = threading.Thread(target=capture_loop, daemon=True)
    capture_thread.start()

    # ESC 監聽（背景）
    def on_esc():
        STATE["running"] = False
        STATE["capturing"] = False
    keyboard.add_hotkey("esc", on_esc)

    # 自動點擊播放
    print("\n  ▶ 點擊播放按鈕...")
    click_play_button()
    time.sleep(1)

    # 確認已開始播放
    if is_still_playing():
        print("  ✓ 偵測到 || 圖示，播放中")
    else:
        print("  ⚠ 未偵測到播放狀態，嘗試再次點擊...")
        click_play_button()
        time.sleep(1)
        if not is_still_playing():
            print("  ⚠ 仍無法確認播放，繼續擷取（可按 ESC 中止）")

    # 開始擷取
    STATE["capturing"] = True
    STATE["start_perf_time"] = time.perf_counter()
    STATE["start_ocr_sec"] = None
    print("  🎵 開始擷取和弦...\n")

    # 等待完成（播放結束或 ESC）
    while STATE["running"] and not STATE.get("finished"):
        time.sleep(0.5)

    STATE["running"] = False
    STATE["capturing"] = False

    print(f"\n\n  擷取結束，共 {len(STATE['records'])} 個和弦")

    if STATE["records"]:
        save_results(song_name, level)

        # 詢問是否繼續下一首
        next_song = input("\n  繼續下一首？輸入歌曲名稱（Enter 跳過）: ").strip()
        if next_song:
            # 重置狀態
            STATE.update({"capturing": False, "running": True, "finished": False,
                          "records": [], "last_chord": None, "last_time": None,
                          "start_perf_time": None, "start_ocr_sec": None,
                          "ref_chords": [], "ref_idx": 0, "last_box_center": None})
            # 遞迴呼叫（保留區域設定）
            main_continue(next_song, level)
    else:
        print("  無資料，未儲存")


def main_continue(song_name: str, level: str):
    """連續擷取下一首（區域設定不變）"""
    # 檢查已有
    if level:
        existing = TEST_SONGS_DIR / level / f"{song_name}.lab"
    else:
        existing = TEST_SONGS_DIR / f"{song_name}.lab"

    if existing.is_file():
        data = json.loads(existing.read_text(encoding="utf-8"))
        n = len(data.get("entries", []))
        print(f"\n  ⚠ 已存在 ({n} 個和弦)")
        if input("  重新擷取？(y/n): ").strip().lower() != 'y':
            return

    print(f"\n  準備擷取: {song_name}")
    print("  請在 Chordify 切換到該歌曲頁面，準備好後按 Enter")
    input("  按 Enter 開始...")

    load_reference_png(song_name, level)

    capture_thread = threading.Thread(target=capture_loop, daemon=True)
    capture_thread.start()

    print("  ▶ 點擊播放...")
    click_play_button()
    time.sleep(1)

    STATE["capturing"] = True
    STATE["start_perf_time"] = time.perf_counter()
    STATE["start_ocr_sec"] = None
    print("  🎵 擷取中...\n")

    while STATE["running"] and not STATE.get("finished"):
        time.sleep(0.5)

    STATE["running"] = False
    STATE["capturing"] = False

    print(f"\n  擷取結束，共 {len(STATE['records'])} 個和弦")
    if STATE["records"]:
        save_results(song_name, level)

        next_song = input("\n  繼續下一首？輸入名稱（Enter 跳過）: ").strip()
        if next_song:
            STATE.update({"capturing": False, "running": True, "finished": False,
                          "records": [], "last_chord": None, "last_time": None,
                          "start_perf_time": None, "start_ocr_sec": None,
                          "ref_chords": [], "ref_idx": 0, "last_box_center": None})
            main_continue(next_song, level)


if __name__ == "__main__":
    main()
