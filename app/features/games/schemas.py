"""Pydantic models for the games feature.

Round/attempt responses are heterogeneous by game (Codle board vs Flappy run), so
those endpoints return plain dicts the service builds. Only the stable shapes get
schemas here: the game registry row and the attempt request body.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class GameOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    name: str
    surface: str  # discord|portal|both
    active: bool


class AttemptRequest(BaseModel):
    """A play submission. `submission` is the game-native payload the engine
    validates (Codle: {"guess": "PRINT"}; Flappy: {"session","score","duration_ms","flaps"}).

    `account_id` is the portal (student) account id — the identity the server
    keys the attempt to. The trusted service-key caller (games site / bot) is
    responsible for having authenticated that account; the API does NOT trust any
    client-supplied score, only the identity.
    """

    account_id: int
    email: str | None = None
    display_name: str | None = None
    submission: dict[str, Any]
    surface: str | None = None  # portal|discord
