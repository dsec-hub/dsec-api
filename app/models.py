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
    func,
    text,
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
    # Optional DUSA student id — when set, links this person to their live
    # paid-membership row (see `Member`) by student id. Populated by the person
    # themselves (dsec-app profile page) or an admin.
    student_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    # Self-managed social / portfolio links (edited via the dsec-app profile page).
    discord: Mapped[str | None] = mapped_column(String(128), nullable=True)
    instagram: Mapped[str | None] = mapped_column(String(128), nullable=True)
    github: Mapped[str | None] = mapped_column(String(128), nullable=True)
    linkedin: Mapped[str | None] = mapped_column(String(256), nullable=True)
    website: Mapped[str | None] = mapped_column(String(256), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
        server_default=func.now(),
    )
    archived: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), index=True
    )


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
    # Public "buy tickets / register" link, surfaced on the website event card +
    # detail page. Free-form URL; null means no external ticketing for this event.
    ticket_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # Tiered ticket pricing, shown on the public event page. A list of
    # {"label": str, "price": float | None} — price 0 = free, null = unset/POA.
    # The dashboard seeds three defaults (DSEC member / DUSA member / general
    # public) and allows custom tiers. Ticketing is moot once an event is past.
    ticket_tiers: Mapped[list | None] = mapped_column(JSON, default=list)
    event_lead_id: Mapped[int | None] = mapped_column(
        ForeignKey("people.id"), index=True, nullable=True
    )
    committee: Mapped[str | None] = mapped_column(String(128), nullable=True)
    dusa_submission_status: Mapped[str | None] = mapped_column(
        String(64), index=True, nullable=True
    )
    dusa_deadline: Mapped[date | None] = mapped_column(Date, index=True, nullable=True)
    dusa_required: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    food_provided: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    external_guests: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    expected_attendance: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_attendance: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Free-form event description (Markdown) shown on the public website.
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Budget planning: a grant of 50% of the budget is auto-applied (see
    # features.finance.service.set_event_budget); both stored for visibility.
    budget_aud: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    grant_aud: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    # Non-financial collaboration: what an external partner provides for this
    # event (e.g. ["Speakers","Food"]) and who the partner organisation is. Lets
    # an event exist as a partner-run collaboration with no money/sponsor link.
    support_types: Mapped[list | None] = mapped_column(JSON, default=list)
    partner_org: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # Optional link to a Sponsor/Partner record providing support for this event.
    related_sponsor_id: Mapped[int | None] = mapped_column(
        ForeignKey("sponsors.id"), index=True, nullable=True
    )
    # Post-event review form (Tally). Set once a form is created via the reviews
    # feature; null means "no review form yet". `review_form_id` is the Tally form
    # id used to fetch submissions; `review_form_url` is the public fill link.
    # These are owned by the reviews feature — never set via a generic event PATCH.
    review_form_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    review_form_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    review_form_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
        server_default=func.now(),
    )
    archived: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), index=True
    )


class Sponsor(Base):
    """A sponsorship lead/relationship through its pipeline stages."""

    __tablename__ = "sponsors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organisation: Mapped[str] = mapped_column(String(256))
    stage: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    # Sponsor (gives money) vs Partner (in-kind only). Drives labelling/filtering;
    # null is treated as "Sponsor". Partners typically use the "Custom" tier.
    relationship_type: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    contact_person_id: Mapped[int | None] = mapped_column(
        ForeignKey("people.id"), index=True, nullable=True
    )
    tier: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # value_aud stays nullable: in-kind sponsors give support, not money.
    value_aud: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    # What the sponsor provides — financial and/or in-kind (e.g. ["Cash","Venue"]).
    support_types: Mapped[list | None] = mapped_column(JSON, default=list)
    dusa_approved: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    # Show this sponsor (with its uploaded logo) on the public website's sponsor
    # wall. Off by default — the sponsor table is a pipeline, so prospects stay
    # private until an exec explicitly publishes a confirmed sponsor.
    show_on_website: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    # Lightweight CRM/pipeline fields (the dashboard manages these as a task list).
    contact_email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    website: Mapped[str | None] = mapped_column(String(256), nullable=True)
    next_action: Mapped[str | None] = mapped_column(String(512), nullable=True)
    next_action_date: Mapped[date | None] = mapped_column(Date, index=True, nullable=True)
    last_contact_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
        server_default=func.now(),
    )
    archived: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), index=True
    )


