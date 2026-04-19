"""Depth-defense guard: endpoints that must not be reachable on the beta instance.

Mirror of `feedback_api._require_beta` / `analytics_api._require_beta` — these
endpoints live only on the personal instance (8800). Attach as a FastAPI
dependency to reject 404 when LIVECHORD_MODE=beta, regardless of front-end
state or browser cache.
"""

from fastapi import HTTPException

from config import is_beta_mode


def require_personal_mode():
    if is_beta_mode():
        raise HTTPException(status_code=404, detail="Not available on this instance")
