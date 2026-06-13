"""Server-rendered audit dashboard. GET /dashboard, basic-auth protected.

Shows recent EventLog rows across every integration (email/discord/calcom/notion)
in one place, with simple filters by source and action. Plain HTML — it's an audit
trail, not a product.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_basic_auth
from app.db import get_db
from app.models import EventLog

router = APIRouter()

_templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
# Disable Jinja's template LRU cache — sidesteps a cache-key bug on newer Python
# builds and is fine for a low-traffic audit page.
_templates.env.cache = None


@router.get("/")
def dashboard(
    request: Request,
    source: str | None = Query(default=None),
    action: str | None = Query(default=None),
    db: Session = Depends(get_db),
    _: str = Depends(require_basic_auth),
):
    stmt = select(EventLog).order_by(EventLog.created_at.desc())
    if source:
        stmt = stmt.where(EventLog.source == source)
    if action:
        stmt = stmt.where(EventLog.action == action)
    rows = db.execute(stmt.limit(200)).scalars().all()
    return _templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "rows": rows,
            "source": source or "",
            "action": action or "",
            "sources": ["email", "discord", "calcom", "notion"],
            "actions": ["draft", "ignore", "received", "sync"],
        },
    )
