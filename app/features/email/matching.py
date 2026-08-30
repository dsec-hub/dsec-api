"""Resolve an inbound email to the Event it is about — conservatively.

The email decision-maker must never mutate the wrong event, so this matcher is
deliberately strict: it only ever returns ONE candidate, it prefers an exact
name occurrence over fuzzy similarity, and it refuses (returns None) when the two
best candidates are too close to tell apart. The caller applies a confidence
threshold on top of that; anything below it degrades to a drafted reply flagged
for a human.

Stdlib ``difflib`` only — a committee has tens of events, not thousands, so a
fuzzy library would be a dependency for no measurable gain.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Event

# Ignore very short event names for substring matching — a 3-letter name like
# "AI" would "occur" inside unrelated words and produce false positives.
_MIN_NAME_LEN = 4
# If the best and second-best candidates are within this confidence margin AND
# both are plausible, the email is ambiguous — refuse rather than guess.
_AMBIGUITY_MARGIN = 0.08
_AMBIGUITY_FLOOR = 0.55


@dataclass
class EventMatch:
    event_id: int
    event_name: str
    confidence: float  # 0..1
    method: str  # exact-subject | exact-body | fuzzy


def _score(name: str, subject: str, body: str) -> tuple[float, str]:
    """Best (score, method) for one event name against the email text."""
    name_l = name.strip().lower()
    if len(name_l) >= _MIN_NAME_LEN:
        if name_l in subject.lower():
            return 1.0, "exact-subject"
        if name_l in body.lower():
            return 0.9, "exact-body"
    # Fuzzy: compare the name against the subject and against each body line,
    # taking the strongest similarity. autojunk off so long bodies still score.
    subj_ratio = SequenceMatcher(None, name_l, subject.strip().lower(), autojunk=False).ratio()
    best = subj_ratio
    for line in body.splitlines():
        line_l = line.strip().lower()
        if not line_l:
            continue
        r = SequenceMatcher(None, name_l, line_l, autojunk=False).ratio()
        if r > best:
            best = r
    return best, "fuzzy"


def match_event(
    db: Session,
    *,
    subject: str,
    body: str,
    candidates: list[Event] | None = None,
) -> EventMatch | None:
    """Return the single best event match, or None if none is confident/unambiguous.

    ``candidates`` defaults to every non-archived event. The returned
    ``confidence`` is what the caller thresholds; None means "do not act" (no
    candidates, or the top two are too close to separate).
    """
    subject = subject or ""
    body = body or ""
    if candidates is None:
        candidates = list(
            db.execute(select(Event).where(Event.archived.is_(False))).scalars().all()
        )

    scored: list[EventMatch] = []
    for ev in candidates:
        if not ev.name:
            continue
        score, method = _score(ev.name, subject, body)
        scored.append(EventMatch(event_id=ev.id, event_name=ev.name, confidence=round(score, 4), method=method))

    if not scored:
        return None
    scored.sort(key=lambda m: m.confidence, reverse=True)
    top = scored[0]
    if len(scored) > 1:
        runner = scored[1]
        # Two plausible candidates that are near-tied → ambiguous, refuse.
        if (
            runner.confidence >= _AMBIGUITY_FLOOR
            and (top.confidence - runner.confidence) < _AMBIGUITY_MARGIN
        ):
            return None
    return top
