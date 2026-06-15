"""Finance helpers over the DUSA-imported P&L + event budgets/grants.

DUSA is the source of truth for actuals (imported via /ingest). This layer adds
the planning side the committee controls: a budget per event with an auto 50%
grant, plus a 'current finances' summary the weekly update reads from.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Event, FinanceReport, FinanceTransaction

GRANT_RATE = 0.5  # DUSA club grant: 50% of approved budget.


def set_event_budget(
    db: Session, event_id: int, budget_aud: float | None, grant_rate: float = GRANT_RATE
) -> Event | None:
    """Set an event's budget and auto-apply the grant (grant = budget * rate)."""
    ev = db.get(Event, event_id)
    if ev is None:
        return None
    ev.budget_aud = budget_aud
    ev.grant_aud = round(float(budget_aud) * grant_rate, 2) if budget_aud is not None else None
    db.commit()
    db.refresh(ev)
    return ev


def current_report(db: Session) -> FinanceReport | None:
    return db.execute(
        select(FinanceReport).where(FinanceReport.is_current.is_(True))
    ).scalar_one_or_none()


def _f(value) -> float | None:
    return float(value) if value is not None else None


def finances_summary(db: Session) -> dict:
    """Headline numbers for the weekly finance update."""
    rep = current_report(db)
    budget_total = db.execute(
        select(func.coalesce(func.sum(Event.budget_aud), 0)).where(Event.archived.is_(False))
    ).scalar_one()
    grant_total = db.execute(
        select(func.coalesce(func.sum(Event.grant_aud), 0)).where(Event.archived.is_(False))
    ).scalar_one()
    return {
        "report_date": rep.report_date.isoformat() if rep and rep.report_date else None,
        "opening_balance": _f(rep.opening_balance) if rep else None,
        "total_income": _f(rep.total_income) if rep else None,
        "total_expense": _f(rep.total_expense) if rep else None,
        "closing_balance": _f(rep.closing_balance) if rep else None,
        "transaction_count": rep.transaction_count if rep else 0,
        "total_event_budget": float(budget_total or 0),
        "total_event_grant": float(grant_total or 0),
    }


def list_transactions(
    db: Session, *, kind: str | None = None, limit: int = 100, offset: int = 0
) -> list[FinanceTransaction]:
    rep = current_report(db)
    if rep is None:
        return []
    stmt = select(FinanceTransaction).where(FinanceTransaction.report_id == rep.id)
    if kind:
        stmt = stmt.where(FinanceTransaction.kind == kind)
    stmt = stmt.order_by(FinanceTransaction.posting_date.desc()).limit(min(limit, 500)).offset(offset)
    return list(db.execute(stmt).scalars().all())


def list_reports(db: Session, *, limit: int = 20) -> list[FinanceReport]:
    return list(
        db.execute(
            select(FinanceReport).order_by(FinanceReport.created_at.desc()).limit(min(limit, 100))
        ).scalars().all()
    )
