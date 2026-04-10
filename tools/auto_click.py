"""
LiveChord 自動下載工具 (GUI)
Stay on top
1. pic1/pic2 點擊（Chordify 下載 MIDI）
2. yt-dlp 下載 FLAC
3. 以 FLAC 檔名重命名 .mid → X:\
4. .flac → Y:\
"""

import tkinter as tk
from tkinter import scrolledtext
import threading
import time
import subprocess
import sys
import os
import re
import shutil
import glob

import pyautogui
import pyperclip

# 設定 — 打包成 exe 時用 exe 所在目錄
if getattr(sys, 'frozen', False):
    TOOL_DIR = os.path.dirname(sys.executable)
else:
    TOOL_DIR = os.path.dirname(os.path.abspath(__file__))

PIC1 = os.path.join(TOOL_DIR, "pic1.jpg")
PIC2 = os.path.join(TOOL_DIR, "pic2.jpg")
CONFIDENCE = 0.8
TIMEOUT = 10

# yt-dlp
YT_DLP_EXE = r"E:\MyMusic\yt-dlp.exe"
YT_DLP_CWD = r"E:\MyMusic"

# 檔案路徑
MID_SRC = os.path.expanduser("~/Downloads")
MID_DST = "X:/"
FLAC_SRC = r"E:\MyMusic"
FLAC_DST = "Y:/"


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("LiveChord Downloader")
        self.root.geometry("520x420")
        self.root.attributes("-topmost", True)
        self.root.configure(bg="#1a1a2e")
        self.root.resizable(True, True)

        # 按鈕區
        btn_frame = tk.Frame(self.root, bg="#1a1a2e")
        btn_frame.pack(fill="x", padx=10, pady=8)

        self.btn_run = tk.Button(
            btn_frame, text="\u25b6  \u958b\u59cb", font=("Segoe UI", 13, "bold"),
            bg="#e94560", fg="white", activebackground="#c73e54",
            relief="flat", padx=20, pady=6, cursor="hand2",
            command=self.on_run,
        )
        self.btn_run.pack(side="left")

        self.status = tk.Label(
            btn_frame, text="\u5c31\u7dd2", font=("Segoe UI", 10),
            bg="#1a1a2e", fg="#999", anchor="w",
        )
        self.status.pack(side="left", padx=12)

        # 網址區（自動偵測剪貼簿）
        url_frame = tk.Frame(self.root, bg="#1a1a2e")
        url_frame.pack(fill="x", padx=10, pady=(0, 6))

        tk.Label(url_frame, text="URL:", font=("Segoe UI", 10),
                 bg="#1a1a2e", fg="#999").pack(side="left")

        self.url_var = tk.StringVar()
        self.url_entry = tk.Entry(
            url_frame, textvariable=self.url_var, font=("Consolas", 10),
            bg="#0a0a0a", fg="#4caf50", insertbackground="#4caf50",
            relief="flat", selectbackground="#e94560",
        )
        self.url_entry.pack(side="left", fill="x", expand=True, padx=(6, 0))

        # 開始監聽剪貼簿
        self._last_clip = ""
        self._poll_clipboard()

        # Log 區
        self.log = scrolledtext.ScrolledText(
            self.root, font=("Consolas", 9), bg="#0a0a0a", fg="#ccc",
            insertbackground="#ccc", relief="flat", wrap="word",
        )
        self.log.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.log.configure(state="disabled")

        self._print("LiveChord Downloader")
        self._print(f"  yt-dlp: {YT_DLP_EXE}")
        self._print(f"  MIDI: {MID_SRC} -> X:/")
        self._print(f"  FLAC: {FLAC_SRC} -> Y:/")
        self._print("")
        self._print("流程：複製 YouTube 網址 -> 按「開始」")
        self._print("  1. pic1/pic2（Chordify 下載 MIDI）")
        self._print("  2. yt-dlp 下載 FLAC")
        self._print("  3. 以 FLAC 檔名重命名 .mid -> X:/")
        self._print("  4. .flac -> Y:/")
        self._print("")

    def _poll_clipboard(self):
        try:
            clip = pyperclip.paste()
            if clip != self._last_clip:
                self._last_clip = clip
                if clip and ("youtube.com" in clip or "youtu.be" in clip or "chordify.net" in clip):
                    self.url_var.set(self._clean_youtube_url(clip))
                    vid = self._extract_video_id(clip)
                    if vid:
                        self._set_status(f"ID: {vid}", "#4caf50")
        except Exception:
            pass
        self.root.after(500, self._poll_clipboard)

    def _print(self, msg):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_status(self, text, color="#999"):
        self.status.configure(text=text, fg=color)

    def on_run(self):
        self.btn_run.configure(state="disabled", bg="#666")
        threading.Thread(target=self._run_task, daemon=True).start()

    def _run_task(self):
        try:
            self._do_run()
        except Exception as e:
            self._print(f"[ERROR] {e}")
            self._set_status("\u932f\u8aa4", "#f44")
        finally:
            self.root.after(0, lambda: self.btn_run.configure(state="normal", bg="#e94560"))

    def _do_run(self):
        url = self._clean_youtube_url(self.url_var.get().strip())
        self.url_var.set(url)
        if not url:
            self._print("[ERROR] URL 為空，請先複製 YouTube 網址")
            self._set_status("URL 為空", "#f44")
            return

        self._print("=" * 50)

        # === Part 1: pic1 → pic2（Chordify 下載 MIDI）===
        self._print("[Part 1] 下載 MIDI")

        self._set_status("pic1...", "#ff9800")
        self._print("  搜尋 pic1...")
        if not self._find_and_click(PIC1, "pic1"):
            self._print("  pic1 未找到，跳過")

        time.sleep(1)

        self._set_status("pic2...", "#ff9800")
        self._print("  搜尋 pic2...")
        if not self._find_and_click(PIC2, "pic2"):
            self._print("  pic2 未找到，跳過")

        # 等待 MIDI 下載（最多 30 秒）
        self._set_status("等待 MIDI...", "#ff9800")
        existing_mids = set(glob.glob(os.path.join(MID_SRC, "*.mid")) + glob.glob(os.path.join(MID_SRC, "*.midi")))
        self._print("  等待 MIDI 下載...")
        for i in range(30):
            current = set(glob.glob(os.path.join(MID_SRC, "*.mid")) + glob.glob(os.path.join(MID_SRC, "*.midi")))
            if current - existing_mids:
                time.sleep(1)
                self._print("  MIDI 下載完成")
                break
            time.sleep(1)
        else:
            self._print("  MIDI 等待逾時，繼續...")

        # === Part 2: yt-dlp 下載 FLAC ===
        self._print(f"\n[Part 2] yt-dlp 下載 FLAC")
        self._set_status("yt-dlp...", "#4caf50")

        existing_flacs = set(glob.glob(os.path.join(FLAC_SRC, "*.flac")))

        cmd = f'"{YT_DLP_EXE}" --config-location track.conf "{url}"'
        self._print(f"  {cmd}\n")

        proc = subprocess.Popen(
            cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            cwd=YT_DLP_CWD, text=True, encoding="utf-8", errors="replace",
        )
        for line in proc.stdout:
            self._print(line.rstrip())
        proc.wait()

        if proc.returncode != 0:
            self._print(f"\n[ERROR] yt-dlp 返回碼: {proc.returncode}")
            self._set_status(f"yt-dlp 失敗 ({proc.returncode})", "#f44")
            return

        self._print("\n  [OK] yt-dlp 完成")

        # === Part 3: 找新 FLAC，重命名 MIDI，移動兩者 ===
        self._print("\n[Part 3] 整理檔案")
        self._set_status("整理檔案...", "#ff9800")

        # 找新下載的 .flac
        current_flacs = set(glob.glob(os.path.join(FLAC_SRC, "*.flac")))
        new_flacs = current_flacs - existing_flacs
        if new_flacs:
            flac_file = max(new_flacs, key=os.path.getmtime)
        else:
            flac_file = self._find_newest(FLAC_SRC, ("*.flac",))

        if not flac_file:
            self._print("  沒有找到 .flac")
            self._set_status("無 FLAC", "#f44")
            return

        flac_name = os.path.splitext(os.path.basename(flac_file))[0]
        self._print(f"  FLAC: {os.path.basename(flac_file)}")

        # 找最新 .mid，用 FLAC 檔名重命名
        mid_file = self._find_newest(MID_SRC, ("*.mid", "*.midi"))
        if mid_file:
            mid_ext = os.path.splitext(mid_file)[1]
            old_mid_name = os.path.basename(mid_file)
            new_mid_name = flac_name + mid_ext
            self._print(f"  MIDI: {old_mid_name}")
            self._print(f"  重命名: {old_mid_name} -> {new_mid_name}")

            mid_dst = os.path.join(MID_DST, new_mid_name)
            if os.path.isfile(mid_dst):
                self._print(f"  X:/ 已存在 {new_mid_name}，跳過")
            else:
                shutil.copy2(mid_file, mid_dst)
                os.remove(mid_file)
                self._print(f"  [OK] {new_mid_name} -> X:/")
        else:
            self._print("  Downloads 沒有 .mid")

        # 移動 .flac → Y:\
        flac_dst = os.path.join(FLAC_DST, os.path.basename(flac_file))
        if os.path.isfile(flac_dst):
            self._print(f"  Y:/ 已存在 {os.path.basename(flac_file)}，跳過")
        else:
            shutil.copy2(flac_file, flac_dst)
            os.remove(flac_file)
            self._print(f"  [OK] {os.path.basename(flac_file)} -> Y:/")

        self._print("\n[DONE] 全部完成!")
        self._set_status("完成!", "#4caf50")

    def _find_newest(self, directory, patterns):
        files = []
        for pat in patterns:
            files.extend(glob.glob(os.path.join(directory, pat)))
        if not files:
            return None
        return max(files, key=os.path.getmtime)

    def _find_and_click(self, image_path, label):
        if not os.path.isfile(image_path):
            self._print(f"  [SKIP] 圖片不存在: {image_path}")
            return False

        start = time.time()
        while time.time() - start < TIMEOUT:
            try:
                loc = pyautogui.locateOnScreen(image_path, confidence=CONFIDENCE)
                if loc:
                    center = pyautogui.center(loc)
                    pyautogui.click(center)
                    self._print(f"  {label} OK ({center.x}, {center.y})")
                    return True
            except pyautogui.ImageNotFoundException:
                pass
            except Exception as e:
                self._print(f"  [WARN] {e}")
            time.sleep(0.5)
        return False

    def _clean_youtube_url(self, url):
        """Strip playlist/radio params, keep only ?v=VIDEO_ID."""
        vid = self._extract_video_id(url)
        if vid and ("youtube.com" in url or "youtu.be" in url):
            return f"https://www.youtube.com/watch?v={vid}"
        return url

    def _extract_video_id(self, text):
        m = re.search(r"[?&]v=([a-zA-Z0-9_-]+)", text)
        if m:
            return m.group(1)
        m = re.search(r"youtu\.be/([a-zA-Z0-9_-]+)", text)
        if m:
            return m.group(1)
        m = re.search(r"youtube:([a-zA-Z0-9_-]+)", text)
        if m:
            return m.group(1)
        return None

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    App().run()
