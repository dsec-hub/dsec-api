"""Tests for the request-size guard, webhook fail-closed, and prod-config guard.

These lock in the security hardening added alongside the v1 → workspace-backend
growth: an attacker-facing webhook must not write to the DB unauthenticated in
production, oversized bodies are rejected before they're buffered, and a
misconfigured production deploy refuses to boot.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.auth import require_cron_secret
from app.config import Settings, is_production, settings, validate_production_settings
from app.core.net import client_ip


class _Headers(dict):
    """Minimal case-insensitive header map (like Starlette's Headers)."""

    def get(self, key, default=None):  # type: ignore[override]
        return super().get(key.lower(), default)


def _req(headers: dict, host: str = "9.9.9.9"):
    return SimpleNamespace(
        headers=_Headers({k.lower(): v for k, v in headers.items()}),
        client=SimpleNamespace(host=host),
    )


def test_client_ip_prefers_trusted_x_real_ip():
    # A spoofed leftmost X-Forwarded-For must be ignored when x-real-ip is set.
    r = _req({"x-real-ip": "1.2.3.4", "x-forwarded-for": "6.6.6.6, 1.2.3.4"})
    assert client_ip(r) == "1.2.3.4"


def test_client_ip_uses_rightmost_xff_hop():
    r = _req({"x-forwarded-for": "spoofed, 9.9.9.9"})
    assert client_ip(r) == "9.9.9.9"


def test_rotating_leftmost_xff_shares_one_bucket():
    # Two requests with DIFFERENT spoofed leftmost values but the same trusted
    # tail must resolve to the SAME ip — so an attacker can't mint a fresh
    # rate-limit bucket per request by rotating X-Forwarded-For.
    a = client_ip(_req({"x-forwarded-for": "aaa.aaa.aaa.aaa, 5.5.5.5"}))
    b = client_ip(_req({"x-forwarded-for": "bbb.bbb.bbb.bbb, 5.5.5.5"}))
    assert a == b == "5.5.5.5"


def test_client_ip_falls_back_to_socket_peer():
    assert client_ip(_req({})) == "9.9.9.9"


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


def test_oversized_body_rejected_on_lookalike_prefix_route(client):
    """/mcp-setup is NOT /mcp: a bare prefix match used to exempt it from the cap.

    The middleware runs before routing, so a 413 comes back even though
    /mcp-setup/anything has no handler (which is the property being tested).
    """
    big = "x" * 200_000
    resp = client.post("/mcp-setup/anything", content=big)
    assert resp.status_code == 413


def test_exempt_prefix_still_exempt_on_subpaths(client):
    """The real exempt routes must keep their exemption."""
    big = "x" * 200_000
    assert client.post("/media", content=big).status_code != 413
    assert client.post("/media/upload", content=big).status_code != 413


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


# --- SEC-03: the production predicate keys off APP_ENV, and the three auth ---
# guards fail closed on the VPS (where VERCEL is unset). ----------------------


def test_is_production_keys_off_app_env(monkeypatch):
    """On the VPS APP_ENV=production and there is no VERCEL var — the whole
    reason the old VERCEL-only predicate silently disabled every guard."""
    monkeypatch.delenv("VERCEL", raising=False)

    monkeypatch.setattr(settings, "APP_ENV", "development")
    assert is_production() is False

    monkeypatch.setattr(settings, "APP_ENV", "production")  # the VPS
    assert is_production() is True

    monkeypatch.setattr(settings, "APP_ENV", "development")  # the legacy path
    monkeypatch.setenv("VERCEL", "1")
    assert is_production() is True


def test_cron_dep_fails_closed_in_production_without_secret(monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.setattr(settings, "CRON_SECRET", "")
    monkeypatch.setattr(settings, "APP_ENV", "production")
    with pytest.raises(HTTPException) as exc:
        require_cron_secret(None)
    assert exc.value.status_code == 503


def test_discord_webhook_fails_closed_in_production_without_key(client, monkeypatch):
    """The core SEC-03 bug: on the live VPS a forged, unsigned interaction reached
    the handler with a 200. Under APP_ENV=production with a blank key it must 503."""
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.setattr(settings, "DISCORD_PUBLIC_KEY", "")
    monkeypatch.setattr(settings, "APP_ENV", "production")
    resp = client.post("/discord/interactions", json={"type": 1})
    assert resp.status_code == 503


def test_discord_webhook_reachable_in_dev_without_key(client, monkeypatch):
    """Outside production a blank key stays passthrough so the endpoint is
    exercisable — PONG, not 503."""
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.setattr(settings, "APP_ENV", "development")
    resp = client.post("/discord/interactions", json={"type": 1})
    assert resp.status_code == 200 and resp.json() == {"type": 1}


def test_calcom_webhook_fails_closed_in_production_via_app_env(client, monkeypatch):
    """Cal.com already failed closed under VERCEL=1; prove the APP_ENV path too,
    since that is what the VPS actually sets."""
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.setattr(settings, "CALCOM_WEBHOOK_SECRET", "")
    monkeypatch.setattr(settings, "APP_ENV", "production")
    resp = client.post("/calcom/webhook", json={"triggerEvent": "BOOKING_CREATED"})
    assert resp.status_code == 503
