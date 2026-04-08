"""Playwright QA — Accordion (手風琴) integration test
Validates the new accordion instrument tab:
- Page loads without JS errors after adding accordion
- Accordion tab switching and panel visibility
- Bass grid canvas rendering
- Bass waterfall canvas rendering
- Chord name display
- Bass pattern selector
- Capo hidden for accordion
- API endpoints return accordion data
- Existing instrument tabs not broken (regression)
- Tab persistence
"""

import re
import pytest
from playwright.sync_api import Page, expect

BASE = "http://192.168.50.6:8800"
TEST_TRACK = "Jazz/Bob James/Bob James - Jazz Hands/Beerbohm.flac"


def player_url(track=TEST_TRACK):
    from urllib.parse import quote
    return f"{BASE}/player?path={quote(track, safe='')}"


def _click_instrument_tab(page: Page, tab_name: str):
    """Click instrument tab via JS dispatch — avoids popup hover-area issues
    when the button is far from the narrow #tbInstrument trigger."""
    page.evaluate(f'''document.querySelector('#tbInstrument .tb-popup-btn[data-tab="{tab_name}"]').click()''')


def _switch_to_accordion(page: Page, wait_ms=3000):
    """Navigate to player and switch to accordion tab."""
    page.goto(player_url())
    page.wait_for_timeout(2000)
    _click_instrument_tab(page, "accordion")
    page.wait_for_timeout(wait_ms)


# ---------------------------------------------------------------------------
# 1. No JS Errors
# ---------------------------------------------------------------------------

class TestAccordionNoJSErrors:
    """Page loads and switches to accordion without JavaScript errors"""

    def test_no_js_errors_on_load(self, page: Page):
        """No JS errors on initial page load (accordion script loaded)"""
        errors = []
        page.on("pageerror", lambda err: errors.append(str(err)))
        page.goto(player_url())
        page.wait_for_timeout(4000)
        assert errors == [], f"JS errors on load: {errors}"

    def test_no_js_errors_switching_to_accordion(self, page: Page):
        """No JS errors when switching to accordion tab"""
        errors = []
        page.on("pageerror", lambda err: errors.append(str(err)))
        page.goto(player_url())
        page.wait_for_timeout(3000)
        _click_instrument_tab(page, "accordion")
        page.wait_for_timeout(2000)
        assert errors == [], f"JS errors switching to accordion: {errors}"

    def test_no_js_errors_cycling_all_tabs_with_accordion(self, page: Page):
        """No JS errors when cycling through ALL instrument tabs including accordion"""
        errors = []
        page.on("pageerror", lambda err: errors.append(str(err)))
        page.goto(player_url())
        page.wait_for_timeout(3000)

        for tab_name in ["guitar", "ukulele", "accordion", "piano"]:
            _click_instrument_tab(page, tab_name)
            page.wait_for_timeout(2000)

        assert errors == [], f"JS errors during full tab cycling: {errors}"


# ---------------------------------------------------------------------------
# 2. Tab Switching
# ---------------------------------------------------------------------------

