"""Tests for the dumb inbound-email capture endpoint (`POST /ingest/email`).

This path records an email to the EventLog with NO LLM spend and NO triage — so
the assertions are about auth, idempotency, and that a row is written, not about
classification (that lives in test_email_pipeline.py).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app import models
from app.core.apikeys import generate_key


@pytest.fixture
def ingest_key(db):
    gen = generate_key()
    db.add(models.APIKey(name="ingest", prefix=gen.prefix, key_hash=gen.key_hash,
                         scopes=["read", "ingest"]))
    db.commit()
    return gen.raw_key


@pytest.fixture
def read_only_key(db):
    gen = generate_key()
    db.add(models.APIKey(name="ro", prefix=gen.prefix, key_hash=gen.key_hash, scopes=["read"]))
    db.commit()
    return gen.raw_key


def _post(client, key, **overrides):
    body = {
        "message_id": "cap-1",
        "from": "person@example.com",
        "to": "committee@dsec.club",
        "subject": "Hello",
        "body": "Just saying hi.",
        "received_at": "2026-06-13T10:00:00Z",
        "thread_id": "t-1",
    }
    body.update(overrides)
    return client.post(
        "/ingest/email",
        headers={"Authorization": f"Bearer {key}"},
        json=body,
    )


def test_capture_requires_auth(client):
    r = client.post("/ingest/email", json={"message_id": "x"})
    assert r.status_code == 401


def test_capture_requires_ingest_scope(client, read_only_key):
    r = _post(client, read_only_key)
    assert r.status_code == 403


def test_capture_writes_eventlog_row(client, ingest_key, db):
    r = _post(client, ingest_key, message_id="cap-write")
    assert r.status_code == 200
    payload = r.json()
    assert payload["status"] == "captured"
    assert payload["message_id"] == "cap-write"
    assert payload["event_id"] is not None

    row = db.execute(
        select(models.EventLog).where(models.EventLog.external_id == "cap-write")
    ).scalar_one()
    assert row.source == "email"
    assert row.action == "captured"
    assert row.sender == "person@example.com"
    assert row.subject == "Hello"
    # No LLM ran: no decision-making, no spend.
    assert row.classification is None
    assert row.tokens is None
    assert row.cost is None
    assert row.payload["body"] == "Just saying hi."


def test_capture_is_idempotent(client, ingest_key, db):
    first = _post(client, ingest_key, message_id="dup-1")
    assert first.json()["status"] == "captured"

    again = _post(client, ingest_key, message_id="dup-1", subject="resend")
    assert again.status_code == 200
    assert again.json()["status"] == "duplicate"
    # The re-send points back at the original row and creates no second one.
    assert again.json()["event_id"] == first.json()["event_id"]

    rows = db.execute(
        select(models.EventLog).where(models.EventLog.external_id == "dup-1")
    ).scalars().all()
    assert len(rows) == 1


def test_capture_only_requires_message_id(client, ingest_key):
    # A malformed header must never cost us the capture — only message_id is required.
    r = client.post(
        "/ingest/email",
        headers={"Authorization": f"Bearer {ingest_key}"},
        json={"message_id": "bare"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "captured"
