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
    "read": "View everything: members, finances, events, projects, tasks, docs, and sponsors",
    "write": "Create and update events, projects, tasks, docs, sponsors, people, and partners",
    "trigger": "Run AI features, like generating meeting notes from a transcript",
    "ingest": "Import the weekly DUSA membership and P&L spreadsheets",
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


# The official DSEC duck wordmark (matches dsec-website/public/logo.svg). Inlined
# so the consent screen makes zero third-party requests — an OAuth sign-in page
# should be fully self-contained (CSP-friendly, private, instant). Brand colours
# kept verbatim; they read cleanly on the near-black canvas.
_LOGO = (
    '<svg class="logo" viewBox="0 0 76 48" fill="none" role="img" aria-label="DSEC"'
    ' xmlns="http://www.w3.org/2000/svg">'
    '<path d="M55.2514 14.5498H43.6484V32.7831H55.2514V29.0485H47.5161V25.314H53.7473V21.7992H47.5161V18.2843H55.2514V14.5498Z" fill="#D63384"/>'
    '<path d="M36.8447 25.1593L20.2773 17.1033V20.1515C20.3804 21.2625 20.6323 21.6937 21.6204 22.07L38.6963 30.4919V27.5543C38.5818 26.3716 38.2039 25.8364 36.8447 25.1593Z" fill="#D63384"/>'
    '<path d="M61.1535 29.0441C60.0141 27.4715 59.4056 25.5767 59.4162 23.6348L63.5323 23.6572C63.5265 24.7241 63.8608 25.7652 64.4868 26.6292C65.1128 27.4932 65.9978 28.1352 67.0134 28.4621C68.029 28.789 69.1224 28.7837 70.1348 28.447C71.1473 28.1103 71.2379 27.7197 72.7425 27.4461C74.2471 27.1725 76.0001 28.9724 76.0001 28.9724C74.876 30.556 73.2765 31.7401 71.4337 32.3529C69.591 32.9657 67.6009 32.9753 65.7523 32.3804C63.9037 31.7854 62.2928 30.6168 61.1535 29.0441Z" fill="#D63384"/>'
    '<path d="M61.1535 18.3252C60.0141 19.8979 59.4056 21.7927 59.4162 23.7346L63.5323 23.7122C63.5265 22.6452 63.8608 21.6042 64.4868 20.7402C65.1128 19.8762 65.9978 19.2342 67.0134 18.9073C68.029 18.5804 69.1224 18.5857 70.1348 18.9224C71.1473 19.2591 71.2379 19.6497 72.7425 19.9233C74.2471 20.1968 76.0001 18.397 76.0001 18.397C74.876 16.8134 73.2765 15.6293 71.4337 15.0165C69.591 14.4037 67.6009 14.394 65.7523 14.989C63.9037 15.584 62.2928 16.7526 61.1535 18.3252Z" fill="#D63384"/>'
    '<path d="M4.14392 23.6664H0V14.5498H7.63354C12.868 14.5498 16.5757 18.8911 16.5757 23.6664H12.4318C12.4318 20.8446 10.3598 18.674 7.63354 18.674C6.01929 18.6314 4.14392 18.674 4.14392 18.674V23.6664Z" fill="#D63384"/>'
    '<path d="M4.14392 23.6663H0V32.783H7.63354C12.868 32.783 16.5757 28.4417 16.5757 23.6663H12.4318C12.4318 26.4881 10.3598 28.6588 7.63354 28.6588C6.01929 28.7014 4.14392 28.6588 4.14392 28.6588V23.6663Z" fill="#D63384"/>'
    '<path d="M30.6785 0H27.4414C29.2212 5.00649 30.3169 7.80293 32.3923 12.7777C33.1562 13.779 33.6579 14.2282 34.8677 14.5498H38.676L33.1539 2.14516C32.354 0.617519 31.814 0.152977 30.6785 0Z" fill="#19E6E6"/>'
    '<path d="M28.916 6.44619C28.916 5.52532 26.7519 0.736788 26.7519 0.736788C25.5087 0.644703 23.5625 7.27159 20.2598 14.5499H23.9292C24.8283 14.1787 25.2703 13.7771 25.9992 12.8002C26.5637 11.9714 28.916 6.81454 28.916 6.44619Z" fill="#009A9C"/>'
    '<path d="M28.257 47.3328H31.4941C29.7143 42.3263 28.6187 39.5298 26.5433 34.5551C25.7794 33.5538 25.2776 33.1046 24.0679 32.783H20.2595L25.7816 45.1876C26.5816 46.7152 27.1216 47.1798 28.257 47.3328Z" fill="#19E6E6"/>'
    '<path d="M30.0196 40.8866C30.0196 41.8074 32.1836 46.596 32.1836 46.596C33.4268 46.6881 35.373 40.0612 38.6758 32.7829H35.0063C34.1072 33.154 33.6652 33.5556 32.9364 34.5326C32.3718 35.3613 30.0196 40.5182 30.0196 40.8866Z" fill="#009A9C"/>'
    "</svg>"
)


