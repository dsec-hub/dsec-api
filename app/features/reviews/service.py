"""Post-event review orchestration — reused by REST + MCP.

Creates a Tally review form for an event (persisting the id/url on the shared
`events` row) and reads its submissions back, mapped onto our known questions.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import Event

from . import tally, template

# template question key -> ReviewResponse field name
_FIELD_BY_KEY = {
    "rating": "rating",
    "enjoyed": "enjoyed",
    "improve": "improve",
    "return": "likelihood",
    "comments": "comments",
}
# template keys whose answers are numeric (coerced to int)
_INT_KEYS = {"rating", "return"}


def create_review_form(db: Session, event_id: int, *, force: bool = False) -> Event | None:
    """Create (or return the existing) Tally review form for an event.

    Returns None if the event doesn't exist. Idempotent: if a form already
    exists it's returned untouched unless `force=True` (which makes a fresh one).
    May raise TallyNotConfigured / TallyError from the Tally client.
    """
    event = db.get(Event, event_id)
    if event is None:
        return None
    if event.review_form_id and not force:
        return event

    form = tally.create_form(template.build_blocks(event.name), name=event.name)
    form_id = str(form.get("id") or "").strip()
    if not form_id:
        raise tally.TallyError("Tally did not return a form id")

    event.review_form_id = form_id
    event.review_form_url = tally.fill_url(form_id)
    event.review_form_created_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(event)
    return event


def get_review_status(db: Session, event_id: int) -> dict | None:
    """Lightweight status for an event's review form (None if event missing).

    The live response count is best-effort: if Tally is unconfigured or
    unreachable it's left None — the form link still works regardless.
    """
    event = db.get(Event, event_id)
    if event is None:
        return None
    count: int | None = None
    if event.review_form_id:
        try:
            count = _response_count(tally.get_submissions(event.review_form_id))
        except Exception:  # noqa: BLE001 — best-effort; never block status on Tally
            count = None
    return {
        "event_id": event.id,
        "configured": bool(event.review_form_id),
        "form_id": event.review_form_id,
        "form_url": event.review_form_url,
        "created_at": event.review_form_created_at,
        "response_count": count,
    }


def get_review_summary(db: Session, event_id: int) -> dict | None:
    """Full submissions + headline stats (None if event missing).

    Raises TallyNotConfigured / TallyError if a form exists but Tally can't be
    reached (so the caller can surface 503/502); returns an empty summary when
    the event simply has no form yet.
    """
    event = db.get(Event, event_id)
    if event is None:
        return None
    if not event.review_form_id:
        return {
            "event_id": event.id,
            "form_id": None,
            "form_url": None,
            "response_count": 0,
            "average_rating": None,
            "responses": [],
        }
    parsed = _parse_submissions(tally.get_submissions(event.review_form_id))
    return {
        "event_id": event.id,
        "form_id": event.review_form_id,
        "form_url": event.review_form_url,
        **parsed,
    }


# --------------------------------------------------------------------------- #
# submission parsing (defensive — Tally answer encoding varies by field type)
# --------------------------------------------------------------------------- #

def _response_count(data: dict) -> int:
    subs = data.get("submissions")
    if isinstance(subs, list):
        return len(subs)
    totals = data.get("totalNumberOfSubmissionsPerFilter") or {}
    if isinstance(totals, dict) and isinstance(totals.get("all"), int):
        return totals["all"]
    return 0


def _parse_submissions(data: dict) -> dict:
    questions = data.get("questions") or []
    qid_to_key = {
        q.get("id"): template.TITLE_TO_KEY[(q.get("title") or "").strip()]
        for q in questions
        if (q.get("title") or "").strip() in template.TITLE_TO_KEY
    }

    submissions = data.get("submissions") or []
    responses: list[dict] = []
    ratings: list[int] = []
    for sub in submissions:
        row: dict = {"submitted_at": sub.get("submittedAt")}
        for ans in sub.get("responses") or []:
            key = qid_to_key.get(ans.get("questionId"))
            if not key:
                continue
            field = _FIELD_BY_KEY[key]
            value = _to_int(ans.get("answer")) if key in _INT_KEYS else _to_text(ans.get("answer"))
            row[field] = value
        if isinstance(row.get("rating"), int):
            ratings.append(row["rating"])
        responses.append(row)

    avg = round(sum(ratings) / len(ratings), 2) if ratings else None
    return {
        "response_count": len(submissions),
        "average_rating": avg,
        "responses": responses,
    }


def _to_int(val) -> int | None:
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return int(val)
    if isinstance(val, str):
        try:
            return int(float(val.strip()))
        except ValueError:
            return None
    if isinstance(val, list) and val:
        return _to_int(val[0])
    return None


def _to_text(val) -> str | None:
    if val is None:
        return None
    if isinstance(val, str):
        return val.strip() or None
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, list):
        parts = [t for t in (_to_text(v) for v in val) if t]
        return ", ".join(parts) or None
    if isinstance(val, dict):
        for k in ("text", "label", "value", "answer", "title"):
            if k in val:
                return _to_text(val[k])
    return None
