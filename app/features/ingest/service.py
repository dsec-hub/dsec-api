"""Ingest logic for the weekly DUSA imports.

Parses first (no DB writes), then lands the data transactionally. Idempotent on
the Gmail ``message_id``: a repeat upload of an already-ingested message is a
no-op the router reports as ``409``. Parse failures are still recorded (as a
``failed`` ``DusaImport`` row) so the audit trail captures them.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import (
    DusaImport,
    FinanceReport,
    FinanceTransaction,
    Member,
    MemberReport,
)

from .parser import parse_membership, parse_pnl
from .schemas import FinanceSummary, MembershipSummary

REPORT_TYPES = ("membership", "pnl")


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
):
    """Ingest one uploaded workbook. Returns (import_row, rows, summary).

    Raises ``DuplicateImport`` if already ingested, ``IngestError`` on parse
    failure (with a recorded failed import), or ``ValueError`` on bad input.
    """
    if report_type not in REPORT_TYPES:
        raise ValueError(f"unknown report_type {report_type!r} (expected one of {REPORT_TYPES})")

    existing = db.execute(
        select(DusaImport).where(DusaImport.message_id == message_id)
    ).scalar_one_or_none()
    if existing is not None and existing.status == "ok":
        raise DuplicateImport(existing)

    report_date = received_at.date() if received_at else date.today()

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
        rows = _ingest_membership(db, parsed, imp.id, report_date)
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


def _ingest_membership(db: Session, parsed, import_id: int, report_date: date) -> int:
    """Upsert the roster by student_id; flip non-present members to not-current."""
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
