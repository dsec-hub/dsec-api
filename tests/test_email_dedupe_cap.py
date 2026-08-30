"""Email path: per-message dedupe and the keyless global LLM cap."""

from __future__ import annotations

from sqlalchemy import func, select

from app.config import settings
from app.core.ratelimit import limiter
from app.features.email.pipeline import run_pipeline
from app.features.email.schemas import EmailRequest
from app.models import EventLog, RateLimit


def _draft_logs(db):
    return db.execute(
        select(func.count(EventLog.id)).where(EventLog.action == "draft")
    ).scalar_one()


# --- Dedupe -------------------------------------------------------------------


def test_duplicate_message_id_is_ignored_without_redraft(db, make_email, patch_llm):
    rec = patch_llm(label="simple-reply")
    body = make_email(messageId="dup-123", subject="Please reply")
    r1 = run_pipeline(EmailRequest(**body), db, dedupe=True)
    r2 = run_pipeline(EmailRequest(**body), db, dedupe=True)
    assert r1.action == "draft"
    assert r2.action == "ignore"
    assert len(rec.generate_args) == 1  # drafted exactly once
    assert _draft_logs(db) == 1


def test_transient_capped_ignore_stays_retryable(db, make_email, patch_llm, monkeypatch):
    """A message dropped only by a cap hit must NOT be deduped forever."""
    rec = patch_llm(label="simple-reply")
    body = make_email(messageId="retry-me", subject="Please reply")

    # First delivery lands while the global cap is exhausted → transient ignore.
    monkeypatch.setattr(settings, "GLOBAL_DAILY_LLM_CAP", 0)
    r1 = run_pipeline(EmailRequest(**body), db, dedupe=True, enforce_llm_cap=True)
    assert r1.action == "ignore"
    assert len(rec.generate_args) == 0

    # Cap restored; the same messageId is re-delivered → it must draft now.
    monkeypatch.setattr(settings, "GLOBAL_DAILY_LLM_CAP", 1000)
    r2 = run_pipeline(EmailRequest(**body), db, dedupe=True, enforce_llm_cap=True)
    assert r2.action == "draft"
    assert len(rec.generate_args) == 1

    # But once drafted, a further re-delivery IS deduped.
    r3 = run_pipeline(EmailRequest(**body), db, dedupe=True, enforce_llm_cap=True)
    assert r3.action == "ignore"
    assert len(rec.generate_args) == 1


def test_dedupe_off_allows_reprocess(db, make_email, patch_llm):
    rec = patch_llm(label="simple-reply")
    body = make_email(messageId="same", subject="Hi")
    run_pipeline(EmailRequest(**body), db, dedupe=False)
    run_pipeline(EmailRequest(**body), db, dedupe=False)
    assert len(rec.generate_args) == 2  # no dedupe → drafted twice


# --- Global LLM cap (keyless path) --------------------------------------------


def test_email_path_counts_and_enforces_global_cap(db, make_email, patch_llm, monkeypatch):
    monkeypatch.setattr(settings, "GLOBAL_DAILY_LLM_CAP", 2)
    rec = patch_llm(label="simple-reply")

    def _run(mid):
        return run_pipeline(
            EmailRequest(**make_email(messageId=mid, subject="Reply please")),
            db, enforce_llm_cap=True,
        )

    assert _run("a").action == "draft"
    assert _run("b").action == "draft"
    third = _run("c")
    assert third.action == "ignore"  # capped
    # The third email never reached the classify LLM.
    assert len(rec.classify_args) == 2

    # Email spend is recorded in the shared 'trigger' bucket (keyless row).
    total = db.execute(
        select(func.coalesce(func.sum(RateLimit.trigger_count_today), 0)).where(
            RateLimit.bucket == "trigger"
        )
    ).scalar_one()
    assert total == 2

    # And it counts against a KEYED trigger route too (shared budget).
    import pytest
    from fastapi import HTTPException
    # Mint isn't needed; call the global check directly — it should already be at cap.
    with pytest.raises(HTTPException):
        limiter.check_and_count_llm_global(db)


def test_cap_not_enforced_when_flag_off(db, make_email, patch_llm, monkeypatch):
    monkeypatch.setattr(settings, "GLOBAL_DAILY_LLM_CAP", 1)
    patch_llm(label="simple-reply")
    # enforce_llm_cap defaults False (the /public/draft path), so no counting.
    for mid in ("x", "y", "z"):
        r = run_pipeline(EmailRequest(**make_email(messageId=mid)), db)
        assert r.action == "draft"
    total = db.execute(
        select(func.coalesce(func.sum(RateLimit.trigger_count_today), 0)).where(
            RateLimit.bucket == "trigger"
        )
    ).scalar_one()
    assert total == 0
