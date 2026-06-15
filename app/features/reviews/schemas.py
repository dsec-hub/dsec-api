"""Pydantic models for the post-event review feature."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ReviewFormOut(BaseModel):
    """Status of an event's review form."""

    event_id: int
    configured: bool  # whether a Tally form exists for this event
    form_id: str | None = None
    form_url: str | None = None  # public fill link to share with attendees
    created_at: datetime | None = None
    # Live submission count from Tally; null if not fetched / Tally unavailable.
    response_count: int | None = None


class ReviewResponse(BaseModel):
    """One submission, mapped onto our known questions (any field may be absent)."""

    submitted_at: datetime | None = None
    rating: int | None = None
    enjoyed: str | None = None
    improve: str | None = None
    likelihood: int | None = None  # the "come to another event" linear scale
    comments: str | None = None


class ReviewResponsesOut(BaseModel):
    """A form's submissions plus a couple of headline stats."""

    event_id: int
    form_id: str | None = None
    form_url: str | None = None
    response_count: int = 0
    average_rating: float | None = None
    responses: list[ReviewResponse] = []
