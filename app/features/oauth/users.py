"""Authenticate a human DSEC user during the OAuth /authorize flow, and map that
user's dashboard role onto the coarse API/MCP scopes.

The credential store is the shared ``app_user`` table (the same one dsec-hub's
NextAuth signs in against): passwords are bcrypt hashes written by Node
(``bcryptjs``, cost 12). We verify them with the Python ``bcrypt`` package — the
hash format is interoperable. The role → scope mapping mirrors dsec-hub's
``allowedScopesFor`` (lib/api-tokens.ts).

The richer RBAC (``app_role.modules`` / ``write_modules`` via ``app_user.role_id``)
was added to Neon OUTSIDE dsec-api's Alembic chain, so those columns/tables may
be ABSENT in some environments (notably the SQLite test DB). Every read of them
is therefore wrapped defensively and falls back to the legacy ``role`` varchar.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import AppUser

_logger = logging.getLogger("dsec.oauth")


def verify_password(password: str, password_hash: str | None) -> bool:
    """Constant-time-ish bcrypt verify. Fails closed if bcrypt is unavailable or
    the stored hash is malformed, so a broken hash can never grant access."""
    if not password or not password_hash:
        return False
    try:
        import bcrypt  # lazy: an OAuth-only dependency shouldn't block API boot
    except ImportError:  # pragma: no cover — bcrypt is a declared dependency
        _logger.error("bcrypt not installed; OAuth password login disabled")
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def authenticate(db: Session, email: str, password: str) -> AppUser | None:
    """Return the active ``app_user`` for valid credentials, else None.

    Email is matched case-insensitively. Inactive accounts (``is_active=False``)
    are refused exactly like dsec-hub's sign-in. The same generic failure is
    returned for unknown-email and wrong-password so callers can't enumerate."""
    email = (email or "").strip().lower()
    if not email or not password:
        return None
    row = db.execute(
        select(AppUser).where(func.lower(AppUser.email) == email)
    ).scalar_one_or_none()
    if row is None or not row.is_active:
        return None
    if not verify_password(password, row.password_hash):
        return None
    return row


def allowed_scopes_for(db: Session, user: AppUser) -> set[str]:
    """The scopes this user MAY grant, bounded by their dashboard role.

    Mirrors dsec-hub ``allowedScopesFor``:
      - read:    any module visible
      - write:   any module editable
      - trigger: can edit Meetings (the AI surface)
      - ingest:  admin only
    Admins get all four. Falls back to the legacy ``role`` varchar when the
    ``app_role`` RBAC tables aren't present.
    """
    modules, write_modules = _role_perms(db, user)
    if modules is not None:
        admin = "admin" in modules
        out: set[str] = set()
        if admin or modules:
            out.add("read")
        if admin or write_modules:
            out.add("write")
        if admin or ("meetings" in modules and "meetings" in write_modules):
            out.add("trigger")
        if admin:
            out.add("ingest")
        return out
    # Fallback: the legacy single-value role column (dsec-api's model always has
    # it; default "exec"). Conservative — unknown roles get read-only.
    role = (user.role or "").strip().lower()
    if role == "admin":
        return {"read", "write", "trigger", "ingest"}
    if role == "exec":
        return {"read", "write", "trigger"}
    return {"read"}


def _role_perms(db: Session, user: AppUser) -> tuple[list[str] | None, list[str] | None]:
    """(modules, write_modules) from ``app_role`` via ``app_user.role_id``.

    Returns (None, None) — signalling "use the fallback" — when the RBAC tables
    or columns don't exist (raises a DB error we swallow) or the user has no
    linked role row.
    """
    try:
        r = db.execute(
            text(
                "SELECT ar.modules AS modules, ar.write_modules AS write_modules "
                "FROM app_user au JOIN app_role ar ON au.role_id = ar.id "
                "WHERE au.id = :uid"
            ),
            {"uid": user.id},
        ).mappings().first()
    except SQLAlchemyError:
        db.rollback()  # the failed statement aborts the transaction; clear it
        return None, None
    if not r:
        return None, None
    return _as_list(r["modules"]), _as_list(r["write_modules"])


def _as_list(value: object) -> list[str]:
    """Coerce a JSON/JSONB column into a list of strings. Postgres hands back a
    Python list; a stray text encoding is parsed; anything else → []."""
    import json

    if value is None:
        return []
    if isinstance(value, list):
        return [str(x) for x in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return [str(x) for x in parsed] if isinstance(parsed, list) else []
        except (ValueError, TypeError):
            return []
    return []
