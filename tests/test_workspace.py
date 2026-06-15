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
