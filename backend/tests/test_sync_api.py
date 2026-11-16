"""/api/sync: the tablet (主控) drives the PC (喇叭) over the LAN.

The PC plays the sound by extrapolating the tablet's playhead from the
server's timestamps, so the server must stamp every post with its own
clock, keep users' rooms apart, and push a late-joining follower the latest
state straight away (or the PC would sit silent until the next change).
"""

import asyncio
import json
import sys
import time
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi import HTTPException  # noqa: E402

import sync_api  # noqa: E402


def _state(t=12.5, paused=False):
    return {"song": {"path": "@1/a.flac", "hash": None}, "t": t, "paused": paused,
            "rate": 1, "latency_ms": 80, "settings": {"style": "Block"}}


class _Req:
    def __init__(self, disconnect_after):
        self.calls = 0
        self.limit = disconnect_after

    async def is_disconnected(self):
        self.calls += 1
        return self.calls > self.limit


async def _first_events(user, n, disconnect_after=3):
    resp = await sync_api.sync_stream(_Req(disconnect_after), user=user)
    out = []
    async for chunk in resp.body_iterator:
        text = chunk.decode() if isinstance(chunk, bytes) else chunk
        out.append(json.loads(text.removeprefix("data: ").strip()))
        if len(out) >= n:
            break
    return out


class TestSyncState(unittest.TestCase):
    def setUp(self):
        sync_api._rooms.clear()

    def test_post_is_stamped_with_server_clock_for_extrapolation(self):
        before = time.time()
        sync_api.sync_post_state(_state(), user="u")
        got = sync_api.sync_get_state(user="u")
        self.assertEqual(got["state"]["t"], 12.5)
        self.assertGreaterEqual(got["state"]["server_ts"], before)
        self.assertGreaterEqual(got["server_now"], got["state"]["server_ts"])

    def test_rooms_are_per_user(self):
        sync_api.sync_post_state(_state(t=1), user="a")
        sync_api.sync_post_state(_state(t=2), user="b")
        self.assertEqual(sync_api.sync_get_state(user="a")["state"]["t"], 1)
        self.assertEqual(sync_api.sync_get_state(user="b")["state"]["t"], 2)

    def test_every_post_bumps_the_version(self):
        v1 = sync_api.sync_post_state(_state(), user="u")["version"]
        v2 = sync_api.sync_post_state(_state(), user="u")["version"]
        self.assertEqual(v2, v1 + 1)

    def test_oversized_state_is_refused(self):
        with self.assertRaises(HTTPException) as cm:
            sync_api.sync_post_state({"junk": "x" * 10000}, user="u")
        self.assertEqual(cm.exception.status_code, 413)


class TestSyncStream(unittest.TestCase):
    def setUp(self):
        sync_api._rooms.clear()

    def test_late_follower_gets_the_current_state_first(self):
        sync_api.sync_post_state(_state(t=42.0), user="u")
        ev = asyncio.run(_first_events("u", 1))[0]
        self.assertEqual(ev["type"], "state")
        self.assertEqual(ev["state"]["t"], 42.0)
        self.assertGreaterEqual(ev["server_now"], ev["state"]["server_ts"])

    def test_new_post_is_pushed(self):
        async def run():
            task = asyncio.create_task(_first_events("u", 2, disconnect_after=100))
            await asyncio.sleep(0.02)
            sync_api.post_state("u", _state(t=7.0, paused=True))
            return await asyncio.wait_for(task, 2)
        events = asyncio.run(run())
        self.assertIsNone(events[0]["state"])
        self.assertTrue(events[1]["state"]["paused"])
        self.assertEqual(events[1]["state"]["t"], 7.0)


if __name__ == "__main__":
    unittest.main()
