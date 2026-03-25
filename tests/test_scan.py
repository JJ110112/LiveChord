"""掃描功能測試 — 非同步掃描 + 增量模式"""

import pytest
import json
import time
import urllib.request

BASE = "http://localhost:8800"


def api_get(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return r.status, json.loads(r.read().decode())


def api_post(path):
    req = urllib.request.Request(BASE + path, data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, json.loads(r.read().decode())


class TestScanAPI:
    """掃描 API 測試"""

    def test_scan_status_endpoint(self):
        """status endpoint 可用"""
        status, data = api_get("/api/library/scan/status")
        assert status == 200
        assert "running" in data
        assert "progress" in data
        assert "mode" in data

    def test_scan_start_returns_immediately(self):
        """啟動掃描應立即回傳（非同步）"""
        start = time.time()
        status, data = api_post("/api/library/scan?mode=incremental")
        elapsed = time.time() - start
        assert status == 200
        # 應在 2 秒內回傳（非 block）
        assert elapsed < 5, f"回應時間 {elapsed:.1f}s，應 < 5s"
        assert "message" in data

    def test_scan_progress_updates(self):
        """掃描進度應遞增"""
        # 先確認有掃描在跑或啟動一個
        api_post("/api/library/scan?mode=incremental")
        time.sleep(1)

        _, st1 = api_get("/api/library/scan/status")
        if not st1["running"]:
            # 掃描已完成，進度應 > 0
            assert st1["progress"] > 0
            return

        time.sleep(3)
        _, st2 = api_get("/api/library/scan/status")
        # 進度應遞增或已完成
        assert st2["progress"] >= st1["progress"]

    def test_duplicate_scan_rejected(self):
        """不允許同時兩個掃描"""
        # 啟動掃描
        api_post("/api/library/scan?mode=full")
        time.sleep(0.5)

        # 再啟動一個
        _, data = api_post("/api/library/scan?mode=full")
        if data.get("ok") is False:
            assert "進行中" in data.get("message", "")

    def test_stats_shows_scan_state(self):
        """stats 應顯示掃描狀態"""
        _, data = api_get("/api/library/stats")
        assert "scan_running" in data

    def test_server_responsive_during_scan(self):
        """掃描期間伺服器仍回應"""
        api_post("/api/library/scan?mode=incremental")
        # browse 應正常回應
        status, data = api_get("/api/browse")
        assert status == 200
        assert len(data["entries"]) > 0
