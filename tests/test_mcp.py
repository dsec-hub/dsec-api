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
    # legacy "read" ⊇ every read:* EXCEPT the isolated modules (finance/sponsors).
    assert has_scope(R({"read"}), "read:members")     # non-isolated: legacy read covers it
    # SEC-05: the legacy coarse scopes never cover an isolated module.
    assert not has_scope(R({"read"}), "read:sponsors")
    assert not has_scope(R({"read"}), "read:finance")
    assert not has_scope(R({"write"}), "write:finance")
    assert not has_scope(R({"write"}), "read:sponsors")
    # legacy "write" still ⊇ non-isolated read/write scopes, and legacy "read"
    assert has_scope(R({"write"}), "write:people")
    assert has_scope(R({"write"}), "read")
    # write:X implies read:X (including the isolated modules)
    assert has_scope(R({"write:sponsors"}), "read:sponsors")
    assert has_scope(R({"write:finance"}), "read:finance")
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
    """SEC-05: a focus-only module key can't reach the isolated Sponsors/Finance
    tools, a per-module key reaches only its own module, and — unlike before —
    even a legacy `read`/`write` key is refused (the coarse scopes no longer
    cover an isolated module)."""
    # A key with only read:events is rejected by the enforced tools.
    with as_key(["read:events"]):
        with pytest.raises(mcpauth.MCPScopeError):
            mcpserver.list_sponsors()
        with pytest.raises(mcpauth.MCPScopeError):
            mcpserver.finance_summary()
    # SEC-05: a legacy read/write key can no longer reach the isolated tools.
    with as_key(["read", "write"]):
        with pytest.raises(mcpauth.MCPScopeError):
            mcpserver.list_sponsors()
        with pytest.raises(mcpauth.MCPScopeError):
            mcpserver.finance_summary()
    # A per-module key reaches its own module but not the other enforced one.
    with as_key(["read:sponsors"]):
        assert mcpserver.list_sponsors() == []
        with pytest.raises(mcpauth.MCPScopeError):
            mcpserver.finance_summary()


def test_focus_only_role_cannot_reach_enforced_tools(db, monkeypatch):
    """SEC-05 composition test: feed scopes_for_grant's REAL output into has_scope.

    An Events-Lead-style role (events + people, write events) is issued blanket
    read/write PLUS its per-module scopes — the exact grant that used to let its
    blanket write satisfy write:finance. Fails on the pre-fix has_scope.
    """
    from app.features.oauth import service, users
    monkeypatch.setattr(users, "_role_perms", lambda d, u: (["events", "people"], ["events"]))
    granted = frozenset(service.scopes_for_grant(db, object(), {"read", "write"}))
    for s in ("read:finance", "write:finance", "read:sponsors", "write:sponsors"):
        assert not mcpauth.has_scope(granted, s), s


def test_focus_only_grant_is_refused_by_enforced_tools_end_to_end(db, monkeypatch):
    """End-to-end mirror of the composition test: the grant scopes_for_grant
    actually produces must be refused by the enforced MCP tools themselves."""
    from app.features.oauth import service, users
    monkeypatch.setattr(users, "_role_perms", lambda d, u: (["events", "people"], ["events"]))
    granted = list(service.scopes_for_grant(db, object(), {"read", "write"}))
    with as_key(granted):
        with pytest.raises(mcpauth.MCPScopeError):
            mcpserver.finance_summary()
        with pytest.raises(mcpauth.MCPScopeError):
            mcpserver.list_sponsors()


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


# --------------------------------------------------------------------------- #
# media upload (the MCP upload_media tool + compression pipeline)
# --------------------------------------------------------------------------- #

def _tiny_png_b64(size=(1200, 800), color=(30, 144, 255)) -> str:
    import base64
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


