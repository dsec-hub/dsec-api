"""Email feature router. POST /email/process — the Gmail Apps Script endpoint."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import require_agent_secret
from app.db import get_db
from app.features.email.pipeline import run_pipeline
from app.features.email.schemas import EmailRequest, EmailResponse

_logger = logging.getLogger("dsec.email")

router = APIRouter()


@router.post("/process", response_model=EmailResponse)
def process_email(
    req: EmailRequest,
    db: Session = Depends(get_db),
    _: None = Depends(require_agent_secret),
) -> EmailResponse:
    """Run spam gate -> classify -> draft. Never auto-sends; returns a draft only.

    Any unexpected failure is downgraded to ``{"action": "ignore"}`` so the Apps
    Script never sees a 500 (which it would just retry forever).
    """
    try:
        return run_pipeline(req, db)
    except Exception:  # pragma: no cover - last-resort safety net
        _logger.exception("unhandled email pipeline error for thread %s", req.threadId)
        return EmailResponse(action="ignore")
