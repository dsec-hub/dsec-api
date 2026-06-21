"""Tests for the pre-meeting agenda feature (REST + MCP).

Covers: set/get agenda + live total duration, draft-by-default privacy, the
share lifecycle (token + public URL, idempotent), lock-then-freeze, FK
validation, agenda set at creation time, and the MCP tools (incl. share's
confirm gate and token-scoped lookup).
"""

from __future__ import annotations

import pytest

from app import models
from app.core.apikeys import generate_key
from app.features.meetings import service as agenda_service


@pytest.fixture
def rw_key(db):
    gen = generate_key()
    db.add(models.APIKey(name="rw", prefix=gen.prefix, key_hash=gen.key_hash,
                         scopes=["read", "write"]))
    db.commit()
    return gen.raw_key


def _h(key):
    return {"Authorization": f"Bearer {key}"}


def _meeting(client, rw_key, **extra) -> int:
    body = {"title": "Exec sync", "type": "Exec", "location": "DUSA 3.01", **extra}
    r = client.post("/meetings", json=body, headers=_h(rw_key))
    assert r.status_code == 201, r.text
    return r.json()["id"]


# --- defaults + set/get + total duration -----------------------------------

def test_agenda_defaults_private_then_set_and_total(client, rw_key):
    mid = _meeting(client, rw_key)

    # Brand-new meeting: agenda is an empty private draft with no share link.
    a = client.get(f"/meetings/{mid}/agenda", headers=_h(rw_key)).json()
    assert a["agenda_status"] == "draft"
    assert a["agenda_share_token"] is None and a["share_url"] is None
    assert a["items"] == [] and a["total_estimated_minutes"] == 0

    # Replace with two items (sent in display order, no ids/orders supplied).
    items = [
        {"title": "Welcome", "duration_minutes": 5},
        {"title": "Budget review", "duration_minutes": 15, "notes": "**Q3** figures"},
    ]
    r = client.put(f"/meetings/{mid}/agenda", json={"items": items}, headers=_h(rw_key))
    assert r.status_code == 200, r.text
    out = r.json()
    assert [i["order"] for i in out["items"]] == [0, 1]          # renumbered
    assert all(i["id"] for i in out["items"])                    # ids assigned
    assert out["total_estimated_minutes"] == 20                  # 5 + 15
    assert out["agenda_status"] == "draft"                       # set != share

    # MeetingOut also surfaces the agenda fields.
    m = client.get(f"/meetings/{mid}", headers=_h(rw_key)).json()
    assert m["agenda_status"] == "draft" and len(m["agenda_items"]) == 2


def test_agenda_duration_zero_is_preserved(client, rw_key):
    # A "0 min" item is valid (schema is ge=0) and must round-trip as 0, not null
    # — dsec-hub relies on this contract when editing agendas.
    mid = _meeting(client, rw_key)
    out = client.put(
        f"/meetings/{mid}/agenda",
        json={"items": [{"title": "Quick note", "duration_minutes": 0}]},
        headers=_h(rw_key),
    ).json()
    assert out["items"][0]["duration_minutes"] == 0
    assert out["total_estimated_minutes"] == 0


def test_agenda_item_id_preserved_on_reorder(client, rw_key):
    mid = _meeting(client, rw_key)
    first = client.put(f"/meetings/{mid}/agenda",
                       json={"items": [{"title": "A"}, {"title": "B"}]},
                       headers=_h(rw_key)).json()
    a_id = next(i["id"] for i in first["items"] if i["title"] == "A")
    # Resend with B first, keeping A's id → A keeps its id, order flips.
    second = client.put(
        f"/meetings/{mid}/agenda",
        json={"items": [{"title": "B"}, {"id": a_id, "title": "A"}]},
        headers=_h(rw_key),
    ).json()
    a_after = next(i for i in second["items"] if i["title"] == "A")
    assert a_after["id"] == a_id and a_after["order"] == 1


# --- share lifecycle -------------------------------------------------------

def test_share_mints_stable_token_and_url(client, rw_key):
    mid = _meeting(client, rw_key)
    client.put(f"/meetings/{mid}/agenda", json={"items": [{"title": "Kickoff"}]},
               headers=_h(rw_key))

    r = client.post(f"/meetings/{mid}/agenda/share", headers=_h(rw_key))
    assert r.status_code == 200, r.text
    shared = r.json()
    token = shared["agenda_share_token"]
    assert shared["agenda_status"] == "shared"
    assert shared["agenda_shared_at"] is not None
    assert token and shared["share_url"].endswith(f"/agenda/{token}")

    # Idempotent: re-sharing keeps the same token + shared_at.
    again = client.post(f"/meetings/{mid}/agenda/share", headers=_h(rw_key)).json()
    assert again["agenda_share_token"] == token
    assert again["agenda_shared_at"] == shared["agenda_shared_at"]


