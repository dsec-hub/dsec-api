"""REST tests for the workspace features: projects, tasks, meetings, documents,
sponsors, events, people, finance, members, and the public website feed."""

from __future__ import annotations

from datetime import date

import pytest

from app import models
from app.core.apikeys import generate_key


@pytest.fixture
def rw_key(db):
    gen = generate_key()
    db.add(models.APIKey(name="rw", prefix=gen.prefix, key_hash=gen.key_hash,
                         scopes=["read", "write"]))
    db.commit()
    return gen.raw_key


@pytest.fixture
def ro_key(db):
    gen = generate_key()
    db.add(models.APIKey(name="ro", prefix=gen.prefix, key_hash=gen.key_hash, scopes=["read"]))
    db.commit()
    return gen.raw_key


def _h(key):
    return {"Authorization": f"Bearer {key}"}


# --- projects --------------------------------------------------------------

def test_projects_crud_and_scope(client, rw_key, ro_key):
    # read-only key cannot create
    assert client.post("/projects", json={"name": "X"}, headers=_h(ro_key)).status_code == 403
    r = client.post("/projects", json={"name": "DuckType", "is_public": True, "status": "Showcased"},
                    headers=_h(rw_key))
    assert r.status_code == 201
    pid = r.json()["id"]
    assert r.json()["slug"] == "ducktype"
    assert client.get(f"/projects/{pid}", headers=_h(ro_key)).status_code == 200
    assert client.patch(f"/projects/{pid}", json={"status": "Completed"}, headers=_h(rw_key)).json()["status"] == "Completed"
    assert client.post(f"/projects/{pid}/archive", headers=_h(rw_key)).json()["archived"] is True
    assert len(client.get("/projects", headers=_h(ro_key)).json()) == 0  # archived excluded


# --- tasks (board + cards + move) ------------------------------------------

def test_tasks_board_and_move(client, rw_key):
    b = client.post("/tasks/boards", json={"name": "Sponsorship"}, headers=_h(rw_key)).json()
    t1 = client.post("/tasks", json={"title": "Email ACME", "board_id": b["id"], "status": "To Do"},
                     headers=_h(rw_key)).json()
    t2 = client.post("/tasks", json={"title": "Draft deck", "board_id": b["id"], "status": "To Do"},
                     headers=_h(rw_key)).json()
    assert (t1["position"], t2["position"]) == (0, 1)  # auto-append within column
    moved = client.post(f"/tasks/{t1['id']}/move", json={"status": "Done", "position": 0},
                        headers=_h(rw_key)).json()
    assert moved["status"] == "Done" and moved["completed_at"] is not None
    assert len(client.get(f"/tasks?board_id={b['id']}", headers=_h(rw_key)).json()) == 2


# --- documents (nesting + assignment) --------------------------------------

def test_documents_nesting(client, rw_key):
    d = client.post("/documents", json={"title": "Playbook", "type": "SponsorDoc", "content": "# Hi"},
                    headers=_h(rw_key)).json()
    client.post("/documents", json={"title": "Deliverable", "type": "Deliverable",
                                    "parent_id": d["id"], "assignee_id": 1}, headers=_h(rw_key))
    assert len(client.get("/documents?top_level=true", headers=_h(rw_key)).json()) == 1
    assert len(client.get(f"/documents?parent_id={d['id']}", headers=_h(rw_key)).json()) == 1


# --- meetings + events + people + sponsors quick CRUD ----------------------

def test_meetings_events_people_sponsors(client, rw_key):
    assert client.post("/meetings", json={"title": "Exec", "type": "Exec"}, headers=_h(rw_key)).status_code == 201
    # meeting_date + optional "HH:MM" start time round-trip (date column untouched)
    mt = client.post("/meetings", json={"title": "Standup", "meeting_date": "2026-08-01",
                                        "meeting_time": "18:30"}, headers=_h(rw_key))
    assert mt.status_code == 201 and mt.json()["meeting_time"] == "18:30"
    assert client.patch(f"/meetings/{mt.json()['id']}", json={"meeting_time": "19:00"},
                        headers=_h(rw_key)).json()["meeting_time"] == "19:00"
    e = client.post("/events-api", json={"name": "Hackathon", "start_date": "2026-08-01"}, headers=_h(rw_key))
    assert e.status_code == 201 and e.json()["start_date"] == "2026-08-01"
    assert client.post("/people", json={"name": "Ada", "type": "Exec"}, headers=_h(rw_key)).status_code == 201
    sp = client.post("/sponsors", json={"organisation": "ACME", "stage": "Contacted"}, headers=_h(rw_key))
    assert sp.status_code == 201
    upd = client.patch(f"/sponsors/{sp.json()['id']}", json={"stage": "Negotiating", "dusa_approved": True},
                       headers=_h(rw_key)).json()
    assert upd["stage"] == "Negotiating" and upd["dusa_approved"] is True


