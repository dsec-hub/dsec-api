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

    # --- API keys & rate limiting ---
    API_KEY_PREFIX: str = "dsec_live_"
    RATE_LIMIT_PER_MIN: int = 60
    RATE_LIMIT_TRIGGER_PER_DAY: int = 200
    GLOBAL_DAILY_LLM_CAP: int = 1000
    RATE_LIMIT_PER_IP_PER_MIN: int = 120
    MAX_REQUEST_BYTES: int = 100_000

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

    # --- Supabase Storage (image media for events/projects) ---
    # Server-side only. The service-role key bypasses RLS — never expose it to
    # the browser. Create a PUBLIC bucket named SUPABASE_STORAGE_BUCKET in the
    # Supabase dashboard. We do our own WebP/PNG conversion in Pillow, so the
    # paid image-transform add-on is not required.
    SUPABASE_URL: str = ""
    SUPABASE_SERVICE_ROLE_KEY: str = ""
    SUPABASE_STORAGE_BUCKET: str = "media"
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

    # --- v2 webhook secrets (reserved) ---
    DISCORD_WEBHOOK_SECRET: str = ""
    CALCOM_WEBHOOK_SECRET: str = ""

    # --- CORS (browser origins allowed to call the API directly) ---
    # Server-to-server callers (our Next.js apps' server code) are NOT subject to
    # CORS — this only governs fetches issued from a browser tab on one of our own
    # subdomains (e.g. a client component on hub.dsec.club / app.dsec.club).
    # Comma-separated; defaults cover the prod subdomains + local dev ports.
    CORS_ALLOW_ORIGINS: str = (
        "https://dsec.club,https://www.dsec.club,"
        "https://app.dsec.club,https://hub.dsec.club,"
        "http://localhost:3000,http://localhost:3001,http://localhost:3002"
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

    Called once at app startup (see app.main.create_app). Only enforced on Vercel
    (``VERCEL=1``); local/dev/test keep the convenient fallbacks. Without this a
    missing env var would silently leave the admin key-minting endpoint, gated
    docs and dashboard behind publicly-known credentials, or persist data to a
    throwaway serverless SQLite file. Failing loudly beats running open.
    """
    if os.environ.get("VERCEL") != "1":
        return
    s = s or settings
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
            + ". Set the real values as Vercel environment variables."
        )


settings = get_settings()
