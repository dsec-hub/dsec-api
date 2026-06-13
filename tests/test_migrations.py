"""The baseline Alembic migration applies cleanly to a fresh database."""

from __future__ import annotations

from sqlalchemy import create_engine, inspect

import app.db as db_mod


def test_baseline_migration_creates_all_tables(tmp_path, monkeypatch):
    db_file = tmp_path / "migrated.db"
    url = f"sqlite:///{db_file}"
    # env.py reads settings.DATABASE_URL (the shared singleton) at migration time.
    monkeypatch.setattr(db_mod.settings, "DATABASE_URL", url)

    db_mod.run_migrations()

    tables = set(inspect(create_engine(url)).get_table_names())
    assert {"event_log", "api_key", "rate_limit", "event", "alembic_version"} <= tables
