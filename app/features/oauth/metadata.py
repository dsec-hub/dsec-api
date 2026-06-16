"""OAuth discovery metadata (RFC 8414 + RFC 9728) and the proxy-aware base URL.

The base URL (issuer) is pinned by ``OAUTH_ISSUER`` when set; otherwise it's
derived from the request's forwarded scheme + host so it's correct locally, in
tests, and behind Vercel's proxy without configuration.
"""

from __future__ import annotations

from fastapi import Request

from app.config import settings
from app.features.oauth.service import SUPPORTED_SCOPES


def base_url(request: Request) -> str:
    """The public origin (no trailing slash): scheme://host."""
    if settings.OAUTH_ISSUER:
        return settings.OAUTH_ISSUER.rstrip("/")
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    if not proto:
        proto = request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if not host:
        host = request.url.netloc
    return f"{proto}://{host}"


def resource_url(base: str) -> str:
    """The protected resource (the MCP endpoint) this AS issues tokens for."""
    return f"{base}/mcp"


def authorization_server_metadata(base: str) -> dict:
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "registration_endpoint": f"{base}/oauth/register",
        "revocation_endpoint": f"{base}/oauth/revoke",
        "scopes_supported": list(SUPPORTED_SCOPES),
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": [
            "none",
            "client_secret_post",
            "client_secret_basic",
        ],
        "code_challenge_methods_supported": ["S256"],
        "service_documentation": f"{base}/mcp-setup",
    }


def protected_resource_metadata(base: str) -> dict:
    return {
        "resource": resource_url(base),
        "authorization_servers": [base],
        "scopes_supported": list(SUPPORTED_SCOPES),
        "bearer_methods_supported": ["header"],
        "resource_documentation": f"{base}/mcp-setup",
    }
