"""Generic helpers for the per-entity co-owner join tables (task_owner,
event_owner, project_owner).

Each table is (id, <entity>_id, person_id) with UNIQUE(<entity>_id, person_id).
The entity keeps its single PRIMARY owner column (assignee_id / event_lead_id /
lead_id); these tables hold only the ADDITIONAL owners. Parameterised by the
join model + its entity foreign-key column so one set of helpers serves all
three features.
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session


def list_owner_ids(db: Session, model, fk, entity_id: int) -> list[int]:
    """The co-owner person ids for one entity, in insertion order."""
    return list(
        db.execute(
            select(model.person_id).where(fk == entity_id).order_by(model.id)
        ).scalars().all()
    )


def owner_ids_map(db: Session, model, fk, entity_ids: list[int]) -> dict[int, list[int]]:
    """Batch version of `list_owner_ids` — {entity_id: [person_id, ...]} — so a
    list endpoint resolves every row's co-owners in a single query."""
    if not entity_ids:
        return {}
    out: dict[int, list[int]] = {}
    for eid, pid in db.execute(
        select(fk, model.person_id).where(fk.in_(entity_ids)).order_by(model.id)
    ).all():
        out.setdefault(eid, []).append(pid)
    return out


def set_owners(
    db: Session, model, fk, entity_id: int, person_ids, *, exclude: int | None = None
) -> None:
    """Replace an entity's co-owner set (full PATCH replace). De-dupes, drops
    `exclude` (the primary owner — it lives on the entity row, not here), then
    commits."""
    wanted = [pid for pid in dict.fromkeys(int(p) for p in person_ids) if pid != exclude]
    db.execute(delete(model).where(fk == entity_id))
    db.add_all([model(person_id=pid, **{fk.key: entity_id}) for pid in wanted])
    db.commit()


def attach_owner_ids(db: Session, model, fk, rows) -> None:
    """Set `.co_owner_ids` on each ORM row — a non-mapped instance attribute the
    `*Out` schema reads via from_attributes. Accepts a single row, a list, or
    None (no-ops on None entries)."""
    items = rows if isinstance(rows, list) else [rows]
    items = [r for r in items if r is not None]
    if not items:
        return
    by_id = owner_ids_map(db, model, fk, [r.id for r in items])
    for r in items:
        r.co_owner_ids = by_id.get(r.id, [])
