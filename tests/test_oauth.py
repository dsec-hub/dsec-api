"""OAuth 2.1 authorization-server tests: discovery, registration, the full PKCE
authorization-code flow, refresh-token rotation, replay defences, scope bounding
by role, revocation, and acceptance of issued tokens at the /mcp endpoint.

All run against the throwaway SQLite DB from conftest (no Neon). app_role doesn't
exist there, so role→scope mapping exercises the legacy-`role`-varchar fallback.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from urllib.parse import parse_qs, urlparse

import bcrypt
import pytest

from app.features.mcp import auth as mcpauth
from app.features.oauth import service
from app.models import AppUser, OAuthToken


# --------------------------------------------------------------------------- #
# helpers / fixtures
# --------------------------------------------------------------------------- #

def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)  # 64 chars, within 43–128
    challenge = service.b64url_encode(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


@pytest.fixture
def user(db):
    """An active exec user with a known bcrypt password (as dsec-hub writes it)."""
    pw_hash = bcrypt.hashpw(b"correct horse battery", bcrypt.gensalt(rounds=4)).decode()
    u = AppUser(email="exec@dsec.club", name="Exec", password_hash=pw_hash, role="exec", is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _register(client, **overrides) -> dict:
    body = {
        "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
        "client_name": "Claude",
        "token_endpoint_auth_method": "none",
        **overrides,
    }
    r = client.post("/oauth/register", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _authorize_get(client, *, client_id, redirect_uri, challenge, scope="read write trigger ingest", state="xyz"):
    r = client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": scope,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": "https://api.dsec.club/mcp",
        },
        follow_redirects=False,
    )
    return r


def _req_token_from_html(html: str) -> str:
    m = re.search(r'name="req" value="([^"]+)"', html)
    assert m, "no signed request token in consent page"
    return m.group(1)


def _login(client, req_token, *, email="exec@dsec.club", password="correct horse battery", action="allow"):
    return client.post(
        "/oauth/authorize",
        data={"req": req_token, "email": email, "password": password, "action": action},
        follow_redirects=False,
    )


def _full_authorize(client, user) -> tuple[str, str, str]:
    """Run register → authorize → login. Returns (client_id, code, verifier)."""
    reg = _register(client)
    cid, redirect = reg["client_id"], reg["redirect_uris"][0]
    verifier, challenge = _pkce()
    g = _authorize_get(client, client_id=cid, redirect_uri=redirect, challenge=challenge)
    assert g.status_code == 200, g.text
    req = _req_token_from_html(g.text)
    resp = _login(client, req)
    assert resp.status_code == 302, resp.text
    code = parse_qs(urlparse(resp.headers["location"]).query)["code"][0]
    return cid, code, verifier


# --------------------------------------------------------------------------- #
# discovery metadata
# --------------------------------------------------------------------------- #

def test_protected_resource_metadata(client):
    r = client.get("/.well-known/oauth-protected-resource")
    assert r.status_code == 200
    body = r.json()
    assert body["resource"].endswith("/mcp")
    assert body["authorization_servers"]
    assert set(body["scopes_supported"]) == {"read", "write", "trigger", "ingest"}


def test_protected_resource_metadata_mcp_suffix(client):
    # Newer MCP clients probe the path-suffixed variant.
    assert client.get("/.well-known/oauth-protected-resource/mcp").status_code == 200


def test_authorization_server_metadata(client):
    r = client.get("/.well-known/oauth-authorization-server")
    assert r.status_code == 200
    body = r.json()
    assert body["authorization_endpoint"].endswith("/oauth/authorize")
    assert body["token_endpoint"].endswith("/oauth/token")
    assert body["registration_endpoint"].endswith("/oauth/register")
    assert body["code_challenge_methods_supported"] == ["S256"]
    assert "authorization_code" in body["grant_types_supported"]


# --------------------------------------------------------------------------- #
# dynamic client registration
# --------------------------------------------------------------------------- #

def test_register_public_client(client):
    reg = _register(client)
    assert reg["client_id"].startswith("dsec_client_")
    assert "client_secret" not in reg  # public client → no secret
    assert reg["token_endpoint_auth_method"] == "none"


def test_register_confidential_client_returns_secret(client):
    reg = _register(client, token_endpoint_auth_method="client_secret_post")
    assert reg["client_secret"]


def test_register_rejects_plain_http_remote_redirect(client):
    r = client.post("/oauth/register", json={"redirect_uris": ["http://evil.example.com/cb"]})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_redirect_uri"


def test_register_allows_http_loopback(client):
    reg = _register(client, redirect_uris=["http://localhost:7777/callback"])
    assert reg["client_id"]


def test_register_rejects_dangerous_scheme(client):
    r = client.post("/oauth/register", json={"redirect_uris": ["javascript:alert(1)"]})
    assert r.status_code == 400 and r.json()["error"] == "invalid_redirect_uri"


def test_register_rejects_non_object_body(client):
    r = client.post("/oauth/register", json=["not", "an", "object"])
    assert r.status_code == 400


# --------------------------------------------------------------------------- #
# authorization endpoint guards
# --------------------------------------------------------------------------- #

def test_authorize_unknown_client_is_html_error(client):
    _, challenge = _pkce()
    r = client.get(
        "/oauth/authorize",
        params={"response_type": "code", "client_id": "nope", "redirect_uri": "https://x/cb",
                "code_challenge": challenge, "code_challenge_method": "S256"},
        follow_redirects=False,
    )
    assert r.status_code == 400 and "text/html" in r.headers["content-type"]


def test_authorize_unregistered_redirect_is_html_error_not_redirect(client):
    reg = _register(client)
    _, challenge = _pkce()
    r = client.get(
        "/oauth/authorize",
        params={"response_type": "code", "client_id": reg["client_id"],
                "redirect_uri": "https://attacker.example/cb",
                "code_challenge": challenge, "code_challenge_method": "S256"},
        follow_redirects=False,
    )
    assert r.status_code == 400  # never a 302 to the unvalidated URI


def test_authorize_requires_pkce(client):
    reg = _register(client)
    r = client.get(
        "/oauth/authorize",
        params={"response_type": "code", "client_id": reg["client_id"],
                "redirect_uri": reg["redirect_uris"][0], "state": "s"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["error"] == ["invalid_request"]


def test_authorize_bad_response_type_redirects_error(client):
    reg = _register(client)
    _, challenge = _pkce()
    r = client.get(
        "/oauth/authorize",
        params={"response_type": "token", "client_id": reg["client_id"],
                "redirect_uri": reg["redirect_uris"][0], "code_challenge": challenge,
                "code_challenge_method": "S256", "state": "s"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert parse_qs(urlparse(r.headers["location"]).query)["error"] == ["unsupported_response_type"]


def test_consent_page_blocks_framing(client):
    reg = _register(client)
    _, challenge = _pkce()
    g = _authorize_get(client, client_id=reg["client_id"], redirect_uri=reg["redirect_uris"][0], challenge=challenge)
    assert g.headers["x-frame-options"] == "DENY"


# --------------------------------------------------------------------------- #
# login / consent
# --------------------------------------------------------------------------- #

def test_wrong_password_rerenders_without_code(client, user):
    reg = _register(client)
    verifier, challenge = _pkce()
    g = _authorize_get(client, client_id=reg["client_id"], redirect_uri=reg["redirect_uris"][0], challenge=challenge)
    req = _req_token_from_html(g.text)
    r = _login(client, req, password="wrong")
    assert r.status_code == 200  # back to the consent page, not a redirect
    assert "Invalid email or password" in r.text


def test_inactive_user_cannot_log_in(client, db, user):
    user.is_active = False
    db.commit()
    reg = _register(client)
    verifier, challenge = _pkce()
    g = _authorize_get(client, client_id=reg["client_id"], redirect_uri=reg["redirect_uris"][0], challenge=challenge)
    r = _login(client, _req_token_from_html(g.text))
    assert r.status_code == 200 and "Invalid email or password" in r.text


def test_deny_redirects_with_access_denied(client, user):
    reg = _register(client)
    _, challenge = _pkce()
    g = _authorize_get(client, client_id=reg["client_id"], redirect_uri=reg["redirect_uris"][0], challenge=challenge)
    r = _login(client, _req_token_from_html(g.text), action="deny")
    assert r.status_code == 302
    assert parse_qs(urlparse(r.headers["location"]).query)["error"] == ["access_denied"]


def test_tampered_request_token_is_rejected(client, user):
    reg = _register(client)
    _, challenge = _pkce()
    g = _authorize_get(client, client_id=reg["client_id"], redirect_uri=reg["redirect_uris"][0], challenge=challenge)
    req = _req_token_from_html(g.text)
    body, _, mac = req.partition(".")
    r = _login(client, f"{body}x.{mac}")  # corrupt the payload
    assert r.status_code == 400 and "expired" in r.text.lower()


# --------------------------------------------------------------------------- #
# token endpoint — authorization_code + PKCE
# --------------------------------------------------------------------------- #

def test_full_code_flow_issues_tokens(client, user):
    cid, code, verifier = _full_authorize(client, user)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": code, "client_id": cid,
        "code_verifier": verifier, "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
    })
    assert r.status_code == 200, r.text
    tok = r.json()
    assert tok["token_type"] == "Bearer"
    assert tok["access_token"].startswith("dsec_at_")
    assert tok["refresh_token"].startswith("dsec_rt_")
    # exec role (fallback mapping) → read+write+trigger, never ingest
    assert set(tok["scope"].split()) == {"read", "write", "trigger"}
    assert r.headers["cache-control"] == "no-store"


def test_token_rejects_bad_pkce_verifier(client, user):
    cid, code, _ = _full_authorize(client, user)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": code, "client_id": cid,
        "code_verifier": "wrong-verifier-wrong-verifier-wrong-verifier-xx",
    })
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"


def test_auth_code_is_single_use(client, user):
    cid, code, verifier = _full_authorize(client, user)
    data = {"grant_type": "authorization_code", "code": code, "client_id": cid, "code_verifier": verifier}
    assert client.post("/oauth/token", data=data).status_code == 200
    again = client.post("/oauth/token", data=data)
    assert again.status_code == 400 and again.json()["error"] == "invalid_grant"


def test_token_unknown_client_is_invalid_client(client, user):
    _, code, verifier = _full_authorize(client, user)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": code, "client_id": "ghost", "code_verifier": verifier,
    })
    assert r.status_code == 401 and r.json()["error"] == "invalid_client"


def test_unsupported_grant_type(client, user):
    cid, _, _ = _full_authorize(client, user)
    r = client.post("/oauth/token", data={"grant_type": "password", "client_id": cid})
    assert r.status_code == 400 and r.json()["error"] == "unsupported_grant_type"


# --------------------------------------------------------------------------- #
# refresh-token rotation + reuse detection
# --------------------------------------------------------------------------- #

def _issue(client, user):
    cid, code, verifier = _full_authorize(client, user)
    tok = client.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": code, "client_id": cid, "code_verifier": verifier,
    }).json()
    return cid, tok


def test_refresh_rotates_and_old_token_dies(client, user):
    cid, tok = _issue(client, user)
    r = client.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": cid,
    })
    assert r.status_code == 200, r.text
    rotated = r.json()
    assert rotated["refresh_token"] != tok["refresh_token"]
    # Re-using the old (now spent) refresh token is rejected as a replay.
    reuse = client.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": cid,
    })
    assert reuse.status_code == 400 and reuse.json()["error"] == "invalid_grant"


def test_refresh_reuse_revokes_family(client, user, db):
    cid, tok = _issue(client, user)
    rotated = client.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": cid,
    }).json()
    # Present the spent refresh token again → whole family revoked, incl. the new one.
    client.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": cid,
    })
    assert service.verify_access_token(rotated["access_token"], db) is None


def test_refresh_can_narrow_scope(client, user):
    cid, tok = _issue(client, user)
    r = client.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
        "client_id": cid, "scope": "read",
    })
    assert r.status_code == 200
    assert r.json()["scope"] == "read"


# --------------------------------------------------------------------------- #
# the issued access token at the resource server (/mcp)
# --------------------------------------------------------------------------- #

def test_access_token_resolves_to_keycontext(client, user, db):
    _, tok = _issue(client, user)
    ctx = mcpauth._resolve_context(tok["access_token"], db)
    assert ctx is not None
    assert ctx.kind == "oauth"
    assert ctx.user_id == user.id
    assert ctx.scopes == frozenset({"read", "write", "trigger"})


def test_mcp_middleware_accepts_oauth_token(client, user):
    """Drive the auth middleware directly (the full MCP transport needs the
    lifespan's session manager, which conftest doesn't run). Proves the accept
    path: the downstream app is reached, the contextvar carries the OAuth scopes,
    and per-IP-only rate limiting doesn't hit the rate_limit→api_key FK."""
    import asyncio

    _, tok = _issue(client, user)
    reached: dict = {}

    async def fake_app(scope, receive, send):
        reached["ctx"] = mcpauth.current_key()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    mw = mcpauth.MCPAuthMiddleware(fake_app)
    scope = {
        "type": "http", "method": "POST", "path": "/", "scheme": "http",
        "query_string": b"",
        "headers": [
            (b"authorization", f"Bearer {tok['access_token']}".encode()),
            (b"host", b"testserver"),
        ],
    }
    sent: list = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg):
        sent.append(msg)

    asyncio.run(mw(scope, receive, send))
    assert sent[0]["status"] == 200  # auth + rate-limit passed → downstream reached
    ctx = reached["ctx"]
    assert ctx is not None and ctx.kind == "oauth" and ctx.user_id == user.id
    assert ctx.scopes == frozenset({"read", "write", "trigger"})


