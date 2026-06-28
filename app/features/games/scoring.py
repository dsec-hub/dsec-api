"""Cross-game fairness: native raw_score -> normalised, capped points.

Each engine emits raw_score in its OWN units and converts it to points via
`raw_to_points`. ONLY points accumulate toward the monthly draw. This module
applies the per-game DAILY cap so no single game can be farmed to dominate the
overall standings: a play earns at most what remains under the game's
`points_per_day_cap` for that player today (UTC).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import GameAttempt

from .engines import GameEngine


def _start_of_utc_day(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def points_today(db: Session, *, game_id: int, player_id: int, now: datetime) -> int:
    """Points this player has already banked from this game today (UTC)."""
    total = db.execute(
        select(func.coalesce(func.sum(GameAttempt.points), 0)).where(
            GameAttempt.game_id == game_id,
            GameAttempt.player_id == player_id,
            GameAttempt.played_at >= _start_of_utc_day(now),
        )
    ).scalar_one()
    return int(total or 0)


def award_points(
    db: Session,
    *,
    engine: GameEngine,
    game_id: int,
    player_id: int,
    raw_score: float,
    detail: dict,
    is_member_play: bool,
    now: datetime,
) -> int:
    """Engine points for this play, clamped to what remains under the daily cap.

    Call this BEFORE the attempt row's points are written (so today's running
    total doesn't already include this play). For the stateful game the row
    exists with 0 points during play, so it doesn't double-count either.
    """
    base = max(0, engine.raw_to_points(raw_score, detail, is_member_play))
    already = points_today(db, game_id=game_id, player_id=player_id, now=now)
    remaining = max(0, engine.points_per_day_cap - already)
    return min(base, remaining)
