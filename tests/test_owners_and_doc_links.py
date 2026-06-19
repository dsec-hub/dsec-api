"""REST tests for the two newest workspace capabilities:

* document ↔ task linking (document.related_task_id)
* co-owners (multi-assignee / multi-lead) on tasks, events and projects
"""

from __future__ import annotations

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


def _h(key):
    return {"Authorization": f"Bearer {key}"}


@pytest.fixture
def people(db):
    rows = [models.Person(name=n) for n in ("Alice", "Bob", "Carol")]
    db.add_all(rows)
    db.commit()
    for r in rows:
        db.refresh(r)
    return {r.name: r.id for r in rows}


# --- document ↔ task link --------------------------------------------------


def test_document_links_to_task(client, rw_key):
    task = client.post("/tasks", json={"title": "Write the spec"}, headers=_h(rw_key)).json()
    doc = client.post(
        "/documents",
        json={"title": "Spec", "content": "# Spec", "related_task_id": task["id"]},
        headers=_h(rw_key),
    ).json()
    assert doc["related_task_id"] == task["id"]

    # GET echoes the link, and the list filter finds it.
    assert client.get(f"/documents/{doc['id']}", headers=_h(rw_key)).json()["related_task_id"] == task["id"]
    filtered = client.get(f"/documents?related_task_id={task['id']}", headers=_h(rw_key)).json()
    assert [d["id"] for d in filtered] == [doc["id"]]

    # PATCH can unlink (explicit null).
    cleared = client.patch(
        f"/documents/{doc['id']}", json={"related_task_id": None}, headers=_h(rw_key)
    ).json()
    assert cleared["related_task_id"] is None
    assert client.get(f"/documents?related_task_id={task['id']}", headers=_h(rw_key)).json() == []


# --- task co-owners --------------------------------------------------------


def test_task_co_owners_roundtrip_and_excludes_primary(client, rw_key, people):
    # Primary = Alice; co-owners requested as [Alice, Bob] — Alice is the primary
    # so she must NOT be duplicated into the co-owner set.
    task = client.post(
        "/tasks",
        json={"title": "Ship it", "assignee_id": people["Alice"],
              "co_owner_ids": [people["Alice"], people["Bob"]]},
        headers=_h(rw_key),
    ).json()
    assert task["co_owner_ids"] == [people["Bob"]]

    # PATCH replaces the whole set.
    patched = client.patch(
        f"/tasks/{task['id']}",
        json={"co_owner_ids": [people["Bob"], people["Carol"]]},
        headers=_h(rw_key),
    ).json()
    assert sorted(patched["co_owner_ids"]) == sorted([people["Bob"], people["Carol"]])
    assert client.get(f"/tasks/{task['id']}", headers=_h(rw_key)).json()["co_owner_ids"] == patched["co_owner_ids"]

    # Empty list clears them; omitting the field leaves them unchanged.
    assert client.patch(f"/tasks/{task['id']}", json={"co_owner_ids": []}, headers=_h(rw_key)).json()["co_owner_ids"] == []
    client.patch(f"/tasks/{task['id']}", json={"co_owner_ids": [people["Carol"]]}, headers=_h(rw_key))
    untouched = client.patch(f"/tasks/{task['id']}", json={"title": "Ship it now"}, headers=_h(rw_key)).json()
    assert untouched["co_owner_ids"] == [people["Carol"]]


def test_event_and_project_co_owners(client, rw_key, people):
    event = client.post(
        "/events-api",
        json={"name": "Hackathon", "event_lead_id": people["Alice"],
              "co_owner_ids": [people["Bob"], people["Carol"]]},
        headers=_h(rw_key),
    ).json()
    assert sorted(event["co_owner_ids"]) == sorted([people["Bob"], people["Carol"]])
    assert sorted(client.get(f"/events-api/{event['id']}", headers=_h(rw_key)).json()["co_owner_ids"]) == sorted(
        [people["Bob"], people["Carol"]]
    )

    project = client.post(
        "/projects",
        json={"name": "DuckType", "lead_id": people["Alice"], "co_owner_ids": [people["Bob"]]},
        headers=_h(rw_key),
    ).json()
    assert project["co_owner_ids"] == [people["Bob"]]
