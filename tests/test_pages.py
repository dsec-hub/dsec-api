"""Custom pages: a Document published as a public dsec.club/<slug> page.

Covers the publish gate (slug + is_public), the public /website/pages feed, block
sanitization, and the signed draft-preview link.
"""

from __future__ import annotations

from app.core.apikeys import generate_key
from app import models
from app.features.website.preview import make_page_preview_token


def _write_key(db) -> str:
    gen = generate_key()
    db.add(models.APIKey(name="t", prefix=gen.prefix, key_hash=gen.key_hash,
                         scopes=["read", "write"]))
    db.commit()
    return gen.raw_key


def _read_key(db) -> str:
    gen = generate_key()
    db.add(models.APIKey(name="ro", prefix=gen.prefix, key_hash=gen.key_hash,
                         scopes=["read"]))
    db.commit()
    return gen.raw_key


def _blocks() -> dict:
    return {"version": 1, "blocks": [
        {"id": "h", "type": "hero", "title": "Welcome",
         "buttons": [{"label": "Join", "href": "/join"},
                     {"label": "x", "href": "javascript:alert(1)"}]},
        {"id": "t", "type": "richtext", "markdown": "## Hi\n\nSome **text**."},
        {"id": "bad", "type": "totally-unknown", "foo": 1},
        {"id": "s", "type": "stats",
         "items": [{"value": "200+", "label": "members", "accent": "pink"}]},
    ]}


def test_publish_and_fetch_page(client, db):
    key = _write_key(db)
    h = {"authorization": f"Bearer {key}"}
    r = client.post("/documents", headers=h, json={
        "title": "About the club", "type": "Page", "slug": "about-club",
        "is_public": True, "content_json": _blocks(),
        "show_in_nav": True, "nav_area": "header", "nav_order": 5,
        "seo_description": "All about us", "cover_image_url": "https://cdn/x.webp",
    })
    assert r.status_code == 201, r.text

    # Listed in the public nav/pages feed.
    pages = client.get("/website/pages").json()
    mine = next(p for p in pages if p["slug"] == "about-club")
    assert mine["show_in_nav"] is True
    assert mine["nav_area"] == "header"
    assert mine["title"] == "About the club"

    # Full page resolves with sanitized blocks.
    page = client.get("/website/pages/about-club")
    assert page.status_code == 200
    body = page.json()
    types = [b["type"] for b in body["blocks"]]
    assert types == ["hero", "richtext", "stats"]          # unknown block dropped
    hero = body["blocks"][0]
    assert [b["href"] for b in hero["buttons"]] == ["/join"]  # javascript: dropped


def test_draft_page_is_hidden_but_previewable(client, db):
    key = _write_key(db)
    h = {"authorization": f"Bearer {key}"}
    r = client.post("/documents", headers=h, json={
        "title": "Secret", "type": "Page", "slug": "secret-page", "is_public": False,
        "content_json": _blocks(),
    })
    doc_id = r.json()["id"]

    # Not in the public feed, and the slug 404s while it's a draft.
    assert all(p["slug"] != "secret-page" for p in client.get("/website/pages").json())
    assert client.get("/website/pages/secret-page").status_code == 404

    # But the committee can mint a preview link and see it.
    link = client.get(f"/documents/{doc_id}/page-preview-link", headers=h)
    assert link.status_code == 200
    path = link.json()["path"]
    assert path.startswith("/pages/preview/")
    prev = client.get(f"/website{path}")
    assert prev.status_code == 200
    assert prev.json()["title"] == "Secret"

    # A tampered token 404s (never reveals the doc).
    assert client.get("/website/pages/preview/9.9.deadbeef").status_code == 404


def test_published_doc_without_slug_is_not_a_page(client, db):
    key = _write_key(db)
    h = {"authorization": f"Bearer {key}"}
    client.post("/documents", headers=h, json={
        "title": "Note", "is_public": True,  # public but no slug → not a page
    })
    assert client.get("/website/pages").json() == [] or all(
        p["slug"] for p in client.get("/website/pages").json()
    )


def test_reserved_or_duplicate_slug_is_rejected(client, db):
    key = _write_key(db)
    h = {"authorization": f"Bearer {key}"}
    # A reserved website route can't be claimed as a page slug.
    r = client.post("/documents", headers=h, json={"title": "Sneaky", "slug": "events"})
    assert r.status_code == 422
    # First real page is fine; the slug is normalised (spaces/caps → hyphens).
    r1 = client.post("/documents", headers=h, json={"title": "X", "slug": "My Page!"})
    assert r1.status_code == 201
    assert r1.json()["slug"] == "my-page"
    # A second doc can't reuse that slug.
    r2 = client.post("/documents", headers=h, json={"title": "Y", "slug": "my-page"})
    assert r2.status_code == 422


def test_empty_blocks_are_dropped(client, db):
    key = _write_key(db)
    h = {"authorization": f"Bearer {key}"}
    client.post("/documents", headers=h, json={
        "title": "Sparse", "slug": "sparse", "is_public": True,
        "content_json": {"version": 1, "blocks": [
            {"id": "1", "type": "hero", "align": "center", "variant": "banner"},  # no content
            {"id": "2", "type": "gallery", "images": [], "columns": 3},            # no images
            {"id": "3", "type": "stats", "title": "Numbers", "items": []},         # no items
            {"id": "4", "type": "heading", "title": "Real heading"},               # keeps
            {"id": "5", "type": "divider", "variant": "line"},                     # keeps
        ]},
    })
    blocks = client.get("/website/pages/sparse").json()["blocks"]
    assert [b["type"] for b in blocks] == ["heading", "divider"]


