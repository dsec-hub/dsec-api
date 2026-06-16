"""Supabase Storage wrapper for image media.

Thin adapter over the official `supabase` Python SDK. The service-role client is
created lazily and cached (one per warm function instance) — it holds no
per-request state, so it is safe under Vercel Fluid Compute. All objects live in
a single public bucket (`settings.SUPABASE_STORAGE_BUCKET`).
"""

from __future__ import annotations

import logging
from functools import lru_cache

from app.config import settings

_logger = logging.getLogger("dsec")


class StorageError(RuntimeError):
    """Base for storage problems the router surfaces as a 503 with a message."""


class StorageNotConfigured(StorageError):
    """Raised when Supabase credentials are missing — surfaced as a 503."""


class StorageUnavailable(StorageError):
    """Raised when the storage backend rejects a request (missing bucket, auth,
    network, …). Carries the backend message so the caller sees *why*, instead
    of an opaque 500."""


@lru_cache
def _client():
    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_ROLE_KEY:
        raise StorageNotConfigured(
            "Supabase Storage is not configured "
            "(set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY)."
        )
    # Imported here so the dependency is only required when storage is used.
    from supabase import create_client

    return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)


def _bucket():
    return _client().storage.from_(settings.SUPABASE_STORAGE_BUCKET)


def upload_object(path: str, data: bytes, content_type: str) -> str:
    """Upload bytes to `path` in the bucket and return the public URL.

    `upsert=true` so re-processing the same path overwrites cleanly. A long
    cache-control is safe because object paths are UUID-unique per upload.

    A backend failure (e.g. the bucket doesn't exist) raises ``StorageUnavailable``
    so the router can return a 503 with a useful message instead of a bare 500.
    """
    try:
        _bucket().upload(
            path=path,
            file=data,
            file_options={
                "content-type": content_type,
                "cache-control": "31536000",
                "upsert": "true",
            },
        )
        # get_public_url may append a trailing "?" on some SDK versions — strip it.
        return _bucket().get_public_url(path).rstrip("?")
    except StorageError:
        raise  # not-configured etc. — already typed, let it through
    except Exception as exc:  # storage3 StorageApiError, httpx errors, …
        bucket = settings.SUPABASE_STORAGE_BUCKET
        raise StorageUnavailable(
            f"image storage upload failed (bucket {bucket!r}): {exc}"
        ) from exc


def download_object(path: str) -> bytes:
    """Fetch the raw bytes of an object. Raises ``StorageUnavailable`` on a
    backend error (missing object, auth, network) — used by the recompress
    backfill, which reads the stored PNG back as its source."""
    try:
        return _bucket().download(path)
    except StorageError:
        raise
    except Exception as exc:  # storage3 StorageApiError, httpx errors, …
        raise StorageUnavailable(
            f"image download failed (path {path!r}): {exc}"
        ) from exc


def delete_objects(paths: list[str]) -> None:
    """Remove objects from the bucket. Best-effort: a missing object or a
    transient backend error is logged, never raised — cleanup must not block
    deleting the owning DB row."""
    clean = [p for p in paths if p]
    if not clean:
        return
    try:
        _bucket().remove(clean)
    except Exception as exc:  # noqa: BLE001 — best-effort cleanup
        _logger.warning("storage cleanup failed for %s: %s", clean, exc)
