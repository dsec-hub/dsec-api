"""The monthly draw.

Highest total points among MEMBER plays wins — a skill-based competition, NOT a
random lottery (this matters for the DUSA framing: do not implement a random
draw). Non-members still appear on the public leaderboards, but the gift-card
draw counts only member plays (`is_member_play`).

`close_cycle` is idempotent and is triggered by the monthly cron route.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.logging import log_event
from app.models import DrawCycle, GameAttempt, GamePlayer

_PERIOD_KEY_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def is_valid_period_key(period_key: str) -> bool:
    """True for a well-formed YYYY-MM key with a real month (01-12) whose UTC
    month bounds are actually constructible.

    The regex alone accepts extremes like ``0000-01`` and ``9999-12`` that then
    blow up in ``month_bounds`` (``datetime(0, …)`` / ``datetime(10000, …)`` are
    out of range) — and in ``get_draw`` that raise lands AFTER a DrawCycle row is
    committed, leaving a junk row and a 500. Range-checking here lets both routes
    reject with a 422 before any DB write."""
    if not _PERIOD_KEY_RE.match(period_key or ""):
        return False
    try:
        month_bounds(period_key)
    except (ValueError, OverflowError):
        return False
    return True


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def cycle_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m")


def prev_cycle_key(dt: datetime) -> str:
    year, month = dt.year, dt.month
    if month == 1:
        return f"{year - 1}-12"
    return f"{year}-{month - 1:02d}"


def month_bounds(period_key: str) -> tuple[datetime, datetime]:
    """[start, end) UTC datetimes for a YYYY-MM period key."""
    year, month = (int(p) for p in period_key.split("-"))
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    return start, end


def get_or_create_open_cycle(db: Session, period_key: str) -> DrawCycle:
    cycle = db.execute(
        select(DrawCycle).where(DrawCycle.period_key == period_key)
    ).scalars().first()
    if cycle is None:
        cycle = DrawCycle(period_key=period_key, status="open")
        db.add(cycle)
        db.commit()
        db.refresh(cycle)
    return cycle


def standings(
    db: Session,
    period_key: str,
    *,
    members_only: bool = True,
    limit: int | None = None,
) -> list[dict]:
    """Ranked total points per player for the cycle (draw is members_only)."""
    start, end = month_bounds(period_key)
    stmt = (
        select(
            GameAttempt.player_id,
            func.coalesce(func.sum(GameAttempt.points), 0).label("pts"),
        )
        .where(GameAttempt.played_at >= start, GameAttempt.played_at < end)
        .group_by(GameAttempt.player_id)
        .having(func.coalesce(func.sum(GameAttempt.points), 0) > 0)
        .order_by(func.coalesce(func.sum(GameAttempt.points), 0).desc(), GameAttempt.player_id.asc())
    )
    if members_only:
        stmt = stmt.where(GameAttempt.is_member_play.is_(True))
    if limit:
        stmt = stmt.limit(limit)
    rows = db.execute(stmt).all()
    player_ids = [r.player_id for r in rows]
    players = (
        {
            p.id: p
            for p in db.execute(
                select(GamePlayer).where(GamePlayer.id.in_(player_ids))
            ).scalars().all()
        }
        if player_ids
        else {}
    )
    out = []
    for rank, row in enumerate(rows, 1):
        p = players.get(row.player_id)
        out.append(
            {
                "rank": rank,
                "player_id": row.player_id,
                "account_id": p.account_id if p else None,
                "display_name": (p.display_name if p and p.display_name else None) or "Anonymous Duck",
                "points": int(row.pts or 0),
            }
        )
    return out


def roll_up_cycle(db: Session, period_key: str) -> dict:
    """Sum + rank points for the cycle; the top member total is the winner."""
    ranked = standings(db, period_key, members_only=True)
    winner = ranked[0] if ranked else None
    return {"period_key": period_key, "winner": winner, "standings": ranked}


def close_cycle(db: Session, period_key: str, *, force: bool = False) -> DrawCycle:
    """Pick the winner (highest member points), mark closed, open the next cycle.

    Idempotent: a cycle already closed is returned unchanged. Refuses (ValueError)
    to close a period that has not finished yet unless ``force`` is set — closing
    a live month would award the real gift card on a partial roll-up and there is
    no reopen path. The clock is checked BEFORE any row is created, so a refused
    close leaves nothing behind.
    """
    # Already-closed cycles return unchanged (idempotent) — including one that was
    # force-closed early and is later re-run by the monthly cron.
    existing = db.execute(
        select(DrawCycle).where(DrawCycle.period_key == period_key)
    ).scalars().first()
    if existing is not None and existing.status == "closed":
        return existing

    _, end = month_bounds(period_key)
    if not force and _utcnow() < end:
        raise ValueError(
            f"period {period_key} has not ended yet (ends {end.date().isoformat()}); "
            "pass force=true to close it early"
        )

    cycle = get_or_create_open_cycle(db, period_key)
    rolled = roll_up_cycle(db, period_key)
    winner = rolled["winner"]
    cycle.status = "closed"
    cycle.closed_at = _utcnow()
    cycle.winner_player_id = winner["player_id"] if winner else None
    cycle.total_points_snapshot = {
        "winner": winner,
        "standings": rolled["standings"][:25],  # keep the snapshot bounded
    }
    db.commit()
    db.refresh(cycle)
    # Make sure the next month's cycle is open and ready to accumulate.
    year, month = (int(p) for p in period_key.split("-"))
    nxt = f"{year + 1}-01" if month == 12 else f"{year}-{month + 1:02d}"
    get_or_create_open_cycle(db, nxt)
    # Audit the close so a disputed draw can be reconstructed.
    log_event(
        db, source="games", action="draw_closed", external_id=cycle.period_key,
        payload={"winner_player_id": cycle.winner_player_id, "forced": force},
    )
    return cycle
