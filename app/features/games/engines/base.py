"""The game engine interface — the extensibility seam.

Every game is ONE class implementing `GameEngine`. Adding a game later = a new
file in this package + one line in `engines/__init__.py` REGISTRY. Nothing else
in the platform changes.

The engine is the brain's per-game logic: it builds the deterministic daily
round, validates a submission (rejecting bad / impossible input), and converts
its own native `raw_score` into normalised, capped `points`. It NEVER touches the
database, the request, or the player's identity — the service layer owns all of
that. Keeping engines pure makes them trivially unit-testable.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

Surface = Literal["discord", "portal", "both"]


class GameEngine(ABC):
    """Per-game logic. Pure: no DB, no IO, no identity."""

    slug: str
    name: str
    surface: Surface

    # --- Cross-game fairness ------------------------------------------------
    # The most points one player can earn from THIS game in a single UTC day.
    # `submit_attempt` clamps awarded points to what remains under this cap, so
    # no single game can be farmed to dominate the points-based monthly draw.
    points_per_day_cap: int = 50

    # --- Round / play shape -------------------------------------------------
    # True if every round permits exactly ONE attempt per player (the row is
    # continued across turns, e.g. Codle's guesses). False for a replayable
    # arcade round (e.g. Flappy Duck) where each play is its own attempt row.
    single_attempt_per_round: bool = True
    # How many attempts a NON-member may make per round (the taste/hook). Members
    # are unlimited on replayable games. Ignored when single_attempt_per_round
    # (everyone gets exactly one attempt then).
    nonmember_round_play_cap: int = 1

    # True if the game's score is computed client-side and must be bound to a
    # short-lived server-signed play session (anti-replay + wall-clock check). The
    # service issues the token on `round` and verifies it on `attempt`. Declared
    # here so the service branches on a CAPABILITY, not a game name — keeping the
    # "new game = one file + one registry line" contract intact.
    requires_session: bool = False

    @abstractmethod
    def generate_round(self, period_key: str) -> dict:
        """Build `round.payload` deterministically for `period_key`.

        Same `period_key` MUST always yield the same payload so a Discord play
        and a portal play on the same day share one puzzle. The payload holds the
        FULL puzzle including any answer; it is SERVER-ONLY and is never returned
        to a client verbatim.
        """

    @abstractmethod
    def public_round(self, payload: dict) -> dict:
        """The safe subset of a round payload to hand a client.

        For an answer-bearing game (Codle) this MUST omit the answer — return
        only what the client needs to render (e.g. word length, max guesses).
        """

    @abstractmethod
    def validate_attempt(
        self,
        round_payload: dict | None,
        submission: dict,
        prior_detail: dict | None = None,
    ) -> dict:
        """Validate one submission against the round.

        Returns a dict with at least:
            valid: bool
            raw_score: float          (game-native units)
            detail: dict              (board / guesses / run stats; client-safe)
            finished: bool            (is this attempt complete?)
        plus any game-specific feedback keys.

        `prior_detail` carries a stateful game's progress so far (e.g. Codle's
        previous guesses); it is None for a fresh attempt or a stateless game.

        Raises ValueError on malformed input or an impossible/cheating score.
        """

    @abstractmethod
    def raw_to_points(self, raw_score: float, detail: dict, is_member_play: bool) -> int:
        """Convert native `raw_score` to normalised points (>= 0).

        The daily cap is applied by the caller, not here. `is_member_play` is
        provided for engines that want to weight it, but the default policy is
        membership-agnostic points (the gift-card draw — not the points — is the
        members-only part).
        """
