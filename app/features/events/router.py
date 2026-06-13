"""Events admin router — manual sync trigger lives here.

The public GET /public/events read route lives in the public feature (section 8c).
This router exposes the internal "run the sync now" endpoint, mounted under
/admin so it shares the admin basic-auth guard.
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import require_basic_auth
from app.config import settings
from app.db import get_db
from app.features.events.sync import sync_notion_events

router = APIRouter()


@router.post("/sync/notion")
def trigger_notion_sync(
    db: Session = Depends(get_db),
    _: str = Depends(require_basic_auth),
) -> dict:
    """Manual "push it now" sync (section 8c, trigger #3). Admin basic-auth."""
    return sync_notion_events(db, trigger="manual")


@router.get("/sync/notion/cron")
def cron_notion_sync(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
) -> dict:
    """Vercel Cron reconciliation sync (section 8c, trigger #2).

    Vercel Cron sends ``Authorization: Bearer <CRON_SECRET>``. We validate that
    rather than basic-auth so the scheduled hit needs no user credentials.
    """
    expected = f"Bearer {settings.CRON_SECRET}"
    if not settings.CRON_SECRET or not authorization or not hmac.compare_digest(
        authorization, expected
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid cron secret"
        )
    return sync_notion_events(db, trigger="cron")
