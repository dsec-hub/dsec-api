"""Tally HTTP client for post-event review forms.

A thin, sync wrapper over the Tally REST API (https://developers.tally.so).
Like the Supabase storage adapter, the key is read from settings and the
feature is a no-op (raises ``TallyNotConfigured`` -> 503) when unconfigured.

Calls are synchronous (the service layer is sync and runs in FastAPI's
threadpool) using ``httpx`` — already a project dependency.
"""

from __future__ import annotations

import logging

import httpx

from app.config import settings

_logger = logging.getLogger("dsec.tally")

# Public fill link for a created form, e.g. https://tally.so/r/<id>.
FORM_FILL_BASE = "https://tally.so/r"

_TIMEOUT = httpx.Timeout(15.0)


class TallyNotConfigured(RuntimeError):
    """Raised when TALLY_API_KEY is missing — surfaced as a 503."""


class TallyError(RuntimeError):
    """Raised when the Tally API errors or is unreachable — surfaced as a 502."""


def _headers() -> dict[str, str]:
    if not settings.TALLY_API_KEY:
        raise TallyNotConfigured("Tally is not configured (set TALLY_API_KEY).")
    return {
        "Authorization": f"Bearer {settings.TALLY_API_KEY}",
        "Content-Type": "application/json",
    }


def _base() -> str:
    return settings.TALLY_API_BASE.rstrip("/")


def fill_url(form_id: str) -> str:
    """Public, shareable link attendees use to fill in the form."""
    return f"{FORM_FILL_BASE}/{form_id}"


def create_form(blocks: list[dict], *, name: str | None = None) -> dict:
    """Create a PUBLISHED Tally form from `blocks`; return the created form JSON."""
    headers = _headers()
    body = {"status": "PUBLISHED", "blocks": blocks}
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.post(f"{_base()}/forms", headers=headers, json=body)
    except httpx.HTTPError as exc:
        raise TallyError(f"could not reach Tally: {exc}") from exc
    if resp.status_code >= 300:
        raise TallyError(
            f"Tally create-form failed ({resp.status_code}): {resp.text[:300]}"
        )
    _logger.info("tally: created review form for %r", name)
    return resp.json()


def get_submissions(form_id: str) -> dict:
    """Fetch a form's submissions JSON ({questions, submissions, ...})."""
    headers = _headers()
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(f"{_base()}/forms/{form_id}/submissions", headers=headers)
    except httpx.HTTPError as exc:
        raise TallyError(f"could not reach Tally: {exc}") from exc
    if resp.status_code >= 300:
        raise TallyError(
            f"Tally submissions failed ({resp.status_code}): {resp.text[:300]}"
        )
    return resp.json()
