"""Execute the decision-maker's proposed action — conservatively and auditable.

The decision stage of the email pipeline hands this module a proposed action
(parsed from the LLM's JSON). This module decides whether it is safe to apply and
either applies it or explains why not. Every path returns a ``DecisionOutcome``
that the pipeline logs to EventLog, so the dashboard audit shows exactly what
happened and why — nothing is ever mutated silently.

Safety rules, all enforced here:
  * Exactly ONE event is ever touched per email (one proposal in, one action out).
  * The target event is resolved by the DETERMINISTIC matcher, not by trusting an
    LLM-supplied id. If the LLM does supply an id, it must AGREE with the matcher,
    or the decision is flagged for a human instead of applied.
  * The proposed status must be a canonical DUSA status or the decision is flagged.
  * Below the confidence threshold, or when the match is ambiguous, nothing is
    written — the email degrades to a drafted reply flagged for a human.
  * In dry-run mode the decision is fully evaluated and logged but never written.

The ``ACTIONS`` registry is intentionally tiny and explicit; new action types
(sponsor stage, finance, meeting notes) are added by registering another handler,
never by widening one.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from email.utils import parseaddr
from typing import Callable

from sqlalchemy.orm import Session

from app.features.email.matching import match_event
from app.features.events import service as events_service
from app.features.events.dusa import normalize_dusa_status

UPDATE_DUSA_STATUS = "update_dusa_status"


@dataclass
class DecisionOutcome:
    action: str  # 'update_dusa_status' | 'none'
    applied: bool
    dry_run: bool
    flagged_for_human: bool
    reason: str
    event_id: int | None = None
    event_name: str | None = None
    status: str | None = None  # canonical proposed status
    confidence: float | None = None
    method: str | None = None

    def audit_payload(self) -> dict:
        """The subset worth persisting in EventLog.payload for the audit view."""
        return asdict(self)


def _none(reason: str, *, dry_run: bool) -> DecisionOutcome:
    return DecisionOutcome(
        action="none", applied=False, dry_run=dry_run, flagged_for_human=False, reason=reason
    )


def _flag(reason: str, *, dry_run: bool, **fields) -> DecisionOutcome:
    return DecisionOutcome(
        action=UPDATE_DUSA_STATUS,
        applied=False,
        dry_run=dry_run,
        flagged_for_human=True,
        reason=reason,
        **fields,
    )


def _sender_domain(sender: str) -> str:
    """Bare lower-cased domain from a From header ("A <a@x.org>" -> "x.org")."""
    addr = parseaddr(sender or "")[1]
    _, _, domain = addr.partition("@")
    return domain.strip().lower()


def _apply_update_dusa_status(db: Session, event_id: int, status: str) -> None:
    """Handler: write the DUSA status. Raises ValueError on a bad status/event."""
    if events_service.set_dusa_status(db, event_id, status) is None:
        raise ValueError(f"event {event_id} not found")


# action name -> handler(db, event_id, status). Explicit and small on purpose.
ACTIONS: dict[str, Callable[[Session, int, str], None]] = {
    UPDATE_DUSA_STATUS: _apply_update_dusa_status,
}


def execute_decision(
    db: Session,
    proposal: dict,
    *,
    subject: str,
    body: str,
    sender: str,
    trusted_domains: set[str],
    dry_run: bool,
    min_confidence: float,
) -> DecisionOutcome:
    """Evaluate + (unless dry-run) apply the LLM's proposed action.

    ``proposal`` is the parsed decision JSON, expected to look like::

        {"action": "update_dusa_status", "event_id": 12, "status": "Approved",
         "reasoning": "..."}

    ``event_id`` is optional and only ever used as a cross-check against the
    deterministic matcher — never as the sole source of the target.

    ``sender``/``trusted_domains`` gate WHETHER the sender may drive the change:
    the matcher decides which event, but only an email from an authorised DUSA
    domain may actually move it. An empty ``trusted_domains`` trusts nobody, so a
    proposal is flagged (never applied) — this is what keeps live mode inert until
    an operator lists the real DUSA sender domain.
    """
    if not isinstance(proposal, dict):
        return _none("no structured proposal", dry_run=dry_run)

    action = (proposal.get("action") or "").strip()
    if action in ("", "none"):
        return _none("decision-maker proposed no action", dry_run=dry_run)
    if action not in ACTIONS:
        return _none(f"unsupported action {action!r}", dry_run=dry_run)

    # Validate the proposed status up front.
    status = normalize_dusa_status(proposal.get("status"))
    if status is None:
        return _flag(
            f"proposed status {proposal.get('status')!r} is not a DUSA status",
            dry_run=dry_run,
        )

    # Resolve the target deterministically. This — not the LLM's id — decides
    # which event (if any) is touched.
    match = match_event(db, subject=subject, body=body)
    if match is None:
        return _flag("no confident/unambiguous event match", dry_run=dry_run, status=status)

    fields = dict(
        event_id=match.event_id,
        event_name=match.event_name,
        status=status,
        confidence=match.confidence,
        method=match.method,
    )

    # If the LLM named an event id, it must agree with the matcher.
    llm_event_id = proposal.get("event_id")
    if isinstance(llm_event_id, int) and llm_event_id != match.event_id:
        return _flag(
            f"LLM event {llm_event_id} disagrees with matched event {match.event_id}",
            dry_run=dry_run,
            **fields,
        )

    if match.confidence < min_confidence:
        return _flag(
            f"match confidence {match.confidence} below threshold {min_confidence}",
            dry_run=dry_run,
            **fields,
        )

    # Sender authenticity: the matcher proved WHICH event; this proves the sender
    # is allowed to move it. Checked before the dry-run/apply split so a dry-run
    # audit row honestly shows a spoofed sender would have been refused.
    domain = _sender_domain(sender)
    if not domain or domain not in trusted_domains:
        return _flag(
            f"sender domain {domain or '(none)'!r} is not an authorised DUSA sender",
            dry_run=dry_run,
            **fields,
        )

    if dry_run:
        return DecisionOutcome(
            action=action,
            applied=False,
            dry_run=True,
            flagged_for_human=False,
            reason=f"dry-run: would set event {match.event_id} DUSA status to {status!r}",
            **fields,
        )

    try:
        ACTIONS[action](db, match.event_id, status)
    except ValueError as exc:
        return _flag(f"apply failed: {exc}", dry_run=dry_run, **fields)

    return DecisionOutcome(
        action=action,
        applied=True,
        dry_run=False,
        flagged_for_human=False,
        reason=f"set event {match.event_id} DUSA status to {status!r}",
        **fields,
    )
