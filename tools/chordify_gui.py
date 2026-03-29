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
        if text and conf > 0.15 and any(c in text for c in 'ABCDEFG'):
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
    # # 被讀成 3 或 7 等數字
    "C37": "C#7", "C3": "C#", "B7/D3": "B7/D#",
    "F3m": "F#m", "F3m7": "F#m7",
    "G3": "G#", "A3": "A#",
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

# ---------------------------------------------------------------------------
# 和弦網格編輯器
# ---------------------------------------------------------------------------

class ChordGridEditor(tk.Toplevel):
    """
    Chordify 風格的和弦網格編輯器。
    每格代表一拍，點擊可編輯和弦名稱。
    """

    # 顏色
    BG = "#f7f9fb"
    CELL_EMPTY = "#f0f4f7"
    CELL_CHORD = "#ffffff"
    CELL_HOVER = "#dce1ff"
    CELL_EDIT = "#cad2ff"
    BORDER = "#d9e4ea"
    BAR_LINE = "#2151da"
    TEXT = "#2a3439"
    TEXT_DIM = "#717c82"

    def __init__(self, parent, beats: list, beats_per_row: int, beats_per_bar: int,
                 save_path, log_fn=None):
        super().__init__(parent)
        self.title("Chord Grid Editor")
        self.geometry("780x600")
        self.configure(bg=self.BG)
        self.attributes('-topmost', True)

        self.beats = list(beats)
        self.bpr = beats_per_row
        self.bpb = beats_per_bar
        self.save_path = save_path
        self.log_fn = log_fn
        self.selected = None  # (row, col) of selected cell
        self.cell_widgets = {}  # (row, col) → Label

        self._build()

    def _build(self):
        # Header
        header = tk.Frame(self, bg=self.BG)
        header.pack(fill=tk.X, padx=12, pady=8)

        tk.Label(header, text="Chord Grid Editor", font=("Segoe UI", 14, "bold"),
                 bg=self.BG, fg="#2151da").pack(side=tk.LEFT)

        tk.Button(header, text="💾 Save", command=self._save,
                  bg="#2151da", fg="white", font=("Segoe UI", 10, "bold"),
                  relief="flat", padx=12, pady=3, cursor="hand2").pack(side=tk.RIGHT, padx=4)
        tk.Button(header, text="Close", command=self.destroy,
                  bg="#d3e4fe", fg="#435368", font=("Segoe UI", 9, "bold"),
                  relief="flat", padx=10, pady=3, cursor="hand2").pack(side=tk.RIGHT, padx=4)

        # Info
        n_rows = (len(self.beats) + self.bpr - 1) // self.bpr
        chord_count = sum(1 for b in self.beats if b)
        tk.Label(header, text=f"{len(self.beats)} beats | {n_rows} rows | {chord_count} chords | {self.bpb}/4",
                 bg=self.BG, fg=self.TEXT_DIM, font=("Consolas", 9)).pack(side=tk.RIGHT, padx=10)

        # Row number header
        colhead = tk.Frame(self, bg=self.BG)
        colhead.pack(fill=tk.X, padx=12)
        tk.Label(colhead, text="", width=4, bg=self.BG).pack(side=tk.LEFT)
        for c in range(self.bpr):
            bar_num = c // self.bpb + 1
            beat_num = c % self.bpb + 1
            lbl_text = f"{beat_num}" if beat_num == 1 else f"{beat_num}"
            fg = "#2151da" if beat_num == 1 else self.TEXT_DIM
            tk.Label(colhead, text=lbl_text, width=6, bg=self.BG, fg=fg,
                     font=("Consolas", 8)).pack(side=tk.LEFT, padx=1)

        # Scrollable grid
        container = tk.Frame(self, bg=self.BG)
        container.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))

        canvas = tk.Canvas(container, bg=self.BG, highlightthickness=0)
        scrollbar = tk.Scrollbar(container, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        grid_frame = tk.Frame(canvas, bg=self.BG)
        canvas.create_window((0, 0), window=grid_frame, anchor="nw")
        grid_frame.bind("<Configure>",
                        lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        # Mouse wheel scrolling
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        self.bind("<Destroy>", lambda e: canvas.unbind_all("<MouseWheel>"))

        # Build grid
        n_rows = (len(self.beats) + self.bpr - 1) // self.bpr

        for row in range(n_rows):
            row_frame = tk.Frame(grid_frame, bg=self.BG)
            row_frame.pack(fill=tk.X, pady=1)

            # Row number
            tk.Label(row_frame, text=f"{row+1:3d}", width=4, bg=self.BG, fg=self.TEXT_DIM,
                     font=("Consolas", 9), anchor="e").pack(side=tk.LEFT)

            for col in range(self.bpr):
                idx = row * self.bpr + col
                chord = self.beats[idx] if idx < len(self.beats) else ""
                is_bar_start = (col % self.bpb == 0)

                # Cell frame with bar line
                cell_frame = tk.Frame(row_frame, bg=self.BG)
                cell_frame.pack(side=tk.LEFT, padx=0)

                # Bar line (blue left border for beat 1 of each bar)
                if is_bar_start:
                    tk.Frame(cell_frame, bg=self.BAR_LINE, width=2).pack(side=tk.LEFT, fill=tk.Y)

                # Cell label
                bg = self.CELL_CHORD if chord else self.CELL_EMPTY
                lbl = tk.Label(cell_frame, text=chord or "·", width=6, height=1,
                               bg=bg, fg=self.TEXT if chord else "#c0c0c0",
                               font=("Segoe UI", 10, "bold" if chord else "normal"),
                               relief="flat", cursor="hand2",
                               highlightbackground=self.BORDER, highlightthickness=1)
                lbl.pack(side=tk.LEFT, padx=0, pady=0)

                # Bind events
                lbl.bind("<Button-1>", lambda e, r=row, c=col: self._on_click(r, c))
                lbl.bind("<Enter>", lambda e, l=lbl: l.configure(bg=self.CELL_HOVER))
                lbl.bind("<Leave>", lambda e, l=lbl, ch=chord:
                         l.configure(bg=self.CELL_CHORD if ch else self.CELL_EMPTY))

                self.cell_widgets[(row, col)] = lbl

    def _on_click(self, row, col):
        """點擊格子 → 彈出輸入框編輯和弦"""
        idx = row * self.bpr + col
        if idx >= len(self.beats):
            return

        old = self.beats[idx]
        lbl = self.cell_widgets[(row, col)]
        lbl.configure(bg=self.CELL_EDIT)

        # 用 simpledialog
        from tkinter import simpledialog
        new = simpledialog.askstring(
            "Edit Chord",
            f"Row {row+1}, Beat {col+1} (bar {col//self.bpb+1} beat {col%self.bpb+1})\n\n"
            f"Current: {old or '(empty)'}\n"
            f"Enter new chord (empty = clear):",
            initialvalue=old,
            parent=self
        )

        if new is not None:
            self.beats[idx] = new.strip()
            chord = self.beats[idx]
            lbl.configure(
                text=chord or "·",
                fg=self.TEXT if chord else "#c0c0c0",
                font=("Segoe UI", 10, "bold" if chord else "normal"),
                bg=self.CELL_CHORD if chord else self.CELL_EMPTY
            )
        else:
            lbl.configure(bg=self.CELL_CHORD if old else self.CELL_EMPTY)

    def _save(self):
        """存檔為 .chords.txt（逗號分隔格式）"""
        compact = ",".join(self.beats)
        self.save_path.write_text(compact, encoding="utf-8")

        chord_count = sum(1 for b in self.beats if b)
        if self.log_fn:
            self.log_fn(f"💾 已儲存 {self.save_path.name} ({chord_count} chords)", "state")

        messagebox.showinfo("Saved", f"已儲存 {chord_count} 個和弦", parent=self)


class ChordifyCapture(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ChordCurator Studio")
        self.geometry("680x820")
        self.configure(bg="#f7f9fb")
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
        # cell_size 由「框選一列 ÷ 拍數」自動計算，不需要單獨框選
        self.capturing = False
        self.records = []           # [(time_sec, chord)]
        self.screenshots = []       # [PIL.Image] 分段截圖
        self.ref_chords = []        # OCR 出的和弦序列
        self._thread = None

        # 確保 config 有拍數設定（防止舊 config 缺值）
        dirty = False
        if not self.cfg.get("beats_per_bar"):
            self.cfg["beats_per_bar"] = 4
            dirty = True
        if not self.cfg.get("bars_per_row"):
            self.cfg["bars_per_row"] = 4
            dirty = True
        if not self.cfg.get("beats_per_row") or self.cfg["beats_per_row"] != self.cfg["beats_per_bar"] * self.cfg["bars_per_row"]:
            self.cfg["beats_per_row"] = self.cfg["beats_per_bar"] * self.cfg["bars_per_row"]
            dirty = True
        if dirty:
            save_config(self.cfg)

        self._build_ui()
        self._update_region_display()

    # ---- UI ----

    def _build_ui(self):
        # Material Design 配色（參照 ChordCurator Studio）
        C = {
            "bg": "#f7f9fb",           # surface
            "card": "#ffffff",         # surface-container-lowest
            "card2": "#f0f4f7",        # surface-container-low
            "card3": "#e8eff3",        # surface-container
            "border": "#d9e4ea",       # surface-variant
            "text": "#2a3439",         # on-surface
            "text2": "#566166",        # on-surface-variant
            "dim": "#717c82",          # outline
            "primary": "#2151da",      # primary
            "primary_fg": "#f8f7ff",   # on-primary
            "secondary": "#506076",    # secondary
            "sec_container": "#d3e4fe", # secondary-container
            "sec_text": "#435368",     # on-secondary-container
            "tertiary": "#006592",     # tertiary
            "error": "#9f403d",        # error
            "success": "#2d6a4f",
        }
        self.C = C

        style = {"bg": C["bg"], "fg": C["text"], "font": ("Segoe UI", 10)}
        btn_style = {"bg": C["sec_container"], "fg": C["sec_text"],
                     "activebackground": C["primary"], "activeforeground": C["primary_fg"],
                     "font": ("Segoe UI", 9, "bold"), "relief": "flat", "cursor": "hand2",
                     "padx": 10, "pady": 3, "bd": 0}

        self.configure(bg=C["bg"])

        # ---- Header ----
        header = tk.Frame(self, bg=C["bg"])
        header.pack(fill=tk.X, padx=16, pady=(12, 4))

        tk.Label(header, text="♫", font=("Segoe UI", 18), bg=C["bg"],
                 fg=C["primary"]).pack(side=tk.LEFT)
        tk.Label(header, text="ChordCurator Studio", font=("Segoe UI", 16, "bold"),
                 bg=C["bg"], fg=C["primary"]).pack(side=tk.LEFT, padx=(6, 0))

        # ---- Song Info Card ----
        song_card = tk.Frame(self, bg=C["card"], highlightbackground=C["border"],
                             highlightthickness=1, padx=16, pady=12)
        song_card.pack(fill=tk.X, padx=16, pady=6)

        tk.Label(song_card, text="ACTIVE COMPOSITION", font=("Segoe UI", 8, "bold"),
                 bg=C["card"], fg=C["dim"]).pack(anchor="w")

        song_row = tk.Frame(song_card, bg=C["card"])
        song_row.pack(fill=tk.X, pady=(4, 0))

        tk.Entry(song_row, textvariable=self.song_name, width=28,
                 bg=C["card2"], fg=C["text"], insertbackground=C["text"],
                 font=("Segoe UI", 13, "bold"), relief="flat", bd=2).pack(side=tk.LEFT)

        tk.Button(song_row, text="瀏覽", command=self._browse_song, **btn_style).pack(side=tk.LEFT, padx=8)

        level_frame = tk.Frame(song_row, bg=C["sec_container"], padx=8, pady=2)
        level_frame.pack(side=tk.RIGHT)
        tk.Label(level_frame, text="Level", font=("Segoe UI", 8, "bold"),
                 bg=C["sec_container"], fg=C["sec_text"]).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Combobox(level_frame, textvariable=self.level, values=LEVELS,
                     width=4, state="readonly").pack(side=tk.LEFT)

        # ---- Extraction Config Card ----
        config_card = tk.Frame(self, bg=C["card2"], padx=12, pady=8)
        config_card.pack(fill=tk.X, padx=16, pady=4)

        tk.Label(config_card, text="EXTRACTION CONFIGURATION", font=("Segoe UI", 8, "bold"),
                 bg=C["card2"], fg=C["dim"]).pack(anchor="w", pady=(0, 6))

        self.region_labels = {}
        for key, label, icon in [("play_btn", "Play/Pause", "▶||"),
                                  ("time", "Current Time", "00:00"),
                                  ("duration", "Total Length", "03:53"),
                                  ("chord", "Chord Grid", "⬜")]:
            row = tk.Frame(config_card, bg=C["card2"])
            row.pack(fill=tk.X, pady=1)

            tk.Label(row, text=f"{icon} {label}", width=16, anchor="w",
                     bg=C["card2"], fg=C["text2"], font=("Segoe UI", 9)).pack(side=tk.LEFT)

            lbl = tk.Label(row, text="Not set", bg=C["card"], fg=C["dim"], width=28, anchor="w",
                           font=("Consolas", 8), padx=4, relief="flat", bd=1)
            lbl.pack(side=tk.LEFT, padx=4)
            self.region_labels[key] = lbl

            tk.Button(row, text="Select", command=lambda k=key: self._select_region(k),
                      **btn_style).pack(side=tk.LEFT, padx=1)
            tk.Button(row, text="Test", command=lambda k=key: self._test_region(k),
                      bg=C["card3"], fg=C["text"], font=("Segoe UI", 8),
                      relief="flat", cursor="hand2", padx=6, pady=2).pack(side=tk.LEFT, padx=1)

        # ---- Main Action ----
        action_frame = tk.Frame(self, bg=C["bg"])
        action_frame.pack(fill=tk.X, padx=16, pady=8)

        self.btn_start = tk.Button(action_frame, text="▶  Start Extraction",
                                   command=self._start_capture,
                                   bg=C["primary"], fg=C["primary_fg"],
                                   font=("Segoe UI", 13, "bold"), relief="flat",
                                   padx=24, pady=8, cursor="hand2")
        self.btn_start.pack(side=tk.LEFT, padx=(0, 8))

        self.btn_stop = tk.Button(action_frame, text="⏹ Stop",
                                  command=self._stop_capture, state=tk.DISABLED,
                                  **btn_style)
        self.btn_stop.pack(side=tk.LEFT, padx=4)

        self.auto_overwrite = tk.BooleanVar(value=True)
        tk.Checkbutton(action_frame, text="Auto overwrite", variable=self.auto_overwrite,
                       bg=C["bg"], fg=C["dim"], selectcolor=C["card"],
                       activebackground=C["bg"], font=("Segoe UI", 8)).pack(side=tk.LEFT, padx=(12, 0))

        self.topmost_var = tk.BooleanVar(value=True)
        def toggle_topmost():
            self.attributes("-topmost", self.topmost_var.get())
        toggle_topmost()
        tk.Checkbutton(action_frame, text="Stay on Top", variable=self.topmost_var,
                       command=toggle_topmost,
                       bg=C["bg"], fg=C["dim"], selectcolor=C["card"],
                       activebackground=C["bg"], font=("Segoe UI", 8)).pack(side=tk.LEFT, padx=(4, 0))

        # ---- Score Snippets (拍號 + 狀態) ----
        snippet_frame = tk.Frame(self, bg=C["bg"])
        snippet_frame.pack(fill=tk.X, padx=16, pady=4)

        # 拍號卡片
        beat_card = tk.Frame(snippet_frame, bg=C["card"], highlightbackground=C["border"],
                             highlightthickness=1, padx=12, pady=8)
        beat_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 4))

        tk.Label(beat_card, text="TEMPO & GRID", font=("Segoe UI", 8, "bold"),
                 bg=C["card"], fg=C["dim"]).pack(anchor="w")

        beat_row = tk.Frame(beat_card, bg=C["card"])
        beat_row.pack(fill=tk.X, pady=(4, 0))

        tk.Label(beat_row, text="Row Ref", bg=C["card"], fg=C["text2"],
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 4))
        tk.Button(beat_row, text="Select", command=self._select_row_ref,
                  **btn_style).pack(side=tk.LEFT, padx=1)
        tk.Button(beat_row, text="Test", command=lambda: self._test_region("chord"),
                  bg=C["card3"], fg=C["text"], font=("Segoe UI", 8),
                  relief="flat", cursor="hand2", padx=6, pady=2).pack(side=tk.LEFT, padx=(1, 6))

        tk.Label(beat_row, text="Beat:", bg=C["card"], fg=C["dim"],
                 font=("Segoe UI", 8)).pack(side=tk.LEFT, padx=(4, 2))
        self.bpb_var = tk.IntVar(value=self.cfg.get("beats_per_bar", 4))
        bpb_cb = ttk.Combobox(beat_row, textvariable=self.bpb_var,
                               values=[3, 4, 6], width=2, state="readonly")
        bpb_cb.pack(side=tk.LEFT)

        tk.Label(beat_row, text="Bar:", bg=C["card"], fg=C["dim"],
                 font=("Segoe UI", 8)).pack(side=tk.LEFT, padx=(6, 2))
        self.bpr_var = tk.IntVar(value=self.cfg.get("bars_per_row", 4))
        bpr_cb = ttk.Combobox(beat_row, textvariable=self.bpr_var,
                               values=[2, 3, 4, 8], width=2, state="readonly")
        bpr_cb.pack(side=tk.LEFT)

        # 起始休止符拍數
        beat_row2 = tk.Frame(beat_card, bg=C["card"])
        beat_row2.pack(fill=tk.X, pady=(2, 0))

        tk.Label(beat_row2, text="Start rest:", bg=C["card"], fg=C["dim"],
                 font=("Segoe UI", 8)).pack(side=tk.LEFT, padx=(0, 2))
        self.offset_var = tk.IntVar(value=self.cfg.get("start_beat_offset", 0))
        offset_cb = ttk.Combobox(beat_row2, textvariable=self.offset_var,
                                  values=[0, 1, 2, 3, 4, 8], width=2, state="readonly")
        offset_cb.pack(side=tk.LEFT)
        tk.Label(beat_row2, text="beats", bg=C["card"], fg=C["dim"],
                 font=("Segoe UI", 8)).pack(side=tk.LEFT, padx=(2, 0))

        self.cell_size_var = tk.StringVar()
        self._update_cell_display()
        tk.Label(beat_row2, textvariable=self.cell_size_var,
                 bg=C["card"], fg=C["primary"], font=("Consolas", 8, "bold"),
                 padx=6).pack(side=tk.LEFT, padx=4)

        def _on_beat_change(*_):
            self.cfg["beats_per_bar"] = self.bpb_var.get()
            self.cfg["bars_per_row"] = self.bpr_var.get()
            self.cfg["beats_per_row"] = self.bpb_var.get() * self.bpr_var.get()
            self.cfg["start_beat_offset"] = self.offset_var.get()
            save_config(self.cfg)
            self._update_cell_display()

        bpb_cb.bind("<<ComboboxSelected>>", _on_beat_change)
        bpr_cb.bind("<<ComboboxSelected>>", _on_beat_change)
        offset_cb.bind("<<ComboboxSelected>>", _on_beat_change)

        # 狀態卡片
        status_card = tk.Frame(snippet_frame, bg="#e8f5e9", highlightbackground="#c8e6c9",
                               highlightthickness=1, padx=12, pady=8)
        status_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(4, 0))

        tk.Label(status_card, text="STATUS", font=("Segoe UI", 8, "bold"),
                 bg="#e8f5e9", fg=C["dim"]).pack(anchor="w")

        self.status_var = tk.StringVar(value="Ready")
        tk.Label(status_card, textvariable=self.status_var,
                 bg="#e8f5e9", fg=C["success"], font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(2, 0))

        self.progress_var = tk.StringVar(value="Chords: 0 | Time: --:--")
        tk.Label(status_card, textvariable=self.progress_var,
                 bg="#e8f5e9", fg=C["text2"], font=("Consolas", 8)).pack(anchor="w")

        # ---- 截圖面板 ----
        shot_card = tk.Frame(self, bg=C["card2"], padx=12, pady=8)
        shot_card.pack(fill=tk.X, padx=16, pady=4)

        tk.Label(shot_card, text="SCORE SCREENSHOTS", font=("Segoe UI", 8, "bold"),
                 bg=C["card2"], fg=C["dim"]).pack(anchor="w", pady=(0, 4))

        shot_btns = tk.Frame(shot_card, bg=C["card2"])
        shot_btns.pack(fill=tk.X)

        tk.Button(shot_btns, text="📷 Capture", command=self._take_screenshot_select, **btn_style).pack(side=tk.LEFT, padx=2)
        tk.Button(shot_btns, text="🗑 Remove", command=self._remove_last_screenshot, **btn_style).pack(side=tk.LEFT, padx=2)
        tk.Button(shot_btns, text="✅ Stitch + OCR", command=self._stitch_and_ocr,
                  bg=C["primary"], fg=C["primary_fg"], font=("Segoe UI", 9, "bold"),
                  relief="flat", cursor="hand2", padx=10, pady=3).pack(side=tk.LEFT, padx=2)

        tk.Button(shot_btns, text="📝 Edit Ref", command=self._edit_chords_txt, **btn_style).pack(side=tk.LEFT, padx=2)

        self.screenshot_list_var = tk.StringVar(value="Shots: 0")
        tk.Label(shot_btns, textvariable=self.screenshot_list_var,
                 bg=C["card2"], fg=C["dim"], font=("Consolas", 9), padx=8).pack(side=tk.LEFT)

        # 縮圖
        thumb_outer = tk.Frame(shot_card, bg=C["card"], height=75)
        thumb_outer.pack(fill=tk.X, pady=(4, 0))
        thumb_outer.pack_propagate(False)

        thumb_canvas = tk.Canvas(thumb_outer, bg=C["card"], height=70, highlightthickness=0)
        thumb_scroll = tk.Scrollbar(thumb_outer, orient=tk.HORIZONTAL, command=thumb_canvas.xview)
        thumb_canvas.configure(xscrollcommand=thumb_scroll.set)
        thumb_scroll.pack(side=tk.BOTTOM, fill=tk.X)
        thumb_canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.thumb_inner = tk.Frame(thumb_canvas, bg=C["card"])
        thumb_canvas.create_window((0, 0), window=self.thumb_inner, anchor="nw")
        self.thumb_inner.bind("<Configure>",
                              lambda e: thumb_canvas.configure(scrollregion=thumb_canvas.bbox("all")))
        self.thumb_canvas = thumb_canvas
        self._thumb_refs = []

        # ---- Log ----
        log_frame = tk.Frame(self, bg=C["bg"], padx=0, pady=0)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=(4, 8))

        log_header = tk.Frame(log_frame, bg=C["bg"])
        log_header.pack(fill=tk.X, pady=(0, 4))
        tk.Label(log_header, text="EXTRACTION LOG", font=("Segoe UI", 8, "bold"),
                 bg=C["bg"], fg=C["dim"]).pack(side=tk.LEFT)
        tk.Label(log_header, text="Real-time Analysis", font=("Segoe UI", 7),
                 bg=C["bg"], fg=C["dim"]).pack(side=tk.RIGHT)

        log_container = tk.Frame(log_frame, bg=C["border"])
        log_container.pack(fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(log_container, bg=C["card3"], fg=C["text"], height=10,
                                font=("Consolas", 9), wrap=tk.WORD, state=tk.DISABLED,
                                insertbackground=C["text"], relief="flat", padx=8, pady=4)
        scrollbar = tk.Scrollbar(log_container, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # tag colors
        self.log_text.tag_configure("info", foreground=C["text2"])
        self.log_text.tag_configure("chord", foreground=C["primary"])
        self.log_text.tag_configure("state", foreground=C["success"])
        self.log_text.tag_configure("warn", foreground="#b8860b")
        self.log_text.tag_configure("error", foreground=C["error"])

    def _log(self, msg, tag="info"):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, msg + "\n", tag)
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    # ---- 格子尺寸 ----

    def _update_cell_display(self):
        row_h = self.cfg.get("row_height")
        row_w = self.cfg.get("row_width")
        bpb = self.cfg.get("beats_per_bar", 4)
        bpr = self.cfg.get("bars_per_row", 4)
        beats = bpb * bpr  # 永遠用實際計算值，不讀舊的 beats_per_row
        if row_h and row_w:
            beat_w = row_w / beats
            self.cell_size_var.set(f"列高:{row_h}px  {bpr}小節×{bpb}拍={beats}拍  每拍:{beat_w:.0f}px")
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

        # 用 dialog 詢問小節數和拍號
        beat_dialog = tk.Toplevel(self)
        beat_dialog.title("設定拍數")
        beat_dialog.geometry("320x220")
        beat_dialog.configure(bg="#1a1a2e")
        beat_dialog.attributes('-topmost', True)
        beat_dialog.grab_set()

        tk.Label(beat_dialog, text=f"框選的列: {row_w}×{row_h} px",
                 bg="#1a1a2e", fg="#888", font=("Consolas", 10)).pack(pady=(10, 5))

        # 每小節幾拍
        f1 = tk.Frame(beat_dialog, bg="#1a1a2e")
        f1.pack(pady=5)
        tk.Label(f1, text="每小節拍數:", bg="#1a1a2e", fg="#e0e0e0",
                 font=("Segoe UI", 11)).pack(side=tk.LEFT, padx=5)
        beats_per_bar_var = tk.IntVar(value=self.cfg.get("beats_per_bar", 4))
        bpb_combo = ttk.Combobox(f1, textvariable=beats_per_bar_var, values=[3, 4, 6], width=5, state="readonly")
        bpb_combo.pack(side=tk.LEFT, padx=5)

        # 每列幾小節
        f2 = tk.Frame(beat_dialog, bg="#1a1a2e")
        f2.pack(pady=5)
        tk.Label(f2, text="每列小節數:", bg="#1a1a2e", fg="#e0e0e0",
                 font=("Segoe UI", 11)).pack(side=tk.LEFT, padx=5)
        bars_per_row_var = tk.IntVar(value=self.cfg.get("bars_per_row", 4))
        bpr_combo = ttk.Combobox(f2, textvariable=bars_per_row_var, values=[2, 3, 4, 8], width=5, state="readonly")
        bpr_combo.pack(side=tk.LEFT, padx=5)

        # 即時預覽
        preview_var = tk.StringVar()
        preview_label = tk.Label(beat_dialog, textvariable=preview_var,
                                 bg="#0d0d1a", fg="#e94560", font=("Segoe UI", 11, "bold"), padx=10, pady=5)
        preview_label.pack(fill=tk.X, padx=20, pady=5)

        def update_preview(*_):
            bpb = beats_per_bar_var.get()
            bpr = bars_per_row_var.get()
            total = bpb * bpr
            bw = row_w / total
            preview_var.set(f"一列 {total} 拍 ({bpr}小節×{bpb}拍)  每拍 {bw:.0f}px")

        bpb_combo.bind("<<ComboboxSelected>>", update_preview)
        bpr_combo.bind("<<ComboboxSelected>>", update_preview)
        update_preview()

        result = [None]

        def confirm():
            result[0] = (beats_per_bar_var.get(), bars_per_row_var.get())
            beat_dialog.destroy()

        tk.Button(beat_dialog, text="確定", command=confirm,
                  bg="#e94560", fg="#fff", font=("Segoe UI", 11, "bold"),
                  relief="flat", padx=20).pack(pady=10)

        beat_dialog.wait_window()

        if not result[0]:
            self._log("  取消", "info")
            return

        beats_per_bar, bars_per_row = result[0]
        beats = beats_per_bar * bars_per_row
        beat_w = row_w / beats

        # 儲存
        self.cfg["row_height"] = row_h
        self.cfg["row_width"] = row_w
        self.cfg["beats_per_bar"] = beats_per_bar
        self.cfg["bars_per_row"] = bars_per_row
        self.cfg["beats_per_row"] = beats
        save_config(self.cfg)
        self._update_cell_display()
        self._log(f"  ✓ 列: {row_w}×{row_h}px", "state")
        self._log(f"  ✓ {bars_per_row}小節×{beats_per_bar}拍 = {beats}拍/列, 每拍 {beat_w:.0f}px", "state")

    # ---- 區域設定 ----

    def _update_region_display(self):
        for key, lbl in self.region_labels.items():
            r = self.regions.get(key)
            if r:
                lbl.configure(text=f"x={r[0]}, y={r[1]}, w={r[2]}, h={r[3]}", fg="#2d6a4f")
            else:
                lbl.configure(text="Not set", fg="#717c82")

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

            frame = tk.Frame(self.thumb_inner, bg="#d9e4ea", padx=2, pady=2)
            frame.pack(side=tk.LEFT, padx=3)

            lbl = tk.Label(frame, image=photo, bg="#ffffff")
            lbl.pack()
            tk.Label(frame, text=f"#{i+1}", bg="#d9e4ea", fg="#566166",
                     font=("Consolas", 8)).pack()

    def _edit_chords_txt(self):
        """打開和弦網格編輯器"""
        name = self.song_name.get().strip()
        lv = self.level.get()
        if not name:
            self._log("⚠ 請輸入歌曲名稱", "warn")
            return

        txt_path = TEST_SONGS_DIR / lv / f"{name}.chords.txt"
        if not txt_path.is_file():
            self._log(f"⚠ {txt_path.name} 不存在，請先 Stitch + OCR", "warn")
            return

        # 載入序列
        raw = txt_path.read_text(encoding="utf-8").strip()
        if '\t' in raw:
            beats = []
            for line in raw.split('\n'):
                parts = line.strip().split('\t')
                beats.append(parts[1] if len(parts) >= 2 else "")
        else:
            beats = [c.strip() for c in raw.split(',')]

        bpr = self.cfg.get("beats_per_row", 16)
        bpb = self.cfg.get("beats_per_bar", 4)

        ChordGridEditor(self, beats, bpr, bpb, txt_path, self._log)

    def _stitch_and_ocr(self):
        """拼接截圖 → 存 .png → OCR。若已有 .png 且無新截圖，直接 OCR"""
        name = self.song_name.get().strip()
        lv = self.level.get()
        if not name:
            messagebox.showwarning("提示", "請輸入歌曲名稱")
            return

        save_dir = TEST_SONGS_DIR / lv
        save_dir.mkdir(parents=True, exist_ok=True)
        png_path = save_dir / f"{name}.png"

        if self.screenshots:
            # 有新截圖 → 拼接並存檔
            total_h = sum(s.height for s in self.screenshots)
            max_w = max(s.width for s in self.screenshots)
            combined = Image.new("RGB", (max_w, total_h), (255, 255, 255))
            y = 0
            for s in self.screenshots:
                combined.paste(s, (0, y))
                y += s.height
            png_path.write_bytes(b"")  # 先清空
            combined.save(str(png_path))
            self._log(f"✓ 已儲存 {png_path.name} ({max_w}×{total_h}, {len(self.screenshots)} 頁)", "state")
        elif png_path.is_file():
            # 無新截圖但已有 .png → 直接用現有的 OCR
            self._log(f"📂 使用現有 {png_path.name}，直接 OCR", "info")
        else:
            self._log("⚠ 無截圖且無現有 .png，請先擷取", "warn")
            return

        # 2. 逐格 OCR 辨識和弦序列
        row_w = self.cfg.get("row_width")
        row_h = self.cfg.get("row_height")
        beats = self.cfg.get("beats_per_row", 8)
        if not row_w or not row_h:
            self._log("⚠ 請先「框選一列」設定列參照", "warn")
            return

        cell_w = row_w  # 傳入整列寬度，OCR 內部會除以 beats
        cell_h = row_h

        self._safe_log(f"🔍 逐格 OCR（{beats}拍/列, 每拍 {row_w/beats:.0f}px）...", "info")
        self.status_var.set("🔍 逐格 OCR 辨識中...")

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
            if not self.auto_overwrite.get() and not messagebox.askyesno("已存在", f"已有 {name}.lab，要覆蓋嗎？"):
                return

        # 記住歌曲選擇
        self.cfg["last_song"] = name
        self.cfg["last_level"] = lv
        save_config(self.cfg)

        # 重置
        self.records = []
        self.screenshots = []
        self.ref_chords = []
        self._ref_sequence = []  # 從 .chords.txt 載入的參照序列
        self._ref_idx = 0

        # 自動載入 .chords.txt 作為和弦參照（截圖 OCR 的結果）
        chords_txt = TEST_SONGS_DIR / lv / f"{name}.chords.txt"
        if chords_txt.is_file():
            raw = chords_txt.read_text(encoding="utf-8").strip()
            # 格式可能是 "time\tchord" 逐行，或 "chord,chord,..." 逗號分隔
            if '\t' in raw:
                # time\tchord 格式
                for line in raw.split('\n'):
                    parts = line.strip().split('\t')
                    if len(parts) >= 2 and parts[1]:
                        self._ref_sequence.append(parts[1])
            else:
                # 逗號分隔格式（逐拍，空 = 延續）
                for c in raw.split(','):
                    c = c.strip()
                    if c:
                        self._ref_sequence.append(c)

            if self._ref_sequence:
                self._log(f"📋 載入參照序列: {len(self._ref_sequence)} 個和弦 (從 .chords.txt)", "state")
                self._log(f"   → 擷取時只綁定時間，不做 OCR（更快更準）", "info")

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
            while True:
                try:
                    item = ocr_queue.get(timeout=0.1)
                except queue.Empty:
                    if shared["stop"] or not self.capturing:
                        break
                    continue

                chord_img, precise_time, jump_beats = item

                try:
                    # 漏拍補償
                    skipped = jump_beats
                    skipped = max(0, min(skipped, 5))

                    if skipped > 0:
                        prev_time = self.records[-1][0] if self.records else precise_time
                        beat_dur = (precise_time - prev_time) / (skipped + 1)
                        for si in range(skipped):
                            st = prev_time + beat_dur * (si + 1)
                            # 有參照序列就用它補，否則標記為 skip
                            if self._ref_sequence and self._ref_idx < len(self._ref_sequence):
                                skip_chord = self._ref_sequence[self._ref_idx]
                                self._ref_idx += 1
                                self.records.append((round(st, 3), skip_chord))
                                self._safe_log(f"  {int(st//60)}:{int(st%60):02d}.{int((st%1)*1000):03d}  {skip_chord}  📋補", "warn")
                            else:
                                self.records.append((round(st, 3), f"({skipped}skip)"))
                                self._safe_log(f"  {int(st//60)}:{int(st%60):02d}.{int((st%1)*1000):03d}  ({skipped}skip)", "warn")

                    # 和弦來源：優先用參照序列（截圖 OCR），否則即時 OCR
                    if self._ref_sequence and self._ref_idx < len(self._ref_sequence):
                        # 從預載的 .chords.txt 取和弦（100% 準確，0ms）
                        chord = self._ref_sequence[self._ref_idx]
                        self._ref_idx += 1
                        source = "📋"
                    else:
                        # 即時 OCR（慢，但不阻塞 producer）
                        chord, _ = find_highlighted_chord(chord_img)
                        source = "🔍"

                    if chord:
                        self.records.append((round(precise_time, 3), chord))
                        shared["last_chord"] = chord
                        self.ref_chords.append(chord)

                        m = int(precise_time // 60)
                        s = int(precise_time % 60)
                        ms = int((precise_time % 1) * 1000)
                        count = len(self.records)
                        total_ref = len(self._ref_sequence) if self._ref_sequence else "?"
                        self._safe_log(f"  {m}:{s:02d}.{ms:03d}  {chord}  {source}", "chord")
                        self._safe_progress(f"和弦: {count}/{total_ref} | "
                                            f"時間: {m}:{s:02d}.{ms:03d}")
                except Exception:
                    pass

                ocr_queue.task_done()

        # ---- 啟動 consumer + time OCR 線程 ----
        t_ocr = threading.Thread(target=ocr_consumer_thread, daemon=True)
        t_time = threading.Thread(target=time_ocr_thread, daemon=True)
        t_ocr.start()
        t_time.start()

        # ---- Producer: 精確格子邊界判斷 ----
        screenshot_interval = 0
        current_grid_idx = -1
        current_row_idx = -1
        is_first_row = True

        # 載入格子邊界（轉換為相對於截圖寬度的比例）
        beat_bounds_raw = self.cfg.get("beat_boundaries")
        row_h = self.cfg.get("row_height", 112)
        start_offset = self.cfg.get("start_beat_offset", 0)

        # 實際截圖寬度
        chord_w = chord_r[2]  # 和弦區域的寬度

        # 把 GIMP 參照圖的座標轉為實際截圖的座標（按比例縮放）
        beat_bounds = None
        if beat_bounds_raw and len(beat_bounds_raw) > 0:
            ref_w = beat_bounds_raw[-1][1]  # 參照圖的總寬度
            scale = chord_w / ref_w if ref_w > 0 else 1.0
            beat_bounds = [(int(x1 * scale), int(x2 * scale)) for x1, x2 in beat_bounds_raw]
            self._safe_log(f"  格子邊界: {len(beat_bounds)} 拍, 縮放 {scale:.3f} (ref={ref_w} → actual={chord_w})", "info")

        # 滯後 = 格寬的 40%
        avg_beat_w = chord_w / self.cfg.get("beats_per_row", 16) if not beat_bounds else \
                     (beat_bounds[-1][1] - beat_bounds[0][0]) / len(beat_bounds)
        HYSTERESIS = int(avg_beat_w * 0.4)

        def _cx_to_grid(cx):
            """用精確邊界表查詢 cx 在第幾格（含滯後防抖）"""
            if beat_bounds:
                for i, (x1, x2) in enumerate(beat_bounds):
                    # 加入滯後：如果目前在格 i，只有 cx 超過 x2 + HYSTERESIS 才算離開
                    # 但如果尚未進入任何格子（首次），用正常邊界
                    if x1 <= cx < x2:
                        return i
                if cx >= beat_bounds[-1][1]:
                    return len(beat_bounds) - 1
                return 0
            else:
                return int(cx / grid_width) if grid_width > 0 else 0

        def _should_advance(cx, current_idx):
            """判斷是否該前進到下一格（含滯後）"""
            if current_idx < 0:
                return True, _cx_to_grid(cx)
            if not beat_bounds:
                new = int(cx / grid_width) if grid_width > 0 else 0
                return new != current_idx, new

            # 目前格子的右邊界
            _, cur_right = beat_bounds[current_idx] if current_idx < len(beat_bounds) else (0, 0)

            # 只有 cx 超過目前格子右邊界 + 滯後才算跨格
            if cx > cur_right + HYSTERESIS:
                new_grid = _cx_to_grid(cx)
                return new_grid != current_idx, new_grid

            return False, current_idx

        while self.capturing and not shared["stop"]:
            try:
                now = time.perf_counter()

                chord_img = capture_region(chord_r)
                center = _find_dark_block_center(chord_img)

                new_beat = False
                jump_beats = 0

                if center:
                    cx, cy = center
                    new_row = int(cy / row_h) if row_h > 0 else 0

                    if current_grid_idx < 0:
                        # 第一次偵測
                        new_grid = _cx_to_grid(cx)
                        current_grid_idx = new_grid
                        current_row_idx = new_row
                        # 第一列的休止符拍：方塊經過但不記錄
                        if is_first_row and start_offset > 0 and new_grid < start_offset:
                            self._safe_log(f"  (休止符 beat {new_grid+1}，等待 beat {start_offset+1})", "info")
                        else:
                            new_beat = True
                    elif new_row != current_row_idx:
                        # 換行
                        new_beat = True
                        jump_beats = 0
                        current_grid_idx = _cx_to_grid(cx)
                        current_row_idx = new_row
                        is_first_row = False
                    else:
                        # 同行：用滯後判斷是否跨格
                        advanced, new_grid = _should_advance(cx, current_grid_idx)
                        if advanced and new_grid > current_grid_idx:
                            # 第一列休止符 → 有和弦的過渡
                            if is_first_row and start_offset > 0 and current_grid_idx < start_offset:
                                if new_grid >= start_offset:
                                    # 從休止符區進入有和弦區 → 第一個真正的和弦
                                    new_beat = True
                                    current_grid_idx = new_grid
                                    self._safe_log(f"  (音樂開始 beat {new_grid+1})", "state")
                                else:
                                    # 還在休止符區內移動
                                    current_grid_idx = new_grid
                            else:
                                new_beat = True
                                jump_beats = max(0, new_grid - current_grid_idx - 1)
                                current_grid_idx = new_grid
                    # else: 同一格內滑動 → 忽略

                if not new_beat:
                    time.sleep(0.03)
                    continue

                # ---- 跨格了！計算精確時間 ----
                if shared["last_ocr_sec"] is not None and shared["last_ocr_perf"] is not None:
                    precise_time = shared["last_ocr_sec"] + (now - shared["last_ocr_perf"])
                elif shared["last_time_sec"] is not None:
                    precise_time = float(shared["last_time_sec"])
                else:
                    precise_time = 0.0

                # 放入 buffer（jump_beats 用於漏拍補償）
                try:
                    ocr_queue.put_nowait((chord_img.copy(), precise_time, jump_beats))
                except queue.Full:
                    pass

                # 定期截圖
                screenshot_interval += 1
                if screenshot_interval >= 30:  # 每 30 拍截一次
                    screenshot_interval = 0
                    self.screenshots.append(chord_img.copy())

            except Exception:
                pass

            time.sleep(0.03)  # 30ms — producer 不受 OCR 拖累

        # ---- 等 consumer 處理完 buffer 剩餘（最多 5 秒防死鎖）----
        shared["stop"] = True
        end_wait = time.time() + 5.0
        while not ocr_queue.empty() and time.time() < end_wait:
            time.sleep(0.1)
        # 額外等 0.5 秒讓 consumer 完成最後一項 OCR
        time.sleep(0.5)

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

        # 儲存擷取紀錄（不覆蓋 .chords.txt — 那是 OCR 基準資料）
        if self.records:
            capture_txt = save_dir / f"{name}.capture.txt"
            capture_txt.write_text("\n".join(
                f"{t:.3f}\t{c}" for t, c in sorted(set(self.records))
            ), encoding="utf-8")
            self._safe_log(f"✓ 已儲存 {capture_txt.name}", "state")

        # 拼接截圖為完整 .png
        if self.screenshots:
            total_h = sum(s.height for s in self.screenshots)
            max_w = max(s.width for s in self.screenshots)
            combined = Image.new("RGB", (max_w, total_h), (255, 255, 255))
            y = 0
            for s in self.screenshots:
                combined.paste(s, (0, y))
                y += s.height
            # 存為 .capture-screenshots.png（不覆蓋 OCR 基準 .png）
            cap_png = save_dir / f"{name}.capture-screenshots.png"
            combined.save(str(cap_png))
            self._safe_log(f"✓ 已儲存 {cap_png.name} ({max_w}×{total_h}, {len(self.screenshots)} 段)", "state")

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
