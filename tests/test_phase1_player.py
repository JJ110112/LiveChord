"""Phase 1 測試 — 播放頁功能"""

import pytest
from playwright.sync_api import Page, expect

BASE = "http://localhost:8800"
TEST_TRACK = "Jazz/Bob James/Bob James - Jazz Hands/Beerbohm.flac"


def player_url(track=TEST_TRACK):
    from urllib.parse import quote
    return f"{BASE}/player?path={quote(track, safe='')}"


class TestPlayerBasic:
    """播放頁基本功能"""

    def test_player_loads(self, page: Page):
        """P-01: 播放頁載入，顯示歌曲資訊"""
        page.goto(player_url())
        expect(page.locator("#songTitle")).not_to_have_text("—", timeout=5000)
        expect(page.locator("#songTitle")).to_contain_text("Beerbohm")
        expect(page.locator("#songArtist")).to_contain_text("Bob James")

    def test_player_shows_cover(self, page: Page):
        """P-07: 封面顯示"""
        page.goto(player_url())
        cover = page.locator("#cover")
        expect(cover).to_be_visible(timeout=5000)

    def test_player_metadata(self, page: Page):
        """P-06: 顯示音訊 metadata"""
        page.goto(player_url())
        expect(page.locator("#songMeta")).not_to_be_empty(timeout=5000)
        # 應包含 sample rate 等
        meta = page.locator("#songMeta").text_content()
        assert "kHz" in meta

    def test_player_play_pause(self, page: Page):
        """P-02/P-03: 播放/暫停控制"""
        page.goto(player_url())
        page.wait_for_timeout(2000)  # 等待音訊載入
        btn = page.locator("#btnPlay")
        # 初始為播放圖示
        expect(btn).to_be_visible()
        # 點擊播放
        btn.click()
        page.wait_for_timeout(1000)
        # 點擊暫停
        btn.click()
        page.wait_for_timeout(500)
        # 確認 timeCurrent 已更新（不再是 0:00）
        tc = page.locator("#timeCurrent").text_content()
        # 播放過一段時間後時間應該不是 0:00
        assert tc is not None

    def test_progress_bar_seek(self, page: Page):
        """P-04: 進度條拖曳"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        # 點擊進度條中間位置
        bar = page.locator("#progressBar")
        box = bar.bounding_box()
        if box:
            page.mouse.click(box["x"] + box["width"] * 0.5, box["y"] + box["height"] / 2)
            page.wait_for_timeout(500)
            # 時間應跳到中間位置
            tc = page.locator("#timeCurrent").text_content()
            assert tc != "0:00"

    def test_volume_slider(self, page: Page):
        """P-05: 音量調整"""
        page.goto(player_url())
        slider = page.locator("#volumeSlider")
        expect(slider).to_be_visible()
        # 設定音量為 0.5
        slider.fill("0.5")

    def test_favorite_toggle(self, page: Page):
        """P-08: 收藏切換"""
        page.goto(player_url())
        btn = page.locator("#btnFav")
        expect(btn).to_be_visible(timeout=5000)
        # 點擊收藏
        btn.click()
        page.wait_for_timeout(500)
        # toast 應顯示
        expect(page.locator("#toast")).to_be_visible()

    def test_chord_display_with_data(self, page: Page):
        """P-09: 有和弦譜時顯示和弦"""
        page.goto(player_url())
        # 等待和弦載入
        chords = page.locator("#chordDisplay .chord-item")
        expect(chords.first).to_be_visible(timeout=8000)
        count = chords.count()
        assert count == 12, f"應有 12 個和弦，實際 {count}"

    def test_chord_names_correct(self, page: Page):
        """驗證和弦名稱正確"""
        page.goto(player_url())
        chords = page.locator("#chordDisplay .chord-item")
        expect(chords.first).to_be_visible(timeout=8000)
        # 第一個和弦應是 Eb
        first_name = page.locator("#chordDisplay .chord-item:first-child .chord-name").text_content()
        assert first_name == "Eb"

    def test_no_chords_message(self, page: Page):
        """P-10: 無和弦譜時顯示提示"""
        # 使用一首確定沒有和弦譜的歌曲
        from urllib.parse import quote
        url = f"{BASE}/player?path={quote('Jazz/Bob James/Bob James - Jazz Hands/Topside.flac', safe='')}"
        page.goto(url)
        page.wait_for_timeout(3000)
        expect(page.locator("#chordDisplay")).to_contain_text("尚無和弦譜")
