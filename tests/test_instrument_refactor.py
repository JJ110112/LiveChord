"""Playwright QA — Instrument refactoring regression test
Validates that the StringInstrument registry refactoring does not break:
- Page loading (no JS errors)
- Piano / Guitar / Ukulele tab switching
- Chord display in all modes
- Canvas rendering (fretboard, waterfall)
"""

import re
import pytest
from playwright.sync_api import Page, expect

BASE = "http://192.168.50.6:8800"
TEST_TRACK = "Jazz/Bob James/Bob James - Jazz Hands/Beerbohm.flac"


def player_url(track=TEST_TRACK):
    from urllib.parse import quote
    return f"{BASE}/player?path={quote(track, safe='')}"


class TestNoJSErrors:
    """Page loads without JavaScript errors"""

    def test_no_js_errors_on_load(self, page: Page):
        """No JS errors on initial page load"""
        errors = []
        page.on("pageerror", lambda err: errors.append(str(err)))
        page.goto(player_url())
        page.wait_for_timeout(4000)
        assert errors == [], f"JS errors on load: {errors}"

    def test_no_js_errors_switching_all_tabs(self, page: Page):
        """No JS errors when cycling through all instrument tabs"""
        errors = []
        page.on("pageerror", lambda err: errors.append(str(err)))
        page.goto(player_url())
        page.wait_for_timeout(3000)

        # Open instrument popup (hover to reveal) and switch to each instrument
        for tab_name in ["guitar", "ukulele", "piano"]:
            page.locator("#tbInstrument").hover()
            page.wait_for_timeout(300)
            page.locator(f'#tbInstrument .tb-popup-btn[data-tab="{tab_name}"]').click()
            page.wait_for_timeout(2000)

        assert errors == [], f"JS errors during tab switching: {errors}"


class TestInstrumentSwitching:
    """Instrument tab switching via toolbar popup"""

    def test_instrument_button_exists(self, page: Page):
        """Instrument trigger button exists"""
        page.goto(player_url())
        page.wait_for_selector("#btnInstrument", timeout=5000)
        expect(page.locator("#btnInstrument")).to_be_visible()

    def test_switch_to_guitar(self, page: Page):
        """Switch to guitar — guitar panel visible"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        page.locator("#tbInstrument").hover()
        page.wait_for_timeout(300)
        page.locator('#tbInstrument .tb-popup-btn[data-tab="guitar"]').click()
        page.wait_for_timeout(1500)
        expect(page.locator("#chordDisplayGuitar")).to_be_visible()
        expect(page.locator("#chordDisplayPiano")).to_be_hidden()

    def test_switch_to_ukulele(self, page: Page):
        """Switch to ukulele — ukulele panel visible"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        page.locator("#tbInstrument").hover()
        page.wait_for_timeout(300)
        page.locator('#tbInstrument .tb-popup-btn[data-tab="ukulele"]').click()
        page.wait_for_timeout(1500)
        expect(page.locator("#chordDisplayUkulele")).to_be_visible()
        expect(page.locator("#chordDisplayPiano")).to_be_hidden()

    def test_switch_to_piano(self, page: Page):
        """Switch to piano — piano panel visible"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        # First switch to guitar, then back to piano
        page.locator("#tbInstrument").hover()
        page.wait_for_timeout(300)
        page.locator('#tbInstrument .tb-popup-btn[data-tab="guitar"]').click()
        page.wait_for_timeout(1000)
        page.locator("#tbInstrument").hover()
        page.wait_for_timeout(300)
        page.locator('#tbInstrument .tb-popup-btn[data-tab="piano"]').click()
        page.wait_for_timeout(1000)
        expect(page.locator("#chordDisplayPiano")).to_be_visible()
        expect(page.locator("#chordDisplayGuitar")).to_be_hidden()

    def test_tab_persists_in_localstorage(self, page: Page):
        """Tab selection persists via localStorage"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        page.locator("#tbInstrument").hover()
        page.wait_for_timeout(300)
        page.locator('#tbInstrument .tb-popup-btn[data-tab="guitar"]').click()
        page.wait_for_timeout(1000)
        stored = page.evaluate("localStorage.getItem('livechord_tab')")
        assert stored == "guitar", f"Expected 'guitar', got '{stored}'"


class TestGuitarTab:
    """Guitar tab via StringInstrument class"""

    def test_guitar_fretboard_canvas(self, page: Page):
        """Guitar fretboard canvas exists and has dimensions"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        page.locator("#tbInstrument").hover()
        page.wait_for_timeout(300)
        page.locator('#tbInstrument .tb-popup-btn[data-tab="guitar"]').click()
        page.wait_for_timeout(3000)
        canvas = page.locator("#guitarVerticalFretboard")
        expect(canvas).to_be_visible()
        box = canvas.bounding_box()
        assert box and box["width"] > 0 and box["height"] > 0

    def test_guitar_waterfall_canvas(self, page: Page):
        """Guitar waterfall canvas exists"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        page.locator("#tbInstrument").hover()
        page.wait_for_timeout(300)
        page.locator('#tbInstrument .tb-popup-btn[data-tab="guitar"]').click()
        page.wait_for_timeout(3000)
        canvas = page.locator("#guitarRhWaterfall")
        expect(canvas).to_be_visible()

    def test_guitar_chord_name_populated(self, page: Page):
        """Guitar chord name is populated (not '—')"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        page.locator("#tbInstrument").hover()
        page.wait_for_timeout(300)
        page.locator('#tbInstrument .tb-popup-btn[data-tab="guitar"]').click()
        page.wait_for_timeout(5000)
        name = page.locator("#gtChordName").text_content()
        assert name and name != "—", f"Expected chord name, got '{name}'"

    def test_guitar_voicing_row_exists(self, page: Page):
        """Voicing row DOM element exists (may be empty if chord has 1 voicing)"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        page.locator("#tbInstrument").hover()
        page.wait_for_timeout(300)
        page.locator('#tbInstrument .tb-popup-btn[data-tab="guitar"]').click()
        page.wait_for_timeout(5000)
        row = page.locator("#gtVoicingRow")
        assert row.count() == 1, "Voicing row element should exist in DOM"


