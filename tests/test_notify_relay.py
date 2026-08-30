"""Outbound Discord relay: notify_discord helper, POST /public/notify, Cal.com."""

from __future__ import annotations

from sqlalchemy import select

from app.config import settings
from app.core import notify as notify_mod
from app.core.apikeys import generate_key
from app import models
from app.models import EventLog


class _FakeResp:
    def __init__(self, status_code=204):
        self.status_code = status_code


def _capture_httpx(monkeypatch, status_code=204):
    calls = []

    def fake_post(url, **kw):
        calls.append({"url": url, **kw})
        return _FakeResp(status_code)

    monkeypatch.setattr(notify_mod.httpx, "post", fake_post)
    return calls


# --- notify_discord helper ----------------------------------------------------


def test_notify_discord_noop_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "DISCORD_NOTIFY_WEBHOOK_URL", "")
    calls = _capture_httpx(monkeypatch)
    assert notify_mod.notify_discord("hi") is False
    assert calls == []


def test_notify_discord_posts_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "DISCORD_NOTIFY_WEBHOOK_URL", "https://discord/webhook")
    calls = _capture_httpx(monkeypatch, status_code=204)
    assert notify_mod.notify_discord("hello", username="Bot") is True
    assert len(calls) == 1
    assert calls[0]["json"]["content"] == "hello"
    assert calls[0]["json"]["username"] == "Bot"


def test_notify_discord_suppresses_mentions(monkeypatch):
    monkeypatch.setattr(settings, "DISCORD_NOTIFY_WEBHOOK_URL", "https://discord/webhook")
    calls = _capture_httpx(monkeypatch)
    notify_mod.notify_discord("hi @everyone")
    assert calls[0]["json"]["allowed_mentions"] == {"parse": []}


def test_notify_discord_empty_body_is_noop(monkeypatch):
    monkeypatch.setattr(settings, "DISCORD_NOTIFY_WEBHOOK_URL", "https://discord/webhook")
    calls = _capture_httpx(monkeypatch)
    assert notify_mod.notify_discord("   ") is False
    assert calls == []


def test_notify_discord_truncates_over_2000(monkeypatch):
    monkeypatch.setattr(settings, "DISCORD_NOTIFY_WEBHOOK_URL", "https://discord/webhook")
    calls = _capture_httpx(monkeypatch)
    notify_mod.notify_discord("x" * 5000)
    assert len(calls[0]["json"]["content"]) <= 2000


def test_notify_discord_swallows_errors(monkeypatch):
    monkeypatch.setattr(settings, "DISCORD_NOTIFY_WEBHOOK_URL", "https://discord/webhook")

    def boom(*a, **k):
        raise RuntimeError("network")

    monkeypatch.setattr(notify_mod.httpx, "post", boom)
    assert notify_mod.notify_discord("hi") is False  # never raises


def test_notify_discord_reports_http_error(monkeypatch):
    monkeypatch.setattr(settings, "DISCORD_NOTIFY_WEBHOOK_URL", "https://discord/webhook")
    _capture_httpx(monkeypatch, status_code=400)
    assert notify_mod.notify_discord("hi") is False


# --- POST /public/notify ------------------------------------------------------


def _trigger_key(db):
    gen = generate_key()
    db.add(models.APIKey(name="k", prefix=gen.prefix, key_hash=gen.key_hash, scopes=["trigger"]))
    db.commit()
    return gen.raw_key


def test_public_notify_503_when_unconfigured(client, db, monkeypatch):
    monkeypatch.setattr(settings, "DISCORD_NOTIFY_WEBHOOK_URL", "")
    key = _trigger_key(db)
    r = client.post("/public/notify", json={"message": "hi"},
                    headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 503


def test_public_notify_relays_and_logs(client, db, monkeypatch):
    monkeypatch.setattr(settings, "DISCORD_NOTIFY_WEBHOOK_URL", "https://discord/webhook")
    # Patch the symbol imported into the router module.
    import app.features.public_api.router as pr
    sent = []
    monkeypatch.setattr(pr, "notify_discord", lambda msg, username=None: sent.append(msg) or True)
    key = _trigger_key(db)
    r = client.post("/public/notify", json={"message": "deploy done"},
                    headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200
    assert r.json()["delivered"] is True
    assert sent == ["deploy done"]
    log = db.execute(
        select(EventLog).where(EventLog.action == "discord_relay")
    ).scalars().first()
    assert log is not None and log.classification == "delivered"


def test_public_notify_requires_trigger_scope(client, db, monkeypatch):
    monkeypatch.setattr(settings, "DISCORD_NOTIFY_WEBHOOK_URL", "https://discord/webhook")
    gen = generate_key()
    db.add(models.APIKey(name="ro", prefix=gen.prefix, key_hash=gen.key_hash, scopes=["read"]))
    db.commit()
    r = client.post("/public/notify", json={"message": "hi"},
                    headers={"Authorization": f"Bearer {gen.raw_key}"})
    assert r.status_code == 403


# --- Cal.com booking alert ----------------------------------------------------


def test_calcom_booking_notifies_discord_when_enabled(client, db, monkeypatch):
    monkeypatch.setattr(settings, "CALCOM_WEBHOOK_SECRET", "")  # dev: no signature
    monkeypatch.setattr(settings, "CALCOM_NOTIFY_DISCORD", True)
    import app.features.calcom.router as cr
    sent = []
    monkeypatch.setattr(cr, "notify_discord", lambda msg, username=None: sent.append(msg) or True)
    payload = {
        "triggerEvent": "BOOKING_CREATED",
        "payload": {"attendees": [{"name": "Acme Co", "email": "rep@acme.com"}]},
    }
    r = client.post("/calcom/webhook", json=payload)
    assert r.status_code == 200
    assert len(sent) == 1 and "Acme Co" in sent[0]


def test_calcom_booking_silent_when_disabled(client, db, monkeypatch):
    monkeypatch.setattr(settings, "CALCOM_WEBHOOK_SECRET", "")
    monkeypatch.setattr(settings, "CALCOM_NOTIFY_DISCORD", False)
    import app.features.calcom.router as cr
    sent = []
    monkeypatch.setattr(cr, "notify_discord", lambda msg, username=None: sent.append(msg) or True)
    payload = {
        "triggerEvent": "BOOKING_CREATED",
        "payload": {"attendees": [{"name": "Acme", "email": "rep@acme.com"}]},
    }
    r = client.post("/calcom/webhook", json=payload)
    assert r.status_code == 200
    assert sent == []
