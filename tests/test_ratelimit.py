"""Rate-limiter robustness — the per-IP path must survive duplicate counter rows.

The `rate_limit` unique constraint is (key_id, window_start) and excludes
`bucket`; Postgres also treats NULL key_ids as distinct. So concurrent per-IP
requests (key_id=None) can race in duplicate rows for the same bucket+window.
The limiter must tolerate that instead of raising MultipleResultsFound (which was
500-ing the public website feed in production).
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.ratelimit import _minute_window, limiter
from app.models import RateLimit


def test_per_ip_limiter_tolerates_duplicate_rows(db):
    window = _minute_window(datetime.now(timezone.utc))
    # Simulate the race: two NULL-key_id rows for the same ip bucket + window.
    db.add(RateLimit(key_id=None, bucket="ip:1.2.3.4", window_start=window, count=1))
    db.add(RateLimit(key_id=None, bucket="ip:1.2.3.4", window_start=window, count=1))
    db.commit()

    # Must not raise sqlalchemy.exc.MultipleResultsFound.
    limiter.check_request(db, key_id=None, ip="1.2.3.4")


# --- lost updates under concurrency ------------------------------------------
#
# _bump_minute used to SELECT the row, do `row.count += 1` in Python, and
# commit. Concurrent requests therefore all read the same value and wrote back
# the same value + 1, losing every increment but one. Measured on the live VPS:
# 30 sequential requests raised the counter by 30; 30 CONCURRENT requests raised
# it by 9. A flood is concurrent by definition, so the per-IP limit failed in
# precisely the case it exists for.
#
# The real proof is the load test against Postgres (SQLite serialises writers,
# so a race will not reproduce here). What these pin is the observable contract
# the fix has to keep: every call counts, across sessions, and the limit fires
# on the correct request.


def test_every_bump_counts_across_independent_sessions():
    from app.core.ratelimit import limiter
    from app.db import SessionLocal

    counts = []
    for _ in range(20):
        s = SessionLocal()
        try:
            counts.append(limiter._bump_minute(s, key_id=None, bucket="ip:10.1.1.1"))
        finally:
            s.close()
    # 1..20 with nothing skipped or repeated — a lost update shows up as a
    # duplicate value here.
    assert counts == list(range(1, 21))


def test_limit_fires_on_the_request_that_crosses_it(db, monkeypatch):
    from app.core import ratelimit as rl

    monkeypatch.setattr(rl.settings, "RATE_LIMIT_PER_IP_PER_MIN", 5)
    for i in range(5):
        rl.limiter.check_request(db, key_id=None, ip="10.2.2.2")  # 1..5 allowed
    with pytest.raises(HTTPException) as exc:
        rl.limiter.check_request(db, key_id=None, ip="10.2.2.2")  # 6th rejected
    assert exc.value.status_code == 429


def test_separate_ips_do_not_share_a_bucket(db, monkeypatch):
    from app.core import ratelimit as rl

    monkeypatch.setattr(rl.settings, "RATE_LIMIT_PER_IP_PER_MIN", 3)
    for _ in range(3):
        rl.limiter.check_request(db, key_id=None, ip="10.3.3.3")
    # a different IP starts clean
    rl.limiter.check_request(db, key_id=None, ip="10.4.4.4")
    with pytest.raises(HTTPException):
        rl.limiter.check_request(db, key_id=None, ip="10.3.3.3")


def test_trigger_and_request_counters_coexist_in_the_same_window(db):
    """OPS-05: at 00:00 UTC the minute-window and the day-window are the same
    timestamp, so one key's 'trigger' row and its 'req' row collide on
    UNIQUE(key_id, window_start). The constraint must include `bucket`.

    Fails with IntegrityError against the old (key_id, window_start) key; passes
    once the unique key is (key_id, bucket, window_start).
    """
    midnight = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    db.add(RateLimit(key_id=1, bucket="trigger", window_start=midnight, trigger_count_today=1))
    db.commit()
    # Same key, same window, different bucket — must be allowed.
    db.add(RateLimit(key_id=1, bucket="req", window_start=midnight, count=1))
    db.commit()  # <-- IntegrityError on the pre-fix constraint


def test_check_and_count_trigger_survives_midnight_req_row(db):
    """OPS-05 end-to-end: a per-minute 'req' row already sits at today's midnight
    for a key; check_and_count_trigger must still create that key's daily trigger
    counter for the SAME window (bucket-aware unique key) instead of 500-ing."""
    from sqlalchemy import select

    from app.core import ratelimit as rl

    day = rl._day_window(datetime.now(timezone.utc))
    db.add(RateLimit(key_id=7, bucket="req", window_start=day, count=1))
    db.commit()

    rl.limiter.check_and_count_trigger(db, key_id=7)  # must not raise

    row = db.execute(
        select(RateLimit).where(
            RateLimit.key_id == 7,
            RateLimit.bucket == "trigger",
            RateLimit.window_start == day,
        )
    ).scalar_one()
    assert row.trigger_count_today == 1


def test_trigger_insert_degrades_to_503_under_old_constraint():
    """OPS-05 recovery correctness against the PRE-widening unique key. Build a
    rate_limit table with the OLD (key_id, window_start) constraint, seed the
    midnight 'req' row, and drive check_and_count_trigger: its insert collides on
    a DIFFERENT bucket, the re-SELECT finds no trigger row, and recovery must
    degrade to a controlled 503 rather than re-inserting the same conflicting row
    (which would 500 / loop)."""
    from sqlalchemy import (
        Column,
        DateTime,
        Integer,
        MetaData,
        String,
        Table,
        UniqueConstraint,
        create_engine,
    )
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    from app.core import ratelimit as rl

    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    md = MetaData()
    Table(
        "rate_limit", md,
        Column("id", Integer, primary_key=True),
        Column("key_id", Integer),
        Column("bucket", String(128)),
        Column("window_start", DateTime(timezone=True)),
        Column("count", Integer, default=0),
        Column("trigger_count_today", Integer, default=0),
        # The OLD unique key — excludes `bucket`.
        UniqueConstraint("key_id", "window_start", name="uq_ratelimit_key_window"),
    )
    md.create_all(eng)

    with Session(eng) as s:
        day = rl._day_window(datetime.now(timezone.utc))
        s.add(rl.RateLimit(key_id=5, bucket="req", window_start=day, count=1))
        s.commit()
        with pytest.raises(HTTPException) as exc:
            rl.limiter.check_and_count_trigger(s, key_id=5)
        assert exc.value.status_code == 503


def test_prune_rate_limit_cron_deletes_stale_rows(client, db, monkeypatch):
    """OPS-05: the nightly cron route deletes rows older than two days and keeps
    recent ones. With a blank CRON_SECRET (dev), the route is reachable."""
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "CRON_SECRET", "")
    monkeypatch.setattr(app_settings, "APP_ENV", "development")
    now = datetime.now(timezone.utc)
    db.add(RateLimit(key_id=None, bucket="ip:old", window_start=now - timedelta(days=3), count=1))
    db.add(RateLimit(key_id=None, bucket="ip:new", window_start=now - timedelta(hours=1), count=1))
    db.commit()

    r = client.get("/admin/cron/prune-rate-limit")
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] == 1

    db.expire_all()
    remaining = {row.bucket for row in db.execute(select(RateLimit)).scalars()}
    assert remaining == {"ip:new"}