# --- finance budget/grant + summary ----------------------------------------

def test_finance_budget_and_summary(client, rw_key, db):
    ev = client.post("/events-api", json={"name": "Workshop"}, headers=_h(rw_key)).json()
    b = client.post(f"/finance/events/{ev['id']}/budget", json={"budget_aud": 300.0}, headers=_h(rw_key)).json()
    assert b["budget_aud"] == 300.0 and b["grant_aud"] == 150.0  # auto 50%
    db.add(models.FinanceReport(report_date=date(2026, 6, 12), opening_balance=1970.09,
                                total_income=978.71, total_expense=1634.71, closing_balance=1314.09,
                                transaction_count=44, is_current=True))
    db.commit()
    s = client.get("/finance/summary", headers=_h(rw_key)).json()
    assert s["closing_balance"] == 1314.09 and s["total_event_budget"] == 300.0


# --- members stats ----------------------------------------------------------

def test_members_read_and_stats(client, rw_key, db):
    db.add(models.Member(student_id="1", full_name="Ada", is_current=True, dusa_member=True))
    db.add(models.Member(student_id="2", full_name="Bo", is_current=True, dusa_member=False))
    db.add(models.MemberReport(report_date=date(2026, 6, 12), total_members=2, dusa_member_count=1,
                               non_dusa_count=1, new_count=2, renewal_count=0))
    db.commit()
    assert len(client.get("/members", headers=_h(rw_key)).json()) == 2
    stats = client.get("/members/stats", headers=_h(rw_key)).json()
    assert stats["counts"]["current_members"] == 2 and len(stats["trend"]) == 1


# --- public website feed (no auth) -----------------------------------------

def test_website_feed_no_auth(client, rw_key):
    client.post("/projects", json={"name": "Campus Compass", "is_public": True, "status": "Showcased"},
                headers=_h(rw_key))
    client.post("/projects", json={"name": "Secret", "is_public": False}, headers=_h(rw_key))
    pub = client.get("/website/projects").json()  # no auth header
    assert len(pub) == 1 and pub[0]["title"] == "Campus Compass"
    stats = client.get("/website/stats").json()
    assert stats["projects_shipped"] == 1


def test_website_events_excludes_drafts(client, rw_key, db):
    """Draft events (is_public=False) are hidden from the public feed; published
    ones show. New events default to draft; the dashboard publishes them."""
    # Created via the API with no is_public → defaults to draft.
    draft = client.post("/events-api", json={"name": "Draft Night", "start_date": "2099-03-03"},
                        headers=_h(rw_key)).json()
    assert draft["is_public"] is False
    # A published event.
    client.post("/events-api", json={"name": "Launch Night", "start_date": "2099-04-04",
                                     "is_public": True}, headers=_h(rw_key))

    titles = {e["title"] for e in client.get("/website/events").json()}  # no auth
    assert titles == {"Launch Night"}  # draft excluded

    # Publishing the draft (PATCH is_public=True) makes it appear.
    client.patch(f"/events-api/{draft['id']}", json={"is_public": True}, headers=_h(rw_key))
    titles = {e["title"] for e in client.get("/website/events").json()}
    assert titles == {"Launch Night", "Draft Night"}

    # The authenticated dashboard list still sees the draft regardless, and can
    # filter to drafts only.
    drafts = client.get("/events-api?is_public=false", headers=_h(rw_key)).json()
    assert drafts == []  # both are published now


def _media(db, *, entity_type, entity_id, role, webp):
    db.add(
        models.MediaAsset(
            entity_type=entity_type, entity_id=entity_id, role=role,
            webp_url=webp, png_url=webp.replace(".webp", ".png"),
            webp_path="p.webp", png_path="p.png",
        )
    )


