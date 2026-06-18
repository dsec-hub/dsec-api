"""OAuth 2.1 endpoints: discovery metadata, dynamic client registration, the
authorize (login + consent) screen, the token endpoint, and revocation.

Mounted at the app ROOT (no prefix) so the ``/.well-known/*`` documents sit where
RFC 8414 / RFC 9728 require. Everything is public (no API-key auth): security
comes from PKCE, exact redirect-URI matching, the HMAC-signed request token, and
password login — not from gating these endpoints.
"""

from __future__ import annotations

import base64
import binascii
from datetime import timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.net import client_ip
from app.core.ratelimit import limiter
from app.core.usage import log_usage
from app.db import get_db
from app.features.oauth import metadata, pages
from app.features.oauth import service, users
from app.features.oauth.service import SUPPORTED_SCOPES

router = APIRouter()

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
# Block framing of the login/consent page (clickjacking → consent hijack).
_FRAME_GUARD = {
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
}


# --------------------------------------------------------------------------- #
# Discovery metadata
# --------------------------------------------------------------------------- #

@router.get("/.well-known/oauth-authorization-server", include_in_schema=False)
@router.get("/.well-known/oauth-authorization-server/mcp", include_in_schema=False)
def authorization_server_metadata(request: Request) -> JSONResponse:
    base = metadata.base_url(request)
    return JSONResponse(metadata.authorization_server_metadata(base))


@router.get("/.well-known/oauth-protected-resource", include_in_schema=False)
@router.get("/.well-known/oauth-protected-resource/mcp", include_in_schema=False)
def protected_resource_metadata(request: Request) -> JSONResponse:
    base = metadata.base_url(request)
    return JSONResponse(metadata.protected_resource_metadata(base))


# --------------------------------------------------------------------------- #
# Dynamic client registration (RFC 7591)
# --------------------------------------------------------------------------- #

# Schemes that must never appear in a redirect URI — a 302 to one of these would
# be an XSS / data-exfiltration vector rather than a real app callback.
_DANGEROUS_SCHEMES = {"javascript", "data", "vbscript", "file", "blob"}


def _valid_redirect_uri(uri: str) -> bool:
    """Allow https anywhere, http only on loopback, and native private-use
    schemes (RFC 8252). Reject plain-http remote URIs and dangerous schemes."""
    if not uri or len(uri) > 1024:
        return False
    try:
        u = urlparse(uri)
    except ValueError:
        return False
    scheme = (u.scheme or "").lower()
    if scheme in _DANGEROUS_SCHEMES:
        return False
    if scheme == "https":
        return bool(u.netloc)
    if scheme == "http":
        return u.hostname in ("localhost", "127.0.0.1", "::1")
    # A non-http(s) scheme is treated as a native app's private-use redirect.
    return bool(scheme)


