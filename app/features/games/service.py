"""Games service — the brain.

The API decides every score, every point, every leaderboard position and the
monthly draw. Clients (the games site, the Discord bot) only render and submit;
nothing client-side computes points or decides a winner. A Discord play and a
portal play on the same day write to the SAME round and roll into the SAME
monthly points total, keyed to the student account.

Pure, Session-based functions reused by both REST (router) and the in-process
Discord interaction handler.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.features.game_link import service as link_service
from app.models import Game, GameAttempt, GamePlayer, GameRound

from . import draws, scoring, sessions
from .engines import REGISTRY, get_engine


class GameError(Exception):
    """A submit/round error the router maps to an HTTP status."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _day_key(now: datetime) -> str:
    return now.strftime("%Y-%m-%d")


def _start_of_utc_day(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


# -----------------------------------------------------------------------------
# Game registry (rows mirror the engine REGISTRY; created lazily + idempotently)
# -----------------------------------------------------------------------------


def ensure_games(db: Session) -> list[Game]:
    """Upsert a `game` row per registered engine. Idempotent."""
    existing = {
        g.slug: g for g in db.execute(select(Game)).scalars().all()
    }
    created = False
    for slug, engine in REGISTRY.items():
        if slug not in existing:
            db.add(Game(slug=slug, name=engine.name, surface=engine.surface, active=True))
            created = True
    if created:
        db.commit()
    return list(
        db.execute(
            select(Game).where(Game.archived.is_(False)).order_by(Game.id)
        ).scalars().all()
    )


def list_games(db: Session) -> list[Game]:
    return [g for g in ensure_games(db) if g.active]


def get_game(db: Session, slug: str) -> Game | None:
    ensure_games(db)
    return db.execute(select(Game).where(Game.slug == slug)).scalars().first()


# -----------------------------------------------------------------------------
# Rounds (the shared daily puzzle)
# -----------------------------------------------------------------------------


def get_or_create_today_round(db: Session, game: Game, engine, now: datetime) -> GameRound:
    period_key = _day_key(now)
    existing = db.execute(
        select(GameRound).where(
            GameRound.game_id == game.id, GameRound.period_key == period_key
        )
    ).scalars().first()
    if existing is not None:
        return existing
    opens = _start_of_utc_day(now)
    round_ = GameRound(
        game_id=game.id,
        period_key=period_key,
        payload=engine.generate_round(period_key),
        opens_at=opens,
        closes_at=opens + timedelta(days=1),
    )
    db.add(round_)
    try:
        db.commit()
    except IntegrityError:
        # Lost a race to create the same (game, period_key) — use the winner.
        db.rollback()
        return db.execute(
            select(GameRound).where(
                GameRound.game_id == game.id, GameRound.period_key == period_key
            )
        ).scalars().first()
    db.refresh(round_)
    return round_


def game_state(db: Session, *, slug: str, account_id: int, now: datetime | None = None) -> dict:
    """A player's resumable state for any game (e.g. Codle's board so far), so a
    client can resume after a refresh.

    Engine-agnostic: it returns the public round shape plus the player's latest
    attempt `detail`, which the engine itself builds client-safe (Codle only puts
    the answer into detail once the player's own attempt has finished). A new
    stateful game flows through here with NO router/service edits.
    """
    now = now or _utcnow()
    engine = get_engine(slug)
    game = get_game(db, slug)
    if engine is None or game is None:
        return {"started": False}
    round_ = get_or_create_today_round(db, game, engine, now)
    state = {
        "game": slug,
        "period_key": round_.period_key,
        "started": False,
        **engine.public_round(round_.payload or {}),
    }
    player = link_service.get_player_by_account(db, account_id)
    if player is None:
        return state
    attempt = _latest_attempt(db, game.id, round_.id, player.id)
    if attempt is None:
        return state
    # detail is the engine's own client-safe attempt shape (board/run stats).
    state.update({"started": True, "points": attempt.points, **(attempt.detail or {})})
    return state


def codle_state(db: Session, *, account_id: int, now: datetime | None = None) -> dict:
    """Codle board for a player — thin alias the Discord bot uses to render it."""
    return game_state(db, slug="codle", account_id=account_id, now=now)


def public_round(db: Session, slug: str, *, now: datetime | None = None) -> dict | None:
    """Today's round for a client — public payload only (never the answer)."""
    now = now or _utcnow()
    engine = get_engine(slug)
    game = get_game(db, slug)
    if engine is None or game is None:
        return None
    round_ = get_or_create_today_round(db, game, engine, now)
    out = {
        "game": slug,
        "name": engine.name,
        "period_key": round_.period_key,
        "round_id": round_.id,
        **engine.public_round(round_.payload or {}),
    }
    # Client-scored games bind each play to a short-lived server-signed session.
    if engine.requires_session:
        out["session"] = sessions.sign_session(
            seed=int((round_.payload or {}).get("seed", 0)),
            issued_at_epoch=int(now.timestamp()),
        )
    return out


# -----------------------------------------------------------------------------
# Submitting a play — the single write path for ALL surfaces
# -----------------------------------------------------------------------------


def _latest_attempt(db: Session, game_id: int, round_id: int, player_id: int) -> GameAttempt | None:
    return db.execute(
        select(GameAttempt)
        .where(
            GameAttempt.game_id == game_id,
            GameAttempt.round_id == round_id,
            GameAttempt.player_id == player_id,
        )
        .order_by(GameAttempt.id.desc())
    ).scalars().first()


def _serialize_daily_writes(db: Session, *, game_id: int, player_id: int, now: datetime) -> None:
    """Serialize a player's daily read-modify-write for a game so the per-day
    points cap and the non-member play cap can't be beaten by concurrent submits
    (the cap is check-then-act: read today's total, then insert).

    Postgres: a transaction-scoped advisory lock keyed on (player, game, day),
    auto-released at commit/rollback — so overlapping submits for the same player
    on the same day run one at a time and each re-reads the updated total. No-op
    on SQLite (single-writer; tests run sequentially) and any non-Postgres backend.
    """
    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        return
    # Stable, deterministic, positive 62-bit key (pg takes a signed bigint).
    day_ord = now.toordinal()
    key = ((player_id & 0x7FFFFFF) << 35) | ((game_id & 0x3FF) << 25) | (day_ord & 0x1FFFFFF)
    db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": key})


def _attempt_count(db: Session, game_id: int, round_id: int, player_id: int) -> int:
    return int(
        db.execute(
            select(func.count(GameAttempt.id)).where(
                GameAttempt.game_id == game_id,
                GameAttempt.round_id == round_id,
                GameAttempt.player_id == player_id,
            )
        ).scalar_one()
    )


def _verify_play_session(submission: dict, round_: GameRound, now: datetime) -> None:
    token = submission.get("session")
    if not token:
        raise GameError("missing play session token", 400)
    try:
        sess = sessions.verify_session(
            token, now_epoch=int(now.timestamp()), ttl=settings.GAMES_SESSION_TTL
        )
    except ValueError as exc:
        raise GameError(f"invalid play session: {exc}", 400) from exc
    if round_.payload and sess["seed"] != int(round_.payload.get("seed", -1)):
        raise GameError("play session does not match today's round", 400)
    # Wall-clock guard: a run can't have lasted longer than the real time elapsed
    # since the session was issued (2s slack for latency).
    elapsed_ms = (int(now.timestamp()) - sess["issued_at"]) * 1000
    duration = submission.get("duration_ms")
    if isinstance(duration, int) and duration > elapsed_ms + 2000:
        raise GameError("reported run longer than elapsed time", 400)


def submit_attempt(
    db: Session,
    *,
    slug: str,
    account_id: int,
    email: str | None = None,
    display_name: str | None = None,
    submission: dict,
    surface: str | None = None,
    now: datetime | None = None,
) -> dict:
    """Resolve membership, validate via the engine, compute capped points, write
    the attempt, and return the OFFICIAL result. The only place a play is scored.
    """
    now = now or _utcnow()
    engine = get_engine(slug)
    game = get_game(db, slug)
    if engine is None or game is None:
        raise GameError("unknown game", 404)

    player = link_service.upsert_player(
        db, account_id=account_id, email=email, display_name=display_name
    )
    is_member = link_service.is_current_member(db, email or player.email)
    round_ = get_or_create_today_round(db, game, engine, now)

    if engine.requires_session:
        _verify_play_session(submission, round_, now)

    # Serialize this player's daily writes for this game so the cap checks below
    # are atomic against concurrent submits (held until the commit inside the
    # _submit_* helpers releases it).
    _serialize_daily_writes(db, game_id=game.id, player_id=player.id, now=now)

    try:
        if engine.single_attempt_per_round:
            attempt = _submit_single(db, engine, game, round_, player, submission, is_member, surface, now)
        else:
            attempt = _submit_replayable(db, engine, game, round_, player, submission, is_member, surface, now)
    except ValueError as exc:
        # Engine rejected the submission (bad/impossible input).
        raise GameError(str(exc), 422) from exc

    detail = attempt.detail or {}
    return {
        "game": slug,
        "raw_score": attempt.raw_score,
        "points": attempt.points,
        "detail": detail,
        "finished": bool(detail.get("finished", True)),
        "solved": detail.get("solved"),
        "is_member_play": attempt.is_member_play,
        "leaderboard_position": leaderboard_position(
            db, player_id=player.id, window="cycle", now=now
        ),
        "attempt_id": attempt.id,
    }


def _submit_single(db, engine, game, round_, player, submission, is_member, surface, now) -> GameAttempt:
    """Single-attempt-per-round games (Codle): one row, continued across turns."""
    existing = _latest_attempt(db, game.id, round_.id, player.id)
    if existing is not None and (existing.detail or {}).get("finished"):
        raise GameError("you've already played today's round", 409)
    prior = existing.detail if existing else None
    result = engine.validate_attempt(round_.payload, submission, prior)
    attempt = existing or GameAttempt(
        game_id=game.id, round_id=round_.id, player_id=player.id, surface=surface
    )
    attempt.raw_score = result["raw_score"]
    attempt.detail = result["detail"]
    attempt.is_member_play = is_member
    if surface:
        attempt.surface = surface
    if result["finished"]:
        attempt.points = scoring.award_points(
            db,
            engine=engine,
            game_id=game.id,
            player_id=player.id,
            raw_score=result["raw_score"],
            detail=result["detail"],
            is_member_play=is_member,
            now=now,
        )
    else:
        attempt.points = 0
    if existing is None:
        db.add(attempt)
    db.commit()
    db.refresh(attempt)
    return attempt


def _submit_replayable(db, engine, game, round_, player, submission, is_member, surface, now) -> GameAttempt:
    """Replayable arcade (Flappy Duck): a new row per play; non-members capped."""
    if not is_member:
        played = _attempt_count(db, game.id, round_.id, player.id)
        if played >= engine.nonmember_round_play_cap:
            raise GameError(
                "non-members get one play per day — become a member for unlimited plays",
                403,
            )
    result = engine.validate_attempt(round_.payload, submission, None)
    points = scoring.award_points(
        db,
        engine=engine,
        game_id=game.id,
        player_id=player.id,
        raw_score=result["raw_score"],
        detail=result["detail"],
        is_member_play=is_member,
        now=now,
    )
    attempt = GameAttempt(
        game_id=game.id,
        round_id=round_.id,
        player_id=player.id,
        raw_score=result["raw_score"],
        points=points,
        detail=result["detail"],
        is_member_play=is_member,
        surface=surface,
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    return attempt


# -----------------------------------------------------------------------------
# Leaderboards (daily / weekly / cycle are three time-window queries, not tables)
# -----------------------------------------------------------------------------


def _window_start(window: str, now: datetime) -> datetime:
    if window == "daily":
        return _start_of_utc_day(now)
    if window == "weekly":
        monday = now - timedelta(days=now.weekday())
        return _start_of_utc_day(monday)
    if window == "cycle":
        start, _ = draws.month_bounds(draws.cycle_key(now))
        return start
    raise GameError("window must be daily, weekly or cycle", 400)


def leaderboard(
    db: Session,
    *,
    game_slug: str | None = None,
    window: str = "daily",
    limit: int = 20,
    now: datetime | None = None,
) -> list[dict]:
    now = now or _utcnow()
    start = _window_start(window, now)
    stmt = (
        select(
            GameAttempt.player_id,
            func.coalesce(func.sum(GameAttempt.points), 0).label("pts"),
        )
        .where(GameAttempt.played_at >= start)
        .group_by(GameAttempt.player_id)
        .having(func.coalesce(func.sum(GameAttempt.points), 0) > 0)
        .order_by(func.coalesce(func.sum(GameAttempt.points), 0).desc(), GameAttempt.player_id.asc())
        .limit(limit)
    )
    if game_slug:
        game = get_game(db, game_slug)
        if game is None:
            return []
        stmt = stmt.where(GameAttempt.game_id == game.id)
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


def leaderboard_position(
    db: Session,
    *,
    player_id: int,
    window: str = "cycle",
    game_id: int | None = None,
    now: datetime | None = None,
) -> int | None:
    """1-based rank of a player within a window, by summed points (None if they
    have no points yet)."""
    now = now or _utcnow()
    start = _window_start(window, now)
    base = (
        select(
            GameAttempt.player_id.label("pid"),
            func.coalesce(func.sum(GameAttempt.points), 0).label("pts"),
        )
        .where(GameAttempt.played_at >= start)
    )
    if game_id:
        base = base.where(GameAttempt.game_id == game_id)
    base = base.group_by(GameAttempt.player_id).subquery()
    mine = db.execute(select(base.c.pts).where(base.c.pid == player_id)).scalar()
    if mine is None or mine <= 0:
        return None
    higher = db.execute(
        select(func.count()).select_from(base).where(base.c.pts > mine)
    ).scalar_one()
    return int(higher) + 1


# -----------------------------------------------------------------------------
# A player's own summary (attempts/points this cycle + streak)
# -----------------------------------------------------------------------------


def _streak_days(db: Session, player_id: int, now: datetime) -> int:
    rows = db.execute(
        select(GameAttempt.played_at).where(GameAttempt.player_id == player_id)
    ).scalars().all()
    played_days = {dt.date() for dt in rows if dt is not None}
    streak = 0
    day = now.date()
    while day in played_days:
        streak += 1
        day = day - timedelta(days=1)
    return streak


def player_summary(db: Session, *, account_id: int, now: datetime | None = None) -> dict | None:
    now = now or _utcnow()
    player = link_service.get_player_by_account(db, account_id)
    if player is None:
        return None
    cycle_start, cycle_end = draws.month_bounds(draws.cycle_key(now))
    attempts = list(
        db.execute(
            select(GameAttempt)
            .where(
                GameAttempt.player_id == player.id,
                GameAttempt.played_at >= cycle_start,
                GameAttempt.played_at < cycle_end,
            )
            .order_by(GameAttempt.played_at.desc())
        ).scalars().all()
    )
    slugs = {g.id: g.slug for g in db.execute(select(Game)).scalars().all()}
    by_game: dict[str, int] = {}
    for a in attempts:
        key = slugs.get(a.game_id, str(a.game_id))
        by_game[key] = by_game.get(key, 0) + a.points
    return {
        "account_id": account_id,
        "player_id": player.id,
        "display_name": player.display_name,
        "cycle_period": draws.cycle_key(now),
        "cycle_points": sum(a.points for a in attempts),
        "cycle_position": leaderboard_position(db, player_id=player.id, window="cycle", now=now),
        "streak_days": _streak_days(db, player.id, now),
        "attempts_this_cycle": len(attempts),
        "by_game": by_game,
    }
