"""Tests for the features added in the MCP/self-service-token expansion:

* New REST routers — partners, event speakers/sponsors/partners, sponsor contacts.
* The self-service key mint endpoint (`POST /admin/keys/self`).
* A representative slice of the new MCP tools (scope gating + round trips).
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
# REST: partners
# --------------------------------------------------------------------------- #

def test_partners_crud_and_scope(client, rw_key, ro_key):
    assert client.post("/partners", json={"name": "GDG"}, headers=_h(ro_key)).status_code == 403
    r = client.post("/partners", json={"name": "GDG Burwood", "website": "https://gdg.dev"},
                    headers=_h(rw_key))
    assert r.status_code == 201
    pid = r.json()["id"]
    assert client.patch(f"/partners/{pid}", json={"notes": "co-host"}, headers=_h(rw_key)).json()["notes"] == "co-host"
    assert client.get("/partners", headers=_h(ro_key)).json()[0]["name"] == "GDG Burwood"
    assert client.post(f"/partners/{pid}/archive", headers=_h(rw_key)).json()["archived"] is True
    assert client.get("/partners", headers=_h(ro_key)).json() == []  # archived excluded


# --------------------------------------------------------------------------- #
# REST: event relations (speakers / sponsor links / partner links)
# --------------------------------------------------------------------------- #

def test_event_speakers_and_links(client, rw_key):
    ev = client.post("/events-api", json={"name": "AI Night", "start_date": "2026-09-01"},
                     headers=_h(rw_key)).json()
    eid = ev["id"]

    # speaker needs a name or person_id
    assert client.post(f"/events-api/{eid}/speakers", json={"title": "nobody"},
                       headers=_h(rw_key)).status_code == 422
    sp = client.post(f"/events-api/{eid}/speakers",
                     json={"name": "Ada Lovelace", "title": "Pioneer"}, headers=_h(rw_key))
    assert sp.status_code == 201
    speaker_id = sp.json()["id"]
    assert len(client.get(f"/events-api/{eid}/speakers", headers=_h(rw_key)).json()) == 1
    client.delete(f"/events-api/{eid}/speakers/{speaker_id}", headers=_h(rw_key))
    assert client.get(f"/events-api/{eid}/speakers", headers=_h(rw_key)).json() == []

    # sponsor link is idempotent and hard-unlinks
    sponsor = client.post("/sponsors", json={"organisation": "ACME"}, headers=_h(rw_key)).json()
    assert client.post(f"/events-api/{eid}/sponsors", json={"sponsor_id": 999},
                       headers=_h(rw_key)).status_code == 404
    client.post(f"/events-api/{eid}/sponsors", json={"sponsor_id": sponsor["id"], "tier": "Gold"},
                headers=_h(rw_key))
    client.post(f"/events-api/{eid}/sponsors", json={"sponsor_id": sponsor["id"], "tier": "Platinum"},
                headers=_h(rw_key))  # re-link updates, no dupe
    links = client.get(f"/events-api/{eid}/sponsors", headers=_h(rw_key)).json()
    assert len(links) == 1 and links[0]["tier"] == "Platinum"
    assert client.delete(f"/events-api/{eid}/sponsors/{sponsor['id']}", headers=_h(rw_key)).status_code == 204

    # partner link
    partner = client.post("/partners", json={"name": "WIT"}, headers=_h(rw_key)).json()
    client.post(f"/events-api/{eid}/partners", json={"partner_id": partner["id"], "role": "Co-host"},
                headers=_h(rw_key))
    assert len(client.get(f"/events-api/{eid}/partners", headers=_h(rw_key)).json()) == 1


# --------------------------------------------------------------------------- #
# REST: sponsor contacts
# --------------------------------------------------------------------------- #

def test_sponsor_contacts(client, rw_key):
    sponsor = client.post("/sponsors", json={"organisation": "Globex"}, headers=_h(rw_key)).json()
    sid = sponsor["id"]
    assert client.post(f"/sponsors/{sid}/contacts", json={"role": "Contact"},
                       headers=_h(rw_key)).status_code == 422  # needs name/person_id
    c = client.post(f"/sponsors/{sid}/contacts",
                    json={"name": "Hank Scorpio", "role": "Signatory"}, headers=_h(rw_key))
    assert c.status_code == 201
    cid = c.json()["id"]
    assert client.patch(f"/sponsors/{sid}/contacts/{cid}", json={"email": "hank@globex.com"},
                        headers=_h(rw_key)).json()["email"] == "hank@globex.com"
    assert len(client.get(f"/sponsors/{sid}/contacts", headers=_h(rw_key)).json()) == 1
    assert client.delete(f"/sponsors/{sid}/contacts/{cid}", headers=_h(rw_key)).status_code == 204
    assert client.get(f"/sponsors/{sid}/contacts", headers=_h(rw_key)).json() == []


# --------------------------------------------------------------------------- #
# Self-service key mint
# --------------------------------------------------------------------------- #

def test_self_mint_enforces_subset(client, db):
    service_key = _make_key(db, ["read", "write", "trigger"], "service")

    # a subset of the caller's scopes is allowed
    r = client.post(
        "/admin/keys/self",
        json={"name": "Alex MCP", "scopes": ["read", "write"], "owner": "appuser:42"},
        headers=_h(service_key),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["raw_key"].startswith("dsec_live_")
    assert sorted(body["scopes"]) == ["read", "write"]
    row = db.get(models.APIKey, body["id"])
    assert row.created_by == "appuser:42"

    # cannot mint a scope the caller key lacks (no privilege escalation)
    r = client.post(
        "/admin/keys/self",
        json={"name": "sneaky", "scopes": ["ingest"], "owner": "appuser:42"},
        headers=_h(service_key),
    )
    assert r.status_code == 403

    # unknown scope -> 400
    r = client.post(
        "/admin/keys/self",
        json={"name": "bad", "scopes": ["superuser"], "owner": "appuser:42"},
        headers=_h(service_key),
    )
    assert r.status_code == 400

    # unauthenticated -> 401
    assert client.post("/admin/keys/self",
                       json={"name": "x", "scopes": ["read"], "owner": "appuser:1"}).status_code == 401


# --------------------------------------------------------------------------- #
# MCP tools (direct calls with the auth contextvar set)
# --------------------------------------------------------------------------- #

def test_mcp_registry_includes_new_tools():
    import asyncio

    names = {t.name for t in asyncio.run(mcpserver.mcp.list_tools())}
    assert {
        "list_partners", "create_partner", "update_partner",
        "add_event_speaker", "list_event_speakers", "remove_event_speaker",
        "link_event_sponsor", "unlink_event_sponsor",
        "link_event_partner", "unlink_event_partner",
        "list_sponsor_contacts", "add_sponsor_contact",
        "list_sponsor_packages", "create_sponsor_package", "update_sponsor_package",
        "delete_sponsor_package", "list_sponsor_leads", "update_sponsor_lead",
        "update_person", "list_media", "list_attachments", "archive_event",
    } <= names


def test_mcp_event_publish_and_lineup(db):
    with as_key(["read", "write"]):
        ev = mcpserver.create_event(name="Showcase", start_date="2026-10-01", is_public=True)
        assert ev["is_public"] is True

        partner = mcpserver.create_partner(name="GDG")
        link = mcpserver.link_event_partner(ev["id"], partner["id"], role="Co-host")
        assert link["partner_id"] == partner["id"]
        assert len(mcpserver.list_event_partners(ev["id"])) == 1

        sp = mcpserver.add_event_speaker(ev["id"], name="Grace Hopper", title="Rear Admiral")
        assert sp["name"] == "Grace Hopper"
        assert len(mcpserver.list_event_speakers(ev["id"])) == 1


def test_mcp_sponsor_packages_and_contacts(db):
    with as_key(["read", "write"]):
        pkg = mcpserver.create_sponsor_package(name="Headline", price="from $1000",
                                               includes=["Logo", "Booth"])
        assert pkg["name"] == "Headline"
        assert mcpserver.list_sponsor_packages()[0]["price"] == "from $1000"
        mcpserver.delete_sponsor_package(pkg["id"])
        assert mcpserver.list_sponsor_packages() == []

        sponsor = mcpserver.create_sponsor(organisation="ACME", relationship_type="Partner",
                                           support_types=["Venue"], show_on_website=True)
        assert sponsor["relationship_type"] == "Partner"
        contact = mcpserver.add_sponsor_contact(sponsor["id"], name="Wile E.", role="Organiser")
        assert len(mcpserver.list_sponsor_contacts(sponsor["id"])) == 1
        mcpserver.remove_sponsor_contact(contact["id"])
        assert mcpserver.list_sponsor_contacts(sponsor["id"]) == []


def test_mcp_read_scope_cannot_write_new_tools(db):
    with as_key(["read"]):
        mcpserver.list_partners()  # allowed
        with pytest.raises(mcpauth.MCPScopeError):
            mcpserver.create_partner(name="Nope")
