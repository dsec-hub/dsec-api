#!/usr/bin/env python3
"""Copy media objects from Supabase Storage to Cloudflare R2, then repoint the DB.

Run from the repo root with the API's environment loaded (the container already
has it):

    docker compose exec -T api python deploy/migrate-media-to-r2.py --dry-run
    docker compose exec -T api python deploy/migrate-media-to-r2.py --copy
    docker compose exec -T api python deploy/migrate-media-to-r2.py --repoint

Three separate phases, on purpose:

  --dry-run   report what would move; touches nothing
  --copy      upload every object to R2 and verify each one byte-for-byte.
              Idempotent, and does NOT modify the database — so the site keeps
              serving from Supabase throughout and this can be run repeatedly
              until it reports zero failures.
  --repoint   rewrite media_asset.webp_url / png_url to the R2 public base.
              Refuses to run unless every object has been verified present in R2
              first, so the DB can never point at something that is not there.

Nothing is ever deleted from Supabase. Rolling back is `--repoint-back`, which
restores the Supabase URLs from the same paths. Delete the Supabase bucket only
once you are satisfied, and never in the same sitting.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import MediaAsset


def _r2():
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        region_name="auto",
        config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
    )


def _supabase_bucket():
    from supabase import create_client

    return create_client(
        settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY
    ).storage.from_(settings.SUPABASE_STORAGE_BUCKET)


def _content_type(path: str) -> str:
    ext = path.rsplit(".", 1)[-1].lower()
    return {
        "webp": "image/webp", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "png": "image/png", "gif": "image/gif",
    }.get(ext, "application/octet-stream")


def _rows(db):
    return list(db.execute(select(MediaAsset)).scalars())


def _paths(rows) -> list[str]:
    seen, out = set(), []
    for r in rows:
        for p in (r.webp_path, r.png_path):
            if p and p not in seen:
                seen.add(p)
                out.append(p)
    return out


def cmd_dry_run() -> int:
    db = SessionLocal()
    try:
        rows = _rows(db)
        paths = _paths(rows)
        print(f"media_asset rows : {len(rows)}")
        print(f"distinct objects : {len(paths)}")
        print(f"source           : supabase bucket {settings.SUPABASE_STORAGE_BUCKET!r}")
        print(f"destination      : r2 bucket {settings.R2_BUCKET!r}")
        print(f"public base      : {settings.R2_PUBLIC_BASE_URL or '(UNSET — --repoint will refuse)'}")
        already = sum(1 for r in rows if (r.webp_url or "").startswith(settings.R2_PUBLIC_BASE_URL or "\0"))
        print(f"rows already on R2: {already}")
    finally:
        db.close()
    return 0


def cmd_copy() -> int:
    db = SessionLocal()
    try:
        paths = _paths(_rows(db))
    finally:
        db.close()
    src, dst = _supabase_bucket(), _r2()
    ok = skipped = failed = 0
    for i, path in enumerate(paths, 1):
        try:
            # Already there and the right size? Leave it — makes this re-runnable.
            try:
                head = dst.head_object(Bucket=settings.R2_BUCKET, Key=path)
                data = src.download(path)
                if head["ContentLength"] == len(data):
                    skipped += 1
                    continue
            except Exception:
                data = src.download(path)

            dst.put_object(
                Bucket=settings.R2_BUCKET, Key=path, Body=data,
                ContentType=_content_type(path),
                CacheControl="public, max-age=31536000, immutable",
            )
            # Verify byte-for-byte rather than trusting the write.
            back = dst.get_object(Bucket=settings.R2_BUCKET, Key=path)["Body"].read()
            if back != data:
                print(f"  MISMATCH {path} (wrote {len(data)}, read back {len(back)})")
                failed += 1
                continue
            ok += 1
        except Exception as exc:  # noqa: BLE001 — report and continue
            print(f"  FAILED {path}: {exc}")
            failed += 1
        if i % 25 == 0:
            print(f"  … {i}/{len(paths)}")
    print(f"\ncopied {ok}, already-present {skipped}, failed {failed}, total {len(paths)}")
    print("database NOT modified — the site still serves from Supabase.")
    return 1 if failed else 0


def _verify_all_present() -> tuple[int, list[str]]:
    db = SessionLocal()
    try:
        paths = _paths(_rows(db))
    finally:
        db.close()
    dst = _r2()
    missing = []
    for p in paths:
        try:
            dst.head_object(Bucket=settings.R2_BUCKET, Key=p)
        except Exception:
            missing.append(p)
    return len(paths), missing


def cmd_repoint(back: bool = False) -> int:
    if not back and not settings.R2_PUBLIC_BASE_URL:
        print("R2_PUBLIC_BASE_URL is unset — refusing to write unreachable URLs.")
        return 2
    if not back:
        total, missing = _verify_all_present()
        if missing:
            print(f"{len(missing)}/{total} objects are NOT in R2 yet — run --copy first.")
            for m in missing[:10]:
                print("  missing:", m)
            return 2
        print(f"verified all {total} objects present in R2.")

    supa_base = f"{settings.SUPABASE_URL.rstrip('/')}/storage/v1/object/public/{settings.SUPABASE_STORAGE_BUCKET}"
    r2_base = settings.R2_PUBLIC_BASE_URL.rstrip("/")
    db = SessionLocal()
    changed = 0
    try:
        for r in _rows(db):
            new_base, path_base = (supa_base, r2_base) if back else (r2_base, supa_base)
            for attr, path_attr in (("webp_url", "webp_path"), ("png_url", "png_path")):
                path = getattr(r, path_attr)
                if not path:
                    continue
                want = f"{new_base}/{path.lstrip('/')}"
                if getattr(r, attr) != want:
                    setattr(r, attr, want)
                    changed += 1
        db.commit()
    finally:
        db.close()
    print(f"rewrote {changed} URL column(s) -> {'supabase' if back else 'r2'}")
    print("Set STORAGE_BACKEND accordingly and redeploy so NEW uploads go to the same place.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--copy", action="store_true")
    g.add_argument("--repoint", action="store_true")
    g.add_argument("--repoint-back", action="store_true")
    a = ap.parse_args()
    if a.dry_run:
        return cmd_dry_run()
    if a.copy:
        return cmd_copy()
    if a.repoint:
        return cmd_repoint()
    return cmd_repoint(back=True)


if __name__ == "__main__":
    sys.exit(main())
