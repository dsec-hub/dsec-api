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

from datetime import datetime, timezone

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