class TestAccordionSwitching:
    """Accordion tab switching via toolbar popup"""

    def test_accordion_button_exists(self, page: Page):
        """Accordion button exists in instrument popup"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        # Force popup open via JS to check button exists
        page.evaluate('document.querySelector("#tbInstrument .tb-popup").style.display = "flex"')
        page.wait_for_timeout(300)
        btn = page.locator('#tbInstrument .tb-popup-btn[data-tab="accordion"]')
        expect(btn).to_be_visible()

    def test_accordion_button_text(self, page: Page):
        """Accordion button shows correct label"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        page.evaluate('document.querySelector("#tbInstrument .tb-popup").style.display = "flex"')
        page.wait_for_timeout(300)
        btn = page.locator('#tbInstrument .tb-popup-btn[data-tab="accordion"]')
        text = btn.text_content()
        assert "手風琴" in text, f"Expected '手風琴' in button text, got '{text}'"

    def test_switch_to_accordion(self, page: Page):
        """Switch to accordion — accordion panel visible, others hidden"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        _click_instrument_tab(page, "accordion")
        page.wait_for_timeout(1500)
        expect(page.locator("#chordDisplayAccordion")).to_be_visible()
        expect(page.locator("#chordDisplayPiano")).to_be_hidden()
        expect(page.locator("#chordDisplayGuitar")).to_be_hidden()

    def test_switch_away_from_accordion(self, page: Page):
        """Switch from accordion to piano — accordion hidden"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        _click_instrument_tab(page, "accordion")
        page.wait_for_timeout(1000)
        _click_instrument_tab(page, "piano")
        page.wait_for_timeout(1000)
        expect(page.locator("#chordDisplayPiano")).to_be_visible()
        expect(page.locator("#chordDisplayAccordion")).to_be_hidden()

    def test_accordion_tab_persists_in_localstorage(self, page: Page):
        """Tab selection 'accordion' persists via localStorage"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        _click_instrument_tab(page, "accordion")
        page.wait_for_timeout(1000)
        stored = page.evaluate("localStorage.getItem('livechord_tab')")
        assert stored == "accordion", f"Expected 'accordion', got '{stored}'"

    def test_accordion_icon_updates(self, page: Page):
        """Instrument trigger button icon updates when accordion selected"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        _click_instrument_tab(page, "accordion")
        page.wait_for_timeout(1000)
        icon = page.locator("#btnInstrument").text_content()
        assert icon and icon.strip() != "", f"Icon should be set, got '{icon}'"


# ---------------------------------------------------------------------------
# 3. Bass Grid Canvas
# ---------------------------------------------------------------------------

class TestAccordionBassGrid:
    """Bass grid canvas rendering"""

    def _go_accordion(self, page: Page, wait_ms=3000):
        _switch_to_accordion(page, wait_ms)

    def test_bass_grid_canvas_exists(self, page: Page):
        """Bass grid canvas exists and is visible"""
        _switch_to_accordion(page)
        canvas = page.locator("#accordionBassGrid")
        expect(canvas).to_be_visible()

    def test_bass_grid_canvas_has_dimensions(self, page: Page):
        """Bass grid canvas has non-zero dimensions"""
        _switch_to_accordion(page)
        canvas = page.locator("#accordionBassGrid")
        box = canvas.bounding_box()
        assert box and box["width"] > 50 and box["height"] > 50, \
            f"Bass grid canvas should have reasonable size, got {box}"

    def test_bass_grid_canvas_rendered(self, page: Page):
        """Bass grid canvas has pixel data (not blank)"""
        _switch_to_accordion(page)
        # Check if canvas has been drawn to (non-blank)
        has_pixels = page.evaluate("""() => {
            const c = document.querySelector('#accordionBassGrid');
            if (!c) return false;
            const ctx = c.getContext('2d');
            const data = ctx.getImageData(0, 0, c.width, c.height).data;
            for (let i = 3; i < data.length; i += 4) {
                if (data[i] > 0) return true;
            }
            return false;
        }""")
        assert has_pixels, "Bass grid canvas should have rendered content"


# ---------------------------------------------------------------------------
# 4. Bass Waterfall Canvas
# ---------------------------------------------------------------------------

class TestAccordionWaterfall:
    """Bass pattern waterfall canvas"""

    def _go_accordion(self, page: Page, wait_ms=3000):
        _switch_to_accordion(page, wait_ms)

    def test_waterfall_canvas_exists(self, page: Page):
        """Bass waterfall canvas exists and is visible"""
        _switch_to_accordion(page)
        canvas = page.locator("#accordionBassWaterfall")
        expect(canvas).to_be_visible()

    def test_waterfall_canvas_has_dimensions(self, page: Page):
        """Bass waterfall canvas has non-zero dimensions"""
        _switch_to_accordion(page)
        canvas = page.locator("#accordionBassWaterfall")
        box = canvas.bounding_box()
        assert box and box["width"] > 50 and box["height"] > 50, \
            f"Waterfall canvas should have reasonable size, got {box}"


