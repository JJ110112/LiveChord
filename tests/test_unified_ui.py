"""Unified UI + Guitar Teaching Mode — Playwright QA"""

import re
import pytest
from playwright.sync_api import Page, expect

BASE = "http://192.168.50.6:8800"
TEST_TRACK = "Jazz/Bob James/Bob James - Jazz Hands/Beerbohm.flac"


def player_url(track=TEST_TRACK):
    from urllib.parse import quote
    return f"{BASE}/player?path={quote(track, safe='')}"


class TestImmersiveLayout:
    """預設沉浸式佈局"""

    def test_display_area_is_fixed(self, page: Page):
        """UI-01: chord-display-area 預設 position:fixed (沉浸式)"""
        page.goto(player_url())
        page.wait_for_selector("#chordDisplay", timeout=8000)
        pos = page.locator("#chordDisplay").evaluate(
            "el => getComputedStyle(el).position"
        )
        assert pos == "fixed", f"Expected fixed, got {pos}"

    def test_overlay_tools_visible(self, page: Page):
        """UI-02: 頂部覆蓋工具列 (迷你播放控制) 可見"""
        page.goto(player_url())
        page.wait_for_selector(".chord-overlay-tools", timeout=8000)
        expect(page.locator(".chord-overlay-tools")).to_be_visible()

    def test_no_fullscreen_button(self, page: Page):
        """UI-03: 不再有 #btnFullscreen 按鈕"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        assert page.locator("#btnFullscreen").count() == 0


class TestTabSwitching:
    """三 Tab 切換: Overview | Piano | Guitar"""

    def test_three_tabs_exist(self, page: Page):
        """TAB-01: 三個 tab 按鈕存在"""
        page.goto(player_url())
        page.wait_for_selector("#tabOverview", timeout=8000)
        expect(page.locator("#tabOverview")).to_be_visible()
        expect(page.locator("#tabPiano")).to_be_visible()
        expect(page.locator("#tabGuitar")).to_be_visible()

    def test_no_old_tabs(self, page: Page):
        """TAB-02: 舊 tab (#tabDiagrams, #tabKeys) 不存在"""
        page.goto(player_url())
        page.wait_for_timeout(2000)
        assert page.locator("#tabDiagrams").count() == 0
        assert page.locator("#tabKeys").count() == 0

    def test_overview_default_active(self, page: Page):
        """TAB-03: Overview tab 預設 active"""
        page.goto(player_url())
        page.wait_for_selector("#tabOverview", timeout=8000)
        expect(page.locator("#tabOverview")).to_have_class(re.compile("active"))

    def test_switch_to_piano(self, page: Page):
        """TAB-04: 點擊 Piano tab 切換成功"""
        page.goto(player_url())
        page.wait_for_selector("#tabPiano", timeout=8000)
        page.locator("#tabPiano").click()
        page.wait_for_timeout(500)
        expect(page.locator("#tabPiano")).to_have_class(re.compile("active"))
        expect(page.locator("#chordDisplayPiano")).to_be_visible()
        # Overview 應該隱藏
        expect(page.locator("#chordDisplayOverview")).to_be_hidden()

    def test_switch_to_guitar(self, page: Page):
        """TAB-05: 點擊 Guitar tab 切換成功"""
        page.goto(player_url())
        page.wait_for_selector("#tabGuitar", timeout=8000)
        page.locator("#tabGuitar").click()
        page.wait_for_timeout(500)
        expect(page.locator("#tabGuitar")).to_have_class(re.compile("active"))
        expect(page.locator("#chordDisplayGuitar")).to_be_visible()

    def test_switch_back_to_overview(self, page: Page):
        """TAB-06: 切換回 Overview"""
        page.goto(player_url())
        page.wait_for_selector("#tabGuitar", timeout=8000)
        page.locator("#tabGuitar").click()
        page.wait_for_timeout(300)
        page.locator("#tabOverview").click()
        page.wait_for_timeout(300)
        expect(page.locator("#tabOverview")).to_have_class(re.compile("active"))
        expect(page.locator("#chordDisplayOverview")).to_be_visible()


class TestToolbar:
    """統一工具列"""

    def test_toolbar_visible(self, page: Page):
        """TB-01: 底部工具列可見"""
        page.goto(player_url())
        page.wait_for_selector(".chord-toolbar", timeout=8000)
        expect(page.locator(".chord-toolbar")).to_be_visible()

    def test_ab_loop_in_toolbar(self, page: Page):
        """TB-02: A-B Loop 按鈕在工具列中 (所有 tab 共用)"""
        page.goto(player_url())
        page.wait_for_selector("#btnABRepeat", timeout=8000)
        expect(page.locator("#btnABRepeat")).to_be_visible()

    def test_ab_loop_visible_in_guitar_tab(self, page: Page):
        """TB-03: A-B Loop 在 Guitar tab 也可見"""
        page.goto(player_url())
        page.wait_for_selector("#tabGuitar", timeout=8000)
        page.locator("#tabGuitar").click()
        page.wait_for_timeout(500)
        expect(page.locator("#btnABRepeat")).to_be_visible()

    def test_instrument_dropdown(self, page: Page):
        """TB-04: 樂器切換 dropdown"""
        page.goto(player_url())
        page.wait_for_selector("#btnInstrument", timeout=8000)
        expect(page.locator("#btnInstrument")).to_be_visible()
        # 點開 dropdown
        page.locator("#btnInstrument").click()
        expect(page.locator("#instrumentMenu")).to_be_visible()
        # 選吉他
        page.locator('.inst-option[data-mode="guitar"]').click()
        page.wait_for_timeout(300)
        # dropdown 應關閉
        expect(page.locator("#instrumentMenu")).to_be_hidden()

    def test_transpose_visible(self, page: Page):
        """TB-05: 移調控制可見"""
        page.goto(player_url())
        page.wait_for_selector("#btnTransposeUp", timeout=8000)
        expect(page.locator("#btnTransposeUp")).to_be_visible()
        expect(page.locator("#btnTransposeDown")).to_be_visible()


class TestPianoTab:
    """Piano tab (合併 Diagrams + 88-Keys)"""

    def test_piano_submode_buttons(self, page: Page):
        """PIANO-01: 靜態/瀑布子模式按鈕"""
        page.goto(player_url())
        page.wait_for_selector("#tabPiano", timeout=8000)
        page.locator("#tabPiano").click()
        page.wait_for_timeout(500)
        expect(page.locator("#btnSubStatic")).to_be_visible()
        expect(page.locator("#btnSubWaterfall")).to_be_visible()

    def test_waterfall_default_active(self, page: Page):
        """PIANO-02: 瀑布子模式預設 active"""
        page.goto(player_url())
        page.wait_for_selector("#tabPiano", timeout=8000)
        page.locator("#tabPiano").click()
        page.wait_for_timeout(500)
        expect(page.locator("#btnSubWaterfall")).to_have_class(re.compile("active"))

    def test_teach_controls_visible(self, page: Page):
        """PIANO-03: 教學控制 (伴奏風格等) 在 Piano tab 可見"""
        page.goto(player_url())
        page.wait_for_selector("#tabPiano", timeout=8000)
        page.locator("#tabPiano").click()
        page.wait_for_timeout(500)
        expect(page.locator("#teachControls")).to_be_visible()


class TestGuitarTab:
    """Guitar 教學模式"""

    def test_guitar_layout_three_panels(self, page: Page):
        """GT-01: 吉他 tab 三欄佈局"""
        page.goto(player_url())
        page.wait_for_selector("#tabGuitar", timeout=8000)
        page.locator("#tabGuitar").click()
        page.wait_for_timeout(1000)
        expect(page.locator(".guitar-layout")).to_be_visible()
        expect(page.locator("#guitarTimeline")).to_be_visible()
        expect(page.locator("#guitarFretboard")).to_be_visible()
        expect(page.locator("#guitarTeachInfo")).to_be_visible()

    def test_guitar_fretboard_canvas(self, page: Page):
        """GT-02: 指板 canvas 存在"""
        page.goto(player_url())
        page.wait_for_selector("#tabGuitar", timeout=8000)
        page.locator("#tabGuitar").click()
        page.wait_for_timeout(1500)
        expect(page.locator("#guitarFretboardCanvas")).to_be_visible()

    def test_guitar_chord_name_display(self, page: Page):
        """GT-03: 和弦名稱顯示"""
        page.goto(player_url())
        page.wait_for_selector("#tabGuitar", timeout=8000)
        page.locator("#tabGuitar").click()
        page.wait_for_timeout(5000)
        name = page.locator("#gtChordName").text_content()
        assert name and name != "—", f"Expected chord name, got '{name}'"

    def test_guitar_timeline_items(self, page: Page):
        """GT-04: 和弦時間軸有項目"""
        page.goto(player_url())
        page.wait_for_selector("#tabGuitar", timeout=8000)
        page.locator("#tabGuitar").click()
        page.wait_for_timeout(5000)
        items = page.locator(".gt-timeline-item")
        assert items.count() > 0, "Timeline should have chord items"

    def test_guitar_fingering_guide(self, page: Page):
        """GT-05: 指法說明區塊存在"""
        page.goto(player_url())
        page.wait_for_selector("#tabGuitar", timeout=8000)
        page.locator("#tabGuitar").click()
        page.wait_for_timeout(2000)
        expect(page.locator("#gtFingeringGuide")).to_be_visible()

    def test_guitar_chord_in_key(self, page: Page):
        """GT-06: 調性分析區塊存在"""
        page.goto(player_url())
        page.wait_for_selector("#tabGuitar", timeout=8000)
        page.locator("#tabGuitar").click()
        page.wait_for_timeout(2000)
        expect(page.locator("#gtChordInKey")).to_be_visible()


class TestAPI:
    """新 API 端點"""

    def test_voicings_api(self, page: Page):
        """API-01: /api/chord/voicings/guitar/C 回傳多把位"""
        resp = page.request.get(f"{BASE}/api/chord/voicings/guitar/C")
        assert resp.ok
        data = resp.json()
        assert "voicings" in data
        assert len(data["voicings"]) >= 1
        # 第一個 voicing 應有 fingers
        v = data["voicings"][0]
        assert "fingers" in v
        assert "strings" in v

    def test_analysis_api(self, page: Page):
        """API-02: /api/chord/analysis/C/Am 回傳級數"""
        resp = page.request.get(f"{BASE}/api/chord/analysis/C/Am")
        assert resp.ok
        data = resp.json()
        assert data["roman"] == "vi"
        assert data["degree"] == 6
        assert data["type_name"] == "Minor"

    def test_analysis_borrowed_chord(self, page: Page):
        """API-03: 借用和弦分析 (♭VII)"""
        resp = page.request.get(f"{BASE}/api/chord/analysis/C/Bb")
        assert resp.ok
        data = resp.json()
        assert "♭" in data["roman"]
        assert "VII" in data["roman"]

    def test_existing_diagram_still_works(self, page: Page):
        """API-04: 舊端點 /api/chord/diagram/guitar/C 仍正常"""
        resp = page.request.get(f"{BASE}/api/chord/diagram/guitar/C")
        assert resp.ok
        data = resp.json()
        assert "strings" in data
        assert "fingers" in data

    def test_chord_info_still_works(self, page: Page):
        """API-05: 舊端點 /api/chord/info/C 仍正常"""
        resp = page.request.get(f"{BASE}/api/chord/info/C")
        assert resp.ok
        data = resp.json()
        assert "notes" in data
        assert "type_name" in data
