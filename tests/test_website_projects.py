"""Tests for the public /website/projects feed (dsec-api portion).

Covers the slug boundary: a slugless public project must never surface in the
feed, because its card on dsec.club/projects would link to a guaranteed 404
(the detail endpoint looks up the real, unique slug column). Mirrors the filter
/website/pages already applies to `Document.slug`. (NEW-WEBDEEP-01)
"""

from __future__ import annotations

from app import models


def test_public_projects_excludes_slugless(client, db):
    # A NULL slug is only reachable via a direct DB write (e.g. dsec-hub inserting
    # a project named purely in non-Latin script), so seed the rows directly.
    db.add(models.Project(name="No Slug", slug=None, is_public=True, review_state="approved"))
    db.add(models.Project(name="Has Slug", slug="has-slug", is_public=True, review_state="approved"))
    db.commit()

    feed = client.get("/website/projects").json()

    slugs = [p["slug"] for p in feed]
    assert "has-slug" in slugs           # the well-formed project still surfaces
    assert None not in slugs             # the slugless one is dropped
    assert all(p["slug"] for p in feed)  # every card links to a resolvable URL


def test_public_projects_slugless_is_not_fetchable_by_detail(client, db):
    # Belt-and-braces: even the slugless row's name-derived path 404s, proving the
    # feed filter is the only thing standing between it and a dead card.
    db.add(models.Project(name="No Slug", slug=None, is_public=True, review_state="approved"))
    db.commit()

    assert client.get("/website/projects").json() == []
    assert client.get("/website/projects/no-slug").status_code == 404
