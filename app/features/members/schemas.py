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


class MemberVerification(BaseModel):
    """The member's own membership-card payload (authenticated; portal fetches it)."""

    member_id: int
    code: str                 # display code, e.g. "DSEC-7K2P-9XQ4"
    full_name: str | None
    membership_type: str | None
    member_since: date | None  # first_subscription_date
    is_current: bool
    verify_url: str           # public URL the QR encodes
    qr_svg: str | None        # inline, CSS-sizable SVG QR (None if unavailable)


class PublicVerifyResult(BaseModel):
    """Public scan/verify result — capability-gated by knowing the code.

    Intentionally minimal: only what a door/event volunteer needs to confirm the
    person in front of them (name + active status). `member_id` lets the portal
    join to the member's face photo server-side; no email or student id here.
    """

    valid: bool
    member_id: int | None = None
    full_name: str | None = None
    membership_type: str | None = None
    member_since: date | None = None
    is_current: bool = False
