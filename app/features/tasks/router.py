"""Tasks REST API (boards + cards). Reads need `read`; writes need `write`."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core.apikeys import require_api_key
from app.core.ratelimit import limiter
from app.core.net import client_ip
from app.db import get_db
from app.models import APIKey

from . import service
from .schemas import (
    BoardCreate,
    BoardOut,
    BoardUpdate,
    TaskCreate,
    TaskMove,
    TaskOut,
    TaskUpdate,
)

router = APIRouter()




# -----------------------------------------------------------------------------
# Boards — registered before the /{task_id} routes so they don't get shadowed.
# -----------------------------------------------------------------------------


@router.get("/boards", response_model=list[BoardOut])
def list_boards(
    request: Request,
    committee: str | None = None,
    include_archived: bool = False,
    limit: int = Query(100, le=200),
    offset: int = 0,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> list[BoardOut]:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    rows = service.list_boards(
        db, archived=include_archived, committee=committee, limit=limit, offset=offset,
    )
    return [BoardOut.model_validate(r) for r in rows]


@router.post("/boards", response_model=BoardOut, status_code=status.HTTP_201_CREATED)
def create_board(
    body: BoardCreate,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> BoardOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    board = service.create_board(db, body.model_dump(exclude_unset=True))
    return BoardOut.model_validate(board)


@router.get("/boards/{board_id}", response_model=BoardOut)
def get_board(
    board_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> BoardOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    board = service.get_board(db, board_id)
    if board is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "board not found")
    return BoardOut.model_validate(board)


@router.patch("/boards/{board_id}", response_model=BoardOut)
def update_board(
    board_id: int,
    body: BoardUpdate,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> BoardOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    board = service.update_board(db, board_id, body.model_dump(exclude_unset=True))
    if board is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "board not found")
    return BoardOut.model_validate(board)


@router.post("/boards/{board_id}/archive", response_model=BoardOut)
def archive_board(
    board_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> BoardOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    board = service.archive_board(db, board_id)
    if board is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "board not found")
    return BoardOut.model_validate(board)


# -----------------------------------------------------------------------------
# Tasks (cards)
# -----------------------------------------------------------------------------


@router.get("", response_model=list[TaskOut])
def list_tasks(
    request: Request,
    board_id: int | None = None,
    assignee_id: int | None = None,
    status: str | None = None,
    committee: str | None = None,
    include_archived: bool = False,
    limit: int = Query(200, le=500),
    offset: int = 0,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> list[TaskOut]:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    rows = service.list_tasks(
        db, archived=include_archived, board_id=board_id, assignee_id=assignee_id,
        status=status, committee=committee, limit=limit, offset=offset,
    )
    return [TaskOut.model_validate(r) for r in rows]


@router.post("", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
def create_task(
    body: TaskCreate,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> TaskOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    task = service.create_task(db, body.model_dump(exclude_unset=True))
    return TaskOut.model_validate(task)


@router.get("/{task_id}", response_model=TaskOut)
def get_task(
    task_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> TaskOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    task = service.get_task(db, task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "task not found")
    return TaskOut.model_validate(task)


@router.patch("/{task_id}", response_model=TaskOut)
def update_task(
    task_id: int,
    body: TaskUpdate,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> TaskOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    task = service.update_task(db, task_id, body.model_dump(exclude_unset=True))
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "task not found")
    return TaskOut.model_validate(task)


@router.post("/{task_id}/move", response_model=TaskOut)
def move_task(
    task_id: int,
    body: TaskMove,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> TaskOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    task = service.move_task(db, task_id, status=body.status, position=body.position)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "task not found")
    return TaskOut.model_validate(task)


@router.post("/{task_id}/archive", response_model=TaskOut)
def archive_task(
    task_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> TaskOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    task = service.archive_task(db, task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "task not found")
    return TaskOut.model_validate(task)
