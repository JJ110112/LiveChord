"""
Chordify Ground Truth 擷取工具 — GUI 版
========================================
全自動 GUI：
1. 設定/顯示 play按鈕、時間、和弦區域座標
2. 選擇歌曲檔案 → 自動對應 test_songs 等級
3. Chordify 播放時分段擷取截圖 → 拼接為完整 .png
4. 同時 OCR 辨識和弦序列 → 存為 .txt 對照檔
5. 偵測 play/pause 圖示切換判斷播放狀態
6. 下方即時 log 顯示進度

用法: python chordify_gui.py
"""

import os
import sys
import json
import time
import threading
from pathlib import Path
from collections import Counter

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import mss
import cv2
import numpy as np
from PIL import Image, ImageTk
import easyocr

# ---------------------------------------------------------------------------
# 路徑
# ---------------------------------------------------------------------------
TOOLS_DIR = Path(__file__).parent
PROJECT_DIR = TOOLS_DIR.parent
TEST_SONGS_DIR = PROJECT_DIR / "data" / "test_songs"
CONFIG_FILE = TOOLS_DIR / "capture_config.json"
PLAY_ICON = TOOLS_DIR / "play.png"
PAUSE_ICON = TOOLS_DIR / "pause.png"
LEVELS = ["Lv1", "Lv2", "Lv3", "Lv4", "Lv5"]

# ---------------------------------------------------------------------------
# Template matching 用的 play/pause 圖示
# ---------------------------------------------------------------------------
_play_tmpl = None
_pause_tmpl = None


def _load_templates():
    global _play_tmpl, _pause_tmpl
    if PLAY_ICON.exists():
        img = cv2.imread(str(PLAY_ICON), cv2.IMREAD_COLOR)
        if img is not None:
            _play_tmpl = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    if PAUSE_ICON.exists():
        img = cv2.imread(str(PAUSE_ICON), cv2.IMREAD_COLOR)
        if img is not None:
            _pause_tmpl = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


# ---------------------------------------------------------------------------
# OCR
# ---------------------------------------------------------------------------
_reader = None


def get_reader():
    global _reader
    if _reader is None:
        _reader = easyocr.Reader(['en'], gpu=False, verbose=False)
    return _reader


# ---------------------------------------------------------------------------
# 螢幕擷取
# ---------------------------------------------------------------------------

def capture_region(region):
    x, y, w, h = region
    with mss.mss() as sct:
        monitor = {"left": x, "top": y, "width": w, "height": h}
        img = sct.grab(monitor)
        return Image.frombytes("RGB", (img.width, img.height), img.rgb)


def pil_to_cv(img):
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