class FinanceEntry(Base):
    """A grant, income, reimbursement, or expense line."""

    __tablename__ = "finance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item: Mapped[str] = mapped_column(String(256))
    # Grant / Sponsorship Income / Reimbursement / Other Expense
    type: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    amount_aud: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    gst_included: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    status: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    date_requested: Mapped[date | None] = mapped_column(Date, nullable=True)
    date_paid: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    related_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("events.id"), index=True, nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
        server_default=func.now(),
    )
    archived: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), index=True
    )


# =============================================================================
# DUSA weekly imports (ingested from email by the Gmail forwarder)
# =============================================================================
#
# Every Friday DUSA emails two spreadsheets. A Google Apps Script forwards each
# `.xlsx` to `POST /ingest/dusa`, which parses it (openpyxl) and lands it here.
#
# * `DusaImport`        — one row per upload: dedup key + audit trail (success
#                         *and* failure). The `message_id` makes re-sends no-ops.
# * `Member`            — the live paid-membership roster, upserted by student id.
# * `MemberReport`      — one weekly membership snapshot's stats (for trends).
# * `FinanceReport`     — one weekly P&L snapshot's headline balances.
# * `FinanceTransaction`— individual ledger lines from a P&L snapshot.
#
# These are *imported* tables (DUSA is the source of truth), kept separate from
# the exec-managed `finance` table so an import never clobbers manual entries.


class DusaImport(Base):
    """One DUSA upload. Dedupes on `message_id`; records successes and failures."""

    __tablename__ = "dusa_import"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Gmail message id — the idempotency key. A repeat upload is a no-op (409).
    message_id: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    report_type: Mapped[str] = mapped_column(String(32), index=True)  # membership|pnl
    filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    sender: Mapped[str | None] = mapped_column(String(512), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), default="ok", index=True)  # ok|failed
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    rows_ingested: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), index=True
    )


class Member(Base):
    """A paid club member, from the weekly DUSA membership report.

    Upserted by `student_id` each week. `is_current` marks rows present in the
    latest report (the report *is* the current paid list); members who don't
    renew simply stop appearing and get flipped to `is_current=False`.
    """

    __tablename__ = "members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    full_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    email: Mapped[str | None] = mapped_column(String(256), index=True, nullable=True)
    campus: Mapped[str | None] = mapped_column(String(128), nullable=True)
    faculty: Mapped[str | None] = mapped_column(String(256), nullable=True)
    payment_option: Mapped[str | None] = mapped_column(String(256), nullable=True)
    membership_type: Mapped[str | None] = mapped_column(  # New|Renewal
        String(32), index=True, nullable=True
    )
    dusa_member: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), index=True
    )
    first_subscription_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_paid_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, index=True, nullable=True)
    is_current: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), index=True
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
        server_default=func.now(),
    )


class MemberReport(Base):
    """Headline stats for one weekly membership snapshot (drives trend charts)."""

    __tablename__ = "member_report"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    import_id: Mapped[int | None] = mapped_column(
        ForeignKey("dusa_import.id"), index=True, nullable=True
    )
    report_date: Mapped[date | None] = mapped_column(Date, index=True, nullable=True)
    total_members: Mapped[int] = mapped_column(Integer, default=0)
    dusa_member_count: Mapped[int] = mapped_column(Integer, default=0)
    non_dusa_count: Mapped[int] = mapped_column(Integer, default=0)
    new_count: Mapped[int] = mapped_column(Integer, default=0)
    renewal_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )


class FinanceReport(Base):
    """Headline balances for one weekly P&L snapshot.

    The P&L is cumulative (full FY-to-date) every week, so each import supersedes
    the last. `is_current` flags the newest; transactions hang off the report.
    """

    __tablename__ = "finance_report"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    import_id: Mapped[int | None] = mapped_column(
        ForeignKey("dusa_import.id"), index=True, nullable=True
    )
    report_date: Mapped[date | None] = mapped_column(Date, index=True, nullable=True)
    fy_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    opening_balance: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    total_income: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    total_expense: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    closing_balance: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    transaction_count: Mapped[int] = mapped_column(Integer, default=0)
    is_current: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )


