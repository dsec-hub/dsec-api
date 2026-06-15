"""Report which tables exist in the target database (intended for Neon).

READ-ONLY: connects, inspects the schema, and prints a summary. It never creates
or drops anything — to CREATE the schema, run ``scripts/migrate.py`` instead.

Usage (from dsec-api/), with the pooled Neon URL:
    DATABASE_URL="postgresql+psycopg://USER:PASS@ep-xxx-pooler.REGION.aws.neon.tech/DB?sslmode=require" \\
        .venv/bin/python scripts/check_neon.py

Exit codes: 0 = all expected tables present, 1 = some missing, 2 = cannot connect.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, inspect, text  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402

from app.config import settings  # noqa: E402

# The tables the application currently defines (see app/models.py).
EXPECTED_TABLES = [
    "event_log",
    "api_key",
    "rate_limit",
    "people",
    "events",
    "sponsors",
    "finance",
    "app_user",
    # DUSA weekly imports (ingested from email)
    "dusa_import",
    "members",
    "member_report",
    "finance_report",
    "finance_transaction",
    # Workspace (tasks, projects, meetings, documents)
    "project",
    "task_board",
    "task",
    "meeting",
    "document",
    # Usage / activity log
    "usage_event",
]


def main() -> int:
    url = settings.DATABASE_URL
    print(f"Target database: {url.split('@')[-1]}")

    if url.startswith("sqlite"):
        print(
            "NOTE: DATABASE_URL points at SQLite, not Neon. Set the pooled Neon "
            "URL (with sslmode=require) to check the real production schema."
        )
    elif "sslmode" not in url:
        print("WARNING: Postgres URL has no sslmode=require — Neon expects TLS.")

    try:
        engine = create_engine(url, pool_pre_ping=True)
        insp = inspect(engine)
        existing = set(insp.get_table_names())
    except SQLAlchemyError as exc:
        print(f"\nERROR: could not connect / inspect the database:\n  {exc}")
        return 2

    print("\nExpected application tables:")
    missing: list[str] = []
    for table in EXPECTED_TABLES:
        if table in existing:
            n_cols = len(insp.get_columns(table))
            print(f"  [x] {table}  ({n_cols} columns)")
        else:
            missing.append(table)
            print(f"  [ ] {table}  -- MISSING")

    if "alembic_version" in existing:
        try:
            with engine.connect() as conn:
                rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            print(f"\nAlembic revision: {rev}")
        except SQLAlchemyError:
            print("\nAlembic revision: (alembic_version present but unreadable)")
    else:
        print("\nAlembic revision: (none — migrations have not been applied)")

    other = sorted(existing - set(EXPECTED_TABLES) - {"alembic_version"})
    if other:
        print(f"\nOther tables present: {', '.join(other)}")

    if missing:
        print(f"\nResult: {len(missing)} expected table(s) MISSING: {', '.join(missing)}")
        print("Apply the schema with:  .venv/bin/python scripts/migrate.py")
        return 1

    print("\nResult: all expected tables present — schema is applied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
