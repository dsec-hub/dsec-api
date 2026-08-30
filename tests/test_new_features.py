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
    # A full committee key. Since SEC-05, blanket read/write no longer reaches the
    # isolated finance/sponsors modules, so a key exercising those routes must hold
    # their per-module scopes explicitly.
    return _make_key(
        db,
        ["read", "write", "read:sponsors", "write:sponsors", "read:finance", "write:finance"],
        "rw",
    )


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
# NEW-APIROUTERS-06: nested mutations are scoped to the parent in their own URL
# --------------------------------------------------------------------------- #

def test_update_speaker_scoped_to_event_in_url(client, rw_key, db):
    a = client.post("/events-api", json={"name": "A", "start_date": "2099-01-01"}, headers=_h(rw_key)).json()
    b = client.post("/events-api", json={"name": "B", "start_date": "2099-02-01"}, headers=_h(rw_key)).json()
    sp = client.post(f"/events-api/{a['id']}/speakers", json={"name": "Ada"}, headers=_h(rw_key)).json()
    # Editing the speaker under event B (wrong parent) must 404 and change nothing.
    r = client.patch(f"/events-api/{b['id']}/speakers/{sp['id']}",
                     json={"name": "Hacked"}, headers=_h(rw_key))
    assert r.status_code == 404
    row = db.get(models.EventSpeaker, sp["id"])
    assert row.name == "Ada"
    # A non-existent parent 404s "event not found".
    r2 = client.patch(f"/events-api/999999/speakers/{sp['id']}",
                      json={"name": "X"}, headers=_h(rw_key))
    assert r2.status_code == 404 and "event not found" in r2.text
    # The correct URL still works.
    assert client.patch(f"/events-api/{a['id']}/speakers/{sp['id']}",
                        json={"name": "Ada L."}, headers=_h(rw_key)).json()["name"] == "Ada L."


def test_remove_speaker_scoped_to_event_in_url(client, rw_key, db):
    a = client.post("/events-api", json={"name": "A", "start_date": "2099-01-01"}, headers=_h(rw_key)).json()
    b = client.post("/events-api", json={"name": "B", "start_date": "2099-02-01"}, headers=_h(rw_key)).json()
    sp = client.post(f"/events-api/{a['id']}/speakers", json={"name": "Ada"}, headers=_h(rw_key)).json()
    r = client.delete(f"/events-api/{b['id']}/speakers/{sp['id']}", headers=_h(rw_key))
    assert r.status_code == 404
    db.expire_all()
    assert db.get(models.EventSpeaker, sp["id"]).archived is False  # soft-delete didn't fire
    # correct URL archives it
    assert client.delete(f"/events-api/{a['id']}/speakers/{sp['id']}", headers=_h(rw_key)).status_code == 204


def test_update_contact_scoped_to_sponsor_in_url(client, rw_key, db):
    a = client.post("/sponsors", json={"organisation": "A"}, headers=_h(rw_key)).json()
    b = client.post("/sponsors", json={"organisation": "B"}, headers=_h(rw_key)).json()
    c = client.post(f"/sponsors/{a['id']}/contacts", json={"name": "Hank"}, headers=_h(rw_key)).json()
    r = client.patch(f"/sponsors/{b['id']}/contacts/{c['id']}",
                     json={"email": "x@y.com"}, headers=_h(rw_key))
    assert r.status_code == 404
    row = db.get(models.SponsorContact, c["id"])
    assert row.email is None
    r2 = client.patch(f"/sponsors/999999/contacts/{c['id']}",
                      json={"email": "z@y.com"}, headers=_h(rw_key))
    assert r2.status_code == 404 and "sponsor not found" in r2.text


def test_remove_contact_scoped_to_sponsor_in_url(client, rw_key, db):
    a = client.post("/sponsors", json={"organisation": "A"}, headers=_h(rw_key)).json()
    b = client.post("/sponsors", json={"organisation": "B"}, headers=_h(rw_key)).json()
    c = client.post(f"/sponsors/{a['id']}/contacts", json={"name": "Hank"}, headers=_h(rw_key)).json()
    r = client.delete(f"/sponsors/{b['id']}/contacts/{c['id']}", headers=_h(rw_key))
    assert r.status_code == 404
    db.expire_all()
    assert db.get(models.SponsorContact, c["id"]).archived is False
    assert client.delete(f"/sponsors/{a['id']}/contacts/{c['id']}", headers=_h(rw_key)).status_code == 204


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


