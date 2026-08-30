"""Build the service-continuity export bundle.

The bundle is a *manifest*, not a data dump: it describes the deployment so the
next committee can stand up a fresh Vercel + Neon without manual archaeology. It
deliberately contains NO secret values and NO row-level PII —

  * env vars are listed by NAME only (never their value), with a ``sensitive``
    flag so the next team knows which ones must be set to a real secret;
  * API keys are listed WITHOUT their hash and without the (unrecoverable) raw
    key — just the accountability metadata;
  * the database is described by SCHEMA + per-table ROW COUNTS, not contents.

A genuine data snapshot (student PII) is out of scope for an HTTP endpoint on
purpose — that belongs to a `pg_dump` run by the owner with DB credentials.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import Base
from app.models import APIKey

# Substrings that mark an env var as holding a secret the next team must supply a
# real value for. Matched case-insensitively against the var NAME (never a value).
_SENSITIVE_MARKERS = ("SECRET", "PASS", "TOKEN", "KEY", "DATABASE_URL", "DSN", "WEBHOOK_URL")


def _is_sensitive(name: str) -> bool:
    upper = name.upper()
    # ``ANTHROPIC_MODEL`` / ``API_KEY_PREFIX`` name a config value, not a secret.
    if upper in {"ANTHROPIC_MODEL", "API_KEY_PREFIX"}:
        return False
    return any(marker in upper for marker in _SENSITIVE_MARKERS)


def _env_var_manifest() -> list[dict]:
    """Every settings field, by NAME, flagged sensitive-or-not. No values."""
    return [
        {"name": name, "sensitive": _is_sensitive(name)}
        for name in sorted(Settings.model_fields.keys())
    ]


def _schema_snapshot() -> list[dict]:
    """Declared tables and columns, from the ORM metadata (not the live DB)."""
    tables = []
    for table in Base.metadata.sorted_tables:
        tables.append({
            "table": table.name,
            "columns": [
                {
                    "name": col.name,
                    "type": str(col.type),
                    "nullable": bool(col.nullable),
                    "primary_key": bool(col.primary_key),
                }
                for col in table.columns
            ],
        })
    return tables


def _row_counts(db: Session) -> dict[str, int | None]:
    """Per-table row counts (None if a table is absent / unreadable).

    Each probe rolls back on failure so a missing table (partial DB) doesn't
    poison the session for the next probe. Only DB errors are swallowed — a
    programming bug or interrupt still propagates.
    """
    counts: dict[str, int | None] = {}
    for table in Base.metadata.sorted_tables:
        try:
            counts[table.name] = db.execute(
                select(func.count()).select_from(table)
            ).scalar_one()
        except SQLAlchemyError:  # table absent / not migrated — record as unknown
            db.rollback()
            counts[table.name] = None
    return counts


def _alembic_revision(db: Session) -> str | list[str] | None:
    """Current migration head. A list (not a bare string) if — abnormally —
    Alembic reports multiple heads, so a divergent chain is visible, not hidden."""
    try:
        revs = db.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
    except SQLAlchemyError:
        db.rollback()
        return None
    if not revs:
        return None
    return revs[0] if len(revs) == 1 else sorted(revs)


def _api_key_manifest(db: Session) -> list[dict]:
    """API keys as accountability metadata for handover.

    Projects ONLY safe columns in SQL, so the argon2 ``key_hash`` is never even
    loaded into memory (defence in depth beyond just not serialising it). The
    free-form ``created_by`` label is deliberately omitted — it can carry an
    ``appuser:N`` id or an email (PII) and isn't needed to re-provision keys.
    ``name`` is a committee-authored label (same field already shown by
    GET /admin/keys); the raw key is unrecoverable and never stored.
    """
    rows = db.execute(
        select(
            APIKey.id,
            APIKey.name,
            APIKey.prefix,
            APIKey.scopes,
            APIKey.created_at,
            APIKey.last_used_at,
            APIKey.revoked,
        ).order_by(APIKey.created_at.desc())
    ).all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "prefix": r.prefix,          # display fragment only; not the secret
            "scopes": list(r.scopes or []),
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "last_used_at": r.last_used_at.isoformat() if r.last_used_at else None,
            "revoked": bool(r.revoked),
        }
        for r in rows
    ]


def build_export_bundle(db: Session) -> dict:
    """Assemble the secret-free, PII-free continuity manifest."""
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "alembic_revision": _alembic_revision(db),
        "schema": _schema_snapshot(),
        "row_counts": _row_counts(db),
        "env_vars": _env_var_manifest(),
        "api_keys": _api_key_manifest(db),
    }
