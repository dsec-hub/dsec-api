"""Application configuration via pydantic Settings.

All values are loaded from environment variables / a local `.env` file.
In production (Vercel) these are set as project environment variables and
nothing is committed. See `.env.example` for the full list.
"""

from __future__ import annotations

from functools import lru_cache

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

    # --- OpenAI ---
    OPENAI_API_KEY: str = ""
    OPENAI_CLASSIFY_MODEL: str = "gpt-4o-mini"
    OPENAI_DRAFT_MODEL: str = "gpt-4o-mini"

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
    # serverless (Vercel) prefer a deploy-step migration and set this false.
    RUN_MIGRATIONS_ON_STARTUP: bool = True

    # --- API keys & rate limiting ---
    API_KEY_PREFIX: str = "dsec_live_"
    RATE_LIMIT_PER_MIN: int = 60
    RATE_LIMIT_TRIGGER_PER_DAY: int = 200
    GLOBAL_DAILY_LLM_CAP: int = 1000
    RATE_LIMIT_PER_IP_PER_MIN: int = 120
    MAX_REQUEST_BYTES: int = 100_000

    # --- Vercel Cron auth (daily reconciliation sync) ---
    CRON_SECRET: str = ""

    # --- v2 webhook secrets (reserved) ---
    DISCORD_WEBHOOK_SECRET: str = ""
    CALCOM_WEBHOOK_SECRET: str = ""
    NOTION_WEBHOOK_SECRET: str = ""

    # --- Notion events sync (reserved for v2 implementation) ---
    NOTION_API_KEY: str = ""
    NOTION_EVENTS_DATABASE_ID: str = ""


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance.

    Cached so settings are parsed once per warm function instance. Safe under
    Vercel's Fluid Compute model since it holds no per-request state.
    """
    return Settings()


settings = get_settings()
