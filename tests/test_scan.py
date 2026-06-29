"""Tests for the /scan QR-wall feature (dsec-api portion).

Covers the REST CRUD + scope gating, URL/accent validation, reorder, the
editable page header (singleton scan_page), the public `/website/scan` wall feed
(header + visible cards), and the MCP tools. Mirrors tests/test_links.py.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from app import models
from app.core.apikeys import generate_key
from app.features.mcp import auth as mcpauth
from app.features.mcp import server as mcpserver
from app.features.scan import service as scan_service


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #

def _make_key(db, scopes, name="k"):
    gen = generate_key()
    db.add(models.APIKey(name=name, prefix=gen.prefix, key_hash=gen.key_hash, scopes=scopes))
    db.commit()
    return gen.raw_key


@pytest.fixture
def rw_key(db):
    return _make_key(db, ["read", "write"], "rw")


@pytest.fixture
def ro_key(db):
    return _make_key(db, ["read"], "ro")


def _h(key):
    return {"Authorization": f"Bearer {key}"}


@contextmanager
def as_key(scopes):
    ctx = mcpauth.KeyContext(id=1, prefix="dsec_live_test", scopes=frozenset(scopes))
    token = mcpauth._current_key.set(ctx)
    try:
        yield
    finally:
        mcpauth._current_key.reset(token)


# --------------------------------------------------------------------------- #
# REST: scan-target CRUD + scope gating
# --------------------------------------------------------------------------- #

def test_scan_crud_and_scope(client, rw_key, ro_key):
    # write is gated
    assert client.post("/scan", json={"label": "X", "url": "https://dsec.club"},
                       headers=_h(ro_key)).status_code == 403

    # create
    r = client.post(
        "/scan",
        json={"label": "Website", "url": "https://dsec.club", "caption": "See what we build",
              "pretty": "dsec.club", "accent": "blue"},
        headers=_h(rw_key),
    )
    assert r.status_code == 201
    body = r.json()
    sid = body["id"]
    assert body["label"] == "Website" and body["pretty"] == "dsec.club"
    assert body["caption"] == "See what we build" and body["accent"] == "blue"
    assert body["is_visible"] is True and body["archived"] is False
    assert body["display_order"] == 0

    # read one
    assert client.get(f"/scan/{sid}", headers=_h(ro_key)).json()["url"] == "https://dsec.club"
    assert client.get("/scan/99999", headers=_h(ro_key)).status_code == 404

    # patch
    patched = client.patch(f"/scan/{sid}", json={"caption": "Updated", "is_visible": False},
                           headers=_h(rw_key)).json()
    assert patched["caption"] == "Updated" and patched["is_visible"] is False

    # list shows hidden by default (dashboard view)
    assert [t["id"] for t in client.get("/scan", headers=_h(ro_key)).json()] == [sid]
    # include_hidden=false drops it
    assert client.get("/scan", params={"include_hidden": False}, headers=_h(ro_key)).json() == []

    # archive (soft delete)
    assert client.post(f"/scan/{sid}/archive", headers=_h(rw_key)).json()["archived"] is True
    assert client.get("/scan", headers=_h(ro_key)).json() == []
    assert len(client.get("/scan", params={"include_archived": True},
                          headers=_h(ro_key)).json()) == 1


def test_scan_create_requires_label_and_url(client, rw_key):
    assert client.post("/scan", json={"label": "No url"}, headers=_h(rw_key)).status_code == 422
    assert client.post("/scan", json={"url": "https://x.com"}, headers=_h(rw_key)).status_code == 422


def test_scan_url_and_accent_validation(client, rw_key):
    # dangerous schemes rejected (XSS guard)
    assert client.post("/scan", json={"label": "Evil", "url": "javascript:alert(1)"},
                       headers=_h(rw_key)).status_code == 422
    # relative paths rejected for scan (a QR can't encode them)
    assert client.post("/scan", json={"label": "Rel", "url": "/events"},
                       headers=_h(rw_key)).status_code == 422
    # an accent outside the 4 light scan accents is rejected
    assert client.post("/scan", json={"label": "Bad", "url": "https://x.com", "accent": "violet"},
                       headers=_h(rw_key)).status_code == 422
    # http(s) + mailto accepted
    ok = client.post("/scan", json={"label": "Mail", "url": "mailto:hi@dsec.club"}, headers=_h(rw_key))
    assert ok.status_code == 201 and ok.json()["url"] == "mailto:hi@dsec.club"


def test_scan_reorder(client, rw_key):
    a = client.post("/scan", json={"label": "A", "url": "https://a.com"}, headers=_h(rw_key)).json()
    b = client.post("/scan", json={"label": "B", "url": "https://b.com"}, headers=_h(rw_key)).json()
    reordered = client.post("/scan/reorder", json={"ordered_ids": [b["id"], a["id"]]},
                            headers=_h(rw_key)).json()
    assert [t["id"] for t in reordered] == [b["id"], a["id"]]


# --------------------------------------------------------------------------- #
# Public feed
# --------------------------------------------------------------------------- #

def test_public_scan_feed(client, rw_key):
    second = client.post("/scan", json={"label": "Join", "url": "https://dsec.club/join",
                                        "display_order": 2}, headers=_h(rw_key)).json()
    first = client.post("/scan", json={"label": "Website", "url": "https://dsec.club",
                                       "display_order": 1, "accent": "blue", "pretty": "dsec.club"},
                        headers=_h(rw_key)).json()
    hidden = client.post("/scan", json={"label": "Secret", "url": "https://x.com",
                                        "is_visible": False}, headers=_h(rw_key)).json()
    gone = client.post("/scan", json={"label": "Old", "url": "https://old.com"},
                       headers=_h(rw_key)).json()
    client.post(f"/scan/{gone['id']}/archive", headers=_h(rw_key))

    feed = client.get("/website/scan").json()  # public, no auth
    # The wall is { title, description, targets:[...] }; cards keep order, hidden
    # + archived excluded.
    assert [t["label"] for t in feed["targets"]] == ["Website", "Join"]
    web = feed["targets"][0]
    assert web["accent"] == "blue" and web["pretty"] == "dsec.club" and web["display_order"] == 1
    # public card shape carries only the safe fields
    assert set(web.keys()) == {"label", "caption", "url", "pretty", "accent", "display_order"}
    # header defaults to the built-in copy when no scan_page row has been saved
    assert feed["title"] == scan_service.DEFAULT_PAGE_TITLE
    assert feed["description"] == scan_service.DEFAULT_PAGE_DESCRIPTION
    assert first["id"] and second["id"] and hidden["id"]


def test_public_scan_feed_empty_by_default(client):
    """No fallback CARD set — an empty table yields an empty `targets` list (the
    website renders its own default cards then). The header still defaults to the
    built-in copy so the page heading is never blank."""
    feed = client.get("/website/scan").json()
    assert feed["targets"] == []
    assert feed["title"] == scan_service.DEFAULT_PAGE_TITLE
    assert feed["description"] == scan_service.DEFAULT_PAGE_DESCRIPTION


# --------------------------------------------------------------------------- #
# MCP tools
# --------------------------------------------------------------------------- #

def test_mcp_registry_includes_scan_tools():
    import asyncio

    names = {t.name for t in asyncio.run(mcpserver.mcp.list_tools())}
    assert {
        "list_scan_targets", "get_scan_target", "create_scan_target",
        "update_scan_target", "archive_scan_target", "reorder_scan_targets",
        "get_scan_page", "update_scan_page",
    } <= names


def test_mcp_scan_round_trip(db):
    with as_key(["read", "write"]):
        a = mcpserver.create_scan_target(label="Website", url="https://dsec.club", pretty="dsec.club")
        b = mcpserver.create_scan_target(label="Join", url="https://dsec.club/join")
        assert a["label"] == "Website" and a["pretty"] == "dsec.club"

        reordered = mcpserver.reorder_scan_targets([b["id"], a["id"]])
        assert [t["id"] for t in reordered] == [b["id"], a["id"]]

        mcpserver.update_scan_target(a["id"], is_visible=False)
        assert [t["id"] for t in mcpserver.list_scan_targets(include_hidden=False)] == [b["id"]]

        mcpserver.archive_scan_target(a["id"])
        mcpserver.archive_scan_target(b["id"])
        assert mcpserver.list_scan_targets() == []
        assert len(mcpserver.list_scan_targets(include_archived=True)) == 2


def test_mcp_scan_read_scope_cannot_write(db):
    with as_key(["read"]):
        mcpserver.list_scan_targets()  # allowed
        with pytest.raises(mcpauth.MCPScopeError):
            mcpserver.create_scan_target(label="Nope", url="https://x.com")


# --------------------------------------------------------------------------- #
# REST + feed: the editable page header (singleton scan_page)
# --------------------------------------------------------------------------- #

def test_scan_page_header_crud_and_scope(client, rw_key, ro_key):
    # default header before any save: both fields null (the page then shows the
    # built-in default copy).
    got = client.get("/scan/page", headers=_h(ro_key)).json()
    assert got["id"] == 1 and got["title"] is None and got["description"] is None

    # write is gated
    assert client.patch("/scan/page", json={"title": "Hi"}, headers=_h(ro_key)).status_code == 403

    # set a custom heading
    saved = client.patch(
        "/scan/page",
        json={"title": "Welcome to the Hackathon", "description": "Scan to check in."},
        headers=_h(rw_key),
    ).json()
    assert saved["title"] == "Welcome to the Hackathon"
    assert saved["description"] == "Scan to check in."

    # it persists
    assert client.get("/scan/page", headers=_h(ro_key)).json()["title"] == "Welcome to the Hackathon"

    # a blank value clears just that field (→ null = default shows again); the
    # untouched field is left as-is (PATCH semantics).
    cleared = client.patch("/scan/page", json={"title": "   "}, headers=_h(rw_key)).json()
    assert cleared["title"] is None
    assert cleared["description"] == "Scan to check in."

    # over-long title is rejected (varchar(120) guard → a clean 422, not a 500)
    assert client.patch("/scan/page", json={"title": "x" * 121},
                        headers=_h(rw_key)).status_code == 422


def test_public_scan_feed_uses_custom_header(client, rw_key):
    client.patch("/scan/page", json={"title": "Big Night", "description": "Tap in."},
                 headers=_h(rw_key))
    feed = client.get("/website/scan").json()
    assert feed["title"] == "Big Night" and feed["description"] == "Tap in."
    assert feed["targets"] == []


def test_mcp_scan_page_round_trip(db):
    with as_key(["read", "write"]):
        before = mcpserver.get_scan_page()
        assert before["title"] is None and before["description"] is None

        saved = mcpserver.update_scan_page(title="Scan in", description="No app needed")
        assert saved["title"] == "Scan in" and saved["description"] == "No app needed"

        # an empty string clears a field back to the default copy; an omitted
        # field is left unchanged.
        cleared = mcpserver.update_scan_page(title="")
        assert cleared["title"] is None and cleared["description"] == "No app needed"


def test_mcp_scan_page_read_scope_cannot_write(db):
    with as_key(["read"]):
        mcpserver.get_scan_page()  # allowed
        with pytest.raises(mcpauth.MCPScopeError):
            mcpserver.update_scan_page(title="Nope")
