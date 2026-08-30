"""Email pipeline: (dedupe) -> spam gate -> classify -> [decide] -> draft -> log.

Strict order:
1. Dedupe (real Gmail path only). A re-delivered message (same messageId) is
   ignored without spending a single LLM call.
2. Spam gate (no LLM). Junk / no-reply -> ignore immediately. Cost guard.
3. Global LLM cap (real Gmail path only). Counts one unit of today's LLM budget
   BEFORE any model call; degrades to ignore if the cap is already reached.
4. Classify: needs-meeting / simple-reply / fyi-no-reply. `fyi-no-reply` -> ignore.
   (One model does both this and the draft step — see app/core/llm.py.)
5. Decision-maker (OPTIONAL, ships dark — see EMAIL_DECISION_MAKER_ENABLED). Reads
   the email against a compact snapshot of open events and proposes ONE structured
   action (update_dusa_status). Applied only when enabled, not dry-run, and the
   deterministic event match clears the confidence threshold; otherwise logged +
   flagged for a human. Never mutates more than one event per email.
6. Draft the reply. `needs-meeting` appends CALCOM_LINK.
7. Log the outcome regardless of action.

Failure rule: any error in classify/decide/draft is logged and downgraded to
``{"action": "ignore"}`` (or continues without a decision). Never crash, never
500 to the Apps Script.
"""

from __future__ import annotations

import json
import logging

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core import logging as event_logging
from app.core.llm import LLMError, classify, decide, generate
from app.core.ratelimit import limiter
from app.features.email.actions import DecisionOutcome, execute_decision
from app.features.email.schemas import EmailDecision, EmailRequest, EmailResponse
from app.features.email.spam import is_spam_or_noreply
from app.features.events.dusa import DUSA_STATUSES
from app.models import Event, EventLog

_logger = logging.getLogger("dsec.email")

_VALID_CLASSES = {"needs-meeting", "simple-reply", "fyi-no-reply"}

_CLASSIFY_SYSTEM = (
    "You triage inbound email for a student committee. Respond with EXACTLY one "
    "of these labels and nothing else: needs-meeting, simple-reply, fyi-no-reply. "
    "Use 'needs-meeting' when the sender wants to meet, call, or schedule time. "
    "Use 'fyi-no-reply' for newsletters, receipts, automated notices, or anything "
    "that plainly needs no response. Otherwise use 'simple-reply'."
)

# Snapshot size cap for the decision prompt — bounds token spend and keeps the
# prompt legible. A committee has tens of live events, so this rarely bites.
_SNAPSHOT_LIMIT = 80


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


def _decision_system_prompt() -> str:
    return (
        "You keep a student committee's DUSA event-submission tracker in sync from "
        "email. DUSA is the student association that must approve club events. Given "
        "one inbound email and a snapshot of current events, decide whether the email "
        "reports a change to a specific event's DUSA submission status.\n\n"
        "Respond with ONLY a JSON object, no prose, no code fences:\n"
        '{"action": "update_dusa_status" | "none", "event_id": <int or null>, '
        '"status": <one of the allowed statuses or null>, "reasoning": "<one sentence>"}\n\n'
        f"Allowed status values (use this exact casing): {', '.join(DUSA_STATUSES)}.\n\n"
        "Rules: choose 'update_dusa_status' ONLY when the email clearly concerns one "
        "specific event's DUSA submission (e.g. DUSA says an event is approved, "
        "rejected, needs more info, or was submitted). Set event_id to the matching "
        "event from the snapshot when you are confident, else null. When in any doubt, "
        "return {\"action\": \"none\"}. Never guess."
    )


def _event_snapshot(db: Session) -> str:
    rows = db.execute(
        select(Event.id, Event.name, Event.dusa_submission_status)
        .where(Event.archived.is_(False))
        .order_by(Event.id.desc())
        .limit(_SNAPSHOT_LIMIT)
    ).all()
    if not rows:
        return "(no events)"
    return "\n".join(
        f"- [{eid}] {name} (DUSA: {status or 'Not Started'})" for eid, name, status in rows
    )


