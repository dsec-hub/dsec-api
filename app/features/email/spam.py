"""Cheap heuristic spam / no-reply gate. **No LLM call.**

This is the cost guard: nothing reaches the LLM until it passes. Pure string
heuristics only — keyword blocklist, marketing markers, no-reply senders, and
length checks. Returns a reason when the message should be ignored.
"""

from __future__ import annotations

import re

# Obvious spam / promotional keywords (substring match, case-insensitive).
_BLOCK_KEYWORDS = (
    "viagra",
    "lottery",
    "you have won",
    "crypto investment",
    "act now",
    "limited time offer",
    "click here to claim",
    "wire transfer",
    "nigerian prince",
)

# Markers strongly associated with bulk / marketing mail.
_MARKETING_MARKERS = (
    "unsubscribe",
    "view this email in your browser",
    "manage your preferences",
    "you are receiving this email because",
)

# Sender local-parts / patterns that never expect a human reply.
_NOREPLY_PATTERNS = (
    "no-reply",
    "noreply",
    "do-not-reply",
    "donotreply",
    "mailer-daemon",
    "notifications@",
    "newsletter@",
    "bounce@",
)

_MAX_BODY_CHARS = 50_000  # absurdly long bodies are almost always junk/threads


def is_spam_or_noreply(*, sender: str, subject: str, body: str) -> str | None:
    """Return a reason string if the email should be ignored, else None."""
    s = (sender or "").lower()
    subj = (subject or "").lower()
    text = (body or "").lower()

    if any(p in s for p in _NOREPLY_PATTERNS):
        return "no-reply sender"

    haystack = f"{subj}\n{text}"
    for kw in _BLOCK_KEYWORDS:
        if kw in haystack:
            return f"blocked keyword: {kw}"

    if any(m in text for m in _MARKETING_MARKERS):
        return "marketing markers (likely bulk mail)"

    if len(body or "") > _MAX_BODY_CHARS:
        return "body too long"

    if not (body or "").strip():
        return "empty body"

    # Mostly-link bodies (e.g. promo blasts): many URLs, little prose.
    url_count = len(re.findall(r"https?://", text))
    if url_count >= 8 and len(text) < url_count * 200:
        return "link-heavy body (likely promotional)"

    return None
