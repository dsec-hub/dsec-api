"""Tests for the DSEC link-tree feature (dsec-api portion).

Covers the REST CRUD + scope gating, reorder, the singleton profile upsert, the
public `/website/linktree` feed (visible-only + ordering), and the MCP tools.
Follows tests/test_new_features.py style/fixtures.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from app import models
from app.core.apikeys import generate_key
from app.features.mcp import auth as mcpauth
from app.features.mcp import server as mcpserver


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
# REST: link CRUD + scope gating
# --------------------------------------------------------------------------- #

def test_links_crud_and_scope(client, rw_key, ro_key):
    # write is gated
    assert client.post("/links", json={"title": "X", "url": "/x"},
                       headers=_h(ro_key)).status_code == 403

    # create
    r = client.post(
        "/links",
        json={"title": "Join the Discord", "url": "https://discord.gg/dsec",
              "icon": "🎮", "accent": "blue", "subtitle": "500+ members"},
        headers=_h(rw_key),
    )
    assert r.status_code == 201
    body = r.json()
    lid = body["id"]
    assert body["title"] == "Join the Discord"
    assert body["icon"] == "🎮" and body["accent"] == "blue"
    assert body["is_visible"] is True and body["archived"] is False
    assert body["display_order"] == 0

    # read one
    assert client.get(f"/links/{lid}", headers=_h(ro_key)).json()["url"] == "https://discord.gg/dsec"
    assert client.get("/links/99999", headers=_h(ro_key)).status_code == 404

    # patch
    patched = client.patch(f"/links/{lid}", json={"subtitle": "600+ members", "is_visible": False},
                           headers=_h(rw_key)).json()
    assert patched["subtitle"] == "600+ members" and patched["is_visible"] is False

    # list shows hidden by default (dashboard view)
    listed = client.get("/links", headers=_h(ro_key)).json()
    assert [ln["id"] for ln in listed] == [lid]

    # include_hidden=false drops it
    assert client.get("/links", params={"include_hidden": False}, headers=_h(ro_key)).json() == []

    # archive (soft delete)
    assert client.post(f"/links/{lid}/archive", headers=_h(rw_key)).json()["archived"] is True
    assert client.get("/links", headers=_h(ro_key)).json() == []          # excluded by default
    assert len(client.get("/links", params={"include_archived": True},
                          headers=_h(ro_key)).json()) == 1                 # opt-in shows it


def test_links_create_requires_title_and_url(client, rw_key):
    assert client.post("/links", json={"title": "No url"}, headers=_h(rw_key)).status_code == 422
    assert client.post("/links", json={"url": "/no-title"}, headers=_h(rw_key)).status_code == 422


def test_links_url_validation(client, rw_key):
    # dangerous schemes are rejected on create (XSS guard)
    assert client.post("/links", json={"title": "Evil", "url": "javascript:alert(1)"},
                       headers=_h(rw_key)).status_code == 422
    assert client.post("/links", json={"title": "Evil", "url": "data:text/html,<script>"},
                       headers=_h(rw_key)).status_code == 422

    # a relative in-app path and a mailto: scheme are accepted
    rel = client.post("/links", json={"title": "Events", "url": "/events"}, headers=_h(rw_key))
    assert rel.status_code == 201 and rel.json()["url"] == "/events"
    mail = client.post("/links", json={"title": "Email", "url": "mailto:hi@dsec.club"},
                       headers=_h(rw_key))
    assert mail.status_code == 201 and mail.json()["url"] == "mailto:hi@dsec.club"

    # PATCH is gated by the same rule
    lid = rel.json()["id"]
    assert client.patch(f"/links/{lid}", json={"url": "javascript:alert(1)"},
                        headers=_h(rw_key)).status_code == 422
    assert client.patch(f"/links/{lid}", json={"url": "https://dsec.club"},
                        headers=_h(rw_key)).status_code == 200


def test_links_accent_max_length(client, rw_key):
    # an over-long accent returns a clean 422, not a DB 500
    assert client.post("/links", json={"title": "X", "url": "/x", "accent": "x" * 17},
                       headers=_h(rw_key)).status_code == 422


# --------------------------------------------------------------------------- #
# REST: reorder
# --------------------------------------------------------------------------- #

def test_links_reorder(client, rw_key):
    ids = [
        client.post("/links", json={"title": t, "url": f"/{t}"}, headers=_h(rw_key)).json()["id"]
        for t in ("a", "b", "c")
    ]
    # reverse the order
    reordered = client.post("/links/reorder", json={"ordered_ids": list(reversed(ids))},
                            headers=_h(rw_key)).json()
    assert [ln["id"] for ln in reordered] == list(reversed(ids))
    assert [ln["display_order"] for ln in reordered] == [0, 1, 2]
    # list reflects the persisted order
    assert [ln["id"] for ln in client.get("/links", headers=_h(rw_key)).json()] == list(reversed(ids))

    # reorder is write-gated
    assert client.post("/links/reorder", json={"ordered_ids": ids}).status_code == 401


# --------------------------------------------------------------------------- #
# REST: profile (singleton, upsert) + static/id route collision
# --------------------------------------------------------------------------- #

def test_link_profile_upsert(client, rw_key, ro_key):
    # GET returns a default object even before any row is saved (id=1).
    default = client.get("/links/profile", headers=_h(ro_key)).json()
    assert default["id"] == 1
    assert default["title"] == "DSEC"
    assert default["tagline"] == "Deakin Software Engineering Club"
    assert default["mascot"] == "duck-mascot"

    # PATCH upserts the singleton; write-gated.
    assert client.patch("/links/profile", json={"title": "DSEC Hub"},
                        headers=_h(ro_key)).status_code == 403
    updated = client.patch("/links/profile",
                           json={"title": "DSEC Crew", "tagline": "Deakin Cyber",
                                 "mascot": "duck-wave"}, headers=_h(rw_key)).json()
    assert updated["id"] == 1 and updated["title"] == "DSEC Crew"
    assert updated["tagline"] == "Deakin Cyber" and updated["mascot"] == "duck-wave"

    # persisted (a re-read returns the saved row, not the default)
    assert client.get("/links/profile", headers=_h(ro_key)).json()["title"] == "DSEC Crew"

    # "/profile" must NOT be parsed as an integer id route
    assert client.get("/links/profile", headers=_h(ro_key)).status_code == 200


# --------------------------------------------------------------------------- #
# Public website: /website/linktree feed (visible-only + ordering)
# --------------------------------------------------------------------------- #

def test_public_linktree_feed(client, rw_key):
    # three links: one hidden, one archived, both must be excluded; the rest
    # come back in display_order.
    second = client.post("/links", json={"title": "Website", "url": "/", "display_order": 2},
                         headers=_h(rw_key)).json()
    first = client.post("/links", json={"title": "Discord", "url": "https://discord.gg/dsec",
                                        "display_order": 1, "icon": "🎮", "accent": "blue"},
                        headers=_h(rw_key)).json()
    hidden = client.post("/links", json={"title": "Secret", "url": "/secret", "is_visible": False},
                         headers=_h(rw_key)).json()
    gone = client.post("/links", json={"title": "Old", "url": "/old"}, headers=_h(rw_key)).json()
    client.post(f"/links/{gone['id']}/archive", headers=_h(rw_key))

    # customise the profile header + the canonical socials. `email` is stored
    # bare (a leading mailto: is stripped); the four URL socials must be http(s).
    client.patch("/links/profile", json={
        "title": "DSEC", "tagline": "Tag", "mascot": "duck-trophy",
        "instagram": "https://instagram.com/dsec", "discord": "https://discord.gg/dsec",
        "linkedin": "https://linkedin.com/company/dsec", "github": "https://github.com/dsec-hub",
        "email": "mailto:admin@dsec.club",
    }, headers=_h(rw_key))

    feed = client.get("/website/linktree").json()  # public, no auth
    assert feed["profile"] == {
        "title": "DSEC", "tagline": "Tag", "mascot": "duck-trophy",
        "socials": {
            "instagram": "https://instagram.com/dsec", "discord": "https://discord.gg/dsec",
            "linkedin": "https://linkedin.com/company/dsec", "github": "https://github.com/dsec-hub",
            "email": "admin@dsec.club",
        },
    }

    titles = [ln["title"] for ln in feed["links"]]
    assert titles == ["Discord", "Website"]  # display_order asc; hidden + archived excluded
    disc = feed["links"][0]
    assert disc["icon"] == "🎮" and disc["accent"] == "blue" and disc["display_order"] == 1
    # public shape carries only the safe fields
    assert set(disc.keys()) == {"title", "subtitle", "url", "icon", "accent", "display_order"}

    # unused ids referenced only to satisfy linters / make intent explicit
    assert first["id"] and second["id"] and hidden["id"]


def test_public_linktree_default_profile_when_empty(client):
    """With nothing saved, the feed still returns the default header and no links
    (so the website never renders an empty page)."""
    feed = client.get("/website/linktree").json()
    assert feed["profile"] == {
        "title": "DSEC", "tagline": "Deakin Software Engineering Club", "mascot": "duck-mascot",
        "socials": {"instagram": None, "discord": None, "linkedin": None,
                    "github": None, "email": None},
    }
    assert feed["links"] == []


# --------------------------------------------------------------------------- #
# MCP tools
# --------------------------------------------------------------------------- #

def test_mcp_registry_includes_link_tools():
    import asyncio

    names = {t.name for t in asyncio.run(mcpserver.mcp.list_tools())}
    assert {
        "list_links", "get_link", "create_link", "update_link", "archive_link",
        "reorder_links", "get_link_profile", "update_link_profile",
    } <= names


def test_mcp_links_round_trip(db):
    with as_key(["read", "write"]):
        a = mcpserver.create_link(title="Discord", url="https://discord.gg/dsec", icon="🎮")
        b = mcpserver.create_link(title="Instagram", url="https://instagram.com/dsec")
        assert a["title"] == "Discord" and a["icon"] == "🎮"

        # reorder: b before a
        reordered = mcpserver.reorder_links([b["id"], a["id"]])
        assert [ln["id"] for ln in reordered] == [b["id"], a["id"]]

        # hide a, then the public-style (hidden-excluded) list drops it
        mcpserver.update_link(a["id"], is_visible=False)
        visible = mcpserver.list_links(include_hidden=False)
        assert [ln["id"] for ln in visible] == [b["id"]]

        # profile upsert
        prof = mcpserver.update_link_profile(title="DSEC", tagline="Cyber", mascot="duck-wave")
        assert prof["mascot"] == "duck-wave"
        assert mcpserver.get_link_profile()["title"] == "DSEC"

        # archive both — the default list (archived excluded) is then empty, but
        # `a` is only hidden (not archived) so it still shows until archived.
        assert [ln["id"] for ln in mcpserver.list_links()] == [b["id"], a["id"]]  # a hidden but visible-in-dash
        mcpserver.archive_link(a["id"])
        mcpserver.archive_link(b["id"])
        assert mcpserver.list_links() == []
        assert len(mcpserver.list_links(include_archived=True)) == 2


def test_mcp_links_read_scope_cannot_write(db):
    with as_key(["read"]):
        mcpserver.list_links()  # allowed
        with pytest.raises(mcpauth.MCPScopeError):
            mcpserver.create_link(title="Nope", url="/nope")