def _parse_decision_json(text: str) -> dict | None:
    """Extract the JSON object from the model's reply, tolerating code fences."""
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        s = s.strip("`")
        # drop an optional leading "json" language tag
        if s[:4].lower() == "json":
            s = s[4:]
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        obj = json.loads(s[start : end + 1])
    except (ValueError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


# Only a produced draft suppresses a re-delivery. Transient ignores (cap hit,
# classify/draft LLM error) produced nothing, so the same messageId must stay
# retryable — otherwise a momentary outage or a cap-window bounce would drop a
# real committee email forever. NB: an `email_decision` row is written BEFORE the
# draft and even for dry-run/`none` outcomes, so it must NOT count here — if it
# did, a decision that succeeds followed by a draft timeout would be deduped as
# "done" and never retried. Re-running the decision on a retry is safe: the DUSA
# write is idempotent (setting the same status again is a no-op). Spam/fyi ignores
# are deterministic and cheap to reprocess, so they are not worth deduping on.
_DURABLE_ACTIONS = ("draft",)


def _already_processed(db: Session, message_id: str) -> bool:
    """True if this exact messageId already produced a durable outcome."""
    if not message_id:
        return False
    row = db.execute(
        select(EventLog.id)
        .where(EventLog.source == "email")
        .where(EventLog.action.in_(_DURABLE_ACTIONS))
        .where(EventLog.payload["messageId"].as_string() == message_id)
        .limit(1)
    ).first()
    return row is not None


def run_pipeline(
    req: EmailRequest,
    db: Session,
    *,
    dedupe: bool = False,
    enforce_llm_cap: bool = False,
) -> EmailResponse:
    """Execute the full pipeline and return the draft/ignore decision.

    ``dedupe`` / ``enforce_llm_cap`` are the real-Gmail-path guards (see
    ``/email/process``); the internal /public/draft caller leaves them off because
    it has already metered the call against its API key and reuses a synthetic
    messageId that must not be deduped.
    """
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

    # 0. Dedupe — a re-delivered message must not re-draft or re-decide.
    if dedupe and _already_processed(db, req.messageId):
        return _ignore("duplicate", f"duplicate messageId: {req.messageId}")

    # 1. Spam gate — no LLM call.
    spam_reason = is_spam_or_noreply(
        sender=req.from_, subject=req.subject, body=req.body
    )
    if spam_reason:
        return _ignore("spam", f"spam gate: {spam_reason}")

    # 2. Global LLM cap — count one unit before any model call. Keyless callers
    #    (the agent-secret Gmail path) were previously uncounted and unbounded.
    if enforce_llm_cap:
        try:
            limiter.check_and_count_llm_global(db)
        except HTTPException as exc:
            return _ignore("capped", f"llm cap: {exc.detail}")

    # 3. Classify (cheap model).
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

    total_tokens = c.tokens
    total_cost = c.cost

    if label == "fyi-no-reply":
        return _ignore(label, "classified fyi-no-reply", tokens=total_tokens, cost=round(total_cost, 6))

    # 4. Decision-maker (optional, ships dark). Never blocks the draft.
    decision_model: EmailDecision | None = None
    if settings.EMAIL_DECISION_MAKER_ENABLED:
        outcome, dec_tokens, dec_cost = _run_decision(db, req, user_content, payload, label)
        total_tokens += dec_tokens
        total_cost += dec_cost
        if outcome is not None:
            decision_model = EmailDecision(
                action=outcome.action,
                applied=outcome.applied,
                dryRun=outcome.dry_run,
                flaggedForHuman=outcome.flagged_for_human,
                reason=outcome.reason,
                eventId=outcome.event_id,
                eventName=outcome.event_name,
                status=outcome.status,
                confidence=outcome.confidence,
            )

    # 5. Draft (draft model).
    try:
        d = generate(_draft_system_prompt(label), user_content)
    except LLMError as exc:
        _logger.warning("draft failed: %s", exc)
        return _ignore(label, f"draft error: {exc}", tokens=total_tokens, cost=round(total_cost, 6))

    total_tokens += d.tokens
    total_cost = round(total_cost + d.cost, 6)

    # 6. Log the draft outcome.
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
    return EmailResponse(action="draft", draftBody=d.text, decision=decision_model)


def _run_decision(
    db: Session, req: EmailRequest, user_content: str, payload: dict, label: str
) -> tuple[DecisionOutcome | None, int, float]:
    """Run the decision stage; return (outcome, tokens, cost).

    Isolated so a decision failure can never take down the draft: any error
    returns (None, 0, 0.0) and the pipeline proceeds to draft as usual.
    """
    try:
        snapshot = _event_snapshot(db)
        dec_input = f"{user_content}\n\n--- CURRENT EVENTS ---\n{snapshot}"
        result = decide(_decision_system_prompt(), dec_input)
    except LLMError as exc:
        _logger.warning("decision LLM failed: %s", exc)
        return None, 0, 0.0

    proposal = _parse_decision_json(result.text) or {"action": "none"}
    try:
        outcome = execute_decision(
            db,
            proposal,
            subject=req.subject,
            body=req.body,
            sender=req.from_,
            trusted_domains=settings.dusa_sender_domains,
            dry_run=settings.EMAIL_DECISION_DRY_RUN,
            min_confidence=settings.EMAIL_MATCH_MIN_CONFIDENCE,
        )
    except Exception as exc:  # noqa: BLE001 — a decision must never break the pipeline
        _logger.exception("decision executor error: %s", exc)
        return None, result.tokens, result.cost

    # Audit every decision — including 'none' — so the dashboard shows what the
    # agent considered and why, never a silent write.
    audit = outcome.audit_payload()
    audit["messageId"] = req.messageId
    audit["classification"] = label
    # Audit-only row: tokens/cost are None here and folded into the "draft" row's
    # total by the caller, so a cost dashboard summing EventLog.cost does not
    # double-count this email's decision spend.
    event_logging.log_event(
        db,
        source="email",
        action="email_decision",
        external_id=req.threadId,
        sender=req.from_,
        subject=req.subject,
        classification=outcome.action,
        payload=audit,
        output=outcome.reason,
        tokens=None,
        cost=None,
    )
    return outcome, result.tokens, result.cost
