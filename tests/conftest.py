"""Playwright 測試共用設定"""

import pytest

BASE_URL = "http://localhost:8800"

# 測試用已知 track
TEST_TRACK = "Jazz/Bob James/Bob James - Jazz Hands/Beerbohm.flac"
TEST_TRACK_ENCODED = "Jazz%2FBob%20James%2FBob%20James%20-%20Jazz%20Hands%2FBeerbohm.flac"


@pytest.fixture(scope="session")
def base_url():
    return BASE_URL
