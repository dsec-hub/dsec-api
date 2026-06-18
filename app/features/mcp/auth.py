"""Auth for the mounted MCP server (API keys + OAuth 2.1 access tokens).

The MCP transport is a Starlette sub-app; we can't use FastAPI's dependency
injection there. Instead a tiny pure-ASGI middleware sits in front of it: it
resolves the caller's bearer credential — either a ``dsec_live_`` API key or an
OAuth access token minted by the login flow (features/oauth) — stashes the
resulting scopes in a contextvar, and rejects unauthenticated calls with 401.
MCP tools then call `require_scope(...)` to read that contextvar.

On rejection it advertises the OAuth authorization server via the
``WWW-Authenticate: ... resource_metadata=...`` header (RFC 9728), which is what
lets a header-less client like Claude.ai's "Add custom connector" discover the
login flow from just the bare server URL.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import parse_qs

from fastapi import HTTPException

from app.config import settings
from app.core.apikeys import verify_key
from app.core.ratelimit import limiter
from app.core.usage import log_usage
from app.db import SessionLocal


def _client_ip_from_scope(headers: dict[bytes, bytes]) -> str:
    """Trusted client IP from raw ASGI headers (mirrors core.net.client_ip).

    The MCP transport is pure ASGI, so we read the lowercased byte headers
    directly rather than a FastAPI Request. Prefer the platform-set ``x-real-ip``,
    then the rightmost (trusted) ``x-forwarded-for`` hop — never the spoofable
    leftmost value.
    """
    real = headers.get(b"x-real-ip", b"").decode().strip()
    if real:
        return real
    forwarded = headers.get(b"x-forwarded-for", b"").decode()
    hops = [h.strip() for h in forwarded.split(",") if h.strip()]
    if hops:
        return hops[-1]
    return "unknown"


@dataclass(frozen=True)
class KeyContext:
    id: int
    prefix: str
    scopes: frozenset
    label: str | None = None
    # "apikey" for a dsec_live_ key, "oauth" for a login-issued access token.
    kind: str = "apikey"
    # The app_user.id behind an OAuth token (None for API keys).
    user_id: int | None = None


_current_key: contextvars.ContextVar[KeyContext | None] = contextvars.ContextVar(
    "mcp_current_key", default=None
)


class MCPScopeError(Exception):
    """Raised by a tool when the caller's key lacks the required scope."""


def current_key() -> KeyContext | None:
    return _current_key.get()


def has_scope(scopes: frozenset[str], required: str) -> bool:
    """Does a credential carrying ``scopes`` satisfy ``required``?

    Backward-compatible scope algebra so that every existing credential — the
    ``dsec_live_`` keys and OAuth tokens that carry the legacy coarse
    ``read``/``write`` — keeps working everywhere, while the new per-module
    scopes (``read:sponsors``, ``write:finance``, …) provide tighter isolation:

    - legacy ``"write"`` is a superset of every ``write:*``, every ``read:*`` and
      legacy ``"read"``.
    - legacy ``"read"`` is a superset of every ``read:*``.
    - ``"write:X"`` satisfies ``"read:X"``.
    - any other scope (``trigger``, ``ingest``, an exact module scope) matches
      only itself.
    """
    if required in scopes:
        return True
    # Legacy "write" — the universal superset of every read/write scope, coarse
    # or per-module, plus legacy "read".
    if "write" in scopes and (required == "read" or required.startswith(("read:", "write:"))):
        return True
    # Legacy "read" covers every read scope (exact "read" handled above).
    if "read" in scopes and required.startswith("read:"):
        return True
    # write:X implies read:X.
    if required.startswith("read:") and f"write:{required[len('read:'):]}" in scopes:
        return True
    return False


def require_scope(scope: str) -> KeyContext:
    ctx = _current_key.get()
    if ctx is None:
        raise MCPScopeError("not authenticated (no API key on this MCP session)")
    if not has_scope(ctx.scopes, scope):
        raise MCPScopeError(
            f"your API key is missing the '{scope}' scope; "
            f"it has: {sorted(ctx.scopes) or 'none'}"
        )
    return ctx


def _extract_key(headers: dict[bytes, bytes], query_string: bytes = b"") -> str | None:
    auth = headers.get(b"authorization", b"").decode()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    xkey = headers.get(b"x-api-key", b"").decode().strip()
    if xkey:
        return xkey
    # Fall back to a `?key=`/`?api_key=` query param. Clients like Claude.ai's
    # "Add custom connector" dialog accept only a URL — there's no field for an
    # Authorization header — so the key has to ride in the link itself. The whole
    # URL is therefore a secret (mirror that warning in the UI that hands it out).
    qs = parse_qs(query_string.decode("latin-1"))
    for name in ("key", "api_key"):
        vals = qs.get(name)
        if vals and vals[0].strip():
            return vals[0].strip()
    return None


