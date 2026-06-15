"""Project repository functions — pure, Session-based, reused by REST + MCP.

Convention shared by every workspace feature:
    list_<x>(db, *, archived=False, limit, offset, **filters) -> list[Model]
    get_<x>(db, id) -> Model | None
    create_<x>(db, data: dict) -> Model
    update_<x>(db, id, data: dict) -> Model | None        (PATCH; only given keys)
    archive_<x>(db, id) -> Model | None                   (soft delete)
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Project


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug or "project"


def _unique_slug(db: Session, base: str) -> str:
    slug, n = base, 2
    while db.execute(select(Project.id).where(Project.slug == slug)).first() is not None:
        slug = f"{base}-{n}"
        n += 1
    return slug


def list_projects(
    db: Session,
    *,
    archived: bool = False,
    is_public: bool | None = None,
    featured: bool | None = None,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Project]:
    stmt = select(Project)
    if not archived:
        stmt = stmt.where(Project.archived.is_(False))
    if is_public is not None:
        stmt = stmt.where(Project.is_public.is_(is_public))
    if featured is not None:
        stmt = stmt.where(Project.featured.is_(featured))
    if status:
        stmt = stmt.where(Project.status == status)
    stmt = (
        stmt.order_by(Project.featured.desc(), Project.updated_at.desc())
        .limit(min(limit, 200))
        .offset(offset)
    )
    return list(db.execute(stmt).scalars().all())


def get_project(db: Session, project_id: int) -> Project | None:
    return db.get(Project, project_id)


def create_project(db: Session, data: dict) -> Project:
    data = dict(data)
    if not data.get("slug"):
        data["slug"] = _unique_slug(db, _slugify(data.get("name", "")))
    proj = Project(**data)
    db.add(proj)
    db.commit()
    db.refresh(proj)
    return proj


def update_project(db: Session, project_id: int, data: dict) -> Project | None:
    proj = db.get(Project, project_id)
    if proj is None:
        return None
    for key, value in data.items():
        setattr(proj, key, value)
    db.commit()
    db.refresh(proj)
    return proj


def archive_project(db: Session, project_id: int) -> Project | None:
    proj = db.get(Project, project_id)
    if proj is None:
        return None
    proj.archived = True
    db.commit()
    db.refresh(proj)
    return proj
