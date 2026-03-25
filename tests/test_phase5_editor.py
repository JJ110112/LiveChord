"""Phase 5 測試 — 和弦譜時間軸編輯器"""

import pytest
from playwright.sync_api import Page, expect

BASE = "http://localhost:8800"
TEST_TRACK = "Jazz/Bob James/Bob James - Jazz Hands/Beerbohm.flac"


def editor_url(track=TEST_TRACK):
    from urllib.parse import quote
    return f"{BASE}/editor?path={quote(track, safe='')}"


class TestEditorBasic:
    """編輯器基本功能"""

    def test_editor_loads(self, page: Page):
        """E-01: 編輯器載入"""
        page.goto(editor_url())
        expect(page.locator("#timeline")).to_be_visible(timeout=5000)
        expect(page.locator("#chordPalette")).to_be_visible()
        expect(page.locator("#btnSave")).to_be_visible()

    def test_editor_shows_title(self, page: Page):
        """顯示歌曲標題"""
        page.goto(editor_url())
        expect(page.locator("#editorTitle")).not_to_be_empty(timeout=5000)

    def test_existing_chords_loaded(self, page: Page):
        """載入現有和弦譜"""
        page.goto(editor_url())
        # Beerbohm 有 seed data
        blocks = page.locator(".chord-block")
        expect(blocks.first).to_be_visible(timeout=5000)
        assert blocks.count() > 0

    def test_chord_palette_visible(self, page: Page):
        """和弦面板可見"""
        page.goto(editor_url())
        buttons = page.locator("#chordPalette .palette-btn")
        expect(buttons.first).to_be_visible(timeout=3000)
        # 應有多個和弦按鈕
        assert buttons.count() >= 30

    def test_back_button(self, page: Page):
        """返回播放頁"""
        page.goto(editor_url())
        link = page.locator("#btnBack")
        expect(link).to_be_visible()
        href = link.get_attribute("href")
        assert "/player" in href


class TestEditorInteraction:
    """編輯器互動"""

    def test_select_chord_block(self, page: Page):
        """選取和弦方塊"""
        page.goto(editor_url())
        blocks = page.locator(".chord-block")
        expect(blocks.first).to_be_visible(timeout=5000)
        # 點擊第一個方塊
        blocks.first.click()
        page.wait_for_timeout(300)
        # 應有 selected class
        expect(page.locator(".chord-block.selected")).to_have_count(1)
        # chordNameInput 應有值
        val = page.locator("#chordNameInput").input_value()
        assert len(val) > 0

    def test_update_chord_name(self, page: Page):
        """更新和弦名稱"""
        page.goto(editor_url())
        blocks = page.locator(".chord-block")
        expect(blocks.first).to_be_visible(timeout=5000)
        # 選取
        blocks.first.click()
        page.wait_for_timeout(300)
        # 改名
        page.locator("#chordNameInput").fill("Dm7")
        page.locator("#btnUpdate").click()
        page.wait_for_timeout(300)
        # 第一個方塊應更新
        expect(page.locator(".chord-block").first).to_contain_text("Dm7")

    def test_delete_chord(self, page: Page):
        """刪除和弦"""
        page.goto(editor_url())
        blocks = page.locator(".chord-block")
        expect(blocks.first).to_be_visible(timeout=5000)
        count_before = blocks.count()
        # 選取並刪除
        blocks.first.click()
        page.wait_for_timeout(200)
        page.locator("#btnDelete").click()
        page.wait_for_timeout(300)
        count_after = page.locator(".chord-block").count()
        assert count_after == count_before - 1

    def test_save_chords(self, page: Page):
        """儲存和弦譜"""
        page.goto(editor_url())
        blocks = page.locator(".chord-block")
        expect(blocks.first).to_be_visible(timeout=5000)
        # 點擊儲存
        page.locator("#btnSave").click()
        # toast 應顯示成功
        expect(page.locator("#toast")).to_contain_text("已儲存")

    def test_palette_select(self, page: Page):
        """面板選取和弦"""
        page.goto(editor_url())
        expect(page.locator("#chordPalette .palette-btn").first).to_be_visible(timeout=3000)
        # 點擊 Am 按鈕
        page.locator("#chordPalette .palette-btn", has_text="Am").first.click()
        page.wait_for_timeout(200)
        # 應有 selected class
        expect(page.locator("#chordPalette .palette-btn.selected")).to_have_count(1)

    def test_zoom_slider(self, page: Page):
        """縮放控制"""
        page.goto(editor_url())
        slider = page.locator("#zoomSlider")
        expect(slider).to_be_visible(timeout=3000)
        # 拉到最大
        slider.fill("30")
        page.wait_for_timeout(500)
        # timeline 寬度應增加

    def test_play_pause_in_editor(self, page: Page):
        """編輯器內播放控制"""
        page.goto(editor_url())
        page.wait_for_timeout(2000)
        btn = page.locator("#btnPlay")
        expect(btn).to_be_visible()
        btn.click()
        page.wait_for_timeout(500)
        btn.click()  # pause

    def test_keyboard_delete(self, page: Page):
        """按 Delete 鍵刪除"""
        page.goto(editor_url())
        blocks = page.locator(".chord-block")
        expect(blocks.first).to_be_visible(timeout=5000)
        count_before = blocks.count()
        # 選取
        blocks.first.click()
        page.wait_for_timeout(200)
        # 按 Delete
        page.keyboard.press("Delete")
        page.wait_for_timeout(300)
        count_after = page.locator(".chord-block").count()
        assert count_after == count_before - 1

    def test_edit_link_from_player(self, page: Page):
        """從播放頁跳轉到編輯頁"""
        from urllib.parse import quote
        page.goto(f"{BASE}/player?path={quote(TEST_TRACK, safe='')}")
        link = page.locator("#btnEdit")
        expect(link).to_be_visible(timeout=5000)
        link.click()
        page.wait_for_url("**/editor?path=**", timeout=5000)
        assert "/editor" in page.url
