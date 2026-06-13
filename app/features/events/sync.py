"""Notion -> Neon event sync — the single source of truth.

`sync_notion_events()` is the ONE implementation invoked by all three triggers
(Notion webhook, Vercel Cron, manual admin endpoint). Never duplicate this logic.

Data flow: Notion (committee edits) -> sync -> Neon `Event` table -> dsec.club
reads Neon directly. FastAPI owns ingest/writes; it is not in the site's read path.

The actual Notion API fetch is stubbed for v1 — the contract, upsert, soft-delete,
and logging are fully built so wiring the Notion client in v2 is a drop-in.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core import logging as event_logging
from app.models import Event

_logger = logging.getLogger("dsec.events")


@dataclass
class NotionEvent:
    """Normalised event row fetched from Notion (pre-upsert shape)."""

    notion_page_id: str
    title: str
    description: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    location: str | None = None
    status: str = "draft"
    tags: list | None = None
    updated_at: datetime | None = None


def _fetch_notion_events() -> list[NotionEvent]:
    """Fetch + normalise rows from the Notion events database.

    STUB for v1. In v2 this queries the Notion API using `NOTION_API_KEY` and
    `NOTION_EVENTS_DATABASE_ID`, paginates, and maps Notion properties onto
    `NotionEvent`. Returns an empty list until configured so the sync is a no-op
    rather than an error.
    """
    if not (settings.NOTION_API_KEY and settings.NOTION_EVENTS_DATABASE_ID):
        _logger.info("notion sync skipped: NOTION_API_KEY / DATABASE_ID not configured")
        return []
    # TODO(v2): real Notion query + property normalisation.
    return []


def _upsert_event(db: Session, ne: NotionEvent) -> None:
    row = db.execute(
        select(Event).where(Event.notion_page_id == ne.notion_page_id)
    ).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if row is None:
        row = Event(notion_page_id=ne.notion_page_id)
        db.add(row)
    row.title = ne.title
    row.description = ne.description
    row.starts_at = ne.starts_at
    row.ends_at = ne.ends_at
    row.location = ne.location
    row.status = ne.status
    row.tags = ne.tags
    row.updated_at = ne.updated_at
    row.synced_at = now
    row.deleted = False


def sync_notion_events(db: Session, *, trigger: str = "manual") -> dict:
    """Run the full reconciliation sync. Returns a summary dict.

    Steps: fetch Notion rows -> upsert each into Neon -> soft-delete any Neon row
    whose Notion page is gone -> log to EventLog.

    `trigger` is one of "webhook" / "cron" / "manual" for the audit trail.
    """
    fetched = _fetch_notion_events()
    seen_ids = {e.notion_page_id for e in fetched}

    for ne in fetched:
        _upsert_event(db, ne)

    # Soft-delete events that disappeared from Notion.
    deleted = 0
    if seen_ids:
        existing = db.execute(
            select(Event).where(Event.deleted.is_(False))
        ).scalars().all()
        for row in existing:
            if row.notion_page_id not in seen_ids:
                row.deleted = True
                row.synced_at = datetime.now(timezone.utc)
                deleted += 1

    db.commit()

    summary = {"trigger": trigger, "upserted": len(fetched), "soft_deleted": deleted}
    event_logging.log_event(
        db,
        source="notion",
        action="sync",
        external_id=None,
        subject="event sync",
        output=str(summary),
        payload=summary,
    )
    _logger.info("notion sync complete: %s", summary)
    return summary
