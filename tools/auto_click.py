"""
自動化小工具：取得瀏覽器網址 → 搜尋圖案點擊 → 執行 yt-dlp
使用方式：python auto_click.py
"""

import time
import subprocess
import sys
import os
import re

import pyautogui
import pyperclip

# 設定
TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
PIC1 = os.path.join(TOOL_DIR, "pic1.jpg")
PIC2 = os.path.join(TOOL_DIR, "pic2.jpg")
CONFIDENCE = 0.8  # 圖片比對信心度 (0~1)
TIMEOUT = 10      # 搜尋圖片最多等幾秒

# yt-dlp 路徑與指令模板
YT_DLP_EXE = r"E:\MyMusic\yt-dlp.exe"
YT_DLP_CMD = '"{exe}" --config-location track.conf "https://www.youtube.com/watch?v={video_id}"'


def switch_to_browser():
    """切換到瀏覽器視窗"""
    try:
        import ctypes
        import ctypes.wintypes

        EnumWindows = ctypes.windll.user32.EnumWindows
        GetWindowTextW = ctypes.windll.user32.GetWindowTextW
        SetForegroundWindow = ctypes.windll.user32.SetForegroundWindow
        ShowWindow = ctypes.windll.user32.ShowWindow

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
        found = [None]

        def callback(hwnd, _):
            buf = ctypes.create_unicode_buffer(256)
            GetWindowTextW(hwnd, buf, 256)
            title = buf.value.lower()
            if any(b in title for b in ["chrome", "edge", "firefox", "brave", "opera"]):
                found[0] = hwnd
                return False  # stop
            return True

        EnumWindows(WNDENUMPROC(callback), 0)
        if found[0]:
            ShowWindow(found[0], 9)  # SW_RESTORE
            SetForegroundWindow(found[0])
            time.sleep(0.5)
            return True
    except Exception:
        pass
    # fallback
    pyautogui.hotkey("alt", "tab")
    time.sleep(0.5)
    return True


def get_browser_url():
    """從瀏覽器取得網址"""
    old_clip = pyperclip.paste()

    pyautogui.hotkey("ctrl", "l")
    time.sleep(0.3)
    pyautogui.hotkey("ctrl", "c")
    time.sleep(0.3)

    url = pyperclip.paste()

    try:
        pyperclip.copy(old_clip)
    except Exception:
        pass

    pyautogui.press("escape")
    return url


def extract_video_id(url):
    """從 YouTube 網址取得 video ID"""
    # https://www.youtube.com/watch?v=dQw4w9WgXcQ
    m = re.search(r"[?&]v=([a-zA-Z0-9_-]+)", url)
    if m:
        return m.group(1)
    # https://youtu.be/dQw4w9WgXcQ
    m = re.search(r"youtu\.be/([a-zA-Z0-9_-]+)", url)
    if m:
        return m.group(1)
    return None


def find_and_click(image_path, label=""):
    """搜尋螢幕上的圖片並點擊"""
    if not os.path.isfile(image_path):
        print(f"  [SKIP] 找不到圖片: {image_path}")
        return False

    print(f"  搜尋 {label or os.path.basename(image_path)}...", end=" ", flush=True)

    start = time.time()
    while time.time() - start < TIMEOUT:
        try:
            location = pyautogui.locateOnScreen(image_path, confidence=CONFIDENCE)
            if location:
                center = pyautogui.center(location)
                pyautogui.click(center)
                print(f"OK ({center.x}, {center.y})")
                return True
        except pyautogui.ImageNotFoundException:
            pass
        except Exception as e:
            print(f"\n  [WARN] {e}")
        time.sleep(0.5)

    print("超時未找到")
    return False


def run_ytdlp(video_id):
    """執行 yt-dlp 下載"""
    cmd = YT_DLP_CMD.replace("{exe}", YT_DLP_EXE).replace("{video_id}", video_id)
    print(f"\n  指令: {cmd}\n")
    print("=" * 60)

    # 在 yt-dlp 所在目錄執行（讀取 track.conf）
    cwd = os.path.dirname(YT_DLP_EXE)
    result = subprocess.run(cmd, shell=True, capture_output=False, cwd=cwd)

    print("=" * 60)
    if result.returncode == 0:
        print("\n[OK] 下載完成!")
    else:
        print(f"\n[ERROR] yt-dlp 返回碼: {result.returncode}")
    return result.returncode


def main():
    print("=" * 60)
    print("  LiveChord 自動化工具")
    print("  1. 取得瀏覽器網址 (YouTube video ID)")
    print("  2. 搜尋 pic1 → 點擊")
    print("  3. 搜尋 pic2 → 點擊")
    print("  4. 執行 yt-dlp 下載")
    print("=" * 60)

    input("\n請開啟瀏覽器 YouTube 頁面，按 Enter 開始...")

    # Step 1: 切換到瀏覽器 + 取得網址
    print("\n[Step 1] 切換到瀏覽器，取得網址...")
    switch_to_browser()
    time.sleep(0.5)
    url = get_browser_url()
    print(f"  網址: {url}")

    video_id = extract_video_id(url)
    if not video_id:
        print("[ERROR] 無法從網址擷取 YouTube video ID")
        print("  請確認網址是 YouTube 頁面")
        return

    print(f"  Video ID: {video_id}")

    # Step 2: 搜尋 pic1 並點擊
    print("\n[Step 2] 搜尋 pic1...")
    find_and_click(PIC1, "pic1")
    time.sleep(1)

    # Step 3: 搜尋 pic2 並點擊
    print("\n[Step 3] 搜尋 pic2...")
    find_and_click(PIC2, "pic2")
    time.sleep(1)

    # Step 4: 執行 yt-dlp
    print("\n[Step 4] 執行 yt-dlp...")
    run_ytdlp(video_id)

    print("\n完成!")


if __name__ == "__main__":
    main()
