"""Rate limiting & abuse protection — Neon-backed, serverless-safe.

Defence in layers, calibrated to committee scale:
- Per-key fixed-window request limit (`RATE_LIMIT_PER_MIN`).
- Per-IP fixed-window limit, independent of key.
- Per-key daily `trigger` cap and a global daily LLM cap — the real money guard.

A `RateLimiter` Protocol defines the interface; `NeonRateLimiter` is the one
implementation. Redis is the documented swap-in when/if the API goes public —
not built yet. No in-process counters: all state lives in the `RateLimit` table,
so it survives Vercel's stateless function model.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import RateLimit


def _minute_window(now: datetime) -> datetime:
    return now.replace(second=0, microsecond=0)


def _day_window(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


class RateLimiter(Protocol):
    """Interface so the backend can be swapped (Neon now, Redis later)."""

    def check_request(self, db: Session, *, key_id: int | None, ip: str) -> None: ...

    def check_and_count_trigger(self, db: Session, *, key_id: int) -> None: ...


class NeonRateLimiter:
    """Fixed-window counters stored in Postgres (Neon). Slightly loose under
    burst, one write per request — acceptable at committee scale."""

    def _bump_minute(self, db: Session, *, key_id: int | None, bucket: str) -> int:
        now = datetime.now(timezone.utc)
        window = _minute_window(now)
        # `.first()`, not `.scalar_one_or_none()`: the unique constraint is on
        # (key_id, window_start) and excludes `bucket`, and Postgres treats NULL
        # key_ids as distinct — so concurrent per-IP (key_id=None) requests can
        # race in duplicate rows for the same bucket+window. `.first()` tolerates
        # that (picks one, increments it) instead of raising MultipleResultsFound
        # and 500-ing the request.
        row = db.execute(
            select(RateLimit)
            .where(
                RateLimit.key_id == key_id,
                RateLimit.bucket == bucket,
                RateLimit.window_start == window,
            )
            .order_by(RateLimit.id)
        ).scalars().first()
        if row is None:
            row = RateLimit(key_id=key_id, bucket=bucket, window_start=window, count=0)
            db.add(row)
        row.count += 1
        db.commit()
        return row.count

    def check_request(self, db: Session, *, key_id: int | None, ip: str) -> None:
        # Per-IP limit (key_id null, bucket = ip:<addr>).
        ip_count = self._bump_minute(db, key_id=None, bucket=f"ip:{ip}")
        if ip_count > settings.RATE_LIMIT_PER_IP_PER_MIN:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="per-IP rate limit exceeded",
                headers={"Retry-After": "60"},
            )
        # Per-key limit.
        if key_id is not None:
            key_count = self._bump_minute(db, key_id=key_id, bucket="req")
            if key_count > settings.RATE_LIMIT_PER_MIN:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="per-key rate limit exceeded",
                    headers={"Retry-After": "60"},
                )

    def check_and_count_trigger(self, db: Session, *, key_id: int) -> None:
        """Enforce per-key daily trigger cap AND the global daily LLM cap.

        Called by trigger routes BEFORE any LLM work. If a cap is hit, raises 429
        and no LLM call is made.
        """
        now = datetime.now(timezone.utc)
        day = _day_window(now)

        # Global daily cap across all keys (sum of today's trigger counters).
        global_today = db.execute(
            select(func.coalesce(func.sum(RateLimit.trigger_count_today), 0)).where(
                RateLimit.window_start == day,
                RateLimit.bucket == "trigger",
            )
        ).scalar_one()
        if global_today >= settings.GLOBAL_DAILY_LLM_CAP:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="global daily LLM cap reached; no LLM call made",
                headers={"Retry-After": "3600"},
            )

        # Per-key daily trigger counter.
        row = db.execute(
            select(RateLimit).where(
                RateLimit.key_id == key_id,
                RateLimit.bucket == "trigger",
                RateLimit.window_start == day,
            )
        ).scalar_one_or_none()
        if row is None:
            row = RateLimit(
                key_id=key_id, bucket="trigger", window_start=day, trigger_count_today=0
            )
            db.add(row)
        if row.trigger_count_today >= settings.RATE_LIMIT_TRIGGER_PER_DAY:
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="per-key daily trigger cap reached; no LLM call made",
                headers={"Retry-After": "3600"},
            )
        row.trigger_count_today += 1
        db.commit()


# Single shared instance. Swap this construction for a RedisRateLimiter to migrate.
limiter: RateLimiter = NeonRateLimiter()