def detect_button_state(img, region) -> str:
    """用 template matching 偵測 play(▶) 或 pause(||)"""
    if _play_tmpl is None or _pause_tmpl is None:
        return "unknown"

    gray = cv2.cvtColor(pil_to_cv(img), cv2.COLOR_BGR2GRAY)

    # 縮放模板到與區域相近的大小
    rh = region[3]
    scores = {}
    for name, tmpl in [("play", _play_tmpl), ("pause", _pause_tmpl)]:
        # 多尺度匹配
        best = -1
        for scale in [0.6, 0.8, 1.0, 1.2, 1.5]:
            tw = int(tmpl.shape[1] * scale * rh / tmpl.shape[0])
            th = int(rh * scale)
            if tw < 10 or th < 10 or tw > gray.shape[1] or th > gray.shape[0]:
                continue
            resized = cv2.resize(tmpl, (tw, th))
            result = cv2.matchTemplate(gray, resized, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(result)
            best = max(best, max_val)
        scores[name] = best

    if scores.get("pause", 0) > scores.get("play", 0) and scores["pause"] > 0.3:
        return "playing"   # 顯示 || → 正在播放
    elif scores.get("play", 0) > 0.3:
        return "stopped"   # 顯示 ▶ → 已停止
    return "unknown"


def ocr_time(img):
    reader = get_reader()
    img3x = img.resize((img.width * 3, img.height * 3), Image.LANCZOS)
    arr = np.array(img3x)
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


def _find_dark_block_center(img) -> tuple:
    """
    快速找到高亮方塊的中心座標（純像素運算，不做 OCR）。
    用於高頻迴圈中追蹤方塊移動。約 2-5ms。
    """
    arr = np.array(img)
    h, w, _ = arr.shape
    if h == 0 or w == 0:
        return None

    # 縮小 8 倍做快速掃描
    scale = 8
    small_w, small_h = max(1, w // scale), max(1, h // scale)
    small = img.resize((small_w, small_h), Image.BILINEAR)
    gray_small = np.mean(np.array(small), axis=2)

    min_val = np.min(gray_small)
    if min_val > 120:
        return None

    min_y, min_x = np.unravel_index(np.argmin(gray_small), gray_small.shape)
    cx = int(min_x * scale + scale / 2)
    cy = int(min_y * scale + scale / 2)
    return (max(0, min(w - 1, cx)), max(0, min(h - 1, cy)))


def find_highlighted_chord(img):
    """回傳 (chord_name, (cx, cy)) 或 (None, None)"""
    reader = get_reader()
    arr = np.array(img)
    h, w, _ = arr.shape
    if h == 0 or w == 0:
        return None, None

    gray = np.mean(arr, axis=2)
    scale = 8
    small_w, small_h = max(1, w // scale), max(1, h // scale)
    img_small = img.resize((small_w, small_h), Image.BILINEAR)
    gray_small = np.mean(np.array(img_small), axis=2)

    min_val = np.min(gray_small)
    if min_val > 120:
        return None, None

    min_y_s, min_x_s = np.unravel_index(np.argmin(gray_small), gray_small.shape)
    cx = int(min_x_s * scale + scale / 2)
    cy = int(min_y_s * scale + scale / 2)
    cx = max(0, min(w - 1, cx))
    cy = max(0, min(h - 1, cy))

    # 擴張找邊界
    top = cy
    while top > 0 and np.min(gray[top, max(0, cx - 20):min(w, cx + 20)]) < 100:
        top -= 1
    bottom = cy
    while bottom < h - 1 and np.min(gray[bottom, max(0, cx - 20):min(w, cx + 20)]) < 100:
        bottom += 1
    left = cx
    while left > 0 and np.min(gray[max(0, cy - 10):min(h, cy + 10), left]) < 100:
        left -= 1
    right = cx
    while right < w - 1 and np.min(gray[max(0, cy - 10):min(h, cy + 10), right]) < 100:
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
            return _fix_chord_ocr(text), (cx, cy)
    return None, (cx, cy)


# Chordify OCR 修正表（EasyOCR 常見誤讀）
_OCR_FIX = {
    # D/A 的各種誤讀
    "DA": "D/A", "DIA": "D/A", "DJA": "D/A", "D1A": "D/A", "DlA": "D/A",
    # D/F# 的各種誤讀
    "DFA": "D/F#", "DIF:": "D/F#", "DIF#": "D/F#", "DF:": "D/F#", "DIF;": "D/F#",
    # E/A, E/G#, A/E
    "EJA": "E/A", "EIA": "E/A", "E1A": "E/A",
    "AE": "A/E", "AIE": "A/E", "AJE": "A/E",
    "E/G:": "E/G#", "E/G;": "E/G#", "E/Ga": "E/G#", "EIG:": "E/G#",
    # F#m / F#m7
    "Fim": "F#m", "Frm": "F#m", "Fzm": "F#m", "Fsm": "F#m",
    "Fim7": "F#m7", "Frm7": "F#m7", "Fzm7": "F#m7", "Frm?": "F#m7",
    'Frm"': "F#m7", "Fim?": "F#m7", "Fzm?": "F#m7",
    # B7/D#
    "B7/D:": "B7/D#", "B7/D;": "B7/D#", "B7 /D:": "B7/D#",
    "B7/Di": "B7/D#", "B?/D:": "B7/D#", "B? /D:": "B7/D#",
    # Bm7/E
    "Bmi/E": "Bm7/E", "Bme/E": "Bm7/E", 'Bm"/E': "Bm7/E",
    "Bm?/E": "Bm7/E", "Bm'/E": "Bm7/E", 'Bm" /E': "Bm7/E",
    "Bm7 /E": "Bm7/E",
    # C#7
    "C;7": "C#7", "Cz7": "C#7", "C:7": "C#7", "C47": "C#7",
    # Bm7, Bm
    "Bm?": "Bm7", 'Bm"': "Bm7", "Bmz": "Bm7",
    # E7
    "E?": "E7",
    # F → F#m (Chordify 的 F#m 常被截成 F)
    "F": "F#m",
}


def _fix_chord_ocr(raw: str) -> str:
    """修正 OCR 誤讀"""
    raw = raw.strip().rstrip(':;.,?')

    # 精確匹配修正表
    if raw in _OCR_FIX:
        return _OCR_FIX[raw]

    # 通用替換
    raw = raw.replace("min", "m").replace("MIN", "m")
    raw = raw.replace("Maj", "maj").replace("MAJ", "maj")
    if raw and raw[0].islower():
        raw = raw[0].upper() + raw[1:]

    # 再查一次
    if raw in _OCR_FIX:
        return _OCR_FIX[raw]

    return raw


# ---------------------------------------------------------------------------
# 設定檔
# ---------------------------------------------------------------------------

def load_config() -> dict:
    if CONFIG_FILE.is_file():
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    return {}


def save_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class ChordifyCapture(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Chordify Ground Truth 擷取工具")
        self.geometry("700x750")
        self.configure(bg="#1a1a2e")
        self.resizable(True, True)

        # DPI awareness
        if os.name == 'nt':
            import ctypes
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                pass

        _load_templates()

        self.cfg = load_config()
        self.regions = {
            "play_btn": self.cfg.get("play_btn_region"),
            "time": self.cfg.get("time_region"),
            "duration": self.cfg.get("duration_region"),
            "chord": self.cfg.get("chord_region"),
        }
        self.song_name = tk.StringVar(value=self.cfg.get("last_song", ""))
        self.level = tk.StringVar(value=self.cfg.get("last_level", "Lv1"))
        self.cell_size = self.cfg.get("cell_size")  # (w, h) 單格尺寸
        self.capturing = False
        self.records = []           # [(time_sec, chord)]
        self.screenshots = []       # [PIL.Image] 分段截圖
        self.ref_chords = []        # OCR 出的和弦序列
        self._thread = None

        self._build_ui()
        self._update_region_display()

    # ---- UI ----

    def _build_ui(self):
        style = {"bg": "#1a1a2e", "fg": "#e0e0e0", "font": ("Segoe UI", 10)}
        btn_style = {"bg": "#16213e", "fg": "#e94560", "activebackground": "#e94560",
                     "activeforeground": "#fff", "font": ("Segoe UI", 10, "bold"),
                     "relief": "flat", "cursor": "hand2", "padx": 10, "pady": 4}

        # ---- 頂部：歌曲選擇 ----
        top = tk.Frame(self, bg="#1a1a2e")
        top.pack(fill=tk.X, padx=10, pady=(10, 5))

        tk.Label(top, text="歌曲:", **style).pack(side=tk.LEFT)
        tk.Entry(top, textvariable=self.song_name, width=30,
                 bg="#0d0d1a", fg="#e0e0e0", insertbackground="#e0e0e0",
                 font=("Segoe UI", 11)).pack(side=tk.LEFT, padx=5)

        tk.Button(top, text="瀏覽...", command=self._browse_song, **btn_style).pack(side=tk.LEFT, padx=2)

        tk.Label(top, text="等級:", **style).pack(side=tk.LEFT, padx=(15, 0))
        combo = ttk.Combobox(top, textvariable=self.level, values=LEVELS, width=5, state="readonly")
        combo.pack(side=tk.LEFT, padx=5)

        # ---- 區域設定 ----
        region_frame = tk.LabelFrame(self, text=" 擷取區域 ", bg="#1a1a2e", fg="#888",
                                     font=("Segoe UI", 10), padx=10, pady=5)
        region_frame.pack(fill=tk.X, padx=10, pady=5)

        self.region_labels = {}
        for i, (key, label) in enumerate([("play_btn", "播放按鈕 ▶/||"),
                                           ("time", "目前時間 00:00"),
                                           ("duration", "總長度 03:53"),
                                           ("chord", "和弦網格區域")]):
            row = tk.Frame(region_frame, bg="#1a1a2e")
            row.pack(fill=tk.X, pady=2)
            tk.Label(row, text=f"{label}:", width=16, anchor="w", **style).pack(side=tk.LEFT)
            lbl = tk.Label(row, text="未設定", bg="#0d0d1a", fg="#888", width=30, anchor="w",
                           font=("Consolas", 9), padx=5)
            lbl.pack(side=tk.LEFT, padx=5)
            self.region_labels[key] = lbl
            tk.Button(row, text="框選", command=lambda k=key: self._select_region(k), **btn_style).pack(side=tk.LEFT, padx=2)
            tk.Button(row, text="測試", command=lambda k=key: self._test_region(k), **btn_style).pack(side=tk.LEFT, padx=2)

        # ---- 控制按鈕 ----
        ctrl = tk.Frame(self, bg="#1a1a2e")
        ctrl.pack(fill=tk.X, padx=10, pady=5)

        self.btn_start = tk.Button(ctrl, text="▶ 開始擷取", command=self._start_capture,
                                   bg="#e94560", fg="#fff", font=("Segoe UI", 12, "bold"),
                                   relief="flat", padx=20, pady=6, cursor="hand2")
        self.btn_start.pack(side=tk.LEFT, padx=5)

        self.btn_stop = tk.Button(ctrl, text="⏹ 停止", command=self._stop_capture,
                                  state=tk.DISABLED, **btn_style)
        self.btn_stop.pack(side=tk.LEFT, padx=5)

        # ---- 截圖面板 ----
        shot_frame = tk.LabelFrame(self, text=" 樂譜截圖（多頁拼接）", bg="#1a1a2e", fg="#888",
                                   font=("Segoe UI", 10), padx=10, pady=5)
        shot_frame.pack(fill=tk.X, padx=10, pady=5)

        # 格子尺寸設定行
        cell_row = tk.Frame(shot_frame, bg="#1a1a2e")
        cell_row.pack(fill=tk.X, pady=(0, 4))

        tk.Button(cell_row, text="⬜ 框選一列", command=self._select_row_ref, **btn_style).pack(side=tk.LEFT, padx=2)
        self.cell_size_var = tk.StringVar()
        self._update_cell_display()
        tk.Label(cell_row, textvariable=self.cell_size_var,
                 bg="#0d0d1a", fg="#2d6a4f", font=("Consolas", 9), padx=8).pack(side=tk.LEFT, padx=5)

        # 截圖按鈕行
        shot_btns = tk.Frame(shot_frame, bg="#1a1a2e")
        shot_btns.pack(fill=tk.X)

        tk.Button(shot_btns, text="📷 框選擷取", command=self._take_screenshot_select, **btn_style).pack(side=tk.LEFT, padx=2)
        tk.Button(shot_btns, text="🗑 移除最後一張", command=self._remove_last_screenshot, **btn_style).pack(side=tk.LEFT, padx=2)
        tk.Button(shot_btns, text="✅ 拼接儲存 + OCR", command=self._stitch_and_ocr, **btn_style).pack(side=tk.LEFT, padx=2)

        self.screenshot_list_var = tk.StringVar(value="截圖: 0 張")
        tk.Label(shot_btns, textvariable=self.screenshot_list_var,
                 bg="#1a1a2e", fg="#888", font=("Consolas", 9), padx=10).pack(side=tk.LEFT)

        # 縮圖顯示區（水平捲動）
        thumb_outer = tk.Frame(shot_frame, bg="#0d0d1a", height=80)
        thumb_outer.pack(fill=tk.X, pady=(5, 0))
        thumb_outer.pack_propagate(False)

        thumb_canvas = tk.Canvas(thumb_outer, bg="#0d0d1a", height=75, highlightthickness=0)
        thumb_scroll = tk.Scrollbar(thumb_outer, orient=tk.HORIZONTAL, command=thumb_canvas.xview)
        thumb_canvas.configure(xscrollcommand=thumb_scroll.set)
        thumb_scroll.pack(side=tk.BOTTOM, fill=tk.X)
        thumb_canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.thumb_inner = tk.Frame(thumb_canvas, bg="#0d0d1a")
        thumb_canvas.create_window((0, 0), window=self.thumb_inner, anchor="nw")
        self.thumb_inner.bind("<Configure>",
                              lambda e: thumb_canvas.configure(scrollregion=thumb_canvas.bbox("all")))
        self.thumb_canvas = thumb_canvas
        self._thumb_refs = []  # 保持 PhotoImage 參照避免 GC

        # ---- 狀態 ----
        status_frame = tk.Frame(self, bg="#1a1a2e")
        status_frame.pack(fill=tk.X, padx=10, pady=5)

        self.status_var = tk.StringVar(value="就緒")
        tk.Label(status_frame, textvariable=self.status_var,
                 bg="#16213e", fg="#e94560", font=("Segoe UI", 11, "bold"),
                 padx=10, pady=4).pack(fill=tk.X)

        # ---- 進度條 ----
        self.progress_var = tk.StringVar(value="和弦: 0 | 截圖: 0 | 時間: --:--")
        tk.Label(self, textvariable=self.progress_var,
                 bg="#1a1a2e", fg="#888", font=("Consolas", 10)).pack(fill=tk.X, padx=10)

        # ---- Log ----
        log_frame = tk.LabelFrame(self, text=" 擷取紀錄 ", bg="#1a1a2e", fg="#888",
                                  font=("Segoe UI", 10), padx=5, pady=5)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.log_text = tk.Text(log_frame, bg="#0d0d1a", fg="#e0e0e0", height=15,
                                font=("Consolas", 9), wrap=tk.WORD, state=tk.DISABLED,
                                insertbackground="#e0e0e0")
        scrollbar = tk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # tag colors
        self.log_text.tag_configure("info", foreground="#e0e0e0")
        self.log_text.tag_configure("chord", foreground="#e94560")
        self.log_text.tag_configure("state", foreground="#2d6a4f")
        self.log_text.tag_configure("warn", foreground="#e9c46a")
        self.log_text.tag_configure("error", foreground="#e76f51")

    def _log(self, msg, tag="info"):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, msg + "\n", tag)
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    # ---- 格子尺寸 ----

    def _update_cell_display(self):
        row_h = self.cfg.get("row_height")
        beats = self.cfg.get("beats_per_row", 8)
        row_w = self.cfg.get("row_width")
        if row_h and row_w:
            beat_w = row_w / beats
            self.cell_size_var.set(f"列高:{row_h}px  {beats}拍/列  每拍:{beat_w:.0f}px")
        elif self.cell_size:
            self.cell_size_var.set(f"格子: {self.cell_size[0]}×{self.cell_size[1]} px")
        else:
            self.cell_size_var.set("未設定（請框選一列）")

    def _select_row_ref(self):
        """框選 Chordify 上的一整列和弦，再詢問拍數"""
        from tkinter import simpledialog

        self._log("⬜ 請在 Chordify 上框選「一整列」和弦（包含左右邊界）...", "info")
        self.withdraw()
        time.sleep(0.3)
        region = self._do_select("框選一整列和弦")
        self.deiconify()

        if not region:
            self._log("  取消", "info")
            return

        row_w, row_h = region[2], region[3]

        # 詢問一列幾拍（用 simpledialog，保證顯示在最上層）
        default_beats = self.cfg.get("beats_per_row", 8)
        beats = simpledialog.askinteger(
            "一列幾拍？",
            f"框選的列: {row_w}×{row_h} px\n\n"
            f"這一列有幾拍？\n"
            f"  4/4 拍 → 8\n"
            f"  3/4 拍 → 6\n",
            initialvalue=default_beats,
            minvalue=2, maxvalue=24,
            parent=self
        )

        if not beats:
            beats = default_beats

        # 儲存
        beat_w = row_w / beats
        self.cfg["row_height"] = row_h
        self.cfg["row_width"] = row_w
        self.cfg["beats_per_row"] = beats
        self.cfg["cell_size"] = [row_w, row_h]
        self.cell_size = (row_w, row_h)
        save_config(self.cfg)
        self._update_cell_display()
        self._log(f"  ✓ 列: {row_w}×{row_h}px, {beats} 拍/列, 每拍 {beat_w:.0f}px", "state")

    # ---- 區域設定 ----

    def _update_region_display(self):
        for key, lbl in self.region_labels.items():
            r = self.regions.get(key)
            if r:
                lbl.configure(text=f"x={r[0]}, y={r[1]}, w={r[2]}, h={r[3]}", fg="#2d6a4f")
            else:
                lbl.configure(text="未設定", fg="#888")

    def _select_region(self, key):
        names = {"play_btn": "播放按鈕", "time": "目前時間", "duration": "總長度", "chord": "和弦網格"}
        self.withdraw()
        time.sleep(0.3)
        region = self._do_select(f"框選「{names[key]}」區域")
        self.deiconify()
        if region:
            self.regions[key] = region
            self.cfg[f"{key}_region"] = list(region)
            save_config(self.cfg)
            self._update_region_display()
            self._log(f"✓ {names[key]} 設定: {region}", "state")

    def _do_select(self, title):
        """
        用 pynput 全域滑鼠監聽 + 即時紅色框線。
        不遮擋螢幕，使用者直接在目標位置拖曳。
        """
        result = [None]

        # 小提示視窗
        hint = tk.Toplevel(self)
        hint.title("框選")
        hint.geometry("420x80+50+50")
        hint.attributes('-topmost', True)
        hint.configure(bg="#e94560")
        tk.Label(hint, text=f"{title}", font=("Segoe UI", 13, "bold"),
                 fg="white", bg="#e94560").pack(pady=5)
        tk.Label(hint, text="在螢幕上按住左鍵拖曳框選，放開完成。ESC 取消。",
                 font=("Segoe UI", 10), fg="white", bg="#e94560").pack()

        # 透明框線視窗（跟隨拖曳即時顯示紅色邊框）
        border_win = tk.Toplevel(self)
        border_win.overrideredirect(True)
        border_win.attributes('-topmost', True)
        border_win.attributes('-alpha', 0.5)
        border_win.configure(bg="red")
        border_win.withdraw()  # 先隱藏

        # 內部透明區（讓邊框可見、中間透空）
        inner = tk.Frame(border_win, bg="black")

        try:
            from pynput import mouse as pynput_mouse

            sx, sy = [0], [0]
            dragging = [False]
            BORDER = 3

            def update_border(x1, y1, x2, y2):
                """更新紅色框線位置"""
                left, top = min(x1, x2), min(y1, y2)
                w, h = abs(x2 - x1), abs(y2 - y1)
                if w < 5 or h < 5:
                    return
                try:
                    border_win.geometry(f"{w}x{h}+{left}+{top}")
                    inner.place(x=BORDER, y=BORDER,
                                width=max(1, w - BORDER * 2),
                                height=max(1, h - BORDER * 2))
                    border_win.deiconify()
                except tk.TclError:
                    pass

            def on_click(x, y, button, pressed):
                if button != pynput_mouse.Button.left:
                    return
                if pressed:
                    sx[0], sy[0] = x, y
                    dragging[0] = True
                else:
                    if dragging[0]:
                        x1, y1 = min(sx[0], x), min(sy[0], y)
                        x2, y2 = max(sx[0], x), max(sy[0], y)
                        if x2 - x1 > 5 and y2 - y1 > 5:
                            result[0] = (x1, y1, x2 - x1, y2 - y1)
                        dragging[0] = False
                        listener.stop()

            def on_move(x, y):
                if dragging[0]:
                    # 在主線程更新 UI
                    try:
                        hint.after_idle(update_border, sx[0], sy[0], x, y)
                    except tk.TclError:
                        pass

            listener = pynput_mouse.Listener(on_click=on_click, on_move=on_move)
            listener.start()

            def check_done():
                if not listener.is_alive():
                    try:
                        border_win.destroy()
                    except tk.TclError:
                        pass
                    hint.destroy()
                else:
                    hint.after(50, check_done)

            def cancel(e=None):
                listener.stop()
                try:
                    border_win.destroy()
                except tk.TclError:
                    pass
                hint.destroy()

            hint.bind("<Escape>", cancel)
            hint.after(50, check_done)
            hint.wait_window()

        except ImportError:
            hint.destroy()
            try:
                border_win.destroy()
            except tk.TclError:
                pass
            self._log("⚠ 需要 pynput: pip install pynput", "warn")
            result[0] = self._manual_region_input(title)

        return result[0]

    def _manual_region_input(self, title):
        """手動輸入座標的 dialog"""
        dialog = tk.Toplevel(self)
        dialog.title(f"手動輸入 — {title}")
        dialog.geometry("300x200")
        dialog.configure(bg="#1a1a2e")
        dialog.attributes('-topmost', True)

        result = [None]
        entries = {}
        for i, label in enumerate(["x", "y", "寬度 w", "高度 h"]):
            tk.Label(dialog, text=f"{label}:", bg="#1a1a2e", fg="#e0e0e0").grid(row=i, column=0, padx=10, pady=5)
            e = tk.Entry(dialog, width=10, bg="#0d0d1a", fg="#e0e0e0")
            e.grid(row=i, column=1, padx=10, pady=5)
            entries[label[0] if label[0] in 'xywh' else label[-1]] = e

        def ok():
            try:
                result[0] = (int(entries['x'].get()), int(entries['y'].get()),
                             int(entries['w'].get()), int(entries['h'].get()))
            except ValueError:
                pass
            dialog.destroy()

        tk.Button(dialog, text="確定", command=ok, bg="#e94560", fg="#fff").grid(row=4, column=0, columnspan=2, pady=10)
        dialog.wait_window()
        return result[0]

    def _test_region(self, key):
        r = self.regions.get(key)
        if not r:
            self._log(f"⚠ {key} 未設定", "warn")
            return
        img = capture_region(tuple(r))
        if key == "play_btn":
            state = detect_button_state(img, tuple(r))
            icon = "||" if state == "playing" else "▶" if state == "stopped" else "?"
            self._log(f"播放按鈕: {icon} ({state})", "state")
        elif key in ("time", "duration"):
            t = ocr_time(img)
            if t is not None:
                m, s = divmod(t, 60)
                label = "目前時間" if key == "time" else "總長度"
                self._log(f"{label}: {m}:{s:02d} ({t}s)", "state")
            else:
                self._log(f"{key}: 無法辨識", "warn")
        elif key == "chord":
            chord, center = find_highlighted_chord(img)
            self._log(f"和弦: {chord or '無'}, 位置: {center}", "state")

    # ---- 歌曲瀏覽 ----

    def _browse_song(self):
        path = filedialog.askopenfilename(
            title="選擇測試曲目",
            initialdir=str(TEST_SONGS_DIR),
            filetypes=[("FLAC", "*.flac"), ("All", "*.*")]
        )
        if path:
            p = Path(path)
            self.song_name.set(p.stem)
            # 自動偵測等級
            for lv in LEVELS:
                if lv in str(p):
                    self.level.set(lv)
                    break
            self._log(f"選擇: {p.name} ({self.level.get()})", "info")

    # ---- 手動截圖 (分段拼接用) ----

    def _take_screenshot_select(self):
        """框選螢幕區域擷取截圖，加入清單"""
        self._log("📷 請在螢幕上框選要擷取的樂譜區域...", "info")
        self.withdraw()
        time.sleep(0.3)
        region = self._do_select("框選樂譜區域")
        self.deiconify()

        if not region:
            self._log("  取消擷取", "info")
            return

        img = capture_region(region)
        self.screenshots.append(img)
        n = len(self.screenshots)
        self._log(f"  ✓ 截圖 #{n} ({img.size[0]}×{img.size[1]})", "state")
        self.screenshot_list_var.set(f"截圖: {n} 張")
        self._refresh_thumbnails()

    def _remove_last_screenshot(self):
        """移除最後一張截圖"""
        if not self.screenshots:
            self._log("  無截圖可移除", "warn")
            return
        self.screenshots.pop()
        n = len(self.screenshots)
        self._log(f"  🗑 已移除，剩餘 {n} 張", "info")
        self.screenshot_list_var.set(f"截圖: {n} 張")
        self._refresh_thumbnails()

    def _refresh_thumbnails(self):
        """更新縮圖顯示"""
        # 清除舊的
        for w in self.thumb_inner.winfo_children():
            w.destroy()
        self._thumb_refs.clear()

        THUMB_H = 65

        for i, img in enumerate(self.screenshots):
            # 等比縮放
            ratio = THUMB_H / img.height
            thumb_w = max(1, int(img.width * ratio))
            thumb = img.resize((thumb_w, THUMB_H), Image.LANCZOS)
            photo = ImageTk.PhotoImage(thumb)
            self._thumb_refs.append(photo)

            frame = tk.Frame(self.thumb_inner, bg="#2a2a4a", padx=2, pady=2)
            frame.pack(side=tk.LEFT, padx=3)

            lbl = tk.Label(frame, image=photo, bg="#0d0d1a")
            lbl.pack()
            tk.Label(frame, text=f"#{i+1}", bg="#2a2a4a", fg="#888",
                     font=("Consolas", 8)).pack()

    def _stitch_and_ocr(self):
        """拼接所有截圖 → 存 .png → OCR 辨識和弦 → 存 .chords.txt"""
        if not self.screenshots:
            self._log("⚠ 無截圖，請先擷取", "warn")
            return

        name = self.song_name.get().strip()
        lv = self.level.get()
        if not name:
            messagebox.showwarning("提示", "請輸入歌曲名稱")
            return

        save_dir = TEST_SONGS_DIR / lv
        save_dir.mkdir(parents=True, exist_ok=True)

        # 1. 上下拼接
        total_h = sum(s.height for s in self.screenshots)
        max_w = max(s.width for s in self.screenshots)
        combined = Image.new("RGB", (max_w, total_h), (255, 255, 255))
        y = 0
        for s in self.screenshots:
            combined.paste(s, (0, y))
            y += s.height

        png_path = save_dir / f"{name}.png"
        combined.save(str(png_path))
        self._log(f"✓ 已儲存 {png_path.name} ({max_w}×{total_h}, {len(self.screenshots)} 頁)", "state")

        # 2. 逐格 OCR 辨識和弦序列
        if not self.cell_size:
            self._log("⚠ 請先「框選一格」設定格子大小", "warn")
            return

        self._log(f"🔍 逐格 OCR 辨識中（格子 {self.cell_size[0]}×{self.cell_size[1]}）...", "info")
        self.status_var.set("🔍 逐格 OCR 辨識中...")

        cell_w, cell_h = self.cell_size

        def do_ocr():
            try:
                sys.path.insert(0, str(TOOLS_DIR))
                from chordify_ocr import extract_chords_grid, sequence_to_compact, save_results
                seq = extract_chords_grid(str(png_path), cell_w, cell_h, verbose=False)
                compact = sequence_to_compact(seq)

                save_results(seq, name, str(save_dir))

                unique = sorted(set(c for c in seq if c))
                chord_count = sum(1 for c in seq if c)
                total_beats = len(seq)

                self._safe_log(f"✓ OCR 完成: {total_beats} 拍, {chord_count} 個和弦", "state")
                self._safe_log(f"  和弦: {', '.join(unique)}", "info")
                self._safe_log(f"  序列: {compact[:120]}{'...' if len(compact) > 120 else ''}", "info")
                self._safe_status(f"✓ {name}.png + .chords.txt 已儲存")
            except Exception as e:
                self._safe_log(f"⚠ OCR 失敗: {e}", "error")
                self._safe_status("OCR 失敗")

        threading.Thread(target=do_ocr, daemon=True).start()

    # ---- 開始擷取 ----

    def _start_capture(self):
        # 驗證
        if not self.song_name.get().strip():
            messagebox.showwarning("提示", "請輸入歌曲名稱")
            return
        for key in ["play_btn", "time", "duration", "chord"]:
            if not self.regions.get(key):
                names = {"play_btn": "播放按鈕", "time": "目前時間", "duration": "總長度", "chord": "和弦網格"}
                messagebox.showwarning("提示", f"請先框選「{names[key]}」區域")
                return

        # 檢查已有
        lv = self.level.get()
        name = self.song_name.get().strip()
        lab_path = TEST_SONGS_DIR / lv / f"{name}.lab"
        if lab_path.is_file():
            if not messagebox.askyesno("已存在", f"已有 {name}.lab，要覆蓋嗎？"):
                return

        # 記住歌曲選擇
        self.cfg["last_song"] = name
        self.cfg["last_level"] = lv
        save_config(self.cfg)

        # 重置
        self.records = []
        self.screenshots = []
        self.ref_chords = []
        self.capturing = True
        self.btn_start.configure(state=tk.DISABLED)
        self.btn_stop.configure(state=tk.NORMAL)
        self.status_var.set("⏳ 等待播放...")
        self._log(f"\n{'='*50}", "info")
        self._log(f"開始擷取: {name} ({lv})", "state")

        self._thread = threading.Thread(target=self._capture_worker, daemon=True)
        self._thread.start()

    def _stop_capture(self):
        self.capturing = False

    # ---- 擷取主迴圈 ----

    def _capture_worker(self):
        import pyautogui

        play_r = tuple(self.regions["play_btn"])
        time_r = tuple(self.regions["time"])
        dur_r = tuple(self.regions["duration"])
        chord_r = tuple(self.regions["chord"])
        name = self.song_name.get().strip()
        lv = self.level.get()

        # Phase 0: 讀取歌曲總長度
        self._safe_log("讀取歌曲總長度...", "info")
        total_duration = None
        for attempt in range(5):
            dur_img = capture_region(dur_r)
            total_duration = ocr_time(dur_img)
            if total_duration and total_duration > 10:
                break
            time.sleep(0.3)

        if total_duration:
            m, s = divmod(total_duration, 60)
            self._safe_log(f"歌曲總長: {m}:{s:02d} ({total_duration}s)", "state")
        else:
            self._safe_log("⚠ 無法讀取總長度，將持續擷取直到手動停止", "warn")
            total_duration = 9999  # 不自動停止

        # Phase 1: 自動點擊播放按鈕
        self._safe_log("自動點擊播放...", "info")
        self._safe_status("▶ 點擊播放按鈕...")
        cx = play_r[0] + play_r[2] // 2
        cy = play_r[1] + play_r[3] // 2
        pyautogui.click(cx, cy)
        time.sleep(1.5)

        if not self.capturing:
            self._finish(name, lv)
            return

        self._safe_log("🎵 開始擷取和弦...", "state")
        self._safe_status("🎵 擷取中...")

        # =================================================================
        # Phase 2: Producer-Consumer Buffer 架構
        #
        # Producer (快速線程, 30ms):
        #   截圖 → 偵測方塊位置 → 移動了？→ 放入 buffer (含圖片+時間+位置)
        #
        # Consumer (OCR 線程):
        #   從 buffer 取出 → OCR 辨識和弦 → 記錄
        #
        # Time OCR (校時線程, 每 2 秒):
        #   截圖時間區域 → OCR → 更新基準時間
        #
        # 三個線程完全獨立，互不阻塞。
        # =================================================================

        import queue

        ocr_queue = queue.Queue(maxsize=30)  # buffer: 最多存 30 張待辨識
        shared = {
            "last_ocr_sec": None,
            "last_ocr_perf": None,
            "last_time_sec": None,
            "time_stall_count": 0,
            "last_center": None,
            "last_chord": None,
            "screenshot_count": 0,
            "stop": False,
        }

        cfg_row_w = self.cfg.get("row_width")
        cfg_beats = self.cfg.get("beats_per_row", 8)
        grid_width = (cfg_row_w / cfg_beats) if cfg_row_w else 120

        # ---- Time OCR 線程 ----
        def time_ocr_thread():
            while self.capturing and not shared["stop"]:
                try:
                    time_img = capture_region(time_r)
                    current_sec = ocr_time(time_img)
                    now = time.perf_counter()

                    if current_sec is not None:
                        if shared["last_ocr_sec"] is None or current_sec != shared["last_ocr_sec"]:
                            shared["last_ocr_sec"] = current_sec
                            shared["last_ocr_perf"] = now

                        # 結束判斷 1: 歌曲結尾
                        if current_sec >= total_duration - 2:
                            m, s = divmod(current_sec, 60)
                            self._safe_log(f"⏹ 到達歌曲結尾 ({m}:{s:02d})", "state")
                            shared["stop"] = True
                            break

                        # 結束判斷 2: 時間停滯
                        if shared["last_time_sec"] is not None:
                            if current_sec <= shared["last_time_sec"]:
                                shared["time_stall_count"] += 1
                                if shared["time_stall_count"] >= 3:
                                    self._safe_log("⏹ 時間停滯 6 秒，判定播放結束", "state")
                                    shared["stop"] = True
                                    break
                            else:
                                shared["time_stall_count"] = 0

                        shared["last_time_sec"] = current_sec

                except Exception:
                    pass
                time.sleep(2.0)  # 每 2 秒校時

        # ---- Consumer: OCR 線程 ----
        def ocr_consumer_thread():
            while self.capturing and not shared["stop"]:
                try:
                    item = ocr_queue.get(timeout=0.5)
                except queue.Empty:
                    continue

                chord_img, precise_time, jump_distance = item

                try:
                    # 漏拍補償
                    skipped = int(jump_distance / grid_width) - 1 if grid_width > 0 else 0
                    skipped = max(0, min(skipped, 5))

                    if skipped > 0:
                        prev_time = self.records[-1][0] if self.records else precise_time
                        beat_dur = (precise_time - prev_time) / (skipped + 1)
                        for si in range(skipped):
                            st = prev_time + beat_dur * (si + 1)
                            self.records.append((round(st, 3), f"({skipped}skip)"))
                            self._safe_log(f"  {int(st//60)}:{int(st%60):02d}.{int((st%1)*1000):03d}  ({skipped}skip)", "warn")

                    # OCR 和弦（慢，但不阻塞 producer）
                    chord, _ = find_highlighted_chord(chord_img)
                    if chord and chord != shared["last_chord"]:
                        self.records.append((round(precise_time, 3), chord))
                        shared["last_chord"] = chord
                        self.ref_chords.append(chord)

                        m = int(precise_time // 60)
                        s = int(precise_time % 60)
                        ms = int((precise_time % 1) * 1000)
                        count = len(self.records)
                        self._safe_log(f"  {m}:{s:02d}.{ms:03d}  {chord}", "chord")
                        self._safe_progress(f"和弦: {count} | 截圖: {len(self.screenshots)} | "
                                            f"時間: {m}:{s:02d}.{ms:03d}")
                except Exception:
                    pass

                ocr_queue.task_done()

        # ---- 啟動 consumer + time OCR 線程 ----
        t_ocr = threading.Thread(target=ocr_consumer_thread, daemon=True)
        t_time = threading.Thread(target=time_ocr_thread, daemon=True)
        t_ocr.start()
        t_time.start()

        # ---- Producer: 快速截圖 + 方塊追蹤（主迴圈）----
        screenshot_interval = 0

        while self.capturing and not shared["stop"]:
            try:
                now = time.perf_counter()

                # 截圖（~10ms）
                chord_img = capture_region(chord_r)

                # 方塊位置偵測（~3ms）
                center = _find_dark_block_center(chord_img)

                box_moved = False
                jump_distance = 0
                if center:
                    if shared["last_center"] is None:
                        box_moved = True
                    else:
                        dx = abs(center[0] - shared["last_center"][0])
                        dy = abs(center[1] - shared["last_center"][1])
                        if dx > 15 or dy > 15:
                            box_moved = True
                            jump_distance = max(dx, dy)
                    if box_moved:
                        shared["last_center"] = center

                if box_moved:
                    # 計算精確時間
                    if shared["last_ocr_sec"] is not None and shared["last_ocr_perf"] is not None:
                        precise_time = shared["last_ocr_sec"] + (now - shared["last_ocr_perf"])
                    elif shared["last_time_sec"] is not None:
                        precise_time = float(shared["last_time_sec"])
                    else:
                        precise_time = 0.0

                    # 放入 buffer（不等 OCR）
                    try:
                        ocr_queue.put_nowait((chord_img.copy(), precise_time, jump_distance))
                    except queue.Full:
                        pass  # buffer 滿了就丟棄（OCR 太慢跟不上）

                # 定期截圖
                screenshot_interval += 1
                if screenshot_interval >= 150:  # ~4.5 秒
                    screenshot_interval = 0
                    self.screenshots.append(chord_img.copy())

            except Exception:
                pass

            time.sleep(0.03)  # 30ms — producer 不受 OCR 拖累

        # ---- 等 consumer 處理完 buffer 剩餘 ----
        shared["stop"] = True
        ocr_queue.join()  # 等所有 buffer 項目處理完

        # Phase 3: 儲存
        self._finish(name, lv)

    def _finish(self, name, lv):
        self._safe_status("儲存中...")

        save_dir = TEST_SONGS_DIR / lv
        save_dir.mkdir(parents=True, exist_ok=True)

        # 儲存 .lab
        if self.records:
            entries = []
            sorted_recs = sorted(set(self.records))
            for i, (sec, chord) in enumerate(sorted_recs):
                end = sorted_recs[i + 1][0] if i + 1 < len(sorted_recs) else sec + 4.0
                entries.append({"time": sec, "end": end, "chord": chord})

            roots = [c[0] if len(c) < 2 or c[1] not in '#b' else c[:2]
                     for _, c in sorted_recs if c and c[0] in 'ABCDEFG']
            key = Counter(roots).most_common(1)[0][0] if roots else ""

            lab = {"song": name, "level": lv, "key": key,
                   "source": "Chordify (GUI capture)", "entries": entries}
            lab_path = save_dir / f"{name}.lab"
            lab_path.write_text(json.dumps(lab, ensure_ascii=False, indent=2), encoding="utf-8")
            self._safe_log(f"✓ 已儲存 {lab_path.name} ({len(entries)} 和弦, Key: {key})", "state")

        # 儲存 .txt（和弦序列對照檔）
        if self.ref_chords:
            txt_path = save_dir / f"{name}.chords.txt"
            txt_path.write_text("\n".join(
                f"{t:.3f}\t{c}" for t, c in sorted(set(self.records))
            ), encoding="utf-8")
            self._safe_log(f"✓ 已儲存 {txt_path.name}", "state")

        # 拼接截圖為完整 .png
        if self.screenshots:
            total_h = sum(s.height for s in self.screenshots)
            max_w = max(s.width for s in self.screenshots)
            combined = Image.new("RGB", (max_w, total_h), (255, 255, 255))
            y = 0
            for s in self.screenshots:
                combined.paste(s, (0, y))
                y += s.height
            png_path = save_dir / f"{name}.png"
            combined.save(str(png_path))
            self._safe_log(f"✓ 已儲存 {png_path.name} ({max_w}×{total_h}, {len(self.screenshots)} 段)", "state")

        count = len(self.records)
        self._safe_log(f"\n完成！共 {count} 個和弦", "state")
        self._safe_status(f"✓ 完成 ({count} 和弦)")

        # 恢復按鈕
        self.after(0, lambda: self.btn_start.configure(state=tk.NORMAL))
        self.after(0, lambda: self.btn_stop.configure(state=tk.DISABLED))
        self.capturing = False

    # ---- thread-safe UI 更新 ----

    def _safe_log(self, msg, tag="info"):
        self.after(0, lambda: self._log(msg, tag))

    def _safe_status(self, msg):
        self.after(0, lambda: self.status_var.set(msg))

    def _safe_progress(self, msg):
        self.after(0, lambda: self.progress_var.set(msg))


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = ChordifyCapture()
    app.mainloop()
