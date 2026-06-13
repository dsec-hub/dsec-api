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