def test_mcp_rejects_bogus_oauth_token_with_metadata_hint(client):
    r = client.post("/mcp", headers={"Authorization": "Bearer dsec_at_bogus"},
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401
    assert "resource_metadata=" in r.headers.get("www-authenticate", "")


# --------------------------------------------------------------------------- #
# revocation
# --------------------------------------------------------------------------- #

def test_revoke_access_token(client, user, db):
    _, tok = _issue(client, user)
    assert service.verify_access_token(tok["access_token"], db) is not None
    r = client.post("/oauth/revoke", data={"token": tok["access_token"]})
    assert r.status_code == 200
    db.rollback()  # end this session's read snapshot so it sees the committed revoke
    assert service.verify_access_token(tok["access_token"], db) is None


def test_revoke_unknown_token_is_ok(client):
    assert client.post("/oauth/revoke", data={"token": "dsec_at_nope"}).status_code == 200


# --------------------------------------------------------------------------- #
# scope bounding by role (fallback mapping; admin keeps ingest)
# --------------------------------------------------------------------------- #

def test_admin_role_can_grant_ingest(client, db):
    pw = bcrypt.hashpw(b"adminpass-adminpass", bcrypt.gensalt(rounds=4)).decode()
    db.add(AppUser(email="admin@dsec.club", name="Admin", password_hash=pw, role="admin", is_active=True))
    db.commit()
    reg = _register(client)
    verifier, challenge = _pkce()
    g = _authorize_get(client, client_id=reg["client_id"], redirect_uri=reg["redirect_uris"][0], challenge=challenge)
    resp = _login(client, _req_token_from_html(g.text), email="admin@dsec.club", password="adminpass-adminpass")
    code = parse_qs(urlparse(resp.headers["location"]).query)["code"][0]
    tok = client.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": code, "client_id": reg["client_id"], "code_verifier": verifier,
    }).json()
    assert set(tok["scope"].split()) == {"read", "write", "trigger", "ingest"}
