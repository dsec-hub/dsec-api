"""Apply database migrations (Alembic ``upgrade head``).

Usage (from dsec-api/):  .venv/bin/python scripts/migrate.py

Intended as the deploy/release step so app cold-starts don't have to run
migrations themselves (set RUN_MIGRATIONS_ON_STARTUP=false in that case).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.db import run_migrations  # noqa: E402


def main() -> None:
    target = settings.DATABASE_URL.split("@")[-1]
    print(f"Applying migrations (upgrade head) to: {target}")
    run_migrations()
    print("Done — database is at head.")


if __name__ == "__main__":
    main()
