"""Per-module scopes for the PII-heavy modules (members / people / documents).

Verifies the rollout is backward-compatible (legacy read/write still works) and
correctly isolating (a granular key for one module is rejected on another).
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from app import models
from app.core.apikeys import VALID_SCOPES, generate_key, has_scope
from app.features.mcp import auth as mcpauth
from app.features.mcp import server as mcpserver
from app.features.mcp.auth import MCPScopeError
from app.features.mcp.catalog import CATALOG, SCOPE_ORDER, SCOPE_SUMMARY


@contextmanager
def _as_key(scopes):
    ctx = mcpauth.KeyContext(id=1, prefix="dsec_live_test", scopes=frozenset(scopes))
    tok = mcpauth._current_key.set(ctx)
    try:
        yield
    finally:
        mcpauth._current_key.reset(tok)


def _key(db, scopes):
    gen = generate_key()
    db.add(models.APIKey(name="k", prefix=gen.prefix, key_hash=gen.key_hash, scopes=scopes))
    db.commit()
    return gen.raw_key


def _auth(k):
    return {"Authorization": f"Bearer {k}"}


# --- REST enforcement ---------------------------------------------------------


def test_granular_members_key_reads_members(client, db):
    k = _key(db, ["read:members"])
    assert client.get("/members", headers=_auth(k)).status_code == 200


def test_legacy_read_still_reads_members(client, db):
    k = _key(db, ["read"])
    assert client.get("/members", headers=_auth(k)).status_code == 200


def test_members_key_rejected_on_people(client, db):
    k = _key(db, ["read:members"])
    assert client.get("/people", headers=_auth(k)).status_code == 403


def test_people_read_key_cannot_write_people(client, db):
    k = _key(db, ["read:people"])
    r = client.post("/people", json={"name": "Ada"}, headers=_auth(k))
    assert r.status_code == 403


def test_people_write_key_can_write_people(client, db):
    k = _key(db, ["write:people"])
    r = client.post("/people", json={"name": "Ada"}, headers=_auth(k))
    assert r.status_code in (200, 201)


def test_documents_key_rejected_on_members(client, db):
    k = _key(db, ["read:documents"])
    assert client.get("/members", headers=_auth(k)).status_code == 403


# --- Scope algebra ------------------------------------------------------------


def test_has_scope_module_isolation():
    assert has_scope({"read"}, "read:members") is True       # legacy read covers it
    assert has_scope({"write"}, "write:people") is True      # legacy write covers it
    assert has_scope({"write:people"}, "read:people") is True  # write implies read
    assert has_scope({"read:members"}, "read:people") is False  # isolated
    assert has_scope({"read:documents"}, "write:documents") is False


# --- Registry consistency -----------------------------------------------------


def test_new_scopes_registered():
    for s in ("read:members", "read:people", "write:people", "read:documents", "write:documents"):
        assert s in VALID_SCOPES
        assert s in SCOPE_ORDER
        assert s in SCOPE_SUMMARY


def test_catalog_scopes_subset_of_valid():
    for tool in CATALOG:
        if tool.scope == "meta":
            continue
        assert tool.scope in VALID_SCOPES, f"{tool.name} has unknown scope {tool.scope}"


def test_scope_order_all_valid():
    for s in SCOPE_ORDER:
        assert s in VALID_SCOPES


# --- OAuth grants isolate the new modules (no blanket-read fallthrough) --------


def test_oauth_members_only_role_gets_no_blanket_read(monkeypatch):
    from app.features.oauth import service as oauth_service
    from app.features.oauth import users as oauth_users
    monkeypatch.setattr(oauth_users, "_role_perms", lambda db, user: (["members"], []))
    scopes = set(oauth_service.scopes_for_grant(None, object(), {"read"}))
    assert "read:members" in scopes
    assert "read" not in scopes  # isolated: no blanket read to reach other PII


def test_oauth_multi_module_role_still_needs_blanket_read(monkeypatch):
    # A role that also touches a focus-only module (events) still gets blanket
    # read, because those modules have no granular scope — the documented caveat.
    from app.features.oauth import service as oauth_service
    from app.features.oauth import users as oauth_users
    monkeypatch.setattr(oauth_users, "_role_perms", lambda db, user: (["members", "events"], []))
    scopes = set(oauth_service.scopes_for_grant(None, object(), {"read"}))
    assert "read:members" in scopes
    assert "read" in scopes


# --- MCP surface enforces the SAME granular scopes as REST --------------------


def test_mcp_members_tool_accepts_granular_key(db):
    with _as_key({"read:members"}):
        assert mcpserver.list_members() == []  # granular key works, no raise


def test_mcp_members_tool_accepts_legacy_read(db):
    with _as_key({"read"}):
        assert mcpserver.list_members() == []  # legacy coarse key still works


def test_mcp_members_tool_rejects_other_module_key(db):
    with _as_key({"read:documents"}), pytest.raises(MCPScopeError):
        mcpserver.list_members()


def test_mcp_people_write_tool_needs_write_people(db):
    with _as_key({"read:people"}), pytest.raises(MCPScopeError):
        mcpserver.create_person(name="Ada")
    with _as_key({"write:people"}):
        person = mcpserver.create_person(name="Ada")
        assert person["name"] == "Ada"
