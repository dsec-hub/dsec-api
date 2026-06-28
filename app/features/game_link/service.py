"""Player identity, Discord links, and membership resolution.

`GamePlayer` is the engine's identity row — one per portal (student) account.
Both surfaces resolve to it: the portal passes `account_id` directly; Discord
resolves `discord_user_id` through `game_account_link`. So a Discord play and a
portal play key to the SAME player and the SAME points ledger.

Membership ("is this a current DUSA member?") is resolved against the read-only
members roster (the weekly DUSA import is the oracle) — never a second notion of
member.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import GameAccountLink, GamePlayer, Member

from . import verification


def _clean_email(email: str | None) -> str | None:
    return email.strip().lower() if email else None


def get_player(db: Session, player_id: int) -> GamePlayer | None:
    return db.get(GamePlayer, player_id)


def get_player_by_account(db: Session, account_id: int) -> GamePlayer | None:
    return db.execute(
        select(GamePlayer).where(GamePlayer.account_id == account_id)
    ).scalars().first()


def get_player_by_discord(db: Session, discord_user_id: str) -> GamePlayer | None:
    link = db.execute(
        select(GameAccountLink).where(GameAccountLink.discord_user_id == str(discord_user_id))
    ).scalars().first()
    if link is None:
        return None
    return db.get(GamePlayer, link.player_id)


def list_players(db: Session) -> list[GamePlayer]:
    return list(db.execute(select(GamePlayer)).scalars().all())


def discord_for_player(db: Session, player_id: int) -> str | None:
    """The Discord id bound to a player, or None if not linked."""
    return db.execute(
        select(GameAccountLink.discord_user_id).where(GameAccountLink.player_id == player_id)
    ).scalars().first()


def upsert_player(
    db: Session,
    *,
    account_id: int,
    email: str | None = None,
    display_name: str | None = None,
) -> GamePlayer:
    """Find-or-create the player for a portal account; refresh email/name."""
    email = _clean_email(email)
    player = get_player_by_account(db, account_id)
    if player is None:
        player = GamePlayer(account_id=account_id, email=email, display_name=display_name)
        db.add(player)
        db.commit()
        db.refresh(player)
        return player
    changed = False
    if email and player.email != email:
        player.email = email
        changed = True
    if display_name and player.display_name != display_name:
        player.display_name = display_name
        changed = True
    if changed:
        db.commit()
        db.refresh(player)
    return player


def link_discord(db: Session, *, discord_user_id: str, code: str) -> GamePlayer | None:
    """Claim a link code: bind `discord_user_id` to the player the code derives
    from. Returns the player, or None if the code matches no player."""
    discord_user_id = str(discord_user_id)
    player = verification.find_player_by_link_code(list_players(db), code)
    if player is None:
        return None
    existing = db.execute(
        select(GameAccountLink).where(GameAccountLink.discord_user_id == discord_user_id)
    ).scalars().first()
    if existing is None:
        db.add(GameAccountLink(player_id=player.id, discord_user_id=discord_user_id))
        db.commit()
    elif existing.player_id != player.id:
        existing.player_id = player.id  # re-bind to a different account
        db.commit()
    return player


def is_current_member(db: Session, email: str | None) -> bool:
    """True when `email` matches a current member on the DUSA roster (the oracle).

    A current member (`is_current`) plays unlimited and counts toward the draw.
    """
    email = _clean_email(email)
    if not email:
        return False
    row = db.execute(
        select(Member.id).where(
            func.lower(Member.email) == email,
            Member.is_current.is_(True),
        )
    ).scalars().first()
    return row is not None
