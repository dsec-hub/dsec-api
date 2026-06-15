"""Finance schemas."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict


class FinanceSummary(BaseModel):
    report_date: str | None
    opening_balance: float | None
    total_income: float | None
    total_expense: float | None
    closing_balance: float | None
    transaction_count: int
    total_event_budget: float
    total_event_grant: float


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    posting_date: date | None
    document_no: str | None
    gl_account_no: str | None
    gl_account_name: str | None
    description: str | None
    amount: float | None
    amount_abs: float | None
    kind: str | None


class ReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    report_date: date | None
    opening_balance: float | None
    total_income: float | None
    total_expense: float | None
    closing_balance: float | None
    transaction_count: int
    is_current: bool


class SetBudget(BaseModel):
    budget_aud: float | None
    grant_rate: float = 0.5


class EventBudgetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    budget_aud: float | None
    grant_aud: float | None