@router.post("/oauth/register", include_in_schema=False)
async def register(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    limiter.check_request(db, key_id=None, ip=client_ip(request))
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — any malformed body is a client error
        body = None
    if not isinstance(body, dict):
        return _register_error("invalid_client_metadata", "request body must be a JSON object")

    redirect_uris = body.get("redirect_uris")
    if not isinstance(redirect_uris, list) or not redirect_uris:
        return _register_error("invalid_redirect_uri", "redirect_uris is required")
    redirect_uris = [str(u) for u in redirect_uris]
    bad = [u for u in redirect_uris if not _valid_redirect_uri(u)]
    if bad:
        return _register_error("invalid_redirect_uri", f"unsupported redirect_uri: {bad[0]}")

    auth_method = str(body.get("token_endpoint_auth_method") or "none")
    if auth_method not in ("none", "client_secret_post", "client_secret_basic"):
        return _register_error("invalid_client_metadata", "unsupported token_endpoint_auth_method")

    grant_types = body.get("grant_types") or ["authorization_code", "refresh_token"]
    response_types = body.get("response_types") or ["code"]
    client_name = body.get("client_name")
    client_name = str(client_name)[:256] if client_name else None
    # Restrict the registered scope to what we support; blank → all supported.
    req_scope = str(body.get("scope") or "").split()
    pool = [s for s in req_scope if s in SUPPORTED_SCOPES] or list(SUPPORTED_SCOPES)

    client, secret = service.register_client(
        db,
        redirect_uris=redirect_uris,
        client_name=client_name,
        grant_types=[str(g) for g in grant_types][:8],
        response_types=[str(r) for r in response_types][:8],
        token_endpoint_auth_method=auth_method,
        scope=" ".join(sorted(set(pool))),
    )
    out = {
        "client_id": client.client_id,
        "client_id_issued_at": int(client.created_at.replace(tzinfo=client.created_at.tzinfo or timezone.utc).timestamp()),
        "redirect_uris": client.redirect_uris,
        "grant_types": client.grant_types,
        "response_types": client.response_types,
        "token_endpoint_auth_method": client.token_endpoint_auth_method,
        "client_name": client.client_name,
        "scope": client.scope,
    }
    if secret is not None:
        out["client_secret"] = secret
        out["client_secret_expires_at"] = 0  # never expires
    return JSONResponse(out, status_code=201, headers=_NO_STORE)


def _register_error(error: str, desc: str) -> JSONResponse:
    return JSONResponse({"error": error, "error_description": desc}, status_code=400, headers=_NO_STORE)


# --------------------------------------------------------------------------- #
# Authorization endpoint (login + consent)
# --------------------------------------------------------------------------- #

def _norm_scopes(raw_scope: str | None, client_scope: str | None) -> list[str]:
    allowed = set(client_scope.split()) if client_scope else set(SUPPORTED_SCOPES)
    pool = [s for s in SUPPORTED_SCOPES if s in allowed]  # supported ∩ client-allowed, ordered
    requested = [s for s in (raw_scope or "").split() if s]
    if not requested:
        return pool
    return [s for s in pool if s in requested]


def _append_query(uri: str, params: dict) -> str:
    parts = urlparse(uri)
    q = dict(parse_qsl(parts.query, keep_blank_values=True))
    q.update({k: v for k, v in params.items() if v is not None})
    return urlunparse(parts._replace(query=urlencode(q)))


def _redirect_error(uri: str, error: str, state: str | None, desc: str | None = None) -> RedirectResponse:
    params = {"error": error, "state": state}
    if desc:
        params["error_description"] = desc
    return RedirectResponse(_append_query(uri, params), status_code=302, headers=_NO_STORE)


@router.get("/oauth/authorize", include_in_schema=False)
def authorize_get(
    request: Request,
    response_type: str | None = Query(None),
    client_id: str | None = Query(None),
    redirect_uri: str | None = Query(None),
    scope: str | None = Query(None),
    state: str | None = Query(None),
    code_challenge: str | None = Query(None),
    code_challenge_method: str = Query("S256"),
    resource: str | None = Query(None),
    db: Session = Depends(get_db),
):
    client = service.get_client(db, client_id or "")
    if client is None:
        return HTMLResponse(
            pages.render_error(title="Unknown application", message="This client is not registered with DSEC."),
            status_code=400, headers=_FRAME_GUARD,
        )
    # Never redirect to an unvalidated URI (open-redirect guard).
    if not redirect_uri or redirect_uri not in (client.redirect_uris or []):
        return HTMLResponse(
            pages.render_error(title="Invalid redirect", message="The redirect URI does not match this client's registration."),
            status_code=400, headers=_FRAME_GUARD,
        )
    if response_type != "code":
        return _redirect_error(redirect_uri, "unsupported_response_type", state)
    if not code_challenge or code_challenge_method != "S256":
        return _redirect_error(redirect_uri, "invalid_request", state, "PKCE with S256 is required")
    scopes = _norm_scopes(scope, client.scope)
    if not scopes:
        return _redirect_error(redirect_uri, "invalid_scope", state, "no supported scopes requested")

    req_token = pages.sign_request({
        "client_id": client.client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(scopes),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "resource": resource,
    })
    html = pages.render_consent(req_token=req_token, client_name=client.client_name, scopes=scopes)
    return HTMLResponse(html, headers=_FRAME_GUARD)


@router.post("/oauth/authorize", include_in_schema=False)
async def authorize_post(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    payload = pages.verify_request(str(form.get("req") or ""))
    if payload is None:
        return HTMLResponse(
            pages.render_error(title="Session expired", message="Your sign-in page expired or was tampered with. Start the connection again."),
            status_code=400, headers=_FRAME_GUARD,
        )
    client = service.get_client(db, payload.get("client_id", ""))
    redirect_uri = payload.get("redirect_uri", "")
    # Re-validate against the live registration (defence in depth).
    if client is None or redirect_uri not in (client.redirect_uris or []):
        return HTMLResponse(
            pages.render_error(title="Invalid request", message="This authorization request is no longer valid."),
            status_code=400, headers=_FRAME_GUARD,
        )
    state = payload.get("state")
    scopes = payload.get("scope", "")

    if str(form.get("action")) == "deny":
        return _redirect_error(redirect_uri, "access_denied", state)

    # Login brute-force guard (per IP, shared with the rest of the API).
    try:
        limiter.check_request(db, key_id=None, ip=client_ip(request))
    except HTTPException:
        return _consent_again(payload, client, "Too many attempts — wait a minute and try again.")

    user = users.authenticate(db, str(form.get("email") or ""), str(form.get("password") or ""))
    if user is None:
        return _consent_again(payload, client, "Invalid email or password.")

    # Coarse grant = what the client requested ∩ what the user's role may grant.
    coarse_granted = set(scopes.split()) & users.allowed_scopes_for(db, user)
    # Expand into the module-aware scopes the MCP layer enforces: the enforced
    # modules (Sponsors, Finance) become per-module scopes a role-without-them
    # never receives; focus-only modules keep the legacy coarse read/write.
    granted = service.scopes_for_grant(db, user, coarse_granted)
    if not granted:
        return _redirect_error(redirect_uri, "access_denied", state, "your DSEC account has no permissions to grant")

    code = service.create_auth_code(
        db,
        client_id=client.client_id,
        user_id=user.id,
        redirect_uri=redirect_uri,
        scope=" ".join(granted),
        code_challenge=payload["code_challenge"],
        code_challenge_method=payload.get("code_challenge_method", "S256"),
        resource=payload.get("resource"),
    )
    log_usage(
        actor_type="user", actor_id=user.id, actor_label=user.email,
        source="mcp", action="login", detail=f"oauth authorize ({client.client_name or client.client_id})",
    )
    return RedirectResponse(
        _append_query(redirect_uri, {"code": code, "state": state}),
        status_code=302, headers=_NO_STORE,
    )


def _consent_again(payload: dict, client, error: str) -> HTMLResponse:
    """Re-render the login page (fresh signed token) after a recoverable error."""
    req_token = pages.sign_request({
        "client_id": payload["client_id"],
        "redirect_uri": payload["redirect_uri"],
        "scope": payload["scope"],
        "state": payload.get("state"),
        "code_challenge": payload["code_challenge"],
        "code_challenge_method": payload.get("code_challenge_method", "S256"),
        "resource": payload.get("resource"),
    })
    html = pages.render_consent(
        req_token=req_token, client_name=client.client_name,
        scopes=payload["scope"].split(), error=error,
    )
    return HTMLResponse(html, status_code=200, headers=_FRAME_GUARD)


# --------------------------------------------------------------------------- #
# Token endpoint
# --------------------------------------------------------------------------- #

def _token_error(error: str, status_code: int, desc: str | None = None) -> JSONResponse:
    body = {"error": error}
    if desc:
        body["error_description"] = desc
    return JSONResponse(body, status_code=status_code, headers=_NO_STORE)


def _token_response(tokens: service.IssuedTokens) -> JSONResponse:
    return JSONResponse(
        {
            "access_token": tokens.access_token,
            "token_type": "Bearer",
            "expires_in": tokens.expires_in,
            "refresh_token": tokens.refresh_token,
            "scope": tokens.scope,
        },
        headers=_NO_STORE,
    )


def _client_credentials(request: Request, form) -> tuple[str | None, str | None]:
    """Client id/secret from HTTP Basic (client_secret_basic) or the body."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("basic "):
        try:
            decoded = base64.b64decode(auth[6:]).decode("utf-8")
        except (binascii.Error, ValueError, UnicodeDecodeError):
            return None, None
        cid, _, csec = decoded.partition(":")
        return cid or None, csec or None
    return (form.get("client_id") or None), (form.get("client_secret") or None)


@router.post("/oauth/token", include_in_schema=False)
async def token(request: Request, db: Session = Depends(get_db)):
    try:
        limiter.check_request(db, key_id=None, ip=client_ip(request))
    except HTTPException as exc:
        return _token_error("temporarily_unavailable", exc.status_code, str(exc.detail))

    form = await request.form()
    grant_type = str(form.get("grant_type") or "")
    client_id, client_secret = _client_credentials(request, form)
    client = service.get_client(db, client_id or "")
    if client is None:
        return _token_error("invalid_client", 401, "unknown client")
    if not service.verify_client_secret(client, client_secret):
        return _token_error("invalid_client", 401, "bad client authentication")

    if grant_type == "authorization_code":
        res = service.consume_auth_code(
            db, str(form.get("code") or ""),
            client_id=client.client_id,
            redirect_uri=(form.get("redirect_uri") or None),
        )
        if not res.ok or res.code is None:
            return _token_error(res.error or "invalid_grant", 400)
        ac = res.code
        if not service.verify_pkce(str(form.get("code_verifier") or ""), ac.code_challenge, ac.code_challenge_method):
            return _token_error("invalid_grant", 400, "PKCE verification failed")
        tokens = service.issue_tokens(
            db, client_id=client.client_id, user_id=ac.user_id, scope=ac.scope, resource=ac.resource,
        )
        return _token_response(tokens)

    if grant_type == "refresh_token":
        res = service.use_refresh_token(db, str(form.get("refresh_token") or ""), client_id=client.client_id)
        if not res.ok or res.token is None:
            return _token_error(res.error or "invalid_grant", 400)
        old = res.token
        scope = old.scope
        req_scope = str(form.get("scope") or "").split()
        if req_scope:  # refresh may only narrow scope, never widen it
            narrowed = [s for s in old.scope.split() if s in req_scope]
            scope = " ".join(narrowed) if narrowed else old.scope
        old.revoked = True  # rotation: the presented refresh token is now spent
        db.commit()
        tokens = service.issue_tokens(
            db, client_id=client.client_id, user_id=old.user_id, scope=scope, resource=old.resource,
        )
        return _token_response(tokens)

    return _token_error("unsupported_grant_type", 400, f"unsupported grant_type: {grant_type or '(none)'}")


# --------------------------------------------------------------------------- #
# Revocation (RFC 7009)
# --------------------------------------------------------------------------- #

@router.post("/oauth/revoke", include_in_schema=False)
async def revoke(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    form = await request.form()
    # Per RFC 7009 the response is 200 regardless of whether the token existed.
    service.revoke_by_raw(db, str(form.get("token") or ""))
    return JSONResponse({}, headers=_NO_STORE)
