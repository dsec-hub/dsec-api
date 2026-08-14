"""Object storage for image media — Cloudflare R2 or Supabase Storage.

The backend is chosen by ``settings.STORAGE_BACKEND`` ("r2" | "supabase"). Both
implement the same three operations and raise the same exception types, so the
router and the recompress backfill are unaware of which one is live.

**Object paths are identical across backends.** `media_asset` stores the path
(`webp_path` / `png_path`) separately from the public URL (`webp_url` /
`png_url`), so migrating means copying objects under the same keys and rewriting
only the URL prefix. That is what makes the move reversible: the objects on the
other side are never mutated, so flipping the variable back and re-pointing the
URLs restores the previous state exactly.

Clients are created lazily and cached — they hold no per-request state.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from app.config import settings

_logger = logging.getLogger("dsec")


class StorageError(RuntimeError):
    """Base for storage problems the router surfaces as a 503 with a message."""


class StorageNotConfigured(StorageError):
    """Raised when the active backend's credentials are missing — 503."""


class StorageUnavailable(StorageError):
    """Raised when the storage backend rejects a request (missing bucket, auth,
    network, …). Carries the backend message so the caller sees *why*, instead
    of an opaque 500."""


def _backend() -> str:
    return (settings.STORAGE_BACKEND or "supabase").strip().lower()


# --------------------------------------------------------------------------- #
# Cloudflare R2 (S3-compatible)
# --------------------------------------------------------------------------- #

@lru_cache
def _r2_client():
    missing = [
        name
        for name, val in (
            ("R2_ACCOUNT_ID", settings.R2_ACCOUNT_ID),
            ("R2_ACCESS_KEY_ID", settings.R2_ACCESS_KEY_ID),
            ("R2_SECRET_ACCESS_KEY", settings.R2_SECRET_ACCESS_KEY),
            # Without this the app would write unreachable URLs into the DB and
            # only fail later, in a browser, as a broken image.
            ("R2_PUBLIC_BASE_URL", settings.R2_PUBLIC_BASE_URL),
        )
        if not val
    ]
    if missing:
        raise StorageNotConfigured(
            "Cloudflare R2 is not configured (missing: " + ", ".join(missing) + ")."
        )
    # Imported here so boto3 is only needed when R2 is actually the backend.
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        # R2 ignores regions but SigV4 requires one; "auto" is what Cloudflare
        # documents. s3v4 is mandatory — R2 does not accept the older signature.
        region_name="auto",
        config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
    )


def _r2_public_url(path: str) -> str:
    return f"{settings.R2_PUBLIC_BASE_URL.rstrip('/')}/{path.lstrip('/')}"


def _r2_upload(path: str, data: bytes, content_type: str) -> str:
    try:
        _r2_client().put_object(
            Bucket=settings.R2_BUCKET,
            Key=path,
            Body=data,
            ContentType=content_type,
            # Object paths are UUID-unique per upload, so a one-year immutable
            # cache is safe and keeps repeat views off the origin entirely.
            CacheControl="public, max-age=31536000, immutable",
        )
    except StorageError:
        raise
    except Exception as exc:  # botocore ClientError, endpoint errors, …
        raise StorageUnavailable(
            f"image storage upload failed (R2 bucket {settings.R2_BUCKET!r}): {exc}"
        ) from exc
    return _r2_public_url(path)


def _r2_download(path: str) -> bytes:
    try:
        return _r2_client().get_object(Bucket=settings.R2_BUCKET, Key=path)["Body"].read()
    except StorageError:
        raise
    except Exception as exc:
        raise StorageUnavailable(f"image download failed (path {path!r}): {exc}") from exc


def _r2_delete(paths: list[str]) -> None:
    # delete_objects caps at 1000 keys per call; chunk so a large cleanup cannot
    # silently drop the tail.
    client = _r2_client()
    for i in range(0, len(paths), 1000):
        client.delete_objects(
            Bucket=settings.R2_BUCKET,
            Delete={"Objects": [{"Key": p} for p in paths[i:i + 1000]], "Quiet": True},
        )


# --------------------------------------------------------------------------- #
# Supabase Storage
# --------------------------------------------------------------------------- #

@lru_cache
def _supabase_client():
    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_ROLE_KEY:
        raise StorageNotConfigured(
            "Supabase Storage is not configured "
            "(set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY)."
        )
    # Imported here so the dependency is only required when storage is used.
    from supabase import create_client

    return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)


def _bucket():
    return _supabase_client().storage.from_(settings.SUPABASE_STORAGE_BUCKET)


def _supabase_upload(path: str, data: bytes, content_type: str) -> str:
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


def _supabase_download(path: str) -> bytes:
    try:
        return _bucket().download(path)
    except StorageError:
        raise
    except Exception as exc:  # storage3 StorageApiError, httpx errors, …
        raise StorageUnavailable(f"image download failed (path {path!r}): {exc}") from exc


def _supabase_delete(paths: list[str]) -> None:
    _bucket().remove(paths)


# --------------------------------------------------------------------------- #
# Public interface — unchanged across backends
# --------------------------------------------------------------------------- #

def upload_object(path: str, data: bytes, content_type: str) -> str:
    """Upload bytes to `path` and return the public URL.

    Overwrites cleanly if the path already exists, so re-processing is safe. A
    backend failure raises ``StorageUnavailable`` so the router returns a 503
    with a useful message instead of a bare 500.
    """
    if _backend() == "r2":
        return _r2_upload(path, data, content_type)
    return _supabase_upload(path, data, content_type)


def download_object(path: str) -> bytes:
    """Fetch the raw bytes of an object. Raises ``StorageUnavailable`` on a
    backend error (missing object, auth, network) — used by the recompress
    backfill, which reads the stored PNG back as its source."""
    if _backend() == "r2":
        return _r2_download(path)
    return _supabase_download(path)


def delete_objects(paths: list[str]) -> None:
    """Remove objects. Best-effort: a missing object or a transient backend error
    is logged, never raised — cleanup must not block deleting the owning DB row."""
    clean = [p for p in paths if p]
    if not clean:
        return
    try:
        if _backend() == "r2":
            _r2_delete(clean)
        else:
            _supabase_delete(clean)
    except Exception as exc:  # noqa: BLE001 — best-effort cleanup
        _logger.warning("storage cleanup failed for %s: %s", clean, exc)
