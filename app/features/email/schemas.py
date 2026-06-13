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


class EmailResponse(BaseModel):
    action: str  # "draft" | "ignore"
    draftBody: str | None = None