def test_lock_freezes_edits(client, rw_key):
    mid = _meeting(client, rw_key)
    client.put(f"/meetings/{mid}/agenda", json={"items": [{"title": "Item"}]},
               headers=_h(rw_key))
    client.post(f"/meetings/{mid}/agenda/share", headers=_h(rw_key))

    locked = client.post(f"/meetings/{mid}/agenda/lock", headers=_h(rw_key)).json()
    assert locked["agenda_status"] == "locked"

    # Further edits are rejected with 409.
    r = client.put(f"/meetings/{mid}/agenda", json={"items": [{"title": "New"}]},
                   headers=_h(rw_key))
    assert r.status_code == 409


# --- FK validation ---------------------------------------------------------

def test_agenda_validates_owner_and_links(client, rw_key):
    mid = _meeting(client, rw_key)
    # Unknown owner_person_id → 422 listing the bad ref.
    bad = client.put(f"/meetings/{mid}/agenda",
                     json={"items": [{"title": "X", "owner_person_id": 9999}]},
                     headers=_h(rw_key))
    assert bad.status_code == 422
    assert "owner_person_id=9999" in bad.text

    # A real person makes it valid.
    pid = client.post("/people", json={"name": "Ada", "type": "Exec"},
                      headers=_h(rw_key)).json()["id"]
    ok = client.put(f"/meetings/{mid}/agenda",
                    json={"items": [{"title": "X", "owner_person_id": pid}]},
                    headers=_h(rw_key))
    assert ok.status_code == 200
    assert ok.json()["items"][0]["owner_person_id"] == pid


# --- set at creation time --------------------------------------------------

def test_create_meeting_with_agenda(client, rw_key):
    r = client.post("/meetings", json={
        "title": "Planning",
        "agenda_items": [{"title": "Intro", "duration_minutes": 10}],
    }, headers=_h(rw_key))
    assert r.status_code == 201, r.text
    mid = r.json()["id"]
    a = client.get(f"/meetings/{mid}/agenda", headers=_h(rw_key)).json()
    assert a["total_estimated_minutes"] == 10
    assert a["agenda_status"] == "draft"          # still private until shared
    assert a["items"][0]["title"] == "Intro"


# --- service: token lookup is shared/locked only ---------------------------

def test_token_lookup_only_when_public(client, rw_key, db):
    mid = _meeting(client, rw_key)
    client.put(f"/meetings/{mid}/agenda", json={"items": [{"title": "Item"}]},
               headers=_h(rw_key))
    shared = client.post(f"/meetings/{mid}/agenda/share", headers=_h(rw_key)).json()
    token = shared["agenda_share_token"]

    found = agenda_service.get_by_share_token(db, token)
    assert found is not None and found.id == mid
    assert agenda_service.get_by_share_token(db, "nope") is None


# --- MCP tools -------------------------------------------------------------

def test_mcp_agenda_tools(db):
    from app.features.mcp import auth as mcpauth
    from app.features.mcp import server as mcpserver

    ctx = mcpauth.KeyContext(id=1, prefix="dsec_live_test",
                             scopes=frozenset({"read", "write"}))
    token = mcpauth._current_key.set(ctx)
    try:
        meeting = mcpserver.create_meeting(title="MCP meeting")
        mid = meeting["id"]

        # set + get with a live total
        mcpserver.set_meeting_agenda(mid, [
            {"title": "Topic 1", "duration_minutes": 10},
            {"title": "Topic 2", "duration_minutes": 20},
        ])
        got = mcpserver.get_meeting_agenda(mid)
        assert got["total_estimated_minutes"] == 30
        assert [i["title"] for i in got["items"]] == ["Topic 1", "Topic 2"]

        # share is confirm-gated
        with pytest.raises(ValueError):
            mcpserver.share_meeting_agenda(mid)            # no confirm → refuses
        shared = mcpserver.share_meeting_agenda(mid, confirm=True)
        assert shared["agenda_status"] == "shared"
        assert shared["share_url"].endswith(shared["agenda_share_token"])

        # lock freezes further edits
        locked = mcpserver.lock_meeting_agenda(mid)
        assert locked["agenda_status"] == "locked"
        with pytest.raises(ValueError):
            mcpserver.set_meeting_agenda(mid, [{"title": "Late"}])
    finally:
        mcpauth._current_key.reset(token)


def test_mcp_agenda_tools_in_catalog():
    from app.features.mcp import catalog

    names = catalog.all_tool_names()
    assert {
        "get_meeting_agenda", "set_meeting_agenda",
        "share_meeting_agenda", "lock_meeting_agenda",
    } <= names
