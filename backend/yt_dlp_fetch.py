"""
Three-tier yt-dlp invocation wrapper for VPS deployments where the host IP
is flagged by YouTube (Hetzner ranges hit 403/SABR walls regularly through
2026).

Tiers, attempted in order:

  1. Direct subprocess call from the VPS IP. Fastest, free. Many videos
     still serve fine even from flagged IPs — never preemptively skip.
  2. Same call with `--cookies <COOKIES_PATH>` if the file exists. Cookies
     are dumped weekly from a low-value Google account browser session
     (see doc/OPS.md). ~80% effective against tier-1 failures.
  3. (stub) Modal serverless yt-dlp from a CPU container with a cleaner
     outbound IP. Mirrors backend/modal_btc.py shape. Tracked in
     LiveChord-5gg (Phase H). Stub raises so the wrapper degrades to
     "tier 1+2 only" until the Modal app is deployed.

Personal mode (NUC, residential ISP) stays effectively tier-1-only because
the cookies file usually isn't present and the wrapper is a no-op when
tier 1 succeeds. Public mode (VPS) fills in tiers 2/3 for resilience.

Callers receive a `(CompletedProcess, fallback_tier)` tuple and can log
or persist `fallback_tier` for monitoring (see doc/OPS.md §Monitoring).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Sequence

logger = logging.getLogger("livechord.yt_dlp_fetch")


# ---------------------------------------------------------------------------
# Binary resolution (lifted from process_queue._find_ytdlp so import order
# doesn't matter — process_queue imports this module, not the other way)
# ---------------------------------------------------------------------------

def _find_ytdlp_bin() -> str:
    found = shutil.which("yt-dlp")
    if found:
        return found
    fallback = Path.home() / "AppData/Local/Python/pythoncore-3.14-64/Scripts/yt-dlp.exe"
    if fallback.is_file():
        return str(fallback)
    raise FileNotFoundError("yt-dlp not found on PATH or in known locations")


YTDLP_BIN = _find_ytdlp_bin()


# ---------------------------------------------------------------------------
# Cookies path resolution
# ---------------------------------------------------------------------------

def _cookies_path() -> Optional[str]:
    """Return cookies.txt path if the file exists, else None.

    Order of search:
      1. $LIVECHORD_YTDLP_COOKIES (explicit override)
      2. ~/.config/yt-dlp/cookies.txt (Linux/VPS canonical)
      3. backend/data/yt-dlp-cookies.txt (Windows-friendly fallback)
    """
    override = os.environ.get("LIVECHORD_YTDLP_COOKIES")
    if override and Path(override).is_file():
        return override
    canonical = Path.home() / ".config" / "yt-dlp" / "cookies.txt"
    if canonical.is_file():
        return str(canonical)
    backend_local = Path(__file__).resolve().parent / "data" / "yt-dlp-cookies.txt"
    if backend_local.is_file():
        return str(backend_local)
    return None


# ---------------------------------------------------------------------------
# IP-block heuristic
# ---------------------------------------------------------------------------

# Substrings that commonly appear in yt-dlp stderr when YouTube refuses based
# on IP/SABR signals. Match is case-insensitive. Errors NOT in this list
# (e.g. "Video unavailable", "Private video") are genuine bad URLs and tier 2
# won't help — fail fast instead of wasting a cookie attempt.
_IP_BLOCK_SIGNATURES = (
    "http error 403",
    "http error 429",
    "sign in to confirm",
    "sabr",
    "this content isn",  # "this content isn't available in your country" etc
    "blocked",
    "rate-limit",
    "rate limit",
    "too many requests",
)


def _looks_like_ip_block(stderr: str) -> bool:
    if not stderr:
        return False
    low = stderr.lower()
    return any(sig in low for sig in _IP_BLOCK_SIGNATURES)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_yt_dlp(
    args: Sequence[str],
    *,
    timeout: int = 180,
    encoding: str = "utf-8",
    errors: str = "replace",
    text: Optional[bool] = None,
) -> tuple[subprocess.CompletedProcess, int]:
    """Run yt-dlp with three-tier fallback.

    ``args`` is the argv suffix after the yt-dlp binary itself
    (e.g. ``["--dump-json", "--no-download", url]``). Do NOT include
    YTDLP_BIN; this function prepends it.

    Returns ``(CompletedProcess, fallback_tier)`` where ``fallback_tier`` is
    1, 2, or 3 indicating which tier produced the result. On total failure
    the last tier's CompletedProcess is returned with its non-zero
    returncode — caller decides whether to raise.

    ``encoding="utf-8"`` is the default because Windows NUC consoles default
    to cp950 and silently drop CJK from yt-dlp metadata. Pass ``text=True``
    explicitly only if you don't want the encoding kwargs (rare; subprocess
    will then return bytes).
    """
    base = [YTDLP_BIN, *args]
    run_kwargs: dict = {"capture_output": True, "timeout": timeout}
    if text is None:
        run_kwargs.update({"encoding": encoding, "errors": errors})
    else:
        run_kwargs["text"] = text

    # Tier 1: direct
    result = subprocess.run(base, **run_kwargs)
    if result.returncode == 0:
        return result, 1

    stderr = (result.stderr or "")
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", errors="replace")

    # Tier 2: cookies, only if (a) a cookies file exists AND (b) the failure
    # signature looks IP-block-ish. Genuine bad URLs ("Video unavailable")
    # won't be helped by cookies and we'd just waste a round-trip.
    cookies = _cookies_path()
    if cookies and _looks_like_ip_block(stderr):
        logger.info("yt_dlp_fetch tier1 failed (IP-block signature); retrying with cookies")
        with_cookies = [YTDLP_BIN, "--cookies", cookies, *args]
        result2 = subprocess.run(with_cookies, **run_kwargs)
        if result2.returncode == 0:
            return result2, 2
        # Fall through to tier 3 with the cookies-attempt result so caller
        # sees the most informative stderr.
        result = result2
        stderr = (result.stderr or "")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")

    # Tier 3: Modal — Phase H, see LiveChord-5gg. Until that ships we surface
    # the tier-2 (or tier-1) failure to the caller. Logging the gap so the
    # missing-Modal hits are countable in journalctl.
    if os.environ.get("LIVECHORD_USE_MODAL_YTDL") == "1":
        try:
            from modal_ytdl import run_via_modal  # type: ignore  # Phase H module
        except ImportError:
            logger.warning(
                "yt_dlp_fetch tier3 requested (LIVECHORD_USE_MODAL_YTDL=1) "
                "but backend/modal_ytdl.py not found; staying on tier-2 result"
            )
        else:
            try:
                cp = run_via_modal(args, timeout=timeout)
                if cp.returncode == 0:
                    return cp, 3
                # tier 3 itself failed → return its result
                return cp, 3
            except Exception as e:
                logger.error("yt_dlp_fetch tier3 Modal call raised: %s", e)

    # All available tiers exhausted; return the last attempt for the caller
    # to handle. fallback_tier reflects how far we made it.
    return result, (2 if cookies and _looks_like_ip_block(stderr) else 1)
