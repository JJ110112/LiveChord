"""Playwright QA — Arranger Keyboard (編曲鍵盤) integration test
Validates the arranger keyboard instrument tab:
- Page loads without JS errors after adding arranger
- Arranger tab switching and panel visibility
- Waterfall canvas rendering and sizing
- Keyboard canvas rendering
- Split arrow rendering, hover, and drag
- Split point persistence (localStorage)
- Canvas sizing on reload (no oversized text)
- Existing instrument tabs not broken (regression)
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


def _switch_to_arranger(page: Page, wait_ms=3000):
    """Navigate to player and switch to arranger tab."""
    page.goto(player_url())
    page.wait_for_timeout(2000)
    _click_instrument_tab(page, "arranger")
    page.wait_for_timeout(wait_ms)


# ---------------------------------------------------------------------------
# 1. No JS Errors
# ---------------------------------------------------------------------------

class TestArrangerNoJSErrors:
    """Page loads and switches to arranger without JavaScript errors"""

    def test_no_js_errors_on_load(self, page: Page):
        """No JS errors on initial page load (arranger script loaded)"""
        errors = []
        page.on("pageerror", lambda err: errors.append(str(err)))
        page.goto(player_url())
        page.wait_for_timeout(4000)
        assert errors == [], f"JS errors on load: {errors}"

    def test_no_js_errors_switching_to_arranger(self, page: Page):
        """No JS errors when switching to arranger tab"""
        errors = []
        page.on("pageerror", lambda err: errors.append(str(err)))
        page.goto(player_url())
        page.wait_for_timeout(3000)
        _click_instrument_tab(page, "arranger")
        page.wait_for_timeout(2000)
        assert errors == [], f"JS errors switching to arranger: {errors}"

    def test_no_js_errors_cycling_all_tabs_with_arranger(self, page: Page):
        """No JS errors when cycling through ALL instrument tabs including arranger"""
        errors = []
        page.on("pageerror", lambda err: errors.append(str(err)))
        page.goto(player_url())
        page.wait_for_timeout(3000)

        for tab_name in ["guitar", "ukulele", "accordion", "arranger", "piano"]:
            _click_instrument_tab(page, tab_name)
            page.wait_for_timeout(2000)

        assert errors == [], f"JS errors during full tab cycling: {errors}"


# ---------------------------------------------------------------------------
# 2. Tab Switching
# ---------------------------------------------------------------------------

class TestArrangerSwitching:
    """Arranger tab switching via toolbar popup"""

    def test_arranger_button_exists(self, page: Page):
        """Arranger button exists in instrument popup"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        page.evaluate('document.querySelector("#tbInstrument .tb-popup").style.display = "flex"')
        page.wait_for_timeout(300)
        btn = page.locator('#tbInstrument .tb-popup-btn[data-tab="arranger"]')
        expect(btn).to_be_visible()

    def test_switch_to_arranger(self, page: Page):
        """Switch to arranger — arranger panel visible, others hidden"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        _click_instrument_tab(page, "arranger")
        page.wait_for_timeout(1500)
        expect(page.locator("#chordDisplayArranger")).to_be_visible()
        expect(page.locator("#chordDisplayPiano")).to_be_hidden()
        expect(page.locator("#chordDisplayGuitar")).to_be_hidden()

    def test_switch_away_from_arranger(self, page: Page):
        """Switch from arranger to piano — arranger hidden"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        _click_instrument_tab(page, "arranger")
        page.wait_for_timeout(1000)
        _click_instrument_tab(page, "piano")
        page.wait_for_timeout(1000)
        expect(page.locator("#chordDisplayPiano")).to_be_visible()
        expect(page.locator("#chordDisplayArranger")).to_be_hidden()

    def test_arranger_tab_persists_in_localstorage(self, page: Page):
        """Tab selection 'arranger' persists via localStorage"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        _click_instrument_tab(page, "arranger")
        page.wait_for_timeout(1000)
        stored = page.evaluate("localStorage.getItem('livechord_tab')")
        assert stored == "arranger", f"Expected 'arranger', got '{stored}'"


# ---------------------------------------------------------------------------
# 3. Waterfall Canvas
# ---------------------------------------------------------------------------

class TestArrangerWaterfall:
    """Waterfall canvas rendering and sizing"""

    def test_waterfall_canvas_exists(self, page: Page):
        """Waterfall canvas exists and is visible"""
        _switch_to_arranger(page)
        canvas = page.locator("#arrangerWaterfall")
        expect(canvas).to_be_visible()

    def test_waterfall_canvas_has_dimensions(self, page: Page):
        """Waterfall canvas has non-zero dimensions"""
        _switch_to_arranger(page)
        canvas = page.locator("#arrangerWaterfall")
        box = canvas.bounding_box()
        assert box and box["width"] > 100 and box["height"] > 100, \
            f"Waterfall canvas should have reasonable size, got {box}"

    def test_waterfall_canvas_rendered(self, page: Page):
        """Waterfall canvas has pixel data (not blank)"""
        _switch_to_arranger(page)
        has_pixels = page.evaluate("""() => {
            const c = document.querySelector('#arrangerWaterfall');
            if (!c) return false;
            const ctx = c.getContext('2d');
            const data = ctx.getImageData(0, 0, c.width, c.height).data;
            for (let i = 3; i < data.length; i += 4) {
                if (data[i] > 0) return true;
            }
            return false;
        }""")
        assert has_pixels, "Waterfall canvas should have rendered content"

    def test_waterfall_no_explicit_style_height(self, page: Page):
        """Waterfall canvas must NOT have inline style.height (flex controls sizing)"""
        _switch_to_arranger(page)
        style_h = page.evaluate("""() => {
            const c = document.querySelector('#arrangerWaterfall');
            return c ? c.style.height : '';
        }""")
        assert style_h == "", f"Waterfall should have no inline style.height, got '{style_h}'"


# ---------------------------------------------------------------------------
# 4. Keyboard Canvas
# ---------------------------------------------------------------------------

class TestArrangerKeyboard:
    """Keyboard canvas rendering"""

    def test_keyboard_canvas_exists(self, page: Page):
        """Keyboard canvas exists and is visible"""
        _switch_to_arranger(page)
        canvas = page.locator("#arrangerKeyboard")
        expect(canvas).to_be_visible()

    def test_keyboard_canvas_has_dimensions(self, page: Page):
        """Keyboard canvas has non-zero dimensions"""
        _switch_to_arranger(page)
        canvas = page.locator("#arrangerKeyboard")
        box = canvas.bounding_box()
        assert box and box["width"] > 100 and box["height"] > 30, \
            f"Keyboard canvas should have reasonable size, got {box}"

    def test_keyboard_canvas_rendered(self, page: Page):
        """Keyboard canvas has pixel data (not blank)"""
        _switch_to_arranger(page)
        has_pixels = page.evaluate("""() => {
            const c = document.querySelector('#arrangerKeyboard');
            if (!c) return false;
            const ctx = c.getContext('2d');
            const data = ctx.getImageData(0, 0, c.width, c.height).data;
            for (let i = 3; i < data.length; i += 4) {
                if (data[i] > 0) return true;
            }
            return false;
        }""")
        assert has_pixels, "Keyboard canvas should have rendered content"


# ---------------------------------------------------------------------------
# 5. Split Arrow
# ---------------------------------------------------------------------------

class TestArrangerSplitArrow:
    """Split point arrow rendering and interaction"""

    def test_split_arrow_rendered(self, page: Page):
        """Split arrow has red pixels in top area of keyboard canvas"""
        _switch_to_arranger(page)
        has_red = page.evaluate("""() => {
            const c = document.querySelector('#arrangerKeyboard');
            if (!c) return false;
            const ctx = c.getContext('2d');
            // Check top 20px of canvas for red-ish pixels (the arrow)
            const data = ctx.getImageData(0, 0, c.width, 20).data;
            for (let i = 0; i < data.length; i += 4) {
                if (data[i] > 150 && data[i+1] < 100 && data[i+2] < 100 && data[i+3] > 50) {
                    return true;
                }
            }
            return false;
        }""")
        assert has_red, "Split arrow should render red pixels in top area of keyboard"

    def test_split_default_value(self, page: Page):
        """Default split point is MIDI 56 (G#3)"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        # Clear any stored value first
        page.evaluate("localStorage.removeItem('livechord_arranger_split')")
        _click_instrument_tab(page, "arranger")
        page.wait_for_timeout(2000)
        # Read the actual split point from the instrument instance
        split = page.evaluate("""() => {
            const stored = localStorage.getItem('livechord_arranger_split');
            return stored ? parseInt(stored) : 56;
        }""")
        assert split == 56, f"Default split should be 56 (G#3), got {split}"

    def test_split_hover_cursor(self, page: Page):
        """Hovering near split arrow changes cursor to 'grab'"""
        _switch_to_arranger(page)
        # Get arrow position from JS
        pos = page.evaluate("""() => {
            const c = document.querySelector('#arrangerKeyboard');
            if (!c) return null;
            const rect = c.getBoundingClientRect();
            const ctx = c.getContext('2d');
            // Scan top rows for red pixels to find arrow center
            const dpr = window.devicePixelRatio || 1;
            const data = ctx.getImageData(0, 0, c.width, Math.round(14 * dpr)).data;
            let sumX = 0, count = 0;
            const stride = c.width * 4;
            for (let x = 0; x < c.width; x++) {
                for (let y = 0; y < Math.round(14 * dpr); y++) {
                    const i = (y * c.width + x) * 4;
                    if (data[i] > 150 && data[i+1] < 100 && data[i+2] < 100 && data[i+3] > 50) {
                        sumX += x; count++;
                    }
                }
            }
            if (count === 0) return null;
            const centerX = (sumX / count) / dpr;
            return { x: rect.left + centerX, y: rect.top + 7 };
        }""")
        assert pos is not None, "Could not find split arrow position"
        page.mouse.move(pos["x"], pos["y"])
        page.wait_for_timeout(200)
        cursor = page.evaluate("document.querySelector('#arrangerKeyboard').style.cursor")
        assert cursor == "grab", f"Cursor should be 'grab' near arrow, got '{cursor}'"


# ---------------------------------------------------------------------------
# 6. Split Drag
# ---------------------------------------------------------------------------

class TestArrangerSplitDrag:
    """Split point drag interaction"""

    def test_split_drag_changes_value(self, page: Page):
        """Dragging split arrow horizontally changes localStorage value"""
        _switch_to_arranger(page)
        # Set known split to 56
        page.evaluate("localStorage.setItem('livechord_arranger_split', '56')")
        page.reload()
        page.wait_for_timeout(2000)
        _click_instrument_tab(page, "arranger")
        page.wait_for_timeout(3000)

        # Find arrow position
        pos = page.evaluate("""() => {
            const c = document.querySelector('#arrangerKeyboard');
            if (!c) return null;
            const rect = c.getBoundingClientRect();
            const ctx = c.getContext('2d');
            const dpr = window.devicePixelRatio || 1;
            const data = ctx.getImageData(0, 0, c.width, Math.round(14 * dpr)).data;
            let sumX = 0, count = 0;
            for (let x = 0; x < c.width; x++) {
                for (let y = 0; y < Math.round(14 * dpr); y++) {
                    const i = (y * c.width + x) * 4;
                    if (data[i] > 150 && data[i+1] < 100 && data[i+2] < 100 && data[i+3] > 50) {
                        sumX += x; count++;
                    }
                }
            }
            if (count === 0) return null;
            const centerX = (sumX / count) / dpr;
            return { x: rect.left + centerX, y: rect.top + 7 };
        }""")
        assert pos is not None, "Could not find split arrow"

        # Drag 80px to the right
        page.mouse.move(pos["x"], pos["y"])
        page.mouse.down()
        page.mouse.move(pos["x"] + 80, pos["y"], steps=10)
        page.mouse.up()
        page.wait_for_timeout(500)

        new_split = page.evaluate("parseInt(localStorage.getItem('livechord_arranger_split') || '56')")
        assert new_split != 56, f"Split should have changed from 56 after drag, got {new_split}"
        assert 48 <= new_split <= 60, f"Split should be in valid range 48-60, got {new_split}"

    def test_split_drag_outside_canvas(self, page: Page):
        """Drag continues even when mouse moves above canvas (bug fix validation)"""
        _switch_to_arranger(page)
        page.evaluate("localStorage.setItem('livechord_arranger_split', '56')")
        page.reload()
        page.wait_for_timeout(2000)
        _click_instrument_tab(page, "arranger")
        page.wait_for_timeout(3000)

        pos = page.evaluate("""() => {
            const c = document.querySelector('#arrangerKeyboard');
            if (!c) return null;
            const rect = c.getBoundingClientRect();
            const ctx = c.getContext('2d');
            const dpr = window.devicePixelRatio || 1;
            const data = ctx.getImageData(0, 0, c.width, Math.round(14 * dpr)).data;
            let sumX = 0, count = 0;
            for (let x = 0; x < c.width; x++) {
                for (let y = 0; y < Math.round(14 * dpr); y++) {
                    const i = (y * c.width + x) * 4;
                    if (data[i] > 150 && data[i+1] < 100 && data[i+2] < 100 && data[i+3] > 50) {
                        sumX += x; count++;
                    }
                }
            }
            if (count === 0) return null;
            const centerX = (sumX / count) / dpr;
            return { x: rect.left + centerX, y: rect.top + 7, top: rect.top };
        }""")
        assert pos is not None, "Could not find split arrow"

        # Drag RIGHT and UPWARD (above canvas — the original bug scenario)
        page.mouse.move(pos["x"], pos["y"])
        page.mouse.down()
        # Move up above the canvas while dragging right
        page.mouse.move(pos["x"] + 60, pos["top"] - 50, steps=10)
        page.mouse.up()
        page.wait_for_timeout(500)

        new_split = page.evaluate("parseInt(localStorage.getItem('livechord_arranger_split') || '56')")
        assert new_split != 56, f"Drag should work even outside canvas, split stayed at 56"


# ---------------------------------------------------------------------------
# 7. Split Persistence & Migration
# ---------------------------------------------------------------------------

class TestArrangerSplitPersistence:
    """Split point localStorage persistence and migration"""

    def test_split_persists_on_reload(self, page: Page):
        """Split point value persists across page reloads"""
        _switch_to_arranger(page)
        page.evaluate("localStorage.setItem('livechord_arranger_split', '52')")
        page.reload()
        page.wait_for_timeout(3000)
        stored = page.evaluate("localStorage.getItem('livechord_arranger_split')")
        assert stored == "52", f"Split should persist as '52', got '{stored}'"

    def test_valid_low_split_not_cleared(self, page: Page):
        """MIDI 48 (valid minimum) is NOT cleared by migration"""
        page.goto(player_url())
        page.wait_for_timeout(1000)
        page.evaluate("localStorage.setItem('livechord_arranger_split', '48')")
        page.reload()
        page.wait_for_timeout(3000)
        stored = page.evaluate("localStorage.getItem('livechord_arranger_split')")
        assert stored == "48", f"MIDI 48 is valid and should persist, got '{stored}'"

    def test_invalid_split_cleared_by_migration(self, page: Page):
        """MIDI 47 (below valid range) is cleared by migration"""
        page.goto(player_url())
        page.wait_for_timeout(1000)
        page.evaluate("localStorage.setItem('livechord_arranger_split', '47')")
        page.reload()
        page.wait_for_timeout(3000)
        stored = page.evaluate("localStorage.getItem('livechord_arranger_split')")
        assert stored is None, f"MIDI 47 should be cleared by migration, got '{stored}'"


# ---------------------------------------------------------------------------
# 8. Canvas Sizing on Reload (Bug 1 validation)
# ---------------------------------------------------------------------------

class TestArrangerCanvasSizing:
    """Validates no oversized text after hard reload"""

    def test_waterfall_buffer_matches_display_while_paused(self, page: Page):
        """Canvas buffer dimensions must match CSS display size (no enlarged text).
        This is the core bug: update() only runs during playback, so after init
        the buffer could be stuck at wrong dimensions if flex layout changed."""
        _switch_to_arranger(page)
        result = page.evaluate("""() => {
            const c = document.querySelector('#arrangerWaterfall');
            if (!c) return { error: 'no canvas' };
            const dpr = window.devicePixelRatio || 1;
            const cssW = c.clientWidth;
            const cssH = c.clientHeight;
            const bufW = c.width;
            const bufH = c.height;
            return { cssW, cssH, bufW, bufH, dpr,
                     expectW: Math.round(cssW * dpr),
                     expectH: Math.round(cssH * dpr) };
        }""")
        assert "error" not in result, result.get("error", "")
        assert abs(result["bufW"] - result["expectW"]) <= 1, \
            f"Buffer width {result['bufW']} != expected {result['expectW']} (CSS {result['cssW']} * dpr {result['dpr']})"
        assert abs(result["bufH"] - result["expectH"]) <= 1, \
            f"Buffer height {result['bufH']} != expected {result['expectH']} (CSS {result['cssH']} * dpr {result['dpr']})"

    def test_waterfall_buffer_after_arranger_reload(self, page: Page):
        """Buffer matches display after arranger tab reload (direct, no play)"""
        page.goto(player_url())
        page.wait_for_timeout(1000)
        page.evaluate("localStorage.setItem('livechord_tab', 'arranger')")
        page.reload()
        page.wait_for_timeout(4000)
        result = page.evaluate("""() => {
            const c = document.querySelector('#arrangerWaterfall');
            if (!c) return { error: 'no canvas' };
            const dpr = window.devicePixelRatio || 1;
            return { bufH: c.height, expectH: Math.round(c.clientHeight * dpr),
                     cssH: c.clientHeight };
        }""")
        assert "error" not in result, result.get("error", "")
        assert abs(result["bufH"] - result["expectH"]) <= 1, \
            f"After reload: buffer H {result['bufH']} != expected {result['expectH']} (CSS H {result['cssH']})"

    def test_waterfall_buffer_after_piano_to_arranger(self, page: Page):
        """Buffer matches display after piano->arranger switch (no play)"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        _click_instrument_tab(page, "piano")
        page.wait_for_timeout(1000)
        _click_instrument_tab(page, "arranger")
        page.wait_for_timeout(3000)
        result = page.evaluate("""() => {
            const c = document.querySelector('#arrangerWaterfall');
            if (!c) return { error: 'no canvas' };
            const dpr = window.devicePixelRatio || 1;
            return { bufH: c.height, expectH: Math.round(c.clientHeight * dpr),
                     cssH: c.clientHeight };
        }""")
        assert "error" not in result, result.get("error", "")
        assert abs(result["bufH"] - result["expectH"]) <= 1, \
            f"After switch: buffer H {result['bufH']} != expected {result['expectH']} (CSS H {result['cssH']})"

    def test_waterfall_size_stable_after_reload(self, page: Page):
        """Waterfall canvas dimensions stable: first load vs reload"""
        # First load
        page.goto(player_url())
        page.wait_for_timeout(2000)
        _click_instrument_tab(page, "arranger")
        page.wait_for_timeout(3000)
        box1 = page.locator("#arrangerWaterfall").bounding_box()
        assert box1, "Waterfall should have a bounding box"

        # Reload
        page.reload()
        page.wait_for_timeout(3000)
        _click_instrument_tab(page, "arranger")
        page.wait_for_timeout(3000)
        box2 = page.locator("#arrangerWaterfall").bounding_box()
        assert box2, "Waterfall should have a bounding box after reload"

        # Dimensions should match within 5px tolerance
        assert abs(box1["width"] - box2["width"]) < 5, \
            f"Width changed after reload: {box1['width']} -> {box2['width']}"
        assert abs(box1["height"] - box2["height"]) < 5, \
            f"Height changed after reload: {box1['height']} -> {box2['height']}"

    def test_direct_arranger_reload(self, page: Page):
        """Switch to arranger, reload page — arranger should render correctly"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        _click_instrument_tab(page, "arranger")
        page.wait_for_timeout(2000)
        # Now localStorage has livechord_tab=arranger
        page.reload()
        page.wait_for_timeout(4000)

        # Arranger should be visible (restored from localStorage)
        expect(page.locator("#chordDisplayArranger")).to_be_visible()

        # Both canvases should have rendered content
        wf_rendered = page.evaluate("""() => {
            const c = document.querySelector('#arrangerWaterfall');
            if (!c) return false;
            const ctx = c.getContext('2d');
            const data = ctx.getImageData(0, 0, c.width, c.height).data;
            for (let i = 3; i < data.length; i += 4) {
                if (data[i] > 0) return true;
            }
            return false;
        }""")
        assert wf_rendered, "Waterfall should render after direct arranger reload"

    def test_piano_to_arranger_switch_sizes_match(self, page: Page):
        """Canvas sizes same whether loading arranger directly or via piano->arranger"""
        # Method 1: Load arranger directly via localStorage
        page.goto(player_url())
        page.wait_for_timeout(1000)
        page.evaluate("localStorage.setItem('livechord_tab', 'arranger')")
        page.reload()
        page.wait_for_timeout(4000)
        box_direct = page.locator("#arrangerWaterfall").bounding_box()

        # Method 2: Load piano first, then switch to arranger
        page.evaluate("localStorage.setItem('livechord_tab', 'piano')")
        page.reload()
        page.wait_for_timeout(2000)
        _click_instrument_tab(page, "arranger")
        page.wait_for_timeout(3000)
        box_switch = page.locator("#arrangerWaterfall").bounding_box()

        assert box_direct and box_switch, "Both should have bounding boxes"
        assert abs(box_direct["height"] - box_switch["height"]) < 10, \
            f"Direct load height {box_direct['height']} vs switch height {box_switch['height']} differ too much"


# ---------------------------------------------------------------------------
# 9. API Endpoints
# ---------------------------------------------------------------------------

class TestArrangerAPI:
    """Arranger-related API endpoints"""

    def test_instruments_endpoint_includes_arranger(self, page: Page):
        """GET /api/instruments returns arranger metadata"""
        resp = page.request.get(f"{BASE}/api/instruments")
        assert resp.ok
        data = resp.json()
        assert "arranger" in data, "arranger should be in instruments list"

    def test_chord_diagram_arranger_c(self, page: Page):
        """GET /api/chord/diagram/arranger/C returns MIDI notes"""
        resp = page.request.get(f"{BASE}/api/chord/diagram/arranger/C?split=56")
        assert resp.ok
        data = resp.json()
        assert data["available"] is True
        assert len(data["midi_notes"]) >= 2, "C chord should have at least 2 MIDI notes"

    def test_chord_diagram_arranger_split_param(self, page: Page):
        """Split parameter affects arranger chord voicing"""
        resp48 = page.request.get(f"{BASE}/api/chord/diagram/arranger/C?split=48")
        resp60 = page.request.get(f"{BASE}/api/chord/diagram/arranger/C?split=60")
        assert resp48.ok and resp60.ok
        data48 = resp48.json()
        data60 = resp60.json()
        # Higher split allows more notes below split
        assert data60["split_point"] == 60
        assert data48["split_point"] == 48


# ---------------------------------------------------------------------------
# 10. Regression — Existing Instruments Still Work
# ---------------------------------------------------------------------------

class TestArrangerRegression:
    """Existing instruments not broken by arranger addition"""

    def test_piano_still_works(self, page: Page):
        """Piano tab still functional after visiting arranger"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        _click_instrument_tab(page, "arranger")
        page.wait_for_timeout(2000)
        _click_instrument_tab(page, "piano")
        page.wait_for_timeout(1500)
        expect(page.locator("#chordDisplayPiano")).to_be_visible()

    def test_guitar_still_works(self, page: Page):
        """Guitar tab still functional after visiting arranger"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        _click_instrument_tab(page, "arranger")
        page.wait_for_timeout(1000)
        _click_instrument_tab(page, "guitar")
        page.wait_for_timeout(2000)
        expect(page.locator("#chordDisplayGuitar")).to_be_visible()

    def test_accordion_still_works(self, page: Page):
        """Accordion tab still functional after visiting arranger"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        _click_instrument_tab(page, "arranger")
        page.wait_for_timeout(1000)
        _click_instrument_tab(page, "accordion")
        page.wait_for_timeout(2000)
        expect(page.locator("#chordDisplayAccordion")).to_be_visible()
