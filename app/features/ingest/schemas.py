"""Response models for the ingest feature."""

from __future__ import annotations

from pydantic import BaseModel, Field


class MembershipSummary(BaseModel):
    total_members: int
    dusa_member_count: int
    non_dusa_count: int
    new_count: int
    renewal_count: int


class FinanceSummary(BaseModel):
    opening_balance: float
    total_income: float
    total_expense: float
    closing_balance: float
    transaction_count: int


class IngestResponse(BaseModel):
    status: str  # "ingested"
    report_type: str  # "membership" | "pnl"
    message_id: str
    import_id: int
    rows_ingested: int
    membership: MembershipSummary | None = None
    finance: FinanceSummary | None = None


class ImportLogEntry(BaseModel):
    id: int
    message_id: str
    report_type: str
    filename: str | None
    status: str
    rows_ingested: int | None
    detail: str | None
    created_at: str


class EmailCaptureRequest(BaseModel):
    """One inbound email forwarded raw by the Gmail capture script.

    Deliberately permissive: only ``message_id`` is required so a malformed
    header never costs us the capture. ``from`` is aliased because it is a
    Python keyword.
    """

    message_id: str
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    subject: str | None = None
    body: str | None = None
    received_at: str | None = None  # ISO8601 string
    thread_id: str | None = None

    model_config = {"populate_by_name": True}


class EmailCaptureResponse(BaseModel):
    status: str  # "captured" | "duplicate"
    message_id: str
    event_id: int | None = None
