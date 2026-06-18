"""Transcript → structured meeting minutes, via the shared LLM wrapper.

Reuses app.core.llm.generate (same Anthropic client + cost estimate as the email
agent). The router gates this behind the `trigger` scope and the daily LLM cap,
so it never spends money without the cost guard passing first.
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.core.llm import LLMResult, generate
from app.models import Document, EventLog, Meeting

_SYSTEM = """You are a meticulous minute-taker for the Deakin Software Engineering \
Club (DSEC) committee. Given a raw meeting transcript, produce concise, faithful \
minutes. Do NOT invent attendees, decisions, or action items that aren't supported \
by the transcript.

Return ONLY valid JSON (no markdown code fences) with exactly this shape:
{
  "summary": "2-4 sentence overview of the meeting",
  "notes_markdown": "Full minutes in GitHub-flavoured markdown: agenda, key \
discussion points, and decisions made.",
  "action_items": [
    {"text": "the action", "owner": "person name or null", "due": "YYYY-MM-DD or null"}
  ]
}"""


def generate_meeting_notes(
    db: Session,
    meeting: Meeting,
    *,
    transcript: str | None = None,
    create_document: bool = True,
) -> LLMResult:
    """Summarise a meeting's transcript into notes + action items, in place.

    Raises ValueError if there's no transcript; LLMError propagates from the
    provider (the router maps it to 502).
    """
    text = transcript if transcript is not None else (meeting.transcript or "")
    if not text.strip():
        raise ValueError("meeting has no transcript to summarise")
    if transcript is not None:
        meeting.transcript = transcript

    result = generate(_SYSTEM, f"Transcript:\n\n{text}")
    parsed = _parse_json(result.text)

    meeting.summary = parsed.get("summary")
    meeting.notes = parsed.get("notes_markdown") or result.text
    meeting.action_items = _clean_action_items(parsed.get("action_items"))
    meeting.status = "NotesDraft"

    db.add(EventLog(
        source="meeting", external_id=str(meeting.id), action="notes_generated",
        subject=meeting.title, tokens=result.tokens, cost=result.cost,
    ))

    if create_document:
        db.add(Document(
            title=f"Minutes — {meeting.title}", type="MeetingNotes",
            content=meeting.notes, status="Draft",
            committee=meeting.committee,
            related_meeting_id=meeting.id, related_event_id=meeting.related_event_id,
            created_by="meeting-ai",
        ))

    db.commit()
    db.refresh(meeting)
    return result


def _parse_json(text: str) -> dict:
    """Best-effort parse of the model's JSON, tolerating stray code fences/prose."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text[:4].lower() == "json":
            text = text[4:]
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        i, j = text.find("{"), text.rfind("}")
        if 0 <= i < j:
            try:
                return json.loads(text[i:j + 1])
            except (ValueError, TypeError):
                pass
    return {}


def _clean_action_items(items) -> list[dict]:
    if not isinstance(items, list):
        return []
    out = []
    for it in items:
        if isinstance(it, dict) and it.get("text"):
            out.append({
                "text": str(it["text"]),
                "owner": it.get("owner") or None,
                "due": it.get("due") or None,
            })
        elif isinstance(it, str) and it.strip():
            out.append({"text": it.strip(), "owner": None, "due": None})
    return out
