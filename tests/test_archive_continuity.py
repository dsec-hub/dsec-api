"""Archive/continuity cluster: reversible soft-delete (unarchive) + the
service-continuity export manifest.

Soft-delete was previously one-way (archive with no undo). These tests cover the
new `unarchive_*` across REST + service + MCP, and assert the
`GET /admin/archive/export` bundle is complete but leaks no secret value or key
hash.
"""

from __future__ import annotations

import json
from contextlib import contextmanager

import pytest
from sqlalchemy import select

from app import models
from app.config import settings
from app.core.apikeys import generate_key
from app.features.events import service as events_service
from app.features.mcp import auth as mcpauth
from app.features.mcp import server as mcpserver
from app.features.mcp.auth import MCPScopeError
from app.features.mcp.catalog import CATALOG
from app.models import Event, EventLog


# --- helpers ------------------------------------------------------------------


def _key(db, scopes):
    gen = generate_key()
    db.add(models.APIKey(name="k", prefix=gen.prefix, key_hash=gen.key_hash, scopes=scopes))
    db.commit()
    return gen.raw_key


def _auth(k):
    return {"Authorization": f"Bearer {k}"}


_BASIC = ("admin", "test-dashboard-pass")  # conftest DASHBOARD_USER / DASHBOARD_PASS


@contextmanager
def _as_key(scopes):
    ctx = mcpauth.KeyContext(id=1, prefix="dsec_live_test", scopes=frozenset(scopes))
    tok = mcpauth._current_key.set(ctx)
    try:
        yield
    finally:
        mcpauth._current_key.reset(tok)


def _make_event(client, wk, name="AI Night"):
    return client.post(
        "/events-api", json={"name": name, "start_date": "2026-09-01"}, headers=_auth(wk)
    ).json()


# --- REST: unarchive round-trip ----------------------------------------------


def test_event_archive_then_unarchive_roundtrip(client, db):
    wk = _key(db, ["write"])
    rk = _key(db, ["read"])
    eid = _make_event(client, wk)["id"]

    assert client.post(f"/events-api/{eid}/archive", headers=_auth(wk)).status_code == 200
    active = [e["id"] for e in client.get("/events-api", headers=_auth(rk)).json()]
    assert eid not in active  # archived hidden from the default list

    r = client.post(f"/events-api/{eid}/unarchive", headers=_auth(wk))
    assert r.status_code == 200
    active = [e["id"] for e in client.get("/events-api", headers=_auth(rk)).json()]
    assert eid in active  # restored


def test_unarchive_missing_event_is_404(client, db):
    wk = _key(db, ["write"])
    assert client.post("/events-api/999999/unarchive", headers=_auth(wk)).status_code == 404


def test_unarchive_person_enforces_write_people_scope(client, db):
    wk = _key(db, ["write:people"])
    ro = _key(db, ["read:people"])
    pid = client.post("/people", json={"name": "Ada"}, headers=_auth(wk)).json()["id"]
    client.post(f"/people/{pid}/archive", headers=_auth(wk))

    assert client.post(f"/people/{pid}/unarchive", headers=_auth(ro)).status_code == 403
    assert client.post(f"/people/{pid}/unarchive", headers=_auth(wk)).status_code == 200


# --- service + MCP ------------------------------------------------------------


def test_service_unarchive_flips_flag_back(db):
    ev = Event(name="X")
    db.add(ev)
    db.commit()
    db.refresh(ev)
    events_service.archive_event(db, ev.id)
    assert db.get(Event, ev.id).archived is True
    events_service.unarchive_event(db, ev.id)
    assert db.get(Event, ev.id).archived is False


def test_mcp_unarchive_event_round_trips(db):
    ev = Event(name="X")
    db.add(ev)
    db.commit()
    db.refresh(ev)
    with _as_key({"write"}):
        mcpserver.archive_event(ev.id)
        out = mcpserver.unarchive_event(ev.id)
    assert out["archived"] is False


def test_mcp_unarchive_person_needs_write_people(db):
    p = models.Person(name="Ada")
    db.add(p)
    db.commit()
    db.refresh(p)
    with _as_key({"write:people"}):
        mcpserver.archive_person(p.id)
    with _as_key({"read:people"}), pytest.raises(MCPScopeError):
        mcpserver.unarchive_person(p.id)


