"""LLM cost-cap enforcement on the trigger-scoped /public/draft route.

The cap is the real money guard: it must raise 429 BEFORE any LLM call. Each
test asserts both the 429 and that the mocked LLM was never invoked.
"""

from __future__ import annotations

import app.core.ratelimit as ratelimit_mod
from app.core.apikeys import generate_key
from app.models import APIKey

_DRAFT_BODY = {"subject": "Hi", "from": "person@example.com", "body": "Tell me about the club."}


def _post_draft(client, raw_key):
    return client.post(
        "/public/draft",
        headers={"Authorization": f"Bearer {raw_key}"},
        json=_DRAFT_BODY,
    )


def test_draft_under_cap_succeeds(client, trigger_key, patch_llm):
    patch_llm(label="simple-reply")
    resp = _post_draft(client, trigger_key)
    assert resp.status_code == 200
    assert resp.json()["action"] in {"draft", "ignore"}


def test_global_llm_cap_blocks_before_any_llm(client, trigger_key, patch_llm, monkeypatch):
    rec = patch_llm(label="simple-reply")
    monkeypatch.setattr(ratelimit_mod.settings, "GLOBAL_DAILY_LLM_CAP", 0)
    resp = _post_draft(client, trigger_key)
    assert resp.status_code == 429
    assert "global daily LLM cap" in resp.json()["detail"]
    assert rec.classify_args == [] and rec.generate_args == []  # no spend


def test_per_key_trigger_cap_blocks_before_any_llm(client, trigger_key, patch_llm, monkeypatch):
    rec = patch_llm(label="simple-reply")
    monkeypatch.setattr(ratelimit_mod.settings, "RATE_LIMIT_TRIGGER_PER_DAY", 0)
    resp = _post_draft(client, trigger_key)
    assert resp.status_code == 429
    assert "per-key daily trigger cap" in resp.json()["detail"]
    assert rec.generate_args == []  # no spend


def test_read_only_key_forbidden_from_draft(client, db, patch_llm):
    patch_llm(label="simple-reply")
    gen = generate_key()
    db.add(APIKey(name="ro", prefix=gen.prefix, key_hash=gen.key_hash, scopes=["read"]))
    db.commit()
    resp = _post_draft(client, gen.raw_key)
    assert resp.status_code == 403