# ---------------------------------------------------------------------------
# 5. Chord Name & Hints
# ---------------------------------------------------------------------------

class TestAccordionChordDisplay:
    """Chord name and hint text display"""

    def test_chord_name_displayed(self, page: Page):
        """Chord name element exists and is populated"""
        _switch_to_accordion(page, wait_ms=5000)
        name = page.locator("#accChordName").text_content()
        assert name and name != "--", f"Expected chord name, got '{name}'"

    def test_lh_hint_displayed(self, page: Page):
        """Left-hand hint exists"""
        _switch_to_accordion(page, wait_ms=5000)
        hint = page.locator("#accLhHint")
        expect(hint).to_be_visible()
        text = hint.text_content()
        assert text and len(text) > 0, "LH hint should have text"


# ---------------------------------------------------------------------------
# 6. Bass Pattern Selector
# ---------------------------------------------------------------------------

class TestAccordionPatternSelector:
    """Bass pattern selector functionality"""

    def test_pattern_select_exists(self, page: Page):
        """Bass pattern selector dropdown exists"""
        _switch_to_accordion(page)
        select = page.locator("#accBassPatternSelect")
        expect(select).to_be_visible()

    def test_pattern_select_has_options(self, page: Page):
        """Pattern selector has at least 4 options"""
        _switch_to_accordion(page)
        options = page.locator("#accBassPatternSelect option")
        count = options.count()
        assert count >= 4, f"Expected at least 4 pattern options, got {count}"

    def test_pattern_select_persists(self, page: Page):
        """Pattern selection persists in localStorage"""
        _switch_to_accordion(page)
        page.locator("#accBassPatternSelect").select_option("waltz")
        page.wait_for_timeout(500)
        stored = page.evaluate("localStorage.getItem('livechord_acc_pattern')")
        assert stored == "waltz", f"Expected 'waltz' persisted, got '{stored}'"


# ---------------------------------------------------------------------------
# 7. Capo Hidden for Accordion
# ---------------------------------------------------------------------------

