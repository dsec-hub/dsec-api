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

from app import models
from app.core.apikeys import generate_key
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


def test_http_endpoint_requires_key(client):
    # The mounted /mcp endpoint is behind MCPAuthMiddleware — no key -> 401.
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401


def test_http_endpoint_rejects_bad_key(client):
    r = client.post("/mcp", headers={"Authorization": "Bearer dsec_live_bogus"},
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401
