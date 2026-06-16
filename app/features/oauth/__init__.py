"""OAuth 2.1 authorization server for the MCP endpoint.

Lets MCP clients (e.g. Claude.ai's "Add custom connector", whose dialog accepts
only a URL) authenticate by logging in + approving, instead of pasting a
``dsec_live_`` key. dsec-api is both the authorization server (this package) and
the resource server (``/mcp``), so tokens are opaque and validated by a local DB
lookup. See ``service.py`` for the token primitives and ``router.py`` for the
endpoints; ``app/features/mcp/auth.py`` accepts the issued access tokens.
"""