# SEC-05 minting bootstrap: after isolating finance/sponsors, the owner must still
# have a way to mint the per-module replacement keys. It is the basic-auth owner
# endpoint (POST /admin/keys), which has no scope-algebra restriction — so the
# bootstrap trap Codex worried about does not exist.
_ADMIN_BASIC = ("admin", "test-dashboard-pass")  # conftest DASHBOARD_USER / _PASS


def test_owner_basic_auth_can_mint_isolated_scope_keys(client, db):
    r = client.post(
        "/admin/keys",
        json={"name": "treasurer", "scopes": ["read:finance", "write:sponsors"]},
        auth=_ADMIN_BASIC,
    )
    assert r.status_code == 200, r.text
    assert sorted(r.json()["scopes"]) == ["read:finance", "write:sponsors"]


def test_self_mint_legacy_caller_cannot_escalate_into_isolated_modules(client, db):
    """SEC-05 boundary: a legacy read/write service key can NOT mint a
    finance/sponsors key through /keys/self (no coarse→isolated bypass). The
    owner uses the basic-auth path above to bootstrap those instead."""
    legacy = _make_key(db, ["read", "write"], "legacy-svc")
    r = client.post(
        "/admin/keys/self",
        json={"name": "esc", "scopes": ["write:sponsors"], "owner": "appuser:9"},
        headers=_h(legacy),
    )
    assert r.status_code == 403


# SEC-07d: dsec-hub used to revoke a user's own key with a direct Neon write, which
# bypassed the parent->child cascade. It now calls POST /admin/keys/revoke-for-owner,
# authenticated by its service key and scoped to the caller-supplied owner label.
def _svc_key(db, name="hub-svc", scopes=("read", "write")):
    """A service key (raw + row), the parent of everything it mints via /keys/self."""
    gen = generate_key()
    row = models.APIKey(name=name, prefix=gen.prefix, key_hash=gen.key_hash, scopes=list(scopes))
    db.add(row)
    db.commit()
    db.refresh(row)
    return gen.raw_key, row


