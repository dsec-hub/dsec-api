"""Public API request/response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class DraftRequest(BaseModel):
    subject: str = ""
    from_: str = Field(default="", alias="from")
    body: str = Field(..., max_length=50_000)

    model_config = {"populate_by_name": True}


class DraftResponse(BaseModel):
    action: str
    draftBody: str | None = None


class NotifyRequest(BaseModel):
    """A message to relay to the committee's Discord channel."""

    message: str = Field(..., min_length=1, max_length=2000)
    # Optional label shown as the webhook's display name (e.g. "DSEC Bot").
    username: str | None = Field(default=None, max_length=80)


class NotifyResponse(BaseModel):
    delivered: bool


class LogEntry(BaseModel):
    id: int
    created_at: datetime
    source: str
    sender: str | None = None
    subject: str | None = None
    classification: str | None = None
    action: str | None = None
    output: str | None = None
    tokens: int | None = None
    cost: float | None = None

    model_config = {"from_attributes": True}


class StatusResponse(BaseModel):
    status: str
    log_count: int
    llm_calls_today: int
    global_daily_cap: int
