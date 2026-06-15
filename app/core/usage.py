"""Tiny usage/audit logger. Never raises — logging must not break a request."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import UsageEvent

_logger = logging.getLogger("dsec.usage")


def log_usage(
    *,
    actor_type: str,
    source: str,
    action: str,
    actor_id: int | None = None,
    actor_label: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    path: str | None = None,
    detail: str | None = None,
    db: Session | None = None,
) -> None:
    own = db is None
    sess = db or SessionLocal()
    try:
        sess.add(UsageEvent(
            actor_type=actor_type, actor_id=actor_id, actor_label=actor_label,
            source=source, action=action, target_type=target_type, target_id=target_id,
            path=path, detail=detail,
        ))
        sess.commit()
    except Exception:  # noqa: BLE001 — usage logging is best-effort
        sess.rollback()
        _logger.debug("usage log failed", exc_info=True)
    finally:
        if own:
            sess.close()