def _owned_key(db, owner, name="owned", parent_id=None):
    """A dashboard-user self-service key (created_by = appuser:<id>). Returns the row."""
    gen = generate_key()
    row = models.APIKey(
        name=name, prefix=gen.prefix, key_hash=gen.key_hash, scopes=["read"],
        created_by=owner, parent_key_id=parent_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_revoke_for_owner_cascades_to_children(client, db):
    svc_raw, svc = _svc_key(db)
    # user keys are minted BY the service key, so parent_key_id == svc.id
    parent = _owned_key(db, "appuser:42", "parent", parent_id=svc.id)
    child = _owned_key(db, "appuser:42", "child", parent_id=parent.id)
    grandchild = _owned_key(db, "appuser:42", "grandchild", parent_id=child.id)

    r = client.post(
        "/admin/keys/revoke-for-owner",
        json={"key_id": parent.id, "owner": "appuser:42"},
        headers=_h(svc_raw),
    )
    assert r.status_code == 200, r.text
    assert r.json()["revoked"] is True
    for k in (parent, child, grandchild):
        db.refresh(k)
        assert k.revoked is True  # cascade reached every descendant


def test_revoke_for_owner_rejects_self_service_caller(client, db):
    """SEC-07d finding-1 regression: require_api_key() accepts ANY valid key, so a
    dashboard user must not be able to revoke ANOTHER user's key using their own
    self-service key as the bearer — even when they pass the victim's REAL owner
    label. A self-service caller (created_by = appuser:<id>) is rejected 403 and the
    victim key survives."""
    svc_raw, svc = _svc_key(db)
    victim = _owned_key(db, "appuser:99", "victim", parent_id=svc.id)
    # the attacker's own self-service key (created_by is an appuser label)
    attacker_gen = generate_key()
    db.add(models.APIKey(
        name="attacker", prefix=attacker_gen.prefix, key_hash=attacker_gen.key_hash,
        scopes=["read", "write"], created_by="appuser:42", parent_key_id=svc.id,
    ))
    db.commit()

    r = client.post(
        "/admin/keys/revoke-for-owner",
        json={"key_id": victim.id, "owner": "appuser:99"},  # victim's ACTUAL owner
        headers=_h(attacker_gen.raw_key),
    )
    assert r.status_code == 403, r.text
    db.refresh(victim)
    assert victim.revoked is False


def test_revoke_for_owner_rejects_narrow_service_key(client, db):
    """SEC-07d round-3 regression: a leaked NARROW service key (no blanket write —
    e.g. the public dsec-games key, created_by is non-appuser so it passes the caller
    gate) must NOT revoke user tokens. The write:keys scope gate rejects it (403)."""
    svc_raw, svc = _svc_key(db)
    victim = _owned_key(db, "appuser:7", "victim", parent_id=svc.id)
    narrow = _make_key(db, ["read:games", "write:games"], "games")  # non-appuser, no blanket write

    r = client.post(
        "/admin/keys/revoke-for-owner",
        json={"key_id": victim.id, "owner": "appuser:7"},
        headers=_h(narrow),
    )
    assert r.status_code == 403, r.text
    db.refresh(victim)
    assert victim.revoked is False


def test_revoke_for_owner_scopes_to_owner_and_revokes_pre_lineage(client, db):
    """The service key can only revoke keys whose created_by matches the passed
    owner: an admin-minted key (username owner) and a missing id both 404. But a
    PRE-LINEAGE user key (parent_key_id NULL, as the migration left every existing
    row) with a matching owner IS revocable — a direct-parent check would have
    wrongly 404'd it (round-2 finding)."""
    svc_raw, svc = _svc_key(db)
    admin_key = _owned_key(db, "cleo", "admin-minted", parent_id=svc.id)  # wrong owner label
    for kid in (admin_key.id, 999999):
        r = client.post(
            "/admin/keys/revoke-for-owner",
            json={"key_id": kid, "owner": "appuser:42"},
            headers=_h(svc_raw),
        )
        assert r.status_code == 404, (kid, r.text)
    db.refresh(admin_key)
    assert admin_key.revoked is False

    pre_lineage = _owned_key(db, "appuser:42", "legacy", parent_id=None)  # NULL parent
    r = client.post(
        "/admin/keys/revoke-for-owner",
        json={"key_id": pre_lineage.id, "owner": "appuser:42"},
        headers=_h(svc_raw),
    )
    assert r.status_code == 200, r.text
    db.refresh(pre_lineage)
    assert pre_lineage.revoked is True


def test_revoke_for_owner_validates_owner_label_and_auth(client, db):
    svc_raw, svc = _svc_key(db)
    k = _owned_key(db, "appuser:1", "k", parent_id=svc.id)
    # malformed owner label -> 400 (before any lookup)
    assert client.post(
        "/admin/keys/revoke-for-owner",
        json={"key_id": k.id, "owner": "appuser:1\n"},
        headers=_h(svc_raw),
    ).status_code == 400
    # unauthenticated -> 401
    assert client.post(
        "/admin/keys/revoke-for-owner",
        json={"key_id": k.id, "owner": "appuser:1"},
    ).status_code == 401
    db.refresh(k)
    assert k.revoked is False


def test_self_mint_rejected_when_caller_key_revoked(client, db):
    """SEC-07d mint-race guard (auth-level slice): a revoked service key cannot mint.
    verify_key already rejects revoked keys at auth; the db.refresh(with_for_update)
    re-check covers the narrower window where the parent is revoked mid-request (see
    test_locked_refresh_sees_out_of_band_revocation for the mechanism)."""
    svc_raw, svc = _svc_key(db)
    svc.revoked = True
    db.commit()
    r = client.post(
        "/admin/keys/self",
        json={"name": "x", "scopes": ["read"], "owner": "appuser:1"},
        headers=_h(svc_raw),
    )
    assert r.status_code == 401


def test_locked_refresh_sees_out_of_band_revocation(db):
    """SEC-07d mint-race mechanism: after the limiter's commit expires the caller ORM
    object, a bare select(...).with_for_update() would return the stale identity-map
    row (revoked=False). Session.refresh(..., with_for_update=True) must repopulate it
    so a revocation committed by another session is seen. This guards the mint-race
    fix against silently regressing to the stale-read behaviour."""
    from sqlalchemy import update as sa_update

    from app.db import SessionLocal

    gen = generate_key()
    row = models.APIKey(name="svc", prefix=gen.prefix, key_hash=gen.key_hash, scopes=["read"])
    db.add(row)
    db.commit()
    key_id = row.id

    caller = db.get(models.APIKey, key_id)
    assert caller.revoked is False
    db.commit()          # mimic the limiter commit: expires `caller`
    _ = caller.id        # touching an attr reloads the object UNLOCKED (revoked=False)

    other = SessionLocal()
    try:
        other.execute(sa_update(models.APIKey).where(models.APIKey.id == key_id).values(revoked=True))
        other.commit()
    finally:
        other.close()

    db.refresh(caller, with_for_update=True)  # the fix: locked re-SELECT repopulates
    assert caller.revoked is True


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
    # SEC-05: sponsor tools need the per-module scope; blanket write no longer covers it.
    with as_key(["read", "write", "read:sponsors", "write:sponsors"]):
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


def test_sponsor_lead_rejects_overlong_company(client):
    """COL-API-03: an over-length value is a 422 naming the field, not a 500.

    (SQLite ignores VARCHAR widths, so this proves the new Pydantic validation
    rather than reproducing the old Postgres StringDataRightTruncation 500.)
    """
    r = client.post("/sponsor-leads", json={
        "source": "enquiry", "email": "a@b.com", "company": "x" * 300,
    })
    assert r.status_code == 422
    assert "company" in r.text


def test_flagship_signup_rejects_overlong_company(client):
    """COL-API-03: body validation fires before the slug lookup, so an
    over-length company is a 422 naming the field, not a 500."""
    r = client.post("/website/flagship/any-slug/signup", json={
        "kind": "sponsor", "email": "a@b.com", "company": "x" * 300,
    })
    assert r.status_code == 422
    assert "company" in r.text


# --------------------------------------------------------------------------- #
# NEW-APIROUTERS-05: flagship redaction fails closed + constrained flagship_state
# --------------------------------------------------------------------------- #

def _make_flagship(client, db, rw_key, name, bad_state):
    """Create a published flagship event, then set an out-of-range flagship_state
    directly through the ORM (the API schema would now reject it — which is the
    point: that is how a bad value arrives from dsec-hub's own Drizzle schema)."""
    ev = client.post("/events-api", json={
        "name": name, "start_date": "2099-12-01", "is_public": True,
        "is_flagship": True, "flagship_theme": "nightrun", "flagship_state": "teaser",
        "description": "TOP SECRET", "venue": "The Bunker",
        "ticket_url": "https://t.example/x",
    }, headers=_h(rw_key)).json()
    row = db.get(models.Event, ev["id"])
    row.flagship_state = bad_state
    db.commit()
    feed = client.get("/website/events").json()
    slug = next(e["slug"] for e in feed if e["title"] == name)
    return client.get(f"/website/events/{slug}").json()


def test_flagship_capital_teaser_is_redacted(client, db, rw_key):
    page = _make_flagship(client, db, rw_key, "Duckshot Cap", "Teaser")
    assert page["description"] is None
    assert page["venue"] is None
    assert page["ticket_url"] is None


def test_flagship_unknown_state_is_redacted(client, db, rw_key):
    page = _make_flagship(client, db, rw_key, "Duckshot Draft", "draft")
    assert page["description"] is None
    assert page["venue"] is None
    assert page["ticket_url"] is None


def test_patch_event_rejects_bad_flagship_state(client, db, rw_key):
    eid = client.post("/events-api", json={"name": "E", "start_date": "2099-12-01"},
                      headers=_h(rw_key)).json()["id"]
    assert client.patch(f"/events-api/{eid}", json={"flagship_state": "Teaser"},
                        headers=_h(rw_key)).status_code == 422


def test_patch_event_rejects_bad_flagship_theme(client, db, rw_key):
    eid = client.post("/events-api", json={"name": "E2", "start_date": "2099-12-01"},
                      headers=_h(rw_key)).json()["id"]
    assert client.patch(f"/events-api/{eid}", json={"flagship_theme": "nonsense"},
                        headers=_h(rw_key)).status_code == 422


def test_mcp_event_tools_constrain_flagship_fields():
    """The create/update event tool signatures accept only the legal values."""
    import inspect
    from typing import Literal, get_args, get_origin
    from app.features.mcp import server as mcpserver

    def _literal_values(annotation):
        # `X | None` under `from __future__ import annotations` → resolved via
        # eval_str; pick the Literal arm and return its values.
        for arm in get_args(annotation):
            if get_origin(arm) is Literal:
                return set(get_args(arm))
        return set()

    for tool in (mcpserver.create_event, mcpserver.update_event):
        # eval_str resolves the PEP 563 string annotations to real types.
        params = inspect.signature(tool, eval_str=True).parameters
        assert _literal_values(params["flagship_state"].annotation) == {"teaser", "revealed"}
        assert _literal_values(params["flagship_theme"].annotation) == {"arena", "blueprint", "nightrun"}


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
