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


engine = create_engine(settings.DATABASE_URL, **_engine_kwargs())
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    """Create all tables if they don't yet exist.

    Imported lazily so every model is registered on `Base.metadata` before
    `create_all` runs. Safe to call on every startup (idempotent).
    """
    from app import models  # noqa: F401  (ensure models are imported/registered)

    Base.metadata.create_all(bind=engine)


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a scoped DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
