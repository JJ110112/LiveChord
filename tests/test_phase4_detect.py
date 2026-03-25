"""Phase 4 測試 — 音訊和弦自動偵測"""

import pytest
import urllib.request
import urllib.parse
import json
from playwright.sync_api import Page, expect

BASE = "http://localhost:8800"
# 使用一首較短的歌曲做偵測測試
TEST_TRACK = "Jazz/Bob James/Bob James - Jazz Hands/Beerbohm.flac"
# 使用一首沒有和弦譜的歌曲做前端偵測按鈕測試
DETECT_TRACK = "Jazz/Bob James/Bob James - Jazz Hands/Come Into My Dream.flac"


def api_post(path):
    url = BASE + path
    req = urllib.request.Request(url, data=b"", method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        return e.code, {"error": body}


class TestDetectAPI:
    """和弦偵測 API 測試"""

    def test_detect_chords(self):
        """偵測和弦 API 回傳結果"""
        track = urllib.parse.quote(DETECT_TRACK)
        status, data = api_post(f"/api/chords/detect?path={track}")
        assert status == 200, f"API 回傳 {status}: {data}"
        assert data["ok"] is True
        assert data["chord_count"] > 0
        assert len(data["chords"]) > 0
        assert "key" in data
        # 每個和弦應有 time, end, chord
        for c in data["chords"]:
            assert "time" in c
            assert "end" in c
            assert "chord" in c

    def test_detect_saves_to_file(self):
        """偵測後自動儲存，GET /api/chords 可取得"""
        track = urllib.parse.quote(DETECT_TRACK)
        # 先偵測
        status, _ = api_post(f"/api/chords/detect?path={track}")
        assert status == 200
        # 再取得
        url = BASE + f"/api/chords?path={track}"
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        assert data["exists"] is True
        assert len(data["chords"]) > 0

    def test_detect_key(self):
        """偵測調性"""
        track = urllib.parse.quote(DETECT_TRACK)
        status, data = api_post(f"/api/chords/detect?path={track}")
        assert status == 200
        key = data["key"]
        # key 應為有效的調性名稱
        assert len(key) >= 1
        assert key[0] in "CDEFGAB"

    def test_detect_nonexistent(self):
        """偵測不存在的檔案"""
        status, _ = api_post("/api/chords/detect?path=NOPE.flac")
        assert status in (403, 404)


class TestDetectUI:
    """前端偵測按鈕測試"""

    def test_detect_button_visible(self, page: Page):
        """偵測按鈕可見"""
        from urllib.parse import quote
        page.goto(f"{BASE}/player?path={quote(DETECT_TRACK, safe='')}")
        expect(page.locator("#btnDetect")).to_be_visible(timeout=5000)
        expect(page.locator("#btnDetect")).to_have_text("偵測")
