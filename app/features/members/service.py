"""Member roster reads + stats. Members are READ-ONLY via the API — the roster
is owned by the weekly DUSA import (see features/ingest)."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Member, MemberReport


def list_members(
    db: Session,
    *,
    current_only: bool = True,
    dusa_only: bool | None = None,
    search: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[Member]:
    stmt = select(Member)
    if current_only:
        stmt = stmt.where(Member.is_current.is_(True))
    if dusa_only is not None:
        stmt = stmt.where(Member.dusa_member.is_(dusa_only))
    if search:
        like = f"%{search.lower()}%"
        stmt = stmt.where(
            func.lower(Member.full_name).like(like) | func.lower(Member.email).like(like)
        )
    stmt = stmt.order_by(Member.full_name).limit(min(limit, 1000)).offset(offset)
    return list(db.execute(stmt).scalars().all())


def get_member(db: Session, member_id: int) -> Member | None:
    return db.get(Member, member_id)


def all_current_members(db: Session) -> list[Member]:
    """Every CURRENT roster row — used to resolve a verification code to a member
    (we recompute each one's code and compare). The roster is small; no limit."""
    return list(
        db.execute(select(Member).where(Member.is_current.is_(True))).scalars().all()
    )


def member_counts(db: Session) -> dict:
    current = db.execute(
        select(func.count()).select_from(Member).where(Member.is_current.is_(True))
    ).scalar_one()
    dusa = db.execute(
        select(func.count()).select_from(Member)
        .where(Member.is_current.is_(True), Member.dusa_member.is_(True))
    ).scalar_one()
    total_seen = db.execute(select(func.count()).select_from(Member)).scalar_one()
    return {
        "current_members": current or 0,
        "dusa_members": dusa or 0,
        "non_dusa_members": (current or 0) - (dusa or 0),
        "total_ever_seen": total_seen or 0,
    }


def member_trend(db: Session, *, limit: int = 52) -> list[MemberReport]:
    """Weekly membership snapshots (newest first), for trend charts."""
    return list(
        db.execute(
            select(MemberReport).order_by(MemberReport.report_date.desc().nullslast())
            .limit(min(limit, 200))
        ).scalars().all()
    )