class FinanceTransaction(Base):
    """One ledger line from a P&L snapshot (the 'Detailed Club Transactions' tab).

    DUSA's sign convention is preserved in `amount` (income negative, expense
    positive); `kind` and `amount_abs` are derived for convenient querying.
    """

    __tablename__ = "finance_transaction"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_id: Mapped[int] = mapped_column(
        ForeignKey("finance_report.id"), index=True
    )
    posting_date: Mapped[date | None] = mapped_column(Date, index=True, nullable=True)
    document_no: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    gl_account_no: Mapped[str | None] = mapped_column(String(16), index=True, nullable=True)
    gl_account_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    department_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    club_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    amount_abs: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    kind: Mapped[str | None] = mapped_column(String(16), index=True, nullable=True)  # income|expense|balance

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )


# =============================================================================
# Workspace tables (tasks, projects, meetings, documents)
# =============================================================================
#
# Read/write source of truth for the exec dashboard AND the MCP server. Same
# conventions as the club-domain tables: String enum-likes with inline allowed
# values, nullable indexed FKs, the created_at/updated_at/archived block.


class Project(Base):
    """A community project to showcase (drives the public website when public)."""

    __tablename__ = "project"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    slug: Mapped[str | None] = mapped_column(String(256), unique=True, index=True, nullable=True)
    summary: Mapped[str | None] = mapped_column(String(512), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Idea / Active / Completed / Showcased / On Hold
    status: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tech_tags: Mapped[list | None] = mapped_column(JSON, default=list)  # ["python","react"]
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("people.id"), index=True, nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    repo_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    demo_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    featured: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), index=True)
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), index=True)
    related_event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id"), index=True, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, server_default=func.now()
    )
    archived: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), index=True
    )


class TaskBoard(Base):
    """A Trello-style board. `columns` is the ordered list of column names."""

    __tablename__ = "task_board"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    committee: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    # Ordered column/list names; a task's `status` is one of these.
    columns: Mapped[list] = mapped_column(
        JSON, default=lambda: ["Backlog", "To Do", "In Progress", "Done"]
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, server_default=func.now()
    )
    archived: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), index=True
    )


class Task(Base):
    """A board card. `status` is the column name; `position` orders within it."""

    __tablename__ = "task"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    board_id: Mapped[int | None] = mapped_column(ForeignKey("task_board.id"), index=True, nullable=True)
    title: Mapped[str] = mapped_column(String(512))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(64), index=True, default="Backlog", server_default="Backlog")
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    priority: Mapped[str | None] = mapped_column(String(16), index=True, nullable=True)  # Low/Medium/High/Urgent
    # Who/what it's assigned to: a person, and/or a team/committee name.
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("people.id"), index=True, nullable=True)
    committee: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, index=True, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, index=True, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    related_event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id"), index=True, nullable=True)
    related_project_id: Mapped[int | None] = mapped_column(ForeignKey("project.id"), index=True, nullable=True)
    related_sponsor_id: Mapped[int | None] = mapped_column(ForeignKey("sponsors.id"), index=True, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, server_default=func.now()
    )
    archived: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), index=True
    )


class Meeting(Base):
    """A meeting + its transcript and AI-generated notes/action items."""

    __tablename__ = "meeting"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(512))
    # Committee / Exec / Sponsorship / General / Other
    type: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    meeting_date: Mapped[date | None] = mapped_column(Date, index=True, nullable=True)
    location: Mapped[str | None] = mapped_column(String(256), nullable=True)
    attendees: Mapped[list | None] = mapped_column(JSON, default=list)  # ["Name", ...]
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)  # markdown
    action_items: Mapped[list | None] = mapped_column(JSON, default=list)  # [{text,owner,due}]
    # Scheduled / Held / NotesDraft / NotesFinal
    status: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    related_event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id"), index=True, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(256), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, server_default=func.now()
    )
    archived: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), index=True
    )


