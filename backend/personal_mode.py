"""Depth-defense guard: endpoints that only exist on the personal instance.

These endpoints (auto worker controls, library scanning, full backup admin)
are LAN-self-use only and should not be reachable on either the beta instance
or the public cloud instance. Attach as a FastAPI dependency to reject 404
on any non-personal mode, regardless of front-end state or browser cache.
"""

from fastapi import HTTPException

from config import is_personal_mode


def require_personal_mode():
    if not is_personal_mode():
        raise HTTPException(status_code=404, detail="Not available on this instance")
