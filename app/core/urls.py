"""Shared URL validation for values that reach the public website.

A value stored here is rendered into an `href` on dsec.club. Anything other than
an allowed scheme (e.g. javascript:, data:, vbscript:) is rejected so a stored
value can never become script execution on a visitor's browser.
"""

from __future__ import annotations

import re

from pydantic_core import PydanticCustomError

# Default allowed absolute-URL schemes. `mailto`/`tel` are safe destinations for
# a link button; a repo/demo/image link is http(s) only (see callers).
_DEFAULT_SCHEMES = ("https", "http", "mailto", "tel")

# Leading scheme of an absolute URL, e.g. "https:" -> "https". A relative path
# ("/events") has no scheme and is handled separately.
_SCHEME_RE = re.compile(r"^([a-z][a-z0-9+.-]*):", re.IGNORECASE)


def _too_long(max_length: int) -> PydanticCustomError:
    return PydanticCustomError(
        "value_error", "url must be at most {max_length} characters",
        {"max_length": max_length},
    )


def validate_public_url(
    v: str | None,
    *,
    max_length: int,
    allow_relative: bool,
    schemes: tuple[str, ...] = _DEFAULT_SCHEMES,
    blank_to_none: bool = False,
    coerce_scheme: str | None = None,
) -> str | None:
    """Validate a URL destined for a public `href`.

    ``None`` passes (a PATCH may omit the field). Otherwise the value is stripped,
    length-checked against ``max_length`` (so an over-length value is a clean 422
    rather than a database truncation error) and required to be either a relative
    in-app path (only when ``allow_relative``) or an absolute URL using one of
    ``schemes``. A ``PydanticCustomError`` (not a bare ``ValueError``) keeps the
    error JSON-serialisable for the app's RequestValidationError handler.

    Two opt-ins soften the rules for fields that echo user-stored records (e.g.
    a project's repo/demo link, which a client resubmits verbatim on an unrelated
    PATCH):

    - ``blank_to_none``: a blank/whitespace-only value normalises to ``None``
      (a cleared field) instead of being rejected.
    - ``coerce_scheme``: a schemeless bare host like ``github.com/org/repo`` is
      prefixed with this scheme (e.g. ``https``) instead of being rejected. A
      value carrying a *disallowed* scheme (``javascript:``/``data:``) is still
      rejected — only genuinely schemeless hosts are coerced.
    """
    if v is None:
        return v
    v = v.strip()
    if not v:
        if blank_to_none:
            return None
        # else: fall through to the scheme error (empty is not a destination)
    elif allow_relative and v.startswith("/"):
        if len(v) > max_length:
            raise _too_long(max_length)
        return v
    else:
        m = _SCHEME_RE.match(v)
        if m:
            if m.group(1).lower() in schemes:
                if len(v) > max_length:
                    raise _too_long(max_length)
                return v
            # a disallowed scheme (javascript:, data:, …) — never coerce, reject.
        elif coerce_scheme and not v.startswith("/"):
            v = f"{coerce_scheme}://{v}"
            if len(v) > max_length:
                raise _too_long(max_length)
            return v

    allowed = ", ".join(schemes)
    if allow_relative:
        raise PydanticCustomError(
            "value_error",
            "url must be a relative path (starting with '/') or use one of these "
            "schemes: {allowed}",
            {"allowed": allowed},
        )
    raise PydanticCustomError(
        "value_error", "url must be an absolute URL using one of these schemes: {allowed}",
        {"allowed": allowed},
    )
