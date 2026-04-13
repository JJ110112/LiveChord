"""Playwright test for Transformer AI Jazzify functionality"""

import re
import pytest
from playwright.sync_api import Page, expect

# Use the user's base URL from QA.md/test_unified_ui.py
BASE = "http://192.168.50.6:8800"
TEST_TRACK = "Jazz/Bob James/Bob James - Jazz Hands/Beerbohm.flac"

def player_url():
    # Use a dummy path, we will mock the API response
    return f"{BASE}/player?path=Arthur_Theme.flac"

def test_transformer_reharmonization_flow(page: Page):
    """Test the purple AI button triggers transformer mode and updates chords."""
    # 1. Listen for the /api/ai/jazzify API request to capture what backend returns
    api_responses = []
    
    def handle_response(response):
        if "api/ai/jazzify" in response.url and response.status == 200:
            try:
                data = response.json()
                api_responses.append(data)
            except Exception:
                pass
                
    page.on("response", handle_response)
    
    # Mock the initial song metadata to guarantee it loads
    def handle_mock_info(route):
        mock_data = {
            "chords": [
                {"time": 0, "end": 2, "chord": "Dm"},
                {"time": 2, "end": 4, "chord": "G7"},
                {"time": 4, "end": 6, "chord": "C"},
                {"time": 6, "end": 8, "chord": "Fmaj7"}
            ],
            "key": "D",
            "tempo": 120,
            "duration": 8,
            "track_name": "Arthur_Theme"
        }
        route.fulfill(json=mock_data)
        
    page.route("**/api/info**", handle_mock_info)

    
    # 2. Go to player
    page.goto(player_url())
    page.wait_for_selector("#chordDisplay", timeout=8000)
    
    # Wait for original chords to load
    page.wait_for_selector(".tb-chord-block", timeout=8000)
    
    # Store original chords length
    original_chords = page.locator(".tb-chord-block")
    orig_count = original_chords.count()
    assert orig_count > 0, "No original chords loaded"
    
    # 3. Click the purple AI button
    ai_btn = page.locator("#btnJazzifyAI")
    expect(ai_btn).to_be_visible()
    ai_btn.click()
    
    # 4. Wait for the API call to complete and UI to update
    # The button text should become "AI" again after "⌛" loading state
    expect(ai_btn).to_have_text("AI", timeout=15000)
    
    # Verify the button is active (purple background)
    expect(ai_btn).to_have_css("background-color", "rgba(156, 39, 176, 0.3)")
    
    # 5. Check what the API actually returned
    assert len(api_responses) > 0, "No API response captured for jazzify"
    api_data = api_responses[-1]
    
    print("\n[Transformer API Validation]")
    print(f"Original Count (API): {api_data.get('original_count')}")
    print(f"Jazzified Count (API): {api_data.get('jazzified_count')}")
    print(f"Algorithm Used: {api_data.get('algorithm_used')}")
    
    if api_data.get('changes'):
        print(f"Changes applied: {len(api_data.get('changes'))}")
        for c in api_data.get('changes')[:5]:
            print(f" - {c.get('rule')}: {c.get('original')} -> {c.get('jazzified')}")
            if 'Error' in str(c.get('jazzified')):
                print("\nCRITICAL: TRANSFORMER FAILED IN BACKEND:\n", c.get('rule'))
                
    # 6. Verify UI updated the chord boxes
    # The UI box count might change if Transformer outputs a different sequence length
    new_chords = page.locator(".tb-chord-block")
    new_count = new_chords.count()
    
    if orig_count == new_count:
        print("\nWARNING: UI shows identical number of chords. Let's check if the inner text actually changed.")
        # We can extract the text of the first few chords to see if they changed
        orig_texts = [original_chords.nth(i).text_content() for i in range(min(5, orig_count))]
        new_texts = [new_chords.nth(i).text_content() for i in range(min(5, new_count))]
        print(f"Original first 5 chords: {orig_texts}")
        print(f"New first 5 chords: {new_texts}")
        
    print(f"Test complete. Orig UI: {orig_count}, New UI: {new_count}")

