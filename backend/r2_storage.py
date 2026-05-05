"""
Cloudflare R2 cover-art storage (Phase F).

Public-mode opt-in. Set LIVECHORD_USE_R2=1 plus the four R2 env vars and
process_queue's cover-write path uploads to R2 instead of local disk;
process_api's cover-serve route streams back through FastAPI from R2.

Personal/beta deployments — and the NUC today — leave the env flag off, in
which case `is_r2_enabled()` returns False and the rest of LiveChord runs
the original local-disk path with no behavior change.

Usage from `chord_detect`-style callers — same pattern as modal_btc:

    from r2_storage import is_r2_enabled, upload_cover, download_cover
    if is_r2_enabled():
        upload_cover(result_hash, cover_data)
    else:
        cover_path.write_bytes(cover_data)

The boto3 dep is optional (declared in requirements.txt as cloud-only).
A missing boto3 plus LIVECHORD_USE_R2=1 raises a clear error at first
call rather than at module import.
"""

from __future__ import annotations

import io
import os
import threading
from typing import Optional


_BUCKET_DEFAULT = "livechord-covers"
_CACHE_HEADER = "public, max-age=31536000, immutable"  # 1 year


# ---------------------------------------------------------------------------
# Env-driven config + boto3 client (lazy, cached)
# ---------------------------------------------------------------------------

_client_lock = threading.Lock()
_client = None
_bucket: Optional[str] = None


def is_r2_enabled() -> bool:
    """LIVECHORD_USE_R2 in {1,true,yes,on}."""
    val = (os.environ.get("LIVECHORD_USE_R2") or "").strip().lower()
    return val in ("1", "true", "yes", "on")


def _required(name: str) -> str:
    val = (os.environ.get(name) or "").strip()
    if not val:
        raise RuntimeError(
            f"R2 env var {name} is unset. See doc/R2_DEPLOY.md for the four "
            "required vars (R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, "
            "R2_SECRET_ACCESS_KEY, R2_BUCKET_COVERS)."
        )
    return val


def _ensure_client():
    """Build the boto3 S3 client on first call, reuse afterward."""
    global _client, _bucket
    if _client is not None:
        return _client, _bucket

    with _client_lock:
        if _client is not None:
            return _client, _bucket

        try:
            import boto3  # type: ignore
            from botocore.config import Config  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "boto3 is not installed; pip install boto3 to enable "
                "LIVECHORD_USE_R2. Module import was deferred so personal/"
                "beta deployments without boto3 still run."
            ) from e

        account_id = _required("R2_ACCOUNT_ID")
        access_key = _required("R2_ACCESS_KEY_ID")
        secret_key = _required("R2_SECRET_ACCESS_KEY")
        bucket = (os.environ.get("R2_BUCKET_COVERS") or _BUCKET_DEFAULT).strip()

        endpoint = f"https://{account_id}.r2.cloudflarestorage.com"

        _client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            # R2 ignores region but boto3 requires one. "auto" is the convention
            # in Cloudflare's R2 docs.
            region_name="auto",
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                # 5 s connect, 30 s read — covers are ~50 KB, no reason to wait.
                connect_timeout=5,
                read_timeout=30,
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )
        _bucket = bucket
        return _client, _bucket


# ---------------------------------------------------------------------------
# Public API — upload / download / delete
# ---------------------------------------------------------------------------


def _key_for(hash_: str) -> str:
    """One cover per song hash, jpg only — matches the local disk layout."""
    return f"{hash_}.jpg"


def upload_cover(hash_: str, data: bytes, content_type: str = "image/jpeg") -> None:
    """Upload `data` as the cover for `hash_`. Overwrites any existing cover."""
    client, bucket = _ensure_client()
    client.put_object(
        Bucket=bucket,
        Key=_key_for(hash_),
        Body=data,
        ContentType=content_type,
        CacheControl=_CACHE_HEADER,
    )


def download_cover(hash_: str) -> Optional[bytes]:
    """Return the cover bytes, or None if missing. Used by the FastAPI serve
    route to proxy R2 reads through the VPS — keeps URLs stable as
    /covers/<hash>.jpg without requiring a custom domain or signed URLs."""
    client, bucket = _ensure_client()
    try:
        resp = client.get_object(Bucket=bucket, Key=_key_for(hash_))
        return resp["Body"].read()
    except client.exceptions.NoSuchKey:
        return None
    except Exception as e:
        # Treat all other errors as "missing" so the route returns 404 cleanly
        # rather than 500. Log once for visibility.
        print(f"[r2] download_cover({hash_}) failed: {e}", flush=True)
        return None


def delete_cover(hash_: str) -> None:
    """Delete the cover for `hash_`. Safe to call on missing keys."""
    client, bucket = _ensure_client()
    try:
        client.delete_object(Bucket=bucket, Key=_key_for(hash_))
    except Exception as e:
        print(f"[r2] delete_cover({hash_}) failed: {e}", flush=True)


def cover_exists(hash_: str) -> bool:
    """HEAD probe — used by the serve route to send a quick 404 without the
    body roundtrip. Cheap on R2 (no egress charge for HEAD)."""
    client, bucket = _ensure_client()
    try:
        client.head_object(Bucket=bucket, Key=_key_for(hash_))
        return True
    except Exception:
        return False
