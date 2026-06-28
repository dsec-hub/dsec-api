"""Codle — Wordle for code. Server-authoritative.

The SERVER holds the answer (in round.payload, never sent to a client). The
client submits one guess at a time; the engine returns per-letter feedback
(correct / present / absent) and tracks the board. The answer only appears in the
returned detail once the player's own attempt finishes (solved or out of
guesses), which is exactly when Wordle reveals it.

Same daily round across Discord and the portal: the answer is chosen
deterministically from `period_key`, so everyone on a given date plays the same
word and a cross-surface play continues the same ledger.
"""

from __future__ import annotations

import hashlib

from .base import GameEngine
from .wordlist import WORDS

_LENGTH = 5
_MAX_GUESSES = 6
# Stable salt so the daily index is deterministic across processes/restarts but
# not a trivial `date -> word` that a player could precompute without the code.
_SALT = "dsec-codle:v1"


def _score_guess(guess: str, answer: str) -> list[str]:
    """Wordle two-pass marking. Greens first, then yellows bounded by remaining
    letter counts, so duplicate letters are marked correctly."""
    marks = ["absent"] * len(guess)
    # Count answer letters not already consumed by an exact match.
    remaining: dict[str, int] = {}
    for i, ch in enumerate(answer):
        if guess[i] == ch:
            marks[i] = "correct"
        else:
            remaining[ch] = remaining.get(ch, 0) + 1
    for i, ch in enumerate(guess):
        if marks[i] == "correct":
            continue
        if remaining.get(ch, 0) > 0:
            marks[i] = "present"
            remaining[ch] -= 1
    return marks


class CodleEngine(GameEngine):
    slug = "codle"
    name = "Codle"
    surface = "both"
    points_per_day_cap = 60  # a single solve maxes out; can't be farmed
    single_attempt_per_round = True
    nonmember_round_play_cap = 1

    def generate_round(self, period_key: str) -> dict:
        digest = hashlib.sha256(f"{_SALT}:{period_key}".encode()).digest()
        idx = int.from_bytes(digest[:8], "big") % len(WORDS)
        return {
            "answer": WORDS[idx],
            "length": _LENGTH,
            "max_guesses": _MAX_GUESSES,
        }

    def public_round(self, payload: dict) -> dict:
        # NEVER the answer.
        return {
            "length": payload.get("length", _LENGTH),
            "max_guesses": payload.get("max_guesses", _MAX_GUESSES),
        }

    def validate_attempt(
        self,
        round_payload: dict | None,
        submission: dict,
        prior_detail: dict | None = None,
    ) -> dict:
        if not round_payload or "answer" not in round_payload:
            raise ValueError("no active codle round")
        answer = str(round_payload["answer"]).upper()
        length = int(round_payload.get("length", _LENGTH))
        max_guesses = int(round_payload.get("max_guesses", _MAX_GUESSES))

        guess = str(submission.get("guess", "")).strip().upper()
        if len(guess) != length or not guess.isalpha():
            raise ValueError(f"guess must be {length} letters")

        prior = prior_detail or {}
        if prior.get("finished"):
            raise ValueError("today's codle is already complete")

        guesses = list(prior.get("guesses", []))
        marks = _score_guess(guess, answer)
        guesses.append({"guess": guess, "marks": marks})

        solved = guess == answer
        guesses_used = len(guesses)
        finished = solved or guesses_used >= max_guesses
        # Solve in 1 -> 6 raw, solve in 6 -> 1 raw, fail -> 0.
        raw_score = float(max_guesses - guesses_used + 1) if solved else 0.0

        detail = {
            "guesses": guesses,
            "solved": solved,
            "finished": finished,
            "guesses_used": guesses_used,
            "max_guesses": max_guesses,
            "length": length,
        }
        if finished:
            # The board is over for THIS player — fair to reveal the word now
            # (needed for the loss screen and the share card).
            detail["answer"] = answer

        return {
            "valid": True,
            "raw_score": raw_score,
            "detail": detail,
            "finished": finished,
            "solved": solved,
            "feedback": marks,
        }

    def raw_to_points(self, raw_score: float, detail: dict, is_member_play: bool) -> int:
        # 10 points per remaining guess on a solve: solve-in-1 = 60 ... solve-in-6
        # = 10, a loss = 0. Membership-agnostic (the draw, not the points, is the
        # members-only part).
        return int(raw_score) * 10