class Document(Base):
    """A Notion-style doc. Markdown content now; `content_json` reserved for a
    future block editor. Can be a meeting-notes doc, a sponsor doc, or a
    per-person deliverable (via `assignee_id`)."""

    __tablename__ = "document"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(512))
    # Note / MeetingNotes / SponsorDoc / Deliverable / Policy / General
    type: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)  # markdown
    content_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # future block editor
    # Draft / InReview / Final
    status: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("document.id"), index=True, nullable=True)
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("people.id"), index=True, nullable=True)
    related_event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id"), index=True, nullable=True)
    related_sponsor_id: Mapped[int | None] = mapped_column(ForeignKey("sponsors.id"), index=True, nullable=True)
    related_project_id: Mapped[int | None] = mapped_column(ForeignKey("project.id"), index=True, nullable=True)
    related_meeting_id: Mapped[int | None] = mapped_column(ForeignKey("meeting.id"), index=True, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(256), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, server_default=func.now()
    )
    archived: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), index=True
    )


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
    # Optional link to the person's roster record. Set by dsec-app when an
    # invite is accepted (match by email, else a new `people` row is created),
    # so a login and its committee/contact record stay in sync.
    person_id: Mapped[int | None] = mapped_column(
        ForeignKey("people.id"), index=True, nullable=True
    )
    role: Mapped[str] = mapped_column(String(32), default="exec", server_default="exec")
    # Per-user UI theme, set via the dsec-app Appearance settings tab. Null on
    # any falls back to the brand default (Action Pink / Geist Mono / Inter).
    theme_accent: Mapped[str | None] = mapped_column(String(16), nullable=True)
    theme_font_title: Mapped[str | None] = mapped_column(String(32), nullable=True)
    theme_font_body: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
        server_default=func.now(),
    )


# =============================================================================
# Usage / activity log (who did what, from the dashboard and via MCP)
# =============================================================================


class UsageEvent(Base):
    """A single usage/audit event — a dashboard login/access, a content view or
    edit, or an MCP tool call. Powers the admin usage-stats view.

    Written by BOTH apps: `dsec-app` (Drizzle) logs dashboard logins/access and
    mutations; `dsec-api` logs MCP requests. `actor_label` is the human handle
    (member email, or the API key's label) so stats group cleanly per person.
    """

    __tablename__ = "usage_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_type: Mapped[str] = mapped_column(String(16), index=True)  # user|apikey|anon
    actor_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    actor_label: Mapped[str | None] = mapped_column(String(256), index=True, nullable=True)
    source: Mapped[str] = mapped_column(String(16), index=True)  # dashboard|mcp|api
    action: Mapped[str] = mapped_column(String(32), index=True)  # login|access|view|create|update|archive|tool_call
    target_type: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    path: Mapped[str | None] = mapped_column(String(512), nullable=True)  # url path or tool name
    detail: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), index=True
    )


class MediaAsset(Base):
    """An uploaded image attached to an event or project.

    Binaries live in Supabase Storage (a public bucket); only URLs + metadata
    live here. Each source upload is cropped client-side, then processed by the
    media feature into a compressed **WebP** (web display) and a **PNG**
    (download). Polymorphic by (`entity_type`, `entity_id`) — no FK so one table
    serves both events and projects. Read by `dsec-app` (gallery) and the public
    `/website` feed (dsec-website).
    """

    __tablename__ = "media_asset"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(16))  # event|project
    entity_id: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16))  # image|poster|banner
    alt_text: Mapped[str | None] = mapped_column(String(512), nullable=True)
    original_filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Public Supabase URLs (what the apps render / link to)
    webp_url: Mapped[str] = mapped_column(String(1024))
    png_url: Mapped[str] = mapped_column(String(1024))
    # Storage object paths within the bucket (needed to delete the objects)
    webp_path: Mapped[str] = mapped_column(String(512))
    png_path: Mapped[str] = mapped_column(String(512))
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)  # webp size
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, server_default=func.now()
    )
    archived: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), index=True
    )

    __table_args__ = (
        Index("ix_media_asset_entity", "entity_type", "entity_id"),
    )


# =============================================================================
# Sponsor packages (public-facing tier definitions, editable via dsec-app)
# =============================================================================


class SponsorPackage(Base):
    """A named sponsorship tier shown on the public website.

    The exec edits these from dsec-app; the API serves them at
    /website/sponsor-packages (no auth). Replaces the hardcoded `tiers` in
    dsec-website/src/lib/content.ts once at least one visible package exists.
    """

    __tablename__ = "sponsor_package"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))  # Supporter / Partner / Headline
    pitch: Mapped[str | None] = mapped_column(String(512), nullable=True)
    price: Mapped[str | None] = mapped_column(String(64), nullable=True)  # "from $500"
    includes: Mapped[list | None] = mapped_column(JSON, default=list)  # string[]
    featured: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    is_visible: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"), index=True)
    display_order: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, server_default=func.now()
    )


# =============================================================================
# Sponsor leads (inbound enquiries from website forms + Cal.com bookings)
# =============================================================================


