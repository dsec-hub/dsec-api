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

    def check_and_count_llm_global(self, db: Session) -> None: ...


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

        `.first()` rather than `.scalar_one_or_none()`: historically the unique
        key was (key_id, window_start) and excluded `bucket`, and Postgres treats
        NULL key_ids as distinct, so two per-IP requests that both missed could
        race into duplicate rows. Migration b1d4f7a9c3e2 made the key
        (key_id, bucket, window_start) with NULLS NOT DISTINCT, which prevents
        those duplicates on Neon; the order-by-id read stays as belt-and-braces
        (and still matters on SQLite dev, where NULLs remain distinct).
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

    def _global_llm_today(self, db: Session, day: datetime) -> int:
        """Sum today's trigger counters across every key (and the keyless bucket)."""
        return db.execute(
            select(func.coalesce(func.sum(RateLimit.trigger_count_today), 0)).where(
                RateLimit.window_start == day,
                RateLimit.bucket == "trigger",
            )
        ).scalar_one()

    def check_and_count_llm_global(self, db: Session) -> None:
        """Enforce + count the global daily LLM cap for a KEYLESS caller.

        The REST trigger routes hold an APIKey and go through
        ``check_and_count_trigger``; the agent-secret paths (``/email/process``)
        have no key, so their LLM spend was never counted against — nor bounded
        by — ``GLOBAL_DAILY_LLM_CAP``. This counts one unit of spend for today
        into a NULL-keyed ``trigger`` row, which the global sum already includes,
        so email and the keyed trigger routes share one daily budget.

        Raises 429 (no increment) when the cap is already reached, exactly like
        the keyed path; the email pipeline catches that and degrades to ignore.
        """
        now = datetime.now(timezone.utc)
        day = _day_window(now)
        if self._global_llm_today(db, day) >= settings.GLOBAL_DAILY_LLM_CAP:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="global daily LLM cap reached; no LLM call made",
                headers={"Retry-After": "3600"},
            )
        self._bump_global_trigger(db, day)

    def _bump_global_trigger(self, db: Session, day: datetime) -> None:
        """Increment the NULL-keyed daily trigger counter, in SQL.

        Mirrors ``_bump_minute``: the increment is ``+ 1`` under the UPDATE's row
        lock (not read-modify-write in Python, which drops concurrent increments),
        and the first-of-day insert has an IntegrityError fallback so a concurrent
        first email converges on an update instead of raising.
        """
        where = (
            RateLimit.key_id.is_(None),
            RateLimit.bucket == "trigger",
            RateLimit.window_start == day,
        )
        row_id = db.execute(
            select(RateLimit.id).where(*where).order_by(RateLimit.id).limit(1)
        ).scalars().first()
        if row_id is not None:
            db.execute(
                update(RateLimit)
                .where(RateLimit.id == row_id)
                .values(trigger_count_today=RateLimit.trigger_count_today + 1)
            )
            db.commit()
            return
        try:
            db.execute(
                insert(RateLimit).values(
                    key_id=None, bucket="trigger", window_start=day, trigger_count_today=1
                )
            )
            db.commit()
        except IntegrityError:
            db.rollback()
            db.execute(
                update(RateLimit)
                .where(*where)
                .values(trigger_count_today=RateLimit.trigger_count_today + 1)
            )
            db.commit()

    def check_and_count_trigger(self, db: Session, *, key_id: int) -> None:
        """Enforce per-key daily trigger cap AND the global daily LLM cap.

        Called by trigger routes BEFORE any LLM work. If a cap is hit, raises 429
        and no LLM call is made.
        """
        now = datetime.now(timezone.utc)
        day = _day_window(now)

        # Global daily cap across all keys (sum of today's trigger counters).
        if self._global_llm_today(db, day) >= settings.GLOBAL_DAILY_LLM_CAP:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="global daily LLM cap reached; no LLM call made",
                headers={"Retry-After": "3600"},
            )

        # Per-key daily trigger counter.
        where = (
            RateLimit.key_id == key_id,
            RateLimit.bucket == "trigger",
            RateLimit.window_start == day,
        )
        row = db.execute(select(RateLimit).where(*where)).scalar_one_or_none()
        if row is None:
            # Belt-and-braces: two first-of-day triggers can race to insert, and
            # in the (non-atomic) window around the OPS-05 constraint deploy an
            # insert could still collide with the midnight 'req' row under the old
            # (key_id, window_start) key. Catch IntegrityError, roll back, and
            # re-SELECT so we converge on the existing row instead of surfacing a
            # 500 that would pin every trigger route for that key for the UTC day.
            row = RateLimit(
                key_id=key_id, bucket="trigger", window_start=day, trigger_count_today=0
            )
            db.add(row)
            try:
                db.flush()
            except IntegrityError:
                db.rollback()
                row = db.execute(select(RateLimit).where(*where)).scalar_one_or_none()
                if row is None:
                    # A concurrent prune deleted it between the flush and this
                    # re-select; treat this call as the first trigger of the day.
                    row = RateLimit(
                        key_id=key_id, bucket="trigger", window_start=day,
                        trigger_count_today=0,
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
