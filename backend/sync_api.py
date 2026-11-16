"""Cross-device playback sync (player Tools → 同步).

One device is the 主控 (leader): the full player, muted. Another is the
喇叭 (follower): the player on the PC that drives the speakers, following the
leader's song, playhead and sound settings.

The leader POSTs its playback state (song, playhead, paused, rate, sound
settings) on every change and once a second while playing. The server
stamps it with its own clock and pushes it to followers over Server-Sent
Events. Followers extrapolate the playhead from `server_now - server_ts`,
so a follower that connects late still lands on the right spot.

State is in memory, one room per user (LAN personal mode → one room).
Nothing is persisted: a restart just means the leader re-posts on its next
heartbeat.
"""

import asyncio
import json
import threading
import time

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from auth_api import get_current_user

router = APIRouter(prefix="/api/sync", tags=["sync"])

_MAX_STATE_BYTES = 8192
_POLL_S = 0.05      # how often a stream checks for a new state
_TICK_S = 2.0       # keep-alive so followers can tell a leader went silent

_lock = threading.Lock()
_rooms: dict[str, dict] = {}   # user -> {"state": dict | None, "version": int}


def _room(user: str) -> dict:
    return _rooms.setdefault(user, {"state": None, "version": 0})


def post_state(user: str, state: dict) -> int:
    with _lock:
        room = _room(user)
        room["state"] = {**state, "server_ts": time.time()}
        room["version"] += 1
        return room["version"]


def snapshot(user: str) -> tuple[int, dict | None]:
    with _lock:
        room = _room(user)
        return room["version"], room["state"]


def _event(state: dict | None, kind: str) -> str:
    payload = {"type": kind, "server_now": time.time(), "state": state}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/state")
def sync_post_state(state: dict = Body(...), user: str = Depends(get_current_user)):
    if len(json.dumps(state)) > _MAX_STATE_BYTES:
        raise HTTPException(status_code=413, detail="sync state too large")
    return {"version": post_state(user, state)}


@router.get("/state")
def sync_get_state(user: str = Depends(get_current_user)):
    version, state = snapshot(user)
    return {"version": version, "server_now": time.time(), "state": state}


@router.get("/stream")
async def sync_stream(request: Request, user: str = Depends(get_current_user)):
    async def gen():
        seen, state = snapshot(user)
        yield _event(state, "state")
        last_sent = time.monotonic()
        while not await request.is_disconnected():
            await asyncio.sleep(_POLL_S)
            version, state = snapshot(user)
            if version != seen:
                seen = version
                yield _event(state, "state")
                last_sent = time.monotonic()
            elif time.monotonic() - last_sent >= _TICK_S:
                yield _event(None, "tick")
                last_sent = time.monotonic()

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
