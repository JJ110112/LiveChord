"""API 測試 — 後端 endpoint 驗證（使用 httpx 直接呼叫）"""

import pytest
import urllib.request
import urllib.parse
import json

BASE = "http://localhost:8800"


def api_get(path):
    url = BASE + path
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, None


def api_get_raw(path, headers=None):
    url = BASE + path
    req = urllib.request.Request(url)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, None, {}


def api_post(path, data):
    url = BASE + path
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, None


def api_delete(path):
    url = BASE + path
    req = urllib.request.Request(url, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, None


class TestBrowseAPI:
    def test_browse_root(self):
        """B-01: 瀏覽根目錄"""
        status, data = api_get("/api/browse")
        assert status == 200
        names = [e["name"] for e in data["entries"]]
        assert "Jazz" in names

    def test_browse_jazz(self):
        """B-02: 瀏覽 Jazz"""
        status, data = api_get("/api/browse?path=Jazz")
        assert status == 200
        assert len(data["entries"]) > 0

    def test_browse_to_tracks(self):
        """B-03: 瀏覽到 track"""
        status, data = api_get("/api/browse?path=" + urllib.parse.quote("Jazz/Bob James/Bob James - Jazz Hands"))
        assert status == 200
        flacs = [e for e in data["entries"] if not e["is_dir"]]
        assert any("Beerbohm" in f["name"] for f in flacs)

    def test_browse_nonexistent(self):
        """B-18: 不存在"""
        status, _ = api_get("/api/browse?path=NONEXISTENT_DIR")
        assert status == 404


class TestTrackAPI:
    TRACK = "Jazz/Bob James/Bob James - Jazz Hands/Beerbohm.flac"

    def test_track_info(self):
        """B-06: FLAC metadata"""
        status, data = api_get("/api/track/info?path=" + urllib.parse.quote(self.TRACK))
        assert status == 200
        assert data["title"] == "Beerbohm"
        assert data["artist"] == "Bob James"
        assert data["duration"] > 0

    def test_track_stream_range(self):
        """B-07: 串流 Range"""
        status, body, headers = api_get_raw(
            "/api/track/stream?path=" + urllib.parse.quote(self.TRACK),
            headers={"Range": "bytes=0-1023"})
        assert status == 206
        assert len(body) == 1024

    def test_track_cover(self):
        """B-08: 封面"""
        status, body, headers = api_get_raw("/api/track/cover?path=" + urllib.parse.quote(self.TRACK))
        assert status == 200
        ct = headers.get("Content-Type", "") or headers.get("content-type", "")
        assert "image" in ct

    def test_track_info_nonexistent(self):
        """B-18: 不存在"""
        status, _ = api_get("/api/track/info?path=NOPE.flac")
        assert status in (400, 403, 404)

    def test_path_traversal(self):
        """B-17: 路徑穿越"""
        status, _ = api_get("/api/track/info?path=" + urllib.parse.quote("../../etc/passwd"))
        assert status in (400, 403, 404)


class TestChordAPI:
    def test_chord_info(self):
        """B-15: 和弦資訊"""
        status, data = api_get("/api/chord/info/Cmaj7")
        assert status == 200
        assert data["jianpu"] == "1 3 5 7"

    def test_chord_diagram_guitar(self):
        """B-16: 吉他和弦圖"""
        status, data = api_get("/api/chord/diagram/guitar/Am")
        assert status == 200
        assert data["numStrings"] == 6

    def test_chord_diagram_ukulele(self):
        status, data = api_get("/api/chord/diagram/ukulele/C")
        assert status == 200
        assert data["numStrings"] == 4

    def test_chord_unknown(self):
        status, _ = api_get("/api/chord/info/Xzz99")
        assert status == 404


class TestFavoritesAPI:
    TRACK = "Jazz/Bob James/Bob James - Jazz Hands/Beerbohm.flac"

    def test_add_get_remove_favorite(self):
        """B-11~B-13: 最愛 CRUD"""
        # 新增
        status, data = api_post("/api/favorites", {"path": self.TRACK})
        assert status == 200 and data["ok"]
        # 取得
        status, data = api_get("/api/favorites")
        assert any(f["path"] == self.TRACK for f in data["favorites"])
        # 移除
        status, data = api_delete("/api/favorites?path=" + urllib.parse.quote(self.TRACK))
        assert status == 200
        # 確認移除
        status, data = api_get("/api/favorites")
        assert all(f["path"] != self.TRACK for f in data["favorites"])


class TestRecentAPI:
    def test_recent(self):
        """B-14: 最近播放"""
        status, data = api_get("/api/recent")
        assert status == 200
        assert "recent" in data


class TestSearchAPI:
    def test_empty_search(self):
        """B-05: 空搜尋"""
        status, data = api_get("/api/search?q=")
        assert status == 200
        assert data["results"] == []
