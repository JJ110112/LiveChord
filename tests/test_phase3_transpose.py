"""Phase 3 測試 — 移調與 Capo"""

import pytest
from playwright.sync_api import Page, expect

BASE = "http://localhost:8800"
TEST_TRACK = "Jazz/Bob James/Bob James - Jazz Hands/Beerbohm.flac"


def player_url():
    from urllib.parse import quote
    return f"{BASE}/player?path={quote(TEST_TRACK, safe='')}"


class TestTranspose:
    """移調功能測試"""

    def test_transpose_controls_visible(self, page: Page):
        """移調控制元件可見"""
        page.goto(player_url())
        expect(page.locator("#btnTransposeUp")).to_be_visible(timeout=3000)
        expect(page.locator("#btnTransposeDown")).to_be_visible()
        expect(page.locator("#transposeValue")).to_have_text("0")

    def test_transpose_up(self, page: Page):
        """移調升半音"""
        page.goto(player_url())
        chords = page.locator("#chordDisplay .chord-item")
        expect(chords.first).to_be_visible(timeout=8000)

        # 原始第一和弦是 Eb
        first = page.locator("#chordDisplay .chord-item:first-child .chord-name")
        expect(first).to_have_text("Eb")

        # 點擊升 1 半音
        page.locator("#btnTransposeUp").click()
        page.wait_for_timeout(1000)
        expect(page.locator("#transposeValue")).to_have_text("+1")

        # Eb + 1 = E
        first = page.locator("#chordDisplay .chord-item:first-child .chord-name")
        expect(first).to_have_text("E")

    def test_transpose_down(self, page: Page):
        """移調降半音"""
        page.goto(player_url())
        chords = page.locator("#chordDisplay .chord-item")
        expect(chords.first).to_be_visible(timeout=8000)

        # 降 1 半音: Eb → D
        page.locator("#btnTransposeDown").click()
        page.wait_for_timeout(1000)
        expect(page.locator("#transposeValue")).to_have_text("-1")

        first = page.locator("#chordDisplay .chord-item:first-child .chord-name")
        expect(first).to_have_text("D")

    def test_transpose_multiple_steps(self, page: Page):
        """多次移調"""
        page.goto(player_url())
        chords = page.locator("#chordDisplay .chord-item")
        expect(chords.first).to_be_visible(timeout=8000)

        # 升 3 半音: Eb → Gb
        for _ in range(3):
            page.locator("#btnTransposeUp").click()
            page.wait_for_timeout(300)
        page.wait_for_timeout(1000)

        expect(page.locator("#transposeValue")).to_have_text("+3")
        first = page.locator("#chordDisplay .chord-item:first-child .chord-name")
        name = first.text_content()
        # Eb(3) + 3 = Gb(6) 或 F#
        assert name in ("Gb", "F#"), f"Eb+3 應為 Gb 或 F#，實際 {name}"


class TestCapo:
    """Capo 功能測試"""

    def test_capo_select_visible(self, page: Page):
        """Capo 選單可見"""
        page.goto(player_url())
        expect(page.locator("#capoSelect")).to_be_visible(timeout=3000)

    def test_capo_changes_chords(self, page: Page):
        """設定 Capo 應改變和弦"""
        page.goto(player_url())
        chords = page.locator("#chordDisplay .chord-item")
        expect(chords.first).to_be_visible(timeout=8000)

        # 原始第一和弦是 Eb
        first = page.locator("#chordDisplay .chord-item:first-child .chord-name")
        expect(first).to_have_text("Eb")

        # Capo 3: 和弦降 3 半音 → Eb → C
        page.locator("#capoSelect").select_option("3")
        page.wait_for_timeout(1000)

        first = page.locator("#chordDisplay .chord-item:first-child .chord-name")
        expect(first).to_have_text("C")

    def test_transpose_and_capo_combined(self, page: Page):
        """移調 + Capo 組合"""
        page.goto(player_url())
        chords = page.locator("#chordDisplay .chord-item")
        expect(chords.first).to_be_visible(timeout=8000)

        # Transpose +2, Capo 3 → net shift = 2 - 3 = -1 → Eb - 1 = D
        page.locator("#btnTransposeUp").click()
        page.wait_for_timeout(200)
        page.locator("#btnTransposeUp").click()
        page.wait_for_timeout(200)
        page.locator("#capoSelect").select_option("3")
        page.wait_for_timeout(1000)

        first = page.locator("#chordDisplay .chord-item:first-child .chord-name")
        expect(first).to_have_text("D")
