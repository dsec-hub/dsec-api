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