class TestAccordionCapo:
    """Capo control should be hidden for accordion"""

    def test_capo_hidden_for_accordion(self, page: Page):
        """Capo group display:none when accordion is active"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        _click_instrument_tab(page, "accordion")
        page.wait_for_timeout(1000)
        display = page.locator("#capoGroup").evaluate("el => el.style.display")
        assert display == "none", f"Capo should be display:none for accordion, got '{display}'"


# ---------------------------------------------------------------------------
# 8. API Endpoints
# ---------------------------------------------------------------------------

class TestAccordionAPI:
    """Accordion-related API endpoints"""

    def test_instruments_endpoint_includes_accordion(self, page: Page):
        """GET /api/instruments returns accordion metadata"""
        resp = page.request.get(f"{BASE}/api/instruments")
        assert resp.ok
        data = resp.json()
        assert "accordion" in data, "accordion should be in instruments list"
        assert data["accordion"]["type"] == "accordion"
        assert data["accordion"]["bass_rows"] == 3
        assert data["accordion"]["bass_cols"] == 7

    def test_chord_diagram_accordion_c(self, page: Page):
        """GET /api/chord/diagram/accordion/C returns bass button mapping"""
        resp = page.request.get(f"{BASE}/api/chord/diagram/accordion/C")
        assert resp.ok
        data = resp.json()
        assert data["available"] is True
        assert len(data["buttons"]) == 2
        # C bass at col 2, row 0
        bass = data["buttons"][0]
        assert bass["col"] == 2 and bass["row"] == 0 and bass["label"] == "C"
        # C major at col 2, row 1
        chord = data["buttons"][1]
        assert chord["col"] == 2 and chord["row"] == 1

    def test_chord_diagram_accordion_am(self, page: Page):
        """GET /api/chord/diagram/accordion/Am returns minor chord mapping"""
        resp = page.request.get(f"{BASE}/api/chord/diagram/accordion/Am")
        assert resp.ok
        data = resp.json()
        assert data["available"] is True
        # A bass at col 5, row 0
        bass = data["buttons"][0]
        assert bass["col"] == 5 and bass["row"] == 0
        # Am at col 5, row 2 (minor row)
        chord = data["buttons"][1]
        assert chord["col"] == 5 and chord["row"] == 2

    def test_chord_diagram_accordion_g7(self, page: Page):
        """GET /api/chord/diagram/accordion/G7 maps to G major button"""
        resp = page.request.get(f"{BASE}/api/chord/diagram/accordion/G7")
        assert resp.ok
        data = resp.json()
        assert data["available"] is True
        # G bass at col 3
        assert data["buttons"][0]["col"] == 3
        # G major at col 3, row 1
        assert data["buttons"][1]["row"] == 1

    def test_chord_diagram_accordion_unavailable_root(self, page: Page):
        """GET /api/chord/diagram/accordion/B returns available=False with warning"""
        resp = page.request.get(f"{BASE}/api/chord/diagram/accordion/B")
        assert resp.ok
        data = resp.json()
        assert data["available"] is False
        assert "warning" in data and data["warning"]

    def test_chord_voicings_accordion(self, page: Page):
        """GET /api/chord/voicings/accordion/C returns single voicing"""
        resp = page.request.get(f"{BASE}/api/chord/voicings/accordion/C")
        assert resp.ok
        data = resp.json()
        assert data["type"] == "accordion"
        assert len(data["voicings"]) >= 1

    def test_chord_diagram_accordion_altbass(self, page: Page):
        """Accordion diagram includes altBass (alternate bass for alternating pattern)"""
        resp = page.request.get(f"{BASE}/api/chord/diagram/accordion/C")
        assert resp.ok
        data = resp.json()
        assert "altBass" in data and data["altBass"] is not None
        # C's alt bass should be G (col 3)
        assert data["altBass"]["col"] == 3
        assert data["altBass"]["label"] == "G"


# ---------------------------------------------------------------------------
# 9. Regression — Existing Instruments Still Work
# ---------------------------------------------------------------------------

class TestAccordionRegression:
    """Existing instruments not broken by accordion addition"""

    def test_guitar_still_works(self, page: Page):
        """Guitar tab still functional"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        _click_instrument_tab(page, "guitar")
        page.wait_for_timeout(2000)
        expect(page.locator("#chordDisplayGuitar")).to_be_visible()
        canvas = page.locator("#guitarVerticalFretboard")
        expect(canvas).to_be_visible()

    def test_ukulele_still_works(self, page: Page):
        """Ukulele tab still functional"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        _click_instrument_tab(page, "ukulele")
        page.wait_for_timeout(2000)
        expect(page.locator("#chordDisplayUkulele")).to_be_visible()

    def test_piano_still_works(self, page: Page):
        """Piano tab still functional"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        _click_instrument_tab(page, "piano")
        page.wait_for_timeout(1500)
        expect(page.locator("#chordDisplayPiano")).to_be_visible()

    def test_guitar_voicings_api_unchanged(self, page: Page):
        """Guitar voicings API still returns numStrings=6"""
        resp = page.request.get(f"{BASE}/api/chord/voicings/guitar/C")
        assert resp.ok
        data = resp.json()
        assert data["numStrings"] == 6

    def test_ukulele_diagram_api_unchanged(self, page: Page):
        """Ukulele diagram API still returns numStrings=4"""
        resp = page.request.get(f"{BASE}/api/chord/diagram/ukulele/Am")
        assert resp.ok
        data = resp.json()
        assert data["numStrings"] == 4
