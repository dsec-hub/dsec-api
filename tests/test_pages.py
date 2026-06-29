"""Custom pages: a Document published as a public dsec.club/<slug> page.

Covers the publish gate (slug + is_public), the public /website/pages feed, block
sanitization, and the signed draft-preview link.
"""

from __future__ import annotations

from app.core.apikeys import generate_key
from app import models


def _write_key(db) -> str:
    gen = generate_key()
    db.add(models.APIKey(name="t", prefix=gen.prefix, key_hash=gen.key_hash,
                         scopes=["read", "write"]))
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
        "title": "Secret", "slug": "secret-page", "is_public": False,
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
