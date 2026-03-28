"""
Chordify Web 擷取工具（Playwright）
====================================
透過瀏覽器自動化讀取 Chordify 頁面的和弦資料。

使用方式:
  python chordify_web.py "https://chordify.net/chords/dancing-queen-abba" "Dancing Queen" Lv1

或互動模式:
  python chordify_web.py
"""

import sys
import os
import json
import time

from playwright.sync_api import sync_playwright


def extract_chords_from_chordify(url: str, timeout: int = 30) -> dict:
    """
    用 Playwright 開啟 Chordify 頁面，模擬播放並擷取和弦時間軸。
    """
    print(f"  開啟: {url}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)  # 顯示瀏覽器讓使用者操作
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=60000)

        print("  頁面已載入")
        print()
        print("  ╔══════════════════════════════════════════════════╗")
        print("  ║  請在瀏覽器中：                                  ║")
        print("  ║  1. 確認歌曲已載入（看到和弦網格）               ║")
        print("  ║  2. 按下播放按鈕                                 ║")
        print("  ║  3. 等歌曲播完（或播到你需要的位置）             ║")
        print("  ║  4. 回到此視窗按 Enter 開始擷取                  ║")
        print("  ╚══════════════════════════════════════════════════╝")
        input("\n  播放完成後按 Enter 擷取和弦資料...")

        # 從 DOM 提取和弦資料
        # Chordify 的和弦資料在頁面的特定 DOM 結構中
        chords_data = page.evaluate("""
        () => {
            const results = [];

            // 方法 1: 從 chord-diagram 元素提取
            const diagrams = document.querySelectorAll('[data-chord]');
            diagrams.forEach(el => {
                results.push({
                    chord: el.getAttribute('data-chord'),
                    time: el.getAttribute('data-time') || '',
                });
            });

            // 方法 2: 從 chord overview 格子提取
            if (results.length === 0) {
                const cells = document.querySelectorAll('.chord-cell, .chord-name, [class*="chord"]');
                cells.forEach(el => {
                    const text = el.textContent.trim();
                    if (text && /^[A-G]/.test(text) && text.length <= 10) {
                        results.push({ chord: text, time: '' });
                    }
                });
            }

            // 方法 3: 取所有可見文字，過濾出和弦
            if (results.length === 0) {
                const walker = document.createTreeWalker(
                    document.body, NodeFilter.SHOW_TEXT, null, false
                );
                while (walker.nextNode()) {
                    const text = walker.currentNode.textContent.trim();
                    if (/^[A-G][#b]?(m|maj|dim|aug|sus|7|9|6|\/)?/.test(text) && text.length <= 10) {
                        const rect = walker.currentNode.parentElement.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            results.push({
                                chord: text,
                                time: '',
                                x: rect.x,
                                y: rect.y,
                            });
                        }
                    }
                }
            }

            // 取歌曲資訊
            const title = document.querySelector('h1')?.textContent?.trim() || '';
            const artist = document.querySelector('[class*="artist"]')?.textContent?.trim() || '';

            // 取歌曲長度
            const duration = document.querySelector('[class*="duration"], [class*="time"]');
            const durationText = duration ? duration.textContent.trim() : '';

            return { chords: results, title, artist, durationText };
        }
        """)

        # 也嘗試截圖保存
        screenshot_path = os.path.join(os.path.dirname(__file__), "..", "data", "test_songs", "_chordify_screenshot.png")
        page.screenshot(path=screenshot_path, full_page=True)
        print(f"  截圖已儲存: {screenshot_path}")

        browser.close()

    return chords_data


def save_lab(chords: list, song_name: str, level: str, duration: float = 240):
    """儲存為 .lab 格式"""
    if not chords:
        print("  未提取到和弦")
        return

    # 過濾有效和弦
    valid_chords = [c["chord"] for c in chords if c.get("chord")]

    # 如果有時間資訊，使用之；否則估算
    has_time = any(c.get("time") for c in chords)

    entries = []
    if has_time:
        for c in chords:
            if c.get("time"):
                try:
                    t = float(c["time"])
                    entries.append({"time": t, "chord": c["chord"]})
                except (ValueError, TypeError):
                    pass
    else:
        # 均分時間
        interval = duration / max(len(valid_chords), 1)
        for i, chord in enumerate(valid_chords):
            entries.append({"time": round(i * interval, 2), "chord": chord})

    # 補 end
    for i in range(len(entries) - 1):
        entries[i]["end"] = entries[i + 1]["time"]
    if entries:
        entries[-1]["end"] = entries[-1]["time"] + 4.0

    result = {
        "song": song_name, "level": level,
        "key": valid_chords[0] if valid_chords else "",
        "source": "Chordify (web extraction)",
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
    print(f"    和弦數: {len(entries)}")
    return save_path


def main():
    print("=" * 60)
    print("  Chordify Web 擷取工具")
    print("=" * 60)

    if len(sys.argv) >= 2:
        url = sys.argv[1]
        song_name = sys.argv[2] if len(sys.argv) > 2 else "unknown"
        level = sys.argv[3] if len(sys.argv) > 3 else ""
    else:
        url = input("  Chordify URL: ").strip()
        song_name = input("  歌曲名稱: ").strip()
        level = input("  等級 (Lv1~Lv5): ").strip()

    if not url:
        print("  取消")
        return

    data = extract_chords_from_chordify(url)

    print(f"\n  歌曲: {data.get('title', '?')} - {data.get('artist', '?')}")
    print(f"  提取到 {len(data.get('chords', []))} 個和弦元素")

    if data.get("chords"):
        chord_list = [c["chord"] for c in data["chords"] if c.get("chord")]
        print(f"  和弦: {', '.join(chord_list[:30])}")
        if len(chord_list) > 30:
            print(f"         ... (+{len(chord_list) - 30})")

        duration = float(input("\n  歌曲總長（秒，預設 240）: ") or "240")
        save_lab(data["chords"], song_name, level, duration)
    else:
        print("  ⚠ 未能從 DOM 提取和弦，請嘗試截圖模式:")
        print(f"    python chordify_screenshot.py screenshot.png \"{song_name}\" {level}")


if __name__ == "__main__":
    main()
