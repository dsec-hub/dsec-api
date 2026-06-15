"""Mint an API key from the command line.

The raw key is printed exactly once — copy it immediately; only its hash is
stored. Useful for provisioning the Gmail forwarder (scope ``ingest``) or any
trusted internal tool.

Usage (from dsec-api/, with the target DATABASE_URL in .env or the environment):

    .venv/bin/python -m scripts.create_api_key --scopes ingest --label "gmail-forwarder"
    .venv/bin/python -m scripts.create_api_key --scopes read,ingest --label "ops"

Scopes: read, trigger, ingest (see app/core/apikeys.VALID_SCOPES).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.apikeys import VALID_SCOPES, generate_key  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import APIKey  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Create a DSEC API key.")
    ap.add_argument("--label", "--name", dest="label", required=True, help="human label for the key")
    ap.add_argument(
        "--scopes",
        default="read",
        help=f"comma-separated scopes (allowed: {', '.join(sorted(VALID_SCOPES))})",
    )
    ap.add_argument("--created-by", default="cli", help="who created it (audit)")
    args = ap.parse_args()

    scopes = [s.strip() for s in args.scopes.split(",") if s.strip()]
    invalid = set(scopes) - VALID_SCOPES
    if invalid:
        print(f"ERROR: invalid scope(s): {sorted(invalid)}; allowed: {sorted(VALID_SCOPES)}")
        return 1

    gen = generate_key()
    with SessionLocal() as db:
        row = APIKey(
            name=args.label,
            prefix=gen.prefix,
            key_hash=gen.key_hash,
            scopes=scopes,
            created_by=args.created_by,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        key_id = row.id

    print("API key created — copy the raw key now; it is not shown again.\n")
    print(f"  id:     {key_id}")
    print(f"  label:  {args.label}")
    print(f"  scopes: {', '.join(scopes)}")
    print(f"  prefix: {gen.prefix}")
    print(f"\n  RAW KEY: {gen.raw_key}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