class TestUkuleleTab:
    """Ukulele tab via StringInstrument class"""

    def test_ukulele_fretboard_canvas(self, page: Page):
        """Ukulele fretboard canvas exists"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        page.locator("#tbInstrument").hover()
        page.wait_for_timeout(300)
        page.locator('#tbInstrument .tb-popup-btn[data-tab="ukulele"]').click()
        page.wait_for_timeout(3000)
        canvas = page.locator("#ukuleleVerticalFretboard")
        expect(canvas).to_be_visible()
        box = canvas.bounding_box()
        assert box and box["width"] > 0 and box["height"] > 0

    def test_ukulele_chord_name_populated(self, page: Page):
        """Ukulele chord name is populated"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        page.locator("#tbInstrument").hover()
        page.wait_for_timeout(300)
        page.locator('#tbInstrument .tb-popup-btn[data-tab="ukulele"]').click()
        page.wait_for_timeout(5000)
        name = page.locator("#ukChordName").text_content()
        assert name and name != "—", f"Expected chord name, got '{name}'"

    def test_ukulele_waterfall_canvas(self, page: Page):
        """Ukulele waterfall canvas exists"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        page.locator("#tbInstrument").hover()
        page.wait_for_timeout(300)
        page.locator('#tbInstrument .tb-popup-btn[data-tab="ukulele"]').click()
        page.wait_for_timeout(3000)
        canvas = page.locator("#ukuleleRhWaterfall")
        expect(canvas).to_be_visible()


class TestPianoTab:
    """Piano tab — not refactored, but must not break"""

    def test_piano_canvas_visible(self, page: Page):
        """Piano 88-key canvas renders"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        page.locator("#tbInstrument").hover()
        page.wait_for_timeout(300)
        page.locator('#tbInstrument .tb-popup-btn[data-tab="piano"]').click()
        page.wait_for_timeout(1500)
        expect(page.locator("#piano88Canvas")).to_be_visible()

    def test_waterfall_canvas_visible(self, page: Page):
        """Waterfall canvas renders in piano tab"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        page.locator("#tbInstrument").hover()
        page.wait_for_timeout(300)
        page.locator('#tbInstrument .tb-popup-btn[data-tab="piano"]').click()
        page.wait_for_timeout(1500)
        expect(page.locator("#waterfallCanvas")).to_be_visible()


class TestCapoVisibility:
    """Capo control visibility based on instrument type"""

    def test_capo_display_for_guitar(self, page: Page):
        """Capo group display:'' (not none) when guitar is active"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        page.locator("#tbInstrument").hover()
        page.wait_for_timeout(300)
        page.locator('#tbInstrument .tb-popup-btn[data-tab="guitar"]').click()
        page.wait_for_timeout(1000)
        display = page.locator("#capoGroup").evaluate("el => el.style.display")
        assert display != "none", f"Capo should not be display:none for guitar, got '{display}'"

    def test_capo_display_for_ukulele(self, page: Page):
        """Capo group display:'' (not none) when ukulele is active"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        page.locator("#tbInstrument").hover()
        page.wait_for_timeout(300)
        page.locator('#tbInstrument .tb-popup-btn[data-tab="ukulele"]').click()
        page.wait_for_timeout(1000)
        display = page.locator("#capoGroup").evaluate("el => el.style.display")
        assert display != "none", f"Capo should not be display:none for ukulele, got '{display}'"

    def test_capo_hidden_for_piano(self, page: Page):
        """Capo group display:none when piano is active"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        page.locator("#tbInstrument").hover()
        page.wait_for_timeout(300)
        page.locator('#tbInstrument .tb-popup-btn[data-tab="piano"]').click()
        page.wait_for_timeout(1000)
        display = page.locator("#capoGroup").evaluate("el => el.style.display")
        assert display == "none", f"Capo should be display:none for piano, got '{display}'"


class TestNewAPIEndpoint:
    """New /api/instruments endpoint"""

    def test_instruments_endpoint(self, page: Page):
        """GET /api/instruments returns instrument metadata"""
        resp = page.request.get(f"{BASE}/api/instruments")
        assert resp.ok
        data = resp.json()
        assert "guitar" in data
        assert "ukulele" in data
        assert data["guitar"]["num_strings"] == 6
        assert data["ukulele"]["num_strings"] == 4

    def test_chord_voicings_still_works(self, page: Page):
        """GET /api/chord/voicings/guitar/C still returns voicings"""
        resp = page.request.get(f"{BASE}/api/chord/voicings/guitar/C")
        assert resp.ok
        data = resp.json()
        assert data["numStrings"] == 6
        assert len(data["voicings"]) >= 1

    def test_chord_diagram_ukulele_still_works(self, page: Page):
        """GET /api/chord/diagram/ukulele/Am still returns 4-string diagram"""
        resp = page.request.get(f"{BASE}/api/chord/diagram/ukulele/Am")
        assert resp.ok
        data = resp.json()
        assert data["numStrings"] == 4
