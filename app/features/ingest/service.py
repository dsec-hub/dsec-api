"""Ingest logic for the weekly DUSA imports.

Parses first (no DB writes), then lands the data transactionally. Idempotent on
the Gmail ``message_id``: a repeat upload of an already-ingested message is a
no-op the router reports as ``409``. Parse failures are still recorded (as a
``failed`` ``DusaImport`` row) so the audit trail captures them.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.clock import local_date, today_local
from app.models import (
    DusaImport,
    FinanceReport,
    FinanceTransaction,
    Member,
    MemberReport,
)

from .parser import parse_membership, parse_pnl
from .schemas import FinanceSummary, MembershipSummary

log = logging.getLogger(__name__)

REPORT_TYPES = ("membership", "pnl")

# A membership report REPLACES the whole roster: _ingest_membership marks every
# member not-current, then turns back on only the students present in this file.
# So a truncated, mis-parsed or partially-delivered spreadsheet silently strands
# every member it omits (they land on /locked). Refuse to ingest a membership
# report whose row count has collapsed relative to the previous one — a real
# term-boundary drop happens, but a sudden fall below this fraction of last week
# is far more likely a bad file. Tune WITH the club (too tight and people learn to
# click through the override). A human who has eyeballed the spreadsheet can still
# apply a genuine mass drop via the override (settings.DUSA_INGEST_OVERRIDE, wired
# to `override_roster_guard` at the ingest entry point). (NEW-APPDEEP-03)
ROSTER_DROP_MIN_FRACTION = 0.8


class DuplicateImport(Exception):
    """Raised when a message_id has already been successfully ingested."""

    def __init__(self, existing: DusaImport):
        self.existing = existing
        super().__init__(f"message {existing.message_id} already ingested")


class IngestError(Exception):
    """Raised on a parse/ingest failure (a failed import row has been recorded)."""

    def __init__(self, message: str, imp: DusaImport):
        self.imp = imp
        super().__init__(message)


class RosterGuardRejected(Exception):
    """Raised (before any write) when a membership import would gut the roster —
    an empty report, or one whose row count has collapsed below
    ``ROSTER_DROP_MIN_FRACTION`` of the previous report. The caller records the
    import with a distinguishable status (``needs_review``) and a detail naming
    both row counts, and leaves every member's ``is_current`` untouched."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def handle_dusa_upload(
    db: Session,
    *,
    report_type: str,
    message_id: str,
    data: bytes,
    filename: str | None = None,
    sender: str | None = None,
    subject: str | None = None,
    received_at: datetime | None = None,
    override_roster_guard: bool = False,
):
    """Ingest one uploaded workbook. Returns (import_row, rows, summary).

    Raises ``DuplicateImport`` if already ingested, ``IngestError`` on parse
    failure (with a recorded failed import), ``RosterGuardRejected`` if a
    membership import would gut the roster (with a recorded ``needs_review``
    import and the roster left untouched), or ``ValueError`` on bad input.

    ``override_roster_guard`` (wired to ``settings.DUSA_INGEST_OVERRIDE`` at the
    HTTP entry point) lets a human apply a genuine, reviewed mass drop; it never
    bypasses the zero-row check.
    """
    if report_type not in REPORT_TYPES:
        raise ValueError(f"unknown report_type {report_type!r} (expected one of {REPORT_TYPES})")

    existing = db.execute(
        select(DusaImport).where(DusaImport.message_id == message_id)
    ).scalar_one_or_none()
    if existing is not None and existing.status == "ok":
        raise DuplicateImport(existing)

    # The date a committee member reads as "as of ..." must be the club's local
    # (Melbourne) calendar day, not the server's UTC day — a Friday-morning report
    # arriving before 10/11am Melbourne would otherwise be filed under Thursday.
    report_date = local_date(received_at) if received_at else today_local()

    # --- Parse first; record a failed import (its own commit) if it blows up ---
    try:
        if report_type == "membership":
            parsed = parse_membership(data)
        else:
            parsed = parse_pnl(data)
    except Exception as exc:  # noqa: BLE001 — we want to record *any* parse failure
        imp = _upsert_import(
            db, existing, report_type, message_id, filename, sender, subject,
            received_at, status="failed", detail=f"parse error: {exc}", rows=None,
        )
        db.commit()
        raise IngestError(str(exc), imp) from exc

    # --- Land it ---
    imp = _upsert_import(
        db, existing, report_type, message_id, filename, sender, subject,
        received_at, status="ok", detail=None, rows=None,
    )
    db.flush()  # assign imp.id for the FK

    if report_type == "membership":
        try:
            rows = _ingest_membership(
                db, parsed, imp.id, report_date, override=override_roster_guard
            )
        except RosterGuardRejected as rej:
            # The guard runs BEFORE the destructive not-current sweep, so nothing
            # was mutated. Record the import with a distinguishable status + both
            # row counts (the detail) so it stands out loudly in the hub's import
            # list rather than as a quiet "ok" row, then re-raise. Every member's
            # is_current is left exactly as it was. (NEW-APPDEEP-03)
            imp.status = "needs_review"
            imp.detail = str(rej)
            imp.rows_ingested = 0
            db.commit()
            log.warning("ingest: membership import %s rejected — %s", message_id, rej)
            raise
        summary: MembershipSummary | FinanceSummary = MembershipSummary(
            total_members=parsed.total,
            dusa_member_count=parsed.dusa_member_count,
            non_dusa_count=parsed.non_dusa_count,
            new_count=parsed.new_count,
            renewal_count=parsed.renewal_count,
        )
    else:
        rows = _ingest_pnl(db, parsed, imp.id, report_date)
        summary = FinanceSummary(
            opening_balance=parsed.opening_balance,
            total_income=parsed.total_income,
            total_expense=parsed.total_expense,
            closing_balance=parsed.closing_balance,
            transaction_count=len(parsed.transactions),
        )

    imp.rows_ingested = rows
    db.commit()
    return imp, rows, summary