def _resolve_context(raw: str, db) -> KeyContext | None:
    """Resolve a bearer credential to a KeyContext.

    An OAuth access token (carrying the OAuth prefix, feature enabled) is tried
    first; otherwise the credential is treated as a ``dsec_live_`` API key. Both
    paths produce the same KeyContext so the MCP tools are oblivious to which
    auth mechanism was used.
    """
    if settings.OAUTH_ENABLED and raw.startswith(settings.OAUTH_ACCESS_TOKEN_PREFIX):
        from app.features.oauth import service as oauth_service
        from app.models import AppUser

        tok = oauth_service.verify_access_token(raw, db)
        if tok is None:
            return None
        user = db.get(AppUser, tok.user_id)
        label = f"oauth:{user.email}" if user is not None else f"oauth:user{tok.user_id}"
        try:  # best-effort "last used" stamp; never block a call on it
            tok.last_used_at = datetime.now(timezone.utc)
            db.commit()
        except Exception:  # pragma: no cover
            db.rollback()
        return KeyContext(
            id=tok.id, prefix=tok.access_prefix,
            scopes=frozenset((tok.scope or "").split()),
            label=label, kind="oauth", user_id=tok.user_id,
        )

    row = verify_key(raw, db)
    if row is None:
        return None
    return KeyContext(
        id=row.id, prefix=row.prefix,
        scopes=frozenset(row.scopes or []), label=row.name, kind="apikey",
    )


def _resource_metadata_url(headers: dict[bytes, bytes], scope) -> str:
    """The RFC 9728 protected-resource-metadata URL to advertise on a 401."""
    if settings.OAUTH_ISSUER:
        base = settings.OAUTH_ISSUER.rstrip("/")
    else:
        proto = headers.get(b"x-forwarded-proto", b"").decode().split(",")[0].strip()
        if not proto:
            proto = scope.get("scheme", "https")
        host = (headers.get(b"x-forwarded-host") or headers.get(b"host", b"")).decode()
        base = f"{proto}://{host}"
    return f"{base}/.well-known/oauth-protected-resource"


class MCPAuthMiddleware:
    """Pure-ASGI auth wrapper placed in front of the MCP streamable-HTTP app."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        raw = _extract_key(headers, scope.get("query_string", b""))

        ctx: KeyContext | None = None
        if raw:
            db = SessionLocal()
            try:
                ctx = _resolve_context(raw, db)
                if ctx is not None:
                    # Same per-IP + per-key/minute limit the REST surface enforces,
                    # so a credential can't issue unlimited MCP tool calls and
                    # bypass the guard. OAuth tokens aren't api_key rows, so their
                    # per-key counter would violate the rate_limit→api_key FK —
                    # limit those per-IP only (key_id=None).
                    rl_key = ctx.id if ctx.kind == "apikey" else None
                    try:
                        limiter.check_request(
                            db, key_id=rl_key, ip=_client_ip_from_scope(headers)
                        )
                    except HTTPException as exc:
                        return await self._too_many(send, exc.detail)
            finally:
                db.close()

        if ctx is None:
            return await self._reject(send, _resource_metadata_url(headers, scope))

        # Best-effort usage log: every authenticated MCP request. OAuth requests
        # are attributed to the human (their app_user.id) so usage groups per
        # person; API-key requests stay attributed to the key.
        try:
            log_usage(
                actor_type=("oauth" if ctx.kind == "oauth" else "apikey"),
                actor_id=(ctx.user_id if ctx.kind == "oauth" else ctx.id),
                actor_label=ctx.label,
                source="mcp", action="mcp_request", path=scope.get("path"),
            )
        except Exception:  # pragma: no cover — logging must never break a call
            pass

        token = _current_key.set(ctx)
        try:
            await self.app(scope, receive, send)
        finally:
            _current_key.reset(token)

    @staticmethod
    async def _reject(send, resource_metadata_url: str | None = None):
        body = (
            b'{"error":"authentication required. Send Authorization: Bearer '
            b'dsec_live_... (or append ?key=dsec_live_... to the URL), or connect '
            b'with OAuth - log in when your client prompts."}'
        )
        www = b'Bearer realm="dsec-mcp"'
        if resource_metadata_url:
            www += b', resource_metadata="' + resource_metadata_url.encode("ascii", "ignore") + b'"'
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", www),
            ],
        })
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    async def _too_many(send, detail: str):
        import json

        body = json.dumps({"error": detail}).encode()
        await send({
            "type": "http.response.start",
            "status": 429,
            "headers": [
                (b"content-type", b"application/json"),
                (b"retry-after", b"60"),
            ],
        })
        await send({"type": "http.response.body", "body": body})