# Theme tokens mirror dsec-hub's dark theme (src/app/globals.css): near-pure-black
# Resend canvas, off-white ink, translucent-white hairlines, atmospheric glow (not
# shadow), and DSEC's Action Pink lifted to #ff5c8a for contrast on the dark floor.
# Titles run monospace (the hub uses Geist Mono); here we fall back to the system
# mono stack — the same fallback the hub renders before its web font loads.
_STYLE = """
:root {
  --bg:#000; --surface:#0a0a0c; --elevated:#141416;
  --border:rgba(255,255,255,.1); --border-strong:rgba(255,255,255,.16);
  --fg:#fcfdff; --muted:#9a9da0; --body:#cdd0d3;
  --accent:#ff5c8a; --accent-fg:#1a0a10; --danger:#ff2047;
  --r-card:16px; --r-control:12px;
  --font-sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Inter,Roboto,sans-serif;
  --font-mono:ui-monospace,"SF Mono",SFMono-Regular,Menlo,"Cascadia Code","Roboto Mono",monospace;
}
* { box-sizing:border-box; }
html { color-scheme:dark; }
body { margin:0; min-height:100vh; display:flex; align-items:center;
       justify-content:center; padding:24px; color:var(--fg);
       font:15px/1.55 var(--font-sans);
       -webkit-font-smoothing:antialiased; text-rendering:optimizeLegibility;
       background:radial-gradient(72% 55% at 50% -8%, rgba(255,92,138,.12), transparent 68%), var(--bg); }
.card { width:100%; max-width:424px; background:var(--surface);
        border:1px solid var(--border); border-radius:var(--r-card); padding:30px 28px;
        box-shadow:inset 0 1px 0 rgba(255,255,255,.04), 0 24px 70px -28px rgba(0,0,0,.9);
        animation:pop-in .22s cubic-bezier(.16,1,.3,1) both; }
@keyframes pop-in { from { opacity:0; transform:translateY(6px) scale(.985); } to { opacity:1; transform:none; } }
.brand { margin-bottom:22px; }
.logo { height:30px; width:auto; display:block; }
h1 { font-family:var(--font-mono); font-size:19px; line-height:1.25; letter-spacing:-.01em;
     font-weight:600; margin:0 0 8px; text-wrap:balance; }
p.sub { color:var(--muted); margin:0 0 22px; font-size:13.5px; line-height:1.5; }
.client { color:var(--fg); font-weight:600; }
label { display:block; font-size:12.5px; color:var(--muted); margin:0 0 7px; letter-spacing:.01em; }
input { width:100%; padding:11px 13px; border-radius:var(--r-control); border:1px solid var(--border);
        background:#070709; color:var(--fg); font-size:14px; font-family:inherit; margin-bottom:15px;
        transition:border-color .15s ease, box-shadow .15s ease, background .15s ease; }
input::placeholder { color:#6c6f72; }
input:hover { border-color:var(--border-strong); }
input:focus { outline:none; border-color:var(--accent); background:#0b0b0e;
              box-shadow:0 0 0 3px rgba(255,92,138,.16); }
.grant { margin:2px 0 9px; }
.scopes { border:1px solid var(--border); border-radius:var(--r-control); padding:2px 14px;
          margin:0 0 22px; background:rgba(255,255,255,.012); }
.scope { display:flex; gap:11px; align-items:flex-start; padding:11px 0;
         border-bottom:1px solid var(--border); }
.scope:last-child { border-bottom:none; }
.scope .s { flex:none; margin-top:1px; font-family:var(--font-mono); font-size:11px; font-weight:600;
            color:var(--accent); background:var(--elevated); border:1px solid var(--border);
            padding:2px 7px; border-radius:6px; letter-spacing:.02em; }
.scope .d { font-size:12.5px; line-height:1.45; color:var(--body); }
.row { display:flex; gap:10px; margin-top:2px; }
button { flex:1; padding:12px; border-radius:var(--r-control); border:1px solid transparent;
         font-size:14px; font-weight:600; font-family:inherit; cursor:pointer;
         transition:transform .12s cubic-bezier(.16,1,.3,1), background .16s ease,
                    color .16s ease, border-color .16s ease, filter .16s ease; }
button:active { transform:scale(.985); }
.allow { background:var(--accent); color:var(--accent-fg); }
.allow:hover { filter:brightness(1.07); }
.deny { background:transparent; color:var(--muted); border-color:var(--border); }
.deny:hover { background:var(--elevated); color:var(--fg); border-color:var(--border-strong); }
.err { background:rgba(255,32,71,.12); color:#ff9aac; border:1px solid rgba(255,32,71,.4);
       padding:10px 13px; border-radius:var(--r-control); font-size:13px; line-height:1.45; margin-bottom:18px; }
.foot { color:var(--muted); font-size:12px; line-height:1.5; margin:20px 0 0; text-align:center; }
.foot .arrow { color:var(--fg); }
:focus-visible { outline:2px solid var(--accent); outline-offset:2px; border-radius:4px; }
@media (prefers-reduced-motion:reduce) {
  *, *::before, *::after { animation-duration:.001ms !important; transition-duration:.001ms !important; }
}
"""


def _scope_rows(scopes: list[str]) -> str:
    out = []
    for s in scopes:
        out.append(
            f'<div class="scope"><span class="s">{html.escape(s)}</span>'
            f'<span class="d">{html.escape(_SCOPE_LABELS.get(s, s))}</span></div>'
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
  <div class="brand">{_LOGO}</div>
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
    <label class="grant">This connection will be able to:</label>
    <div class="scopes">{_scope_rows(scopes)}</div>
    <div class="row">
      <button class="deny" type="submit" name="action" value="deny">Cancel</button>
      <button class="allow" type="submit" name="action" value="allow">Sign in &amp; approve</button>
    </div>
  </form>
  <p class="foot">You're approving access for your DSEC account only. You can
     revoke it any time from Settings <span class="arrow">&rarr;</span> API &amp; MCP.</p>
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
  <div class="brand">{_LOGO}</div>
  <h1>{html.escape(title)}</h1>
  <p class="sub">{html.escape(message)}</p>
</div></body></html>"""
