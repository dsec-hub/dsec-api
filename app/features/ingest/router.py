"""Ingestion API — receives the weekly DUSA workbooks from the Gmail forwarder.

`POST /ingest/dusa` (scope: ``ingest``) takes a multipart upload of one `.xlsx`
plus metadata, parses it server-side, and lands it in Neon. Idempotent on the
Gmail ``message_id`` (a re-send returns ``409``).

See ``integrations/dusa-gmail-forwarder`` for the Apps Script that calls this.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import logging as event_logging
from app.core.apikeys import require_api_key
from app.core.ratelimit import limiter
from app.db import get_db
from app.features.ingest import service
from app.features.ingest.schemas import (
    EmailCaptureRequest,
    EmailCaptureResponse,
    FinanceSummary,
    ImportLogEntry,
    IngestResponse,
    MembershipSummary,
)
from app.models import APIKey, DusaImport, EventLog

router = APIRouter()

# Generous upload ceiling — these workbooks are tens of KB; this just stops an
# accidental huge POST. (The basic-auth MAX_REQUEST_BYTES guard does not apply
# to API-key routes.)
_MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@router.post("/dusa", response_model=IngestResponse)
async def ingest_dusa(
    request: Request,
    report_type: str = Form(...),
    message_id: str = Form(...),
    file: UploadFile = File(...),
    received_at: str | None = Form(default=None),
    sender: str | None = Form(default=None),
    subject: str | None = Form(default=None),
    filename: str | None = Form(default=None),
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("ingest")),
) -> IngestResponse:
    limiter.check_request(db, key_id=key.id, ip=_client_ip(request))

    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty upload")
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "file too large")

    try:
        imp, rows, summary = service.handle_dusa_upload(
            db,
            report_type=report_type,
            message_id=message_id,
            data=data,
            filename=filename or file.filename,
            sender=sender,
            subject=subject,
            received_at=_parse_dt(received_at),
        )
    except service.DuplicateImport as dup:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"message {dup.existing.message_id} already ingested (import {dup.existing.id})",
        )
    except ValueError as bad:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(bad))
    except service.IngestError as err:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"could not parse {report_type} workbook: {err}",
        )

    return IngestResponse(
        status="ingested",
        report_type=report_type,
        message_id=message_id,
        import_id=imp.id,
        rows_ingested=rows,
        membership=summary if isinstance(summary, MembershipSummary) else None,
        finance=summary if isinstance(summary, FinanceSummary) else None,
    )


@router.post("/email", response_model=EmailCaptureResponse)
def capture_email(
    request: Request,
    req: EmailCaptureRequest,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("ingest")),
) -> EmailCaptureResponse:
    """Dumb capture: record one inbound email to the EventLog. No decisions.

    Deliberately does NOT run the spam-gate / classify / draft pipeline — that
    lives at ``/email/process`` and is layered on later. This endpoint only ever
    *records*, so we start capturing everything now with zero LLM spend.

    Idempotent on the Gmail ``message_id``: a re-send returns ``status="duplicate"``
    with a ``200`` (not a 409) so the Apps Script treats it as success and never
    retries.
    """
    limiter.check_request(db, key_id=key.id, ip=_client_ip(request))

    existing = db.execute(
        select(EventLog).where(
            EventLog.source == "email",
            EventLog.action == "captured",
            EventLog.external_id == req.message_id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return EmailCaptureResponse(
            status="duplicate", message_id=req.message_id, event_id=existing.id
        )

    entry = event_logging.log_event(
        db,
        source="email",
        action="captured",
        external_id=req.message_id,
        sender=req.from_,
        subject=req.subject,
        payload=req.model_dump(by_alias=True),
    )
    return EmailCaptureResponse(
        status="captured",
        message_id=req.message_id,
        event_id=entry.id if entry else None,
    )


@router.get("/imports", response_model=list[ImportLogEntry])
def list_imports(
    request: Request,
    limit: int = 20,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> list[ImportLogEntry]:
    limiter.check_request(db, key_id=key.id, ip=_client_ip(request))
    rows = db.execute(
        select(DusaImport).order_by(DusaImport.created_at.desc()).limit(min(limit, 100))
    ).scalars().all()
    return [
        ImportLogEntry(
            id=r.id,
            message_id=r.message_id,
            report_type=r.report_type,
            filename=r.filename,
            status=r.status,
            rows_ingested=r.rows_ingested,
            detail=r.detail,
            created_at=(r.created_at or datetime.now(timezone.utc)).isoformat(),
        )
        for r in rows
    ]
