"""Schema + DB-level defaults validation on the SQLite fallback.

The Alembic migrations are Postgres-targeted (Neon) and are exercised against
the real database by the deploy/migrate workflow (`scripts/migrate.py`). These
tests run with no external services and validate that the models define a
coherent schema and that the database-level defaults (timestamps, flags) apply.
"""

from __future__ import annotations

from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from app.db import Base
from app.models import Person


def test_models_create_full_schema(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'schema.db'}")
    Base.metadata.create_all(engine)
    tables = set(inspect(engine).get_table_names())
    assert {
        "event_log",
        "api_key",
        "rate_limit",
        "people",
        "events",
        "sponsors",
        "finance",
        "app_user",
    } <= tables


def test_server_defaults_apply_without_orm(tmp_path):
    """A raw INSERT (bypassing SQLAlchemy's python-side defaults) still succeeds,
    proving the database itself supplies created_at / updated_at / archived."""
    engine = create_engine(f"sqlite:///{tmp_path / 'defaults.db'}")
    Base.metadata.create_all(engine)

    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO people (name) VALUES ('Raw Insert')")

    with Session(engine) as session:
        person = session.execute(select(Person)).scalar_one()
        assert person.created_at is not None
        assert person.updated_at is not None
        assert person.archived is False
