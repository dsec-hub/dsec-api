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


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance.

    Cached so settings are parsed once per warm function instance. Safe under
    Vercel's Fluid Compute model since it holds no per-request state.
    """
    return Settings()


settings = get_settings()