# --------------------------------------------------------------------------- #
# NEW-APIROUTERS-01: page-preview links are Page-only and need the write scope
# --------------------------------------------------------------------------- #

def test_page_preview_link_requires_write_scope(client, db):
    key = _write_key(db)
    h = {"authorization": f"Bearer {key}"}
    doc_id = client.post("/documents", headers=h, json={
        "title": "P", "type": "Page", "slug": "p-scope", "content_json": _blocks(),
    }).json()["id"]
    # A read-only key cannot mint the delegation link.
    ro = {"authorization": f"Bearer {_read_key(db)}"}
    assert client.get(f"/documents/{doc_id}/page-preview-link", headers=ro).status_code == 403


def test_page_preview_link_rejects_non_page_document(client, db):
    key = _write_key(db)
    h = {"authorization": f"Bearer {key}"}
    doc_id = client.post("/documents", headers=h, json={
        "title": "Minutes", "type": "MeetingNotes", "content_json": _blocks(),
    }).json()["id"]
    r = client.get(f"/documents/{doc_id}/page-preview-link", headers=h)
    assert r.status_code == 400


def test_meeting_notes_cannot_be_reached_through_page_preview(client, db):
    # Build a non-Page doc directly and mint a valid token for it, bypassing the
    # router's own guard so we test the consumer's independent Page filter.
    doc = models.Document(title="Sponsorship negotiation", type="MeetingNotes",
                          content_json=_blocks(), archived=False)
    db.add(doc)
    db.commit()
    token = make_page_preview_token(doc.id)
    assert client.get(f"/website/pages/preview/{token}").status_code == 404


def test_page_preview_token_defaults_to_page_ttl():
    from app.config import settings
    import time
    before = int(time.time())
    token = make_page_preview_token(123)
    exp = int(token.split(".")[1])
    # Within a small window of now + PAGE_PREVIEW_TTL, and far below EVENT_PREVIEW_TTL.
    assert abs(exp - (before + settings.PAGE_PREVIEW_TTL)) <= 5
    assert exp < before + settings.EVENT_PREVIEW_TTL


def test_preview_does_not_persist_synthesised_slug(client, db):
    """NEW-APIROUTERS-09: previewing a slug-less draft must not write a slug onto
    the session-managed row (a future commit would otherwise persist it)."""
    from app.db import SessionLocal

    doc = models.Document(title="Draft", type="Page", slug=None,
                          content_json=_blocks(), archived=False)
    db.add(doc)
    db.commit()
    doc_id = doc.id

    token = make_page_preview_token(doc_id)
    resp = client.get(f"/website/pages/preview/{token}")
    assert resp.status_code == 200
    assert resp.json()["slug"] == f"preview-{doc_id}"  # synthesised for the layout

    # A brand-new session reads the actual DB row — its slug must still be NULL.
    fresh = SessionLocal()
    try:
        row = fresh.get(models.Document, doc_id)
        assert row.slug is None
    finally:
        fresh.close()


# --------------------------------------------------------------------------- #
# Page type invariant (#3): every slug-published doc is type=="Page"
# --------------------------------------------------------------------------- #

def test_backfill_sets_type_on_page_shaped_rows(client, db):
    """A page created before the invariant (slug, is_public, type=None) is served
    but not previewable; the idempotent backfill repairs it."""
    from app.features.documents import service

    doc = models.Document(title="Legacy Page", slug="legacy-page", is_public=True,
                          type=None, content_json=_blocks(), archived=False)
    db.add(doc)
    db.commit()
    doc_id = doc.id

    # Preview minting refuses a non-Page doc today.
    key = _write_key(db)
    assert client.get(f"/documents/{doc_id}/page-preview-link",
                      headers={"authorization": f"Bearer {key}"}).status_code == 400

    n = service.backfill_page_document_type(db)
    assert n == 1
    db.expire_all()
    assert db.get(models.Document, doc_id).type == "Page"

    # Idempotent: a second run changes nothing.
    assert service.backfill_page_document_type(db) == 0

    # Now it mints + previews cleanly.
    link = client.get(f"/documents/{doc_id}/page-preview-link",
                      headers={"authorization": f"Bearer {key}"})
    assert link.status_code == 200
    prev = client.get(f"/website{link.json()['path']}")
    assert prev.status_code == 200 and prev.json()["title"] == "Legacy Page"


def test_backfill_leaves_non_page_and_slugless_rows_alone(client, db):
    from app.features.documents import service

    note = models.Document(title="Note", type="MeetingNotes", content_json=_blocks(), archived=False)
    slugless = models.Document(title="Draft", type=None, content_json=_blocks(), archived=False)
    db.add_all([note, slugless])
    db.commit()
    service.backfill_page_document_type(db)
    db.expire_all()
    assert db.get(models.Document, note.id).type == "MeetingNotes"  # untouched
    assert db.get(models.Document, slugless.id).type is None        # no slug → not a page


def test_create_page_with_slug_defaults_type_to_page(client, db):
    key = _write_key(db)
    h = {"authorization": f"Bearer {key}"}
    doc = client.post("/documents", headers=h, json={
        "title": "Auto", "slug": "auto-page", "is_public": True, "content_json": _blocks(),
    }).json()
    assert doc["type"] == "Page"
    # ...and is therefore previewable.
    assert client.get(f"/documents/{doc['id']}/page-preview-link", headers=h).status_code == 200


def test_create_document_with_slug_and_conflicting_type_rejected(client, db):
    key = _write_key(db)
    h = {"authorization": f"Bearer {key}"}
    r = client.post("/documents", headers=h, json={
        "title": "Bad", "slug": "bad-note", "type": "MeetingNotes",
    })
    assert r.status_code == 422
