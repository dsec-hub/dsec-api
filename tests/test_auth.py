"""Agent-secret auth on POST /email/process (reject / accept).

The accept case uses a no-reply sender so the spam gate short-circuits to
`ignore` BEFORE any LLM call — proving auth acceptance without touching OpenAI.
"""

from __future__ import annotations


def test_email_process_rejects_missing_secret(client, make_email):
    resp = client.post("/email/process", json=make_email())
    assert resp.status_code == 401


def test_email_process_rejects_wrong_secret(client, make_email):
    resp = client.post(
        "/email/process",
        json=make_email(),
        headers={"X-Agent-Secret": "definitely-wrong"},
    )
    assert resp.status_code == 401


def test_email_process_accepts_valid_secret(client, make_email, agent_headers):
    resp = client.post(
        "/email/process",
        json=make_email(**{"from": "no-reply@newsletter.example.com"}),
        headers=agent_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["action"] == "ignore"  # spam gate, no LLM needed


# --------------------------------------------------------------------------- #
# SEC-07(c): api_key.expires_at — NULL never expires; past rejected
# --------------------------------------------------------------------------- #

def test_verify_key_honours_expiry(db):
    from datetime import datetime, timedelta, timezone

    from app import models
    from app.core.apikeys import generate_key, verify_key

    def _add(name, expires_at):
        g = generate_key()
        db.add(models.APIKey(name=name, prefix=g.prefix, key_hash=g.key_hash,
                             scopes=["read"], expires_at=expires_at))
        return g

    now = datetime.now(timezone.utc)
    never = _add("never", None)                       # NULL → accepted (service keys)
    future = _add("future", now + timedelta(days=30))  # future → accepted
    past = _add("past", now - timedelta(seconds=1))    # past → rejected
    db.commit()

    assert verify_key(never.raw_key, db) is not None
    assert verify_key(future.raw_key, db) is not None
    assert verify_key(past.raw_key, db) is None


def test_new_admin_key_gets_default_expiry(client):
    """A freshly minted key carries a (future) 180-day expiry, surfaced in the
    admin key list so dsec-hub's Settings -> API can show it."""
    auth = ("admin", "test-dashboard-pass")
    made = client.post("/admin/keys", json={"name": "fresh", "scopes": ["read"]}, auth=auth)
    assert made.status_code == 200, made.text
    listed = client.get("/admin/keys", auth=auth).json()
    row = next(k for k in listed if k["id"] == made.json()["id"])
    assert row["expires_at"] is not None