# --- every unarchive route is actually mounted --------------------------------


# (path, scope needed, handler-level 404 message) for a bogus id. A generic
# "Not Found" would mean the route isn't mounted; our custom message proves it is.
_UNARCHIVE_ROUTES = [
    ("/events-api/9999/unarchive", ["write"], "event not found"),
    ("/documents/9999/unarchive", ["write:documents"], "document not found"),
    ("/sponsors/9999/unarchive", ["write:sponsors"], "sponsor not found"),
    ("/partners/9999/unarchive", ["write"], "partner not found"),
    ("/projects/9999/unarchive", ["write"], "project not found"),
    ("/meetings/9999/unarchive", ["write"], "meeting not found"),
    ("/links/9999/unarchive", ["write"], "link not found"),
    ("/scan/9999/unarchive", ["write"], "scan target not found"),
    ("/tasks/boards/9999/unarchive", ["write"], "board not found"),
    ("/tasks/9999/unarchive", ["write"], "task not found"),
    ("/people/9999/unarchive", ["write:people"], "person not found"),
]


@pytest.mark.parametrize("path,scopes,msg", _UNARCHIVE_ROUTES)
def test_unarchive_route_is_mounted(client, db, path, scopes, msg):
    k = _key(db, scopes)
    r = client.post(path, headers=_auth(k))
    assert r.status_code == 404
    assert r.json()["detail"] == msg  # handler ran => route mounted


# --- catalog symmetry ---------------------------------------------------------


def test_every_archive_tool_has_a_matching_unarchive():
    by_name = {t.name: t for t in CATALOG}
    archives = [t for t in CATALOG if t.name.startswith("archive_")]
    assert archives  # sanity: there ARE archive tools
    for t in archives:
        un = "un" + t.name
        assert un in by_name, f"{t.name} has no {un}"
        assert by_name[un].scope == t.scope  # same least-privilege scope
        assert by_name[un].group == t.group


# --- export manifest ----------------------------------------------------------


def test_archive_export_requires_basic_auth(client):
    assert client.get("/admin/archive/export").status_code == 401


def test_archive_export_bundle_shape_and_leaks_no_secrets(client, db):
    gen = generate_key()
    # created_by carries a PII-shaped sentinel to prove the free-form label can't
    # ride along into the handover manifest.
    db.add(models.APIKey(name="handover-key", prefix=gen.prefix,
                         key_hash=gen.key_hash, scopes=["read"],
                         created_by="student@deakin.edu.au::SENTINEL_PII"))
    db.commit()

    r = client.get("/admin/archive/export", auth=_BASIC)
    assert r.status_code == 200
    b = r.json()
    assert {"schema", "row_counts", "env_vars", "api_keys",
            "alembic_revision", "generated_at"} <= set(b.keys())

    # env vars are NAMES only, with a sensitivity flag
    env = {e["name"]: e for e in b["env_vars"]}
    assert "AGENT_SECRET" in env and env["AGENT_SECRET"]["sensitive"] is True
    assert env["ANTHROPIC_MODEL"]["sensitive"] is False  # a config value, not a secret

    # NO secret VALUE, key hash, or free-form PII label appears anywhere
    blob = json.dumps(b)
    assert settings.AGENT_SECRET not in blob
    assert settings.DASHBOARD_PASS not in blob
    assert gen.key_hash not in blob
    assert "SENTINEL_PII" not in blob  # created_by is intentionally omitted

    # API keys carry accountability metadata only — never the hash, raw key, or created_by
    assert b["api_keys"], "seeded key should appear"
    for k in b["api_keys"]:
        assert set(k.keys()) == {
            "id", "name", "prefix", "scopes",
            "created_at", "last_used_at", "revoked",
        }

    # schema names core tables
    tables = {t["table"] for t in b["schema"]}
    assert {"events", "people", "api_key"} <= tables


def test_archive_export_is_audited(client, db):
    client.get("/admin/archive/export", auth=_BASIC)
    log = db.execute(
        select(EventLog).where(EventLog.action == "archive_export")
    ).scalars().first()
    assert log is not None and log.classification == "generated"
