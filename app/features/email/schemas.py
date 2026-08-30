"""Request/response models for the email endpoint."""

from __future__ import annotations

from pydantic import BaseModel, Field


class EmailRequest(BaseModel):
    threadId: str
    messageId: str
    from_: str = Field(alias="from")
    to: str
    subject: str
    body: str
    date: str  # ISO8601 string

    model_config = {"populate_by_name": True}


class EmailDecision(BaseModel):
    """What the (optional) decision-maker stage proposed/did for this email.

    Present only when the decision-maker is enabled. Additive and ignorable — the
    Apps Script keys off ``action``/``draftBody`` and can disregard this.
    """

    action: str  # "update_dusa_status" | "none"
    applied: bool
    dryRun: bool
    flaggedForHuman: bool
    reason: str
    eventId: int | None = None
    eventName: str | None = None
    status: str | None = None
    confidence: float | None = None


class EmailResponse(BaseModel):
    action: str  # "draft" | "ignore"
    draftBody: str | None = None
    decision: EmailDecision | None = None
