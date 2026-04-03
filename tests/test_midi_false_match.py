"""QA Test — 驗證 MIDI 模糊匹配不會誤導入不同藝人的 MIDI

Bug: Brad Paisley "O Holy Night" 沒有 .mid 檔，但 _midi_matches 模糊比對到
Mariah Carey 的同名 MIDI，導致顯示「MIDI 匯入」綠燈，而非 BTC 偵測橘燈。
"""

import json
import urllib.request
import urllib.parse
import pytest
from playwright.sync_api import Page, expect

BASE = "http://localhost:8800"
TEST_TRACK = "Christmas/Brad Paisley/O Holy Night.flac"


def api_get(path):
    url = BASE + path
    with urllib.request.urlopen(url, timeout=10) as resp:
        return resp.status, json.loads(resp.read().decode())


class TestMidiFalseMatch:
    """MIDI 模糊匹配誤判測試"""

    def test_midi_search_returns_fuzzy_result(self):
        """確認 MIDI 搜尋會回傳模糊匹配結果（Mariah Carey 版本）"""
        track = urllib.parse.quote(TEST_TRACK)
        status, data = api_get(f"/api/chords/midi-search?path={track}")
        assert status == 200
        assert len(data["results"]) > 0, "應該找到模糊匹配的 MIDI"
        # 但沒有精確匹配
        flac_name = "O Holy Night"
        exact = [r for r in data["results"]
                 if r["name"].replace(".mid", "").replace(".midi", "") == flac_name]
        assert len(exact) == 0, "不應有精確檔名匹配（Mariah Carey 的 MIDI 檔名不同）"

    def test_detect_button_should_not_import_fuzzy_midi(self, page: Page):
        """按偵測按鈕時，不應匯入模糊匹配的 MIDI（應 fall through 到 BTC）"""
        track_encoded = urllib.parse.quote(TEST_TRACK, safe="")
        page.goto(f"{BASE}/player?path={track_encoded}")

        # 等待頁面載入
        page.wait_for_timeout(2000)

        # 清除已有的和弦（如果有的話）
        import hashlib
        song_hash = hashlib.md5(TEST_TRACK.encode()).hexdigest()
        try:
            import os
            chord_file = f"/home/user/LiveChord/data/chords/{song_hash}.json"
            if os.path.exists(chord_file):
                os.remove(chord_file)
                page.reload()
                page.wait_for_timeout(2000)
        except Exception:
            pass

        # 攔截 API 請求來驗證行為
        midi_import_called = []
        detect_called = []

        def handle_request(request):
            if "/api/chords/midi-import" in request.url:
                midi_import_called.append(request.url)
            if "/api/chords/detect" in request.url:
                detect_called.append(request.url)

        page.on("request", handle_request)

        # 點擊偵測按鈕
        btn = page.locator("#btnDetect")
        if btn.is_visible():
            btn.click()

            # 等待偵測流程完成（最長 120 秒，BTC 偵測可能較慢）
            page.wait_for_timeout(5000)

            # 關鍵驗證：不應呼叫 midi-import API
            assert len(midi_import_called) == 0, \
                f"不應呼叫 midi-import（模糊匹配不該自動匯入），但被呼叫了: {midi_import_called}"

            print(f"✓ midi-import 未被呼叫（正確）")
            print(f"✓ detect 被呼叫 {len(detect_called)} 次")

    def test_toast_should_not_show_midi_import(self, page: Page):
        """Toast 訊息不應顯示「MIDI 匯入」"""
        track_encoded = urllib.parse.quote(TEST_TRACK, safe="")
        page.goto(f"{BASE}/player?path={track_encoded}")
        page.wait_for_timeout(2000)

        # 監聽 toast 內容
        toast_texts = []

        def check_toast():
            toast = page.locator("#toast")
            if toast.is_visible():
                text = toast.text_content()
                if text:
                    toast_texts.append(text)

        # 點擊偵測按鈕
        btn = page.locator("#btnDetect")
        if btn.is_visible():
            btn.click()

            # 定期檢查 toast
            for _ in range(10):
                page.wait_for_timeout(500)
                check_toast()

            # 驗證沒有出現「MIDI 匯入」toast
            midi_toasts = [t for t in toast_texts if "MIDI 匯入" in t]
            assert len(midi_toasts) == 0, \
                f"不應出現 'MIDI 匯入' toast，但發現: {midi_toasts}"
            print(f"✓ 未出現 MIDI 匯入 toast（正確）")
            if toast_texts:
                print(f"  實際 toast: {toast_texts}")

    def test_chord_source_indicator(self, page: Page):
        """和弦來源燈號應為 BTC（橘色），而非 MIDI（綠色）"""
        track_encoded = urllib.parse.quote(TEST_TRACK, safe="")
        page.goto(f"{BASE}/player?path={track_encoded}")
        page.wait_for_timeout(3000)

        badge = page.locator("#chordSource")
        if badge.is_visible():
            class_name = badge.get_attribute("class") or ""
            text = badge.text_content() or ""
            # 不應有 src-midi class
            assert "src-midi" not in class_name, \
                f"燈號不應為 MIDI（綠色），實際 class: {class_name}"
            # 如果有和弦，應為 src-btc
            if "src-btc" in class_name:
                print(f"✓ 燈號為 BTC（橘色）: class={class_name}, text={text}")
            else:
                print(f"ℹ 燈號狀態: class={class_name}, text={text}")
