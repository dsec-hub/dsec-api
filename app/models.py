"""ORM models.

Two groups of tables share the Neon database:

* **Operational** — `EventLog`, `APIKey`, `RateLimit`. Written/used by the
  FastAPI email-agent service (audit log, public API keys, rate limiter).
* **Club domain** — `Person`, `Event`, `Sponsor`, `FinanceEntry`. The single
  source of truth for the internal exec dashboard (`dsec-app`), which reads and
  writes these directly. `dsec-api` owns the schema (these models + Alembic);
  `dsec-app` introspects the live tables. There is no Notion in the loop.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# =============================================================================
# Operational tables (FastAPI email agent)
# =============================================================================


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


# =============================================================================
# Club-domain tables (exec dashboard — read/write source of truth)
# =============================================================================
#
# Field names follow the club's existing Notion schema. Every table carries
# `created_at`, `updated_at`, and an `archived` flag for soft-deletes (the app
# never hard-deletes a row). Relations are plain nullable FKs; the dashboard
# resolves them with joins.


class Person(Base):
    """A member/contact: exec, committee, general member, or external contact."""

    __tablename__ = "people"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    # Exec / Committee Lead / Committee Member / General Member / External Contact
    type: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    committee: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    role_title: Mapped[str | None] = mapped_column(String(128), nullable=True)
    email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # Active / Inactive / Alumni / Prospect
    status: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class Event(Base):
    """A club event, with DUSA submission tracking and attendance."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(512))
    type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, index=True, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    trimester: Mapped[str | None] = mapped_column(String(32), nullable=True)
    format: Mapped[str | None] = mapped_column(String(64), nullable=True)
    venue: Mapped[str | None] = mapped_column(String(256), nullable=True)
    event_lead_id: Mapped[int | None] = mapped_column(
        ForeignKey("people.id"), index=True, nullable=True
    )
    committee: Mapped[str | None] = mapped_column(String(128), nullable=True)
    dusa_submission_status: Mapped[str | None] = mapped_column(
        String(64), index=True, nullable=True
    )
    dusa_deadline: Mapped[date | None] = mapped_column(Date, index=True, nullable=True)
    dusa_required: Mapped[bool] = mapped_column(Boolean, default=False)
    food_provided: Mapped[bool] = mapped_column(Boolean, default=False)
    external_guests: Mapped[bool] = mapped_column(Boolean, default=False)
    expected_attendance: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_attendance: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class Sponsor(Base):
    """A sponsorship lead/relationship through its pipeline stages."""

    __tablename__ = "sponsors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organisation: Mapped[str] = mapped_column(String(256))
    stage: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    contact_person_id: Mapped[int | None] = mapped_column(
        ForeignKey("people.id"), index=True, nullable=True
    )
    tier: Mapped[str | None] = mapped_column(String(64), nullable=True)
    value_aud: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    dusa_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class FinanceEntry(Base):
    """A grant, income, reimbursement, or expense line."""

    __tablename__ = "finance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item: Mapped[str] = mapped_column(String(256))
    # Grant / Sponsorship Income / Reimbursement / Other Expense
    type: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    amount_aud: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    gst_included: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    date_requested: Mapped[date | None] = mapped_column(Date, nullable=True)
    date_paid: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    related_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("events.id"), index=True, nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


# =============================================================================
# Dashboard auth (dsec-app / NextAuth credentials)
# =============================================================================


class AppUser(Base):
    """A dsec-app login (exec).

    `dsec-api` owns the table (schema + migrations); `dsec-app` creates users
    (hashing passwords in Node) and verifies them at sign-in via NextAuth. The
    Python service never reads or writes password hashes.
    """

    __tablename__ = "app_user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    role: Mapped[str] = mapped_column(String(32), default="exec")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
