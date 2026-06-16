"""Recompress already-stored media down to the current byte budgets.

Older uploads were stored at full resolution with no size target, so some PNGs
are multiple MB. This walks the ``media_asset`` table, re-downloads each stored
download copy (the highest-fidelity copy we keep), runs it back through the
Pillow pipeline — which now squeezes WebP under ``MEDIA_WEBP_MAX_BYTES`` and the
download under ``MEDIA_DOWNLOAD_MAX_BYTES`` — and overwrites both derivatives.
Photo downloads switch from PNG to JPEG (the old .png object is removed); the
WebP path is stable, so its public URL keeps working.

Usage (from dsec-api/):
    .venv/bin/python scripts/recompress_media.py              # dry run (no writes)
    .venv/bin/python scripts/recompress_media.py --commit     # actually re-upload
    .venv/bin/python scripts/recompress_media.py --commit --limit 20
    .venv/bin/python scripts/recompress_media.py --force      # include already-small ones
    .venv/bin/python scripts/recompress_media.py --include-archived

By default only assets that currently breach a budget are touched, so re-running
is cheap and safe. ``--commit`` overwrites the stored objects — the original
upload is not retained, so the PNG becomes the source of truth.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.features.media import processing, storage  # noqa: E402
from app.models import MediaAsset  # noqa: E402


def _kb(n: int | None) -> str:
    return "?" if n is None else f"{n / 1000:.0f} KB"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--commit", action="store_true",
                   help="re-upload the recompressed objects (default: dry run)")
    p.add_argument("--limit", type=int, default=None,
                   help="process at most N assets")
    p.add_argument("--force", action="store_true",
                   help="recompress even assets already under budget")
    p.add_argument("--include-archived", action="store_true",
                   help="also process archived rows")
    p.add_argument("--entity-type", default=None,
                   help="restrict to one entity_type (event/project/sponsor/…)")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    webp_cap = settings.MEDIA_WEBP_MAX_BYTES
    dl_cap = settings.MEDIA_DOWNLOAD_MAX_BYTES

    mode = "COMMIT — overwriting storage" if args.commit else "DRY RUN — no writes"
    print(f"Recompress media [{mode}]")
    print(f"  budgets: webp <= {_kb(webp_cap)}, download <= {_kb(dl_cap)}\n")

    session = SessionLocal()
    try:
        stmt = select(MediaAsset).order_by(MediaAsset.id)
        if not args.include_archived:
            stmt = stmt.where(MediaAsset.archived.is_(False))
        if args.entity_type:
            stmt = stmt.where(MediaAsset.entity_type == args.entity_type)
        assets = list(session.execute(stmt).scalars().all())

        processed = skipped = failed = 0
        old_total = new_total = 0

        for asset in assets:
            if args.limit is not None and processed >= args.limit:
                break
            tag = f"#{asset.id} {asset.entity_type}/{asset.entity_id} {asset.role}"

            try:
                source = storage.download_object(asset.png_path)
            except storage.StorageError as exc:
                print(f"  ! {tag}: download failed — {exc}")
                failed += 1
                continue

            cur_dl = len(source)  # current download (.png/.jpg) size
            cur_webp = asset.size_bytes  # stored webp size (may be None on old rows)
            oversized = cur_dl > dl_cap or (cur_webp or 0) > webp_cap
            if not oversized and not args.force:
                skipped += 1
                continue

            try:
                result = processing.process_image(
                    source, keep_transparency=(asset.role == "logo")
                )
            except ValueError as exc:
                print(f"  ! {tag}: processing failed — {exc}")
                failed += 1
                continue

            new_dl = len(result.download_bytes)
            new_webp = len(result.webp_bytes)
            # The download format may change (.png → .jpg for photos); keep the
            # same uid, swap only the extension.
            base = asset.png_path.rsplit(".", 1)[0]
            new_dl_path = f"{base}.{result.download_ext}"
            old_total += cur_dl + (cur_webp or 0)
            new_total += new_dl + new_webp
            processed += 1

            print(
                f"  • {tag}: "
                f"download {_kb(cur_dl)} → {_kb(new_dl)} (.{result.download_ext}), "
                f"webp {_kb(cur_webp)} → {_kb(new_webp)} "
                f"({result.width}×{result.height})"
            )

            if args.commit:
                storage.upload_object(asset.webp_path, result.webp_bytes, "image/webp")
                new_dl_url = storage.upload_object(
                    new_dl_path, result.download_bytes, result.download_content_type
                )
                if new_dl_path != asset.png_path:
                    storage.delete_objects([asset.png_path])  # drop the orphaned .png
                    asset.png_path = new_dl_path
                    asset.png_url = new_dl_url
                asset.size_bytes = new_webp
                asset.width = result.width
                asset.height = result.height
                session.commit()

        print(
            f"\nDone. recompressed={processed} skipped(under budget)={skipped} "
            f"failed={failed}"
        )
        if processed:
            saved = old_total - new_total
            print(
                f"  bytes: {_kb(old_total)} → {_kb(new_total)} "
                f"(saved {_kb(saved)}, {saved / old_total * 100:.0f}%)"
            )
        if not args.commit and processed:
            print("  (dry run — re-run with --commit to apply)")
    finally:
        session.close()


if __name__ == "__main__":
    main()
