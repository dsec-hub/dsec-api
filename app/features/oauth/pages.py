"""The login + consent screen, plus the HMAC-signed request token that carries
the validated authorize parameters across the GET → POST round-trip.

The signed ``req`` token is the anti-tamper + anti-CSRF mechanism: the GET
handler validates the OAuth request, packs the parameters into a token signed
with ``AGENT_SECRET``, and the POST handler trusts ONLY that token (not resubmit-
table form fields). An attacker can neither forge it nor alter the parameters,
and it expires, so a stale or cross-site POST is rejected.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import json
from datetime import datetime, timezone

from app.config import settings
from app.features.oauth.service import b64url_decode, b64url_encode

_REQUEST_TTL_SECONDS = 900  # the login page is valid for 15 minutes

_SCOPE_LABELS = {
    "read": "Read everything — members, finances, events, projects, tasks, docs, sponsors",
    "write": "Create & update events, projects, tasks, docs, sponsors, people, partners",
    "trigger": "Run AI features (e.g. generate meeting notes from a transcript)",
    "ingest": "Import the weekly DUSA membership / P&L spreadsheets",
}


def _now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def sign_request(payload: dict) -> str:
    payload = {**payload, "iat": _now_ts()}
    body = b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    mac = b64url_encode(
        hmac.new(settings.AGENT_SECRET.encode(), body.encode(), hashlib.sha256).digest()
    )
    return f"{body}.{mac}"


def verify_request(token: str) -> dict | None:
    if not token or "." not in token:
        return None
    body, _, mac = token.partition(".")
    expected = b64url_encode(
        hmac.new(settings.AGENT_SECRET.encode(), body.encode(), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(expected, mac):
        return None
    try:
        payload = json.loads(b64url_decode(body))
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    iat = payload.get("iat", 0)
    if not isinstance(iat, int) or _now_ts() - iat > _REQUEST_TTL_SECONDS:
        return None
    return payload


_STYLE = """
:root { --pink:#e91e63; --bg:#0b0b0d; --card:#15151a; --line:#26262e;
        --txt:#f4f4f5; --muted:#a1a1aa; }
* { box-sizing:border-box; }
body { margin:0; min-height:100vh; display:flex; align-items:center;
       justify-content:center; background:var(--bg); color:var(--txt);
       font:15px/1.5 ui-sans-serif,system-ui,-apple-system,Segoe UI,Inter,sans-serif;
       padding:24px; }
.card { width:100%; max-width:420px; background:var(--card);
        border:1px solid var(--line); border-radius:16px; padding:28px; }
.brand { display:flex; align-items:center; gap:10px; margin-bottom:18px; }
.dot { width:10px; height:10px; border-radius:50%; background:var(--pink); }
.brand b { font-size:15px; letter-spacing:.02em; }
h1 { font-size:19px; margin:0 0 6px; }
p.sub { color:var(--muted); margin:0 0 20px; font-size:13.5px; }
label { display:block; font-size:13px; color:var(--muted); margin:0 0 6px; }
input { width:100%; padding:11px 13px; border-radius:10px; border:1px solid var(--line);
        background:#0e0e12; color:var(--txt); font-size:14px; margin-bottom:14px; }
input:focus { outline:none; border-color:var(--pink); }
.scopes { border:1px solid var(--line); border-radius:12px; padding:6px 14px;
          margin:6px 0 20px; }
.scopes div { padding:8px 0; border-bottom:1px solid var(--line); font-size:13px; }
.scopes div:last-child { border-bottom:none; }
.scopes .s { color:var(--pink); font-weight:600; margin-right:8px;
             font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
.row { display:flex; gap:10px; }
button { flex:1; padding:12px; border-radius:10px; border:none; font-size:14px;
         font-weight:600; cursor:pointer; }
.allow { background:var(--pink); color:#fff; }
.deny { background:transparent; color:var(--muted); border:1px solid var(--line); }
.err { background:rgba(233,30,99,.12); color:#ff8ab4; border:1px solid rgba(233,30,99,.4);
       padding:10px 12px; border-radius:10px; font-size:13px; margin-bottom:16px; }
.foot { color:var(--muted); font-size:12px; margin-top:18px; text-align:center; }
.client { color:var(--txt); font-weight:600; }
"""


def _scope_rows(scopes: list[str]) -> str:
    out = []
    for s in scopes:
        out.append(
            f'<div><span class="s">{html.escape(s)}</span>'
            f'{html.escape(_SCOPE_LABELS.get(s, s))}</div>'
        )
    return "".join(out)


def render_consent(*, req_token: str, client_name: str, scopes: list[str], error: str | None = None) -> str:
    """The combined login + consent page."""
    err_html = f'<div class="err">{html.escape(error)}</div>' if error else ""
    client = html.escape(client_name or "An application")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Connect to DSEC</title><style>{_STYLE}</style></head>
<body><div class="card">
  <div class="brand"><span class="dot"></span><b>DSEC</b></div>
  <h1>Connect to the DSEC workspace</h1>
  <p class="sub"><span class="client">{client}</span> wants to access the DSEC
     workspace on your behalf. Sign in to approve.</p>
  {err_html}
  <form method="post" action="/oauth/authorize" autocomplete="on">
    <input type="hidden" name="req" value="{html.escape(req_token)}">
    <label for="email">Email</label>
    <input id="email" name="email" type="email" required autocomplete="username" autofocus>
    <label for="password">Password</label>
    <input id="password" name="password" type="password" required autocomplete="current-password">
    <label>This will grant the connection:</label>
    <div class="scopes">{_scope_rows(scopes)}</div>
    <div class="row">
      <button class="deny" type="submit" name="action" value="deny">Cancel</button>
      <button class="allow" type="submit" name="action" value="allow">Sign in &amp; approve</button>
    </div>
  </form>
  <p class="foot">You're approving access for your DSEC account only. You can
     revoke it any time from Settings → API &amp; MCP.</p>
</div></body></html>"""


def render_error(*, title: str, message: str) -> str:
    """A standalone error page used when we must NOT redirect (bad client /
    redirect_uri) — redirecting an unvalidated URI would be an open redirect."""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>{html.escape(title)}</title><style>{_STYLE}</style></head>
<body><div class="card">
  <div class="brand"><span class="dot"></span><b>DSEC</b></div>
  <h1>{html.escape(title)}</h1>
  <p class="sub">{html.escape(message)}</p>
</div></body></html>"""
