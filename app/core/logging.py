"""Central EventLog writer with token/cost tracking.

Every feature logs through `log_event` so the dashboard sees all activity in one
place. Logging never raises into the caller — an audit-trail failure must not
break a request pipeline.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models import EventLog

_logger = logging.getLogger("dsec.events")


def log_event(
    db: Session,
    *,
    source: str,
    action: str,
    external_id: str | None = None,
    sender: str | None = None,
    subject: str | None = None,
    classification: str | None = None,
    payload: dict | None = None,
    output: str | None = None,
    tokens: int | None = None,
    cost: float | None = None,
) -> EventLog | None:
    """Persist one EventLog row. Returns the row, or None on failure (never raises)."""
    entry = EventLog(
        source=source,
        action=action,
        external_id=external_id,
        sender=sender,
        subject=subject,
        classification=classification,
        payload=payload,
        output=output,
        tokens=tokens,
        cost=cost,
    )
    try:
        db.add(entry)
        db.commit()
        db.refresh(entry)
        return entry
    except Exception:  # pragma: no cover - defensive; logging must not break callers
        db.rollback()
        _logger.exception("failed to write EventLog (source=%s action=%s)", source, action)
        return None
