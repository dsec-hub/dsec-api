"""Flappy Duck — the arcade anchor. The DSEC duck dodges pipes; score = pipes
passed. The score is computed on the CLIENT (a canvas game), so it is inherently
spoofable. We mitigate, we do not pretend to prevent:

  * The round carries a daily seed so every player gets the same pipe layout.
  * Each play is bound to a short-lived server-signed session token issued at
    round time (verified in the service layer) so a recorded score can't be
    replayed indefinitely, and real wall-clock time must cover the reported run.
  * The submission carries a gameplay digest (duration + flap count). This engine
    rejects internally-impossible runs: a score-per-second over the ceiling, a
    run too short for its score, or far too few inputs for the pipes passed.
  * Points are CAPPED per day (see points_per_day_cap), so even a score that
    slips through can't dominate the points-based monthly draw.

This is acceptable because the draw is points-based and capped, not raw-score
based. The honest limitation is documented here on purpose.
"""

from __future__ import annotations

import hashlib

from .base import GameEngine

# --- Anti-cheat heuristics (sanity bounds, not proofs) ----------------------
_MAX_SCORE = 2000  # absolute sane ceiling for pipes passed in one run
_MIN_MS_PER_PIPE = 600  # a pipe can't arrive faster than this -> caps pts/sec ~1.66
_MIN_FLAPS_RATIO = 0.5  # need at least ~1 flap per 2 pipes; fewer = not real play


class FlappyDuckEngine(GameEngine):
    slug = "flappy-duck"
    name = "Flappy Duck"
    surface = "portal"  # can't run inside a Discord message; portal only
    points_per_day_cap = 50  # caps a spoofed score's reach into the draw
    single_attempt_per_round = False  # replayable arcade
    nonmember_round_play_cap = 1  # non-members get one taste play per day
    requires_session = True  # client-scored: bind each play to a signed session

    def generate_round(self, period_key: str) -> dict:
        digest = hashlib.sha256(f"dsec-flappy:v1:{period_key}".encode()).digest()
        seed = int.from_bytes(digest[:6], "big")
        return {
            "seed": seed,
            "gravity": 0.4,
            "pipe_gap": 180,
            "pipe_spacing": 260,
            "speed": 2.2,
        }

    def public_round(self, payload: dict) -> dict:
        # Nothing secret in a Flappy round — the seed is meant to be shared so
        # everyone plays the same layout.
        return {
            "seed": payload.get("seed"),
            "gravity": payload.get("gravity"),
            "pipe_gap": payload.get("pipe_gap"),
            "pipe_spacing": payload.get("pipe_spacing"),
            "speed": payload.get("speed"),
        }

    def validate_attempt(
        self,
        round_payload: dict | None,
        submission: dict,
        prior_detail: dict | None = None,
    ) -> dict:
        score = submission.get("score")
        duration_ms = submission.get("duration_ms")
        flaps = submission.get("flaps")

        if not isinstance(score, int) or isinstance(score, bool) or score < 0:
            raise ValueError("score must be a non-negative integer")
        if not isinstance(duration_ms, int) or isinstance(duration_ms, bool) or duration_ms <= 0:
            raise ValueError("duration_ms must be a positive integer")
        if not isinstance(flaps, int) or isinstance(flaps, bool) or flaps < 0:
            raise ValueError("flaps must be a non-negative integer")

        if score > _MAX_SCORE:
            raise ValueError("score exceeds the sane ceiling")
        # A run must be long enough to have passed that many pipes.
        if score > 0 and duration_ms < score * _MIN_MS_PER_PIPE:
            raise ValueError("run too short for its score")
        # You have to flap to fly — too few inputs for the pipes passed is a bot.
        if score > 0 and flaps < score * _MIN_FLAPS_RATIO:
            raise ValueError("too few inputs for the score")

        detail = {
            "score": score,
            "duration_ms": duration_ms,
            "flaps": flaps,
            "seed": (round_payload or {}).get("seed"),
        }
        return {
            "valid": True,
            "raw_score": float(score),
            "detail": detail,
            "finished": True,
        }

    def raw_to_points(self, raw_score: float, detail: dict, is_member_play: bool) -> int:
        # One point per pipe passed; the daily cap (applied by the caller) is the
        # real ceiling on a game's contribution to the draw.
        return int(raw_score)
