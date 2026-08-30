"""Email decision-maker -> DUSA pipeline: matcher, executor, and integration.

The decision-maker ships DARK (EMAIL_DECISION_MAKER_ENABLED defaults False) and
never writes in dry-run. These tests exercise it with the flags flipped and the
LLM `decide` stage stubbed, asserting the conservative guardrails hold.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.config import settings
from app.core.llm import LLMResult
from app.features.email import pipeline as email_pipeline
from app.features.email.actions import execute_decision
from app.features.email.matching import match_event
from app.features.email.pipeline import run_pipeline
from app.features.email.schemas import EmailRequest
from app.features.events import service as events_service
from app.features.events.dusa import DUSA_STATUSES, normalize_dusa_status
from app.models import Event, EventLog


def _event(db, name, **kw):
    ev = Event(name=name, **kw)
    db.add(ev)
    db.commit()
    db.refresh(ev)
    return ev


TRUSTED = {"example.com"}
DUSA_SENDER = "clubs@example.com"  # domain in TRUSTED


def _patch_decide(monkeypatch, obj):
    """Stub the decision LLM to return a fixed JSON object (as its text)."""
    import json

    def fake_decide(system_prompt, user_content, model=None):
        return LLMResult(text=json.dumps(obj), tokens=15, cost=0.0001, model="test")

    monkeypatch.setattr(email_pipeline, "decide", fake_decide)


# --- DUSA vocabulary ----------------------------------------------------------


def test_dusa_statuses_and_normalize():
    assert "Approved" in DUSA_STATUSES
    assert normalize_dusa_status("approved") == "Approved"
    assert normalize_dusa_status("  SUBMITTED ") == "Submitted"
    assert normalize_dusa_status("bogus") is None
    assert normalize_dusa_status(None) is None


def test_set_dusa_status_validates(db):
    ev = _event(db, "Robotics Night")
    out = events_service.set_dusa_status(db, ev.id, "approved")
    assert out.dusa_submission_status == "Approved"
    with pytest.raises(ValueError):
        events_service.set_dusa_status(db, ev.id, "nope")
    assert events_service.set_dusa_status(db, 999999, "Approved") is None


# --- Matcher (conservative) ---------------------------------------------------


def test_match_exact_subject_beats_fuzzy(db):
    _event(db, "Intro to Rust Workshop")
    _event(db, "End of Year Gala")
    m = match_event(db, subject="Re: Intro to Rust Workshop — DUSA approval", body="")
    assert m is not None
    assert m.event_name == "Intro to Rust Workshop"
    assert m.method == "exact-subject"
    assert m.confidence == 1.0


def test_match_ambiguous_returns_none(db):
    _event(db, "Committee Meeting 2026")
    _event(db, "Committee Meeting 2025")
    # Nothing names either specifically; near-tied fuzzy → refuse.
    m = match_event(db, subject="committee meeting", body="hello")
    assert m is None


def test_match_none_when_no_events(db):
    assert match_event(db, subject="anything", body="body") is None


# --- Executor guardrails ------------------------------------------------------


def test_executor_dry_run_does_not_write(db):
    ev = _event(db, "Sponsor Summit")
    proposal = {"action": "update_dusa_status", "event_id": ev.id, "status": "Approved"}
    out = execute_decision(
        db, proposal, subject="Sponsor Summit approved", body="",
        sender=DUSA_SENDER, trusted_domains=TRUSTED,
        dry_run=True, min_confidence=0.8,
    )
    assert out.applied is False and out.dry_run is True
    assert out.event_id == ev.id and out.status == "Approved"
    db.refresh(ev)
    assert ev.dusa_submission_status is None  # nothing written


def test_executor_applies_when_confident(db):
    ev = _event(db, "Sponsor Summit")
    proposal = {"action": "update_dusa_status", "event_id": ev.id, "status": "Approved"}
    out = execute_decision(
        db, proposal, subject="Sponsor Summit approved", body="",
        sender=DUSA_SENDER, trusted_domains=TRUSTED,
        dry_run=False, min_confidence=0.8,
    )
    assert out.applied is True and out.flagged_for_human is False
    db.refresh(ev)
    assert ev.dusa_submission_status == "Approved"


def test_executor_flags_bad_status(db):
    ev = _event(db, "Sponsor Summit")
    out = execute_decision(
        db, {"action": "update_dusa_status", "status": "definitely-maybe"},
        subject="Sponsor Summit", body="", sender=DUSA_SENDER, trusted_domains=TRUSTED,
        dry_run=False, min_confidence=0.8,
    )
    assert out.flagged_for_human is True and out.applied is False


def test_executor_flags_llm_matcher_disagreement(db):
    ev = _event(db, "Sponsor Summit")
    other = _event(db, "Robotics Night")
    # LLM claims `other`, but the email clearly names Sponsor Summit.
    out = execute_decision(
        db, {"action": "update_dusa_status", "event_id": other.id, "status": "Approved"},
        subject="Sponsor Summit approved", body="", sender=DUSA_SENDER, trusted_domains=TRUSTED,
        dry_run=False, min_confidence=0.8,
    )
    assert out.flagged_for_human is True and out.applied is False
    db.refresh(ev)
    assert ev.dusa_submission_status is None


def test_executor_flags_low_confidence(db):
    _event(db, "Some Totally Different Event Name")
    out = execute_decision(
        db, {"action": "update_dusa_status", "status": "Approved"},
        subject="unrelated subject xyz", body="body", sender=DUSA_SENDER, trusted_domains=TRUSTED,
        dry_run=False, min_confidence=0.95,
    )
    # Either no match (None→flag) or below-threshold flag; never applied.
    assert out.applied is False


def test_executor_flags_untrusted_sender(db):
    """A confident match + valid status from an UNlisted sender must not apply."""
    ev = _event(db, "Sponsor Summit")
    out = execute_decision(
        db, {"action": "update_dusa_status", "event_id": ev.id, "status": "Approved"},
        subject="Sponsor Summit approved", body="",
        sender="attacker@evil.com", trusted_domains=TRUSTED,
        dry_run=False, min_confidence=0.8,
    )
    assert out.flagged_for_human is True and out.applied is False
    db.refresh(ev)
    assert ev.dusa_submission_status is None


def test_executor_empty_trusted_domains_never_applies(db):
    """Blank allowlist trusts nobody — even a DUSA-looking sender is refused."""
    ev = _event(db, "Sponsor Summit")
    out = execute_decision(
        db, {"action": "update_dusa_status", "event_id": ev.id, "status": "Approved"},
        subject="Sponsor Summit approved", body="",
        sender=DUSA_SENDER, trusted_domains=set(),
        dry_run=False, min_confidence=0.8,
    )
    assert out.applied is False and out.flagged_for_human is True
    db.refresh(ev)
    assert ev.dusa_submission_status is None


def test_executor_none_action(db):
    out = execute_decision(
        db, {"action": "none"}, subject="hi", body="", sender=DUSA_SENDER,
        trusted_domains=TRUSTED, dry_run=False, min_confidence=0.8,
    )
    assert out.action == "none" and out.applied is False and out.flagged_for_human is False


# --- Pipeline integration -----------------------------------------------------


def test_pipeline_decision_disabled_by_default(db, make_email, patch_llm, monkeypatch):
    _event(db, "Rust Workshop")
    monkeypatch.setattr(settings, "EMAIL_DECISION_MAKER_ENABLED", False)
    # decide() must never be called when disabled.
    def boom(*a, **k):
        raise AssertionError("decide called while disabled")
    monkeypatch.setattr(email_pipeline, "decide", boom)
    patch_llm(label="simple-reply")
    resp = run_pipeline(EmailRequest(**make_email(subject="Rust Workshop update")), db)
    assert resp.action == "draft"
    assert resp.decision is None


def test_pipeline_decision_dry_run_logs_without_write(db, make_email, patch_llm, monkeypatch):
    ev = _event(db, "Rust Workshop")
    monkeypatch.setattr(settings, "EMAIL_DECISION_MAKER_ENABLED", True)
    monkeypatch.setattr(settings, "EMAIL_DECISION_DRY_RUN", True)
    monkeypatch.setattr(settings, "EMAIL_DUSA_SENDER_DOMAINS", "example.com")
    patch_llm(label="simple-reply")
    _patch_decide(monkeypatch, {"action": "update_dusa_status", "event_id": ev.id, "status": "Approved"})
    resp = run_pipeline(
        EmailRequest(**make_email(subject="Rust Workshop approved by DUSA")), db
    )
    assert resp.decision is not None
    assert resp.decision.action == "update_dusa_status"
    assert resp.decision.applied is False and resp.decision.dryRun is True
    db.refresh(ev)
    assert ev.dusa_submission_status is None  # dry-run wrote nothing
    # An audit row exists.
    dec_log = db.execute(
        select(EventLog).where(EventLog.action == "email_decision")
    ).scalars().first()
    assert dec_log is not None


def test_pipeline_decision_live_writes(db, make_email, patch_llm, monkeypatch):
    ev = _event(db, "Rust Workshop")
    monkeypatch.setattr(settings, "EMAIL_DECISION_MAKER_ENABLED", True)
    monkeypatch.setattr(settings, "EMAIL_DECISION_DRY_RUN", False)
    monkeypatch.setattr(settings, "EMAIL_MATCH_MIN_CONFIDENCE", 0.8)
    monkeypatch.setattr(settings, "EMAIL_DUSA_SENDER_DOMAINS", "example.com")
    patch_llm(label="simple-reply")
    _patch_decide(monkeypatch, {"action": "update_dusa_status", "event_id": ev.id, "status": "Approved"})
    resp = run_pipeline(
        EmailRequest(**make_email(subject="Rust Workshop approved by DUSA")), db
    )
    assert resp.action == "draft"  # still drafts a reply
    assert resp.decision.applied is True
    db.refresh(ev)
    assert ev.dusa_submission_status == "Approved"


def test_pipeline_spoofed_sender_never_writes(db, make_email, patch_llm, monkeypatch):
    """Live mode: an email from an unauthorised domain cannot move a DUSA card."""
    ev = _event(db, "Rust Workshop")
    monkeypatch.setattr(settings, "EMAIL_DECISION_MAKER_ENABLED", True)
    monkeypatch.setattr(settings, "EMAIL_DECISION_DRY_RUN", False)
    monkeypatch.setattr(settings, "EMAIL_DUSA_SENDER_DOMAINS", "deakin.edu.au")
    patch_llm(label="simple-reply")
    _patch_decide(monkeypatch, {"action": "update_dusa_status", "event_id": ev.id, "status": "Approved"})
    resp = run_pipeline(
        EmailRequest(**make_email(subject="Rust Workshop approved by DUSA",
                                  **{"from": "attacker@evil.com"})),
        db,
    )
    assert resp.decision.flaggedForHuman is True
    assert resp.decision.applied is False
    db.refresh(ev)
    assert ev.dusa_submission_status is None  # spoof did not move the card
    assert resp.action == "draft"  # still drafted a reply


def test_pipeline_decision_failure_never_breaks_draft(db, make_email, patch_llm, monkeypatch):
    _event(db, "Rust Workshop")
    monkeypatch.setattr(settings, "EMAIL_DECISION_MAKER_ENABLED", True)
    patch_llm(label="simple-reply")
    from app.core.llm import LLMError

    def bad_decide(*a, **k):
        raise LLMError("model down")
    monkeypatch.setattr(email_pipeline, "decide", bad_decide)
    resp = run_pipeline(EmailRequest(**make_email(subject="Rust Workshop")), db)
    assert resp.action == "draft"  # draft proceeds despite decision failure
    assert resp.decision is None
