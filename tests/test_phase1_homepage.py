"""Phase 1 測試 — 首頁功能"""

import pytest
from playwright.sync_api import Page, expect

BASE = "http://localhost:8800"


class TestHomepage:
    """首頁基本功能"""

    def test_homepage_loads(self, page: Page):
        """F-01: 首頁載入，顯示 Genre 分類"""
        page.goto(BASE)
        expect(page.locator("h1")).to_have_text("LiveChord")
        grid = page.locator("#browseGrid")
        expect(grid).to_be_visible()
        items = page.locator("#browseGrid .grid-item")
        expect(items.first).to_be_visible(timeout=5000)
        assert items.count() >= 5

    def test_browse_genre(self, page: Page):
        """F-02: 點擊 Genre 展開 Artist"""
        page.goto(BASE)
        page.locator("#browseGrid .grid-item", has_text="Jazz").click()
        items = page.locator("#browseGrid .grid-item")
        expect(items.first).to_be_visible(timeout=5000)
        assert items.count() > 0

    def test_browse_to_album(self, page: Page):
        """F-03~F-04: 瀏覽到 Album 層級"""
        page.goto(BASE)
        page.locator("#browseGrid .grid-item", has_text="Jazz").click()
        # 使用精確路徑定位
        page.locator('.grid-item[data-path="Jazz/Bob James"]').click()
        items = page.locator("#browseGrid .grid-item")
        expect(items.first).to_be_visible(timeout=5000)
        expect(page.locator(".grid-item", has_text="Jazz Hands")).to_be_visible()

    def test_browse_to_tracks(self, page: Page):
        """F-04: 點擊 Album 顯示 Track 清單"""
        page.goto(BASE)
        page.locator(".grid-item", has_text="Jazz").click()
        page.locator('.grid-item[data-path="Jazz/Bob James"]').click()
        page.locator(".grid-item", has_text="Jazz Hands").click()
        tracks = page.locator("#trackList li")
        expect(tracks.first).to_be_visible(timeout=5000)
        assert tracks.count() > 0

    def test_breadcrumb_navigation(self, page: Page):
        """麵包屑導覽"""
        page.goto(BASE)
        page.locator(".grid-item", has_text="Jazz").click()
        page.locator('.grid-item[data-path="Jazz/Bob James"]').click()
        # 點麵包屑返回 Jazz
        page.locator("#breadcrumb a", has_text="Jazz").click()
        items = page.locator("#browseGrid .grid-item")
        expect(items.first).to_be_visible(timeout=5000)

    def test_track_click_navigates_to_player(self, page: Page):
        """F-05: 點擊 Track 跳轉播放頁"""
        page.goto(BASE)
        page.locator(".grid-item", has_text="Jazz").click()
        page.locator('.grid-item[data-path="Jazz/Bob James"]').click()
        page.locator(".grid-item", has_text="Jazz Hands").click()
        page.locator("#trackList li", has_text="Beerbohm").click()
        page.wait_for_url("**/player?path=**", timeout=5000)
        assert "/player" in page.url

    def test_tabs_switching(self, page: Page):
        """Tab 切換：瀏覽/最愛/最近播放"""
        page.goto(BASE)
        page.locator(".tabs button", has_text="最愛").click()
        expect(page.locator("#tabFavorites")).to_be_visible()
        page.locator(".tabs button", has_text="最近播放").click()
        expect(page.locator("#tabRecent")).to_be_visible()
        page.locator(".tabs button", has_text="瀏覽").click()
        expect(page.locator("#tabBrowse")).to_be_visible()


class TestSearch:
    """搜尋功能"""

    def test_search_shows_dropdown(self, page: Page):
        """搜尋欄輸入後顯示下拉"""
        page.goto(BASE)
        page.locator("#searchInput").fill("zzzznotexist999")
        page.wait_for_timeout(500)
        expect(page.locator("#searchResults")).to_be_visible()
