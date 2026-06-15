"""Sponsor package repository."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SponsorPackage


def list_packages(
    db: Session,
    *,
    visible_only: bool = False,
) -> list[SponsorPackage]:
    stmt = select(SponsorPackage)
    if visible_only:
        stmt = stmt.where(SponsorPackage.is_visible.is_(True))
    stmt = stmt.order_by(SponsorPackage.display_order.asc(), SponsorPackage.id.asc())
    return list(db.execute(stmt).scalars().all())


def get_package(db: Session, package_id: int) -> SponsorPackage | None:
    return db.get(SponsorPackage, package_id)


def create_package(db: Session, data: dict) -> SponsorPackage:
    pkg = SponsorPackage(**data)
    db.add(pkg)
    db.commit()
    db.refresh(pkg)
    return pkg


def update_package(db: Session, package_id: int, data: dict) -> SponsorPackage | None:
    pkg = db.get(SponsorPackage, package_id)
    if pkg is None:
        return None
    for key, value in data.items():
        setattr(pkg, key, value)
    db.commit()
    db.refresh(pkg)
    return pkg


def delete_package(db: Session, package_id: int) -> bool:
    pkg = db.get(SponsorPackage, package_id)
    if pkg is None:
        return False
    db.delete(pkg)
    db.commit()
    return True
