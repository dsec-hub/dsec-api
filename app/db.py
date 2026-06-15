"""Database engine, session handling, and table initialisation.

Targets Neon (Postgres) via `DATABASE_URL`. Use Neon's **pooled** (pgBouncer)
connection string in production — serverless functions open many short
connections and would otherwise exhaust Neon's direct connection limit.

`pool_pre_ping=True` transparently reconnects when Neon suspends idle compute.
Models stay DB-agnostic so local dev can fall back to SQLite.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    """Declarative base shared by all ORM models."""


def _db_url() -> str:
    url = settings.DATABASE_URL
    # Neon and most Postgres hosts give a plain postgresql:// or postgres:// URL.
    # SQLAlchemy defaults to psycopg2 for those; we ship psycopg (v3), so rewrite
    # the scheme so SQLAlchemy picks the right driver.
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def _engine_kwargs() -> dict:
    url = settings.DATABASE_URL
    if url.startswith("sqlite"):
        # SQLite (local dev) — no connection pool tuning, allow cross-thread use.
        return {"connect_args": {"check_same_thread": False}}
    # Postgres / Neon — keep the pool small; rely on pre-ping for idle suspends.
    return {
        "pool_pre_ping": True,
        "pool_size": 5,
        "max_overflow": 2,
        "pool_recycle": 300,
    }


engine = create_engine(_db_url(), **_engine_kwargs())
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def run_migrations() -> None:
    """Apply Alembic migrations up to ``head``.

    Replaces the old ``create_all`` so the schema is only ever created/evolved
    through versioned migrations. Idempotent — a DB already at head is a fast
    no-op. Imports Alembic lazily so it's only required where migrations run.
    """
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parent.parent  # dsec-api/
    command.upgrade(Config(str(root / "alembic.ini")), "head")


def init_db() -> None:
    """Backwards-compatible alias — now applies migrations (not ``create_all``)."""
    run_migrations()


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a scoped DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
