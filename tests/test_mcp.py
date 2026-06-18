"""MCP server tests: tool registry, scope gating, and HTTP auth.

The full streamable-HTTP protocol is exercised manually; here we unit-test the
scope/auth logic by calling the tool functions directly (the @mcp.tool decorator
returns the original function) with the auth contextvar set, plus a TestClient
check that the mounted /mcp endpoint rejects unauthenticated calls.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager

import pytest

from app.features.mcp import auth as mcpauth
from app.features.mcp import server as mcpserver


@contextmanager
def as_key(scopes):
    ctx = mcpauth.KeyContext(id=1, prefix="dsec_live_test", scopes=frozenset(scopes))
    token = mcpauth._current_key.set(ctx)
    try:
        yield
    finally:
        mcpauth._current_key.reset(token)


def test_tool_registry_covers_features():
    names = {t.name for t in asyncio.run(mcpserver.mcp.list_tools())}
    assert {
        "whoami", "list_members", "member_stats", "finance_summary", "set_event_budget",
        "list_events", "create_event", "create_project", "list_tasks", "create_task",
        "move_task", "create_meeting", "generate_meeting_notes", "create_document",
        "list_sponsors", "create_person",
    } <= names
    assert len(names) >= 25


def test_catalog_matches_registered_tools():
    """The hand-written catalogue (catalog.py) must list exactly the tools the
    FastMCP server registers — otherwise the /info inventory and the per-key
    LLM guide would silently drift from reality."""
    from app.features.mcp import catalog

    registered = {t.name for t in asyncio.run(mcpserver.mcp.list_tools())}
    assert catalog.all_tool_names() == registered


def test_llm_guide_is_scope_aware():
    from app.features.mcp.guide import build_llm_guide

    url = "https://api.dsec.club/mcp"
    banner = "This key is **read-only**"
    read_only = build_llm_guide({"read"}, server_url=url)
    assert banner in read_only                  # read-only callout shown
    assert "list_events" in read_only           # a read tool is documented
    assert "create_event" not in read_only      # a write tool is hidden
    assert "dsec_live_YOUR_KEY" in read_only     # placeholder, never a live key

    full = build_llm_guide({"read", "write", "trigger"}, server_url=url)
    assert "create_event" in full
    assert "generate_meeting_notes" in full
    assert banner not in full                    # no read-only callout when writable


def test_llm_guide_endpoint(client):
    r = client.get("/mcp-setup/llm", params={"scopes": "read,write"})
    assert r.status_code == 200
    assert "text/markdown" in r.headers["content-type"]
    assert "DSEC workspace" in r.text


def test_whoami_reports_scopes():
    with as_key(["read", "write"]):
        who = mcpserver.whoami()
    assert who["authenticated"] is True
    assert who["scopes"] == ["read", "write"]
    assert who["capabilities"]["create_update_data"] is True
    assert who["capabilities"]["generate_meeting_notes_ai"] is False


def test_unauthenticated_tool_raises():
    with pytest.raises(mcpauth.MCPScopeError):
        mcpserver.list_members()  # no contextvar set


def test_read_scope_cannot_write(db):
    with as_key(["read"]):
        mcpserver.list_members()  # allowed
        with pytest.raises(mcpauth.MCPScopeError):
            mcpserver.create_project(name="Nope")


def test_write_scope_round_trip(db):
    with as_key(["read", "write"]):
        proj = mcpserver.create_project(name="DuckType", is_public=True, status="Showcased")
        assert proj["slug"] == "ducktype"
        names = [p["name"] for p in mcpserver.list_projects(is_public=True)]
        assert "DuckType" in names


def test_create_event_coerces_iso_date(db):
    with as_key(["read", "write"]):
        ev = mcpserver.create_event(name="Hackathon", start_date="2026-08-01")
    assert ev["start_date"] == "2026-08-01"


def test_trigger_scope_required_for_notes(db):
    with as_key(["read", "write"]):
        with pytest.raises(mcpauth.MCPScopeError):
            mcpserver.generate_meeting_notes(meeting_id=1)


# --------------------------------------------------------------------------- #
# enforced-module isolation (Sponsors / Finance) + backward-compatible scopes
# --------------------------------------------------------------------------- #

def test_has_scope_backward_compatible_algebra():
    has_scope = mcpauth.has_scope
    R = frozenset
    # legacy "read" ⊇ every read:* (kept working everywhere)
    assert has_scope(R({"read"}), "read:sponsors")
    assert has_scope(R({"read"}), "read:finance")
    # legacy "write" ⊇ every write:*, every read:*, and legacy "read"
    assert has_scope(R({"write"}), "write:finance")
    assert has_scope(R({"write"}), "read:sponsors")
    assert has_scope(R({"write"}), "read")
    # write:X implies read:X
    assert has_scope(R({"write:sponsors"}), "read:sponsors")
    # module scopes match exactly — no cross-module bleed
    assert not has_scope(R({"read:events"}), "read:sponsors")
    assert not has_scope(R({"read:sponsors"}), "read:finance")
    # legacy "read" never grants write or trigger
    assert not has_scope(R({"read"}), "write")
    assert not has_scope(R({"read"}), "write:sponsors")
    assert not has_scope(R({"write"}), "trigger")
    # a pure module key is NOT a legacy read — it can't reach the broad tools
    assert not has_scope(R({"read:events"}), "read")


def test_enforced_module_scope_isolation(db):
    """The Phase-2A proof: a focus-only module key can't reach the isolated
    Sponsors/Finance tools, a per-module key reaches only its module, and a
    legacy `read` key still reaches everything (backward compatible)."""
    # A key with only read:events is rejected by the enforced tools.
    with as_key(["read:events"]):
        with pytest.raises(mcpauth.MCPScopeError):
            mcpserver.list_sponsors()
        with pytest.raises(mcpauth.MCPScopeError):
            mcpserver.finance_summary()
    # A legacy read key is accepted by list_sponsors (and runs cleanly).
    with as_key(["read"]):
        assert mcpserver.list_sponsors() == []
    # A per-module key reaches its own module but not the other enforced one.
    with as_key(["read:sponsors"]):
        assert mcpserver.list_sponsors() == []
        with pytest.raises(mcpauth.MCPScopeError):
            mcpserver.finance_summary()


def test_oauth_scope_derivation_isolates_enforced_modules(db, monkeypatch):
    """`service.scopes_for_grant` never hands an enforced-module scope to a role
    that lacks the module, keeps focus-only modules on legacy read/write, and
    falls back to the coarse grant when the RBAC tables are absent."""
    from app.features.oauth import service, users

    # Treasurer (Finance only) → finance module scopes, no sponsors, no legacy r/w.
    monkeypatch.setattr(users, "_role_perms", lambda d, u: (["finance"], ["finance"]))
    assert set(service.scopes_for_grant(db, object(), {"read", "write"})) == {
        "read:finance", "write:finance",
    }
    # Focus-only role (events/tasks) → legacy read/write, never *:sponsors/*:finance.
    monkeypatch.setattr(users, "_role_perms", lambda d, u: (["events", "tasks"], ["events"]))
    out = set(service.scopes_for_grant(db, object(), {"read", "write"}))
    assert {"read", "write"} <= out
    assert not any(s.endswith((":sponsors", ":finance")) for s in out)
    # Admin superuser → explicit enforced-module scopes are present.
    monkeypatch.setattr(users, "_role_perms", lambda d, u: (["admin"], ["admin"]))
    out = set(service.scopes_for_grant(db, object(), {"read", "write", "trigger"}))
    assert {"read:sponsors", "write:sponsors", "read:finance", "write:finance"} <= out
    assert "trigger" in out
    # No RBAC tables → unchanged coarse grant (backward compatible).
    monkeypatch.setattr(users, "_role_perms", lambda d, u: (None, None))
    assert set(service.scopes_for_grant(db, object(), {"read", "write"})) == {"read", "write"}


def test_mcp_transport_security_does_not_block_remote_host():
    # FastMCP auto-applies a localhost-only Host allowlist (its default host is
    # 127.0.0.1), which 421s every real request to a remote deploy
    # (Host: api.dsec.club). We override it; with MCP_ALLOWED_HOSTS unset the
    # DNS-rebinding check must be OFF so prod requests aren't rejected.
    ts = mcpserver.mcp.settings.transport_security
    assert ts is not None
    assert ts.enable_dns_rebinding_protection is False


def test_http_endpoint_requires_key(client):
    # The mounted /mcp endpoint is behind MCPAuthMiddleware — no key -> 401.
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401


def test_http_endpoint_rejects_bad_key(client):
    r = client.post("/mcp", headers={"Authorization": "Bearer dsec_live_bogus"},
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401


def test_extract_key_reads_bearer_header():
    headers = {b"authorization": b"Bearer dsec_live_abc"}
    assert mcpauth._extract_key(headers) == "dsec_live_abc"


def test_extract_key_reads_x_api_key_header():
    headers = {b"x-api-key": b"dsec_live_xyz"}
    assert mcpauth._extract_key(headers) == "dsec_live_xyz"


def test_extract_key_reads_query_param():
    # Claude.ai's "Add custom connector" dialog has no header field, so the key
    # rides in the URL as ?key=… (or ?api_key=…).
    assert mcpauth._extract_key({}, b"key=dsec_live_qs") == "dsec_live_qs"
    assert mcpauth._extract_key({}, b"foo=1&api_key=dsec_live_qs2") == "dsec_live_qs2"


def test_extract_key_header_wins_over_query_param():
    headers = {b"authorization": b"Bearer dsec_live_hdr"}
    assert mcpauth._extract_key(headers, b"key=dsec_live_qs") == "dsec_live_hdr"


def test_extract_key_none_when_absent():
    assert mcpauth._extract_key({}, b"") is None
    assert mcpauth._extract_key({}, b"key=") is None
