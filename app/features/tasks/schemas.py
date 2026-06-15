"""Pydantic models for the tasks feature (Trello-style boards + cards)."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


# -----------------------------------------------------------------------------
# Boards
# -----------------------------------------------------------------------------


class BoardBase(BaseModel):
    """All fields optional — reused for create (name overridden) and update."""

    name: str | None = None
    description: str | None = None
    committee: str | None = None
    columns: list[str] | None = None


class BoardCreate(BoardBase):
    name: str  # required on create


class BoardUpdate(BoardBase):
    """Every field optional; only those set are applied (PATCH semantics)."""


class BoardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    committee: str | None
    columns: list | None
    archived: bool
    created_at: datetime
    updated_at: datetime


# -----------------------------------------------------------------------------
# Tasks (cards)
# -----------------------------------------------------------------------------


class TaskBase(BaseModel):
    """All fields optional — reused for create (title overridden) and update."""

    board_id: int | None = None
    title: str | None = None
    description: str | None = None
    status: str | None = None
    position: int | None = None
    priority: str | None = None
    assignee_id: int | None = None
    committee: str | None = None
    start_date: date | None = None
    due_date: date | None = None
    completed_at: datetime | None = None
    related_event_id: int | None = None
    related_project_id: int | None = None
    related_sponsor_id: int | None = None


class TaskCreate(TaskBase):
    title: str  # required on create


class TaskUpdate(TaskBase):
    """Every field optional; only those set are applied (PATCH semantics)."""


class TaskMove(BaseModel):
    status: str
    position: int = 0


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    board_id: int | None
    title: str
    description: str | None
    status: str
    position: int
    priority: str | None
    assignee_id: int | None
    committee: str | None
    start_date: date | None
    due_date: date | None
    completed_at: datetime | None
    related_event_id: int | None
    related_project_id: int | None
    related_sponsor_id: int | None
    archived: bool
    created_at: datetime
    updated_at: datetime
