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
            norm = text.replace("min", "m").replace("MIN", "m").replace("Maj", "maj")
            if norm and norm[0].islower():
                norm = norm[0].upper() + norm[1:]
            return norm, (cx, cy)
    return None, (cx, cy)


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

        self.btn_screenshot = tk.Button(ctrl, text="📷 擷取當前畫面", command=self._take_screenshot, **btn_style)
        self.btn_screenshot.pack(side=tk.LEFT, padx=5)

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

    def _take_screenshot(self):
        r = self.regions.get("chord")
        if not r:
            self._log("⚠ 請先設定和弦區域", "warn")
            return
        img = capture_region(tuple(r))
        self.screenshots.append(img)
        self._log(f"📷 擷取截圖 #{len(self.screenshots)} ({img.size[0]}×{img.size[1]})", "info")
        self.progress_var.set(f"和弦: {len(self.records)} | 截圖: {len(self.screenshots)} | 時間: --:--")

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

        # 時間策略：直接用 Chordify OCR 時間（整數秒）作為基準，
        # 同一秒內用 perf_counter 內插毫秒精度。
        # 這樣時間永遠和 Chordify 同步，不受點擊延遲影響。
        last_ocr_sec = None       # 上次 OCR 讀到的整數秒
        last_ocr_perf = None      # 讀到那個整數秒時的 perf_counter
        last_chord = None
        last_center = None
        last_time_sec = None
        time_stall_count = 0
        screenshot_interval = 0

        # Phase 2: 持續擷取（用 Chordify 時間判斷結束）
        while self.capturing:
            try:
                # OCR 目前時間（Chordify 顯示的秒數）
                time_img = capture_region(time_r)
                current_sec = ocr_time(time_img)

                if current_sec is not None:
                    # 更新 OCR 時間基準（每次讀到新秒數就校正）
                    if last_ocr_sec is None or current_sec != last_ocr_sec:
                        last_ocr_sec = current_sec
                        last_ocr_perf = time.perf_counter()

                    # 結束判斷 1: 目前時間 ≥ 總長度 - 2 秒
                    if current_sec >= total_duration - 2:
                        m, s = divmod(current_sec, 60)
                        self._safe_log(f"⏹ 到達歌曲結尾 ({m}:{s:02d})", "state")
                        break

                    # 結束判斷 2: 時間停滯超過 5 秒
                    if last_time_sec is not None:
                        if current_sec <= last_time_sec:
                            time_stall_count += 1
                            if time_stall_count >= 50:
                                self._safe_log("⏹ 時間停滯 5 秒，判定播放結束", "state")
                                break
                        else:
                            time_stall_count = 0

                    last_time_sec = current_sec

                # 和弦偵測
                chord_img = capture_region(chord_r)
                chord, center = find_highlighted_chord(chord_img)

                # 定期截圖（每 50 次 ≈ 5 秒）
                screenshot_interval += 1
                if screenshot_interval >= 50:
                    screenshot_interval = 0
                    self.screenshots.append(chord_img.copy())

                # 方塊移動偵測
                box_moved = False
                if center:
                    if last_center is None:
                        box_moved = True
                    else:
                        dx = abs(center[0] - last_center[0])
                        dy = abs(center[1] - last_center[1])
                        if dx > 15 or dy > 15:
                            box_moved = True
                    if box_moved:
                        last_center = center

                if box_moved and chord and chord != last_chord:
                    # 精確時間 = OCR 整數秒 + perf_counter 內插毫秒
                    if last_ocr_sec is not None and last_ocr_perf is not None:
                        sub_sec = time.perf_counter() - last_ocr_perf
                        precise_time = last_ocr_sec + sub_sec
                    elif current_sec is not None:
                        precise_time = float(current_sec)
                    else:
                        precise_time = 0.0

                    self.records.append((round(precise_time, 3), chord))
                    last_chord = chord
                    self.ref_chords.append(chord)

                    m = int(precise_time // 60)
                    s = int(precise_time % 60)
                    ms = int((precise_time % 1) * 1000)
                    count = len(self.records)
                    self._safe_log(f"  {m}:{s:02d}.{ms:03d}  {chord}", "chord")
                    self._safe_progress(f"和弦: {count} | 截圖: {len(self.screenshots)} | "
                                        f"時間: {m}:{s:02d}.{ms:03d}")

            except Exception as e:
                self._safe_log(f"⚠ {e}", "warn")

            time.sleep(0.1)

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
