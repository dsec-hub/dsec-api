"""Pydantic models for the link-tree feature.

A `Link` is one tappable button on the public, chromeless `/links` page; the
`LinkProfile` is the singleton header (title / tagline / mascot). Deliberately
lightweight — see app/models.py::Link / LinkProfile and the shared contract.
"""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

# Allowed absolute-URL schemes for a link destination. Anything else (e.g.
# javascript:, data:, vbscript:) is rejected to avoid XSS via the public page.
_URL_SCHEME_RE = re.compile(r"^(https?|mailto|tel):", re.IGNORECASE)

# Socials are external profile URLs, so they must be absolute http(s) (no
# relative paths, no other schemes). `email` is handled separately.
_HTTP_RE = re.compile(r"^https?://", re.IGNORECASE)


def _clean_social_url(v: str | None) -> str | None:
    """Validator for the four URL socials (instagram/discord/linkedin/github).
    Blank ⇒ None (so the editor can clear a social by submitting an empty field);
    otherwise require an absolute http(s) URL ≤ 512 chars."""
    if v is None:
        return None
    v = v.strip()
    if not v:
        return None
    if len(v) > 512:
        raise PydanticCustomError("value_error", "social url must be at most 512 characters")
    if not _HTTP_RE.match(v):
        raise PydanticCustomError("value_error", "social url must be an absolute http(s) URL")
    return v


def _clean_email(v: str | None) -> str | None:
    """Validator for the contact `email` social. Blank ⇒ None; strips a leading
    `mailto:` and stores a bare address (consumers build the mailto: link)."""
    if v is None:
        return None
    v = v.strip()
    if not v:
        return None
    if v.lower().startswith("mailto:"):
        v = v[len("mailto:") :].strip()
    if len(v) > 254 or "@" not in v:
        raise PydanticCustomError("value_error", "email must be a valid address")
    return v


class LinkBase(BaseModel):
    """Shared fields — reused for create (title overridden) and update."""

    title: str | None = None
    subtitle: str | None = None
    url: str | None = None
    icon: str | None = None
    # One of: blue, pink, yellow, mint, sky, violet, lime, coral.
    # NULL ⇒ the public page auto-cycles an accent by visible position.
    accent: str | None = Field(default=None, max_length=16)
    display_order: int | None = None
    is_visible: bool | None = None

    @field_validator("url")
    @classmethod
    def _validate_url(cls, v: str | None) -> str | None:
        # None passes (PATCH may omit url); otherwise enforce a safe destination.
        if v is None:
            return v
        v = v.strip()
        # PydanticCustomError (not a bare ValueError) keeps the error context
        # JSON-serialisable for the app's RequestValidationError handler, which
        # dumps exc.errors() directly. Both surface as a clean 422.
        if len(v) > 2048:
            raise PydanticCustomError("value_error", "url must be at most 2048 characters")
        # Either a relative in-app path ("/events") or an allowed-scheme URL.
        if not (v.startswith("/") or _URL_SCHEME_RE.match(v)):
            raise PydanticCustomError(
                "value_error",
                "url must be a relative path (starting with '/') or use an "
                "http(s), mailto or tel scheme",
            )
        return v


class LinkCreate(LinkBase):
    title: str  # required on create
    url: str    # a destination is required on create


class LinkUpdate(LinkBase):
    """Every field optional; only those set are applied (PATCH semantics)."""


class LinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    subtitle: str | None
    url: str
    icon: str | None
    accent: str | None
    display_order: int
    is_visible: bool
    archived: bool
    created_at: datetime
    updated_at: datetime


class LinkProfileUpdate(BaseModel):
    """The singleton header — all fields optional (PATCH semantics).

    The four URL socials and `email` are validated + normalised (blank clears
    the field). They're the club's canonical handles, served to every surface
    via the public feed.
    """

    title: str | None = None
    tagline: str | None = None
    mascot: str | None = None
    instagram: str | None = None
    discord: str | None = None
    linkedin: str | None = None
    github: str | None = None
    email: str | None = None

    @field_validator("instagram", "discord", "linkedin", "github")
    @classmethod
    def _validate_social_url(cls, v: str | None) -> str | None:
        return _clean_social_url(v)

    @field_validator("email")
    @classmethod
    def _validate_email(cls, v: str | None) -> str | None:
        return _clean_email(v)


class LinkProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    tagline: str | None
    mascot: str | None
    instagram: str | None = None
    discord: str | None = None
    linkedin: str | None = None
    github: str | None = None
    email: str | None = None
    updated_at: datetime


class ReorderIn(BaseModel):
    """Body for POST /links/reorder — the link ids in their new display order."""

    ordered_ids: list[int]
