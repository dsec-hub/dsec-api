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
from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import IntegrityError
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
        """Increment this minute's counter and return the new value.

        The increment is computed **in SQL** (``count = count + 1`` under the
        row lock the UPDATE takes), not read into Python and written back.

        That distinction is the whole point. The previous version did
        ``SELECT`` → ``row.count += 1`` → ``commit``, so concurrent requests all
        read the same value and wrote back the same value + 1, losing every
        increment but one. Measured against the live box: 30 sequential requests
        raised the counter by 30; **30 concurrent requests raised it by 9**. A
        flood is concurrent by definition, so the per-IP limit failed in exactly
        the case it exists for, while looking correct under manual testing.

        `.first()` rather than `.scalar_one_or_none()`: the unique constraint is
        on (key_id, window_start) and excludes `bucket`, and Postgres treats NULL
        key_ids as distinct — so two per-IP requests that both miss can still
        race in duplicate rows. Ordering by id makes every later request pick the
        same one, so the duplicates stop mattering after the first instant
        instead of splitting the count indefinitely.
        """
        now = datetime.now(timezone.utc)
        window = _minute_window(now)
        where = (
            RateLimit.key_id == key_id,
            RateLimit.bucket == bucket,
            RateLimit.window_start == window,
        )
        row_id = db.execute(
            select(RateLimit.id).where(*where).order_by(RateLimit.id).limit(1)
        ).scalars().first()

        if row_id is not None:
            new_count = db.execute(
                update(RateLimit)
                .where(RateLimit.id == row_id)
                .values(count=RateLimit.count + 1)
                .returning(RateLimit.count)
            ).scalar_one()
            db.commit()
            return new_count

        # First request in this window. Two callers can reach here at once; the
        # loser of that race retries as an update rather than inserting a second
        # row or surfacing an IntegrityError to the request.
        try:
            new_count = db.execute(
                insert(RateLimit)
                .values(key_id=key_id, bucket=bucket, window_start=window, count=1)
                .returning(RateLimit.count)
            ).scalar_one()
            db.commit()
            return new_count
        except IntegrityError:
            db.rollback()
            new_count = db.execute(
                update(RateLimit)
                .where(*where)
                .values(count=RateLimit.count + 1)
                .returning(RateLimit.count)
            ).scalars().first()
            db.commit()
            # A concurrent DELETE (the sweeper) between the failed insert and
            # this update would leave nothing to increment. Treat it as the first
            # request of the window rather than 500-ing.
            return new_count if new_count is not None else 1

    def check_request(self, db: Session, *, key_id: int | None, ip: str) -> None:
        # Authenticated callers are limited PER KEY, not per IP.
        #
        # The per-IP bucket exists to protect the unauthenticated surface (the
        # public /website feed, the OAuth endpoints, the Discord webhook) from an
        # anonymous flood. Applying it to key-authenticated traffic actively
        # breaks us: dsec-website, dsec-app, dsec-hub and dsec-games all call this
        # API from *server-side* code, so they egress from a small number of
        # addresses — often one. Under a shared per-IP bucket they burn each
        # other's budget and 429 in a way that looks random and platform-agnostic.
        # Each platform holds its own API key, so the per-key limit below is both
        # the correct control and a strictly tighter one.
        if key_id is None:
            ip_count = self._bump_minute(db, key_id=None, bucket=f"ip:{ip}")
            if ip_count > settings.RATE_LIMIT_PER_IP_PER_MIN:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="per-IP rate limit exceeded",
                    headers={"Retry-After": "60"},
                )
        else:
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
