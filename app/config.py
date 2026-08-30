"""Application configuration via pydantic Settings.

All values are loaded from environment variables / a local `.env` file.
In production (Vercel) these are set as project environment variables and
nothing is committed. See `.env.example` for the full list.
"""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Deployment environment ---
    # Drives validate_production_settings(). Historically that check keyed off
    # Vercel's `VERCEL=1`, which silently disabled every production guard the
    # moment the app moved to its own server. Set APP_ENV=production on the VPS
    # (docker compose does this) so the insecure-default checks still run.
    APP_ENV: str = Field(
        default_factory=lambda: "production" if os.environ.get("VERCEL") == "1" else "development"
    )

    # --- Shared agent / Apps Script auth ---
    # NOTE: the literal defaults below double as the "insecure default" sentinels
    # checked by validate_production_settings() — keep them in sync.
    AGENT_SECRET: str = "change-me-agent-secret"

    # --- Anthropic ---
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-haiku-4-5-20251001"

    # --- Email drafting context ---
    CALCOM_LINK: str = "https://cal.com/dsec"
    SIGNATURE: str = "Best regards,\nThe DSEC Committee"
    TONE: str = "friendly, concise, and professional"

    # --- Dashboard basic auth ---
    DASHBOARD_USER: str = "admin"
    DASHBOARD_PASS: str = "change-me-dashboard-pass"

    # --- Database (Neon Postgres, pooled connection string) ---
    DATABASE_URL: str = "sqlite:///./local.db"
    # Apply `alembic upgrade head` on startup. Convenient for local/dev; on
    # serverless (Vercel) it crashes the cold-start function and is the wrong
    # place to migrate Neon, so it defaults OFF when running on Vercel (which
    # always exports VERCEL=1). Migrations there are hand-run as a deploy step.
    # An explicit RUN_MIGRATIONS_ON_STARTUP env var still overrides this.
    RUN_MIGRATIONS_ON_STARTUP: bool = Field(
        default_factory=lambda: os.environ.get("VERCEL") != "1"
    )
    # SQLAlchemy pool sizing. The old hard-coded 5/2 was chosen for serverless,
    # where N ephemeral function instances EACH held a pool and the risk was
    # exhausting Neon's connection limit. One long-lived process inverts that:
    # 5 becomes the throughput ceiling, since Starlette runs the ~190 sync
    # endpoints in a 40-thread pool that all queue on those 5 connections.
    DB_POOL_SIZE: int = 15
    DB_MAX_OVERFLOW: int = 5
    DB_POOL_RECYCLE: int = 300

    # --- API keys & rate limiting ---
    API_KEY_PREFIX: str = "dsec_live_"
    # Per-key ceiling for AUTHENTICATED callers. 60/min (= 1 req/s) was calibrated
    # for humans holding keys; it is far too tight for our four Next.js apps, which
    # call this API server-side and can fan out to several endpoints in a single
    # page render. 300/min (= 5 req/s) per key still caps runaway loops while
    # leaving normal dashboard use comfortable. See ratelimit.check_request for
    # why authenticated traffic is no longer also charged to the per-IP bucket.
    RATE_LIMIT_PER_MIN: int = 300
    RATE_LIMIT_TRIGGER_PER_DAY: int = 200
    GLOBAL_DAILY_LLM_CAP: int = 1000
    # Per-IP ceiling, applied to UNAUTHENTICATED traffic only (public /website
    # feed, OAuth, webhooks). Depends on the reverse proxy setting a trustworthy
    # X-Real-IP — see app/core/net.py.
    RATE_LIMIT_PER_IP_PER_MIN: int = 120
    MAX_REQUEST_BYTES: int = 100_000
    # SEC-06 deploy-3: cap on OUTSTANDING (non-revoked) child keys a single caller
    # (service key) may have minted via POST /admin/keys/self. The per-minute
    # throttle is not a key-count limit, so without this a leaked service key could
    # mint an unbounded number of persistent child keys. Generous for legitimate
    # per-user minting; revoking children frees the budget back up.
    SELF_KEY_MAX_OUTSTANDING: int = 50

    # --- Member verification cards (digital membership card in the portal) ---
    # The member portal shows each verified member a "membership card" with a
    # stable, unique code + QR. The code is HMAC-derived from the roster row id
    # (deterministic → no storage, no migration) and the QR points at the public
    # verify page. Blank reuses AGENT_SECRET (already required non-default in
    # prod) so there is no NEW required secret; set it to rotate codes
    # independently of the agent secret.
    MEMBER_CODE_SECRET: str = ""
    # Public base the QR encodes, e.g. https://app.dsec.club/verify/<code>. Door
    # staff scan it to confirm the member (name + active status + face photo).
    MEMBER_VERIFY_BASE_URL: str = "https://app.dsec.club/verify"

    # --- Event preview links ("see it before publishing") ---
    # The committee dashboard can open a *draft* event on the public marketing
    # site before it goes live, via an unguessable, time-limited signed link (no
    # login). dsec-api mints + verifies the token — an HMAC over the event id +
    # expiry — so it needs no DB column (stateless), can't be forged or
    # enumerated, and stops working after EVENT_PREVIEW_TTL. Blank reuses
    # AGENT_SECRET (already required non-default in prod) so there is no NEW
    # required secret; set it to rotate every outstanding preview link at once.
    EVENT_PREVIEW_SECRET: str = ""
    EVENT_PREVIEW_TTL: int = 7 * 24 * 3600  # signed-link lifetime (7 days)
    # Page previews are a "look before you publish" action, not a shareable link.
    # Deliberately much shorter than EVENT_PREVIEW_TTL.
    PAGE_PREVIEW_TTL: int = 60 * 60  # 1 hour

    # --- Pre-meeting agenda share links ---
    # When a meeting agenda is shared, the API stamps a stable, unguessable token
    # and returns a public read-only URL of the form <base>/<token>. The view
    # itself is rendered by the committee dashboard (dsec-hub), which reads the
    # meeting row directly from Neon by token — no auth, no API key. Override per
    # environment (local dev runs the hub on :3002).
    AGENDA_SHARE_BASE_URL: str = "https://hub.dsec.club/agenda"

    # --- OAuth 2.1 (MCP authorization server) ---
    # The /mcp endpoint ALSO accepts OAuth 2.1 access tokens (alongside the
    # dsec_live_ API keys), so MCP clients whose "add connector" dialog only takes
    # a URL (e.g. Claude.ai) can connect → log in → approve, with no key pasting.
    # dsec-api is BOTH the authorization server and the resource server: it
    # authenticates users against the shared app_user/app_role tables and issues
    # opaque, DB-backed tokens, so validation is a local lookup (no JWT keys to
    # manage) and revocation is instant. The HMAC that signs the in-flight
    # authorize request reuses AGENT_SECRET (already required non-default in prod).
    OAUTH_ENABLED: bool = True
    # Public issuer origin used in discovery metadata + the resource (audience) id.
    # Blank → derived per-request from the (proxy-aware) Host, which is what you
    # want locally and in tests. Pin it in production (e.g. https://api.dsec.club)
    # so the issuer can't be influenced by a spoofed Host header.
    OAUTH_ISSUER: str = ""
    OAUTH_ACCESS_TOKEN_TTL: int = 3600  # access token lifetime, seconds (1 hour)
    OAUTH_REFRESH_TOKEN_TTL: int = 60 * 60 * 24 * 60  # refresh lifetime (60 days)
    OAUTH_AUTH_CODE_TTL: int = 600  # authorization-code lifetime (10 minutes)
    OAUTH_ACCESS_TOKEN_PREFIX: str = "dsec_at_"
    OAUTH_REFRESH_TOKEN_PREFIX: str = "dsec_rt_"
    # Redirect-URI hosts DSEC vouches for on the consent page (NEW-APIROUTERS-04).
    # Open Dynamic Client Registration lets anyone register a client with any
    # https callback and any display name, so the consent page names the callback
    # host and warns "DSEC has not verified this application" for any host NOT in
    # this comma-separated allowlist. Add the club's genuine MCP client callback
    # hosts (e.g. claude.ai) here so real connections don't show the warning.
    OAUTH_TRUSTED_REDIRECT_HOSTS: str = (
        "dsec.club,www.dsec.club,hub.dsec.club,localhost,127.0.0.1"
    )

    @property
    def oauth_trusted_redirect_hosts(self) -> set[str]:
        """OAUTH_TRUSTED_REDIRECT_HOSTS parsed to a lower-cased set of hosts."""
        return {
            h.strip().lower()
            for h in self.OAUTH_TRUSTED_REDIRECT_HOSTS.split(",")
            if h.strip()
        }

    # --- MCP transport security (DNS-rebinding protection) ---
    # The MCP SDK auto-enables a localhost-only Host allowlist, which 421s every
    # real request to a remote deploy (Host: api.dsec.club). This is a remote,
    # token-authenticated HTTPS API, so that protection is unnecessary. Comma-
    # separated Host allowlist (supports a ":*" port wildcard, e.g. "localhost:*").
    # BLANK (default) DISABLES the check — correct here, since /mcp already
    # requires a bearer key/OAuth token and CORS pins browser origins, and the
    # Host is dynamic (api.dsec.club + *.vercel.app previews). Set it to lock the
    # transport to specific hosts.
    MCP_ALLOWED_HOSTS: str = ""

    # --- Media object storage ---
    # Which backend holds the image binaries: "supabase" or "r2".
    #
    # Moving to Cloudflare R2 because R2 charges nothing for egress, which is the
    # line item that grows with traffic and the one that would eventually force a
    # paid Supabase tier. Storage itself is trivial either way — the whole library
    # measured 13.7 MB across 150 files — so this is done now precisely BECAUSE it
    # is small. The migration cost scales with file count, not bytes.
    #
    # Object paths are identical across backends (media_asset.webp_path /
    # png_path), so switching backends only changes the URL prefix stored in
    # webp_url / png_url. That is what makes this reversible: flip the variable
    # back and re-point the URLs, the objects on the other side are untouched.
    STORAGE_BACKEND: str = "supabase"

    # Server-side only. The service-role key bypasses RLS — never expose it to
    # the browser. Create a PUBLIC bucket named SUPABASE_STORAGE_BUCKET in the
    # Supabase dashboard. We do our own WebP/PNG conversion in Pillow, so the
    # paid image-transform add-on is not required.
    SUPABASE_URL: str = ""
    SUPABASE_SERVICE_ROLE_KEY: str = ""
    SUPABASE_STORAGE_BUCKET: str = "media"

    # Cloudflare R2 (S3-compatible). The endpoint is derived from the account id.
    # R2_PUBLIC_BASE_URL is what gets written into media_asset.*_url and served to
    # browsers — either an r2.dev public-bucket URL or, preferably, a custom
    # domain on the DSEC zone. It is NOT derivable from the account id, because a
    # bucket is private until you explicitly expose it, so it must be set
    # separately and the app refuses to use R2 without it.
    R2_ACCOUNT_ID: str = ""
    R2_ACCESS_KEY_ID: str = ""
    R2_SECRET_ACCESS_KEY: str = ""
    R2_BUCKET: str = "dsec-media"
    R2_PUBLIC_BASE_URL: str = ""
    MEDIA_MAX_UPLOAD_BYTES: int = 15_000_000  # 15 MB per source image
    MEDIA_MAX_DIMENSION: int = 2000  # longest side, px (downscaled if larger)
    # Hard byte budgets for the two derivatives. The Pillow pipeline steps down
    # quality (then pixel dimensions) until the encoded bytes fit under these.
    # WebP is the on-screen image; the download is a JPEG (opaque images) or a
    # PNG (transparent logos, which JPEG can't represent).
    MEDIA_WEBP_MAX_BYTES: int = 100_000  # display webp ceiling (~100 KB)
    MEDIA_DOWNLOAD_MAX_BYTES: int = 200_000  # download (jpeg/png) ceiling (~200 KB)
    # Document/image attachments (sponsors) — PDFs allowed, auto-compressed.
    ATTACHMENT_MAX_UPLOAD_BYTES: int = 25_000_000  # 25 MB per source file

    # --- Tally (post-event review forms) ---
    # Per-event feedback forms are created in Tally from the dashboard. The key
    # lives here (server-side only) like every other third-party secret; the
    # dsec-app reaches this API and never sees it. Blank disables the feature
    # (POST .../review-form -> 503). Get a key at tally.so → Settings → API.
    TALLY_API_KEY: str = ""
    TALLY_API_BASE: str = "https://api.tally.so"

    # --- Vercel Cron auth (daily reconciliation sync) ---
    CRON_SECRET: str = ""

    # --- Task-assignment notifications handed off to dsec-hub ---
    # dsec-api has NO notification delivery of its own — email/Telegram/Discord
    # and every per-user channel pref live in the committee dashboard (dsec-hub).
    # A task assigned via the REST API or the MCP server never touches hub's
    # server actions, so the dashboard's on-assign hook can't fire for it. To
    # close that gap we POST a small, best-effort event to hub's internal
    # endpoint, which runs the SAME on-assign notifier the dashboard uses (same
    # prefs + dedupe). One-way fire-and-forget: a slow/down hub never blocks or
    # fails the task write. Blank URL/secret disables the hand-off (silent no-op),
    # which is the right default for local dev with no hub running.
    HUB_NOTIFY_URL: str = ""  # e.g. https://hub.dsec.club/api/internal/notify-assignment
    HUB_NOTIFY_SECRET: str = ""  # must equal HUB_NOTIFY_SECRET on the dsec-hub deploy

    # --- v2 webhook secrets (reserved) ---
    DISCORD_WEBHOOK_SECRET: str = ""
    CALCOM_WEBHOOK_SECRET: str = ""

    # --- Outbound Discord relay (POST /public/notify, Cal.com booking alerts) ---
    # A Discord *incoming webhook* URL (Server Settings -> Integrations -> Webhooks
    # -> New Webhook -> Copy URL). This is the simplest outbound channel: no bot
    # token, no gateway socket, no OAuth — an HTTPS POST of {"content": "..."}
    # drops a message into one channel. Kept server-side like every other secret.
    # BLANK (default) disables all outbound Discord: /public/notify returns 503 and
    # the Cal.com booking alert silently no-ops. That is the correct default for
    # local dev and for a deploy that has not wired a webhook yet.
    DISCORD_NOTIFY_WEBHOOK_URL: str = ""
    # Post a short alert to Discord whenever a Cal.com booking lands (a new sponsor
    # lead). Off by default so enabling the webhook for /public/notify does not also
    # start narrating bookings; flip on to get booking pings in the channel.
    CALCOM_NOTIFY_DISCORD: bool = False

    # --- DUSA membership ingest safety valve ---
    # A membership import replaces the whole roster (see ingest/service.py): it
    # marks everyone not-current, then turns back on only the students in this
    # week's file. The ingest refuses a report that is empty or has collapsed
    # relative to the previous week, so a truncated/mis-parsed spreadsheet cannot
    # silently lock out every omitted member. Set this True for ONE deliberate,
    # human-reviewed mass drop (e.g. a real term rollover), then set it back — it
    # bypasses the fractional-drop check but NEVER the zero-row check. (NEW-APPDEEP-03)
    DUSA_INGEST_OVERRIDE: bool = False

    # --- Structured logging (host log drains) ---
    # LOG_FORMAT="json" emits one JSON object per line (timestamp, level, logger,
    # message, + any structured extras) for a log drain / aggregator to parse;
    # "text" (default) keeps the human-readable console format, so this is a no-op
    # until explicitly switched on. LOG_LEVEL tunes verbosity (DEBUG/INFO/WARNING/...).
    LOG_FORMAT: str = "text"
    LOG_LEVEL: str = "INFO"

    # --- Email decision-maker -> DUSA pipeline (ships DARK) ---
    # After the classify+draft stages, an optional LLM "decision-maker" stage can
    # read the email against a compact snapshot of open events and propose ONE
    # structured action (today: update_dusa_status) to keep the dashboard's DUSA
    # kanban in sync without anyone touching it by hand.
    #
    # Two independent guards, both defaulting to the SAFE position so the feature
    # ships dark and mutates nothing until an operator opts in:
    #   * EMAIL_DECISION_MAKER_ENABLED=False -> the stage never runs (unchanged
    #     draft-only behaviour). Turn on to START PROPOSING actions.
    #   * EMAIL_DECISION_DRY_RUN=True -> the stage runs and LOGS its proposed
    #     action to EventLog (so the audit view shows exactly what it *would* do)
    #     but writes NOTHING to the event. Set False only once the logged
    #     proposals look right — that is the human-confirm step from the runbook.
    # A proposal is applied only when enabled AND not dry-run AND the deterministic
    # event match clears EMAIL_MATCH_MIN_CONFIDENCE; otherwise it degrades to a
    # drafted reply flagged for a human. At most ONE event is mutated per email.
    EMAIL_DECISION_MAKER_ENABLED: bool = False
    EMAIL_DECISION_DRY_RUN: bool = True
    # Minimum name-match confidence (0..1) before an email may mutate an event.
    # Below this the decision is logged + flagged for a human, never applied.
    EMAIL_MATCH_MIN_CONFIDENCE: float = 0.8
    # Comma-separated sender domains authorised to drive a DUSA status change
    # (e.g. "deakin.edu.au,dusa.org.au"). The matcher decides WHICH event; this
    # decides WHETHER the sender is allowed to move it — otherwise anyone who
    # emails the committee could spoof "your event is approved" and, in live mode,
    # flip the kanban. BLANK trusts NO sender: a decision is still logged/flagged
    # but never applied, so live mode does nothing until this is configured.
    EMAIL_DUSA_SENDER_DOMAINS: str = ""

    @property
    def dusa_sender_domains(self) -> set[str]:
        """EMAIL_DUSA_SENDER_DOMAINS parsed to a lower-cased set of bare domains."""
        return {
            d.strip().lower().lstrip("@")
            for d in self.EMAIL_DUSA_SENDER_DOMAINS.split(",")
            if d.strip()
        }

    # --- Discord bot / slash-command interactions (games platform) ---
    # The Discord bot is a WEBHOOK bot (no gateway socket): Discord POSTs each
    # interaction to /discord/interactions, which is verified with Ed25519 (NOT
    # HMAC) against this PUBLIC KEY — a hex string from the Discord Developer
    # Portal -> your application -> General Information -> Public Key. Blank
    # disables verification in dev/test and fails closed in production. The
    # application id + bot token are used only to REGISTER slash commands and send
    # REST follow-ups (no socket). Leave blank to disable the integration.
    DISCORD_PUBLIC_KEY: str = ""
    DISCORD_APPLICATION_ID: str = ""
    DISCORD_BOT_TOKEN: str = ""

    # --- Games platform (arcade + Codle; surface = games.dsec.club) ---
    # Master switch. False unmounts /games and /game-link entirely, so the routes
    # 404 rather than answering — and the monthly draw cron stops firing.
    #
    # Set False in production on 2026-08-14: the games platform is parked while a
    # decision is made about giving it its own box. It is the only surface that
    # calls the API per user action (dsec-games fetches with `cache: "no-store"`,
    # unlike the website, which serves from Vercel's CDN and makes ZERO API calls
    # when idle), so it is also the only surface whose traffic scales with players
    # rather than with publishing. Parking it removes essentially all per-user
    # load from the API.
    #
    # Default True so local dev and the tests are unaffected; production turns it
    # off through the environment. Flip it back and redeploy to restore — no code
    # change, and no data is touched either way.
    GAMES_ENABLED: bool = True
    # Public base URL of the playable web surface the bot deep-links players to
    # (e.g. /play -> ${GAMES_BASE_URL}/flappy-duck). Override per environment.
    GAMES_BASE_URL: str = "https://games.dsec.club"
    # Secret that derives the short Discord <-> account link codes (HMAC over the
    # account id, same idea as the membership-card code). Blank reuses
    # AGENT_SECRET (already required non-default in prod), so there is no NEW
    # required secret; set it to rotate link codes independently.
    GAMES_LINK_SECRET: str = ""
    # Secret that signs the short-lived Flappy Duck play-session token (binds a
    # score submission to a server-issued round so a recorded score can't be
    # replayed forever). Blank reuses AGENT_SECRET.
    GAMES_SESSION_SECRET: str = ""
    # How long a signed Flappy session stays valid after the round is fetched.
    GAMES_SESSION_TTL: int = 3600  # seconds

    # --- CORS (browser origins allowed to call the API directly) ---
    # Server-to-server callers (our Next.js apps' server code) are NOT subject to
    # CORS — this only governs fetches issued from a browser tab on one of our own
    # subdomains (e.g. a client component on hub.dsec.club / app.dsec.club).
    # Comma-separated; defaults cover the prod subdomains + local dev ports.
    CORS_ALLOW_ORIGINS: str = (
        "https://dsec.club,https://www.dsec.club,"
        "https://app.dsec.club,https://hub.dsec.club,https://games.dsec.club,"
        "http://localhost:3000,http://localhost:3001,http://localhost:3002,http://localhost:3003"
    )

    @property
    def cors_allow_origins_list(self) -> list[str]:
        """CORS_ALLOW_ORIGINS parsed into a clean list of origins."""
        return [o.strip() for o in self.CORS_ALLOW_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance.

    Cached so settings are parsed once per warm function instance. Safe under
    Vercel's Fluid Compute model since it holds no per-request state.
    """
    return Settings()


# Insecure factory defaults that must never reach production (kept in sync with
# the field defaults above).
_INSECURE_DEFAULTS = {
    "AGENT_SECRET": "change-me-agent-secret",
    "DASHBOARD_PASS": "change-me-dashboard-pass",
}


def validate_production_settings(s: "Settings | None" = None) -> None:
    """Refuse to boot in production with insecure defaults or an ephemeral DB.

    Called once at app startup (see app.main.create_app). Without this a missing
    env var would silently leave the admin key-minting endpoint, gated docs and
    dashboard behind publicly-known credentials, or persist data to a throwaway
    SQLite file. Failing loudly beats running open.

    Enforced whenever ``APP_ENV=production`` (set by docker compose on the VPS) or
    the legacy ``VERCEL=1`` is present. It previously keyed off ``VERCEL=1``
    ALONE, which meant lifting the app onto its own server silently disabled every
    check below — the guard would have vanished exactly when it started to matter.
    """
    s = s or settings
    if s.APP_ENV.lower() != "production" and os.environ.get("VERCEL") != "1":
        return
    problems: list[str] = []
    if s.AGENT_SECRET == _INSECURE_DEFAULTS["AGENT_SECRET"]:
        problems.append("AGENT_SECRET is still the default")
    if s.DASHBOARD_PASS == _INSECURE_DEFAULTS["DASHBOARD_PASS"]:
        problems.append("DASHBOARD_PASS is still the default")
    if s.DATABASE_URL.startswith("sqlite"):
        problems.append("DATABASE_URL still points at SQLite (set the Neon pooled URL)")
    if problems:
        raise RuntimeError(
            "Refusing to start: insecure production configuration — "
            + "; ".join(problems)
            + ". Set the real values in the deployment environment "
            "(VPS: the .env file read by docker compose)."
        )


def is_production() -> bool:
    """True on the VPS (``APP_ENV=production``) or the legacy Vercel deploy.

    The single definition of "are we live"; the auth guards fail CLOSED when it
    is true. It used to live in ``app.auth`` keyed off ``VERCEL=1`` alone, which
    returned False on the VPS (Vercel exports ``VERCEL``; the VPS does not) and so
    silently put every production auth check on its dev / fail-open path. Defined
    here, next to ``validate_production_settings`` — which already keys off
    ``APP_ENV`` — so there is one predicate and never a second one to forget.
    """
    return settings.APP_ENV.lower() == "production" or os.environ.get("VERCEL") == "1"


settings = get_settings()
