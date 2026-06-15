"""API-key auth for the mounted MCP server.

The MCP transport is a Starlette sub-app; we can't use FastAPI's dependency
injection there. Instead a tiny pure-ASGI middleware sits in front of it:
it verifies the key (same hashing/scope model as the REST API), stashes the
key's scopes in a contextvar, and rejects unauthenticated calls with 401.
MCP tools then call `require_scope(...)` to read that contextvar.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass

from fastapi import HTTPException

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


_current_key: contextvars.ContextVar[KeyContext | None] = contextvars.ContextVar(
    "mcp_current_key", default=None
)


class MCPScopeError(Exception):
    """Raised by a tool when the caller's key lacks the required scope."""


def current_key() -> KeyContext | None:
    return _current_key.get()


def require_scope(scope: str) -> KeyContext:
    ctx = _current_key.get()
    if ctx is None:
        raise MCPScopeError("not authenticated (no API key on this MCP session)")
    if scope not in ctx.scopes:
        raise MCPScopeError(
            f"your API key is missing the '{scope}' scope; "
            f"it has: {sorted(ctx.scopes) or 'none'}"
        )
    return ctx


def _extract_key(headers: dict[bytes, bytes]) -> str | None:
    auth = headers.get(b"authorization", b"").decode()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    xkey = headers.get(b"x-api-key", b"").decode().strip()
    return xkey or None


class MCPAuthMiddleware:
    """Pure-ASGI auth wrapper placed in front of the MCP streamable-HTTP app."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        raw = _extract_key(headers)

        ctx: KeyContext | None = None
        if raw:
            db = SessionLocal()
            try:
                row = verify_key(raw, db)
                if row is not None:
                    ctx = KeyContext(
                        id=row.id, prefix=row.prefix,
                        scopes=frozenset(row.scopes or []), label=row.name,
                    )
                    # Apply the same per-IP + per-key/minute limit the REST surface
                    # enforces, so a key can't issue unlimited MCP tool calls
                    # (each does an argon2 verify + DB work) and bypass the guard.
                    try:
                        limiter.check_request(
                            db, key_id=ctx.id, ip=_client_ip_from_scope(headers)
                        )
                    except HTTPException as exc:
                        return await self._too_many(send, exc.detail)
            finally:
                db.close()

        if ctx is None:
            return await self._reject(send)

        # Best-effort usage log: every authenticated MCP request by this key.
        try:
            log_usage(
                actor_type="apikey", actor_id=ctx.id, actor_label=ctx.label,
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
    async def _reject(send):
        body = b'{"error":"missing or invalid API key. Send Authorization: Bearer dsec_live_..."}'
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", b'Bearer realm="dsec-mcp"'),
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
