"""ORM models shared across all features.

A single `EventLog` table is written by every integration (email, discord,
calcom, notion) so the dashboard shows all activity in one place. `APIKey`,
`RateLimit`, and `Event` support the public API, the Neon-backed limiter, and
the Notion->Neon event mirror respectively.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EventLog(Base):
    """Generic activity log usable by every feature, not just email."""

    __tablename__ = "event_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
    source: Mapped[str] = mapped_column(String(32), index=True)  # email/discord/...
    external_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    sender: Mapped[str | None] = mapped_column(String(512), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    classification: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost: Mapped[float | None] = mapped_column(Float, nullable=True)


class APIKey(Base):
    """A trusted-but-accountable API key. Raw key is never stored."""

    __tablename__ = "api_key"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    prefix: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    key_hash: Mapped[str] = mapped_column(String(512))
    scopes: Mapped[list] = mapped_column(JSON, default=list)  # ["read","trigger"]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    created_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class RateLimit(Base):
    """Fixed-window counter row backing the Neon rate limiter."""

    __tablename__ = "rate_limit"
    __table_args__ = (
        UniqueConstraint("key_id", "window_start", name="uq_ratelimit_key_window"),
        Index("ix_ratelimit_key_window", "key_id", "window_start"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key_id: Mapped[int | None] = mapped_column(
        ForeignKey("api_key.id"), nullable=True, index=True
    )
    # For per-IP limiting we reuse this table with a null key_id and an ip bucket.
    bucket: Mapped[str] = mapped_column(String(128), index=True, default="")
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    trigger_count_today: Mapped[int] = mapped_column(Integer, default=0)


class Event(Base):
    """Notion -> Neon event mirror that dsec.club reads directly."""

    __tablename__ = "event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    notion_page_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(512))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    starts_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    location: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    tags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
