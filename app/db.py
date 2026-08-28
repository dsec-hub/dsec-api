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
    # Postgres / Neon — rely on pre-ping to survive idle-compute suspends.
    #
    # Pool sizing is env-driven (see config.DB_POOL_SIZE). The old hard-coded
    # 5 + 2 was correct for serverless, where every one of N concurrent function
    # instances held its OWN pool and the danger was exhausting Neon's connection
    # limit. A single long-lived process inverts the tradeoff: Starlette runs the
    # ~190 sync endpoints on a 40-thread pool, all of which queue on these
    # connections, so 5 becomes the throughput ceiling rather than a safety net.
    return {
        "pool_pre_ping": True,
        "pool_size": settings.DB_POOL_SIZE,
        "max_overflow": settings.DB_MAX_OVERFLOW,
        "pool_recycle": settings.DB_POOL_RECYCLE,
    }


engine = create_engine(_db_url(), **_engine_kwargs())
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def run_migrations() -> None:
    """Bring the database schema up to date.

    **Postgres (Neon — production):** apply Alembic migrations up to ``head``.
    The schema is only ever created/evolved through versioned migrations.
    Idempotent — a DB already at head is a fast no-op.

    **SQLite (local dev only):** build the schema directly from the ORM models
    and stamp Alembic at ``head`` instead of replaying the migration chain.

    The migrations are hand-written against Postgres, and 23 of them use
    operations SQLite cannot perform — ``ALTER COLUMN ... SET DEFAULT`` (which
    SQLite has no syntax for) and ``DROP``/``ADD CONSTRAINT`` (which SQLAlchemy
    rejects outright: *"No support for ALTER of constraints in SQLite dialect"*).
    Replaying them against SQLite raises on startup and takes the app down, which
    broke the zero-external-services local start the README promises.

    Guarding each offending revision individually was the alternative; it was
    rejected because it leaves a trap for every future migration author, who has
    no reason to think about a dialect the migration will never run against.
    Deriving the dev schema from the models is equivalent for local work — the
    models are the same source the migrations were generated from — and cannot
    drift out of support. Production behaviour is completely unchanged.
    """
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parent.parent  # dsec-api/
    cfg = Config(str(root / "alembic.ini"))

    if engine.dialect.name == "sqlite":
        import app.models  # noqa: F401  — register every model on Base.metadata

        Base.metadata.create_all(engine)
        command.stamp(cfg, "head")
        return

    command.upgrade(cfg, "head")


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
