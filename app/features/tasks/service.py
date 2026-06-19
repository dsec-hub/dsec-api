"""Tasks repository functions — pure, Session-based, reused by REST + MCP.

Two models: TaskBoard (a Trello-style board) and Task (a board card). Same
convention shared by every workspace feature:
    list_<x>(db, *, archived=False, limit, offset, **filters) -> list[Model]
    get_<x>(db, id) -> Model | None
    create_<x>(db, data: dict) -> Model
    update_<x>(db, id, data: dict) -> Model | None        (PATCH; only given keys)
    archive_<x>(db, id) -> Model | None                   (soft delete)
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.notify import notify_task_assigned
from app.core.owners import attach_owner_ids, set_owners
from app.models import Task, TaskBoard, TaskOwner


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _attach_owners(db: Session, rows):
    """Populate `.co_owner_ids` on a task (or list of tasks) for the Out schema."""
    attach_owner_ids(db, TaskOwner, TaskOwner.task_id, rows)
    return rows


# -----------------------------------------------------------------------------
# Boards
# -----------------------------------------------------------------------------


def list_boards(
    db: Session,
    *,
    archived: bool = False,
    committee: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[TaskBoard]:
    stmt = select(TaskBoard)
    if not archived:
        stmt = stmt.where(TaskBoard.archived.is_(False))
    if committee:
        stmt = stmt.where(TaskBoard.committee == committee)
    stmt = stmt.order_by(TaskBoard.updated_at.desc()).limit(min(limit, 200)).offset(offset)
    return list(db.execute(stmt).scalars().all())


def get_board(db: Session, board_id: int) -> TaskBoard | None:
    return db.get(TaskBoard, board_id)


def create_board(db: Session, data: dict) -> TaskBoard:
    board = TaskBoard(**data)
    db.add(board)
    db.commit()
    db.refresh(board)
    return board


def update_board(db: Session, board_id: int, data: dict) -> TaskBoard | None:
    board = db.get(TaskBoard, board_id)
    if board is None:
        return None
    for key, value in data.items():
        setattr(board, key, value)
    db.commit()
    db.refresh(board)
    return board


def archive_board(db: Session, board_id: int) -> TaskBoard | None:
    board = db.get(TaskBoard, board_id)
    if board is None:
        return None
    board.archived = True
    db.commit()
    db.refresh(board)
    return board


# -----------------------------------------------------------------------------
# Tasks (cards)
# -----------------------------------------------------------------------------


def list_tasks(
    db: Session,
    *,
    archived: bool = False,
    board_id: int | None = None,
    assignee_id: int | None = None,
    status: str | None = None,
    committee: str | None = None,
    related_event_id: int | None = None,
    related_project_id: int | None = None,
    related_sponsor_id: int | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[Task]:
    stmt = select(Task)
    if not archived:
        stmt = stmt.where(Task.archived.is_(False))
    if board_id is not None:
        stmt = stmt.where(Task.board_id == board_id)
    if assignee_id is not None:
        stmt = stmt.where(Task.assignee_id == assignee_id)
    if status:
        stmt = stmt.where(Task.status == status)
    if committee:
        stmt = stmt.where(Task.committee == committee)
    if related_event_id is not None:
        stmt = stmt.where(Task.related_event_id == related_event_id)
    if related_project_id is not None:
        stmt = stmt.where(Task.related_project_id == related_project_id)
    if related_sponsor_id is not None:
        stmt = stmt.where(Task.related_sponsor_id == related_sponsor_id)
    stmt = (
        stmt.order_by(Task.status, Task.position, Task.id)
        .limit(min(limit, 500))
        .offset(offset)
    )
    rows = list(db.execute(stmt).scalars().all())
    return _attach_owners(db, rows)


def get_task(db: Session, task_id: int) -> Task | None:
    return _attach_owners(db, db.get(Task, task_id))


def create_task(db: Session, data: dict) -> Task:
    data = dict(data)
    co_owner_ids = data.pop("co_owner_ids", None)
    if data.get("position") is None:
        status = data.get("status") or "Backlog"
        max_pos = db.execute(
            select(func.max(Task.position)).where(
                Task.board_id == data.get("board_id"),
                Task.status == status,
            )
        ).scalar_one()
        data["position"] = (max_pos + 1) if max_pos is not None else 0
    task = Task(**data)
    db.add(task)
    db.commit()
    db.refresh(task)
    if co_owner_ids is not None:
        set_owners(db, TaskOwner, TaskOwner.task_id, task.id, co_owner_ids, exclude=task.assignee_id)
    # A brand-new task that already has an assignee IS an assignment — hand off to
    # dsec-hub so the assignee gets notified (the dashboard's own on-assign hook
    # can't see REST/MCP writes). Best-effort; never blocks or fails the create.
    if task.assignee_id is not None:
        notify_task_assigned(task_id=task.id, assignee_person_id=task.assignee_id)
    return _attach_owners(db, task)


def update_task(db: Session, task_id: int, data: dict) -> Task | None:
    task = db.get(Task, task_id)
    if task is None:
        return None
    data = dict(data)
    co_owner_ids = data.pop("co_owner_ids", None)
    prev_assignee_id = task.assignee_id
    for key, value in data.items():
        setattr(task, key, value)
    db.commit()
    db.refresh(task)
    if co_owner_ids is not None:
        set_owners(db, TaskOwner, TaskOwner.task_id, task.id, co_owner_ids, exclude=task.assignee_id)
    # Notify only on a real (re)assignment to a person — the patch touched
    # assignee_id and landed on a non-null value different from before. Clearing
    # the assignee or re-saving the same one stays silent.
    if (
        "assignee_id" in data
        and task.assignee_id is not None
        and task.assignee_id != prev_assignee_id
    ):
        notify_task_assigned(task_id=task.id, assignee_person_id=task.assignee_id)
    return _attach_owners(db, task)


def archive_task(db: Session, task_id: int) -> Task | None:
    task = db.get(Task, task_id)
    if task is None:
        return None
    task.archived = True
    db.commit()
    db.refresh(task)
    return _attach_owners(db, task)


def move_task(db: Session, task_id: int, *, status: str, position: int) -> Task | None:
    task = db.get(Task, task_id)
    if task is None:
        return None
    task.status = status
    task.position = position
    if status == "Done":
        task.completed_at = _utcnow()
    elif task.completed_at is not None:
        task.completed_at = None
    db.commit()
    db.refresh(task)
    return _attach_owners(db, task)