class SponsorLead(Base):
    """An inbound sponsorship lead from dsec-website (form or Cal.com booking).

    Written by three sources:
    - /sponsor-leads POST (pricing-unlock modal, public — no auth)
    - /sponsor-leads POST (enquiry form, public — no auth)
    - /calcom/webhook  (BOOKING_CREATED event, HMAC-verified)

    The exec manages the pipeline in dsec-app (leads page, status updates).
    """

    __tablename__ = "sponsor_lead"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # pricing_unlock | enquiry | cal_booking
    source: Mapped[str] = mapped_column(String(32), index=True)
    tier: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    email: Mapped[str] = mapped_column(String(256), index=True)
    company: Mapped[str | None] = mapped_column(String(256), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    budget: Mapped[str | None] = mapped_column(String(64), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # new | contacted | converted | closed
    status: Mapped[str] = mapped_column(
        String(16), default="new", server_default=text("'new'"), index=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)  # internal team notes

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, server_default=func.now()
    )


# =============================================================================
# Sponsor contacts (individual people attached to a sponsorship)
# =============================================================================


class SponsorContact(Base):
    """A person attached to a sponsorship, with a role (Organiser, Contact, …).

    Either links an existing `people` row (`person_id`) or carries a free-text
    `name` for someone not in the directory. The dashboard manages these; the
    sponsor's headline `contact_person_id` remains the primary contact.
    """

    __tablename__ = "sponsor_contact"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sponsor_id: Mapped[int] = mapped_column(ForeignKey("sponsors.id"), index=True)
    person_id: Mapped[int | None] = mapped_column(
        ForeignKey("people.id"), index=True, nullable=True
    )
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)  # free-text fallback
    role: Mapped[str | None] = mapped_column(String(64), nullable=True)  # Organiser/Contact/Signatory/Other
    email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, server_default=func.now()
    )
    archived: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), index=True
    )


class EventSpeaker(Base):
    """A speaker presenting at an event.

    Either links an existing `people` row (`person_id`, which autofills the
    display name/title in the dashboard) or carries a free-text `name` for an
    external guest not in the directory. The headshot lives in `media_asset`
    (entity_type="speaker", entity_id=this row's id, role="photo").
    """

    __tablename__ = "event_speaker"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    person_id: Mapped[int | None] = mapped_column(
        ForeignKey("people.id"), index=True, nullable=True
    )
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)  # free-text fallback
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)  # e.g. "CTO at Acme"
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, server_default=func.now()
    )
    archived: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), index=True
    )


class EventSponsor(Base):
    """Links a sponsor to an event (many-to-many) so an event can show a wall of
    sponsor logos. The logo itself lives on the sponsor (media_asset
    entity_type="sponsor", role="logo") and is reused across every event.

    Distinct from `Event.related_sponsor_id`, which stays as the single headline
    sponsor link; this table is the multi-sponsor display mechanism.
    """

    __tablename__ = "event_sponsor"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    sponsor_id: Mapped[int] = mapped_column(ForeignKey("sponsors.id"), index=True)
    tier: Mapped[str | None] = mapped_column(String(64), nullable=True)  # optional per-event tier
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, server_default=func.now()
    )
    archived: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), index=True
    )

    __table_args__ = (
        UniqueConstraint("event_id", "sponsor_id", name="uq_event_sponsor"),
    )


# =============================================================================
# Attachments (uploaded files — PDFs & images — for sponsors etc.)
# =============================================================================


class Attachment(Base):
    """An uploaded document/image attached to an entity (sponsors today).

    Like `MediaAsset`, binaries live in Supabase Storage and only the public URL
    + metadata land here. Unlike `MediaAsset` (image-only, dual WebP/PNG), this
    holds a single auto-compressed file of any supported kind: images are
    re-encoded to WebP, PDFs are recompressed with pikepdf. Polymorphic by
    (`entity_type`, `entity_id`) — no FK, so one table serves many owners.
    """

    __tablename__ = "attachment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(16))  # sponsor (extensible)
    entity_id: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))  # image|pdf|file
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    original_filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    url: Mapped[str] = mapped_column(String(1024))  # public Supabase URL
    path: Mapped[str] = mapped_column(String(512))  # bucket object path (for delete)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)  # stored (compressed)
    original_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)  # images only
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, server_default=func.now()
    )
    archived: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), index=True
    )

    __table_args__ = (
        Index("ix_attachment_entity", "entity_type", "entity_id"),
    )