def test_website_event_detail_includes_speakers_and_sponsors(client, db):
    ev = models.Event(name="AI Night", start_date=date(2099, 1, 1), is_public=True)
    person = models.Person(name="Ada Lovelace")
    sponsor = models.Sponsor(organisation="Acme", website="https://acme.test")
    db.add_all([ev, person, sponsor])
    db.commit()

    # A linked speaker (name resolved from the person) + a free-text guest.
    db.add(models.EventSpeaker(event_id=ev.id, person_id=person.id, title="Engineer"))
    guest = models.EventSpeaker(event_id=ev.id, name="Guest Star")
    db.add(guest)
    db.add(models.EventSponsor(event_id=ev.id, sponsor_id=sponsor.id, tier="Gold"))
    db.commit()
    _media(db, entity_type="speaker", entity_id=guest.id, role="photo", webp="http://x/g.webp")
    _media(db, entity_type="sponsor", entity_id=sponsor.id, role="logo", webp="http://x/acme.webp")
    db.commit()

    slug = client.get("/website/events").json()[0]["slug"]
    detail = client.get(f"/website/events/{slug}").json()

    names = {s["name"] for s in detail["speakers"]}
    assert {"Ada Lovelace", "Guest Star"} <= names
    guest_row = next(s for s in detail["speakers"] if s["name"] == "Guest Star")
    assert guest_row["photo"] == "http://x/g.webp"

    assert len(detail["sponsors"]) == 1
    assert detail["sponsors"][0]["name"] == "Acme"
    assert detail["sponsors"][0]["tier"] == "Gold"
    assert detail["sponsors"][0]["logo"] == "http://x/acme.webp"


def test_website_speaker_inherits_person_photo_with_override(client, db):
    """A linked speaker with no own headshot falls back to the linked person's
    profile photo; a speaker-specific photo overrides that fallback."""
    ev = models.Event(name="Inherit Night", start_date=date(2099, 2, 2), is_public=True)
    p_inherit = models.Person(name="Grace Hopper")  # should inherit profile photo
    p_override = models.Person(name="Alan Turing")  # own speaker photo wins
    db.add_all([ev, p_inherit, p_override])
    db.commit()

    sp_inherit = models.EventSpeaker(event_id=ev.id, person_id=p_inherit.id)
    sp_override = models.EventSpeaker(event_id=ev.id, person_id=p_override.id)
    db.add_all([sp_inherit, sp_override])
    db.commit()

    # Both people have a profile photo (entity_type="person")...
    _media(db, entity_type="person", entity_id=p_inherit.id, role="photo", webp="http://x/grace.webp")
    _media(db, entity_type="person", entity_id=p_override.id, role="photo", webp="http://x/alan-profile.webp")
    # ...but only the override speaker also has their own speaker headshot.
    _media(db, entity_type="speaker", entity_id=sp_override.id, role="photo", webp="http://x/alan-talk.webp")
    db.commit()

    slug = client.get("/website/events").json()[0]["slug"]
    detail = client.get(f"/website/events/{slug}").json()
    by_name = {s["name"]: s for s in detail["speakers"]}

    # No own speaker photo → inherits the person's profile photo.
    assert by_name["Grace Hopper"]["photo"] == "http://x/grace.webp"
    # Own speaker photo wins over the person's profile photo.
    assert by_name["Alan Turing"]["photo"] == "http://x/alan-talk.webp"


def test_website_sponsors_wall_only_published_with_logo(client, db):
    published = models.Sponsor(organisation="Shown", show_on_website=True)
    no_logo = models.Sponsor(organisation="NoLogo", show_on_website=True)
    hidden = models.Sponsor(organisation="Hidden", show_on_website=False)
    db.add_all([published, no_logo, hidden])
    db.commit()
    _media(db, entity_type="sponsor", entity_id=published.id, role="logo", webp="http://x/s.webp")
    _media(db, entity_type="sponsor", entity_id=hidden.id, role="logo", webp="http://x/h.webp")
    db.commit()

    wall = client.get("/website/sponsors").json()  # no auth
    names = {s["name"] for s in wall}
    assert names == {"Shown"}  # NoLogo excluded (no logo), Hidden excluded (flag off)


def test_website_partners_wall_published_logo_optional(client, db):
    published = models.Partner(name="Logo Club", show_on_website=True)
    no_logo = models.Partner(name="Nameonly Club", show_on_website=True)
    hidden = models.Partner(name="Hidden Club", show_on_website=False)
    archived = models.Partner(name="Archived Club", show_on_website=True, archived=True)
    db.add_all([published, no_logo, hidden, archived])
    db.commit()
    _media(db, entity_type="partner", entity_id=published.id, role="logo", webp="http://x/p.webp")
    db.commit()

    wall = client.get("/website/partners").json()  # no auth
    by_name = {p["name"]: p for p in wall}
    # Logo-less but published partners are still shown (unlike sponsors); the
    # site falls back to the club name. Hidden + archived never leak.
    assert set(by_name) == {"Logo Club", "Nameonly Club"}
    assert by_name["Logo Club"]["logo"] == "http://x/p.webp"
    assert by_name["Nameonly Club"]["logo"] is None
    # Ordered by name.
    assert [p["name"] for p in wall] == ["Logo Club", "Nameonly Club"]
