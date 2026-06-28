"""Pydantic models for the Discord <-> account link feature."""

from __future__ import annotations

from pydantic import BaseModel


class LinkStartRequest(BaseModel):
    """Portal-initiated: ensure a player exists and hand back their link code."""

    account_id: int
    email: str | None = None
    display_name: str | None = None


class LinkClaimRequest(BaseModel):
    """Discord-initiated: bind a Discord user to the account a code derives from."""

    discord_user_id: str
    code: str