@pytest.fixture
def _stub_storage(monkeypatch):
    """Capture uploads instead of hitting R2/Supabase; return a public-ish URL."""
    saved: dict[str, bytes] = {}

    def fake_upload(path, data, content_type):
        saved[path] = data
        return f"https://media.test/{path}"

    monkeypatch.setattr(mcpserver.media_storage, "upload_object", fake_upload)
    return saved


def test_upload_media_runs_pipeline_and_stores(db, _stub_storage):
    with as_key(["read", "write"]):
        ev = mcpserver.create_event(name="Gallery Night")
        asset = mcpserver.upload_media(
            entity_type="event", entity_id=ev["id"], role="poster",
            image_base64=_tiny_png_b64(), alt_text="cover", sort_order=0,
        )
        listed = mcpserver.list_media(entity_type="event", entity_id=ev["id"])
    assert asset["entity_type"] == "event" and asset["role"] == "poster"
    assert asset["alt_text"] == "cover" and asset["sort_order"] == 0
    assert asset["webp_url"].endswith(".webp")
    assert asset["png_url"].endswith(".jpg")  # opaque photo → JPEG download
    assert (asset["width"], asset["height"]) == (1200, 800)
    # Two objects stored (display WebP + download), both under budget.
    assert len(_stub_storage) == 2
    assert [m["id"] for m in listed] == [asset["id"]]


def test_upload_media_data_url_prefix_tolerated(db, _stub_storage):
    with as_key(["read", "write"]):
        ev = mcpserver.create_event(name="Data URL")
        asset = mcpserver.upload_media(
            entity_type="event", entity_id=ev["id"], role="image",
            image_base64="data:image/png;base64," + _tiny_png_b64((300, 300)),
        )
    assert asset["width"] == 300


def test_upload_media_requires_exactly_one_source(db):
    with as_key(["read", "write"]):
        with pytest.raises(ValueError):
            mcpserver.upload_media(entity_type="event", entity_id=1, role="image")
        with pytest.raises(ValueError):
            mcpserver.upload_media(entity_type="event", entity_id=1, role="image",
                                   image_base64="x", source_url="https://x/y.png")


def test_upload_media_ssrf_guard_blocks_internal_url(db):
    with as_key(["read", "write"]):
        for bad in ("http://127.0.0.1/p.png", "http://169.254.169.254/latest",
                    "http://localhost/p.png"):
            with pytest.raises(ValueError):
                mcpserver.upload_media(entity_type="event", entity_id=1, role="image",
                                       source_url=bad)


def test_upload_media_rejects_bad_role(db, _stub_storage):
    with as_key(["read", "write"]):
        ev = mcpserver.create_event(name="Bad Role")
        with pytest.raises(ValueError):
            mcpserver.upload_media(entity_type="event", entity_id=ev["id"],
                                   role="not_a_role", image_base64=_tiny_png_b64())


def test_read_scope_cannot_upload_media(db):
    with as_key(["read"]):
        with pytest.raises(mcpauth.MCPScopeError):
            mcpserver.upload_media(entity_type="event", entity_id=1, role="image",
                                   image_base64=_tiny_png_b64())


def test_update_and_delete_media_round_trip(db, _stub_storage, monkeypatch):
    monkeypatch.setattr(mcpserver.media_storage, "delete_objects", lambda paths: None)
    with as_key(["read", "write"]):
        ev = mcpserver.create_event(name="Edit Me")
        asset = mcpserver.upload_media(entity_type="event", entity_id=ev["id"],
                                       role="image", image_base64=_tiny_png_b64((400, 400)))
        edited = mcpserver.update_media(asset["id"], alt_text="new", role="banner",
                                        sort_order=3)
        assert edited["alt_text"] == "new" and edited["role"] == "banner"
        assert edited["sort_order"] == 3
        assert mcpserver.delete_media(asset["id"]) == {"deleted": True, "media_id": asset["id"]}
        assert mcpserver.list_media(entity_type="event", entity_id=ev["id"]) == []
