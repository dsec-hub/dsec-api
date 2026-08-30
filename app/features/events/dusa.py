"""DUSA submission-status vocabulary — the single source of truth in dsec-api.

The five allowed values mirror the committee dashboard's ``DUSA_STATUSES``; the
dashboard's kanban reads ``Event.dusa_submission_status`` straight from Neon, so
a write here (from the email decision-maker or a REST/MCP update) is all the
board needs. The column itself is a free-form ``String(64)`` — this module is
what keeps an agent- or human-supplied value inside the vocabulary the board
understands.
"""

from __future__ import annotations

# Ordered roughly along the submission lifecycle. This is the canonical casing.
DUSA_STATUSES: tuple[str, ...] = (
    "Not Started",
    "Submitted",
    "Approved",
    "Rejected",
    "Not Required",
)

# Lower-cased lookup for tolerant matching of agent/human input.
_CANONICAL_BY_LOWER = {s.lower(): s for s in DUSA_STATUSES}


def is_valid_status(value: str) -> bool:
    """True if ``value`` (case-insensitively) names a canonical DUSA status."""
    return isinstance(value, str) and value.strip().lower() in _CANONICAL_BY_LOWER


def normalize_dusa_status(value: str | None) -> str | None:
    """Return the canonical status string, or None if ``value`` is not one.

    Tolerant of case and surrounding whitespace so an LLM returning "approved"
    or " Submitted " resolves; anything outside the vocabulary returns None so
    the caller can refuse the write rather than store an arbitrary string.
    """
    if not isinstance(value, str):
        return None
    return _CANONICAL_BY_LOWER.get(value.strip().lower())
