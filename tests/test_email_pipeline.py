"""Email pipeline branch coverage: spam-gate -> classify -> draft -> log.

Each branch is exercised by calling `run_pipeline` directly with the test DB
session, asserting both the returned decision and the EventLog row written.
The LLM is mocked via the `patch_llm` fixture so no OpenAI call is ever made.
"""

from __future__ import annotations

from sqlalchemy import select

from app.config import settings
from app.core.llm import LLMError
from app.features.email.pipeline import run_pipeline
from app.features.email.schemas import EmailRequest
from app.models import EventLog


def _latest_log(db):
    return db.execute(
        select(EventLog).order_by(EventLog.id.desc())
    ).scalars().first()


# --- 1. Spam gate (no LLM) ----------------------------------------------------


def test_spam_gate_noreply_sender_ignored_without_llm(db, make_email, patch_llm):
    rec = patch_llm()
    resp = run_pipeline(
        EmailRequest(**make_email(**{"from": "no-reply@bulk.example.com"})), db
    )
    assert resp.action == "ignore"
    assert rec.classify_args == []  # LLM never reached
    assert rec.generate_args == []
    row = _latest_log(db)
    assert row.action == "ignore"
    assert row.classification == "spam"


def test_spam_gate_blocked_keyword_ignored(db, make_email, patch_llm):
    rec = patch_llm()
    resp = run_pipeline(
        EmailRequest(**make_email(subject="You have won the lottery", body="claim now")),
        db,
    )
    assert resp.action == "ignore"
    assert rec.classify_args == []


def test_spam_gate_empty_body_ignored(db, make_email, patch_llm):
    rec = patch_llm()
    resp = run_pipeline(EmailRequest(**make_email(body="    ")), db)
    assert resp.action == "ignore"
    assert rec.classify_args == []


# --- 2. Classify branch -------------------------------------------------------


def test_fyi_no_reply_ignored_after_classify(db, make_email, patch_llm):
    rec = patch_llm(label="fyi-no-reply")
    resp = run_pipeline(EmailRequest(**make_email(body="Monthly newsletter, for your info.")), db)
    assert resp.action == "ignore"
    assert len(rec.classify_args) == 1
    assert rec.generate_args == []  # never drafts
    assert _latest_log(db).classification == "fyi-no-reply"


def test_classify_error_downgrades_to_ignore(db, make_email, patch_llm):
    rec = patch_llm(classify_exc=LLMError("upstream down"))
    resp = run_pipeline(EmailRequest(**make_email(body="A genuine question about events.")), db)
    assert resp.action == "ignore"
    assert rec.generate_args == []  # never drafts on classify failure


# --- 3. Draft branch ----------------------------------------------------------


def test_simple_reply_produces_draft(db, make_email, patch_llm):
    rec = patch_llm(
        label="simple-reply",
        draft="Sure — here are the details.\n\nBest regards,\nThe DSEC Committee",
    )
    resp = run_pipeline(EmailRequest(**make_email(body="What are your meeting times?")), db)
    assert resp.action == "draft"
    assert resp.draftBody and "DSEC Committee" in resp.draftBody
    assert len(rec.generate_args) == 1
    assert _latest_log(db).action == "draft"


def test_needs_meeting_injects_calcom_link(db, make_email, patch_llm):
    rec = patch_llm(label="needs-meeting")
    resp = run_pipeline(EmailRequest(**make_email(body="Can we schedule a call next week?")), db)
    assert resp.action == "draft"
    # The draft system prompt must invite booking via the Cal.com link.
    system_prompt = rec.generate_args[0][0]
    assert settings.CALCOM_LINK in system_prompt


def test_draft_error_downgrades_to_ignore(db, make_email, patch_llm):
    rec = patch_llm(label="simple-reply", draft_exc=LLMError("draft model timeout"))
    resp = run_pipeline(EmailRequest(**make_email(body="A genuine question about events.")), db)
    assert resp.action == "ignore"
    assert len(rec.classify_args) == 1
    assert len(rec.generate_args) == 1  # draft attempted, then degraded


# --- 4. Invariant: never auto-sends ------------------------------------------


def test_pipeline_only_ever_drafts_or_ignores(db, make_email, patch_llm):
    patch_llm(label="simple-reply")
    resp = run_pipeline(EmailRequest(**make_email()), db)
    assert resp.action in {"draft", "ignore"}
    assert not hasattr(resp, "sent")  # there is no send path
