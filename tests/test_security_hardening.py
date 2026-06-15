"""Tests for the request-size guard, webhook fail-closed, and prod-config guard.

These lock in the security hardening added alongside the v1 → workspace-backend
growth: an attacker-facing webhook must not write to the DB unauthenticated in
production, oversized bodies are rejected before they're buffered, and a
misconfigured production deploy refuses to boot.
"""

from __future__ import annotations

import pytest

from app.config import Settings, validate_production_settings


def test_oversized_body_rejected_on_non_exempt_route(client):
    """A body over MAX_REQUEST_BYTES is rejected with 413 before auth runs."""
    big = "x" * 200_000  # > the 100 KB default cap
    resp = client.post("/public/draft", content=big)
    assert resp.status_code == 413
    assert resp.json()["detail"] == "request body too large"


def test_oversized_body_allowed_on_exempt_upload_route(client):
    """Upload routes (their own per-file caps) are exempt from the global cap."""
    big = "x" * 200_000
    resp = client.post("/media", content=big)
    # Not a 413 — it falls through to the media handler's own auth/validation.
    assert resp.status_code != 413


def test_calcom_webhook_fails_closed_in_production_without_secret(client, monkeypatch):
    """With no CALCOM_WEBHOOK_SECRET, the booking webhook must not accept writes
    in production — it returns 503 instead of creating a SponsorLead."""
    monkeypatch.setenv("VERCEL", "1")
    resp = client.post("/calcom/webhook", json={"triggerEvent": "BOOKING_CREATED"})
    assert resp.status_code == 503


def test_calcom_webhook_reachable_in_dev_without_secret(client, monkeypatch):
    """Outside production the stub stays reachable (so it can be exercised)."""
    monkeypatch.delenv("VERCEL", raising=False)
    resp = client.post("/calcom/webhook", json={"triggerEvent": "SOMETHING_ELSE"})
    # Reaches the handler (which ignores non-booking triggers) rather than 503.
    assert resp.status_code == 200


def test_production_config_guard_rejects_insecure_defaults(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    insecure = Settings(
        AGENT_SECRET="change-me-agent-secret",
        DASHBOARD_PASS="change-me-dashboard-pass",
        DATABASE_URL="sqlite:///./local.db",
    )
    with pytest.raises(RuntimeError) as exc:
        validate_production_settings(insecure)
    msg = str(exc.value)
    assert "AGENT_SECRET" in msg and "DASHBOARD_PASS" in msg and "SQLite" in msg


def test_production_config_guard_passes_when_configured(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    secure = Settings(
        AGENT_SECRET="a-real-long-random-secret",
        DASHBOARD_PASS="another-strong-secret",
        DATABASE_URL="postgresql+psycopg://user:pw@host/db?sslmode=require",
    )
    validate_production_settings(secure)  # must not raise


def test_production_config_guard_noop_outside_vercel(monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    insecure = Settings(AGENT_SECRET="change-me-agent-secret")
    validate_production_settings(insecure)  # no-op locally
