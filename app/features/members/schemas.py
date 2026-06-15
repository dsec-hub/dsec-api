"""Member schemas (read-only)."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    student_id: str
    full_name: str | None
    email: str | None
    campus: str | None
    faculty: str | None
    membership_type: str | None
    dusa_member: bool
    first_subscription_date: date | None
    last_paid_date: date | None
    end_date: date | None
    is_current: bool


class MemberCounts(BaseModel):
    current_members: int
    dusa_members: int
    non_dusa_members: int
    total_ever_seen: int


class MemberTrendPoint(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    report_date: date | None
    total_members: int
    dusa_member_count: int
    non_dusa_count: int
    new_count: int
    renewal_count: int


class MemberStats(BaseModel):
    counts: MemberCounts
    trend: list[MemberTrendPoint]
