"""Phase 2 測試 — 即時和弦同步"""

import pytest
from playwright.sync_api import Page, expect

BASE = "http://localhost:8800"
TEST_TRACK = "Jazz/Bob James/Bob James - Jazz Hands/Beerbohm.flac"


def player_url():
    from urllib.parse import quote
    return f"{BASE}/player?path={quote(TEST_TRACK, safe='')}"


class TestChordSync:
    """即時和弦同步測試"""

    def test_chord_highlight_on_play(self, page: Page):
        """播放後應高亮當前和弦"""
        page.goto(player_url())
        chords = page.locator("#chordDisplay .chord-item")
        expect(chords.first).to_be_visible(timeout=8000)

        # 播放
        page.locator("#btnPlay").click()
        page.wait_for_timeout(2000)
        page.locator("#btnPlay").click()  # 暫停

        # 應有一個 active 和弦
        active = page.locator("#chordDisplay .chord-item.active")
        expect(active).to_have_count(1)

    def test_chord_highlight_first_chord(self, page: Page):
        """時間 0~8 秒應高亮第一個和弦 Eb"""
        page.goto(player_url())
        chords = page.locator("#chordDisplay .chord-item")
        expect(chords.first).to_be_visible(timeout=8000)

        # seek 到 2 秒
        page.evaluate("document.querySelector('#audio').currentTime = 2")
        page.wait_for_timeout(500)

        active = page.locator("#chordDisplay .chord-item.active")
        expect(active).to_have_count(1)
        name = active.locator(".chord-name").text_content()
        assert name == "Eb", f"時間 2 秒應高亮 Eb，實際 {name}"

    def test_chord_changes_on_seek(self, page: Page):
        """seek 到不同時間點，和弦高亮應跟隨變化"""
        page.goto(player_url())
        chords = page.locator("#chordDisplay .chord-item")
        expect(chords.first).to_be_visible(timeout=8000)

        # seek 到 10 秒 → 應高亮 Gm (8~16)
        page.evaluate("document.querySelector('#audio').currentTime = 10")
        page.wait_for_timeout(500)
        active = page.locator("#chordDisplay .chord-item.active .chord-name")
        expect(active).to_have_text("Gm")

        # seek 到 25 秒 → 應高亮 Fm7 (24~32)
        page.evaluate("document.querySelector('#audio').currentTime = 25")
        page.wait_for_timeout(500)
        active = page.locator("#chordDisplay .chord-item.active .chord-name")
        expect(active).to_have_text("Fm7")

        # seek 到 50 秒 → 應高亮 Abmaj7 (48~56)
        page.evaluate("document.querySelector('#audio').currentTime = 50")
        page.wait_for_timeout(500)
        active = page.locator("#chordDisplay .chord-item.active .chord-name")
        expect(active).to_have_text("Abmaj7")

    def test_click_chord_seeks_audio(self, page: Page):
        """點擊和弦應跳轉到對應時間"""
        page.goto(player_url())
        chords = page.locator("#chordDisplay .chord-item")
        expect(chords.first).to_be_visible(timeout=8000)

        # 點擊第四個和弦 (Fm7, time=24)
        chords.nth(3).click()
        page.wait_for_timeout(1000)

        # 音訊時間應接近 24 秒
        current_time = page.evaluate("document.querySelector('#audio').currentTime")
        assert abs(current_time - 24) < 2, f"點擊後時間應為 ~24，實際 {current_time}"

    def test_mode_switch_preserves_highlight(self, page: Page):
        """切換顯示模式後，高亮應保持"""
        page.goto(player_url())
        chords = page.locator("#chordDisplay .chord-item")
        expect(chords.first).to_be_visible(timeout=8000)

        # seek 到 10 秒
        page.evaluate("document.querySelector('#audio').currentTime = 10")
        page.wait_for_timeout(500)

        # 切換到吉他模式
        page.locator(".mode-btn[data-mode='guitar']").click()
        page.wait_for_timeout(1000)

        # 應仍有 canvas 圖 (吉他和弦圖)
        canvases = page.locator("#chordDisplay canvas")
        assert canvases.count() > 0, "吉他模式應顯示 canvas 和弦圖"

        # 切換到烏克麗麗
        page.locator(".mode-btn[data-mode='ukulele']").click()
        page.wait_for_timeout(1000)

        # 切回簡譜
        page.locator(".mode-btn[data-mode='jianpu']").click()
        page.wait_for_timeout(500)
        jianpu = page.locator("#chordDisplay .chord-jianpu")
        assert jianpu.count() > 0, "簡譜模式應顯示簡譜文字"

    def test_jianpu_content_correct(self, page: Page):
        """簡譜內容正確性"""
        page.goto(player_url())
        chords = page.locator("#chordDisplay .chord-item")
        expect(chords.first).to_be_visible(timeout=8000)
        # Eb 的簡譜應包含 "b3"（降3）
        first_jianpu = page.locator("#chordDisplay .chord-item:first-child .chord-jianpu").inner_html()
        assert "3" in first_jianpu, f"Eb 簡譜應包含 3，實際: {first_jianpu}"