def _upsert_import(db, existing, report_type, message_id, filename, sender, subject,
                   received_at, *, status, detail, rows) -> DusaImport:
    """Create or reuse the DusaImport row (a prior *failed* row is retried)."""
    imp = existing or DusaImport(message_id=message_id)
    imp.report_type = report_type
    imp.filename = filename
    imp.sender = sender
    imp.subject = subject
    imp.received_at = received_at
    imp.status = status
    imp.detail = detail
    imp.rows_ingested = rows
    if existing is None:
        db.add(imp)
    return imp


def _roster_guard_reason(db: Session, incoming_total: int, *, override: bool) -> str | None:
    """Return WHY this membership import must be refused, or ``None`` to proceed.

    An empty report is never legitimate and is refused even under ``override``. A
    below-``ROSTER_DROP_MIN_FRACTION`` drop is refused unless ``override`` is set
    (a deliberate, human-reviewed mass drop). The first-ever import has no baseline
    and always proceeds (unless empty). ``MemberReport`` rows are only created for
    successful membership imports, so the newest one is the right baseline.
    """
    if incoming_total <= 0:
        return (
            f"membership report has {incoming_total} rows — refusing to wipe the "
            "roster (an empty report is never valid; the override does not apply)"
        )
    if override:
        return None
    prev_total = db.execute(
        select(MemberReport.total_members).order_by(MemberReport.id.desc())
    ).scalars().first()
    if not prev_total or prev_total <= 0:
        return None  # no prior baseline to compare against (first real import)
    floor = ROSTER_DROP_MIN_FRACTION * prev_total
    if incoming_total < floor:
        return (
            f"membership report has {incoming_total} rows, below "
            f"{ROSTER_DROP_MIN_FRACTION:.0%} of the previous report's {prev_total} "
            f"(floor {floor:.0f}) — refusing to wipe the roster. Set "
            f"DUSA_INGEST_OVERRIDE to apply a genuine, reviewed mass drop."
        )
    return None


def _ingest_membership(
    db: Session, parsed, import_id: int | None, report_date: date, *, override: bool = False
) -> int:
    """Upsert the roster by student_id; flip non-present members to not-current.

    Refuses (raising ``RosterGuardRejected`` BEFORE the destructive not-current
    sweep, so nothing is mutated) an import that would gut the roster — see
    ``_roster_guard_reason``. (NEW-APPDEEP-03)
    """
    reason = _roster_guard_reason(db, parsed.total, override=override)
    if reason is not None:
        raise RosterGuardRejected(reason)

    # The report IS the current paid list: start everyone not-current, then turn
    # the rows present in this report back on.
    db.execute(update(Member).values(is_current=False))

    for rec in parsed.members:
        sid = rec.get("student_id")
        if not sid:
            continue
        row = db.execute(
            select(Member).where(Member.student_id == sid)
        ).scalar_one_or_none()
        if row is None:
            row = Member(student_id=sid)
            db.add(row)
        row.full_name = rec.get("full_name")
        row.email = rec.get("email")
        row.campus = rec.get("campus")
        row.faculty = rec.get("faculty")
        row.payment_option = rec.get("payment_option")
        row.membership_type = rec.get("membership_type")
        row.dusa_member = bool(rec.get("dusa_member"))
        row.first_subscription_date = rec.get("first_subscription_date")
        row.last_paid_date = rec.get("last_paid_date")
        row.end_date = rec.get("end_date")
        row.is_current = True
        row.last_seen_at = _utcnow()

    db.add(MemberReport(
        import_id=import_id,
        report_date=report_date,
        total_members=parsed.total,
        dusa_member_count=parsed.dusa_member_count,
        non_dusa_count=parsed.non_dusa_count,
        new_count=parsed.new_count,
        renewal_count=parsed.renewal_count,
    ))
    return parsed.total


def _ingest_pnl(db: Session, parsed, import_id: int, report_date: date) -> int:
    """Supersede the prior P&L snapshot and insert this one's ledger lines."""
    db.execute(update(FinanceReport).values(is_current=False))

    report = FinanceReport(
        import_id=import_id,
        report_date=report_date,
        fy_start=parsed.fy_start,
        opening_balance=parsed.opening_balance,
        total_income=parsed.total_income,
        total_expense=parsed.total_expense,
        closing_balance=parsed.closing_balance,
        transaction_count=len(parsed.transactions),
        is_current=True,
    )
    db.add(report)
    db.flush()  # assign report.id

    for t in parsed.transactions:
        db.add(FinanceTransaction(
            report_id=report.id,
            posting_date=t.get("posting_date"),
            document_no=t.get("document_no"),
            gl_account_no=t.get("gl_account_no"),
            gl_account_name=t.get("gl_account_name"),
            description=t.get("description"),
            department_code=t.get("department_code"),
            club_code=t.get("club_code"),
            amount=t.get("amount"),
            amount_abs=t.get("amount_abs"),
            kind=t.get("kind"),
        ))
    return len(parsed.transactions)
