"""Tests for the features added in the MCP/self-service-token expansion:

* New REST routers — partners, event speakers/sponsors/partners, sponsor contacts.
* The self-service key mint endpoint (`POST /admin/keys/self`).
* A representative slice of the new MCP tools (scope gating + round trips).
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date

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


def test_event_connections(client, rw_key, ro_key):
    a = client.post("/events-api", json={"name": "Kickoff Night", "start_date": "2026-09-01",
                                         "is_public": True}, headers=_h(rw_key)).json()
    b = client.post("/events-api", json={"name": "Closing Gala", "start_date": "2026-11-01",
                                         "is_public": True}, headers=_h(rw_key)).json()
    aid, bid = a["id"], b["id"]

    # write-gated; can't connect to a missing event; can't connect to itself
    assert client.post(f"/events-api/{aid}/connections", json={"other_event_id": bid},
                       headers=_h(ro_key)).status_code == 403
    assert client.post(f"/events-api/{aid}/connections", json={"other_event_id": 9999},
                       headers=_h(rw_key)).status_code == 404
    assert client.post(f"/events-api/{aid}/connections", json={"other_event_id": aid},
                       headers=_h(rw_key)).status_code == 422

    # connect A -> B with a label
    r = client.post(f"/events-api/{aid}/connections",
                    json={"other_event_id": bid, "label": "Series"}, headers=_h(rw_key))
    assert r.status_code == 201
    assert r.json()["other_event_id"] == bid and r.json()["label"] == "Series"

    # symmetric: B sees A too (order-independent), resolved relative to B
    from_b = client.get(f"/events-api/{bid}/connections", headers=_h(ro_key)).json()
    assert len(from_b) == 1 and from_b[0]["other_event_id"] == aid
    assert from_b[0]["other_event_name"] == "Kickoff Night"

    # idempotent re-link from the other side updates the label — no duplicate row
    client.post(f"/events-api/{bid}/connections",
                json={"other_event_id": aid, "label": "Follow-up"}, headers=_h(rw_key))
    from_a = client.get(f"/events-api/{aid}/connections", headers=_h(rw_key)).json()
    assert len(from_a) == 1 and from_a[0]["label"] == "Follow-up"

    # published connections surface on the public website feed
    feed = client.get("/website/events").json()
    slug_a = next(e["slug"] for e in feed if e["title"] == "Kickoff Night")
    detail = client.get(f"/website/events/{slug_a}").json()
    assert [e["title"] for e in detail["related_events"]] == ["Closing Gala"]

    # a DRAFT connected event shows in the dashboard but never leaks to the public feed
    c = client.post("/events-api", json={"name": "Secret Planning", "start_date": "2026-10-01"},
                    headers=_h(rw_key)).json()  # is_public defaults to False
    client.post(f"/events-api/{aid}/connections", json={"other_event_id": c["id"]}, headers=_h(rw_key))
    detail = client.get(f"/website/events/{slug_a}").json()
    assert [e["title"] for e in detail["related_events"]] == ["Closing Gala"]  # draft excluded
    assert len(client.get(f"/events-api/{aid}/connections", headers=_h(rw_key)).json()) == 2  # dashboard sees both

    # unlink is order-independent and hard-deletes
    assert client.delete(f"/events-api/{aid}/connections/{bid}", headers=_h(rw_key)).status_code == 204
    assert client.get(f"/events-api/{bid}/connections", headers=_h(rw_key)).json() == []


# --------------------------------------------------------------------------- #
# Public website: team feed + per-person profile page
# --------------------------------------------------------------------------- #

def test_public_team_feed_and_member_detail(client, db):
    """The published roster surfaces on /website/team with a stable slug, and each
    person's /website/team/{slug} detail carries their role + the events/projects
    they lead (published only). Unpublished people never leak."""
    pres = models.Person(
        name="Ada Lovelace", type="Exec", role_title="President",
        committee="Executive", bio="Runs the club.", show_on_website=True,
        display_order=0, instagram="@ada", linkedin="/in/ada", github="adal",
        website="https://ada.dev", discord="ada#1",
    )
    lead = models.Person(
        name="Grace Hopper", type="Committee Lead", role_title="Web Lead",
        committee="Web Development", show_on_website=True, display_order=1,
    )
    hidden = models.Person(name="Secret Member", type="Committee Member", show_on_website=False)
    db.add_all([pres, lead, hidden])
    db.commit()

    ev = models.Event(name="Launch Night", start_date=date(2099, 9, 1),
                      is_public=True, event_lead_id=pres.id)
    draft = models.Event(name="Secret Planning", start_date=date(2099, 10, 1),
                         is_public=False, event_lead_id=pres.id)  # draft — must not leak
    proj = models.Project(name="Duck Bot", slug="duck-bot", summary="A bot.",
                          is_public=True, lead_id=pres.id)
    db.add_all([ev, draft, proj])
    db.commit()

    # List feed: only published people, in display order, each with a slug.
    feed = client.get("/website/team").json()
    assert [p["name"] for p in feed] == ["Ada Lovelace", "Grace Hopper"]  # hidden excluded
    ada = feed[0]
    assert ada["slug"] == "ada-lovelace"
    assert ada["type"] == "Exec" and ada["role"] == "President"
    assert ada["github"] == "adal" and "discord" not in ada  # discord is detail-only

    # Detail: full profile + the events/projects they lead (published only).
    detail = client.get("/website/team/ada-lovelace").json()
    assert detail["committee"] == "Executive" and detail["discord"] == "ada#1"
    assert [e["title"] for e in detail["led_events"]] == ["Launch Night"]  # draft excluded
    assert detail["led_events"][0]["upcoming"] is True
    assert [p["title"] for p in detail["led_projects"]] == ["Duck Bot"]

    # Unpublished + unknown slugs 404 (the slug must resolve in the published roster).
    assert client.get("/website/team/secret-member").status_code == 404
    assert client.get("/website/team/nobody").status_code == 404


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
        "list_event_connections", "link_event_connection", "unlink_event_connection",
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


def test_mcp_event_connections(db):
    with as_key(["read", "write"]):
        a = mcpserver.create_event(name="Hack A", start_date="2026-09-01")
        b = mcpserver.create_event(name="Hack B", start_date="2026-09-08")
        link = mcpserver.link_event_connection(a["id"], b["id"], label="Series")
        assert link["other_event_id"] == b["id"] and link["label"] == "Series"
        # symmetric: visible from B, resolved relative to B
        from_b = mcpserver.list_event_connections(b["id"])
        assert len(from_b) == 1 and from_b[0]["other_event_id"] == a["id"]
        # self-connection rejected
        with pytest.raises(ValueError):
            mcpserver.link_event_connection(a["id"], a["id"])
        # unlink is order-independent
        mcpserver.unlink_event_connection(b["id"], a["id"])
        assert mcpserver.list_event_connections(a["id"]) == []


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


# --------------------------------------------------------------------------- #
# Flagship marketing event: secrecy gating + public teaser funnel
# --------------------------------------------------------------------------- #

def test_flagship_secrecy_gating_and_signup(client, rw_key):
    """A flagship event in `teaser` state hides its real specifics on the public
    feed, exposes the flagship_* fields, declassifies on reveal, and feeds an
    idempotent public signup funnel (sponsor signups also seed a sponsor lead)."""
    # A published flagship event, still teasing, with real specifics + a line-up.
    ev = client.post(
        "/events-api",
        json={
            "name": "Operation Duckshot", "start_date": "2099-12-01",
            "is_public": True, "is_flagship": True, "flagship_theme": "nightrun",
            "flagship_state": "teaser",
            "flagship_teaser_title": "OPERATION DUCKSHOT",
            "flagship_teaser_body": "Something big is coming.",
            "flagship_reveal_at": "2099-11-25T18:00:00+00:00",
            "description": "TOP SECRET 48h hackathon.", "venue": "The Bunker",
            "ticket_url": "https://tickets.example/duckshot",
        },
        headers=_h(rw_key),
    ).json()
    eid = ev["id"]
    # EventOut round-trips the flagship fields.
    assert ev["is_flagship"] is True and ev["flagship_theme"] == "nightrun"
    assert ev["flagship_state"] == "teaser"
    client.post(f"/events-api/{eid}/speakers", json={"name": "Mystery Guest"}, headers=_h(rw_key))

    feed = client.get("/website/events").json()
    slug = next(e["slug"] for e in feed if e["title"] == "Operation Duckshot")
    assert slug == "operation-duckshot-2099-12-01"

    # Teaser gating: the safe shell + flagship_* remain; specifics are nulled.
    teaser = client.get(f"/website/events/{slug}").json()
    assert teaser["flagship"] is True and teaser["flagship_theme"] == "nightrun"
    assert teaser["flagship_state"] == "teaser"
    assert teaser["flagship_teaser_title"] == "OPERATION DUCKSHOT"
    assert teaser["flagship_teaser_body"] == "Something big is coming."
    assert teaser["flagship_reveal_at"].startswith("2099-11-25T18:00:00")
    assert teaser["title"] == "Operation Duckshot"        # kept
    assert teaser["date"] == "2099-12-01"                 # kept
    assert teaser["description"] is None                  # gated
    assert teaser["venue"] is None                        # gated
    assert teaser["ticket_url"] is None                   # gated
    assert teaser["speakers"] == []                       # gated

    # Reveal: declassify → everything is exposed as a normal event.
    client.patch(f"/events-api/{eid}", json={"flagship_state": "revealed"}, headers=_h(rw_key))
    revealed = client.get(f"/website/events/{slug}").json()
    assert revealed["flagship"] is True and revealed["flagship_state"] == "revealed"
    assert revealed["description"] == "TOP SECRET 48h hackathon."
    assert revealed["venue"] == "The Bunker"
    assert revealed["ticket_url"] == "https://tickets.example/duckshot"
    assert [s["name"] for s in revealed["speakers"]] == ["Mystery Guest"]

    # Public funnel: notify signup → ok, and a re-submit is idempotent (never 500).
    assert client.post(f"/website/flagship/{slug}/signup",
                       json={"kind": "notify", "email": "fan@example.com"}).json() == {"ok": True}
    assert client.post(f"/website/flagship/{slug}/signup",
                       json={"kind": "notify", "email": "fan@example.com"}).json() == {"ok": True}

    # Validation: bad kind → 422; unknown slug → 404.
    assert client.post(f"/website/flagship/{slug}/signup",
                       json={"kind": "bogus", "email": "x@y.com"}).status_code == 422
    assert client.post("/website/flagship/not-a-real-event/signup",
                       json={"kind": "notify", "email": "x@y.com"}).status_code == 404

    # Sponsor signup → ok AND seeds the existing sponsor-lead pipeline.
    assert client.post(f"/website/flagship/{slug}/signup",
                       json={"kind": "sponsor", "email": "ceo@acme.com",
                             "company": "ACME", "message": "We're in."}).json() == {"ok": True}
    leads = client.get("/sponsor-leads", headers=_h(rw_key)).json()
    seeded = next(l for l in leads if l["email"] == "ceo@acme.com")
    assert seeded["company"] == "ACME" and seeded["source"] == "flagship"


def test_flagship_signup_requires_flagship_event(client, rw_key):
    """A non-flagship event has no public funnel — its slug 404s on signup."""
    client.post("/events-api", json={"name": "Plain Meetup", "start_date": "2099-09-01",
                                     "is_public": True}, headers=_h(rw_key))
    feed = client.get("/website/events").json()
    slug = next(e["slug"] for e in feed if e["title"] == "Plain Meetup")
    assert client.post(f"/website/flagship/{slug}/signup",
                       json={"kind": "notify", "email": "x@y.com"}).status_code == 404


# --------------------------------------------------------------------------- #
# Event preview links ("see it before publishing")
# --------------------------------------------------------------------------- #

def test_event_preview_token_roundtrip():
    from app.features.website import preview

    tok = preview.make_preview_token(42)
    assert preview.verify_preview_token(tok) == 42
    # Tampered signature → rejected.
    flipped = tok[:-1] + ("A" if tok[-1] != "A" else "B")
    assert preview.verify_preview_token(flipped) is None
    # Re-pointed event id (keeping the original exp+sig) → rejected.
    assert preview.verify_preview_token("999." + tok.split(".", 1)[1]) is None
    # Malformed / empty → None, never raises.
    assert preview.verify_preview_token("garbage") is None
    assert preview.verify_preview_token("") is None
    # Past its expiry → None.
    assert preview.verify_preview_token(preview.make_preview_token(42, ttl=-10)) is None


def test_event_preview_endpoint_serves_draft(client, rw_key, ro_key):
    """A draft event is hidden from the public feed but visible via a preview link."""
    ev = client.post("/events-api", json={"name": "Secret Draft", "start_date": "2099-10-01"},
                     headers=_h(rw_key)).json()
    eid = ev["id"]
    # Draft (is_public defaults False) → absent from the public events feed.
    feed = client.get("/website/events").json()
    assert all(e["title"] != "Secret Draft" for e in feed)

    # The dashboard mints a preview link (read scope is enough)…
    link = client.get(f"/events-api/{eid}/preview-link", headers=_h(ro_key))
    assert link.status_code == 200
    path = link.json()["path"]
    assert path.startswith("/events/preview/")

    # …and the token renders the full draft via the public token-gated feed.
    got = client.get(f"/website{path}")
    assert got.status_code == 200
    assert got.json()["title"] == "Secret Draft"

    # A bad token 404s — drafts never leak without a valid link.
    assert client.get("/website/events/preview/not-a-token").status_code == 404


def test_event_preview_link_unknown_event_404(client, rw_key):
    assert client.get("/events-api/999999/preview-link", headers=_h(rw_key)).status_code == 404
