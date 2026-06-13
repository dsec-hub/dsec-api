"""Email pipeline: spam gate -> classify -> draft -> log.

Strict order (section 9 of the spec):
1. Spam gate (no LLM). Junk / no-reply -> ignore immediately. Cost guard.
2. Classify with the cheap model: needs-meeting / simple-reply / fyi-no-reply.
   `fyi-no-reply` -> ignore.
3. Draft with the draft model. `needs-meeting` appends CALCOM_LINK.
4. Log the outcome regardless of action.

Failure rule: any error in classify/draft is logged and downgraded to
``{"action": "ignore"}``. Never crash, never 500 to the Apps Script.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.config import settings
from app.core import logging as event_logging
from app.core.llm import LLMError, classify, generate
from app.features.email.schemas import EmailRequest, EmailResponse
from app.features.email.spam import is_spam_or_noreply

_logger = logging.getLogger("dsec.email")

_VALID_CLASSES = {"needs-meeting", "simple-reply", "fyi-no-reply"}

_CLASSIFY_SYSTEM = (
    "You triage inbound email for a student committee. Respond with EXACTLY one "
    "of these labels and nothing else: needs-meeting, simple-reply, fyi-no-reply. "
    "Use 'needs-meeting' when the sender wants to meet, call, or schedule time. "
    "Use 'fyi-no-reply' for newsletters, receipts, automated notices, or anything "
    "that plainly needs no response. Otherwise use 'simple-reply'."
)


def _draft_system_prompt(classification: str) -> str:
    base = (
        f"You draft email replies for the DSEC committee in a {settings.TONE} tone. "
        "Write only the reply body — no subject line, no 'Draft:' preamble. "
        "Keep it concise. End with this signature exactly:\n"
        f"{settings.SIGNATURE}"
    )
    if classification == "needs-meeting":
        base += (
            "\n\nThe sender wants to meet. Do NOT propose specific times. Instead, "
            f"invite them to book using this link: {settings.CALCOM_LINK}"
        )
    return base


def run_pipeline(req: EmailRequest, db: Session) -> EmailResponse:
    """Execute the full pipeline and return the draft/ignore decision."""
    payload = req.model_dump(by_alias=True)

    def _ignore(classification: str | None, reason: str, *, tokens=None, cost=None):
        event_logging.log_event(
            db,
            source="email",
            action="ignore",
            external_id=req.threadId,
            sender=req.from_,
            subject=req.subject,
            classification=classification,
            payload=payload,
            output=reason,
            tokens=tokens,
            cost=cost,
        )
        return EmailResponse(action="ignore")

    # 1. Spam gate — no LLM call.
    spam_reason = is_spam_or_noreply(
        sender=req.from_, subject=req.subject, body=req.body
    )
    if spam_reason:
        return _ignore("spam", f"spam gate: {spam_reason}")

    # 2. Classify (cheap model).
    user_content = f"Subject: {req.subject}\nFrom: {req.from_}\n\n{req.body}"
    try:
        c = classify(_CLASSIFY_SYSTEM, user_content)
    except LLMError as exc:
        _logger.warning("classify failed: %s", exc)
        return _ignore(None, f"classify error: {exc}")

    label = c.text.strip().lower()
    if label not in _VALID_CLASSES:
        # Be forgiving: pick the first valid label mentioned, else simple-reply.
        label = next((v for v in _VALID_CLASSES if v in label), "simple-reply")

    if label == "fyi-no-reply":
        return _ignore(label, "classified fyi-no-reply", tokens=c.tokens, cost=c.cost)

    # 3. Draft (draft model).
    try:
        d = generate(_draft_system_prompt(label), user_content)
    except LLMError as exc:
        _logger.warning("draft failed: %s", exc)
        return _ignore(label, f"draft error: {exc}", tokens=c.tokens, cost=c.cost)

    total_tokens = c.tokens + d.tokens
    total_cost = round(c.cost + d.cost, 6)

    # 4. Log the draft outcome.
    event_logging.log_event(
        db,
        source="email",
        action="draft",
        external_id=req.threadId,
        sender=req.from_,
        subject=req.subject,
        classification=label,
        payload=payload,
        output=d.text,
        tokens=total_tokens,
        cost=total_cost,
    )
    return EmailResponse(action="draft", draftBody=d.text)
