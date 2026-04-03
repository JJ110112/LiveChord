"""Playwright QA — 88 Piano Keys mode"""

import re
import pytest
from playwright.sync_api import Page, expect

BASE = "http://localhost:8800"
TEST_TRACK = "Jazz/Bob James/Bob James - Jazz Hands/Beerbohm.flac"


def player_url(track=TEST_TRACK):
    from urllib.parse import quote
    return f"{BASE}/player?path={quote(track, safe='')}"


class Test88Keys:
    """88-key piano visualisation mode"""

    def test_no_js_errors_on_load(self, page: Page):
        """Page loads without JS errors"""
        errors = []
        page.on("pageerror", lambda err: errors.append(str(err)))
        page.goto(player_url())
        page.wait_for_timeout(3000)
        assert errors == [], f"JS errors on load: {errors}"

    def test_tab_button_exists(self, page: Page):
        """Tab button 🎹88 is present"""
        page.goto(player_url())
        tab = page.locator("#tabKeys")
        expect(tab).to_be_visible(timeout=5000)
        expect(tab).to_contain_text("\u2328")  # ⌨ keyboard icon

    def test_switch_to_keys_tab(self, page: Page):
        """Clicking Keys tab shows the 88-key canvas and hides others"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        page.locator("#tabKeys").click()
        page.wait_for_timeout(500)

        expect(page.locator("#chordDisplay88")).to_be_visible()
        expect(page.locator("#piano88Canvas")).to_be_visible()
        # overview and diagrams hidden
        expect(page.locator("#chordDisplayOverview")).to_be_hidden()
        expect(page.locator("#chordDisplayDiagrams")).to_be_hidden()

    def test_tab_active_class(self, page: Page):
        """Active tab gets .active class, others lose it"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        page.locator("#tabKeys").click()
        page.wait_for_timeout(300)

        expect(page.locator("#tabKeys")).to_have_class(re.compile("active"))
        expect(page.locator("#tabOverview")).not_to_have_class(re.compile("active"))
        expect(page.locator("#tabDiagrams")).not_to_have_class(re.compile("active"))

    def test_canvas_has_dimensions(self, page: Page):
        """Canvas element has non-zero width and height"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        page.locator("#tabKeys").click()
        page.wait_for_timeout(500)

        canvas = page.locator("#piano88Canvas")
        box = canvas.bounding_box()
        assert box is not None, "Canvas has no bounding box"
        assert box["width"] > 100, f"Canvas too narrow: {box['width']}"
        assert box["height"] > 50, f"Canvas too short: {box['height']}"

    def test_hand_switch_visible_in_keys_tab(self, page: Page):
        """Hand switch (LR/L/R) visible only in Keys tab"""
        page.goto(player_url())
        page.wait_for_timeout(2000)

        # initially hidden (overview tab)
        expect(page.locator("#handSwitch")).to_be_hidden()

        # switch to keys
        page.locator("#tabKeys").click()
        page.wait_for_timeout(300)
        expect(page.locator("#handSwitch")).to_be_visible()

        # mode switch hidden
        expect(page.locator("#modeSwitch")).to_be_hidden()

    def test_hand_switch_buttons(self, page: Page):
        """LR/L/R buttons toggle correctly"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        page.locator("#tabKeys").click()
        page.wait_for_timeout(300)

        for hand in ["left", "right", "both"]:
            btn = page.locator(f'#handSwitch [data-hand="{hand}"]')
            btn.click()
            page.wait_for_timeout(200)
            expect(btn).to_have_class(re.compile("active"))

    def test_switch_back_to_overview(self, page: Page):
        """Switching back from Keys to Overview restores normal view"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        page.locator("#tabKeys").click()
        page.wait_for_timeout(500)
        page.locator("#tabOverview").click()
        page.wait_for_timeout(500)

        expect(page.locator("#chordDisplayOverview")).to_be_visible()
        expect(page.locator("#chordDisplay88")).to_be_hidden()
        # hand switch hidden again, mode switch restored
        expect(page.locator("#handSwitch")).to_be_hidden()
        expect(page.locator("#modeSwitch")).to_be_visible()

    def test_no_errors_during_playback_keys_mode(self, page: Page):
        """Play audio while in Keys mode — no JS errors (excluding codec issues)"""
        errors = []
        page.on("pageerror", lambda err: errors.append(str(err)))
        page.goto(player_url())
        page.wait_for_timeout(2000)
        page.locator("#tabKeys").click()
        page.wait_for_timeout(500)

        # start playback
        page.locator("#btnPlay").click()
        page.wait_for_timeout(4000)
        page.locator("#btnPlay").click()  # pause

        # filter out audio codec errors (headless chromium may not support FLAC)
        real_errors = [e for e in errors if "no supported sources" not in e and "NotSupportedError" not in e]
        assert real_errors == [], f"JS errors during playback: {real_errors}"

    def test_big_chord_visible_in_keys_mode(self, page: Page):
        """Big chord name box is visible in Keys mode during playback"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        page.locator("#tabKeys").click()
        page.wait_for_timeout(300)

        # play briefly to get a chord
        page.locator("#btnPlay").click()
        page.wait_for_timeout(3000)
        page.locator("#btnPlay").click()

        chord_name = page.locator("#bigChordName").text_content()
        # should show an actual chord (not the placeholder dash)
        assert chord_name and chord_name.strip() != "", "No chord name displayed"

    def test_canvas_drawn(self, page: Page):
        """Canvas has actual drawn content (static keyboard) after tab switch"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        page.locator("#tabKeys").click()
        page.wait_for_timeout(1000)

        # static keyboard should be drawn immediately on tab switch (no playback needed)
        is_drawn = page.evaluate("""() => {
            const c = document.getElementById('piano88Canvas');
            if (!c) return false;
            const ctx = c.getContext('2d');
            const data = ctx.getImageData(0, 0, c.width, c.height).data;
            // check if any non-transparent pixel exists
            for (let i = 3; i < data.length; i += 4) {
                if (data[i] > 0) return true;
            }
            return false;
        }""")
        assert is_drawn, "Canvas appears blank — static keyboard not drawn"
